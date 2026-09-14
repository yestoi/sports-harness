import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from harness.feeds.http import FetchResult, HttpClient

log = logging.getLogger(__name__)

Sport = Literal["nfl", "ncaaf"]
_PATH = {"nfl": "/nfl/scoreboard", "ncaaf": "/college-football/scoreboard"}
_PARAMS = {"nfl": None, "ncaaf": {"groups": "80", "limit": "400"}}
#: The summary endpoint of addendum §4.1, on the same host and the same sport
#: segment as the scoreboard above it.
_SUMMARY_PATH = {"nfl": "/nfl/summary", "ncaaf": "/college-football/summary"}


@dataclass(frozen=True)
class Kickoff:
    sport: str
    espn_event_id: str
    kickoff_utc: datetime
    home: str
    away: str
    status: str


def parse_kickoffs(sport: str, body: dict | list | None) -> list[Kickoff]:
    if not isinstance(body, dict):
        return []
    out: list[Kickoff] = []
    for ev in body.get("events", []):
        try:
            ts = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
            comps = ev["competitions"][0]["competitors"]
            home = next(c["team"]["displayName"] for c in comps if c["homeAway"] == "home")
            away = next(c["team"]["displayName"] for c in comps if c["homeAway"] == "away")
            status = ev.get("status", {}).get("type", {}).get("name", "")
            out.append(Kickoff(sport, str(ev["id"]), ts, home, away, status))
        except (KeyError, ValueError, IndexError, StopIteration, AttributeError):
            continue
    return out


class EspnClient:
    def __init__(self, http: HttpClient, base_url: str, gamelog_url: str | None = None):
        self._http = http
        self._base = base_url.rstrip("/")
        #: `Settings.espn_gamelog_url`: the one pinned path on ESPN's browser-facing host
        #: (journal 184 item 3). Optional so the existing construction sites are unchanged; a
        #: client built without it never fetches a game log and every caller reads
        #: `no season data yet`.
        self._gamelog_url = gamelog_url

    def fetch_scoreboard(self, sport: Sport, dates: str | None = None) -> FetchResult:
        """`dates` (ESPN's own `YYYYMMDD` format) asks for a specific day's scoreboard rather
        than "today" in US/Eastern -- fix 14's dated re-fetch for games that fell off the
        undated body at the Eastern midnight rollover. The path and every other param are
        unchanged."""
        params = dict(_PARAMS[sport]) if _PARAMS[sport] else {}
        if dates:
            params["dates"] = dates
        return self._http.get(f"{self._base}{_PATH[sport]}", params=params or None, redact_params=())

    def fetch_summary(self, sport: Sport, espn_event_id: str) -> FetchResult:
        """One game's box score and scoring plays (addendum §4.1). The recorder's own host; no
        key, no credential, no new outbound host (invariant 8)."""
        return self._http.get(f"{self._base}{_SUMMARY_PATH[sport]}",
                              params={"event": str(espn_event_id)}, redact_params=())

    def fetch_roster(self, sport: Sport, team_id: int | str) -> FetchResult:
        """One team's roster (addendum §4.1), fetched once per team per week for the teams of
        the watched prop events. The sport segment is taken from `_PATH` rather than repeated."""
        return self._http.get(f"{self._base}{_PATH[sport].rsplit('/', 1)[0]}"
                              f"/teams/{team_id}/roster", params=None, redact_params=())

    def fetch_gamelog(self, sport: Sport, athlete_id: str) -> FetchResult | None:
        """One athlete's game log, the draft context line's source (addendum §4.1).

        The user's ruling (journal 184 item 3, carried by the plan's invariant 8) pins this to
        one path -- `Settings.espn_gamelog_url`, ESPN's v3 NFL game log on the browser-facing
        `site.web.api.espn.com`, with `{athlete_id}` formatted in -- and to nothing else on that
        host. The recorder's own host (`espn_base_url`) has no game log: its
        `/{sport}/athletes/{id}/gamelog` answered 404 for every athlete tried.

        That host is browser-facing and less stable than the recorder's, so this fetch **fails
        soft**. A sport other than NFL (the v3 path is NFL-only today, so a college athlete asks
        nothing), an unconfigured URL, a transport error, a non-200, a body that is not a JSON
        object, and an athlete id that is not ASCII digits each return `None` with one WARNING
        naming the athlete; a body carrying neither `names` nor `seasonTypes` (the measured
        `{"filters": [...]}` body of a player with no games) is the expected week-one path and
        is INFO, counted per pass by the caller. The caller writes
        `parse_gamelog(result.body if result else None, athlete_id)`, reads `no season data
        yet`, and never sees an exception reach a tick.
        """
        if sport not in _PATH:      # the same sport validation the other three fetchers get
            raise KeyError(sport)
        if sport != "nfl" or not self._gamelog_url:
            log.warning("espn gamelog: no fetch for athlete %s (sport=%s, url configured=%s)",
                        athlete_id, sport, bool(self._gamelog_url))
            return None
        # Task 3 review, carried item 1: the id is interpolated into the one pinned path, so
        # anything but ASCII digits could walk that URL to another path on that host
        # (`../../teams/1/roster`). ESPN athlete ids are digits; anything else asks nothing.
        athlete = str(athlete_id)
        if not (athlete.isascii() and athlete.isdigit()):
            log.warning("espn gamelog: refusing a non-numeric athlete id %r", athlete_id)
            return None
        try:
            # Carried item 2: the `.format()` is inside the try with the request it builds, so
            # a URL template that lost its placeholder is the same soft failure as a transport
            # error rather than an exception reaching a tick.
            url = self._gamelog_url.format(athlete_id=athlete)
            result = self._http.get(url, params=None, redact_params=())
        except Exception as exc:  # fail soft by ruling: a browser-facing host never fails a tick
            log.warning("espn gamelog: fetch failed for athlete %s: %r", athlete_id, exc)
            return None
        if result.status != 200 or not isinstance(result.body, dict):
            log.warning("espn gamelog: athlete %s answered status %s with %s body",
                        athlete_id, result.status, type(result.body).__name__)
            return None
        if "names" not in result.body or "seasonTypes" not in result.body:
            # The controller's ruling on the Task 3 review: a player with no games yet is the
            # expected path in week one, not an abnormal one, so it is INFO and is counted per
            # pass (`ctx["props"]["gamelog_no_season"]`); WARNING stays for the three paths
            # above, which are the host misbehaving.
            log.info("espn gamelog: athlete %s has no season data yet", athlete_id)
            return None
        return result
