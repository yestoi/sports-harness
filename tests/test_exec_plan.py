"""The executor's pure decisions: both rule chains, both sides, one fixed `now`.

Every fixture is built in the *side's own* probability space and converted to the YES space
the market and the order actually store, so one assertion reads the same for a YES order and
for its NO mirror. No database: `plan_actions` and `rebuild_state` are pure functions.
"""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from harness.execution import EXECUTOR_VERSION
from harness.execution.book import BookState, opp, side_p
from harness.execution.plan import (
    CapGate,
    Cancel,
    ExecSettings,
    Expire,
    FillView,
    IntentView,
    MarketNow,
    OpenOrderView,
    Place,
    PositionView,
    Skip,
    cap_labels,
    config_hash,
    confidently_matched,
    first_false_cap,
    plan_actions,
    rebuild_state,
)
from harness.strategy.run import StrategyState

NOW = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
KICKOFF = NOW + timedelta(hours=3)
SIDES = ["yes", "no"]
S = ExecSettings()
ONE = Decimal("1")


def dec(x) -> Decimal:
    return Decimal(str(x))


def uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def cfg(apply_caps=False, **over) -> dict:
    """The exec-relevant slice of a variant config (`sharp_direct` numbers)."""
    base = {"stale_s": 180, "apply_caps": apply_caps, "bankroll": 3000, "per_bet_cap": 0.03,
            "per_game_cap": 0.05, "daily_cap": 0.15, "max_open": 25}
    base.update(over)
    return base


VARIANTS = {"v1": cfg(), "v2": cfg(apply_caps=True)}


def book_for(side, bid="0.48", ask="0.52", dirty=False, as_of=NOW, size="100") -> BookState:
    """A two-sided book whose best bid/ask on `side` are `bid`/`ask` in that side's space."""
    ladders = {side: {dec(bid): dec(size)}, opp(side): {ONE - dec(ask): dec(size)}}
    return BookState(ticker="K1", yes_bids=ladders["yes"], no_bids=ladders["no"], sid=2, seq=5,
                     as_of=as_of, source="ws", anchor_id=10, last_event_id=10, dirty=dirty)


def market(side="yes", vm_id=1, fair="0.60", mid="0.50", bid="0.48", ask="0.52", fair_ts=NOW,
           book="auto", book_dirty=False, matched=True, match_key="k1", stale_allowance_s=0,
           staleness_s=30) -> MarketNow:
    """`fair`, `mid`, `bid` and `ask` are given in `side`'s space; the row stores YES space.

    The book defaults to one agreeing with the quote, the ordinary case; `book=None` is R10's
    market with no book at all.
    """
    if book == "auto":
        book = book_for(side, bid=bid, ask=ask)
    def to_yes(v):
        return None if v is None else side_p(dec(v), side)

    bid_yes, ask_yes = (to_yes(bid), to_yes(ask)) if side == "yes" else (to_yes(ask), to_yes(bid))
    return MarketNow(venue_market_id=vm_id, ticker="K1", fair_p=to_yes(fair), fair_ts=fair_ts,
                     fair_row_id=7, staleness_s=staleness_s, stale_allowance_s=stale_allowance_s,
                     feed_kind="featured", best_bid_yes=bid_yes, best_ask_yes=ask_yes,
                     mid_yes=to_yes(mid), book=book, book_dirty=book_dirty, matched=matched,
                     match_key=match_key)


def intent(side="yes", variant="v1", vm_id=1, prob="0.45", contracts="20", edge="0.03",
           stake="60", game_id=11, kickoff=KICKOFF, decision="candidate", n=1,
           created=None) -> IntentView:
    return IntentView(intent_id=uid(n), signal_id=100 + n, variant_id=variant,
                      venue_market_id=vm_id, ticker="K1", side=side,
                      target_prob=None if prob is None else dec(prob),
                      target_contracts=None if contracts is None else dec(contracts),
                      edge=dec(edge), edge_min=dec("0.02"),
                      fair_p=dec("0.60"), game_id=game_id, kickoff_utc=kickoff, stake=dec(stake),
                      signal_created_at=created or NOW - timedelta(seconds=5),
                      latest_decision=decision)


def order(side="yes", variant="v1", vm_id=1, prob="0.45", contracts="20", oid=1, n=1,
          expiry=None, venue_mid="0.50", fair_at_place="0.60", edge_min="0.02", as_at="0.01",
          match_key="k1", stake="60", game_id=11, filled="0") -> OpenOrderView:
    """`venue_mid` and `fair_at_place` are given in `side`'s space; the row stores YES space."""
    return OpenOrderView(order_id=oid, intent_id=uid(n), variant_id=variant, ticker="K1",
                         venue_market_id=vm_id, side=side, prob=dec(prob),
                         contracts=dec(contracts), filled_contracts=dec(filled),
                         placed_at=NOW - timedelta(minutes=5),
                         expiry=expiry if expiry is not None else KICKOFF - timedelta(minutes=10),
                         fair_p_at_place=side_p(dec(fair_at_place), side),
                         venue_mid_at_place=side_p(dec(venue_mid), side),
                         edge_min_at_place=dec(edge_min), as_at_place=dec(as_at),
                         kickoff_utc=KICKOFF, game_id=game_id, stake=dec(stake),
                         match_key=match_key)


def plan(intents=(), orders=(), markets=None, states=None, variants=None, kill=False, now=NOW,
         settings=S):
    if markets is None:
        sides = {i.side for i in intents} | {o.side for o in orders}
        markets = {1: market(side=sides.pop() if len(sides) == 1 else "yes")}
    return plan_actions(list(intents), list(orders), markets,
                        states if states is not None else {}, variants or VARIANTS, kill, now,
                        settings)


# --- versions and the config hash -------------------------------------------------


def test_executor_version_is_bumped_for_plan():
    assert EXECUTOR_VERSION == "3.4"


def test_config_hash_changes_with_settings_and_version(monkeypatch):
    base = config_hash("v1", ExecSettings())
    assert len(base) == 64
    assert config_hash("v1", ExecSettings()) == base
    assert config_hash("v2", ExecSettings()) != base
    assert config_hash("v1", ExecSettings(period_s=30)) != base
    assert config_hash("v1", ExecSettings(cancel_venue_move_pts=Decimal("0.03"))) != base
    monkeypatch.setattr("harness.execution.EXECUTOR_VERSION", "9.9")
    assert config_hash("v1", ExecSettings()) != base


def test_exec_settings_from_settings_reads_every_field(env_settings):
    s = ExecSettings.from_settings(env_settings)
    assert (s.period_s, s.kickoff_cutoff_min, s.max_open_orders) == (15, 10, 150)
    assert (s.intent_ttl_s, s.book_max_age_s) == (900, 120)
    assert s.cancel_venue_move_pts == Decimal("0.02")
    assert s.reprice_fair_move_pts == Decimal("0.01")
    assert ExecSettings() == s


def test_no_renew_action_exists():
    from harness.execution import plan as plan_module

    assert not hasattr(plan_module, "Renew")
    assert not any("renew" in name.lower() for name in dir(plan_module))


# --- MarketNow reads both spaces --------------------------------------------------


@pytest.mark.parametrize("side", SIDES)
def test_market_now_reads_side_space_from_the_book_then_the_quote(side):
    quote_only = market(side=side, mid="0.46", bid="0.40", ask="0.52", book=None)
    assert quote_only.best_ask(side) == Decimal("0.5200")
    # The ask on the other side is the complement of our own 0.40 bid: one book, two spaces.
    assert quote_only.best_ask(opp(side)) == Decimal("0.6000")
    assert quote_only.mid(side) == Decimal("0.4600")
    assert quote_only.mid(opp(side)) == Decimal("0.5400")
    booked = market(side=side, mid="0.90", bid="0.10", ask="0.90",
                    book=book_for(side, bid="0.48", ask="0.52"))
    # The book wins wherever it exists: the stale quote said 0.90.
    assert booked.best_ask(side) == Decimal("0.5200")
    assert booked.mid(side) == Decimal("0.5000")


def test_fair_age_s_is_the_executors_own_freshness_quantity():
    m = market(fair_ts=NOW - timedelta(seconds=90))
    assert m.fair_age_s(NOW) == 90
    assert m.staleness_s == 30  # the signal's pricing-time value, never recomputed here
    assert market(fair_ts=None).fair_age_s(NOW) is None


def test_confidently_matched_follows_the_strategys_constant():
    assert confidently_matched("matched") and confidently_matched("manual")
    assert not confidently_matched("fuzzy")
    assert not confidently_matched(None)


# --- open-order chain, in the order the rules are written -------------------------


@pytest.mark.parametrize("side", SIDES)
def test_expire_precedes_kill(side):
    o = order(side=side, expiry=NOW - timedelta(seconds=1))
    assert plan(orders=[o], kill=True, markets={1: market(side=side)}) == [Expire(1)]
    assert plan(orders=[o], kill=False, markets={1: market(side=side)}) == [Expire(1)]


@pytest.mark.parametrize("side", SIDES)
def test_kill_cancels_the_open_order_and_skips_its_intent(side):
    actions = plan(intents=[intent(side=side, vm_id=2)], orders=[order(side=side)],
                   markets={1: market(side=side), 2: market(side=side, vm_id=2)}, kill=True)
    assert actions == [Cancel(1, "kill_switch"), Skip(uid(1), "kill_switch")]


@pytest.mark.parametrize("side", SIDES)
def test_unmatched_cancels_the_order_and_skips_the_intent(side):
    m = {1: market(side=side, matched=False), 2: market(side=side, vm_id=2, matched=False)}
    actions = plan(intents=[intent(side=side, vm_id=2, n=2)], orders=[order(side=side)],
                   markets=m)
    assert actions == [Cancel(1, "unmatched"), Skip(uid(2), "unmatched")]


@pytest.mark.parametrize("side", SIDES)
def test_open_order_cancels_unmatched_when_match_key_changes(side):
    m = {1: market(side=side, match_key="k2")}
    assert plan(orders=[order(side=side, match_key="k1")], markets=m) == [Cancel(1, "unmatched")]
    assert plan(orders=[order(side=side, match_key="k2")], markets=m) == []


@pytest.mark.parametrize("side", SIDES)
def test_unmatched_precedes_the_dirty_book_hold(side):
    m = {1: market(side=side, matched=False, book=book_for(side, dirty=True))}
    assert plan(orders=[order(side=side)], markets=m) == [Cancel(1, "unmatched")]


@pytest.mark.parametrize("side", SIDES)
def test_dirty_book_holds_order_and_skips_intent(side):
    m = {1: market(side=side, fair="0.30", book=book_for(side, dirty=True)),
         2: market(side=side, vm_id=2, book=book_for(side, dirty=True))}
    # fair 0.30 would decay the order's edge: the hold precedes that rule and nothing is emitted.
    actions = plan(intents=[intent(side=side, vm_id=2, n=2)], orders=[order(side=side)],
                   markets=m)
    assert actions == [Skip(uid(2), "book_dirty")]


@pytest.mark.parametrize("side", SIDES)
def test_a_book_older_than_book_max_age_reads_dirty(side):
    old = book_for(side, as_of=NOW - timedelta(seconds=121))
    m = {1: market(side=side, fair="0.30", book=old)}
    assert plan(orders=[order(side=side)], markets=m) == []
    fresh = book_for(side, as_of=NOW - timedelta(seconds=119))
    m = {1: market(side=side, fair="0.30", book=fresh)}
    assert plan(orders=[order(side=side)], markets=m) == [Cancel(1, "edge_decay")]


@pytest.mark.parametrize("side", SIDES)
def test_fair_stale_uses_allowance(side):
    old = NOW - timedelta(seconds=300)
    def markets(allowance):
        return {1: market(side=side, fair_ts=old, stale_allowance_s=allowance),
                2: market(side=side, vm_id=2, fair_ts=old, stale_allowance_s=allowance)}

    tight, loose = markets(0), markets(1000)
    args = {"intents": [intent(side=side, vm_id=2, n=2)], "orders": [order(side=side)]}
    # 300 s beats the variant's 180 s stale_s but not a 1000 s feed allowance.
    assert plan(markets=tight, **args) == [Cancel(1, "fair_stale"), Skip(uid(2), "fair_stale")]
    assert plan(markets=loose, **args) == [Place(uid(2), side, Decimal("0.4500"),
                                                 Decimal("20.00"), KICKOFF - timedelta(minutes=10),
                                                 False)]


@pytest.mark.parametrize("side", SIDES)
def test_a_market_without_a_fair_value_reads_stale(side):
    m = {1: market(side=side, fair=None, fair_ts=None)}
    assert plan(orders=[order(side=side)], markets=m) == [Cancel(1, "fair_stale")]


@pytest.mark.parametrize("side", SIDES)
def test_venue_move_cancels_the_order(side):
    # Placed against a 0.50 mid in the order's own space; 0.47 is 3 pts against it.
    def quote(mid):
        return {1: market(side=side, mid=mid, book=None)}

    assert plan(orders=[order(side=side)], markets=quote("0.47")) == [Cancel(1, "venue_move")]
    # Exactly 2 pts is already a cancel (<=); 1 pt is not.
    assert plan(orders=[order(side=side)], markets=quote("0.48")) == [Cancel(1, "venue_move")]
    assert plan(orders=[order(side=side)], markets=quote("0.49")) == []


@pytest.mark.parametrize("side", SIDES)
def test_venue_move_reads_the_book_when_there_is_one(side):
    m = {1: market(side=side, mid="0.50", book=book_for(side, bid="0.45", ask="0.49"))}
    assert plan(orders=[order(side=side)], markets=m) == [Cancel(1, "venue_move")]


@pytest.mark.parametrize("side", SIDES)
def test_edge_decay_cancels_the_order(side):
    # 0.47 - 0.45 - fee(0.0043) - as(0.01) = 0.0057, below edge_min_at_place / 2 = 0.01.
    assert plan(orders=[order(side=side)], markets={1: market(side=side, fair="0.47")}) == [
        Cancel(1, "edge_decay")]
    assert plan(orders=[order(side=side)], markets={1: market(side=side, fair="0.49")}) == []


@pytest.mark.parametrize("side", SIDES)
def test_signal_rejected_cancels_the_order(side):
    rejected = intent(side=side, decision="rejected")
    assert plan(intents=[rejected], orders=[order(side=side)]) == [Cancel(1, "signal_rejected")]


@pytest.mark.parametrize("side", SIDES)
def test_reprice_yields_cancel_then_place(side):
    moved = intent(side=side, prob="0.44", n=3)
    actions = plan(intents=[moved], orders=[order(side=side, prob="0.45")])
    assert actions == [Cancel(1, "reprice"),
                       Place(uid(3), side, Decimal("0.4400"), Decimal("20.00"),
                             KICKOFF - timedelta(minutes=10), False)]


@pytest.mark.parametrize("side", SIDES)
def test_a_fair_move_below_one_point_holds_the_order(side):
    held = intent(side=side, prob="0.4499")
    assert plan(intents=[held], orders=[order(side=side, prob="0.45")]) == []


def test_an_intent_whose_order_still_rests_is_not_placed_again():
    assert plan(intents=[intent()], orders=[order()]) == []


def test_an_order_cancelled_for_edge_decay_is_not_replaced_this_loop():
    actions = plan(intents=[intent()], orders=[order()], markets={1: market(fair="0.47")})
    assert actions == [Cancel(1, "edge_decay")]


# --- intent chain -----------------------------------------------------------------


@pytest.mark.parametrize("side", SIDES)
def test_kickoff_cutoff_skips_the_intent(side):
    near = NOW + timedelta(minutes=9)
    assert plan(intents=[intent(side=side, kickoff=near)], markets={1: market(side=side)}) == [
        Skip(uid(1), "kickoff")]
    far = NOW + timedelta(minutes=11)
    assert plan(intents=[intent(side=side, kickoff=far)], markets={1: market(side=side)}) == [
        Place(uid(1), side, Decimal("0.4500"), Decimal("20.00"), far - timedelta(minutes=10),
              False)]


@pytest.mark.parametrize("side", SIDES)
def test_post_only_reject_on_place_and_reprice(side):
    # A limit at or through the book's own ask would be rejected by a live post-only order.
    m = {1: market(side=side, bid="0.44", ask="0.45")}
    assert plan(intents=[intent(side=side, prob="0.45")], markets=m) == [
        Skip(uid(1), "post_only_reject")]
    # The same rule on the reprice path: the cancel stands, the replacement is not placed.
    actions = plan(intents=[intent(side=side, prob="0.45", n=4)],
                   orders=[order(side=side, prob="0.42", venue_mid="0.445")], markets=m)
    assert actions == [Cancel(1, "reprice"), Skip(uid(4), "post_only_reject")]
    assert plan(intents=[intent(side=side, prob="0.4499")], markets=m) == [
        Place(uid(1), side, Decimal("0.4499"), Decimal("20.00"), KICKOFF - timedelta(minutes=10),
              False)]


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("missing", ["prob", "contracts"])
def test_a_null_target_skips_rather_than_crashing_the_tick(side, missing):
    """An intent with no target price or size cannot be placed, and must not abort the tick."""
    null = intent(side=side, **{"prob" if missing == "prob" else "contracts": None})
    assert plan(intents=[null], markets={1: market(side=side)}) == [
        Skip(uid(1), "no_target")]
    # Every rule the brief puts before the target still runs and still names its own reason.
    assert plan(intents=[null], markets={1: market(side=side, matched=False)}) == [
        Skip(uid(1), "unmatched")]
    assert plan(intents=[null], markets={1: market(side=side)},
                now=KICKOFF - timedelta(minutes=9)) == [Skip(uid(1), "kickoff")]


@pytest.mark.parametrize("missing", ["prob", "contracts"])
def test_a_null_target_leaves_the_other_variants_alone(missing):
    """The skip is one intent's, not the tick's: every other variant still gets its action."""
    null = intent(variant="v1", vm_id=1, n=1, edge="0.09",
                  **{"prob" if missing == "prob" else "contracts": None})
    healthy = intent(variant="v2", vm_id=2, n=2, side="no", edge="0.03", game_id=12)
    markets = {1: market(vm_id=1), 2: market(vm_id=2, side="no")}
    assert plan(intents=[null, healthy], markets=markets) == [
        Skip(uid(1), "no_target"),
        Place(uid(2), "no", Decimal("0.4500"), Decimal("20.00"),
              KICKOFF - timedelta(minutes=10), False)]


@pytest.mark.parametrize("side", SIDES)
def test_no_book_places_with_flag(side):
    m = {1: market(side=side, book=None)}
    [action] = plan(intents=[intent(side=side)], markets=m)
    assert action == Place(uid(1), side, Decimal("0.4500"), Decimal("20.00"),
                           KICKOFF - timedelta(minutes=10), True)
    m = {1: market(side=side, book=book_for(side))}
    [action] = plan(intents=[intent(side=side)], markets=m)
    assert action.no_book is False


def test_cap_gate_recorded_for_every_variant_and_blocks_only_apply_caps():
    # daily_cap 0.15 x 3000 = 450; 440 already spent leaves no room for a 60 stake.
    states = {"v1": StrategyState(daily_exposure=Decimal("440")),
              "v2": StrategyState(daily_exposure=Decimal("440"))}
    markets = {1: market(vm_id=1), 2: market(vm_id=2, side="no")}
    intents = [intent(variant="v1", vm_id=1, n=1, game_id=11),
               intent(variant="v2", vm_id=2, n=2, game_id=12, side="no")]
    actions = plan(intents=intents, markets=markets, states=states)
    assert CapGate(uid(1), "cap_daily", False) in actions
    assert CapGate(uid(2), "cap_daily", True) in actions
    # v1 only labels the cap, so it still places; v2 enforces it and does not.
    placed = [a for a in actions if isinstance(a, Place)]
    assert [a.intent_id for a in placed] == [uid(1)]


def test_cap_gate_reports_the_first_false_label_in_cap_order():
    # cap_per_bet (0.03 x 3000 = 90) fails first even though cap_daily also fails.
    states = {"v2": StrategyState(daily_exposure=Decimal("440"))}
    actions = plan(intents=[intent(variant="v2", stake="500")], states=states)
    assert actions == [CapGate(uid(1), "cap_per_bet", True)]


def test_placements_accumulate_against_the_caps_within_one_loop():
    states = {"v2": StrategyState(daily_exposure=Decimal("330"))}
    markets = {i: market(vm_id=i) for i in (1, 2, 3)}
    intents = [intent(variant="v2", vm_id=i, n=i, game_id=10 + i, edge=f"0.0{6 - i}")
               for i in (1, 2, 3)]
    actions = plan(intents=intents, markets=markets, states=states)
    # 330 + 60 + 60 = 450 is exactly the daily cap; the third intent has no room left.
    assert [a.intent_id for a in actions if isinstance(a, Place)] == [uid(1), uid(2)]
    assert CapGate(uid(3), "cap_daily", True) in actions


def test_capacity_truncates_lowest_edge_first():
    markets = {i: market(vm_id=i) for i in (1, 2, 3)}
    intents = [intent(vm_id=1, n=1, edge="0.03", game_id=11),
               intent(vm_id=2, n=2, edge="0.05", game_id=12),
               intent(vm_id=3, n=3, edge="0.04", game_id=13)]
    actions = plan(intents=intents, markets=markets, settings=ExecSettings(max_open_orders=2))
    assert [a.intent_id for a in actions if isinstance(a, Place)] == [uid(2), uid(3)]
    assert Skip(uid(1), "exec_capacity") in actions


def test_capacity_counts_the_orders_that_stay_open():
    markets = {i: market(vm_id=i) for i in (1, 2)}
    resting = order(vm_id=1, oid=9)
    actions = plan(intents=[intent(vm_id=2, n=2)], orders=[resting], markets=markets,
                   settings=ExecSettings(max_open_orders=1))
    assert actions == [Skip(uid(2), "exec_capacity")]


def test_capacity_frees_the_slot_of_an_order_being_repriced():
    m = {1: market()}
    actions = plan(intents=[intent(prob="0.44", n=5)], orders=[order(prob="0.45")], markets=m,
                   settings=ExecSettings(max_open_orders=1))
    assert [type(a) for a in actions] == [Cancel, Place]


def test_missing_variant_config_is_a_loud_failure():
    with pytest.raises(KeyError):
        plan(intents=[intent(variant="ghost")], variants={"v1": cfg()})


# --- exposure rebuild ---------------------------------------------------------------


def test_rebuild_state_sums_stakes_and_daily_fills_since_local_midnight():
    """`fills_today` is filtered to 00:00 America/Chicago by the caller; the sum lands here."""
    orders = [order(variant="v1", oid=1, stake="60", game_id=11),
              order(variant="v1", oid=2, stake="40", game_id=12),
              order(variant="v2", oid=3, stake="999", game_id=11)]
    positions = [PositionView("v1", 11, 5, "yes", Decimal("100"), Decimal("0.03")),
                 PositionView("v1", 11, 5, "yes", Decimal("20"), Decimal("0.07")),
                 PositionView("v2", 11, 5, "yes", Decimal("500"), Decimal("0.09"))]
    fills = [FillView("v1", Decimal("25")), FillView("v2", Decimal("900"))]

    state = rebuild_state(orders, positions, fills, "v1")

    assert state.open_orders == 2
    assert state.daily_exposure == Decimal("125")  # orders 60 + 40, fills 25; positions excluded
    assert state.game_exposure == {11: Decimal("180"), 12: Decimal("40")}  # orders + positions
    # Same key twice keeps the better edge, exactly as `run_strategy` records it.
    assert state.positions == {(11, 5, "yes"): Decimal("0.07")}


def test_rebuild_state_counts_a_filled_contract_once_in_each_aggregate():
    """One order filled today is a position *and* a fill; each aggregate must see it once.

    `daily_exposure` takes the open orders and today's fills; `game_exposure` takes the open
    orders and the unsettled positions. Adding all three to one sum double-counts the fill and
    binds `cap_daily` at about half its intended level.
    """
    orders = [order(variant="v1", oid=1, stake="100", game_id=7)]
    # The same 40 of stake, seen twice: once as the position it created, once as today's fill.
    positions = [PositionView("v1", 7, 5, "yes", Decimal("40"), Decimal("0.03"))]
    fills = [FillView("v1", Decimal("40"))]

    state = rebuild_state(orders, positions, fills, "v1")

    assert state.daily_exposure == Decimal("140")  # order 100 + fill 40, not 180
    assert state.game_exposure == {7: Decimal("140")}  # order 100 + position 40, not 180


def test_rebuild_state_keys_positions_by_side():
    positions = [PositionView("v1", 11, 5, "yes", Decimal("10"), Decimal("0.03")),
                 PositionView("v1", 11, 5, "no", Decimal("10"), Decimal("0.04"))]
    state = rebuild_state([], positions, [], "v1")
    assert set(state.positions) == {(11, 5, "yes"), (11, 5, "no")}


def test_cap_labels_read_the_rebuilt_state():
    state = StrategyState(open_orders=25, daily_exposure=Decimal("0"))
    labels = cap_labels(intent(), cfg(), state)
    assert labels == {"cap_per_bet": True, "cap_per_game": True, "cap_daily": True,
                      "max_open": False}
    assert first_false_cap(labels) == "max_open"
    assert first_false_cap({k: True for k in labels}) is None


# --- determinism ---------------------------------------------------------------------


def test_deterministic():
    markets = {i: market(vm_id=i) for i in (1, 2, 3, 4)}
    markets[4] = market(vm_id=4, side="no")
    intents = [intent(vm_id=2, n=2, edge="0.05", game_id=12),
               intent(vm_id=3, n=3, edge="0.04", game_id=13),
               intent(vm_id=1, n=1, prob="0.44", edge="0.06"),
               intent(vm_id=4, n=4, side="no", edge="0.03", game_id=14, variant="v2")]
    orders = [order(vm_id=1, oid=1, prob="0.45")]
    states = {"v1": StrategyState(open_orders=1, daily_exposure=Decimal("60"),
                                  game_exposure={11: Decimal("60")}),
              "v2": StrategyState()}
    book = book_for("yes")
    markets[2] = market(vm_id=2, book=book)

    first = plan(intents=intents, orders=orders, markets=markets, states=states)
    second = plan(intents=intents, orders=orders, markets=markets, states=states)

    assert first == second
    assert len(first) > 1
    # The caller's state and book are inputs, not scratch space.
    assert states["v1"].open_orders == 1
    assert states["v1"].daily_exposure == Decimal("60")
    assert states["v1"].game_exposure == {11: Decimal("60")}
    assert book.yes_bids == {Decimal("0.48"): Decimal("100")}


def test_only_the_newest_intent_for_a_key_is_acted_on():
    older = intent(n=1, prob="0.40", created=NOW - timedelta(minutes=5))
    newer = intent(n=2, prob="0.44", created=NOW - timedelta(seconds=5))
    actions = plan(intents=[newer, older], orders=[order(prob="0.45")])
    assert actions == [Cancel(1, "reprice"),
                       Place(uid(2), "yes", Decimal("0.4400"), Decimal("20.00"),
                             KICKOFF - timedelta(minutes=10), False)]
