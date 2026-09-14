"""The rationale: a template unless the key exists, and the template on any error."""
import re
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from harness.parlay.build import build_card
from harness.parlay.rationale import EFFORT, MAX_OUTPUT_TOKENS, write_rationale
from tests.conftest import _PARLAY_SPORT
from tests.test_parlay_build import seed_lottery_prop_pool

NOW = datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)


def test_without_a_key_the_template_is_used(db_session, env_settings, built_card):
    text_out = write_rationale(db_session, env_settings, built_card.card, built_card.legs, NOW)
    assert text_out and len(text_out) <= 600
    assert db_session.execute(text("select count(*) from research_notes")).scalar() == 0


def test_the_call_is_low_effort_two_hundred_tokens_and_toolless():
    assert EFFORT == "low" and MAX_OUTPUT_TOKENS == 200


def test_with_a_key_one_call_is_made_and_recorded(db_session, keyed_settings, built_card,
                                                  fake_client):
    out = write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                          client=fake_client)
    assert out == "LSU and the Saints on the same slip. Let us cook."
    call = fake_client.calls[0]
    assert call["effort"] == "low" and call["tools"] == () and call["max_output_tokens"] == 200
    row = db_session.execute(text(
        "select kind, subject_id, effort from research_notes")).first()
    assert (row.kind, row.subject_id, row.effort) == ("parlay", str(built_card.card.id), "low")


def test_the_reservation_is_released(db_session, keyed_settings, built_card, fake_client):
    write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                    client=fake_client)
    assert db_session.execute(text(
        "select coalesce(sum(usd_reserved), 0) from research_spend")).scalar() == Decimal("0.0000")


def test_the_rationale_reserves_no_searches(db_session, keyed_settings, built_card, fake_client):
    """The parlay rationale runs with no tools, so reserving three searches would hold budget
    that could never be spent and would push the veto toward dormancy for nothing."""
    write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                    client=fake_client)
    assert db_session.execute(text(
        "select searches from research_spend where kind = 'parlay'")).scalar() == 0


def test_a_budget_refusal_falls_back_to_the_template(db_session, keyed_settings, built_card,
                                                     fake_client):
    settings = keyed_settings.model_copy(update={"veto_daily_usd_cap": Decimal("0.001")})
    out = write_rationale(db_session, settings, built_card.card, built_card.legs, NOW,
                          client=fake_client)
    assert "Let us cook" not in out and out
    assert fake_client.calls == []


def test_a_call_error_falls_back_to_the_template(db_session, keyed_settings, built_card,
                                                 erroring_client):
    out = write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                          client=erroring_client)
    assert out and "Let us cook" not in out


def test_no_advisory_lock_is_held_during_the_call(db_session, keyed_settings, built_card,
                                                  lock_checking_client):
    """Review round 1, Important 2: `reserve_spend`'s ISO-week advisory lock must not still be
    held while the live call is in flight, or a manual `parlay build` would block the veto
    worker's own reservations for the length of the request. The client double takes the exact
    same lock key on a separate connection from inside its own `call(...)`; free means the
    reservation already committed and dropped it before the call was ever made."""
    write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                    client=lock_checking_client)
    assert lock_checking_client.lock_was_free is True


def test_the_text_is_sanitized_at_six_hundred_not_two_hundred(db_session, keyed_settings,
                                                              built_card, wordy_client):
    """Ruling A-I5 and B-M11: `sanitize_model_text(text, 600)`, and the apostrophe survives --
    `sanitize_reason` would cap at 200 and turn "LSU's defense" into "LSUs defense"."""
    out = write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                          client=wordy_client)
    assert len(out) == 600
    assert "'" in out and "<" not in out


# --- Phase 4.6 Task 6: the prop rule in the prompt, and the caller's timeout (B-I9, B-I18) ----


class _EchoClient:
    """Answers with exactly what the prompt allows a model to write: the recorded selection,
    its price and the leg's own context line, and nothing else. Accepts `timeout_s`, which the
    scheduled stage passes through `write_rationale` (B-I9)."""

    def __init__(self, text_out: str):
        self.text_out = text_out
        self.calls: list[dict] = []

    def call(self, *, model, system, user, schema, effort, max_output_tokens=None, tools=(),
             thinking=None, timeout_s=None):
        from harness.research.client import CallResult
        from harness.research.spend import Usage

        self.calls.append({"system": system, "user": user, "timeout_s": timeout_s})
        return CallResult(model=model, output={"text": self.text_out},
                          usage=Usage(input_tokens=500, output_tokens=80, cache_read_tokens=0,
                                      cache_write_tokens=0, searches=0),
                          stop_reason="end_turn", request_id="req_fake", latency_ms=5,
                          tool_calls=[], snippets={"items": [], "truncated": False}, error=None)


class _TimingOutClient:
    """A client whose one call never comes back inside the caller's bound."""

    def __init__(self):
        self.calls: list[float | None] = []

    def call(self, *, model, system, user, schema, effort, max_output_tokens=None, tools=(),
             thinking=None, timeout_s=None):
        self.calls.append(timeout_s)
        raise TimeoutError("the model call exceeded the caller's bound")


def _numbers_in(written: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", written))


def _numbers_of(card, legs) -> set[str]:
    """Every number the card itself records. A number in the prose that is not in here is a
    number the model invented (B-I18)."""
    fields = [str(card.stake), str(card.dk_payout_est), str(card.true_prob_est)]
    for leg in legs:
        fields += [leg.plain_text, leg.context_text or "", str(leg.dk_american),
                   str(leg.dk_decimal), str(leg.threshold), str(leg.p_at_build),
                   str(leg.odds_snapshot_id), str(leg.seq)]
    numbers: set[str] = set()
    for field in fields:
        numbers |= _numbers_in(field)
    return numbers


@pytest.fixture
def prop_card(db_session, env_settings):
    """One built card carrying a prop leg, with its own rationale written from the template."""
    from harness.db.models import ParlayLeg

    seed_lottery_prop_pool(db_session)
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", NOW)
    legs = db_session.query(ParlayLeg).filter_by(card_id=card.id).order_by(ParlayLeg.seq).all()
    return card, legs


def test_the_prompt_forbids_usage_injury_form_and_news_on_a_prop_card(db_session, keyed_settings,
                                                                      prop_card):
    """B-I18. The model may restate the recorded selection, line, price age and context line and
    nothing else: it has no usage or injury data, so a sentence about either would be invented.
    """
    from harness.parlay.rationale import _SYSTEM

    prompt = _SYSTEM[0]["text"].lower()
    for word in ("usage", "injury", "form", "news"):
        assert word in prompt
    card, legs = prop_card
    prop = next(leg for leg in legs if leg.market_type == "prop")
    echo = _EchoClient(f"{prop.plain_text} at {prop.dk_american:+d}. {prop.context_text}.")
    written = write_rationale(db_session, keyed_settings, card, legs, NOW, client=echo)
    for forbidden in ("questionable", "snap share", "targets per game", "expected to"):
        assert forbidden not in written.lower()
    for number in _numbers_in(written):
        assert number in _numbers_of(card, legs)


def test_a_model_call_that_times_out_falls_back_to_the_template_and_records_no_result(
        db_session, keyed_settings, built_card):
    """B-I9: the scheduled stage bounds its one model call. A timeout is not an error here --
    it falls back to the template exactly as `BudgetRefused` already does -- and no
    `research_notes` row may claim a result that never arrived."""
    client = _TimingOutClient()
    out = write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                          client=client, timeout_s=30.0)
    assert out and "Let us cook" not in out
    assert client.calls == [30.0]
    assert db_session.execute(text("select count(*) from research_notes")).scalar() == 0


def test_the_timeout_keyword_is_optional_and_the_existing_callers_are_unchanged(
        db_session, keyed_settings, built_card):
    """`None` is today's behaviour, so the CLI path and every existing caller are unchanged: no
    bound is forwarded to a client that was never given one (the client here records the
    keyword, so an accidental `timeout_s=None` on the wire would show)."""
    echo = _EchoClient("LSU and the Saints on the same slip.")
    out = write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                          client=echo)
    assert out == "LSU and the Saints on the same slip."
    assert echo.calls and echo.calls[0]["timeout_s"] is None
