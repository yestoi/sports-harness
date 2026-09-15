import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

#: The executor's loop must fail fast: a query that outlives the loop period is a stall, not a
#: slow query, and the next loop is more useful than this one finishing.
EXEC_STATEMENT_TIMEOUT_MS = 10_000
#: The settlement, benchmark and markout batches legitimately run for minutes.
BATCH_STATEMENT_TIMEOUT_MS = 900_000


def make_engine(url: str, statement_timeout_ms: int = 30000) -> Engine:
    """The one engine factory, with the per-engine statement timeout and the service's name.

    Fix 71 narrowing (journal 224 item 9a): each compose app service sets
    `HARNESS_SERVICE=<service name>`, which libpq sends as `application_name`, so the server
    records which harness process a backend belongs to. `scripts/release-omarchy.py` then
    drains the sessions of exactly the services it just stopped, by name, instead of
    terminating every client backend that is not a known tool.

    Unset -- the test suite, a developer shell, any process outside the stack -- sends no
    `application_name` at all and keeps libpq's default, so nothing outside compose changes.
    """
    connect_args = {"connect_timeout": 5,
                    "options": f"-c statement_timeout={statement_timeout_ms}"}
    service = os.environ.get("HARNESS_SERVICE")
    if service:
        connect_args["application_name"] = service
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
