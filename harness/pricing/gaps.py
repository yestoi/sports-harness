import functools
import importlib.resources
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import FairValue, Game, MarketGapSnapshot, VenueMarket, VenueQuote
from harness.pricing.direct import soft_fair
from harness.pricing.fees import KALSHI_FOOTBALL, FeeModel, fee_per_contract
from harness.pricing.lines import LineKey, Line, latest_book_lines, ml_pair

MATCHED_STATUSES = ("matched", "fuzzy", "manual")
FOUR = Decimal("0.0001")

# (market_type, outcome_team_id, outcome_side, threshold) -- mirrors harness.pricing.fair._shapes_for_game
Shape = tuple[str, int | None, str | None, Decimal | None]


@functools.lru_cache(maxsize=1)
def load_popularity() -> dict[str, dict[int, int]]:
    ref = importlib.resources.files("harness.pricing").joinpath("popularity.yaml")
    with importlib.resources.as_file(ref) as path:
        data = yaml.safe_load(path.read_text()) or {}
    return {sport: {int(k): int(v) for k, v in (tiers or {}).items()} for sport, tiers in data.items()}


def _shape_for_market(market: VenueMarket) -> Shape | None:
    if market.market_type == "moneyline":
        return ("moneyline", market.side_team_id, None, None)
    if market.market_type == "spread":
        return ("spread", market.side_team_id, None, market.threshold)
    if market.market_type == "total":
        return ("total", None, "over", market.threshold)
    return None


def _fair_key(fv) -> tuple:
    """The shape a fair value prices, plus which model produced it.

    Takes a `FairValue` or any row exposing the same six attributes -- both reads below project
    columns rather than loading the mapped object (fix 49).
    """
    return (fv.game_id, fv.market_type, fv.outcome_team_id, fv.outcome_side, fv.threshold, fv.fair_source)


#: The shape-and-source key both fair-value reads group by, as columns.
_KEY_COLUMNS = (FairValue.game_id, FairValue.market_type, FairValue.outcome_team_id,
                FairValue.outcome_side, FairValue.threshold, FairValue.fair_source)
#: Everything this module reads off *this run's* fair values. Deliberately not `model_json`:
#: a derived row carries the whole margin model in it and nothing here looks at it, so loading
#: the mapped object meant carrying thousands of those dicts through the snapshot build.
_FAIR_COLUMNS = (FairValue.id, *_KEY_COLUMNS, FairValue.fair_p, FairValue.n_groups,
                 FairValue.disagreement, FairValue.staleness_s, FairValue.feed_kind,
                 FairValue.feed_lag_s, FairValue.stale_allowance_s)
#: Everything it reads off the *previous* fair value for a shape: just the two snapshot columns.
_PREV_COLUMNS = (*_KEY_COLUMNS, FairValue.fair_p, FairValue.created_at)


#: Fix 48's three ways to call this.
#:
#: "all" is what it always did and what every caller outside the pipeline still wants: one row
#: for every matched market this run quoted. The pipeline splits it, because it now builds gap
#: snapshots twice -- once on the direct fair values, so the gate variant and the primary can be
#: scored before the 45 s budget is gone, and once more after the derived pass.
#:
#: "direct" writes a row only for a market whose shape already has a fair value; everything else
#: (a shape still waiting on the margin model, an unmapped market type, a game with no sharp
#: line at all) is left for the second call. "derived" writes the rest. The split is that way
#: round, rather than "everything now, top up later", because `market_gap_snapshots` is unique
#: on (run_id, venue_market_id) and the insert does nothing on conflict: a row written in the
#: first call with `fair_p` still NULL could never be corrected by the second one.
GAP_PHASES = ("all", "direct", "derived")


def ttk_minutes_at(kickoff_utc: datetime, now: datetime) -> int:
    """Minutes from `now` to kickoff, floored: the one formula `market_gap_snapshots.ttk_minutes`
    is written with. Extracted from the snapshot build below so the enumeration capture and the
    stored row cannot drift -- the expression is unchanged, and a market that gains a gap row
    later in the same run therefore lands in the cell it was enumerated under."""
    return int((kickoff_utc - now).total_seconds() // 60)


class MarketFacts(NamedTuple):
    """What the gap build already knows about a market when it captures the ordering.

    The three cell columns addendum §1.1 otherwise fills from a gap row: `sport` and `market_type`
    are read off the same `Game` and `VenueMarket` objects `_load_gap_rows` reads them off
    (`harness/strategy/pipeline.py:75,77`), and `ttk_minutes` by the same formula (`ttk_minutes_at`,
    once `gaps.py:220`) that wrote the `snap.ttk_minutes` line 91 copies. `feed_kind` is deliberately **not** here: `feed`
    is `market_gap_snapshots.feed_kind`, a market with no snapshot has none, and the user's
    ruling of 2026-09-18 leaves it null rather than deriving a second answer at completion.
    """

    sport: str
    market_type: str
    ttk_minutes: int


def build_gap_snapshots(
    session: Session,
    run_id: int,
    now: datetime,
    tz: str,
    fee_model: FeeModel = KALSHI_FOOTBALL,
    errored_game_ids: frozenset[int] = frozenset(),
    phase: str = "all",
    market_order: list[int] | None = None,
    market_facts: dict[int, MarketFacts] | None = None,
) -> int:
    if phase not in GAP_PHASES:
        raise ValueError(f"phase must be one of {GAP_PHASES}, got {phase!r}")
    tzinfo = ZoneInfo(tz)
    popularity = load_popularity()

    window_start = now - timedelta(hours=4)
    # `venue_quotes` is scanned through `ix_quotes_run_market (run_id, venue_market_id)` (fix 42):
    # `run_id` is the scan key and `venue_market_id` the join column, so a run's quotes are one
    # index scan. Before that index existed this was a nested loop over the matched markets, each
    # one walking `ix_quotes_market_fetched (venue_market_id, fetched_at)` with `run_id` only a
    # filter -- every quote ever recorded for the market read to keep the handful of this run --
    # and on 2026-09-11 it went past the 30 s statement timeout on every pricing run.
    stmt = (
        select(VenueQuote, VenueMarket, Game)
        .join(VenueMarket, VenueMarket.id == VenueQuote.venue_market_id)
        .join(Game, Game.id == VenueMarket.game_id)
        .where(
            VenueQuote.run_id == run_id,
            VenueMarket.match_status.in_(MATCHED_STATUSES),
            Game.kickoff_utc > window_start,
        )
    )
    if phase == "derived":
        # The second call's bound (fix 48): skip every market the first call already snapshotted,
        # rather than building each row again only for the insert to do nothing on conflict. The
        # anti-join rides `uq_gap_run_market (run_id, venue_market_id)`, the same unique index
        # that constraint uses.
        stmt = stmt.where(~exists(
            select(MarketGapSnapshot.id).where(
                MarketGapSnapshot.run_id == run_id,
                MarketGapSnapshot.venue_market_id == VenueQuote.venue_market_id,
            )
        ))
    rows = session.execute(stmt).all()
    if market_order is not None:
        # Capture the original unfiltered quote traversal before splitting phases. Snapshot IDs
        # now reflect phase order; strategy's equal-edge tiebreak must not inherit that order.
        # Only scalar IDs survive this call, bounded by this run's quoted markets.
        market_order.extend(dict.fromkeys(market.id for _, market, _ in rows))
    if market_facts is not None:
        # Docket item 21 step 1. The same capture point and the same bound as `market_order`
        # above: this is the last place the market and its game are both in hand before the
        # direct phase's no-fair filter (line 219) drops a market from this pass, which is why
        # the markets that end the phase with no gap row can still name their own cell. Three
        # small scalars per market, first write wins (one market can carry several quote rows).
        for _quote, market, game in rows:
            if market.id not in market_facts:
                market_facts[market.id] = MarketFacts(
                    sport=game.sport, market_type=market.market_type,
                    ttk_minutes=ttk_minutes_at(game.kickoff_utc, now))
    if not rows:
        return 0

    game_ids = {game.id for _, _, game in rows}

    # This run's own fair values, one row per shape. Scanned through
    # `ix_fair_game_type_created (game_id, market_type, created_at)` on the `game_id` leg, with
    # `run_id` a filter; bounded by the run's own shape count, which is what it writes.
    fair_by_key: dict[tuple, object] = {}
    for fv in session.execute(
        select(*_FAIR_COLUMNS).where(FairValue.run_id == run_id, FairValue.game_id.in_(game_ids))
    ):
        fair_by_key[_fair_key(fv)] = fv

    lookback_start = now - timedelta(minutes=15)
    # Fix 49: the previous fair value per shape, resolved to one row per key *in the database*.
    # This read used to load every mapped `FairValue` written in the trailing fifteen minutes
    # for these games -- on the 30 s heartbeat that is thirty runs, and at 3,269 fair values a
    # run on a Saturday slate roughly a hundred thousand objects, each carrying its `model_json`
    # -- and then throw all but the newest per key away. Measured on a twenty-game synthetic
    # slate (`tests/test_recorder_memory.py`) the old shape grew this stage's peak allocation by
    # about 1 MiB per tick with no ceiling but the window; `DISTINCT ON` makes it one row per
    # shape, so the cost is the run's shape count rather than the history behind it.
    # Same index as above: `game_id` leads the scan and `created_at` bounds it.
    prev_by_key: dict[tuple, object] = {}
    for fv in session.execute(
        select(*_PREV_COLUMNS)
        .distinct(*_KEY_COLUMNS)
        .where(
            FairValue.game_id.in_(game_ids),
            FairValue.run_id != run_id,
            FairValue.created_at < now,
            FairValue.created_at >= lookback_start,
        )
        # `DISTINCT ON` keeps the first row of each key group, so the ordering *is* the choice of
        # "previous": newest first, and `id` breaks a tie between two rows stamped with the same
        # `created_at` (the old read left that tie to whatever order the scan returned).
        .order_by(*_KEY_COLUMNS, FairValue.created_at.desc(), FairValue.id.desc())
    ):
        prev_by_key[_fair_key(fv)] = fv

    lines_cache: dict[int, dict[LineKey, Line]] = {}
    inserted = 0

    for quote, market, game in rows:
        shape = _shape_for_market(market)
        fair = None  # a projected row (see _FAIR_COLUMNS), not a mapped FairValue
        if shape is not None:
            market_type, team_id, side, threshold = shape
            fair = fair_by_key.get((game.id, market_type, team_id, side, threshold, "direct")) or fair_by_key.get(
                (game.id, market_type, team_id, side, threshold, "derived")
            )

        if phase == "direct" and fair is None:
            # Nothing to say about this market yet: it may still gain a derived fair value, and
            # a row written now could not be corrected once it had. The "derived" call takes it.
            continue

        prev_fair = None
        if fair is not None:
            prev_fair = prev_by_key.get(
                (game.id, shape[0], shape[1], shape[2], shape[3], fair.fair_source)
            )

        yes_bid, yes_ask = quote.yes_bid, quote.yes_ask
        mid = None
        if yes_bid is not None and yes_ask is not None:
            mid = ((yes_bid + yes_ask) / Decimal(2)).quantize(FOUR, rounding=ROUND_HALF_UP)

        fair_p = fair.fair_p if fair is not None else None

        no_fair_reason = None
        if fair_p is None:
            if shape is None:
                no_fair_reason = "unmapped_market_type"
            elif game.id in errored_game_ids:
                no_fair_reason = "pricing_error"
            else:
                no_fair_reason = "no_sharp_line"

        gap_mid = gap_taker_net = gap_maker_net = None
        if fair_p is not None:
            if mid is not None:
                gap_mid = (fair_p - mid).quantize(FOUR, rounding=ROUND_HALF_UP)
            if yes_ask is not None:
                taker_fee = fee_per_contract(fee_model, "taker", yes_ask, 100)
                gap_taker_net = (fair_p - yes_ask - taker_fee).quantize(FOUR, rounding=ROUND_HALF_UP)
            if yes_bid is not None:
                maker_fee = fee_per_contract(fee_model, "maker", yes_bid, 100)
                gap_maker_net = (fair_p - yes_bid - maker_fee).quantize(FOUR, rounding=ROUND_HALF_UP)

        ttk_minutes = ttk_minutes_at(game.kickoff_utc, now)

        now_local = now.astimezone(tzinfo)
        dow = now_local.weekday()
        hour_ct = now_local.hour

        price_bucket = None
        if mid is not None:
            price_bucket = (int(mid * 100) // 5) * 5

        soft_minus_sharp = None
        if shape is not None and shape[0] == "moneyline" and fair_p is not None:
            team_id = shape[1]
            opp_id = game.away_team_id if team_id == game.home_team_id else game.home_team_id
            if game.id not in lines_cache:
                lines_cache[game.id] = latest_book_lines(session, game.id, now)
            pairs = ml_pair(lines_cache[game.id], team_id, opp_id)
            sf = soft_fair(pairs, "draftkings")
            if sf is not None:
                soft_minus_sharp = (sf - fair_p).quantize(FOUR, rounding=ROUND_HALF_UP)

        sport_tiers = popularity.get(game.sport, {})
        home_tier = sport_tiers.get(game.home_team_id, 0)
        away_tier = sport_tiers.get(game.away_team_id, 0)

        stmt = (
            insert(MarketGapSnapshot)
            .values(
                run_id=run_id,
                venue_market_id=market.id,
                fair_value_id=fair.id if fair is not None else None,
                fair_source=fair.fair_source if fair is not None else None,
                fair_p=fair_p,
                no_fair_reason=no_fair_reason,
                prev_fair_p=prev_fair.fair_p if prev_fair is not None else None,
                prev_fair_ts=prev_fair.created_at if prev_fair is not None else None,
                venue_mid=mid,
                best_bid=yes_bid,
                best_ask=yes_ask,
                bid_size=quote.yes_bid_size,
                ask_size=quote.yes_ask_size,
                n_groups=fair.n_groups if fair is not None else 0,
                disagreement=fair.disagreement if fair is not None else None,
                staleness_s=fair.staleness_s if fair is not None else None,
                feed_kind=fair.feed_kind if fair is not None else None,
                feed_lag_s=fair.feed_lag_s if fair is not None else None,
                stale_allowance_s=fair.stale_allowance_s if fair is not None else None,
                gap_mid=gap_mid,
                gap_taker_net=gap_taker_net,
                gap_maker_net=gap_maker_net,
                ttk_minutes=ttk_minutes,
                dow=dow,
                hour_ct=hour_ct,
                price_bucket=price_bucket,
                volume_24h=quote.volume_24h,
                open_interest=quote.open_interest,
                soft_minus_sharp=soft_minus_sharp,
                home_popularity_tier=home_tier,
                away_popularity_tier=away_tier,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["run_id", "venue_market_id"])
            .returning(MarketGapSnapshot.id)
        )
        result = session.execute(stmt)
        inserted += len(result.fetchall())

    session.commit()
    return inserted
