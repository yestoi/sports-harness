"""Wires the pricing and strategy stages together for one run: fair values, gap snapshots,
then every active variant scored against this run's gaps, with signals persisted.

Runs inside the tick (spec 9.1-9.6) once Kalshi markets have refreshed for the tick, and via
`harness price-once` for manual checks. A `budget_s` wall-clock deadline is checked between
stages and between variants; when it is spent, remaining work is skipped and the result records
`budget_exhausted: True`. Stage 1 (fair values) always runs regardless of the budget.
"""

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.db.models import Game, MarketGapSnapshot, Signal, VenueMarket
from harness.pricing.fair import compute_fair_values
from harness.pricing.gaps import build_gap_snapshots
from harness.strategy.as_measured import as_measured_table
from harness.strategy.run import GapRow, run_strategy
from harness.strategy.variants import Variant, active_variants

#: Rows per INSERT statement in `_insert_signals`. A signal binds 21 parameters, and
#: psycopg refuses a statement with more than 65535 of them, so a single multi-VALUES
#: insert dies above 3120 signals -- which a variant clears every tick now that there
#: are thousands of matched venue markets. 1000 rows is 21,000 parameters a statement.
SIGNAL_INSERT_CHUNK = 1000

log = logging.getLogger(__name__)


def _load_gap_rows(session: Session, run_id: int) -> list[GapRow]:
    stmt = (
        select(MarketGapSnapshot, VenueMarket, Game)
        .join(VenueMarket, VenueMarket.id == MarketGapSnapshot.venue_market_id)
        .join(Game, Game.id == VenueMarket.game_id)
        .where(MarketGapSnapshot.run_id == run_id)
        .order_by(MarketGapSnapshot.id)
    )
    rows = []
    for snap, market, game in session.execute(stmt).all():
        rows.append(GapRow(
            gap_snapshot_id=snap.id,
            venue_market_id=market.id,
            sport=game.sport,
            game_id=game.id,
            market_type=market.market_type,
            side_team_id=market.side_team_id,
            threshold=market.threshold,
            fair_p=snap.fair_p,
            fair_source=snap.fair_source,
            disagreement=snap.disagreement,
            n_groups=snap.n_groups,
            staleness_s=snap.staleness_s,
            prev_fair_p=snap.prev_fair_p,
            prev_fair_ts=snap.prev_fair_ts,
            best_bid=snap.best_bid,
            best_ask=snap.best_ask,
            bid_size=snap.bid_size,
            ask_size=snap.ask_size,
            ttk_minutes=snap.ttk_minutes,
            volume_24h=snap.volume_24h,
            open_interest=snap.open_interest,
            venue_mid=snap.venue_mid,
            match_status=market.match_status,
            stale_allowance_s=snap.stale_allowance_s,
            feed_kind=snap.feed_kind,
            price_ranges=market.price_ranges,
        ))
    return rows


def _insert_signals(
    session: Session, run_id: int, variant: Variant, now: datetime, signals: list, replay: bool = False
) -> int:
    if not signals:
        return 0
    values = [
        dict(
            run_id=run_id,
            variant_id=variant.variant_id,
            gap_snapshot_id=s.gap_snapshot_id,
            venue_market_id=s.venue_market_id,
            side=s.side,
            fair_p=s.fair_p,
            fair_source=s.fair_source,
            venue_best_bid=s.venue_best_bid,
            venue_best_ask=s.venue_best_ask,
            price_target=s.price_target,
            fee_at_target=s.fee_at_target,
            as_estimate=s.as_estimate,
            edge=s.edge,
            edge_min=s.edge_min,
            stake=s.stake,
            contracts=s.contracts,
            decision=s.decision,
            rejection_reason=s.rejection_reason,
            labels=s.labels,
            as_measured=s.as_measured,
            replay=replay,
            created_at=now,
        )
        for s in signals
    ]
    inserted = 0
    for start in range(0, len(values), SIGNAL_INSERT_CHUNK):
        stmt = (
            insert(Signal)
            .values(values[start:start + SIGNAL_INSERT_CHUNK])
            .on_conflict_do_nothing(index_elements=["run_id", "variant_id", "venue_market_id", "side", "replay"])
            .returning(Signal.id)
        )
        inserted += len(session.execute(stmt).fetchall())
    return inserted



def pricing_clock_for_run(run, tick_budget_s: float) -> datetime:
    """The `now` to price a stored run with. A run's odds are fetched *after* `started_at`, so
    pricing at `started_at` would exclude its own books; `finished_at` bounds everything the run
    recorded. A run that never finished falls back to its start plus the tick budget."""
    if run.finished_at is not None:
        return run.finished_at
    return run.started_at + timedelta(seconds=tick_budget_s)


def pricing_order(variants: list[Variant], gate_variant_name: str,
                  run_id: int) -> tuple[list[Variant], bool]:
    """Amendment 4: the gate variant and the active primary are scored on every tick; only the
    secondaries rotate. Returns the ordered list and whether `gate_variant_name` was absent
    from the active set (the Amendment 3 rename incident, made visible instead of silent).
    """
    head: list[Variant] = []
    gate = next((v for v in variants if v.name == gate_variant_name), None)
    missing = gate is None
    if gate is not None:
        head.append(gate)
    primary = next((v for v in variants if v.tier == "primary" and v not in head), None)
    if primary is not None:
        head.append(primary)
    tail = [v for v in variants if v not in head]
    if tail:
        start = run_id % len(tail)
        tail = tail[start:] + tail[:start]
    return head + tail, missing


def price_and_signal(session: Session, run_id: int, now: datetime, settings: Settings, budget_s: float) -> dict:
    deadline = time.monotonic() + budget_s

    def ok() -> bool:
        return time.monotonic() < deadline

    result = {
        "fair_direct": 0,
        "fair_derived": 0,
        "no_sharp": 0,
        "fair_errors": 0,
        "gaps": 0,
        "signals": {},
        "budget_exhausted": False,
        "variants_run": [],
        "variants_skipped": [],
        "variant_ms": {},
        "order": [],
        "gate_variant_missing": False,
        "gate_variant_id": None,
    }

    # Stage 1 always runs, even with no budget left, so fair values keep advancing every tick.
    fair_counts = compute_fair_values(session, run_id, now, settings)
    result["fair_direct"] = fair_counts.direct
    result["fair_derived"] = fair_counts.derived
    result["no_sharp"] = fair_counts.no_sharp
    result["fair_errors"] = fair_counts.errors

    if not ok():
        result["budget_exhausted"] = True
        return result

    result["gaps"] = build_gap_snapshots(
        session, run_id, now, tz=settings.tz_local, errored_game_ids=fair_counts.errored_game_ids
    )

    if not ok():
        result["budget_exhausted"] = True
        return result

    variants = active_variants(session)
    if not variants:
        return result

    rows = _load_gap_rows(session, run_id)

    # D12: computed once per run, not once per variant -- every variant's signals are labelled
    # against the same trailing bucket table. Read unconditionally, with no `ok()` check of its
    # own, so it costs the loop's deterministic budget accounting (each `ok()` call spends one
    # `time.monotonic()` tick that `test_variant_order_leads_with_the_gate_variant...` counts,
    # as does each scored variant's Amendment 4 timing) nothing new; a variant loop that is
    # about to skip everything below still gets a correctly-labelled `as_measured` on whatever
    # it does score before its own `ok()` check trips.
    as_measured = as_measured_table(session, now)

    # Amendment 4 (2026-09-08): a busy tick that runs out of budget mid-loop must not drop the
    # two variants every conclusion rests on. The gate variant and the active primary are scored
    # first on every tick; only the secondary tail still rotates by run_id, so the missingness
    # it carries stays spread evenly rather than always falling on the same (name-sorted) tail.
    ordered, gate_missing = pricing_order(variants, settings.gate_variant, run_id)
    result["gate_variant_missing"] = gate_missing
    result["gate_variant_id"] = None if gate_missing else ordered[0].variant_id
    result["order"] = [v.name for v in ordered]
    if gate_missing:
        log.warning("gate variant %r is not in the active set; pricing order falls back to "
                    "the primary first (run %s)", settings.gate_variant, run_id)

    for i, variant in enumerate(ordered):
        if not ok():
            result["budget_exhausted"] = True
            result["variants_skipped"] = [v.name for v in ordered[i:]]
            break
        t_variant = time.monotonic()
        signals = run_strategy(rows, variant, now, as_measured=as_measured)
        candidate = sum(1 for s in signals if s.decision == "candidate")
        rejected = len(signals) - candidate
        _insert_signals(session, run_id, variant, now, signals)
        session.commit()
        result["signals"][variant.name] = {"candidate": candidate, "rejected": rejected}
        result["variants_run"].append(variant.name)
        result["variant_ms"][variant.name] = int((time.monotonic() - t_variant) * 1000)

    return result
