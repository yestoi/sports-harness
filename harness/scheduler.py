from apscheduler.schedulers.background import BackgroundScheduler

from harness.config.settings import Settings
from harness.db.engine import EXEC_STATEMENT_TIMEOUT_MS, make_engine, make_session_factory
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.execution.loop import Executor
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


def build_executor(settings: Settings) -> Executor:
    """The paper executor. No venue client and no credential: it reads the tape and writes rows.

    Its engine carries `EXEC_STATEMENT_TIMEOUT_MS` rather than the default: a query that outlives
    the loop period is a stall, and the next loop is more useful than this one finishing.
    """
    factory = make_session_factory(make_engine(settings.database_url, EXEC_STATEMENT_TIMEOUT_MS))
    return Executor(settings, factory)


def build_exec_scheduler(executor: Executor, period_s: int) -> BackgroundScheduler:
    """`max_instances=1` and `coalesce=True` so a slow step delays the next one rather than
    running two executors against the same orders; the advisory lock is the backstop."""
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(executor.step, "interval", seconds=period_s, id="exec_step",
                  max_instances=1, coalesce=True, misfire_grace_time=30)
    return sched
