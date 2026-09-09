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


def test_get_fills_decodes_the_dollars_and_fp_names_to_the_same_view():
    """Fix round 2. A fill in the `yes_price_dollars` / `count_fp` vocabulary decoded to a null
    price and a null count, which is what every reconciled fill would have carried if the fills
    endpoint sends those names. The repo's Kalshi reference shows only the older names for this
    endpoint, so the fallbacks are inert until it does not."""
    old_names = {"trade_id": "t1", "order_id": "o1", "ticker": "T", "outcome_side": "yes",
                 "book_side": "bid", "price": "0.4400", "count": "2.00", "is_taker": False,
                 "created_time": "2026-09-13T21:00:00Z"}
    live_names = {"trade_id": "t1", "order_id": "o1", "ticker": "T", "outcome_side": "yes",
                  "book_side": "bid", "yes_price_dollars": "0.4400",
                  "no_price_dollars": "0.5600", "count_fp": "2.00", "is_taker": False,
                  "created_time": "2026-09-13T21:00:00Z"}
    fills = [KalshiReader(FakeTransport(queued=[_ok({"fills": [f], "cursor": ""})])).get_fills()[0]
             for f in (old_names, live_names)]
    assert fills[0] == fills[1]
    assert fills[1].price == Decimal("0.4400") and fills[1].count == Decimal("2.00")
    assert fills[1].is_taker is False


def test_the_explicit_fill_field_names_win_when_the_venue_sends_both():
    """Fix round 3: the name that states its units wins, as it does for a bucket capacity."""
    t = FakeTransport(queued=[_ok({"fills": [{
        "trade_id": "t1", "ticker": "T", "outcome_side": "yes",
        "price": "0.9900", "yes_price_dollars": "0.4400",
        "count": "99.00", "count_fp": "2.00"}], "cursor": ""})])
    fill = KalshiReader(t).get_fills()[0]
    assert fill.price == Decimal("0.4400") and fill.count == Decimal("2.00")


def test_a_fill_with_only_a_no_price_decodes_to_a_null_price():
    """`VenueFillView.price` is a YES-leg price, exactly as `OrderView.price` is. A fill is a
    record of something that already happened, so the null reaches the reconcile path rather
    than a cancel; that path's handling of a null price is unchanged."""
    t = FakeTransport(queued=[_ok({"fills": [{
        "trade_id": "t1", "ticker": "T", "outcome_side": "no", "book_side": "ask",
        "no_price_dollars": "0.5600", "count_fp": "2.00"}], "cursor": ""})])
    fill = KalshiReader(t).get_fills()[0]
    assert fill.price is None and fill.count == Decimal("2.00")
    assert fill.outcome_side == "no"


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


def test_get_account_limits_decodes_the_shape_the_live_venue_actually_sends():
    """Fix 23. The live `GET /account/limits` names the tier `usage_tier` and each bucket's
    ceiling `bucket_capacity`; only `refill_rate` matched the names this decoder used, which is
    why the first production read came back with rates beside a null tier and null capacities."""
    t = FakeTransport(queued=[_ok({
        "usage_tier": "advanced",
        "read": {"refill_rate": "10", "bucket_capacity": "100"},
        "write": {"refill_rate": "5", "bucket_capacity": "50"}})])
    limits = KalshiReader(t).get_account_limits()
    assert limits.tier == "advanced"
    assert limits.read_refill_rate == Decimal("10") and limits.read_capacity == Decimal("100")
    assert limits.write_refill_rate == Decimal("5") and limits.write_capacity == Decimal("50")


def test_account_limits_prefers_the_current_names_over_the_older_ones():
    t = FakeTransport(queued=[_ok({
        "usage_tier": "advanced", "tier": "basic",
        "read": {"refill_rate": "10", "bucket_capacity": "100", "capacity": "1"},
        "write": {"refill_rate": "5", "bucket_capacity": "50", "capacity": "2"}})])
    limits = KalshiReader(t).get_account_limits()
    assert limits.tier == "advanced"
    assert limits.read_capacity == Decimal("100") and limits.write_capacity == Decimal("50")


def test_account_limits_falls_back_when_the_venue_sends_only_the_older_names():
    t = FakeTransport(queued=[_ok({"tier": "basic",
                                   "read": {"refill_rate": "10", "capacity": "100"},
                                   "write": {"refill_rate": "5", "capacity": "50"}})])
    limits = KalshiReader(t).get_account_limits()
    assert limits.tier == "basic" and limits.read_capacity == Decimal("100")


def test_account_limits_with_neither_capacity_name_decodes_to_none():
    t = FakeTransport(queued=[_ok({"read": {"refill_rate": "10"},
                                   "write": {"refill_rate": "5"}})])
    limits = KalshiReader(t).get_account_limits()
    assert limits.tier is None
    assert limits.read_capacity is None and limits.write_capacity is None
    assert limits.read_refill_rate == Decimal("10")


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


def test_the_api_error_code_strips_every_control_character_and_del():
    # fix round 2, Minor: not just \n/\r -- every C0 control char (here \t and \x01) and DEL.
    t = FakeTransport(queued=[_err(400, code="ab\tc\x01d\x7fe")])
    with pytest.raises(KalshiApiError) as exc_info:
        KalshiReader(t).get_account_limits()
    assert exc_info.value.code == "abcde"


# --- fix round 1, Important 2: canonical_side wired into the decoders ------------------------

def test_order_with_only_book_side_decodes_outcome_side_from_it():
    t = FakeTransport(queued=[_ok({"orders": [{
        "order_id": "o1", "ticker": "T", "book_side": "ask"}], "cursor": ""})])
    order = KalshiReader(t).get_orders()[0]
    assert order.outcome_side == "no" and order.book_side == "ask"


@pytest.mark.parametrize("side,action,expected_outcome,expected_book", [
    # fix round 2, Important: action flips the outcome relative to the legacy side; an
    # absent action is treated as buy (round 1's version read `side` verbatim and ignored
    # `action`, which inverted every sell order).
    ("yes", "buy", "yes", "bid"),
    ("yes", "sell", "no", "ask"),
    ("no", "buy", "no", "ask"),
    ("no", "sell", "yes", "bid"),
    ("yes", None, "yes", "bid"),   # absent action treated as buy
    ("no", None, "no", "ask"),
])
def test_order_with_only_legacy_side_and_action_decodes_correctly(
        side, action, expected_outcome, expected_book):
    payload = {"order_id": "o1", "ticker": "T", "side": side}
    if action is not None:
        payload["action"] = action
    t = FakeTransport(queued=[_ok({"orders": [payload], "cursor": ""})])
    order = KalshiReader(t).get_orders()[0]
    assert order.outcome_side == expected_outcome and order.book_side == expected_book


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


def test_get_order_decodes_the_dollars_and_fp_names_the_reference_sends():
    """Fix round 1, Important 1. The single-order GET sends `yes_price_dollars` and the `_fp`
    counts, which the rest of this repo already decodes on the public side. Reading only
    `price`/`count`/... left every one of them None."""
    t = FakeTransport(queued=[_ok({"order": {
        "order_id": "o1", "client_order_id": "c1", "ticker": "KXNFLGAME-X",
        "book_side": "bid",
        "yes_price_dollars": "0.5600", "no_price_dollars": "0.4400",
        "count_fp": "10.00", "remaining_count_fp": "7.00", "fill_count_fp": "3.00",
        "status": "resting", "order_group_id": "g1"}})])
    order = KalshiReader(t).get_order("o1")
    assert order.price == Decimal("0.5600") and order.count == Decimal("10.00")
    assert order.remaining_count == Decimal("7.00")
    assert order.fill_count == Decimal("3.00")
    assert order.outcome_side == "yes" and order.book_side == "bid"


def test_get_order_reads_initial_count_fp_when_the_venue_sends_no_count_at_all():
    """Fix 28. The single-order read the demo venue really answers with carries
    `initial_count_fp`, `remaining_count_fp` and `fill_count_fp` -- and no `count_fp` and no
    `count` (measured 2026-09-09 13:25 UTC, evidence
    `docs/superpowers/autopilot/evidence/2026-09-09-demo-amend-diag-0825.txt`). `count` was
    therefore None on every real read, which is what printed `contracts=none` at the smoke's
    `place` step on every run. The fixture above sends `count_fp`, a shape the venue never
    sends, which is why the suite did not catch it.
    """
    t = FakeTransport(queued=[_ok({"order": {
        "order_id": "01a08658-31d0-7248-9a42-c8e755af43f2",
        "client_order_id": "bd78e150-03fc-4245-b0ed-9baf158c0ff7",
        "ticker": "KXNFLGAME-26SEP09NESEA-SEA", "side": "yes", "action": "buy",
        "outcome_side": "yes", "book_side": "bid",
        "yes_price_dollars": "0.0200", "no_price_dollars": "0.9800",
        "initial_count_fp": "1.00", "remaining_count_fp": "2.00", "fill_count_fp": "0.00",
        "status": "resting"}})])
    order = KalshiReader(t).get_order("01a08658-31d0-7248-9a42-c8e755af43f2")
    assert order.count == Decimal("1.00")            # the size at placement, not the amended one
    assert order.remaining_count == Decimal("2.00")
    assert order.fill_count == Decimal("0.00")
    assert order.price == Decimal("0.0200")


def test_an_order_with_only_a_no_price_decodes_to_no_price_at_all():
    """`no_price_dollars` is never read: `decode_side_price` is defined on the YES leg, and a
    second conversion here would sit in front of the echo check's own."""
    t = FakeTransport(queued=[_ok({"order": {
        "order_id": "o1", "ticker": "T", "book_side": "ask",
        "no_price_dollars": "0.4400", "count_fp": "10.00"}})])
    assert KalshiReader(t).get_order("o1").price is None


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
