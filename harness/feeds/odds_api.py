from dataclasses import dataclass
from datetime import datetime, timezone

from harness.feeds.http import FetchResult, HttpClient

FEATURED_MARKETS = "h2h,spreads,totals"
ALTERNATE_MARKETS = "alternate_spreads,alternate_totals"
#: Addendum §3.1 (D3): the five release-one families plus the `_alternate` variants of the four
#: yardage/count families. Nine keys, so at most nine credits a call (cost is unique markets
#: returned x regions, one region). The bookmakers string is the client's, unchanged (gate 5).
PROP_MARKETS = ("player_pass_yds,player_rush_yds,player_reception_yds,player_receptions,"
                "player_anytime_td,player_pass_yds_alternate,player_rush_yds_alternate,"
                "player_reception_yds_alternate,player_receptions_alternate")


@dataclass(frozen=True)
class Credits:
    last: int
    used: int
    remaining: int


def parse_credit_headers(headers: dict[str, str]) -> Credits:
    def _i(k: str) -> int:
        v = headers.get(k, "0")
        try:
            return int(float(v))
        except ValueError:
            return 0
    return Credits(_i("x-requests-last"), _i("x-requests-used"), _i("x-requests-remaining"))


def parse_event_ids_and_times(body: dict | list | None) -> list[tuple[str, datetime]]:
    if not isinstance(body, list):
        return []
    out: list[tuple[str, datetime]] = []
    for ev in body:
        try:
            ts = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
            out.append((ev["id"], ts))
        except (KeyError, ValueError, AttributeError):
            continue
    return out


class OddsApiClient:
    def __init__(self, http: HttpClient, base_url: str, api_key: str, bookmakers: str):
        self._http = http
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._bookmakers = bookmakers

    def _params(self, markets: str) -> dict:
        return {"apiKey": self._key, "bookmakers": self._bookmakers, "markets": markets,
                "oddsFormat": "decimal", "dateFormat": "iso"}

    def fetch_featured(self, sport: str) -> FetchResult:
        return self._http.get(f"{self._base}/sports/{sport}/odds", params=self._params(FEATURED_MARKETS))

    def fetch_event_alternates(self, sport: str, event_id: str) -> FetchResult:
        return self._http.get(f"{self._base}/sports/{sport}/events/{event_id}/odds",
                              params=self._params(ALTERNATE_MARKETS))

    def fetch_event_props(self, sport: str, event_id: str) -> FetchResult:
        """One event's player props, on the per-event endpoint the recorder already calls for
        alternates. `includeLinks`/`includeSids` are asked for here and nowhere else: the
        featured and alternates calls are byte-identical to what they were (gate 5)."""
        params = dict(self._params(PROP_MARKETS), includeLinks="true", includeSids="true")
        return self._http.get(f"{self._base}/sports/{sport}/events/{event_id}/odds",
                              params=params)
