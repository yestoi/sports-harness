"""Layer 2b invariants as data (`docs/superpowers/autopilot/verify.md`), run daily by the
housekeeping stage and recorded one row per check to `check_results` (design spec §3.6).

Every check is read-only and bounded: none names the two bulk tape tables at all
(`orderbook_events`, `raw_responses`), the one check that reads `venue_trades` is bounded to
the current weekly partition, and `run_checks` wraps each one in its own 2 second
`SET LOCAL statement_timeout` so a check over a bulk table can never stall housekeeping --
a timeout is recorded as `skip`, not as a failure of the whole stage.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import CheckResult

log = logging.getLogger(__name__)

#: Held as a module attribute (not a bare literal in `run_checks`) so a test can lower it to
#: force a real timeout without needing a slow fixture query.
STATEMENT_TIMEOUT_MS = 2000

_FORBIDDEN_TABLES = ("orderbook_events", "raw_responses")


@dataclass(frozen=True)
class Check:
    name: str
    sql: str
    threshold: str
    ok: Callable[[object], bool]
    #: Carried fix 16: a check whose SQL must name the current weekly partition by name so the
    #: planner prunes at plan time rather than scanning every attached partition. `sql` stays
    #: the representative static text (assert_no_tape_reads and verify.md read it); `run_checks`
    #: executes `sql_for(now)` when it is set.
    sql_for: Callable[[datetime], str] | None = None


def _zero(value: object) -> bool:
    return value is not None and float(value) == 0.0


def current_trades_partition(now: datetime) -> str:
    """The `venue_trades` weekly partition holding `now`, named exactly as
    `harness.db.schema._partition_name` names it."""
    from harness.db.schema import week_bounds

    start, _ = week_bounds(now)
    iso = start.isocalendar()
    return f"venue_trades_y{iso.year}w{iso.week:02d}"


def _duplicate_trades_sql(now: datetime) -> str:
    return f"""
        select count(*) from (
            select venue, trade_id
            from {current_trades_partition(now)}
            group by venue, trade_id
            having count(*) > 1
        ) d
    """


CHECKS: list[Check] = [
    Check(
        "duplicate_trades",
        # Static text for assert_no_tape_reads and for verify.md's copy; `sql_for` is what runs.
        # Carried fix 16: `from venue_trades where ts >= date_trunc('week', now())` still made
        # the planner touch every attached partition, which exceeded the 2 s check timeout on
        # NAS-sized tape. Naming the partition in Python prunes at plan time and lets the
        # partition's own unique `(venue, trade_id)` index answer the grouping.
        """
        select count(*) from (
            select venue, trade_id, count(*) as c
            from venue_trades
            where ts >= date_trunc('week', now())
            group by venue, trade_id
            having count(*) > 1
        ) d
        """,
        "== 0", _zero, sql_for=_duplicate_trades_sql),
    Check(
        "clv_p_used_matches_order_prob",
        # order_clv.p_used_kind = 'order' means p_used was copied from the order's own price
        # (harness/settlement/order_clv.py compute_order_clv); it must still equal it.
        """
        select count(*) from order_clv c
        join orders o on o.id = c.order_id
        where c.p_used_kind = 'order' and c.p_used <> o.prob
        """,
        "== 0", _zero),
    Check(
        "settle_errors_24h",
        "select count(*) from job_runs "
        "where job = 'settle' and started_at >= now() - interval '24 hours' and status = 'error'",
        "== 0", _zero),
    Check(
        "derived_without_venue_row_48h",
        # R11: every derived settlement should pick up the venue's own result within a couple
        # of days; one still missing after 48h is a settlement pass that has been failing
        # quietly on that ticker.
        """
        select count(*) from venue_settlements d
        where d.source = 'derived' and d.settled_at < now() - interval '48 hours'
          and not exists (
            select 1 from venue_settlements v
            where v.venue = d.venue and v.ticker = d.ticker and v.source = 'venue'
          )
        """,
        "== 0", _zero),
    Check(
        "build_sha_drift",
        # verify.md's "Build stamp" plausibility band as an invariant: every recorder run in
        # the last 24h should carry the same build_sha as the newest one, or a container is
        # still running an old image.
        """
        select count(*) from runs
        where started_at >= now() - interval '24 hours' and build_sha is not null
          and build_sha <> (select build_sha from runs where build_sha is not null
                            order by id desc limit 1)
        """,
        "== 0", _zero),
    Check(
        "orders_open_past_expiry",
        # A 5 minute buffer past `expiry`, not zero: the executor cancels/expires an order on
        # its own 15 s loop, so a handful of seconds of lag is normal and only a stuck loop
        # should trip this.
        """
        select count(*) from orders
        where status in ('open', 'partially_filled') and replay = false
          and expiry is not null and expiry < now() - interval '5 minutes'
        """,
        "== 0", _zero),
    Check(
        "fills_without_print",
        # verify.md Layer 2b, verbatim: a queue-model fill older than 2 minutes with no print
        # behind it is a sanity failure of the fill simulator, not evidence of realism (F12).
        """
        select count(*) from fills f
        where f.replay = false and f.fill_method = 'queue_model' and f.has_print = false
          and f.filled_at < now() - interval '2 minutes'
        """,
        "== 0", _zero),
    Check(
        "gate_rows_one_gate_variant",
        # Exactly one gate_reports row per evaluation carries gate_variant = true
        # (harness/report/gate.py gate_row_variant); zero or more than one is a bug in that
        # marking, not a legitimate evaluation.
        """
        select count(*) from (
            select evaluated_at
            from gate_reports
            group by evaluated_at
            having count(*) filter (where gate_variant) <> 1
        ) g
        """,
        "== 1 per evaluation", _zero),
    # --- Fix round 1, coverage: every Layer 2b invariant statement in verify.md:127-144,
    # not only the eight the brief's own parenthetical names. `fills_without_print` above is
    # already one of the thirteen (verbatim); these eleven are the rest.
    Check(
        "fair_values_negative_staleness",
        # Carried fix 16: unbounded, this scanned 475 MB and timed out. The 24 h bound rides
        # the additive `ix_fair_created_brin` (harness/db/schema.py); `ix_fair_game_type_created`
        # leads on game_id and cannot serve a bare created_at predicate.
        "select count(*) from fair_values "
        "where created_at > now() - interval '24 hours' and staleness_s < 0",
        "== 0", _zero),
    Check(
        "runs_taker_side_missing_24h",
        "select count(*) from runs where started_at > now() - interval '24 hours' "
        "and coalesce((notes->>'taker_side_missing')::int, 0) > 0",
        "== 0", _zero),
    Check(
        "orders_without_place_event",
        "select count(*) from orders o where replay = false and not exists "
        "(select 1 from order_events e where e.order_id = o.id and e.kind = 'place')",
        "== 0", _zero),
    Check(
        "intents_without_order_or_skip",
        """
        select count(*) from intents i
        where i.replay = false and i.created_at < now() - interval '2 minutes'
          and not exists (select 1 from orders o where o.intent_id = i.id)
          and not exists (select 1 from order_events e
                          where e.intent_id = i.id and e.kind = 'skipped')
        """,
        "== 0", _zero),
    Check(
        "fill_contracts_exceed_order_contracts",
        "select count(*) from fills f join orders o on o.id = f.order_id "
        "where f.replay = false and f.contracts > o.contracts",
        "== 0", _zero),
    Check(
        "orders_filled_exceeds_contracts",
        "select count(*) from orders where replay = false and filled_contracts > contracts",
        "== 0", _zero),
    Check(
        "settlement_result_mismatch",
        # Distinct from derived_without_venue_row_48h above: this is the pair actually
        # disagreeing, not one side missing.
        """
        select count(*) from venue_settlements d
        join venue_settlements v on v.venue = d.venue and v.ticker = d.ticker
        where d.source = 'derived' and v.source = 'venue' and d.result <> v.result
        """,
        "== 0", _zero),
    Check(
        "fair_values_negative_feed_lag",
        "select count(*) from fair_values where feed_lag_s < 0",
        "== 0", _zero),
    Check(
        "benchmarks_source_after_target",
        "select count(*) from benchmarks where source_ts > target_ts",
        "== 0", _zero),
    Check(
        "fills_outside_placement_window",
        """
        select count(*) from fills f
        join orders o on o.id = f.order_id
        join games g on g.id = o.game_id
        where f.replay = false
          and (f.filled_at < o.placed_at or f.filled_at > g.kickoff_utc - interval '10 minutes')
        """,
        "== 0", _zero),
    Check(
        "markouts_at_after_horizon",
        "select count(*) from markouts where at_ts > horizon_ts",
        "== 0", _zero),
    # --- Final fix wave, I1: the eight Task 12b telemetry statements (verify.md:191-208).
    # Task 12b built this registry against verify.md:145-190; Task 14 then extended the file
    # with one invariant per new telemetry table, and until these landed "every check passed"
    # was no longer "every Layer 2b invariant in verify.md is zero". Each is bounded the same
    # way verify.md writes it: a 24 h (25 h for the daily check sweep) `ts` window on the
    # append-only sample tables, and nothing at all on `report_runs`/`report_cells`, which
    # gain a handful of rows a week.
    Check(
        "metric_samples_negative_24h",
        "select count(*) from metric_samples "
        "where ts > now() - interval '24 hours' and value < 0",
        "== 0", _zero),
    Check(
        "operator_events_empty_summary_24h",
        "select count(*) from operator_events "
        "where ts > now() - interval '24 hours' and trim(summary) = ''",
        "== 0", _zero),
    Check(
        "order_watch_negative_queue_24h",
        """
        select count(*) from order_watch_samples
        where ts > now() - interval '24 hours'
          and (queue_remaining < 0 or nw_queue_remaining < 0)
        """,
        "== 0", _zero),
    Check(
        "equity_mtm_coverage_out_of_range_24h",
        """
        select count(*) from equity_snapshots
        where ts > now() - interval '24 hours' and mtm_coverage is not null
          and (mtm_coverage < 0 or mtm_coverage > 1)
        """,
        "== 0", _zero),
    Check(
        "game_score_went_down_24h",
        # A game's score can never go down: a later event carrying a lower home or away score
        # than one of its own game's earlier events is a normalizer bug, not a comeback. The
        # inner scan is bounded by `p.game_id = e.game_id` on the same 24 h slice.
        """
        select count(*) from game_score_events e
        where e.ts > now() - interval '24 hours'
          and exists (select 1 from game_score_events p
                      where p.game_id = e.game_id and p.ts < e.ts
                        and (p.home_score > e.home_score or p.away_score > e.away_score))
        """,
        "== 0", _zero),
    Check(
        "check_results_unknown_status_25h",
        # 25 h, not 24: the sweep is daily, so a 24 h window can miss the previous run by
        # minutes. Same window verify.md's own "all pass" query uses.
        "select count(*) from check_results "
        "where ts > now() - interval '25 hours' and status not in ('pass', 'fail', 'skip')",
        "== 0", _zero),
    Check(
        "report_runs_generated_in_future",
        # Small table (one provisional row per report_wtd pass plus one per weekly report),
        # so no window is needed to keep it bounded.
        "select count(*) from report_runs where generated_at > now()",
        "== 0", _zero),
    Check(
        "report_cells_orphan",
        # Also small, and also unwindowed: an orphan cell is a persist_report that wrote its
        # cells without their run, which no window would be allowed to age out of view.
        """
        select count(*) from report_cells rc
        where not exists (select 1 from report_runs rr where rr.id = rc.report_run_id)
        """,
        "== 0", _zero),
]


def assert_no_tape_reads(checks: list[Check]) -> None:
    """The static guarantee ruling 2 requires: no check's SQL names the two bulk tape tables at
    all, and any check over `venue_trades` carries the current-week partition bound in its own
    text. Raises on the first violation; called at import so a future check that forgets the
    bound breaks the build, not just a test run."""
    for check in checks:
        lowered = check.sql.lower()
        for forbidden in _FORBIDDEN_TABLES:
            if forbidden in lowered:
                raise ValueError(f"check {check.name!r} reads {forbidden}, which no check may")
        if "venue_trades" in lowered and "date_trunc('week'" not in lowered:
            raise ValueError(f"check {check.name!r} reads venue_trades unbounded by week")
        if check.sql_for is not None:
            probe = check.sql_for(datetime(2026, 1, 5, tzinfo=timezone.utc)).lower()
            for forbidden in _FORBIDDEN_TABLES:
                if forbidden in probe:
                    raise ValueError(f"check {check.name!r} reads {forbidden}, which no check may")


assert_no_tape_reads(CHECKS)


def run_checks(session: Session, now: datetime, job_run_id: int,
              checks: list[Check] | None = None) -> list[CheckResult]:
    """Run every check under its own statement timeout and write one `check_results` row each.

    Each check runs inside its own savepoint so one failing or timing-out statement cannot take
    the others down with it: a timeout (Postgres `query_canceled`) records `status = 'skip'`
    with `detail = 'timeout'`; any other failure is also recorded as a skip (ruling 1: a
    telemetry failure must never fail the housekeeping stage) with the exception in `detail`.
    """
    results: list[CheckResult] = []
    for check in checks if checks is not None else CHECKS:
        value = None
        detail: str | None = None
        try:
            with session.begin_nested():
                session.execute(text(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}"))
                sql = check.sql_for(now) if check.sql_for is not None else check.sql
                value = session.execute(text(sql)).scalar()
            status = "pass" if check.ok(value) else "fail"
        except Exception as exc:  # noqa: BLE001 - one check must not cost the stage
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if sqlstate == "57014":  # query_canceled: our own SET LOCAL statement_timeout
                status, detail = "skip", "timeout"
            else:
                status, detail = "skip", f"{type(exc).__name__}: {exc}"[:200]
                log.exception("check %s failed", check.name)
        results.append(CheckResult(job_run_id=job_run_id, ts=now, check_name=check.name,
                                   status=status, value=value, threshold=check.threshold,
                                   detail=detail))
    for row in results:
        session.add(row)
    session.flush()
    # Fix round 1, M1: `SET LOCAL` inside a savepoint that RELEASEs (a check that neither
    # errored nor timed out) survives the release and would otherwise keep the 2 s timeout in
    # effect for the rest of housekeeping's transaction. Reset it once, outside every savepoint,
    # so nothing after this call runs under a timeout it never asked for.
    try:
        session.execute(text("set statement_timeout = default"))
    except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails the stage
        log.exception("resetting statement_timeout after run_checks failed")
    return results
