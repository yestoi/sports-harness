"""Unit tests for `truncate_all` (carried fix 56): the teardown truncate must run under its own
statement timeout and retry a single `QueryCanceled` cancel, without needing a database."""
import psycopg
import pytest
from sqlalchemy.exc import OperationalError

from harness.db.models import Base
from tests.conftest import truncate_all


class _StubResult:
    pass


class _StubConnection:
    """Shares `statements` and the truncate-call counter with the engine across every
    `engine.begin()` call, so a per-attempt fresh connection doesn't reset the failure state."""

    def __init__(self, engine):
        self._engine = engine

    def execute(self, clause, *args, **kwargs):
        stmt = str(clause)
        self._engine.statements.append(stmt)
        if stmt.startswith("truncate"):
            self._engine.truncate_calls += 1
            if self._engine.fail_first_truncate and self._engine.truncate_calls == 1:
                raise OperationalError("truncate ...", {}, psycopg.errors.QueryCanceled())
        return _StubResult()


class _StubBeginContext:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return _StubConnection(self._engine)

    def __exit__(self, exc_type, exc, tb):
        return False


class _StubEngine:
    def __init__(self, fail_first_truncate=True):
        self.statements = []
        self.fail_first_truncate = fail_first_truncate
        self.truncate_calls = 0

    def begin(self):
        return _StubBeginContext(self)


class _NonCancelStubConnection:
    def __init__(self, engine):
        self._engine = engine

    def execute(self, clause, *args, **kwargs):
        stmt = str(clause)
        self._engine.statements.append(stmt)
        if stmt.startswith("truncate"):
            raise OperationalError("truncate ...", {}, psycopg.errors.LockNotAvailable())
        return _StubResult()


class _NonCancelStubBeginContext:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return _NonCancelStubConnection(self._engine)

    def __exit__(self, exc_type, exc, tb):
        return False


class _NonCancelStubEngine:
    def __init__(self):
        self.statements = []

    def begin(self):
        return _NonCancelStubBeginContext(self)


def test_truncate_all_retries_a_single_query_canceled_cancel():
    stub = _StubEngine(fail_first_truncate=True)

    attempts = truncate_all(stub, wait_s=0)

    assert attempts == 2
    assert len(stub.statements) == 4
    assert stub.statements[0] == "set local statement_timeout = 120000"
    assert stub.statements[1].startswith("truncate")
    assert stub.statements[2] == stub.statements[0]
    assert stub.statements[3] == stub.statements[1]


def test_truncate_all_propagates_the_cancel_when_out_of_attempts():
    stub = _StubEngine(fail_first_truncate=True)

    with pytest.raises(OperationalError) as exc_info:
        truncate_all(stub, attempts=1, wait_s=0)

    assert isinstance(exc_info.value.orig, psycopg.errors.QueryCanceled)


def test_truncate_all_propagates_a_non_cancel_error_without_retrying():
    stub = _NonCancelStubEngine()

    with pytest.raises(OperationalError) as exc_info:
        truncate_all(stub, wait_s=0)

    assert isinstance(exc_info.value.orig, psycopg.errors.LockNotAvailable)
    assert len(stub.statements) == 2
    assert stub.statements[1].startswith("truncate")


def test_truncate_statement_names_every_table_sorted_and_restarts_identity():
    stub = _StubEngine(fail_first_truncate=False)

    truncate_all(stub, wait_s=0)

    truncate_stmt = stub.statements[1]
    expected_tables = ", ".join(sorted(Base.metadata.tables))
    assert truncate_stmt == f"truncate {expected_tables} restart identity cascade"
