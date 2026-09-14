import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from harness.feeds.espn import EspnClient, Kickoff, parse_kickoffs
from harness.feeds.http import HttpClient

FIX = json.loads((Path(__file__).parent / "fixtures" / "espn_nfl_scoreboard.json").read_text())


def test_parse_kickoffs_skips_malformed():
    out = parse_kickoffs("nfl", FIX)
    assert out == [Kickoff("nfl", "401872656", datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc),
                           "Seattle Seahawks", "New England Patriots", "STATUS_SCHEDULED")]


@respx.mock
def test_fetch_scoreboard_urls():
    r1 = respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=FIX))
    r2 = respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    c = EspnClient(HttpClient(1, sleep=lambda s: None), "https://e")
    c.fetch_scoreboard("nfl")
    c.fetch_scoreboard("ncaaf")
    assert r1.called and dict(r2.calls[0].request.url.params) == {"groups": "80", "limit": "400"}


@respx.mock
def test_fetch_scoreboard_dates_param_added_alongside_existing_params():
    """Fix 14: the dated re-fetch adds `dates=YYYYMMDD` without dropping a sport's other
    params (ncaaf's `groups`/`limit`), and adds it alone for nfl (which has none)."""
    r1 = respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    r2 = respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    c = EspnClient(HttpClient(1, sleep=lambda s: None), "https://e")
    c.fetch_scoreboard("nfl", dates="20260908")
    c.fetch_scoreboard("ncaaf", dates="20260908")
    assert dict(r1.calls[0].request.url.params) == {"dates": "20260908"}
    assert dict(r2.calls[0].request.url.params) == {"groups": "80", "limit": "400", "dates": "20260908"}


@respx.mock
def test_the_summary_and_roster_fetchers_use_the_recorder_s_own_host_and_paths(env_settings):
    """Invariant 8 as amended (the user's ruling, journal 184 item 3): summary and roster stay
    on the recorder's own host, with ESPN's own sport segment (`nfl` / `college-football`), the
    same mapping `fetch_scoreboard` already uses. The game log is the one pinned v3 path on the
    browser-facing host and is never asked of this one -- that host has no game log (it answered
    404), and no other path on the v3 host is fetched.
    """
    summary = respx.get("https://e/nfl/summary").mock(return_value=httpx.Response(200, json={}))
    roster = respx.get("https://e/college-football/teams/99/roster").mock(
        return_value=httpx.Response(200, json={}))
    c = EspnClient(HttpClient(1, sleep=lambda s: None), "https://e", env_settings.espn_gamelog_url)
    c.fetch_summary("nfl", "401671789")
    c.fetch_roster("ncaaf", "99")
    assert dict(summary.calls[0].request.url.params) == {"event": "401671789"}
    assert roster.called
    assert {call.request.url.host for call in respx.calls} == {"e"}
    assert env_settings.espn_gamelog_url.startswith(
        "https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/")


# --- the game log: one pinned v3 path on ESPN's browser-facing host (journal 184 item 3) -----

GAMELOG_ATHLETE = "4426348"
#: The two keys the v3 body carries when the player has games; the measured empty body has
#: neither (`{"filters": [...]}` only).
GAMELOG_BODY = {"names": ["passingYards"], "labels": ["YDS"],
                "seasonTypes": [{"displayName": "2026 Regular Season", "categories": [
                    {"events": [{"eventId": "401872656", "stats": ["241"]}]}]}]}


def _gamelog_client(env_settings):
    return EspnClient(HttpClient(1, sleep=lambda s: None), "https://e",
                      env_settings.espn_gamelog_url)


@respx.mock
def test_the_game_log_fetcher_calls_exactly_the_pinned_settings_url(env_settings):
    """The user's ruling (journal 184 item 3): this one v3 path on `site.web.api.espn.com`,
    nothing else on that host. The URL is `Settings.espn_gamelog_url` with `{athlete_id}`
    formatted in -- read from settings, never rebuilt from parts here.
    """
    url = env_settings.espn_gamelog_url.format(athlete_id=GAMELOG_ATHLETE)
    route = respx.get(url).mock(return_value=httpx.Response(200, json=GAMELOG_BODY))
    result = _gamelog_client(env_settings).fetch_gamelog("nfl", GAMELOG_ATHLETE)
    assert route.called
    assert str(route.calls[0].request.url) == url
    assert result is not None and result.status == 200 and result.body == GAMELOG_BODY


@respx.mock
def test_another_sport_makes_no_game_log_request_at_all(env_settings, caplog):
    """The v3 path is NFL-only today, so a college athlete is the `no season data yet` outcome
    without a request -- never a second host, never a guessed college path."""
    with caplog.at_level(logging.WARNING):
        assert _gamelog_client(env_settings).fetch_gamelog("ncaaf", "99") is None
    assert respx.calls.call_count == 0
    assert "99" in caplog.text


@pytest.mark.parametrize("name,responder", [
    ("404", lambda request: httpx.Response(404, json={"error": "not found"})),
    ("500", lambda request: httpx.Response(500, text="upstream")),
    ("not json", lambda request: httpx.Response(200, text="<html>no</html>")),
    ("transport", httpx.ConnectError("boom")),
])
def test_a_game_log_fetch_that_fails_is_soft_and_names_the_athlete(env_settings, caplog, name,
                                                                   responder):
    """That host is browser-facing and less stable than the recorder's, so every failure is the
    `no season data yet` outcome plus one WARNING naming the athlete -- never an exception that
    reaches a tick (journal 184 item 3).
    """
    url = env_settings.espn_gamelog_url.format(athlete_id=GAMELOG_ATHLETE)
    with respx.mock:
        respx.get(url).mock(side_effect=responder)
        with caplog.at_level(logging.WARNING):
            assert _gamelog_client(env_settings).fetch_gamelog("nfl", GAMELOG_ATHLETE) is None
    assert GAMELOG_ATHLETE in caplog.text


@respx.mock
def test_a_client_built_without_the_game_log_url_never_fetches(env_settings, caplog):
    """The three existing construction sites pass two arguments; a client without the pinned URL
    reads `no season data yet` rather than inventing one."""
    c = EspnClient(HttpClient(1, sleep=lambda s: None), "https://e")
    with caplog.at_level(logging.WARNING):
        assert c.fetch_gamelog("nfl", GAMELOG_ATHLETE) is None
    assert respx.calls.call_count == 0


def test_an_athlete_id_that_is_not_digits_is_never_interpolated_into_the_pinned_url(
        env_settings, caplog):
    """Task 3 review, carried item 1: the athlete id is formatted into the one pinned v3 path,
    so anything but digits could walk that URL to another path on that host (`../../teams/1`).
    ESPN athlete ids are digits; anything else asks nothing at all.
    """
    with respx.mock:
        route = respx.get(url__regex=r".*").mock(
            return_value=httpx.Response(200, json=GAMELOG_BODY))
        with caplog.at_level(logging.WARNING):
            for bad in ("../../teams/1/roster", "4426348/../../x", "", "abc", "44 26"):
                assert _gamelog_client(env_settings).fetch_gamelog("nfl", bad) is None
        assert route.call_count == 0
    assert "athlete" in caplog.text


@respx.mock
def test_a_player_with_no_season_data_is_logged_at_info_not_as_a_fault(env_settings, caplog):
    """The controller's ruling on the Task 3 review: a player with no games yet is the expected
    path in week one, not an abnormal one, so it is INFO and is counted per pass; WARNING stays
    for the transport, status and shape failures above.
    """
    url = env_settings.espn_gamelog_url.format(athlete_id=GAMELOG_ATHLETE)
    respx.get(url).mock(return_value=httpx.Response(200, json={"filters": [{"name": "season"}]}))
    with caplog.at_level(logging.INFO):
        assert _gamelog_client(env_settings).fetch_gamelog("nfl", GAMELOG_ATHLETE) is None
    records = [r for r in caplog.records if GAMELOG_ATHLETE in r.getMessage()]
    assert records and all(r.levelno == logging.INFO for r in records)
