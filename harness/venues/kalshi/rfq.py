"""Combo RFQ arrivals: parsed frames in, rows out (addendum §1.6, roadmap item (f), H5).

**This module has no socket.** It receives parsed frames and never the connection object, so it
contains nothing that could send anything at all, and `tests/test_rfq_refusal.py` asserts that
statically. That is the whole of conformance item 5 for this component: not "we chose not to
quote" but "there is nothing here that could".

**Storage is on arrival, but only for a frame that clears the boundary filter** (fix 38, journal
110; strengthened to require every leg by fix 40, journal 112). The venue's `communications`
channel is every RFQ create and delete on the exchange, not the sporadic combo trickle earlier
phases assumed -- 11,000-14,000 frames a minute sustained, almost all on combos touching no
football market at all, or mixing one football leg with legs on others that this harness can
never quote. `handle_frame` counts and drops those, and an `rfq_deleted` for an id never stored,
before either ever reaches `store_rfq`; see its docstring for the exact rule. What follows in
this docstring is about the frame that does get stored.

**Storage is on arrival** (R:243, F71). Quotes have not been queryable after the fact since
2026-06-25, so the socket is the only record and the row is written the moment the frame lands.
`rfqs.raw` holds the whole `msg`, capped at 8 KB with a `truncated` flag, because the report
needs the legs and no snapshot builder may read `raw_responses` (D13) -- and `rfqs` itself is on
the builders' forbidden list, so the report reads `rfq_quotes` and the surfaces read counts.

**Every field here is the venue's.** The frame is untrusted data end to end, so each value is
bounded before it reaches a column: identifiers are cut to their column width, numbers that are
not finite or would not fit are dropped rather than written, and `raw` is trimmed until it fits
the 8 KB budget no matter what shape arrived. A frame we cannot parse costs us that frame; a
frame that raises inside the writer would cost us the connection, and through it the arrivals
H5 is counting.

**The free text is stored and never rendered raw** (F60). The report shows a 120-character
quoted, sanitized excerpt of `market_ticker` and nothing else, and no field here ever reaches a
prompt.
"""
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import Rfq
from harness.execution.venue import sanitize_venue_text
from harness.venues.kalshi.public import FOOTBALL_SERIES

log = logging.getLogger(__name__)

CHANNEL = "communications"
VENUE = "kalshi_rfq"
ENV = "prod"

#: Ruling B-M9: `rfqs.raw` is capped at write with a flag inside the object.
RAW_MAX_BYTES = 8 * 1024
#: F60: the one thing the report renders, and only quoted. Doubles as the width every string
#: inside a trimmed `raw` is cut to.
EXCERPT_MAX = 120

#: Addendum 0.8. The documented frame codes that mean "this subscription will not work": 8
#: unknown channel, 9 authentication required, 10 channel error, 11 invalid parameter, 27 rate
#: limited. Deliberately not 19-22 (shard validations) or 25/26 (subscription limits): none of
#: those is a permission answer, and idling an hour on one would hide a bug rather than survive
#: it.
IDLE_ERROR_CODES = frozenset({8, 9, 10, 11, 27})
#: How long the listener idles after one of those. The market channels are unaffected: this is a
#: different socket in a different thread.
IDLE_S = 3600

#: Fix 44 (journal 125): the other kind of error frame. 25 (subscription buffer overflow) is not
#: a permission answer -- nothing is being refused -- so it must not cost an hour of blindness;
#: but it is the venue dropping *this subscription* underneath a socket it leaves open, which is
#: exactly what happened at 21:10:02Z on 2026-09-11 and was then shrugged off for 80 minutes.
#: Between idling and doing nothing there is the answer that was missing: drop and resubscribe.
RESUBSCRIBE_ERROR_CODES = frozenset({25})

#: The two frame types that are ours. `QuoteCreated`, `QuoteAccepted` and `QuoteExecuted` are
#: sent only to a quote's creator or an RFQ's creator, so a listener that never quotes receives
#: none; if one arrives it is counted and dropped by the connection module.
_RFQ_TYPES = ("rfq_created", "rfq_deleted")

#: Every identifier column on `rfqs` is `String(64)`.
_ID_MAX = 64
#: A multivariate collection is a handful of legs. This is not that number -- it is the ceiling
#: past which a leg list stops being data and starts being a payload.
MAX_LEGS = 64
#: About 120 decimal digits. Past this an integer is not a market number, and past roughly
#: 4300 digits CPython refuses to render one at all.
_INT_MAX_BITS = 400
#: `raw`'s trimming walks the message; a frame nested deeper than this is not a shape the venue
#: documents, and the depth cap is what keeps the walk from being the attack.
_RAW_MAX_DEPTH = 6

#: `contracts_fp` is `Numeric(14, 2)` and `target_cost_dollars` is `Numeric(14, 4)`; these are
#: the quanta and the magnitudes those columns can actually hold. A value outside them is
#: dropped, because an arrival with no size recorded is still an arrival and an insert that
#: raises is a lost connection.
_CONTRACTS_Q, _CONTRACTS_MAX = Decimal("0.01"), Decimal(10) ** 12
_MONEY_Q, _MONEY_MAX = Decimal("0.0001"), Decimal(10) ** 10

#: `datetime.fromtimestamp` accepts a range far wider than the column's; these bound it to
#: something that could plausibly be a market timestamp.
_TS_MIN, _TS_MAX = -2 * 10 ** 10, 2 * 10 ** 10


@dataclass(frozen=True)
class RfqEvent:
    kind: str
    rfq_id: str
    created_ts: datetime | None
    deleted_ts: datetime | None
    event_ticker: str | None
    market_ticker: str
    contracts_fp: Decimal | None
    target_cost_dollars: Decimal | None
    mve_collection_ticker: str | None
    legs: list[dict]
    raw: dict


#: Fix 38 (journal 110), strengthened by fix 40 (journal 112): the two reasons a frame is
#: counted and dropped at the boundary, before `store_rfq` ever runs. Passed to `handle_frame`'s
#: `on_dropped` callback so a caller counting them (the listener) never has to duplicate the
#: decision.
DROP_NOT_ALL_FOOTBALL = "not_all_football"
DROP_UNKNOWN_DELETE = "unknown_delete"


def _series_ticker(event_ticker: str | None) -> str:
    """The series prefix of a venue event ticker: the segment before the first `-`, the same
    derivation `harness/venues/kalshi/public.py`'s `MarketSummary.series_ticker` uses
    (`event_ticker.split("-")[0]`). Deliberately not a `venue_markets` lookup: the boundary
    filter below has no session and runs ahead of every write, at 11,000-14,000 frames a minute
    (journal 110) -- a per-leg DB probe here is exactly the cost fix 35 already removed from the
    quote path, reintroduced one step earlier."""
    return (event_ticker or "").split("-", 1)[0]


def _all_legs_football(event: RfqEvent) -> bool:
    """Fix 40 (journal 112): whether every market this arrival touches is football -- checked at
    the boundary, before anything is written. `harness.venues.kalshi.public.FOOTBALL_SERIES` is
    the one place the six series this harness prices are named, the same membership fix 35's
    `_is_football` (`harness/venues/kalshi/rfq_quote.py`) gates the counterfactual quote on;
    this checks it against the ticker string rather than a `venue_markets` row, since there is
    no session at this point and nothing here may cost a DB round trip per leg.

    Fix 38's original rule was "at least one leg is football", on the reasoning that a combo
    with even one football leg might still be quotable. Journal 112 found that wrong: the
    venue's cross-category combos mix a football leg with legs on other markets, and
    `compute_quote`'s own stronger, DB-resolved "every leg is football" gate (`_is_football`,
    `rfq_quote.py`) declines every one of them `no_fair` -- 1,837 `no_fair` and 163
    `same_game` in six minutes, zero quotable, out of the mixed combos fix 38 let through. The
    only population this harness can ever quote is a combo whose every leg is football, so the
    boundary now matches that exactly rather than storing a population it can only ever decline.

    A single-market RFQ (no `mve_selected_legs`) is not a combo and carries no legs at all, so
    it is checked against its own top-level `event_ticker`, the only market it names, rather
    than against an empty leg list (which `all()` would vacuously pass). A combo is checked leg
    by leg, and its own top-level ticker is not consulted at all: a combo's top-level ticker is
    typically its MVE collection id rather than any priced series (the incident's own
    `KXMVECROSSCATEGORY-SHARD1-...` shape), so it says nothing about whether the combo itself is
    all-football.
    """
    if not event.legs:
        return _series_ticker(event.event_ticker) in FOOTBALL_SERIES
    return all(_series_ticker(leg.get("event_ticker")) in FOOTBALL_SERIES for leg in event.legs)


def _dec(value, quantum: Decimal, ceiling: Decimal) -> Decimal | None:
    """One venue number, or None when it is not one this column can hold.

    `Decimal(str(...))` happily returns `NaN` and `Infinity`, and `1E+400` parses fine and then
    fails at the insert. Each of those is a frame that would take the connection down rather
    than one field, so each becomes None.
    """
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite() or abs(parsed) >= ceiling:
        return None
    try:
        return parsed.quantize(quantum)
    except (InvalidOperation, ValueError):
        return None


def _ts(value) -> datetime | None:
    """One venue epoch-second timestamp, or None. Documented as an integer; a float is accepted
    because a venue that starts sending one is not a reason to lose the arrival."""
    if value is None or value == "" or isinstance(value, (dict, list, bool)):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        try:
            seconds = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return None
    if not _TS_MIN <= seconds <= _TS_MAX:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _no_nul(text: str) -> str:
    """A text column cannot hold a NUL byte and neither can a `jsonb` string: the driver refuses
    the whole insert. So the one character that is a storage error rather than a rendering
    problem is removed here, at the write, and everything else about the venue's string is left
    exactly as it arrived for F60 to deal with at the render."""
    return text.replace("\x00", "") if "\x00" in text else text


def _text(value, limit: int = _ID_MAX) -> str:
    """A venue string cut to its column. Not sanitized here beyond the NUL: `rfqs` stores the
    venue's own bytes and F60 sanitizes at the render, which is the only place the string is
    ever looked at."""
    return "" if value is None else _no_nul(str(value))[:limit]


def _legs(msg: dict) -> list[dict]:
    """`mve_selected_legs`, normalized. Each leg carries its own `event_ticker`, which is what
    F72's independence test and §8.2's "decline any RFQ with two legs from one game" both read.
    """
    raw_legs = msg.get("mve_selected_legs")
    if not isinstance(raw_legs, list):
        return []
    out = []
    for leg in raw_legs[:MAX_LEGS]:
        if not isinstance(leg, dict):
            continue
        ticker = leg.get("market_ticker")
        if not isinstance(ticker, str) or not ticker:
            continue
        out.append({"event_ticker": _text(leg.get("event_ticker")),
                    "market_ticker": _text(ticker),
                    "side": _text(leg.get("side"), 8),
                    "yes_settlement_value_dollars": _text(
                        leg.get("yes_settlement_value_dollars"), 32)})
    return out


#: The fields the venue documents on `rfq_created` and `rfq_deleted`. A trimmed `raw` keeps
#: these and drops everything else, so an unknown key can pad a message but never survive it.
_RAW_KEEP = ("id", "creator_id", "market_ticker", "event_ticker", "created_ts", "deleted_ts",
             "contracts_fp", "target_cost_dollars", "mve_collection_ticker",
             "mve_selected_legs")


def _raw_size(payload: dict) -> int:
    return len(json.dumps(payload, default=str).encode())


def _cut(value, depth: int = 0):
    """One value, bounded in every dimension a JSON document has: string length, list length,
    mapping size, number of digits, and nesting depth."""
    if depth >= _RAW_MAX_DEPTH:
        return "..."
    if isinstance(value, str):
        return _no_nul(value)[:EXCERPT_MAX]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        # A JSON number has no width limit, and an integer wide enough cannot even be turned
        # into a string on this interpreter (`int_max_str_digits`), so it is measured in bits.
        return value if value.bit_length() <= _INT_MAX_BITS else "..."
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [_cut(item, depth + 1) for item in value[:MAX_LEGS]]
    if isinstance(value, dict):
        return {_no_nul(str(k))[:EXCERPT_MAX]: _cut(v, depth + 1)
                for k, v in list(value.items())[:MAX_LEGS]}
    return str(value)[:EXCERPT_MAX]


def _strip_nul(value):
    """Every NUL byte gone, walked over the **decoded** structure.

    Not over the serialized text: the six characters JSON writes for a NUL are also the tail of
    an escaped backslash, so deleting them from the encoded document turns a ticker holding the
    literal characters `\\u0000b` into `\\b`, which is a backspace. That corrupts `raw` while
    leaving `truncated` false. Keys are walked as well as values, because a key is a string the
    venue chose too.
    """
    if isinstance(value, str):
        return _no_nul(value)
    if isinstance(value, dict):
        return {(_no_nul(k) if isinstance(k, str) else k): _strip_nul(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    return value


def _collapse(trimmed: dict) -> dict:
    """The last resort: every kept key reduced to a bounded scalar.

    `_cut` bounds each level of a value but not the product of them, so a documented key that
    arrived as a nested document rather than a number is still large after it. Nine bounded
    scalars and their key names are under two kilobytes whatever they held.
    """
    out = {}
    for key, value in trimmed.items():
        if value is None or isinstance(value, (int, float, bool)):
            out[key] = value
        elif isinstance(value, str):
            out[key] = _no_nul(value)[:EXCERPT_MAX]
        else:
            out[key] = "..."
    return out


def _capped_raw(msg: dict) -> dict:
    """The whole message, or as much of it as fits, with the flag inside the object.

    The cap is a real cap and not a hope: whatever arrives, what is returned fits in
    `RAW_MAX_BYTES`. First the message whole. Then the documented fields with every level of
    every value bounded, the leg list halved until the object fits, and finally -- for a
    documented key that arrived as a nested document -- every kept value collapsed to a scalar.
    That last step is what makes the guarantee unconditional rather than a claim about the
    shapes we happened to think of.
    """
    whole = True
    try:
        msg = _strip_nul(msg)
    except RecursionError:
        # Deeper than this interpreter will walk. It cannot be stored whole, and the trimming
        # path below caps depth at `_RAW_MAX_DEPTH` and strips NULs as it goes.
        whole = False
    if whole:
        payload = {"msg": msg, "truncated": False}
        if _fits(payload):
            return payload
    trimmed = {k: _cut(v) for k, v in msg.items() if k in _RAW_KEEP}
    legs = trimmed.pop("mve_selected_legs", None)
    legs = legs if isinstance(legs, list) else []
    while True:
        payload = {"msg": {**trimmed, "mve_selected_legs": legs}, "truncated": True}
        if _fits(payload):
            return payload
        if legs:
            legs = legs[:len(legs) // 2]
            continue
        return {"msg": {**_collapse(trimmed), "mve_selected_legs": []}, "truncated": True}


def _fits(payload: dict) -> bool:
    """Whether this payload is inside the budget. A payload that cannot be rendered at all does
    not fit, which sends the caller to the next, smaller shape."""
    try:
        return _raw_size(payload) <= RAW_MAX_BYTES
    except (TypeError, ValueError, RecursionError):
        return False


def parse_rfq_frame(msg) -> RfqEvent | None:
    """One WebSocket frame as an event, or None when it is not one of ours."""
    if not isinstance(msg, dict):
        return None
    kind = msg.get("type")
    if kind not in _RFQ_TYPES:
        return None
    body = msg.get("msg")
    if not isinstance(body, dict):
        return None
    rfq_id, ticker = body.get("id"), body.get("market_ticker")
    if not isinstance(rfq_id, str) or not rfq_id or not isinstance(ticker, str) or not ticker:
        return None
    return RfqEvent(
        kind=kind,
        rfq_id=_text(rfq_id),
        created_ts=_ts(body.get("created_ts")),
        deleted_ts=_ts(body.get("deleted_ts")),
        event_ticker=(_text(body.get("event_ticker")) or None),
        market_ticker=_text(ticker),
        contracts_fp=_dec(body.get("contracts_fp"), _CONTRACTS_Q, _CONTRACTS_MAX),
        target_cost_dollars=_dec(body.get("target_cost_dollars"), _MONEY_Q, _MONEY_MAX),
        mve_collection_ticker=(_text(body.get("mve_collection_ticker")) or None),
        legs=_legs(body),
        raw=_capped_raw(body),
    )


def store_rfq(session: Session, event: RfqEvent, now: datetime) -> Rfq:
    """Upsert one arrival.

    The first frame for an id writes the row and the rest never rewrite it: `received_at` is the
    arrival timestamp H5's latency reads, and a repeated `rfq_created` is the same arrival seen
    twice, not a second one. A `rfq_deleted` is the exception, because it carries the one fact
    the create could not: that the RFQ is gone.

    Fix 38: this primitive still upserts either way -- a `rfq_deleted` for an id with no row
    here would happily create one, the F71 behaviour this had before fix 38. The guard against
    that (three quarters of the incident's flood, journal 110) lives one level up, in
    `handle_frame`, which never calls this at all for a delete whose id is not already stored:
    that frame is counted (`DROP_UNKNOWN_DELETE`) and dropped before it reaches here.
    """
    row = session.get(Rfq, event.rfq_id)
    if row is None:
        row = Rfq(id=event.rfq_id, received_at=now,
                  created_ts=event.created_ts, event_ticker=event.event_ticker,
                  market_ticker=event.market_ticker, contracts_fp=event.contracts_fp,
                  target_cost_dollars=event.target_cost_dollars,
                  mve_collection_ticker=event.mve_collection_ticker, legs=event.legs,
                  raw=event.raw, status="open", deleted_ts=None)
        session.add(row)
    if event.kind == "rfq_deleted":
        row.status = "deleted"
        row.deleted_ts = event.deleted_ts or now
    session.flush()
    return row


def handle_frame(session: Session, msg, now: datetime,
                 on_replay: Callable[[], None] | None = None,
                 allow_quote: Callable[[], bool] | None = None,
                 on_dropped: Callable[[str], None] | None = None) -> Rfq | None:
    """One frame. Returns the stored row, or None when the frame was not an RFQ event, or was
    one but was dropped at the boundary.

    The counterfactual quote is computed **on arrival**, beside the row, because that is the only
    moment the leg fair values are the ones we would actually have quoted against. It is stored
    and never sent; `harness/venues/kalshi/rfq_quote.py` has no way to send it.

    Review I2: the arrival is the fact H5 counts and the socket is the only record of it (F71);
    the quote is a counterfactual computed beside it. `compute_quote` runs inside its own
    savepoint, so anything it raises rolls back only the quote attempt -- never the `rfqs` row
    already written above, and never out of this function to take the caller's socket loop down
    with it. The row is returned either way.

    **Fix 38 (journal 110), strengthened by fix 40 (journal 112): the boundary filter.** The
    venue's `communications` channel is every RFQ create and delete on the exchange --
    11,000-14,000 frames a minute sustained, three quarters `rfq_deleted`, almost all on combos
    touching no football market at all. Neither kind reaches `store_rfq` unless it clears its
    own cheap, DB-free check first: an `rfq_created` only when `_all_legs_football` finds every
    leg (or the RFQ's own top-level ticker, for a single-market RFQ) on a series `store_rfq`'s
    caller actually prices -- fix 40 tightened this from fix 38's original "at least one leg",
    which still stored every cross-category combo mixing one football leg with others, all of
    them declined `no_fair` downstream and none of them quotable; an `rfq_deleted` only when its
    id is already a row here (`session.get`, the same primary-key probe `store_rfq` itself
    makes). Either drop is counted through `on_dropped` (`DROP_NOT_ALL_FOOTBALL` /
    `DROP_UNKNOWN_DELETE`) and never logged per frame -- at flood volume a log line per drop is
    the same cost this filter exists to remove. The raw cap and NUL stripping from T13 still
    apply to whatever does get stored; nothing about parsing or trimming changes.

    **Fix 35, item 2: never recompute a quote already held.** The venue replays the whole open
    RFQ set on every subscribe (journal 109: ten reconnects, 4,902 frames, in the 03:15-03:45 CT
    incident), so a replayed `rfq_created` for an id `rfq_quotes` already has a row for is by far
    the common case on a reconnect. `uq_rfq_quote_rfq` makes "already quoted" one indexed probe;
    when it hits, the arrival is stored exactly as any other frame is and nothing else happens --
    logged (DEBUG, review I3: this fires at burst volume and the id is unsanitized venue text),
    and, when the caller is counting (the listener's `replayed`), passed to `on_replay`.

    **Fix 35 round 1 (review C1): the reconnect replay burst is rate-limited, not budgeted.**
    `allow_quote`, when given, is called **only** at the point a quote is actually about to be
    attempted -- after `store_rfq` and after the "already quoted" check has already found no row,
    so neither a dedupe hit nor an `rfq_deleted` frame ever calls it or spends whatever budget it
    is guarding (the listener's sliding `RFQ_QUOTE_RATE_MAX` per `RFQ_QUOTE_RATE_WINDOW_S`,
    `harness/venues/kalshi/rfq_socket.py`). A `False` return skips the quote decision for this
    frame; the frame is still stored as an arrival, never quoted.
    """
    event = parse_rfq_frame(msg)
    if event is None:
        return None
    if event.kind == "rfq_created":
        if not _all_legs_football(event):
            if on_dropped is not None:
                on_dropped(DROP_NOT_ALL_FOOTBALL)
            return None
    else:   # rfq_deleted
        if session.get(Rfq, event.rfq_id) is None:
            if on_dropped is not None:
                on_dropped(DROP_UNKNOWN_DELETE)
            return None
    row = store_rfq(session, event, now)
    if event.kind == "rfq_created":
        existing = session.execute(
            text("select 1 from rfq_quotes where rfq_id = :id"), {"id": row.id}).first()
        if existing is None:
            if allow_quote is None or allow_quote():
                try:
                    with session.begin_nested():
                        from harness.config.settings import get_settings
                        from harness.venues.kalshi.rfq_quote import compute_quote

                        compute_quote(session, get_settings(), row, now)
                except Exception:  # noqa: BLE001 - the arrival must survive a quote failure
                    # Review I3: the same unsanitized-render gap the replay line below had, on a
                    # line that fires far less often (only on a failure) but is worth the same
                    # fix -- `row.id` is the venue's own `rfq_id`, never escaped before this.
                    log.exception("computing the counterfactual quote for rfq %s failed",
                                  sanitize_venue_text(row.id, _ID_MAX))
        else:
            log.debug("rfq listener: rfq %s already quoted; replayed frame stored, no recompute",
                      sanitize_venue_text(row.id, _ID_MAX))
            if on_replay is not None:
                on_replay()
    return row


def _error_code(error_msg) -> int | None:
    """The `code` from an error frame, or None. Documented as an integer; a digit string is
    accepted, because refusing to read a refusal is the one reading error that costs an hour of
    reconnects instead of an hour of idling."""
    if not isinstance(error_msg, dict):
        return None
    body = error_msg.get("msg")
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.strip().lstrip("-").isdigit():
        return int(code)
    return None


def idle_reason(status: int | None, error_msg: dict | None) -> str | None:
    """Why the listener should idle, or None (addendum 0.8, F71).

    "Non-200 from the subscribe" conflates two different layers, so it is resolved into both: a
    handshake HTTP status other than 101, and the documented error-frame codes that mean the
    subscription will not work. The reason is bounded to `venue_status.reason`'s 120 characters
    and sanitized, because every character of it is the venue's.
    """
    parts: list[str] = []
    if status is not None and status != 101:
        parts.append(f"handshake {status}")
    code = _error_code(error_msg)
    if code in IDLE_ERROR_CODES:
        body = error_msg.get("msg") if isinstance(error_msg, dict) else None
        detail = body.get("msg") if isinstance(body, dict) else ""
        parts.append(f"frame {code}: {detail}")
    if not parts:
        return None
    return sanitize_venue_text(" ".join(parts), EXCERPT_MAX)


def resubscribe_reason(error_msg: dict | None, sid: int | None) -> str | None:
    """Why the listener should drop this socket and subscribe again, or None (fix 44).

    Two frames say the subscription is gone while the connection is not: one of
    `RESUBSCRIBE_ERROR_CODES`, and any error frame the venue addresses to the sid this listener
    holds -- an error about *our* subscription is about our subscription whatever its code.
    Checked only after `idle_reason`, so a refusal that happens to carry our sid still idles for
    an hour rather than reconnecting against a venue that is telling us no. The reason is the
    venue's own text, sanitized and bounded to `venue_status.reason`'s 120 characters.
    """
    if not isinstance(error_msg, dict):
        return None
    code = _error_code(error_msg)
    own_sid = sid is not None and not isinstance(error_msg.get("sid"), bool) \
        and error_msg.get("sid") == sid
    if code not in RESUBSCRIBE_ERROR_CODES and not own_sid:
        return None
    body = error_msg.get("msg")
    detail = body.get("msg") if isinstance(body, dict) else ""
    return sanitize_venue_text(f"frame {code}: {detail}", EXCERPT_MAX)
