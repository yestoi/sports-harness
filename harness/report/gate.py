"""The go-live gate: the pre-registered criteria as data, evaluated per exec variant and
stored with their own definitions.

`CRITERIA` is the source of truth for what the gate is. Each `Criterion` carries the
definition text verbatim from spec v2 §9.5 as amended by the phase-3 addendum §0.7, the name
of the function that computes it, and its threshold; `criteria_hash()` is the sha256 of the
sorted definition strings, which is the identity of every stored `gate_reports` row and the
hash the weekly report prints. `harness/report/__init__.py` renders `CRITERIA_TEXT` from this
tuple, so the report's hash and the gate rows' hash cannot drift apart (ruling R1: editing a
threshold, a family or a definition here is a dated user decision and a pre-registration
amendment, never a tidy-up).

Two criteria are constants: `legal_decision` and `live_trading_env` are `False` by
construction in phase 3, so `GateResult.passed` -- which requires every criterion -- can never
be true from this code. That is the invariant, not an oversight: going live is a decision made
outside the harness.

Read-only over everything except `gate_reports`. Every query is scoped to one variant (each
exec variant is simulated as the sole participant, so pooling two variants' fills would count
the same tape twice), filters `replay = false`, and is bounded at the evaluation instant
`now`. The gate window is the whole paper run up to `now`, not one ISO week: the counts are
150 fills across 40 games, which no single week produces. Criteria that average a quantity
("gate means") additionally exclude games whose kickoff moved more than 5 min after their
first gap snapshot (`benchmarks.kickoff_moved`); the counting criteria do not, because a moved
kickoff does not make a fill stop having happened.

Units follow the report: `Decimal` on the way out of the database, `float` at the inference
layer (`harness.report.stats`, reused here, never re-implemented), and every per-order
quantity in the order's own side space through `side_p`.
"""

import hashlib
import logging
import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import GateReport
from harness.execution.book import side_p
from harness.report.stats import CI, cluster_ci
# The order -> episode mapping the `order_episodes` view encodes. The view answers one row per
# episode and so cannot say which episode a given order is in; `_episode_of` is Task 10's
# reproduction of its rule and is reused here rather than restated (criterion 6 is the only
# criterion scored per episode).
from harness.report.tables import _episode_of

log = logging.getLogger(__name__)

#: The confidence level every interval in the gate is read at (addendum §4).
LEVEL = 0.90
#: Criterion outcomes as they are stored in `criteria_json` (addendum §4: "passed | insufficient").
PASSED, FAILED, INSUFFICIENT = "passed", "failed", "insufficient"
#: The seven benchmark types criterion 7 gates. `result` and `opening_first_seen` are reported
#: with CIs by the weekly report and never gated (addendum §0.7(a)).
GATE_BENCHMARKS = ("pinnacle_t5", "consensus_t5", "consensus_t60", "consensus_t180",
                   "kalshi_mid_t5", "kalshi_last_trade_pre_kick", "novig_devig_t5")
#: The benchmark criterion 3 and criterion 6 read.
PINNACLE = "pinnacle_t5"
#: Criterion 4's anchor and horizon (F3, F15: the no-watcher fill against the sharp fair).
MARKOUT_ANCHOR, MARKOUT_HORIZON, DRIFT_HORIZON = "nw_fill", "30m", "0m"
#: Criterion 4's t threshold, and criterion 6's minimum game clusters per side.
MARKOUT_T = 2.0
MIN_CLUSTERS_PER_SIDE = 20


@dataclass(frozen=True)
class Criterion:
    """One gate criterion as data: what it says, what computes it, and where the line is."""

    name: str
    definition: str
    fn: str
    threshold: Any


@dataclass(frozen=True)
class CriterionResult:
    """One criterion evaluated for one variant, as it is stored in `criteria_json`.

    `n_clusters` counts games, never observations -- every t here is cluster-robust by game
    with G - 1 degrees of freedom. `detail` carries the quantities the definitions say are
    printed beside the verdict (the share of fills on a clean WS book, the share of markout
    rows whose fair never moved, the per-benchmark means) and is never itself gated.
    """

    value: Any
    threshold: Any
    passed: bool
    n_obs: int
    n_clusters: int
    definition: str
    fn: str
    status: str = FAILED
    detail: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"value": _j(self.value), "threshold": _j(self.threshold), "passed": self.passed,
                "status": self.status, "n_obs": self.n_obs, "n_clusters": self.n_clusters,
                "definition": self.definition, "fn": self.fn, "detail": _j(self.detail)}


@dataclass(frozen=True)
class GateResult:
    """One variant's whole gate: every criterion, the definitions' hash, and the verdict."""

    variant_id: str
    gate_variant: bool
    criteria: dict[str, CriterionResult]
    criteria_hash: str
    passed: bool


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "fill_events",
        "orders of the variant with at least one `queue_model` fill, non-replay, count >= 150 "
        "across >= 40 distinct games with both sports present, of which >= 80 % have "
        "`book_source = 'ws'` and `dirty_minutes = 0`",
        "fill_events", 150),
    Criterion(
        "marquee_share",
        "share of those fill events in NFL or in NCAAF with "
        "`venue_ask_at_place - venue_bid_at_place <= 0.04` >= 0.30",
        "marquee_share", 0.30),
    Criterion(
        "clv_pinnacle_lb",
        "90 % cluster-robust lower bound of mean `order_clv.clv_p_net` for `pinnacle_t5` with "
        "`stale = false` over fill events > 0",
        "clv_pinnacle_lb", 0.0),
    Criterion(
        "markout_30m",
        "mean `markouts` at `anchor = nw_fill`, `horizon = 30m`, `fair_changed = true`, "
        "`fair_p - p_used - fee_per_contract` > 0 with cluster-robust t > 2 (the share with "
        "`fair_changed = false` printed)",
        "markout_30m", 0.0),
    Criterion(
        "adverse_drift",
        "mean over fill events of `fair_p(nw_fill anchor, 0m) - side_p(fair_p_at_place)` on "
        "rows whose `0m` fair has `fair_changed = true` > -0.010 (the unconditioned share is "
        "printed)",
        "adverse_drift", -0.010),
    Criterion(
        "filled_vs_unfilled",
        "90 % cluster-robust upper bound of mean(unfilled episodes' `order_clv.clv_p_net` vs "
        "`pinnacle_t5` - filled episodes') < 0.010 with >= 20 game clusters on each side, else "
        "insufficient (fails)",
        "filled_vs_unfilled", 0.010),
    Criterion(
        "clv_every_benchmark",
        "mean `order_clv.clv_p_net` >= 0 for each of `pinnacle_t5`, `consensus_t5`, "
        "`consensus_t60`, `consensus_t180`, `kalshi_mid_t5`, `kalshi_last_trade_pre_kick` and "
        "`novig_devig_t5`, `stale = false` rows only, a type with no row reading insufficient",
        "clv_every_benchmark", 0.0),
    Criterion(
        "staleness_median",
        "median of `fair_values.staleness_s` (pricing time) over the variant's non-replay "
        "candidate signals in the window, joined through `signals.gap_snapshot_id -> "
        "market_gap_snapshots.fair_value_id`, < 90",
        "staleness_median", 90),
    Criterion(
        "settlement",
        "zero derived-vs-venue disagreements and >= 90 % of settled markets with the variant's "
        "fills carrying a `source = venue` row",
        "settlement", 0.90),
    Criterion(
        "mismatched_markets",
        "zero orders whose market's current `venue_markets.match_key` differs from "
        "`orders.match_key`",
        "mismatched_markets", 0),
    Criterion(
        "legal_decision",
        "the user's separate, documented legal decision. False by construction in phase 3",
        "legal_decision", True),
    Criterion(
        "live_trading_env",
        "LIVE_TRADING=1 in the container environment at start plus the config flag. False by "
        "construction in phase 3",
        "live_trading_env", True),
)


def criteria_hash(criteria: tuple[Criterion, ...] | None = None) -> str:
    """The sha256 of the sorted definition strings -- the identity of every stored gate row."""
    definitions = sorted(c.definition for c in (CRITERIA if criteria is None else criteria))
    return hashlib.sha256("\n".join(definitions).encode("utf-8")).hexdigest()


# --- helpers ---------------------------------------------------------------------------------


def _j(value):
    """A value as JSONB can store it: `nan` and `inf` are not JSON numbers, and Postgres
    rejects them outright, so an undefined estimate is stored as null rather than as a token
    that would make the whole row unwritable."""
    if isinstance(value, dict):
        return {k: _j(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_j(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _f(value) -> float | None:
    return None if value is None else float(value)


def _finite(value: float | None) -> bool:
    return value is not None and not math.isnan(value)


def _result(criterion: Criterion, value, passed: bool, n_obs: int, n_clusters: int,
            detail: dict | None = None, status: str | None = None) -> CriterionResult:
    return CriterionResult(value=value, threshold=criterion.threshold, passed=passed,
                           n_obs=n_obs, n_clusters=n_clusters, definition=criterion.definition,
                           fn=criterion.fn,
                           status=status or (PASSED if passed else FAILED),
                           detail=detail or {})


#: A fill event: one of the variant's own non-replay orders with at least one non-replay
#: `queue_model` fill (addendum §0.7(b)). `has_print` is a sanity assertion in table 6, not a
#: filter here.
_FILL_EVENT = """
      and exists (select 1 from fills f
                  where f.order_id = o.id and f.fill_method = 'queue_model'
                    and f.replay = false)
"""
#: A game whose kickoff moved more than 5 min after its first gap snapshot is excluded from
#: gate means (spec §9.5 as amended, F46). `kickoff_moved` is a property of the game, written
#: on every benchmark row it has.
_NOT_MOVED = """
      and not exists (select 1 from benchmarks b
                      where b.game_id = o.game_id and b.kickoff_moved)
"""
_VARIANT_ORDERS = """
    from orders o
    left join games g on g.id = o.game_id
    where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
"""

_FILL_EVENTS = text(f"""
    select o.id, o.game_id, coalesce(g.sport, o.sport) as sport, o.book_source,
           o.dirty_minutes, o.venue_bid_at_place, o.venue_ask_at_place
    {_VARIANT_ORDERS} {_FILL_EVENT}
""")


# --- the criteria ----------------------------------------------------------------------------


def fill_events(session: Session, now: datetime, variant: str,
                criterion: Criterion) -> CriterionResult:
    """Criterion 1: the fill count, its game and sport coverage, and the clean-book share."""
    rows = session.execute(_FILL_EVENTS, {"variant": variant, "now": now}).all()
    n = len(rows)
    games = {r.game_id for r in rows if r.game_id is not None}
    sports = {r.sport for r in rows if r.sport}
    clean = sum(1 for r in rows if r.book_source == "ws" and (r.dirty_minutes or 0) == 0)
    share = clean / n if n else None
    passed = (n >= criterion.threshold and len(games) >= 40
              and {"nfl", "ncaaf"} <= sports and share is not None and share >= 0.80)
    return _result(criterion, n, passed, n, len(games),
                   {"n_games": len(games), "sports": sorted(sports),
                    "ws_clean_share": share, "min_games": 40, "min_ws_clean_share": 0.80})


def marquee_share(session: Session, now: datetime, variant: str,
                  criterion: Criterion) -> CriterionResult:
    """Criterion 2: the share of fill events on NFL, or on NCAAF inside a 4c book."""
    rows = session.execute(_FILL_EVENTS, {"variant": variant, "now": now}).all()
    n = len(rows)
    marquee = 0
    for row in rows:
        if row.sport == "nfl":
            marquee += 1
        elif (row.sport == "ncaaf" and row.venue_ask_at_place is not None
                and row.venue_bid_at_place is not None
                and row.venue_ask_at_place - row.venue_bid_at_place <= Decimal("0.04")):
            marquee += 1
    share = marquee / n if n else None
    passed = share is not None and share >= criterion.threshold
    return _result(criterion, share, passed, n,
                   len({r.game_id for r in rows if r.game_id is not None}),
                   {"n_marquee": marquee, "max_ncaaf_spread": 0.04})


_CLV_FILL_EVENTS = text(f"""
    select o.game_id, c.benchmark_type, c.clv_p_net
    from order_clv c
    join orders o on o.id = c.order_id
    left join games g on g.id = o.game_id
    where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
      and o.game_id is not null and c.stale = false and c.clv_p_net is not null
      and c.benchmark_type = any(:benchmarks)
      {_FILL_EVENT} {_NOT_MOVED}
""")


def _clv_by_benchmark(session: Session, now: datetime, variant: str,
                      benchmarks: tuple[str, ...]) -> dict[str, list[tuple[float, int]]]:
    """`(clv_p_net, game_id)` per benchmark type over the variant's fill events.

    `order_clv`, never `gap_outcomes`: the gap outcome is the counterfactual for a market
    nobody traded, and criteria 3, 6 and 7 are about the orders this variant actually placed
    (addendum §0.7). Stale benchmark rows are excluded here, once, for all three.
    """
    out: dict[str, list[tuple[float, int]]] = {b: [] for b in benchmarks}
    for row in session.execute(_CLV_FILL_EVENTS,
                               {"variant": variant, "now": now,
                                "benchmarks": list(benchmarks)}):
        out[row.benchmark_type].append((float(row.clv_p_net), row.game_id))
    return out


def _ci(pairs: list[tuple[float, int]]) -> CI:
    return cluster_ci([v for v, _ in pairs], [c for _, c in pairs], level=LEVEL)


def clv_pinnacle_lb(session: Session, now: datetime, variant: str,
                    criterion: Criterion) -> CriterionResult:
    """Criterion 3: the 90 % cluster-robust lower bound of mean net CLV against Pinnacle."""
    pairs = _clv_by_benchmark(session, now, variant, (PINNACLE,))[PINNACLE]
    ci = _ci(pairs)
    passed = _finite(ci.lo) and ci.lo > criterion.threshold
    return _result(criterion, ci.lo if _finite(ci.lo) else None, passed, ci.n_obs,
                   ci.n_clusters, {"mean": ci.mean if _finite(ci.mean) else None,
                                   "hi": ci.hi if _finite(ci.hi) else None,
                                   "level": LEVEL, "benchmark": PINNACLE})


_MARKOUTS = text(f"""
    select k.fair_p, k.p_used, k.fee_per_contract, k.fair_changed, o.game_id
    from markouts k
    join orders o on o.id = k.order_id
    left join games g on g.id = o.game_id
    where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
      and o.game_id is not null and k.anchor = :anchor and k.horizon = :horizon
      {_NOT_MOVED}
""")


def markout_30m(session: Session, now: datetime, variant: str,
                criterion: Criterion) -> CriterionResult:
    """Criterion 4: the 30 m markout off the no-watcher fill, net of the maker fee.

    On `fair_changed` rows only: an unchanged fair carries no information about how the order
    aged and averaging those structural zeros in pulls the estimate toward zero (spec F15). The
    share it excludes is printed beside the verdict, which is what the definition asks for.
    """
    rows = session.execute(_MARKOUTS, {"variant": variant, "now": now,
                                       "anchor": MARKOUT_ANCHOR,
                                       "horizon": MARKOUT_HORIZON}).all()
    unchanged = sum(1 for r in rows if not r.fair_changed)
    pairs = [(float(r.fair_p - (r.p_used or 0) - (r.fee_per_contract or 0)), r.game_id)
             for r in rows if r.fair_changed and r.fair_p is not None]
    ci = _ci(pairs)
    passed = (_finite(ci.mean) and ci.mean > criterion.threshold
              and _finite(ci.t) and ci.t > MARKOUT_T)
    return _result(criterion, ci.mean if _finite(ci.mean) else None, passed, ci.n_obs,
                   ci.n_clusters,
                   {"t": ci.t if _finite(ci.t) else None, "min_t": MARKOUT_T,
                    "fair_unchanged_share": unchanged / len(rows) if rows else None,
                    "anchor": MARKOUT_ANCHOR, "horizon": MARKOUT_HORIZON})


_DRIFT = text(f"""
    select k.fair_p, k.fair_changed, o.side, o.fair_p_at_place, o.game_id
    from markouts k
    join orders o on o.id = k.order_id
    left join games g on g.id = o.game_id
    where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
      and o.game_id is not null and o.fair_p_at_place is not null
      and k.anchor = :anchor and k.horizon = :horizon
      {_FILL_EVENT} {_NOT_MOVED}
""")


def adverse_drift(session: Session, now: datetime, variant: str,
                  criterion: Criterion) -> CriterionResult:
    """Criterion 5: how far the fair moved between placement and the fill, in the order's side.

    `markouts.fair_p` is already in the order's own side space (the settler passes it through
    `side_p` before storing it), so only `fair_p_at_place` -- a YES-space column -- is
    converted here. Positive means the fair moved our way.
    """
    rows = session.execute(_DRIFT, {"variant": variant, "now": now, "anchor": MARKOUT_ANCHOR,
                                    "horizon": DRIFT_HORIZON}).all()
    usable = [r for r in rows if r.fair_p is not None]
    pairs = [(float(r.fair_p - side_p(r.fair_p_at_place, r.side)), r.game_id)
             for r in usable if r.fair_changed]
    ci = _ci(pairs)
    unconditioned = _ci([(float(r.fair_p - side_p(r.fair_p_at_place, r.side)), r.game_id)
                         for r in usable])
    passed = _finite(ci.mean) and ci.mean > criterion.threshold
    return _result(criterion, ci.mean if _finite(ci.mean) else None, passed, ci.n_obs,
                   ci.n_clusters,
                   {"fair_unchanged_share": (sum(1 for r in usable if not r.fair_changed)
                                             / len(usable)) if usable else None,
                    "mean_unconditioned": (unconditioned.mean
                                           if _finite(unconditioned.mean) else None),
                    "n_obs_unconditioned": unconditioned.n_obs,
                    "anchor": MARKOUT_ANCHOR, "horizon": DRIFT_HORIZON})


_EPISODE_ORDERS = text(f"""
    select o.id, o.variant_id, o.venue_market_id, o.side, o.placed_at, o.cancel_reason,
           o.cancelled_at, o.game_id, c.clv_p_net,
           exists (select 1 from fills f
                   where f.order_id = o.id and f.fill_method = 'queue_model'
                     and f.replay = false) as filled
    from orders o
    left join games g on g.id = o.game_id
    left join order_clv c on c.order_id = o.id and c.benchmark_type = :benchmark
                         and c.stale = false
    where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
      and o.game_id is not null
      {_NOT_MOVED}
""")


def filled_vs_unfilled(session: Session, now: datetime, variant: str,
                       criterion: Criterion) -> CriterionResult:
    """Criterion 6: an equivalence bound on unfilled-minus-filled CLV, per episode.

    The difference of two means is read through `cluster_ci` rather than a second estimator:
    weighting each episode by `N / n_side` (negative on the filled side) makes the mean of the
    weighted values the difference of the two means, and the cluster-robust variance of that
    mean is exactly the clustered sandwich for the difference (addendum §4's formula, applied
    once). Fewer than 20 game clusters on either side is `insufficient`, which fails.
    """
    rows = [dict(r._mapping) for r in session.execute(
        _EPISODE_ORDERS, {"variant": variant, "now": now, "benchmark": PINNACLE})]
    episodes = _episode_of(rows)
    grouped: dict[int, dict] = {}
    for row in rows:
        episode = grouped.setdefault(episodes[row["id"]],
                                     {"values": [], "filled": False, "game": row["game_id"]})
        episode["filled"] = episode["filled"] or row["filled"]
        if row["clv_p_net"] is not None:
            episode["values"].append(float(row["clv_p_net"]))
    scored = [(sum(e["values"]) / len(e["values"]), e["filled"], e["game"])
              for e in grouped.values() if e["values"]]
    filled = [(v, g) for v, f, g in scored if f]
    unfilled = [(v, g) for v, f, g in scored if not f]
    clusters_filled = len({g for _, g in filled})
    clusters_unfilled = len({g for _, g in unfilled})
    detail = {"n_filled": len(filled), "n_unfilled": len(unfilled),
              "n_clusters_filled": clusters_filled, "n_clusters_unfilled": clusters_unfilled,
              "min_clusters_per_side": MIN_CLUSTERS_PER_SIDE, "level": LEVEL,
              "mean_filled": (sum(v for v, _ in filled) / len(filled)) if filled else None,
              "mean_unfilled": ((sum(v for v, _ in unfilled) / len(unfilled))
                                if unfilled else None)}
    n_obs, n_clusters = len(scored), len({g for _, _, g in scored})
    if min(clusters_filled, clusters_unfilled) < MIN_CLUSTERS_PER_SIDE:
        return _result(criterion, None, False, n_obs, n_clusters, detail, status=INSUFFICIENT)
    total = len(filled) + len(unfilled)
    weighted = ([(total * v / len(unfilled), g) for v, g in unfilled]
                + [(-total * v / len(filled), g) for v, g in filled])
    ci = _ci(weighted)
    passed = _finite(ci.hi) and ci.hi < criterion.threshold
    detail["difference"] = ci.mean if _finite(ci.mean) else None
    return _result(criterion, ci.hi if _finite(ci.hi) else None, passed, n_obs, n_clusters,
                   detail)


def clv_every_benchmark(session: Session, now: datetime, variant: str,
                        criterion: Criterion) -> CriterionResult:
    """Criterion 7: mean net CLV at or above zero under each of the seven gated benchmarks.

    A type with no non-stale row is `insufficient`, not a pass: a benchmark that never resolved
    is a benchmark this variant was never measured against.
    """
    series = _clv_by_benchmark(session, now, variant, GATE_BENCHMARKS)
    means, counts, games = {}, {}, set()
    for benchmark, pairs in series.items():
        ci = _ci(pairs)
        means[benchmark] = ci.mean if _finite(ci.mean) else None
        counts[benchmark] = ci.n_obs
        games |= {g for _, g in pairs}
    missing = [b for b, mean in means.items() if mean is None]
    values = [m for m in means.values() if m is not None]
    detail = {"by_benchmark": means, "n_by_benchmark": counts, "missing": missing}
    n_obs = sum(counts.values())
    if missing:
        return _result(criterion, min(values) if values else None, False, n_obs, len(games),
                       detail, status=INSUFFICIENT)
    worst = min(values)
    return _result(criterion, worst, worst >= criterion.threshold, n_obs, len(games), detail)


_STALENESS = text("""
    select percentile_disc(0.5) within group (order by v.staleness_s) as median,
           count(v.staleness_s) as n,
           count(distinct v.game_id) as games
    from signals s
    join market_gap_snapshots gs on gs.id = s.gap_snapshot_id
    join fair_values v on v.id = gs.fair_value_id
    where s.variant_id = :variant and s.replay = false and s.decision = 'candidate'
      and s.created_at <= :now and v.staleness_s is not null
""")


def staleness_median(session: Session, now: datetime, variant: str,
                     criterion: Criterion) -> CriterionResult:
    """Criterion 8: the median pricing-time staleness behind the variant's candidate signals."""
    row = session.execute(_STALENESS, {"variant": variant, "now": now}).one()
    median = None if row.median is None else int(row.median)
    passed = median is not None and median < criterion.threshold
    return _result(criterion, median, passed, int(row.n or 0), int(row.games or 0),
                   {"source": "fair_values.staleness_s via market_gap_snapshots.fair_value_id"})


#: Derived-versus-venue disagreements are counted over every settled market, not only the ones
#: this variant traded: the definition scopes "the variant's fills" to the coverage clause
#: alone, and a settlement our score-derived result got wrong is a harness-wide fault.
_SETTLEMENT_MISMATCHES = text("""
    select count(*)
    from venue_settlements d
    join venue_settlements v
      on v.venue = d.venue and v.ticker = d.ticker and v.source = 'venue'
    where d.source = 'derived' and d.settled_at <= :now
      and coalesce(d.result, '') <> coalesce(v.result, '')
""")

_SETTLEMENT_COVERAGE = text(f"""
    select count(*) as markets,
           count(*) filter (where exists (
               select 1 from venue_settlements v
               where v.venue = d.venue and v.ticker = d.ticker and v.source = 'venue'
                 and v.settled_at <= :now)) as with_venue
    from (select distinct s.venue, s.ticker
          from venue_settlements s
          where s.source = 'derived' and s.settled_at <= :now
            and exists (select 1
                        {_VARIANT_ORDERS} and o.ticker = s.ticker {_FILL_EVENT})) d
""")


def settlement(session: Session, now: datetime, variant: str,
               criterion: Criterion) -> CriterionResult:
    """Criterion 9: no derived-vs-venue disagreement, and the venue's own result on 90 % of the
    settled markets this variant was filled in.

    A variant with no settled market fails rather than passes: the coverage clause exists so
    the check cannot pass vacuously (R11).
    """
    mismatches = int(session.execute(_SETTLEMENT_MISMATCHES, {"now": now}).scalar() or 0)
    row = session.execute(_SETTLEMENT_COVERAGE, {"variant": variant, "now": now}).one()
    markets, with_venue = int(row.markets or 0), int(row.with_venue or 0)
    coverage = with_venue / markets if markets else None
    passed = (mismatches == 0 and coverage is not None and coverage >= criterion.threshold)
    return _result(criterion, coverage, passed, markets, markets,
                   {"mismatches": mismatches, "markets_with_venue_row": with_venue})


_MISMATCHED = text(f"""
    select count(*) filter (where m.match_key is distinct from o.match_key) as mismatched,
           count(*) as orders,
           count(distinct o.game_id) as games
    from orders o
    join venue_markets m on m.id = o.venue_market_id
    where o.variant_id = :variant and o.replay = false and o.placed_at <= :now
""")


def mismatched_markets(session: Session, now: datetime, variant: str,
                       criterion: Criterion) -> CriterionResult:
    """Criterion 10: no order whose market's `match_key` has moved since placement.

    A variant with no order fails: zero orders is zero evidence, and a criterion that passes on
    an empty table would let the gate's tenth item be satisfied by never trading.
    """
    row = session.execute(_MISMATCHED, {"variant": variant, "now": now}).one()
    orders, mismatched = int(row.orders or 0), int(row.mismatched or 0)
    passed = orders > 0 and mismatched == 0
    return _result(criterion, mismatched, passed, orders, int(row.games or 0),
                   {"n_orders": orders})


def legal_decision(session: Session, now: datetime, variant: str,
                   criterion: Criterion) -> CriterionResult:
    """Criterion 11: the user's documented legal decision. False by construction in phase 3."""
    return _result(criterion, False, False, 0, 0, {"manual": True})


def live_trading_env(session: Session, now: datetime, variant: str,
                     criterion: Criterion) -> CriterionResult:
    """Criterion 12: `LIVE_TRADING=1` plus the config flag. False by construction in phase 3."""
    return _result(criterion, False, False, 0, 0, {"manual": True})


_FUNCTIONS = {
    "fill_events": fill_events,
    "marquee_share": marquee_share,
    "clv_pinnacle_lb": clv_pinnacle_lb,
    "markout_30m": markout_30m,
    "adverse_drift": adverse_drift,
    "filled_vs_unfilled": filled_vs_unfilled,
    "clv_every_benchmark": clv_every_benchmark,
    "staleness_median": staleness_median,
    "settlement": settlement,
    "mismatched_markets": mismatched_markets,
    "legal_decision": legal_decision,
    "live_trading_env": live_trading_env,
}


# --- evaluation ------------------------------------------------------------------------------


def evaluate_gate(session: Session, now: datetime, variant_id: str) -> GateResult:
    """Every criterion for one variant, as of `now`. Writes nothing."""
    criteria = {c.name: _FUNCTIONS[c.fn](session, now, variant_id, c) for c in CRITERIA}
    return GateResult(variant_id=variant_id, gate_variant=False, criteria=criteria,
                      criteria_hash=criteria_hash(),
                      passed=all(r.passed for r in criteria.values()))


_ACTIVE_VARIANTS = text("""
    select variant_id, name, tier from strategy_variants where active = true
    order by case when tier = 'primary' then 0 else 1 end, name
""")


def registered_variants(session: Session) -> list:
    """The active registered variants as `(variant_id, name, tier)` rows, primary first."""
    return list(session.execute(_ACTIVE_VARIANTS))


def exec_variant_ids(session: Session, settings) -> list[str]:
    """The registered, active variants the executor places orders for, plus the active primary.

    The primary is included even when it is not an exec variant, so the gate row always has a
    fallback to mark (D1).
    """
    rows = registered_variants(session)
    by_name = {r.name: r for r in rows}
    ids = [by_name[name].variant_id for name in settings.exec_variants if name in by_name]
    primary = next((r for r in rows if r.tier == "primary"), None)
    if primary is not None and primary.variant_id not in ids:
        ids.append(primary.variant_id)
    return ids


def gate_row_variant(session: Session, variant_ids: list[str], gate_variant: str) -> str | None:
    """The one variant id the phase gate is judged on (U5, D1).

    `Settings.gate_variant` names a variant; it is marked when that variant is registered and
    active and was evaluated, and otherwise the active primary's row is marked, so exactly one
    row carries `gate_variant = true` at every evaluation.
    """
    rows = registered_variants(session)
    named = next((r for r in rows if r.name == gate_variant), None)
    if named is not None and named.variant_id in variant_ids:
        return named.variant_id
    primary = next((r for r in rows if r.tier == "primary"), None)
    if primary is not None and primary.variant_id in variant_ids:
        return primary.variant_id
    log.warning("no gate row marked: neither %r nor an active primary was evaluated",
                gate_variant)
    return None


def evaluate_all(session: Session, now: datetime, variant_ids: list[str],
                 gate_variant: str) -> list[GateResult]:
    """Evaluate every variant and store one `gate_reports` row each, marking the gate row.

    A re-run stores a new report run: rows are keyed by `evaluated_at` and are never rewritten,
    so the history of what the gate said and when is append-only.
    """
    marked = gate_row_variant(session, variant_ids, gate_variant)
    results = []
    for variant_id in variant_ids:
        result = evaluate_gate(session, now, variant_id)
        result = replace(result, gate_variant=variant_id == marked)
        results.append(result)
        session.add(GateReport(
            evaluated_at=now, variant_id=result.variant_id, gate_variant=result.gate_variant,
            criteria_json={name: r.as_json() for name, r in result.criteria.items()},
            criteria_hash=result.criteria_hash, passed=result.passed))
    session.flush()
    return results


# --- rendering -------------------------------------------------------------------------------


def _format(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def render_gate(results: list[GateResult], names: dict[str, str],
                tiers: dict[str, str]) -> str:
    """The compact summary `harness gate` prints: one block per variant, one line per criterion.

    Pure. The gate row is marked `[gate]` and the primary is labelled beside it, so a reader
    can see which variant the phase gate was judged on and what the primary said.
    """
    lines = []
    for result in results:
        vid = result.variant_id
        marks = " ".join(filter(None, ["[gate]" if result.gate_variant else "",
                                       f"[{tiers.get(vid, '?')}]"]))
        lines.append(f"== {names.get(vid, vid)} ({vid}) {marks} passed={result.passed}")
        for name, row in result.criteria.items():
            lines.append(f"   {name:<20} {_format(row.value):>12} "
                         f"vs {_format(row.threshold):<8} "
                         f"n={row.n_obs:<6} games={row.n_clusters:<5} {row.status}")
    lines.append(f"criteria_hash={results[0].criteria_hash if results else criteria_hash()}")
    return "\n".join(lines)
