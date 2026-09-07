import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from harness.pricing.direct import direct_fair, soft_fair
from harness.pricing.lines import Line, LineKey, ml_pair, spread_pair, total_pair

FIXD = Path(__file__).parent / "fixtures"
ROWS = json.loads((FIXD / "odds_lines_game.json").read_text())

HOME, AWAY = 14, 19
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
LOOKBACK_S = 1200


def _build_lines() -> dict[LineKey, Line]:
    """Pure-python equivalent of latest_book_lines: newest row per key within the lookback window."""
    since = NOW - timedelta(seconds=LOOKBACK_S)
    best: dict[LineKey, Line] = {}
    for r in ROWS:
        fetched_at = datetime.fromisoformat(r["fetched_at"])
        if fetched_at < since:
            continue
        point = Decimal(str(r["point"])) if r["point"] is not None else None
        key = LineKey(r["book"], r["market_type"], r["outcome_team_id"], r["outcome_side"], point)
        line = Line(key, Decimal(str(r["price_decimal"])), datetime.fromisoformat(r["book_last_update"]), fetched_at)
        existing = best.get(key)
        if existing is None or line.fetched_at > existing.fetched_at:
            best[key] = line
    return best


LINES = _build_lines()


def test_stale_lowvig_row_excluded_from_built_lines():
    key = LineKey("lowvig", "h2h", HOME, None, None)
    assert LINES[key].price == Decimal("1.6079")


def test_direct_fair_moneyline_has_two_groups():
    pairs = ml_pair(LINES, HOME, AWAY)
    result = direct_fair(pairs, NOW)
    assert result is not None
    consensus, fairs = result
    assert consensus.n_groups == 2
    assert {bf.book for bf in fairs} == {"pinnacle", "betonlineag", "lowvig"}


def test_direct_fair_spread_at_main_threshold():
    pairs = spread_pair(LINES, HOME, AWAY, Decimal("3.5"))
    result = direct_fair(pairs, NOW)
    assert result is not None
    consensus, _ = result
    assert consensus.n_groups == 2


def test_direct_fair_spread_at_alternate_threshold():
    pairs = spread_pair(LINES, HOME, AWAY, Decimal("6.5"))
    result = direct_fair(pairs, NOW)
    assert result is not None
    consensus, fairs = result
    assert consensus.n_groups == 2
    assert {bf.book for bf in fairs} == {"pinnacle", "betonlineag"}


def test_direct_fair_spread_with_no_lines_is_none():
    pairs = spread_pair(LINES, HOME, AWAY, Decimal("9.5"))
    assert pairs == {}
    assert direct_fair(pairs, NOW) is None


def test_direct_fair_total_at_44_5():
    pairs = total_pair(LINES, Decimal("44.5"))
    result = direct_fair(pairs, NOW)
    assert result is not None
    consensus, fairs = result
    assert consensus.n_groups == 2
    assert {bf.book for bf in fairs} == {"pinnacle", "betonlineag", "lowvig"}


def test_soft_fair_returns_decimal_for_draftkings():
    pairs = ml_pair(LINES, HOME, AWAY)
    fair = soft_fair(pairs, book="draftkings")
    assert isinstance(fair, Decimal)
    assert Decimal("0") < fair < Decimal("1")


def test_soft_fair_none_when_book_missing():
    pairs = spread_pair(LINES, HOME, AWAY, Decimal("6.5"))  # draftkings has no alternate line
    assert soft_fair(pairs, book="draftkings") is None
