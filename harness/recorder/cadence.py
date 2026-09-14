from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from harness.feeds.espn import Kickoff
from harness.venues.kalshi.public import MarketSummary

SPORTS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}
#: The sort key of an event no prop call has ever been made for: older than any real stamp, so
#: a never-fetched event always leads the rotation.
_NEVER = datetime.min.replace(tzinfo=timezone.utc)
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


def alternates_due(now: datetime, events: list[tuple[str, datetime]], last_alt: dict[str, datetime],
                    near_s: int = 120, far_s: int = 120, window_h: int = 36) -> list[str]:
    # Task 3b/U1: the near/far split lives in the interface again so a future settings change
    # can revive it without touching this function; `near_s` applies inside 180 minutes of
    # kickoff, `far_s` from there out to `window_h`, and nothing beyond the window is ever due.
    # These defaults mirror Settings.odds_alt_interval_near_s/far_s/window_h (120/120/36) --
    # no caller relies on a different default, so the interface no longer implies a pre-U1
    # shape no production path uses (Task 3b fix round 1, Minor 7).
    out: list[str] = []
    for event_id, commence in events:
        until = commence - now
        if until < timedelta(0) or until > timedelta(hours=window_h):
            continue
        interval = near_s if until <= timedelta(minutes=180) else far_s
        if is_due(last_alt.get(event_id), now, interval):
            out.append(event_id)
    return out


@dataclass(frozen=True)
class PropEvent:
    """One candidate prop event as the recorder reads it out of `games` (3.2).

    `event_id` is `games.odds_api_event_id` -- the key the per-event prop endpoint takes, not the
    internal game id. `signal` is whether the game carried a candidate signal with a direct fair
    inside `pool_window_hours`; the anchor test is made here, from the two abbreviations, so the
    anchor list stays one config value read in one place.
    """
    event_id: str
    sport: str
    kickoff_utc: datetime
    home_abbr: str | None
    away_abbr: str | None
    signal: bool


@dataclass(frozen=True)
class WatchedPropEvent:
    """A watched event, with the reason it is watched resolved."""
    event_id: str
    sport: str
    kickoff_utc: datetime
    anchor: bool


def prop_events_watched(now: datetime, events: list[PropEvent], *, window_h: int, per_sport: int,
                        anchors: frozenset[str]) -> list[WatchedPropEvent]:
    """The watched prop events of this tick, anchor first then kickoff, at most `per_sport` each
    (3.2).

    Pure: the caller reads `games` once per sport and this decides nothing by clock or database.
    An event already kicked off is not watched -- props are a pre-game surface -- and neither is
    one beyond `window_h`.
    """
    watched: list[WatchedPropEvent] = []
    for sport in SPORTS:
        mine = []
        for event in events:
            if event.sport != sport or not event.event_id:
                continue
            until = event.kickoff_utc - now
            if until < timedelta(0) or until > timedelta(hours=window_h):
                continue
            anchor = (event.home_abbr in anchors) or (event.away_abbr in anchors)
            if not anchor and not event.signal:
                continue
            mine.append(WatchedPropEvent(event.event_id, sport, event.kickoff_utc, anchor))
        # Anchor first, then kickoff: the cap bites on a full college Saturday, and LSU's game
        # is the one event the product is built around (R:216).
        mine.sort(key=lambda w: (not w.anchor, w.kickoff_utc, w.event_id))
        watched.extend(mine[:per_sport])
    return watched


def prop_events_due(now: datetime, events: list[PropEvent], last_fetched: dict[str, datetime], *,
                    window_h: int, per_sport: int, calls: int,
                    anchors: frozenset[str]) -> list[str]:
    """The prop events to fetch on this tick (3.2).

    Watched: events kicking off inside `window_h` that carry an anchor team or a priced signal,
    ordered anchor first then by kickoff, at most `per_sport` a sport. Due: of those, the
    `calls` oldest-fetched first, across both sports -- a rotation, so 32 events refresh inside
    two 900 s ticks, which is 30 minutes and therefore inside `leg_max_age_minutes`.

    An event never fetched sorts oldest of all; ties keep the watched order, so a first tick
    takes the first sport's cap and the next tick takes the other's.
    """
    watched = prop_events_watched(now, events, window_h=window_h, per_sport=per_sport,
                                  anchors=anchors)
    order = {w.event_id: index for index, w in enumerate(watched)}
    due = sorted(watched, key=lambda w: (last_fetched.get(w.event_id) or _NEVER,
                                         order[w.event_id]))
    return [w.event_id for w in due[:calls]]


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
