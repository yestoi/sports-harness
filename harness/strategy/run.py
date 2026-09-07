"""The pure strategy: gap rows in, one labelled signal row out per gap row.

Every matched contract with a quote produces a signal every tick (spec §9.6), so this
never raises on a malformed row -- it labels it False and rejects it. Filters are labels;
`decision` is `candidate` only when every label that counts is True. The cap labels count
only for variants with `apply_caps: true`; for everyone else they are recorded so replay
can measure what the caps would have cost.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from harness.pricing.fees import KALSHI_FOOTBALL, FeeModel, fee_per_contract
from harness.strategy.variants import Variant

CENT = Decimal("0.01")
FOUR = Decimal("0.0001")
ZERO = Decimal("0")
ONE = Decimal("1")

# The fixed order in which labels are evaluated; `rejection_reason` is the first False one.
LABEL_ORDER = [
    "has_fair",
    "source_allowed",
    "sport_allowed",
    "match_confidence",
    "not_stale",
    "price_band",
    "ttk",
    "spread",
    "volume",
    "velocity",
    "disagreement_ok",
    "edge",
    "min_contracts",
    "cap_per_bet",
    "cap_per_game",
    "cap_daily",
    "max_open",
]
CAP_LABELS = ["cap_per_bet", "cap_per_game", "cap_daily", "max_open"]
FILTER_LABELS = [label for label in LABEL_ORDER if label not in CAP_LABELS]

# Same-side moneyline and spread on one game are a single position (spec §6.5); totals
# are their own position, so they never take a dedupe key.
SIDE_MARKETS = ("moneyline", "spread")

# Spec 6.4 requires match confidence 1.0: an operator-confirmed match counts, a fuzzy one does not.
CONFIDENT_MATCHES = ("matched", "manual")


@dataclass(frozen=True)
class GapRow:
    """A `market_gap_snapshots` row joined to its market and game."""

    gap_snapshot_id: int
    venue_market_id: int
    sport: str | None
    game_id: int | None
    market_type: str | None
    side_team_id: int | None
    threshold: Decimal | None
    fair_p: Decimal | None
    fair_source: str | None
    disagreement: Decimal | None
    #: How many independent book groups fed the fair value's consensus (spec §9.6). A
    #: Pinnacle-only fair has n_groups == 1 (or 0 with no fair at all) and is measured
    #: against nothing, so `disagreement_ok` requires at least 2 rather than trusting
    #: `disagreement`, which `consensus()` sets to 0.0000 -- not None -- for a single group.
    n_groups: int
    staleness_s: int | None
    prev_fair_p: Decimal | None
    prev_fair_ts: datetime | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    bid_size: int | None
    ask_size: int | None
    ttk_minutes: int | None
    volume_24h: int | None
    open_interest: int | None
    venue_mid: Decimal | None
    match_status: str = "matched"
    #: The staleness budget implied by this row's feed cadence (spec F11); `not_stale` takes
    #: whichever of this and the variant's `stale_s` is looser. None when the row has no fair
    #: value to key a feed off of, in which case `not_stale` falls back to `stale_s` alone.
    stale_allowance_s: int | None = None
    #: "featured" or "alternate" -- which Odds API feed produced the fair value's newest line.
    feed_kind: str | None = None


@dataclass
class StrategyState:
    """Exposure carried across `run_strategy` calls (one trading day, one variant)."""

    open_orders: int = 0
    daily_exposure: Decimal = ZERO
    game_exposure: dict[int, Decimal] = field(default_factory=dict)
    #: (game_id, side_team_id) -> the edge of the position already taken on that side.
    positions: dict[tuple[int, int], Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalRow:
    """Mirrors the `signals` columns that the strategy decides (ids and run come later)."""

    venue_market_id: int
    gap_snapshot_id: int
    side: str = "yes"
    fair_p: Decimal | None = None
    fair_source: str | None = None
    venue_best_bid: Decimal | None = None
    venue_best_ask: Decimal | None = None
    price_target: Decimal | None = None
    fee_at_target: Decimal | None = None
    as_estimate: Decimal | None = None
    edge: Decimal | None = None
    edge_min: Decimal | None = None
    stake: Decimal | None = None
    contracts: int | None = None
    decision: str = "rejected"
    rejection_reason: str | None = None
    labels: dict[str, bool] = field(default_factory=dict)


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _q4(value: Decimal) -> Decimal:
    return value.quantize(FOUR, rounding=ROUND_HALF_UP)


def floor_cents(price: Decimal) -> Decimal:
    """Round a limit price down to a whole cent; the venue's minimum tick is 1c."""
    return max(price.quantize(CENT, rounding=ROUND_FLOOR), CENT)


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return min(max(value, low), high)


@dataclass
class _Draft:
    """One row's filter labels and numbers, before the caps are applied."""

    row: GapRow
    labels: dict[str, bool]
    edge_min: Decimal | None = None
    as_estimate: Decimal | None = None
    price_target: Decimal | None = None
    fee_at_target: Decimal | None = None
    edge: Decimal | None = None
    stake: Decimal | None = None
    #: The Kelly stake before the per-bet cap clamps it; `cap_per_bet` is labelled against
    #: this, not against `stake`, which is already clamped and so would always pass.
    uncapped_stake: Decimal | None = None
    contracts: int | None = None


def _filters(row: GapRow, cfg: dict) -> dict[str, bool]:
    fair = row.fair_p
    band_lo, band_hi = (_dec(x) for x in cfg["price_band"])
    reference = row.venue_mid if row.venue_mid is not None else row.best_ask
    max_spread = cfg["max_spread_c"].get(row.sport) if isinstance(cfg["max_spread_c"], dict) else None

    velocity = True
    if row.prev_fair_p is not None:
        velocity = fair is not None and abs(fair - row.prev_fair_p) < _dec(cfg["velocity_max_pts"])

    return {
        "has_fair": fair is not None,
        "source_allowed": row.fair_source in cfg["sources_allowed"],
        "sport_allowed": row.sport in cfg["sports"],
        "match_confidence": row.match_status in CONFIDENT_MATCHES,
        "not_stale": (
            row.staleness_s is not None and row.staleness_s <= max(cfg["stale_s"], row.stale_allowance_s or 0)
        ),
        "price_band": reference is not None and band_lo <= reference <= band_hi,
        "ttk": row.ttk_minutes is not None and row.ttk_minutes > cfg["min_ttk_min"],
        "spread": (
            max_spread is not None
            and row.best_bid is not None
            and row.best_ask is not None
            and (row.best_ask - row.best_bid) * 100 <= _dec(max_spread)
        ),
        "volume": row.volume_24h is not None and row.volume_24h >= cfg["min_volume_24h"],
        "velocity": velocity,
        # A single book group (Pinnacle-only, no corroborating group) has nothing to measure
        # disagreement against; `consensus()` still writes disagreement=0.0000 for it, which
        # would otherwise look like perfect agreement rather than no signal at all.
        "disagreement_ok": row.n_groups >= 2,
    }


def _price_and_size(row: GapRow, cfg: dict, fee_model: FeeModel, labels: dict[str, bool]) -> _Draft:
    """Spec §6.3 target price and §6.5 sizing. Only runs when the row has a fair value."""
    fair = row.fair_p
    edge_floor, edge_ceiling = _dec(cfg["edge_floor"]), _dec(cfg["edge_ceiling"])
    if row.n_groups < 2:
        # No corroborating book: require the strictest (ceiling) edge rather than letting an
        # unmeasured disagreement of 0 default to the loosest (floor) threshold.
        edge_min = _q4(edge_ceiling)
    else:
        disagreement = row.disagreement if row.disagreement is not None else ZERO
        edge_min = _q4(clamp(edge_floor + _dec(cfg["disagreement_mult"]) * disagreement, edge_floor, edge_ceiling))
    as_estimate = _dec(cfg["as_seed"])

    p0 = fair - edge_min - as_estimate
    price_target = floor_cents(p0 - fee_per_contract(fee_model, "maker", p0, 100))
    fee_at_target = fee_per_contract(fee_model, "maker", price_target, 100)
    edge = _q4(fair - price_target - fee_at_target)
    labels["edge"] = edge >= edge_min and row.best_ask is not None and price_target < row.best_ask

    bankroll = _dec(cfg["bankroll"])
    per_bet_cap = _dec(cfg["per_bet_cap"]) * bankroll
    p_cond = fair - as_estimate
    cost = price_target + fee_at_target
    f_star = (p_cond - cost) / (ONE - cost) if cost < ONE else ZERO
    uncapped_stake = ZERO
    if f_star > 0:
        uncapped_stake = max(_dec(cfg["floor_stake"]), _dec(cfg["kelly_fraction"]) * f_star * bankroll)
        stake = min(uncapped_stake, per_bet_cap)
    else:
        stake = ZERO
    stake = stake.quantize(CENT, rounding=ROUND_DOWN)
    contracts = int(stake / price_target)
    labels["min_contracts"] = contracts >= 1

    return _Draft(
        row=row,
        labels=labels,
        edge_min=edge_min,
        as_estimate=_q4(as_estimate),
        price_target=_q4(price_target),
        fee_at_target=_q4(fee_at_target),
        edge=edge,
        stake=stake,
        uncapped_stake=uncapped_stake,
        contracts=contracts,
    )


def _dedupe_key(row: GapRow) -> tuple[int, int] | None:
    if row.market_type in SIDE_MARKETS and row.game_id is not None and row.side_team_id is not None:
        return (row.game_id, row.side_team_id)
    return None


def _apply_caps(draft: _Draft, cfg: dict, state: StrategyState) -> None:
    row, labels = draft.row, draft.labels
    bankroll = _dec(cfg["bankroll"])
    stake = draft.stake if draft.stake is not None else ZERO
    uncapped_stake = draft.uncapped_stake if draft.uncapped_stake is not None else ZERO

    key = _dedupe_key(row)
    held = state.positions.get(key) if key is not None else None
    dedupe_ok = held is None or (draft.edge is not None and draft.edge > held)

    game_held = state.game_exposure.get(row.game_id, ZERO) if row.game_id is not None else ZERO
    # Labelled against the uncapped Kelly stake, not the recorded `stake` -- that one is
    # already clamped to this same ceiling, so comparing it back would always pass (spec §9.6
    # wants the caps' cost measured, not hidden by the clamp they themselves impose).
    labels["cap_per_bet"] = uncapped_stake <= _dec(cfg["per_bet_cap"]) * bankroll
    labels["cap_per_game"] = dedupe_ok and game_held + stake <= _dec(cfg["per_game_cap"]) * bankroll
    labels["cap_daily"] = state.daily_exposure + stake <= _dec(cfg["daily_cap"]) * bankroll
    labels["max_open"] = state.open_orders + 1 <= cfg["max_open"]


def _decide(labels: dict[str, bool], apply_caps: bool) -> tuple[str, str | None]:
    counted = FILTER_LABELS + (CAP_LABELS if apply_caps else [])
    for label in LABEL_ORDER:
        if label in counted and not labels[label]:
            return "rejected", label
    return "candidate", None


def run_strategy(
    rows: list[GapRow],
    variant: Variant,
    now: datetime,
    state: StrategyState | None = None,
    fee_model: FeeModel = KALSHI_FOOTBALL,
) -> list[SignalRow]:
    """Evaluate `rows` under `variant`, returning one `SignalRow` per row in input order.

    Caps are applied in edge-descending order so the best rows claim the bankroll first;
    `state` accumulates only what candidates consume.
    """
    cfg = variant.config
    state = state if state is not None else StrategyState()
    apply_caps = bool(cfg["apply_caps"])

    drafts: list[_Draft] = []
    for row in rows:
        labels = _filters(row, cfg)
        if labels["has_fair"]:
            drafts.append(_price_and_size(row, cfg, fee_model, labels))
        else:
            labels["edge"] = False
            labels["min_contracts"] = False
            drafts.append(_Draft(row=row, labels=labels))

    order = sorted(
        range(len(drafts)),
        key=lambda i: (-(drafts[i].edge if drafts[i].edge is not None else Decimal("-999")), i),
    )

    signals: list[SignalRow | None] = [None] * len(drafts)
    for i in order:
        draft = drafts[i]
        _apply_caps(draft, cfg, state)
        decision, rejection_reason = _decide(draft.labels, apply_caps)
        if decision == "candidate":
            stake = draft.stake if draft.stake is not None else ZERO
            state.open_orders += 1
            state.daily_exposure += stake
            if draft.row.game_id is not None:
                state.game_exposure[draft.row.game_id] = state.game_exposure.get(draft.row.game_id, ZERO) + stake
            key = _dedupe_key(draft.row)
            if key is not None and draft.edge is not None:
                state.positions[key] = max(draft.edge, state.positions.get(key, draft.edge))

        row = draft.row
        signals[i] = SignalRow(
            venue_market_id=row.venue_market_id,
            gap_snapshot_id=row.gap_snapshot_id,
            side="yes",
            fair_p=row.fair_p,
            fair_source=row.fair_source,
            venue_best_bid=row.best_bid,
            venue_best_ask=row.best_ask,
            price_target=draft.price_target,
            fee_at_target=draft.fee_at_target,
            as_estimate=draft.as_estimate,
            edge=draft.edge,
            edge_min=draft.edge_min,
            stake=draft.stake,
            contracts=draft.contracts,
            decision=decision,
            rejection_reason=rejection_reason,
            labels={label: draft.labels[label] for label in LABEL_ORDER},
        )
    return [s for s in signals if s is not None]
