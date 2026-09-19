"""§1.1(c)/(e) and §2: the experiment writer and the hashed file tree."""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path

from datetime import datetime, timezone

from sqlalchemy import MetaData, Table, insert as sa_insert, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
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

    def insert_guard(self, table: Table) -> None:
        """§1.1(c)'s two refusals, shared by `insert` and `upsert`."""
        if not table.name.startswith(EXP_TABLE_PREFIX):
            raise IsolationError(
                f"the experiment writer refuses the production table {table.name!r}: its "
                f"destination is the exp_* tables alone (§1.1c)")
        known = load_exp_metadata().tables.get(table.name)
        if known is None or table is not known:
            raise IsolationError(
                f"{table.name!r} is not the writer's own table object; pass "
                f"ExperimentWriter.table({table.name!r}) (§1.1c)")

    def insert(self, table: Table, rows: Sequence[dict]) -> int:
        self.insert_guard(table)
        if not rows:
            return 0
        if len(rows) > self._batch_rows:
            raise ValueError(
                f"{len(rows)} rows exceeds exp_batch_rows={self._batch_rows}; batch the insert "
                f"(§4.3's bound)")
        # Bounded by the assertion above: at most `exp_batch_rows` rows in one statement.
        self._session.execute(sa_insert(table), list(rows))
        return len(rows)

    def upsert(self, table: Table, rows: Sequence[dict], *, key: Sequence[str],
               update: Sequence[str]) -> int:
        """The same insert, resolved against an existing row on `key` (§4.7's role holds
        `INSERT` **and** `UPDATE` on `exp_*` alone, C2).

        Two rows of this milestone are written more than once by construction and cannot be
        plain inserts: `exp_checkpoint`, which is one row per `(run, arm)` rewritten at every
        resume point (§1.4), and `exp_allocation`, whose row for a print grows as the portfolio
        consumes it (§1.5). Every refusal `insert` makes is made here first, by delegation, so
        no path around the production-table guard exists.
        """
        if not rows:
            return 0
        self.insert_guard(table)
        if len(rows) > self._batch_rows:
            raise ValueError(
                f"{len(rows)} rows exceeds exp_batch_rows={self._batch_rows}; batch the write "
                f"(§4.3's bound)")
        statement = pg_insert(table).values(list(rows))
        statement = statement.on_conflict_do_update(
            index_elements=list(key),
            set_={name: getattr(statement.excluded, name) for name in update})
        self._session.execute(statement)
        return len(rows)

    def commit(self) -> None:
        self._session.commit()

    def close(self) -> None:
        self._session.rollback()
        self._session.close()

# --- the rows §2 names, written through the writer and nothing else ----------------------------


def write_run(writer: ExperimentWriter, *, manifest, manifest_hash: str, created_at: datetime,
              code_sha: str | None = None, status: str = "frozen") -> int:
    """One `exp_run` row: the frozen manifest and the hash every resume is checked against."""
    import json

    # `as_json()` is the one serialisation the hash was taken over (§1.2), so the stored
    # `manifest` column and `manifest_hash` can never describe different documents.
    return writer.insert(writer.table("exp_run"), [{
        "run_id": writer.run_id, "created_at": created_at, "manifest_hash": manifest_hash,
        "manifest": json.loads(manifest.as_json()), "code_sha": code_sha,
        "clock_mode": manifest.clock_mode, "status": status,
        "supersedes": manifest.supersedes}])


def write_arms(writer: ExperimentWriter, rows: Sequence[dict]) -> int:
    """One `exp_arm` row per arm, with the spec its decisions were made under (§1.2)."""
    return writer.insert(writer.table("exp_arm"), list(rows))


def write_orders(writer: ExperimentWriter, rows: Sequence[dict]) -> int:
    """`exp_order` rows, keyed `(run_id, arm_id, id)` -- the arm's own negative order ids, so
    the same run chunked two ways writes the same keys (§1.4)."""
    return writer.upsert(
        writer.table("exp_order"), list(rows), key=("run_id", "arm_id", "id"),
        update=("variant_id", "intent_id", "venue_market_id", "ticker", "side", "prob",
                "contracts", "filled_contracts", "placed_at", "expiry", "queue_ahead_at_place",
                "cancelled_at", "cancel_reason", "status", "episode_id"))


def write_fills(writer: ExperimentWriter, rows: Sequence[dict]) -> int:
    return writer.insert(writer.table("exp_fill"), list(rows))


def write_allocations(writer: ExperimentWriter, rows: Sequence[dict]) -> int:
    """`liquidity.PortfolioLedger.as_rows()` projected onto `exp_allocation`'s own columns
    (§2): the ledger's `ticker`/`taker_side` live in the checkpoint's `state`, which is what a
    resume restores from, while the table carries the portfolio identity and the trade id its
    primary key is."""
    return writer.upsert(
        writer.table("exp_allocation"),
        [{"run_id": row["run_id"], "arm_id": row["arm_id"], "variant_id": row["variant_id"],
          "source_trade_id": row["source_trade_id"], "available": row["available"],
          "allocated": row["allocated"]} for row in rows],
        key=("run_id", "arm_id", "variant_id", "source_trade_id"),
        update=("available", "allocated"))


def write_mismatches(writer: ExperimentWriter, rows: Sequence) -> int:
    """`baseline.Mismatch` rows (§1.3f). The dataclass already refuses an unknown kind and an
    explained row with no cause, which is §2's invariant query asserted before the write."""
    return writer.insert(writer.table("exp_mismatch"), [
        {"run_id": row.run_id, "arm_id": row.arm_id, "instant": row.instant,
         "venue_market_id": row.venue_market_id, "kind": row.kind, "expected": row.expected,
         "actual": row.actual, "cause": row.cause, "explained": row.explained}
        for row in rows])


def write_limitations(writer: ExperimentWriter, rows: Sequence) -> int:
    """`capture.Limitation` rows (§2). The dataclass refuses an unknown kind and a null scope,
    which is that table's invariant query asserted before the write."""
    return writer.insert(writer.table("exp_limitation"), [
        {"run_id": row.run_id, "kind": row.kind, "scope": row.scope, "detail": row.detail,
         "created_at": row.created_at} for row in rows])


def save_checkpoint(writer: ExperimentWriter, *, run_id: str, arm_id: str,
                    cursor_event_id: int | None, state: dict, manifest_hash: str) -> None:
    """§1.4: one resume point per `(run, arm)`, rewritten in place.

    `state` carries the liquidity ledger, the open-order set, the capacity counter, the
    exposure and the arm's next order id -- everything `adapter.ArmRunner` needs to continue a
    chunk boundary as if it had never stopped. `manifest_hash` is written beside it so
    `resume()` can refuse a changed manifest without reading the state at all.
    """
    writer.upsert(writer.table("exp_checkpoint"), [{
        "run_id": run_id, "arm_id": arm_id, "cursor_event_id": cursor_event_id,
        "state": state, "manifest_hash": manifest_hash,
        "updated_at": datetime.now(timezone.utc)}],
        key=("run_id", "arm_id"), update=("cursor_event_id", "state", "manifest_hash",
                                          "updated_at"))


#: Two bounded reads on `exp_checkpoint`'s own primary key `(run_id, arm_id)`: the hash first,
#: so a refusal never reads the state, and the state only once the hash has been accepted.
_CHECKPOINT_HASH = text("select manifest_hash from exp_checkpoint "
                        "where run_id = :run_id and arm_id = :arm_id")
_CHECKPOINT_STATE = text("select cursor_event_id, state from exp_checkpoint "
                         "where run_id = :run_id and arm_id = :arm_id")


def resume(session: Session, *, run_id: str, arm_id: str, manifest) -> dict | None:
    """The stored state for `(run_id, arm_id)`, or None where there is no checkpoint yet.

    §1.2: `check_resume` is called **before** the checkpoint row is touched, so a changed
    manifest raises `ManifestMismatch` with the row byte-identical -- true by construction
    rather than by a rollback. Nothing here writes.
    """
    from harness.experiments.execution_viability.manifest import check_resume

    params = {"run_id": run_id, "arm_id": arm_id}
    stored_hash = session.execute(_CHECKPOINT_HASH, params).scalar()
    if stored_hash is None:
        return None
    check_resume(stored_hash, manifest)
    row = session.execute(_CHECKPOINT_STATE, params).one()
    state = dict(row.state or {})
    state["cursor_event_id"] = row.cursor_event_id
    return state
