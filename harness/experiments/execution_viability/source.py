"""§1.1(b): the read-only production source reader, refused at session open under any writing role."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.db.engine import make_engine, make_session_factory
from harness.experiments.execution_viability import EXP_DB_ROLE, IsolationError

log = logging.getLogger("harness.exp")

#: §4.3's cooperative bound, applied to the engine and re-asserted on the session.
SOURCE_STATEMENT_TIMEOUT_MS = 25_000

#: A catalogue function call: no table is read, so no index or row bound applies.
_PRIVILEGE_PROBE = text(
    "select current_user as role_name, "
    "has_table_privilege(current_user, 'orders', 'INSERT') as can_write")


def assert_least_privilege(session: Session) -> str:
    """The server's own answer to 'may this connection write production?' (C2)."""
    row = session.execute(_PRIVILEGE_PROBE).one()
    if row.can_write:
        raise IsolationError(
            f"role {row.role_name!r} can INSERT into orders; the experiment refuses to open a "
            f"session under a role that can write a production table (§1.1b). Create the "
            f"{EXP_DB_ROLE!r} role as docs/runbooks/experiments.md §4.7 shows.")
    return row.role_name


def _require_secret(s: Settings) -> None:
    if not s.has_exp_db_password():
        raise IsolationError(
            f"the experiment secret {s.exp_db_password_file} is absent or empty; the run is "
            f"refused (fail closed, §1.1b). The user places it: docs/runbooks/experiments.md.")


def source_engine(s: Settings, *, role: str = EXP_DB_ROLE) -> Engine:
    _require_secret(s)
    return make_engine(s.exp_database_url(role), SOURCE_STATEMENT_TIMEOUT_MS)


@contextmanager
def reader(s: Settings, *, engine: Engine | None = None) -> Iterator[Session]:
    """A read-only session for the production tape.

    `engine` is the test seam: the secret and the privilege probe run either way, so an injected
    engine cannot bypass the refusal. Nothing here logs the URL - it carries the password.
    """
    _require_secret(s)
    eng = engine if engine is not None else source_engine(s)
    with make_session_factory(eng)() as session:
        for stmt in (f"set statement_timeout = '{SOURCE_STATEMENT_TIMEOUT_MS // 1000}s'",
                     "set lock_timeout = '1s'",
                     "set default_transaction_read_only = on"):
            session.execute(text(stmt))
        session.commit()
        role = assert_least_privilege(session)
        log.info("exp source session open role=%s timeout_ms=%d", role,
                 SOURCE_STATEMENT_TIMEOUT_MS)
        try:
            yield session
        finally:
            session.rollback()
