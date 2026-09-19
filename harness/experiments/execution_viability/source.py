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

#: §4.3's other two settings, carried in the **connection's own startup options** (M4) rather
#: than only as a session `SET`. `make_engine` uses `pool_pre_ping`, which silently replaces a
#: dead connection with a fresh one: a `SET` that ran on the old connection is not on the new
#: one, and the read-only GUC in particular would then be absent on a session the code believes
#: is read-only. In the options string the setting is part of every connection this pool opens
#: and survives the `reset all` a pooled connection gets at check-in. `reader()` still issues
#: the same `SET`s afterwards, because an injected engine (the tests') has its own options and
#: the defence is worth having twice; the role's SELECT-only grant is the third layer and the
#: only one the server itself enforces against a writing statement.
SOURCE_SESSION_GUCS = {"lock_timeout": "1s", "default_transaction_read_only": "on"}

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
    return make_engine(s.exp_database_url(role), SOURCE_STATEMENT_TIMEOUT_MS,
                       session_gucs=SOURCE_SESSION_GUCS)


@contextmanager
def reader(s: Settings, *, engine: Engine | None = None) -> Iterator[Session]:
    """A read-only session for the production tape.

    `engine` is the test seam: the secret and the privilege probe run either way, so an injected
    engine cannot bypass the refusal. Nothing here logs the URL - it carries the password.

    The three §4.3 settings are applied twice on the engine this module builds: once in the
    connection's startup options (`SOURCE_SESSION_GUCS`, which is what makes them true of a
    connection the pool replaces mid-life) and once as the `SET`s below, which are the only
    layer an injected engine has.
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
