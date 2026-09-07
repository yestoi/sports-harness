import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from harness.feeds.http import HttpClient

log = logging.getLogger(__name__)


def server_time_offset_ms(http: HttpClient, base_url: str) -> int | None:
    """Return the Kalshi server clock's offset from the local clock, in milliseconds.

    Positive means the server is ahead of local time. Returns None if the status endpoint
    doesn't respond 2xx or its `date` header is missing/unparseable.
    """
    result = http.get(f"{base_url}/exchange/status", redact_params=())
    if not (200 <= result.status < 300):
        return None
    date_header = result.headers.get("date")
    if not date_header:
        return None
    try:
        server_dt = parsedate_to_datetime(date_header)
    except (TypeError, ValueError):
        return None
    if server_dt.tzinfo is None:
        server_dt = server_dt.replace(tzinfo=timezone.utc)
    local_dt = datetime.now(timezone.utc)
    offset_ms = int((server_dt - local_dt).total_seconds() * 1000)
    if abs(offset_ms) > 5000:
        log.info("kalshi server clock offset %d ms", offset_ms)
    return offset_ms
