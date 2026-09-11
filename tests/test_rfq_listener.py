"""The RFQ listener: frame parsing, storage on arrival, idling, and the second socket."""
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import websocket
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from harness.db.models import Rfq
from harness.venues.kalshi.rfq import (CHANNEL, DROP_NOT_ALL_FOOTBALL, DROP_UNKNOWN_DELETE, ENV,
                                       EXCERPT_MAX, IDLE_ERROR_CODES, IDLE_S, RAW_MAX_BYTES,
                                       VENUE, handle_frame, idle_reason, parse_rfq_frame,
                                       store_rfq)
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


def _non_football_created(rfq_id="rfq_nf"):
    """Fix 38 (journal 110): the incident's own shape -- a combo whose top-level ticker is a
    synthetic MVE collection id, not a priced series, and whose legs are all on
    `KXMVECROSSCATEGORY-SHARD*`, a series this harness never prices at all."""
    return {"type": "rfq_created", "sid": 7, "msg": {
        "id": rfq_id, "creator_id": "", "market_ticker": "KXMVECROSSCATEGORY-SHARD1-X",
        "event_ticker": "KXMVECROSSCATEGORY-SHARD1", "created_ts": 1789000000,
        "mve_collection_ticker": "MVE-X-1",
        "mve_selected_legs": [
            {"event_ticker": "KXMVECROSSCATEGORY-SHARD1-A",
             "market_ticker": "KXMVECROSSCATEGORY-SHARD1-A-YES", "side": "yes",
             "yes_settlement_value_dollars": "1.0000"},
            {"event_ticker": "KXMVECROSSCATEGORY-SHARD2-B",
             "market_ticker": "KXMVECROSSCATEGORY-SHARD2-B-YES", "side": "yes",
             "yes_settlement_value_dollars": "1.0000"}]}}


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

def test_the_row_is_written_on_arrival(db_session, env_settings):
    row = handle_frame(db_session, _created(), NOW)
    assert isinstance(row, Rfq)
    assert row.status == "open" and row.received_at == NOW
    assert row.market_ticker == "KXNFLGAME-26SEP14DALNYG-DAL"
    assert len(row.legs) == 2


def test_a_delete_marks_the_existing_row(db_session, env_settings):
    handle_frame(db_session, _created(), NOW)
    handle_frame(db_session, _deleted(), NOW + timedelta(minutes=30))
    row = db_session.get(Rfq, "rfq_1")
    assert row.status == "deleted" and row.deleted_ts is not None
    assert row.received_at == NOW      # the arrival time is not overwritten


def test_a_delete_for_an_unseen_rfq_is_dropped_without_a_write(db_session):
    """Fix 38 (journal 110): three quarters of the incident's flood was `rfq_deleted` frames,
    almost all for combos this listener never stored in the first place (no football leg, or
    arrived before the listener was ever turned on). A delete for an id with no row here is now
    counted and dropped, not written -- the pre-fix-38 behaviour (F71: "the socket is the only
    record, so write it anyway") is exactly the incident's other three quarters."""
    dropped = []
    row = handle_frame(db_session, _deleted("rfq_never_seen"), NOW, on_dropped=dropped.append)
    assert row is None
    assert dropped == [DROP_UNKNOWN_DELETE]
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 0


def test_a_delete_for_a_stored_rfq_still_applies(db_session, env_settings):
    """The other half of the same rule: a delete for an id `handle_frame` already stored is
    applied as before, not dropped."""
    handle_frame(db_session, _created(), NOW)
    row = handle_frame(db_session, _deleted(), NOW + timedelta(minutes=5))
    assert row is not None and row.status == "deleted"


def test_a_repeated_create_does_not_duplicate(db_session, env_settings):
    handle_frame(db_session, _created(), NOW)
    handle_frame(db_session, _created(), NOW + timedelta(seconds=1))
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 1


# --- fix 38 / fix 40: the boundary filter (journal 110, journal 112) --------------------------

def test_a_non_football_rfq_created_is_counted_and_not_stored(db_session):
    """The incident's own shape: a combo touching no football market at all is counted and
    dropped before `store_rfq` -- never written, never quoted."""
    dropped = []
    row = handle_frame(db_session, _non_football_created(), NOW, on_dropped=dropped.append)
    assert row is None
    assert dropped == [DROP_NOT_ALL_FOOTBALL]
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 0
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 0


def test_an_all_football_two_game_combo_is_stored_and_quoted_as_before(db_session, env_settings):
    """The ordinary case, unaffected: every leg is `KXNFLGAME`, so the filter passes it straight
    through to storage and the quote path exactly as before fix 38/40."""
    dropped = []
    row = handle_frame(db_session, _created(), NOW, on_dropped=dropped.append)
    assert row is not None and dropped == []
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 1
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 1


def test_a_combo_with_one_football_leg_and_one_non_football_leg_is_dropped(db_session,
                                                                           env_settings):
    """Fix 40 (journal 112): the incident's own shape -- fix 38's original filter was 'at least
    one leg', which let a mixed combo like this one reach storage and then `no_fair` decline it
    downstream (`rfq_quote.py`'s `_is_football`), 1,837 `no_fair` declines and zero quotable in
    six minutes. The boundary now requires every leg to be football, so a mixed combo -- top
    -level ticker deliberately non-football too, so only the leg-level check is what could have
    let it through -- is counted and dropped before it is ever written."""
    frame = _created(rfq_id="rfq_mixed", legs=[
        {"event_ticker": "KXNFLGAME-26SEP14DALNYG",
         "market_ticker": "KXNFLGAME-26SEP14DALNYG-DAL", "side": "yes",
         "yes_settlement_value_dollars": "1.0000"},
        {"event_ticker": "KXMVECROSSCATEGORY-SHARD1",
         "market_ticker": "KXMVECROSSCATEGORY-SHARD1-A", "side": "yes",
         "yes_settlement_value_dollars": "1.0000"}])
    frame["msg"]["event_ticker"] = "KXMVECROSSCATEGORY-SHARD1"
    dropped = []
    row = handle_frame(db_session, frame, NOW, on_dropped=dropped.append)
    assert row is None
    assert dropped == [DROP_NOT_ALL_FOOTBALL]
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 0
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 0


def test_a_non_football_single_market_rfq_is_dropped(db_session):
    """No `mve_selected_legs` at all -- a single-market RFQ, not a combo -- is checked against
    its own top-level ticker, the only market it names: non-football drops it exactly as a combo
    would."""
    frame = _created(rfq_id="rfq_single_nf", legs=[])
    frame["msg"]["event_ticker"] = "KXMVECROSSCATEGORY-SHARD1"
    frame["msg"]["market_ticker"] = "KXMVECROSSCATEGORY-SHARD1-A"
    dropped = []
    row = handle_frame(db_session, frame, NOW, on_dropped=dropped.append)
    assert row is None and dropped == [DROP_NOT_ALL_FOOTBALL]
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 0


def test_a_single_leg_football_frame_is_stored_and_declines_single_leg(db_session, env_settings):
    """A single-market RFQ on a football ticker clears the (now all-legs) boundary filter same as
    always -- it has no legs to fail the 'every leg' check, and its own top-level ticker is
    football -- and is stored; the counterfactual quote then declines it downstream for an
    unrelated reason (`rfq_quote.py`'s `single_leg`, fewer than two resolved legs), not by the
    boundary filter, which never looks at leg count at all."""
    frame = _created(rfq_id="rfq_single_football", legs=[])
    dropped = []
    row = handle_frame(db_session, frame, NOW, on_dropped=dropped.append)
    assert row is not None and dropped == []
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 1
    quote = db_session.execute(text(
        "select declined_reason from rfq_quotes where rfq_id = :id"),
        {"id": "rfq_single_football"}).first()
    assert quote is not None and quote.declined_reason == "single_leg"


# --- fix 35: cheap quotes -------------------------------------------------------------------

def test_a_replayed_rfq_created_for_an_already_quoted_id_stores_and_recomputes_nothing(
        db_session, env_settings):
    """Item 2: the venue replays the whole open RFQ set on every subscribe (journal 109's
    incident, ten reconnects in 30 minutes). A repeated `rfq_created` for an id `rfq_quotes`
    already holds a row for must store the arrival and touch nothing else -- not a second quote
    row, and `on_replay` is the one signal a caller gets that it happened."""
    frame = _created(rfq_id="rfq_replay", legs=[])     # legs=[] declines single_leg, no fixtures
    handle_frame(db_session, frame, NOW)
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 1

    calls = []
    row = handle_frame(db_session, frame, NOW + timedelta(minutes=1),
                       on_replay=lambda: calls.append(1))
    assert row is not None
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 1
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 1
    assert calls == [1]


def test_the_replay_log_line_is_debug_not_info(db_session, env_settings, caplog):
    """Review I3: the replay line fires at burst volume (roughly 490 per reconnect in the
    incident); it must not render at production's default INFO level."""
    frame = _created(rfq_id="rfq_debug_check", legs=[])
    handle_frame(db_session, frame, NOW)
    with caplog.at_level(logging.INFO, logger="harness.venues.kalshi.rfq"):
        handle_frame(db_session, frame, NOW + timedelta(minutes=1))
    assert not any("already quoted" in r.message for r in caplog.records)
    with caplog.at_level(logging.DEBUG, logger="harness.venues.kalshi.rfq"):
        handle_frame(db_session, frame, NOW + timedelta(minutes=2))
    debug_lines = [r for r in caplog.records if "already quoted" in r.message]
    assert len(debug_lines) == 1 and debug_lines[0].levelno == logging.DEBUG


def test_the_replay_log_line_sanitizes_the_venue_id(db_session, env_settings, caplog):
    """Review I3: `row.id` is the venue's own `rfq_id`, never escaped before this render -- a
    control character in it must not reach the log verbatim."""
    hostile_id = "rfq\x1b[31mHOSTILE"
    frame = _created(rfq_id=hostile_id, legs=[])
    handle_frame(db_session, frame, NOW)
    with caplog.at_level(logging.DEBUG, logger="harness.venues.kalshi.rfq"):
        handle_frame(db_session, frame, NOW + timedelta(minutes=1))
    debug_lines = [r.message for r in caplog.records if "already quoted" in r.message]
    assert len(debug_lines) == 1
    assert "\x1b" not in debug_lines[0]


def test_allow_quote_false_stores_the_arrival_and_never_quotes(db_session, env_settings):
    """Item 4 (round 1: the quote rate limit, `harness/venues/kalshi/rfq_socket.py`): `allow_quote`
    is a callable, not a bool -- called only at the point a quote would actually be attempted,
    and a `False` return skips the quote decision entirely. The frame is stored as an arrival
    like any other, even for a rfq the listener has never seen before."""
    frame = _created(rfq_id="rfq_over_cap", legs=[])
    row = handle_frame(db_session, frame, NOW, allow_quote=lambda: False)
    assert row is not None
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 1
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 0


def test_allow_quote_is_never_called_for_a_dedupe_hit(db_session, env_settings):
    """C1's exact fix: `allow_quote` must not be able to spend the rate budget on a frame that
    was never going to call `compute_quote` anyway."""
    frame = _created(rfq_id="rfq_dedupe_gate", legs=[])
    handle_frame(db_session, frame, NOW)
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 1

    def _boom():
        raise AssertionError("allow_quote was called for an already-quoted rfq")

    row = handle_frame(db_session, frame, NOW + timedelta(minutes=1), allow_quote=_boom)
    assert row is not None
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 1


def test_allow_quote_is_never_called_for_an_rfq_deleted_frame(db_session, env_settings):
    """C1's exact fix, the other half: a delete never reaches the quote decision at all, so it
    must never reach `allow_quote` either. (Fix 38: the id has to be stored first -- a delete
    for an id never stored is now dropped at the boundary before this decision is even in play,
    covered separately by `test_a_delete_for_an_unseen_rfq_is_dropped_without_a_write`.)"""
    handle_frame(db_session, _created(rfq_id="rfq_delete_gate"), NOW)

    def _boom():
        raise AssertionError("allow_quote was called for an rfq_deleted frame")

    row = handle_frame(db_session, _deleted("rfq_delete_gate"), NOW, allow_quote=_boom)
    assert row is not None and row.status == "deleted"


def test_the_raw_message_is_stored_capped_with_a_flag(db_session, env_settings):
    """Ruling B-M9 and D13: the raw message lives in `rfqs.raw` because the report needs the
    legs and no builder may read `raw_responses`. Capped at 8 KB with a flag."""
    frame = _created()
    frame["msg"]["padding"] = "x" * 20_000
    row = handle_frame(db_session, frame, NOW)
    assert row.raw["truncated"] is True
    assert len(json.dumps(row.raw).encode()) <= RAW_MAX_BYTES


def test_a_small_message_is_stored_whole_and_unflagged(db_session, env_settings):
    row = handle_frame(db_session, _created(), NOW)
    assert row.raw["truncated"] is False
    assert row.raw["msg"]["id"] == "rfq_1"


def test_hostile_free_text_reaches_no_rendered_string(db_session, env_settings):
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


def _listener(db_session, env_settings, ws=None, monotonic=None):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    # `sign` is the seam: `env_settings` keeps the container defaults for the two Kalshi key
    # paths, which do not exist on the Mac, so a real `sign_request` would raise
    # `FileNotFoundError` before the ws factory was ever reached.
    # `monotonic`, when given, is fix 35 round 1's seam for the quote rate limit's sliding
    # window -- a test can slide it without a real sleep. Omitted, `RfqListener` uses the real
    # `time.monotonic`.
    kwargs = {} if monotonic is None else {"monotonic": monotonic}
    return RfqListener(env_settings, factory, ws_factory=lambda *a, **k: ws,
                       clock=lambda: NOW, sleep=lambda *_: None, sign=lambda *_a, **_k: {},
                       **kwargs)


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


def test_the_listener_counts_a_replayed_arrival_through_the_socket(db_session, env_settings):
    """Item 2, end to end: the listener's own `replayed` counter is what `on_replay` feeds."""
    frame = _created(rfq_id="rfq_replay_socket", legs=[])
    ws = FakeWs([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}, frame, frame])
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    listener.run_once(ws)      # the ack
    listener.run_once(ws)      # the first rfq_created -> quoted
    listener.run_once(ws)      # the replay
    assert listener.replayed == 1
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 1


#: The rate-limit and burst-summary tests below feed everything through one listener rather than
#: mixing a direct `handle_frame(db_session, ...)` call with `listener.run_once`: `run_once`
#: opens its own session per frame (`self._factory()`, a separate connection from `db_session`'s)
#: and commits it, so a row `db_session` inserted but never committed would not exist yet from
#: that second connection's point of view -- the two would race for the same primary key instead
#: of one building on the other's already-quoted state.
RFQ_SOCKET_LOGGER = "harness.venues.kalshi.rfq_socket"


def test_the_rate_limit_never_spends_its_budget_on_a_replay_burst(db_session, env_settings,
                                                                   monkeypatch):
    """Review Critical 1: the exact scenario that defeated the first cut of item 4. The venue
    replays the whole open RFQ set on every subscribe; if the rate limit counted those replayed
    frames the way the old frame-count cap did, a reconnect's replay of a set the listener had
    *already* quoted would spend the whole budget on frames doing no `fair_values` work at all
    and silently starve a genuinely new RFQ arriving right after it on the same connection.

    Cap 3, frames `[ack, old, old, old, new]` where `old` is already quoted before the
    connection starts: both arrivals store, and `new` still gets quoted -- the three replays of
    `old` never touch the budget at all."""
    from harness.venues.kalshi import rfq_socket as rfq_socket_mod

    monkeypatch.setattr(rfq_socket_mod, "RFQ_QUOTE_RATE_MAX", 3)
    old_frame = _created(rfq_id="rfq_rate_old", legs=[])
    handle_frame(db_session, old_frame, NOW)
    # Committed, not left pending: `listener.run_once` reads and writes through its own session
    # (`self._factory()`, a separate connection from `db_session`'s), which cannot see a row
    # `db_session` has only flushed and not committed -- it would insert a colliding primary key
    # instead of finding `old` already quoted.
    db_session.commit()
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 1

    new_frame = _created(rfq_id="rfq_rate_new", legs=[])
    frames = ([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}]
             + [old_frame, old_frame, old_frame, new_frame])
    ws = FakeWs(frames)
    listener = _listener(db_session, env_settings, ws)
    listener.subscribe(ws)
    for _ in range(1 + 4):
        listener.run_once(ws)
    assert db_session.execute(text("select count(*) from rfqs")).scalar() == 2
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 2
    assert listener.replayed == 3
    assert listener.quotes_skipped_rate == 0


def test_the_rate_limit_engages_then_releases_once_the_window_slides(db_session, env_settings,
                                                                      monkeypatch, caplog):
    """Cap 3 with four brand-new ids inside one second: three quote, the fourth is turned away
    and counted in `quotes_skipped_rate`, and the engagement logs exactly one WARNING (not one
    per turned-away frame). Advancing the injected clock past `RFQ_QUOTE_RATE_WINDOW_S` and
    sending a fifth new id lets it quote again, and logs exactly one INFO release."""
    from harness.venues.kalshi import rfq_socket as rfq_socket_mod

    monkeypatch.setattr(rfq_socket_mod, "RFQ_QUOTE_RATE_MAX", 3)
    clock = [1_000.0]
    frames = ([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}]
             + [_created(rfq_id=f"rfq_rate_{i}", legs=[]) for i in range(4)])
    ws = FakeWs(frames)
    listener = _listener(db_session, env_settings, ws, monotonic=lambda: clock[0])
    listener.subscribe(ws)
    with caplog.at_level(logging.WARNING, logger=RFQ_SOCKET_LOGGER):
        for _ in range(1 + 4):
            listener.run_once(ws)
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 3
    assert listener.quotes_skipped_rate == 1
    engaged = [r for r in caplog.records
              if r.name == RFQ_SOCKET_LOGGER and r.levelno == logging.WARNING]
    assert len(engaged) == 1 and "rate limit engaged" in engaged[0].message

    caplog.clear()
    clock[0] += rfq_socket_mod.RFQ_QUOTE_RATE_WINDOW_S + 1
    ws._frames.append(_created(rfq_id="rfq_rate_4", legs=[]))
    with caplog.at_level(logging.INFO, logger=RFQ_SOCKET_LOGGER):
        listener.run_once(ws)
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 4
    assert listener.quotes_skipped_rate == 1
    released = [r for r in caplog.records
               if r.name == RFQ_SOCKET_LOGGER and r.levelno == logging.INFO
               and "rate limit released" in r.message]
    assert len(released) == 1


def test_the_rate_limit_log_flapping_is_bounded_to_two_lines(db_session, env_settings,
                                                              monkeypatch, caplog):
    """Fix 38 (journal 110): the incident's rate limiter flapped -- engage/release pairs
    100 ms apart at the window boundary. Ten such flips inside about a second must not produce
    ten log lines: each direction logs a transition at most once per `RFQ_RATE_LOG_COOLDOWN_S`
    (cap 1, window 0.05s here so the flapping is reproducible without a real sleep), so the
    burst logs exactly two lines total -- the first engage and the first release -- and stays
    quiet through the rest. The limit's own behaviour (when a call is turned away) is
    unaffected; only how often a transition may log is bounded."""
    from harness.venues.kalshi import rfq_socket as rfq_socket_mod

    monkeypatch.setattr(rfq_socket_mod, "RFQ_QUOTE_RATE_MAX", 1)
    monkeypatch.setattr(rfq_socket_mod, "RFQ_QUOTE_RATE_WINDOW_S", 0.05)
    clock = [2_000.0]
    listener = _listener(db_session, env_settings, ws=None, monotonic=lambda: clock[0])

    with caplog.at_level(logging.INFO, logger=RFQ_SOCKET_LOGGER):
        for _ in range(10):
            listener._try_quote()      # fills the one-slot window: not yet over cap
            clock[0] += 0.01           # still inside the window
            listener._try_quote()      # the window's second occupant: over cap -> engaged
            clock[0] += 0.06           # past the window: the next pair starts released again
    lines = [r for r in caplog.records if r.name == RFQ_SOCKET_LOGGER
            and ("rate limit engaged" in r.message or "rate limit released" in r.message)]
    assert len(lines) == 2
    assert sum("engaged" in r.message for r in lines) == 1
    assert sum("released" in r.message for r in lines) == 1


def test_the_burst_summary_logs_once_after_silence_not_once_per_frame(db_session, env_settings,
                                                                       caplog):
    """Review I3: `replayed=<n> quoted=<n> skipped_rate=<n>` logs once, `RFQ_BURST_SILENCE_S`
    after the last frame processed -- not once per replayed frame, and not before the burst is
    actually over."""
    from harness.venues.kalshi import rfq_socket as rfq_socket_mod

    old_frame = _created(rfq_id="rfq_burst_old", legs=[])
    new_frame = _created(rfq_id="rfq_burst_new", legs=[])
    clock = [5_000.0]
    frames = ([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}]
             + [old_frame, old_frame, new_frame])
    ws = FakeWs(frames)
    listener = _listener(db_session, env_settings, ws, monotonic=lambda: clock[0])
    listener.subscribe(ws)
    with caplog.at_level(logging.INFO, logger=RFQ_SOCKET_LOGGER):
        listener.run_once(ws)      # the ack
        listener.run_once(ws)      # old, quoted for the first time
        listener.run_once(ws)      # old again -- the replay
        listener.run_once(ws)      # new, quoted
        assert not any("replayed=" in r.message for r in caplog.records
                       if r.name == RFQ_SOCKET_LOGGER), (
            "the summary must not log before the burst's silence window has passed")
        clock[0] += rfq_socket_mod.RFQ_BURST_SILENCE_S + 1
        ws._frames.append({"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 99}})
        listener.run_once(ws)      # any next frame triggers the pending flush
    summaries = [r for r in caplog.records
                if r.name == RFQ_SOCKET_LOGGER and "replayed=" in r.message]
    assert len(summaries) == 1
    assert "replayed=1 quoted=2 skipped_rate=0" in summaries[0].message


def test_the_burst_summary_carries_the_four_new_counters(db_session, env_settings, caplog):
    """Fix 38 (journal 110), counter renamed by fix 40 (journal 112): `frames_seen`,
    `frames_stored`, `dropped_not_all_football` and `dropped_unknown_delete` ride the same
    one-per-burst summary the replayed/quoted/skipped_rate trio already used -- one stored
    frame, one not-all-football drop and one unknown-delete drop, each counted once."""
    from harness.venues.kalshi import rfq_socket as rfq_socket_mod

    stored_frame = _created(rfq_id="rfq_summary_stored", legs=[])
    nonfootball_frame = _non_football_created("rfq_summary_nf")
    unknown_delete_frame = _deleted("rfq_summary_never_seen")
    clock = [8_000.0]
    frames = ([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}]
             + [stored_frame, nonfootball_frame, unknown_delete_frame])
    ws = FakeWs(frames)
    listener = _listener(db_session, env_settings, ws, monotonic=lambda: clock[0])
    listener.subscribe(ws)
    with caplog.at_level(logging.INFO, logger=RFQ_SOCKET_LOGGER):
        listener.run_once(ws)      # the ack
        listener.run_once(ws)      # stored
        listener.run_once(ws)      # dropped: not all football
        listener.run_once(ws)      # dropped: unknown delete
        clock[0] += rfq_socket_mod.RFQ_BURST_SILENCE_S + 1
        ws._frames.append({"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 99}})
        listener.run_once(ws)      # any next frame triggers the pending flush
    summaries = [r for r in caplog.records
                if r.name == RFQ_SOCKET_LOGGER and "replayed=" in r.message]
    assert len(summaries) == 1
    message = summaries[0].message
    assert "frames_seen=3" in message
    assert "frames_stored=1" in message
    assert "dropped_not_all_football=1" in message
    assert "dropped_unknown_delete=1" in message
    assert (listener.frames_seen, listener.frames_stored) == (3, 1)
    assert (listener.dropped_not_all_football, listener.dropped_unknown_delete) == (1, 1)


def test_the_burst_summary_also_flushes_periodically_during_continuous_flow(db_session,
                                                                            env_settings,
                                                                            monkeypatch, caplog):
    """Fix 38 (journal 110): the flood that started this fix never goes quiet -- silence alone
    would never flush the summary while it was happening. `RFQ_SUMMARY_PERIOD_S` since the last
    flush is the other trigger (monkeypatched small here), so a connection that never stops
    receiving frames -- each one well under `RFQ_BURST_SILENCE_S` after the last -- still logs a
    heartbeat instead of staying dark until the flow eventually stops."""
    from harness.venues.kalshi import rfq_socket as rfq_socket_mod

    monkeypatch.setattr(rfq_socket_mod, "RFQ_SUMMARY_PERIOD_S", 3.0)
    clock = [9_000.0]
    ws = FakeWs([{"type": "subscribed", "msg": {"channel": CHANNEL, "sid": 7}}])
    listener = _listener(db_session, env_settings, ws, monotonic=lambda: clock[0])
    listener.subscribe(ws)
    with caplog.at_level(logging.INFO, logger=RFQ_SOCKET_LOGGER):
        listener.run_once(ws)      # the ack; no counters yet, nothing to flush
        for i in range(6):
            ws._frames.append(_created(rfq_id=f"rfq_periodic_{i}", legs=[]))
            clock[0] += 1.0        # well under RFQ_BURST_SILENCE_S (5s) -- never silent
            listener.run_once(ws)
    summaries = [r for r in caplog.records
                if r.name == RFQ_SOCKET_LOGGER and "replayed=" in r.message]
    assert len(summaries) >= 1


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
    """`connected` is False before `run_forever` too, so the assertion that carries the claim is
    the signing seam and the factory: a disabled listener reads no key and opens no socket."""
    settings = env_settings.model_copy(update={"rfq_listener_enabled": False})
    calls = []
    listener = RfqListener(settings, sessionmaker(bind=db_session.get_bind()),
                           ws_factory=lambda *a, **k: calls.append("ws_factory"),
                           clock=lambda: NOW, sleep=lambda *_: None,
                           sign=lambda *_a, **_k: calls.append("sign"))
    listener.run_forever()          # returns immediately
    assert calls == []
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

def test_a_hostile_numeric_is_dropped_rather_than_written(db_session, env_settings):
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


def test_the_raw_cap_holds_for_every_oversized_shape(db_session, env_settings):
    """The trimmed fallback keeps the documented fields, and two of those are attacker-sized:
    `mve_selected_legs` and any string in it. A cap that only survives an unknown padding key
    is not a cap, so each shape is asserted against the byte budget."""
    shapes = {
        "padding": lambda m: m.update({"padding": "x" * 20_000}),
        # Fix 40: every leg's `event_ticker` has to resolve to a football series or the boundary
        # filter drops the frame before any of this trimming ever runs -- the attacker-sized
        # string still has to carry the `KXNFLGAME-` prefix `_series_ticker` reads.
        "many_legs": lambda m: m.update({"mve_selected_legs": [
            {"event_ticker": "KXNFLGAME-" + "E" * 190, "market_ticker": "M" * 200, "side": "yes",
             "yes_settlement_value_dollars": "1"} for _ in range(400)]}),
        "one_huge_string": lambda m: m.update({"market_ticker": "z" * 60_000}),
        "huge_number": lambda m: m.update({"created_ts": 10 ** 9000}),
        "deep_nesting": lambda m: m.update({"mve_selected_legs": _nest(20, "x" * 20_000)}),
        # Review T13, I1: a documented key arriving as a nested document rather than a number.
        # `_cut` bounds each level and not their product, so this stored 508,154 bytes with a
        # `truncated` flag on top until the collapse was added.
        "nested_scalar": lambda m: m.update({"contracts_fp": [["y" * 120] * 64] * 64}),
    }
    assert RAW_MAX_BYTES == 8_192
    for name, mutate in shapes.items():
        frame = _created(rfq_id=f"rfq_{name}")
        mutate(frame["msg"])
        row = handle_frame(db_session, frame, NOW)
        assert row is not None, name
        assert row.raw["truncated"] is True, name
        assert len(json.dumps(row.raw).encode()) <= RAW_MAX_BYTES, name


def test_a_nul_is_stripped_without_corrupting_a_neighbouring_backslash(db_session, env_settings):
    """Review T13, I3. The six characters JSON writes for a NUL are also the tail of an escaped
    backslash, so stripping them out of the *serialized* document turns a ticker holding the
    literal characters backslash-u-0-0-0-0 followed by `b` into a backspace: the `b` vanishes, a
    control character appears, and `truncated` stays false. The strip walks the decoded
    structure, so the literal survives and only the real NUL goes."""
    frame = _created(rfq_id="rfq_backslash")
    frame["msg"]["mve_collection_ticker"] = "a\\u0000b"
    frame["msg"]["creator_id"] = "c\x00d"
    frame["msg"]["\x00key"] = "value"
    row = handle_frame(db_session, frame, NOW)
    assert row.raw["msg"]["mve_collection_ticker"] == "a\\u0000b"
    assert row.raw["msg"]["creator_id"] == "cd"
    assert "key" in row.raw["msg"] and row.raw["truncated"] is False
    assert row.mve_collection_ticker == "a\\u0000b"


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


def test_an_oversized_identifier_is_cut_to_its_column(db_session, env_settings):
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


# --- the 401 handshake: a clock skew and a refusal are different failures ----------------------

def _bad_status(status: int, date: str | None = None):
    headers = {"Date": date} if date else {}
    return websocket.WebSocketBadStatusException("handshake %s", status, resp_headers=headers)


def _http_date(when):
    return when.strftime("%a, %d %b %Y %H:%M:%S GMT")


def _signed_at(env_settings, factory_bind, outcomes):
    """A listener whose ws factory replays `outcomes` and whose signer records its timestamps."""
    stamps = []
    calls = []

    def factory(url, **kwargs):
        calls.append(url)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    listener = RfqListener(env_settings, sessionmaker(bind=factory_bind), ws_factory=factory,
                           clock=lambda: NOW, sleep=lambda *_: None,
                           sign=lambda _m, _p, ts_ms: stamps.append(ts_ms) or {})
    return listener, stamps, calls


def test_a_401_carrying_a_date_is_retried_once_with_the_server_s_offset(db_session,
                                                                       env_settings):
    """Review T13's ruling. The recorder's own 401 recovery reads the `Date` header off the
    refused handshake and needs no HTTP client, so the listener can do it while still holding no
    transport. A recoverable clock skew must not cost an hour of blindness."""
    ws = FakeWs([])
    listener, stamps, calls = _signed_at(
        env_settings, db_session.get_bind(),
        [_bad_status(401, _http_date(NOW + timedelta(seconds=90))), ws])
    assert listener.connect() is ws
    assert len(calls) == 2
    # The second signature is stamped 90 s ahead of ours, which is where the venue's clock is.
    assert stamps[1] - stamps[0] == 90_000


def test_a_second_401_is_raised_rather_than_retried_again(db_session, env_settings):
    """One retry, never a loop: a 401 that survives the correction is a refusal, and 0.8 idles
    on it unchanged."""
    listener, _stamps, calls = _signed_at(
        env_settings, db_session.get_bind(),
        [_bad_status(401, _http_date(NOW + timedelta(seconds=90))), _bad_status(401)])
    with pytest.raises(websocket.WebSocketBadStatusException):
        listener.connect()
    assert len(calls) == 2


def test_a_401_without_a_usable_date_is_not_retried(db_session, env_settings):
    listener, _stamps, calls = _signed_at(env_settings, db_session.get_bind(),
                                          [_bad_status(401)])
    with pytest.raises(websocket.WebSocketBadStatusException):
        listener.connect()
    assert len(calls) == 1


def test_a_403_is_never_retried(db_session, env_settings):
    """Only a 401 is a clock question. A 403 is an answer about the account."""
    listener, _stamps, calls = _signed_at(
        env_settings, db_session.get_bind(),
        [_bad_status(403, _http_date(NOW + timedelta(seconds=90)))])
    with pytest.raises(websocket.WebSocketBadStatusException):
        listener.connect()
    assert len(calls) == 1


def test_a_handshake_that_stays_refused_idles_for_an_hour(db_session, env_settings, tmp_path):
    """The other half of the ruling: the retry does not weaken 0.8. A 401 that survives the
    offset correction still idles the listener and marks `venue_status`."""
    key_id, key_pem = tmp_path / "key_id", tmp_path / "key.pem"
    key_id.write_text("unused")           # the signer is stubbed; these exist only so that
    key_pem.write_bytes(b"unused")        # `has_kalshi_credentials()` is true.
    settings = env_settings.model_copy(update={"kalshi_key_id_file": key_id,
                                               "kalshi_private_key_file": key_pem})
    listener = None

    def factory(url, **kwargs):
        raise _bad_status(401, _http_date(NOW + timedelta(seconds=90)))

    def sleep(_seconds):
        listener.stop()                   # one idle sleep is enough to prove the window

    listener = RfqListener(settings, sessionmaker(bind=db_session.get_bind()),
                           ws_factory=factory, clock=lambda: NOW, sleep=sleep,
                           sign=lambda *_a, **_k: {})
    listener.run_forever()
    assert listener.idle_until == NOW + timedelta(seconds=IDLE_S)
    row = db_session.execute(text("select status, reason from venue_status where venue = :v"),
                             {"v": VENUE}).first()
    assert row.status == "unavailable" and "401" in row.reason


def test_a_subscribed_frame_with_a_hostile_msg_does_not_raise(db_session, env_settings):
    """A non-dict `msg` on an ack used to raise `AttributeError` into the broad handler and a
    backoff loop; every path in the handler module already guards this shape."""
    ws = FakeWs([{"type": "subscribed", "msg": "not-a-dict", "sid": 7}])
    listener = _listener(db_session, env_settings, ws)
    assert listener.run_once(ws) is True
    assert listener._sid == 7
