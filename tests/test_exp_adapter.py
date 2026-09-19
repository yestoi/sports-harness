"""§1.3: one instant at a time, and only what was visible at it.

Every expectation here is written from the fixture's own stamps, never read back from
`simulate_fills` or from `plan_actions` (6B's I-13 rule). The fixtures are complete rows -- a
venue market, its gap snapshot and fair value, a REST ladder, prints -- because the runner calls
the shared reads and a half-built market would be answered by the `unmatched` rule rather than by
the rule the case is about.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.db.models import (FairValue, Game, Intent, MarketDirtyInterval,
                               MarketGapSnapshot, Order, OrderEvent, OrderbookSnapshot,
                               VenueMarket, VenueTrade)
from harness.execution import store
from harness.experiments.execution_viability.adapter import ArmRunner

RUN = "0198e2b0-0000-7000-8000-000000000001"
NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
KICKOFF = datetime(2026, 9, 16, 17, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=15)
TICKER = "KXNFLGAME-1"
#: The exec slice of a variant config, the `sharp_direct` numbers `tests/test_exec_plan.py` uses.
VARIANT_CFG = {"v_base": {"stale_s": 180, "apply_caps": False, "bankroll": 3000,
                          "per_bet_cap": 0.03, "per_game_cap": 0.05, "daily_cap": 0.15,
                          "max_open": 25}}


def _game(session, *, kickoff=KICKOFF):
    game = Game(sport="nfl", home_team_id=1, away_team_id=2, kickoff_utc=kickoff,
                espn_event_id="4017", status="scheduled")
    session.add(game)
    session.flush()
    return game


def _market(session, *, game_id, ticker=TICKER):
    market = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME",
                         series_ticker="KXNFLGAME", game_id=game_id, market_type="moneyline",
                         match_status="matched", match_key="k1", first_seen_raw_id=1,
                         last_seen_at=NOW)
    session.add(market)
    session.flush()
    return market


def _priced(session, market, *, at, fair_p="0.5100", bid="0.4800", ask="0.5200"):
    """A gap snapshot and the fair value it is keyed to: what `market_rows(at=)` reads."""
    fair = FairValue(run_id=int(at.timestamp()), game_id=market.game_id, market_type="moneyline",
                     outcome_team_id=1, fair_p=Decimal(fair_p), fair_source="direct",
                     created_at=at)
    session.add(fair)
    session.flush()
    session.add(MarketGapSnapshot(
        run_id=int(at.timestamp()), venue_market_id=market.id, fair_value_id=fair.id,
        fair_source="direct", fair_p=Decimal(fair_p), venue_mid=Decimal("0.5000"),
        best_bid=Decimal(bid), best_ask=Decimal(ask), staleness_s=10, stale_allowance_s=180,
        dow=at.weekday(), hour_ct=at.hour, created_at=at))
    session.flush()


def _intent(session, market, *, created_at, target_prob="0.4800", signal_id=1,
            kickoff=KICKOFF):
    intent = Intent(signal_id=signal_id, variant_id="v_base", venue="kalshi",
                    venue_market_id=market.id, ticker=market.ticker, side="yes",
                    target_prob=Decimal(target_prob), target_contracts=Decimal("20"),
                    edge=Decimal("0.0300"), edge_min=Decimal("0.0100"),
                    fair_p=Decimal("0.5100"), game_id=market.game_id, kickoff_utc=kickoff,
                    stake=Decimal("9.60"), signal_created_at=created_at, created_at=created_at,
                    replay=False)
    session.add(intent)
    session.flush()
    return intent


def _order(session, intent, market, *, placed_at, contracts="20", status="open", expiry=None,
           n=1, prob="0.4800", kickoff=None):
    """One production `orders` row, as the arm adopts it.

    `kickoff` defaults to the module's own `KICKOFF`; a case whose game kicks off elsewhere
    passes its own (M9). It used to be the module constant unconditionally, which left the
    overnight case rebuilding the adopted `OpenOrderView` through `__class__(**__dict__)` to
    patch a stamp the fixture could simply have written.
    """
    order = Order(intent_id=intent.id, variant_id="v_base", venue="kalshi",
                  client_order_id=f"prod-{n}", ticker=intent.ticker, venue_market_id=market.id,
                  side="yes", prob=Decimal(prob), contracts=Decimal(contracts),
                  status=status, placed_at=placed_at,
                  expiry=expiry or placed_at + timedelta(seconds=220), game_id=market.game_id,
                  sport="nfl", kickoff_utc=kickoff or KICKOFF, match_key="k1", replay=False)
    session.add(order)
    session.flush()
    session.add(OrderEvent(order_id=order.id, ts=placed_at, kind="place", prob=order.prob,
                           contracts=order.contracts, replay=False))
    session.flush()
    return order


def _rest_book(session, market, *, fetched_at, raw_id=1, yes_bids=None, no_bids=None):
    """A REST ladder: `fetched_at` is the only availability stamp the tape keeps for it."""
    session.add(OrderbookSnapshot(raw_id=raw_id, venue_market_id=market.id,
                                  fetched_at=fetched_at,
                                  yes_bids=yes_bids or [["0.4800", "100"]],
                                  no_bids=no_bids or [["0.4500", "100"]]))
    session.flush()


def _print(session, *, ts, trade_id, count="12", yes_price="0.4800", ticker=TICKER):
    """A public print that lifted resting YES size: the taker is on the other side."""
    session.add(VenueTrade(venue="kalshi", trade_id=trade_id, ticker=ticker, ts=ts,
                           yes_price=Decimal(yes_price), count=Decimal(count), taker_side="no",
                           taker_outcome_side="no", source="ws"))
    session.flush()


def _runner(env_settings, *, walkers=None, policy=None):
    return ArmRunner(run_id=RUN, arm_id="A", policy=policy, variant_cfg=VARIANT_CFG,
                     exec_settings=env_settings, walkers=walkers or {})


def _recording(monkeypatch, name):
    """Wrap a real `store` function, recording the keywords it was called with. Not a mock: the
    wrapped function still runs and still returns its own rows (6B's I-13 rule)."""
    seen, original = [], getattr(store, name)

    def wrapper(*args, **kwargs):
        seen.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, name, wrapper)
    return seen


def test_the_runner_steps_only_the_instants_it_is_given(db_session, env_settings, monkeypatch):
    market = _market(db_session, game_id=_game(db_session).id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    db_session.commit()
    seen = _recording(monkeypatch, "market_rows")
    instants = [NOW, NOW + STEP, NOW + 4 * STEP]        # a deliberate gap: no 12:00:30 instant
    results = _runner(env_settings).run(db_session, instants)
    assert [r.instant for r in results] == instants
    # One market read per instant, plus the second bounded read at the first instant, where the
    # intent named a market this arm had not priced before. Every one of them is at its own
    # instant: nothing is read at `now`, and nothing is carried over from a later instant.
    assert [kw["at"] for kw in seen] == [NOW, NOW, NOW + STEP, NOW + 4 * STEP]


def test_the_runner_reads_markets_and_intents_at_the_instant_not_at_now(db_session, env_settings,
                                                                        monkeypatch):
    market = _market(db_session, game_id=_game(db_session).id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    db_session.commit()
    markets_at = _recording(monkeypatch, "market_rows")
    intents_at = _recording(monkeypatch, "load_intents")
    instants = [NOW, NOW + STEP]
    _runner(env_settings).run(db_session, instants)
    assert sorted({kw["at"] for kw in markets_at}) == instants
    assert [kw["at"] for kw in intents_at] == instants
    assert [kw["replay"] for kw in intents_at] == [False, False]   # §0.12: not reused


def test_the_runner_never_writes_a_production_table(db_session, env_settings):
    game = _game(db_session)
    market = _market(db_session, game_id=game.id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _rest_book(db_session, market, fetched_at=NOW - timedelta(seconds=20))
    intent = _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    _order(db_session, intent, market, placed_at=NOW)
    db_session.commit()
    before = {t: db_session.execute(text(f"select count(*) from {t}")).scalar()
              for t in ("orders", "order_events", "fills", "intents", "positions")}
    results = _runner(env_settings).run(db_session, [NOW, NOW + STEP, NOW + 2 * STEP])
    after = {t: db_session.execute(text(f"select count(*) from {t}")).scalar() for t in before}
    assert after == before
    assert any(r.actions for r in results)          # the arm did act; it just wrote nothing


def test_a_row_whose_availability_stamp_is_after_the_instant_is_invisible(db_session,
                                                                          env_settings):
    # A book snapshot whose `fetched_at` -- the only availability stamp the tape keeps for it
    # (models.py:241) -- is 12:00:20 is invisible to a 12:00:00 decision and visible to a
    # 12:00:30 one, even though the arm is stepping the same market both times.
    market = _market(db_session, game_id=_game(db_session).id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    _rest_book(db_session, market, fetched_at=NOW + timedelta(seconds=20))
    db_session.commit()
    runner = _runner(env_settings)
    first, second = runner.run(db_session, [NOW, NOW + 2 * STEP])
    # At 12:00:00 there is no book at all, which is R10's `no_book` placement: the order is
    # placed and stays out of fill simulation. At 12:00:30 the ladder exists and is the book.
    assert [a["kind"] for a in first.actions] == ["place"]
    assert first.actions[0]["no_book"] is True and first.actions[0]["book_source"] == "none"
    assert [a["kind"] for a in second.actions] == []          # the order of 12:00 still rests
    assert second.open_orders[0]["queue_ahead"] is None
    resting = runner.world.open_orders[0]
    assert runner.walkers[TICKER].at(NOW) is None
    assert runner.walkers[TICKER].at(NOW + 2 * STEP).source == "rest"
    assert resting.placed_at == NOW


def test_a_gap_and_recovery_dirties_only_the_interval_it_covers(db_session, env_settings):
    market = _market(db_session, game_id=_game(db_session).id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    db_session.add(MarketDirtyInterval(venue_market_id=market.id, ticker=TICKER,
                                       started_at=NOW + STEP, ended_at=NOW + 2 * STEP,
                                       cause="ws_gap", replay=False))
    db_session.commit()
    results = _runner(env_settings).run(
        db_session, [NOW, NOW + STEP, NOW + 2 * STEP, NOW + 3 * STEP])
    assert [r.dirty for r in results] == [False, True, True, False]


def test_a_cancel_replacement_chain_matches_the_hand_written_transition_table(db_session,
                                                                              env_settings):
    # The fixture's own stamps: an order resting at 0.48 from 12:00, and a newer intent at
    # 12:00:15 whose target is 0.50 -- two points away, past `exec_reprice_fair_move_pts` of
    # 0.01. The transition table that follows is written here: the resting order is cancelled
    # for `reprice` at 12:00:15 and its replacement is placed at the same instant, and nothing
    # happens at 12:00:30 because the replacement is already at the newest target.
    game = _game(db_session)
    market = _market(db_session, game_id=game.id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _priced(db_session, market, at=NOW + STEP - timedelta(seconds=1))
    _rest_book(db_session, market, fetched_at=NOW - timedelta(seconds=10),
               yes_bids=[["0.4800", "100"]], no_bids=[["0.4500", "100"]])
    intent = _intent(db_session, market, created_at=NOW - timedelta(minutes=5), signal_id=1)
    order = _order(db_session, intent, market, placed_at=NOW,
                   expiry=NOW + timedelta(seconds=600))
    _intent(db_session, market, created_at=NOW + STEP, target_prob="0.5000", signal_id=2)
    db_session.commit()
    runner = _runner(env_settings)
    runner.adopt(order, queue_ahead=Decimal("0"))         # the arm owns this resting order
    results = runner.run(db_session, [NOW, NOW + STEP, NOW + 2 * STEP])
    chain = [(a["kind"], a["instant"], a.get("reason")) for r in results for a in r.actions]
    assert chain == [("cancel", NOW + STEP, "reprice"), ("place", NOW + STEP, None)]
    assert [a["status"] for r in results for a in r.actions] == ["cancelled", "open"]
    # The replacement is the arm's own order, never the recorded one, and it rests afterwards.
    assert results[1].actions[1]["order_id"] != order.id
    assert [row["order_id"] for row in results[-1].open_orders] == \
        [results[1].actions[1]["order_id"]]


def test_one_partial_fill_leaves_the_remainder_resting_with_its_queue_position(db_session,
                                                                              env_settings,
                                                                              monkeypatch):
    # The tape prints 12 contracts at 0.48 at 12:00:05, taken from the other side, against an
    # order of 20 that joined with 5 contracts ahead of it: 5 of the print clears the queue and
    # the remaining 7 are ours, leaving 13 resting at the front of the queue.
    game = _game(db_session)
    market = _market(db_session, game_id=game.id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _rest_book(db_session, market, fetched_at=NOW - timedelta(seconds=10))
    intent = _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    order = _order(db_session, intent, market, placed_at=NOW, contracts="20",
                   expiry=NOW + timedelta(seconds=600))
    _print(db_session, ts=NOW + timedelta(seconds=5), trade_id="t1", count="12")
    db_session.commit()
    runner = _runner(env_settings)
    runner.adopt(order, queue_ahead=Decimal("5"))
    prints_at = _recording(monkeypatch, "load_prints")
    deltas_at = _recording(monkeypatch, "load_deltas")
    [result] = runner.run(db_session, [NOW + STEP])
    assert sum(f["contracts"] for f in result.fills) == Decimal("7")   # 12 printed - 5 ahead
    # The tape the simulator is handed is read at the instant as well: a print taped after
    # 12:00:15 is not in the list this step filled from (§1.3b, no lookahead).
    assert [kw["at"] for kw in prints_at] == [NOW + STEP]
    assert [kw["at"] for kw in deltas_at] == [NOW + STEP]
    # The fill record names what the tape is paired on: its market and side (§1.3f).
    assert result.fills[0]["venue_market_id"] == market.id
    assert result.fills[0]["side"] == "yes" and result.fills[0]["kind"] == "fill"
    resting = result.open_orders[0]
    assert resting["contracts"] == Decimal("13") and resting["queue_ahead"] == Decimal("0")


def test_an_overnight_transition_keeps_the_order_until_its_own_rule_cancels_it(db_session,
                                                                               env_settings):
    late = datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc)      # 23:00 CT the evening before
    kickoff = late + timedelta(hours=14)
    game = _game(db_session, kickoff=kickoff)
    market = _market(db_session, game_id=game.id)
    intent = _intent(db_session, market, created_at=late - timedelta(minutes=5), kickoff=kickoff)
    order = _order(db_session, intent, market, placed_at=late, kickoff=kickoff,
                   expiry=late + timedelta(hours=10))
    instants = [late + timedelta(hours=h) for h in (1, 6, 11)]
    for instant in instants:
        _priced(db_session, market, at=instant - timedelta(seconds=30))
    db_session.commit()
    runner = _runner(env_settings)
    runner.adopt(order, queue_ahead=Decimal("0"))
    # M9: the order carries this case's own kickoff, so the adopted view needs no rebuild.
    assert runner.world.open_orders[0].kickoff_utc == kickoff
    results = runner.run(db_session, instants)
    # The order crosses 00:00 CT and 05:00 UTC untouched: the day boundary is not one of its
    # rules. What ends it is its own 10 h expiry, at the third instant.
    assert [bool(r.open_orders) for r in results] == [True, True, False]
    assert results[-1].actions[-1]["kind"] == "expire"    # its own expiry, not the day boundary


def test_a_delayed_loop_does_not_move_a_deadline(db_session, env_settings):
    game = _game(db_session)
    market = _market(db_session, game_id=game.id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    intent = _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    order = _order(db_session, intent, market, placed_at=NOW,
                   expiry=NOW + timedelta(seconds=220))
    db_session.commit()
    runner = _runner(env_settings)
    runner.adopt(order, queue_ahead=Decimal("0"))
    # The next instant is 90 s late (a real loop stall in the tape); the deadline is unmoved.
    [result] = runner.run(db_session, [NOW + timedelta(seconds=310)])
    assert result.actions[-1]["kind"] == "expire"
    assert result.actions[-1]["deadline"] == NOW + timedelta(seconds=220)


def test_an_instant_inside_a_recorded_dirty_interval_places_nothing(db_session, env_settings):
    """\u00a71.3(c): the loop's own dirty verdict is an **input** to the decision.

    `market_dirty_intervals` is what the live loop recorded about this book; at an instant it
    covers, the executor's F36 rule skips the intent rather than pricing against a book it
    could not trust. The arm reads the same record and reaches the same skip -- it does not
    recompute dirtiness, and it does not place and then label the result.
    """
    market = _market(db_session, game_id=_game(db_session).id)
    _priced(db_session, market, at=NOW - timedelta(seconds=30))
    _rest_book(db_session, market, fetched_at=NOW - timedelta(seconds=10))
    _intent(db_session, market, created_at=NOW - timedelta(minutes=5))
    db_session.add(MarketDirtyInterval(venue_market_id=market.id, ticker=TICKER,
                                       started_at=NOW - timedelta(seconds=5), ended_at=None,
                                       cause="ws_gap", replay=False))
    db_session.commit()
    [inside] = _runner(env_settings).run(db_session, [NOW])
    assert [(a["kind"], a.get("reason")) for a in inside.actions] == [("skip", "book_dirty")]
    assert inside.dirty is True and inside.open_orders == ()
    # The same fixture one interval later: the book is trusted again and the intent is placed.
    [after] = _runner(env_settings).run(db_session, [NOW - timedelta(seconds=10)])
    assert [a["kind"] for a in after.actions] == ["place"]


def test_the_previous_days_fills_leave_the_daily_exposure_at_local_midnight(db_session,
                                                                            env_settings):
    """Amendment 2: the daily cap's day is the local day, and yesterday's fills are not in it.

    Hand-written from the fixture: an order of 20 contracts at 0.48 (stake 9.60) resting from
    23:00 CT, and a print of 12 at 0.48 at 23:00:05 CT that fills 12 of it (stake 5.76). At
    23:00:15 CT the day's exposure is 9.60 + 5.76 = 15.36; at 07:00 CT the next morning the
    fill belongs to yesterday and the exposure is the resting order's 9.60 alone.
    """
    from harness.execution.plan import rebuild_state

    caps = {"v_base": {**VARIANT_CFG["v_base"], "apply_caps": True}}
    late = datetime(2026, 9, 16, 4, 0, tzinfo=timezone.utc)        # 23:00 CT the evening before
    first, second = late + STEP, datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    market = _market(db_session, game_id=_game(db_session).id)
    _priced(db_session, market, at=late - timedelta(seconds=30))
    # The next morning's own pricing: without it the resting order is cancelled for
    # `fair_stale` at 07:00 CT and the day boundary would never be the thing under test.
    _priced(db_session, market, at=second - timedelta(seconds=30))
    _rest_book(db_session, market, fetched_at=late - timedelta(seconds=10))
    intent = _intent(db_session, market, created_at=late - timedelta(minutes=5))
    order = _order(db_session, intent, market, placed_at=late,
                   expiry=late + timedelta(hours=10))
    _print(db_session, ts=late + timedelta(seconds=5), trade_id="t1", count="12")
    db_session.commit()
    runner = ArmRunner(run_id=RUN, arm_id="A", policy=None, variant_cfg=caps,
                       exec_settings=env_settings, walkers={}, tz="America/Chicago")
    runner.adopt(order, queue_ahead=Decimal("0"))
    [evening] = runner.run(db_session, [first])
    assert sum(f["contracts"] for f in evening.fills) == Decimal("12")
    yesterday = rebuild_state(runner.world.open_orders, runner.world.positions,
                              runner.world.fills_today, "v_base")
    assert yesterday.daily_exposure == Decimal("15.36")
    runner.run(db_session, [second])
    today = rebuild_state(runner.world.open_orders, runner.world.positions,
                          runner.world.fills_today, "v_base")
    assert runner.world.fills_today == [] and today.daily_exposure == Decimal("9.60")
