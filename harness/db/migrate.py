"""Programmatic Alembic: the four operations the deploy needs, with no `alembic` binary assumed.

`docker compose run --rm app-run migrate ensure` is a `harness` entrypoint, so nothing here may
depend on a console script being on PATH or on the process's working directory. The `Config` is
built in code and `script_location` is resolved to the packaged `migrations/` directory.

The models and `create_schema` are the schema authority; Alembic records additive history. The
guarded branch in `ensure` is the load-bearing part: the NAS database predates Alembic and is
full, so its first deploy must record the baseline rather than execute it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)

#: The revision this checkout carries. `ensure` stamps or upgrades to it **by name**, so a typo
#: in the script directory fails loudly instead of silently stamping nothing -- and so a
#: revision that is not named here is never applied, which is why adding a file is not enough.
#:
#: Phase 4.5 bumped it from "0001_baseline"; fix 32 bumped it to "0003_brin_autosummarize";
#: phase 5 bumped it to "0004_phase5"; fix 35 bumped it to "0005_rfq_lookup"; fix 42 bumps it
#: to "0006_quotes_run_index". Two consequences
#: the runbook states and a test pins: only the full `make deploy-nas` runs `migrate ensure`, so
#: a mid-phase app-only deploy leaves the stamp at the prior revision while `create_schema` still
#: creates the new tables and the view; and a database stamped ahead of a checkout that lacks the
#: matching revision file aborts at `ensure`, because its `current` branch calls `upgrade_head`
#: unconditionally.
HEAD_REVISION = "0006_quotes_run_index"

#: Where the migrations live inside the image. The Dockerfile's `COPY migrations ./migrations`
#: puts them here; the checkout path below is what the test suite and a developer use.
_IMAGE_MIGRATIONS = Path("/app/migrations")


def migrations_dir() -> Path:
    """The packaged `migrations/` directory, from a checkout or from inside the image."""
    checkout = Path(__file__).resolve().parents[2] / "migrations"
    if checkout.is_dir():
        return checkout
    return _IMAGE_MIGRATIONS


def alembic_config(url: str) -> Config:
    """A `Config` built in code: no alembic.ini is read and no binary is assumed.

    `prepend_sys_path` is set to the directory holding `migrations/` so a migration can import
    `migrations.env` for `concurrent_index`, whichever way the process was started.
    """
    directory = migrations_dir()
    config = Config()
    config.set_main_option("script_location", str(directory))
    config.set_main_option("path_separator", "os")
    config.set_main_option("prepend_sys_path", str(directory.parent))
    # A literal '%' in a password would otherwise be read as ConfigParser interpolation.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def upgrade_head(url: str) -> None:
    """Bring a database up to the pinned head revision."""
    command.upgrade(alembic_config(url), HEAD_REVISION)


def stamp_head(url: str) -> None:
    """Record the pinned head as applied without executing it."""
    command.stamp(alembic_config(url), HEAD_REVISION)


def current_revision(url: str) -> str | None:
    """The revision the database records, or None when it has never been stamped."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _state(url: str) -> tuple[bool, bool]:
    """(alembic_version exists, runs exists) -- one short connection, read from pg_tables."""
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            present = {row[0] for row in conn.execute(text(
                "select tablename from pg_tables "
                "where schemaname = current_schema() "
                "and tablename in ('alembic_version', 'runs')"))}
    finally:
        engine.dispose()
    return "alembic_version" in present, "runs" in present


def ensure(url: str) -> str:
    """The guarded one-time stamp. Returns which of three branches it took.

    'stamped'  - no alembic_version, `runs` exists: a populated pre-Alembic database, which is
                 what the NAS is on this phase's first deploy. Record head; never execute the
                 baseline against it, because every object it creates is already there.
    'upgraded' - no alembic_version, no `runs`: an empty database. Build it from the baseline.
    'current'  - alembic_version exists. Upgrade from the stored revision; a no-op at head.
    """
    has_version, has_runs = _state(url)
    if not has_version and has_runs:
        stamp_head(url)
        log.info("alembic: stamped %s on a populated pre-Alembic database", HEAD_REVISION)
        return "stamped"
    if not has_version:
        upgrade_head(url)
        log.info("alembic: upgraded an empty database to %s", HEAD_REVISION)
        return "upgraded"
    upgrade_head(url)
    log.info("alembic: at %s", current_revision(url))
    return "current"
