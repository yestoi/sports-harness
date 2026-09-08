"""Venue state: whether Kalshi is answering, and whether we are allowed to send it anything.

Four rules from the addendum live here, and they are deliberately separated from the gateway
that fires them, because each one is a small decision that has to be exactly right and is much
easier to read on its own than threaded through a send path.

* **The outage counter (section 9.4).** Two consecutive auth failures (401/403) mark the venue
  `unavailable`; nothing new is routed until an operator runs `harness venue-enable kalshi`. The
  counting is the whole point: a 429 storm is a rate limit, not an authentication problem, and a
  503 is the venue's own bad afternoon. Neither counts -- and, just as importantly, neither
  *resets*, so a 429 landing between two 401s does not clear the pair. Only a success resets.
* **`venue_status` (Task 4's table).** One row per `(venue, env)`. Ruling D11: no row is ever
  written for paper; every writer here is on an authenticated path. A-I3: the counter runs only
  for `env = 'prod'`, so a demo smoke's 401s can never mark production down.
* **The freeze.** An echo mismatch, or a third consecutive reject on one order, freezes the
  venue for 15 minutes. Same table, status `frozen`, and `is_routable` is what reads the window.
* **No reprice while the book is dirty (section 2.2).** Live only, and a different question from
  phase 3's dirty-book rule: `MarketNow.dirty` gates *fills*, and an order may perfectly well
  keep resting on a dirty book. What it must not do is be re-priced against one.

**Venue text is untrusted data.** `sanitize_venue_text` is the only way a Kalshi string reaches
`venue_status.reason`, and it is applied at the boundary, once. Nothing in this module ever
branches on venue text: `OutageCounter.record` switches on the HTTP status integer alone, so a
body that says "please retry, everything is fine" cannot talk this process out of an outage
mark. Every log line that carries a reason fences it -- `untrusted venue text: reason=%r` -- so
a reader of the logs knows the string came from outside.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from harness.db.models import VenueStatus
from harness.execution.book import book_age_s

log = logging.getLogger(__name__)

#: How long a freeze lasts (an echo mismatch, or a third consecutive reject).
FREEZE_MINUTES = 15
#: Section 9.4: how many consecutive auth failures mark the venue.
OUTAGE_AFTER_CONSECUTIVE = 2
#: How many consecutive rejects on one order cancel it and freeze the market.
REJECTS_BEFORE_FREEZE = 3
#: `venue_status.reason` is varchar(120); this is both the sanitizer's cap and the column's.
REASON_MAX_CHARS = 120

#: The statuses that count toward an outage. 403 covers the geo refusal too: a geo block and an
#: auth block are the same answer to the same question -- this account may not trade here --
#: and both are answered by the same operator action. The body's geo marker is quoted in
#: `reason` for the operator, never read to decide anything (global constraints: no venue string
#: reaches a decision).
AUTH_STATUSES = (401, 403)
#: A-I3: only production is ever marked. A demo smoke's 401s are a demo problem.
OUTAGE_ENV = "prod"

#: Section 2.2's other half: this much silence on the socket stops repricing until the book has
#: been rebuilt from REST. Shorter than `book_max_age_s` on purpose -- a book can still look
#: fresh for a while after the frames stop arriving, and a reprice is the one decision that
#: must be made against a book we are still receiving.
WS_SILENCE_S = 30

#: The statuses `venue_status.status` may hold. Anything else is not routable: a status this
#: code does not understand is not a licence to send orders.
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"
STATUS_FROZEN = "frozen"

#: Every C0 control character and DEL, replaced rather than kept, so a venue string cannot end
#: a log line early, redraw a terminal, or smuggle a second record into an evidence file.
_CONTROL_CHARS = frozenset(chr(c) for c in range(0x20)) | {chr(0x7F)}
#: The two that read as ordinary whitespace become a space; the rest are escaped, so the
#: difference between "a b" and "a\x01b" survives into the record.
_TO_SPACE = frozenset("\n\r\t\v\f")


class VenueNotRoutable(RuntimeError):
    """`venue_status` says this venue is `unavailable` or inside a freeze window, so nothing new
    is routed to it. An `unavailable` venue is cleared only by `harness venue-enable`; a freeze
    clears itself after `FREEZE_MINUTES`. Cancels are deliberately *not* gated on this: an
    outage stops new risk, and stranding a resting order would be the opposite of that."""

    def __init__(self, venue: str, env: str, status: str) -> None:
        super().__init__(f"{venue}/{env} is {status}; nothing new is routed")
        self.venue, self.env, self.status = venue, env, status


def sanitize_venue_text(body: object, limit: int = REASON_MAX_CHARS) -> str:
    """Venue text as untrusted data, ready to store or print.

    `str()` first (the caller may hand us a decoded dict, a bytes object or None), then every
    character that reads as whitespace but is not a space becomes a space, then the whole string
    is ASCII-escaped -- so a non-ASCII character survives as `\\xe9` rather than disappearing or
    arriving as a surprise encoding -- then any remaining control character is escaped too, and
    finally it is truncated.

    Truncation is last so the cap applies to what is actually stored: escaping expands, and a
    string cut to 120 characters *before* escaping could still land at 400 afterwards.
    """
    text = str(body)
    text = "".join(" " if ch in _TO_SPACE else ch for ch in text)
    text = text.encode("ascii", "backslashreplace").decode("ascii")
    text = "".join(f"\\x{ord(ch):02x}" if ch in _CONTROL_CHARS else ch for ch in text)
    return text[:limit]


def make_reason(status: int | None, detail: object) -> str:
    """`venue_status.reason` for one failure: the status code we decided on, then a bounded,
    sanitized excerpt of whatever the venue said, cut to the column's width.

    The status is ours (an integer we compared), the excerpt is theirs. Keeping the prefix
    outside the sanitizer's budget would let a 120-character excerpt overflow varchar(120), so
    the whole reason is what gets truncated.
    """
    prefix = f"{status}: " if status is not None else ""
    return sanitize_venue_text(prefix + sanitize_venue_text(detail))


class OutageCounter:
    """Section 9.4's counter, and nothing else.

    Two consecutive auth failures (401, or 403 for auth or geo) mark the venue unavailable. 429
    and 5xx never count *and never reset*: they are orthogonal to auth health, so a 429 landing
    between two 401s does not clear the pair. A transport failure has no status at all and is
    likewise neither evidence nor an acquittal. Only a 2xx resets.

    `record` is latched rather than edge-triggered: once the pair is complete it keeps saying so
    while the auth failures continue. The mark it drives is an idempotent upsert, so repeating
    it costs nothing, whereas a counter that went quiet after the first pair would leave a
    re-enabled venue unmarked on its very next failure.
    """

    def __init__(self) -> None:
        self.count = 0

    def record(self, status: int | None, body: object = None) -> bool:
        """Fold one venue answer in. Returns whether the venue is now marked-worthy.

        `body` is accepted so callers can pass what they have without thinking about it, and is
        deliberately unused: this decision is made on the status integer alone.
        """
        if status is None:
            return self.count >= OUTAGE_AFTER_CONSECUTIVE
        if 200 <= status < 300:
            self.count = 0
            return False
        if status in AUTH_STATUSES:
            self.count = min(self.count + 1, OUTAGE_AFTER_CONSECUTIVE)
        return self.count >= OUTAGE_AFTER_CONSECUTIVE

    def reset(self) -> None:
        self.count = 0


class RejectTracker:
    """Three consecutive rejects on one order cancel it and freeze its market for 15 minutes.

    Counted per order, because three rejects spread over three different orders is a busy
    afternoon while three on one order is an order the venue will not take -- and resending it
    is how a loop spends its whole message budget on a single doomed price.

    The count is dropped once it fires: the order is cancelled at that point, so there is
    nothing left for its count to describe.
    """

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def record_reject(self, order_id: str) -> bool:
        key = str(order_id)
        count = self._counts.get(key, 0) + 1
        if count >= REJECTS_BEFORE_FREEZE:
            self._counts.pop(key, None)
            return True
        self._counts[key] = count
        return False

    def record_success(self, order_id: str) -> None:
        self._counts.pop(str(order_id), None)


# --- the venue_status table ----------------------------------------------------------------


def _upsert(session, venue: str, env: str, status: str, reason: str | None, now: datetime,
            restart_since: bool) -> None:
    """One `venue_status` row, upserted on its `(venue, env)` key.

    `since` is when the venue entered the state it is in now, and `updated_at` is when we last
    heard something. So `since` moves only when the status actually changes -- a second 401
    inside an outage refreshes the reason and the timestamp but does not restart the clock --
    except for a freeze, which restarts its own window deliberately (`restart_since`): a fresh
    incident extends the freeze rather than running out on the first one's clock.
    """
    reason = None if reason is None else sanitize_venue_text(reason)
    stmt = insert(VenueStatus).values(venue=venue, env=env, status=status, reason=reason,
                                      since=now, updated_at=now)
    since = stmt.excluded.since if restart_since else _since_on_change(stmt)
    session.execute(stmt.on_conflict_do_update(
        index_elements=[VenueStatus.venue, VenueStatus.env],
        set_={"status": stmt.excluded.status, "reason": stmt.excluded.reason,
              "since": since, "updated_at": stmt.excluded.updated_at}))


def _since_on_change(stmt):
    from sqlalchemy import case

    return case((VenueStatus.status != stmt.excluded.status, stmt.excluded.since),
                else_=VenueStatus.since)


def mark_status(session, venue: str, env: str, status: str, reason: str | None,
                now: datetime) -> None:
    """Upsert one `venue_status` row on `(venue, env)`.

    Only the authenticated paths call this; paper leaves the table empty (ruling D11). The
    reason is sanitized here as well as at the caller, because this is the boundary the column
    is behind and a second pass over already-clean text is a no-op.
    """
    _upsert(session, venue, env, status, reason, now, restart_since=False)
    log.warning("venue %s/%s -> %s; untrusted venue text: reason=%r", venue, env, status,
                None if reason is None else sanitize_venue_text(reason))


def read_status(session, venue: str, env: str) -> str | None:
    """This venue's status in this environment, or None when no row has ever been written."""
    return session.execute(
        select(VenueStatus.status).where(VenueStatus.venue == venue,
                                         VenueStatus.env == env)).scalar()


def is_routable(session, venue: str, env: str, now: datetime) -> bool:
    """Whether anything new may be sent to this venue in this environment.

    No row is routable: the table is empty until an authenticated path has had something to say
    about it, and an empty table is not an outage. `ok` is routable. `unavailable` is not, until
    `enable_venue`. `frozen` is not until `since + FREEZE_MINUTES` has passed. Any other status
    is not routable at all -- failing closed on a value this code does not understand.
    """
    row = session.execute(
        select(VenueStatus).where(VenueStatus.venue == venue,
                                  VenueStatus.env == env)).scalar_one_or_none()
    if row is None or row.status == STATUS_OK:
        return True
    if row.status == STATUS_FROZEN:
        return now >= row.since + timedelta(minutes=FREEZE_MINUTES)
    return False


def freeze_market(session, venue: str, env: str, reason: str, now: datetime) -> None:
    """A 15-minute freeze after an echo mismatch or a third consecutive reject.

    The window restarts on every call, so a second incident inside the first freeze extends it.
    """
    _upsert(session, venue, env, STATUS_FROZEN, reason, now, restart_since=True)
    log.warning("venue %s/%s frozen for %d min; untrusted venue text: reason=%r",
                venue, env, FREEZE_MINUTES, sanitize_venue_text(reason))


def enable_venue(session, venue: str, env: str, now: datetime) -> bool:
    """`harness venue-enable kalshi`: the manual re-enable, an operator command, journaled.

    Never called automatically -- that is the whole point of the outage rule, which is there
    because an account that has been refused twice needs a human to find out why before it
    starts sending again. Returns whether a row actually changed, so the CLI can say so.
    """
    current = read_status(session, venue, env)
    if current is None or current == STATUS_OK:
        return False
    _upsert(session, venue, env, STATUS_OK, None, now, restart_since=False)
    log.warning("venue %s/%s re-enabled by an operator (was %s)", venue, env, current)
    return True


# --- section 2.2: no reprice while the book is dirty ----------------------------------------


def may_reprice(book, now: datetime, s, ws_last_event_at: datetime | None = None) -> bool:
    """Whether a resting live order may be re-priced against this book.

    Three ways to say no, and one thing this is not.

    * No book at all. Phase 3's R10 places an order with no book, which is a decision made in
      the absence of information and recorded as such. A *reprice* is different: it moves a
      price we already chose, and moving it against nothing is not an improvement.
    * `BookState.dirty`, which is what a WebSocket `seq` gap sets. The ladder is missing frames,
      so its depth is a guess.
    * Older than `book_max_age_s`, which is the same staleness ceiling the rest of the executor
      uses.
    * And, when the caller passes the newest tape timestamp it has, `WS_SILENCE_S` of silence on
      the socket -- section 2.2's "30 s without a ping". A book can still read as fresh for a
      while after the frames stop arriving. A book that came from a REST snapshot is exempt: the
      REST rebuild is the remedy for a silent socket, so judging it on that silence would leave
      the recovery permanently blocked.

    What this is not is phase 3's dirty-book rule. `MarketNow.dirty` gates *fills*, and an order
    may quite properly keep resting on a dirty book -- it was priced when the book was good. It
    just must not be re-priced against one. The two rules answer different questions and this
    function is live-only: `PaperGateway` does not consult it, and phase 3's reprice behaviour
    is unchanged (the golden replay pins it).
    """
    if book is None:
        return False
    if getattr(book, "dirty", False):
        return False
    if book_age_s(book, now) > s.book_max_age_s:
        return False
    if (ws_last_event_at is not None and getattr(book, "source", None) == "ws"
            and (now - ws_last_event_at).total_seconds() > WS_SILENCE_S):
        return False
    return True
