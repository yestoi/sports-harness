"""The card's rationale: a template unless the Anthropic key exists (R:213-218, addendum §1.3).

One `claude-opus-5` call at effort `low`, no tools, 200 output tokens, reserved through
`reserve_spend('parlay', ...)` -- which is what makes it count against the same U4 caps as the
veto (0.3, D10). **The template on any error at all**: a budget refusal, a transport failure, a
schema failure. A slip with no prose on it is a slip; a slip whose build failed because a model
was busy is a bug.

The text goes through `sanitize_model_text(text, 600)`. 600 because `parlay_cards.rationale` is
`String(600)` (ruling B-M11); `sanitize_model_text` rather than `sanitize_reason` because the
latter caps at 200 and strips the apostrophe out of the fan voice §8.1 asks for (ruling A-I5).

**The reservation runs on its own session, committed immediately.** `reserve_spend`'s own
docstring is explicit that its caller must "commit as soon as this returns": the advisory lock it
takes is scoped to the caller's transaction, and a caller that holds that transaction open across
the live Anthropic call blocks every other reservation on the same ISO week for the length of
that call (up to `REQUEST_TIMEOUT_S`, 120 s). `build_card` never commits its own session until
the whole card is built, so reserving on that session and only releasing after the call would
hold the week's lock for the whole request. A short-lived session bound to the same engine
(review round 1, Important 2) reserves, commits (dropping the lock before the call is even
made), then releases and writes the note in its own transaction after the call returns --
`build_card`'s own session and its uncommitted card/legs are never touched.
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from harness.research.client import PRIMARY_MODEL, ResearchClient, prompt_hash
from harness.research.notes import write_notes
from harness.research.spend import BudgetRefused, release_spend, reserve_spend
from harness.research.text import RATIONALE_MAX, sanitize_model_text

log = logging.getLogger(__name__)

EFFORT = "low"
MAX_OUTPUT_TOKENS = 200
TEMPLATE_MAX = RATIONALE_MAX

_SYSTEM = [{"type": "text", "text":
            "You write one short, upbeat paragraph about a football parlay slip, in the voice of "
            "a fan who follows LSU and the Saints. Three sentences at most. Name the legs. Do not "
            "give betting advice, do not predict a result as certain, and do not invent a number "
            "that is not in the card. Answer only with the JSON object the schema describes.",
            "cache_control": {"type": "ephemeral"}}]
_SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "maxLength": 600}},
           "required": ["text"], "additionalProperties": False}
PROMPT_HASH = prompt_hash(_SYSTEM)


def _template(card, legs) -> str:
    names = ", ".join(leg.plain_text for leg in legs)
    kind = "smart card" if card.kind == "smart" else "lottery ticket"
    correlated = (" Legs from the same game, so DraftKings will quote lower than this: enter the "
                  "slip and compare." if card.correlated else "")
    return sanitize_model_text(
        f"This week's {kind}: {names}. ${card.stake} to win about ${card.dk_payout_est}."
        f"{correlated}", TEMPLATE_MAX)


def write_rationale(session, settings, card, legs, now: datetime, client=None) -> str:
    """The card's prose. Never raises: the template is the floor."""
    fallback = _template(card, legs)
    if client is None:
        if not settings.has_anthropic_key():
            return fallback
        try:
            client = ResearchClient(settings)
        except RuntimeError:
            return fallback

    # A short-lived session on the same engine, never `session` itself: `reserve_spend`'s
    # advisory lock lives for the caller's transaction, and `session` (build_card's) does not
    # commit until the whole card is built. Reserving there and releasing only after the call
    # would hold the ISO week's lock for the length of the live request (review round 1,
    # Important 2).
    spend_factory = sessionmaker(bind=session.get_bind())
    with spend_factory() as spend_session:
        try:
            reservation = reserve_spend(spend_session, now, settings, "parlay", [PRIMARY_MODEL],
                                        searches=0)
            spend_session.commit()
        except BudgetRefused as refused:
            spend_session.rollback()
            log.info("parlay rationale skipped on budget: %s", refused)
            return fallback

        user = ("CARD\n" + "\n".join(
            f"{leg.seq}. {leg.plain_text} at {leg.dk_american:+d} "
            f"(priced {leg.odds_snapshot_id})" for leg in legs)
            + f"\nstake ${card.stake}, estimated payout ${card.dk_payout_est}, "
              f"correlated {'yes' if card.correlated else 'no'}")
        result = None
        try:
            result = client.call(model=PRIMARY_MODEL, system=_SYSTEM, user=user, schema=_SCHEMA,
                                 effort=EFFORT, max_output_tokens=MAX_OUTPUT_TOKENS, tools=())
        finally:
            release_spend(spend_session, reservation,
                          {PRIMARY_MODEL: result.usage} if result is not None else {})
            spend_session.commit()

        write_notes(spend_session, call_id=uuid.uuid4(), kind="parlay", subject_id=str(card.id),
                    effort=EFFORT, prompt_hash=PROMPT_HASH, features={"card_id": card.id},
                    results=[result], created_at=now)
        spend_session.commit()

    if result.error is not None or not isinstance(result.output, dict):
        log.info("parlay rationale fell back to the template: %s", result.error)
        return fallback
    text = sanitize_model_text(result.output.get("text"), RATIONALE_MAX)
    return text or fallback
