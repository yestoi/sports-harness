"""Hourly NWS forecasts for outdoor games, inside the recorder tick (addendum §1.2).

**The schema is pinned, not guessed** (addendum 0.10). `HOURLY_FIELDS` is the list of period keys
a recorded live `forecastHourly` response actually carried, recorded by the controller before any
of this was written. A body missing one is refused with a note, never parsed around: a column
quietly full of nulls is the failure this rule exists to prevent.

**Two guards, and why they matter more than the data** (rulings A-I7, B-I10).
`harness/recorder/cadence.py` returns a 20 s interval for NFL when a kickoff is 60-100 minutes
out. Adding this source there pushes the tick past its own cadence and `max_instances=1,
coalesce=True` drops the next one, so a forecast for a game kicking off in 90 minutes -- which
has no time-sensitivity whatever -- would cost orderbook rows that do. The source runs only on
the 300 s and 900 s cadences, and only with at least 25 s of tick budget left.

**A change log, not a sample log.** Addendum §4 budgets this table at 500 rows a day. Six periods
per game, hourly, across a 72-hour football weekend is thousands. A forecast for a fixed hour
barely moves between reads, so a row is appended for a `(game_id, period_start)` only when a
value differs from the newest stored row for that pair -- the rule `game_score_events` already
uses. "The newest weather snapshot before the signal", which is what the veto asks for, is
answered identically by a change log.

**Fetch order is oldest-snapshot-first** (ruling A-M12), so a budget that binds does not starve
the same game every hour.
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import WeatherSnapshot
from harness.recorder import store
from harness.research.text import sanitize_model_text
from harness.weather.points import resolve_point
from harness.weather.stadiums import is_outdoor, stadium_for

log = logging.getLogger(__name__)

#: Pinned from `tests/fixtures/nws_forecast_hourly_lsu.json` (addendum 0.10). If the installed
#: recording carries different names, these are the recording's, not these. `temperatureUnit` is
#: pinned too, not just present: `temperature_f` assumes Fahrenheit, and a body in Celsius would
#: otherwise fill the column with plausible wrong numbers -- the same failure class a missing
#: field is, which is exactly what the pin exists to catch.
HOURLY_FIELDS = ("startTime", "temperature", "temperatureUnit", "windSpeed", "windDirection",
                 "probabilityOfPrecipitation", "shortForecast")

#: Rulings A-I7 and B-I10: the two cadences the source may run on, and the tick budget it needs.
ALLOWED_CADENCES = (300, 900)
MIN_TICK_REMAINING_S = 25

#: R:211: outdoor games inside 72 hours, hourly.
FORECAST_WINDOW = timedelta(hours=72)
REFETCH_AFTER = timedelta(hours=1)
#: Addendum §1.2: the periods kept, kickoff - 1 h to kickoff + 4 h.
PERIOD_BEFORE = timedelta(hours=1)
PERIOD_AFTER = timedelta(hours=4)
#: `weather_snapshots.short_forecast` is String(80) and is venue free text.
SHORT_FORECAST_MAX = 80

#: "10 mph", "12 to 18 mph". The last number is the one a reader cares about: a gust decides a
#: kicking game, an average does not.
_WIND = re.compile(r"(\d+)(?!.*\d)")


@dataclass(frozen=True)
class GameVenue:
    game_id: int
    sport: str
    home_team_id: int
    away_team_id: int
    kickoff_utc: datetime
    newest_fetched_at: datetime | None


_DUE = text("""
    select g.id, g.sport, g.home_team_id, g.away_team_id, g.kickoff_utc,
           (select max(w.fetched_at) from weather_snapshots w where w.game_id = g.id) as newest
    from games g
    where g.kickoff_utc >= :now and g.kickoff_utc <= :horizon
    order by newest nulls first, g.kickoff_utc
""")

_NEWEST_PERIODS = text("""
    select distinct on (period_start) period_start, temperature_f, wind_mph, wind_dir,
           precip_pct, short_forecast
    from weather_snapshots
    where game_id = :game_id
    order by period_start, fetched_at desc
""")


def games_due(session: Session, now: datetime) -> list[GameVenue]:
    """Every game inside 72 hours whose newest snapshot is at least an hour old, oldest first."""
    rows = session.execute(_DUE, {"now": now, "horizon": now + FORECAST_WINDOW}).all()
    return [GameVenue(r.id, r.sport, r.home_team_id, r.away_team_id, r.kickoff_utc, r.newest)
            for r in rows
            if r.newest is None or now - r.newest >= REFETCH_AFTER]


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_hourly(body) -> list[dict] | None:
    """A `forecastHourly` response as period rows, or None when the pinned schema no longer
    holds. Never a partial parse: a body that lost a field is a recorded refusal."""
    if not isinstance(body, dict):
        return None
    periods = (body.get("properties") or {}).get("periods")
    if not isinstance(periods, list) or not periods:
        return None
    rows: list[dict] = []
    for period in periods:
        if not isinstance(period, dict) or any(f not in period for f in HOURLY_FIELDS):
            return None
        if period["temperatureUnit"] != "F":
            # The pin, not a conversion: `temperature_f` is Fahrenheit by name, and a body that
            # ever answered in Celsius is a recorded refusal, never a silently wrong number.
            return None
        try:
            start = datetime.fromisoformat(str(period["startTime"]).replace("Z", "+00:00"))
        except ValueError:
            return None
        wind_match = _WIND.search(str(period["windSpeed"] or ""))
        precip = period["probabilityOfPrecipitation"]
        raw_short = period["shortForecast"]
        rows.append({
            "period_start": start,
            "temperature_f": _int(period["temperature"]),
            "wind_mph": _int(wind_match.group(1)) if wind_match else None,
            "wind_dir": str(period["windDirection"] or "")[:8] or None,
            # A null probability and a zero probability are different facts, and a feature block
            # that collapsed them would tell a model it knows something it does not.
            "precip_pct": _int(precip.get("value")) if isinstance(precip, dict) else _int(precip),
            # Same distinction for the phrase itself: a null forecast is a fact NWS did not
            # supply one, not the empty string `sanitize_model_text(None, ...)` would otherwise
            # produce, and the column is nullable precisely so this can be stored, not guessed.
            "short_forecast": (sanitize_model_text(raw_short, SHORT_FORECAST_MAX)
                               if raw_short is not None else None),
        })
    return rows


def periods_in_window(rows: Sequence[dict], kickoff: datetime) -> list[dict]:
    """Only the periods from kickoff - 1 h to kickoff + 4 h."""
    lo, hi = kickoff - PERIOD_BEFORE, kickoff + PERIOD_AFTER
    return [row for row in rows if lo <= row["period_start"] <= hi]


def _changed(stored: dict | None, row: dict) -> bool:
    if stored is None:
        return True
    return any(stored.get(field) != row.get(field) for field in
               ("temperature_f", "wind_mph", "wind_dir", "precip_pct", "short_forecast"))


def run_weather_source(session: Session, run_id: int, client, settings, now: datetime,
                       budget, ctx: dict) -> dict:
    """One pass of the source. Returns the counts that go into `runs.notes["weather"]`."""
    counts = {"due": 0, "fetched": 0, "written": 0, "reresolved": 0, "errors": [],
              "skipped": {}, "budget_exhausted": False}
    due = games_due(session, now)
    counts["due"] = len(due)
    for game in due:
        if budget.remaining_s() <= 0:
            counts["budget_exhausted"] = True
            break
        venue = stadium_for(game.sport, game.home_team_id, game.away_team_id,
                            game.kickoff_utc.date())
        if venue is None:
            counts["skipped"][str(game.game_id)] = "no stadium"
            continue
        if not is_outdoor(venue):
            counts["skipped"][str(game.game_id)] = "dome"
            continue
        try:
            _one_game(session, run_id, client, game, venue, now, budget, counts)
        except Exception as exc:  # noqa: BLE001 - one stadium must not cost the pass
            log.warning("nws pass failed for game %s: %s", game.game_id, type(exc).__name__)
            counts["errors"].append({str(game.game_id): type(exc).__name__})
            continue
    return counts


def _one_game(session: Session, run_id: int, client, game: GameVenue, venue, now: datetime,
              budget, counts: dict) -> None:
    """One game's pass. Mutates `counts` directly -- `fetched`/`written`/`reresolved` tallies,
    and, for every terminal outcome that is not a write, a `counts["skipped"][game_id]` reason
    (design §1.2 / addendum 0.10: a schema-pin refusal, a failed gridpoint, or a non-200 all
    belong in `runs.notes["weather"]`, the same as "dome" and "no stadium" already are)."""
    point = resolve_point(session, client, run_id, venue, now)
    if point is None:
        # `resolve_point` returns None only after it has itself made and failed a `/points` GET
        # (a cached point is handed back without ever reaching the network), so this is real
        # network work done this pass, not nothing: it counts as a fetch attempt so an
        # otherwise-quiet tick is not marked "skipped" (Minor 1).
        counts["fetched"] += 1
        counts["skipped"][str(game.game_id)] = "points"
        return
    result = client.get(point.forecast_hourly_url)
    store.store_raw(session, run_id, "nws", "/forecast/hourly",
                    {"game_id": game.game_id, "team": venue.abbreviation}, result)
    counts["fetched"] += 1
    if result.status in (301, 404) and budget.remaining_s() > 0:
        # Ruling A-I6: the gridpoint moved. Re-resolve once, at most once a day, and try again --
        # but only if the budget can still afford a second round of network calls (Minor 3): a
        # tick already down to its last seconds takes the 404 as final rather than spend more of
        # a cadence the loop cannot get back.
        point = resolve_point(session, client, run_id, venue, now, force=True)
        counts["reresolved"] += 1
        if point is None:
            counts["skipped"][str(game.game_id)] = "points"
            return
        result = client.get(point.forecast_hourly_url)
        store.store_raw(session, run_id, "nws", "/forecast/hourly",
                        {"game_id": game.game_id, "team": venue.abbreviation, "retry": True},
                        result)
        counts["fetched"] += 1
    if result.status != 200:
        counts["skipped"][str(game.game_id)] = f"http {result.status}"
        return
    parsed = parse_hourly(result.body)
    if parsed is None:
        log.warning("nws hourly body for game %s did not match the pinned schema", game.game_id)
        counts["skipped"][str(game.game_id)] = "schema pin"
        return

    stored = {r.period_start: dict(r._mapping)
              for r in session.execute(_NEWEST_PERIODS, {"game_id": game.game_id})}
    written = 0
    for row in periods_in_window(parsed, game.kickoff_utc):
        if not _changed(stored.get(row["period_start"]), row):
            continue
        session.add(WeatherSnapshot(run_id=run_id, game_id=game.game_id,
                                    fetched_at=result.fetched_at, roof=venue.roof, **row))
        written += 1
    if written:
        # Every other write in this module flushes as it goes (`store.store_raw`,
        # `resolve_point`): a caller reading the game's newest snapshot back inside the same
        # transaction -- the recorder tick's own checkpoint, or a second pass in the same run --
        # must see rows this pass just wrote, not wait on a later commit elsewhere.
        session.flush()
    counts["written"] += written
