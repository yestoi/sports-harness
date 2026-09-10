"""The weekly futures pass: discovery, the exact exclusion, the budget, the resume, and the row.

Every venue response here is a recorded or hand-built fixture; nothing in this file makes a call.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from harness.db.models import JobState
from harness.feeds.http import FetchResult
from harness.venues.kalshi.futures import (CATEGORY_HINT, FUTURES_PREFIXES, JOB_NAME,
                                           PAGE_PAUSE_S, REQUEST_BUDGET, RESUME_KEY,
                                           discover_series, parse_futures_markets,
                                           run_futures_snapshot, snapshot_week)
from harness.venues.kalshi.public import FOOTBALL_SERIES, decode_fixed_point

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)   # Tuesday 09:00 CT


def _result(body, status=200):
    return FetchResult(status=status, headers={}, body=body, fetched_at=NOW, url="x",
                       elapsed_s=0.0)


class FakeKalshi:
    """Records what was asked for and answers from a script. `_sleep_s` mirrors KalshiPublic's
    own attribute so the pass's page pause is observable."""

    def __init__(self, categories, series_by_category, markets_by_series):
        self.categories = categories
        self.series_by_category = series_by_category
        self.markets_by_series = markets_by_series
        self.asked = []
        self._sleep_s = 0.0

    def fetch_tags_by_categories(self):
        self.asked.append(("tags", None))
        return _result(self.categories)

    def fetch_series_all(self, category, max_pages=10):
        self.asked.append(("series", category))
        return [_result({"series": self.series_by_category.get(category, [])})]

    def fetch_markets_all(self, series_ticker, max_pages=20, status="open", min_settled_ts=None,
                          extra_params=None):
        self.asked.append(("markets", series_ticker))
        return [_result({"markets": self.markets_by_series.get(series_ticker, [])})]


def _market(ticker, event="KXNFLSB-27", **overrides):
    body = {"ticker": ticker, "event_ticker": event, "title": "Super Bowl winner",
            "yes_sub_title": "Kansas City", "market_type": "binary", "strike_type": "custom",
            "yes_bid_dollars": "0.12", "yes_ask_dollars": "0.14",
            "last_price_dollars": "0.13", "volume_fp": "1000.00",
            "open_interest_fp": "500.00", "close_time": "2027-02-08T00:00:00Z"}
    body.update(overrides)
    return body


def _kalshi():
    return FakeKalshi(
        categories={"Sports": ["nfl", "ncaaf"], "Politics": ["senate"]},
        series_by_category={"Sports": [{"ticker": t} for t in
                                       ("KXNFLSB", "KXNFLGAME", "KXNCAAFCHAMP", "KXNCAAFGAME",
                                        "KXNBAFINALS")]},
        markets_by_series={"KXNFLSB": [_market("KXNFLSB-27-KC")],
                           "KXNCAAFCHAMP": [_market("KXNCAAFCHAMP-27-LSU",
                                                    event="KXNCAAFCHAMP-27")]})


# --- discovery ---------------------------------------------------------------------------------

def test_discovery_keeps_only_the_two_prefixes(db_session):
    kept = discover_series(db_session, 1, _kalshi(), {"n": 0, "errors": [], "categories": []})
    assert "KXNBAFINALS" not in kept
    assert all(t.startswith(FUTURES_PREFIXES) for t in kept)


def test_discovery_excludes_the_per_game_series_by_exact_match(db_session):
    """Ruling A-I9: `FOOTBALL_SERIES` is imported, not restated, and the test is exact match --
    a prefix test would also swallow a future KXNFLGAMEMVP, which is exactly what H7 wants."""
    kept = discover_series(db_session, 1, _kalshi(), {"n": 0, "errors": [], "categories": []})
    assert set(kept).isdisjoint(FOOTBALL_SERIES)
    assert kept == ["KXNCAAFCHAMP", "KXNFLSB"]      # sorted, deterministic


def test_a_series_whose_ticker_merely_starts_with_a_per_game_name_is_kept(db_session):
    kalshi = _kalshi()
    kalshi.series_by_category["Sports"].append({"ticker": "KXNFLGAMEMVP"})
    assert "KXNFLGAMEMVP" in discover_series(db_session, 1, kalshi,
                                             {"n": 0, "errors": [], "categories": []})


def test_discovery_reads_the_categories_once_per_pass(db_session):
    kalshi = _kalshi()
    discover_series(db_session, 1, kalshi, {"n": 0, "errors": [], "categories": []})
    assert sum(1 for kind, _ in kalshi.asked if kind == "tags") == 1


def test_discovery_stores_every_body_it_reads(db_session):
    """Addendum §1.1: "raw bodies through `store_raw(source='kalshi_futures')`", and the
    categories read is "recorded once per pass". Discovery is two thirds of the pass's requests,
    so a discovery that stored nothing would leave H7's panel unauditable."""
    discover_series(db_session, 1, _kalshi(), {"n": 0, "errors": [], "categories": []})
    rows = db_session.execute(text(
        "select endpoint, count(*) from raw_responses where source = 'kalshi_futures' "
        "group by 1 order by 1")).all()
    assert [(r.endpoint, r.count) for r in rows] == [
        ("/search/tags_by_categories", 1), ("/series", 1)]


def test_only_the_football_categories_are_walked(db_session):
    kalshi = _kalshi()
    discover_series(db_session, 1, kalshi, {"n": 0, "errors": [], "categories": []})
    assert [arg for kind, arg in kalshi.asked if kind == "series"] == ["Sports"]
    assert CATEGORY_HINT == "sport"


def test_no_matching_category_falls_back_to_every_category(db_session):
    """A season where Kalshi renames the category must degrade to a slower pass, never to an
    empty one, and the fallback is recorded in the notes."""
    kalshi = _kalshi()
    kalshi.categories = {"Contests": ["nfl"], "Politics": ["senate"]}
    kalshi.series_by_category = {"Contests": [{"ticker": "KXNFLSB"}]}
    ctx = {"n": 0, "errors": [], "categories": []}
    assert discover_series(db_session, 1, kalshi, ctx) == ["KXNFLSB"]
    assert ctx["category_fallback"] is True


def test_discovery_unwraps_the_live_response_shape(db_session):
    """The live `GET /search/tags_by_categories` wraps the category map under a
    `tags_by_categories` key (Step 1, tests/fixtures/kalshi_tags_by_categories.json); discovery
    must unwrap it rather than treating the wrapper key itself as the only category."""
    kalshi = _kalshi()
    kalshi.categories = {"tags_by_categories": {"Sports": ["nfl"], "Politics": ["senate"]}}
    kept = discover_series(db_session, 1, kalshi, {"n": 0, "errors": [], "categories": []})
    assert kept == ["KXNCAAFCHAMP", "KXNFLSB"]


# --- parsing -----------------------------------------------------------------------------------

def test_the_dollars_and_fp_strings_are_decoded():
    rows = parse_futures_markets({"markets": [_market("KXNFLSB-27-KC")]})
    assert rows[0]["yes_bid"] == Decimal("0.12")
    assert rows[0]["last_price"] == Decimal("0.13")
    assert rows[0]["volume"] == Decimal("1000.00")
    assert rows[0]["open_interest"] == Decimal("500.00")
    assert decode_fixed_point("0.123456") == Decimal("0.123456")
    assert decode_fixed_point(None) is None and decode_fixed_point("") is None


def test_the_venue_market_type_is_kept_under_its_own_name():
    """Ruling A-M8: `market_type` in Kalshi's vocabulary is binary|scalar and has nothing to do
    with the harness's moneyline|spread|total. Two names, so they can never be confused."""
    rows = parse_futures_markets({"markets": [_market("KXNFLSB-27-KC", market_type="scalar")]})
    assert rows[0]["kalshi_market_type"] == "scalar"
    assert "market_type" not in rows[0]


def test_the_titles_are_sanitized():
    """Venue free text in a stored column. Ruling B-M8 puts `title` and `yes_sub_title` in the
    table; F60 keeps them out of a raw render, and the sanitizer is applied at write."""
    rows = parse_futures_markets({"markets": [
        _market("KXNFLSB-27-KC", title="<b>Super</b> Bowl\x00", yes_sub_title="KC\x07")]})
    assert rows[0]["title"] == "Super Bowl" and rows[0]["yes_sub_title"] == "KC"


def test_a_market_without_a_ticker_is_dropped():
    assert parse_futures_markets({"markets": [{"event_ticker": "X"}]}) == []


def test_an_unparseable_body_yields_no_rows():
    assert parse_futures_markets(None) == [] and parse_futures_markets({"markets": "x"}) == []


# --- the pass ------------------------------------------------------------------------------------

def test_the_week_is_an_iso_week_in_local_time():
    assert snapshot_week(NOW, "America/Chicago") == "2026-W38"


def test_a_pass_writes_rows_a_job_run_and_the_raw_bodies(db_session, env_settings):
    job = run_futures_snapshot(db_session, env_settings, _kalshi(), NOW, trigger="cron")
    assert job.job == JOB_NAME and job.status == "ok"
    assert job.notes["trigger"] == "cron"
    assert sorted(job.notes["series_reached"]) == ["KXNCAAFCHAMP", "KXNFLSB"]
    assert job.notes["requests"] <= REQUEST_BUDGET

    rows = db_session.execute(text(
        "select series_ticker, market_ticker, snapshot_week, kalshi_market_type, yes_bid "
        "from futures_snapshots order by market_ticker")).all()
    assert [r.market_ticker for r in rows] == ["KXNCAAFCHAMP-27-LSU", "KXNFLSB-27-KC"]
    assert {r.snapshot_week for r in rows} == {"2026-W38"}
    assert rows[0].yes_bid == Decimal("0.1200")

    stored = db_session.execute(text(
        "select count(*) from raw_responses where source = 'kalshi_futures'")).scalar()
    assert stored == 4          # the categories, one series page, two market pages


def test_a_hand_run_is_labelled_manual(db_session, env_settings):
    job = run_futures_snapshot(db_session, env_settings, _kalshi(), NOW, trigger="manual")
    assert job.notes["trigger"] == "manual"


def test_the_budget_stops_the_pass_and_records_where_it_stopped(db_session, env_settings):
    """Ruling A-I8: a bound pass records `budget_exhausted`, stores its resume index, and the
    next pass starts there instead of losing the same tail every week."""
    settings = env_settings
    kalshi = _kalshi()
    kalshi.series_by_category["Sports"] = [{"ticker": f"KXNFLX{i:02d}"} for i in range(10)]
    kalshi.markets_by_series = {f"KXNFLX{i:02d}": [_market(f"KXNFLX{i:02d}-27-A")]
                                for i in range(10)}
    job = run_futures_snapshot(db_session, settings, kalshi, NOW, trigger="cron",
                               request_budget=4)
    assert job.budget_exhausted is True
    assert job.notes["resume_after"] is not None
    resume = db_session.get(JobState, RESUME_KEY)
    assert resume is not None and resume.value > 0


def test_the_next_pass_resumes_where_the_last_one_stopped(db_session, env_settings):
    kalshi = _kalshi()
    kalshi.series_by_category["Sports"] = [{"ticker": f"KXNFLX{i:02d}"} for i in range(10)]
    kalshi.markets_by_series = {f"KXNFLX{i:02d}": [_market(f"KXNFLX{i:02d}-27-A")]
                                for i in range(10)}
    first = run_futures_snapshot(db_session, env_settings, kalshi, NOW, trigger="cron",
                                 request_budget=4)
    kalshi.asked.clear()
    second = run_futures_snapshot(db_session, env_settings, kalshi, NOW, trigger="cron",
                                  request_budget=4)
    walked = [arg for kind, arg in kalshi.asked if kind == "markets"]
    assert walked[0] != "KXNFLX00"
    assert second.notes["resumed_from"] == first.notes["resume_after"]


def test_a_changed_series_set_restarts_rather_than_resuming_into_the_wrong_series(
        db_session, env_settings):
    kalshi = _kalshi()
    kalshi.series_by_category["Sports"] = [{"ticker": f"KXNFLX{i:02d}"} for i in range(10)]
    kalshi.markets_by_series = {f"KXNFLX{i:02d}": [_market(f"KXNFLX{i:02d}-27-A")]
                                for i in range(10)}
    run_futures_snapshot(db_session, env_settings, kalshi, NOW, trigger="cron", request_budget=4)
    kalshi.series_by_category["Sports"] = [{"ticker": f"KXNCAAFY{i:02d}"} for i in range(10)]
    kalshi.markets_by_series = {f"KXNCAAFY{i:02d}": [_market(f"KXNCAAFY{i:02d}-27-A")]
                                for i in range(10)}
    second = run_futures_snapshot(db_session, env_settings, kalshi, NOW, trigger="cron",
                                  request_budget=4)
    assert second.notes["resume_reset"] is True


def test_a_failing_series_does_not_stop_the_pass(db_session, env_settings):
    class Broken(FakeKalshi):
        def fetch_markets_all(self, series_ticker, **kwargs):
            if series_ticker == "KXNFLSB":
                raise RuntimeError("venue down")
            return super().fetch_markets_all(series_ticker, **kwargs)

    base = _kalshi()
    kalshi = Broken(base.categories, base.series_by_category, base.markets_by_series)
    job = run_futures_snapshot(db_session, env_settings, kalshi, NOW, trigger="cron")
    assert job.status == "degraded"
    assert job.notes["errors"] and "KXNFLSB" in json.dumps(job.notes["errors"])
    assert db_session.execute(text("select count(*) from futures_snapshots")).scalar() == 1


def test_the_page_pause_is_a_tenth_of_a_second(db_session, env_settings):
    kalshi = _kalshi()
    run_futures_snapshot(db_session, env_settings, kalshi, NOW, trigger="cron")
    assert kalshi._sleep_s == PAGE_PAUSE_S
