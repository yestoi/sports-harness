import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.db.models import FairValue, Game, VenueMarket
from harness.pricing import PRICING_VERSION
from harness.pricing.direct import SHARP_BOOKS, direct_fair
from harness.pricing.lines import Line, feed_kind, latest_book_lines, ml_pair, spread_pair, total_pair
from harness.pricing.margin_model import MarginModel, model_json, p_margin_over, p_moneyline, p_total_over

log = logging.getLogger(__name__)

MATCHED_STATUSES = ("matched", "fuzzy", "manual")

#: Featured-market lines refresh every tick; the allowance is that cadence (a fixed 120s,
#: independent of the actual tick interval) plus the tick's own compute budget (spec F11).
FEATURED_CADENCE_S = 120
#: Below this many minutes to kickoff, alternates are on the "near" cadence; at or beyond it,
#: the (slower) "far" cadence applies.
ALT_NEAR_CUTOFF_MIN = 180


def stale_allowance_s(feed: str, ttk_minutes: float | None, s: Settings) -> int:
    """How old a fair value keyed to `feed` may be before `not_stale` rejects it, given how
    far out the game is. Independent of `Settings.stale_s` -- `not_stale` takes the looser
    of the two (spec F11)."""
    if feed == "featured":
        return FEATURED_CADENCE_S + s.tick_budget_s
    interval = (
        s.odds_alt_interval_near_s if ttk_minutes is not None and ttk_minutes <= ALT_NEAR_CUTOFF_MIN
        else s.odds_alt_interval_far_s
    )
    return interval + s.tick_budget_s


def _feed_info(pairs: dict, newest_ts, now: datetime) -> tuple[str | None, int | None]:
    """The kind and lag of the group-member line whose `last_update == newest_ts` -- the same
    line `direct_fair`/`consensus` used to set a fair value's `newest_book_ts` (spec F11).
    A tie between a featured and an alternate line at the same `last_update` resolves to
    "alternate", since that is the fresher-by-cadence feed whose allowance the row needs."""
    if newest_ts is None:
        return None, None
    candidates: list[Line] = []
    for book in SHARP_BOOKS:
        if book not in pairs:
            continue
        for line in pairs[book]:
            if line.last_update == newest_ts:
                candidates.append(line)
    if not candidates:
        return None, None
    alternates = [line for line in candidates if feed_kind(line) == "alternate"]
    chosen = alternates[0] if alternates else candidates[0]
    kind = "alternate" if alternates else "featured"
    return kind, int((now - chosen.fetched_at).total_seconds())


def _ttk_minutes(game: Game, now: datetime) -> float:
    return (game.kickoff_utc - now).total_seconds() / 60


@dataclass(frozen=True)
class FairCounts:
    direct: int = 0
    derived: int = 0
    no_sharp: int = 0
    games: int = 0
    errors: int = 0
    #: game_ids whose fair-value computation raised and was rolled back this run -- so
    #: `build_gap_snapshots` can label their gap rows `no_fair_reason="pricing_error"`
    #: rather than lumping them in with games that simply had no sharp line to price from.
    errored_game_ids: frozenset[int] = field(default_factory=frozenset)


def _candidate_games(session: Session, now: datetime, game_ids: list[int] | None) -> list[Game]:
    if game_ids is not None:
        if not game_ids:
            return []
        return list(session.execute(select(Game).where(Game.id.in_(game_ids)).order_by(Game.id)).scalars())

    window_start = now - timedelta(hours=4)
    window_end = now + timedelta(days=8)
    stmt = (
        select(Game)
        .join(VenueMarket, VenueMarket.game_id == Game.id)
        .where(
            Game.kickoff_utc >= window_start,
            Game.kickoff_utc <= window_end,
            VenueMarket.match_status.in_(MATCHED_STATUSES),
        )
        .distinct()
        .order_by(Game.id)
    )
    return list(session.execute(stmt).scalars())


def _shapes_for_game(markets: list[VenueMarket]) -> list[tuple]:
    """Distinct contract shapes among a game's matched venue markets.

    Returns tuples of (market_type, outcome_team_id, outcome_side, threshold).
    """
    shapes: dict[tuple, None] = {}
    for m in markets:
        if m.match_status not in MATCHED_STATUSES:
            continue
        if m.market_type == "moneyline":
            key = ("moneyline", m.side_team_id, None, None)
        elif m.market_type == "spread":
            key = ("spread", m.side_team_id, None, m.threshold)
        elif m.market_type == "total":
            key = ("total", None, "over", m.threshold)
        else:
            continue
        shapes[key] = None
    return list(shapes.keys())


def _insert_fair_value(session: Session, **kwargs) -> int:
    stmt = insert(FairValue).values(**kwargs).on_conflict_do_nothing().returning(FairValue.id)
    return len(session.execute(stmt).fetchall())


def _staleness_s(now: datetime, newest_ts: datetime | None) -> int | None:
    if newest_ts is None:
        return None
    return int((now - newest_ts).total_seconds())


def _build_model(sport: str, home_id: int, away_id: int, lines, now: datetime) -> MarginModel | None:
    # Main spread: the `spreads` (never `alternate_spreads`) pair with a Pinnacle leg whose
    # |point| is smallest for the home team.
    spread_points = sorted(
        {k.point for k in lines if k.market_type == "spreads" and k.outcome_team_id == home_id and k.point is not None},
        key=lambda p: abs(p),
    )
    home_point = None
    p_home_cover = None
    for point in spread_points:
        # spread_pair's `threshold` arg is such that the team's line is -threshold.
        pairs = spread_pair(lines, home_id, away_id, -point, markets=("spreads",))
        result = direct_fair(pairs, now)
        if result is None:
            continue
        consensus, _ = result
        home_point = point
        p_home_cover = consensus.fair_p
        break

    if home_point is None:
        return None

    # Main total: the `totals` (never `alternate_totals`) pair with a Pinnacle leg, searching
    # outward from the median of available totals lines rather than requiring an exact match.
    total_points = sorted({k.point for k in lines if k.market_type == "totals" and k.point is not None})
    total_line = None
    p_over = None
    if total_points:
        median = statistics.median(total_points)
        candidates = sorted(total_points, key=lambda p: (abs(p - median), p))
        for point in candidates:
            pairs = total_pair(lines, point, markets=("totals",))
            result = direct_fair(pairs, now)
            if result is not None:
                consensus, _ = result
                total_line = point
                p_over = consensus.fair_p
                break

    return MarginModel.from_main_lines(sport, home_point, p_home_cover, total_line, p_over)


def _process_game(
    session: Session, game: Game, run_id: int, now: datetime, lookback_s: int, settings: Settings,
) -> tuple[int, int, int]:
    """Compute and insert fair values for one game. Returns (direct, derived, no_sharp) counts."""
    markets = list(
        session.execute(
            select(VenueMarket).where(
                VenueMarket.game_id == game.id,
                VenueMarket.match_status.in_(MATCHED_STATUSES),
            )
        ).scalars()
    )
    if not markets:
        return 0, 0, 0

    shapes = _shapes_for_game(markets)
    if not shapes:
        return 0, 0, 0

    lines = latest_book_lines(session, game.id, now, lookback_s)
    home_id, away_id = game.home_team_id, game.away_team_id
    ttk_minutes = _ttk_minutes(game, now)

    direct_n = 0
    direct_results: dict[tuple, tuple] = {}
    for shape in shapes:
        market_type, team_id, side, threshold = shape
        if market_type == "moneyline":
            opp_id = away_id if team_id == home_id else home_id
            pairs = ml_pair(lines, team_id, opp_id)
        elif market_type == "spread":
            opp_id = away_id if team_id == home_id else home_id
            pairs = spread_pair(lines, team_id, opp_id, threshold)
        else:  # total
            pairs = total_pair(lines, threshold)

        result = direct_fair(pairs, now)
        if result is None:
            continue

        consensus, _ = result
        newest_ts = consensus.newest_ts
        kind, lag = _feed_info(pairs, newest_ts, now)
        allowance = stale_allowance_s(kind, ttk_minutes, settings) if kind is not None else None
        n = _insert_fair_value(
            session,
            run_id=run_id,
            game_id=game.id,
            market_type=market_type,
            outcome_team_id=team_id,
            outcome_side=side,
            threshold=threshold,
            fair_p=consensus.fair_p,
            fair_source="direct",
            n_groups=consensus.n_groups,
            disagreement=consensus.disagreement,
            newest_book_ts=newest_ts,
            staleness_s=_staleness_s(now, newest_ts),
            feed_kind=kind,
            feed_lag_s=lag,
            stale_allowance_s=allowance,
            pricing_version=PRICING_VERSION,
            model_json=None,
            created_at=now,
        )
        direct_n += n
        direct_results[shape] = result

    remaining = [s for s in shapes if s not in direct_results]
    if not remaining:
        return direct_n, 0, 0

    model = _build_model(game.sport, home_id, away_id, lines, now)
    if model is None:
        return direct_n, 0, len(remaining)

    # main-line consensus for n_groups/disagreement on derived rows: recompute the main
    # spread's direct consensus (cheap; identical shape to what _build_model already found).
    main_spread_pairs = spread_pair(
        lines, home_id, away_id, -Decimal(str(model.source["home_point"])), markets=("spreads",)
    )
    main_spread_result = direct_fair(main_spread_pairs, now)
    if main_spread_result is None:
        return direct_n, 0, len(remaining)
    main_consensus, _ = main_spread_result
    main_kind, main_lag = _feed_info(main_spread_pairs, main_consensus.newest_ts, now)
    main_allowance = stale_allowance_s(main_kind, ttk_minutes, settings) if main_kind is not None else None

    derived_n = no_sharp_n = 0
    for shape in remaining:
        market_type, team_id, side, threshold = shape
        if market_type == "moneyline":
            team_is_home = team_id == home_id
            fair_p = p_moneyline(model, team_is_home)
        elif market_type == "spread":
            team_is_home = team_id == home_id
            fair_p = p_margin_over(model, team_is_home, threshold)
        else:  # total
            if model.mu_total is None:
                no_sharp_n += 1
                continue
            fair_p = p_total_over(model, threshold)

        mj = model_json(model)
        mj["shape"] = {
            "market_type": market_type,
            "outcome_team_id": team_id,
            "outcome_side": side,
            "threshold": str(threshold) if threshold is not None else None,
        }
        n = _insert_fair_value(
            session,
            run_id=run_id,
            game_id=game.id,
            market_type=market_type,
            outcome_team_id=team_id,
            outcome_side=side,
            threshold=threshold,
            fair_p=fair_p,
            fair_source="derived",
            n_groups=main_consensus.n_groups,
            disagreement=main_consensus.disagreement,
            newest_book_ts=main_consensus.newest_ts,
            staleness_s=_staleness_s(now, main_consensus.newest_ts),
            feed_kind=main_kind,
            feed_lag_s=main_lag,
            stale_allowance_s=main_allowance,
            pricing_version=PRICING_VERSION,
            model_json=mj,
            created_at=now,
        )
        derived_n += n

    return direct_n, derived_n, no_sharp_n


def compute_fair_values(
    session: Session,
    run_id: int,
    now: datetime,
    settings: Settings,
    lookback_s: int = 1200,
    game_ids: list[int] | None = None,
) -> FairCounts:
    games = _candidate_games(session, now, game_ids)
    direct_n = derived_n = no_sharp_n = error_n = 0
    errored_game_ids: set[int] = set()

    for game in games:
        try:
            with session.begin_nested():
                d, der, ns = _process_game(session, game, run_id, now, lookback_s, settings)
            direct_n += d
            derived_n += der
            no_sharp_n += ns
        except Exception:
            log.exception("fair value computation failed for game_id=%s", game.id)
            error_n += 1
            errored_game_ids.add(game.id)

    session.commit()
    return FairCounts(direct=direct_n, derived=derived_n, no_sharp=no_sharp_n, games=len(games), errors=error_n,
                       errored_game_ids=frozenset(errored_game_ids))
