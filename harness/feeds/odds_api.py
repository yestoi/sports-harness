from dataclasses import dataclass
from datetime import datetime, timezone

from harness.feeds.http import FetchResult, HttpClient

FEATURED_MARKETS = "h2h,spreads,totals"
ALTERNATE_MARKETS = "alternate_spreads,alternate_totals"


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
