"""The source inside the tick: the two guards, the due order, the change log, and the skips."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx
from sqlalchemy import text

from harness.db.models import Game, Team, WeatherPoint
from harness.feeds.nws import NwsClient
from harness.weather.snapshots import games_due, run_weather_source

FIXTURES = Path(__file__).parent / "fixtures"
POINTS = json.loads((FIXTURES / "nws_points_lsu.json").read_text())
HOURLY = json.loads((FIXTURES / "nws_forecast_hourly_lsu.json").read_text())
# The recorded hourly fixture's 156 periods run 2026-09-10T12:00Z through 2026-09-17T00:00Z
# (`startTime` "2026-09-10T07:00:00-05:00" .. "2026-09-16T18:00:00-05:00"). NOW is set so every
# `hours_ahead=10` kickoff in this file (and its kickoff -1h/+4h window) falls well inside that
# recorded range -- a game window outside it would make `periods_in_window` correctly, but
# vacuously, keep nothing.
NOW = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)


class _Budget:
    def __init__(self, remaining):
        self._remaining = remaining

    def ok(self):
        return self._remaining > 0

    def remaining_s(self):
        return self._remaining


def _seed_game(session, game_id, home_team_id, hours_ahead, sport="ncaaf"):
    session.add(Game(id=game_id, sport=sport, home_team_id=home_team_id, away_team_id=999,
                     kickoff_utc=NOW + timedelta(hours=hours_ahead), status="scheduled"))
    session.flush()


def _stub_hourly(url):
    respx.get(url).mock(return_value=httpx.Response(200, json=HOURLY))


def test_a_game_outside_seventy_two_hours_is_not_due(db_session):
    _seed_game(db_session, 1, 99, hours_ahead=100)
    assert games_due(db_session, NOW) == []


def test_due_games_come_back_oldest_snapshot_first(db_session):
    """Ruling A-M12. A budget that binds must not always starve the same game, so the game whose
    newest snapshot is oldest -- a game with none at all being oldest of all -- goes first."""
    _seed_game(db_session, 1, 99, hours_ahead=10)
    _seed_game(db_session, 2, 98, hours_ahead=10)
    db_session.execute(text(
        "insert into weather_snapshots (run_id, game_id, fetched_at, period_start, roof) "
        "values (1, 1, :ts, :ts, 'open')"), {"ts": NOW - timedelta(minutes=90)})
    db_session.flush()
    assert [g.game_id for g in games_due(db_session, NOW)] == [2, 1]


def test_a_game_snapshotted_inside_the_hour_is_not_due(db_session):
    _seed_game(db_session, 1, 99, hours_ahead=10)
    db_session.execute(text(
        "insert into weather_snapshots (run_id, game_id, fetched_at, period_start, roof) "
        "values (1, 1, :ts, :ts, 'open')"), {"ts": NOW - timedelta(minutes=10)})
    db_session.flush()
    assert games_due(db_session, NOW) == []


@respx.mock
def test_a_dome_is_never_fetched_and_never_gets_a_points_row(db_session, env_settings,
                                                             monkeypatch):
    """D2. The reason is recorded once per game rather than every hour for the rest of the
    season."""
    from harness.weather import snapshots as module
    from harness.weather.stadiums import Stadium

    dome = Stadium("ncaaf", 77, "DOME", "A Dome", 30.0, -90.0, "dome", "https://example.org")
    monkeypatch.setattr(module, "stadium_for", lambda *a, **k: dome)
    _seed_game(db_session, 1, 77, hours_ahead=10)
    client = NwsClient(env_settings)
    try:
        counts = run_weather_source(db_session, 1, client, env_settings, NOW, _Budget(60), {})
    finally:
        client.close()
    assert counts["skipped"] == {"1": "dome"}
    assert db_session.execute(text("select count(*) from weather_points")).scalar() == 0
    assert respx.calls.call_count == 0


@respx.mock
def test_a_game_with_no_stadium_row_is_skipped_with_its_reason(db_session, env_settings,
                                                               monkeypatch):
    from harness.weather import snapshots as module

    monkeypatch.setattr(module, "stadium_for", lambda *a, **k: None)
    _seed_game(db_session, 1, 12345, hours_ahead=10)
    client = NwsClient(env_settings)
    try:
        counts = run_weather_source(db_session, 1, client, env_settings, NOW, _Budget(60), {})
    finally:
        client.close()
    assert counts["skipped"] == {"1": "no stadium"}


@respx.mock
def test_a_retractable_roof_is_fetched_and_labelled(db_session, env_settings, monkeypatch):
    from harness.weather import snapshots as module
    from harness.weather.stadiums import Stadium

    site = Stadium("nfl", 22, "ARI", "State Farm", 33.5276, -112.2626, "retractable",
                   "https://example.org")
    monkeypatch.setattr(module, "stadium_for", lambda *a, **k: site)
    respx.get("https://api.weather.gov/points/33.5276,-112.2626").mock(
        return_value=httpx.Response(200, json=POINTS))
    _stub_hourly(POINTS["properties"]["forecastHourly"])
    _seed_game(db_session, 1, 22, hours_ahead=10, sport="nfl")

    client = NwsClient(env_settings)
    try:
        run_weather_source(db_session, 1, client, env_settings, NOW, _Budget(60), {})
    finally:
        client.close()
    roofs = db_session.execute(text("select distinct roof from weather_snapshots")).scalars().all()
    assert roofs == ["retractable"]


@respx.mock
def test_a_second_pass_writes_nothing_when_the_forecast_has_not_changed(db_session,
                                                                        env_settings,
                                                                        monkeypatch):
    """Addendum §4's disk budget: a forecast for a fixed hour barely moves between reads, so the
    writer is a change log keyed (game_id, period_start), the same rule game_score_events uses."""
    from harness.weather import snapshots as module
    from harness.weather.stadiums import Stadium

    lsu = Stadium("ncaaf", 99, "LSU", "Tiger Stadium", 30.4118, -91.1836, "open",
                  "https://example.org")
    monkeypatch.setattr(module, "stadium_for", lambda *a, **k: lsu)
    respx.get("https://api.weather.gov/points/30.4118,-91.1836").mock(
        return_value=httpx.Response(200, json=POINTS))
    _stub_hourly(POINTS["properties"]["forecastHourly"])
    _seed_game(db_session, 1, 99, hours_ahead=10)

    client = NwsClient(env_settings)
    try:
        first = run_weather_source(db_session, 1, client, env_settings, NOW, _Budget(60), {})
        db_session.execute(text("update weather_snapshots set fetched_at = fetched_at "
                                "- interval '2 hours'"))
        db_session.flush()
        second = run_weather_source(db_session, 1, client, env_settings,
                                    NOW + timedelta(hours=2), _Budget(60), {})
    finally:
        client.close()
    assert first["written"] > 0
    assert second["written"] == 0 and second["fetched"] == 1


@respx.mock
def test_a_changed_temperature_appends_a_row(db_session, env_settings, monkeypatch):
    from harness.weather import snapshots as module
    from harness.weather.stadiums import Stadium

    lsu = Stadium("ncaaf", 99, "LSU", "Tiger Stadium", 30.4118, -91.1836, "open",
                  "https://example.org")
    monkeypatch.setattr(module, "stadium_for", lambda *a, **k: lsu)
    respx.get("https://api.weather.gov/points/30.4118,-91.1836").mock(
        return_value=httpx.Response(200, json=POINTS))
    warmer = json.loads(json.dumps(HOURLY))
    for period in warmer["properties"]["periods"]:
        period["temperature"] = int(period["temperature"]) + 5
    respx.get(POINTS["properties"]["forecastHourly"]).mock(
        side_effect=[httpx.Response(200, json=HOURLY), httpx.Response(200, json=warmer)])
    _seed_game(db_session, 1, 99, hours_ahead=10)

    client = NwsClient(env_settings)
    try:
        first = run_weather_source(db_session, 1, client, env_settings, NOW, _Budget(60), {})
        db_session.execute(text("update weather_snapshots set fetched_at = fetched_at "
                                "- interval '2 hours'"))
        db_session.flush()
        second = run_weather_source(db_session, 1, client, env_settings,
                                    NOW + timedelta(hours=2), _Budget(60), {})
    finally:
        client.close()
    assert second["written"] == first["written"]


@respx.mock
def test_a_404_on_the_hourly_url_re_resolves_points_once(db_session, env_settings, monkeypatch):
    """Ruling A-I6: a stale gridpoint URL must not blind a stadium for the season."""
    from harness.weather import snapshots as module
    from harness.weather.stadiums import Stadium

    lsu = Stadium("ncaaf", 99, "LSU", "Tiger Stadium", 30.4118, -91.1836, "open",
                  "https://example.org")
    monkeypatch.setattr(module, "stadium_for", lambda *a, **k: lsu)
    points = respx.get("https://api.weather.gov/points/30.4118,-91.1836").mock(
        return_value=httpx.Response(200, json=POINTS))
    respx.get(POINTS["properties"]["forecastHourly"]).mock(
        side_effect=[httpx.Response(404), httpx.Response(200, json=HOURLY)])
    _seed_game(db_session, 1, 99, hours_ahead=10)
    db_session.add(WeatherPoint(sport="ncaaf", team_id=99, office="LIX", grid_x=1, grid_y=1,
                                forecast_hourly_url=POINTS["properties"]["forecastHourly"],
                                fetched_at=NOW - timedelta(days=2)))
    db_session.flush()

    client = NwsClient(env_settings)
    try:
        counts = run_weather_source(db_session, 1, client, env_settings, NOW, _Budget(60), {})
    finally:
        client.close()
    assert points.call_count == 1
    assert counts["reresolved"] == 1


@respx.mock
def test_the_budget_stops_the_pass_between_games(db_session, env_settings, monkeypatch):
    from harness.weather import snapshots as module
    from harness.weather.stadiums import Stadium

    monkeypatch.setattr(module, "stadium_for", lambda sport, home, away, day: Stadium(
        sport, home, "X", "X", 30.0 + home / 1000, -90.0, "open", "https://example.org"))
    for game_id, team in ((1, 91), (2, 92), (3, 93)):
        respx.get(f"https://api.weather.gov/points/{30.0 + team / 1000},-90.0").mock(
            return_value=httpx.Response(200, json=POINTS))
        _seed_game(db_session, game_id, team, hours_ahead=10)
    _stub_hourly(POINTS["properties"]["forecastHourly"])

    client = NwsClient(env_settings)
    try:
        counts = run_weather_source(db_session, 1, client, env_settings, NOW, _Budget(0.0), {})
    finally:
        client.close()
    assert counts["fetched"] == 0 and counts["budget_exhausted"] is True
