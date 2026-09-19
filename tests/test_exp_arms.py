"""§1.6: what arm B's allowance is in each regime, and what B is not.

Every expectation is written from §1.6(b)'s regime table and from the fixture's own stamps,
never read back from the code under test (6B's I-13 rule). Two fixture conventions the cases
below depend on, both stated once here:

* **The fair row is taped one second before its nominal instant.** `MarketNow.fair_age_s`
  floors to whole seconds and F36's rule is strictly `age > allowance`, so a row stamped
  exactly at 12:00:00 is 220 s old at 12:03:40 and survives that step by a hair. Stamping it
  at 11:59:59 puts the hand-computed deadlines -- 12:03:40 for A's 220 s and 12:16:40 for B's
  1,000 s -- on the nominal instants the addendum names.
* **The feed is alive exactly where the case says it is.** With no `fair_move` the fixture
  tapes one fair row and nothing after it: that is §1.6's starvation picture, where A's
  allowance expires long before the next observation. With a `fair_move` the feed has
  delivered new information, so it keeps delivering on its own schedule (`interval_s`) for the
  rest of the window; a case about repricing is not also a case about starvation.
"""
import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text

from harness.db.models import (FairValue, Game, Intent, MarketGapSnapshot, Order,
                               OrderbookSnapshot, VenueMarket, VenueTrade)
from harness.execution import policy as policy_mod
from harness.experiments.execution_viability import arms
from harness.experiments.execution_viability.adapter import ArmRunner
from harness.feeds.espn import Kickoff
from tests.test_exec_plan import market as exec_market       # the executor's own MarketNow builder

CT = ZoneInfo("America/Chicago")
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)      # Wednesday, outside any window
TICK_BUDGET_S = 100
EXEC_PERIOD_S = 15
STALE_S = 180                        # the variant's `cfg["stale_s"]`, the floor `_fair_stale` keeps


def _kickoff(offset):
    return Kickoff(sport="nfl", espn_event_id="e1", kickoff_utc=NOW + offset, home="DET",
                   away="BAL", status="pre")


def _allowance(interval_fn, *, kickoffs=(), window_start=NOW - timedelta(hours=12)):
    return arms.cadence_allowance_for("nfl", lambda at: list(kickoffs), CT,
                                      tick_budget_s=TICK_BUDGET_S, exec_period_s=EXEC_PERIOD_S,
                                      window_start=window_start, interval_fn=interval_fn)


# --- the two fixture helpers this file runs its arm pairs through -------------------------------

RUN = "0198e2b0-0000-7000-8000-000000000002"
TICKER = "KXNFLGAME-26SEP20DETBAL-DET"
VARIANT = "v_base"
#: The exec slice of a registered variant config: `sharp_direct`'s numbers, `stale_s = 180`.
VARIANT_CFG = {VARIANT: {"stale_s": 180, "apply_caps": False, "bankroll": 3000,
                         "per_bet_cap": 0.03, "per_game_cap": 0.05, "daily_cap": 0.15,
                         "max_open": 25}}
#: A's allowance on a featured order: the **pricing-time** value `harness/pricing/fair.py`
#: produced (`FEATURED_CADENCE_S = 120` plus `Settings.tick_budget_s = 100`), stored on the gap
#: snapshot and never recomputed at the loop clock (§1.6a).
A_ALLOWANCE_S = 220
STEP = timedelta(seconds=20)
#: The resting order's own numbers, frozen at placement: 0.48 against a 0.5100 fair, an
#: adverse-selection seed of zero and a 1-point edge floor, so a 1.5-point adverse fair move
#: leaves `0.4950 - 0.4800 - 0.0044 = 0.0106` of edge (above `edge floor / 2 = 0.0050`) and is
#: therefore a **reprice**, while a 10-point one is an edge-decay cancel.
ORDER_PROB = Decimal("0.4800")
ORDER_CONTRACTS = Decimal("20")
FAIR_P = Decimal("0.5100")


def _seed_market(session, *, kickoff):
    game = Game(sport="nfl", home_team_id=1, away_team_id=2, kickoff_utc=kickoff,
                espn_event_id="4017", status="scheduled")
    session.add(game)
    session.flush()
    market = VenueMarket(venue="kalshi", ticker=TICKER, event_ticker="KXNFLGAME",
                         series_ticker="KXNFLGAME", game_id=game.id, market_type="moneyline",
                         match_status="matched", match_key="k1", first_seen_raw_id=1,
                         last_seen_at=kickoff)
    session.add(market)
    session.flush()
    return market


def _priced(session, market, *, at, fair_p=FAIR_P, allowance_s=A_ALLOWANCE_S):
    """One fair value and the gap snapshot keyed to it: what `market_rows(at=)` reads.

    `at` is both the fair's `created_at` (the executor's `fair_ts`) and the snapshot's, so the
    row becomes visible at the instant it was taped.
    """
    run = int(at.timestamp())
    fair = FairValue(run_id=run, game_id=market.game_id, market_type="moneyline",
                     outcome_team_id=1, fair_p=fair_p, fair_source="direct", created_at=at)
    session.add(fair)
    session.flush()
    session.add(MarketGapSnapshot(
        run_id=run, venue_market_id=market.id, fair_value_id=fair.id, fair_source="direct",
        fair_p=fair_p, venue_mid=Decimal("0.5000"), best_bid=Decimal("0.4700"),
        best_ask=Decimal("0.5200"), staleness_s=10, stale_allowance_s=allowance_s,
        dow=at.weekday(), hour_ct=at.hour, created_at=at))
    session.flush()


def _books(session, market, *, start, end, every=timedelta(seconds=60)):
    """A REST ladder every 60 s: `exec_book_max_age_s` is 120, and a book older than that
    reads dirty, which holds an order **before** the staleness rule is ever reached. The book
    is alive in every case here; what goes quiet is the fair value."""
    at, raw_id = start, 1
    while at <= end:
        session.add(OrderbookSnapshot(raw_id=raw_id, venue_market_id=market.id, fetched_at=at,
                                      yes_bids=[["0.4700", "100"]], no_bids=[["0.4800", "100"]]))
        at, raw_id = at + every, raw_id + 1
    session.flush()


def _intent(session, market, *, created_at, target_prob, kickoff, signal_id):
    intent = Intent(signal_id=signal_id, variant_id=VARIANT, venue="kalshi",
                    venue_market_id=market.id, ticker=market.ticker, side="yes",
                    target_prob=target_prob, target_contracts=ORDER_CONTRACTS,
                    edge=Decimal("0.0300"), edge_min=Decimal("0.0100"), fair_p=FAIR_P,
                    game_id=market.game_id, kickoff_utc=kickoff, stake=Decimal("9.60"),
                    signal_created_at=created_at, created_at=created_at, replay=False)
    session.add(intent)
    session.flush()
    return intent


def _resting_order(session, market, *, placed_at, kickoff):
    """The order both arms start the slice holding (§1.3e): one recorded row, adopted twice.

    Its intent is taped two hours before the window, so it is outside `exec_intent_ttl_s` at
    every instant here and no arm is offered a second placement on the same key.
    """
    intent = _intent(session, market, created_at=placed_at - timedelta(hours=2),
                     target_prob=ORDER_PROB, kickoff=kickoff, signal_id=1)
    order = Order(intent_id=intent.id, variant_id=VARIANT, venue="kalshi",
                  client_order_id="prod-1", ticker=market.ticker, venue_market_id=market.id,
                  side="yes", prob=ORDER_PROB, contracts=ORDER_CONTRACTS, status="open",
                  placed_at=placed_at, expiry=kickoff - timedelta(minutes=10),
                  fair_p_at_place=FAIR_P, venue_mid_at_place=Decimal("0.5000"),
                  edge_min_at_place=Decimal("0.0100"), as_at_place=Decimal("0.0000"),
                  queue_ahead_at_place=Decimal("0"), game_id=market.game_id, sport="nfl",
                  kickoff_utc=kickoff, match_key="k1", replay=False)
    session.add(order)
    session.flush()
    return order


def _print(session, *, ts, trade_id, count=ORDER_CONTRACTS, yes_price=ORDER_PROB):
    """A public print that lifted resting YES size: the taker is on the other side."""
    session.add(VenueTrade(venue="kalshi", trade_id=trade_id, ticker=TICKER, ts=ts,
                           yes_price=yes_price, count=count, taker_side="no",
                           taker_outcome_side="no", source="ws"))
    session.flush()


def _runner(settings, *, arm_id, policy, allowance=None):
    return ArmRunner(run_id=RUN, arm_id=arm_id, policy=policy, variant_cfg=VARIANT_CFG,
                     exec_settings=settings, cadence_allowance=allowance)


def _merged(results):
    """One arm's records in the order they happened: each step's actions then its fills."""
    out = []
    for result in results:
        out.extend(result.actions)
        out.extend(result.fills)
    return out


def run_arm_pair(session, settings, *, fair_ts, print_at, interval_s, fair_move=None,
                 arm="A", run_id=RUN, late_fair=None):
    """Seed one market's tape and step the same instants twice, once per arm.

    Returns `(a_actions, b_actions, a_fills, b_fills)`, where `a_*` is the arm named by `arm`
    (`"A"` the baseline, or any name in `policy.ALTERNATIVES`) and `b_*` is §1.6's cadence-aware
    arm B with its allowance bound to `interval_s + tick_budget_s`. Both arms adopt the same
    recorded resting order, read the same rows and differ only in the policy argument.
    """
    kickoff = fair_ts + timedelta(hours=6)
    end = print_at + STEP
    # One tape per test: a case that runs two arms against the same fixture (rest-to-expiry
    # against B) seeds it once and steps it twice, so the two arms are compared over rows that
    # are identical by construction rather than by two seedings agreeing.
    market = session.scalars(select(VenueMarket).filter_by(ticker=TICKER)).first()
    if market is not None:
        order = session.scalars(select(Order).filter_by(client_order_id="prod-1")).one()
        return _step_pair(session, settings, order=order, fair_ts=fair_ts, end=end,
                          interval_s=interval_s, arm=arm, run_id=run_id)
    market = _seed_market(session, kickoff=kickoff)
    _priced(session, market, at=fair_ts - timedelta(seconds=1))
    if fair_move is not None:
        move_at, move = fair_move
        at = move_at - timedelta(seconds=1)
        while at <= end:
            _priced(session, market, at=at, fair_p=FAIR_P - move)
            at += timedelta(seconds=interval_s)
        # New information is a new candidate too: the strategy re-prices its target by the same
        # move, which is what gives the arms something to reprice **to**.
        _intent(session, market, created_at=move_at, target_prob=ORDER_PROB - move,
                kickoff=kickoff, signal_id=2)
    if late_fair is not None:
        value_at, available_at = late_fair
        # §1.3(b): the row is taped at `available_at`; a decision at an earlier instant cannot
        # see it. The tape keeps one stamp for a fair value, so `value_at` is recorded only in
        # the fixture's own intent (the event the late row describes).
        assert value_at <= available_at
        _priced(session, market, at=available_at)
    _books(session, market, start=fair_ts - timedelta(seconds=10), end=end)
    order = _resting_order(session, market, placed_at=fair_ts, kickoff=kickoff)
    _print(session, ts=print_at, trade_id="t1")
    session.commit()
    return _step_pair(session, settings, order=order, fair_ts=fair_ts, end=end,
                      interval_s=interval_s, arm=arm, run_id=run_id)


def _step_pair(session, settings, *, order, fair_ts, end, interval_s, arm, run_id):
    """Step the same instants twice: the named arm, then §1.6's arm B."""
    instants = []
    at = fair_ts
    while at <= end:
        instants.append(at)
        at += STEP

    allowance = arms.cadence_allowance_for(
        "nfl", lambda at: [], settings.tz_local, tick_budget_s=settings.tick_budget_s,
        exec_period_s=settings.exec_period_s, window_start=fair_ts - timedelta(hours=1),
        interval_fn=lambda sport, at, kickoffs, tz: interval_s)
    first_policy = None if arm == "A" else policy_mod.ALTERNATIVES[arm]
    runner_a = _runner(settings, arm_id="A", policy=first_policy)
    runner_b = _runner(settings, arm_id="B", policy=arms.ARMS["B"].policy, allowance=allowance)
    runner_a.run_id = runner_b.run_id = run_id
    out = []
    for runner in (runner_a, runner_b):
        runner.adopt(order, queue_ahead=Decimal("0"))
        out.append(_merged(runner.run(session, instants)))
    return (out[0], out[1],
            [row for row in out[0] if row["kind"] == "fill"],
            [row for row in out[1] if row["kind"] == "fill"])


def seed_c_observations(session, settings, *, run_id=RUN):
    """Arm C's own prospective observations, in the same run (§1.6e).

    `exp_observation` carries no `arm_id` column (§2, ruling I5) -- it is arm C's table and
    nothing else writes it -- so C's rows are identified by the run and by `source`.
    """
    session.execute(text(
        "insert into exp_observation (run_id, observed_at, available_at, sport, "
        "venue_market_id, fair_p, source, credits, status) "
        "values (:run, :at, :at, 'nfl', null, 0.5300, 'exp_observer', 3, 'ok')"),
        {"run": run_id, "at": NOW + timedelta(minutes=1)})
    session.commit()
    return run_id


# --- §1.6(b)'s regime table --------------------------------------------------------------------


@pytest.mark.parametrize("label,interval,expected", [
    ("nfl_burst", 20, 180),        # the cfg["stale_s"] floor wins; B is TIGHTER than A's 220
    ("game_window", 120, 220),     # identical to A
    ("weekend_offwindow", 300, 400),
    ("weekday_offwindow", 900, 1000),
])
def test_bs_allowance_follows_the_regime_table(label, interval, expected):
    allowance = _allowance(lambda sport, at, kickoffs, tz: interval)
    market = exec_market(fair_ts=NOW - timedelta(seconds=30))
    # `_fair_stale` takes `max(cfg["stale_s"], allowance)`, which is where the burst row's floor
    # comes from: 20 + 100 = 120 seconds, raised to the variant's 180.
    assert max(STALE_S, allowance(market)) == expected


def test_overnight_walks_back_to_the_last_finite_interval():
    # I4's closed form: the latest `exec_period_s` step at or before fair_ts whose `interval_for`
    # is finite. Here 900 at 01:59:45 and None from 02:00:00 onwards -> 900 + 100 = 1,000.
    anchor = datetime(2026, 9, 16, 1, 59, 45, tzinfo=timezone.utc)

    def interval_fn(sport, at, kickoffs, tz):
        return 900 if at <= anchor else None

    allowance = _allowance(interval_fn, window_start=anchor - timedelta(hours=1))
    market = exec_market(fair_ts=datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc))
    assert allowance(market) == 1000


def test_an_unanchored_overnight_row_takes_one_thousand_seconds_and_is_labelled():
    allowance, label = arms.cadence_allowance_with_label(
        "nfl", lambda at: [], CT, tick_budget_s=TICK_BUDGET_S, exec_period_s=EXEC_PERIOD_S,
        window_start=NOW - timedelta(hours=2),
        interval_fn=lambda sport, at, kickoffs, tz: None)
    market = exec_market(fair_ts=NOW)
    assert allowance(market) == arms.OVERNIGHT_UNANCHORED_S == 1000
    assert label(market) == "overnight_unanchored"


def test_a_missed_fetch_does_not_extend_its_own_deadline():
    # The allowance is a function of the *scheduled* interval at fair_ts, never of the elapsed
    # gap to the next actual row (§1.6d): the same fair row gets the same number however late
    # the following fetch arrives.
    seen = []

    def interval_fn(sport, at, kickoffs, tz):
        seen.append(at)
        return 300

    allowance = _allowance(interval_fn)
    fair_ts = NOW - timedelta(seconds=30)
    early = exec_market(fair_ts=fair_ts)
    assert allowance(early) == 400
    assert allowance(early) == 400            # evaluated 20 minutes later: the same number
    # Asked at fair_ts, never at `now`, and asked **once**: the resolver is memoised on the
    # fair row's own instant (fix round 1, Important 4), so the second evaluation reuses it.
    assert seen == [fair_ts]


def test_the_as_of_kickoffs_are_read_at_the_fair_rows_own_instant():
    # I3: `interval_for` is handed the kickoff list reconstructed **as of** the instant it is
    # evaluated at, which is the fair row's creation instant -- never today's schedule.
    asked = []

    def kickoffs_at(at):
        asked.append(at)
        return [_kickoff(timedelta(hours=3))]

    allowance = arms.cadence_allowance_for(
        "nfl", kickoffs_at, CT, tick_budget_s=TICK_BUDGET_S, exec_period_s=EXEC_PERIOD_S,
        window_start=NOW - timedelta(hours=2),
        interval_fn=lambda sport, at, kickoffs, tz: 120 if kickoffs else 900)
    fair_ts = NOW - timedelta(seconds=45)
    assert allowance(exec_market(fair_ts=fair_ts)) == 220
    assert asked == [fair_ts]


# --- §1.6's two required fixtures, and §1.6(c)'s four distinctions ------------------------------


def test_b_receives_a_print_that_as_stale_cancel_blocked_for_a(db_session, env_settings):
    # §1.6's expected result, computed by hand: a Tuesday 12:00:00 fair, off-window; A cancels at
    # 12:03:40 (220 s) and records no fill; the hitting print lands at 12:09:00; B holds to
    # 12:16:40 (1,000 s) and receives it.
    a_actions, b_actions, a_fills, b_fills = run_arm_pair(
        db_session, env_settings, fair_ts=NOW, print_at=NOW + timedelta(minutes=9),
        interval_s=900)
    assert [(a["kind"], a["instant"]) for a in a_actions][-1] == (
        "cancel", NOW + timedelta(seconds=220))
    assert a_fills == []
    assert b_fills[0]["filled_at"] == NOW + timedelta(minutes=9)
    assert [b["kind"] for b in b_actions][-1] == "fill"


def test_new_information_reprices_b_exactly_as_it_reprices_a(db_session, env_settings):
    # A 1.5-point fair move at 12:05:00 reprices both arms, so the apparent counterfactual gain
    # does not survive it (§1.6's second required fixture).
    a_actions, b_actions, a_fills, b_fills = run_arm_pair(
        db_session, env_settings, fair_ts=NOW, print_at=NOW + timedelta(minutes=9),
        interval_s=900, fair_move=(NOW + timedelta(minutes=5), Decimal("0.0150")))
    repriced = [x["instant"] for x in b_actions if x["kind"] == "place"]
    assert NOW + timedelta(minutes=5) in repriced
    assert [x["kind"] for x in a_actions if x["kind"] == "place"] == \
        [x["kind"] for x in b_actions if x["kind"] == "place"]
    assert b_fills == []          # the 12:09 print no longer hits B's new price either


def test_rest_to_expiry_produces_a_different_action_list_from_b(db_session, env_settings):
    # §1.6(c) distinction 1: B is not "rest to expiry". An arm that never cancels keeps the
    # order past 12:16:40; B cancels there.
    _, b_actions, _, _ = run_arm_pair(db_session, env_settings, fair_ts=NOW,
                                      print_at=NOW + timedelta(minutes=30), interval_s=900)
    rest_actions, _, _, _ = run_arm_pair(db_session, env_settings, fair_ts=NOW,
                                         print_at=NOW + timedelta(minutes=30), interval_s=900,
                                         arm="rest_to_expiry")
    assert [x["kind"] for x in b_actions] != [x["kind"] for x in rest_actions]
    assert ("cancel", NOW + timedelta(seconds=1000)) in [(x["kind"], x["instant"])
                                                         for x in b_actions]


def test_b_does_not_widen_the_strategys_own_not_stale_filter():
    # §1.6(c) distinction 2: the signal-side filter is untouched; only `_fair_stale` reads the
    # allowance, and `arms.py` names no other consumer.
    source = Path(arms.__file__).read_text()
    assert "candidate_signals" not in source and "edge_min" not in source
    assert arms.ARMS["B"].policy.cadence_allowance is not None
    assert arms.ARMS["B"].policy.stale_allowance_s is None


def test_b_does_not_suppress_repricing_edge_decay_or_the_venue_move_check(db_session,
                                                                          env_settings):
    # §1.6(c) distinctions 3 and 4: every other cancel reason still fires under B.
    _, b_actions, _, _ = run_arm_pair(db_session, env_settings, fair_ts=NOW,
                                      print_at=NOW + timedelta(minutes=30), interval_s=900,
                                      fair_move=(NOW + timedelta(minutes=2), Decimal("0.1000")))
    reasons = [x.get("reason") for x in b_actions if x["kind"] == "cancel"]
    assert "edge_decay" in reasons
    assert "fair_stale" not in reasons        # 120 s is well inside B's 1,000 s allowance


def test_a_and_b_cannot_read_c_only_observations(db_session, env_settings):
    run_id = seed_c_observations(db_session, env_settings)     # arm C rows, same run
    a_actions, b_actions, _, _ = run_arm_pair(db_session, env_settings, fair_ts=NOW,
                                              print_at=NOW + timedelta(minutes=9),
                                              interval_s=900, run_id=run_id)
    for action in a_actions + b_actions:
        assert action.get("source") != "exp_observation"
    assert db_session.execute(text(
        "select count(*) from exp_observation where run_id = :r and source = 'exp_observer'"),
        {"r": run_id}).scalar() > 0


def test_no_row_whose_availability_stamp_is_after_the_decision_is_visible(db_session,
                                                                          env_settings):
    # The same guard T2 asserts for the adapter, asserted here for the arm: a fair row taped
    # after the instant cannot change an arm's decision at it.
    a_actions, b_actions, _, _ = run_arm_pair(db_session, env_settings, fair_ts=NOW,
                                              print_at=NOW + timedelta(minutes=9),
                                              interval_s=900,
                                              late_fair=(NOW, NOW + timedelta(minutes=3)))
    assert all(x["instant"] <= NOW + timedelta(minutes=3) or x["kind"] != "place"
               for x in a_actions + b_actions)


def test_the_arm_spec_records_every_distinction_and_hashes_them():
    spec = arms.ARMS["B"]
    assert len(spec.notes) == 4                     # §1.6(c)'s four distinctions
    entry = spec.as_manifest_entry()
    assert entry["notes"] == list(spec.notes)
    # `dataclasses.replace` rather than `ArmSpec(**spec.__dict__)`: `ArmSpec` is a frozen
    # **slots** dataclass (§1.6's own shape) and has no instance `__dict__`. The construction is
    # the same one -- this spec with three notes instead of four.
    changed = dataclasses.replace(spec, notes=spec.notes[:3])
    assert changed.spec_hash() != spec.spec_hash()  # the notes are inside the hash, not beside it


def test_the_resolver_is_memoised_on_fair_ts_and_never_reasks_the_same_instant():
    # Important 4: `_fair_stale` asks this question for every resting order at every retained
    # instant, and the real binding reads the as-of kickoff snapshots per probe. One
    # reconstruction per distinct fair row, not per evaluation.
    seen = []

    def interval_fn(sport, at, kickoffs, tz):
        seen.append(at)
        return 120

    allowance = _allowance(interval_fn)
    first, second = NOW - timedelta(seconds=30), NOW - timedelta(seconds=90)
    for _ in range(25):
        assert allowance(exec_market(fair_ts=first)) == 220
    assert seen == [first]                       # twenty-five evaluations, one resolution
    assert allowance(exec_market(fair_ts=second)) == 220
    assert seen == [first, second]               # a different fair row is its own entry


def test_the_walk_back_starts_one_step_before_fair_ts_and_is_shared_with_the_label():
    # The instant `fair_ts` itself was just asked and answered None; asking it again is a
    # duplicate reconstruction, not a probe (Important 4). With `exec_period_s` = 15 the first
    # probe is fair_ts - 15 s, and the two views share one cache.
    seen = []

    def interval_fn(sport, at, kickoffs, tz):
        seen.append(at)
        return None if at > NOW - timedelta(seconds=45) else 900

    allowance, label = arms.cadence_allowance_with_label(
        "nfl", lambda at: [], CT, tick_budget_s=TICK_BUDGET_S, exec_period_s=EXEC_PERIOD_S,
        window_start=NOW - timedelta(hours=2), interval_fn=interval_fn)
    market = exec_market(fair_ts=NOW)
    assert allowance(market) == 1000             # 900 + 100, from the last finite step
    assert label(market) == arms.WALKED_BACK     # the label costs no second resolution
    assert seen == [NOW, NOW - timedelta(seconds=EXEC_PERIOD_S),
                    NOW - timedelta(seconds=2 * EXEC_PERIOD_S),
                    NOW - timedelta(seconds=3 * EXEC_PERIOD_S)]
