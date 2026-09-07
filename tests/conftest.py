import os
from datetime import datetime, timezone

import pytest


@pytest.fixture
def env_settings(monkeypatch, tmp_path):
    key_file = tmp_path / "odds_api_key"
    key_file.write_text("test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("ODDS_API_KEY_FILE", str(key_file))
    from harness.config.settings import Settings

    return Settings()


@pytest.fixture(scope="session")
def _schema():
    """Build the test schema once per session. Dropping and recreating ~35 tables plus their
    indexes and views for every DB test cost more than the tests themselves; db_session
    truncates instead."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    from harness.db import schema as schema_module
    from harness.db.engine import make_engine
    from harness.db.schema import create_schema, drop_schema

    engine = make_engine(url)
    drop_schema(engine)
    create_schema(engine)
    # Task 2b turns ensure_partitions into the three-table version (raw_responses,
    # orderbook_events, venue_trades); it exists today for raw_responses only. Called through
    # getattr so Task 2b can widen it without touching this fixture.
    ensure_partitions = getattr(schema_module, "ensure_partitions", None)
    if ensure_partitions is not None:
        from sqlalchemy.orm import sessionmaker

        with sessionmaker(bind=engine)() as session:
            ensure_partitions(session, datetime.now(timezone.utc))
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(_schema):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    from harness.db.models import Base

    with sessionmaker(bind=_schema)() as session:
        yield session
        session.rollback()
    tables = ", ".join(sorted(Base.metadata.tables))
    with _schema.begin() as conn:
        conn.execute(text(f"truncate {tables} restart identity cascade"))
