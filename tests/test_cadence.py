from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from harness.feeds.espn import Kickoff
from harness.recorder.cadence import alternates_due, interval_for, is_due, select_ladders, select_trade_tickers
from harness.venues.kalshi.public import MarketSummary

TZ = "America/Chicago"
UTC = timezone.utc


def k(sport, ts, id_="1"):
    return Kickoff(sport, id_, ts, "H", "A", "STATUS_SCHEDULED")


def test_quiet_hours_returns_none():
    # 03:00 CT on a Wednesday = 08:00 UTC
    now = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    assert interval_for("nfl", now, [], TZ) is None


def test_weekday_no_games_900():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)  # 10:00 CT Wed
    assert interval_for("nfl", now, [], TZ) == 900


def test_weekend_300():
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)  # Sat 10:00 CT
    assert interval_for("ncaaf", now, [], TZ) == 300


def test_game_window_120():
    kick = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)  # Wed 19:20 CT
    now = kick - timedelta(hours=2)
    assert interval_for("nfl", now, [k("nfl", kick)], TZ) == 120
    assert interval_for("ncaaf", now, [k("nfl", kick)], TZ) == 900  # other sport unaffected


def test_nfl_inactives_burst_20():
    kick = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
    now = kick - timedelta(minutes=80)
    assert interval_for("nfl", now, [k("nfl", kick)], TZ) == 20
    assert interval_for("ncaaf", now, [k("nfl", kick)], TZ) == 300  # Sunday, no ncaaf burst


def test_is_due():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    assert is_due(None, now, 120)
    assert not is_due(now - timedelta(seconds=60), now, 120)
    assert is_due(now - timedelta(seconds=121), now, 120)
    assert not is_due(None, now, None)


def test_alternates_due():
    # Task 3b brings the near/far split back into the interface; today's settings keep
    # odds_alt_interval_near_s and odds_alt_interval_far_s equal (both 120), so a near and a
    # far event inside the window are equally due -- this test pins that equal-settings case.
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    events = [("near", now + timedelta(hours=2)), ("far", now + timedelta(hours=20)), ("toofar", now + timedelta(hours=48))]
    last = {"near": now - timedelta(seconds=130), "far": now - timedelta(seconds=130)}
    assert alternates_due(now, events, last, near_s=120, far_s=120) == ["near", "far"]
    assert alternates_due(now, events, {}, near_s=120, far_s=120) == ["near", "far"]


def test_alternates_due_near_and_far_intervals_are_independently_configurable():
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    far = ("far", now + timedelta(hours=30))
    assert alternates_due(now, [far], {"far": now - timedelta(seconds=119)}, near_s=120, far_s=120) == []
    assert alternates_due(now, [far], {"far": now - timedelta(seconds=120)}, near_s=120, far_s=120) == ["far"]
    assert alternates_due(now, [far], {"far": now - timedelta(seconds=899)}, near_s=999999, far_s=900) == []
    assert alternates_due(now, [far], {"far": now - timedelta(seconds=900)}, near_s=999999, far_s=900) == ["far"]


def test_alternates_due_default_far_interval_differs_from_near():
    # The interface's own defaults (near_s=120, far_s=900) are the pre-U1 shape. Production
    # settings currently pass 120/120 so the split is a no-op there, but the function itself
    # still tells near and far apart when nothing overrides its defaults.
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    far = ("far", now + timedelta(hours=20))
    last = {"far": now - timedelta(seconds=200)}
    assert alternates_due(now, [far], last) == []  # 200s < the default far_s of 900
    assert alternates_due(now, [far], last, far_s=120) == ["far"]


def test_alternates_due_near_boundary_is_180_minutes():
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    edge_near = ("edge_near", now + timedelta(minutes=180))
    edge_far = ("edge_far", now + timedelta(minutes=181))
    last = {"edge_near": now - timedelta(seconds=200), "edge_far": now - timedelta(seconds=200)}
    # 180 minutes out is still "near" (near_s=120 <= 200s elapsed => due); 181 minutes is
    # "far" and a far_s of 999999 is nowhere near due.
    assert alternates_due(now, [edge_near], last, near_s=120, far_s=999999) == ["edge_near"]
    assert alternates_due(now, [edge_far], last, near_s=120, far_s=999999) == []


def test_alternates_due_40h_out_is_never_due():
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    toofar = ("toofar", now + timedelta(hours=40))
    assert alternates_due(now, [toofar], {}) == []
    assert alternates_due(now, [toofar], {}, near_s=900, far_s=900, window_h=36) == []


def test_alternates_due_window_h_is_configurable():
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    out_of_default_window = ("x", now + timedelta(hours=40))
    assert alternates_due(now, [out_of_default_window], {}, window_h=48) == ["x"]


def test_alternates_due_uses_settings_cadence(env_settings):
    # Task 3b item 9: alternates_due reads Task 1's odds_alt_interval_near_s/far_s/window_h
    # settings; today they are 120/120/36 so this just proves the wiring, not a cadence change.
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    near = ("near", now + timedelta(hours=2))
    kwargs = dict(near_s=env_settings.odds_alt_interval_near_s, far_s=env_settings.odds_alt_interval_far_s,
                 window_h=env_settings.odds_alt_window_h)
    assert alternates_due(now, [near], {"near": now - timedelta(seconds=119)}, **kwargs) == []
    assert alternates_due(now, [near], {"near": now - timedelta(seconds=120)}, **kwargs) == ["near"]


def _ms(ticker, ev_date, bid, ask, vol):
    return MarketSummary(ticker, "E", "S", ev_date, Decimal(bid) if bid else None, Decimal(ask) if ask else None,
                         Decimal(vol), None)


def test_select_ladders_filters_and_caps():
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)  # Sat 15:00 CT
    kicks = [k("ncaaf", now + timedelta(hours=1))]
    today = date(2026, 9, 12)
    ms = [
        _ms("in_band", today, "0.40", "0.42", "100"),
        _ms("edge_band", today, "0.15", "0.25", "500"),
        _ms("out_band", today, "0.05", "0.08", "900"),
        _ms("no_quote", today, None, "0.50", "900"),
        _ms("tomorrow", date(2026, 9, 13), "0.50", "0.52", "900"),
    ]
    assert select_ladders(now, ms, kicks, TZ, cap=10) == ["edge_band", "in_band"]
    assert select_ladders(now, ms, kicks, TZ, cap=1) == ["edge_band"]
    assert select_ladders(now, ms, [], TZ, cap=10) == []


def test_select_trade_tickers():
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    ms = [_ms("changed", None, None, None, "12"), _ms("same", None, None, None, "10"),
          _ms("new_quiet", None, None, None, "0"), _ms("new_active", None, None, None, "3")]
    wm = {"changed": (now - timedelta(hours=1), Decimal("10")), "same": (now - timedelta(hours=1), Decimal("10"))}
    out = select_trade_tickers(now, ms, wm)
    assert out == [("changed", now - timedelta(hours=1, seconds=5)), ("new_active", now - timedelta(hours=24))]


def test_quiet_hours_suppressed_while_a_game_of_that_sport_is_in_progress():
    now = datetime(2026, 9, 13, 7, 0, tzinfo=UTC)  # 02:00 CT Sunday
    kick = now - timedelta(hours=2)
    assert interval_for("ncaaf", now, [k("ncaaf", kick)], TZ) == 120
    assert interval_for("ncaaf", now, [], TZ) is None
    assert interval_for("nfl", now, [k("ncaaf", kick)], TZ) is None  # other sport still quiet
    stale = now - timedelta(hours=5)  # ended more than 4h ago
    assert interval_for("ncaaf", now, [k("ncaaf", stale)], TZ) is None
