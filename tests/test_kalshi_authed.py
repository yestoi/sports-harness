"""Task 6: KalshiReader and its V2 decoders.

Everything here drives a `FakeTransport` in place of `KalshiTransport`, so no test opens a
socket and no test needs a credential. `FakeTransport` is defined here and imported by Tasks
7, 9 and 10.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from harness.feeds.http import FetchResult
from harness.venues.kalshi.authed import (
    KalshiApiError, KalshiDecodeError, KalshiReader, canonical_side, dec,
)


@dataclass
class FakeTransport:
    """Records `(method, path, params, json)` and replays queued FetchResults. The fake stands
    in for KalshiTransport everywhere the live path is exercised, so no test ever opens a
    socket and no test needs a credential."""
    queued: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    env: str = "prod"

    def request(self, method, path, params=None, json=None):
        self.calls.append((method, path, params, json))
        if not self.queued:
            raise AssertionError(f"unqueued {method} {path}")
        result = self.queued.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _ok(body: dict) -> FetchResult:
    return FetchResult(
        status=200, headers={}, body=body,
        fetched_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        url="https://api.elections.kalshi.com/trade-api/v2/x", elapsed_s=0.01,
    )


def _err(status: int, code: str | None = None) -> FetchResult:
    return FetchResult(
        status=status, headers={}, body={"code": code} if code else {},
        fetched_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        url="https://api.elections.kalshi.com/trade-api/v2/x", elapsed_s=0.01,
    )


def test_reader_has_no_write_methods():
    for name in ("place_limit", "amend", "cancel", "cancel_group", "create_group"):
        assert not hasattr(KalshiReader, name)


def test_get_orders_decodes_v2_shapes_with_decimals():
    t = FakeTransport(queued=[_ok({"orders": [{
        "order_id": "o1", "client_order_id": "c1", "ticker": "KXNFLGAME-X",
        "outcome_side": "yes", "book_side": "bid",
        "price": "0.5600", "count": "10.00",
        "remaining_count": "7.00", "fill_count": "3.00",
        "status": "resting", "order_group_id": "g1",
        "expiration_time": "2026-09-13T23:50:00Z",
        "created_time": "2026-09-13T20:00:00Z"}], "cursor": ""})])
    order = KalshiReader(t).get_orders("resting")[0]
    assert order.price == Decimal("0.5600") and order.count == Decimal("10.00")
    assert order.remaining_count == Decimal("7.00") and order.fill_count == Decimal("3.00")
    assert order.outcome_side == "yes" and order.book_side == "bid"
    assert order.expiration_time == datetime(2026, 9, 13, 23, 50, tzinfo=timezone.utc)
    assert t.calls[0][0] == "GET" and t.calls[0][1] == "/portfolio/orders"


def test_canonical_side_prefers_outcome_side():
    assert canonical_side({"outcome_side": "no", "book_side": "bid", "side": "yes"}) == "no"


def test_canonical_side_falls_back_to_book_side_when_outcome_side_is_absent():
    assert canonical_side({"book_side": "bid"}) == "yes"
    assert canonical_side({"book_side": "ask"}) == "no"


def test_canonical_side_ignores_the_deprecated_fields():
    assert canonical_side({"side": "yes", "action": "buy"}) is None


def test_decoders_tolerate_absent_legacy_fields():
    t = FakeTransport(queued=[_ok({"orders": [{"order_id": "o1", "ticker": "T",
                                               "outcome_side": "no"}], "cursor": ""})])
    order = KalshiReader(t).get_orders()[0]
    assert order.outcome_side == "no" and order.book_side is None and order.price is None


def test_get_fills_passes_since_as_unix_seconds():
    t = FakeTransport(queued=[_ok({"fills": [], "cursor": ""})])
    since = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    KalshiReader(t).get_fills(since)
    assert t.calls[0][2]["min_ts"] == str(int(since.timestamp()))


def test_get_fills_decodes_a_venue_fill():
    t = FakeTransport(queued=[_ok({"fills": [{
        "trade_id": "t1", "order_id": "o1", "ticker": "T", "outcome_side": "yes",
        "book_side": "bid", "price": "0.4400", "count": "2.00", "is_taker": False,
        "created_time": "2026-09-13T21:00:00Z"}], "cursor": ""})])
    fill = KalshiReader(t).get_fills()[0]
    assert fill.price == Decimal("0.4400") and fill.count == Decimal("2.00")
    assert fill.is_taker is False


def test_get_balance_and_positions_decode_decimals():
    t = FakeTransport(queued=[_ok({"balance": "123.45"}),
                              _ok({"market_positions": [
                                  {"ticker": "T", "position": "5.00",
                                   "market_exposure": "2.20",
                                   "resting_orders_count": "1.00"}], "cursor": ""})])
    r = KalshiReader(t)
    assert r.get_balance().balance == Decimal("123.45")
    assert r.get_positions()[0].position == Decimal("5.00")


def test_get_account_limits_decodes_the_buckets():
    t = FakeTransport(queued=[_ok({"tier": "basic",
                                   "read": {"refill_rate": "10", "capacity": "100"},
                                   "write": {"refill_rate": "5", "capacity": "50"}})])
    limits = KalshiReader(t).get_account_limits()
    assert limits.tier == "basic" and limits.read_refill_rate == Decimal("10")
    assert limits.write_capacity == Decimal("50")


def test_list_endpoints_follow_the_cursor():
    # fix round 1: _decode_order now requires a resolvable direction (Important 2), so these
    # fixtures carry a minimal outcome_side -- this test is about cursor-following, not
    # direction decoding, and a real order always has one.
    t = FakeTransport(queued=[
        _ok({"orders": [{"order_id": "a", "ticker": "T", "outcome_side": "yes"}], "cursor": "c1"}),
        _ok({"orders": [{"order_id": "b", "ticker": "T", "outcome_side": "yes"}], "cursor": ""})])
    assert [o.order_id for o in KalshiReader(t).get_orders()] == ["a", "b"]
    assert t.calls[1][2]["cursor"] == "c1"


def test_dec_never_returns_a_float():
    assert dec("0.5600") == Decimal("0.5600") and isinstance(dec(3), Decimal)
    assert dec(None) is None


# --- fix round 1, Important 1: non-2xx responses raise, never swallowed ---------------------

def test_a_401_on_account_limits_raises_with_the_status():
    t = FakeTransport(queued=[_err(401, code="unauthorized")])
    with pytest.raises(KalshiApiError) as exc_info:
        KalshiReader(t).get_account_limits()
    assert exc_info.value.status == 401
    assert exc_info.value.code == "unauthorized"


def test_a_401_on_orders_raises_rather_than_returning_an_empty_list():
    t = FakeTransport(queued=[_err(401)])
    with pytest.raises(KalshiApiError) as exc_info:
        KalshiReader(t).get_orders()
    assert exc_info.value.status == 401


def test_a_200_with_an_empty_list_still_returns_an_empty_list():
    t = FakeTransport(queued=[_ok({"orders": [], "cursor": ""})])
    assert KalshiReader(t).get_orders() == []


def test_a_non_200_mid_paging_raises_rather_than_stopping_silently():
    t = FakeTransport(queued=[
        _ok({"orders": [{"order_id": "a", "ticker": "T", "outcome_side": "yes"}], "cursor": "c1"}),
        _err(500)])
    with pytest.raises(KalshiApiError) as exc_info:
        KalshiReader(t).get_orders()
    assert exc_info.value.status == 500


def test_the_api_error_code_is_ascii_escaped_and_truncated():
    t = FakeTransport(queued=[_err(400, code="x" * 60 + "\n\r" + "é")])
    with pytest.raises(KalshiApiError) as exc_info:
        KalshiReader(t).get_account_limits()
    code = exc_info.value.code
    assert len(code) == 40 and "\n" not in code and "\r" not in code
    assert code.isascii()


# --- fix round 1, Important 2: canonical_side wired into the decoders ------------------------

def test_order_with_only_book_side_decodes_outcome_side_from_it():
    t = FakeTransport(queued=[_ok({"orders": [{
        "order_id": "o1", "ticker": "T", "book_side": "ask"}], "cursor": ""})])
    order = KalshiReader(t).get_orders()[0]
    assert order.outcome_side == "no" and order.book_side == "ask"


def test_order_with_only_legacy_side_and_action_decodes_correctly():
    t = FakeTransport(queued=[_ok({"orders": [{
        "order_id": "o1", "ticker": "T", "side": "no", "action": "sell"}], "cursor": ""})])
    order = KalshiReader(t).get_orders()[0]
    assert order.outcome_side == "no" and order.book_side == "ask"


def test_order_with_no_direction_anywhere_raises_a_decode_error():
    t = FakeTransport(queued=[_ok({"orders": [{"order_id": "o1", "ticker": "T"}], "cursor": ""})])
    with pytest.raises(KalshiDecodeError):
        KalshiReader(t).get_orders()


def test_fill_with_only_legacy_side_decodes_correctly():
    t = FakeTransport(queued=[_ok({"fills": [{
        "trade_id": "t1", "ticker": "T", "side": "yes", "price": "0.5000", "count": "1.00",
        "created_time": "2026-09-13T21:00:00Z"}], "cursor": ""})])
    fill = KalshiReader(t).get_fills()[0]
    assert fill.outcome_side == "yes" and fill.book_side == "bid"


# --- fix round 1, Important 3: dec rejects non-finite decimals ------------------------------

@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity"])
def test_dec_rejects_non_finite_venue_text(text):
    with pytest.raises(KalshiDecodeError):
        dec(text)


# --- fix round 1, Minor: naive timestamps are UTC, not local --------------------------------

def test_ts_treats_a_naive_timestamp_as_utc():
    t = FakeTransport(queued=[_ok({"orders": [{
        "order_id": "o1", "ticker": "T", "outcome_side": "yes",
        "created_time": "2026-09-13T21:00:00"}], "cursor": ""})])
    order = KalshiReader(t).get_orders()[0]
    assert order.created_time == datetime(2026, 9, 13, 21, 0, tzinfo=timezone.utc)


# --- fix round 1, Minor: the three previously-untested methods ------------------------------

def test_get_order_decodes_a_single_order():
    t = FakeTransport(queued=[_ok({"order": {
        "order_id": "o1", "ticker": "T", "outcome_side": "yes"}})])
    order = KalshiReader(t).get_order("o1")
    assert order.order_id == "o1"
    assert t.calls[0][0] == "GET" and t.calls[0][1] == "/portfolio/orders/o1"


def test_get_order_percent_encodes_the_order_id():
    t = FakeTransport(queued=[_ok({"order": {
        "order_id": "a/b", "ticker": "T", "outcome_side": "yes"}})])
    KalshiReader(t).get_order("a/b")
    assert t.calls[0][1] == "/portfolio/orders/a%2Fb"


def test_get_exchange_status_returns_the_body():
    t = FakeTransport(queued=[_ok({"exchange_active": True, "trading_active": True})])
    status = KalshiReader(t).get_exchange_status()
    assert status == {"exchange_active": True, "trading_active": True}
    assert t.calls[0][1] == "/exchange/status"


def test_get_series_returns_the_body():
    t = FakeTransport(queued=[_ok({"series_ticker": "KXNFLGAME",
                                   "fee_type": "quadratic", "fee_multiplier": "0.07"})])
    body = KalshiReader(t).get_series("KXNFLGAME")
    assert body["fee_type"] == "quadratic"
    assert t.calls[0][1] == "/series/KXNFLGAME"


# --- fix round 1, Minor: the fake transport's exception-replay branch -----------------------

def test_a_transport_exception_propagates_through_the_reader():
    class _Boom(RuntimeError):
        pass

    t = FakeTransport(queued=[_Boom("network exploded")])
    with pytest.raises(_Boom):
        KalshiReader(t).get_orders()
