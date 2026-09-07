#!/usr/bin/env python3
"""Ensure a test database exists on the local test Postgres (localhost:5433).

Usage: scripts/testdb.py NAME
Prints the SQLAlchemy URL for NAME. Idempotent; safe to call from every test run.
"""
import sys

import psycopg

ADMIN = "postgresql://harness:harness@localhost:5433/postgres"


def main(name: str) -> None:
    if not name.replace("_", "").isalnum() or len(name) > 63:
        sys.exit(f"testdb: bad database name {name!r}")
    with psycopg.connect(ADMIN, autocommit=True) as conn:
        exists = conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(f'create database "{name}"')
    print(f"postgresql+psycopg://harness:harness@localhost:5433/{name}")


if __name__ == "__main__":
    main(sys.argv[1])
