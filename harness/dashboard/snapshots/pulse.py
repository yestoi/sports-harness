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
from harness.dashboard.snapshots import base_payload, register_builder, section
from harness.execution.risk import DRAWDOWN_STOP_PCT, stopped_variants
from harness.health import (CREDITS_LOW_FRACTION, CREDITS_WATCH_FRACTION, DB_BROKEN_FRACTION,
                            DB_WATCH_FRACTION, DISK_FREE_MIN_FRACTION, HEARTBEAT_BROKEN_S,
                            HEARTBEAT_WATCH_S, STALE_AFTER_S, WS_EVENT_BROKEN_S,
                            WS_EVENT_WATCH_S)
from harness.telemetry import sanitize_reason

log = logging.getLogger(__name__)

CADENCE_S = 30
#: The tape strip's window and its bucket size (spec §2.1 item 2).
TAPE_WINDOW = timedelta(hours=24)
TAPE_BUCKET_MIN = 5
#: The gap rule's window (spec §2.1: "any `gap` row in the last 2 h", re-sourced to
#: `sum(ws.gaps)` from metric_samples by ruling A-I2).
GAP_WINDOW = timedelta(hours=2)
#: The floor builder's own budget, and the window its p95 is taken over (addendum §1, A-I9).
FLOOR_P95_BUDGET_MS = 250
FLOOR_P95_WINDOW = timedelta(minutes=10)
#: How many operator events the surface shows (spec §2.1 item 6).
EVENTS_LIMIT = 10
#: The cadence each snapshot's staleness is judged against, so the rule can say 2x and 3x.
#: Deliberately the *out-of-window* cadence for floor and ticket, not the in-window one: a
#: scheduler that flips floor to 15 s would otherwise make a healthy 40 s-old snapshot read as
#: stale the moment a game ended. The cost is that a dead floor job is noticed at 120 s rather
#: than at 30 s, which is inside the window the recorder's own staleness rule already covers.
#: `harness/dashboard/scheduler.py` has its own CADENCES: those are the intervals jobs run at.
CADENCES = {"pulse": 30, "floor": 60, "gate": 60, "ticket": 60, "study": 600}

PULSE_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                        "status", "tape", "vitals", "storage", "invariants",
                        "operator_events", "snapshots", "build"})

LEVEL_RANK = {"fine": 0, "not_evaluated": 0, "watch": 1, "broken": 2}
_WORDS = {0: "FINE", 1: "WATCH", 2: "BROKEN"}


@dataclass(frozen=True)
class RuleResult:
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


_NEWEST_RUN = text("""
    select started_at, status, build_sha, odds_remaining, budget_exhausted
    from runs order by id desc limit 1
""")
_NEWEST_CREDITS = text("""
    select odds_remaining from runs where odds_remaining is not null order by id desc limit 1
""")
_HEARTBEAT = text("""
    select last_loop_at, ws_last_event_at, loops, loops_skipped, open_orders, last_loop_ms,
           p95_loop_ms, book_dirty_markets, executor_version, last_error
    from exec_heartbeat where id = 1
""")
_KILL = text("select active, reason, set_at from kill_switch where id = 1")
_GAPS_2H = text("""
    select coalesce(sum(value), 0) from metric_samples
    where name = 'ws.gaps' and ts > :since
""")
_NEWEST_METRIC = text("""
    select distinct on (name) name, value, ts from metric_samples
    where name in ('host.disk_free_gb', 'host.disk_total_gb', 'db.size_gb',
                   'host.mem_available_mb')
      and ts > :since
    order by name, ts desc
""")
_TAPE = text("""
    select name, labels->>'source' as source,
           floor(extract(epoch from (:now - ts)) / (:bucket * 60))::int as bucket,
           sum(value) as total
    from metric_samples
    where name in ('ws.events_per_min', 'recorder.fetched') and ts > :since
    group by 1, 2, 3
""")
_VITALS = text("""
    select name, labels, value, ts from metric_samples
    where name in ('exec.loop_ms', 'exec.p95_loop_ms', 'exec.loops_skipped',
                   'exec.dirty_markets', 'exec.ws_event_age_s', 'ws.events_per_min',
                   'ws.trades_per_min', 'ws.reconnects', 'recorder.credits_remaining')
      and ts > :since
    order by ts
""")
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
_SETTLE_RUNS = text("""
    select status, budget_exhausted, started_at from job_runs
    where job = 'settle' and started_at > :since
    order by started_at desc limit 10
""")
_HOUSEKEEPING = text("""
    select notes from job_runs where job = 'settle' order by started_at desc limit 30
""")
_EVENTS = text("""
    select ts, kind, summary from operator_events order by ts desc limit :limit
""")
_SNAPSHOT_AGES = text("select name, generated_at, elapsed_ms, error from dashboard_snapshots")
_GAMES_LIVE = text("select count(*) from games where status = 'in_progress'")
_WORST_DRAWDOWN = text("""
    select min(drawdown_pct) from equity_snapshots
    where ts >= :since and drawdown_stop = true
""")


def _housekeeping_counts(session: Session) -> dict | None:
    """The newest `housekeeping` stage note that actually ran, over a bounded scan of recent
    settlement passes -- the same read the legacy page's database-ceiling section does."""
    for notes in session.execute(_HOUSEKEEPING).scalars():
        for stage in (notes or {}).get("stages", []):
            if stage.get("name") == "housekeeping" and "size_gb" in (stage.get("counts") or {}):
                return stage["counts"]
    return None


def gather(session: Session, now: datetime, settings: Settings) -> dict:
    """Every value the rules read, one bounded query each.

    `recorder` is read here rather than through `harness.health.compute_health`, which takes a
    `sessionmaker` and would check out a second connection from a pool of two while a build is
    already holding one. The staleness rule itself is not re-derived: `STALE_AFTER_S` is
    imported from the same module `compute_health` enforces it in, so the two cannot disagree
    about when a recorder is stale.
    """
    run = session.execute(_NEWEST_RUN).first()
    heartbeat = session.execute(_HEARTBEAT).first()
    kill = session.execute(_KILL).first()
    metrics = {row.name: float(row.value) for row in
               session.execute(_NEWEST_METRIC, {"since": now - TAPE_WINDOW})
               if row.value is not None}
    sweep = [dict(row._mapping) for row in session.execute(_LATEST_SWEEP)]
    settle = [dict(row._mapping) for row in
              session.execute(_SETTLE_RUNS, {"since": now - timedelta(hours=24)})]
    floor_ms = [float(v) for v in session.execute(
        _FLOOR_MS, {"since": now - FLOOR_P95_WINDOW}).scalars()]
    snapshots = [dict(row._mapping) for row in session.execute(_SNAPSHOT_AGES)]
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
        "kill_active": bool(kill.active) if kill else False,
        "gaps_2h": float(session.execute(
            _GAPS_2H, {"since": now - GAP_WINDOW}).scalar() or 0),
        "disk_free_gb": metrics.get("host.disk_free_gb"),
        "disk_total_gb": metrics.get("host.disk_total_gb"),
        "mem_available_mb": metrics.get("host.mem_available_mb"),
        "housekeeping": _housekeeping_counts(session),
        "credits_remaining": session.execute(_NEWEST_CREDITS).scalar(),
        "sweep": sweep,
        "settle": settle,
        "floor_ms": floor_ms,
        "snapshots": snapshots,
        "games_live": int(session.execute(_GAMES_LIVE).scalar() or 0),
        "stopped": sorted(stopped_variants(session, now)),
        "drawdown_pct": session.execute(
            _WORST_DRAWDOWN, {"since": now - timedelta(days=7)}).scalar(),
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
    return _flag("kill_switch", v["kill_active"], "broken", 1 if v["kill_active"] else 0, 0,
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
    in paper the stop is information and the executor keeps placing (phase 4 decision 6)."""
    if not v["stopped"]:
        return RuleResult("drawdown_stop", "fine", 0.0, float(DRAWDOWN_STOP_PCT), "fraction")
    worst = float(v["drawdown_pct"]) if v["drawdown_pct"] is not None else None
    return RuleResult("drawdown_stop", "watch", worst, float(DRAWDOWN_STOP_PCT), "fraction")


def rule_snapshot_stale(v) -> RuleResult:
    """Two times its own cadence is a WATCH, three times a BROKEN (spec §4). This is also how a
    dead `app-serve` job is detected: nothing else notices a scheduler that stopped."""
    if not v["snapshots"]:
        return _absent("snapshot_stale", 2, "count")
    worst_ratio, level = 0.0, "fine"
    for row in v["snapshots"]:
        cadence = CADENCES.get(row["name"].split(":")[0], 60)
        ratio = (v["now"] - row["generated_at"]).total_seconds() / cadence
        worst_ratio = max(worst_ratio, ratio)
    if worst_ratio >= 3:
        level = "broken"
    elif worst_ratio >= 2:
        level = "watch"
    return RuleResult("snapshot_stale", level, worst_ratio, 2.0, "count")


def rule_snapshot_budget(v) -> RuleResult:
    """Addendum §1 / ruling A-I9: the floor builder's p95 over the last 10 minutes against its
    250 ms share of the 2 s-per-minute CPU budget. The scheduler backs the in-window floor
    cadence off to 30 s on the same condition; this is the rule that says so out loud."""
    samples = v["floor_ms"]
    if not samples:
        return _absent("snapshot_budget", FLOOR_P95_BUDGET_MS, "count")
    p95 = samples[min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))]
    return _ladder("snapshot_budget", p95, FLOOR_P95_BUDGET_MS, None, "count")


RULES: tuple[Callable[[dict], RuleResult], ...] = (
    rule_recorder_stale, rule_heartbeat_watch, rule_heartbeat_broken, rule_ws_event_watch,
    rule_ws_event_broken, rule_tape_gap, rule_kill_switch, rule_disk_free, rule_db_ceiling,
    rule_credits_low, rule_check_fail, rule_check_skipped, rule_settle_error_24h,
    rule_budget_exhausted, rule_book_dirty_in_game, rule_drawdown_stop, rule_snapshot_stale,
    rule_snapshot_budget,
)


def evaluate(values: dict) -> list[RuleResult]:
    return [rule(values) for rule in RULES]


def _tape(session: Session, now: datetime) -> dict:
    """The 24 h continuity strip: one bar per source, one bucket per 5 minutes, gaps drawn as
    breaks. `ws.events_per_min` is the socket; `recorder.fetched{source}` is odds, kalshi and
    espn. Bucket 0 is the most recent."""
    buckets = int(TAPE_WINDOW.total_seconds() // 60 // TAPE_BUCKET_MIN)
    series: dict[str, list[float]] = {}
    for row in session.execute(_TAPE, {"now": now, "since": now - TAPE_WINDOW,
                                       "bucket": TAPE_BUCKET_MIN}):
        key = "ws events" if row.name == "ws.events_per_min" else sanitize_reason(
            row.source or "unknown")
        lane = series.setdefault(key, [0.0] * buckets)
        if 0 <= row.bucket < buckets:
            lane[row.bucket] += float(row.total or 0)
    gaps = float(session.execute(_GAPS_2H, {"since": now - GAP_WINDOW}).scalar() or 0)
    return {"window_h": int(TAPE_WINDOW.total_seconds() // 3600),
            "bucket_min": TAPE_BUCKET_MIN,
            "sources": [{"source": name, "buckets": lane,
                         "gaps": gaps if name == "ws events" else 0}
                        for name, lane in sorted(series.items())]}


def _vitals(session: Session, now: datetime, values: dict) -> dict:
    """The stat tiles and their 24 h sparklines, from `metric_samples` alone."""
    lines: dict[str, list[list]] = {}
    for row in session.execute(_VITALS, {"since": now - TAPE_WINDOW}):
        lines.setdefault(row.name, []).append(
            [row.ts.isoformat(), float(row.value) if row.value is not None else None])
    heartbeat = values["heartbeat"] or {}
    return {
        "sparklines": lines,
        "tiles": [
            {"label": "executor heartbeat", "technical": "exec_heartbeat.last_loop_at",
             "value": values["heartbeat_age_s"], "unit": "s",
             "threshold": HEARTBEAT_WATCH_S},
            {"label": "loop time", "technical": "exec.p95_loop_ms",
             "value": heartbeat.get("p95_loop_ms"), "unit": "ms", "threshold": None},
            {"label": "loops skipped", "technical": "exec_heartbeat.loops_skipped",
             "value": heartbeat.get("loops_skipped"), "unit": "count", "threshold": None},
            {"label": "markets with a book we distrust", "technical": "book_dirty_markets",
             "value": heartbeat.get("book_dirty_markets"), "unit": "count", "threshold": 0},
            {"label": "last exchange message", "technical": "ws_last_event_at",
             "value": values["ws_event_age_s"], "unit": "s", "threshold": WS_EVENT_WATCH_S},
            {"label": "credits left this month", "technical": "runs.odds_remaining",
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
    out = []
    for row in values["snapshots"]:
        cadence = CADENCES.get(row["name"].split(":")[0], 60)
        out.append({"name": row["name"], "generated_at": row["generated_at"].isoformat(),
                    "age_s": (values["now"] - row["generated_at"]).total_seconds(),
                    "cadence_s": cadence, "elapsed_ms": row["elapsed_ms"],
                    "error": row["error"]})
    return sorted(out, key=lambda r: r["name"])


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
    section(payload, "tape", lambda: _tape(session, now))
    section(payload, "vitals", lambda: _vitals(session, now, values))
    section(payload, "storage", lambda: _storage(values))
    section(payload, "invariants", lambda: _invariants(values))
    section(payload, "operator_events", lambda: _events(session))
    section(payload, "snapshots", lambda: _snapshots(values))
    payload["build"] = {"recorder_build_sha": values["run_build_sha"],
                        "executor_version": values["executor_version"],
                        "serving_build_sha": settings.build_sha,
                        "agree": (values["run_build_sha"] == settings.build_sha)}
    payload["sentences"] = {
        "status": sentences.pulse_status(payload["status"]),
        "tape": sentences.pulse_tape(payload["tape"] if isinstance(payload["tape"], dict) else {}),
    }
    payload["readings"] = {"rules": [sentences.pulse_rule_reading(r.as_dict()) for r in rules]}
    return payload


register_builder("pulse", build_pulse)
