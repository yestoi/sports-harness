from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from harness.feeds.http import FetchResult, HttpClient

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
    def __init__(self, http: HttpClient, base_url: str):
        self._http = http
        self._base = base_url.rstrip("/")

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

    def fetch_gamelog(self, sport: Sport, athlete_id: str) -> FetchResult:
        """One athlete's game log, the draft context line's source (addendum §4.1). Its shape is
        measured by the plan's evidence task before the line is trusted; until then an
        unrecognised body is the expected path and reads `no season data yet`.

        Recorded 2026-09-14: this path -- the addendum's own
        `{espn_base_url}/{sport}/athletes/{id}/gamelog` -- answered 404 for every athlete tried,
        while the bodies the evidence task read came from a different ESPN host and API version.
        Moving the host is the user's call (invariant 8, gate 7), so nothing here moves and this
        fetcher keeps yielding the `no season data yet` path until that decision is taken.
        """
        return self._http.get(f"{self._base}{_PATH[sport].rsplit('/', 1)[0]}"
                              f"/athletes/{athlete_id}/gamelog", params=None, redact_params=())
