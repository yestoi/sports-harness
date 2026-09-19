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

from harness.experiments.execution_viability import decision
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


def test_an_adoption_proposal_is_absent_rather_than_implied():
    out = decision.render(_complete_evidence(), now=NOW)
    assert "adoption proposal: none is made here" in out
    with_proposal = decision.render(
        _complete_evidence(adoption_proposal="settings: exec_max_open_orders 150 (unchanged)"),
        now=NOW)
    assert "exec_max_open_orders 150" in with_proposal
    for field in ("exact settings", "hash", "effective date", "rollback",
                  "affected measurement populations"):
        assert field in with_proposal


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


def test_decide_renders_the_six_sections_for_a_frozen_run(db_session, env_settings, monkeypatch):
    """The command renders §1.11's report from the record, with nothing hand-written.

    The run has no `exp_outcome` row - nothing calls `outcomes.record_outcomes` on a real run
    yet (plan gap G2) - so the report must say the outcomes are unavailable, recommend
    "insufficient evidence", and still print every section.
    """
    from contextlib import contextmanager

    from harness.db.models import ExpRun
    from harness.experiments.execution_viability import cli

    run_id = "0198e2b0-0000-7000-8000-00000000c0de"
    db_session.add(ExpRun(run_id=run_id, created_at=NOW, manifest_hash="b" * 64,
                          manifest={"opportunity_definition": {"cadence_in_force": 120}},
                          clock_mode="recorded_tape", status="frozen"))
    db_session.flush()

    @contextmanager
    def reader(_s):
        yield db_session

    monkeypatch.setattr(cli.source, "reader", reader)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)
    result = runner.invoke(exp_app, ["decide", "--run-id", run_id])
    assert result.exit_code == 0, result.stdout
    assert result.stdout.splitlines()[0].strip() == cli.EXP_LABEL
    for name in EVIDENCE_SECTIONS:
        assert decision.SECTION_TITLES[name] in result.stdout
    assert "insufficient evidence" in result.stdout
    assert "arm C: unavailable" in result.stdout
    assert "activated" not in result.stdout.lower()
