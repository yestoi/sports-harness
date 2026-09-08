"""The ten tables of spec §7.2, built over one ISO week's rows.

Read-only: every function here runs `select`s and returns `Table` values. Nothing writes, and
nothing imports the executor's or the settler's write paths (addendum ruling 6).

Three rules bind every table and are enforced in one place each, so a new table cannot quietly
break them:

* **The week's rows only.** `week_bounds` fixes the half-open interval [Monday 00:00 local,
  the next Monday 00:00 local) in UTC. Order-derived tables key on `orders.placed_at`,
  snapshot-derived tables on `market_gap_snapshots.created_at`, and signal-derived counts on
  `signals.created_at`. A fill, a markout or a CLV row belongs to the week its *order* was
  placed in, never to the week it happened to land in: otherwise table 1's fill rate would
  divide a week's fills by a different week's orders.
* **Non-replay only.** Every query that touches `orders`, `signals` or `fills` filters
  `replay = false`; `order_episodes` already excludes replay rows itself.
* **Never pooled across variants.** Every per-variant table groups by `variant_id` first.

A cell is the tuple `(estimate, n_obs, n_clusters, lo, hi)` (F14). `n_clusters` counts games,
and it -- not `n_obs` -- decides grey (below 10) and flagged (below 30). A cell with nothing in
it is `PLACEHOLDER`, never an empty string: a blank in a scored table reads as a zero.
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.execution.book import BookState, side_p
from harness.pricing.fees import KALSHI_FOOTBALL
from harness.report.stats import (
    CI,
    bh_reject,
    cluster_ci,
    eb_shrink,
    holm_reject,
    km_median,
    paired_contrast,
    two_sided_p,
)
from harness.settlement.benchmarks import BENCHMARK_TYPES
from harness.settlement.order_clv import clv_formulas

TABLE_KEYS = ("t1", "t2", "t3", "t4", "t4b", "t5", "t6", "t7", "t8", "t9", "t10")

#: What an empty cell prints. Never "" (the brief's `test_render_has_no_empty_cells`).
PLACEHOLDER = "--"
#: F14, invariant under R1: a cell is greyed below this many game clusters and flagged below
#: FLAG_CLUSTERS. Both count games, never observations.
GREY_CLUSTERS = 10
FLAG_CLUSTERS = 30
#: BH false-discovery rate for families A and B, and Holm's family-wise alpha for family C.
BH_Q = 0.10
HOLM_ALPHA = 0.10
#: The §9.6 success criterion: at least this many cells whose posterior interval excludes zero.
SIGNIFICANT_CELLS_REQUIRED = 3

SPORTS = ("nfl", "ncaaf")
MARKET_TYPES = ("moneyline", "spread", "total")
FAIR_SOURCES = ("direct", "derived")
#: Addendum §4's grid, in cents of probability. Half-open below, closed at the very top.
PRICE_BUCKETS = (("20-35", 0.20, 0.35), ("35-50", 0.35, 0.50),
                 ("50-65", 0.50, 0.65), ("65-80", 0.65, 0.80))
#: Time to kickoff, in minutes.
TTK_BUCKETS = (("> 24 h", 1440, None), ("3-24 h", 180, 1440), ("< 3 h", None, 180))
#: Staleness strata, in seconds (addendum §4).
STALENESS_BUCKETS = (("< 120", None, 120), ("120-300", 120, 300),
                     ("300-1000", 300, 1000), ("> 1000", 1000, None))
#: Table 3's staleness split at the featured feed's own allowance (addendum §0.1).
STALENESS_SPLIT_S = 220
FEED_KINDS = ("featured", "alternate", "unknown")
#: The stratum the headline H2 claim is read from (addendum §4). It is a stratum, not a
#: filter: the family runs over every feed and `feed_kind` is reported as strata columns
#: beside the staleness buckets (fix round 1, I1).
HEADLINE_FEED_KIND = "featured"
#: The panel the mispricing map's families, shrinkage and §9.6 criterion are judged on. Spec
#: §9.7 states H2 as "venue mid is systematically off sharp fair", and the stored `gap_mid`
#: (`fair - mid`) is that quantity, so the family tests the hypothesis's own quantity (fix
#: round 1, I2). `gap_maker_net` -- the tradeable version of the same gap -- is reported
#: beside it as a panel column outside the family.
HEADLINE_PANEL = "gap_mid"
#: Table 2's Holm family (C) is the variant contrasts against this benchmark.
CONTRAST_BENCHMARK = "pinnacle_t5"
#: Table 3's fill sets and the markout anchor each one is scored from (addendum §0.15).
FILL_SETS = (("queue_model", "fill", ("queue_model",)),
             ("queue_model + no_watcher", "nw_fill", ("queue_model", "no_watcher")),
             ("queue_model + snapshot_cross", "cross_fill", ("queue_model", "snapshot_cross")))
#: Key numbers for table 4b's distance buckets (F57).
KEY_NUMBERS = (3.0, 7.0)
KEY_DISTANCE_BUCKETS = (("<= 0.5", 0.5), ("0.5-1.5", 1.5), ("1.5-3", 3.0), ("> 3", None))
NOT_APPLICABLE = "n/a"
#: A sharp move this large starts a convergence-lag observation (spec table 5), and a lag is
#: censored when the venue has not covered half of it within this window.
MOVE_PTS = 0.02
MAX_LAG = timedelta(minutes=60)
#: Bounds on the two tape scans, so a report over a busy week cannot run unbounded.
MAX_MOVES = 2000
MAX_QUEUE_SAMPLES = 5000
#: How far before the week's Monday table 3 reads orders, so a reprice chain that straddles the
#: boundary resolves to its true head (M3). A chain lives inside one order's life, which ends at
#: kickoff - 10 min, so two weeks is generous.
CHAIN_LOOKBACK = timedelta(days=14)

NOT_COLLECTED = "not collected in phase 3 (addendum §0.4)"


@dataclass(frozen=True)
class Table:
    """One rendered table. `header` states the estimand, the sign convention and the stratum;
    `note` carries the exclusions and counts the reader needs to interpret the cells."""

    title: str
    header: str
    columns: list[str]
    rows: list[list]
    note: str | None = None


# --- cells ----------------------------------------------------------------------------------


def cell(ci: CI) -> Any:
    """A CI as the report's cell tuple, or the placeholder when the cell has no rows."""
    if ci.n_obs == 0:
        return PLACEHOLDER
    return (ci.mean, ci.n_obs, ci.n_clusters, ci.lo, ci.hi)


def _is_cell(value: Any) -> bool:
    return isinstance(value, tuple) and len(value) == 5


def is_grey(value: Any) -> bool:
    """Below 10 game clusters: printed, excluded from every BH/Holm family and from §9.6."""
    return _is_cell(value) and value[2] < GREY_CLUSTERS


def is_flagged(value: Any) -> bool:
    """Below 30 game clusters: printed with a flag, still inside the families."""
    return _is_cell(value) and value[2] < FLAG_CLUSTERS


def cell_excludes_zero(value: Any) -> bool:
    """Whether the cell's interval lies entirely on one side of zero.

    A zero-width interval never does, whatever side of zero it sits on (C1). Two routes
    produce one at a non-zero estimate: a stratum with `tau^2 = 0`, where every `B` is zero and
    the posterior collapses onto the grand mean, and a cell whose between-cluster variance is
    exactly zero, where the half-width is zero. Counting either as significant would let a
    homogeneous stratum declare the §9.6 criterion met on no evidence of heterogeneity at all,
    which the pre-registration record forbids in as many words.
    """
    if not _is_cell(value):
        return False
    lo, hi = value[3], value[4]
    if lo != lo or hi != hi:  # nan
        return False
    if hi <= lo:
        return False
    return lo > 0.0 or hi < 0.0


# --- week bounds ------------------------------------------------------------------------------


def week_bounds(year: int, week: int, tz: str) -> tuple[datetime, datetime]:
    """[Monday 00:00 local, the next Monday 00:00 local) of ISO week `week`, in UTC.

    Local, not UTC: a Sunday-night game finishing after midnight UTC is still that week's game
    in Louisiana, and the pre-registration record's calendar is stated in CT.
    """
    zone = ZoneInfo(tz)
    monday: date = date.fromisocalendar(year, week, 1)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=zone)
    return start.astimezone(ZoneInfo("UTC")), (start + timedelta(days=7)).astimezone(ZoneInfo("UTC"))


# --- small helpers ----------------------------------------------------------------------------


def _f(value) -> float | None:
    """A `Decimal` (or None) as the float the statistics work in."""
    return None if value is None else float(value)


def _bucket_price(mid: float | None) -> str | None:
    if mid is None:
        return None
    for name, lo, hi in PRICE_BUCKETS:
        if lo <= mid < hi or (name == PRICE_BUCKETS[-1][0] and mid == hi):
            return name
    return None


def _bucket_ttk(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    for name, lo, hi in TTK_BUCKETS:
        if (lo is None or minutes >= lo) and (hi is None or minutes < hi):
            return name
    return None


def _bucket_staleness(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    for name, lo, hi in STALENESS_BUCKETS:
        if (lo is None or seconds >= lo) and (hi is None or seconds < hi):
            return name
    return None


def _feed_kind(value: str | None) -> str:
    return value if value in ("featured", "alternate") else "unknown"


def _share(numerator: int, denominator: int) -> Any:
    return PLACEHOLDER if denominator == 0 else numerator / denominator


def _quantile(values: Sequence[float], q: float) -> Any:
    """A plain order statistic (nearest rank). No interpolation: these are diagnostics."""
    if not values:
        return PLACEHOLDER
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def _grouped_ci(pairs: Iterable[tuple[float, Any]]) -> CI:
    """`cluster_ci` over `(value, game_id)` pairs, which is how every table collects its cells."""
    values, clusters = [], []
    for value, cluster in pairs:
        values.append(value)
        clusters.append(cluster)
    return cluster_ci(values, clusters)


def _episode_of(orders: Sequence[dict]) -> dict[int, int]:
    """Order id -> episode id, by the rule `order_episodes` uses.

    The view answers one row per episode rather than per order, so it cannot say which episode
    a given order belongs to; this reproduces its rule (an order joins its predecessor's
    episode when that predecessor on the same variant x market x side was cancelled for
    `reprice` no later than this order was placed) over the week's orders. Replay orders are
    already excluded by the caller, as they are by the view.
    """
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for order in orders:
        by_key[(order["variant_id"], order["venue_market_id"], order["side"])].append(order)
    episodes: dict[int, int] = {}
    for chain in by_key.values():
        chain.sort(key=lambda o: (o["placed_at"], o["id"]))
        previous = None
        for order in chain:
            if (previous is not None
                    and previous["cancel_reason"] == "reprice"
                    and previous["cancelled_at"] is not None
                    and previous["cancelled_at"] <= order["placed_at"]):
                episodes[order["id"]] = episodes[previous["id"]]
            else:
                episodes[order["id"]] = order["id"]
            previous = order
    return episodes


def _placeholder_table(title: str, header: str, columns: list[str], note: str) -> Table:
    return Table(title, header, columns, [[PLACEHOLDER] * len(columns)], note)


# --- table 1: funnel ---------------------------------------------------------------------------

_T1_SIGNALS = text("""
    select s.variant_id, g.sport,
           count(*) as signals,
           count(*) filter (where s.decision = 'candidate') as candidates
    from signals s
    join venue_markets m on m.id = s.venue_market_id
    join games g on g.id = m.game_id
    where s.replay = false and s.created_at >= :start and s.created_at < :end
    group by s.variant_id, g.sport
""")

_T1_ORDERS = text("""
    select o.variant_id, coalesce(o.sport, 'unknown') as sport,
           count(distinct o.id) as orders,
           count(distinct o.venue_market_id) as distinct_markets,
           count(distinct (o.venue_market_id, (o.placed_at at time zone :tz)::date))
               as distinct_market_days,
           count(distinct o.id) filter (where f.id is not null) as filled_orders,
           count(f.id) as fills
    from orders o
    left join fills f
           on f.order_id = o.id and f.fill_method = 'queue_model' and f.replay = false
    where o.replay = false and o.placed_at >= :start and o.placed_at < :end
    group by o.variant_id, coalesce(o.sport, 'unknown')
""")

_T1_SCANNED = text("""
    select g.sport, count(distinct s.venue_market_id) as markets
    from market_gap_snapshots s
    join venue_markets m on m.id = s.venue_market_id
    join games g on g.id = m.game_id
    where s.created_at >= :start and s.created_at < :end
    group by g.sport
""")

_T1_COLUMNS = ["variant", "sport", "markets_scanned", "signals", "candidates", "orders",
               "fills", "fill_rate", "distinct_markets", "distinct_market_days"]


def _table1(session: Session, window: dict, variants: list[dict]) -> Table:
    header = ("Funnel per registered variant x sport, over the week's non-replay rows. "
              "`fill_rate` is orders with at least one `queue_model` fill divided by orders; "
              "`markets_scanned` is variant-independent (distinct markets with a gap snapshot).")
    signals = {(r.variant_id, r.sport): r for r in session.execute(_T1_SIGNALS, window)}
    orders = {(r.variant_id, r.sport): r for r in session.execute(_T1_ORDERS, window)}
    scanned = {r.sport: r.markets for r in session.execute(_T1_SCANNED, window)}
    if not variants:
        return _placeholder_table("Table 1 (t1): funnel", header, _T1_COLUMNS,
                                  "no registered variant")
    rows = []
    for variant in variants:
        for sport in SPORTS:
            key = (variant["variant_id"], sport)
            s, o = signals.get(key), orders.get(key)
            n_orders = o.orders if o else 0
            rows.append([
                variant["name"], sport, scanned.get(sport, 0),
                s.signals if s else 0, s.candidates if s else 0, n_orders,
                o.fills if o else 0,
                _share(o.filled_orders, n_orders) if o else PLACEHOLDER,
                o.distinct_markets if o else 0,
                o.distinct_market_days if o else 0,
            ])
    return Table("Table 1 (t1): funnel", header, _T1_COLUMNS, rows)


# --- table 2: CLV per variant, paired against the primary ---------------------------------------

_T2_ORDER_CLV = text("""
    select o.variant_id, o.gap_snapshot_id, o.game_id, c.benchmark_type, c.clv_p_net
    from order_clv c
    join orders o on o.id = c.order_id
    where o.replay = false and o.placed_at >= :start and o.placed_at < :end
      and o.gap_snapshot_id is not null and o.game_id is not null
      and c.stale = false and c.clv_p_net is not null
""")

#: The counterfactual for a variant that places no order: the snapshot's benchmark, scored at
#: that variant's own `price_target` (F42's whole-population map, not the primary's price).
#: Spec §7.2's table 2 ends "gate status": the newest stored gate verdict per variant as of the
#: end of the week. `harness gate` (Task 11) writes these rows; until it runs, the column is a
#: placeholder rather than a claim.
_T2_GATE = text("""
    select distinct on (variant_id) variant_id, passed, gate_variant, evaluated_at
    from gate_reports
    where evaluated_at < :end
    order by variant_id, evaluated_at desc
""")

_T2_GAP_CLV = text("""
    select s.variant_id, s.gap_snapshot_id, s.side, s.price_target,
           m.game_id, o.benchmark_type, o.p_bench
    from signals s
    join gap_outcomes o on o.gap_snapshot_id = s.gap_snapshot_id
    join venue_markets m on m.id = s.venue_market_id
    where s.replay = false and s.created_at >= :start and s.created_at < :end
      and s.decision = 'candidate' and s.price_target is not null
      and o.p_bench is not null and m.game_id is not null
""")


def _table2(session: Session, window: dict, variants: list[dict]) -> Table:
    columns = ["variant", "tier", "basis", *BENCHMARK_TYPES, f"holm({CONTRAST_BENCHMARK})",
               "gate"]
    header = ("Net-of-fee CLV per registered variant against each benchmark. The primary's row "
              "is its level mean; every other row is the paired difference (variant - primary) "
              "on shared gap snapshots, clustered by game. Executed variants are read from "
              "`order_clv`; the rest from `gap_outcomes` at the variant's own `price_target`. "
              "Stale benchmark rows are excluded (their share is in table 8). Family C: Holm "
              f"at alpha = {HOLM_ALPHA} over the contrasts against `{CONTRAST_BENCHMARK}`. "
              "`gate` is the newest stored gate verdict for the variant as of the end of the "
              "week (`harness gate` writes it); `*` marks the row the phase gate is judged on.")
    gates = {r.variant_id: r for r in session.execute(_T2_GATE, window)}
    primary = next((v for v in variants if v["tier"] == "primary"), None)
    if primary is None:
        return _placeholder_table("Table 2 (t2): CLV per variant", header, columns,
                                  "no primary variant registered; no contrast can be paired")

    # variant -> benchmark -> {gap_snapshot_id: (value, game_id)}
    series: dict[str, dict[str, dict[int, tuple[float, int]]]] = defaultdict(lambda: defaultdict(dict))
    raw: dict[tuple[str, str, int], list[tuple[float, int]]] = defaultdict(list)
    for row in session.execute(_T2_ORDER_CLV, window):
        raw[(row.variant_id, row.benchmark_type, row.gap_snapshot_id)].append(
            (float(row.clv_p_net), row.game_id))
    executed = {variant for variant, _, _ in raw}
    for row in session.execute(_T2_GAP_CLV, window):
        if row.variant_id in executed:
            continue  # an executed variant is scored on its own orders, never on the snapshot
        p_bench = side_p(row.p_bench, row.side)
        _, clv_net, _ = clv_formulas(p_bench, row.price_target)
        raw[(row.variant_id, row.benchmark_type, row.gap_snapshot_id)].append(
            (float(clv_net), row.game_id))
    for (variant, benchmark, snapshot), values in raw.items():
        mean = sum(v for v, _ in values) / len(values)
        series[variant][benchmark][snapshot] = (mean, values[0][1])

    rows, contrast_cells, contrast_index = [], [], []
    for variant in variants:
        vid, is_primary = variant["variant_id"], variant["tier"] == "primary"
        row = [variant["name"], variant["tier"], "level" if is_primary else "contrast"]
        for benchmark in BENCHMARK_TYPES:
            mine = series.get(vid, {}).get(benchmark, {})
            if is_primary:
                row.append(cell(_grouped_ci((value, game) for value, game in mine.values())))
                continue
            theirs = series.get(primary["variant_id"], {}).get(benchmark, {})
            shared = sorted(set(mine) & set(theirs))
            ci = paired_contrast([mine[s][0] for s in shared], [theirs[s][0] for s in shared],
                                 [mine[s][1] for s in shared])
            value = cell(ci) if shared else PLACEHOLDER
            row.append(value)
            if benchmark == CONTRAST_BENCHMARK:
                contrast_cells.append((ci, value))
                contrast_index.append(len(rows))
        row.append(PLACEHOLDER)
        gate = gates.get(vid)
        row.append(PLACEHOLDER if gate is None
                   else ("passed" if gate.passed else "not passed") + ("*" if gate.gate_variant else ""))
        rows.append(row)

    # Family C: Holm over the non-primary contrasts, greyed cells excluded and counted.
    eligible = [(i, ci) for i, (ci, value) in zip(contrast_index, contrast_cells)
                if _is_cell(value) and not is_grey(value)]
    greyed = len(contrast_cells) - len(eligible)
    if eligible:
        pvalues = [two_sided_p(ci.t, ci.n_clusters - 1) for _, ci in eligible]
        pvalues = [p if p == p else 1.0 for p in pvalues]
        for (i, _), rejected in zip(eligible, holm_reject(pvalues, alpha=HOLM_ALPHA)):
            rows[i][columns.index(f"holm({CONTRAST_BENCHMARK})")] = "reject" if rejected else "-"
    holm_column = columns.index(f"holm({CONTRAST_BENCHMARK})")
    for i, (_, value) in zip(contrast_index, contrast_cells):
        if rows[i][holm_column] == PLACEHOLDER and _is_cell(value):
            rows[i][holm_column] = "grey"
    note = (f"family C: {len(eligible)} contrast(s) tested, {greyed} greyed out "
            f"(< {GREY_CLUSTERS} game clusters) and excluded from Holm.")
    if not rows:
        return _placeholder_table("Table 2 (t2): CLV per variant", header, columns,
                                  "no registered variant")
    return Table("Table 2 (t2): CLV per variant", header, columns, rows, note)


# --- table 3: adverse selection and markouts ----------------------------------------------------

#: Orders reach back past the week's Monday by CHAIN_LOOKBACK so a reprice chain that
#: straddles the boundary can be resolved (M3). Only orders inside the window are ever reported;
#: the earlier rows exist solely to find a chain's head.
_T3_ORDERS = text("""
    select o.id, o.variant_id, o.venue_market_id, o.side, o.placed_at, o.cancel_reason,
           o.cancelled_at, o.game_id, o.feed_kind, o.staleness_at_place, o.fair_p_at_place
    from orders o
    where o.replay = false and o.placed_at >= :chain_start and o.placed_at < :end
""")

_T3_FILLS = text("""
    select f.order_id, f.fill_method
    from fills f
    join orders o on o.id = f.order_id
    where o.replay = false and o.placed_at >= :start and o.placed_at < :end
      and f.replay = false
""")

_T3_MARKOUTS = text("""
    select k.order_id, k.anchor, k.horizon, k.p_used, k.fee_per_contract, k.fair_p,
           k.fair_changed, k.venue_mid, k.source
    from markouts k
    join orders o on o.id = k.order_id
    where o.replay = false and o.placed_at >= :start and o.placed_at < :end
""")

_T3_COLUMNS = ["variant", "fill_set", "feed_kind", "staleness", "episodes", "fills",
               "markout_1m_mid", "markout_5m_mid", "markout_30m_fair", "markout_120m_fair",
               "adverse_drift"]


def _table3(session: Session, window: dict, variants: list[dict]) -> Table:
    header = ("Adverse selection per fill set, split by the feed that priced the order and by "
              f"staleness at placement (the featured allowance, {STALENESS_SPLIT_S} s). "
              "Reprice chains are collapsed to episodes. The 1 m and 5 m markouts come off the "
              "venue mid; the 30 m and 120 m markouts off the sharp fair and only on "
              "`fair_changed` rows. `adverse_drift` is the anchor's own 0 m fair minus the fair "
              "at placement in the order's side space, on `fair_changed` rows only; positive "
              "means the fair moved our way. It is the placeholder for the `cross_fill` set by "
              "construction: `ZERO_M_ANCHORS` writes a 0 m markout for the `fill` and `nw_fill` "
              "anchors only, so a cross has no fair-at-fill row to difference. "
              "All net of the maker fee. Split by variant first: each exec variant is simulated "
              "as the sole participant, so pooling two variants' fills on one market would "
              "count the same tape twice. `episodes` counts every episode in the slice, filled "
              "or not; an episode belongs to the week its first order was placed in, so a "
              "reprice chain straddling Monday is counted once, in the earlier week.")
    chain_window = dict(window, chain_start=window["start"] - CHAIN_LOOKBACK)
    resolvable = [dict(r._mapping) for r in session.execute(_T3_ORDERS, chain_window)]
    by_id = {o["id"]: o for o in resolvable}
    episodes = _episode_of(resolvable)
    # An episode belongs to the week its first order was placed in, so a chain whose head
    # predates Monday is the previous report's and is not recounted here (M3).
    orders = [o for o in resolvable if window["start"] <= o["placed_at"] < window["end"]]
    in_week = {o["id"] for o in orders}

    fills: dict[int, list[str]] = defaultdict(list)
    for row in session.execute(_T3_FILLS, window):
        fills[row.order_id].append(row.fill_method)
    markouts: dict[tuple[int, str, str], Any] = {}
    for row in session.execute(_T3_MARKOUTS, window):
        markouts[(row.order_id, row.anchor, row.horizon)] = row

    names = {v["variant_id"]: v["name"] for v in variants}
    for order in orders:
        names.setdefault(order["variant_id"], order["variant_id"])
    slices = ((f"<= {STALENESS_SPLIT_S} s", None, STALENESS_SPLIT_S),
              (f"> {STALENESS_SPLIT_S} s", STALENESS_SPLIT_S, None),
              ("unknown", None, None))

    def in_slice(order: dict, feed: str, label: str, lo, hi) -> bool:
        if _feed_kind(order["feed_kind"]) != feed:
            return False
        staleness = order["staleness_at_place"]
        if label == "unknown":
            return staleness is None
        if staleness is None:
            return False
        return (lo is None or staleness > lo) and (hi is None or staleness <= hi)

    def markout_cell(ids: list[int], anchor: str, horizon: str, field: str) -> Any:
        """The net markout at one horizon: the mid or the fair, minus the price and the fee.

        `venue_mid` needs a mid that actually existed at the instant (`source` is not `none`);
        `fair_p` is reported only where the fair moved (`fair_changed`), because an unchanged
        fair carries no information about how the order aged (spec F15).
        """
        pairs = []
        for order_id in ids:
            row = markouts.get((order_id, anchor, horizon))
            if row is None:
                continue
            if field == "venue_mid":
                if row.venue_mid is None or row.source in (None, "none"):
                    continue
                value = row.venue_mid
            else:
                if row.fair_p is None or not row.fair_changed:
                    continue
                value = row.fair_p
            pairs.append((float(value - (row.p_used or 0) - (row.fee_per_contract or 0)),
                          by_id[order_id]["game_id"]))
        return cell(_grouped_ci(pairs))

    def drift_cell(ids: list[int], anchor: str) -> Any:
        """`fair at the anchor - fair at place`, in the order's own side space.

        On `fair_changed` rows only (addendum §3, criterion 5). An order whose fair never moved
        contributes a structural zero, and averaging those in pulls the estimate toward zero --
        the direction that makes the `> -1.0 pt` threshold look safer than it is.
        """
        pairs = []
        for order_id in ids:
            row = markouts.get((order_id, anchor, "0m"))
            at_place = by_id[order_id]["fair_p_at_place"]
            if row is None or row.fair_p is None or at_place is None or not row.fair_changed:
                continue
            pairs.append((float(row.fair_p - side_p(at_place, by_id[order_id]["side"])),
                          by_id[order_id]["game_id"]))
        return cell(_grouped_ci(pairs))

    rows = []
    for variant_id, variant_name in sorted(names.items(), key=lambda kv: kv[1]):
        mine = [o for o in orders if o["variant_id"] == variant_id]
        for set_name, anchor, methods in FILL_SETS:
            for feed in FEED_KINDS:
                for label, lo, hi in slices:
                    ids = [o["id"] for o in mine if in_slice(o, feed, label, lo, hi)]
                    n_fills = sum(1 for i in ids
                                  for method in fills.get(i, []) if method in methods)
                    # Every episode in the slice, filled or not (M4): the other cells in the
                    # row are over all of the slice's orders, so this column must be too.
                    slice_episodes = {episodes[i] for i in ids if episodes[i] in in_week}
                    rows.append([
                        variant_name, set_name, feed, label,
                        len(slice_episodes), n_fills,
                        markout_cell(ids, anchor, "1m", "venue_mid"),
                        markout_cell(ids, anchor, "5m", "venue_mid"),
                        markout_cell(ids, anchor, "30m", "fair_p"),
                        markout_cell(ids, anchor, "120m", "fair_p"),
                        drift_cell(ids, anchor),
                    ])
    if not rows:
        return _placeholder_table("Table 3 (t3): adverse selection", header, _T3_COLUMNS,
                                  "no registered variant and no non-replay order in the week")
    return Table("Table 3 (t3): adverse selection", header, _T3_COLUMNS, rows)


# --- table 4: the mispricing map ----------------------------------------------------------------

_T4_SNAPSHOTS = text("""
    select s.id, s.fair_source, s.venue_mid, s.ttk_minutes, s.gap_mid, s.gap_maker_net,
           s.staleness_s, s.feed_kind, g.sport, m.market_type, g.id as game_id,
           o.clv_mid_p
    from market_gap_snapshots s
    join venue_markets m on m.id = s.venue_market_id
    join games g on g.id = m.game_id
    left join gap_outcomes o
           on o.gap_snapshot_id = s.id and o.benchmark_type = :benchmark
    where s.created_at >= :start and s.created_at < :end
      and s.fair_p is not null
""")

_T4_COLUMNS = ["fair_source", "price_bucket", "ttk", "sport", "market_type",
               "gap_mid", "gap_maker_net", "clv_mid_p", "posterior", "bh",
               "feed featured", "feed alternate", "feed unknown",
               "stale < 120", "stale 120-300", "stale 300-1000", "stale > 1000"]


def _table4(session: Session, window: dict) -> Table:
    header = (
        "Mispricing map: price x time-to-kickoff x sport x market type, 72 cells per fair "
        "source. Sign convention: `gap_mid` is stored as `fair - mid`, so a positive value "
        "means the venue is cheap relative to the sharp fair (the spec's 'venue mid minus "
        "sharp fair' is its negative). Panel cells cover every feed; the three `feed` columns "
        "and the four `stale` columns are the same "
        f"`{HEADLINE_PANEL}` panel within each stratum, and the headline H2 claim is the "
        f"`feed {HEADLINE_FEED_KIND}` column. `posterior` is the "
        "empirical-Bayes interval `m~ +/- t_{0.95, G-1} sqrt(B se^2)` shrunk within sport x "
        f"market type; `bh` is Benjamini-Hochberg at q = {BH_Q} on the two-sided "
        f"cluster-robust t of `{HEADLINE_PANEL}` (spec §9.7's H2 quantity), family A the "
        "direct cells and family B the derived cells. `gap_maker_net`, the tradeable version "
        "of the same gap, is reported beside it and is not in either family. The §9.6 "
        "criterion is judged on `posterior`, and a stratum with no heterogeneity contributes "
        "no significant cell.")
    params = dict(window, benchmark=CONTRAST_BENCHMARK)
    snapshots = [r for r in session.execute(_T4_SNAPSHOTS, params)]

    # (fair_source, price, ttk, sport, market_type) -> panel/stratum -> [(value, game_id)]
    buckets: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in snapshots:
        price = _bucket_price(_f(row.venue_mid))
        ttk = _bucket_ttk(row.ttk_minutes)
        if (price is None or ttk is None or row.sport not in SPORTS
                or row.market_type not in MARKET_TYPES or row.fair_source not in FAIR_SOURCES):
            continue
        key = (row.fair_source, price, ttk, row.sport, row.market_type)
        for panel, value in (("gap_mid", row.gap_mid), ("gap_maker_net", row.gap_maker_net),
                             ("clv_mid_p", row.clv_mid_p)):
            if value is not None:
                buckets[key][panel].append((float(value), row.game_id))
        family_value = getattr(row, HEADLINE_PANEL)
        if family_value is None:
            continue
        point = (float(family_value), row.game_id)
        buckets[key][f"feed {_feed_kind(row.feed_kind)}"].append(point)
        stratum = _bucket_staleness(row.staleness_s)
        if stratum is not None:
            buckets[key][f"stale {stratum}"].append(point)

    grid = [(source, price, ttk, sport, market_type)
            for source in FAIR_SOURCES
            for price, _, _ in PRICE_BUCKETS
            for ttk, _, _ in TTK_BUCKETS
            for sport in SPORTS
            for market_type in MARKET_TYPES]
    cis = {key: _grouped_ci(buckets[key][HEADLINE_PANEL]) for key in grid}

    # Shrinkage within sport x market type, per fair source, over cells with an estimable SE.
    posterior: dict[tuple, Any] = {key: PLACEHOLDER for key in grid}
    notes = []
    for source in FAIR_SOURCES:
        for sport in SPORTS:
            for market_type in MARKET_TYPES:
                stratum = [k for k in grid
                           if k[0] == source and k[3] == sport and k[4] == market_type
                           and cis[k].n_clusters >= 2 and cis[k].se == cis[k].se]
                if not stratum:
                    continue
                shrunk = eb_shrink([cis[k].mean for k in stratum],
                                   [cis[k].se for k in stratum],
                                   [cis[k].n_clusters - 1 for k in stratum])
                if shrunk.note:
                    # tau^2 = 0: every cell collapses onto the grand mean, so there is no
                    # posterior interval to report and no cell counts toward §9.6 (C1; the
                    # pre-registration record's analysis plan says so in as many words).
                    notes.append(f"{source}/{sport}/{market_type}: {shrunk.note} "
                                 "(no posterior interval; no cell counts toward §9.6)")
                    continue
                for i, key in enumerate(stratum):
                    posterior[key] = (shrunk.m_tilde[i], cis[key].n_obs, cis[key].n_clusters,
                                      shrunk.lo[i], shrunk.hi[i])

    # Families A (direct) and B (derived): BH on the headline panel, greyed cells excluded.
    verdict: dict[tuple, str] = {key: PLACEHOLDER for key in grid}
    family_notes = []
    for source, family in zip(FAIR_SOURCES, ("A", "B")):
        members = [k for k in grid if k[0] == source and cis[k].n_obs > 0]
        eligible = [k for k in members if cis[k].n_clusters >= GREY_CLUSTERS]
        for key in members:
            verdict[key] = "grey" if key not in eligible else "-"
        if eligible:
            pvalues = [two_sided_p(cis[k].t, cis[k].n_clusters - 1) for k in eligible]
            pvalues = [p if p == p else 1.0 for p in pvalues]
            for key, rejected in zip(eligible, bh_reject(pvalues, q=BH_Q)):
                verdict[key] = "reject" if rejected else "-"
        family_notes.append(f"family {family} ({source}): {len(eligible)} tested, "
                            f"{len(members) - len(eligible)} greyed and excluded")

    rows = []
    for key in grid:
        source, price, ttk, sport, market_type = key
        panels = [cell(_grouped_ci(buckets[key][panel]))
                  for panel in ("gap_mid", "gap_maker_net", "clv_mid_p")]
        feeds = [cell(_grouped_ci(buckets[key][f"feed {name}"])) for name in FEED_KINDS]
        strata = [cell(_grouped_ci(buckets[key][f"stale {name}"]))
                  for name, _, _ in STALENESS_BUCKETS]
        rows.append([source, price, ttk, sport, market_type, *panels,
                     posterior[key], verdict[key], *feeds, *strata])
    significant = sum(1 for key in grid
                      if cis[key].n_clusters >= GREY_CLUSTERS and cell_excludes_zero(posterior[key]))
    note = "; ".join([*family_notes,
                      f"cells whose posterior interval excludes zero: {significant} "
                      f"(§9.6 needs {SIGNIFICANT_CELLS_REQUIRED})", *notes])
    return Table("Table 4 (t4): mispricing map", header, _T4_COLUMNS, rows, note)


# --- table 4b: derived minus direct by key-number distance --------------------------------------

_T4B_FAIRS = text("""
    select f.run_id, f.game_id, f.market_type, f.outcome_team_id, f.outcome_side, f.threshold,
           f.fair_source, f.fair_p
    from fair_values f
    where f.created_at >= :start and f.created_at < :end
      and f.fair_source in ('direct', 'derived') and f.fair_p is not null
""")

_T4B_COLUMNS = ["market_type", "key_distance", "derived_minus_direct"]


def _key_distance_bucket(market_type: str, threshold: Decimal | None) -> str:
    """Distance from the rung to the nearest key number, for spreads.

    Key numbers are a spread concept (3 and 7 are the margins football actually lands on), so
    totals and moneylines get `n/a` rather than a distance computed against a scale they do not
    live on -- H4 is stated per market type either way.
    """
    if market_type != "spread" or threshold is None:
        return NOT_APPLICABLE
    distance = min(abs(abs(float(threshold)) - key) for key in KEY_NUMBERS)
    for name, limit in KEY_DISTANCE_BUCKETS:
        if limit is None or distance <= limit:
            return name
    return NOT_APPLICABLE


def _table4b(session: Session, window: dict) -> Table:
    header = ("Derived fair minus direct fair on shapes where the pricing tick produced both, "
              "bucketed by the rung's distance to the nearest key number (3, 7). Positive means "
              "the margin model prices the rung above the sharp line, so H4 is read net of the "
              "model's own error. Clustered by game.")
    both: dict[tuple, dict[str, float]] = defaultdict(dict)
    shapes: dict[tuple, tuple] = {}
    for row in session.execute(_T4B_FAIRS, window):
        key = (row.run_id, row.game_id, row.market_type, row.outcome_team_id,
               row.outcome_side, row.threshold)
        both[key][row.fair_source] = float(row.fair_p)
        shapes[key] = (row.market_type, row.threshold, row.game_id)

    values: dict[tuple[str, str], list] = defaultdict(list)
    for key, sources in both.items():
        if "direct" not in sources or "derived" not in sources:
            continue
        market_type, threshold, game_id = shapes[key]
        bucket = _key_distance_bucket(market_type, threshold)
        values[(market_type, bucket)].append((sources["derived"] - sources["direct"], game_id))

    labels = [name for name, _ in KEY_DISTANCE_BUCKETS] + [NOT_APPLICABLE]
    rows = [[market_type, bucket, cell(_grouped_ci(values[(market_type, bucket)]))]
            for market_type in MARKET_TYPES for bucket in labels]
    return Table("Table 4b (t4b): derived minus direct by key number", header, _T4B_COLUMNS, rows)


# --- table 5: convergence lag -------------------------------------------------------------------

_T5_FAIRS = text("""
    select f.game_id, f.market_type, f.outcome_team_id, f.outcome_side, f.threshold,
           f.fair_p, f.newest_book_ts, f.created_at, g.sport, m.ticker
    from fair_values f
    join games g on g.id = f.game_id
    join venue_markets m
      on m.game_id = f.game_id and m.market_type = f.market_type
     and coalesce(m.side_team_id, -1) = coalesce(f.outcome_team_id, -1)
     and coalesce(m.side, '') = coalesce(f.outcome_side, '')
     and coalesce(m.threshold, 0) = coalesce(f.threshold, 0)
    where f.created_at >= :start and f.created_at < :end
      and f.feed_kind = 'featured' and f.fair_p is not null
    order by f.game_id, f.market_type, f.outcome_team_id, f.outcome_side, f.threshold,
             f.created_at
""")

_T5_SNAPSHOTS = text("""
    select ticker, ts, raw
    from orderbook_events
    where kind = 'snapshot' and ticker = any(:tickers)
      and ts >= :from_ts and ts < :to_ts
    order by ticker, ts
""")

_T5_COLUMNS = ["sport", "market_type", "moves", "km_median_lag_s", "lag_interval_lo_s",
               "censored_share", "negative_share", "sampling_floor_s"]


def _table5(session: Session, window: dict) -> Table:
    header = (
        "Convergence lag on featured shapes: seconds from a sharp move of at least "
        f"{MOVE_PTS * 100:.0f} pts (`fair_values.newest_book_ts`) to the first venue mid that "
        "has covered half of it. Reported as the interval "
        "[max(0, lag - sampling floor), lag] with the Kaplan-Meier median of each bound; the "
        f"observation is censored at {int(MAX_LAG.total_seconds() // 60)} min. The venue mid is "
        "sampled from the WebSocket order-book snapshots on the tape, so the sampling floor is "
        "the interval between snapshots.")
    fairs = [r for r in session.execute(_T5_FAIRS, window)]
    moves = []
    previous_key, previous = None, None
    for row in fairs:
        key = (row.game_id, row.market_type, row.outcome_team_id, row.outcome_side, row.threshold)
        if key == previous_key and previous is not None:
            delta = float(row.fair_p) - float(previous.fair_p)
            if abs(delta) >= MOVE_PTS:
                moves.append((row.ticker, row.sport, row.market_type,
                              row.newest_book_ts or row.created_at,
                              float(previous.fair_p), delta))
        previous_key, previous = key, row
    truncated = len(moves) > MAX_MOVES
    moves = moves[:MAX_MOVES]
    if not moves:
        return _placeholder_table("Table 5 (t5): convergence lag", header, _T5_COLUMNS,
                                  "no featured sharp move of the required size in the week")

    tickers = sorted({m[0] for m in moves})
    from_ts = min(m[3] for m in moves)
    to_ts = max(m[3] for m in moves) + MAX_LAG
    samples: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for row in session.execute(_T5_SNAPSHOTS,
                               {"tickers": tickers, "from_ts": from_ts, "to_ts": to_ts}):
        try:
            mid = BookState.from_ws_raw(row.ticker, row.raw, 0, 0, row.ts, 0).mid()
        except (ValueError, AttributeError, TypeError):
            continue
        if mid is not None:
            samples[row.ticker].append((row.ts, float(mid)))

    observations: dict[tuple[str, str], list] = defaultdict(list)
    for ticker, sport, market_type, move_ts, from_p, delta in moves:
        series = samples.get(ticker, [])
        target = from_p + delta / 2.0
        after = [(ts, mid) for ts, mid in series if ts >= move_ts]
        hit_index = None
        for i, (ts, mid) in enumerate(after):
            if (delta > 0 and mid >= target) or (delta < 0 and mid <= target):
                hit_index = i
                break
        limit = MAX_LAG.total_seconds()
        if hit_index is None:
            floor = _median_gap(after) if len(after) > 1 else limit
            observations[(sport, market_type)].append((limit, True, floor, False))
            continue
        lag = (after[hit_index][0] - move_ts).total_seconds()
        previous_ts = after[hit_index - 1][0] if hit_index else move_ts
        floor = (after[hit_index][0] - previous_ts).total_seconds()
        if lag > limit:
            observations[(sport, market_type)].append((limit, True, floor, False))
        else:
            observations[(sport, market_type)].append((lag, False, floor, lag <= 0))

    rows = []
    for sport in SPORTS:
        for market_type in MARKET_TYPES:
            found = observations[(sport, market_type)]
            if not found:
                rows.append([sport, market_type, 0, *([PLACEHOLDER] * 5)])
                continue
            lags = [o[0] for o in found]
            censored = [o[1] for o in found]
            floors = [o[2] for o in found]
            median, censored_share = km_median(lags, censored)
            lo_median, _ = km_median([max(0.0, lag - floor) for lag, _, floor, _ in found], censored)
            rows.append([sport, market_type, len(found),
                         PLACEHOLDER if median is None else median,
                         PLACEHOLDER if lo_median is None else lo_median,
                         censored_share,
                         sum(1 for o in found if o[3]) / len(found),
                         _quantile(floors, 0.5)])
    note = f"moves capped at {MAX_MOVES} for this report" if truncated else None
    return Table("Table 5 (t5): convergence lag", header, _T5_COLUMNS, rows, note)


def _median_gap(series: Sequence[tuple[datetime, float]]) -> float:
    gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(series, series[1:])]
    value = _quantile(gaps, 0.5)
    return MAX_LAG.total_seconds() if value == PLACEHOLDER else float(value)


# --- table 6: validity panel --------------------------------------------------------------------

_T6_ORDERS = text("""
    select o.variant_id, o.game_id, o.queue_ahead_at_place, o.book_source, o.dirty_minutes,
           o.traded_at_price, o.venue_market_id,
           exists (select 1 from fills f where f.order_id = o.id
                     and f.fill_method = 'queue_model' and f.replay = false) as has_queue_fill,
           o.worst_case_fill
    from orders o
    where o.replay = false and o.placed_at >= :start and o.placed_at < :end
""")

_T6_FILLS = text("""
    select o.variant_id, o.game_id, f.fill_method, f.through, f.tape_source, f.has_print,
           f.fee, f.contracts
    from fills f
    join orders o on o.id = f.order_id
    where o.replay = false and o.placed_at >= :start and o.placed_at < :end
      and f.replay = false
""")

#: Queue accuracy (F12): the REST ladder against the WebSocket book within 60 s, on the markets
#: this week's orders actually sat in. Capped, because it reads the tape.
_T6_QUEUE_ACCURACY = text("""
    with placed as (
        select distinct o.venue_market_id, o.ticker
        from orders o
        where o.replay = false and o.placed_at >= :start and o.placed_at < :end
    ),
    rest as (
        select p.ticker, s.fetched_at, s.yes_bids
        from orderbook_snapshots s
        join placed p on p.venue_market_id = s.venue_market_id
        where s.fetched_at >= :start and s.fetched_at < :end
        order by s.fetched_at
        limit :cap
    )
    select r.ticker, r.fetched_at, r.yes_bids,
           (select e.raw from orderbook_events e
            where e.kind = 'snapshot' and e.ticker = r.ticker
              and e.ts between r.fetched_at - interval '60 seconds'
                          and r.fetched_at + interval '60 seconds'
            order by abs(extract(epoch from (e.ts - r.fetched_at)))
            limit 1) as ws_raw
    from rest r
""")

_T6_COLUMNS = ["variant", "metric", "value", "n_obs", "n_clusters"]


def _best_level(levels) -> tuple[float, float] | None:
    """The best (highest) YES bid and its size from a stored ladder, or None for an empty side."""
    best = None
    for level in levels or []:
        try:
            price, size = float(level[0]), float(level[1])
        except (TypeError, ValueError, IndexError):
            continue
        if best is None or price > best[0]:
            best = (price, size)
    return best


def _table6(session: Session, window: dict, variants: list[dict]) -> Table:
    header = ("Validity panel (F12): whether the paper fills are believable. Every line is one "
              "variant's own non-replay orders and fills; nothing is summed across variants. "
              "`has_print share (queue_model)` is asserted, not gated -- anything below 1.0 is a "
              "bug in the fill simulator, not a result.")
    orders = [dict(r._mapping) for r in session.execute(_T6_ORDERS, window)]
    fills = [dict(r._mapping) for r in session.execute(_T6_FILLS, window)]
    names = {v["variant_id"]: v["name"] for v in variants}
    for row in orders:
        names.setdefault(row["variant_id"], row["variant_id"])

    accuracy: list[float] = []
    for row in session.execute(_T6_QUEUE_ACCURACY, dict(window, cap=MAX_QUEUE_SAMPLES)):
        if row.ws_raw is None:
            continue
        rest_best = _best_level(row.yes_bids)
        try:
            ws = BookState.from_ws_raw(row.ticker, row.ws_raw, 0, 0, row.fetched_at, 0)
        except (ValueError, AttributeError, TypeError):
            continue
        ws_bid = ws.best_bid("yes")
        if rest_best is None or ws_bid is None:
            continue
        ws_size = float(ws.resting_at("yes", ws_bid))
        accuracy.append(abs(rest_best[1] - ws_size) / max(1.0, ws_size))

    rows = []
    for variant_id, name in sorted(names.items(), key=lambda kv: kv[1]):
        mine = [o for o in orders if o["variant_id"] == variant_id]
        my_fills = [f for f in fills if f["variant_id"] == variant_id]
        games = len({o["game_id"] for o in mine if o["game_id"] is not None})
        queue = [float(o["queue_ahead_at_place"]) for o in mine
                 if o["queue_ahead_at_place"] is not None]
        queue_zero = sum(1 for o in mine if o["queue_ahead_at_place"] in (None, 0)
                         or (o["queue_ahead_at_place"] is not None
                             and float(o["queue_ahead_at_place"]) == 0.0))
        consumption = [float(o["traded_at_price"]) / float(o["queue_ahead_at_place"])
                       for o in mine
                       if o["has_queue_fill"] and o["traded_at_price"] is not None
                       and o["queue_ahead_at_place"] not in (None, 0)
                       and float(o["queue_ahead_at_place"]) > 0]
        queue_fills = [f for f in my_fills if f["fill_method"] == "queue_model"]
        contracts = sum(float(f["contracts"]) for f in queue_fills)

        def line(metric, value, n_obs, n_clusters=games):
            rows.append([name, metric, value, n_obs, n_clusters])

        line("queue_ahead_at_place p25", _quantile(queue, 0.25), len(queue))
        line("queue_ahead_at_place median", _quantile(queue, 0.50), len(queue))
        line("queue_ahead_at_place p75", _quantile(queue, 0.75), len(queue))
        line("queue_ahead zero-or-null share", _share(queue_zero, len(mine)), len(mine))
        line("fill prints at our price share",
             _share(sum(1 for f in queue_fills if not f["through"]), len(queue_fills)),
             len(queue_fills))
        line("fill prints through our price share",
             _share(sum(1 for f in queue_fills if f["through"]), len(queue_fills)),
             len(queue_fills))
        for source in ("ws", "rest", "none"):
            line(f"book_source {source} share",
                 _share(sum(1 for o in mine if o["book_source"] == source), len(mine)), len(mine))
        line("dirty order-minutes total", sum(o["dirty_minutes"] or 0 for o in mine), len(mine))
        line("queue-consumption ratio median", _quantile(consumption, 0.5), len(consumption))
        for method in ("queue_model", "no_watcher", "snapshot_cross"):
            line(f"fills {method}", sum(1 for f in my_fills if f["fill_method"] == method),
                 len(my_fills))
        line("orders worst_case_fill", sum(1 for o in mine if o["worst_case_fill"]), len(mine))
        for source in ("ws", "rest"):
            line(f"tape source {source} share",
                 _share(sum(1 for f in queue_fills if f["tape_source"] == source),
                        len(queue_fills)), len(queue_fills))
        line("realised fee per contract",
             PLACEHOLDER if contracts == 0 else sum(float(f["fee"]) for f in queue_fills) / contracts,
             len(queue_fills))
        line("has_print share (queue_model)",
             _share(sum(1 for f in queue_fills if f["has_print"]), len(queue_fills)),
             len(queue_fills))

    # Queue accuracy is a property of the market's two book feeds, not of any one variant, so
    # it is reported once against the markets this week's orders sat in rather than repeated
    # identically under every variant's name.
    scope = "all markets with an order"
    rows.append([scope, "queue accuracy median |REST - WS| / WS", _quantile(accuracy, 0.5),
                 len(accuracy), PLACEHOLDER])
    rows.append([scope, "queue accuracy share within 10 %",
                 _share(sum(1 for a in accuracy if a <= 0.10), len(accuracy)),
                 len(accuracy), PLACEHOLDER])
    return Table("Table 6 (t6): validity panel", header, _T6_COLUMNS, rows)


# --- table 8: data quality -----------------------------------------------------------------------

_T8_COLUMNS = ["metric", "value", "n"]

_T8_QUERIES = {
    "fair_values feed_lag_s p50": ("""
        select percentile_disc(0.5) within group (order by feed_lag_s), count(feed_lag_s)
        from fair_values where created_at >= :start and created_at < :end""", None),
    "fair_values feed_lag_s p95": ("""
        select percentile_disc(0.95) within group (order by feed_lag_s), count(feed_lag_s)
        from fair_values where created_at >= :start and created_at < :end""", None),
    "fair_values staleness_s p50": ("""
        select percentile_disc(0.5) within group (order by staleness_s), count(staleness_s)
        from fair_values where created_at >= :start and created_at < :end""", None),
    "fair_values staleness_s p95": ("""
        select percentile_disc(0.95) within group (order by staleness_s), count(staleness_s)
        from fair_values where created_at >= :start and created_at < :end""", None),
    "orderbook gap events": ("""
        select count(*), count(*) from orderbook_events
        where kind = 'gap' and ts >= :start and ts < :end""", None),
    "unmatched venue markets": ("""
        select count(*) filter (where match_status = 'unmatched'), count(*)
        from venue_markets where last_seen_at >= :start and last_seen_at < :end""", None),
    "odds credits spent": ("""
        select coalesce(sum(credits_used), 0), count(*) from runs
        where started_at >= :start and started_at < :end""", None),
    "missing taker side share": ("""
        select avg(case when taker_outcome_side is null then 1.0 else 0.0 end), count(*)
        from venue_trades where ts >= :start and ts < :end""", None),
    "matched markets not linear_cent": ("""
        select count(*) filter (where coalesce(price_level_structure, '') <> 'linear_cent'),
               count(*)
        from venue_markets
        where match_status = 'matched' and last_seen_at >= :start and last_seen_at < :end""", None),
    "markets with a non-default fee model": ("""
        select count(*) filter (where fee_type is not null
                                   and (fee_type <> 'quadratic'
                                        or coalesce(fee_multiplier, 1) <> :multiplier)),
               count(*)
        from venue_markets where last_seen_at >= :start and last_seen_at < :end""", "multiplier"),
    "markets with a non-zero exchange_index": ("""
        select count(*) filter (where exchange_index <> 0), count(*)
        from venue_markets where last_seen_at >= :start and last_seen_at < :end""", None),
    "post_only_reject rate": ("""
        select (select count(*) from order_events e
                where e.replay = false and e.ts >= :start and e.ts < :end
                  and e.kind = 'skipped' and e.reason = 'post_only_reject')::float
               / nullif((select count(*) from order_events e
                         where e.replay = false and e.ts >= :start and e.ts < :end
                           and e.kind in ('place', 'skipped')), 0),
               (select count(*) from order_events e
                where e.replay = false and e.ts >= :start and e.ts < :end
                  and e.kind in ('place', 'skipped'))""", None),
}

_T8_STALE = text("""
    select b.benchmark_type,
           avg(case when b.stale then 1.0 else 0.0 end) as stale_share,
           count(*) as n
    from benchmarks b
    where b.created_at >= :start and b.created_at < :end
    group by b.benchmark_type
""")


def _table8(session: Session, window: dict) -> Table:
    header = ("Data quality over the week. The stale share per benchmark type is the fraction "
              "whose source was already more than 10 min old at the target instant. Those rows "
              "are excluded from every gate criterion and from table 2's executed-variant path, "
              "which reads `order_clv.stale`. `gap_outcomes` carries no `stale` column, so "
              "table 2's non-executed path and table 4's `clv_mid_p` panel cannot filter on it: "
              "read those two beside this share, not net of it.")
    rows = []
    for metric, (sql, extra) in _T8_QUERIES.items():
        params = dict(window)
        if extra == "multiplier":
            params["multiplier"] = KALSHI_FOOTBALL.multiplier
        value, n = session.execute(text(sql), params).one()
        rows.append([metric, PLACEHOLDER if value is None else float(value), int(n or 0)])
    seen = {r.benchmark_type: r for r in session.execute(_T8_STALE, window)}
    for benchmark in BENCHMARK_TYPES:
        row = seen.get(benchmark)
        rows.append([f"stale share: {benchmark}",
                     PLACEHOLDER if row is None else float(row.stale_share),
                     0 if row is None else int(row.n)])
    return Table("Table 8 (t8): data quality", header, _T8_COLUMNS, rows)


# --- tables 7, 9, 10: not collected ----------------------------------------------------------------


def _not_collected(key: str, title: str, what: str) -> Table:
    return Table(f"Table {key[1:]} ({key}): {title}",
                 f"{what} Phase 3 records nothing this table could read.",
                 ["item", "status"], [[title, NOT_COLLECTED]], NOT_COLLECTED)


# --- entry point --------------------------------------------------------------------------------

_VARIANTS = text("""
    select variant_id, name, tier from strategy_variants
    order by case when tier = 'primary' then 0 else 1 end, name
""")


def weekly_tables(session: Session, year: int, week: int, settings) -> dict[str, Table]:
    """Every table of spec §7.2 for ISO week `week` of `year`, keyed `t1`..`t10` (with `t4b`).

    Read-only. Each table is restricted to the week's non-replay rows and each per-variant table
    groups by variant first; a table with nothing in it still answers a placeholder row.
    """
    start, end = week_bounds(year, week, settings.tz_local)
    window = {"start": start, "end": end, "tz": settings.tz_local}
    variants = [dict(r._mapping) for r in session.execute(_VARIANTS)]
    return {
        "t1": _table1(session, window, variants),
        "t2": _table2(session, window, variants),
        "t3": _table3(session, window, variants),
        "t4": _table4(session, window),
        "t4b": _table4b(session, window),
        "t5": _table5(session, window),
        "t6": _table6(session, window, variants),
        "t7": _not_collected("t7", "veto", "The shadow veto arrives in phase 5."),
        "t8": _table8(session, window),
        "t9": _not_collected("t9", "flow", "H3's flow imbalance is a later phase."),
        "t10": _not_collected("t10", "RFQ", "The combo RFQ listener is a later phase."),
    }


def with_rows(table: Table, rows: list[list], note: str | None = None) -> Table:
    """The same table over a different row set, used by the confirmation restriction."""
    return replace(table, rows=rows or [[PLACEHOLDER] * len(table.columns)],
                   note=note if note is not None else table.note)
