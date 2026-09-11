"""One queue row per signal (ruling B-C1), written where the intent is written."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from harness.research.veto import bucket_start
from tests.veto_fixtures import seed_game

NOW = datetime(2026, 9, 15, 18, 7, tzinfo=timezone.utc)


@pytest.fixture
def seeded_candidates(db_session):
    """Rows shaped like `harness.execution.store.candidate_signals`' result.

    `SimpleNamespace`, because that is all `insert_intents` reads and a real `Row` would need the
    whole join seeded to produce three of them. The `venue_markets` rows behind them are real:
    the enqueue resolves each signal's `market_type` from that table.
    """
    game, market = seed_game(db_session, kickoff=NOW + timedelta(hours=4))
    rows = []
    for index in range(3):
        rows.append(SimpleNamespace(
            signal_id=9000 + index, variant_id="v_base", venue="kalshi",
            venue_market_id=market.id, ticker=market.ticker, side="yes",
            price_target=Decimal("0.4800"), contracts=20, edge=Decimal("0.0300"),
            edge_min=Decimal("0.0100"), fair_p=Decimal("0.5100"), fair_value_id=None,
            game_id=game.id, kickoff_utc=game.kickoff_utc, stake=Decimal("10.00"),
            created_at=NOW + timedelta(seconds=index)))
    return rows


def test_the_bucket_is_tumbling_not_sliding():
    """Underspecified item 1: a tumbling 30-minute bucket and a sliding 30-minute cache
    disagree at the boundary. Two signals two minutes apart across a bucket edge get two calls,
    and this is where that is decided."""
    a = datetime(2026, 9, 15, 18, 29, tzinfo=timezone.utc)
    b = datetime(2026, 9, 15, 18, 31, tzinfo=timezone.utc)
    assert bucket_start(a, 30) == datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)
    assert bucket_start(b, 30) == datetime(2026, 9, 15, 18, 30, tzinfo=timezone.utc)
    assert bucket_start(a, 30) != bucket_start(b, 30)


def test_the_bucket_drops_seconds_and_microseconds():
    edge = datetime(2026, 9, 15, 18, 29, 59, 999_999, tzinfo=timezone.utc)
    assert bucket_start(edge, 30) == datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)


def test_every_intent_enqueues_exactly_one_signal(db_session, env_settings, seeded_candidates):
    """`insert_intents` already writes one intent per candidate signal with
    `on_conflict_do_nothing(signal_id)` and counts only the rows it wrote, which makes it the
    exact once-per-signal hook the queue needs."""
    from harness.execution import store

    written = store.insert_intents(db_session, seeded_candidates, NOW, replay=False)
    db_session.flush()
    rows = db_session.execute(text(
        "select signal_id, game_id, market_type, bucket_start, claimed_at from veto_queue "
        "order by signal_id")).all()
    assert len(rows) == written
    assert all(r.claimed_at is None for r in rows)
    assert {r.bucket_start for r in rows} == {bucket_start(NOW, 30)}
    assert {r.market_type for r in rows} == {"moneyline"}


def test_a_replay_run_enqueues_nothing(db_session, env_settings, seeded_candidates):
    from harness.execution import store

    store.insert_intents(db_session, seeded_candidates, NOW, replay=True)
    db_session.flush()
    assert db_session.execute(text("select count(*) from veto_queue")).scalar() == 0


def test_re_running_the_intake_does_not_double_enqueue(db_session, env_settings,
                                                       seeded_candidates):
    from harness.execution import store

    store.insert_intents(db_session, seeded_candidates, NOW, replay=False)
    store.insert_intents(db_session, seeded_candidates, NOW + timedelta(seconds=15), replay=False)
    db_session.flush()
    counts = db_session.execute(text(
        "select signal_id, count(*) from veto_queue group by signal_id having count(*) > 1")).all()
    assert counts == []


def test_the_enqueue_never_fails_the_executor(db_session, env_settings, seeded_candidates,
                                              monkeypatch):
    """Ruling 1's shape, applied here: the veto is advisory and a queue write must never cost
    the executor an intent."""
    from harness.execution import store

    monkeypatch.setattr(store, "_write_queue_batch",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert store.insert_intents(db_session, seeded_candidates, NOW, replay=False) > 0
    assert db_session.execute(text("select count(*) from intents")).scalar() == 3


def test_a_failed_market_type_lookup_never_fails_the_executor(db_session, env_settings,
                                                              seeded_candidates, monkeypatch):
    """Round 3, from the re-review: `_market_types_of` used to run outside the guard that now
    covers only `_write_queue_batch` -- a raise there (a statement timeout under contention, in
    production) escaped `insert_intents` and cost the whole batch of intents, not just the queue
    write. The lookup now sits inside the same savepoint as the batched write."""
    from harness.execution import store

    monkeypatch.setattr(store, "_market_types_of",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert store.insert_intents(db_session, seeded_candidates, NOW, replay=False) == 3
    assert db_session.execute(text("select count(*) from intents")).scalar() == 3
    assert db_session.execute(text("select count(*) from veto_queue")).scalar() == 0


def test_the_market_type_is_looked_up_once_for_the_batch(db_session, env_settings,
                                                         seeded_candidates):
    """Review round 1, minor: the executor's loop ceiling is 7.5 s and a burst is exactly when
    both the row count and the contention are highest, so the lookup is one statement for the
    batch rather than one per intent."""
    from harness.execution import store

    seen = []
    real = store._market_types_of
    monkey = lambda session, rows: (seen.append(len(rows)), real(session, rows))[1]
    original, store._market_types_of = store._market_types_of, monkey
    try:
        store.insert_intents(db_session, seeded_candidates, NOW, replay=False)
    finally:
        store._market_types_of = original
    assert seen == [3]


def test_no_lookup_happens_when_nothing_is_written(db_session, env_settings, seeded_candidates):
    """The lookup is lazy, so a loop whose candidates all conflict pays for nothing."""
    from harness.execution import store

    store.insert_intents(db_session, seeded_candidates, NOW, replay=False)
    calls = []
    original, store._market_types_of = store._market_types_of, (
        lambda session, rows: calls.append(1) or {})
    try:
        assert store.insert_intents(db_session, seeded_candidates, NOW, replay=False) == 0
    finally:
        store._market_types_of = original
    assert calls == []


def test_a_failed_enqueue_leaves_the_intents_committed(db_session, env_settings,
                                                       seeded_candidates, monkeypatch):
    """The guard is a savepoint, so the failed batched queue write rolls back on its own and
    every intent that shares the transaction survives it."""
    from harness.execution import store

    def _explode(session, queue_values):
        row = queue_values[0]
        session.execute(text("insert into veto_queue (signal_id, market_type, bucket_start, "
                             "enqueued_at) values (:s, :m, :b, :n)"),
                        {"s": row["signal_id"], "m": row["market_type"],
                         "b": row["bucket_start"], "n": row["enqueued_at"]})
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "_write_queue_batch", _explode)
    assert store.insert_intents(db_session, seeded_candidates, NOW, replay=False) == 3
    assert db_session.execute(text("select count(*) from intents")).scalar() == 3
    assert db_session.execute(text("select count(*) from veto_queue")).scalar() == 0


def test_the_whole_batch_is_written_under_one_savepoint(db_session, env_settings,
                                                        seeded_candidates, monkeypatch):
    """Fix round 2, I2: one savepoint around one batched insert of the whole queue-row list,
    not one savepoint per intent -- a burst well past 64 candidates would otherwise overflow
    PostgreSQL's `pg_subtrans` SLRU, which costs every concurrent reader cluster-wide."""
    from harness.execution import store

    calls: list[int] = []
    real_begin_nested = db_session.begin_nested

    def counting_begin_nested(*a, **k):
        calls.append(1)
        return real_begin_nested(*a, **k)

    monkeypatch.setattr(db_session, "begin_nested", counting_begin_nested)
    written = store.insert_intents(db_session, seeded_candidates, NOW, replay=False)
    assert written == 3
    assert len(calls) == 1, "one savepoint should cover the whole batch, not one per intent"
    rows = db_session.execute(text(
        "select signal_id from veto_queue order by signal_id")).scalars().all()
    assert rows == sorted(row.signal_id for row in seeded_candidates)
