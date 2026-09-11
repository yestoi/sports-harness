"""The evidence capsule: bounded reads, a hashed manifest, and the caps that stop a scan.

Every test seeds a small world in the test database and asserts on bounds -- that no row
outside the window reaches a file, that the manifest's counts and digests match the bytes, and
that a capped file says so and makes the command exit 2. No test runs against anything but
`DATABASE_URL_TEST`.
"""

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from harness.capsule import CAPSULE_ROW_CAP, order_slices, period_slices, unverifiable
from harness.cli import app
from harness.db.models import OrderbookEvent

runner = CliRunner()

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
LOWER, UPPER = NOW - timedelta(hours=1), NOW + timedelta(hours=1)
TICKER, OTHER = "KXTEST-A", "KXTEST-B"


def _partitions(session):
    """The weekly partitions this file's fixed clock needs, whatever today's date is.

    `orderbook_events` and `venue_trades` are range-partitioned by week, and `tests/conftest.py`
    builds only the partitions around `datetime.now()`. A file pinned to 2026-09-11 would insert
    happily this week and raise "no partition of relation" from 2026-09-21 on, with 6B and 6C
    still running `make test`. `ensure_partitions(session, now, tables=PARTITIONED_TABLES)`
    (`harness/db/schema.py:52`) creates this week's and next week's for each table and skips
    names that already exist, so calling it for the fixture's own `NOW` is idempotent and the
    file never expires.

    One call covers everything this file writes: the oldest row is `LOWER - 3 days`
    (2026-09-08) and the newest is `UPPER + 2 hours` (2026-09-11), both inside `NOW`'s own ISO
    week 37, which runs Monday 2026-09-07 to Sunday 2026-09-13.
    """
    from harness.db.schema import ensure_partitions

    ensure_partitions(session, NOW)


def _event(session, ticker, ts, kind, *, sid=7, seq=1, raw=None):
    _partitions(session)
    session.add(OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind=kind,
                               raw=raw if raw is not None else {}))
    session.flush()


def _trade(session, ticker, ts, trade_id):
    """One `venue_trades` print. Partitioned by week on `ts`, like `orderbook_events`."""
    from harness.db.models import VenueTrade

    _partitions(session)
    session.add(VenueTrade(venue="kalshi", trade_id=trade_id, ticker=ticker, ts=ts,
                           yes_price=Decimal("0.30"), count=Decimal(5), taker_side="no",
                           taker_outcome_side="no", is_block=False, source="ws"))
    session.flush()


def test_the_tape_anchor_read_is_bounded_below(db_session):
    """The snapshot a tape anchors on may be at most two days older than the window.

    `_WS_SNAPSHOT` was bounded above only, so it opened every weekly partition of
    `orderbook_events` looking for the newest snapshot at or before `:upper` (review I3). Two
    days is one partition back at worst, so the read touches two partitions and no more; a
    ticker whose last snapshot is older than that has no anchor and the capsule says so rather
    than scanning the season to find one.
    """
    from harness.fixtures import export_ws_tape

    _event(db_session, TICKER, LOWER - timedelta(days=3), "snapshot", raw={"old": True})
    stale = export_ws_tape(db_session, TICKER, LOWER, UPPER)
    assert stale["snapshot"] is None

    _event(db_session, TICKER, LOWER - timedelta(hours=6), "snapshot", raw={"old": False})
    fresh = export_ws_tape(db_session, TICKER, LOWER, UPPER)
    assert fresh["snapshot"] is not None and fresh["snapshot"]["raw"] == {"old": False}
