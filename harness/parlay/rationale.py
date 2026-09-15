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
import inspect
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
            "that is not in the card. Answer only with the JSON object the schema describes. "
            "On a prop leg you may restate only the recorded selection, its line, how old the "
            "price is and the context line printed on the card. Never mention usage, injury, "
            "form, news or any number that is not on the card.",
            "cache_control": {"type": "ephemeral"}}]
#: Fix 39 (journal 110): `maxLength` is not in the structured-output subset the API accepts; the
#: 600-character ceiling moves into the description and is enforced, as it already was, by
#: `sanitize_model_text(result.output.get("text"), RATIONALE_MAX)` below.
#: `tests/test_research_client.py` walks this schema (and the annotator's and the veto's)
#: recursively and asserts no key outside the supported set.
_SCHEMA = {"type": "object",
           "properties": {"text": {"type": "string", "description": "At most 600 characters."}},
           "required": ["text"], "additionalProperties": False}
PROMPT_HASH = prompt_hash(_SYSTEM)


def _bounded(client) -> bool:
    """Whether this client's `call` accepts the caller's `timeout_s`.

    `ResearchClient.call` does not take one today -- its request timeout is the module-level
    `REQUEST_TIMEOUT_S` -- so passing the keyword blindly would turn a bounded call into a
    `TypeError` inside a function whose whole contract is that it never raises. A client that
    cannot be bounded is called unbounded, with one WARNING naming it, rather than not at all.
    """
    try:
        parameters = inspect.signature(client.call).parameters
    except (TypeError, ValueError):
        return False
    if "timeout_s" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()):
        return True
    log.warning("parlay rationale: %s.call takes no timeout_s; the call is unbounded",
                type(client).__name__)
    return False


def _template(card, legs) -> str:
    names = ", ".join(leg.plain_text for leg in legs)
    kind = "smart card" if card.kind == "smart" else "lottery ticket"
    correlated = (" Legs from the same game, so DraftKings will quote lower than this: enter the "
                  "slip and compare." if card.correlated else "")
    return sanitize_model_text(
        f"This week's {kind}: {names}. ${card.stake} to win about ${card.dk_payout_est}."
        f"{correlated}", TEMPLATE_MAX)


def write_rationale(session, settings, card, legs, now: datetime, client=None, *,
                    timeout_s: float | None = None) -> str:
    """The card's prose. Never raises: the template is the floor.

    `timeout_s` bounds the model call for a caller that runs under a budget (the `parlay_build`
    stage passes its `RATIONALE_TIMEOUT_S`). `None` is today's behaviour, so the CLI path and
    every existing test are unchanged. A timeout is not an error here: it falls back to the
    template exactly as `BudgetRefused` already does.
    """
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
        timed_out = False
        bounds = {"timeout_s": timeout_s} if timeout_s is not None and _bounded(client) else {}
        try:
            result = client.call(model=PRIMARY_MODEL, system=_SYSTEM, user=user, schema=_SCHEMA,
                                 effort=EFFORT, max_output_tokens=MAX_OUTPUT_TOKENS, tools=(),
                                 **bounds)
        except TimeoutError as expired:
            # The caller's bound, not a fault: the stage runs inside its own 60 s budget and a
            # slip with the template on it is a slip. Nothing is written to `research_notes`,
            # because no result arrived to record.
            timed_out = True
            log.info("parlay rationale timed out after %ss: %s", timeout_s, expired)
        finally:
            release_spend(spend_session, reservation,
                          {PRIMARY_MODEL: result.usage} if result is not None else {})
            spend_session.commit()
        if timed_out:
            return fallback

        write_notes(spend_session, call_id=uuid.uuid4(), kind="parlay", subject_id=str(card.id),
                    effort=EFFORT, prompt_hash=PROMPT_HASH, features={"card_id": card.id},
                    results=[result], created_at=now)
        spend_session.commit()

    if result.error is not None or not isinstance(result.output, dict):
        log.info("parlay rationale fell back to the template: %s", result.error)
        return fallback
    text = sanitize_model_text(result.output.get("text"), RATIONALE_MAX)
    return text or fallback
