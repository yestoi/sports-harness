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
from datetime import datetime
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


def _zero(value: object) -> bool:
    return value is not None and float(value) == 0.0


CHECKS: list[Check] = [
    Check(
        "duplicate_trades",
        # Bounded to the current weekly partition (F19): venue_trades is partitioned on ts,
        # and a scan across every partition ever taped would be exactly the bulk-table cost
        # this registry exists to avoid.
        """
        select count(*) from (
            select venue, trade_id, count(*) as c
            from venue_trades
            where ts >= date_trunc('week', now())
            group by venue, trade_id
            having count(*) > 1
        ) d
        """,
        "== 0", _zero),
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
        "select count(*) from fair_values where staleness_s < 0",
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
                value = session.execute(text(check.sql)).scalar()
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
