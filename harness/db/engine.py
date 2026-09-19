import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

#: The executor's loop must fail fast: a query that outlives the loop period is a stall, not a
#: slow query, and the next loop is more useful than this one finishing.
EXEC_STATEMENT_TIMEOUT_MS = 10_000
#: The settlement, benchmark and markout batches legitimately run for minutes.
BATCH_STATEMENT_TIMEOUT_MS = 900_000


def service_connect_args() -> dict:
    """The `application_name` connect arg for this process, or nothing at all.

    Fix 76 (roadmap row 76): the harness has two engine factories -- this module's `make_engine`
    and `harness/dashboard/snapshots/__init__.py::make_snapshot_engine`, the second engine
    app-serve builds for its snapshot scheduler -- and only the first sent a name, so
    app-serve's snapshot backends were the one kind of client backend the release drain could
    not attribute to a service and so left open across a release. The lookup lives in one place
    now: `harness/db/migrate.py`'s three short-lived engines (the index healer's, the revision
    read and the `_state` probe) take it from here too, and a fourth factory gets the name by
    calling this rather than by remembering the variable.

    Unset or empty -- the test suite, a developer shell, any process outside compose -- sends no
    `application_name` at all and keeps libpq's default.
    """
    service = os.environ.get("HARNESS_SERVICE")
    return {"application_name": service} if service else {}


def make_engine(url: str, statement_timeout_ms: int = 30000, *,
                session_gucs: dict[str, str] | None = None) -> Engine:
    """The one engine factory, with the per-engine statement timeout and the service's name.

    `session_gucs` is additive and empty by default, so every existing caller builds exactly the
    engine it built before. A caller that names settings here gets them in libpq's `options`
    startup string, which means **every** connection this pool ever opens carries them - a
    connection replaced after a failed `pool_pre_ping`, or one returned to the pool and reset,
    included. A session-level `SET` cannot promise that: it belongs to the connection it ran on
    (6D.1 carry-forward M4).

    Fix 71 narrowing (journal 224 item 9a): each compose app service sets
    `HARNESS_SERVICE=<service name>`, which libpq sends as `application_name`, so the server
    records which harness process a backend belongs to. `scripts/release-omarchy.py` then
    drains the sessions of exactly the services it just stopped, by name, instead of
    terminating every client backend that is not a known tool.

    Unset -- the test suite, a developer shell, any process outside the stack -- sends no
    `application_name` at all and keeps libpq's default, so nothing outside compose changes.
    """
    options = [f"-c statement_timeout={statement_timeout_ms}"]
    options += [f"-c {name}={value}" for name, value in sorted((session_gucs or {}).items())]
    connect_args = {"connect_timeout": 5,
                    "options": " ".join(options),
                    **service_connect_args()}
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
