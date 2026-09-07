import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import OddsSnapshot
from harness.pricing.lines import Line, LineKey, latest_book_lines, ml_pair, spread_pair, total_pair

FIXD = Path(__file__).parent / "fixtures"
ROWS = json.loads((FIXD / "odds_lines_game.json").read_text())

GAME_ID = 501
HOME, AWAY = 14, 19
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def _insert_fixture(db_session):
    for i, r in enumerate(ROWS):
        db_session.add(OddsSnapshot(
            raw_id=i + 1,
            run_id=1,
            book=r["book"],
            game_id=GAME_ID,
            market_type=r["market_type"],
            outcome_team_id=r["outcome_team_id"],
            outcome_side=r["outcome_side"],
            point=Decimal(str(r["point"])) if r["point"] is not None else None,
            price_decimal=Decimal(str(r["price_decimal"])),
            book_last_update=datetime.fromisoformat(r["book_last_update"]),
            fetched_at=datetime.fromisoformat(r["fetched_at"]),
        ))
    db_session.flush()


def test_one_line_per_key(db_session):
    _insert_fixture(db_session)
    lines = latest_book_lines(db_session, GAME_ID, NOW, lookback_s=1200)
    keys = list(lines.keys())
    assert len(keys) == len(set(keys))
    # a book/market/team/side/point combination is queried at most once
    seen = set()
    for k in keys:
        combo = (k.book, k.market_type, k.outcome_team_id, k.outcome_side, k.point)
        assert combo not in seen
        seen.add(combo)


def test_stale_row_excluded_by_default_lookback(db_session):
    _insert_fixture(db_session)
    lines = latest_book_lines(db_session, GAME_ID, NOW, lookback_s=1200)
    key = LineKey("lowvig", "h2h", HOME, None, None)
    assert lines[key].price == Decimal("1.6079")  # the fresh (2 min old) price, not the 40-min stale one


def test_tight_lookback_excludes_even_the_fresh_row(db_session):
    _insert_fixture(db_session)
    lines = latest_book_lines(db_session, GAME_ID, NOW, lookback_s=60)
    key = LineKey("lowvig", "h2h", HOME, None, None)
    assert key not in lines


def test_spread_pair_at_main_and_alternate_thresholds(db_session):
    _insert_fixture(db_session)
    lines = latest_book_lines(db_session, GAME_ID, NOW, lookback_s=1200)
    main = spread_pair(lines, HOME, AWAY, Decimal("3.5"))
    assert set(main.keys()) == {"pinnacle", "betonlineag", "lowvig", "draftkings"}
    for team_line, opp_line in main.values():
        assert team_line.key.point == Decimal("-3.5")
        assert opp_line.key.point == Decimal("3.5")

    alt = spread_pair(lines, HOME, AWAY, Decimal("6.5"))
    assert set(alt.keys()) == {"pinnacle", "betonlineag"}


def test_spread_pair_markets_restricted_to_spreads_excludes_alternate(db_session):
    _insert_fixture(db_session)
    lines = latest_book_lines(db_session, GAME_ID, NOW, lookback_s=1200)
    # 6.5 only exists as an alternate_spreads line; restricting to ("spreads",) must find nothing.
    alt_only = spread_pair(lines, HOME, AWAY, Decimal("6.5"), markets=("spreads",))
    assert alt_only == {}
    # 3.5 exists as a real spreads line; restricting still finds it.
    main_only = spread_pair(lines, HOME, AWAY, Decimal("3.5"), markets=("spreads",))
    assert set(main_only.keys()) == {"pinnacle", "betonlineag", "lowvig", "draftkings"}


def test_total_pair_at_44_5(db_session):
    _insert_fixture(db_session)
    lines = latest_book_lines(db_session, GAME_ID, NOW, lookback_s=1200)
    pairs = total_pair(lines, Decimal("44.5"))
    assert set(pairs.keys()) == {"pinnacle", "betonlineag", "lowvig"}
    for over_line, under_line in pairs.values():
        assert over_line.key.outcome_side == "over"
        assert under_line.key.outcome_side == "under"


def test_total_pair_falls_back_to_alternate_totals_by_default(db_session):
    _insert_fixture(db_session)
    extra_fetched_at = NOW - timedelta(minutes=2)
    db_session.add_all([
        OddsSnapshot(raw_id=9001, run_id=1, book="pinnacle", game_id=GAME_ID, market_type="alternate_totals",
                     outcome_team_id=None, outcome_side="over", point=Decimal("47.5"),
                     price_decimal=Decimal("1.95"), book_last_update=extra_fetched_at, fetched_at=extra_fetched_at),
        OddsSnapshot(raw_id=9002, run_id=1, book="pinnacle", game_id=GAME_ID, market_type="alternate_totals",
                     outcome_team_id=None, outcome_side="under", point=Decimal("47.5"),
                     price_decimal=Decimal("1.90"), book_last_update=extra_fetched_at, fetched_at=extra_fetched_at),
    ])
    db_session.flush()
    lines = latest_book_lines(db_session, GAME_ID, NOW, lookback_s=1200)

    # default markets fall back to alternate_totals
    pairs = total_pair(lines, Decimal("47.5"))
    assert set(pairs.keys()) == {"pinnacle"}

    # restricting to ("totals",) must not find the alternate
    restricted = total_pair(lines, Decimal("47.5"), markets=("totals",))
    assert restricted == {}


def test_ml_pair_all_four_books(db_session):
    _insert_fixture(db_session)
    lines = latest_book_lines(db_session, GAME_ID, NOW, lookback_s=1200)
    pairs = ml_pair(lines, HOME, AWAY)
    assert set(pairs.keys()) == {"pinnacle", "betonlineag", "lowvig", "draftkings"}
    for team_line, opp_line in pairs.values():
        assert team_line.key.outcome_team_id == HOME
        assert opp_line.key.outcome_team_id == AWAY
