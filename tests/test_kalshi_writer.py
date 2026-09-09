"""Task 7: KalshiWriter -- the dormant write path.

Nothing here opens a socket or reads a credential: every test drives Task 6's `FakeTransport`
(imported from `tests.test_kalshi_authed`, which is where it is defined and shared). The writer
is never constructed for production in this phase -- Task 8's `make_writer` is the only caller
that will hold the factory token, and these tests import that module-private sentinel to build
one, which is exactly the point of the guard.

The encoder is the judgement in this file. `orders.prob` lives in the order's own side space (a
`no` order at 0.44 means 44 cents for NO), while Kalshi V2 quotes everything on the YES leg as a
bid or an ask. The round-trip sweep below pins that inversion on both sides across the whole cent
grid, because a silent inversion bug prices every NO order at its complement.
"""
import inspect
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from harness.venues.kalshi.authed import (
    ORDER_MESSAGES_PER_MINUTE, CancelResult, CreateOrderResult, EchoMismatch, KalshiDecodeError,
    KalshiReader, FeeModelMismatch, KalshiWriter, MessageBudgetExceeded, OrderIntent,
    PreSendInvariantFailed, PriceOffGrid, TokenBucket, VenueOrder, _FACTORY_TOKEN,
    _decode_create_order, _decode_order, decode_side_price, encode_side_price, fixed_point,
    grid_steps, snap_intent, snap_to_grid,
)

from tests.test_kalshi_authed import FakeTransport, _err, _ok

CENT_GRID = [Decimal(f"0.{n:02d}") for n in range(1, 100)]
CENT_RANGES = [{"start": 0, "end": 1, "step": 0.01}]
#: 1789000000 -> 2026-09-08T18:26:40Z. The intent's expiry is kickoff - 10 min (R8), set once.
EXPIRY = datetime.fromtimestamp(1789000000, tz=timezone.utc)


class _FakeMonotonic:
    """A monotonic clock the test advances by hand, so the bucket's refill is deterministic."""

    def __init__(self, now: float = 1000.0) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _intent(**kw) -> OrderIntent:
    fields = dict(
        client_order_id="11111111-1111-1111-1111-111111111111",
        ticker="KXNFLGAME-X",
        side="yes",
        prob=Decimal("0.56"),
        contracts=Decimal("10"),
        expiration_time=EXPIRY,
        exchange_index=0,
        order_group_id="g1",
        price_ranges=CENT_RANGES,
    )
    fields.update(kw)
    return OrderIntent(**fields)


def _writer(transport, **kw) -> KalshiWriter:
    """A writer built the way Task 8's factory will build one. The caps default high so that a
    test aiming at one pre-send check is not tripped by another."""
    fields = dict(
        per_bet_cap_dollars=Decimal("1000"),
        contract_cap=Decimal("1000"),
        kill_switch_active=lambda: False,
        writes_allowed=True,
    )
    fields.update(kw)
    return KalshiWriter(transport, KalshiReader(transport), _factory_token=_FACTORY_TOKEN,
                        **fields)


def _echo_of(price="0.5600", count="10.00", remaining=None, fill="0.00", book_side="bid",
             order_id="o1", client_order_id=None, **kw) -> dict:
    """A V2 *order*, the shape `GET /portfolio/orders/{id}` answers with. Since fix 24 this is
    what the echo check reads the accepted side and price out of; the create/amend response
    carries neither, and `_created` below is that shape. `remaining` defaults to the whole
    count, so the default pair satisfies the check and a mismatch has to be asked for.

    `client_order_id` defaults to absent, which fix 28's freshness check reads as fresh. That is
    what keeps this fixture shareable: `tests/test_gateway.py` and `tests/test_venue_state.py`
    drive amends whose `updated_client_order_id` is a uuid4 generated inside the gateway, so no
    fixture can echo it back, and a stale-looking read there would cost four sleeps and a
    freeze. The freshness check itself is pinned below with the id passed in explicitly.
    """
    order = {
        "order_id": order_id,
        "client_order_id": client_order_id,
        "ticker": "KXNFLGAME-X",
        "book_side": book_side,
        "price": price,
        "count": count,
        "remaining_count": count if remaining is None else remaining,
        "fill_count": fill,
        "status": "resting",
        "order_group_id": "g1",
    }
    order.update(kw)
    return order


def _live_order_of(price="0.5600", count="10.00", remaining=None, fill="0.00",
                   book_side="bid", order_id="o1", client_order_id=None, **kw) -> dict:
    """The single-order GET as the reference actually sends it: `yes_price_dollars`, `count_fp`,
    `remaining_count_fp`, `fill_count_fp` (fix round 1, Important 1). Same order, other
    vocabulary -- and `no_price_dollars` is present precisely because the decoder must ignore
    it."""
    order = {
        "order_id": order_id,
        "client_order_id": client_order_id,
        "ticker": "KXNFLGAME-X",
        "book_side": book_side,
        "yes_price_dollars": price,
        "no_price_dollars": str(Decimal("1") - Decimal(price)) if price is not None else None,
        "count_fp": count,
        "remaining_count_fp": count if remaining is None else remaining,
        "fill_count_fp": fill,
        "status": "resting",
        "order_group_id": "g1",
    }
    order.update(kw)
    return order


def _created(count="10.00", remaining=None, fill="0.00", order_id="o1", **kw) -> dict:
    """The V2 create/amend response (fix 24, Trade API reference read 2026-09-08): ids, counts,
    an average fill price, an average fee and a timestamp -- and no side and no price at all.
    Decoding this as an order is what made the first demo smoke fail at `place`."""
    body = {
        "order_id": order_id,
        "client_order_id": "11111111-1111-1111-1111-111111111111",
        "fill_count": fill,
        "remaining_count": count if remaining is None else remaining,
        "average_fill_price": None,
        "average_fee_paid": "0.0000",
        "ts_ms": 1789000000000,
    }
    body.update(kw)
    return body


def _accepted(price="0.5600", count="10.00", remaining=None, fill="0.00", book_side="bid",
              order_id="o1", created=None, **order_kw) -> list:
    """The two responses one accepted place or amend now consumes: the create/amend answer the
    counts are checked against, then the `get_order` the side and the price are read back from.
    Shared with `tests/test_gateway.py` and `tests/test_venue_state.py`, which drive the same
    write path through `KalshiGateway`. `created` overrides fields on the create response;
    everything else goes to the order the confirming read returns."""
    return [
        _ok(_created(count=count, remaining=remaining, fill=fill, order_id=order_id,
                     **(created or {}))),
        _ok({"order": _echo_of(price=price, count=count, remaining=remaining, fill=fill,
                               book_side=book_side, order_id=order_id, **order_kw)}),
    ]


# --- encoder / decoder round trip ---------------------------------------------------------------


@pytest.mark.parametrize("p", CENT_GRID)
@pytest.mark.parametrize("side", ["yes", "no"])
def test_encode_decode_round_trip_over_both_sides_and_the_whole_grid(side, p):
    ranges = [{"start": 0, "end": 1, "step": 0.01}]
    book_side, yes_price = encode_side_price(side, p, ranges)
    assert book_side == ("bid" if side == "yes" else "ask")
    back_side, back_p = decode_side_price(book_side, yes_price)
    assert back_side == side and back_p == p


def test_no_side_is_priced_as_one_minus_p_on_the_yes_leg():
    assert encode_side_price("no", Decimal("0.4400"),
                             [{"start": 0, "end": 1, "step": 0.01}]) == ("ask", Decimal("0.5600"))


def test_snap_to_grid_uses_the_markets_own_ranges_and_always_floors():
    # Fix round 1, Important 2: a floor, not a nearest. Rounding a price up costs us money on
    # whichever side we are on, and it would undo the floor the strategy already applied.
    ranges = [{"start": 0, "end": 1, "step": 0.05}]
    assert snap_to_grid(Decimal("0.5200"), ranges) == Decimal("0.5000")
    assert snap_to_grid(Decimal("0.5250"), ranges) == Decimal("0.5000")
    assert snap_to_grid(Decimal("0.5300"), ranges) == Decimal("0.5000")
    assert snap_to_grid(Decimal("0.5500"), ranges) == Decimal("0.5500")   # exact stays put


def test_snap_falls_back_to_the_linear_cent_grid_when_price_ranges_is_null():
    assert snap_to_grid(Decimal("0.5637"), None) == Decimal("0.5600")


def test_the_yes_leg_floors_the_price_we_bid():
    ranges = [{"start": 0, "end": 1, "step": 0.05}]
    assert encode_side_price("yes", Decimal("0.5300"), ranges) == ("bid", Decimal("0.5000"))


def test_the_no_leg_floors_what_we_pay_for_no_not_the_yes_price():
    # NO at 0.4750 on a 5c grid pays 0.45, which is an ask at 0.55. Snapping the YES leg down
    # instead would have sent an ask at 0.50 and paid 0.50 for the NO.
    ranges = [{"start": 0, "end": 1, "step": 0.05}]
    assert encode_side_price("no", Decimal("0.4750"), ranges) == ("ask", Decimal("0.5500"))
    book_side, yes_price, own_prob = snap_intent("no", Decimal("0.4750"), ranges)
    assert (book_side, yes_price, own_prob) == ("ask", Decimal("0.5500"), Decimal("0.4500"))


@pytest.mark.parametrize("side", ["yes", "no"])
def test_a_price_below_the_markets_lowest_tick_is_refused_never_snapped_to_zero(side):
    ranges = [{"start": 0, "end": 1, "step": 0.05}]
    with pytest.raises(PriceOffGrid):
        encode_side_price(side, Decimal("0.0200"), ranges)


def test_a_refused_price_never_reaches_the_transport_or_spends_a_token():
    t = FakeTransport()
    w = _writer(t)
    with pytest.raises(PriceOffGrid):
        w.place_limit(_intent(prob=Decimal("0.02"),
                              price_ranges=[{"start": 0, "end": 1, "step": 0.05}]))
    assert t.calls == [] and w._bucket.tokens == ORDER_MESSAGES_PER_MINUTE


def test_grid_steps_excludes_zero_and_one_and_matches_the_fallback_on_the_cent_grid():
    # 0 and 1 are not tradable prices, so the parsed cent grid is the fallback grid exactly.
    assert grid_steps(CENT_RANGES) == grid_steps(None)
    assert grid_steps(CENT_RANGES)[0] == Decimal("0.0100")
    assert grid_steps(CENT_RANGES)[-1] == Decimal("0.9900")


def test_grid_steps_unions_several_ranges_and_accepts_a_bare_dict():
    steps = grid_steps([{"start": 0, "end": "0.10", "step": "0.01"},
                        {"start": "0.10", "end": 1, "step": "0.10"}])
    assert Decimal("0.0300") in steps and Decimal("0.5000") in steps
    assert Decimal("0.5500") not in steps
    assert grid_steps({"start": 0, "end": 1, "step": 0.05}) == grid_steps(
        [{"start": 0, "end": 1, "step": 0.05}])


@pytest.mark.parametrize("ranges", [
    [], "nonsense", [{"start": 0, "end": 1, "step": 0}], [{"start": 0, "end": 1, "step": "NaN"}],
    [{"start": 1, "end": 0, "step": "0.01"}], [{"start": 0, "end": 1, "step": "0.0000001"}],
    [{"step": "0.01"}], ["not a dict"],
])
def test_grid_steps_falls_back_on_any_unparseable_or_hostile_ranges(ranges):
    # price_ranges is venue text: a zero step, a NaN, or a step small enough to build a
    # multi-million-entry list must not reach the snap loop.
    assert grid_steps(ranges) == grid_steps(None)


def test_decode_side_price_rejects_a_book_side_the_venue_invented():
    with pytest.raises(KalshiDecodeError):
        decode_side_price("sideways", Decimal("0.5000"))


def test_encode_side_price_rejects_a_side_that_is_not_ours():
    with pytest.raises(ValueError):
        encode_side_price("maybe", Decimal("0.5000"), CENT_RANGES)


def test_fixed_point_formats_prices_at_four_places_and_counts_at_two():
    assert fixed_point(Decimal("0.56"), 4) == "0.5600"
    assert fixed_point(Decimal("10"), 2) == "10.00"


# --- request bodies -----------------------------------------------------------------------------


def test_place_limit_sends_every_required_field_and_nothing_else():
    t = FakeTransport(queued=_accepted(price="0.5600", count="10.00"))
    _writer(t).place_limit(_intent(side="yes", prob=Decimal("0.56"), contracts=Decimal("10")))
    method, path, params, body = t.calls[0]
    assert (method, path) == ("POST", "/portfolio/events/orders")
    assert body == {
        "ticker": "KXNFLGAME-X", "side": "bid", "price": "0.5600", "count": "10.00",
        "client_order_id": "11111111-1111-1111-1111-111111111111",
        "order_group_id": "g1", "time_in_force": "good_till_canceled",
        "expiration_time": 1789000000, "post_only": True,
        "cancel_order_on_pause": True, "self_trade_prevention_type": "maker",
        "exchange_index": 0}


def test_place_limit_sends_the_no_leg_as_an_ask_at_one_minus_p():
    t = FakeTransport(queued=_accepted(price="0.5600", count="10.00", book_side="ask"))
    _writer(t).place_limit(_intent(side="no", prob=Decimal("0.44"), contracts=Decimal("10")))
    assert t.calls[0][3]["side"] == "ask" and t.calls[0][3]["price"] == "0.5600"


def test_amend_sends_exactly_seven_fields_and_no_expiry():
    t = FakeTransport(queued=_accepted(price="0.5700", count="12.00"))
    _writer(t).amend("o1", Decimal("0.57"), Decimal("12"), "c1", "c2",
                     ticker="KXNFLGAME-X", side="yes", exchange_index=0,
                     price_ranges=[{"start": 0, "end": 1, "step": 0.01}])
    _, path, _, body = t.calls[0]
    assert path == "/portfolio/events/orders/o1/amend"
    assert set(body) == {"ticker", "side", "price", "count", "client_order_id",
                         "updated_client_order_id", "exchange_index"}


def test_the_writer_never_exposes_an_expiry_amend():
    # R8: no per-cycle renewal anywhere in the live adapter.
    assert "expiry" not in inspect.signature(KalshiWriter.amend).parameters
    assert "expiration_time" not in inspect.signature(KalshiWriter.amend).parameters


def test_cancel_sends_exchange_index_and_market_ticker_in_the_query():
    t = FakeTransport(queued=[_ok({"order_id": "o1", "client_order_id": "c1",
                                   "reduced_by": "3.00", "ts_ms": 1757000000000})])
    result = _writer(t).cancel("o1", ticker="KXNFLGAME-X", exchange_index=2)
    method, path, params, body = t.calls[0]
    assert method == "DELETE" and path == "/portfolio/events/orders/o1"
    assert params == {"exchange_index": "2", "market_ticker": "KXNFLGAME-X"} and body is None
    assert result == CancelResult("o1", "c1", Decimal("3.00"), 1757000000000)


def test_cancel_result_is_not_decoded_as_an_order():
    # A-I7: the V2 cancel response has no ticker, price, count or status.
    assert not hasattr(CancelResult, "price") and not hasattr(CancelResult, "status")
    assert [f for f in CancelResult.__dataclass_fields__] == [
        "order_id", "client_order_id", "reduced_by", "ts_ms"]


def test_create_group_and_cancel_group():
    t = FakeTransport(queued=[_ok({"order_group_id": "g9"}), _ok({})])
    w = _writer(t)
    assert w.create_group(Decimal("5")) == "g9"
    w.cancel_group("g9")
    assert t.calls[1][0] == "DELETE" and t.calls[1][1].endswith("/g9")


def test_create_group_posts_the_create_sub_path_with_the_fixed_point_field():
    # Fix round 1, Critical 1: the endpoint is /portfolio/order_groups/create, and
    # `contracts_limit` on the wire is an int64 -- the fixed-point string form is
    # `contracts_limit_fp`, which is the one that can carry a Numeric(14,2) quantity.
    t = FakeTransport(queued=[_ok({"order_group_id": "g9"})])
    _writer(t).create_group(Decimal("5"))
    method, path, params, body = t.calls[0]
    assert (method, path) == ("POST", "/portfolio/order_groups/create")
    assert body == {"contracts_limit_fp": "5.00"}


def test_create_group_sends_the_exchange_index_only_when_it_is_not_the_default_shard():
    t = FakeTransport(queued=[_ok({"order_group_id": "g9"})])
    _writer(t).create_group(Decimal("2.50"), exchange_index=2)
    assert t.calls[0][3] == {"contracts_limit_fp": "2.50", "exchange_index": 2}


def test_create_group_raises_when_the_venue_returns_no_group_id():
    with pytest.raises(KalshiDecodeError):
        _writer(FakeTransport(queued=[_ok({})])).create_group(Decimal("5"))


@pytest.mark.parametrize("call", [
    lambda w: w.place_limit(_intent()),
    lambda w: w.cancel("o1", ticker="T", exchange_index=0),
    lambda w: w.cancel_group("g9"),
    lambda w: w.create_group(Decimal("5")),
])
def test_every_write_raises_kalshi_api_error_on_a_non_2xx(call):
    from harness.venues.kalshi.authed import KalshiApiError
    with pytest.raises(KalshiApiError):
        call(_writer(FakeTransport(queued=[_err(401, "unauthorized")])))


# --- construction guard --------------------------------------------------------------------------


def test_the_writer_refuses_direct_construction_outside_the_factory():
    with pytest.raises(RuntimeError, match="make_writer"):
        KalshiWriter(FakeTransport(), None, per_bet_cap_dollars=Decimal("1"),
                     contract_cap=Decimal("1"), kill_switch_active=lambda: False,
                     writes_allowed=True)


# --- pre-send invariant (5.2), independent of the encoder ------------------------------------


def test_pre_send_rejects_a_stake_over_the_per_bet_cap():
    w = _writer(FakeTransport(), per_bet_cap_dollars=Decimal("5"))
    with pytest.raises(PreSendInvariantFailed, match="per_bet_cap"):
        w.place_limit(_intent(prob=Decimal("0.60"), contracts=Decimal("100")))


def test_pre_send_rejects_more_contracts_than_the_cap():
    w = _writer(FakeTransport(), contract_cap=Decimal("10"))
    with pytest.raises(PreSendInvariantFailed, match="contract_cap"):
        w.place_limit(_intent(contracts=Decimal("11")))


@pytest.mark.parametrize("p", [Decimal("0.00"), Decimal("0.005"), Decimal("0.995"), Decimal("1")])
def test_pre_send_rejects_a_probability_outside_the_band(p):
    with pytest.raises(PreSendInvariantFailed, match="prob"):
        _writer(FakeTransport()).place_limit(_intent(prob=p))


def test_pre_send_rejects_when_the_kill_switch_is_active():
    w = _writer(FakeTransport(), kill_switch_active=lambda: True)
    with pytest.raises(PreSendInvariantFailed, match="kill switch"):
        w.place_limit(_intent())


def test_pre_send_rejects_when_writes_are_not_allowed():
    w = _writer(FakeTransport(), writes_allowed=False)
    with pytest.raises(PreSendInvariantFailed, match="mode"):
        w.place_limit(_intent())


def test_pre_send_runs_before_any_transport_call():
    t = FakeTransport()
    with pytest.raises(PreSendInvariantFailed):
        _writer(t, contract_cap=Decimal("1")).place_limit(_intent(contracts=Decimal("50")))
    assert t.calls == []


def test_amend_re_checks_the_caps_before_sending():
    # An amend can raise the stake, so it runs the same 5.2 checks on its own numbers.
    t = FakeTransport()
    with pytest.raises(PreSendInvariantFailed, match="contract_cap"):
        _writer(t, contract_cap=Decimal("10")).amend(
            "o1", Decimal("0.57"), Decimal("50"), "c1", "c2", ticker="T", side="yes",
            exchange_index=0, price_ranges=CENT_RANGES)
    assert t.calls == []


@pytest.mark.parametrize("call", [
    lambda w: w.cancel("o1", ticker="T", exchange_index=0),
    lambda w: w.cancel_group("g9"),
    lambda w: w.create_group(Decimal("5")),
])
def test_no_write_verb_leaves_the_process_when_the_mode_forbids_writes(call):
    t = FakeTransport()
    with pytest.raises(PreSendInvariantFailed, match="mode"):
        call(_writer(t, writes_allowed=False))
    assert t.calls == []


def test_a_cancel_still_goes_out_while_the_kill_switch_is_active():
    # The kill switch stops new risk; it must never strand a resting order.
    t = FakeTransport(queued=[_ok({"order_id": "o1"})])
    _writer(t, kill_switch_active=lambda: True).cancel("o1", ticker="T", exchange_index=0)
    assert t.calls[0][0] == "DELETE"


# --- echo check ------------------------------------------------------------------------------
#
# Fix 24: the echo arrives in two pieces. The create/amend response settles the counts, and the
# side and the price come from the `get_order` the check makes straight afterwards. So an
# accepted place is `_accepted(...)` -- two responses -- and a mismatch is asked for in whichever
# of the two carries the field under test.


def test_the_create_response_shape_is_decoded_without_a_side_or_a_price():
    # The exact body the live venue sent when the first demo smoke failed at `place`.
    result = _decode_create_order({
        "order_id": "o1", "client_order_id": "c1", "fill_count": "0.00",
        "remaining_count": "10.00", "average_fill_price": "0.0000",
        "average_fee_paid": "0.0000", "ts_ms": 1789000000000})
    assert result == CreateOrderResult("o1", "c1", Decimal("0.00"), Decimal("10.00"),
                                       Decimal("0.0000"), 1789000000000)
    assert not hasattr(CreateOrderResult, "price") and not hasattr(CreateOrderResult, "side")


def test_a_create_response_no_longer_raises_a_decode_error_for_a_missing_side():
    # The regression itself: `require_side` raised KalshiDecodeError on this body, so `place`
    # never reached the comparison at all.
    t = FakeTransport(queued=_accepted(price="0.5600", count="10.00"))
    order = _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert order.side == "yes" and order.prob == Decimal("0.5600")


def test_the_side_and_price_come_from_a_get_order_after_the_create():
    t = FakeTransport(queued=_accepted(price="0.5600", count="10.00"))
    _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert [(c[0], c[1]) for c in t.calls] == [
        ("POST", "/portfolio/events/orders"), ("GET", "/portfolio/orders/o1")]


def test_an_amend_confirms_itself_with_a_get_order_too():
    t = FakeTransport(queued=_accepted(price="0.5700", count="12.00"))
    _writer(t).amend("o1", Decimal("0.57"), Decimal("12"), "c1", "c2", ticker="KXNFLGAME-X",
                     side="yes", exchange_index=0, price_ranges=CENT_RANGES)
    assert [(c[0], c[1]) for c in t.calls] == [
        ("POST", "/portfolio/events/orders/o1/amend"), ("GET", "/portfolio/orders/o1")]


def test_echo_mismatch_on_the_count_cancels_and_raises():
    # 4 + 3 != 10, and it is the create response that says so: the counts are checked there,
    # before the confirming read is even made.
    t = FakeTransport(queued=[
        _ok(_created(count="10.00", remaining="4.00", fill="3.00")),
        _ok({"order_id": "o1", "client_order_id": "c1"})])              # the cancel
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert t.calls[1][0] == "DELETE"
    assert exc.value.field == "count"
    assert exc.value.freeze_minutes == 15 and exc.value.reason == "echo_mismatch"


def test_a_count_mismatch_is_caught_before_the_confirming_read_is_made():
    t = FakeTransport(queued=[
        _ok(_created(count="10.00", remaining="4.00", fill="3.00")),
        _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch):
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert [c[0] for c in t.calls] == ["POST", "DELETE"]        # no GET in between


def test_echo_mismatch_on_the_price_cancels_and_raises():
    t = FakeTransport(queued=_accepted(price="0.5700", count="10.00")
                      + [_ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(prob=Decimal("0.56"), contracts=Decimal("10")))
    assert exc.value.field == "prob"
    assert t.calls[2][0] == "DELETE"


def test_a_missing_count_in_the_create_response_is_a_mismatch():
    t = FakeTransport(queued=[
        _ok(_created(count="10.00", remaining="10.00", fill=None)),
        _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert exc.value.field == "the echoed counts"


def test_a_create_response_with_no_order_id_is_a_mismatch_with_nothing_to_cancel():
    t = FakeTransport(queued=[_ok(_created(count="10.00", order_id=None))])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert exc.value.field == "order_id"
    assert exc.value.cancel_error == "no order_id to cancel"
    assert len(t.calls) == 1                    # no cancel, and no confirming read


def test_a_confirming_read_that_will_not_decode_is_a_mismatch_and_is_not_retried():
    """A fetched order with no direction anywhere raises `KalshiDecodeError` in the reader. It
    is a mismatch, and unlike a read that never arrived it gets no second attempt: the payload
    would be the same one."""
    order = _echo_of(price="0.5600", count="10.00")
    order.pop("book_side")
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _ok({"order": order}), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert "KalshiDecodeError" in exc.value.field
    assert [c[0] for c in t.calls] == ["POST", "GET", "DELETE"]      # one GET, not two


def test_a_confirmed_order_with_no_price_is_a_mismatch():
    order = _echo_of(price=None, count="10.00")
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _ok({"order": order}), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert exc.value.field == "the confirmed price"


def test_a_failed_cancel_does_not_mask_the_echo_mismatch():
    from harness.venues.kalshi.http import VenueTransportError
    t = FakeTransport(queued=_accepted(price="0.5700", count="10.00")
                      + [VenueTransportError("DELETE", "/portfolio/events/orders/o1",
                                             "ConnectError")])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(prob=Decimal("0.56"), contracts=Decimal("10")))
    assert exc.value.cancel_error == "VenueTransportError"


def test_an_echo_mismatch_on_an_amend_cancels_and_raises():
    t = FakeTransport(queued=_accepted(price="0.9900", count="12.00")
                      + [_ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch):
        _writer(t).amend("o1", Decimal("0.57"), Decimal("12"), "c1", "c2", ticker="KXNFLGAME-X",
                         side="yes", exchange_index=0, price_ranges=CENT_RANGES)
    assert t.calls[2][0] == "DELETE"


def test_an_echo_that_flips_the_book_side_is_a_mismatch():
    # Fix round 1, Important 1: a NO order echoed as a bid at the same YES price is the exact
    # inverse of the order. Comparing only the raw YES-leg price waved it through.
    t = FakeTransport(queued=_accepted(price="0.5600", count="10.00", book_side="bid")
                      + [_ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(side="no", prob=Decimal("0.44"),
                                       contracts=Decimal("10")))
    assert exc.value.field == "side" and exc.value.reason == "echo_mismatch"
    assert t.calls[2][0] == "DELETE"


def test_an_echo_with_a_direction_we_do_not_recognise_is_a_mismatch():
    # Never let unvalidated venue text land in VenueOrder.side.
    order = _echo_of(price="0.5600", count="10.00")
    order["book_side"] = "sideways"
    order["outcome_side"] = "maybe"
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _ok({"order": order}), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert exc.value.field == "side"


def test_an_approved_order_always_carries_our_own_side_and_a_decimal_prob():
    for side, book_side, prob in [("yes", "bid", Decimal("0.56")), ("no", "ask", Decimal("0.44"))]:
        t = FakeTransport(queued=_accepted(price="0.5600", count="10.00",
                                           book_side=book_side))
        order = _writer(t).place_limit(_intent(side=side, prob=prob, contracts=Decimal("10")))
        assert order.side in ("yes", "no") and order.side == side
        assert isinstance(order.prob, Decimal) and order.prob == prob


def test_the_echo_is_compared_against_the_snapped_price_not_the_raw_request():
    # An off-grid request goes out floored, so the venue echoes the floored price. Comparing
    # against the caller's pre-snap number would freeze the market on every such order.
    ranges = [{"start": 0, "end": 1, "step": 0.05}]
    t = FakeTransport(queued=_accepted(price="0.5000", count="10.00"))
    order = _writer(t).place_limit(_intent(prob=Decimal("0.5300"), contracts=Decimal("10"),
                                           price_ranges=ranges))
    assert t.calls[0][3]["price"] == "0.5000" and order.prob == Decimal("0.5000")


def test_a_matching_echo_returns_a_venue_order_decoded_into_our_side_space():
    t = FakeTransport(queued=_accepted(price="0.5600", count="10.00", remaining="10.00",
                                       fill="0.00", book_side="ask"))
    order = _writer(t).place_limit(_intent(side="no", prob=Decimal("0.44"),
                                           contracts=Decimal("10")))
    assert order.side == "no" and order.prob == Decimal("0.4400")


def test_the_echo_check_compares_remaining_and_fill_as_decimals():
    # "10" and "10.00" are the same count; a string comparison would call this a mismatch.
    t = FakeTransport(queued=_accepted(price="0.5600", count="10", remaining="10", fill="0"))
    assert _writer(t).place_limit(_intent(contracts=Decimal("10.00"))) is not None


def test_the_returned_venue_order_carries_the_response_counts_and_the_response_body():
    # Fix 24: the counts and `raw` come from the create response -- the venue's answer to the
    # message we sent -- while the ticker, status and group come from the confirmed order.
    t = FakeTransport(queued=[
        _ok(_created(count="10.00", remaining="7.00", fill="3.00")),
        _ok({"order": _echo_of(price="0.5600", count="10.00", remaining="9.00", fill="1.00")})])
    order = _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert isinstance(order, VenueOrder)
    assert order.remaining_count == Decimal("7.00") and order.fill_count == Decimal("3.00")
    assert order.status == "resting" and order.order_group_id == "g1"
    assert order.raw["order_id"] == "o1" and order.raw["ts_ms"] == 1789000000000


def test_an_echo_with_only_an_outcome_side_still_decodes_into_our_space():
    order = _echo_of(price="0.5600", count="10.00")
    order.pop("book_side")
    order["outcome_side"] = "yes"
    t = FakeTransport(queued=[_ok(_created(count="10.00")), _ok({"order": order})])
    placed = _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert placed.side == "yes" and placed.prob == Decimal("0.5600")


def test_a_mangled_soft_field_does_not_abandon_a_placed_order():
    """`average_fill_price` and `ts_ms` are neither compared nor stored, so a bad one decodes to
    None through `_soft`. Raising instead would walk away from an order the venue has already
    accepted, before the echo check has had the chance to cancel it."""
    t = FakeTransport(queued=_accepted(
        price="0.5600", count="10.00",
        created={"average_fill_price": "NaN", "ts_ms": "not-an-integer"}))
    order = _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert order.side == "yes" and order.prob == Decimal("0.5600")


def test_a_count_the_venue_mangled_cancels_and_freezes_rather_than_raising():
    """Fix round 1, Important 2. A garbled count used to raise `KalshiDecodeError` straight out
    of the decoder, which left an order the venue had already accepted resting with no cancel
    and no freeze. It is a mismatch instead: the count arrives as None and `_count_mismatch`
    says so."""
    t = FakeTransport(queued=[
        _ok(_created(count="10.00", fill="NaN")), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert exc.value.field == "the echoed counts"
    assert t.calls[1][0] == "DELETE" and t.calls[1][1].endswith("/o1")


def test_the_live_order_field_names_confirm_a_place_end_to_end():
    """Fix round 1, Important 1. The single-order GET sends `yes_price_dollars` and the `_fp`
    counts; the decoder read `price`/`count`/... only, so every confirming read would have come
    back with a None price and frozen the market on a perfectly good order."""
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")),
        _ok({"order": _live_order_of(price="0.5600", count="10.00")})])
    order = _writer(t).place_limit(_intent(side="yes", prob=Decimal("0.56"),
                                           contracts=Decimal("10")))
    assert order.side == "yes" and order.prob == Decimal("0.5600")
    assert order.contracts == Decimal("10.00") and order.status == "resting"
    assert [c[0] for c in t.calls] == ["POST", "GET"]        # confirmed, never cancelled


def test_the_no_leg_confirms_from_the_yes_price_in_the_live_vocabulary():
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")),
        _ok({"order": _live_order_of(price="0.5600", count="10.00", book_side="ask")})])
    order = _writer(t).place_limit(_intent(side="no", prob=Decimal("0.44"),
                                           contracts=Decimal("10")))
    assert order.side == "no" and order.prob == Decimal("0.4400")


def test_an_order_carrying_only_a_no_price_stays_unconfirmed_and_is_cancelled():
    """`decode_side_price` is defined on the YES leg. Inverting `no_price_dollars` here would put
    a second, unpinned conversion in front of the echo check's own, so the price stays None and
    the order is cancelled -- the right answer to a price we cannot place on the compared leg."""
    order = _live_order_of(price="0.5600", count="10.00")
    order.pop("yes_price_dollars")
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _ok({"order": order}), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert exc.value.field == "the confirmed price"
    assert t.calls[2][0] == "DELETE"


def test_both_order_field_vocabularies_decode_to_the_same_view():
    old = _decode_order(_echo_of(price="0.5600", count="10.00", remaining="7.00", fill="3.00"))
    new = _decode_order(_live_order_of(price="0.5600", count="10.00", remaining="7.00",
                                       fill="3.00"))
    assert (old.price, old.count, old.remaining_count, old.fill_count) == (
        new.price, new.count, new.remaining_count, new.fill_count)


def test_the_explicit_field_names_win_when_the_venue_sends_both():
    """Fix round 3. `yes_price_dollars` and `count_fp` state their units; `price` and `count` do
    not. A payload carrying both with different values is one where letting the bare legacy name
    win would fail the echo check and freeze the market on a good order."""
    order = _live_order_of(price="0.5600", count="10.00", remaining="7.00", fill="3.00")
    order.update({"price": "0.9900", "count": "99.00",
                  "remaining_count": "90.00", "fill_count": "9.00"})
    view = _decode_order(order)
    assert view.price == Decimal("0.5600") and view.count == Decimal("10.00")
    assert view.remaining_count == Decimal("7.00") and view.fill_count == Decimal("3.00")


# --- the confirming read's one retry (fix round 1, ruling (b)) --------------------------------


def test_a_confirming_read_that_fails_once_is_retried_and_confirms_the_order():
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")),
        _err(500),                                       # the first confirming GET
        _ok({"order": _echo_of(price="0.5600", count="10.00")})])
    order = _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert order.side == "yes" and order.prob == Decimal("0.5600")
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET"]      # no cancel
    assert [c[1] for c in t.calls[1:]] == ["/portfolio/orders/o1"] * 2


def test_a_confirming_read_that_fails_twice_cancels_and_freezes():
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _err(500), _err(500), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert "the confirming read" in exc.value.field and "KalshiApiError" in exc.value.field
    assert exc.value.freeze_minutes == 15 and exc.value.reason == "echo_mismatch"
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET", "DELETE"]


def test_the_confirming_read_is_never_retried_more_than_once():
    from harness.venues.kalshi.authed import CONFIRM_READ_ATTEMPTS
    assert CONFIRM_READ_ATTEMPTS == 2
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _err(500), _err(500), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch):
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert sum(1 for c in t.calls if c[0] == "GET") == CONFIRM_READ_ATTEMPTS


def test_a_retried_confirming_read_spends_no_write_token():
    # The retry is a GET on the reader; the 60-a-minute budget is for messages we send.
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _err(500),
        _ok({"order": _echo_of(price="0.5600", count="10.00")})])
    w = _writer(t)
    before = w._bucket.tokens
    w.place_limit(_intent(contracts=Decimal("10")))
    assert before - w._bucket.tokens == pytest.approx(1.0, abs=1e-6)


def test_the_amend_path_retries_its_confirming_read_too():
    t = FakeTransport(queued=[
        _ok(_created(count="12.00")), _err(500),
        _ok({"order": _echo_of(price="0.5700", count="12.00")})])
    order = _writer(t).amend("o1", Decimal("0.57"), Decimal("12"), "c1", "c2",
                             ticker="KXNFLGAME-X", side="yes", exchange_index=0,
                             price_ranges=CENT_RANGES)
    assert order.prob == Decimal("0.5700")
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET"]


# --- a 404 is read-after-write lag, not a refusal (fix 27) ------------------------------------
#
# The demo venue answered the confirming read 404 twice, 130 ms and 210 ms after returning 201
# for the create, and then answered the cancel on that same id with 200 -- so the order existed
# throughout and the reads simply lost the race. These tests pin the slept schedule that fixes
# that, and pin that nothing else changed policy. No test here sleeps: the writer's sleep is
# injected, and every schedule below is asserted from what it was asked to sleep.


def test_a_confirming_read_that_404s_twice_then_answers_confirms_with_the_backoff():
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")),
        _err(404, "order_not_found"),                     # 130 ms after the create, as measured
        _err(404, "order_not_found"),
        _ok({"order": _echo_of(price="0.5600", count="10.00")})])
    order = _writer(t, sleep=slept.append).place_limit(_intent(contracts=Decimal("10")))
    assert order.side == "yes" and order.prob == Decimal("0.5600")
    assert slept == [0.25, 0.5]                           # the first two of the schedule, no more
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET", "GET"]      # confirmed, no cancel
    assert [c[1] for c in t.calls[1:]] == ["/portfolio/orders/o1"] * 3


def test_four_404s_cancel_the_created_order_and_freeze_naming_the_status_and_count():
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")),
        _err(404), _err(404), _err(404), _err(404),
        _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t, sleep=slept.append).place_limit(_intent(contracts=Decimal("10")))
    # The exact text the brief names: "4" alone is satisfied by the "404", so it is pinned whole.
    assert exc.value.field == "the confirming read (404 after 4 attempts)"
    assert exc.value.order_id == "o1" and exc.value.cancel_error is None
    assert exc.value.freeze_minutes == 15 and exc.value.reason == "echo_mismatch"
    assert slept == [0.25, 0.5, 0.75]                     # 1.5 s of sleeping at the very most
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET", "GET", "GET", "DELETE"]
    assert t.calls[-1][1] == "/portfolio/events/orders/o1"   # cancelled on the create's id


def test_the_404_schedule_is_the_documented_constant():
    from harness.venues.kalshi.authed import CONFIRM_NOT_FOUND_BACKOFF_S
    assert CONFIRM_NOT_FOUND_BACKOFF_S == (0.25, 0.5, 0.75)
    assert sum(CONFIRM_NOT_FOUND_BACKOFF_S) <= 1.5


def test_a_non_404_failure_keeps_the_one_immediate_retry_and_never_sleeps():
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _err(500),
        _ok({"order": _echo_of(price="0.5600", count="10.00")})])
    order = _writer(t, sleep=slept.append).place_limit(_intent(contracts=Decimal("10")))
    assert order.prob == Decimal("0.5600")
    assert slept == []
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET"]


def test_two_non_404_failures_cancel_after_two_attempts_and_never_sleep():
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _err(500), _err(500), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t, sleep=slept.append).place_limit(_intent(contracts=Decimal("10")))
    assert "KalshiApiError" in exc.value.field and "404" not in exc.value.field
    assert slept == []
    assert sum(1 for c in t.calls if c[0] == "GET") == 2
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET", "DELETE"]


def test_a_404_retry_spends_no_write_token():
    # Every retry is a GET on the reader; the 60-a-minute budget is for messages we send.
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _err(404), _err(404),
        _ok({"order": _echo_of(price="0.5600", count="10.00")})])
    w = _writer(t, sleep=lambda _s: None)
    before = w._bucket.tokens
    w.place_limit(_intent(contracts=Decimal("10")))
    assert before - w._bucket.tokens == pytest.approx(1.0, abs=1e-6)


def test_the_amend_path_gets_the_same_404_backoff():
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="12.00")), _err(404), _err(404),
        _ok({"order": _echo_of(price="0.5700", count="12.00")})])
    order = _writer(t, sleep=slept.append).amend(
        "o1", Decimal("0.57"), Decimal("12"), "c1", "c2",
        ticker="KXNFLGAME-X", side="yes", exchange_index=0, price_ranges=CENT_RANGES)
    assert order.prob == Decimal("0.5700")
    assert slept == [0.25, 0.5]
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET", "GET"]


def test_a_read_that_will_not_decode_gets_no_backoff_either():
    # The backoff is for a read that did not arrive. One that arrived and is unreadable is the
    # payload the venue meant to send, and asking again buys the same answer.
    slept = []
    order = _echo_of(price="0.5600", count="10.00")
    order.pop("book_side")
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")), _ok({"order": order}), _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t, sleep=slept.append).place_limit(_intent(contracts=Decimal("10")))
    assert "KalshiDecodeError" in exc.value.field
    assert slept == []
    assert [c[0] for c in t.calls] == ["POST", "GET", "DELETE"]


def test_the_writer_sleeps_with_time_sleep_when_nothing_is_injected():
    import time

    assert "sleep" in inspect.signature(KalshiWriter.__init__).parameters
    assert _writer(FakeTransport())._sleep is time.sleep


# --- a read is fresh only when it carries the id we sent (fix 28) -----------------------------
#
# Measured twice on the demo venue, 2026-09-09 13:23 and 13:25 UTC (evidence
# `docs/superpowers/autopilot/evidence/2026-09-09-demo-amend-diag-0823.txt` and `-0825.txt`).
# The amend answered 200 with the same order id and the `updated_client_order_id` we sent, and
# the confirming read then came back 200 carrying the *original* client id, the old price and
# the old count for another 0.3 to 0.9 s. The read model lags the matching engine after an
# amend exactly as it does after a create -- fix 27's 404s -- but the row already exists, so the
# lag surfaces as a stale 200 instead of a 404 and the echo check froze a perfectly good order.
#
# Price and count cannot tell "stale" from "wrong". The client id can: the venue assigns it on
# the write, so a read that still shows the previous one has not seen the write yet.
#
# The amend response's own `client_order_id` is deliberately not the expectation. The Kalshi
# changelog (2026-05-21) says V2 amend and cancel responses may carry incorrect order details
# even when the operation executed correctly, so the expectation is the id *we* sent.


def _amend(writer, price="0.02", count="2", updated="c2"):
    return writer.amend("o1", Decimal(price), Decimal(count), "c1", updated,
                        ticker="KXNFLGAME-X", side="yes", exchange_index=0,
                        price_ranges=CENT_RANGES)


def test_an_amend_read_still_showing_the_old_client_id_is_retried_until_it_is_fresh():
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="2.00")),
        # +282 ms: the old id, the old price, the old count -- the read model has not caught up.
        _ok({"order": _echo_of(price="0.0100", count="1.00", client_order_id="c1")}),
        # +895 ms: the id the amend assigned, and the amended price.
        _ok({"order": _echo_of(price="0.0200", count="2.00", client_order_id="c2")})])
    order = _amend(_writer(t, sleep=slept.append))
    assert order.side == "yes" and order.prob == Decimal("0.0200")
    assert slept == [0.25]                                   # one wait, then confirmed
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET"]  # confirmed, never cancelled


def test_four_stale_reads_cancel_the_amended_order_and_freeze_naming_stale_and_the_count():
    slept = []
    t = FakeTransport(
        queued=[_ok(_created(count="2.00"))]
        + [_ok({"order": _echo_of(price="0.0100", count="1.00", client_order_id="c1")})
           for _ in range(4)]
        + [_ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _amend(_writer(t, sleep=slept.append))
    assert exc.value.field == "the confirming read (stale after 4 attempts)"
    assert exc.value.order_id == "o1" and exc.value.cancel_error is None
    assert exc.value.freeze_minutes == 15 and exc.value.reason == "echo_mismatch"
    assert slept == [0.25, 0.5, 0.75]                    # the same 1.5 s ceiling as the 404s
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET", "GET", "GET", "DELETE"]
    assert t.calls[-1][1] == "/portfolio/events/orders/o1"


def test_a_fresh_read_at_the_wrong_price_cancels_on_the_first_read_with_no_retry():
    # The freshness retry is for a read that has not seen the write. A read that *has* seen it
    # and disagrees is the venue's final answer, and sleeping on it only delays the cancel.
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="2.00")),
        _ok({"order": _echo_of(price="0.0300", count="2.00", client_order_id="c2")}),
        _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _amend(_writer(t, sleep=slept.append))
    assert exc.value.field == "prob"
    assert slept == []
    assert [c[0] for c in t.calls] == ["POST", "GET", "DELETE"]


def test_a_fresh_read_on_the_flipped_side_cancels_on_the_first_read_too():
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="2.00")),
        _ok({"order": _echo_of(price="0.9800", count="2.00", book_side="ask",
                               client_order_id="c2")}),
        _ok({"order_id": "o1"})])
    with pytest.raises(EchoMismatch) as exc:
        _amend(_writer(t, sleep=slept.append))
    assert exc.value.field == "side"
    assert slept == []
    assert [c[0] for c in t.calls] == ["POST", "GET", "DELETE"]


def test_the_attempt_budget_is_shared_between_a_404_and_a_stale_read():
    # Four reads in all, whichever mix of the two lags produces them: a venue that answers one
    # of each must not earn eight.
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")),
        _err(404, "order_not_found"),
        _ok({"order": _echo_of(price="0.5600", count="10.00", client_order_id="an-older-id")}),
        _ok({"order": _echo_of(price="0.5600", count="10.00",
                               client_order_id="11111111-1111-1111-1111-111111111111")})])
    order = _writer(t, sleep=slept.append).place_limit(_intent(contracts=Decimal("10")))
    assert order.side == "yes" and order.prob == Decimal("0.5600")
    assert slept == [0.25, 0.5]
    assert [c[0] for c in t.calls] == ["POST", "GET", "GET", "GET"]


def test_a_read_that_carries_no_client_order_id_at_all_is_taken_as_fresh():
    """A venue that stops sending the id must not earn four sleeps and a freeze on every order.
    With nothing to compare, the read is compared on side and price exactly as it was before."""
    slept = []
    t = FakeTransport(queued=[
        _ok(_created(count="10.00")),
        _ok({"order": _echo_of(price="0.5600", count="10.00")})])
    order = _writer(t, sleep=slept.append).place_limit(_intent(contracts=Decimal("10")))
    assert order.prob == Decimal("0.5600")
    assert slept == []
    assert [c[0] for c in t.calls] == ["POST", "GET"]


def test_the_amended_size_comes_from_the_amend_response_not_the_reads_initial_count():
    """`initial_count_fp` is the size at placement and does not follow an amend: the venue was
    measured reporting 1.00 there while `remaining_count_fp` had moved to 2.00. So
    `VenueOrder.contracts` is `fill_count + remaining_count` from the response the venue sent
    when it accepted the write -- the pair the count arithmetic already checked."""
    t = FakeTransport(queued=[
        _ok(_created(count="2.00")),
        _ok({"order": _live_order_of(price="0.0200", count=None, remaining="2.00",
                                     client_order_id="c2", initial_count_fp="1.00")})])
    order = _amend(_writer(t))
    assert order.contracts == Decimal("2.00")
    assert order.remaining_count == Decimal("2.00") and order.fill_count == Decimal("0.00")


# --- the amend knows the id it addressed (fix round 1, Minor) ---------------------------------


def test_an_amend_whose_response_names_no_order_cancels_the_id_it_addressed():
    t = FakeTransport(queued=[
        _ok(_created(count="12.00", order_id=None)), _ok({"order_id": "ov1"})])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).amend("ov1", Decimal("0.57"), Decimal("12"), "c1", "c2",
                         ticker="KXNFLGAME-X", side="yes", exchange_index=0,
                         price_ranges=CENT_RANGES)
    assert exc.value.field == "order_id"
    assert exc.value.cancel_error is None and exc.value.order_id == "ov1"
    assert t.calls[1][0] == "DELETE" and t.calls[1][1].endswith("/ov1")


def test_a_place_whose_response_names_no_order_still_has_nothing_to_cancel():
    # A place has no id of its own to fall back on: it was asking the venue to make one.
    t = FakeTransport(queued=[_ok(_created(count="10.00", order_id=None))])
    with pytest.raises(EchoMismatch) as exc:
        _writer(t).place_limit(_intent(contracts=Decimal("10")))
    assert exc.value.cancel_error == "no order_id to cancel"
    assert len(t.calls) == 1


def test_a_create_response_wrapped_in_an_order_key_decodes_the_same_way():
    t = FakeTransport(queued=[
        _ok({"order": _created(count="10.00")}),
        _ok({"order": _echo_of(price="0.5600", count="10.00")})])
    assert _writer(t).place_limit(_intent(contracts=Decimal("10"))).order_id == "o1"


# --- token bucket (9.2) -----------------------------------------------------------------------


def test_the_budget_is_sixty_order_messages_a_minute():
    assert ORDER_MESSAGES_PER_MINUTE == 60


def test_token_bucket_allows_sixty_messages_a_minute():
    clock = _FakeMonotonic()
    bucket = TokenBucket(60, monotonic=clock)
    for _ in range(60):
        bucket.take()
    with pytest.raises(MessageBudgetExceeded):
        bucket.take()


def test_token_bucket_refills_over_time():
    clock = _FakeMonotonic()
    bucket = TokenBucket(60, monotonic=clock)
    for _ in range(60):
        bucket.take()
    clock.advance(60)
    for _ in range(60):
        bucket.take()


def test_token_bucket_refills_gradually_and_never_over_its_capacity():
    clock = _FakeMonotonic()
    bucket = TokenBucket(60, monotonic=clock)
    for _ in range(60):
        bucket.take()
    clock.advance(10)                 # 10 s at one per second
    for _ in range(10):
        bucket.take()
    with pytest.raises(MessageBudgetExceeded):
        bucket.take()
    clock.advance(600)                # ten minutes idle refills to the cap, not past it
    for _ in range(60):
        bucket.take()
    with pytest.raises(MessageBudgetExceeded):
        bucket.take()


def test_a_sixty_first_order_message_in_a_minute_raises():
    # Each accepted place now consumes two responses; only the POST spends a token, because
    # the confirming read is a GET on the reader (fix 24).
    t = FakeTransport(queued=[r for _ in range(61) for r in _accepted()])
    w = _writer(t)
    for _ in range(60):
        w.place_limit(_intent())
    with pytest.raises(MessageBudgetExceeded):
        w.place_limit(_intent())


def test_cancel_and_amend_also_spend_a_token():
    t = FakeTransport(queued=[_ok({"order_id": "o1"}) for _ in range(61)])
    w = _writer(t)
    for _ in range(60):
        w.cancel("o1", ticker="T", exchange_index=0)
    with pytest.raises(MessageBudgetExceeded):
        w.cancel("o1", ticker="T", exchange_index=0)


def test_the_budget_check_runs_after_the_pre_send_invariant():
    # A rejected order must not spend a token: the budget is for messages that go out.
    t = FakeTransport(queued=[r for _ in range(60) for r in _accepted()])
    w = _writer(t, contract_cap=Decimal("20"))
    for _ in range(30):
        with pytest.raises(PreSendInvariantFailed):
            w.place_limit(_intent(contracts=Decimal("50")))
    for _ in range(60):
        w.place_limit(_intent())


# --- fee model (1.3) ---------------------------------------------------------------------------


def test_fee_model_is_read_from_series_once_and_cached():
    t = FakeTransport(queued=[_ok({"series": {"fee_type": "quadratic_with_maker_fees",
                                              "fee_multiplier": 1}})])
    w = _writer(t)
    model = w.fee_model_for("KXNFLGAME-26SEP13ABCDEF-ABC")
    assert model.maker_rate == Decimal("0.0175") and model.taker_rate == Decimal("0.07")
    w.fee_model_for("KXNFLGAME-26SEP13ABCDEF-ABC")     # cached: no second call
    assert len(t.calls) == 1
    assert t.calls[0][:2] == ("GET", "/series/KXNFLGAME")


def test_fee_model_rejects_an_unexpected_football_fee_shape():
    t = FakeTransport(queued=[_ok({"series": {"fee_type": "quadratic", "fee_multiplier": 1}})])
    with pytest.raises(AssertionError, match="maker"):
        _writer(t).fee_model_for("KXNCAAFGAME-X")


def test_fee_model_keeps_a_series_multiplier_as_a_decimal():
    t = FakeTransport(queued=[_ok({"series": {"fee_type": "quadratic_with_maker_fees",
                                              "fee_multiplier": "0.5"}})])
    model = _writer(t).fee_model_for("KXOTHER-X")
    assert model.multiplier == Decimal("0.5")


def test_fee_model_rejects_a_football_series_with_no_fee_type():
    # fee_model_for answers a missing fee_type with the football model itself, so an empty
    # /series/ body would otherwise walk straight through the D14 guard.
    t = FakeTransport(queued=[_ok({"series": {}})])
    with pytest.raises(FeeModelMismatch, match="fee_type"):
        _writer(t).fee_model_for("KXNFLGAME-X")


def test_fee_model_rejects_a_football_series_repriced_by_a_multiplier():
    t = FakeTransport(queued=[_ok({"series": {"fee_type": "quadratic_with_maker_fees",
                                              "fee_multiplier": "2"}})])
    with pytest.raises(FeeModelMismatch, match="multiplier"):
        _writer(t).fee_model_for("KXNFLGAME-X")


def test_the_fee_guard_is_an_explicit_raise_not_a_bare_assert():
    # python -O strips `assert`; this guard must survive it. FeeModelMismatch subclasses
    # AssertionError so the D14 comparison's `pytest.raises(AssertionError)` still holds.
    import harness.venues.kalshi.authed as mod
    source = inspect.getsource(mod._require_football_fees)
    assert "assert " not in source
    assert issubclass(FeeModelMismatch, AssertionError)


def test_the_fee_guard_runs_again_on_a_cache_hit():
    t = FakeTransport(queued=[_ok({"series": {}})])
    w = _writer(t)
    for _ in range(2):
        with pytest.raises(FeeModelMismatch):
            w.fee_model_for("KXNFLGAME-X")
    assert len(t.calls) == 1


def test_fee_model_does_not_assert_football_rates_on_another_series():
    t = FakeTransport(queued=[_ok({"series": {"fee_type": "quadratic", "fee_multiplier": 1}})])
    model = _writer(t).fee_model_for("KXOTHER-X")
    assert model.maker_rate == Decimal("0") and model.taker_rate == Decimal("0.07")


def test_fee_model_reads_series_through_the_reader_not_the_writer():
    # 1.3: limits and series are read on the reader; the writer holds no GET of its own.
    for name in ("get_series", "get_account_limits", "get_orders", "get_fills"):
        assert not hasattr(KalshiWriter, name)
