"""The snapshot layer: the only code in the dashboard that may reach Postgres.

Spec §0.3 makes "no page view runs a query" structural rather than a habit. A builder runs on a
session from `make_snapshot_engine`, which is a second engine with its own statement timeout and
a pool of two; `run_builder` writes the result to `dashboard_snapshots`; a request handler reads
one row of that table by primary key and nothing else. The request path keeps the existing
engine and never calls a builder.

Three failure rules, all inherited by every builder:

* **A failing section marks only itself.** `section` is the `_section` semantics the legacy page
  already uses: one slow or broken part of a surface does not empty the rest.
* **`error` is a class name, never `str(exc)`.** An exception message can carry SQL text and row
  content, and this payload is served over a tunnel to a phone. 80 characters, the column width.
* **A failed build leaves the previous payload alone.** A surface showing the last good numbers
  with a visible age is honest; a surface that empties itself on one timeout is not.
"""

import contextvars
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine, create_engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from harness import telemetry
from harness.config.settings import Settings
from harness.db.models import DashboardSnapshot

log = logging.getLogger(__name__)

#: Strictly below the WebSocket sink's timeout and at or below the checks registry's, asserted
#: by a test. A builder that cannot answer in two seconds has asked the wrong question: the
#: whole design is pre-aggregation, so a slow builder is a bug in the builder, not a budget to
#: raise.
SNAPSHOT_STATEMENT_TIMEOUT_MS = 2000

#: `pool_size=2` with `max_overflow=0`, deliberately: SQLAlchemy's default overflow of 10 would
#: let "a pool of two" become twelve connections under a scheduler, on a Postgres already shared
#: with app-run, app-exec, app-ws and the backup sidecar (ruling A-I10). Two is one for the job
#: that is running and one for the job that just started.
SNAPSHOT_POOL_SIZE = 2

#: A builder takes a session on the snapshot engine, the instant to build for and the settings,
#: and returns the payload. It never commits, never writes anything but through `telemetry`, and
#: never reaches a table on the forbidden list.
Builder = Callable[[Session, datetime, Settings], dict]

#: Anchored, and the only thing that reaches a table lookup or an ETag header. `\d{4}-\d{1,2}`
#: is the ISO year and week; nothing but digits can get through (ruling B-(e), item 8).
SNAPSHOT_NAME_RE = re.compile(r"^(?:pulse|floor|gate|ticket|study:\d{4}-\d{1,2})$")

#: name -> builder. `study:<year>-<week>` names resolve to the `study` builder.
BUILDERS: dict[str, Builder] = {}

#: The snapshot name the current build is for. Every builder but Study ignores it; Study is
#: built once per `(year, week)` and reads the week out of its own name, and threading it as a
#: context variable is what keeps the `Builder` signature the same for all five. Set by
#: `run_builder` for the duration of one build and reset afterwards.
current_name: contextvars.ContextVar[str] = contextvars.ContextVar("snapshot_name", default="")


def register_builder(name: str, builder: Builder) -> None:
    """Register (or replace) one builder. Called at import of each builder module."""
    BUILDERS[name] = builder


def valid_snapshot_name(name: str) -> bool:
    return bool(SNAPSHOT_NAME_RE.fullmatch(name or ""))


def builder_key(name: str) -> str:
    """The registry key a snapshot name resolves to: every `study:<year>-<week>` is built by the
    one `study` builder, which reads the week out of the name."""
    return "study" if name.startswith("study:") else name


def builder_for(name: str) -> Builder | None:
    return BUILDERS.get(builder_key(name))


def make_snapshot_engine(settings: Settings) -> Engine:
    """The second engine, and the only one a builder ever sees."""
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
        pool_size=SNAPSHOT_POOL_SIZE,
        max_overflow=0,
        connect_args={"connect_timeout": 5,
                      "options": f"-c statement_timeout={SNAPSHOT_STATEMENT_TIMEOUT_MS}"},
    )


def base_payload(name: str, now: datetime, settings: Settings, cadence_s: int) -> dict:
    """The keys every payload carries, whatever the surface (spec §4): the build the snapshot
    was made by, the instant it describes, the cadence the UI measures staleness against, and
    the two plain-language layers the builder fills in."""
    del name
    return {"build_sha": settings.build_sha,
            "now": now.isoformat(),
            "cadence_s": cadence_s,
            "sentences": {},
            "readings": {}}


def section(payload: dict, key: str, fn: Callable[[], object]) -> None:
    """One section of a payload, guarded. A failure marks that key `{"error": <class name>}` and
    leaves every other section intact -- the `_section` semantics the legacy page has used since
    fix round 1, with the same catch list (a malformed `runs.notes` raises Python shapes, not
    only SQLAlchemy ones) and the same rule that the class name is all that is recorded."""
    try:
        payload[key] = fn()
    except Exception as exc:  # noqa: BLE001 - one section must not cost the surface
        log.warning("snapshot section %s failed: %s", key, type(exc).__name__)
        payload[key] = {"error": type(exc).__name__}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def run_builder(session_factory: sessionmaker, name: str, now: datetime, settings: Settings,
                cadence_s: int) -> dict:
    """Build one snapshot and upsert its row. Returns the row as a dict.

    `session_factory` is bound to the snapshot engine in production (and to the test engine in
    tests, which is why it is a parameter rather than a module global). The timing is around the
    builder alone, so `serve.snapshot_ms` measures the work and not the write.

    An exception *outside* a section is the whole build failing: the row records the class name
    and keeps the previous payload, so a surface shows its last good numbers with a visible age
    rather than emptying itself. `KeyError` for an unregistered name is raised, not recorded:
    that is a programming error, not a runtime condition.
    """
    builder = builder_for(name)
    if builder is None:
        raise KeyError(f"no snapshot builder registered for {name!r}")

    generated_at = now
    error: str | None = None
    payload: dict | None = None
    started = time.monotonic()
    with session_factory() as session:
        token = current_name.set(name)
        try:
            payload = builder(session, now, settings)
        except Exception as exc:  # noqa: BLE001 - the row is the report of the failure
            log.warning("snapshot %s failed: %s", name, type(exc).__name__)
            session.rollback()
            error = type(exc).__name__[:80]
        finally:
            current_name.reset(token)
        elapsed_ms = int((time.monotonic() - started) * 1000)

        if payload is not None:
            # The cadence the *job* is running at, not the constant the builder defaulted to.
            # Floor's and Ticket's real cadences are decided per run, and `/api/snap`, the front
            # end's staleness flags and verify.md's "under twice its own cadence_s" row all read
            # this number: a healthy 60 s Floor snapshot whose payload still said 15 would read
            # as four times stale.
            payload["cadence_s"] = cadence_s

        values = {"name": name, "generated_at": generated_at, "elapsed_ms": elapsed_ms,
                  "error": error}
        update = {"generated_at": generated_at, "elapsed_ms": elapsed_ms, "error": error}
        if payload is not None:
            values["payload"] = payload
            update["payload"] = payload
        else:
            values["payload"] = {}
        statement = pg_insert(DashboardSnapshot).values(**values)
        session.execute(statement.on_conflict_do_update(
            index_elements=[DashboardSnapshot.name], set_=update))
        try:
            telemetry.record(session, "serve", "serve.snapshot_ms", elapsed_ms, {"name": name},
                             ts=generated_at)
        except Exception:  # noqa: BLE001 - ruling 1: telemetry never fails its caller
            log.exception("recording serve.snapshot_ms for %s failed", name)
        session.commit()
        row = session.get(DashboardSnapshot, name)
        return {"name": row.name, "generated_at": _iso(row.generated_at),
                "elapsed_ms": row.elapsed_ms, "cadence_s": cadence_s,
                "payload": row.payload, "error": row.error}
