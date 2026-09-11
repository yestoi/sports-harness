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
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli import app
from harness.config.settings import get_settings
from harness.db.models import (
    EquitySnapshot,
    FairValue,
    Fill,
    Game,
    GapOutcome,
    Intent,
    MarketGapSnapshot,
    Markout,
    Order,
    OrderbookEvent,
    OrderClv,
    OrderEvent,
    Signal,
    StrategyVariant,
    VenueMarket,
    VenueRequest,
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
            created_at=None, rejection_reason=None) -> Signal:
    row = Signal(run_id=gap.run_id, variant_id=variant_id, gap_snapshot_id=gap.id,
                 venue_market_id=market.id, side="yes",
                 price_target=None if price_target is None else Decimal(price_target),
                 decision=decision, rejection_reason=rejection_reason, labels={},
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


def _venue_request(session, env="prod", method="GET", ts=None, venue="kalshi",
                   path="/trade-api/v2/portfolio/balance", status=200) -> VenueRequest:
    row = VenueRequest(venue=venue, env=env, method=method, path=path, status=status,
                       ts=ts or WED, elapsed_ms=10)
    session.add(row)
    session.flush()
    return row


#: t12's tests read a different ISO week (2026, 37: Mon 2026-09-07 05:00Z to 2026-09-14 05:00Z)
#: so its fixtures never collide with the week-38 rows the rest of this module seeds.
T12_WED = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)


def _seed_declined_week(session) -> None:
    """Ruling B-I5/B-I6: two rejected signals for `sharp_direct` (reasons `edge` and
    `price_band`), each with `gap_outcomes` rows under both benchmarks, and one skipped intent
    for `sharp_direct` (reason `kickoff`) whose candidate signal also carries `gap_outcomes`."""
    game = _game(session, kickoff=T12_WED + timedelta(hours=6))
    market = _market(session, game.id, "T12MKT1")

    def _rejected(reason, price_target):
        gap = _gap(session, market, created_at=T12_WED)
        _gap_outcome(session, gap, benchmark_type="pinnacle_t5")
        _gap_outcome(session, gap, benchmark_type="kalshi_last_trade_pre_kick")
        _signal(session, gap, market, "sharp_direct", price_target=price_target,
               decision="rejected", created_at=T12_WED, rejection_reason=reason)

    _rejected("edge", "0.5000")
    _rejected("price_band", "0.5200")

    gap = _gap(session, market, created_at=T12_WED)
    _gap_outcome(session, gap, benchmark_type="pinnacle_t5")
    _gap_outcome(session, gap, benchmark_type="kalshi_last_trade_pre_kick")
    signal = _signal(session, gap, market, "sharp_direct", price_target="0.5000",
                     decision="candidate", created_at=T12_WED)
    intent = Intent(id=uuid.uuid4(), signal_id=signal.id, variant_id="sharp_direct",
                    venue="kalshi", venue_market_id=market.id, ticker=market.ticker, side="yes",
                    target_prob=Decimal("0.5100"), signal_created_at=T12_WED,
                    created_at=T12_WED, replay=False)
    session.add(intent)
    session.flush()
    event = OrderEvent(intent_id=intent.id, ts=T12_WED, kind="skipped", reason="kickoff",
                       replay=False)
    session.add(event)
    session.flush()


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
    assert set(TABLE_KEYS) == {"t1", "t2", "t3", "t4", "t4b", "t5", "t6", "t7", "t8", "t11", "t9",
                               "t10", "t12"}
    for key, table in tables.items():
        assert isinstance(table, Table), key
        assert table.title and table.header, key
        assert table.columns, key
        assert table.rows, f"{key} must render a placeholder row, never an empty table"
        for row in table.rows:
            assert len(row) == len(table.columns), key
    # T19: t7 (shadow veto CLV) and t10 (combo RFQ) are implemented; only t9 (H3's flow
    # imbalance) is still not-collected in this phase.
    assert "not collected" in tables["t9"].note
    for key in ("t7", "t10"):
        assert "not collected" not in tables[key].note


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


def test_report_cmd_persists_the_report_and_writes_report_written_event(cli_settings, db_session):
    """Task 12b: `harness report` writes `report_runs`/`report_cells` (`provisional = false`)
    and a `report_written` operator_event, in the same transaction as the markdown."""
    from harness.db.models import OperatorEvent, ReportCell, ReportRun

    db_session.commit()
    result = runner.invoke(app, ["report", "--week", str(WEEK), "--year", str(YEAR), "--out", "-"])
    assert result.exit_code == 0, result.output

    run = db_session.query(ReportRun).filter_by(year=YEAR, week=WEEK).one()
    assert run.provisional is False
    assert run.markdown == result.stdout
    assert db_session.query(ReportCell).filter_by(report_run_id=run.id).count() > 0

    event = db_session.query(OperatorEvent).filter_by(kind="report_written").one()
    assert event.ref == {"year": YEAR, "week": WEEK, "report_run_id": run.id}


def test_confirm_report_does_not_persist_a_shadow_run(tmp_path, cli_settings, db_session):
    """Fix round 1, I5: `--confirm` still renders and prints markdown but persists nothing to
    `report_runs`/`report_cells` and writes no `report_written` event -- a restricted
    confirmation run must never be able to shadow the week's real, unrestricted report."""
    from harness.db.models import OperatorEvent, ReportRun

    db_session.commit()
    selection = {"year": YEAR, "week": WEEK, "criteria_hash": "0" * 64, "cells": [], "contrasts": []}
    path = tmp_path / "selection.json"
    write_selected(path, selection)

    result = runner.invoke(app, ["report", "--week", str(WEEK), "--year", str(YEAR),
                                 "--out", "-", "--confirm", str(path)])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("# ")

    assert db_session.query(ReportRun).filter_by(year=YEAR, week=WEEK).count() == 0
    assert db_session.query(OperatorEvent).filter_by(kind="report_written").count() == 0


def test_re_rendering_an_annotated_week_shows_the_fence(cli_settings, db_session):
    """Fix round 2, C2: `build_meta` reads the newest `report_annotations` row for the week and
    sets `meta["annotation"]`, so the annotator's bullets -- which used to reach no reader --
    render inside the fenced "model-written, unverified" block on the next `harness report` for
    that week. Wired through the CLI's report command, not `render_markdown` called directly:
    that only proves the renderer, never that anything sets the key it reads. The annotated run
    is not the one this call persists (`build_meta` runs before `persist_report`), so this also
    proves the read is keyed on the week and not on the run this render is about to create."""
    from harness.db.models import ReportAnnotation, ReportRun
    from harness.report.weekly import ANNOTATION_HEADER

    earlier = ReportRun(year=YEAR, week=WEEK, generated_at=WEEK_START, provisional=False,
                        build_sha="0" * 40, criteria_hash="0" * 64, config_hashes=[])
    db_session.add(earlier)
    db_session.flush()
    db_session.add(ReportAnnotation(report_run_id=earlier.id, model="claude-opus-5",
                                    prompt_hash="0" * 64, bullets=["412 orders t1[0,1]."],
                                    cost_usd=Decimal("0.010000"), created_at=WEEK_START))
    db_session.commit()

    result = runner.invoke(app, ["report", "--week", str(WEEK), "--year", str(YEAR), "--out", "-"])
    assert result.exit_code == 0, result.output
    assert ANNOTATION_HEADER in result.stdout
    assert "412 orders t1[0,1]." in result.stdout

    newest = db_session.query(ReportRun).filter_by(year=YEAR, week=WEEK).order_by(
        ReportRun.id.desc()).first()
    assert newest.id != earlier.id, "the render must persist its own new run"


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


# --- fix round 1 -------------------------------------------------------------------------------


def test_a_homogeneous_stratum_counts_no_significant_cell(db_session, env_settings):
    """C1: the pre-registration record says a tau^2 = 0 stratum "counts no cell as significant
    on the posterior interval". Three cells at a positive grand mean, ten games each, all with
    the same cell mean, must leave the §9.6 count at zero."""
    for i in range(10):
        game = _game(db_session)
        for j, mid in enumerate(("0.3000", "0.4200", "0.5500")):
            market = _market(db_session, game.id, f"T-ML-{i}-{j}")
            # Within-cell variation keeps se > 0; identical cell means keep tau^2 = 0.
            value = "0.0200" if i % 2 == 0 else "0.0400"
            _gap(db_session, market, venue_mid=mid, gap_mid=value, gap_maker_net=value)
    db_session.flush()

    t4 = _tables(db_session, env_settings)["t4"]
    populated = [r for r in t4.rows
                 if r[t4.columns.index("fair_source")] == "direct"
                 and r[t4.columns.index("gap_mid")] != PLACEHOLDER]
    assert len(populated) == 3
    assert all(r[t4.columns.index("gap_mid")][2] == 10 for r in populated), "not greyed"
    assert "no heterogeneity detected" in t4.note
    assert "posterior interval excludes zero: 0" in t4.note
    assert all(r[t4.columns.index("posterior")] == PLACEHOLDER for r in populated)
    # And nothing the selection artefact would carry claims otherwise.
    assert not any(c["posterior_excludes_zero"] for c in select_cells({"t4": t4})["cells"])


def test_table4_reports_feed_kind_as_a_stratum(db_session, env_settings):
    """I1/I2: the grid carries feed_kind beside the staleness buckets as a stratum. The
    `gap_mid`, `gap_maker_net` and `clv_mid_p` panel columns stay display columns pooled over
    every feed (decision 2026-09-08 moved only the family/shrinkage/§9.6 CI to the featured
    feed, not these display cells); each `feed`/`stale` stratum column carries its own slice
    alone."""
    from harness.report.tables import HEADLINE_PANEL

    assert HEADLINE_PANEL == "gap_mid"
    game = _game(db_session)
    featured = _market(db_session, game.id, "T-ML-FEATURED")
    alternate = _market(db_session, game.id, "T-ML-ALTERNATE")
    _gap(db_session, featured, feed_kind="featured", gap_mid="0.0500")
    _gap(db_session, alternate, feed_kind="alternate", gap_mid="0.0700")
    db_session.flush()

    t4 = _tables(db_session, env_settings)["t4"]
    for name in ("feed featured", "feed alternate", "feed unknown"):
        assert name in t4.columns
    row = next(r for r in t4.rows
               if r[t4.columns.index("fair_source")] == "direct"
               and r[t4.columns.index("price_bucket")] == "50-65"
               and r[t4.columns.index("ttk")] == "< 3 h"
               and r[t4.columns.index("sport")] == "nfl"
               and r[t4.columns.index("market_type")] == "moneyline")
    # The display panel still pools both feeds; each stratum column carries its own feed alone.
    assert row[t4.columns.index("gap_mid")][1] == 2
    assert abs(row[t4.columns.index("gap_mid")][0] - 0.06) < 1e-9
    assert row[t4.columns.index("feed featured")][1] == 1
    assert row[t4.columns.index("feed alternate")][1] == 1
    assert row[t4.columns.index("feed unknown")] == PLACEHOLDER
    assert "gap_mid" in t4.header


def test_table4_family_runs_on_the_featured_feed_only(db_session, env_settings):
    """User decision 2026-09-08: families A/B, the empirical-Bayes shrinkage and the §9.6
    significant-cell count run on `gap_mid` restricted to `feed_kind = featured` -- the
    addendum's own words are "the headline H2 claim is from `feed_kind = featured`". Ten
    featured-feed games land clearly positive; ten separate alternate-feed games land clearly
    negative and would pull a pooled-feed mean back toward zero. The cell's BH verdict must
    follow the featured rows (reject, positive direction), and the `feed alternate` display
    column must still show the alternate rows untouched."""
    for i in range(10):
        # Distinct games per iteration: n_clusters counts games (F14), and a pooled-feed CI
        # would otherwise cancel the paired +/- rows within one cluster instead of averaging
        # across ten.
        featured_game = _game(db_session)
        featured_market = _market(db_session, featured_game.id, f"T-ML-FEAT-{i}")
        # Small within-feed variation keeps the cluster-robust SE > 0 without moving the mean
        # off a clearly positive ~0.10.
        featured_value = "0.0900" if i % 2 == 0 else "0.1100"
        _gap(db_session, featured_market, feed_kind="featured", gap_mid=featured_value)
        alternate_game = _game(db_session)
        alternate_market = _market(db_session, alternate_game.id, f"T-ML-ALT-{i}")
        alternate_value = "-0.0900" if i % 2 == 0 else "-0.1100"
        _gap(db_session, alternate_market, feed_kind="alternate", gap_mid=alternate_value)
    db_session.flush()

    t4 = _tables(db_session, env_settings)["t4"]
    row = next(r for r in t4.rows
               if r[t4.columns.index("fair_source")] == "direct"
               and r[t4.columns.index("price_bucket")] == "50-65"
               and r[t4.columns.index("ttk")] == "< 3 h"
               and r[t4.columns.index("sport")] == "nfl"
               and r[t4.columns.index("market_type")] == "moneyline")
    assert row[t4.columns.index("bh")] == "reject", (
        "the featured-only rows are clearly positive and should reject; a pooled-feed CI "
        "(alternate rows cancel the featured ones) would not")
    assert row[t4.columns.index("feed alternate")][1] == 10
    assert row[t4.columns.index("feed alternate")][0] < 0, (
        "the alternate display column must still show the alternate rows' own (negative) mean")


def test_adverse_drift_uses_only_fair_changed_rows(db_session, env_settings):
    """I3: criterion 5 reads adverse drift on fair_changed rows; an order whose fair never
    moved must not be averaged in. cross_fill has no 0 m markout by construction, so its
    column is the placeholder and the header says why."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    game = _game(db_session)
    moved = _market(db_session, game.id, "T-ML-MOVED")
    still = _market(db_session, game.id, "T-ML-STILL")
    changed_order = _order(db_session, moved, PRIMARY)
    unchanged_order = _order(db_session, still, PRIMARY)
    _fill(db_session, changed_order)
    _fill(db_session, unchanged_order)
    _markout(db_session, changed_order, "fill", "0m", fair_p="0.5800", fair_changed=True)
    _markout(db_session, unchanged_order, "fill", "0m", fair_p="0.5500", fair_changed=False)
    db_session.flush()

    t3 = _tables(db_session, env_settings)["t3"]
    row = next(r for r in t3.rows if r[1] == "queue_model" and r[2] == "featured"
               and r[3].startswith("<="))
    drift = row[t3.columns.index("adverse_drift")]
    assert drift[1] == 1, "only the fair_changed row counts"
    assert abs(drift[0] - 0.03) < 1e-9  # 0.58 - 0.55
    cross = next(r for r in t3.rows if r[1].endswith("snapshot_cross") and r[2] == "featured"
                 and r[3].startswith("<="))
    assert cross[t3.columns.index("adverse_drift")] == PLACEHOLDER
    assert "cross_fill" in t3.header


def test_table3_counts_every_episode_in_the_slice_and_not_a_straddling_one(db_session,
                                                                          env_settings):
    """M3/M4: an unfilled order is still an episode, and a reprice chain whose head was placed
    before Monday belongs to the previous week's report, so this one must not recount it.

    The two behaviours sit on different variants so each lands in its own table-3 row and
    neither can mask the other.
    """
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECONDARY, "wide_band", "secondary")
    game = _game(db_session)
    unfilled_market = _market(db_session, game.id, "T-ML-UNFILLED")
    _order(db_session, unfilled_market, PRIMARY)
    straddle_market = _market(db_session, game.id, "T-ML-STRADDLE")
    _order(db_session, straddle_market, SECONDARY, placed_at=WEEK_START - timedelta(hours=2),
           status="cancelled", cancel_reason="reprice",
           cancelled_at=WEEK_START - timedelta(hours=1))
    tail = _order(db_session, straddle_market, SECONDARY,
                  placed_at=WEEK_START + timedelta(hours=1))
    _fill(db_session, tail, filled_at=WEEK_START + timedelta(hours=2))
    db_session.flush()

    t3 = _tables(db_session, env_settings)["t3"]

    def slice_row(variant):
        return next(r for r in t3.rows if r[0] == variant and r[1] == "queue_model"
                    and r[2] == "featured" and r[3].startswith("<="))

    unfilled = slice_row("sharp_direct")
    assert unfilled[t3.columns.index("fills")] == 0
    assert unfilled[t3.columns.index("episodes")] == 1, "an unfilled order is still an episode"

    straddle = slice_row("wide_band")
    assert straddle[t3.columns.index("fills")] == 1, "the tail's fill is this week's"
    assert straddle[t3.columns.index("episodes")] == 0, "the chain's head is last week's"
    assert "first order" in t3.header


def test_table6_ignores_a_replay_fill_on_a_live_order(db_session, env_settings):
    """M6 (reviewer M7): the queue-consumption ratio's `exists` subquery must carry the same
    `replay = false` filter every other fill query does."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-HOME")
    order = _order(db_session, market, PRIMARY)
    fill = _fill(db_session, order)
    fill.replay = True
    db_session.flush()

    t6 = _tables(db_session, env_settings)["t6"]
    row = next(r for r in t6.rows if r[1] == "queue-consumption ratio median")
    assert row[2] == PLACEHOLDER and row[3] == 0


def test_criteria_text_carries_the_kickoff_moved_exclusion_and_the_insufficient_verdict():
    """M1: the hash is the identity of the definition set, so a definitional exclusion that is
    missing from the text is missing from the hash."""
    from harness.report import CRITERIA_TEXT, criteria_hash

    assert "kickoff_moved" in CRITERIA_TEXT
    assert "insufficient (fails)" in CRITERIA_TEXT
    assert len(criteria_hash()) == 64


def test_table8_does_not_claim_a_stale_exclusion_table_4_cannot_perform(db_session,
                                                                       env_settings):
    """M2: `gap_outcomes` has no `stale` column, so only table 2's executed-variant path can
    filter on it."""
    t8 = _tables(db_session, env_settings)["t8"]
    assert "gap_outcomes" in t8.header
    assert "excluded from every gate criterion and from tables 2 and 4" not in t8.header


def test_table11_empty_reads_zero_non_get(db_session, env_settings):
    """I1: an empty week still renders table 11 (a placeholder row, like every other table),
    and the tripwire note reads 0 rather than being silently absent."""
    t11 = _tables(db_session, env_settings)["t11"]
    assert t11.rows == [[PLACEHOLDER] * len(t11.columns)]
    assert t11.note == "prod non-GET = 0"


def test_table11_counts_rows_per_env_and_method(db_session, env_settings):
    """I1: seeded rows land in the right (env, method) bucket and nowhere else."""
    _venue_request(db_session, env="prod", method="GET")
    _venue_request(db_session, env="prod", method="GET")
    _venue_request(db_session, env="demo", method="POST")
    t11 = _tables(db_session, env_settings)["t11"]
    counts = {(r[0], r[1]): r[2] for r in t11.rows}
    assert counts[("prod", "GET")] == 2
    assert counts[("demo", "POST")] == 1
    assert t11.note == "prod non-GET = 0"


def test_table11_flags_a_prod_non_get_row(db_session, env_settings):
    """I1: the tripwire this table exists to carry -- a production write must never be
    dormant-but-silent in the report."""
    _venue_request(db_session, env="prod", method="GET")
    _venue_request(db_session, env="prod", method="POST")
    t11 = _tables(db_session, env_settings)["t11"]
    counts = {(r[0], r[1]): r[2] for r in t11.rows}
    assert counts[("prod", "POST")] == 1
    assert t11.note == "prod non-GET = 1"


def test_restrict_to_selection_keys_a_contrast_on_its_benchmark(db_session, env_settings):
    """M5: `CONTRAST_KEY` promises a two-part key; a selection naming another benchmark must
    not silently match the pinnacle_t5 contrast."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECONDARY, "wide_band", "secondary")
    db_session.flush()
    tables = _tables(db_session, env_settings)

    matching = {"cells": [], "contrasts": [{"variant": "wide_band",
                                            "benchmark_type": "pinnacle_t5"}]}
    other = {"cells": [], "contrasts": [{"variant": "wide_band",
                                         "benchmark_type": "consensus_t5"}]}
    assert [r[0] for r in restrict_to_selection(tables, matching)["t2"].rows] == ["wide_band"]
    assert restrict_to_selection(tables, other)["t2"].rows == [[PLACEHOLDER] * len(
        tables["t2"].columns)]


def test_table1_reports_tick_coverage_per_variant(db_session, env_settings):
    """Amendment 4: two pricing ticks in the week, the primary scored on both and the secondary
    on one, so the rotation's asymmetry is visible beside every cross-variant comparison."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECONDARY, "constrained", "secondary")
    game = _game(db_session)
    market = _market(db_session, game.id, "T-ML-COVER")
    first = _gap(db_session, market, created_at=WED)
    second = _gap(db_session, market, created_at=WED + timedelta(hours=1))
    _signal(db_session, first, market, PRIMARY, created_at=WED)
    _signal(db_session, second, market, PRIMARY, created_at=WED + timedelta(hours=1))
    _signal(db_session, first, market, SECONDARY, created_at=WED)
    db_session.flush()

    table = _tables(db_session, env_settings)["t1"]
    assert "tick_coverage" in table.columns
    by_name = {row[0]: row for row in table.rows}
    idx = table.columns.index("tick_coverage")
    # `_share` returns a float, never a formatted string.
    assert by_name["sharp_direct"][idx] == 1.0
    assert by_name["constrained"][idx] == 0.5


def test_table1_tick_coverage_is_a_placeholder_with_no_pricing_ticks(db_session, env_settings):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    db_session.flush()

    table = _tables(db_session, env_settings)["t1"]
    idx = table.columns.index("tick_coverage")
    assert all(row[idx] is PLACEHOLDER for row in table.rows)


# --- Task 11: table 1's stopped-share note ----------------------------------------------------


def _equity(session, variant_id, ts, stop, cash="3000.00"):
    session.add(EquitySnapshot(
        ts=ts, variant_id=variant_id, cash=Decimal(cash), open_stake=Decimal("0"),
        mtm_open=None, mtm_coverage=None, n_open_positions=0, n_open_orders=0,
        peak_equity_7d=Decimal(cash), drawdown_pct=Decimal("0.0000"), drawdown_stop=stop))
    session.flush()


def test_table1_notes_the_stopped_share_per_variant(db_session, env_settings):
    """§9.3: the stop is reported as a note, never as a column -- an annotation does not enter
    a cross-variant comparison."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _variant(db_session, SECONDARY, "constrained", "secondary")
    for i in range(4):
        _equity(db_session, PRIMARY, WED + timedelta(minutes=5 * i), stop=i == 0)
        _equity(db_session, SECONDARY, WED + timedelta(minutes=5 * i), stop=False)
    db_session.flush()

    table = _tables(db_session, env_settings)["t1"]
    assert "drawdown stop: sharp_direct 25.0% of equity samples this week" in table.note
    assert "constrained" not in table.note        # a zero share is not reported
    assert "drawdown_stop" not in table.columns   # a note, never a column


def test_table1_notes_no_drawdown_stop_when_nothing_tripped(db_session, env_settings):
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _equity(db_session, PRIMARY, WED, stop=False)
    db_session.flush()
    assert _tables(db_session, env_settings)["t1"].note == "drawdown stop: none"


def test_table1_stopped_share_ignores_rows_outside_the_week_and_unevaluated_ones(
        db_session, env_settings):
    """A row written before the gate shipped carries NULL and is neither a stop nor a sample."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    _equity(db_session, PRIMARY, WED - timedelta(days=14), stop=True)   # a previous week
    _equity(db_session, PRIMARY, WED, stop=None)                       # never evaluated
    db_session.flush()
    assert _tables(db_session, env_settings)["t1"].note == "drawdown stop: none"


# --- table 12: declined candidates, with both counterfactual estimators ----------------------


def test_t12_is_appended_after_t10_and_renders_last():
    from harness.report.tables import TABLE_KEYS

    assert TABLE_KEYS[-1] == "t12"
    assert TABLE_KEYS == ("t1", "t2", "t3", "t4", "t4b", "t5", "t6", "t7", "t8", "t11", "t9",
                          "t10", "t12")


def test_t12_row_keys_are_composite_and_unique(db_session, env_settings):
    """Ruling B-I5: the row key is the row's identity, never a position. Two reasons for one
    variant must not collide on `sharp_direct` and `sharp_direct#2`."""
    _seed_declined_week(db_session)
    tables = weekly_tables(db_session, 2026, 37, env_settings)
    t12 = tables["t12"]
    keys = [t12.row_key(row) for row in t12.rows]
    assert len(keys) == len(set(keys))
    assert all("/" in key and ":" in key for key in keys)
    # report_cells.row_key is String(64); persist_report truncates to 60 before its own suffix,
    # so an over-long key would be silently cut there instead of here.
    assert all(len(key) <= 64 for key in keys)
    assert "sharp_direct/rejected:edge" in keys


def test_t12_names_its_two_estimators_in_the_column_keys(db_session, env_settings):
    t12 = weekly_tables(db_session, 2026, 37, env_settings)["t12"]
    assert t12.columns == ["variant/reason", "kind", "count", "share",
                           "clv_rejected_gap_outcomes", "clv_rejected_gap_outcomes_kalshi",
                           "clv_skipped_intent_snapshot", "clv_skipped_intent_snapshot_kalshi"]
    assert "pinnacle_t5" in t12.header and "kalshi_last_trade_pre_kick" in t12.header


def test_a_rejected_row_fills_only_the_rejected_estimator(db_session, env_settings):
    _seed_declined_week(db_session)
    t12 = weekly_tables(db_session, 2026, 37, env_settings)["t12"]
    row = next(r for r in t12.rows if r[0] == "sharp_direct/rejected:edge")
    columns = dict(zip(t12.columns, row))
    assert columns["kind"] == "rejected"
    assert columns["clv_rejected_gap_outcomes"] != PLACEHOLDER
    assert columns["clv_skipped_intent_snapshot"] == PLACEHOLDER


def test_a_skipped_row_fills_only_the_skipped_estimator(db_session, env_settings):
    _seed_declined_week(db_session)
    t12 = weekly_tables(db_session, 2026, 37, env_settings)["t12"]
    row = next(r for r in t12.rows if r[0].startswith("sharp_direct/skipped:"))
    columns = dict(zip(t12.columns, row))
    assert columns["kind"] == "skipped"
    assert columns["clv_skipped_intent_snapshot"] != PLACEHOLDER
    assert columns["clv_rejected_gap_outcomes"] == PLACEHOLDER


def test_t12_shares_sum_to_one_per_kind(db_session, env_settings):
    _seed_declined_week(db_session)
    t12 = weekly_tables(db_session, 2026, 37, env_settings)["t12"]
    for kind in ("rejected", "skipped"):
        shares = [r[3] for r in t12.rows if r[1] == kind and r[3] != PLACEHOLDER]
        assert shares and abs(sum(shares) - 1.0) < 1e-9


def test_t12_is_a_placeholder_on_a_week_with_nothing_declined(db_session, env_settings):
    t12 = weekly_tables(db_session, 2026, 37, env_settings)["t12"]
    assert t12.rows == [[PLACEHOLDER] * len(t12.columns)]


def test_t12_is_not_a_gate_input():
    """R1: adding a table key changes the markdown and the cells and nothing else."""
    from harness.report import gate

    source = Path(gate.__file__).read_text()
    assert "TABLE_KEYS" not in source and "t12" not in source
