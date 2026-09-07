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
