from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from harness.feeds.espn import Kickoff
from harness.venues.kalshi.public import MarketSummary

SPORTS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}
BAND_LO, BAND_HI = Decimal("0.20"), Decimal("0.80")


def _local(now: datetime, tz: str) -> datetime:
    return now.astimezone(ZoneInfo(tz))


def interval_for(sport: str, now: datetime, kickoffs: list[Kickoff], tz: str) -> int | None:
    loc = _local(now, tz)
    mine = [x for x in kickoffs if x.sport == sport]
    # A game of this sport is on the field: kickoff through kickoff + 4h.
    in_progress = any(timedelta(0) <= (now - x.kickoff_utc) <= timedelta(hours=4) for x in mine)
    if 1 <= loc.hour < 8 and not in_progress:
        return None
    if sport == "nfl":
        for x in mine:
            delta = x.kickoff_utc - now
            if timedelta(minutes=60) <= delta <= timedelta(minutes=100):
                return 20
    today = [x.kickoff_utc for x in mine if _local(x.kickoff_utc, tz).date() == loc.date()]
    if in_progress or (today and (min(today) - timedelta(hours=3)) <= now <= max(today)):
        return 120
    if loc.weekday() >= 5:
        return 300
    return 900


def is_due(last: datetime | None, now: datetime, interval: int | None) -> bool:
    if interval is None:
        return False
    if last is None:
        return True
    return (now - last).total_seconds() >= interval


def alternates_due(now: datetime, events: list[tuple[str, datetime]], last_alt: dict[str, datetime]) -> list[str]:
    out: list[str] = []
    for event_id, commence in events:
        until = commence - now
        if until < timedelta(0) or until > timedelta(hours=36):
            continue
        interval = 120 if until <= timedelta(hours=3) else 900
        if is_due(last_alt.get(event_id), now, interval):
            out.append(event_id)
    return out


def _in_band(p: Decimal | None) -> bool:
    return p is not None and BAND_LO <= p <= BAND_HI


def select_ladders(now: datetime, markets: list[MarketSummary], kickoffs: list[Kickoff], tz: str, cap: int) -> list[str]:
    active = any(-timedelta(hours=4) <= (x.kickoff_utc - now) <= timedelta(hours=3) for x in kickoffs)
    if not active:
        return []
    today = _local(now, tz).date()
    picked = [m for m in markets
              if m.event_date == today and m.yes_bid is not None and m.yes_ask is not None
              and (_in_band(m.yes_bid) or _in_band(m.yes_ask))]
    picked.sort(key=lambda m: m.volume_fp, reverse=True)
    return [m.ticker for m in picked[:cap]]


def select_trade_tickers(now: datetime, markets: list[MarketSummary],
                         watermarks: dict[str, tuple[datetime, Decimal]]) -> list[tuple[str, datetime]]:
    out: list[tuple[str, datetime]] = []
    for m in markets:
        wm = watermarks.get(m.ticker)
        if wm is None:
            if m.volume_fp > 0:
                out.append((m.ticker, now - timedelta(hours=24)))
        elif m.volume_fp != wm[1]:
            out.append((m.ticker, wm[0] - timedelta(seconds=5)))
    return out
