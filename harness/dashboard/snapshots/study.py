"""Study: is the strategy any good?

Spec §2.3. The rule that shapes this whole module is brief constraint 6: **the UI never computes
a mean.** Every table cell is read verbatim out of `report_cells` -- `text`, `estimate`,
`n_obs`, `n_clusters`, `lo`, `hi`, `flags` -- keyed `table_key -> row_key -> col_key`, and the
forest plot, the heatmap and the ledger are drawn from those cells. `text` is the exact string
`render_markdown` printed, so the surface cannot round a number differently from the report.

Two honesty requirements the reviews added:

* **The provisional label carries the cells' age.** `report_wtd` runs six-hourly
  (`report_wtd_period_s = 21_600`, and this phase does not change it), so a snapshot rebuilt
  every ten minutes can be showing cells five hours old. Saying "provisional" without saying how
  old is the kind of half-truth this design exists to avoid.
* **The equity curve carries `mtm_coverage`.** It is the one Study number that is not a stored
  cell: cash plus open positions valued at the live midpoint. A curve at 40 % coverage is a
  different number from one at 100 %, so the coverage travels per point, the sentence states it,
  and the marked line is drawn grey below `MTM_GREY_COVERAGE`.

The markdown is carried verbatim and **not** sanitized: the sanitizer strips `%`, `+`, `$`, `[`
and `]`, and applying it to a report would corrupt exactly the strings spec §1.1 says must be
shown as stored. The front end's DOM rule is the guard: the document goes into a `<pre>` as
`textContent`, with its `markdown_sha256` beside it, and no renderer is vendored.
"""

import math
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.snapshots import base_payload, current_name, register_builder, section
from harness.report.tables import CONTRAST_BENCHMARK, week_bounds
from harness.telemetry import sanitize_reason

CADENCE_S = 600
#: Below this share of open contracts valued against a clean book, the marked line is grey.
MTM_GREY_COVERAGE = 0.5

STUDY_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                        "week", "year", "report_run_id", "provisional", "cell_age_s",
                        "generated_at", "cells", "equity", "annotations", "markdown",
                        "markdown_sha256", "weeks", "sentences_gaps"})

_NEWEST_FINAL = text("""
    select id, generated_at, provisional, markdown, markdown_sha256
    from report_runs
    where year = :year and week = :week and provisional = false
    order by generated_at desc limit 1
""")
_NEWEST_ANY = text("""
    select id, generated_at, provisional, markdown, markdown_sha256
    from report_runs
    where year = :year and week = :week
    order by generated_at desc limit 1
""")
_CELLS = text("""
    select table_key, row_key, col_key, estimate, n_obs, n_clusters, lo, hi, text, flags
    from report_cells where report_run_id = :run_id
""")
_WEEKS = text("""
    select distinct year, week from report_runs order by year desc, week desc
""")
_NEWEST_FINAL_PER_WEEK = text("""
    select distinct on (year, week) year, week, id
    from report_runs where provisional = false
    order by year, week, generated_at desc, id desc
""")
_NEWEST_ANY_ID = text("""
    select id from report_runs
    where year = :year and week = :week
    order by generated_at desc, id desc limit 1
""")
_STUDY_SNAPSHOTS = text("""
    select name, payload->>'report_run_id' as run_id from dashboard_snapshots
    where name like 'study:%'
""")
#: Bounded by the week, not by a LIMIT: `Settings.equity_sample_s` defaults to 300, so one week
#: is about 2,016 points per variant and this is by far the largest thing in the Study payload
#: (fix round 1, M5). Predictable rather than unbounded, but if `/api/snap` ever needs it
#: trimmed, the trim belongs here with its rule stated -- never in the front end, which computes
#: nothing.
_EQUITY = text("""
    select ts, variant_id, cash, mtm_open, mtm_coverage
    from equity_snapshots
    where ts >= :start and ts < :end
    order by variant_id, ts
""")
_ANNOTATIONS = text("""
    select ts, kind, summary from operator_events
    where ts >= :start and ts < :end order by ts
""")


def parse_week(name: str) -> tuple[int, int]:
    """`study:2026-37` to `(2026, 37)`. The name has already passed the anchored pattern in
    `harness.dashboard.snapshots`, so nothing but digits reaches here."""
    year, _, week = name.partition(":")[2].partition("-")
    return int(year), int(week)


def weeks_available(session: Session) -> list[str]:
    return [f"{row.year}-{row.week}" for row in session.execute(_WEEKS)]


def stale_study_names(session: Session, now: datetime | None = None) -> list[str]:
    """Every `study:<year>-<week>` the scheduler should rebuild: a week whose stored payload
    records a `report_run_id` other than the run `build_study` would pick for it today, and any
    such week with no snapshot at all.

    This is what makes addendum §0.1 hold: a closed week is rebuilt by the scheduler, not on
    demand by a page view, so spec §0.3's "no page view runs a query" has no exception. A week
    with no run has no snapshot, and the surface says so.

    The predicate mirrors `build_study`'s own choice rather than taking the newest run of any
    kind, and that is the whole point of `now` (fix round 1, I1): a closed week reads the newest
    **non-provisional** run, so a closed week whose newest run is provisional -- the normal state
    between the ISO rollover and `harness report` -- has nothing to build and must not be listed
    on every tick forever. Only the current ISO week takes the newest run of either kind. `now`
    is optional so the scheduler can call this with the session alone; tests pass it to pin the
    week rather than depend on the wall clock.
    """
    current = (now or datetime.now(timezone.utc)).isocalendar()
    stored = {row.name: row.run_id for row in session.execute(_STUDY_SNAPSHOTS)}
    # name -> the run id `build_study` would pick for that week today. A closed week with no
    # non-provisional run is simply absent, which is what stops the forever-stale loop.
    wanted: dict[str, int] = {f"study:{row.year}-{row.week}": row.id
                              for row in session.execute(_NEWEST_FINAL_PER_WEEK)}
    row = session.execute(_NEWEST_ANY_ID,
                          {"year": current.year, "week": current.week}).first()
    if row is not None:
        wanted[f"study:{current.year}-{current.week}"] = row.id
    return sorted(name for name, run_id in wanted.items() if stored.get(name) != str(run_id))


def _finite(x) -> float | None:
    """A cell's `estimate`/`lo`/`hi` as a plain float, or `None` if it is absent or not finite.

    `harness/report/stats.py:31`'s `NAN = float("nan")` lands in a cell whose interval cannot be
    computed (one cluster), and the report stores it in `report_cells.lo`/`hi`. A `NaN` or `inf`
    reaching the payload here would abort the whole snapshot upsert (PostgreSQL's jsonb rejects
    the literal), so it is mapped to `None` at the read, exactly like an absent interval: the
    client already greys a `None` cell, and `n_clusters` stays as stored so the row still shows
    why."""
    if x is None:
        return None
    value = float(x)
    return value if math.isfinite(value) else None


def _cells(session: Session, run_id: int) -> dict:
    out: dict[str, dict] = {}
    for row in session.execute(_CELLS, {"run_id": run_id}):
        table = out.setdefault(row.table_key, {}).setdefault(row.row_key, {})
        table[row.col_key] = {
            "estimate": _finite(row.estimate),
            "n_obs": row.n_obs, "n_clusters": row.n_clusters,
            "lo": _finite(row.lo),
            "hi": _finite(row.hi),
            # Shown as stored (spec §1.1): never sanitized, or the report's own "+3.1 %" and
            # "[-0.2, +6.4]" would come back mangled (ruling A-I5).
            "text": row.text,
            "flags": row.flags or {},
        }
    return out


def _stored_number(cell: dict | None) -> float | None:
    """One cell's number, whether the report stored it as a CI quintet or as a plain value.

    t12's `count` is a plain `int` and its `share` a plain `float`
    (`harness/report/tables.py`), not the quintet `is_cell` looks for, so
    `harness/report/weekly.py::_cell_fields` takes its non-cell branch and leaves `estimate`,
    `n_obs`, `n_clusters`, `lo` and `hi` all None with the number only in `text`. Read it back
    out of `text`; never recompute it (brief constraint 6).
    """
    if not cell:
        return None
    if cell.get("estimate") is not None:
        return cell["estimate"]
    try:
        return float(cell["text"])
    except (TypeError, ValueError):   # PLACEHOLDER "--" when the kind's total is zero
        return None


def _equity(session: Session, start: datetime, end: datetime) -> dict:
    lanes: dict[str, dict] = {}
    for row in session.execute(_EQUITY, {"start": start, "end": end}):
        lane = lanes.setdefault(row.variant_id, {"variant": row.variant_id, "points": [],
                                                 "min_coverage": None})
        cash = float(row.cash)
        mtm = float(row.mtm_open) if row.mtm_open is not None else None
        coverage = float(row.mtm_coverage) if row.mtm_coverage is not None else None
        lane["points"].append([row.ts.isoformat(), cash,
                               (cash + mtm) if mtm is not None else None, coverage])
        if coverage is not None:
            lane["min_coverage"] = (coverage if lane["min_coverage"] is None
                                    else min(lane["min_coverage"], coverage))
    for lane in lanes.values():
        lane["cash"] = lane["points"][-1][1] if lane["points"] else None
        lane["mtm_grey"] = (lane["min_coverage"] is not None
                            and lane["min_coverage"] < MTM_GREY_COVERAGE)
    return {"grey_below": MTM_GREY_COVERAGE,
            "variants": [lanes[k] for k in sorted(lanes)]}


def _annotations(session: Session, start: datetime, end: datetime) -> list[dict]:
    return [{"ts": row.ts.isoformat(), "kind": row.kind,
             "summary": sanitize_reason(row.summary or "")}
            for row in session.execute(_ANNOTATIONS, {"start": start, "end": end})]


def _ledger_rows(cells: dict) -> list[dict]:
    """The variant ledger's rows as the sentence template wants them: one row key with the
    largest `n_clusters` any of its cells carries."""
    rows = []
    for row_key, columns in (cells.get("t1") or {}).items():
        clusters = [c["n_clusters"] for c in columns.values() if c["n_clusters"] is not None]
        rows.append({"row_key": row_key, "n_clusters": max(clusters) if clusters else None})
    return rows


def _contrast_rows(cells: dict, benchmark: str) -> list[dict]:
    rows = []
    for row_key, columns in (cells.get("t2") or {}).items():
        cell = columns.get(benchmark)
        if cell is None:
            continue
        rows.append({"variant": row_key, "estimate": cell["estimate"], "lo": cell["lo"],
                     "hi": cell["hi"], "n_clusters": cell["n_clusters"]})
    return rows


def _declined_rows(cells: dict) -> list[dict]:
    rows = []
    for row_key, columns in (cells.get("t12") or {}).items():
        variant, _, rest = row_key.partition("/")
        kind, _, reason = rest.partition(":")
        estimator = ("clv_rejected_gap_outcomes" if kind == "rejected"
                     else "clv_skipped_intent_snapshot")
        cell = columns.get(estimator) or {}
        rows.append({"variant": variant, "kind": kind, "reason": reason,
                     "estimator": estimator,
                     "count": _stored_number(columns.get("count")),
                     "share": _stored_number(columns.get("share")),
                     "estimate": cell.get("estimate"), "lo": cell.get("lo"),
                     "hi": cell.get("hi"), "n_clusters": cell.get("n_clusters")})
    return sorted(rows, key=lambda r: -(r["count"] or 0))


def build_study(session: Session, now: datetime, settings: Settings) -> dict:
    name = current_name.get() or f"study:{now.isocalendar().year}-{now.isocalendar().week}"
    year, week = parse_week(name)
    payload = base_payload(name, now, settings, CADENCE_S)
    payload["year"], payload["week"] = year, week
    section(session, payload, "weeks", lambda: weeks_available(session))

    current = now.isocalendar()
    is_current = (year, week) == (current.year, current.week)
    run = session.execute(_NEWEST_ANY if is_current else _NEWEST_FINAL,
                          {"year": year, "week": week}).first()
    if run is None:
        payload.update({"report_run_id": None, "provisional": None, "cell_age_s": None,
                        "generated_at": None, "cells": {},
                        "equity": {"variants": [], "grey_below": MTM_GREY_COVERAGE},
                        "annotations": [], "markdown": None, "markdown_sha256": None})
        payload["sentences"] = {"ledger": sentences.study_ledger({"week": f"{year}-{week}"}),
                                "contrasts": sentences.study_contrasts({}),
                                "equity": sentences.study_equity({}),
                                "declined": sentences.study_declined({})}
        payload["readings"] = {"declined": []}
        payload["sentences_gaps"] = []
        return payload

    payload["report_run_id"] = run.id
    payload["provisional"] = bool(run.provisional)
    payload["generated_at"] = run.generated_at.isoformat()
    payload["cell_age_s"] = (now - run.generated_at).total_seconds()
    payload["markdown"] = run.markdown
    payload["markdown_sha256"] = run.markdown_sha256

    section(session, payload, "cells", lambda: _cells(session, run.id))
    start, end = week_bounds(year, week, settings.tz_local)
    section(session, payload, "equity", lambda: _equity(session, start, end))
    section(session, payload, "annotations", lambda: _annotations(session, start, end))

    cells = payload["cells"] if isinstance(payload["cells"], dict) else {}
    equity = payload["equity"] if isinstance(payload["equity"], dict) else {}
    declined = _declined_rows(cells)
    payload["sentences"] = {
        "ledger": sentences.study_ledger({"week": f"{year}-{week}",
                                          "provisional": payload["provisional"],
                                          "cell_age_s": payload["cell_age_s"],
                                          "rows": _ledger_rows(cells)}),
        "contrasts": sentences.study_contrasts(
            {"benchmark": CONTRAST_BENCHMARK,
             "rows": _contrast_rows(cells, CONTRAST_BENCHMARK)}),
        "equity": sentences.study_equity(equity),
        "declined": sentences.study_declined({"rows": declined}),
    }
    payload["readings"] = {"declined": [sentences.study_declined_reading(row)
                                        for row in declined]}
    # Ruling A-I2: the reason codes the declined rows just rendered through `reason_phrase`, so
    # a code outside `REASON_PHRASES` is not silently lost -- the next plan sees it.
    payload["sentences_gaps"] = sentences.unknown_reason_codes(
        row.get("reason") for row in declined)
    return payload


register_builder("study", build_study)
