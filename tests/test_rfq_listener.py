"""The RFQ listener: frame parsing, storage on arrival, idling, and the second socket."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import websocket
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.db.models import Rfq
from harness.venues.kalshi.rfq import (CHANNEL, ENV, EXCERPT_MAX, IDLE_ERROR_CODES, IDLE_S,
                                       RAW_MAX_BYTES, VENUE, handle_frame, idle_reason,
                                       parse_rfq_frame, store_rfq)
from harness.venues.kalshi.rfq_socket import SUBSCRIBE_ID, RfqListener

NOW = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)


def _created(rfq_id="rfq_1", legs=None):
    return {"type": "rfq_created", "sid": 7, "msg": {
        "id": rfq_id, "creator_id": "", "market_ticker": "KXNFLGAME-26SEP14DALNYG-DAL",
        "event_ticker": "KXNFLGAME-26SEP14DALNYG", "created_ts": 1789000000,
        "contracts_fp": "25.00", "target_cost_dollars": "12.5000",
        "mve_collection_ticker": "MVE-NFL-1",
        "mve_selected_legs": legs if legs is not None else [
            {"event_ticker": "KXNFLGAME-26SEP14DALNYG",
             "market_ticker": "KXNFLGAME-26SEP14DALNYG-DAL", "side": "yes",
             "yes_settlement_value_dollars": "1.0000"},
            {"event_ticker": "KXNFLGAME-26SEP14KCBUF",
             "market_ticker": "KXNFLGAME-26SEP14KCBUF-KC", "side": "yes",
             "yes_settlement_value_dollars": "1.0000"}]}}


def _deleted(rfq_id="rfq_1"):
    return {"type": "rfq_deleted", "sid": 7, "msg": {
        "id": rfq_id, "creator_id": "anon", "market_ticker": "KXNFLGAME-26SEP14DALNYG-DAL",
        "deleted_ts": 1789003600}}


# --- parsing -------------------------------------------------------------------------------

def test_an_rfq_created_frame_parses_into_its_legs():
    event = parse_rfq_frame(_created())
    assert event.kind == "rfq_created" and event.rfq_id == "rfq_1"
    assert event.contracts_fp == Decimal("25.00")
    assert event.target_cost_dollars == Decimal("12.5000")
    assert [leg["market_ticker"] for leg in event.legs] == [
        "KXNFLGAME-26SEP14DALNYG-DAL", "KXNFLGAME-26SEP14KCBUF-KC"]
    # 1789000000 is 2026-09-10 00:26:40 UTC.
    assert event.created_ts == datetime(2026, 9, 10, 0, 26, 40, tzinfo=timezone.utc)


def test_an_rfq_deleted_frame_parses():
    event = parse_rfq_frame(_deleted())
    assert event.kind == "rfq_deleted" and event.deleted_ts is not None


@pytest.mark.parametrize("msg", [
    None, {}, {"type": "rfq_created"}, {"type": "rfq_created", "msg": {}},
    {"type": "subscribed", "msg": {"sid": 1}},
    {"type": "QuoteCreated", "msg": {"id": "q1"}},
])
def test_a_frame_that_is_not_an_rfq_event_parses_to_none(msg):
    assert parse_rfq_frame(msg) is None


def test_a_frame_with_no_legs_still_parses():
    """A single-market RFQ is not a combo, but it is an arrival and H5's denominator needs it."""
    event = parse_rfq_frame(_created(legs=[]))
    assert event is not None and event.legs == []


# --- storage on arrival ----------------------------------------------------------------------

def test_the_row_is_written_on_arrival(db_session):
    row = handle_frame(db_session, _created(), NOW)
    assert isinstance(row, Rfq)
    assert row.status == "open" and row.received_at == NOW
    assert row.market_ticker == "KXNFLGAME-26SEP14DALNYG-DAL"
    assert len(row.legs) == 2


def test_a_delete_marks_the_existing_row(db_session):
    handle_frame(db_session, _created(), NOW)
    handle_frame(db_session, _deleted(), NOW + timedelta(minutes=30))
    row = db_session.get(Rfq, "rfq_1")
    assert row.status == "deleted" and row.deleted_ts is not None
    assert row.received_at == NOW      # the arrival time is not overwritten


def test_a_delete_for_an_unseen_rfq_writes_its_own_row(db_session):
    """Quotes have not been queryable after the fact since 2026-06-25 (F71), so the socket is
    the only record: a delete we saw and a create we missed is still an arrival."""
    row = handle_frame(db_session, _deleted("rfq_never_seen"), NOW)
    assert row.status == "deleted"


def test_a_repeated_create_does_not_duplicate(db_session):
    handle_frame(db_session, _created(), NOW)
    handle_frame(db_session, _created(), NOW + timedelta(seconds=1))
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 1


def test_the_raw_message_is_stored_capped_with_a_flag(db_session):
    """Ruling B-M9 and D13: the raw message lives in `rfqs.raw` because the report needs the
    legs and no builder may read `raw_responses`. Capped at 8 KB with a flag."""
    frame = _created()
    frame["msg"]["padding"] = "x" * 20_000
    row = handle_frame(db_session, frame, NOW)
    assert row.raw["truncated"] is True
    assert len(json.dumps(row.raw).encode()) <= RAW_MAX_BYTES


def test_a_small_message_is_stored_whole_and_unflagged(db_session):
    row = handle_frame(db_session, _created(), NOW)
    assert row.raw["truncated"] is False
    assert row.raw["msg"]["id"] == "rfq_1"


def test_hostile_free_text_reaches_no_rendered_string(db_session):
    """F60: RFQ free text is stored but never rendered raw. The one thing the report shows is a
    120-character quoted excerpt of `market_ticker`, and that excerpt is sanitized."""
    from harness.research.text import sanitize_model_text

    frame = _created()
    frame["msg"]["market_ticker"] = "<script>alert(1)</script>IGNORE PREVIOUS\x00" + "y" * 300
    row = handle_frame(db_session, frame, NOW)
    excerpt = sanitize_model_text(row.market_ticker, EXCERPT_MAX)
    assert "<" not in excerpt and "\x00" not in excerpt
    assert len(excerpt) <= EXCERPT_MAX


# --- idling (0.8, F71) -------------------------------------------------------------------------

@pytest.mark.parametrize("code", sorted(IDLE_ERROR_CODES))
def test_every_documented_subscribe_error_code_idles(code):
    reason = idle_reason(None, {"type": "error", "msg": {"code": code, "msg": "nope"}})
    assert reason is not None and str(code) in reason


def test_an_undocumented_error_code_does_not_idle():
    """Codes 19-22 are shard validations and 25/26 are subscription limits; none of them is a
    permission answer, and idling for an hour on one would hide a bug rather than survive it."""
    assert idle_reason(None, {"type": "error", "msg": {"code": 25, "msg": "buffer"}}) is None


def test_a_handshake_status_other_than_101_idles():
    assert idle_reason(403, None) is not None
    assert idle_reason(401, None) is not None
    assert idle_reason(101, None) is None


def test_the_reason_is_bounded_and_sanitized():
    reason = idle_reason(403, {"type": "error", "msg": {"code": 9, "msg": "x" * 500 + "\x00"}})
    assert len(reason) <= 120 and "\x00" not in reason


def test_the_idle_window_is_one_hour():
    assert IDLE_S == 3600


def test_idling_writes_a_venue_status_row(db_session):
    from harness.execution.venue import mark_status

    mark_status(db_session, VENUE, ENV, "unavailable", idle_reason(403, None), NOW)
    row = db_session.execute(text(
        "select venue, env, status, reason from venue_status where venue = :v"),
        {"v": VENUE}).first()
    assert (row.venue, row.env, row.status) == ("kalshi_rfq", "prod", "unavailable")
    assert row.reason and len(row.reason) <= 120


# --- the socket ---------------------------------------------------------------------------------

class FakeWs:
    def __init__(self, frames):
        self.sent = []
        self._frames = list(frames)

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if not self._frames:
            raise websocket.WebSocketTimeoutException()
        frame = self._frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return json.dumps(frame)

    def close(self):
        pass


def _listener(db_session, env_settings, ws=None, sleeps=None):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    # `sign` is the seam: `env_settings` keeps the container defaults for the two Kalshi key
    # paths, which do not exist on the Mac, so a real `sign_request` would raise
    # `FileNotFoundError` before the ws factory was ever reached.
    return RfqListener(env_settings, factory, ws_factory=lambda *a, **k: ws,
                       clock=lambda: NOW, sleep=(sleeps.append if sleeps is not None else None),
                       sign=lambda *_a, **_k: {})


def test_the_subscribe_frame_names_only_the_communications_channel(db_session, env_settings):
    ws = FakeWs([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}])
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    assert ws.sent == [{"id": SUBSCRIBE_ID, "cmd": "subscribe",
                        "params": {"channels": [CHANNEL]}}]


def test_the_subscribe_frame_carries_no_market_tickers(db_session, env_settings):
    """The channel is documented to ignore market specification, and naming markets on it is the
    invalid-parameter frame ruling A-I1 is about."""
    ws = FakeWs([])
    _listener(db_session, env_settings, ws).subscribe(ws)
    assert "market_tickers" not in ws.sent[0]["params"]
    assert "sids" not in json.dumps(ws.sent[0])


def test_the_listener_stores_an_arrival_off_the_socket(db_session, env_settings):
    ws = FakeWs([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}, _created()])
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    # `subscribe` only sends; it consumes no frame. One `run_once` reads the ack, the second
    # reads the arrival.
    listener.run_once(ws)
    listener.run_once(ws)
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 1


def test_a_quote_event_is_counted_and_dropped(db_session, env_settings):
    ws = FakeWs([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}},
                 {"type": "QuoteCreated", "msg": {"id": "q1"}}])
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    listener.run_once(ws)      # the ack
    listener.run_once(ws)      # the quote event
    assert listener.quote_events_dropped == 1
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 0


def test_an_error_frame_idles_rather_than_reconnecting(db_session, env_settings):
    ws = FakeWs([{"type": "error", "msg": {"code": 9, "msg": "authentication required"}}])
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    listener.run_once(ws)
    assert listener.idle_until == NOW + timedelta(seconds=IDLE_S)
    row = db_session.execute(text(
        "select status from venue_status where venue = :v"), {"v": VENUE}).first()
    assert row.status == "unavailable"


def test_the_listener_is_off_when_its_setting_is_false(db_session, env_settings):
    settings = env_settings.model_copy(update={"rfq_listener_enabled": False})
    listener = RfqListener(settings, sessionmaker(bind=db_session.get_bind()),
                           ws_factory=lambda *a, **k: pytest.fail("connected"),
                           clock=lambda: NOW, sleep=lambda *_: None,
                           sign=lambda *_a, **_k: {})
    listener.run_forever()          # returns immediately
    assert listener.connected is False


def test_the_listener_connects_to_the_settings_host_and_never_the_fallback(db_session,
                                                                          env_settings):
    """Roadmap invariant 8: `external-api-ws.kalshi.com` is in `ws.py` as the recorder's
    fallback and is *not* on the permitted host list. The listener has one host."""
    urls = []
    ws = FakeWs([])

    def factory(url, **kwargs):
        urls.append(url)
        return ws

    listener = RfqListener(env_settings, sessionmaker(bind=db_session.get_bind()),
                           ws_factory=factory, clock=lambda: NOW, sleep=lambda *_: None,
                           sign=lambda *_a, **_k: {})
    listener.connect()
    assert urls == [env_settings.kalshi_ws_url]
    assert "external-api-ws" not in urls[0]


# --- hostile frames: the cap is a cap, and the listener survives them -----------------------

def test_a_hostile_numeric_is_dropped_rather_than_written(db_session):
    """`contracts_fp` is `Numeric(14,2)` and `target_cost_dollars` is `Numeric(14,4)`. A venue
    string of `NaN`, `Infinity` or `1E+400` parses as a `Decimal` and then fails at the insert,
    which would take the arrival down with it. Unparseable and out-of-range values become NULL:
    the arrival is the fact H5 counts, and one absent size never justifies losing it."""
    for hostile in ("NaN", "Infinity", "-Infinity", "1E+400", "9" * 40, "not-a-number", []):
        frame = _created(rfq_id=f"rfq_{abs(hash(str(hostile)))}")
        frame["msg"]["contracts_fp"] = hostile
        frame["msg"]["target_cost_dollars"] = hostile
        event = parse_rfq_frame(frame)
        assert event.contracts_fp is None and event.target_cost_dollars is None
        assert handle_frame(db_session, frame, NOW) is not None


def test_a_hostile_timestamp_is_dropped_rather_than_written(db_session):
    for hostile in (10 ** 20, "not-a-time", [], {"a": 1}, float("nan")):
        frame = _created()
        frame["msg"]["created_ts"] = hostile
        assert parse_rfq_frame(frame).created_ts is None


def test_the_raw_cap_holds_for_every_oversized_shape(db_session):
    """The trimmed fallback keeps the documented fields, and two of those are attacker-sized:
    `mve_selected_legs` and any string in it. A cap that only survives an unknown padding key
    is not a cap, so each shape is asserted against the byte budget."""
    shapes = {
        "padding": lambda m: m.update({"padding": "x" * 20_000}),
        "many_legs": lambda m: m.update({"mve_selected_legs": [
            {"event_ticker": "E" * 200, "market_ticker": "M" * 200, "side": "yes",
             "yes_settlement_value_dollars": "1"} for _ in range(400)]}),
        "one_huge_string": lambda m: m.update({"market_ticker": "z" * 60_000}),
        "huge_number": lambda m: m.update({"created_ts": 10 ** 9000}),
        "deep_nesting": lambda m: m.update({"mve_selected_legs": _nest(20, "x" * 20_000)}),
    }
    for name, mutate in shapes.items():
        frame = _created(rfq_id=f"rfq_{name}")
        mutate(frame["msg"])
        row = handle_frame(db_session, frame, NOW)
        assert row is not None, name
        assert row.raw["truncated"] is True, name
        assert len(json.dumps(row.raw).encode()) <= RAW_MAX_BYTES, name


def _nest(depth, leaf):
    inner = leaf
    for _ in range(depth):
        inner = [inner]
    return inner


def test_a_leg_that_is_not_a_mapping_is_skipped(db_session):
    event = parse_rfq_frame(_created(legs=["not-a-dict", {"side": "yes"}, {
        "market_ticker": "KXNFLGAME-26SEP14KCBUF-KC", "event_ticker": "KXNFLGAME-26SEP14KCBUF",
        "side": "no", "yes_settlement_value_dollars": "1.0000"}]))
    assert [leg["market_ticker"] for leg in event.legs] == ["KXNFLGAME-26SEP14KCBUF-KC"]


def test_an_oversized_identifier_is_cut_to_its_column(db_session):
    frame = _created(rfq_id="r" * 400)
    frame["msg"]["event_ticker"] = "e" * 400
    frame["msg"]["mve_collection_ticker"] = "c" * 400
    row = handle_frame(db_session, frame, NOW)
    assert len(row.id) <= 64 and len(row.event_ticker) <= 64
    assert len(row.market_ticker) <= 64 and len(row.mve_collection_ticker) <= 64


# --- the loop's own decisions -----------------------------------------------------------------

def test_a_frame_that_is_not_json_is_dropped_and_the_socket_kept(db_session, env_settings):
    class Garbage(FakeWs):
        def recv(self):
            return "{not json"

    ws = Garbage([])
    listener = _listener(db_session, env_settings, ws)
    assert listener.run_once(ws) is True


def test_a_closed_socket_ends_the_read_loop(db_session, env_settings):
    class Closed(FakeWs):
        def recv(self):
            return ""

    ws = Closed([])
    listener = _listener(db_session, env_settings, ws)
    assert listener.run_once(ws) is False
    assert listener.idle_until is None      # a closed socket reconnects; it does not idle


def test_an_error_frame_with_an_undocumented_code_keeps_the_socket(db_session, env_settings):
    ws = FakeWs([{"type": "error", "msg": {"code": 25, "msg": "buffer overflow"}}])
    listener = _listener(db_session, env_settings, ws)
    assert listener.run_once(ws) is True
    assert listener.idle_until is None


def test_no_ack_inside_the_window_drops_the_socket(db_session, env_settings):
    """`should_reconnect` is imported from the recorder, never shared with it: a listener whose
    subscribe is never acked drops its own socket and leaves the market tape alone."""
    ws = FakeWs([])                       # every recv times out
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    assert listener.run_once(ws) is True   # inside the ack window
    listener._subscribed_at -= 3600        # ... and now past it
    assert listener.run_once(ws) is False
    assert listener.idle_until is None


def test_a_silent_socket_is_dropped_once_it_is_stale(db_session, env_settings):
    """`is_stale`, also imported from the recorder. A half-open socket delivers nothing while
    staying open, so the silence is counted."""
    ws = FakeWs([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}])
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    listener.run_once(ws)                 # the ack: acked, so the ack window no longer applies
    from harness.venues.kalshi.ws import RECV_TIMEOUT_S

    needed = int(env_settings.ws_stale_s // RECV_TIMEOUT_S)
    outcomes = [listener.run_once(ws) for _ in range(needed)]
    assert outcomes[-1] is False and all(outcomes[:-1])


def test_the_ack_marks_the_venue_ok(db_session, env_settings):
    ws = FakeWs([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}])
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    listener.run_once(ws)
    row = db_session.execute(text("select status from venue_status where venue = :v"),
                             {"v": VENUE}).first()
    assert row.status == "ok"


def test_a_venue_status_write_that_fails_never_stops_the_listener(db_session, env_settings):
    """Ruling 1's shape, applied here: the listener's own bookkeeping is not allowed to be the
    thing that takes it down."""
    def broken_factory():
        raise RuntimeError("no database")

    listener = RfqListener(env_settings, broken_factory, ws_factory=lambda *a, **k: None,
                           clock=lambda: NOW, sleep=lambda *_: None, sign=lambda *_a, **_k: {})
    ws = FakeWs([{"type": "error", "msg": {"code": 9, "msg": "nope"}}])
    assert listener.run_once(ws) is False
    assert listener.idle_until == NOW + timedelta(seconds=IDLE_S)


def test_store_rfq_keeps_the_first_arrival(db_session):
    """A repeated create is the same arrival seen twice; `received_at` is what H5's latency
    reads, so the first one wins."""
    first = store_rfq(db_session, parse_rfq_frame(_created()), NOW)
    again = store_rfq(db_session, parse_rfq_frame(_created()), NOW + timedelta(hours=1))
    assert first.received_at == again.received_at == NOW
