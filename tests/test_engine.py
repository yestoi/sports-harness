import os

import pytest
from sqlalchemy import text

from harness.db.engine import BATCH_STATEMENT_TIMEOUT_MS, EXEC_STATEMENT_TIMEOUT_MS, make_engine


def test_engine_statement_timeout_per_engine():
    """The executor's loop must fail fast (10 s) while the settlement/benchmark batches need
    minutes; the default recorder engine keeps 30 s. Each engine carries its own timeout."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    cases = [(EXEC_STATEMENT_TIMEOUT_MS, "10s"), (None, "30s"), (BATCH_STATEMENT_TIMEOUT_MS, "15min")]
    for timeout_ms, expected in cases:
        engine = make_engine(url) if timeout_ms is None else make_engine(url, timeout_ms)
        try:
            with engine.connect() as conn:
                assert conn.execute(text("show statement_timeout")).scalar() == expected
        finally:
            engine.dispose()


def test_engine_timeout_constants():
    assert EXEC_STATEMENT_TIMEOUT_MS == 10_000
    assert BATCH_STATEMENT_TIMEOUT_MS == 900_000


# --- fix 71 narrowing (journal 224 item 9a): the connection names its own service -----------


def test_harness_service_names_the_connection_when_the_variable_is_set(monkeypatch):
    """Every app container sets `HARNESS_SERVICE=<compose service>`, so the server's
    `application_name` says which of the harness's processes a backend belongs to. The release
    drain lists and terminates by that name and by nothing else, so a backend with no name --
    an operator's shell, a tool, a future sidecar -- is never caught by it."""
    import harness.db.engine as engine_module

    monkeypatch.setenv("HARNESS_SERVICE", "app-serve")
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return "engine"

    monkeypatch.setattr(engine_module, "create_engine", fake_create_engine)
    assert engine_module.make_engine("postgresql+psycopg://u:p@h:5432/db") == "engine"
    assert captured["connect_args"] == {"connect_timeout": 5,
                                        "options": "-c statement_timeout=30000",
                                        "application_name": "app-serve"}


def test_no_application_name_is_sent_when_the_variable_is_unset(monkeypatch):
    """The suite and a developer shell keep libpq's default name, so no existing test, and no
    connection outside the compose stack, changes behaviour."""
    import harness.db.engine as engine_module

    monkeypatch.delenv("HARNESS_SERVICE", raising=False)
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured.update(kwargs)
        return "engine"

    monkeypatch.setattr(engine_module, "create_engine", fake_create_engine)
    engine_module.make_engine("postgresql+psycopg://u:p@h:5432/db", 10_000)
    assert captured["connect_args"] == {"connect_timeout": 5,
                                        "options": "-c statement_timeout=10000"}


def test_the_service_name_reaches_the_server(monkeypatch):
    """The end of the wire: what `pg_stat_activity.application_name` actually shows, which is
    what the release's drain predicate matches on."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("HARNESS_SERVICE", "app-research")
    engine = make_engine(url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("select application_name from pg_stat_activity "
                                     "where pid = pg_backend_pid()")).scalar() == "app-research"
    finally:
        engine.dispose()
