import functools
import importlib.resources
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import select
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


def _fair_key(fv: FairValue) -> tuple:
    return (fv.game_id, fv.market_type, fv.outcome_team_id, fv.outcome_side, fv.threshold, fv.fair_source)


def build_gap_snapshots(
    session: Session,
    run_id: int,
    now: datetime,
    tz: str,
    fee_model: FeeModel = KALSHI_FOOTBALL,
    errored_game_ids: frozenset[int] = frozenset(),
) -> int:
    tzinfo = ZoneInfo(tz)
    popularity = load_popularity()

    window_start = now - timedelta(hours=4)
    rows = session.execute(
        select(VenueQuote, VenueMarket, Game)
        .join(VenueMarket, VenueMarket.id == VenueQuote.venue_market_id)
        .join(Game, Game.id == VenueMarket.game_id)
        .where(
            VenueQuote.run_id == run_id,
            VenueMarket.match_status.in_(MATCHED_STATUSES),
            Game.kickoff_utc > window_start,
        )
    ).all()
    if not rows:
        return 0

    game_ids = {game.id for _, _, game in rows}

    fair_by_key: dict[tuple, FairValue] = {}
    for fv in session.execute(
        select(FairValue).where(FairValue.run_id == run_id, FairValue.game_id.in_(game_ids))
    ).scalars():
        fair_by_key[_fair_key(fv)] = fv

    lookback_start = now - timedelta(minutes=15)
    prev_by_key: dict[tuple, FairValue] = {}
    for fv in session.execute(
        select(FairValue)
        .where(
            FairValue.game_id.in_(game_ids),
            FairValue.run_id != run_id,
            FairValue.created_at < now,
            FairValue.created_at >= lookback_start,
        )
        .order_by(FairValue.created_at.desc())
    ).scalars():
        key = _fair_key(fv)
        if key not in prev_by_key:
            prev_by_key[key] = fv

    lines_cache: dict[int, dict[LineKey, Line]] = {}
    inserted = 0

    for quote, market, game in rows:
        shape = _shape_for_market(market)
        fair: FairValue | None = None
        if shape is not None:
            market_type, team_id, side, threshold = shape
            fair = fair_by_key.get((game.id, market_type, team_id, side, threshold, "direct")) or fair_by_key.get(
                (game.id, market_type, team_id, side, threshold, "derived")
            )

        prev_fair: FairValue | None = None
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

        ttk_minutes = int((game.kickoff_utc - now).total_seconds() // 60)

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
