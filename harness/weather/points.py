"""Resolving a stadium's NWS gridpoint, and re-resolving it when the office re-grids.

Ruling A-I6. The first draft cached `/points` forever, so a stale `forecastHourly` URL would
404 on every tick for the rest of the season and the stadium would simply go quiet. The rule
here: a stored row is reused, a caller that saw a 404 or a 301 on the hourly URL asks for a
re-resolution, and a re-resolution happens **at most once per stadium per day**. A second
failure after that is recorded and the stadium is dropped for the day, which lowers the Pulse
weather-coverage count rather than disappearing.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from harness.db.models import WeatherPoint
from harness.feeds.nws import parse_point
from harness.recorder import store

log = logging.getLogger(__name__)

#: Ruling A-I6: at most one `/points` re-resolution per stadium per day.
POINT_RERESOLVE_AFTER = timedelta(days=1)


def resolve_point(session: Session, client, run_id: int, stadium, now: datetime,
                  force: bool = False) -> WeatherPoint | None:
    """The stadium's stored gridpoint, resolving it when there is none or when `force` is set
    and the stored one is at least a day old. Returns None when the call failed.

    `force` is what a caller passes after a 404 or a 301 on the hourly URL. Inside the day it is
    a no-op by design: an office that re-grids does it once, and a re-resolution storm against a
    URL that is failing for another reason is worse than a quiet day of missing forecasts.
    """
    existing = session.get(WeatherPoint, (stadium.sport, stadium.team_id))
    if existing is not None and not (force and now - existing.fetched_at >= POINT_RERESOLVE_AFTER):
        return existing

    path = f"/points/{stadium.lat},{stadium.lon}"
    result = client.get(path)
    store.store_raw(session, run_id, "nws", "/points", {"stadium": stadium.abbreviation}, result)
    grid = parse_point(result.body) if result.status == 200 else None
    if grid is None:
        log.warning("nws /points for %s answered %s and did not parse", stadium.abbreviation,
                    result.status)
        return None

    if existing is None:
        existing = WeatherPoint(sport=stadium.sport, team_id=stadium.team_id,
                                office=grid.office, grid_x=grid.grid_x, grid_y=grid.grid_y,
                                forecast_hourly_url=grid.forecast_hourly_url, fetched_at=now)
        session.add(existing)
    else:
        existing.office, existing.grid_x, existing.grid_y = grid.office, grid.grid_x, grid.grid_y
        existing.forecast_hourly_url = grid.forecast_hourly_url
        existing.fetched_at = now
    session.flush()
    return existing
