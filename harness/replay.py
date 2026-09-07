"""Re-scores recorded gap snapshots under a variant, without touching the live signal set.

`replay` walks a run range that was already priced (its `market_gap_snapshots` exist),
rebuilds the same `GapRow`s the live pipeline used, and scores them under one variant --
either an already-registered one, addressed by name, or a one-off YAML forced onto the
`replay` tier. The resulting `signals` rows carry `replay=True`, so they share the table
with live signals but never collide with them (spec: `uq_signal_key` includes `replay`).

`StrategyState` is created once per `replay()` call and threaded across every run in the
range in ascending order, the same way the live pipeline's per-tick state would accumulate
over a trading day -- caps see the whole range's exposure, not each run's in isolation.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from harness.db.models import MarketGapSnapshot, Run, StrategyVariant
from harness.strategy.pipeline import _insert_signals, _load_gap_rows
from harness.strategy.run import StrategyState, run_strategy
from harness.strategy.variants import Variant, register_variants, variant_from_config


@dataclass(frozen=True)
class ReplayCounts:
    runs: int = 0
    signals_candidate: int = 0
    signals_rejected: int = 0
    inserted: int = 0


def _resolve_variant(
    session: Session, variant_name: str, variant_file: Path | None, now: datetime
) -> Variant:
    if variant_file is not None:
        config = dict(yaml.safe_load(Path(variant_file).read_text()))
        # Replay variants are out-of-band (spec §6.7): forcing the tier here means the
        # variant_id -- which hashes the whole config, tier included -- always reflects it,
        # even when the source YAML was written (or copied) with a different tier.
        config["tier"] = "replay"
        variant = variant_from_config(config, str(variant_file))
        register_variants(session, [variant], now, prune=False)
        return variant

    row = session.execute(
        select(StrategyVariant).where(StrategyVariant.name == variant_name)
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"no variant registered with name {variant_name!r}")
    return Variant(name=row.name, tier=row.tier, config=row.config_json, variant_id=row.variant_id)


def replay(
    session: Session,
    from_run: int,
    to_run: int,
    variant_name: str,
    variant_file: Path | None = None,
    now: datetime | None = None,
) -> ReplayCounts:
    """Re-score every run in `[from_run, to_run]` that has gap snapshots, under one variant.

    Runs with no `market_gap_snapshots` in range are skipped rather than erroring, so a
    sparse or partially-recorded range still replays what it has. Each run is scored with
    `run_strategy(..., now=<that run's started_at>)` and committed individually.
    """
    resolved_now = now if now is not None else datetime.now(timezone.utc)
    variant = _resolve_variant(session, variant_name, variant_file, resolved_now)

    run_ids = (
        session.execute(
            select(MarketGapSnapshot.run_id)
            .where(MarketGapSnapshot.run_id >= from_run)
            .where(MarketGapSnapshot.run_id <= to_run)
            .distinct()
            .order_by(MarketGapSnapshot.run_id)
        )
        .scalars()
        .all()
    )

    counts = ReplayCounts()
    state = StrategyState()
    for run_id in run_ids:
        rows = _load_gap_rows(session, run_id)
        run_row = session.get(Run, run_id)
        run_started_at = run_row.started_at if run_row is not None else resolved_now

        signals = run_strategy(rows, variant, run_started_at, state=state)
        candidate = sum(1 for s in signals if s.decision == "candidate")
        rejected = len(signals) - candidate
        inserted = _insert_signals(session, run_id, variant, resolved_now, signals, replay=True)
        session.commit()

        counts = ReplayCounts(
            runs=counts.runs + 1,
            signals_candidate=counts.signals_candidate + candidate,
            signals_rejected=counts.signals_rejected + rejected,
            inserted=counts.inserted + inserted,
        )
    return counts
