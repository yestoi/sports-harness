"""The kernel clock-synchronization probe (carried fix 57).

No database and no privileges: `clock_synchronized()` is a read of the kernel's own
`adjtimex` state, and every failure mode of the probe has to degrade to None so that a
caller proceeds exactly as it did before the probe existed.
"""

import ctypes

import pytest

from harness.ops import clock as clock_mod
from harness.ops.clock import STA_UNSYNC, TIME_ERROR, clock_synchronized


def test_probe_returns_a_bool_or_none_on_this_host():
    assert clock_synchronized() in (True, False, None)


def test_timex_struct_is_the_64_bit_linux_layout():
    assert ctypes.sizeof(clock_mod._Timex) == 208


def test_sta_unsync_in_the_status_word_reads_false(monkeypatch):
    def unsynced(buf):
        buf.status |= STA_UNSYNC
        return 0

    monkeypatch.setattr(clock_mod, "_adjtimex", unsynced)
    assert clock_synchronized() is False


def test_time_error_return_value_reads_false(monkeypatch):
    monkeypatch.setattr(clock_mod, "_adjtimex", lambda buf: TIME_ERROR)
    assert clock_synchronized() is False


def test_a_synchronized_kernel_reads_true(monkeypatch):
    monkeypatch.setattr(clock_mod, "_adjtimex", lambda buf: 0)
    assert clock_synchronized() is True


def test_a_raising_probe_reads_none(monkeypatch):
    def boom(buf):
        raise OSError("no adjtimex here")

    monkeypatch.setattr(clock_mod, "_adjtimex", boom)
    assert clock_synchronized() is None


def test_a_failed_syscall_reads_none(monkeypatch):
    monkeypatch.setattr(clock_mod, "_adjtimex", lambda buf: -1)
    assert clock_synchronized() is None


def test_a_non_linux_host_reads_none(monkeypatch):
    monkeypatch.setattr(clock_mod.sys, "platform", "darwin")
    assert clock_synchronized() is None


# ---- the unsynchronized-run annotation, and the readers that must honour it ---------------
#
# The user's ruling of 2026-09-14 (journal 184, item 1): `runs.notes->>'clock' = 'unsynced'` is
# an *exclusion*, not documentation. One test per reader family, each asserting the same thing
# -- a marked run contributes nothing to the window's numbers.

from datetime import datetime, timedelta, timezone  # noqa: E402

from sqlalchemy import text  # noqa: E402

from harness.db.models import FairValue, MarketGapSnapshot, Run  # noqa: E402
from harness.ops.clock import UNSYNCED_NOTE  # noqa: E402

WINDOW_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
WINDOW = {"start": WINDOW_NOW - timedelta(hours=6), "end": WINDOW_NOW + timedelta(hours=6)}


def _runs(session) -> tuple[Run, Run]:
    """One clean run and one marked as recorded under an unsynchronized clock."""
    good = Run(started_at=WINDOW_NOW, status="ok",
               notes={"pricing": {"gaps": 1, "signals": {}}})
    bad = Run(started_at=WINDOW_NOW, status="ok",
              notes={"pricing": {"gaps": 1, "signals": {}}, **UNSYNCED_NOTE})
    session.add_all([good, bad])
    session.commit()
    return good, bad


def _fair(session, run: Run, **kw) -> FairValue:
    fields = {"fair_p": "0.5000", "created_at": WINDOW_NOW, "staleness_s": 5,
              "feed_lag_s": 5, **kw}
    row = FairValue(run_id=run.id, game_id=4242, market_type="moneyline",
                    fair_source="direct", **fields)
    session.add(row)
    session.commit()
    return row


def test_dashboard_queries_drop_an_unsynced_run(db_session):
    from harness.dashboard import queries

    good, bad = _runs(db_session)
    cutoff = WINDOW_NOW - timedelta(hours=1)
    for limit in (None, 10):
        assert len(queries.recent_run_notes(db_session, cutoff, limit=limit)) == 1
        assert len(queries.recent_runs(db_session, cutoff, limit=limit)) == 1
        assert len(queries.recent_runs_pricing(db_session, cutoff, limit=limit)) == 1


def test_report_tables_drop_an_unsynced_run(db_session):
    from harness.report.tables import _T1_PRICING_TICKS, _T4B_FAIRS, _T8_QUERIES

    good, bad = _runs(db_session)
    _fair(db_session, good)
    _fair(db_session, bad)
    for run in (good, bad):
        db_session.add(MarketGapSnapshot(run_id=run.id, venue_market_id=1,
                                         created_at=WINDOW_NOW, dow=1, hour_ct=7,
                                         price_bucket=50))
    db_session.commit()
    assert db_session.execute(_T1_PRICING_TICKS, WINDOW).scalar() == 1
    assert len(list(db_session.execute(_T4B_FAIRS, WINDOW))) == 1
    sql, _ = _T8_QUERIES["fair_values staleness_s p50"]
    assert db_session.execute(text(sql), WINDOW).one()[1] == 1


def test_research_features_drop_an_unsynced_run(db_session):
    from harness.research import features

    good, bad = _runs(db_session)
    _fair(db_session, good, fair_p="0.4000")
    # Newer, but written under a clock the kernel disowned: the as-of read must not see it.
    _fair(db_session, bad, fair_p="0.9000")
    params = {"game_id": 4242, "market_type": "moneyline", "as_of": WINDOW_NOW}
    row = db_session.execute(features._NEWEST_FAIR, params).one()
    assert float(row.fair_p) == 0.4


def test_the_invariant_checks_drop_an_unsynced_run(db_session):
    from harness.ops.checks import CHECKS

    good, bad = _runs(db_session)
    _fair(db_session, bad, created_at=datetime.now(timezone.utc))
    db_session.execute(text("update fair_values set staleness_s = -1, feed_lag_s = -1"))
    db_session.commit()
    by_name = {c.name: c for c in CHECKS}
    for name in ("fair_values_negative_staleness", "fair_values_negative_feed_lag"):
        assert db_session.execute(text(by_name[name].sql)).scalar() == 0
