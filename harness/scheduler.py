from apscheduler.schedulers.background import BackgroundScheduler

from harness.config.settings import Settings
from harness.db.engine import (
    BATCH_STATEMENT_TIMEOUT_MS,
    EXEC_STATEMENT_TIMEOUT_MS,
    make_engine,
    make_session_factory,
)
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.execution.loop import Executor
from harness.recorder.tick import Recorder
from harness.settlement.job import Settler
from harness.venues.kalshi.public import KalshiPublic


def build_recorder(settings: Settings) -> Recorder:
    http = HttpClient(settings.http_timeout_s)
    odds = OddsApiClient(http, settings.odds_api_base_url, settings.odds_api_key(), settings.odds_api_bookmakers)
    espn = EspnClient(http, settings.espn_base_url)
    kalshi = KalshiPublic(http, settings.kalshi_base_url, settings.kalshi_sleep_s)
    factory = make_session_factory(make_engine(settings.database_url))
    return Recorder(settings, factory, odds, espn, kalshi)


def build_settler(settings: Settings) -> Settler:
    """The settlement job. Its own engine, carrying `BATCH_STATEMENT_TIMEOUT_MS`: settling a
    Saturday's finals is minutes of work, and the 30 s default would cancel it half-way.

    The Kalshi client is public-data only, exactly as the recorder's is: `run_venue_result`
    makes GET calls and nothing else, and no credential is read anywhere in this package.
    """
    factory = make_session_factory(make_engine(settings.database_url, BATCH_STATEMENT_TIMEOUT_MS))
    http = HttpClient(settings.http_timeout_s)
    kalshi = KalshiPublic(http, settings.kalshi_base_url, settings.kalshi_sleep_s)
    return Settler(settings, factory, kalshi)


def build_scheduler(recorder: Recorder, heartbeat_s: int, settler: Settler | None = None,
                    settle_period_s: int = 3600) -> BackgroundScheduler:
    """The recorder's tick, and the settler on its own slot when one is given.

    `BackgroundScheduler`'s default executor is a thread pool, so the settlement batch runs
    beside the tick on its own session and its own connection rather than inside it: a 600 s
    batch on the tick's thread would delay a 30 s heartbeat, and two jobs writing the same
    `runs` row would lose one of the updates (arch review §3.4). `misfire_grace_time=300`
    because an hourly job that starts a few minutes late is still worth running.
    """
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(recorder.maybe_tick, "interval", seconds=heartbeat_s, id="maybe_tick",
                  max_instances=1, coalesce=True, misfire_grace_time=60)
    if settler is not None:
        sched.add_job(settler.run, "interval", seconds=settle_period_s, id="settle",
                      max_instances=1, coalesce=True, misfire_grace_time=300)
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
