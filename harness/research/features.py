"""The veto's feature vector, frozen as of the signal (ruling A-I3).

Every window here closes at `signal.created_at`, not at call time. A vector built at call time
would show the model line movement the executor never had, and H9 would then measure hindsight
rather than judgement -- the decision would look good precisely when the line had already moved.
Both timestamps are recorded on the decision row so a reader can see the lag.

**Two blocks, and the split is a security boundary** (ruling A-M4, F60). The `numeric` block is
what the prompt tells the model to trust: numbers, an enumerated ESPN status, and explicit
timestamps in UTC and America/Chicago. The `untrusted` block is everything whose text came from
outside -- today that is exactly one field, `short_forecast` -- and it is sanitized and placed
after a fixed instruction that it is data.

**No injury feature** (D21). No injury feed exists, so there is nothing to put here, and a
feature that is always null teaches a model that there is never news. The veto's web search is
this phase's only injury channel, and H6 stays unmeasured (0.12).
"""
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.research.text import sanitize_model_text

log = logging.getLogger(__name__)

#: Addendum 1.4: fair history across books over the six hours before the signal.
FEATURE_WINDOW = timedelta(hours=6)
#: In five-minute buckets, so the vector is at most 72 points however busy the market was.
BUCKET_MINUTES = 5
#: Two probability points of sharp-fair movement re-call a bucket early (addendum 0.2, ruling
#: B-I2). The addendum names it as "the section 6.4 velocity threshold", whose numeric home is
#: `velocity_max_pts: 0.02` in each file under `harness/variants/` -- which this phase may read
#: but must never edit (roadmap invariant: nothing under `harness/variants/` is touched). It is
#: therefore **this phase's own constant with the addendum as its source**, stated once, here.
#: If the variants' velocity threshold is ever re-tuned, this is the second place to change.
FAIR_MOVE_INVALIDATOR = Decimal("0.02")
#: Ruling A-M4: a fixed enum, never the venue's own string. `games.status` is kept raw and
#: lowercased by the scoreboard linker, so anything outside this set becomes `unknown` rather
#: than putting free text where the prompt says the numbers are.
ESPN_STATUSES = ("scheduled", "in_progress", "halftime", "final", "final_ot", "postponed",
                 "canceled", "unknown")
_CT = ZoneInfo("America/Chicago")
#: `weather_snapshots.short_forecast` is already 80 characters at write; capped again here
#: because the block it lands in is a prompt.
_FORECAST_MAX = 80

_FAIR_HISTORY = text("""
    select date_bin(:bucket, created_at, :origin) as ts,
           avg(fair_p) as fair_p, count(*) as n
    from fair_values
    where game_id = :game_id and market_type = :market_type
      and created_at > :since and created_at <= :as_of
    group by 1 order by 1
""")

_VENUE_HISTORY = text("""
    select date_bin(:bucket, fetched_at, :origin) as ts,
           avg((yes_bid + yes_ask) / 2.0) as mid, count(*) as n
    from venue_quotes
    where venue_market_id = :venue_market_id
      and fetched_at > :since and fetched_at <= :as_of
      and yes_bid is not null and yes_ask is not null
    group by 1 order by 1
""")

_BOOK_HISTORY = text("""
    select book, date_bin(:bucket, fetched_at, :origin) as ts,
           avg(1.0 / price_decimal) as implied, count(*) as n
    from odds_snapshots
    where game_id = :game_id and market_type = :market_type
      and fetched_at > :since and fetched_at <= :as_of and price_decimal > 0
    group by 1, 2 order by 1, 2
""")

_NEWEST_FAIR = text("""
    select fair_p, disagreement, staleness_s, fair_source, created_at
    from fair_values
    where game_id = :game_id and market_type = :market_type and created_at <= :as_of
    order by created_at desc limit 1
""")

#: The status as of the signal. `game_score_events` is the live feed's append-only history and
#: `games.status` is only its newest value, so the event row is what an as-of read has to use;
#: the dimension row is the fallback for a game the scoreboard linker has never seen.
#:
#: That fallback is the **one** read in this module that A-I3 does not bound, and it is stated
#: here rather than left to be found. `_GAME` below reads `games` at its current values, so a
#: game with no score event at or before the signal takes a `status` that may have moved after
#: it. The window is narrow -- `link_espn_scoreboard` appends a `scheduled` row the first tick
#: it sees a game, so only a game first linked *after* the signal reaches the fallback at all --
#: and it is the same limitation `harness/execution/store.py` records for the replay horizon:
#: the dimension tables keep no history to bound, while this one's history is exactly the query
#: above. `sport` and `kickoff_utc` are read the same way for the same reason.
_NEWEST_STATUS = text("""
    select status from game_score_events
    where game_id = :game_id and ts <= :as_of
    order by ts desc limit 1
""")

_NEWEST_WEATHER = text("""
    select fetched_at, period_start, temperature_f, wind_mph, wind_dir, precip_pct,
           short_forecast, roof
    from weather_snapshots
    where game_id = :game_id and fetched_at <= :as_of
    order by fetched_at desc limit 1
""")

_GAME = text("select sport, kickoff_utc, status from games where id = :game_id")


def _f(value) -> float | None:
    return None if value is None else float(value)


def _iso(value: datetime | None) -> str | None:
    """UTC, always, and always with an explicit offset.

    The driver hands back whatever zone the session is set to, and a feature vector whose
    timestamps drifted with a server setting would make two otherwise identical prompts differ.
    """
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _ct(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(_CT).isoformat()


def build_features(session: Session, signal, as_of: datetime) -> tuple[dict, dict]:
    """`(numeric, untrusted)` for one signal, as of `as_of` (always `signal.created_at`).

    `signal` is any object carrying `id`, `game_id`, `market_type`, `venue_market_id`, `side`,
    `fair_p`, `edge` and `created_at` -- the shape `harness/research/veto.py` loads.
    """
    params = {"game_id": signal.game_id, "market_type": signal.market_type,
              "venue_market_id": signal.venue_market_id, "as_of": as_of,
              "since": as_of - FEATURE_WINDOW,
              "bucket": timedelta(minutes=BUCKET_MINUTES),
              "origin": as_of - FEATURE_WINDOW}

    fair_history = [{"ts": _iso(r.ts), "fair_p": _f(r.fair_p), "n": int(r.n)}
                    for r in session.execute(_FAIR_HISTORY, params)]
    venue_history = [{"ts": _iso(r.ts), "mid": _f(r.mid), "n": int(r.n)}
                     for r in session.execute(_VENUE_HISTORY, params)]
    book_history: dict[str, list[dict]] = {}
    for row in session.execute(_BOOK_HISTORY, params):
        book_history.setdefault(row.book, []).append(
            {"ts": _iso(row.ts), "implied": _f(row.implied), "n": int(row.n)})

    newest_fair = session.execute(_NEWEST_FAIR, params).first()
    status_row = session.execute(_NEWEST_STATUS, params).first()
    game = session.execute(_GAME, {"game_id": signal.game_id}).first()
    weather = session.execute(_NEWEST_WEATHER, params).first()

    raw_status = (status_row.status if status_row else (game.status if game else None)) or ""
    espn_status = raw_status if raw_status in ESPN_STATUSES else "unknown"
    kickoff = game.kickoff_utc if game else None
    minutes_to_kickoff = (None if kickoff is None
                          else int((kickoff - as_of).total_seconds() // 60))

    numeric = {
        "signal_id": int(signal.id),
        "sport": (game.sport if game else None),
        "market_type": signal.market_type,
        "side": signal.side,
        # Every timestamp explicit, in both zones (addendum 1.4). A model reasoning about a
        # Saturday-night kickoff has to be able to see which Saturday night it is.
        "signal_created_at_utc": _iso(as_of),
        "signal_created_at_ct": _ct(as_of),
        "as_of_utc": _iso(as_of),
        "as_of_ct": _ct(as_of),
        "kickoff_utc": _iso(kickoff),
        "kickoff_ct": _ct(kickoff),
        "minutes_to_kickoff": minutes_to_kickoff,
        "espn_status": espn_status,
        "fair_p": _f(signal.fair_p),
        "edge": _f(signal.edge),
        "disagreement": _f(newest_fair.disagreement) if newest_fair else None,
        "fair_staleness_s": (int(newest_fair.staleness_s)
                             if newest_fair and newest_fair.staleness_s is not None else None),
        "fair_source": (newest_fair.fair_source if newest_fair else None),
        "fair_history": fair_history,
        "venue_history": venue_history,
        "book_history": book_history,
        "weather_fetched_at": _iso(weather.fetched_at) if weather else None,
        "weather_roof": (weather.roof if weather else None),
        "temperature_f": (int(weather.temperature_f)
                          if weather and weather.temperature_f is not None else None),
        "wind_mph": (int(weather.wind_mph) if weather and weather.wind_mph is not None else None),
        "precip_pct": (int(weather.precip_pct)
                       if weather and weather.precip_pct is not None else None),
    }
    untrusted = {
        "weather": None if weather is None else {
            "period_start_utc": _iso(weather.period_start),
            "wind_dir": weather.wind_dir,
            "short_forecast": sanitize_model_text(weather.short_forecast, _FORECAST_MAX),
        },
    }
    return numeric, untrusted


#: The scalar fields the delta and the invalidators compare. A history array moving by one bucket
#: is not news; these six are.
_COMPARED = ("fair_p", "espn_status", "weather_fetched_at", "minutes_to_kickoff",
             "disagreement", "fair_staleness_s")


def feature_delta(trigger: dict, other: dict) -> dict:
    """What moved between the bucket's trigger and one of its cached signals.

    Stored on every `from_cache = true` decision so a reader of t7 can see exactly how much the
    world had moved under a decision the signal inherited (ruling B-I2).
    """
    delta: dict = {}
    for field in _COMPARED:
        before, after = trigger.get(field), other.get(field)
        if before == after:
            continue
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            delta[field] = after - before
        else:
            delta[field] = {"from": before, "to": after}
    return delta


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def invalidated(trigger: dict, other: dict) -> str | None:
    """Which invalidator fired, or None (addendum 0.2, ruling B-I2).

    Three, and only three: the game's ESPN status changed, a new weather snapshot landed for the
    game, or the sharp fair moved by `FAIR_MOVE_INVALIDATOR`. D21 removed the fourth -- the
    injury-status change of section 7.1 -- with the injury feed it depended on.
    """
    if trigger.get("espn_status") != other.get("espn_status"):
        return "espn_status"
    if trigger.get("weather_fetched_at") != other.get("weather_fetched_at"):
        return "weather"
    before, after = _decimal(trigger.get("fair_p")), _decimal(other.get("fair_p"))
    if before is not None and after is not None and abs(after - before) >= FAIR_MOVE_INVALIDATOR:
        return "fair_move"
    return None
