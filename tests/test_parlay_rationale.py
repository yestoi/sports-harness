"""The rationale: a template unless the key exists, and the template on any error."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.parlay.rationale import EFFORT, MAX_OUTPUT_TOKENS, write_rationale

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


def test_the_text_is_sanitized_at_six_hundred_not_two_hundred(db_session, keyed_settings,
                                                              built_card, wordy_client):
    """Ruling A-I5 and B-M11: `sanitize_model_text(text, 600)`, and the apostrophe survives --
    `sanitize_reason` would cap at 200 and turn "LSU's defense" into "LSUs defense"."""
    out = write_rationale(db_session, keyed_settings, built_card.card, built_card.legs, NOW,
                          client=wordy_client)
    assert len(out) == 600
    assert "'" in out and "<" not in out
