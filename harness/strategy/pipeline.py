"""Wires the pricing and strategy stages together for one run: fair values, gap snapshots, then
every active variant scored against this run's gaps, with signals persisted.

Runs inside the tick (spec 9.1-9.6) once Kalshi markets have refreshed for the tick, and via
`harness price-once` for manual checks. A `budget_s` wall-clock deadline is checked between
stages and between variants; when it is spent, the remaining work is skipped and the result
records `budget_exhausted: True`.

Six stages, in this order (fix 48, and the roadmap's 6D pre-loaded decision: "Direct fair values
and the primary/gate evaluations complete before derived pricing and optional research (the 45 s
budget can expire before the first variant); stage costs measured"). Each one's wall-clock cost
is recorded in `notes->'pricing'->'stages'`.

  1. `fair_direct`      -- every candidate game's direct fair values. Always runs, budget or not.
  2. `gaps_direct`      -- gap snapshots for the markets those fair values price.
  3. `variants_direct`  -- every variant whose `sources_allowed` is satisfied by direct rows,
                           in `pricing_order`: the gate variant first, then the primary,
                           including either priority variant when it also consumes derived rows.
  4. `fair_derived`     -- the margin-model fair values for the shapes with no sharp line.
  5. `gaps_derived`     -- gap snapshots for the markets the first call left alone.
  6. `variants_derived` -- the derived consumers, plus a second scoring of the direct-only
                           variants over the rows stage 5 added (see `_score`).

Until 2026-09-12 stage 1 computed direct *and* derived fair values before anything else, and on
the NAS that alone spent the whole 45 s budget: 172 consecutive runs recorded
`{"gaps": 0, "variants_run": [], "budget_exhausted": true, "fair_derived": 3079}` and the harness
produced no signal for eight hours. The order above is that fix. It puts the gate and primary
evaluations ahead of optional derived work; the deadline still applies between stages.
"""

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.db.models import Game, MarketGapSnapshot, Signal, VenueMarket
from harness.execution.risk import stopped_variants
from harness.ops import coverage
from harness.pricing.fair import compute_derived_fair_values, compute_direct_fair_values
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


def _load_gap_rows(session: Session, run_id: int, market_order: list[int] | None = None) -> list[GapRow]:
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
    if market_order is not None:
        # Preserve the traversal captured by the original full quote query, including ties
        # between direct and derived markets. Other callers retain stored snapshot order.
        rank = {market_id: i for i, market_id in enumerate(market_order)}
        rows.sort(key=lambda row: (rank.get(row.venue_market_id, len(rank)), row.gap_snapshot_id))
    return rows


def _insert_signals(
    session: Session, run_id: int, variant: Variant, now: datetime, signals: list, replay: bool = False,
    replace_existing: bool = False,
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
        stmt = insert(Signal).values(values[start:start + SIGNAL_INSERT_CHUNK])
        key = ["run_id", "variant_id", "venue_market_id", "side", "replay"]
        if replace_existing:
            # A mixed-source gate/primary was provisionally scored before derived candidates
            # existed. Refresh that same signal ID atomically with its full-universe result.
            # Bound: this run/variant/market/side/replay and the 1000-row chunk; index uq_signal_key.
            stmt = stmt.on_conflict_do_update(
                index_elements=key,
                set_={name: getattr(stmt.excluded, name) for name in values[start]
                      if name not in key and name != "created_at"},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=key)
        stmt = stmt.returning(Signal.id)
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


#: The six stages `price_and_signal` records a cost for, in the order it runs them.
STAGE_NAMES = ("fair_direct", "gaps_direct", "variants_direct",
               "fair_derived", "gaps_derived", "variants_derived")

#: Stage timing reads this clock, never `time.monotonic`. The budget's deadline is on
#: `time.monotonic` and Amendment 4's ordering test drives it with a counter that returns the
#: call number, so every extra read of that clock would move the budget in a test that is
#: measuring variant order. A stage's elapsed time is measurement and decides nothing, so it
#: takes the higher-resolution clock and leaves that accounting exactly where it was.
_stage_clock = time.perf_counter


def consumes_derived(variant: Variant) -> bool:
    """Whether this variant's `sources_allowed` includes derived fair values.

    Read off the registered variant's own config, never guessed from its name: of the seven
    registered variants only `sharp_plus_derived` says `[direct, derived]`, and it is a secondary,
    so the gate variant and the primary are both scored in `variants_direct`.
    """
    return "derived" in (variant.config.get("sources_allowed") or ())


class _Stages:
    """`notes->'pricing'->'stages'`: what each stage cost and whether it ran at all.

    A cost is only useful next to the thing it was spent instead of, which is why a skipped
    stage still gets an entry. "skipped" here always means the budget was gone before the stage
    started; a stage that ran and had nothing to do is "ran" with a small number.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {
            name: {"name": name, "elapsed_ms": 0, "status": "skipped"} for name in STAGE_NAMES}

    def record(self, name: str, started: float) -> None:
        entry = self._entries[name]
        entry["elapsed_ms"] = int((_stage_clock() - started) * 1000)
        entry["status"] = "ran"

    def as_list(self) -> list[dict]:
        return [self._entries[name] for name in STAGE_NAMES]


def price_and_signal(session: Session, run_id: int, now: datetime, settings: Settings, budget_s: float) -> dict:
    deadline = time.monotonic() + budget_s

    def ok() -> bool:
        return time.monotonic() < deadline

    stages = _Stages()
    result = {
        "fair_direct": 0,
        "fair_derived": 0,
        #: True when the budget ran out before the derived pass started, so a coverage reader can
        #: tell "not computed" from "computed, none found" -- `fair_derived` is 0 either way.
        "fair_derived_skipped": False,
        "no_sharp": 0,
        "fair_errors": 0,
        "gaps": 0,
        "signals": {},
        "budget_exhausted": False,
        "variants_run": [],
        "variants_skipped": [],
        "variants_partial": [],
        "no_sharp_skipped": True,
        "variant_ms": {},
        "order": [],
        "gate_variant_missing": False,
        "gate_variant_id": None,
        "stages": stages.as_list(),
    }

    def finish() -> dict:
        result["stages"] = stages.as_list()
        return result

    # Stage 1 always runs, even with no budget left, so direct fair values keep advancing every
    # tick. Unlike the single pass this replaces, it is the cheap half: 190 direct rows against
    # 3,079 derived ones on the NAS slate that stalled on 2026-09-11.
    t0 = _stage_clock()
    direct = compute_direct_fair_values(session, run_id, now, settings)
    stages.record("fair_direct", t0)
    result["fair_direct"] = direct.counts.direct
    result["fair_errors"] = direct.counts.errors
    errored_game_ids = direct.counts.errored_game_ids

    if not ok():
        result["budget_exhausted"] = True
        result["fair_derived_skipped"] = True
        return finish()

    market_order: list[int] = []
    t0 = _stage_clock()
    result["gaps"] = build_gap_snapshots(
        session, run_id, now, tz=settings.tz_local, errored_game_ids=errored_game_ids,
        phase="direct", market_order=market_order)
    stages.record("gaps_direct", t0)

    if not ok():
        result["budget_exhausted"] = True
        result["fair_derived_skipped"] = True
        return finish()

    # A registry with no active variant scores nothing, but it still prices: stages 4 and 5 run
    # below on an empty `ordered`, so the fair values and gap snapshots a later tick (or a
    # backfill) reads keep being written.
    t0 = _stage_clock()
    variants = active_variants(session)
    as_measured: dict | None = None
    stopped: set = set()
    ordered: list[Variant] = []
    if not variants:
        log.warning("no active variants; run %s prices but scores nothing", run_id)

    # D12: computed once per run, not once per variant -- every variant's signals are labelled
    # against the same trailing bucket table. Read unconditionally, with no `ok()` check of its
    # own, so it costs the loop's deterministic budget accounting (each `ok()` call spends one
    # `time.monotonic()` tick that `test_variant_order_leads_with_the_gate_variant...` counts,
    # as does each scored variant's Amendment 4 timing) nothing new; a variant loop that is
    # about to skip everything below still gets a correctly-labelled `as_measured` on whatever
    # it does score before its own `ok()` check trips.
    if variants:
        as_measured = as_measured_table(session, now)

        # Spec §9.3's risk gate, read once per run for the same reason `as_measured` is: every
        # variant's signals are annotated against one reading of the equity curve, and one query
        # per tick beats one per variant. It is an *annotation* (`ANNOTATION_LABELS`) -- it
        # labels what was true when the signal was made and decides nothing, so a stopped variant
        # prices, sizes and takes exactly what it would have taken unstopped.
        stopped = stopped_variants(session, now)

        # Amendment 4 (2026-09-08): a busy tick that runs out of budget mid-loop must not drop
        # the two variants every conclusion rests on. The gate variant and the active primary are
        # scored first on every tick; only the secondary tail still rotates by run_id, so the
        # missingness it carries stays spread evenly rather than always falling on the same
        # (name-sorted) tail.
        ordered, gate_missing = pricing_order(variants, settings.gate_variant, run_id)
        result["gate_variant_missing"] = gate_missing
        result["gate_variant_id"] = None if gate_missing else ordered[0].variant_id
        result["order"] = [v.name for v in ordered]
        if gate_missing:
            log.warning("gate variant %r is not in the active set; pricing order falls back to "
                        "the primary first (run %s)", settings.gate_variant, run_id)

    scored: set[str] = set()
    complete: set[str] = set()
    #: 6D §1.1: `(variant_id, venue_market_id) -> outcome` for every market a variant
    #: actually said something about, filled in by `score` and read once at completion.
    coverage_outcomes: dict[tuple[str, int], str] = {}

    def record_order() -> None:
        """`variants_run`/`variants_skipped` in `pricing_order`, not in the order stages ran.

        A variant is scored in stage 3 or stage 6 depending on what it consumes, so first-run
        order would move the derived consumers to the end of the list; every reader of these two
        keys wants the canonical order, which `order` also carries.
        """
        result["variants_run"] = [v.name for v in ordered if v.name in scored]
        result["variants_skipped"] = [v.name for v in ordered if v.name not in scored]
        result["variants_partial"] = [v.name for v in ordered if v.name in scored - complete]

    def score(variant: Variant, rows: list[GapRow], *, full: bool = False) -> None:
        """Persist scoring over the available universe, then reconcile a mixed-source priority
        variant when the full universe exists. Direct-only rows retain their original IDs.
        `variants_partial` distinguishes any scoring from a complete market evaluation.
        """
        t_variant = time.monotonic()
        signals = run_strategy(rows, variant, now, as_measured=as_measured,
                               stopped=variant.variant_id in stopped)
        candidate = sum(1 for s in signals if s.decision == "candidate")
        _insert_signals(session, run_id, variant, now, signals,
                        replace_existing=full and variant.name in scored and consumes_derived(variant))
        session.commit()
        result["signals"][variant.name] = {"candidate": candidate,
                                           "rejected": len(signals) - candidate}
        # 6D §1.1: what this variant actually said about each market. A rejection on `has_fair`
        # is a market with no fair value at all (§0.12), which is a coverage fact rather than a
        # strategy one; every other row -- candidate or rejected -- is a completed evaluation.
        for signal in signals:
            coverage_outcomes[(variant.variant_id, signal.venue_market_id)] = (
                "no_fair" if signal.rejection_reason == "has_fair" else "completed")
        result["variant_ms"][variant.name] = (
            result["variant_ms"].get(variant.name, 0) + int((time.monotonic() - t_variant) * 1000))
        scored.add(variant.name)
        if full:
            complete.add(variant.name)

    priority = [v for v in ordered if not consumes_derived(v)
                or v.name == settings.gate_variant or v.tier == "primary"]
    # Load even when there are only derived consumers: stage 5 may add no rows, while those
    # consumers still need the direct gaps already present. Include this read in stage cost.
    direct_rows = _load_gap_rows(session, run_id, market_order) if ordered else []
    direct_complete = len(direct_rows) == len(market_order)

    # 6D §1.1: the scheduled set, recorded before the work is attempted and never derived from
    # the completions. The cell each unit is enumerated under is reused at completion, so §3
    # row 2's reconciliation query -- which matches every cell column with `is not distinct
    # from` -- closes exactly the rows this call opens.
    coverage_cells = coverage.evaluation_cells(direct_rows, market_order)
    coverage_variants = [v.variant_id for v in ordered]
    coverage_at = _stage_clock()
    if ordered:
        coverage.record(session, run_id, coverage.DOMAIN_EVALUATION,
                        coverage.evaluation_scheduled_rows(coverage_cells, coverage_variants))
        # Committed here, not left to the first variant's commit (review rev-6d-t4 Important 1):
        # a stage that raises between here and `score`'s own commit is rolled back by the
        # recorder (`tick.py`'s `except Exception: session.rollback()` around `price_and_signal`),
        # which would discard the very rows that are supposed to record that the work was due.
        # "Scheduled before the work" has to mean durable before the work.
        session.commit()

    for variant in priority:
        if not ok():
            result["budget_exhausted"] = True
            stages.record("variants_direct", t0)
            record_order()
            result["fair_derived_skipped"] = True
            return finish()
        score(variant, direct_rows, full=direct_complete)
    stages.record("variants_direct", t0)
    record_order()

    if not ok():
        result["budget_exhausted"] = True
        result["fair_derived_skipped"] = True
        return finish()

    t0 = _stage_clock()
    derived_counts = compute_derived_fair_values(session, direct.pending, run_id, now, settings)
    stages.record("fair_derived", t0)
    result["fair_derived"] = derived_counts.derived
    result["no_sharp"] = derived_counts.no_sharp
    result["no_sharp_skipped"] = False
    result["fair_errors"] += derived_counts.errors
    errored_game_ids = errored_game_ids | derived_counts.errored_game_ids

    if not ok():
        result["budget_exhausted"] = True
        return finish()

    t0 = _stage_clock()
    new_gaps = build_gap_snapshots(
        session, run_id, now, tz=settings.tz_local, errored_game_ids=errored_game_ids,
        phase="derived")
    stages.record("gaps_derived", t0)
    result["gaps"] += new_gaps

    if not ok():
        result["budget_exhausted"] = True
        record_order()
        return finish()

    # Stage 6 scores every variant over the complete row set, which is exactly what the single
    # pass did. A direct-only variant already scored in stage 3 is re-scored only when stage 5
    # actually added rows; when it added none, stage 3 already saw the whole set and a second
    # pass could not produce a row it has not produced.
    t0 = _stage_clock()
    all_rows = _load_gap_rows(session, run_id, market_order) if new_gaps else direct_rows
    for variant in ordered:
        if not consumes_derived(variant) and not new_gaps:
            continue
        if not ok():
            result["budget_exhausted"] = True
            break
        score(variant, all_rows, full=True)
    stages.record("variants_derived", t0)
    record_order()

    # 6D §1.1: the completion rows for exactly the units scheduled above. `gapped` is the
    # markets that ended the run with a gap row, which is what separates `no_gap` from
    # `no_signal`; the second `_load_gap_rows` result is already in `all_rows`.
    if ordered:
        coverage.record(
            session, run_id, coverage.DOMAIN_EVALUATION,
            coverage.evaluation_completion_rows(
                coverage_cells, coverage_variants, coverage_outcomes,
                gapped={row.venue_market_id for row in all_rows},
                scored={v.variant_id for v in ordered if v.name in scored},
                budget_exhausted=result["budget_exhausted"],
                overdue_ms=int((_stage_clock() - coverage_at) * 1000)))

    return finish()
