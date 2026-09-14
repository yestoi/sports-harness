"""Finding 49 round 3: what one in-process weekly report render leaves behind.

Measured on the live process with rounds 1 and 2 deployed (build ebf0953, all times UTC): the
recorder sat flat at 245-266 MiB for about sixty ticks, then stepped to 700 MiB across settle
job run 178 -- whose `report_wtd` stage took 159.4 s -- and never came back down (637-642 MiB
for the next 4.5 hours, `docker top` RSS 631 MiB). Nothing grew after the step. So this is not
per-tick retention, which rounds 1 and 2 fixed: it is the peak working set of one render, kept
by the process after the render returns.

What the measurement found, over a week-shaped fixture (`tests/report_week_fixture.py`: 21,600
gap snapshots, 28,800 fair values, 96,000 order-book snapshots, 3,000 runs of notes), rendering
the same week materialised (the pre-fix shape) and streamed in one process:

* **No retention.** The harness-attributed traced total is 0.0 KiB before the render and
  8-42 KiB after it returns and after `gc.collect()` -- under 0.2 % of the render's own peak,
  materialised or streamed. Nothing holds the tables, the rows or the session's identity map
  afterwards, so there is no reference to release.
* **All allocator slack.** Materialised, RSS went 136.4 -> 195.9 MiB across the render (+72.6
  on the second render) and stayed there through `del tables` and `gc.collect()`;
  `malloc_trim(0)` handed back 59.9 MiB (72.5) and took it to 135.9, below where it started.
  glibc keeps the arenas the render freed, which is exactly the live step's shape.
* **A peak worth bounding.** Four whole-week reads set the high-water mark: table 4's snapshot
  read (25.6 MiB traced), table 5's fair-value scan (25.3), table 4b's (20.3) and t13's run
  notes (13.9). Streaming them takes the render's peak 25.7 -> 13.4 MiB and the RSS it leaves
  behind +59.5 -> +28.0 MiB.

So the fix is two things, measured separately: bound the peak (the whole-week reads that set the
high-water mark now stream -- `harness/report/tables.py::_stream`), and give the rest back
(`harness.telemetry.malloc_trim`, called once per settle run and once per priced tick).

Run with `-s` for the table and the top growing tracebacks.
"""

import gc
import time
import tracemalloc
from datetime import timedelta

import pytest
import respx

from harness.report.tables import weekly_tables
from harness.telemetry import malloc_trim, rss_mb

from tests.report_week_fixture import WEEK, WEEK_END, YEAR, seed_week

#: Only allocations with a frame under `harness/` -- the inclusive filter round 2 settled on
#: (`tests/test_recorder_memory_shape.py::_THE_RECORDER`), for the same reason: a stdlib or
#: third-party cache that fills on its own must not read as the report's retention, and an
#: allocation made inside SQLAlchemy or psycopg with a report frame on the stack must.
_THE_HARNESS = tracemalloc.Filter(True, "*/harness/*", all_frames=True)

#: The share of the render's own peak that may still be attributed to harness frames once the
#: render has returned and the collector has run. Anything above this is retention.
RETAINED_SHARE = 0.05
#: The RSS band after the trim, in MiB, and as a share of the pre-render reading: the round-2
#: band shape (`tests/test_recorder_memory_shape.py::RSS_FLOOR_MIB`). Four of the ~4 MiB arena
#: steps glibc extends the heap by, so the assertion measures the render rather than arena
#: quantisation. The live 500 MiB / 6 h criterion is unchanged and is not judged here: only the
#: deployed `recorder.rss_mb` series can judge it.
RSS_FLOOR_MIB = 16.0
RSS_SHARE = 0.05


def _harness_traced() -> int:
    snapshot = tracemalloc.take_snapshot().filter_traces([_THE_HARNESS])
    return sum(st.size for st in snapshot.statistics("filename"))


class TablePeaks:
    """Per-table peak allocation and RSS delta, wrapped around each `_tableN` in `tables.py`.

    Which table sets the high-water mark is the whole question for the peak half of the fix, so
    it is measured per table rather than for the render as a whole.
    """

    KEYS = ("_table1", "_table2", "_table3", "_table4", "_table4b", "_table5", "_table6",
            "_table7", "_table8", "_table10", "_table11", "_table12", "_table13")

    def __init__(self):
        self.rows = []

    def wrap(self, name, fn):
        def inner(*args, **kwargs):
            gc.collect()
            rss0 = rss_mb() or 0.0
            tracemalloc.reset_peak()
            base = tracemalloc.get_traced_memory()[0]
            t0 = time.monotonic()
            out = fn(*args, **kwargs)
            peak = tracemalloc.get_traced_memory()[1]
            self.rows.append((name, (peak - base) / 1024, (rss_mb() or 0.0) - rss0,
                              time.monotonic() - t0, peak / 1024))
            return out
        return inner

    def peak_kb(self) -> float:
        """The largest traced total any wrapped table reached.

        `wrap` resets the traced peak per table -- that is how a per-table number is had at all
        -- so the render-level `get_traced_memory()[1]` of a wrapped render only describes the
        last table. The render's real peak is the largest of the per-table absolute peaks.
        """
        return max((row[4] for row in self.rows), default=0.0)

    def text(self):
        lines = [f"{'table':10s} {'peak KiB':>12s} {'RSS delta MiB':>15s} {'s':>8s}"]
        for name, peak, rss, secs, _absolute in self.rows:
            lines.append(f"{name:10s} {peak:12.1f} {rss:15.1f} {secs:8.1f}")
        return "\n".join(lines)


def _render_once(session, settings, peaks=None, frames=3):
    """One `weekly_tables` render with every number the classification turns on.

    `tracemalloc` is running for the whole of it: its own bookkeeping inflates RSS, which is why
    the RSS numbers are only ever compared against each other inside one run.
    """
    from harness.report import tables as tables_mod

    gc.collect()
    tracemalloc.start(frames)
    originals = {}
    try:
        gc.collect()
        before_traced, before_rss = _harness_traced(), rss_mb()
        snap_before = tracemalloc.take_snapshot()
        tracemalloc.reset_peak()
        if peaks is not None:
            for key in TablePeaks.KEYS:
                originals[key] = getattr(tables_mod, key)
                setattr(tables_mod, key, peaks.wrap(key, originals[key]))
        t0 = time.monotonic()
        tables = weekly_tables(session, YEAR, WEEK, settings)
        elapsed = time.monotonic() - t0
        peak = max(tracemalloc.get_traced_memory()[1],
                   (peaks.peak_kb() * 1024) if peaks is not None else 0.0)
        n_rows = sum(len(t.rows) for t in tables.values())
        del tables
        gc.collect()
        after_traced, after_rss = _harness_traced(), rss_mb()
        diff = tracemalloc.take_snapshot().compare_to(snap_before, "traceback")
    finally:
        for key, fn in originals.items():
            setattr(tables_mod, key, fn)
        tracemalloc.stop()

    gc.collect()
    trimmed = malloc_trim()
    return {"elapsed": elapsed, "n_rows": n_rows, "peak_kb": peak / 1024,
            "before_traced_kb": before_traced / 1024, "after_traced_kb": after_traced / 1024,
            "before_rss": before_rss, "after_rss": after_rss, "trim_rss": rss_mb(),
            "trimmed_mb": trimmed, "diff": diff}


def _table(name, m) -> str:
    lines = [f"{name}: {m['elapsed']:.1f} s, {m['n_rows']} rendered rows",
             f"  {'stage':26s} {'harness traced KiB':>20s} {'RSS MiB':>10s}"]
    for label, traced, rss in (("before render", m["before_traced_kb"], m["before_rss"]),
                               ("peak during render", m["peak_kb"], None),
                               ("after render + gc", m["after_traced_kb"], m["after_rss"]),
                               ("after malloc_trim", m["after_traced_kb"], m["trim_rss"])):
        shown = "n/a" if rss is None else f"{rss:10.1f}"
        lines.append(f"  {label:26s} {traced:20.1f} {shown:>10s}")
    lines.append(f"  malloc_trim returned {m['trimmed_mb']} MiB")
    return "\n".join(lines)


def test_one_weekly_render_returns_its_peak_to_the_process(db_session, env_settings):
    """The round-3 acceptance, in the round-2 band shape.

    Two assertions, one per half of the classification:

    * the traced total attributable to harness frames, after the render has returned and the
      collector has run, is under `RETAINED_SHARE` of the render's own peak -- nothing is
      retained; and
    * on Linux, RSS after `malloc_trim` is back inside `max(RSS_SHARE, RSS_FLOOR_MIB)` of the
      pre-render reading -- the peak is given back rather than kept for the life of the process.

    Both are measured on the second render as well, because the live step happened on a process
    that had already been running for three hours: a fix that only works cold is not a fix.
    """
    t0 = time.monotonic()
    counts = seed_week(db_session)
    print(f"\nfixture seeded in {time.monotonic() - t0:.1f} s: "
          + ", ".join(f"{k}={v}" for k, v in counts.items()))

    first = _render_once(db_session, env_settings)
    peaks = TablePeaks()
    second = _render_once(db_session, env_settings, peaks=peaks)
    report = "\n".join([_table("first render", first), _table("second render", second),
                        "", "per-table (second render):", peaks.text()])
    print("\n" + report)
    print("\ntop 15 growing tracebacks, before -> after the first render (unfiltered):")
    for stat in [s for s in first["diff"] if s.size_diff > 0][:15]:
        print(f"  {stat.size_diff / 1024:+10.1f} KiB {stat.count_diff:+8d} blocks")
        for frame in stat.traceback.format():
            print(f"      {frame}")

    for name, m in (("first", first), ("second", second)):
        retained = m["after_traced_kb"] - m["before_traced_kb"]
        assert retained < RETAINED_SHARE * m["peak_kb"], (
            f"the {name} render left {retained:.1f} KiB of harness-attributed allocation behind, "
            f"{retained / m['peak_kb']:.1%} of its {m['peak_kb']:.1f} KiB peak\n{report}")
        if m["before_rss"] is None or m["trim_rss"] is None:
            continue                      # no /proc and no getrusage: nothing to judge
        band = max(RSS_SHARE * m["before_rss"], RSS_FLOOR_MIB)
        assert m["trim_rss"] - m["before_rss"] < band, (
            f"after the {name} render and the trim, RSS is "
            f"{m['trim_rss'] - m['before_rss']:+.1f} MiB on the pre-render reading "
            f"({m['before_rss']:.1f} -> {m['trim_rss']:.1f}), past the {band:.1f} MiB band\n"
            f"{report}")


def test_the_streamed_reads_render_byte_identical_tables(db_session, env_settings):
    """The report's numbers are a measurement, so bounding the peak may not move one of them.

    `_stream` fetches a whole-week read through a server-side cursor instead of materialising
    it. Same statement, same parameters, same rows, same order -- so the rendered tables must be
    identical object for object. This renders the same week both ways in one process and
    compares the whole structure, not a sampled cell.
    """
    from harness.report import tables as tables_mod

    seed_week(db_session, games=6, gaps_per_market=6, fairs_per_shape=6,
              snapshots_per_ticker=8, orders=30, runs=40)
    # A pinned `now`: t13's freshness pair is "seconds since the newest row" against the render
    # instant, so two renders a second apart differ there for a reason that has nothing to do
    # with how the rows were fetched.
    now = WEEK_END - timedelta(hours=1)
    streamed = weekly_tables(db_session, YEAR, WEEK, env_settings, now=now)

    original = tables_mod._stream
    original_runs = tables_mod.recent_runs_pricing
    materialised_calls = []

    def materialise(session, statement, params):
        materialised_calls.append(statement)
        return session.execute(statement, params).all()

    tables_mod._stream = materialise
    # t13's run-notes read has its own streaming switch (`harness/dashboard/queries.py`), so the
    # materialised pass has to take that one back too.
    tables_mod.recent_runs_pricing = (
        lambda session, cutoff, limit=None, stream=False: original_runs(session, cutoff, limit))
    try:
        materialised = weekly_tables(db_session, YEAR, WEEK, env_settings, now=now)
    finally:
        tables_mod._stream = original
        tables_mod.recent_runs_pricing = original_runs

    assert len(materialised_calls) >= 4, "the streamed reads are not being exercised"
    assert set(streamed) == set(materialised)
    for key in streamed:
        assert streamed[key] == materialised[key], f"table {key} differs when streamed"
    # A guard on the fixture: identical placeholders everywhere would prove nothing.
    assert any(any(isinstance(v, tuple) for v in row) for row in streamed["t4"].rows), (
        "the equality fixture produced no populated table-4 cell")


@pytest.mark.parametrize("phase", ["settle"])
def test_the_settle_job_trims_once_and_records_the_mib_it_returned(
        db_session, env_settings, monkeypatch, phase):
    """Fix 49 round 3 (ii), settle half: one `malloc_trim` per settle run, recorded.

    The settle job runs inside the recorder process and is where the live step happened, so the
    trim has to be part of the job rather than of whoever happens to call it next.
    """
    import time as _time

    from sqlalchemy.orm import sessionmaker

    from harness.db.models import MetricSample
    from harness.settlement import job as job_mod
    from harness.settlement.job import Settler

    calls = []
    monkeypatch.setattr(job_mod.telemetry, "malloc_trim",
                        lambda: calls.append(1) or 12.5)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    Settler(env_settings, factory, None, monotonic=_time.monotonic).run()

    assert len(calls) == 1, f"malloc_trim ran {len(calls)} times in one settle job"
    rows = db_session.query(MetricSample).filter(
        MetricSample.name == "recorder.malloc_trim_mb").all()
    assert [(r.source, r.value, r.labels) for r in rows] == [("recorder", 12.5, {"phase": phase})]


def test_a_platform_without_malloc_trim_is_a_no_op(monkeypatch):
    """(ii), the guard half: no libc symbol means `None` and no metric, never a raise.

    musl and macOS have no `malloc_trim`; the tests run on both, and so might a future image.
    """
    from harness import telemetry

    monkeypatch.setattr(telemetry, "_MALLOC_TRIM", False)
    assert telemetry.malloc_trim() is None

    class _NoSymbol:
        def __getattr__(self, name):
            raise AttributeError(name)

    monkeypatch.setattr(telemetry, "_MALLOC_TRIM", None)
    monkeypatch.setattr("ctypes.CDLL", lambda name: _NoSymbol())
    assert telemetry._malloc_trim_fn() is None
    assert telemetry.malloc_trim() is None


@respx.mock
def test_a_priced_tick_trims_once_and_records_the_mib_it_returned(
        env_settings, db_session, monkeypatch):
    """(ii), tick half: one `malloc_trim` per *priced* tick, with its MiB on Pulse.

    Per priced tick, not per tick: the pricing pass is the tick's largest allocation and a tick
    that skipped it has nothing to hand back. `recorder.rss_mb` is sampled after the trim, so
    the series the 500 MiB criterion is read from carries the trimmed number.
    """
    from harness.db.models import MetricSample
    from harness.normalize import runner as runner_mod
    from harness.recorder import tick as tick_mod

    from tests.test_recorder_memory import _driver

    runner_mod._EVENTS.clear()
    calls = []
    monkeypatch.setattr(tick_mod.telemetry, "malloc_trim", lambda: calls.append(1) or 7.5)
    try:
        run_tick, _ = _driver(env_settings, db_session, 1)
        run_tick()
    finally:
        runner_mod._EVENTS.clear()

    assert len(calls) == 1, f"malloc_trim ran {len(calls)} times in one tick"
    rows = db_session.query(MetricSample).filter(
        MetricSample.name == "recorder.malloc_trim_mb").all()
    assert [(r.source, r.value, r.labels) for r in rows] == [("recorder", 7.5, {"phase": "tick"})]
