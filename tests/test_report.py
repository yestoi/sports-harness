"""The weekly report: the ten tables over one ISO week's rows, the selection artefact, and
`harness report --week`.

Week 38 of 2026 is the pre-registration record's freeze week, so every fixture here lands
inside it: Monday 2026-09-14 00:00 America/Chicago through the following Monday, which is
2026-09-14 05:00Z to 2026-09-21 05:00Z.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from harness.cli import app
from harness.config.settings import get_settings
from harness.db.models import (
    FairValue,
    Fill,
    Game,
    GapOutcome,
    MarketGapSnapshot,
    Markout,
    Order,
    OrderbookEvent,
    OrderClv,
    Signal,
    StrategyVariant,
    VenueMarket,
)
from harness.report.tables import (
    FLAG_CLUSTERS,
    GREY_CLUSTERS,
    PLACEHOLDER,
    TABLE_KEYS,
    Table,
    is_flagged,
    is_grey,
    week_bounds,
    weekly_tables,
)
from harness.report.weekly import (
    read_selected,
    render_markdown,
    restrict_to_selection,
    select_cells,
    write_selected,
)

runner = CliRunner()

YEAR, WEEK = 2026, 38
WEEK_START = datetime(2026, 9, 14, 5, 0, tzinfo=timezone.utc)
WED = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
HOME, AWAY = 14, 19
PRIMARY, SECONDARY = "p00000000001", "s00000000001"


# --- fixtures -------------------------------------------------------------------------------


@pytest.fixture
def cli_settings(monkeypatch, db_session):
    """Point `harness.cli`'s own engine at the database `db_session` uses (tests/test_cli.py)."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _game(session, kickoff=None, sport="nfl") -> Game:
    g = Game(sport=sport, home_team_id=HOME, away_team_id=AWAY,
             kickoff_utc=kickoff or WED + timedelta(hours=6), status="scheduled")
    session.add(g)
    session.flush()
    return g


def _market(session, game_id, ticker, market_type="moneyline", threshold=None) -> VenueMarket:
    m = VenueMarket(venue="kalshi", ticker=ticker, event_ticker="KXNFLGAME-EVT",
                    series_ticker="KXNFLGAME", game_id=game_id, market_type=market_type,
                    threshold=None if threshold is None else Decimal(str(threshold)),
                    side_team_id=HOME, match_confidence=Decimal("1.00"), match_status="matched",
                    first_seen_raw_id=1, last_seen_at=WED)
    session.add(m)
    session.flush()
    return m


def _variant(session, variant_id, name, tier) -> StrategyVariant:
    v = StrategyVariant(variant_id=variant_id, name=name, tier=tier, config_json={},
                        registered_at=WEEK_START)
    session.add(v)
    session.flush()
    return v


_runs = iter(range(1, 100_000))


def _gap(session, market, created_at=None, fair_p="0.5500", venue_mid="0.5000",
         fair_source="direct", feed_kind="featured", ttk_minutes=120, staleness_s=60,
         gap_mid="0.0500", gap_maker_net="0.0300") -> MarketGapSnapshot:
    row = MarketGapSnapshot(
        run_id=next(_runs), venue_market_id=market.id, fair_source=fair_source,
        fair_p=Decimal(fair_p), venue_mid=Decimal(venue_mid), best_bid=Decimal("0.4900"),
        n_groups=2, staleness_s=staleness_s, feed_kind=feed_kind,
        gap_mid=Decimal(gap_mid), gap_maker_net=Decimal(gap_maker_net),
        ttk_minutes=ttk_minutes, dow=3, hour_ct=13, created_at=created_at or WED)
    session.add(row)
    session.flush()
    return row


def _gap_outcome(session, gap, benchmark_type="pinnacle_t5", p_bench="0.5600",
                 clv_mid_p="0.0600") -> GapOutcome:
    row = GapOutcome(gap_snapshot_id=gap.id, benchmark_type=benchmark_type,
                     p_bench=Decimal(p_bench), clv_mid_p=Decimal(clv_mid_p),
                     p_used_kind="target")
    session.add(row)
    session.flush()
    return row


def _signal(session, gap, market, variant_id, price_target="0.5000", decision="candidate",
            created_at=None) -> Signal:
    row = Signal(run_id=gap.run_id, variant_id=variant_id, gap_snapshot_id=gap.id,
                 venue_market_id=market.id, side="yes",
                 price_target=Decimal(price_target), decision=decision, labels={},
                 created_at=created_at or WED)
    session.add(row)
    session.flush()
    return row


def _order(session, market, variant_id, gap=None, placed_at=None, prob="0.5000",
           status="open", cancel_reason=None, cancelled_at=None, feed_kind="featured",
           staleness_at_place=60, replay=False, book_source="ws", sport="nfl") -> Order:
    o = Order(intent_id=uuid.uuid4(), variant_id=variant_id, venue="kalshi", mode="paper",
              client_order_id=f"co-{uuid.uuid4()}", ticker=market.ticker,
              venue_market_id=market.id, side="yes", prob=Decimal(prob),
              contracts=Decimal("10.00"), status=status,
              placed_at=placed_at or WED, cancel_reason=cancel_reason, cancelled_at=cancelled_at,
              fair_p_at_place=Decimal("0.5500"), venue_mid_at_place=Decimal("0.5000"),
              queue_ahead_at_place=Decimal("20.00"), book_source=book_source, book_age_s=3,
              staleness_at_place=staleness_at_place, feed_kind=feed_kind,
              config_hash="c" * 8, gap_snapshot_id=None if gap is None else gap.id,
              game_id=market.game_id, sport=sport, kickoff_utc=WED + timedelta(hours=6),
              traded_at_price=Decimal("10.00"), replay=replay)
    session.add(o)
    session.flush()
    return o


def _fill(session, order, fill_method="queue_model", filled_at=None, prob="0.5000") -> Fill:
    f = Fill(order_id=order.id, prob=Decimal(prob), contracts=Decimal("10.00"),
             fee=Decimal("0.2500"), filled_at=filled_at or WED + timedelta(minutes=5),
             fill_method=fill_method, tape_source="ws", through=False, has_print=True)
    session.add(f)
    session.flush()
    return f


def _order_clv(session, order, benchmark_type="pinnacle_t5", clv_p_net="0.0200",
               stale=False) -> OrderClv:
    row = OrderClv(order_id=order.id, benchmark_type=benchmark_type, p_bench=Decimal("0.5600"),
                   p_used=order.prob, p_used_kind="order", clv_p=Decimal("0.0250"),
                   clv_p_net=Decimal(clv_p_net), clv_roi_net=Decimal("0.0400"), stale=stale)
    session.add(row)
    session.flush()
    return row


def _markout(session, order, anchor, horizon, fair_p="0.5600", venue_mid="0.5200",
             fair_changed=True, p_used="0.5000") -> Markout:
    row = Markout(order_id=order.id, anchor=anchor, horizon=horizon, at_ts=WED,
                  horizon_ts=WED + timedelta(minutes=5), p_used=Decimal(p_used),
                  fee_per_contract=Decimal("0.0025"),
                  fair_p=None if fair_p is None else Decimal(fair_p),
                  fair_changed=fair_changed,
                  venue_mid=None if venue_mid is None else Decimal(venue_mid),
                  mid_age_s=5, source="quote")
    session.add(row)
    session.flush()
    return row


def _fair_value(session, game, fair_p, created_at, fair_source="direct", market_type="moneyline",
                threshold=None, newest_book_ts=None, feed_kind="featured", run_id=None) -> FairValue:
    row = FairValue(run_id=run_id if run_id is not None else next(_runs), game_id=game.id,
                    market_type=market_type, outcome_team_id=HOME, outcome_side=None,
                    threshold=None if threshold is None else Decimal(str(threshold)),
                    fair_p=Decimal(fair_p), fair_source=fair_source, n_groups=2,
                    newest_book_ts=newest_book_ts or created_at, feed_kind=feed_kind,
                    created_at=created_at)
    session.add(row)
    session.flush()
    return row


def _ws_snapshot(session, ticker, ts, yes_bid, no_bid, seq=1) -> OrderbookEvent:
    """A stored WebSocket snapshot whose mid is (yes_bid + (1 - no_bid)) / 2."""
    row = OrderbookEvent(ticker=ticker, ts=ts, sid=1, seq=seq, kind="snapshot",
                         raw={"market_ticker": ticker,
                              "yes_dollars_fp": [[yes_bid, "50.00"]],
                              "no_dollars_fp": [[no_bid, "50.00"]]})
    session.add(row)
    session.flush()
    return row


def _tables(db_session, env_settings):
    return weekly_tables(db_session, YEAR, WEEK, env_settings)


# --- tests ----------------------------------------------------------------------------------


def test_week_bounds_are_monday_midnight_local():
    start, end = week_bounds(YEAR, WEEK, "America/Chicago")
    assert start == WEEK_START
    assert end == WEEK_START + timedelta(days=7)


def test_weekly_tables_return_every_key_with_placeholders(db_session, env_settings):
    tables = _tables(db_session, env_settings)
    assert list(tables) == list(TABLE_KEYS)
    assert set(TABLE_KEYS) == {"t1", "t2", "t3", "t4", "t4b", "t5", "t6", "t7", "t8", "t9", "t10"}
    for key, table in tables.items():
        assert isinstance(table, Table), key
        assert table.title and table.header, key
        assert table.columns, key
        assert table.rows, f"{key} must render a placeholder row, never an empty table"
        for row in table.rows:
            assert len(row) == len(table.columns), key
    for key in ("t7", "t9", "t10"):
        assert "not collected" in tables[key].note


def test_grey_rule_uses_clusters_not_rows(db_session, env_settings):
    # A cell is greyed on games, not observations: 40 rows over 3 games is grey, and 12 games
    # is flagged but not grey, whatever the row count.
    many_rows_few_games = (0.01, 40, 3, -0.01, 0.03)
    few_rows_many_games = (0.01, 12, 12, -0.01, 0.03)
    assert GREY_CLUSTERS == 10 and FLAG_CLUSTERS == 30
    assert is_grey(many_rows_few_games) and not is_grey(few_rows_many_games)
    assert is_flagged(few_rows_many_games) and is_flagged(many_rows_few_games)
    assert not is_flagged((0.01, 40, 44, -0.01, 0.03))

    # And the same rule through a real table: four orders on one game is one cluster.
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME")
    for i in range(4):
        gap = _gap(db_session, market)
        # `uq_open_order` allows one resting order per (venue, ticker, side, variant): four
        # observations on one game means four closed orders, not four open ones.
        order = _order(db_session, market, PRIMARY, gap=gap, status="cancelled",
                       placed_at=WED + timedelta(minutes=i))
        _order_clv(db_session, order)
    db_session.flush()
    row = next(r for r in _tables(db_session, env_settings)["t2"].rows if r[0] == "sharp_direct")
    cell = row[row.index("level") + 1]
    assert cell[1] == 4 and cell[2] == 1
    assert is_grey(cell)


def test_table2_is_paired_against_the_primary(db_session, env_settings):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECONDARY, "wide_band", "secondary")
    # Two games, one shared snapshot each: the primary's order and the secondary's order sit on
    # the same gap snapshot, so the contrast is paired and clustered by game.
    for i, net in enumerate((("0.0200", "0.0500"), ("0.0400", "0.0900"))):
        game = _game(db_session)
        market = _market(db_session, game.id, f"T-ML-{i}")
        gap = _gap(db_session, market)
        primary_order = _order(db_session, market, PRIMARY, gap=gap)
        _order_clv(db_session, primary_order, clv_p_net=net[0])
        secondary_order = _order(db_session, market, SECONDARY, gap=gap)
        _order_clv(db_session, secondary_order, clv_p_net=net[1])
    db_session.flush()

    tables = _tables(db_session, env_settings)
    t2 = tables["t2"]
    idx = t2.columns.index("pinnacle_t5")
    primary_row = next(r for r in t2.rows if r[0] == "sharp_direct")
    secondary_row = next(r for r in t2.rows if r[0] == "wide_band")
    assert primary_row[t2.columns.index("basis")] == "level"
    assert secondary_row[t2.columns.index("basis")] == "contrast"
    # level: mean of 0.02 and 0.04; contrast: mean of (0.05 - 0.02) and (0.09 - 0.04).
    assert abs(primary_row[idx][0] - 0.03) < 1e-9
    assert abs(secondary_row[idx][0] - 0.04) < 1e-9
    assert secondary_row[idx][1] == 2 and secondary_row[idx][2] == 2


def test_table3_collapses_reprice_chains(db_session, env_settings):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME")
    first = _order(db_session, market, PRIMARY, placed_at=WED, status="cancelled",
                   cancel_reason="reprice", cancelled_at=WED + timedelta(minutes=1))
    second = _order(db_session, market, PRIMARY, placed_at=WED + timedelta(minutes=2))
    for order in (first, second):
        _fill(db_session, order)
        _markout(db_session, order, "fill", "1m")
        _markout(db_session, order, "fill", "5m")
    db_session.flush()

    t3 = _tables(db_session, env_settings)["t3"]
    row = next(r for r in t3.rows
               if r[0] == "sharp_direct" and r[1] == "queue_model" and r[2] == "featured"
               and r[3].startswith("<="))
    assert row[t3.columns.index("fills")] == 2
    assert row[t3.columns.index("episodes")] == 1


def test_table4_grid_has_72_cells_per_source(db_session, env_settings):
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME")
    gap = _gap(db_session, market)
    _gap_outcome(db_session, gap)
    db_session.flush()

    t4 = _tables(db_session, env_settings)["t4"]
    source_col = t4.columns.index("fair_source")
    for source in ("direct", "derived"):
        assert sum(1 for r in t4.rows if r[source_col] == source) == 72
    assert len(t4.rows) == 144
    # The one seeded snapshot lands in exactly one direct cell: mid 0.50, ttk 120 min, nfl,
    # moneyline -- the 50-65 price bucket, under three hours.
    seeded = [r for r in t4.rows if r[source_col] == "direct"
              and r[t4.columns.index("gap_mid")] != PLACEHOLDER]
    assert len(seeded) == 1
    assert seeded[0][t4.columns.index("price_bucket")] == "50-65"
    assert seeded[0][t4.columns.index("ttk")] == "< 3 h"


def test_render_has_no_empty_cells(db_session, env_settings):
    tables = _tables(db_session, env_settings)
    text = render_markdown(tables, {"year": YEAR, "week": WEEK, "build_sha": "abc1234",
                                    "criteria_hash": "0" * 64, "config_hashes": []})
    assert text.startswith("# ")
    body = [line for line in text.splitlines() if line.startswith("|")]
    assert body
    for line in body:
        for cell in line.strip().strip("|").split("|"):
            assert cell.strip(), f"empty cell in {line!r}"
    for key in TABLE_KEYS:
        assert f"({key})" in text


def test_out_dash_writes_stdout(cli_settings, db_session):
    db_session.commit()
    result = runner.invoke(app, ["report", "--week", str(WEEK), "--year", str(YEAR), "--out", "-"])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("# ")
    assert "(t4)" in result.stdout


def test_selected_json_round_trip_and_confirm_restricts(tmp_path, cli_settings, db_session):
    # `cli_settings` already points get_settings() at the test database; `env_settings` would
    # point it back at a fake host, so this test builds its tables from the same settings the
    # CLI invocation below uses.
    settings = get_settings()
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECONDARY, "wide_band", "secondary")
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME")
    gap = _gap(db_session, market)
    _gap_outcome(db_session, gap)
    primary_order = _order(db_session, market, PRIMARY, gap=gap)
    _order_clv(db_session, primary_order)
    secondary_order = _order(db_session, market, SECONDARY, gap=gap)
    _order_clv(db_session, secondary_order, clv_p_net="0.0500")
    db_session.commit()

    tables = _tables(db_session, settings)
    # A hand-made selection of one cell and one contrast round-trips through the file and then
    # restricts the two tables it names.
    t4 = tables["t4"]
    first_cell = t4.rows[0]
    selection = {
        "year": YEAR, "week": WEEK, "criteria_hash": "0" * 64,
        "cells": [{"fair_source": first_cell[t4.columns.index("fair_source")],
                   "price_bucket": first_cell[t4.columns.index("price_bucket")],
                   "ttk": first_cell[t4.columns.index("ttk")],
                   "sport": first_cell[t4.columns.index("sport")],
                   "market_type": first_cell[t4.columns.index("market_type")],
                   "panel": "gap_maker_net"}],
        "contrasts": [{"variant": "wide_band", "benchmark_type": "pinnacle_t5"}],
    }
    path = tmp_path / "2026-w38-selected.json"
    write_selected(path, selection)
    assert json.loads(path.read_text())["cells"] == selection["cells"]
    assert read_selected(path) == selection

    restricted = restrict_to_selection(tables, selection)
    assert len(restricted["t4"].rows) == 1
    assert [r[0] for r in restricted["t2"].rows] == ["wide_band"]
    assert "confirmation" in restricted["t4"].note
    # Untouched tables pass through unchanged.
    assert restricted["t1"] is tables["t1"]

    # select_cells reads the tables' own BH/Holm columns and is writable as-is.
    computed = select_cells(tables)
    assert set(computed) >= {"cells", "contrasts"}
    out = tmp_path / "computed.json"
    write_selected(out, computed)
    assert read_selected(out) == computed

    # And the CLI writes the artefact whenever --selected-out is given.
    cli_out = tmp_path / "cli-selected.json"
    result = runner.invoke(app, ["report", "--week", str(WEEK), "--year", str(YEAR), "--out", "-",
                                 "--selected-out", str(cli_out)])
    assert result.exit_code == 0, result.output
    assert json.loads(cli_out.read_text())["week"] == WEEK


def test_table2_scores_a_non_executed_variant_at_its_own_price_target(db_session, env_settings):
    """A variant that places no order is still contrasted, from `gap_outcomes` at its own
    `price_target` -- not at the primary's price and not at the snapshot's stored target."""
    from harness.settlement.order_clv import clv_formulas

    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECONDARY, "wide_band", "secondary")
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME")
    gap = _gap(db_session, market)
    _gap_outcome(db_session, gap, p_bench="0.5600")
    primary_order = _order(db_session, market, PRIMARY, gap=gap)
    _order_clv(db_session, primary_order, clv_p_net="0.0200")
    # The secondary bids two cents lower and never places, so its CLV comes off the snapshot.
    _signal(db_session, gap, market, SECONDARY, price_target="0.4800")
    db_session.flush()

    t2 = _tables(db_session, env_settings)["t2"]
    idx = t2.columns.index("pinnacle_t5")
    row = next(r for r in t2.rows if r[0] == "wide_band")
    _, expected_net, _ = clv_formulas(Decimal("0.5600"), Decimal("0.4800"))
    assert abs(row[idx][0] - (float(expected_net) - 0.02)) < 1e-9
    assert row[idx][1] == 1 and row[idx][2] == 1


def test_table4b_buckets_the_derived_direct_gap_by_key_number_distance(db_session, env_settings):
    game = _game(db_session)
    run = next(_runs)
    # A -3.5 rung is half a point off the key number 3.
    _fair_value(db_session, game, "0.5000", WED, "direct", "spread", -3.5, run_id=run)
    _fair_value(db_session, game, "0.5300", WED, "derived", "spread", -3.5, run_id=run)
    db_session.flush()

    t4b = _tables(db_session, env_settings)["t4b"]
    row = next(r for r in t4b.rows if r[0] == "spread" and r[1] == "<= 0.5")
    assert abs(row[2][0] - 0.03) < 1e-9
    assert row[2][1] == 1 and row[2][2] == 1
    # Moneylines have no key-number scale, so their rows sit in the n/a bucket.
    assert any(r[0] == "moneyline" and r[1] == "n/a" for r in t4b.rows)


def test_table5_lags_the_venue_mid_behind_a_sharp_move(db_session, env_settings):
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME")
    move_ts = WED
    _fair_value(db_session, game, "0.5000", move_ts - timedelta(minutes=5))
    _fair_value(db_session, game, "0.5400", move_ts)
    # The move is +4 pts from a 0.50 mid, so half of it is 0.52. The first snapshot is still at
    # 0.50; the one 90 s later is at 0.53 and crosses.
    _ws_snapshot(db_session, market.ticker, move_ts + timedelta(seconds=30), "0.4900", "0.4900")
    _ws_snapshot(db_session, market.ticker, move_ts + timedelta(seconds=90), "0.5200", "0.4600",
                 seq=2)
    db_session.flush()

    t5 = _tables(db_session, env_settings)["t5"]
    row = next(r for r in t5.rows if r[0] == "nfl" and r[1] == "moneyline")
    assert row[t5.columns.index("moves")] == 1
    assert row[t5.columns.index("km_median_lag_s")] == 90.0
    # The sampling floor is the 60 s between the two snapshots, so the interval is [30, 90].
    assert row[t5.columns.index("lag_interval_lo_s")] == 30.0
    assert row[t5.columns.index("censored_share")] == 0.0


def test_report_rejects_a_week_that_does_not_exist(cli_settings):
    result = runner.invoke(app, ["report", "--week", "54", "--year", str(YEAR), "--out", "-"])
    assert result.exit_code == 1
    assert result.stdout == ""
