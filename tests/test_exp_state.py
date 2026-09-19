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
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from harness.db.models import (FairValue, Game, Intent, MarketGapSnapshot, MetricSample,
                               OrderbookSnapshot, VenueMarket, VenueTrade)
from harness.execution import plan as plan_mod
from harness.experiments.execution_viability import adapter, capture, storage
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

def _manifest(run_id: str, *, cadence_allowance=None) -> Manifest:
    """One run's frozen manifest. Its fields describe the **run**, never the chunk, so every
    chunk of one run freezes the same hash (§1.2)."""
    return Manifest(
        run_id=run_id, code_sha="0" * 40, schema_version="0015", simulator_version="6b",
        pricing_version="5", baseline_settings={"stale_s": 180}, variant_configs=VARIANT_CFG,
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
            wanted_orders=None, policy=None) -> str:
    """Freeze a manifest, open a writer, step the retained instants of `[since, until)` and
    return the run id. A resume continues the **same** run id from its own checkpoint."""
    _seed(session, wanted_orders=wanted_orders)
    run_id = resume_of or str(uuid.uuid4())
    manifest = _manifest(run_id)
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
    adapter.run_chunk(session, runner, _instants(session, since=since, until=until),
                      writer=writer, manifest_hash=manifest_hash,
                      stop_after=kill_after_instants)
    writer.commit()
    return run_id


def run_two_arms(session, s, *, instants) -> str:
    """Arms A and B in **one** process and one transaction, on the same tape (§1.4)."""
    _seed(session)
    run_id = str(uuid.uuid4())
    manifest = _manifest(run_id)
    manifest_hash = manifest.freeze()
    writer = _writer(session, run_id)
    storage.write_run(writer, manifest=manifest, manifest_hash=manifest_hash, created_at=NOW)
    storage.write_arms(writer, [
        {"run_id": run_id, "arm_id": "A", "label": "baseline", "spec": {"policy": "baseline"},
         "spec_hash": "a" * 64},
        {"run_id": run_id, "arm_id": "B", "label": "cadence", "spec": {"per_variant_slots": 1},
         "spec_hash": "b" * 64}])
    # B differs in exactly one parameter, which is what §1.6 says an arm may differ in.
    runners = [_runner(session, s, run_id=run_id, arm="A"),
               _runner(session, s, run_id=run_id, arm="B",
                       policy=plan_mod.HoldingPolicy(name="slots", per_variant_slots=1))]
    for runner in runners:
        adapter.run_chunk(session, runner, list(instants), writer=writer,
                          manifest_hash=manifest_hash)
    writer.commit()
    return run_id


def changed_copy(session, run_id: str) -> Manifest:
    """The same run's manifest with one field altered, so its hash differs (§1.2)."""
    return _manifest(run_id, cadence_allowance=1000)


def _rows(session, run_id, arm_id, table="exp_order"):
    return session.execute(text(
        f"select id, ticker, prob, contracts, status, placed_at from {table} "
        "where run_id = :r and arm_id = :a order by placed_at, id"),
        {"r": run_id, "a": arm_id}).all()


# --- §5's list, one case each ------------------------------------------------------------------

def test_two_arms_in_one_process_cannot_see_each_others_orders(db_session, env_settings):
    run_id = run_two_arms(db_session, env_settings, instants=[NOW, NOW + STEP])
    a, b = _rows(db_session, run_id, "A"), _rows(db_session, run_id, "B")
    assert a and b
    assert {r.id for r in a}.isdisjoint({r.id for r in b}) or len(a) != len(b)
    assert db_session.execute(text(
        "select count(*) from exp_order where run_id = :r and arm_id not in ('A','B')"),
        {"r": run_id}).scalar() == 0


def test_two_arms_cannot_see_each_others_capacity_counter_or_cursor(db_session, env_settings):
    run_id = run_two_arms(db_session, env_settings, instants=[NOW, NOW + STEP])
    counters = db_session.execute(text(
        "select arm_id, (state->>'open_orders_count')::int as open_orders, cursor_event_id "
        "from exp_checkpoint where run_id = :r order by arm_id"), {"r": run_id}).all()
    assert [c.arm_id for c in counters] == ["A", "B"]
    assert counters[0].open_orders != counters[1].open_orders or \
        counters[0].cursor_event_id != counters[1].cursor_event_id
    assert len({id(c) for c in counters}) == 2          # two rows, never one shared counter


def test_three_one_hour_chunks_equal_one_three_hour_chunk_row_for_row(db_session, env_settings):
    whole = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=3))
    chunked = None
    for hour in range(3):
        chunked = run_arm(db_session, env_settings, arm="A", since=NOW + timedelta(hours=hour),
                          until=NOW + timedelta(hours=hour + 1), resume_of=chunked)
    assert _rows(db_session, chunked, "A") == _rows(db_session, whole, "A")


def test_a_resumed_run_after_a_mid_slice_kill_equals_the_uninterrupted_run(db_session,
                                                                          env_settings):
    whole = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=2))
    killed = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=2),
                     kill_after_instants=17)
    resumed = run_arm(db_session, env_settings, arm="A", since=NOW,
                      until=NOW + timedelta(hours=2), resume_of=killed)
    assert _rows(db_session, resumed, "A") == _rows(db_session, whole, "A")


def test_capacity_stays_occupied_while_an_order_rests(db_session, env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(minutes=2))
    resting = db_session.execute(text(
        "select count(*) from exp_order where run_id = :r and status = 'open'"),
        {"r": run_id}).scalar()
    occupied = db_session.execute(text(
        "select (state->>'open_orders_count')::int from exp_checkpoint "
        "where run_id = :r and arm_id = 'A'"), {"r": run_id}).scalar()
    assert occupied == resting and resting > 0


def test_capacity_is_released_on_cancel_on_expiry_and_on_fill(db_session, env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    closed = db_session.execute(text(
        "select status, count(*) from exp_order where run_id = :r and status <> 'open' "
        "group by 1"), {"r": run_id}).all()
    assert {row.status for row in closed} == {"cancelled", "expired", "filled"}
    assert db_session.execute(text(
        "select (state->>'open_orders_count')::int from exp_checkpoint "
        "where run_id = :r and arm_id = 'A'"), {"r": run_id}).scalar() == db_session.execute(
            text("select count(*) from exp_order where run_id = :r and status = 'open'"),
            {"r": run_id}).scalar()


def test_the_hundred_and_fifty_first_simultaneous_order_is_blocked_inside_one_arm(db_session,
                                                                                 env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + STEP,
                     wanted_orders=151)
    assert db_session.execute(text(
        "select count(*) from exp_order where run_id = :r and status = 'open'"),
        {"r": run_id}).scalar() == 150
    assert db_session.execute(text(
        "select count(*) from exp_mismatch where run_id = :r and kind = 'capacity'"),
        {"r": run_id}).scalar() == 1


def test_a_key_is_not_permanently_blocked_after_its_first_placement(db_session, env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    per_key = db_session.execute(text(
        "select ticker, count(*) as n from exp_order where run_id = :r group by 1 "
        "order by n desc limit 1"), {"r": run_id}).one()
    assert per_key.n > 1          # the same market is re-entered after its first order closes


def test_there_is_no_fill_after_expiry(db_session, env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    late = db_session.execute(text(
        "select count(*) from exp_fill f join exp_order o on o.id = f.exp_order_id "
        "and o.run_id = f.run_id and o.arm_id = f.arm_id "
        "where f.run_id = :r and o.expiry is not null and f.filled_at > o.expiry"),
        {"r": run_id}).scalar()
    assert late == 0


def test_a_repriced_order_is_a_new_row_at_the_back_of_the_queue(db_session, env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + timedelta(hours=1))
    pair = db_session.execute(text(
        "select id, prob, queue_ahead_at_place as queue_ahead, placed_at from exp_order "
        "where run_id = :r and ticker = :t order by placed_at limit 2"),
        {"r": run_id, "t": REPRICE_TICKER}).all()
    assert pair[0].id != pair[1].id and pair[0].prob != pair[1].prob
    assert pair[1].queue_ahead >= pair[0].queue_ahead     # the back of the queue, not its place


def test_resume_refuses_a_changed_manifest_and_leaves_the_checkpoint_byte_identical(db_session,
                                                                                   env_settings):
    run_id = run_arm(db_session, env_settings, arm="A", since=NOW, until=NOW + STEP)
    changed_manifest = changed_copy(db_session, run_id)
    _CHECKPOINT = text("select md5(state::text), cursor_event_id, manifest_hash "
                       "from exp_checkpoint where run_id = :r and arm_id = :a")
    before = db_session.execute(_CHECKPOINT, {"r": run_id, "a": "A"}).one()
    with pytest.raises(ManifestMismatch):
        storage.resume(db_session, run_id=run_id, arm_id="A", manifest=changed_manifest)
    after = db_session.execute(_CHECKPOINT, {"r": run_id, "a": "A"}).one()
    assert after == before
