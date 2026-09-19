"""§1.4/§1.5: each arm carries its own world, and a resume reproduces it exactly.

Three deviations from the brief's SQL, each for a spec reason and each keeping the assertion:

* `exp_checkpoint` has §2's columns exactly, so the capacity counter is read out of the
  checkpoint's `state` (`state->>'open_orders_count'`) rather than from a column §2 does not
  declare. `state` is where §1.4 puts the counter, and a second copy could drift from it.
* `exp_order`'s queue column is §2's `queue_ahead_at_place`, aliased in the one query that reads
  it.
* An arm's own capacity refusal is an `exp_mismatch` row of §2's `capacity` kind, not an
  `exp_limitation` one: `exp_limitation.kind`'s vocabulary is closed by §2 and its invariant
  query, and `capacity_blocked` is not in it (ruling I5).

Every expectation is written from the fixture's own stamps, never read back from the code under
test (6B's I-13 rule).
"""
import uuid
from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from harness.db.models import (FairValue, Game, Intent, MarketGapSnapshot, MetricSample,
                               OrderbookSnapshot, VenueMarket, VenueTrade)
from harness.execution import plan as plan_mod
from harness.experiments.execution_viability import IsolationError, adapter, capture, storage
from harness.experiments.execution_viability.manifest import Manifest, ManifestMismatch

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=15)

#: The whole run's window, which every chunk of it shares: the manifest is hashed over the run,
#: never over the chunk, so a 3 x 1 h run and a 1 x 3 h run freeze the same hash (§1.2).
WINDOW_END = NOW + timedelta(hours=3)
VARIANT = "v_base"
VARIANT_CFG = {VARIANT: {"stale_s": 180, "apply_caps": False, "bankroll": 3000,
                         "per_bet_cap": 0.03, "per_game_cap": 0.05, "daily_cap": 0.15,
                         "max_open": 200}}
REPRICE_TICKER = "KXNFLGAME-26SEP20DETBAL-DET"
FILL_TICKER = "KXFILL-1"
#: The two derived manifest fields this test freezes a run under; `storage.rebuild_manifest`
#: is handed the same two, so an unchanged run resumes and a changed setting does not (§1.2).
CODE_SHA = "0" * 40
SCHEMA_VERSION = "0015"


# --- the fixture tape --------------------------------------------------------------------------

def _game(session, *, kickoff):
    game = Game(sport="nfl", home_team_id=1, away_team_id=2, kickoff_utc=kickoff,
                espn_event_id=f"e-{int(kickoff.timestamp())}", status="scheduled")
    session.add(game)
    session.flush()
    return game


def _market(session, *, game_id, ticker):
    market = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME",
                         series_ticker="KXNFLGAME", game_id=game_id, market_type="moneyline",
                         match_status="matched", match_key=f"k-{ticker}", first_seen_raw_id=1,
                         last_seen_at=NOW)
    session.add(market)
    session.flush()
    return market


def _priced(session, market, *, at, fair_p="0.5100"):
    # `uq_fair_value_row` is unique on (run_id, game_id, market_type, ...), and this fixture
    # prices several markets of one game at the same instant, so the pricing run id carries the
    # market: one row per (market, instant), which is what the tape holds.
    run = int(at.timestamp()) * 1000 + market.id
    fair = FairValue(run_id=run, game_id=market.game_id, market_type="moneyline",
                     outcome_team_id=1, fair_p=Decimal(fair_p), fair_source="direct",
                     created_at=at)
    session.add(fair)
    session.flush()
    session.add(MarketGapSnapshot(
        run_id=run, venue_market_id=market.id, fair_value_id=fair.id,
        fair_source="direct", fair_p=Decimal(fair_p), venue_mid=Decimal("0.5000"),
        best_bid=Decimal("0.4800"), best_ask=Decimal("0.5200"), staleness_s=10,
        stale_allowance_s=180, dow=at.weekday(), hour_ct=at.hour, created_at=at))
    session.flush()


def _intent(session, market, *, created_at, target_prob="0.4800", signal_id, kickoff):
    intent = Intent(signal_id=signal_id, variant_id=VARIANT, venue="kalshi",
                    venue_market_id=market.id, ticker=market.ticker, side="yes",
                    target_prob=Decimal(target_prob), target_contracts=Decimal("20"),
                    edge=Decimal("0.0300"), edge_min=Decimal("0.0100"),
                    fair_p=Decimal("0.5100"), game_id=market.game_id, kickoff_utc=kickoff,
                    stake=Decimal("9.60"), signal_created_at=created_at, created_at=created_at,
                    replay=False)
    session.add(intent)
    session.flush()
    return intent


def _book(session, market, *, fetched_at, yes_bids, raw_id=1):
    session.add(OrderbookSnapshot(raw_id=raw_id, venue_market_id=market.id,
                                  fetched_at=fetched_at, yes_bids=yes_bids,
                                  no_bids=[["0.4500", "100"]]))
    session.flush()


def _print(session, *, ts, trade_id, ticker, count="20", yes_price="0.4800"):
    session.add(VenueTrade(venue="kalshi", trade_id=trade_id, ticker=ticker, ts=ts,
                           yes_price=Decimal(yes_price), count=Decimal(count), taker_side="no",
                           taker_outcome_side="no", source="ws"))
    session.flush()


def _clock_samples(session, *, until):
    """`exec.loop_ms` samples every 60 s: the retained instants `resolve_instants` unions (C1)."""
    ts = NOW
    while ts <= until:
        session.add(MetricSample(ts=ts, source="exec", name="exec.loop_ms",
                                 labels={}, value=Decimal("900")))
        ts += timedelta(seconds=60)
    session.flush()


def _seed(session, *, wanted_orders=None):
    """One deterministic tape, seeded once per test. Every status §5 names is produced by a
    rule of the shared code: a market that stops being priced is cancelled `fair_stale`, a
    game whose kickoff arrives expires its order, a print fills one, a newer target reprices
    one, and the rest rest."""
    if session.execute(text("select count(*) from venue_markets")).scalar():
        return
    long_game = _game(session, kickoff=NOW + timedelta(hours=6))
    near_game = _game(session, kickoff=NOW + timedelta(minutes=40))
    _clock_samples(session, until=WINDOW_END)

    if wanted_orders:
        # §1.4's capacity case: one instant, `wanted_orders` placeable intents, one shared pool.
        for n in range(wanted_orders):
            market = _market(session, game_id=long_game.id, ticker=f"KXCAP-{n:04d}")
            _priced(session, market, at=NOW - timedelta(seconds=30))
            _intent(session, market, created_at=NOW - timedelta(minutes=5), signal_id=1000 + n,
                    kickoff=long_game.kickoff_utc)
        session.commit()
        return

    # A market that is priced once and never again: cancelled `fair_stale` at 180 s.
    stale = _market(session, game_id=long_game.id, ticker="KXSTALE-1")
    _priced(session, stale, at=NOW - timedelta(seconds=30))
    _intent(session, stale, created_at=NOW - timedelta(minutes=5), signal_id=1,
            kickoff=long_game.kickoff_utc)

    # A market whose kickoff arrives inside the window: its order expires at kickoff - 10 min.
    expiring = _market(session, game_id=near_game.id, ticker="KXEXPIRE-1")
    _intent(session, expiring, created_at=NOW - timedelta(minutes=5), signal_id=2,
            kickoff=near_game.kickoff_utc)

    # A market with a print that takes the whole order: filled, and re-entered afterwards.
    filling = _market(session, game_id=long_game.id, ticker="KXFILL-1")
    _book(session, filling, fetched_at=NOW - timedelta(seconds=20),
          yes_bids=[["0.4700", "50"]], raw_id=2)
    _intent(session, filling, created_at=NOW - timedelta(minutes=5), signal_id=3,
            kickoff=long_game.kickoff_utc)
    _print(session, ts=NOW + timedelta(minutes=10), trade_id="t-4471", ticker="KXFILL-1")

    # The repriced market: a newer target two points away opens a new order at the new price,
    # whose queue is the book's own count at *that* price.
    repriced = _market(session, game_id=long_game.id, ticker=REPRICE_TICKER)
    _book(session, repriced, fetched_at=NOW - timedelta(seconds=20),
          yes_bids=[["0.4800", "10"], ["0.5000", "25"]], raw_id=3)
    _intent(session, repriced, created_at=NOW - timedelta(minutes=5), signal_id=4,
            kickoff=long_game.kickoff_utc)
    _intent(session, repriced, created_at=NOW + timedelta(minutes=20), target_prob="0.5000",
            signal_id=5, kickoff=long_game.kickoff_utc)

    resting = [_market(session, game_id=long_game.id, ticker=f"KXOPEN-{n}") for n in (1, 2)]
    for n, market in enumerate(resting):
        _intent(session, market, created_at=NOW - timedelta(minutes=5), signal_id=6 + n,
                kickoff=long_game.kickoff_utc)

    # Everything except the stale market stays priced every 60 s for the whole window: the
    # baseline cancels a resting order once its fair value is older than
    # `max(stale_s, stale_allowance_s)` = 180 s, so a slower cadence would make every case here
    # a `fair_stale` case.
    at = NOW - timedelta(seconds=30)
    raw_id = 100
    while at <= WINDOW_END:
        for market in [expiring, filling, repriced, *resting]:
            _priced(session, market, at=at)
        # The REST ladder is re-fetched on the same cadence: a book older than
        # `exec_book_max_age_s` is no book at all, and an order placed against no book joins no
        # queue (R10), which would make the reprice case a statement about a missing ladder
        # rather than about queue priority.
        raw_id += 1
        _book(session, filling, fetched_at=at, yes_bids=[["0.4700", "50"]], raw_id=raw_id)
        raw_id += 1
        _book(session, repriced, fetched_at=at,
              yes_bids=[["0.4800", "10"], ["0.5000", "25"]], raw_id=raw_id)
        at += timedelta(seconds=60)
    session.commit()


# --- the run helpers ---------------------------------------------------------------------------

def _exec(s):
    return plan_mod.ExecSettings.from_settings(s)


def _manifest(run_id: str, *, exec_settings, cadence_allowance=None) -> Manifest:
    """One run's frozen manifest. Its fields describe the **run**, never the chunk, so every
    chunk of one run freezes the same hash (§1.2).

    `baseline_settings` is the execution settings document `storage.rebuild_manifest` rebuilds
    at resume time, so a run frozen here is resumable by the real code path and a changed
    setting is a refusal rather than a silently different definition (C1).
    """
    return Manifest(
        run_id=run_id, code_sha=CODE_SHA, schema_version=SCHEMA_VERSION,
        simulator_version="6b",
        pricing_version="5", baseline_settings=storage.settings_document(exec_settings),
        variant_configs=VARIANT_CFG,
        arms=({"arm_id": "A", "label": "baseline", "cadence_allowance": cadence_allowance},
              {"arm_id": "B", "label": "cadence", "cadence_allowance": cadence_allowance}),
        arm_hashes=("a" * 64, "b" * 64), capture_hashes={"orders": {"sha256": "c" * 64}},
        run_id_bounds=(0, 0), order_id_bounds=(0, 0), fill_id_bounds=(0, 0),
        placement_start=NOW, placement_end=WINDOW_END, warmup_start=NOW,
        observation_end=WINDOW_END, extracted_at=NOW, exclusions=(),
        portfolio_identity=(run_id, "A", VARIANT), shared_slot_limit=150,
        clock_mode="retained_action_instants", cohort=("nfl",), selection_seed=1,
        opportunity_definition={"kind": "featured"}, scheduled_observations=0,
        available_observations=0, timestamp_semantics={"ts": "decision_instant"},
        credit_budget=0, request_budget=0, resource_limits={"batch_rows": 20_000},
        markout_horizons=(60, 300, 900), missingness_policy="labelled",
        review_deadline="2026-09-25")


def _writer(session, run_id: str) -> storage.ExperimentWriter:
    """The real writer on the test session: `open()` is the role/secret path, which
    `tests/test_exp_isolation.py` owns."""
    return storage.ExperimentWriter(session, run_id=run_id, batch_rows=20_000)


def _runner(session, s, *, run_id, arm, policy=None):
    return adapter.ArmRunner(run_id=run_id, arm_id=arm, policy=policy, variant_cfg=VARIANT_CFG,
                             exec_settings=s, walkers={}, tz="America/Chicago")


def _instants(session, *, since, until):
    return [i for i in capture.resolve_instants(session, warmup_start=NOW,
                                                observation_end=WINDOW_END,
                                                variant_ids=[VARIANT])
            if since <= i < until]


def run_arm(session, s, *, arm="A", since, until, resume_of=None, kill_after_instants=None,
            wanted_orders=None, policy=None, instants=None, mismatch_max=None) -> str:
    """Freeze a manifest, open a writer, step the retained instants of `[since, until)` and
    return the run id. A resume continues the **same** run id from its own checkpoint."""
    _seed(session, wanted_orders=wanted_orders)
    run_id = resume_of or str(uuid.uuid4())
    manifest = _manifest(run_id, exec_settings=_exec(s))
    manifest_hash = manifest.freeze()
    writer = _writer(session, run_id)
    if resume_of is None:
        storage.write_run(writer, manifest=manifest, manifest_hash=manifest_hash, created_at=NOW)
        storage.write_arms(writer, [{"run_id": run_id, "arm_id": arm, "label": "baseline",
                                     "spec": {"policy": "baseline"}, "spec_hash": "a" * 64}])
    runner = _runner(session, s, run_id=run_id, arm=arm, policy=policy)
    state = storage.resume(session, run_id=run_id, arm_id=arm, manifest=manifest)
    if state is not None:
        adapter.restore(runner, state)
    adapter.run_chunk(session, runner,
                      list(instants) if instants is not None
                      else _instants(session, since=since, until=until),
                      writer=writer, manifest_hash=manifest_hash,
                      stop_after=kill_after_instants, mismatch_max=mismatch_max)
    writer.commit()
    return run_id


def run_two_arms(session, s, *, instants) -> str:
    """Arms A and B in **one** process and one transaction, on the same tape (§1.4).

    Both arms run the **same** policy on the same instants (fix round 1, ruling D23/I5): two
    arms that place different orders cannot tell isolation from divergence, while two arms that
    place the same orders make every shared byte visible -- a shared open-order list, capacity
    counter, order-id counter or ledger would show up immediately as a missing or a merged row.
    """
    _seed(session)
    run_id = str(uuid.uuid4())
    manifest = _manifest(run_id, exec_settings=_exec(s))
    manifest_hash = manifest.freeze()
    writer = _writer(session, run_id)
    storage.write_run(writer, manifest=manifest, manifest_hash=manifest_hash, created_at=NOW)
    storage.write_arms(writer, [
        {"run_id": run_id, "arm_id": "A", "label": "baseline", "spec": {"policy": "baseline"},
         "spec_hash": "a" * 64},
        {"run_id": run_id, "arm_id": "B", "label": "baseline", "spec": {"policy": "baseline"},
         "spec_hash": "b" * 64}])
    runners = [_runner(session, s, run_id=run_id, arm="A"),
               _runner(session, s, run_id=run_id, arm="B")]
    for runner in runners:
        adapter.run_chunk(session, runner, list(instants), writer=writer,
                          manifest_hash=manifest_hash)
    writer.commit()
    return run_id


def changed_copy(s, run_id: str) -> Manifest:
    """The same run's manifest with one field altered, so its hash differs (§1.2)."""
    return _manifest(run_id, exec_settings=_exec(s), cadence_allowance=1000)


def _rows(session, run_id, arm_id):
    """The arm's `exp_order` rows, projected through **its own** order id (D22).

    `exp_order.id` is the database's surrogate key, so two runs of the same tape hold the same
    orders under different ids; `arm_order_id` is the id the arm issued, which is what §1.4's
    "the same run chunked two ways writes the same rows" is a statement about.
    """
    return session.execute(text(
        "select arm_order_id, ticker, prob, contracts, status, placed_at, cancel_reason, "
        "filled_contracts from exp_order "
        "where run_id = :r and arm_id = :a order by placed_at, arm_order_id"),
        {"r": run_id, "a": arm_id}).all()


def _row_ids(session, run_id, arm_id) -> set[int]:
    return {row.id for row in session.execute(text(
        "select id from exp_order where run_id = :r and arm_id = :a"),
        {"r": run_id, "a": arm_id}).all()}


def _fills(session, run_id, arm_id):
    """§1.4's fill set for one arm, on §2's own invariant join `o.id = f.exp_order_id` (D22).

    Projected through the order's own id and ordered by the tape's stamp, so two chunkings of
    one run are comparable row for row and neither side can be empty without the caller seeing
    it.
    """
    return session.execute(text(
        "select o.arm_order_id, o.ticker, f.filled_at, f.contracts, f.prob, f.fill_method, "
        "f.source_trade_id, f.through from exp_fill f "
        "join exp_order o on o.id = f.exp_order_id "
        "where f.run_id = :r and f.arm_id = :a order by f.filled_at, f.id"),
        {"r": run_id, "a": arm_id}).all()


_CHECKPOINT_ROW = text(
    "select (state->>'open_orders_count')::int as open_orders, cursor_event_id, "
    "md5(state::text) as digest, manifest_hash from exp_checkpoint "
    "where run_id = :r and arm_id = :a")


def _checkpoint(session, run_id, arm_id):
    return session.execute(_CHECKPOINT_ROW, {"r": run_id, "a": arm_id}).one()


# --- §5's list, one case each ------------------------------------------------------------------

TWO_INSTANTS = [NOW, NOW + STEP]


def test_two_arms_in_one_process_cannot_see_each_others_orders(db_session, env_settings):
    """Two arms of one run, same policy, same instants: same decisions, separate rows.

    The assertion is strict (fix round 1, ruling D23/I5): every row of A is a row of its own,
    A's set is exactly B's set projected through each arm's own order ids, and both equal what
    the arm produces alone on the same tape -- so nothing either arm holds moved because the
    other placed.
    """
    run_id = run_two_arms(db_session, env_settings, instants=TWO_INSTANTS)
    a, b = _rows(db_session, run_id, "A"), _rows(db_session, run_id, "B")
    assert a and b
    assert a == b                                     # identical arms, identical decisions
    assert _row_ids(db_session, run_id, "A").isdisjoint(_row_ids(db_session, run_id, "B"))
    solo = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + 2 * STEP,
                   instants=TWO_INSTANTS)
    assert _rows(db_session, solo, "A") == a          # B's presence moved nothing of A's
    assert db_session.execute(text(
        "select count(*) from exp_order where run_id = :r and arm_id not in ('A','B')"),
        {"r": run_id}).scalar() == 0


def test_two_arms_cannot_see_each_others_capacity_counter_or_cursor(db_session, env_settings):
    """Each arm's checkpoint carries **its own** counter and cursor: one row each, each equal
    to what that arm alone produces, and neither the sum nor the other arm's number."""
    run_id = run_two_arms(db_session, env_settings, instants=TWO_INSTANTS)
    a, b = _checkpoint(db_session, run_id, "A"), _checkpoint(db_session, run_id, "B")
    assert db_session.execute(text(
        "select count(*) from exp_checkpoint where run_id = :r"), {"r": run_id}).scalar() == 2
    resting = dict(db_session.execute(text(
        "select arm_id, count(*) from exp_order where run_id = :r and status = 'open' "
        "group by 1"), {"r": run_id}).all())
    assert a.open_orders == resting["A"] and b.open_orders == resting["B"]
    assert a.open_orders + b.open_orders == resting["A"] + resting["B"]   # never the sum each
    solo = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + 2 * STEP,
                   instants=TWO_INSTANTS)
    alone = _checkpoint(db_session, solo, "A")
    assert (a.open_orders, a.cursor_event_id) == (alone.open_orders, alone.cursor_event_id)
    assert (b.open_orders, b.cursor_event_id) == (alone.open_orders, alone.cursor_event_id)


def test_three_one_hour_chunks_equal_one_three_hour_chunk_row_for_row(db_session, env_settings):
    whole = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=3))
    chunked = None
    for hour in range(3):
        chunked = run_arm(db_session, env_settings, arm="A", since=NOW + timedelta(hours=hour),
                          until=NOW + timedelta(hours=hour + 1), resume_of=chunked)
    assert _rows(db_session, chunked, "A") == _rows(db_session, whole, "A")
    # §1.4 is about the fills too, and an equality of two empty sets proves nothing: the fill
    # set is asserted non-empty and against the fixture's own print (fix round 1, C3).
    fills = _fills(db_session, whole, "A")
    assert _fills(db_session, chunked, "A") == fills
    assert [(row.ticker, row.filled_at, row.contracts, row.prob, row.source_trade_id)
            for row in fills] == [(FILL_TICKER, NOW + timedelta(minutes=10), Decimal("20.00"),
                                   Decimal("0.4800"), "t-4471")]


def test_a_resumed_run_after_a_mid_slice_kill_equals_the_uninterrupted_run(db_session,
                                                                          env_settings):
    whole = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=2))
    killed = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=2),
                     kill_after_instants=17)
    resumed = run_arm(db_session, env_settings, arm="A", since=NOW,
                      until=NOW + timedelta(hours=2), resume_of=killed)
    assert _rows(db_session, resumed, "A") == _rows(db_session, whole, "A")
    fills = _fills(db_session, whole, "A")
    assert _fills(db_session, resumed, "A") == fills
    # The kill lands at instant 17, before the print at NOW + 10 min, so the resumed run has to
    # produce that fill after the boundary or the equality above would be two empty sets.
    assert [row.source_trade_id for row in fills] == ["t-4471"]


def test_the_recorded_print_is_credited_once_across_the_whole_run(db_session, env_settings):
    """§1.5 end to end: the tape holds one 20-contract print, and however many orders the arm
    rests on that key over an hour, the run's fills on it total the print and no more."""
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    taken = [row for row in _fills(db_session, run_id, "A")
             if row.source_trade_id == "t-4471"]
    assert [(row.ticker, row.contracts, row.prob, row.filled_at, row.fill_method)
            for row in taken] == [(FILL_TICKER, Decimal("20.00"), Decimal("0.4800"),
                                   NOW + timedelta(minutes=10), "queue_model")]
    allocation = db_session.execute(text(
        "select available, allocated from exp_allocation where run_id = :r "
        "and source_trade_id = 't-4471'"), {"r": run_id}).one()
    assert (allocation.available, allocation.allocated) == (Decimal("20.00"), Decimal("20.00"))


def test_capacity_stays_occupied_while_an_order_rests(db_session, env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(minutes=2))
    resting = db_session.execute(text(
        "select count(*) from exp_order where run_id = :r and status = 'open'"),
        {"r": run_id}).scalar()
    occupied = _checkpoint(db_session, run_id, "A").open_orders
    assert occupied == resting and resting > 0


def test_capacity_is_released_on_cancel_on_expiry_and_on_fill(db_session, env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    closed = db_session.execute(text(
        "select status, count(*) from exp_order where run_id = :r and status <> 'open' "
        "group by 1"), {"r": run_id}).all()
    assert {row.status for row in closed} == {"cancelled", "expired", "filled"}
    assert _checkpoint(db_session, run_id, "A").open_orders == db_session.execute(
        text("select count(*) from exp_order where run_id = :r and status = 'open'"),
        {"r": run_id}).scalar()


def test_the_hundred_and_fifty_first_simultaneous_order_is_blocked_inside_one_arm(db_session,
                                                                                 env_settings):
    """One `capacity` row per blocked key per **episode**, not per instant (D23/I4).

    The same intent is refused at both instants of this run; a row per instant would write the
    same fact every 15 s for as long as the pool stayed full, and `exp_mismatch_max` would then
    stop runs on their own bookkeeping. `mismatch_max=0` is passed to prove the ceiling counts
    unexplained rows only: every row here is explained, and the run does not stop.
    """
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + 2 * STEP,
                     instants=TWO_INSTANTS, wanted_orders=151, mismatch_max=0)
    assert db_session.execute(text(
        "select count(*) from exp_order where run_id = :r and status = 'open'"),
        {"r": run_id}).scalar() == 150
    rows = db_session.execute(text(
        "select instant, explained, cause from exp_mismatch where run_id = :r "
        "and kind = 'capacity'"), {"r": run_id}).all()
    assert len(rows) == 1
    assert rows[0].instant == NOW and rows[0].explained is True
    assert rows[0].cause == plan_mod.EXEC_CAPACITY
    # Both instants were stepped: the second wrote no second row rather than never happening.
    checkpoint = db_session.execute(text(
        "select state->'last_instant'->>'__t' as last from exp_checkpoint "
        "where run_id = :r and arm_id = 'A'"), {"r": run_id}).scalar()
    assert datetime.fromisoformat(checkpoint) == NOW + STEP


def test_a_key_is_not_permanently_blocked_after_its_first_placement(db_session, env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    per_key = db_session.execute(text(
        "select ticker, count(*) as n from exp_order where run_id = :r group by 1 "
        "order by n desc limit 1"), {"r": run_id}).one()
    assert per_key.n > 1          # the same market is re-entered after its first order closes


def test_there_is_no_fill_after_expiry(db_session, env_settings):
    """§2's invariant query verbatim, which D22's surrogate key is what makes unambiguous."""
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    late = db_session.execute(text(
        "select count(*) from exp_fill f join exp_order o on o.id = f.exp_order_id "
        "where f.run_id = :r and o.expiry is not null and f.filled_at > o.expiry"),
        {"r": run_id}).scalar()
    assert late == 0
    assert _fills(db_session, run_id, "A")        # a zero over an empty set is not the case


def test_a_repriced_order_is_a_new_row_at_the_back_of_the_queue(db_session, env_settings):
    """The fixture's ladder is `[[0.4800, 10], [0.5000, 25]]`, so the queue each order joins is
    the book's own count at **its** price: 10 for the first, 25 for the re-priced one."""
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    pair = db_session.execute(text(
        "select arm_order_id, prob, queue_ahead_at_place as queue_ahead, placed_at "
        "from exp_order where run_id = :r and ticker = :t order by placed_at limit 2"),
        {"r": run_id, "t": REPRICE_TICKER}).all()
    assert pair[0].arm_order_id != pair[1].arm_order_id
    assert (pair[0].prob, pair[1].prob) == (Decimal("0.4800"), Decimal("0.5000"))
    assert (pair[0].queue_ahead, pair[1].queue_ahead) == (Decimal("10.00"), Decimal("25.00"))


def test_resume_refuses_a_changed_manifest_and_leaves_the_checkpoint_byte_identical(db_session,
                                                                                   env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + STEP)
    changed_manifest = changed_copy(env_settings, run_id)
    before = _checkpoint(db_session, run_id, "A")
    with pytest.raises(ManifestMismatch):
        storage.resume(db_session, run_id=run_id, arm_id="A", manifest=changed_manifest)
    assert _checkpoint(db_session, run_id, "A") == before


def test_a_changed_execution_setting_makes_the_resume_refuse(db_session, env_settings):
    """C1/ruling D21: `exp run` rebuilds the manifest for the code and settings about to step
    the tape and resumes against **that**, so a changed `ExecSettings` field is a refusal.

    The rebuild is asserted through `storage.rebuild_manifest` and `storage.resume`, which is
    the pair `cli.run_cmd` calls; the command itself additionally needs the §4.7 role and the
    exp secret, which `tests/test_exp_isolation.py` owns.
    """
    exec_settings = _exec(env_settings)
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + STEP)
    document = db_session.execute(text(
        "select manifest from exp_run where run_id = :r"), {"r": run_id}).scalar()
    same = storage.rebuild_manifest(document, run_id=run_id, code_sha=CODE_SHA,
                                    schema_version=SCHEMA_VERSION, variant_cfg=VARIANT_CFG,
                                    exec_settings=exec_settings)
    assert same.freeze() == _checkpoint(db_session, run_id, "A").manifest_hash
    assert storage.resume(db_session, run_id=run_id, arm_id="A", manifest=same) is not None
    before = _checkpoint(db_session, run_id, "A")
    for changed in (replace(exec_settings, max_open_orders=149),):
        drifted = storage.rebuild_manifest(document, run_id=run_id, code_sha=CODE_SHA,
                                           schema_version=SCHEMA_VERSION,
                                           variant_cfg=VARIANT_CFG, exec_settings=changed)
        with pytest.raises(ManifestMismatch):
            storage.resume(db_session, run_id=run_id, arm_id="A", manifest=drifted)
    # And a changed code sha, which is the other half of "this code, these settings".
    drifted = storage.rebuild_manifest(document, run_id=run_id, code_sha="1" * 40,
                                       schema_version=SCHEMA_VERSION, variant_cfg=VARIANT_CFG,
                                       exec_settings=exec_settings)
    with pytest.raises(ManifestMismatch):
        storage.resume(db_session, run_id=run_id, arm_id="A", manifest=drifted)
    assert _checkpoint(db_session, run_id, "A") == before


def _resting_order(*, order_id: int, placed_at: datetime, prob="0.4800"):
    """The shape `ArmRunner.adopt` reads: an `orders` row, by duck type."""
    return SimpleNamespace(
        id=order_id, intent_id=None, variant_id=VARIANT, ticker=FILL_TICKER,
        venue_market_id=1, side="yes", prob=Decimal(prob), contracts=Decimal("10"),
        filled_contracts=Decimal("0"), placed_at=placed_at,
        expiry=placed_at + timedelta(minutes=30), queue_ahead_at_place=Decimal("0"),
        kickoff_utc=NOW + timedelta(hours=6), game_id=1, match_key="k")


def test_allocation_order_inside_one_instant_is_placement_order_not_insertion_order(
        db_session, env_settings):
    """§1.5/ruling D23/I6: the ledger divides one print in the order the caller asks, so the
    caller's order is placement instant first and, on a tie, the order the arm issued them in
    -- not the order the runner happens to hold them in. Adopted newest-first here, which is
    the opposite of both. The arm's ids count down from -1, so -1 was issued before -4 and is
    served first on the tie (fix round 2).
    """
    runner = _runner(db_session, env_settings, run_id=str(uuid.uuid4()), arm="A")
    runner.adopt(_resting_order(order_id=-2, placed_at=NOW + timedelta(minutes=5)))
    runner.adopt(_resting_order(order_id=-1, placed_at=NOW))
    runner.adopt(_resting_order(order_id=-4, placed_at=NOW))
    assert [view.order_id for view in runner.world.open_orders] == [-2, -1, -4]
    # -1 and -4 share an instant and break the tie earlier-issued first; -2 is later and last.
    assert runner._allocation_order([]) == [-1, -4, -2]


def test_the_window_stops_at_a_chunk_boundary_when_the_executor_is_busy(db_session,
                                                                        env_settings):
    """C2/ruling D21: §4.3's guard is asked before **every** chunk, and the chunk that has
    already finished keeps its checkpoint, so the next invocation resumes from it."""
    _seed(db_session)
    run_id = str(uuid.uuid4())
    manifest = _manifest(run_id, exec_settings=_exec(env_settings))
    manifest_hash = manifest.freeze()
    writer = _writer(db_session, run_id)
    storage.write_run(writer, manifest=manifest, manifest_hash=manifest_hash, created_at=NOW)
    storage.write_arms(writer, [{"run_id": run_id, "arm_id": "A", "label": "baseline",
                                 "spec": {"policy": "baseline"}, "spec_hash": "a" * 64}])
    runner = _runner(db_session, env_settings, run_id=run_id, arm="A")
    asked = []

    def busy():
        asked.append(datetime.now(timezone.utc))
        return len(asked) > 1            # free for the first chunk, busy before the second

    until = NOW + timedelta(hours=2)
    result = adapter.run_window(
        db_session, runner, _instants(db_session, since=NOW, until=until), writer=writer,
        manifest_hash=manifest_hash, since=NOW, until=until, chunk=timedelta(hours=1),
        busy=busy)
    db_session.commit()
    assert (result.stopped, len(result.chunks), len(asked)) == ("executor_busy", 1, 2)
    assert result.chunks[0].instants > 0
    assert runner.last_instant is not None and runner.last_instant < NOW + timedelta(hours=1)
    stored = db_session.execute(text(
        "select state->'last_instant'->>'__t' as last from exp_checkpoint "
        "where run_id = :r and arm_id = 'A'"), {"r": run_id}).scalar()
    assert datetime.fromisoformat(stored) == runner.last_instant


def test_the_freezer_and_the_resume_rebuild_derive_the_same_manifest_fields(db_session,
                                                                            env_settings):
    """M11 (task-3 fix round 1): the run's frozen `exp_run.manifest` and the manifest a resume
    rebuilds must derive the same fields, or the first resume would refuse a run nothing had
    changed about.

    Asserted field by field over `storage._MANIFEST_DERIVED` -- the four the rebuild derives
    from the caller's own code sha, schema head, variant configuration and `ExecSettings`, plus
    the run id -- and then over the hash, which is the property the refusal actually uses.
    """
    exec_settings = _exec(env_settings)
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + STEP)
    document = db_session.execute(text(
        "select manifest from exp_run where run_id = :r"), {"r": run_id}).scalar()
    rebuilt = storage.rebuild_manifest(document, run_id=run_id, code_sha=CODE_SHA,
                                       schema_version=SCHEMA_VERSION, variant_cfg=VARIANT_CFG,
                                       exec_settings=exec_settings)
    for name in storage._MANIFEST_DERIVED:
        assert document[name] == getattr(rebuilt, name), name
    # `baseline_settings` in particular is the one serialisation both sides take
    # (`settings_document`), not two hand-written projections of `ExecSettings`.
    assert document["baseline_settings"] == storage.settings_document(exec_settings)
    stored_hash = db_session.execute(text(
        "select manifest_hash from exp_run where run_id = :r"), {"r": run_id}).scalar()
    assert rebuilt.freeze() == stored_hash


def test_the_writer_updates_one_row_on_its_natural_key_and_refuses_an_unkeyed_update(db_session,
                                                                                     env_settings):
    """M21's mechanism: `ExperimentWriter.update` rewrites the row a natural key names.

    §4.7's role holds INSERT and UPDATE on the `exp_*` tables and **no DELETE**, which is why
    a superseded row is updated rather than deleted and written again. The guards are the
    writer's own: a production table and a foreign `Table` object are refused here exactly as
    they are by `insert`, and an unkeyed update -- which would rewrite the whole table -- is
    refused before any statement runs.
    """
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + STEP)
    writer = _writer(db_session, run_id)
    table = writer.table("exp_limitation")
    writer.insert(table, [{"run_id": run_id, "kind": "arm_unavailable",
                           "scope": {"arm": "C"}, "detail": "before", "created_at": NOW}])
    writer.commit()
    rewritten = writer.update(table, key={"run_id": run_id, "kind": "arm_unavailable"},
                              values={"detail": "after"})
    writer.commit()
    assert rewritten == 1
    rows = db_session.execute(text(
        "select kind, detail from exp_limitation where run_id = :r and kind = 'arm_unavailable'"),
        {"r": run_id}).all()
    assert [(row.kind, row.detail) for row in rows] == [("arm_unavailable", "after")]
    with pytest.raises(ValueError, match="natural key"):
        writer.update(table, key={}, values={"detail": "everything"})
    from harness.db.models import Base

    with pytest.raises(IsolationError, match="orders"):
        writer.update(Base.metadata.tables["orders"], key={"id": 1}, values={"status": "x"})


def test_the_writers_rollback_discards_its_own_work_and_keeps_the_session_usable(db_session,
                                                                                 env_settings):
    """M17: `rollback()` is `close()` without the close.

    A caller whose step raised still has a row to record about the failure -- the observer's
    failure path is the case -- and needs the session clean rather than gone.
    """
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + STEP)
    writer = _writer(db_session, run_id)
    table = writer.table("exp_limitation")
    writer.insert(table, [{"run_id": run_id, "kind": "arm_unavailable",
                           "scope": {"arm": "C"}, "detail": "uncommitted", "created_at": NOW}])
    writer.rollback()
    assert db_session.execute(text(
        "select count(*) from exp_limitation where run_id = :r and kind = 'arm_unavailable'"),
        {"r": run_id}).scalar() == 0
    # And the session is still a session: the next write goes through.
    writer.insert(table, [{"run_id": run_id, "kind": "arm_unavailable",
                           "scope": {"arm": "C"}, "detail": "recorded", "created_at": NOW}])
    writer.commit()
    assert db_session.execute(text(
        "select detail from exp_limitation where run_id = :r and kind = 'arm_unavailable'"),
        {"r": run_id}).scalar() == "recorded"
