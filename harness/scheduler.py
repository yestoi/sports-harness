from apscheduler.schedulers.background import BackgroundScheduler

from harness.config.settings import Settings
from harness.db.engine import make_engine, make_session_factory
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.recorder.tick import Recorder
from harness.venues.kalshi.public import KalshiPublic


def build_recorder(settings: Settings) -> Recorder:
    http = HttpClient(settings.http_timeout_s)
    odds = OddsApiClient(http, settings.odds_api_base_url, settings.odds_api_key(), settings.odds_api_bookmakers)
    espn = EspnClient(http, settings.espn_base_url)
    kalshi = KalshiPublic(http, settings.kalshi_base_url, settings.kalshi_sleep_s)
    factory = make_session_factory(make_engine(settings.database_url))
    return Recorder(settings, factory, odds, espn, kalshi)


def build_scheduler(recorder: Recorder, heartbeat_s: int) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(recorder.maybe_tick, "interval", seconds=heartbeat_s, id="maybe_tick",
                  max_instances=1, coalesce=True, misfire_grace_time=60)
    return sched
