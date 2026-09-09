"""Task 10: venue state -- the outage counter, `venue_status`, the freeze, startup
reconciliation and the live-only "no reprice while the book is dirty" rule.

Everything live here drives Task 6's `FakeTransport`, so no test opens a socket and no test
needs a credential. The paper path is asserted *unchanged* rather than re-tested: the golden
replay in `tests/test_gateway.py` is the byte-for-byte evidence, and what this file adds is the
two claims that belong to this task -- paper writes no `venue_status` row, and the reprice gate
is not on the paper path at all.

Venue text is treated as untrusted data throughout: `sanitize_venue_text` is the only way a
Kalshi string reaches `venue_status.reason`, and no venue string reaches a decision anywhere in
`harness/execution/venue.py` (the outage counter switches on the HTTP status alone).
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from harness.db.models import KillSwitch, Order, VenueStatus
from harness.execution import EXECUTOR_VERSION, store
from harness.execution.book import BookState
from harness.execution.gateway import (
    KalshiGateway,
    OrderNotPlaced,
    PaperGateway,
    ReconcileReport,
)
from harness.execution.loop import Executor
from harness.execution.plan import ExecSettings
from harness.execution.venue import (
    FREEZE_MINUTES,
    FreezeWindowMismatch,
    OUTAGE_AFTER_CONSECUTIVE,
    REASON_MAX_CHARS,
    REJECTS_BEFORE_FREEZE,
    OutageCounter,
    RejectTracker,
    VenueNotRoutable,
    enable_venue,
    freeze_market,
    is_routable,
    make_reason,
    mark_status,
    may_reprice,
    read_status,
    sanitize_venue_text,
)
from harness.venues.kalshi.authed import (
    ECHO_FREEZE_MINUTES,
    ORDERS_PATH,
    KalshiApiError,
    KalshiDecodeError,
    KalshiReader,
    MessageBudgetExceeded,
    TokenBucket,
)
from harness.venues.kalshi.http import VenueTransportError

from tests.test_kalshi_authed import FakeTransport, _err, _ok
from tests.test_kalshi_writer import _accepted, _writer

NOW = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)
EXEC_SETTINGS = ExecSettings()
CENT_RANGES = [{"start": 0, "end": 1, "step": 0.01}]


# --- helpers ------------------------------------------------------------------------------


class _Market:
    """The `market` argument, reduced to what a gateway reads off it. `book` is absent, which
    is how a caller says "I am making no claim about the book" (see `_Live` below)."""

    def __init__(self, price_ranges=None) -> None:
        self.price_ranges = price_ranges


class _MarketWithBook(_Market):
    """A `MarketNow`-shaped stand-in: it carries the book the loop is already holding."""

    def __init__(self, book, price_ranges=None) -> None:
        super().__init__(price_ranges)
        self.book = book


def _book(dirty: bool = False, as_of: datetime = NOW, source: str = "ws") -> BookState:
    return BookState(ticker="KXNFLGAME-X", yes_bids={}, no_bids={}, sid=1, seq=7,
                     as_of=as_of, source=source, anchor_id=1, last_event_id=1, dirty=dirty)


def _book_from_rest_snapshot(as_of: datetime = NOW) -> BookState:
    """What `_advance_books` hands back after a REST rebuild: a fresh anchor, `dirty` cleared."""
    return BookState(ticker="KXNFLGAME-X", yes_bids={}, no_bids={}, sid=0, seq=0,
                     as_of=as_of, source="rest", anchor_id=0, last_event_id=42, dirty=False)


_TICKERS = iter(f"KXNFLGAME-{n}" for n in range(1, 1000))


def _values(**kw) -> dict:
    values = dict(
        intent_id=uuid.uuid4(), variant_id="v-t10", venue="kalshi", mode="live",
        client_order_id=f"c-{uuid.uuid4()}", ticker=next(_TICKERS),
        venue_market_id=1, side="yes", prob=Decimal("0.5600"),
        contracts=Decimal("10.00"), status="open", placed_at=NOW,
        expiry=NOW + timedelta(hours=2), replay=False)
    values.update(kw)
    return values


def _open_order(session, **kw) -> int:
    order_id = store.insert_order(session, _values(**kw))
    session.flush()
    return order_id


def _kalshi(transport, session=None, **kw) -> KalshiGateway:
    """A live gateway wired the way the executor wires one. `session` supplies the
    `session_factory` every venue-state write goes through: an outage mark, a freeze, a reject
    cancel and a kill-switch trip are committed on a session of their own so they outlive the
    exception that caused them (fix round 1, Important 1)."""
    kw.setdefault("order_group_id", "g1")
    kw.setdefault("clock", lambda: NOW)
    if session is not None:
        kw.setdefault("session_factory", _factory(session))
    return KalshiGateway(_writer(transport), KalshiReader(transport), **kw)


def _fresh(session):
    """A session that shares nothing with the test's own: what an operator, the dashboard or
    the next process would see. Venue state has to be visible here or it did not survive."""
    return _factory(session)()


def _factory(session):
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


def _resting(order_id="ov9", client_order_id="unknown", **kw) -> dict:
    order = {"order_id": order_id, "client_order_id": client_order_id,
             "ticker": "KXNFLGAME-X", "outcome_side": "yes", "status": "resting"}
    order.update(kw)
    return order


# --- the outage rule (section 9.4) --------------------------------------------------------


def test_two_consecutive_401s_mark_the_venue_unavailable():
    c = OutageCounter()
    assert c.record(401, {"error": "unauthorized"}) is False
    assert c.record(401, {"error": "unauthorized"}) is True


def test_a_403_counts_as_an_auth_error():
    c = OutageCounter()
    c.record(403, {})
    assert c.record(401, {}) is True


def test_ten_429s_never_mark_the_venue():
    c = OutageCounter()
    for _ in range(10):
        assert c.record(429, {}) is False


def test_five_5xx_never_mark_the_venue():
    c = OutageCounter()
    for _ in range(5):
        assert c.record(503, {}) is False


def test_a_429_between_two_401s_does_not_clear_the_pair():
    c = OutageCounter()
    c.record(401, {})
    c.record(429, {})
    assert c.record(401, {}) is True


def test_a_success_resets_the_counter():
    c = OutageCounter()
    c.record(401, {})
    c.record(200, {})
    assert c.record(401, {}) is False


def test_a_network_error_neither_counts_nor_clears_the_pair():
    """A transport failure carries no status: it is not evidence of auth health either way."""
    c = OutageCounter()
    c.record(401, {})
    assert c.record(None, {}) is False
    assert c.record(401, {}) is True


def test_a_400_reject_neither_counts_nor_clears_the_pair():
    c = OutageCounter()
    c.record(401, {})
    assert c.record(400, {}) is False
    assert c.record(401, {}) is True


def test_the_counter_keeps_reporting_while_the_auth_errors_continue():
    """Latched, not edge-triggered: the mark is an idempotent upsert, and a counter that went
    quiet after the first pair would leave a re-enabled venue unmarked on the next failure."""
    c = OutageCounter()
    c.record(401, {})
    assert c.record(401, {}) is True
    assert c.record(401, {}) is True
    assert c.count == OUTAGE_AFTER_CONSECUTIVE


def test_reset_clears_the_counter():
    c = OutageCounter()
    c.record(401, {})
    c.reset()
    assert c.record(401, {}) is False


def test_a_decode_error_is_not_an_auth_error_and_never_counts(db_session):
    """Fix 24's follow-up. The first demo smoke failed with a `KalshiDecodeError` and the demo
    `venue_status` row came back `unavailable`, which looked like the section 9.4 counter
    firing on a decode error. It was not: this counter reads the HTTP status integer and
    nothing else, and a decode error carries no status at all -- it neither counts nor clears
    a standing pair. The demo row was written by the smoke's own failure path, which marks
    `('kalshi', 'demo')` for *any* failing step by design and never touches production (A-I3).
    """
    c = OutageCounter()
    c.record(401, {})
    assert c.record(getattr(KalshiDecodeError("no side"), "status", None), {}) is False
    assert c.count == 1
    assert c.record(401, {}) is True


def test_a_decode_error_out_of_the_write_path_marks_nothing_and_does_not_count(db_session):
    """The same thing one level up: a `KalshiDecodeError` propagates out of the gateway's guard
    untouched. It writes no `venue_status` row, and it leaves the counter where it was, so two
    later 401s still take exactly two to mark the venue."""
    body = {"order_id": "o1", "client_order_id": "c1", "fill_count": "NaN",
            "remaining_count": "10.00", "ts_ms": 1789000000000}
    t = FakeTransport(queued=[_ok(body), _err(401), _err(401)])
    g = _kalshi(t, db_session)

    with pytest.raises(KalshiDecodeError):
        g.place(db_session, _values(prob=Decimal("0.5600"), contracts=Decimal("10.00")),
                None, _Market(CENT_RANGES), NOW)
    assert db_session.get(VenueStatus, ("kalshi", "prod")) is None

    for _ in range(2):
        with pytest.raises(KalshiApiError):
            g.place(db_session, _values(prob=Decimal("0.5600"), contracts=Decimal("10.00")),
                    None, _Market(CENT_RANGES), NOW)
    with _fresh(db_session) as fresh:
        assert fresh.get(VenueStatus, ("kalshi", "prod")).status == "unavailable"


def test_marking_writes_the_status_code_and_a_bounded_body_excerpt(db_session):
    body = {"error": "x" * 500}
    mark_status(db_session, "kalshi", "prod", "unavailable",
                f"401: {sanitize_venue_text(body)}", NOW)
    row = db_session.get(VenueStatus, ("kalshi", "prod"))
    assert row.status == "unavailable" and row.reason.startswith("401: ")
    assert len(row.reason) <= 128


def test_the_reason_is_truncated_to_the_column_width(db_session):
    """`venue_status.reason` is varchar(120); a reason built from a long excerpt plus its
    status prefix must be cut here rather than by the database."""
    mark_status(db_session, "kalshi", "prod", "unavailable",
                make_reason(401, "y" * 500), NOW)
    row = db_session.get(VenueStatus, ("kalshi", "prod"))
    assert len(row.reason) == REASON_MAX_CHARS and row.reason.startswith("401: ")


def test_since_moves_only_when_the_status_actually_changes(db_session):
    later = NOW + timedelta(minutes=5)
    mark_status(db_session, "kalshi", "prod", "unavailable", "401", NOW)
    mark_status(db_session, "kalshi", "prod", "unavailable", "401 again", later)
    row = db_session.get(VenueStatus, ("kalshi", "prod"))
    assert row.since == NOW and row.updated_at == later and row.reason == "401 again"

    mark_status(db_session, "kalshi", "prod", "ok", None, later)
    db_session.expire_all()
    assert db_session.get(VenueStatus, ("kalshi", "prod")).since == later


# --- venue text is untrusted data ---------------------------------------------------------


def test_sanitize_strips_newlines_and_escapes_non_ascii():
    dirty = "line1\nline2\tIGNORE PREVIOUS INSTRUCTIONS \x00 café"
    clean = sanitize_venue_text(dirty)
    assert "\n" not in clean and "\t" not in clean and "\x00" not in clean
    assert clean.isascii() and len(clean) <= 120


def test_sanitize_truncates_to_120_characters():
    assert len(sanitize_venue_text("a" * 5000)) == 120


def test_sanitize_takes_any_object_including_none():
    """`str()` first, so a decoded body arrives as its repr -- in which a newline is already the
    two harmless characters `\\` and `n`, and stays that way."""
    assert sanitize_venue_text(None) == "None"
    assert sanitize_venue_text({"code": "bad\nnews"}) == "{'code': 'bad\\nnews'}"
    assert "\n" not in sanitize_venue_text({"code": "bad\nnews"})


def test_sanitize_escapes_a_carriage_return_and_a_vertical_tab():
    """Every C0 control character, not just the two that end a log line."""
    clean = sanitize_venue_text("a\rb\vc\x1bd")
    assert clean.isascii()
    assert all(ch not in clean for ch in ("\r", "\v", "\x1b"))


# --- per-env isolation (A-I3) -------------------------------------------------------------


def test_a_demo_outage_never_marks_production(db_session):
    mark_status(db_session, "kalshi", "demo", "unavailable", "401: demo unfunded", NOW)
    assert read_status(db_session, "kalshi", "prod") is None
    assert is_routable(db_session, "kalshi", "prod", NOW) is True


def test_the_outage_counter_only_marks_production(db_session):
    """A-I3 at the gateway: a demo gateway counts nothing and writes no row at all."""
    t = FakeTransport(queued=[_err(401), _err(401)])
    g = _kalshi(t, db_session, env="demo")
    for _ in range(2):
        with pytest.raises(KalshiApiError):
            g.poll_fills(db_session, NOW - timedelta(hours=1))
    assert db_session.execute(select(func.count()).select_from(VenueStatus)).scalar() == 0


def test_an_unavailable_venue_is_not_routable_until_enabled(db_session):
    mark_status(db_session, "kalshi", "prod", "unavailable", "401", NOW)
    assert is_routable(db_session, "kalshi", "prod", NOW) is False
    assert enable_venue(db_session, "kalshi", "prod", NOW) is True
    assert is_routable(db_session, "kalshi", "prod", NOW) is True


def test_enable_reports_no_change_for_a_venue_that_was_already_ok(db_session):
    assert enable_venue(db_session, "kalshi", "prod", NOW) is False
    mark_status(db_session, "kalshi", "prod", "ok", None, NOW)
    assert enable_venue(db_session, "kalshi", "prod", NOW) is False


def test_a_freeze_expires_after_fifteen_minutes(db_session):
    freeze_market(db_session, "kalshi", "prod", "echo_mismatch", NOW)
    assert is_routable(db_session, "kalshi", "prod",
                       NOW + timedelta(minutes=FREEZE_MINUTES - 1)) is False
    assert is_routable(db_session, "kalshi", "prod",
                       NOW + timedelta(minutes=FREEZE_MINUTES + 1)) is True


def test_a_second_freeze_restarts_the_window(db_session):
    """A fresh incident extends the freeze; it does not run out on the first one's clock."""
    freeze_market(db_session, "kalshi", "prod", "echo_mismatch", NOW)
    freeze_market(db_session, "kalshi", "prod", "echo_mismatch",
                  NOW + timedelta(minutes=10))
    assert is_routable(db_session, "kalshi", "prod",
                       NOW + timedelta(minutes=FREEZE_MINUTES + 1)) is False
    assert is_routable(db_session, "kalshi", "prod",
                       NOW + timedelta(minutes=26)) is True


def test_an_unknown_status_is_not_routable(db_session):
    """Fail closed: a status this code does not understand is not a licence to send orders."""
    mark_status(db_session, "kalshi", "prod", "wedged", None, NOW)
    assert is_routable(db_session, "kalshi", "prod", NOW) is False


def test_paper_never_writes_a_venue_status_row(db_session, env_settings):
    Executor(env_settings, _factory(db_session)).step()
    assert db_session.execute(select(func.count()).select_from(VenueStatus)).scalar() == 0


def test_an_unavailable_venue_routes_nothing_new(db_session):
    t = FakeTransport(queued=[])
    mark_status(db_session, "kalshi", "prod", "unavailable", "401", NOW)
    with pytest.raises(VenueNotRoutable):
        _kalshi(t, db_session).place(db_session, _values(), None, _Market(CENT_RANGES), NOW)
    assert t.calls == []


def test_an_unavailable_venue_still_lets_a_cancel_out(db_session):
    """The outage stops new risk. Stranding a resting order would be the opposite of that."""
    t = FakeTransport(queued=[_ok({"order_id": "ov1", "reduced_by": "10.00"})])
    mark_status(db_session, "kalshi", "prod", "unavailable", "401", NOW)
    order_id = _open_order(db_session, venue_order_id="ov1")
    assert _kalshi(t, db_session).cancel(db_session, order_id, "outage", NOW) is True
    assert t.calls[0][0] == "DELETE"


def test_two_401s_at_the_gateway_mark_production_unavailable(db_session):
    t = FakeTransport(queued=[_err(401, code="unauthorized"), _err(401, code="unauthorized")])
    g = _kalshi(t, db_session)
    for _ in range(2):
        with pytest.raises(KalshiApiError):
            g.poll_fills(db_session, NOW - timedelta(hours=1))
    row = db_session.get(VenueStatus, ("kalshi", "prod"))
    assert row.status == "unavailable" and row.reason.startswith("401: ")
    assert is_routable(db_session, "kalshi", "prod", NOW) is False


def test_a_429_storm_at_the_gateway_never_marks_the_venue(db_session):
    t = FakeTransport(queued=[_err(429) for _ in range(6)])
    g = _kalshi(t, db_session)
    for _ in range(6):
        with pytest.raises(KalshiApiError):
            g.poll_fills(db_session, NOW - timedelta(hours=1))
    assert db_session.execute(select(func.count()).select_from(VenueStatus)).scalar() == 0


# --- three rejects freeze ------------------------------------------------------------------


def test_three_consecutive_rejects_on_one_order_freeze_the_market():
    t = RejectTracker()
    assert t.record_reject("o1") is False
    assert t.record_reject("o1") is False
    assert t.record_reject("o1") is True


def test_a_success_between_rejects_resets_the_count():
    t = RejectTracker()
    t.record_reject("o1")
    t.record_reject("o1")
    t.record_success("o1")
    assert t.record_reject("o1") is False


def test_rejects_are_counted_per_order():
    t = RejectTracker()
    t.record_reject("o1")
    t.record_reject("o2")
    assert t.record_reject("o1") is False


def test_the_tracker_forgets_an_order_once_it_has_fired():
    """The order is cancelled at the third, so its count has nothing left to describe."""
    t = RejectTracker()
    for _ in range(REJECTS_BEFORE_FREEZE):
        fired = t.record_reject("o1")
    assert fired is True
    assert t.record_reject("o1") is False


def test_a_third_reject_on_one_order_cancels_it_and_freezes_the_market(db_session):
    """The venue refuses the same amend three times: the order comes back and the venue is
    frozen for 15 minutes, so nothing new is routed while an operator looks."""
    t = FakeTransport(queued=[_err(400, code="bad_price"), _err(400, code="bad_price"),
                              _err(400, code="bad_price"),
                              _ok({"order_id": "ov1", "reduced_by": "10.00"})])
    g = _kalshi(t, db_session)
    order_id = _open_order(db_session, venue_order_id="ov1", client_order_id="c-rej")
    db_session.commit()      # the order was placed in an earlier, committed step
    market = _Market(CENT_RANGES)

    for _ in range(REJECTS_BEFORE_FREEZE):
        with pytest.raises(KalshiApiError):
            g.amend(db_session, order_id, Decimal("0.4400"), Decimal("10.00"), market, NOW)

    db_session.expire_all()
    assert db_session.get(Order, order_id).status == "cancelled"
    assert is_routable(db_session, "kalshi", "prod", NOW) is False
    assert is_routable(db_session, "kalshi", "prod",
                       NOW + timedelta(minutes=FREEZE_MINUTES + 1)) is True


def test_an_echo_mismatch_freezes_the_market_for_fifteen_minutes(db_session):
    """Task 7's writer cancels the order and raises; this task is the half that records it."""
    t = FakeTransport(queued=_accepted(price="0.9900") + [_ok({"order_id": "o1"})])
    g = _kalshi(t, db_session)
    values = _values(prob=Decimal("0.5600"), contracts=Decimal("10.00"))

    with pytest.raises(Exception):
        g.place(db_session, values, None, _Market(CENT_RANGES), NOW)

    row = db_session.get(VenueStatus, ("kalshi", "prod"))
    assert row.status == "frozen" and row.reason == "echo_mismatch"
    assert is_routable(db_session, "kalshi", "prod", NOW) is False


# --- startup reconciliation (section 9.1) --------------------------------------------------


def test_reconcile_cancels_a_resting_order_unknown_to_our_orders_table(db_session):
    t = FakeTransport(queued=[
        _ok({"orders": [_resting()], "cursor": ""}),
        _ok({"fills": [], "cursor": ""}),
        _ok({"market_positions": [], "cursor": ""}),
        _ok({"order_id": "ov9"})])
    report = _kalshi(t, db_session).reconcile(db_session, NOW)
    assert report.cancelled_unknown == 1
    assert t.calls[-1][0] == "DELETE"


def test_reconcile_asks_the_three_questions_in_order(db_session):
    t = FakeTransport(queued=[
        _ok({"orders": [], "cursor": ""}),
        _ok({"fills": [], "cursor": ""}),
        _ok({"market_positions": [], "cursor": ""})])
    report = _kalshi(t, db_session).reconcile(db_session, NOW)
    assert [c[1] for c in t.calls] == ["/portfolio/orders", "/portfolio/fills",
                                       "/portfolio/positions"]
    assert t.calls[0][2]["status"] == "resting"
    assert report == ReconcileReport()


def test_reconcile_cancels_a_resting_order_past_its_deadline(db_session):
    order_id = _open_order(db_session, client_order_id="c-old", venue_order_id="ov1",
                           expiry=NOW - timedelta(minutes=1))
    t = FakeTransport(queued=[
        _ok({"orders": [_resting(order_id="ov1", client_order_id="c-old")], "cursor": ""}),
        _ok({"fills": [], "cursor": ""}),
        _ok({"market_positions": [], "cursor": ""}),
        _ok({"order_id": "ov1"})])

    report = _kalshi(t, db_session).reconcile(db_session, NOW)

    assert report.cancelled_past_deadline == 1 and report.cancelled_unknown == 0
    assert t.calls[-1][0] == "DELETE"
    assert db_session.get(Order, order_id).status == "cancelled"


def test_reconcile_leaves_a_known_in_deadline_order_resting(db_session):
    order_id = _open_order(db_session, client_order_id="c-live", venue_order_id="ov1",
                           expiry=NOW + timedelta(hours=1))
    t = FakeTransport(queued=[
        _ok({"orders": [_resting(order_id="ov1", client_order_id="c-live")], "cursor": ""}),
        _ok({"fills": [], "cursor": ""}),
        _ok({"market_positions": [], "cursor": ""})])

    report = _kalshi(t, db_session).reconcile(db_session, NOW)

    assert report.cancelled_unknown == 0 and report.cancelled_past_deadline == 0
    assert report.resting == 1
    assert not any(c[0] == "DELETE" for c in t.calls)
    assert db_session.get(Order, order_id).status == "open"


def test_reconcile_rebuilds_positions(db_session):
    t = FakeTransport(queued=[
        _ok({"orders": [], "cursor": ""}),
        _ok({"fills": [], "cursor": ""}),
        _ok({"market_positions": [{"ticker": "A", "position": "5.00"},
                                  {"ticker": "B", "position": "-2.00"},
                                  {"ticker": "C", "position": "0"}], "cursor": ""})])
    g = _kalshi(t, db_session)

    report = g.reconcile(db_session, NOW)

    assert report.positions_rebuilt == 2
    assert g.positions == {"A": Decimal("5.00"), "B": Decimal("-2.00")}


def test_reconcile_counts_the_fills_it_read(db_session):
    t = FakeTransport(queued=[
        _ok({"orders": [], "cursor": ""}),
        _ok({"fills": [{"trade_id": "t1", "order_id": "ov1", "ticker": "T",
                        "outcome_side": "yes", "price": "0.5600", "count": "2.00"}],
             "cursor": ""}),
        _ok({"market_positions": [], "cursor": ""})])
    assert _kalshi(t, db_session).reconcile(db_session, NOW).fills_seen == 1


def test_reconcile_adopts_the_order_group_the_venue_already_holds(db_session):
    """No group is created here: creating one is a message, and reconciliation asks questions.
    A group the resting orders already carry is adopted so `cancel_all` can pull them back."""
    _open_order(db_session, client_order_id="c-live", venue_order_id="ov1",
                order_group_id="g-old")
    t = FakeTransport(queued=[
        _ok({"orders": [_resting(order_id="ov1", client_order_id="c-live",
                                 order_group_id="g-old")], "cursor": ""}),
        _ok({"fills": [], "cursor": ""}),
        _ok({"market_positions": [], "cursor": ""})])
    g = _kalshi(t, db_session, order_group_id=None)

    g.reconcile(db_session, NOW)

    assert g.order_group_id == "g-old"
    assert not any(c[0] == "POST" for c in t.calls)


def test_a_timeout_after_send_is_resolved_by_client_order_id_not_a_resend(db_session):
    t = FakeTransport(queued=[
        httpx.ReadTimeout("t"),
        _ok({"orders": [_resting(order_id="ov1", client_order_id="c-uuid",
                                 order_group_id="g1")], "cursor": ""})])
    g = _kalshi(t, db_session)

    placed = g.place(db_session, _values(client_order_id="c-uuid"), None,
                     _Market(CENT_RANGES), NOW)

    assert placed.venue_order_id == "ov1"
    assert sum(1 for c in t.calls if c[0] == "POST") == 1     # never resent
    assert t.calls[1][:2] == ("GET", "/portfolio/orders")
    assert db_session.get(Order, placed.order_id).venue_order_id == "ov1"


def test_a_timeout_whose_lookup_finds_nothing_reports_not_placed(db_session):
    t = FakeTransport(queued=[httpx.ReadTimeout("t"), _ok({"orders": [], "cursor": ""})])
    g = _kalshi(t, db_session)
    with pytest.raises(OrderNotPlaced):
        g.place(db_session, _values(client_order_id="c-uuid"), None, _Market(CENT_RANGES), NOW)
    assert sum(1 for c in t.calls if c[0] == "POST") == 1
    assert db_session.execute(select(func.count()).select_from(Order)).scalar() == 0


def test_a_timeout_lookup_that_finds_someone_elses_order_reports_not_placed(db_session):
    """The filter is on our own `client_order_id`, so another resting order is not ours."""
    t = FakeTransport(queued=[
        httpx.ReadTimeout("t"),
        _ok({"orders": [_resting(order_id="ov2", client_order_id="not-ours")], "cursor": ""})])
    with pytest.raises(OrderNotPlaced):
        _kalshi(t, db_session).place(db_session, _values(client_order_id="c-uuid"), None,
                         _Market(CENT_RANGES), NOW)


# --- no reprice while the book is dirty (section 2.2, live only) ---------------------------


def test_a_dirty_book_blocks_a_reprice():
    assert may_reprice(_book(dirty=True, as_of=NOW), NOW, EXEC_SETTINGS) is False


def test_a_stale_book_blocks_a_reprice():
    book = _book(dirty=False,
                 as_of=NOW - timedelta(seconds=EXEC_SETTINGS.book_max_age_s + 1))
    assert may_reprice(book, NOW, EXEC_SETTINGS) is False


def test_an_absent_book_blocks_a_reprice():
    assert may_reprice(None, NOW, EXEC_SETTINGS) is False


def test_a_clean_fresh_book_allows_a_reprice():
    assert may_reprice(_book(dirty=False, as_of=NOW), NOW, EXEC_SETTINGS) is True


def test_a_rebuilt_book_allows_repricing_again():
    book = _book(dirty=True, as_of=NOW)
    assert may_reprice(book, NOW, EXEC_SETTINGS) is False
    rebuilt = _book_from_rest_snapshot(NOW)
    assert may_reprice(rebuilt, NOW, EXEC_SETTINGS) is True


def test_thirty_seconds_of_websocket_silence_blocks_a_reprice():
    """Section 2.2's other half. The caller passes the newest tape timestamp it has; the book
    itself can still look fresh when the socket has gone quiet."""
    fresh = _book(dirty=False, as_of=NOW)
    assert may_reprice(fresh, NOW, EXEC_SETTINGS,
                       ws_last_event_at=NOW - timedelta(seconds=31)) is False
    assert may_reprice(fresh, NOW, EXEC_SETTINGS,
                       ws_last_event_at=NOW - timedelta(seconds=29)) is True


def test_a_rest_book_is_not_judged_on_websocket_silence():
    """The REST rebuild is exactly the remedy for a silent socket, so a book that came from
    one is not blocked by the silence that caused it."""
    assert may_reprice(_book_from_rest_snapshot(NOW), NOW, EXEC_SETTINGS,
                       ws_last_event_at=NOW - timedelta(minutes=5)) is True


def test_the_kalshi_gateway_skips_an_amend_on_a_dirty_book(db_session):
    t = FakeTransport()
    g = _kalshi(t, db_session)
    order_id = _open_order(db_session, venue_order_id="ov1")
    assert g.amend(db_session, order_id, Decimal("0.5700"), Decimal("2.00"),
                   _Market(CENT_RANGES), NOW, book=_book(dirty=True, as_of=NOW)) is False
    assert t.calls == []


def test_the_kalshi_gateway_reads_the_book_off_the_market_when_given_one(db_session):
    """`MarketNow` carries the book the loop is already holding, so a caller that forgets the
    keyword is still gated."""
    t = FakeTransport()
    g = _kalshi(t, db_session)
    order_id = _open_order(db_session, venue_order_id="ov1")
    market = _MarketWithBook(_book(dirty=True, as_of=NOW), CENT_RANGES)
    assert g.amend(db_session, order_id, Decimal("0.5700"), Decimal("2.00"),
                   market, NOW) is False
    assert t.calls == []


def test_the_kalshi_gateway_amends_on_a_clean_book(db_session):
    t = FakeTransport(queued=_accepted(price="0.5700"))
    g = _kalshi(t, db_session)
    order_id = _open_order(db_session, venue_order_id="ov1", client_order_id="c-clean")
    market = _MarketWithBook(_book(dirty=False, as_of=NOW), CENT_RANGES)
    assert g.amend(db_session, order_id, Decimal("0.5700"), Decimal("10.00"),
                   market, NOW) is True
    assert [c[0] for c in t.calls] == ["POST", "GET"]


def test_the_paper_path_has_no_reprice_gate():
    """Live only. Phase 3 reprices by cancel + place and `PaperGateway.amend` still refuses,
    with or without a book: the golden replay in tests/test_gateway.py is the byte-for-byte
    evidence that the paper write path did not move."""
    g = PaperGateway()
    with pytest.raises(NotImplementedError):
        g.amend(None, 1, Decimal("0.5"), Decimal("1"), None, NOW)
    assert not hasattr(g, "_outage")


# --- the message budget trips the kill switch (section 9.2) --------------------------------


def _empty_bucket_writer(transport):
    w = _writer(transport)
    w._bucket = TokenBucket(0)      # every take() is a breach
    return w


def test_a_budget_breach_trips_the_kill_switch(db_session):
    t = FakeTransport()
    g = KalshiGateway(_empty_bucket_writer(t), KalshiReader(t), order_group_id="g1")
    with pytest.raises(MessageBudgetExceeded):
        g.place(db_session, _values(), None, _Market(CENT_RANGES), NOW)
    assert db_session.execute(select(KillSwitch.active)).scalar() is True
    assert t.calls == []


def test_the_kill_switch_row_carries_a_reason(db_session):
    t = FakeTransport()
    g = KalshiGateway(_empty_bucket_writer(t), KalshiReader(t), order_group_id="g1")
    with pytest.raises(MessageBudgetExceeded):
        g.place(db_session, _values(), None, _Market(CENT_RANGES), NOW)
    row = db_session.execute(select(KillSwitch)).scalar_one()
    assert row.active is True and "budget" in row.reason and row.set_at == NOW


# --- the version pin -----------------------------------------------------------------------


def test_executor_version_is_bumped_for_venue_state():
    assert EXECUTOR_VERSION == "4.3"


# --- fix round 1: venue state outlives the exception that wrote it -------------------------
#
# `Executor._apply` runs every action inside `session.begin_nested()` and catches the exception
# outside it, so a row written on the caller's session and then followed by a `raise` is rolled
# back with the savepoint. These tests reproduce that savepoint exactly and then read the state
# on a session that shares nothing with the test's own.


def test_an_outage_mark_survives_the_callers_savepoint(db_session):
    t = FakeTransport(queued=[_err(401, code="unauthorized"), _err(401, code="unauthorized")])
    g = _kalshi(t, db_session)

    for _ in range(2):
        with pytest.raises(KalshiApiError):
            with db_session.begin_nested():
                g.poll_fills(db_session, NOW - timedelta(hours=1), NOW)

    with _fresh(db_session) as fresh:
        row = fresh.get(VenueStatus, ("kalshi", "prod"))
        assert row is not None and row.status == "unavailable"
        assert is_routable(fresh, "kalshi", "prod", NOW) is False


def test_an_echo_mismatch_freeze_survives_the_callers_savepoint(db_session):
    t = FakeTransport(queued=_accepted(price="0.9900") + [_ok({"order_id": "o1"})])
    g = _kalshi(t, db_session)

    with pytest.raises(Exception):
        with db_session.begin_nested():
            g.place(db_session, _values(), None, _Market(CENT_RANGES), NOW)

    with _fresh(db_session) as fresh:
        row = fresh.get(VenueStatus, ("kalshi", "prod"))
        assert row is not None and (row.status, row.reason) == ("frozen", "echo_mismatch")


def test_the_kill_switch_trip_survives_the_callers_savepoint(db_session):
    t = FakeTransport()
    g = KalshiGateway(_empty_bucket_writer(t), KalshiReader(t), _factory(db_session),
                      order_group_id="g1", clock=lambda: NOW)

    with pytest.raises(MessageBudgetExceeded):
        with db_session.begin_nested():
            g.place(db_session, _values(), None, _Market(CENT_RANGES), NOW)

    with _fresh(db_session) as fresh:
        assert fresh.execute(select(KillSwitch.active)).scalar() is True


def test_the_reject_cancel_and_freeze_survive_the_callers_savepoint(db_session):
    t = FakeTransport(queued=[_err(400, code="bad_price"), _err(400, code="bad_price"),
                              _err(400, code="bad_price"),
                              _ok({"order_id": "ov1", "reduced_by": "10.00"})])
    g = _kalshi(t, db_session)
    order_id = _open_order(db_session, venue_order_id="ov1", client_order_id="c-sp")
    db_session.commit()

    for _ in range(REJECTS_BEFORE_FREEZE):
        with pytest.raises(KalshiApiError):
            with db_session.begin_nested():
                g.amend(db_session, order_id, Decimal("0.4400"), Decimal("10.00"),
                        _Market(CENT_RANGES), NOW)

    with _fresh(db_session) as fresh:
        assert fresh.get(Order, order_id).status == "cancelled"
        assert is_routable(fresh, "kalshi", "prod", NOW) is False


def test_without_a_session_factory_the_write_still_happens_on_the_callers_session(db_session):
    """The documented fallback. A write that might be rolled back beats no write at all, and
    `poll_fills` is one caller that is not inside a savepoint."""
    t = FakeTransport(queued=[_err(401), _err(401)])
    g = KalshiGateway(_writer(t), KalshiReader(t), order_group_id="g1", clock=lambda: NOW)
    for _ in range(2):
        with pytest.raises(KalshiApiError):
            g.poll_fills(db_session, NOW - timedelta(hours=1), NOW)
    assert read_status(db_session, "kalshi", "prod") == "unavailable"


# --- fix round 1: a frozen venue blocks reprices too ---------------------------------------


def test_a_frozen_venue_blocks_an_amend(db_session):
    """Section 9.4's "nothing new is routed" covers a reprice: it is new risk at a new price,
    and the reject freeze exists precisely to stop the activity that produced the rejects."""
    t = FakeTransport()
    freeze_market(db_session, "kalshi", "prod", "echo_mismatch", NOW)
    order_id = _open_order(db_session, venue_order_id="ov1")
    with pytest.raises(VenueNotRoutable):
        _kalshi(t, db_session).amend(db_session, order_id, Decimal("0.5700"),
                                     Decimal("10.00"), _Market(CENT_RANGES), NOW)
    assert t.calls == []


def test_an_unavailable_venue_blocks_an_amend(db_session):
    t = FakeTransport()
    mark_status(db_session, "kalshi", "prod", "unavailable", "401", NOW)
    order_id = _open_order(db_session, venue_order_id="ov1")
    with pytest.raises(VenueNotRoutable):
        _kalshi(t, db_session).amend(db_session, order_id, Decimal("0.5700"),
                                     Decimal("10.00"), _Market(CENT_RANGES), NOW)
    assert t.calls == []


def test_an_amend_is_allowed_again_once_the_freeze_expires(db_session):
    t = FakeTransport(queued=_accepted(price="0.5700"))
    freeze_market(db_session, "kalshi", "prod", "echo_mismatch", NOW)
    order_id = _open_order(db_session, venue_order_id="ov1", client_order_id="c-thaw")
    later = NOW + timedelta(minutes=FREEZE_MINUTES + 1)
    assert _kalshi(t, db_session).amend(db_session, order_id, Decimal("0.5700"),
                                        Decimal("10.00"), _MarketWithBook(
                                            _book(as_of=later), CENT_RANGES), later) is True


# --- fix round 1: the 30 s ping rule is live ------------------------------------------------


def test_the_loop_hands_the_gateway_its_tape_position(env_settings, db_session):
    """One call site: the loop already computes `ws_last_event_at` for the heartbeat, and the
    gateway needs it for section 2.2's silence test. `PaperGateway` discards it."""
    seen = []

    class _Spy(PaperGateway):
        def observe_tape(self, ws_last_event_at):
            seen.append(ws_last_event_at)

    Executor(env_settings, _factory(db_session), gateway=_Spy()).step()
    assert len(seen) == 1


def test_websocket_silence_blocks_a_live_reprice_and_a_fresh_event_releases_it(db_session):
    t = FakeTransport(queued=_accepted(price="0.5700"))
    g = _kalshi(t, db_session)
    order_id = _open_order(db_session, venue_order_id="ov1", client_order_id="c-ws")
    market = _MarketWithBook(_book(dirty=False, as_of=NOW), CENT_RANGES)

    g.observe_tape(NOW - timedelta(seconds=31))
    assert g.amend(db_session, order_id, Decimal("0.5700"), Decimal("10.00"),
                   market, NOW) is False
    assert t.calls == []

    g.observe_tape(NOW)
    assert g.amend(db_session, order_id, Decimal("0.5700"), Decimal("10.00"),
                   market, NOW) is True
    assert [c[0] for c in t.calls] == ["POST", "GET"]


def test_a_gateway_that_has_seen_no_tape_yet_does_not_block_every_reprice(db_session):
    """None reads as "no claim", not as "silent forever": a gateway before its first step must
    not be permanently unable to reprice."""
    t = FakeTransport(queued=_accepted(price="0.5700"))
    g = _kalshi(t, db_session)
    order_id = _open_order(db_session, venue_order_id="ov1", client_order_id="c-none")
    assert g.amend(db_session, order_id, Decimal("0.5700"), Decimal("10.00"),
                   _MarketWithBook(_book(as_of=NOW), CENT_RANGES), NOW) is True


# --- fix round 1: the remaining minors ------------------------------------------------------


def test_enabling_the_venue_resets_the_gateways_outage_counter(db_session):
    """Without the reset the counter is still latched, so the very next single 401 would
    re-mark a venue an operator has just cleared -- one auth failure, not two."""
    t = FakeTransport(queued=[_err(401), _err(401), _err(401)])
    g = _kalshi(t, db_session)
    for _ in range(2):
        with pytest.raises(KalshiApiError):
            g.poll_fills(db_session, NOW - timedelta(hours=1), NOW)
    assert g.enable(db_session, NOW) is True
    assert is_routable(db_session, "kalshi", "prod", NOW) is True

    with pytest.raises(KalshiApiError):
        g.poll_fills(db_session, NOW - timedelta(hours=1), NOW)
    assert is_routable(db_session, "kalshi", "prod", NOW) is True


def test_a_freeze_window_the_table_cannot_record_is_refused():
    """`venue_status` holds one window length. A caller asking for another is refused rather
    than silently given fifteen minutes, which is what keeps Task 7's `ECHO_FREEZE_MINUTES` and
    this module's `FREEZE_MINUTES` checked against each other instead of assumed equal."""
    assert ECHO_FREEZE_MINUTES == FREEZE_MINUTES
    with pytest.raises(FreezeWindowMismatch):
        freeze_market(None, "kalshi", "prod", "echo_mismatch", NOW, minutes=30)


def test_a_send_timeout_in_the_production_shape_is_resolved_by_client_order_id(db_session):
    """`KalshiTransport` never lets an httpx exception out: it wraps every one into
    `VenueTransportError`, because an httpx exception's `.request` carries the live signed
    headers. That is the shape this branch sees in production."""
    t = FakeTransport(queued=[
        VenueTransportError("POST", ORDERS_PATH, "ReadTimeout"),
        _ok({"orders": [_resting(order_id="ov7", client_order_id="c-prod",
                                 order_group_id="g1")], "cursor": ""})])
    g = _kalshi(t, db_session)

    placed = g.place(db_session, _values(client_order_id="c-prod"), None,
                     _Market(CENT_RANGES), NOW)

    assert placed.venue_order_id == "ov7"
    assert sum(1 for c in t.calls if c[0] == "POST") == 1


def test_poll_fills_marks_with_the_callers_clock(db_session):
    """A mark written from a fill poll must carry the loop's instant, not wall-clock time."""
    t = FakeTransport(queued=[_err(401), _err(401)])
    g = _kalshi(t, db_session, clock=lambda: NOW + timedelta(days=99))
    for _ in range(2):
        with pytest.raises(KalshiApiError):
            g.poll_fills(db_session, NOW - timedelta(hours=1), NOW)
    assert db_session.get(VenueStatus, ("kalshi", "prod")).since == NOW
