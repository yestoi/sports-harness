"""§1.1(c)/(e) and §2: the experiment writer and the hashed file tree."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import MetaData, Table, insert as sa_insert
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.db.engine import make_session_factory
from harness.experiments.execution_viability import EXP_DB_ROLE, IsolationError
from harness.experiments.execution_viability.source import _require_secret, assert_least_privilege

log = logging.getLogger("harness.exp")

EXP_TABLE_PREFIX = "exp_"

#: The writer's own metadata (§1.1c). Empty until T3 declares the eleven `exp_*` models; filled by
#: `load_exp_metadata()`, never at import time - the declarative models are imported inside the
#: functions below, never at module scope (§1.1c, asserted by tests/test_exp_isolation.py).
EXP_METADATA = MetaData()


def load_exp_metadata() -> MetaData:
    """Copy the `exp_*` tables of the declarative metadata into `EXP_METADATA`, once."""
    if EXP_METADATA.tables:
        return EXP_METADATA
    from harness.db.models import Base           # function scope, deliberately

    for name, table in Base.metadata.tables.items():
        if name.startswith(EXP_TABLE_PREFIX):
            table.to_metadata(EXP_METADATA)
    return EXP_METADATA


def production_tables() -> frozenset[str]:
    """§1.1(c)'s refusal list: every declared table that is not an `exp_*` one."""
    from harness.db.models import Base           # function scope, deliberately

    return frozenset(n for n in Base.metadata.tables if not n.startswith(EXP_TABLE_PREFIX))


def check_destination(s: Settings, *, url: str) -> str:
    """§1.1(e): refuse a destination whose dbname is not the configured one."""
    want = make_url(s.database_url).database
    got = make_url(url).database
    if got != want:
        raise IsolationError(
            f"destination database {got!r} is not the configured {want!r}; the experiment writes "
            f"only its own deployment's exp_* tables (§1.1e)")
    return got


def run_dir(s: Settings, run_id: str) -> Path:
    """`/srv/sports-harness/exp/<run_id>/` (§2), created on demand."""
    path = Path(s.exp_dir) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_body(s: Settings, run_id: str, name: str, body: bytes) -> tuple[str, str]:
    """Write one raw body and return `(path, sha256)` for `exp_observation` (§1.6e, I5)."""
    digest = hashlib.sha256(body).hexdigest()
    path = run_dir(s, run_id) / f"{name}-{digest[:12]}.json"
    path.write_bytes(body)
    return str(path), digest


class ExperimentWriter:
    """The only writer of `exp_*` rows. Its destination cannot name a production table."""

    def __init__(self, session: Session, *, run_id: str, batch_rows: int):
        self._session = session
        self.run_id = run_id
        self._batch_rows = batch_rows

    @classmethod
    def open(cls, s: Settings, *, run_id: str, engine: Engine | None = None) -> "ExperimentWriter":
        # Ruling I1: the refusals are ordered **secret -> privilege -> destination**, which is the
        # order §1.1(b) states them and puts C2's boundary first. The reverse order raised on the
        # dbname before the role was ever probed, so an injected test engine (whose database is
        # `harness_test_<branch>`) produced a destination error where the contract promises an
        # `orders` one.
        _require_secret(s)
        if engine is None:
            from harness.db.engine import make_engine
            engine = make_engine(s.exp_database_url(EXP_DB_ROLE))
        session = make_session_factory(engine)()
        role = assert_least_privilege(session)     # raises before any write (C2)
        check_destination(s, url=str(engine.url))  # §1.1(e), after the privilege probe
        log.info("exp writer open role=%s run=%s", role, run_id)
        return cls(session, run_id=run_id, batch_rows=s.exp_batch_rows)

    def table(self, name: str) -> Table:
        """The writer's own `Table` object for `name`; the only one `insert` accepts."""
        tables = load_exp_metadata().tables
        if name not in tables:
            raise IsolationError(f"{name!r} is not one of the experiment's tables (§1.1c)")
        return tables[name]

    def insert(self, table: Table, rows: Sequence[dict]) -> int:
        if not table.name.startswith(EXP_TABLE_PREFIX):
            raise IsolationError(
                f"the experiment writer refuses the production table {table.name!r}: its "
                f"destination is the exp_* tables alone (§1.1c)")
        known = load_exp_metadata().tables.get(table.name)
        if known is None or table is not known:
            raise IsolationError(
                f"{table.name!r} is not the writer's own table object; pass "
                f"ExperimentWriter.table({table.name!r}) (§1.1c)")
        if not rows:
            return 0
        if len(rows) > self._batch_rows:
            raise ValueError(
                f"{len(rows)} rows exceeds exp_batch_rows={self._batch_rows}; batch the insert "
                f"(§4.3's bound)")
        # Bounded by the assertion above: at most `exp_batch_rows` rows in one statement.
        self._session.execute(sa_insert(table), list(rows))
        return len(rows)

    def commit(self) -> None:
        self._session.commit()

    def close(self) -> None:
        self._session.rollback()
        self._session.close()
