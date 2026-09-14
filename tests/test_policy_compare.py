"""The holding/capacity policy: the baseline stated, the alternatives compared, nothing adopted.

Addendum §1.6. The two directional facts and the byte-equality below are the expected results
the component is judged by, each computed by hand from the fixture's own stamps.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text
from typer.testing import CliRunner

from harness.cli import app
from harness.db.models import (
    Base,
    Fill,
    Game,
    Intent,
    Order,
    MarketGapSnapshot,
    MetricSample,
    Run,
    Signal,
    StrategyVariant,
    VenueMarket,
)
from harness.execution import plan as plan_module
from harness.execution.policy import (ALTERNATIVES, BASELINE, BASELINE_RECORD,
                                      COUNTERFACTUAL_LABEL, HoldingPolicy, compare)
#: `cli_settings` points `harness.cli`'s own engine at the test database; imported rather than
#: copied so the two CLI cases below use the same fixture every other CLI test does.
from tests.test_cli import cli_settings  # noqa: F401  (a pytest fixture, used by name)

runner = CliRunner()

NOW = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
KICKOFF = NOW + timedelta(hours=3)
VARIANT_ID = "a1b2c3d4e5f6"
VARIANT_NAME = "t9_policy_fixture"

#: The fixture's three markets and the age of the fair value each carries at the loop instant.
#: 30 s is inside every registered variant's `stale_s = 180`; 300 s is outside it and inside a
#: 900 s allowance, which is the whole of §1.6's worked example.
FRESH_AGE_S = 30
STALE_AGE_S = 300
MARKETS = {1: FRESH_AGE_S, 2: FRESH_AGE_S, 3: STALE_AGE_S}

CONFIG = {
    "name": VARIANT_NAME, "tier": "primary", "sports": ["nfl"], "sources_allowed": ["pinnacle"],
    "edge_floor": 0.02, "edge_ceiling": 0.25, "disagreement_mult": 1.0, "as_seed": 0.01,
    "price_band": [0.2, 0.8], "min_ttk_min": 20, "max_spread_c": 5, "min_volume_24h": 0,
    "velocity_max_pts": 0.1, "stale_s": 180, "kelly_fraction": 0.25, "bankroll": 3000,
    "per_bet_cap": 0.03, "per_game_cap": 0.05, "daily_cap": 0.15, "max_open": 25,
    "apply_caps": False,
}


# --- the pure slice, built from the live path's own fixtures ---------------------------------


def _slice() -> dict:
    """`plan_actions`' arguments as `tests/test_exec_plan.py` builds them.

    Both chains are exercised, so the byte-equality below covers more than one branch: a held
    open order on market 1, a placeable intent on market 2, and an intent on market 3 whose
    fair value is 300 s old and which the baseline therefore skips `fair_stale`.
    """
    from tests.test_exec_plan import NOW as PLAN_NOW
    from tests.test_exec_plan import S, VARIANTS, intent, market, order

    return {
        "intents": [intent(n=1), intent(vm_id=2, n=2), intent(vm_id=3, n=3)],
        "open_orders": [order()],
        "markets": {1: market(), 2: market(vm_id=2),
                    3: market(vm_id=3,
                              fair_ts=PLAN_NOW - timedelta(seconds=STALE_AGE_S))},
        "state_by_variant": {},
        "variant_cfg": VARIANTS,
        "kill_active": False,
        "now": PLAN_NOW,
        "s": S,
    }


def test_the_baseline_policy_changes_nothing_about_the_live_path():
    """The property the whole design rests on: with no policy passed, and with the baseline
    passed explicitly, `plan_actions` produces the identical action list -- byte-equal under
    `repr`, which includes every field of every action.

    Computed independently of the implementation: `BASELINE` is defined as every alternative
    parameter off, so each `if policy.<x>` branch is dead and the function body executed is the
    one that was there before this task.
    """
    fixture = _slice()          # see above: intents, open orders, markets, config, state
    without = plan_module.plan_actions(**fixture)
    with_baseline = plan_module.plan_actions(**fixture, policy=BASELINE)
    assert repr(with_baseline) == repr(without)
    # Not vacuous: the slice really does exercise both chains. One placement (market 2), one
    # `fair_stale` skip (market 3), and market 1's intent blocked by its own resting order.
    assert [type(action).__name__ for action in without] == ["Place", "Skip"]
    assert without[1].reason == plan_module.FAIR_STALE


# --- the recorded slice ---------------------------------------------------------------------


def _seed(session) -> None:
    """One priced run, one recorded loop instant, three markets, three candidate intents.

    Everything is stamped off `NOW`: the run's pricing clock is `NOW`, the single
    `exec.loop_ms` sample is at `NOW`, and each market's fair value is `MARKETS[vm_id]` seconds
    older than it. Kickoff is three hours out, so the `kickoff` rule is quiet, and the quote is
    0.48/0.52 against a 0.45 target, so `post_only_reject` is quiet too.
    """
    session.add(StrategyVariant(variant_id=VARIANT_ID, name=VARIANT_NAME, tier="primary",
                                config_json=CONFIG, registered_at=NOW, active=True))
    run = Run(started_at=NOW - timedelta(seconds=60), finished_at=NOW, status="ok")
    session.add(run)
    session.flush()
    session.add(MetricSample(ts=NOW, source="exec", name="exec.loop_ms", labels={},
                             value=Decimal("120")))
    for vm_id, age in MARKETS.items():
        session.add(Game(id=vm_id, sport="nfl", home_team_id=vm_id, away_team_id=vm_id + 90,
                         kickoff_utc=KICKOFF, status="scheduled"))
        session.add(VenueMarket(id=vm_id, venue="kalshi", ticker=f"K{vm_id}",
                                event_ticker=f"E{vm_id}", series_ticker="S", game_id=vm_id,
                                market_type="moneyline", match_status="matched",
                                match_key=f"k{vm_id}", first_seen_raw_id=vm_id,
                                last_seen_at=NOW, side_team_id=vm_id))
        fair_ts = NOW - timedelta(seconds=age)
        gap = MarketGapSnapshot(
            run_id=run.id, venue_market_id=vm_id, fair_value_id=None, fair_p=Decimal("0.6000"),
            fair_source="direct", venue_mid=Decimal("0.5000"), best_bid=Decimal("0.4800"),
            best_ask=Decimal("0.5200"), staleness_s=age, stale_allowance_s=0,
            feed_kind="featured", dow=6, hour_ct=12, created_at=fair_ts)
        session.add(gap)
        session.flush()
        signal = Signal(run_id=run.id, variant_id=VARIANT_ID, gap_snapshot_id=gap.id,
                        venue_market_id=vm_id, side="yes", fair_p=Decimal("0.6000"),
                        price_target=Decimal("0.4500"), edge=Decimal("0.0300"),
                        edge_min=Decimal("0.0200"), stake=Decimal("60"), contracts=20,
                        decision="candidate", labels={}, replay=False,
                        created_at=NOW - timedelta(seconds=60))
        session.add(signal)
        session.flush()
        session.add(Intent(id=uuid.UUID(int=vm_id), signal_id=signal.id, variant_id=VARIANT_ID,
                           venue="kalshi", venue_market_id=vm_id, ticker=f"K{vm_id}", side="yes",
                           target_prob=Decimal("0.4500"), target_contracts=Decimal("20"),
                           edge=Decimal("0.0300"), edge_min=Decimal("0.0200"),
                           fair_p=Decimal("0.6000"), game_id=vm_id, kickoff_utc=KICKOFF,
                           stake=Decimal("60"), signal_created_at=NOW - timedelta(seconds=60),
                           created_at=NOW - timedelta(seconds=60), replay=False))
    session.flush()
    # The gap snapshot's `fair_ts` is the fair value's own `created_at`; with no `fair_values`
    # row the loader reads NULL, and a market with no fair timestamp is stale for every policy.
    # So the fair value each snapshot is keyed to is written here, at the age the case needs.
    for vm_id, age in MARKETS.items():
        fair_ts = NOW - timedelta(seconds=age)
        fair_id = session.execute(text(
            "insert into fair_values (run_id, game_id, market_type, fair_p, fair_source, "
            "n_groups, created_at) values (:r, :g, 'moneyline', 0.6, 'direct', 1, :ts) "
            "returning id"), {"r": run.id, "g": vm_id, "ts": fair_ts}).scalar()
        session.execute(text("update market_gap_snapshots set fair_value_id = :f "
                             "where venue_market_id = :v"), {"f": fair_id, "v": vm_id})
    session.flush()


def _by_name(results) -> dict:
    return {row.policy: row for row in results}


def _compare(session, settings, policies):
    return _by_name(compare(session, settings, from_run=1, to_run=10 ** 9,
                            variant=VARIANT_NAME, policies=policies, now=NOW))


def test_a_nine_hundred_second_stale_allowance_places_at_least_as_much(db_session, env_settings):
    """§1.6's expected result, first half: with `stale_allowance_s = 900` the same slice
    produces **at least as many** placements as the baseline.

    Computed by hand from the fixture: one intent's fair value is 300 s old at the loop instant.
    The baseline's test is `age > max(stale_s = 180, gap allowance)`, so 300 > 180 and the
    intent is skipped `fair_stale`; under a 900 s allowance 300 <= 900 and it is placed. Every
    other intent is unaffected, so the placement count rises by exactly one and never falls.
    """
    _seed(db_session)
    rows = _compare(db_session, env_settings,
                    [BASELINE, ALTERNATIVES["stale_allowance_900"]])

    assert rows["baseline"].orders_placed == 2
    assert rows["stale_allowance_900"].orders_placed == 3
    assert rows["stale_allowance_900"].orders_placed >= rows["baseline"].orders_placed
    # The one placement the difference is made of, and the reason it was not one before: the
    # baseline's third intent is an `unreliable_data` exclusion (`fair_stale`, §1.4).
    assert rows["baseline"].exclusions["unreliable_data"] == 1
    assert rows["stale_allowance_900"].exclusions["unreliable_data"] == 0
    assert rows["baseline"].unique_opportunities == 2
    assert rows["stale_allowance_900"].unique_opportunities == 3
    # The coverage denominator is the tape's, not the policy's (round 1 review, M3): one
    # recorded loop instant at which the variant had intents, for every row of the table.
    assert [(row.coverage_completed, row.coverage_scheduled) for row in rows.values()] == \
        [(1, 1), (1, 1)]


def test_the_same_slice_has_a_strictly_larger_mean_fair_age_at_placement(db_session,
                                                                        env_settings):
    """§1.6's expected result, second half, computed by hand from the same fixture: the extra
    placement carries a 300 s fair age against the baseline's placements at 30 s, so the mean
    fair age at placement is strictly larger. Both facts are directional and neither is a
    performance claim."""
    _seed(db_session)
    rows = _compare(db_session, env_settings,
                    [BASELINE, ALTERNATIVES["stale_allowance_900"]])

    # (30 + 30) / 2 = 30; (30 + 30 + 300) / 3 = 120.
    assert rows["baseline"].mean_fair_age_s == 30.0
    assert rows["stale_allowance_900"].mean_fair_age_s == 120.0
    assert rows["stale_allowance_900"].mean_fair_age_s > rows["baseline"].mean_fair_age_s


def test_every_alternative_of_decision_4_is_registered_and_none_is_adopted():
    """Decision 4 names six alternatives; D7 adopts none of them. The baseline is the declared
    policy and the six are comparison inputs."""
    assert set(ALTERNATIVES) == {"stale_allowance_900", "rest_to_expiry", "per_variant_slots",
                                 "fillability_admission", "join_the_bid", "near_kickoff_only"}
    assert BASELINE.name == "baseline"
    assert BASELINE == HoldingPolicy()
    for policy in ALTERNATIVES.values():
        assert policy != BASELINE


def test_every_comparison_output_is_labelled_counterfactual(db_session, env_settings):
    """M6: the caption and every row carry the label, so no number produced under a
    non-registered parameter can be read as a registered id's performance."""
    assert "counterfactual" in COUNTERFACTUAL_LABEL.lower()
    assert "exploratory" in COUNTERFACTUAL_LABEL.lower()

    from harness.execution.policy import render

    _seed(db_session)
    results = compare(db_session, env_settings, from_run=1, to_run=10 ** 9,
                      variant=VARIANT_NAME, policies=[BASELINE, ALTERNATIVES["join_the_bid"]],
                      now=NOW)
    assert [row.label for row in results] == [COUNTERFACTUAL_LABEL] * 2
    table = render(results)
    # The caption, and the label again on each of the two rows: three occurrences, so dropping
    # either the caption or a row's label fails here.
    assert table.count(COUNTERFACTUAL_LABEL) == 3
    assert table.startswith("Holding/capacity policy comparison -- ")
    assert "adopts nothing (D7)" in table


def test_the_baseline_record_states_the_numbers_the_files_carry(env_settings):
    """§1.6(a): the baseline is read from `Settings` and the registered YAMLs, never edited and
    never recalled. Each number is asserted against its own source here, so a settings change
    that moved one would fail this rather than silently re-baselining the comparison."""
    assert "180" in BASELINE_RECORD          # stale_s
    assert str(env_settings.exec_max_open_orders) in BASELINE_RECORD
    assert str(env_settings.exec_intent_ttl_s) in BASELINE_RECORD
    assert str(env_settings.exec_period_s) in BASELINE_RECORD
    assert str(env_settings.exec_kickoff_cutoff_min) in BASELINE_RECORD
    # Each of those read off its own source rather than out of this file: `stale_s`,
    # `max_open` and `min_ttk_min` from the registered YAMLs (all seven agree today), the
    # rest from `Settings`, and the participation windows from `cadence.interval_for` itself.
    from harness.execution.policy import _registered_variant_values

    assert set(_registered_variant_values("stale_s")) == {180}
    assert f"stale_s = 180 in all {len(_registered_variant_values('stale_s'))}" in BASELINE_RECORD
    assert str(env_settings.ws_stale_s) in BASELINE_RECORD
    assert str(env_settings.exec_book_max_age_s) in BASELINE_RECORD
    assert "adopts nothing" in BASELINE_RECORD


def test_the_comparison_never_writes_a_row(db_session, env_settings):
    """The harness is a read of recorded tape and a computation over it. It writes no order, no
    intent, no signal and no metric: a counterfactual that left rows behind would be
    indistinguishable from the record it is a counterfactual of.
    """
    _seed(db_session)

    def counts() -> dict:
        return {table.name: db_session.execute(
            text(f"select count(*) from {table.name}")).scalar()
            for table in Base.metadata.sorted_tables}

    before = counts()
    results = compare(db_session, env_settings, from_run=1, to_run=10 ** 9,
                      variant=VARIANT_NAME,
                      policies=[BASELINE, *ALTERNATIVES.values()], now=NOW)
    after = counts()

    assert after == before
    # Every policy ran: an empty result would satisfy the count assertion vacuously.
    assert len(results) == 1 + len(ALTERNATIVES)
    assert before["orders"] == 0 and after["orders"] == 0
    assert after["order_events"] == 0 and after["metric_samples"] == 1


def test_the_command_refuses_a_policy_name_nobody_registered():
    """An unknown `--policies` name exits 1 with the list, before any database is opened.

    The alternative -- silently dropping the name -- would print a table with fewer rows than
    were asked for, and a comparison missing a policy reads as a policy that placed nothing.
    """
    result = runner.invoke(app, ["policy-compare", "--from-run", "1", "--to-run", "2",
                                 "--variant", VARIANT_NAME, "--policies", "hold_forever"])
    assert result.exit_code == 1


def test_the_command_prints_the_baseline_record_above_the_labelled_table(cli_settings,
                                                                        db_session):
    """`--out -` writes the declared baseline (§1.6(a)) and then the captioned table.

    Committed rather than flushed, because the command builds its own engine and session: what
    it can see is the record, which is the point of reading a recorded slice.
    """
    _seed(db_session)
    db_session.commit()
    result = runner.invoke(app, ["policy-compare", "--from-run", "1", "--to-run", "1000000000",
                                 "--variant", VARIANT_NAME, "--out", "-",
                                 "--policies", "baseline,stale_allowance_900,rest_to_expiry"])
    assert result.exit_code == 0, result.output
    assert "The holding and capacity policy in force" in result.output
    assert COUNTERFACTUAL_LABEL in result.output
    assert "baseline" in result.output and "stale_allowance_900" in result.output
    # Round 1 review, I2/M4: the record is built from the settings the command holds, and a
    # policy this harness cannot move says so above the table as well as on its own row.
    from harness.execution.policy import NOT_EXERCISED_NOTE

    # Three times over: the log line the command emits, the caveat above the table and the
    # marked row itself. Two of those are the printed document; the third is the operator's log.
    assert result.output.count(NOT_EXERCISED_NOTE) >= 2
    assert f"rest_to_expiry: {NOT_EXERCISED_NOTE}" in result.output
    # The run left the record exactly as it found it.
    assert db_session.execute(text("select count(*) from orders")).scalar() == 0
    assert db_session.execute(text("select count(*) from order_events")).scalar() == 0


def test_each_alternative_changes_exactly_the_one_thing_it_names():
    """Every branch threaded into `plan.py` is exercised once, against the baseline's own
    answer on the same slice, so none of them is dead code that the comparison would report as
    "no difference".

    Each expectation is computed by hand from `tests/test_exec_plan.py`'s fixtures: the market
    quotes 0.48 bid / 0.52 ask in the side's own space and the intent targets 0.45, three hours
    before kickoff, with the caps not applied.
    """
    from harness.strategy.run import StrategyState
    from tests.test_exec_plan import NOW as PLAN_NOW
    from tests.test_exec_plan import S, VARIANTS, intent, market, order

    def plan(policy, intents, orders=(), states=None, markets=None):
        return plan_module.plan_actions(list(intents), list(orders),
                                        markets or {1: market()}, states or {}, VARIANTS,
                                        False, PLAN_NOW, S, frozenset(), policy)

    # rest_to_expiry: the baseline cancels a resting order whose fair value went stale; the
    # alternative holds it until its own expiry, so no action is emitted for it at all.
    stale = {1: market(fair_ts=PLAN_NOW - timedelta(seconds=STALE_AGE_S))}
    assert [type(a).__name__ for a in plan(BASELINE, [], [order()], markets=stale)] == ["Cancel"]
    assert plan(ALTERNATIVES["rest_to_expiry"], [], [order()], markets=stale) == []

    # per_variant_slots: 25 of the variant's own orders are already open. The shared pool of
    # 150 still has room, so the baseline places; the per-variant slot count does not.
    full = {"v1": StrategyState(open_orders=25)}
    assert any(isinstance(a, plan_module.Place) for a in plan(BASELINE, [intent()], states=full))
    slots = plan(ALTERNATIVES["per_variant_slots"], [intent()], states=full)
    assert [a.reason for a in slots if isinstance(a, plan_module.Skip)] == \
        [plan_module.EXEC_CAPACITY]

    # fillability_admission: a 0.45 target rests behind a 0.48 bid, so the alternative declines
    # it where the baseline places it.
    assert any(isinstance(a, plan_module.Place) for a in plan(BASELINE, [intent()]))
    admitted = plan(ALTERNATIVES["fillability_admission"], [intent()])
    assert [a.reason for a in admitted] == [plan_module.POST_ONLY_REJECT]

    # join_the_bid: the same intent is placed, at the touch (0.48) rather than at its own 0.45.
    joined = [a for a in plan(ALTERNATIVES["join_the_bid"], [intent()])
              if isinstance(a, plan_module.Place)]
    assert [a.prob for a in joined] == [Decimal("0.4800")]

    # near_kickoff_only: four hours out is outside a 180-minute window, so the alternative skips
    # `kickoff` where the baseline places.
    far = intent(kickoff=PLAN_NOW + timedelta(hours=4))
    assert any(isinstance(a, plan_module.Place) for a in plan(BASELINE, [far]))
    assert [a.reason for a in plan(ALTERNATIVES["near_kickoff_only"], [far])] == \
        [plan_module.KICKOFF]


def _place_order(session, intent_id: uuid.UUID, *, oid: int, resting_s: int, fills: int,
                 dirty_s: int = 0) -> None:
    """One recorded order for `intent_id`, resting `resting_s` seconds with `fills` queue fills.

    Placed at the loop instant and cancelled `resting_s` later, so its clean resting interval is
    `resting_s - dirty_s` whatever the number of fills on it.
    """
    session.add(Order(id=oid, intent_id=intent_id, variant_id=VARIANT_ID, venue="kalshi",
                      mode="paper", client_order_id=f"t9-{oid}", ticker="K1", venue_market_id=1,
                      side="yes", prob=Decimal("0.4500"), contracts=Decimal("20"),
                      status="cancelled", placed_at=NOW,
                      cancelled_at=NOW + timedelta(seconds=resting_s),
                      expiry=KICKOFF - timedelta(minutes=10), dirty_seconds=dirty_s,
                      replay=False))
    session.flush()
    for n in range(fills):
        # `uq_fill_source` keys on the tape row a fill came from, so each print gets its own.
        session.add(Fill(order_id=oid, prob=Decimal("0.4500"), contracts=Decimal("1"),
                         fee=Decimal("0.0100"), filled_at=NOW + timedelta(seconds=n + 1),
                         simulated=True, fill_method="queue_model",
                         source_event_id=oid * 100 + n, replay=False))
    session.flush()


def test_clean_resting_seconds_counts_each_order_once_however_often_it_filled(db_session,
                                                                             env_settings):
    """Round 1 review, C1: the interval is a property of the order, not of its fills.

    Computed by hand: both orders rest 100 s with `dirty_seconds = 0`, so the clean resting
    total is 100 + 100 = 200 and the queue-filled count is 2 -- whether one of them printed
    once and the other three times, which is what the earlier `left join fills` turned into
    100 + 300. An order's execution quality must not be ranked by how fragmented its fills were.
    """
    _seed(db_session)
    _place_order(db_session, uuid.UUID(int=1), oid=1, resting_s=100, fills=1)
    _place_order(db_session, uuid.UUID(int=2), oid=2, resting_s=100, fills=3)

    rows = _compare(db_session, env_settings, [BASELINE])

    assert rows["baseline"].orders_placed == 2      # markets 1 and 2, as above
    assert rows["baseline"].queue_filled_orders == 2
    assert rows["baseline"].clean_resting_seconds == 200


def test_dirty_seconds_come_off_the_resting_interval_once(db_session, env_settings):
    """The other half of the same sum: 100 s resting with 40 s of dirty book is 60 clean
    seconds, counted once for an order that filled three times."""
    _seed(db_session)
    _place_order(db_session, uuid.UUID(int=1), oid=1, resting_s=100, fills=3, dirty_s=40)

    rows = _compare(db_session, env_settings, [BASELINE])

    assert rows["baseline"].queue_filled_orders == 1
    assert rows["baseline"].clean_resting_seconds == 60


def test_the_kill_switch_outranks_the_rest_to_expiry_alternative():
    """Round 1 review, I1: no policy parameter may disable the operator's emergency stop.

    `rest_to_expiry` holds an order through `fair_stale`; it does not hold one through the kill
    switch, or through a market whose identity the matcher has withdrawn. Computed from the
    chain's own order: expiry, kill switch and `unmatched` outrank the holding preference and
    everything below it does not.
    """
    from tests.test_exec_plan import NOW as PLAN_NOW
    from tests.test_exec_plan import S, VARIANTS, market, order

    def plan(policy, kill=False, markets=None):
        return plan_module.plan_actions([], [order()], markets or {1: market()}, {}, VARIANTS,
                                        kill, PLAN_NOW, S, frozenset(), policy)

    resting = ALTERNATIVES["rest_to_expiry"]
    killed = plan(resting, kill=True)
    assert [type(a).__name__ for a in killed] == ["Cancel"]
    assert killed[0].reason == plan_module.KILL_SWITCH
    # ... and the baseline's own answer to the same slice is the same cancel.
    assert plan(BASELINE, kill=True)[0].reason == plan_module.KILL_SWITCH
    # A market the matcher withdrew still cancels, too.
    unmatched = plan(resting, markets={1: market(matched=False)})
    assert unmatched[0].reason == plan_module.UNMATCHED
    # And the rule it does hold: a stale fair value.
    stale = {1: market(fair_ts=PLAN_NOW - timedelta(seconds=STALE_AGE_S))}
    assert plan(resting, markets=stale) == []
    assert plan(BASELINE, markets=stale)[0].reason == plan_module.FAIR_STALE


def test_the_two_alternatives_this_harness_cannot_move_say_so_on_their_own_rows(db_session,
                                                                               env_settings):
    """Round 1 review, I2: `compare` passes no open orders and no exposure state, so
    `rest_to_expiry` and `per_variant_slots` cannot change a number here. A reader must not be
    able to take an identical row for a finding, so the row says which it is."""
    from harness.execution.policy import NOT_EXERCISED, NOT_EXERCISED_NOTE, render

    assert NOT_EXERCISED == {"rest_to_expiry", "per_variant_slots"}
    _seed(db_session)
    results = compare(db_session, env_settings, from_run=1, to_run=10 ** 9,
                      variant=VARIANT_NAME,
                      policies=[BASELINE, ALTERNATIVES["rest_to_expiry"],
                                ALTERNATIVES["per_variant_slots"]], now=NOW)
    # Identical rows, which is exactly why they carry the note.
    assert {(row.orders_placed, row.unique_opportunities) for row in results} == {(2, 2)}
    table = render(results)
    assert table.count(NOT_EXERCISED_NOTE) == 2
    assert "baseline: not exercised" not in table


def test_the_tape_is_read_once_per_instant_not_once_per_policy(db_session, env_settings,
                                                               monkeypatch):
    """Round 1 review, I3: the reads depend on the instant, so the instant is the outer loop.

    Counted rather than argued: the fixture has one recorded loop instant, and seven policies
    are compared over it. One `load_intents` and one `market_rows` call is the whole cost; the
    per-policy loop that was here before made seven of each, which on a game-day slice is
    ~40,000 "newest as of" reads instead of ~5,760.
    """
    from harness.execution import store

    calls = {"intents": 0, "markets": 0}
    real_intents, real_markets = store.load_intents, store.market_rows

    def count_intents(*a, **k):
        calls["intents"] += 1
        return real_intents(*a, **k)

    def count_markets(*a, **k):
        calls["markets"] += 1
        return real_markets(*a, **k)

    monkeypatch.setattr(store, "load_intents", count_intents)
    monkeypatch.setattr(store, "market_rows", count_markets)

    _seed(db_session)
    results = compare(db_session, env_settings, from_run=1, to_run=10 ** 9,
                      variant=VARIANT_NAME, policies=[BASELINE, *ALTERNATIVES.values()],
                      now=NOW)

    assert len(results) == 7
    assert calls == {"intents": 1, "markets": 1}
