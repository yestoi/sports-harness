from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from harness.feeds.http import FetchResult, HttpClient

Sport = Literal["nfl", "ncaaf"]
_PATH = {"nfl": "/nfl/scoreboard", "ncaaf": "/college-football/scoreboard"}
_PARAMS = {"nfl": None, "ncaaf": {"groups": "80", "limit": "400"}}


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
