"""§1.11: the decision report - it recommends, and it decides nothing.

`render` refuses before it emits a recommendation while any required section is empty; an
explicitly unavailable arm is a complete input; a negative finding closes the milestone; the
veto component is closed by the dormant, preflighted state; and the two questions §0.14a and
§0.14c hold for the user are carried verbatim, unanswered.
"""
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from harness.experiments.execution_viability import decision, exp_label
from harness.experiments.execution_viability.cli import exp_app
from harness.experiments.execution_viability.decision import EVIDENCE_SECTIONS, MissingEvidence

NOW = datetime(2026, 9, 18, 21, 30, tzinfo=timezone.utc)
runner = CliRunner()


def _complete_evidence(**kw):
    """One of every section §1.11 requires, with arm C named rather than absent."""
    base = dict(
        run_id="0198e2b0-0000-7000-8000-000000000001",
        manifest_hash="a" * 64,
        baseline_proof=("arm A reproduced the recorded slice over 4 800 retained instants; "
                        "6 mismatches, all explained (queue_ahead_unknown x4, book_absent x2)"),
        arm_results=("arm A: 40 orders, 3 filled; arm B: 40 orders, 11 filled, both on the "
                     "same tape, state and resources"),
        arm_c="unavailable: arm C was not measured on this run (§1.6h)",
        book_health=("inactive_confirmed 12, data_loss_confirmed 1, unresolved 3; fresh-outcome "
                     "coverage: 0 mature markout rows"),
        veto_pacing="profile balanced hash 0f1e2d3c4b5a; preflight funded 31 of 44 buckets",
        forecast="baseline_continuation: 0.170 - 0.470 filled orders a day",
        recommendation="insufficient evidence",
        reason="no mature markout outcome exists for either arm's fills",
    )
    base.update(kw)
    return decision.Evidence(**base)


def _without(evidence, section):
    return replace(evidence, **{section: ""})


@pytest.mark.parametrize("missing", EVIDENCE_SECTIONS)
def test_render_raises_when_any_required_section_is_empty(missing):
    evidence = _complete_evidence()
    with pytest.raises(MissingEvidence, match=missing):
        decision.render(_without(evidence, missing), now=NOW)


def test_an_explicitly_unavailable_arm_is_a_complete_input():
    evidence = _complete_evidence(arm_c="unavailable: budget preflight refused, §1.6(h)")
    out = decision.render(evidence, now=NOW)
    assert "arm C: unavailable" in out and "zero invented" in out


def test_a_negative_finding_closes_the_milestone():
    out = decision.render(_complete_evidence(recommendation="stop"), now=NOW)
    assert "positive returns are not a completion requirement" in out.lower()


def test_the_veto_component_is_closed_by_the_dormant_state():
    out = decision.render(_complete_evidence(), now=NOW)
    assert "implemented, preflighted against stored arrivals under unchanged caps, shipped " \
           "dormant" in out
    assert "boundary instant is written at the user's activation" in out


def test_the_report_never_adopts_and_never_activates():
    out = decision.render(_complete_evidence(recommendation="retain"), now=NOW)
    assert "§0.14a" in out and "the user's dated decision" in out
    assert "activated" not in out.lower()


def test_the_recommendation_is_one_of_four():
    assert decision.RECOMMENDATIONS == ("retain", "revise", "stop", "insufficient evidence")


# --- the rest of §1.11's contract --------------------------------------------------------------


def test_an_arm_with_no_result_and_no_reason_is_missing_evidence():
    # §9's permission is for an arm reported unavailable **with its reason**. Silence is not.
    with pytest.raises(MissingEvidence, match="arm_results"):
        decision.render(_complete_evidence(arm_c=""), now=NOW)


def test_the_six_sections_are_printed_in_the_order_1_11_states_them():
    out = decision.render(_complete_evidence(), now=NOW)
    positions = [out.index(decision.SECTION_TITLES[name]) for name in EVIDENCE_SECTIONS]
    assert positions == sorted(positions)


def test_the_milestone_is_done_on_the_evidence_and_never_on_the_cli():
    out = decision.render(_complete_evidence(), now=NOW)
    assert "never when its CLI and tests are finished" in out


def test_both_of_the_user_s_questions_are_carried_verbatim_and_unanswered():
    from harness.experiments.execution_viability import veto_profile

    out = decision.render(_complete_evidence(), now=NOW)
    assert decision.ADOPTION_QUESTION in out
    assert veto_profile.AMENDMENT_QUESTION in out
    assert "unanswered" in out


def test_an_unknown_recommendation_is_refused():
    with pytest.raises(ValueError, match="retain"):
        decision.render(_complete_evidence(recommendation="adopt"), now=NOW)


def test_an_incomplete_adoption_proposal_is_refused_rather_than_rendered():
    # §1.11: *any* production adoption proposal carries exact settings, hash, effective date,
    # rollback and affected measurement populations. Four of five is refused, not printed under
    # a sentence claiming five (fix round 1, Important 3).
    incomplete = ("exact settings: exec_max_open_orders 150 (unchanged); hash abc123; "
                  "effective date 2026-10-01; rollback: unset the setting")
    with pytest.raises(MissingEvidence, match="affected measurement populations"):
        decision.render(_complete_evidence(adoption_proposal=incomplete), now=NOW)

    complete = incomplete + "; affected measurement populations: H9's decided signals"
    out = decision.render(_complete_evidence(adoption_proposal=complete), now=NOW)
    assert "affected measurement populations: H9's decided signals" in out


def test_no_proposal_is_absent_rather_than_implied():
    out = decision.render(_complete_evidence(), now=NOW)
    assert "adoption proposal: none is made here" in out
    for field in decision.PROPOSAL_FIELDS:
        assert field in out           # the obligation is stated even when nothing is proposed


def test_the_recommendation_rule_is_stated_and_conservative():
    # Immature outcomes cannot produce anything but "insufficient evidence", whatever the arms
    # did: an empty `exp_outcome` is an unavailable outcome, never a zero markout.
    assert decision.recommend(comparable_arms=2, accrual_identified=True, mature_outcomes=0,
                              markout_sign=None, baseline_resolved=True,
                              arm_b_better=True)[0] == "insufficient evidence"
    assert decision.recommend(comparable_arms=1, accrual_identified=True, mature_outcomes=90,
                              markout_sign="positive", baseline_resolved=True,
                              arm_b_better=True)[0] == "insufficient evidence"
    assert decision.recommend(comparable_arms=2, accrual_identified=True, mature_outcomes=90,
                              markout_sign="negative", baseline_resolved=True,
                              arm_b_better=True)[0] == "stop"
    assert decision.recommend(comparable_arms=2, accrual_identified=True, mature_outcomes=90,
                              markout_sign="positive", baseline_resolved=True,
                              arm_b_better=True)[0] == "revise"
    assert decision.recommend(comparable_arms=2, accrual_identified=True, mature_outcomes=90,
                              markout_sign="positive", baseline_resolved=True,
                              arm_b_better=False)[0] == "retain"
    assert all(rec in decision.RECOMMENDATIONS
               for rec, _why in [decision.recommend(
                   comparable_arms=n, accrual_identified=a, mature_outcomes=m,
                   markout_sign=s, baseline_resolved=b, arm_b_better=better)
                   for n in (0, 2) for a in (True, False) for m in (0, 90)
                   for s in (None, "positive", "negative") for b in (True, False)
                   for better in (None, True, False)])


# --- `harness exp decide` ----------------------------------------------------------------------


def test_decide_is_registered_and_takes_a_run_id():
    assert "decide" in runner.invoke(exp_app, ["--help"]).stdout
    result = runner.invoke(exp_app, ["decide", "--help"])
    assert result.exit_code == 0
    rendered = " ".join(result.stdout.split())
    assert "--run-id" in rendered


def test_decide_refuses_a_run_that_was_never_frozen(db_session, env_settings, monkeypatch):
    from contextlib import contextmanager

    from harness.experiments.execution_viability import cli

    @contextmanager
    def reader(_s):
        yield db_session

    monkeypatch.setattr(cli.source, "reader", reader)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)
    result = runner.invoke(exp_app, ["decide", "--run-id",
                                     "0198e2b0-0000-7000-8000-00000000dead"])
    assert result.exit_code == 2
    assert result.stdout.splitlines()[0].strip() == cli.EXP_LABEL


WINDOW_SINCE = "2026-09-01T00:00:00+0000"
WINDOW_UNTIL = "2026-09-08T00:00:00+0000"


def _reader_for(db_session, monkeypatch):
    from contextlib import contextmanager

    from harness.experiments.execution_viability import cli

    @contextmanager
    def reader(_s):
        yield db_session

    monkeypatch.setattr(cli.source, "reader", reader)
    return cli


def _frozen_run(db_session, run_id):
    from harness.db.models import ExpRun

    db_session.add(ExpRun(run_id=run_id, created_at=NOW, manifest_hash="b" * 64,
                          manifest={"clock_mode": "recorded_tape",
                                    "opportunity_definition": {"cadence_in_force": 120}},
                          clock_mode="recorded_tape", status="frozen"))
    db_session.flush()


def test_decide_refuses_when_the_veto_section_carries_no_preflight(db_session, env_settings,
                                                                   monkeypatch):
    """§1.11 counts the preflight as completion evidence, so the sentence may not stand alone.

    Without `--veto-profile` and its window the section is empty, `decision.render` refuses and
    the command reports the refusal rather than printing a report (fix round 1, Important 2).
    """
    cli = _reader_for(db_session, monkeypatch)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)
    run_id = "0198e2b0-0000-7000-8000-00000000beef"
    _frozen_run(db_session, run_id)
    result = runner.invoke(exp_app, ["decide", "--run-id", run_id])
    assert result.exit_code == 1
    assert result.stdout.splitlines()[0].strip() == cli.EXP_LABEL
    assert "decision report refused: veto_pacing is empty" in result.stdout
    assert "--veto-profile, --since and --until" in result.stdout
    assert "## 6. Recommendation" not in result.stdout      # nothing was rendered


def test_decide_renders_the_six_sections_for_a_frozen_run(db_session, env_settings, monkeypatch):
    """The command renders §1.11's report from the record, with nothing hand-written.

    The run has no `exp_outcome` row - nothing calls `outcomes.record_outcomes` on a real run
    yet (plan gap G2) - so the report must say the outcomes are unavailable, recommend
    "insufficient evidence", and still print every section.
    """
    from harness.research import pacing

    cli = _reader_for(db_session, monkeypatch)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)
    run_id = "0198e2b0-0000-7000-8000-00000000c0de"
    _frozen_run(db_session, run_id)
    result = runner.invoke(exp_app, ["decide", "--run-id", run_id,
                                     "--veto-profile", pacing.PROFILE_NAMES[0],
                                     "--since", WINDOW_SINCE, "--until", WINDOW_UNTIL])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.splitlines()[0].strip() == cli.EXP_LABEL
    for name in EVIDENCE_SECTIONS:
        assert decision.SECTION_TITLES[name] in result.stdout
    assert "insufficient evidence" in result.stdout
    assert "arm C: unavailable" in result.stdout
    assert "activated" not in result.stdout.lower()
    # The veto section rests on the preflight's own numbers, not on the status sentence alone.
    assert "preflight window 2026-09-01T00:00:00+00:00" in result.stdout
    assert "preflight funding:" in result.stdout
    assert "coverage is reported as opportunities, not outcomes" in result.stdout
    # An unread variant is unread, not a measured zero, and its window is not one fabricated day.
    assert "window: unread" in result.stdout
    assert "clean-book-eligible filled orders: not measured" in result.stdout


def _seed_observed_base(db_session):
    """The live watched base of §1.10, with one replay order and one non-queue_model fill.

    Hand-computed: two **live** orders (the replay one is not observed), one filled order (two
    partial fill rows on it are one order), one distinct filled game, and a four-day window
    (2026-09-01 12:00 to 2026-09-05 12:00). With the replay order counted the window would be
    104 days and the order count three - which is exactly what fix round 1's Critical 1 was.
    """
    import uuid
    from datetime import timedelta
    from decimal import Decimal

    from harness.db.models import Fill, Order, StrategyVariant, VenueMarket

    variant_id = "abc123def456"
    db_session.add(StrategyVariant(variant_id=variant_id, name="sharp_two_sided",
                                   tier="secondary", config_json={}, registered_at=NOW,
                                   active=True))
    markets = []
    for n, game_id in ((1, 101), (2, 102), (3, 103)):
        vm = VenueMarket(venue="kalshi", ticker=f"KXDECIDE-{n}", event_ticker=f"E-{n}",
                         series_ticker="KXDECIDE", game_id=game_id, market_type="moneyline",
                         first_seen_raw_id=n, last_seen_at=NOW)
        db_session.add(vm)
        markets.append(vm)
    db_session.flush()

    t0 = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    orders = []
    for n, (market, placed_at, replay) in enumerate(
            ((markets[0], t0, False),
             (markets[1], t0 + timedelta(days=4), False),
             (markets[2], t0 - timedelta(days=100), True)), start=1):
        order = Order(intent_id=uuid.uuid4(), variant_id=variant_id, venue="kalshi",
                      client_order_id=f"dec-{n}-{uuid.uuid4().hex[:6]}", ticker=market.ticker,
                      venue_market_id=market.id, side="yes", prob=Decimal("0.4800"),
                      contracts=Decimal("10"), status="filled", placed_at=placed_at,
                      sport="nfl", replay=replay)
        db_session.add(order)
        orders.append(order)
    db_session.flush()

    def fill(order, at, method, trade, replay=False):
        # `uq_fill_source` is (order_id, fill_method, source_trade_id, source_event_id): two
        # partial fills on one order are two venue prints, so they carry two trade ids.
        db_session.add(Fill(order_id=order.id, prob=Decimal("0.4800"),
                            contracts=Decimal("5"), fee=Decimal("0.0000"), filled_at=at,
                            simulated=True, fill_method=method, source_trade_id=trade,
                            replay=replay))

    fill(orders[0], t0 + timedelta(hours=1), "queue_model", "t-1")   # one order, two fill rows
    fill(orders[0], t0 + timedelta(hours=2), "queue_model", "t-2")
    fill(orders[1], t0 + timedelta(days=4, hours=1), "snapshot_cross", "t-3")  # not rested
    fill(orders[2], t0 - timedelta(days=99), "queue_model", "t-4", replay=True)  # not observed
    db_session.flush()
    return variant_id, t0


def _seed_arms_and_outcome(db_session, run_id, variant_id, t0):
    """Two arms' exploratory rows for the projected identity, and one matured markout."""
    from decimal import Decimal

    from harness.db.models import ExpOrder, ExpOutcome

    exp_orders = []
    for arm_id, fills in (("A", Decimal("10")), ("B", Decimal("10"))):
        row = ExpOrder(run_id=run_id, arm_id=arm_id, arm_order_id=-1, variant_id=variant_id,
                       ticker="KXDECIDE-1", side="yes", prob=Decimal("0.4800"),
                       contracts=Decimal("10"), filled_contracts=fills, placed_at=t0,
                       status="filled")
        db_session.add(row)
        exp_orders.append(row)
    db_session.flush()
    db_session.add(ExpOutcome(run_id=run_id, arm_id="A", exp_order_id=exp_orders[0].id,
                              horizon="1800", observed_at=t0, value=Decimal("0.012000"),
                              source_age_s=45, censored=False))
    db_session.flush()


def test_decide_reads_the_observed_base_without_replay_or_snapshot_fills(db_session,
                                                                        env_settings,
                                                                        monkeypatch):
    """The numeric path of `exp decide`, against hand-computed rows (fix round 1, Important 6).

    Asserts what Critical 1 got wrong: the replay order is outside both halves of the observed
    base, so the window is four days and not 104, and the order count is two and not three.
    """
    from harness.db.models import ExpBookHealth
    from harness.research import pacing
    from datetime import timedelta

    cli = _reader_for(db_session, monkeypatch)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)
    run_id = "0198e2b0-0000-7000-8000-00000000fa11"
    _frozen_run(db_session, run_id)
    variant_id, t0 = _seed_observed_base(db_session)
    _seed_arms_and_outcome(db_session, run_id, variant_id, t0)
    # One faulted interval over both of the filled order's fill rows: its clean-book
    # eligibility is therefore 0 of 1 filled order, which the fill count would have hidden.
    db_session.add(ExpBookHealth(run_id=run_id, ticker="KXDECIDE-1", interval_start=t0,
                                 interval_end=t0 + timedelta(hours=3),
                                 classification="data_loss_confirmed", evidence={}))
    db_session.flush()

    result = runner.invoke(exp_app, ["decide", "--run-id", run_id,
                                     "--veto-profile", pacing.PROFILE_NAMES[0],
                                     "--since", WINDOW_SINCE, "--until", WINDOW_UNTIL])
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    # Two live orders, two fill rows on one filled order, one distinct filled game.
    assert "observed base: orders=2 placed, 2 partial-fill rows on 1 filled orders" in out
    assert "observed fills: 1 (watched, live facts)" in out
    assert "distinct filled games: 1;" in out
    # The four-day live window, not the 104 days the replay order would have stretched it to.
    assert "(1 in 4 elapsed days)" in out
    assert "104" not in out
    # Clean-book eligibility is measured against this run's own classified intervals.
    assert "clean-book-eligible filled orders: 0" in out
    assert "exp_book_health intervals" in out
    # The matured outcome is arm A's own, and §3's run-wide count names its own scope.
    assert "1 mature markout outcomes for the projected portfolio identity's baseline arm" in out
    assert "post-repair sign positive" in out
    assert "fresh-outcome coverage, run-wide" in out
    # §2's rows come from T4's renderer, so every exploratory row carries its exp_label.
    label = exp_label(run_id, "B", "b" * 64)
    assert label in out
    # Nothing inferred the comparison: accrual is unidentified, so the rule lands on "revise".
    assert "## 6. Recommendation: revise" in out
    assert "accrual is unidentified" in out
