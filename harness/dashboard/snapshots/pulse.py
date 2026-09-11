"""Pulse: is it alive and honest?

Spec §2.1 is the whole contract, and its last line is the one that matters most: "every rule has
a name and the fired list shows it. This is the whole list; a red that is not one of these is a
bug." So the rules are named pure functions over one gathered dict, the status word is a fold
over their levels, and the test file pins the set of names.

Two rules of construction, both from the reviews:

* **Every threshold is imported from the code that enforces it** (spec §1.1). Nothing here
  spells 60, 120, 0.25, 0.6 or 0.8; they come from `harness.health`, and the drawdown stop comes
  from `harness.execution.risk`. The payload carries the threshold beside the value so the front
  end never restates one either.
* **An absent input is `not evaluated`, never `fine`** (ruling A-C2, B-I1). A rule whose value
  has not been recorded yet -- no housekeeping run since the deploy, no heartbeat row, no check
  sweep -- reports that in words and contributes nothing to the status word. A wall that reads
  green over a measurement nobody took is the exact failure it exists to prevent.

**Never shown here.** Any P&L, CLV or strategy figure. Pulse is about the machine, not the edge,
and `PULSE_KEYS` is asserted by the payload-schema test.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.snapshots import (FLOOR_P95_BUDGET_MS, SNAPSHOT_DISABLE_MS,
                                         base_payload, builder_key, disabled_builders,
                                         register_builder, section)
from harness.dashboard.snapshots import floor as _floor
from harness.dashboard.snapshots import gate as _gate
from harness.dashboard.snapshots import study as _study
from harness.dashboard.snapshots import ticket as _ticket
from harness.execution.risk import DRAWDOWN_STOP_PCT, DRAWDOWN_WINDOW, stopped_variants
from harness.health import (CREDITS_LOW_FRACTION, CREDITS_WATCH_FRACTION, DB_BROKEN_FRACTION,
                            DB_WATCH_FRACTION, DISK_FREE_MIN_FRACTION, HEARTBEAT_BROKEN_S,
                            HEARTBEAT_WATCH_S, STALE_AFTER_S, VETO_RATE_WATCH,
                            WS_EVENT_BROKEN_S, WS_EVENT_WATCH_S)
from harness.research.spend import spend_state
from harness.telemetry import sanitize_reason
from harness.weeks import chicago_iso_week

log = logging.getLogger(__name__)

#: 60 s, halved from 30 by fix 31. Pulse is the status word every other surface's header
#: borrows, so it stays the fastest of the five; what it does not need is to re-read
#: `metric_samples` twice a minute on a NAS whose page cache cannot hold the executor's working
#: set alongside it.
CADENCE_S = 60
#: The tape strip's window and its bucket size (spec §2.1 item 2).
TAPE_WINDOW = timedelta(hours=24)
TAPE_BUCKET_MIN = 5
#: The gap rule's window (spec §2.1: "any `gap` row in the last 2 h", re-sourced to
#: `sum(ws.gaps)` from metric_samples by ruling A-I2).
GAP_WINDOW = timedelta(hours=2)
#: The window the floor builder's p95 is taken over (addendum §1, A-I9). The budget it is
#: measured against lives in `harness/dashboard/snapshots/__init__.py`, which T16's scheduler
#: imports from too: it backs the in-window floor cadence off on the same number, and a surface
#: that disagreed with the scheduler about it would be worse than either.
FLOOR_P95_WINDOW = timedelta(minutes=10)
#: How many operator events the surface shows (spec §2.1 item 6).
EVENTS_LIMIT = 10
#: `_VITALS`' row cap (fix 31). `TAPE_WINDOW` at `metric_sample_s` = 60 is about 1,440 rows per
#: name across five names, so this is a little over twice the expected size: enough that the
#: sparklines are never trimmed in normal operation, and low enough that a sampler running hot
#: cannot hand this section an unbounded sort. Rows come back newest-first and are reversed in
#: `_vitals`, so the cap drops the oldest points of the window rather than the newest.
VITALS_LIMIT = 10_000
#: The cadence each snapshot's staleness is *judged* against, so the rule can say 2x and 3x.
#: Deliberately the *out-of-window* cadence for floor and ticket, not the in-window one: a
#: scheduler that flips floor to 30 s would otherwise make a healthy 80 s-old snapshot read as
#: stale the moment a game ended. The cost is that a dead floor job is noticed at 240 s rather
#: than at 60 s, which is inside the window the recorder's own staleness rule already covers.
#: Fix 31 doubled every cadence these read, so the ladder moved with them and no threshold here
#: is written down twice.
#: Named apart from `harness/dashboard/scheduler.py`'s `CADENCES` on purpose: those are the
#: intervals the jobs actually run at, these are the intervals a snapshot's age is read against,
#: and the two differ for `floor` and `ticket` by the paragraph above. Neither is the other's
#: bug to fix.
JUDGED_CADENCES = {"pulse": CADENCE_S, "floor": _floor.CADENCE_OUT_S, "gate": _gate.CADENCE_S,
                   "ticket": _ticket.CADENCE_OUT_S, "study": _study.CADENCE_S}

#: The builder keys the scheduler has stopped running, and the number a builder had to cost to
#: earn that. Imported, not restated: `harness/dashboard/scheduler.py` disables on exactly this
#: threshold, and a surface that named a different one would be describing a guard that does not
#: exist. See `harness/dashboard/snapshots/__init__.py` for why the set is process-local.
_DISABLE_MS = SNAPSHOT_DISABLE_MS

PULSE_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                        "status", "tape", "vitals", "storage", "invariants",
                        "operator_events", "snapshots", "build", "research"})

LEVEL_RANK = {"fine": 0, "not_evaluated": 0, "watch": 1, "broken": 2}
_WORDS = {0: "FINE", 1: "WATCH", 2: "BROKEN"}


@dataclass(frozen=True)
class RuleResult:
    """One rule's reading. `unit` is what `sentences.pulse_rule_reading` formats the value and the
    threshold with, and the enumerated set has no millisecond or multiple: `snapshot_budget`
    (milliseconds) and `snapshot_stale` (a multiple of a cadence) both carry `count`, so their
    readings print bare numbers. T18 labels those two rules itself rather than the set growing a
    unit only two rules would use.
    """

    name: str
    level: str              # fine | watch | broken | not_evaluated
    value: float | None
    threshold: float | None
    unit: str               # s | fraction | gb | count | ""

    def as_dict(self) -> dict:
        return {"name": self.name, "level": self.level, "value": self.value,
                "threshold": self.threshold, "unit": self.unit}


def status_word(rules: list[RuleResult]) -> str:
    return _WORDS[max((LEVEL_RANK[r.level] for r in rules), default=0)]


def _absent(name: str, threshold, unit: str) -> RuleResult:
    return RuleResult(name, "not_evaluated", None, threshold, unit)


def _ladder(name: str, value, watch, broken, unit: str) -> RuleResult:
    """The common shape: higher is worse, two thresholds. The reported threshold is the one the
    rule is being judged against, so the front end can print "74 s against 60 s" without knowing
    anything about the ladder."""
    if value is None:
        return _absent(name, watch, unit)
    value = float(value)
    if broken is not None and value >= float(broken):
        return RuleResult(name, "broken", value, float(broken), unit)
    if watch is not None and value >= float(watch):
        return RuleResult(name, "watch", value, float(watch), unit)
    return RuleResult(name, "fine", value, float(watch if watch is not None else broken), unit)


def _flag(name: str, tripped, level: str, value, threshold, unit: str) -> RuleResult:
    """A boolean rule: tripped or not, with the count that tripped it carried as the value."""
    if tripped is None:
        return _absent(name, threshold, unit)
    return RuleResult(name, level if tripped else "fine", value, threshold, unit)


#: Bound: `limit 1` in primary-key order. Index: the `runs` primary key, read backwards.
_NEWEST_RUN = text("select started_at, build_sha from runs order by id desc limit 1")
#: Final review M7, fixed by fix 31. This was `select odds_remaining from runs where
#: odds_remaining is not null order by id desc limit 1`: a backward walk of the `runs` primary
#: key through every credit-less row until it found one, with no bound at all on how far it
#: would walk. `compute_health` runs the same read under the legacy page's 10 s budget; here the
#: budget is 2 s and the machine has no page cache to spare.
#:
#: The recorder already writes the same number as `recorder.credits_remaining` once per tick,
#: which is what Pulse's own credits tile draws its sparkline from -- so this is one row per
#: minute off `ix_metric_samples_name_ts (name, ts desc)`, bounded to `TAPE_WINDOW`, instead of
#: an unbounded walk of a table with a row per tick per season. A month-old credit reading is
#: not a reading; absent, `rule_credits_low` reports `not evaluated`, which is the honest word
#: for a recorder that has not run in a day.
_NEWEST_CREDITS = text("""
    select value from metric_samples
    where name = 'recorder.credits_remaining' and value is not null and ts > :since
    order by ts desc limit 1
""")
_HEARTBEAT = text("""
    select last_loop_at, ws_last_event_at, loops_skipped, p95_loop_ms, book_dirty_markets,
           executor_version
    from exec_heartbeat where id = 1
""")
_KILL = text("select active from kill_switch where id = 1")
#: Bound: `ts > :since` (`GAP_WINDOW`, 2 h). Index: `ix_metric_samples_name_ts (name, ts desc)`.
_GAPS_2H = text("""
    select coalesce(sum(value), 0) from metric_samples
    where name = 'ws.gaps' and ts > :since
""")
#: Bound: `ts > :since` (`TAPE_WINDOW`, 24 h). Index: `ix_metric_samples_name_ts (name, ts
#: desc)` -- `order by name, ts desc` is the index's own order, so the `distinct on` takes the
#: head of each name's range and stops.
_NEWEST_METRIC = text("""
    select distinct on (name) name, value, ts from metric_samples
    where name in ('host.disk_free_gb', 'host.disk_total_gb', 'host.mem_available_mb')
      and ts > :since
    order by name, ts desc
""")
#: Bound: `ts > :since` (`TAPE_WINDOW`, 24 h). Index: `ix_metric_samples_name_ts (name, ts
#: desc)`, one range per name; the bucket arithmetic groups what the range returns.
_TAPE = text("""
    select name, labels->>'source' as source,
           floor(extract(epoch from (:now - ts)) / (:bucket * 60))::int as bucket,
           sum(value) as total
    from metric_samples
    where name in ('ws.events_per_min', 'recorder.fetched') and ts > :since
    group by 1, 2, 3
""")
#: Ruling B-C3: only the names a vitals tile actually reads by its `metric` key. `exec.loop_ms`,
#: `ws.events_per_min`, `ws.trades_per_min` and `ws.reconnects` used to be collected here too and
#: serialized into every 30 s payload unread -- no tile's `metric` names them.
#: Bound: `ts > :since` (`TAPE_WINDOW`, 24 h) and `limit :limit` (`VITALS_LIMIT`). Index:
#: `ix_metric_samples_name_ts (name, ts desc)`, one range per name. Ordered *descending* so the
#: cap drops the oldest points rather than the newest; `_vitals` reverses each lane (fix 31).
_VITALS = text("""
    select name, value, ts from metric_samples
    where name in ('exec.p95_loop_ms', 'exec.loops_skipped', 'exec.dirty_markets',
                   'exec.ws_event_age_s', 'recorder.credits_remaining')
      and ts > :since
    order by ts desc
    limit :limit
""")
#: Bound: `ts > :since` (`FLOOR_P95_WINDOW`, 10 min). Index: `ix_metric_samples_name_ts (name,
#: ts desc)`; `labels->>'name'` is unindexed and does not need to be, since the range it filters
#: is ten minutes of one metric name.
_FLOOR_MS = text("""
    select value from metric_samples
    where name = 'serve.snapshot_ms' and labels->>'name' = 'floor' and ts > :since
    order by value
""")
_LATEST_SWEEP = text("""
    select check_name, status, value, threshold, detail
    from check_results
    where ts = (select max(ts) from check_results)
    order by check_name
""")
#: No `limit`: the window is the bound. `Settings.settle_period_s` is 3600, so about 24 rows fall
#: inside 24 h, read in index order off `ix_job_runs (job, started_at desc)`. A row cap here used
#: to hide an error twelve hours old behind ten newer passes, which made `settle_error_24h` cover
#: about ten hours while its name, the spec line and the payload all said twenty-four.
_SETTLE_RUNS = text("""
    select status, budget_exhausted from job_runs
    where job = 'settle' and started_at > :since
    order by started_at desc
""")
_HOUSEKEEPING = text("""
    select notes from job_runs where job = 'settle' order by started_at desc limit 30
""")
#: Bound: `limit :limit` (`EVENTS_LIMIT`). Index: `ix_operator_events_ts (ts desc)`.
_EVENTS = text("""
    select ts, kind, summary from operator_events order by ts desc limit :limit
""")
#: Addendum 0.5: `cell_age_s` rides along with the row's own age so the ages panel can show the
#: two apart. It is a key of the *stored payload*, so a surface that does not carry one (every
#: surface but Study) reads SQL NULL and the row says `None` rather than a zero it did not earn.
_SNAPSHOT_AGES = text("""
    select name, generated_at, elapsed_ms, error,
           (payload->>'cell_age_s')::float as cell_age_s
    from dashboard_snapshots
""")
_GAMES_LIVE = text("select count(*) from games where status = 'in_progress'")
#: Each variant's *newest* drawdown reading inside the risk window, mirroring
#: `harness.execution.risk._NEWEST_VERDICT` so the number beside a stop is the same row the stop
#: was decided on. Addendum §0.3 asks for the newest reading, not the worst one in the window: a
#: variant that fell to -0.35 and recovered to -0.21 must render -0.21, because the older figure
#: was true last Tuesday and presenting it as today's is the kind of quiet lie this surface exists
#: to refuse. `drawdown_stop is not null` is the same "not evaluated" filter the risk module uses.
#: Bound: `ts >= :since` (`DRAWDOWN_WINDOW`, 7 d). Index: `ix_equity_variant_ts (variant_id,
#: ts)`, with `ts` as its *second* column -- nothing leads on `ts`, so the window is an index
#: filter over a full scan of that index, and the per-variant heads are sorted out of what
#: it returns. Seven days is what keeps both to a bounded size.
_NEWEST_DRAWDOWN = text("""
    select distinct on (variant_id) variant_id, drawdown_pct from equity_snapshots
    where ts >= :since and drawdown_stop is not null
    order by variant_id, ts desc
""")

#: The decided set (addendum §1.4). Restated nowhere else on this surface.
_DECIDED = ("proceed", "reduce", "veto")

#: Bound: `decided_at > :since` (24 h). Index: `ix_veto_decisions_decided on veto_decisions
#: (decided_at desc)` (T1's schema), so this is an index range scan, not a table scan.
_VETO_RATE = text("""
    select count(*) filter (where decision in ('reduce', 'veto')) as vetoed,
           count(*) filter (where decision in ('proceed', 'reduce', 'veto')) as decided
    from veto_decisions
    where decided_at > :since
""")
#: `rfq_quotes` is not forbidden: it holds no venue or model free text, only the harness's own
#: computed bid. `report_annotations` likewise holds only the surviving bullet count here.
#:
#: Bound: `computed_at > :since` (24 h) on `rfq_quotes`, `created_at > :week_start` on
#: `report_annotations`. Index: `report_annotations` is `TINY_TABLES` (one row a week, keyed by
#: `report_run_id`), so its scan needs none. `rfq_quotes` is `BOUNDED_TABLES` (grows with the
#: season) and its `computed_at` half is served by `ix_rfq_quotes_computed on rfq_quotes
#: (computed_at desc)` (T1's schema), so this is an index range scan, not a table scan.
_RESEARCH_COUNTS = text("""
    select (select count(*) from rfq_quotes where computed_at > :since) as rfq_quotes_24h,
           (select count(*) from report_annotations where created_at > :week_start)
               as annotations_week
""")


def _veto_rate(session: Session, now: datetime) -> float | None:
    """`reduce + veto` over the decided signals of the last 24 h, or None when none decided."""
    row = session.execute(_VETO_RATE, {"since": now - timedelta(hours=24)}).first()
    if row is None or not row.decided:
        return None
    return float(row.vetoed) / float(row.decided)


def _housekeeping_counts(session: Session) -> dict | None:
    """The newest `housekeeping` stage note that actually ran, over a bounded scan of recent
    settlement passes -- the same read the legacy page's database-ceiling section does."""
    for notes in session.execute(_HOUSEKEEPING).scalars():
        for stage in (notes or {}).get("stages", []):
            if stage.get("name") == "housekeeping" and "size_gb" in (stage.get("counts") or {}):
                return stage["counts"]
    return None


def _group(session: Session, name: str, fn: Callable[[], object], default: object = None):
    """One query group of `gather`, isolated (ruling A-I1). `gather` is called outside every
    `section()` guard -- its result feeds `status`, which the shell polls on every surface at
    30 s -- so one slow or broken group must not cost the whole payload the way an unguarded
    `section` body would. A `DBAPIError` deactivates the session for every later statement, so a
    failure here rolls back before the next group runs. `default` is the value each group's own
    consuming rule already treats as absent: `None` where a rule checks `is None`/`if x else`,
    an empty list or dict where a rule iterates or indexes its group unconditionally (`sweep`,
    `settle`, `floor_ms`, `snapshots`, `stopped`, `drawdown_by_variant`, `metrics`) so that rule
    falls through to `_absent` instead of raising past `evaluate()`, which is unguarded too."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - one query group must not cost the whole payload
        log.warning("pulse gather group %s failed: %s", name, type(exc).__name__)
        session.rollback()
        return default


def gather(session: Session, now: datetime, settings: Settings) -> dict:
    """Every value the rules read, one bounded query each, each isolated by `_group` so a
    failure in one leaves the rest intact and that group's rule reads `not_evaluated`.

    `recorder` is read here rather than through `harness.health.compute_health`, which takes a
    `sessionmaker` and would check out a second connection from a pool of two while a build is
    already holding one. The staleness rule itself is not re-derived: `STALE_AFTER_S` is
    imported from the same module `compute_health` enforces it in, so the two cannot disagree
    about when a recorder is stale.
    """
    run = _group(session, "run", lambda: session.execute(_NEWEST_RUN).first())
    heartbeat = _group(session, "heartbeat", lambda: session.execute(_HEARTBEAT).first())
    kill = _group(session, "kill", lambda: session.execute(_KILL).first())
    metrics = _group(session, "metrics", lambda: {
        row.name: float(row.value) for row in
        session.execute(_NEWEST_METRIC, {"since": now - TAPE_WINDOW})
        if row.value is not None}, default={})
    sweep = _group(session, "sweep",
                   lambda: [dict(row._mapping) for row in session.execute(_LATEST_SWEEP)],
                   default=[])
    settle = _group(session, "settle", lambda: [
        dict(row._mapping) for row in
        session.execute(_SETTLE_RUNS, {"since": now - timedelta(hours=24)})], default=[])
    floor_ms = _group(session, "floor_ms", lambda: [float(v) for v in session.execute(
        _FLOOR_MS, {"since": now - FLOOR_P95_WINDOW}).scalars()], default=[])
    snapshots = _group(session, "snapshots",
                       lambda: [dict(row._mapping) for row in session.execute(_SNAPSHOT_AGES)],
                       default=[])
    gaps_2h = _group(session, "gaps_2h", lambda: float(session.execute(
        _GAPS_2H, {"since": now - GAP_WINDOW}).scalar() or 0), default=0.0)
    housekeeping = _group(session, "housekeeping", lambda: _housekeeping_counts(session))
    credits_remaining = _group(session, "credits_remaining", lambda: session.execute(
        _NEWEST_CREDITS, {"since": now - TAPE_WINDOW}).scalar())
    games_live = _group(session, "games_live",
                        lambda: int(session.execute(_GAMES_LIVE).scalar() or 0), default=0)
    stopped = _group(session, "stopped", lambda: sorted(stopped_variants(session, now)),
                     default=[])
    # No query and no `_group`: this is a set in this process, written by the scheduler thread
    # that disabled the builder, and it cannot fail or be absent (fix 31).
    disabled = sorted(disabled_builders())
    drawdown_by_variant = _group(session, "drawdown_by_variant", lambda: {
        row.variant_id: row.drawdown_pct for row in
        session.execute(_NEWEST_DRAWDOWN, {"since": now - DRAWDOWN_WINDOW})}, default={})
    research_spend = _group(session, "research_spend",
                            lambda: spend_state(session, now, settings), default=None)
    veto_rate = _group(session, "veto_rate", lambda: _veto_rate(session, now), default=None)
    return {
        "now": now,
        "settings": settings,
        "run_age_s": (now - run.started_at).total_seconds() if run else None,
        "run_build_sha": run.build_sha if run else None,
        "heartbeat_age_s": (max(0.0, (now - heartbeat.last_loop_at).total_seconds())
                            if heartbeat and heartbeat.last_loop_at else None),
        "ws_event_age_s": (max(0.0, (now - heartbeat.ws_last_event_at).total_seconds())
                           if heartbeat and heartbeat.ws_last_event_at else None),
        "book_dirty_markets": heartbeat.book_dirty_markets if heartbeat else None,
        "executor_version": heartbeat.executor_version if heartbeat else None,
        "heartbeat": dict(heartbeat._mapping) if heartbeat else None,
        # `None`, not `False`, when the singleton row does not exist: the switch's default is off
        # and `init-db` writes the row, but a rule that renders green over a row nobody wrote is
        # the pattern this module's docstring refuses. `rule_kill_switch` reports it unevaluated.
        "kill_active": bool(kill.active) if kill else None,
        "gaps_2h": gaps_2h,
        "disk_free_gb": metrics.get("host.disk_free_gb"),
        "disk_total_gb": metrics.get("host.disk_total_gb"),
        "mem_available_mb": metrics.get("host.mem_available_mb"),
        "housekeeping": housekeeping,
        "credits_remaining": credits_remaining,
        "sweep": sweep,
        "settle": settle,
        "floor_ms": floor_ms,
        "snapshots": snapshots,
        "games_live": games_live,
        "stopped": stopped,
        "drawdown_by_variant": drawdown_by_variant,
        "disabled": disabled,
        "research_spend": research_spend,
        "veto_rate": veto_rate,
    }


# --- the rules, one named function each (spec §2.1) ------------------------------------------

def rule_recorder_stale(v) -> RuleResult:
    return _ladder("recorder_stale", v["run_age_s"], None, STALE_AFTER_S, "s")


def rule_heartbeat_watch(v) -> RuleResult:
    return _ladder("heartbeat_watch", v["heartbeat_age_s"], HEARTBEAT_WATCH_S, None, "s")


def rule_heartbeat_broken(v) -> RuleResult:
    return _ladder("heartbeat_broken", v["heartbeat_age_s"], None, HEARTBEAT_BROKEN_S, "s")


def rule_ws_event_watch(v) -> RuleResult:
    return _ladder("ws_event_watch", v["ws_event_age_s"], WS_EVENT_WATCH_S, None, "s")


def rule_ws_event_broken(v) -> RuleResult:
    return _ladder("ws_event_broken", v["ws_event_age_s"], None, WS_EVENT_BROKEN_S, "s")


def rule_tape_gap(v) -> RuleResult:
    return _flag("tape_gap", v["gaps_2h"] > 0, "broken", v["gaps_2h"], 0, "count")


def rule_kill_switch(v) -> RuleResult:
    active = v["kill_active"]
    return _flag("kill_switch", active, "broken", None if active is None else int(active), 0,
                 "count")


def rule_disk_free(v) -> RuleResult:
    free, total = v["disk_free_gb"], v["disk_total_gb"]
    if free is None or not total:
        return _absent("disk_free", DISK_FREE_MIN_FRACTION, "fraction")
    share = free / total
    level = "broken" if share < DISK_FREE_MIN_FRACTION else "fine"
    return RuleResult("disk_free", level, share, DISK_FREE_MIN_FRACTION, "fraction")


def rule_db_ceiling(v) -> RuleResult:
    counts = v["housekeeping"]
    budget = v["settings"].db_budget_gb
    if not counts or counts.get("size_gb") is None or not budget:
        return _absent("db_ceiling", DB_WATCH_FRACTION, "fraction")
    share = float(counts["size_gb"]) / budget
    return _ladder("db_ceiling", share, DB_WATCH_FRACTION, DB_BROKEN_FRACTION, "fraction")


def rule_credits_low(v) -> RuleResult:
    remaining, budget = v["credits_remaining"], v["settings"].odds_monthly_credits
    if remaining is None or not budget:
        return _absent("credits_low", CREDITS_WATCH_FRACTION, "fraction")
    share = float(remaining) / budget
    if share < CREDITS_LOW_FRACTION:
        return RuleResult("credits_low", "broken", share, CREDITS_LOW_FRACTION, "fraction")
    if share < CREDITS_WATCH_FRACTION:
        return RuleResult("credits_low", "watch", share, CREDITS_WATCH_FRACTION, "fraction")
    return RuleResult("credits_low", "fine", share, CREDITS_WATCH_FRACTION, "fraction")


def rule_check_fail(v) -> RuleResult:
    if not v["sweep"]:
        return _absent("check_fail", 0, "count")
    failed = sum(1 for row in v["sweep"] if row["status"] == "fail")
    return _flag("check_fail", failed > 0, "broken", failed, 0, "count")


def rule_check_skipped(v) -> RuleResult:
    """Ruling B-I1: a check recorded as `skip` timed out or raised. It has not passed, and the
    wall must not be green over it."""
    if not v["sweep"]:
        return _absent("check_skipped", 0, "count")
    skipped = sum(1 for row in v["sweep"] if row["status"] == "skip")
    return _flag("check_skipped", skipped > 0, "watch", skipped, 0, "count")


def rule_settle_error_24h(v) -> RuleResult:
    if not v["settle"]:
        return _absent("settle_error_24h", 0, "count")
    errors = sum(1 for row in v["settle"] if row["status"] == "error")
    return _flag("settle_error_24h", errors > 0, "broken", errors, 0, "count")


def rule_budget_exhausted(v) -> RuleResult:
    newest = v["settle"][:2]
    if len(newest) < 2:
        return _absent("budget_exhausted", 2, "count")
    both = all(row["budget_exhausted"] for row in newest)
    return _flag("budget_exhausted", both, "watch", sum(
        1 for row in newest if row["budget_exhausted"]), 2, "count")


def rule_book_dirty_in_game(v) -> RuleResult:
    dirty = v["book_dirty_markets"]
    if dirty is None:
        return _absent("book_dirty_in_game", 0, "count")
    return _flag("book_dirty_in_game", dirty > 0 and v["games_live"] > 0, "watch", dirty, 0,
                 "count")


def rule_drawdown_stop(v) -> RuleResult:
    """Addendum §0.3: WATCH while any variant's newest verdict is a stop; never BROKEN, because
    in paper the stop is information and the executor keeps placing (phase 4 decision 6).

    The value is the deepest *current* reading among the stopped variants -- each one's newest
    row, not the worst row of the window -- so the figure beside the stop is the one the stop was
    decided on. `None` when a stopped variant has no reading, which `pulse_rule_reading` prints as
    not evaluated rather than inventing a zero.
    """
    if not v["stopped"]:
        return RuleResult("drawdown_stop", "fine", 0.0, float(DRAWDOWN_STOP_PCT), "fraction")
    current = [float(v["drawdown_by_variant"][name]) for name in v["stopped"]
               if v["drawdown_by_variant"].get(name) is not None]
    return RuleResult("drawdown_stop", "watch", min(current) if current else None,
                      float(DRAWDOWN_STOP_PCT), "fraction")


def rule_snapshot_stale(v) -> RuleResult:
    """Two times its own cadence is a WATCH, three times a BROKEN (spec §4). This is also how a
    dead `app-serve` job is detected: nothing else notices a scheduler that stopped.

    Only the surfaces actually on a cadence are judged: the four fixed names plus the current ISO
    week's `study:` snapshot. A *closed* week is rebuilt when its report run changes, not on a
    clock (addendum §0.1), so its age is not a fault -- judging it would have put Pulse into a
    permanent BROKEN from the second week of operation, masking every real BROKEN behind it. The
    ages panel still lists every row, including the closed weeks.
    """
    year, week = chicago_iso_week(v["now"])
    judged = ({n for n in JUDGED_CADENCES if n != "study"}
              | {f"study:{year}-{week}"})
    rows = [r for r in v["snapshots"] if r["name"] in judged]
    if not rows:
        return _absent("snapshot_stale", 2, "count")
    worst_ratio, level = 0.0, "fine"
    for row in rows:
        cadence = JUDGED_CADENCES.get(row["name"].split(":")[0], 60)
        ratio = (v["now"] - row["generated_at"]).total_seconds() / cadence
        worst_ratio = max(worst_ratio, ratio)
    if worst_ratio >= 3:
        level = "broken"
    elif worst_ratio >= 2:
        level = "watch"
    return RuleResult("snapshot_stale", level, worst_ratio, 2.0, "count")


def rule_snapshot_budget(v) -> RuleResult:
    """Addendum §1 / ruling A-I9: the floor builder's p95 over the last 10 minutes against its
    share of the 2 s-per-minute CPU budget. The scheduler backs the in-window floor cadence off to
    30 s on the same condition; this is the rule that says so out loud.

    Strictly greater than, not `_ladder`'s `>=`: the scheduler backs off on `p95 > budget`, and at
    exactly the budget a `>=` here would have made the surface say WATCH while the scheduler did
    nothing. One number, one comparison.
    """
    samples = v["floor_ms"]
    if not samples:
        return _absent("snapshot_budget", FLOOR_P95_BUDGET_MS, "count")
    p95 = float(samples[min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))])
    level = "watch" if p95 > FLOOR_P95_BUDGET_MS else "fine"
    return RuleResult("snapshot_budget", level, p95, float(FLOOR_P95_BUDGET_MS), "count")


def rule_snapshot_disabled(v) -> RuleResult:
    """BROKEN while the scheduler has stopped running any builder (fix 31).

    Never `not_evaluated`: an empty set is a real reading -- nothing has been disabled -- rather
    than a measurement nobody took, which is the distinction the whole module turns on. The
    value is how many builders are off; *which* ones is in the `snapshots` section, one flagged
    row per builder, because that is where a reader is already looking at their ages.

    The re-enable is a restart of `app-serve` and nothing else, so this rule stays BROKEN until
    a person acts. That is the intent: a surface quietly frozen on its last payload is the
    failure this whole phase was written against.
    """
    names = v["disabled"]
    return _flag("snapshot_disabled", bool(names), "broken", float(len(names)), 0, "count")


def rule_research_budget(v) -> RuleResult:
    """WATCH while the research worker is dormant on the U4 caps (addendum §1.4).

    Never BROKEN: a dormant worker is the cap doing its job, and the phase ships with the veto
    shadow-only, so a day with no veto calls costs nothing but a gap in H9's panel. The value is
    today's actual spend and the threshold is the daily cap, so the sentence reads as money.
    """
    state = v["research_spend"]
    if state is None:
        return _absent("research_budget", None, "count")
    return RuleResult("research_budget", "watch" if state.dormant else "fine",
                      float(state.day_usd), float(state.daily_cap), "count")


def rule_veto_rate(v) -> RuleResult:
    """WATCH above `VETO_RATE_WATCH` of decided signals in 24 h (D19)."""
    return _ladder("veto_rate", v["veto_rate"], VETO_RATE_WATCH, None, "fraction")


RULES: tuple[Callable[[dict], RuleResult], ...] = (
    rule_recorder_stale, rule_heartbeat_watch, rule_heartbeat_broken, rule_ws_event_watch,
    rule_ws_event_broken, rule_tape_gap, rule_kill_switch, rule_disk_free, rule_db_ceiling,
    rule_credits_low, rule_check_fail, rule_check_skipped, rule_settle_error_24h,
    rule_budget_exhausted, rule_book_dirty_in_game, rule_drawdown_stop, rule_snapshot_stale,
    rule_snapshot_budget, rule_snapshot_disabled, rule_research_budget, rule_veto_rate,
)


def evaluate(values: dict) -> list[RuleResult]:
    return [rule(values) for rule in RULES]


def _tape(session: Session, now: datetime, gaps: float) -> dict:
    """The 24 h continuity strip: one bar per source, one bucket per 5 minutes, gaps drawn as
    breaks. `ws.events_per_min` is the socket; `recorder.fetched{source}` is odds, kalshi and
    espn. Bucket 0 is the most recent.

    `gaps` is `gather()`'s own `gaps_2h` (ruling A-M4): `_GAPS_2H` was running a second time
    here, once per build, for a number `gather` had already read."""
    buckets = int(TAPE_WINDOW.total_seconds() // 60 // TAPE_BUCKET_MIN)
    series: dict[str, list[float]] = {}
    for row in session.execute(_TAPE, {"now": now, "since": now - TAPE_WINDOW,
                                       "bucket": TAPE_BUCKET_MIN}):
        key = "ws events" if row.name == "ws.events_per_min" else sanitize_reason(
            row.source or "unknown")
        lane = series.setdefault(key, [0.0] * buckets)
        if 0 <= row.bucket < buckets:
            lane[row.bucket] += float(row.total or 0)
    return {"window_h": int(TAPE_WINDOW.total_seconds() // 3600),
            "bucket_min": TAPE_BUCKET_MIN,
            "sources": [{"source": name, "buckets": lane,
                         "gaps": gaps if name == "ws events" else 0}
                        for name, lane in sorted(series.items())]}


def _vitals(session: Session, now: datetime, values: dict) -> dict:
    """The stat tiles and their 24 h sparklines, from `metric_samples` alone.

    `_VITALS` returns newest-first so its `limit` drops the oldest points of the window; each
    lane is reversed here so the series still reads left to right (fix 31)."""
    lines: dict[str, list[list]] = {}
    for row in session.execute(_VITALS, {"since": now - TAPE_WINDOW, "limit": VITALS_LIMIT}):
        lines.setdefault(row.name, []).append(
            [row.ts.isoformat(), float(row.value) if row.value is not None else None])
    for lane in lines.values():
        lane.reverse()
    heartbeat = values["heartbeat"] or {}
    return {
        "sparklines": lines,
        # `technical` stays the glossary key a test checks it as (ruling B-C3); `metric` is the
        # separate `metric_samples` name whose sparkline the tile draws, or `None` when the tile
        # has no series of its own. The two used to be the same field doing two jobs: five of
        # six tiles looked up a `sparklines` key the builder never wrote.
        "tiles": [
            {"label": "executor heartbeat", "technical": "exec_heartbeat.last_loop_at",
             "metric": None,
             "value": values["heartbeat_age_s"], "unit": "s",
             "threshold": HEARTBEAT_WATCH_S},
            {"label": "loop time", "technical": "exec.p95_loop_ms",
             "metric": "exec.p95_loop_ms",
             "value": heartbeat.get("p95_loop_ms"), "unit": "ms", "threshold": None},
            {"label": "loops skipped", "technical": "exec_heartbeat.loops_skipped",
             "metric": "exec.loops_skipped",
             "value": heartbeat.get("loops_skipped"), "unit": "count", "threshold": None},
            {"label": "markets with a book we distrust", "technical": "book_dirty_markets",
             "metric": "exec.dirty_markets",
             "value": heartbeat.get("book_dirty_markets"), "unit": "count", "threshold": 0},
            {"label": "last exchange message", "technical": "ws_last_event_at",
             "metric": "exec.ws_event_age_s",
             "value": values["ws_event_age_s"], "unit": "s", "threshold": WS_EVENT_WATCH_S},
            {"label": "credits left this month", "technical": "runs.odds_remaining",
             "metric": "recorder.credits_remaining",
             "value": (float(values["credits_remaining"])
                       if values["credits_remaining"] is not None else None),
             "unit": "count", "threshold": None},
        ],
    }


def _storage(values: dict) -> dict:
    counts = values["housekeeping"] or {}
    budget = values["settings"].db_budget_gb
    size = counts.get("size_gb")
    return {"measured": bool(counts), "size_gb": size, "budget_gb": budget,
            "share": (float(size) / budget) if size is not None and budget else None,
            "watch_fraction": DB_WATCH_FRACTION, "broken_fraction": DB_BROKEN_FRACTION,
            "growth_gb_per_day": counts.get("growth_gb_per_day"),
            "days_to_ceiling": counts.get("days_to_ceiling"),
            "tables_gb": counts.get("tables_gb", {}),
            "disk_free_gb": values["disk_free_gb"], "disk_total_gb": values["disk_total_gb"],
            "disk_min_fraction": DISK_FREE_MIN_FRACTION,
            "mem_available_mb": values["mem_available_mb"]}


def _invariants(values: dict) -> dict:
    tiles = [{"check_name": row["check_name"], "status": row["status"],
              "value": float(row["value"]) if row["value"] is not None else None,
              "threshold": row["threshold"],
              "detail": sanitize_reason(row["detail"]) if row["detail"] else None}
             for row in values["sweep"]]
    return {"tiles": tiles,
            "n_pass": sum(1 for t in tiles if t["status"] == "pass"),
            "n_fail": sum(1 for t in tiles if t["status"] == "fail"),
            "n_skip": sum(1 for t in tiles if t["status"] == "skip")}


def _events(session: Session) -> list[dict]:
    return [{"ts": row.ts.isoformat(), "kind": row.kind,
             "summary": sanitize_reason(row.summary or "")}
            for row in session.execute(_EVENTS, {"limit": EVENTS_LIMIT})]


def _snapshots(values: dict) -> list[dict]:
    """Every row's age, including the closed `study:` weeks `rule_snapshot_stale` does not judge:
    the ages panel is the place an operator can see that a week was built and when.

    `disabled` is the row's own answer to "why has this stopped moving": the scheduler stopped
    running that builder, and the age beside it is growing for that reason rather than because a
    job crashed. It is per row, keyed on the builder, so every `study:` week reads disabled when
    Study is (fix 31)."""
    disabled = set(values["disabled"])
    out = []
    for row in values["snapshots"]:
        cadence = JUDGED_CADENCES.get(row["name"].split(":")[0], 60)
        out.append({"name": row["name"], "generated_at": row["generated_at"].isoformat(),
                    "age_s": (values["now"] - row["generated_at"]).total_seconds(),
                    "cadence_s": cadence, "elapsed_ms": row["elapsed_ms"],
                    "error": row["error"],
                    "disabled": builder_key(row["name"]) in disabled,
                    "disabled_over_ms": _DISABLE_MS,
                    "cell_age_s": row.get("cell_age_s")})
    return sorted(out, key=lambda r: r["name"])


def _research(session: Session, now: datetime, values: dict) -> dict:
    """The spend tile (addendum §3's walker item). Money and counts, never a model's words."""
    state = values["research_spend"]
    counts = session.execute(_RESEARCH_COUNTS, {
        "since": now - timedelta(hours=24),
        "week_start": now - timedelta(days=now.weekday()),
    }).first()
    decided = session.execute(_VETO_RATE, {"since": now - timedelta(hours=24)}).first()
    section = {
        "day_usd": None if state is None else float(state.day_usd),
        "day_reserved": None if state is None else float(state.day_reserved),
        "week_usd": None if state is None else float(state.week_usd),
        "daily_cap": None if state is None else float(state.daily_cap),
        "weekly_cap": None if state is None else float(state.weekly_cap),
        "dormant": None if state is None else bool(state.dormant),
        "veto_rate": values["veto_rate"],
        "decided_24h": int(decided.decided) if decided else 0,
        "rfq_quotes_24h": int(counts.rfq_quotes_24h) if counts else 0,
        "annotations_week": int(counts.annotations_week) if counts else 0,
    }
    # Fix round 2, I5: the card renders these two prose sentences rather than recomposing them,
    # which is also what makes `sentences.research_reading`/`veto_reading` reachable from
    # anything other than their own tests.
    section["sentence"] = sentences.research_reading(section)
    section["veto_sentence"] = sentences.veto_reading(section)
    return section


def build_pulse(session: Session, now: datetime, settings: Settings) -> dict:
    payload = base_payload("pulse", now, settings, CADENCE_S)
    values = gather(session, now, settings)
    rules = evaluate(values)
    fired = [r for r in rules if r.level in ("watch", "broken")]
    unevaluated = [r for r in rules if r.level == "not_evaluated"]

    payload["status"] = {"status": status_word(rules),
                         "rules": [r.as_dict() for r in fired],
                         "not_evaluated": [r.as_dict() for r in unevaluated],
                         "all": [r.as_dict() for r in rules]}
    section(session, payload, "tape", lambda: _tape(session, now, values["gaps_2h"]))
    section(session, payload, "vitals", lambda: _vitals(session, now, values))
    section(session, payload, "storage", lambda: _storage(values))
    section(session, payload, "invariants", lambda: _invariants(values))
    section(session, payload, "operator_events", lambda: _events(session))
    section(session, payload, "snapshots", lambda: _snapshots(values))
    section(session, payload, "research", lambda: _research(session, now, values))
    payload["build"] = {"recorder_build_sha": values["run_build_sha"],
                        "executor_version": values["executor_version"],
                        "serving_build_sha": settings.build_sha,
                        "agree": (values["run_build_sha"] == settings.build_sha)}
    # `pulse_tape` is handed the section as it stands, error dict included: it reads `sources` with
    # a default and answers a payload without one with its no-activity line, which is the honest
    # sentence for a tape section that failed to build.
    payload["sentences"] = {
        "status": sentences.pulse_status(payload["status"]),
        "tape": sentences.pulse_tape(payload["tape"]),
    }
    payload["readings"] = {"rules": [sentences.pulse_rule_reading(r.as_dict()) for r in rules]}
    return payload


register_builder("pulse", build_pulse)
