import logging
import signal
import time

import typer
import uvicorn

from harness.config.settings import get_settings
from harness.db.engine import make_engine, make_session_factory
from harness.db.schema import create_schema
from harness.logging_setup import configure_logging

app = typer.Typer(no_args_is_help=True)
log = logging.getLogger("harness")


@app.command("init-db")
def init_db() -> None:
    configure_logging()
    s = get_settings()
    create_schema(make_engine(s.database_url))
    log.info("schema created")


@app.command("tick-once")
def tick_once() -> None:
    configure_logging()
    from harness.scheduler import build_recorder

    run = build_recorder(get_settings()).maybe_tick()
    log.info("run %s status=%s n=%s credits=%s", run.id, run.status, run.n_requests, run.credits_used)


@app.command("run")
def run() -> None:
    configure_logging()
    from harness.scheduler import build_recorder, build_scheduler

    s = get_settings()
    sched = build_scheduler(build_recorder(s), s.heartbeat_s)
    sched.start()
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("scheduler started heartbeat=%ss", s.heartbeat_s)
    while not stop["flag"]:
        time.sleep(1)
    sched.shutdown(wait=True)
    log.info("scheduler stopped")


@app.command("serve")
def serve(port: int = 8080, host: str = "0.0.0.0") -> None:
    configure_logging()
    from harness.health import create_app

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    uvicorn.run(create_app(factory), host=host, port=port, log_config=None)


if __name__ == "__main__":
    app()
