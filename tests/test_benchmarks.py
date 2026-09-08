"""Benchmarks: the pure decisions (`benchmark_at`, `kickoff_moved`) and the two stages that
write `benchmarks` rows (`compute_benchmarks`, `insert_result_benchmarks`).
"""

import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from harness.db.models import (
    Benchmark,
    FairValue,
    Game,
    MarketGapSnapshot,
    OddsSnapshot,
    Run,
    VenueMarket,
    VenueQuote,
    VenueSettlement,
    VenueTrade,
)
from harness.pricing.devig import devig
from harness.pricing.lines import Line, LineKey
from harness.settlement import benchmarks as bm
from harness.settlement.benchmarks import (
    BENCHMARK_TYPES,
    GAP_OUTCOME_TYPES,
    Snap,
    benchmark_at,
    compute_benchmarks,
    insert_result_benchmarks,
    kickoff_moved,
)
from harness.settlement.job import Budget, load_stages, new_ctx, use_ctx

NOW = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)
HOME, AWAY = 14, 19


class Mono:
    def __init__(self, *values: float) -> None:
        self.values = list(values) or [0.0]
        self.i = 0

    def __call__(self) -> float:
        value = self.values[min(self.i, len(self.values) - 1)]
        self.i += 1
        return value


def _game(session, kickoff=None, home_score=None, away_score=None, status="final") -> Game:
    g = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY,
             kickoff_utc=kickoff or NOW - timedelta(hours=4), status=status,
             home_score=home_score, away_score=away_score)
    session.add(g)
    session.flush()
    return g


def _market(session, game_id, ticker, market_type="moneyline", threshold=None, side_team_id=None,
           side=None, match_status="matched") -> VenueMarket:
    m = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME-EVT",
                    series_ticker="KXNFLGAME", game_id=game_id, market_type=market_type,
                    threshold=None if threshold is None else Decimal(str(threshold)),
                    side_team_id=side_team_id, side=side, match_confidence=Decimal("1.00"),
                    match_status=match_status, first_seen_raw_id=1, last_seen_at=NOW)
    session.add(m)
    session.flush()
    return m


def _line_row(game_id, book, market_type, fetched_at, price, team=None, side=None, point=None,
             last_update=None):
    return OddsSnapshot(raw_id=1, run_id=1, book=book, game_id=game_id, market_type=market_type,
                        outcome_team_id=team, outcome_side=side,
                        point=None if point is None else Decimal(str(point)),
                        price_decimal=Decimal(str(price)),
                        book_last_update=last_update or fetched_at, fetched_at=fetched_at)


_fair_run_ids = itertools.count(1)


def _fair(session, game_id, created_at, fair_p, newest_book_ts=None, market_type="moneyline",
         team=HOME, side=None, threshold=None, fair_source="direct") -> FairValue:
    # uq_fair_value_row is keyed (run_id, game_id, market_type, ..., fair_source): each snapshot
    # of the same shape needs its own run_id, exactly as the pricing pipeline gives one row per
    # run's tick.
    row = FairValue(run_id=next(_fair_run_ids), game_id=game_id, market_type=market_type,
                    outcome_team_id=team, outcome_side=side,
                    threshold=None if threshold is None else Decimal(str(threshold)),
                    fair_p=Decimal(str(fair_p)), fair_source=fair_source, n_groups=1,
                    newest_book_ts=newest_book_ts or created_at, created_at=created_at)
    session.add(row)
    session.flush()
    return row


# --- pure: benchmark_at -------------------------------------------------------------------


def test_benchmark_at_picks_last_at_or_before_and_flags_stale():
    target = NOW
    snaps = [
        Snap(ts=target - timedelta(minutes=30), p=Decimal("0.50"), source_ts=target - timedelta(minutes=30)),
        Snap(ts=target - timedelta(minutes=3), p=Decimal("0.55"), source_ts=target - timedelta(minutes=3)),
        Snap(ts=target + timedelta(minutes=1), p=Decimal("0.60"), source_ts=target + timedelta(minutes=1)),
    ]
    p, source_ts, stale = benchmark_at("consensus_t5", target, snaps)
    assert p == Decimal("0.55") and source_ts == target - timedelta(minutes=3) and stale is False

    stale_snaps = [Snap(ts=target - timedelta(minutes=1), p=Decimal("0.40"),
                        source_ts=target - timedelta(minutes=20))]
    p2, source_ts2, stale2 = benchmark_at("consensus_t5", target, stale_snaps)
    assert (p2, stale2) == (Decimal("0.40"), True)

    assert benchmark_at("consensus_t5", target, []) is None
    # A target before every snapshot's ts finds nothing at all.
    assert benchmark_at("consensus_t5", target - timedelta(hours=5), snaps) is None
    # A target between two snapshots still picks the last one at or before it.
    p3, _, _ = benchmark_at("consensus_t5", target - timedelta(minutes=10), snaps)
    assert p3 == Decimal("0.50")


def test_opening_first_seen_is_first():
    target = NOW
    snaps = [
        Snap(ts=target - timedelta(minutes=3), p=Decimal("0.55"), source_ts=target - timedelta(minutes=3)),
        Snap(ts=target - timedelta(days=3), p=Decimal("0.40"), source_ts=target - timedelta(days=3)),
        Snap(ts=target - timedelta(minutes=30), p=Decimal("0.50"), source_ts=target - timedelta(minutes=30)),
    ]
    p, source_ts, stale = benchmark_at("opening_first_seen", target, snaps)
    assert (p, source_ts, stale) == (Decimal("0.40"), target - timedelta(days=3), True)

    # target_ts is ignored for selection: a far-future target still returns the earliest snap.
    p2, _, _ = benchmark_at("opening_first_seen", target + timedelta(days=30), snaps)
    assert p2 == Decimal("0.40")


# --- pure-ish: novig normalisation -----------------------------------------------------


def test_novig_devig_normalises_pair():
    lines = {}
    key1 = LineKey("novig", "h2h", HOME, None, None)
    key2 = LineKey("novig", "h2h", AWAY, None, None)
    lines[key1] = Line(key1, Decimal("1.9000"), NOW - timedelta(minutes=10), NOW - timedelta(minutes=9))
    lines[key2] = Line(key2, Decimal("2.1000"), NOW - timedelta(minutes=10), NOW - timedelta(minutes=9))

    got = bm._novig_devig_t5(("moneyline", HOME, None, None), HOME, AWAY, lines, NOW)
    assert got is not None
    kind, p, source_ts, stale = got
    assert kind == "novig_devig_t5"
    imp1, imp2 = Decimal(1) / Decimal("1.9000"), Decimal(1) / Decimal("2.1000")
    expected = (imp1 / (imp1 + imp2)).quantize(Decimal("0.0001"))
    assert p == expected
    # Sums to (very close to) 1 with its complement -- normalised, not power-devigged.
    other = devig([Decimal("2.1000"), Decimal("1.9000")], method="proportional")[0]
    assert abs((p + other) - Decimal(1)) < Decimal("0.0002")
    assert source_ts == NOW - timedelta(minutes=10)
    assert stale is False

    assert bm._novig_devig_t5(("moneyline", HOME, None, None), HOME, AWAY, {}, NOW) is None


# --- pure-ish: kickoff_moved (needs a session, no clock) --------------------------------


def _gap_snapshot(session, venue_market_id, created_at, ttk_minutes) -> MarketGapSnapshot:
    row = MarketGapSnapshot(run_id=1, venue_market_id=venue_market_id, n_groups=0, dow=1,
                            hour_ct=12, ttk_minutes=ttk_minutes, created_at=created_at)
    session.add(row)
    session.flush()
    return row


def test_kickoff_moved_flagged(db_session):
    kickoff = NOW
    game = _game(db_session, kickoff=kickoff)
    market = _market(db_session, game.id, "T-ML-HOME", side_team_id=HOME)
    # First gap snapshot implied kickoff = created_at + 100 min = kickoff - 30 min: the game's
    # kickoff later moved 30 min later than that snapshot expected.
    _gap_snapshot(db_session, market.id, created_at=kickoff - timedelta(minutes=130), ttk_minutes=100)
    db_session.commit()

    assert kickoff_moved(db_session, game) is True

    on_time_game = _game(db_session, kickoff=kickoff)
    on_time_market = _market(db_session, on_time_game.id, "T-ML-HOME-2", side_team_id=HOME)
    _gap_snapshot(db_session, on_time_market.id, created_at=kickoff - timedelta(minutes=100), ttk_minutes=100)
    db_session.commit()
    assert kickoff_moved(db_session, on_time_game) is False

    no_gap_game = _game(db_session, kickoff=kickoff)
    assert kickoff_moved(db_session, no_gap_game) is False


# --- compute_benchmarks: db integration --------------------------------------------------


def _seed_ml_lines(session, game_id, kickoff):
    """Odds snapshots for the moneyline shape, fetched inside the 20 min lookback of
    `kickoff - 5 min`, from pinnacle (main line) and novig (no-vig feed)."""
    fetched = kickoff - timedelta(minutes=6)
    for book, home_price, away_price in (
        ("pinnacle", "1.6500", "2.4000"),
        ("novig", "1.9000", "2.1000"),
        ("betonlineag", "1.6200", "2.4300"),
        ("lowvig", "1.6600", "2.3900"),
    ):
        session.add(_line_row(game_id, book, "h2h", fetched, home_price, team=HOME))
        session.add(_line_row(game_id, book, "h2h", fetched, away_price, team=AWAY))


def test_compute_benchmarks_runs_once_per_game(db_session, monkeypatch):
    kickoff = NOW - timedelta(hours=4)
    game = _game(db_session, kickoff=kickoff)
    market = _market(db_session, game.id, "T-ML-HOME", side_team_id=HOME)
    _seed_ml_lines(db_session, game.id, kickoff)

    t180, t60, t5 = kickoff - timedelta(minutes=180), kickoff - timedelta(minutes=60), kickoff - timedelta(minutes=5)
    _fair(db_session, game.id, created_at=t180 - timedelta(minutes=20), fair_p="0.5500", team=HOME)
    _fair(db_session, game.id, created_at=t60 - timedelta(minutes=5), fair_p="0.5800", team=HOME)
    _fair(db_session, game.id, created_at=t5 - timedelta(minutes=1), fair_p="0.6000", team=HOME)

    db_session.add(VenueQuote(raw_id=1, run_id=1, venue_market_id=market.id,
                              yes_bid=Decimal("0.50"), yes_ask=Decimal("0.54"),
                              fetched_at=kickoff - timedelta(minutes=30)))
    db_session.add(VenueQuote(raw_id=2, run_id=1, venue_market_id=market.id,
                              yes_bid=Decimal("0.56"), yes_ask=Decimal("0.60"),
                              fetched_at=kickoff - timedelta(minutes=6)))
    db_session.add(VenueTrade(venue="kalshi", trade_id="tr-1", ticker=market.ticker,
                              ts=kickoff - timedelta(minutes=20), yes_price=Decimal("0.53"),
                              count=Decimal("10"), taker_side="yes", source="ws"))
    db_session.add(VenueTrade(venue="kalshi", trade_id="tr-2", ticker=market.ticker,
                              ts=kickoff - timedelta(minutes=2), yes_price=Decimal("0.59"),
                              count=Decimal("5"), taker_side="yes", source="ws"))
    db_session.add(VenueTrade(venue="kalshi", trade_id="tr-3", ticker=market.ticker,
                              ts=kickoff + timedelta(minutes=10), yes_price=Decimal("0.90"),
                              count=Decimal("5"), taker_side="yes", source="ws"))
    db_session.commit()

    n = compute_benchmarks(db_session, NOW, Budget(60, Mono(0.0)))
    rows = {r.benchmark_type: r for r in db_session.query(Benchmark).filter_by(game_id=game.id).all()}

    assert n == len(rows)
    assert set(rows) == {"pinnacle_t5", "novig_devig_t5", "consensus_t5", "consensus_t60",
                         "consensus_t180", "opening_first_seen", "kalshi_mid_t5",
                         "kalshi_last_trade_pre_kick"}
    assert rows["consensus_t180"].p == Decimal("0.5500")
    assert rows["consensus_t60"].p == Decimal("0.5800")
    assert rows["consensus_t5"].p == Decimal("0.6000")
    assert rows["opening_first_seen"].p == Decimal("0.5500")
    assert rows["kalshi_mid_t5"].p == Decimal("0.5800")  # (0.56 + 0.60) / 2
    assert rows["kalshi_last_trade_pre_kick"].p == Decimal("0.5900")  # tr-2, not the post-kickoff tr-3
    expected_pinnacle = devig([Decimal("1.6500"), Decimal("2.4000")], method="power")[0]
    assert rows["pinnacle_t5"].p == expected_pinnacle
    assert all(r.kickoff_moved is False for r in rows.values())

    # Fix round 1, I1: each type's target_ts is its own target, not uniformly `kickoff`.
    assert rows["pinnacle_t5"].target_ts == t5
    assert rows["novig_devig_t5"].target_ts == t5
    assert rows["consensus_t5"].target_ts == t5
    assert rows["consensus_t60"].target_ts == t60
    assert rows["consensus_t180"].target_ts == t180
    assert rows["kalshi_mid_t5"].target_ts == t5
    assert rows["kalshi_last_trade_pre_kick"].target_ts == kickoff
    # opening_first_seen stores its own chosen snapshot's ts as target_ts, and is never stale
    # by definition -- there is no target it could be late for.
    assert rows["opening_first_seen"].target_ts == rows["opening_first_seen"].source_ts
    assert rows["opening_first_seen"].stale is False

    # Idempotent: a second pass finds the game already has rows and inserts nothing.
    again = compute_benchmarks(db_session, NOW, Budget(60, Mono(0.0)))
    assert again == 0
    assert db_session.query(Benchmark).filter_by(game_id=game.id).count() == n


def test_compute_benchmarks_skips_games_before_kickoff_plus_5(db_session):
    game = _game(db_session, kickoff=NOW + timedelta(hours=1))
    _market(db_session, game.id, "T-ML-HOME", side_team_id=HOME)
    db_session.commit()

    assert compute_benchmarks(db_session, NOW, Budget(60, Mono(0.0))) == 0
    assert db_session.query(Benchmark).count() == 0


def test_pinnacle_t5_devig_matches_module_and_uses_alternates_for_non_main_rungs(db_session):
    kickoff = NOW - timedelta(hours=4)
    game = _game(db_session, kickoff=kickoff)
    _market(db_session, game.id, "T-SPR-HOME", market_type="spread", threshold="6.5",
           side_team_id=HOME)
    fetched = kickoff - timedelta(minutes=6)
    # Only the alternates market carries this rung for pinnacle -- the main "spreads" market
    # has none at 6.5, so pinnacle_t5 must fall through to alternate_spreads.
    db_session.add(_line_row(game.id, "pinnacle", "alternate_spreads", fetched, "1.9500",
                             team=HOME, point=-6.5))
    db_session.add(_line_row(game.id, "pinnacle", "alternate_spreads", fetched, "1.8700",
                             team=AWAY, point=6.5))
    db_session.commit()

    n = compute_benchmarks(db_session, NOW, Budget(60, Mono(0.0)))
    row = db_session.query(Benchmark).filter_by(game_id=game.id, benchmark_type="pinnacle_t5").one()
    expected = devig([Decimal("1.9500"), Decimal("1.8700")], method="power")[0]
    assert row.p == expected
    assert n >= 1


def test_eligible_games_excludes_games_older_than_7_days_and_warns_while_scanned(db_session):
    """Fix round 1, I2: a matched game whose price sources never produce a single benchmark row
    stays in `_ELIGIBLE_GAMES` forever under the old "not exists benchmarks" gate alone -- the
    7-day window bounds that. Within the window it is still scanned (and still produces
    nothing), which is worth one warning per pass so an operator can see it stuck.
    """
    stuck = _game(db_session, kickoff=NOW - timedelta(hours=4))
    _market(db_session, stuck.id, "T-ML-STUCK", side_team_id=HOME)  # no odds/quotes/trades at all

    aged_out = _game(db_session, kickoff=NOW - timedelta(days=8))
    _market(db_session, aged_out.id, "T-ML-AGED", side_team_id=HOME)
    db_session.commit()

    ctx = new_ctx()
    with use_ctx(ctx):
        n = compute_benchmarks(db_session, NOW, Budget(60, Mono(0.0)))

    assert n == 0
    assert db_session.query(Benchmark).count() == 0
    # Only the in-window game is scanned (and warned about); the 8-day-old one never enters
    # _ELIGIBLE_GAMES at all.
    assert ctx["warnings"] == [{"benchmarks_no_rows": stuck.id}]


def test_process_game_returns_zero_for_a_vanished_game(db_session):
    """Fix round 1, M7: a game deleted between the eligibility query and the loop (or simply a
    bad id) must not raise -- there is nothing left to benchmark, not an error."""
    assert bm._process_game(db_session, 999_999, NOW) == 0


def test_insert_result_benchmarks_calls_kickoff_moved_once_per_game(db_session, monkeypatch):
    """Fix round 1, M9: two shapes on the same game settling in one pass must not compute
    `kickoff_moved` twice -- it is a property of the game, not of the settling row."""
    game = _game(db_session, kickoff=NOW - timedelta(hours=6), home_score=24, away_score=21)
    ml = _market(db_session, game.id, "T-ML-HOME", side_team_id=HOME)
    spread = _market(db_session, game.id, "T-SPR-HOME", market_type="spread", threshold="3.5",
                     side_team_id=HOME)
    db_session.add(VenueSettlement(venue="kalshi", ticker=ml.ticker, source="derived",
                                   result="yes", payout=Decimal("1"),
                                   settled_at=NOW - timedelta(hours=1)))
    db_session.add(VenueSettlement(venue="kalshi", ticker=spread.ticker, source="derived",
                                   result="yes", payout=Decimal("1"),
                                   settled_at=NOW - timedelta(hours=1)))
    db_session.commit()

    calls = []
    real = bm.kickoff_moved

    def spy(session, g):
        calls.append(g.id)
        return real(session, g)

    monkeypatch.setattr(bm, "kickoff_moved", spy)

    n = insert_result_benchmarks(db_session, NOW, Budget(60, Mono(0.0)))
    assert n == 2
    assert calls == [game.id]


# --- insert_result_benchmarks --------------------------------------------------------------


def test_result_rows_accept_half(db_session):
    game = _game(db_session, kickoff=NOW - timedelta(hours=6), home_score=21, away_score=21)
    market = _market(db_session, game.id, "T-ML-HOME", side_team_id=HOME)
    db_session.add(VenueSettlement(venue="kalshi", ticker=market.ticker, source="derived",
                                   result="tie", payout=Decimal("0.5"),
                                   settled_at=NOW - timedelta(hours=1)))
    db_session.commit()

    n = insert_result_benchmarks(db_session, NOW, Budget(60, Mono(0.0)))
    assert n == 1
    row = db_session.query(Benchmark).filter_by(game_id=game.id, benchmark_type="result").one()
    assert row.p == Decimal("0.5000")
    assert row.target_ts == NOW - timedelta(hours=1)
    assert row.source_ts == NOW - timedelta(hours=1)
    assert row.stale is False

    # idempotent
    again = insert_result_benchmarks(db_session, NOW, Budget(60, Mono(0.0)))
    assert again == 0


# --- stage registration ------------------------------------------------------------------


def test_stages_registered_in_order():
    """Every stage `harness.settlement.settle`, `.benchmarks` and `.order_clv` register is
    present exactly once, and each module's own two stages come out in the order that module
    registers them in (source order, guaranteed whichever import first triggers it).

    Fix round 1, M5: `STAGE_MODULES` itself -- a plain list, evaluated once at import time,
    unaffected by which module some other test file happened to import first -- is pinned
    directly to the addendum's declared order. Combined with the per-module relative-order
    assertions below, this pins the cross-module invariant without depending on the *live*
    `STAGES` registry's order, which (because several test files import a stage module directly
    at collection time to reach its pure functions, and pytest collects files alphabetically)
    can have some later module registered ahead of an earlier one when the whole suite runs
    together -- a property of running tests together, not of the registration code, and not
    what `Settler.run()` actually sees in production (there, `load_stages()` is the first thing
    to import these modules, so it gets `STAGE_MODULES`' order exactly).
    """
    from harness.settlement.job import STAGE_MODULES

    assert STAGE_MODULES == ["harness.settlement.settle", "harness.settlement.benchmarks",
                             "harness.settlement.order_clv", "harness.settlement.markouts",
                             "harness.ops.housekeeping", "harness.settlement.report_wtd"]

    names = [name for name, _ in load_stages()]
    expected = {"settle", "venue_result", "benchmarks", "result_benchmarks",
               "gap_outcomes_drain", "order_clv", "markouts", "housekeeping", "report_wtd"}
    assert set(names) == expected
    assert len(names) == len(expected)  # each registered exactly once

    def before(a: str, b: str) -> bool:
        return names.index(a) < names.index(b)

    assert before("settle", "venue_result")
    assert before("benchmarks", "result_benchmarks")
    assert before("gap_outcomes_drain", "order_clv")
    assert set(BENCHMARK_TYPES) >= set(GAP_OUTCOME_TYPES)
    assert BENCHMARK_TYPES == (
        "pinnacle_t5", "consensus_t5", "consensus_t60", "consensus_t180", "opening_first_seen",
        "kalshi_mid_t5", "kalshi_last_trade_pre_kick", "novig_devig_t5", "result")
    assert GAP_OUTCOME_TYPES == ("pinnacle_t5", "consensus_t5", "kalshi_mid_t5", "result")
