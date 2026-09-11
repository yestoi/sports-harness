"""The shadow veto worker (addendum 1.4, roadmap item (d), H9).

**Post-hoc and advisory** (0.1). The executor's decision stands. This worker judges the signal
afterwards from features frozen as of `signal.created_at`, calls both models, and records the
result. Nothing here writes an intent, an order or a cancel, and nothing here reads
`research_worker_enabled` -- the worker loop that hosts it does.

**One paired call per bucket, one decision per signal** (0.2, ruling B-C1). The queue holds one
row per signal. A pass claims every unclaimed row of the oldest `(game_id, market_type,
bucket_start)` bucket, calls the pair once for the bucket's first signal, and writes a decision
for every signal in it -- `from_cache = false` for that trigger, `true` for the rest, each with
its feature delta. A later signal whose features moved past an invalidator gets its own call
instead, and becomes a trigger itself.

**Five labels** (1.4). `proceed`, `reduce` and `veto` are the model's and are the "decided" set
D19's rate is over. `veto_skipped_budget` is the cap's, with a null `call_id`. `veto_error` is
the machine's: `pause_turn`, `refusal`, a search-error block, a schema-parse failure, or a game
that was already final at claim time.

**A `reduce` or `veto` without a resolving evidence id is downgraded to `proceed`** and the
reason code records why. That is the harness half of the injection case: the prompt asks for
quoted evidence, and this is what happens when it does not arrive.

**Sequential, not parallel.** `harness.research.worker.MAX_CONCURRENT_CALLS` is 2 and this pass
makes two calls one after the other, which satisfies the ceiling trivially and keeps each call's
reservation, release and note in one straight line. Latency is not a constraint on a post-hoc
worker.

**Where the transaction boundaries are.** `reserve_spend` holds a transaction advisory lock on
the ISO week, so the pass commits the moment the reservation returns (T4's docstring) -- which
also commits the bucket claim, and is why a second worker cannot take the same bucket. The
release is committed too, in its own `finally`: a reservation committed and then rolled back
without its release would eat the cap for the rest of the day.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import VetoDecision
# One home, three readers. `harness/parlay/needs.py` defines the list, `parlay_grade` imports it
# and so does this module. Restating it here would let a postponement be final for a parlay leg
# and not for a veto decision, on the same game, in the same hour.
from harness.parlay.needs import FINAL_STATUSES
from harness.research.client import (PRIMARY_MODEL, SHADOW_MODEL, ResearchClient,
                                     web_search_tool)
from harness.research.features import build_features, feature_delta, invalidated
from harness.research.notes import write_notes
from harness.research.prompt import (EFFORT, MAX_OUTPUT_TOKENS, OUTPUT_SCHEMA, PROMPT_HASH,
                                     SYSTEM_BLOCKS, THINKING, render_user)
from harness.research.spend import BudgetRefused, release_spend, reserve_spend
from harness.research.text import VETO_REASON_MAX, sanitize_model_text
from harness.research.worker import register_closer, register_pass

log = logging.getLogger(__name__)

#: The five labels (addendum 1.4). `veto_decisions.decision` is String(20); the longest is 19.
DECISIONS = ("proceed", "reduce", "veto", "veto_skipped_budget", "veto_error")
#: "Decided" is the first three -- D19's rate and `veto_h9`'s filter are over exactly this set.
DECIDED = ("proceed", "reduce", "veto")

#: How long a claim is believed before the row is treated as stranded (review round 1, Important
#: 1). The reservation commit carries the claim, so a pass that claims a bucket and then raises
#: leaves it claimed while `ResearchWorker.run_once` rolls the rest back -- and those signals
#: would leave H9's population silently, which is the selection ruling B-C1 exists to prevent.
#: Ten minutes is well clear of the 10-30 s a paired Opus call with web search takes plus the
#: client's own 120 s timeout, so a reclaim never races a call that is still in flight.
STALE_CLAIM = timedelta(minutes=10)

__all__ = ["DECISIONS", "DECIDED", "FINAL_STATUSES", "STALE_CLAIM", "QueuedSignal",
           "bucket_start", "claim_bucket", "close_client", "veto_pass"]


@dataclass(frozen=True)
class QueuedSignal:
    """One claimed queue row joined to its signal.

    `id` is the signal id under the name `build_features` reads, so the same object serves as
    the queue entry and as the feature builder's subject.
    """

    signal_id: int
    game_id: int | None
    market_type: str
    bucket_start: datetime
    created_at: datetime
    venue_market_id: int
    side: str
    fair_p: object
    edge: object
    id: int


def bucket_start(created_at: datetime, minutes: int) -> datetime:
    """The tumbling bucket `created_at` falls in (0.2, underspecified item 1).

    Tumbling, not sliding: two signals two minutes apart across a bucket edge get two calls. A
    sliding window would make the boundary depend on arrival order, which is not a property of
    the market.
    """
    floored = created_at.replace(second=0, microsecond=0)
    return floored - timedelta(minutes=floored.minute % minutes)


#: A row is claimable when it has never been claimed, or when its claim is older than
#: `STALE_CLAIM` and the signal still has no decision. The guard is **per row**, not per bucket:
#: signals arrive throughout the 30-minute window, so a bucket can be claimed before its last
#: signal lands, and a bucket-wide "no decisions anywhere" test would strand every late arrival
#: behind its neighbours' decisions. It also means a crash between two signals of one bucket
#: re-claims exactly the ones that never decided.
_CLAIMABLE = """
    (q.claimed_at is null
     or (q.claimed_at < :stale_before
         and not exists (select 1 from veto_decisions d where d.signal_id = q.signal_id)))
"""

_OLDEST_BUCKET = text(f"""
    select q.game_id, q.market_type, q.bucket_start from veto_queue q
    where {_CLAIMABLE}
    order by q.bucket_start, q.game_id nulls last, q.market_type
    limit 1
""")

#: One statement, so two workers cannot both take the bucket: the loser's UPDATE re-reads the row
#: the winner just wrote, finds `claimed_at = :now` rather than null or stale, and matches
#: nothing. That holds for a reclaim as well as a first claim, because the winner's `claimed_at`
#: is never older than the loser's `stale_before`.
_CLAIM = text(f"""
    update veto_queue q set claimed_at = :now
    where q.game_id is not distinct from :game_id
      and q.market_type = :market_type
      and q.bucket_start = :bucket_start
      and {_CLAIMABLE}
    returning q.signal_id
""")

_SIGNALS = text("""
    select s.id, s.venue_market_id, s.side, s.fair_p, s.edge, s.created_at,
           q.game_id, q.market_type, q.bucket_start
    from veto_queue q join signals s on s.id = q.signal_id
    where q.signal_id = any(:signal_ids)
    order by s.created_at, s.id
""")

#: The game's status **now**, not as of the signal: "final at claim time" is what 1.4 says, and
#: what it is protecting is the money, which is spent now. The newest score event is the live
#: feed's own truth and `games.status` is the fallback for a game the linker has never seen.
_GAME_STATUS = text("""
    select coalesce(
        (select status from game_score_events where game_id = :game_id
          order by ts desc limit 1),
        (select status from games where id = :game_id))
""")


def claim_bucket(session: Session, now: datetime) -> list[QueuedSignal]:
    """Claim every claimable row of the oldest bucket, and return its signals in arrival order.

    Claimable is unclaimed, or claimed longer than `STALE_CLAIM` ago and still undecided.
    """
    stale_before = now - STALE_CLAIM
    head = session.execute(_OLDEST_BUCKET, {"stale_before": stale_before}).first()
    if head is None:
        return []
    claimed = session.execute(_CLAIM, {"now": now, "stale_before": stale_before,
                                       "game_id": head.game_id,
                                       "market_type": head.market_type,
                                       "bucket_start": head.bucket_start}).scalars().all()
    if not claimed:
        return []
    rows = session.execute(_SIGNALS, {"signal_ids": list(claimed)}).all()
    return [QueuedSignal(signal_id=r.id, game_id=r.game_id, market_type=r.market_type,
                         bucket_start=r.bucket_start, created_at=r.created_at,
                         venue_market_id=r.venue_market_id, side=r.side, fair_p=r.fair_p,
                         edge=r.edge, id=r.id)
            for r in rows]


def _confidence(value) -> Decimal | None:
    """The model's confidence as the column's own type. `confidence` is `Numeric(6,4)`, and a
    float handed straight to it is a rounding the record cannot explain later."""
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError):
        return None


def _record(session: Session, queued: QueuedSignal, now: datetime, *, decision: str,
            call_id: uuid.UUID | None, confidence, from_cache: bool, delta: dict,
            reason_code: str | None) -> None:
    if decision not in DECISIONS:
        raise ValueError(f"unknown veto decision {decision!r}")
    session.merge(VetoDecision(signal_id=queued.signal_id, call_id=call_id, decision=decision,
                               confidence=_confidence(confidence), from_cache=from_cache,
                               feature_delta=delta, signal_created_at=queued.created_at,
                               decided_at=now, reason_code=reason_code))
    # Flushed here, not left to the caller's commit: `Session.execute` on a plain `text()` does
    # not autoflush, so a decision that was only pending would be invisible to the next
    # statement this pass runs -- and to anything reading the row inside the same transaction.
    session.flush()


def _grade(output: dict | None, snippets: dict) -> tuple[str, object, str | None]:
    """The model's output as a stored decision.

    A `reduce` or `veto` whose evidence ids resolve to no retrieved snippet is downgraded to
    `proceed` and the reason code says so. That is the harness half of the injection case: an
    instruction inside a page can produce an ungrounded answer, and an ungrounded answer is not
    an answer.
    """
    if not isinstance(output, dict):
        return "veto_error", None, "schema"
    decision = output.get("decision")
    if decision not in DECIDED:
        return "veto_error", None, "schema"
    confidence = output.get("confidence")
    if decision == "proceed":
        return decision, confidence, None
    # The model never sees the harness's own `s1`-shaped ids -- `snippets_from` assigns them
    # *after* the call returns -- so it cites the one identifier Anthropic's `web_search_result`
    # blocks actually carry: the page's `url`. Resolving against both keeps a harness-side id
    # honored if one is ever echoed back, and fixes the live case, which is url-only (fix round
    # 2, C1: the recorded fixture cites eight URLs and none of the harness's own ids).
    items = snippets.get("items") or []
    known = ({item.get("id") for item in items} | {item.get("url") for item in items}) - {None}
    cited = [item for item in (output.get("evidence_ids") or []) if item in known]
    if not cited:
        return "proceed", confidence, "unresolved_evidence"
    return decision, confidence, None


def _sanitized(result):
    """The model's `reason` through the F60 rule before it is stored anywhere."""
    if not isinstance(result.output, dict):
        return result
    output = dict(result.output)
    output["reason"] = sanitize_model_text(output.get("reason"), VETO_REASON_MAX)
    return type(result)(**{**result.__dict__, "output": output})


def _call_pair(session: Session, client, settings, queued: QueuedSignal, numeric: dict,
               untrusted: dict, now: datetime):
    """One paired call, reserved before and released after.

    Returns `(call_id, primary, shadow)`, or raises `BudgetRefused` having made no call.
    """
    models = [PRIMARY_MODEL, SHADOW_MODEL]
    reservation = reserve_spend(session, now, settings, "veto", models,
                                searches=settings.veto_max_searches)
    # The advisory lock lives until this transaction ends, so it is ended immediately: holding it
    # across a 10-30 s Anthropic call would block every other reservation on the ISO week.
    session.commit()
    user = render_user(numeric, untrusted)
    tools = (web_search_tool(settings.veto_max_searches),)
    results, actuals = [], {}
    try:
        for model in models:
            result = client.call(model=model, system=SYSTEM_BLOCKS, user=user,
                                 schema=OUTPUT_SCHEMA, effort=EFFORT,
                                 max_output_tokens=MAX_OUTPUT_TOKENS, tools=tools,
                                 thinking=THINKING)
            results.append(result)
            actuals[model] = result.usage
    finally:
        # Always, and committed: a reservation that is committed and then rolled back without its
        # release eats the cap for the rest of the America/Chicago day.
        try:
            release_spend(session, reservation, actuals)
            session.commit()
        except Exception:  # noqa: BLE001 - a lost release must not also lose the call's record
            log.exception("veto could not release its reservation for signal %s",
                          queued.signal_id)

    call_id = uuid.uuid4()
    cleaned = [_sanitized(result) for result in results]
    write_notes(session, call_id=call_id, kind="veto", subject_id=str(queued.signal_id),
                effort=EFFORT, prompt_hash=PROMPT_HASH, features=numeric, results=cleaned,
                created_at=now)
    return call_id, cleaned[0], cleaned[1]


#: One client for the life of the process. `ResearchClient` wraps an `anthropic.Anthropic`, which
#: owns an HTTP connection pool; building one per sweep would open a new pool every `POLL_S`
#: seconds and never close any of them. The tests never reach this: they inject their own double.
_CLIENT: ResearchClient | None = None


def _shared_client(settings) -> ResearchClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = ResearchClient(settings)
    return _CLIENT


def close_client() -> None:
    """Drop the process-wide client and its HTTP pool. Registered as the worker loop's teardown,
    and safe to call twice or with no client ever built. Clearing the global also means the next
    sweep rebuilds from whatever `Settings` it is handed, so a restarted loop is never wired to a
    stale key path (review round 1, minor)."""
    global _CLIENT
    client, _CLIENT = _CLIENT, None
    if client is not None:
        client.close()


def veto_pass(session: Session, now: datetime, settings, client=None) -> dict:
    """One sweep: claim the oldest bucket and decide every signal in it.

    `client` is injected by the tests and built here in production. Dormancy is checked before
    the claim, never after: a claim without a call would leave the bucket claimed and never
    decided.
    """
    counts = {"status": "ok", "calls": 0, "decided": 0, "skipped_budget": 0}
    if client is None:
        if not settings.has_anthropic_key():
            return {"status": "dormant", "calls": 0, "decided": 0, "skipped_budget": 0}
        client = _shared_client(settings)

    queued = claim_bucket(session, now)
    if not queued:
        return counts

    trigger_features: dict | None = None
    trigger_call: uuid.UUID | None = None
    trigger_decision: tuple[str, object, str | None] | None = None

    for item in queued:
        status = session.execute(_GAME_STATUS, {"game_id": item.game_id}).scalar()
        if status in FINAL_STATUSES:
            # Underspecified item 9. The signal still decides -- H9 counts signals -- but no
            # money is spent asking about a game that has already finished.
            _record(session, item, now, decision="veto_error", call_id=None, confidence=None,
                    from_cache=False, delta={}, reason_code="game_final")
            continue

        numeric, untrusted = build_features(session, item, item.created_at)
        fired = None if trigger_features is None else invalidated(trigger_features, numeric)
        if trigger_features is not None and fired is None:
            decision, confidence, code = trigger_decision
            _record(session, item, now, decision=decision, call_id=trigger_call,
                    confidence=confidence, from_cache=True,
                    delta=feature_delta(trigger_features, numeric), reason_code=code)
            counts["decided"] += decision in DECIDED
            continue

        try:
            call_id, primary, _shadow = _call_pair(session, client, settings, item, numeric,
                                                   untrusted, now)
        except BudgetRefused as refused:
            # `reserve_spend` raised while holding the ISO-week advisory lock, which lives until
            # this transaction ends. Ending it here releases the lock on the error path instead
            # of at the worker's post-pass commit, and keeps the accounting rows and every
            # decision this pass has already written (review round 1, minor).
            session.commit()
            log.warning("veto skipped on budget: %s", refused)
            _record(session, item, now, decision="veto_skipped_budget", call_id=None,
                    confidence=None, from_cache=False, delta={}, reason_code=refused.cap)
            counts["skipped_budget"] += 1
            continue

        counts["calls"] += 1
        if primary.error is not None:
            graded = ("veto_error", None, primary.error)
        else:
            graded = _grade(primary.output, primary.snippets)
        decision, confidence, code = graded
        # The grading code wins when there is one -- a downgrade is the more important thing to
        # record -- and the invalidator that forced this call fills the field otherwise.
        _record(session, item, now, decision=decision, call_id=call_id, confidence=confidence,
                from_cache=False, delta={}, reason_code=code or fired)
        counts["decided"] += decision in DECIDED
        trigger_features, trigger_call, trigger_decision = numeric, call_id, graded

    return counts


register_pass("veto", lambda session, now, settings: veto_pass(session, now, settings))
register_closer("veto", close_client)
