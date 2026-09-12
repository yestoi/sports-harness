"""The weekly report: the ten tables over one ISO week's rows, the selection artefact, and
`harness report --week`.

Week 38 of 2026 is the pre-registration record's freeze week, so every fixture here lands
inside it: Monday 2026-09-14 00:00 America/Chicago through the following Monday, which is
2026-09-14 05:00Z to 2026-09-21 05:00Z.
"""

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
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
    MetricSample,
    Order,
    OrderbookEvent,
    OrderClv,
    OrderEvent,
    ReportRun,
    Run,
    Signal,
    StrategyVariant,
    VenueMarket,
    VenueRequest,
)
from harness.report.tables import (
    CONTRAST_BENCHMARK,
    FLAG_CLUSTERS,
    GREY_CLUSTERS,
    NOT_COLLECTED,
    PLACEHOLDER,
    TABLE_KEYS,
    Table,
    is_flagged,
    is_grey,
    week_bounds,
    weekly_tables,
)
from harness.report.amendments import AMENDMENTS
from harness.report.weekly import (
    build_meta,
    read_selected,
    render_markdown,
    restrict_to_selection,
    select_cells,
    write_selected,
)
from harness.settlement.benchmarks import BENCHMARK_TYPES

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
                               "t10", "t12", "t13"}
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


def test_table1_names_what_its_fill_rate_actually_is(db_session, env_settings):
    """Addendum 0.11: the column key stays `fill_rate` -- it is a stored `report_cells.col_key`
    that the Study surface reads -- and the header says what it measures."""
    t1 = _tables(db_session, env_settings)["t1"]
    assert "fill_rate" in t1.columns
    assert "actual fill rate (orders with a `queue_model` fill / placements)" in t1.header


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


def test_t12_is_appended_after_t10_and_t13_after_t12():
    from harness.report.tables import TABLE_KEYS

    assert TABLE_KEYS[-1] == "t13"
    assert TABLE_KEYS == ("t1", "t2", "t3", "t4", "t4b", "t5", "t6", "t7", "t8", "t11", "t9",
                          "t10", "t12", "t13")


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


# --- table 13: the operational diagnostic (addendum 0.3, 0.4, 1.3) ---------------------------


def test_the_audit_register_opens_with_order_157_pending():
    """Addendum 0.4 / D4: the register is a code constant because `docs/` is not in the image
    and the report has to read it from inside the container. 6B updates it."""
    from harness.report.audits import AUDIT_STATUSES, ORDER_AUDITS

    assert set(ORDER_AUDITS) == {157}
    entry = ORDER_AUDITS[157]
    assert entry.status == "pending" and entry.status in AUDIT_STATUSES
    assert entry.since == "2026-09-11"
    assert "6B" in entry.note
    for audit in ORDER_AUDITS.values():
        assert audit.status in AUDIT_STATUSES
        # Minor 5: an ISO-8601 date, pinned rather than left to the writer's taste.
        date.fromisoformat(audit.since)


def _t13(db_session, env_settings, year=YEAR, week=WEEK, now=None):
    """t13 as a `{item: (value, unit, note)}` map, which is how every assertion below reads it."""
    table = weekly_tables(db_session, year, week, env_settings,
                          now=now or (WEEK_START + timedelta(days=7)))["t13"]
    assert table.columns == ["item", "value", "unit", "note"]
    return {row[0]: (row[1], row[2], row[3]) for row in table.rows}


def test_t13_counts_actual_fills_apart_from_the_counterfactual_ones(db_session, env_settings):
    """Addendum 0.3: an actual filled order has at least one `queue_model` fill; the
    counterfactual population is the orders whose only fills are `no_watcher`. They are two
    rows with two units, never one pooled number."""
    # Three markets, one game: `uq_open_order` allows only one open order per
    # (venue, ticker, side, variant), and every `_order` here defaults to `status="open"`.
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    game = _game(db_session)
    actual = _order(db_session, _market(db_session, game.id, "T13MKT1A"), PRIMARY)
    _fill(db_session, actual, fill_method="queue_model")
    counterfactual = _order(db_session, _market(db_session, game.id, "T13MKT1B"), PRIMARY)
    _fill(db_session, counterfactual, fill_method="no_watcher")
    audited = _order(db_session, _market(db_session, game.id, "T13MKT1C"), PRIMARY)
    _fill(db_session, audited, fill_method="queue_model")
    db_session.flush()

    rows = _t13(db_session, env_settings)
    assert rows["filled orders, week"] == (1 + 1, "orders", rows["filled orders, week"][2])
    assert rows["counterfactual orders, week"][0] == 1
    assert rows["counterfactual orders, week"][1] == "orders"
    assert rows["distinct games filled, week"] == (1, "games", rows["distinct games filled, week"][2])
    assert rows["fill rows, queue_model, week"][0] == 2
    assert rows["fill rows, no_watcher, week"][0] == 1
    assert rows["fill rows, queue_model, week"][1] == "fill rows"
    assert rows["week key"][0] == "America/Chicago ISO week, Amendment 5"


def test_t13_reports_the_audit_register_and_the_orders_under_audit(db_session, env_settings,
                                                                   monkeypatch):
    """Addendum 0.4: one row per audited order, plus the count of the gate variant's actual
    filled orders that are in the register with a non-`validated` status."""
    from harness.report.audits import Audit
    import harness.report.tables as tables_module

    # Two markets: `uq_open_order` allows only one open order per (venue, ticker, side,
    # variant), and every `_order` here defaults to `status="open"`.
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    game = _game(db_session)
    audited = _order(db_session, _market(db_session, game.id, "T13MKT2A"), PRIMARY)
    _fill(db_session, audited, fill_method="queue_model")
    clean = _order(db_session, _market(db_session, game.id, "T13MKT2B"), PRIMARY)
    _fill(db_session, clean, fill_method="queue_model")
    db_session.flush()

    monkeypatch.setattr(tables_module, "ORDER_AUDITS", {
        audited.id: Audit("pending", "fill history not uniquely identified", "2026-09-11"),
        999_999: Audit("validated", "matches the tape", "2026-09-11"),
    })
    rows = _t13(db_session, env_settings)
    assert rows[f"order audit {audited.id}"][0] == "pending"
    assert rows[f"order audit {audited.id}"][1] == "audit status"
    assert "2026-09-11" in rows[f"order audit {audited.id}"][2]
    assert rows["order audit 999999"][0] == "validated"
    # Only the pending one is a fill event under audit, and only because its order actually
    # filled: the validated row and the order that is not in the register are not counted.
    assert rows["orders under audit"] == (1, "orders", rows["orders under audit"][2])


def test_t13_reads_coverage_from_runs_notes_only(db_session, env_settings):
    """Design review C1: no predicate on `runs.started_at`, no anti-join against a pricing
    table. Every coverage row is what `runs.notes` can say."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    db_session.add(Run(started_at=WED, status="ok", build_sha="abc",
                       notes={"pricing": {"gaps": 900, "budget_exhausted": False,
                                          "signals": {"sharp_direct": {"candidate": 12,
                                                                       "rejected": 88}}}}))
    db_session.add(Run(started_at=WED, status="ok", build_sha="abc",
                       notes={"pricing": {"gaps": 5, "budget_exhausted": True, "signals": {}}}))
    db_session.add(Run(started_at=WED, status="skipped", build_sha="abc", notes={"pricing": {}}))
    db_session.flush()

    rows = _t13(db_session, env_settings)
    assert rows["pricing runs, week"] == (2, "runs", rows["pricing runs, week"][2])
    assert rows["runs scoring the gate variant"][0] == 1
    assert rows["runs with no fair, gap or signal count"][0] == 1
    assert rows["runs with budget_exhausted"][0] == 1
    assert rows["runs scoring the gate variant"][1] == "runs"


def test_t13_coverage_counts_only_the_weeks_own_runs(db_session, env_settings):
    """Plan review C1: the coverage read is capped by the primary key walking backwards, so its
    window has to be closed at **both** ends. A run dated on or after the week's end is read and
    then excluded, and is reported in its own row so a cap that lands past a closed week is
    visible; a run a second before the week's start is never read into the window at all.

    Without the upper bound, the Monday 09:00 CT report for week 37 would count about 33 hours of
    week-38 runs among its own, and a report of an older week would describe a different week
    entirely.
    """
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    start, end = week_bounds(YEAR, WEEK, env_settings.tz_local)
    priced = {"pricing": {"gaps": 1, "signals": {"sharp_direct": {"candidate": 1,
                                                                 "rejected": 0}}}}
    db_session.add(Run(started_at=start, status="ok", build_sha="abc", notes=priced))
    db_session.add(Run(started_at=end + timedelta(hours=1), status="ok", build_sha="abc",
                       notes=priced))
    db_session.add(Run(started_at=start - timedelta(seconds=1), status="ok", build_sha="abc",
                       notes=priced))
    db_session.flush()

    rows = _t13(db_session, env_settings)
    assert rows["pricing runs, week"][0] == 1
    assert rows["runs scoring the gate variant"][0] == 1
    assert rows["notes read"][0] == 1
    assert rows["runs after the window"] == (1, "runs", rows["runs after the window"][2])


def test_t13_reads_the_executor_and_tape_counters_from_metric_samples(db_session, env_settings):
    """Ruling I1: tape gaps are the `ws.gaps` counter, not a table. There is no per-loop
    counter either, so the loop row counts `exec.loop_ms` samples and says so."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    db_session.add(MetricSample(ts=WED, source="exec", name="exec.loop_ms", value=Decimal("42")))
    db_session.add(MetricSample(ts=WED, source="exec", name="exec.loop_ms", value=Decimal("51")))
    db_session.add(MetricSample(ts=WED, source="exec", name="exec.loops_skipped",
                                value=Decimal("3")))
    db_session.add(MetricSample(ts=WED, source="ws", name="ws.gaps", value=Decimal("2")))
    db_session.flush()

    rows = _t13(db_session, env_settings)
    assert rows["executor loop samples"] == (2, "metric samples",
                                             rows["executor loop samples"][2])
    assert rows["executor loops skipped"] == (3, "loops", rows["executor loops skipped"][2])
    assert rows["tape gaps"] == (2, "gap events", rows["tape gaps"][2])


def test_t13_carries_the_freshness_pair(db_session, env_settings):
    """Addendum 0.3: this run's `generated_at` beside the newest *final* report's and its age."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    db_session.add(ReportRun(year=2026, week=37, generated_at=WEEK_START - timedelta(hours=2),
                             provisional=False, build_sha="abc", criteria_hash="h",
                             config_hashes=[], markdown="# w37", markdown_sha256="s"))
    db_session.add(ReportRun(year=2026, week=37, generated_at=WEEK_START - timedelta(minutes=5),
                             provisional=True, build_sha="abc", criteria_hash="h",
                             config_hashes=[], markdown=None, markdown_sha256=None))
    db_session.flush()

    now = WEEK_START
    rows = _t13(db_session, env_settings, now=now)
    assert rows["this run generated at"][0] == now.isoformat()
    # The provisional row is five minutes old and is *not* the one reported: a provisional run
    # is a trail, and the freshness pair is about the published report.
    assert rows["newest final report generated at"][0] == (
        WEEK_START - timedelta(hours=2)).isoformat()
    assert rows["newest final report age"] == (7200, "seconds",
                                               rows["newest final report age"][2])


def test_t13_on_an_empty_week_is_zeros_with_units_never_not_collected(db_session, env_settings):
    """Addendum 1.3: an empty week is a week with zero of everything, which is a measurement.
    `NOT_COLLECTED` would say the opposite -- that nothing was even looked at."""
    rows = _t13(db_session, env_settings)
    for item in ("filled orders, week", "distinct games filled, week",
                 "counterfactual orders, week", "fill rows, queue_model, week",
                 "pricing runs, week", "runs scoring the gate variant",
                 "runs with budget_exhausted", "tape gaps", "orders under audit",
                 "notes read", "runs after the window"):
        value, unit, _note = rows[item]
        assert value == 0, item
        assert unit and unit != NOT_COLLECTED, item
    assert rows["newest final report generated at"][0] == PLACEHOLDER
    assert NOT_COLLECTED not in {value for value, _u, _n in rows.values()}


def test_t13_renders_first_and_the_model_view_keeps_table_keys_order(db_session, env_settings):
    """Addendum 0.3 and design review Minor 3: the human reader gets the diagnostic first, the
    model gets it last, and the two orders are named rather than implied."""
    from harness.report.tables import RENDER_ORDER, TABLE_KEYS

    assert RENDER_ORDER[0] == "t13"
    assert set(RENDER_ORDER) == set(TABLE_KEYS)
    assert TABLE_KEYS[-1] == "t13"
    assert list(RENDER_ORDER[1:]) == [k for k in TABLE_KEYS if k != "t13"]

    tables = weekly_tables(db_session, YEAR, WEEK, env_settings, now=WEEK_START)
    text = render_markdown(tables, {"year": YEAR, "week": WEEK, "build_sha": "abc",
                                    "criteria_hash": "0" * 64, "config_hashes": []})
    assert text.index("(t13)") < text.index("(t1)")


def test_t13_sql_keys_no_pricing_table_by_run_id():
    """Design review C1, stated as a structural test so a later edit cannot quietly reintroduce
    the anti-join or the `runs.started_at` predicate.

    Only the **SQL literals** are inspected, not the whole block. The Python around them
    legitimately says `signals` (reading `notes.pricing.signals`) and `started_at` (applying the
    week's upper bound in memory, plan review C1), and a plain substring check over the block
    would fail on its own correct code.
    """
    import re

    from harness.report import tables as tables_module

    source = Path(tables_module.__file__).read_text()
    block = source.split("# --- table 13", 1)[1].split("# --- entry point", 1)[0]
    statements = " ".join(re.findall(r'text\("""(.*?)"""\)', block, flags=re.S)).lower()
    assert statements, "t13 defines no SQL, so this test would be checking nothing"
    assert "started_at" not in statements
    assert "not exists" not in statements
    for name in ("fair_values", "market_gap_snapshots", "signals", "orderbook_events",
                 "venue_trades", "from runs"):
        assert name not in statements, name
    # And every runs-derived count goes through the one capped reader, never a query of its own.
    assert "recent_runs" in block


# --- the confirmation path (addendum 0.8, 1.7; design review C2) -------------------------------


def _confirmation_t4(rows: list[list]) -> Table:
    """A table-4 shaped table with only the columns the confirmation path reads, so these stay
    pure-function tests. `rows` are `[fair_source, price_bucket, ttk, sport, market_type,
    posterior]` and the builder pads the rest with placeholders."""
    from harness.report.tables import _T4_COLUMNS

    columns = list(_T4_COLUMNS)
    posterior = columns.index("posterior")
    out = []
    for key in rows:
        row = [PLACEHOLDER] * len(columns)
        row[:5] = key[:5]
        row[posterior] = key[5]
        out.append(row)
    return Table("Table 4 (t4): mispricing map", "header", columns, out)


def _cell(estimate, n_clusters, lo, hi):
    return (estimate, n_clusters * 4, n_clusters, lo, hi)


def _selected(fair_source="direct", direction=1, n_clusters=42):
    return {"fair_source": fair_source, "price_bucket": "20-35", "ttk": "< 3 h", "sport": "nfl",
            "market_type": "moneyline", "panel": "gap_mid",
            "posterior_excludes_zero": True, "direction": direction, "n_clusters": n_clusters}


def test_select_cells_stores_the_direction_and_the_cluster_count():
    """Addendum 0.8: storing changes no rule. It records what week 2 chose so week 3 can print
    the one-sided count beside the registered two-sided one."""
    from harness.report.tables import _T4_COLUMNS

    columns = list(_T4_COLUMNS)
    rows = []
    for fair_source, estimate in (("direct", 0.0300), ("derived", -0.0200)):
        row = [PLACEHOLDER] * len(columns)
        row[:5] = [fair_source, "20-35", "< 3 h", "nfl", "moneyline"]
        row[columns.index("posterior")] = _cell(estimate, 42, estimate - 0.01, estimate + 0.01)
        row[columns.index("bh")] = "reject"
        rows.append(row)
    cells = select_cells({"t4": Table("t", "h", columns, rows)})["cells"]

    assert [c["direction"] for c in cells] == [1, -1]
    assert [c["n_clusters"] for c in cells] == [42, 42]


def test_select_cells_records_no_direction_for_a_cell_with_no_estimate():
    from harness.report.tables import _T4_COLUMNS

    columns = list(_T4_COLUMNS)
    row = [PLACEHOLDER] * len(columns)
    row[:5] = ["direct", "20-35", "< 3 h", "nfl", "moneyline"]
    row[columns.index("posterior")] = PLACEHOLDER
    row[columns.index("bh")] = "reject"
    cells = select_cells({"t4": Table("t", "h", columns, [row])})["cells"]
    assert cells[0]["direction"] is None and cells[0]["n_clusters"] is None


def test_a_cell_below_the_ten_cluster_floor_is_insufficient_not_confirmed():
    """Design review C2: the floor is a **restoration**. The pre-registration record already
    greys below 10 game clusters (Analysis plan line 63) and excludes greyed cells from every
    family (line 66); the confirmation count had not been applying it."""
    from harness.report.tables import GREY_CLUSTERS

    assert GREY_CLUSTERS == 10
    t4 = _confirmation_t4([
        ["direct", "20-35", "< 3 h", "nfl", "moneyline", _cell(0.0300, 4, 0.0200, 0.0400)],
    ])
    selection = {"cells": [_selected()], "contrasts": []}
    note = restrict_to_selection({"t4": t4}, selection)["t4"].note

    assert "insufficient (< 10 clusters) 1" in note
    assert "confirmed (two-sided) 0" in note


def test_the_confirmation_note_counts_selected_evaluated_insufficient_missing_and_confirmed():
    """Addendum 0.8's exact line: every denominator is printed, so a reader can see why a count
    is what it is rather than inferring it."""
    t4 = _confirmation_t4([
        # confirmed, on the selected direction
        ["direct", "20-35", "< 3 h", "nfl", "moneyline", _cell(0.0300, 42, 0.0200, 0.0400)],
        # confirmed, against the selected direction
        ["derived", "20-35", "< 3 h", "nfl", "moneyline", _cell(-0.0300, 42, -0.0400, -0.0200)],
        # evaluated, interval straddles zero
        ["direct", "35-50", "< 3 h", "nfl", "moneyline", _cell(0.0100, 42, -0.0100, 0.0300)],
        # evaluated, below the floor
        ["direct", "50-65", "< 3 h", "nfl", "moneyline", _cell(0.0300, 4, 0.0200, 0.0400)],
    ])
    selection = {"cells": [
        _selected(fair_source="direct"),
        _selected(fair_source="derived", direction=1),
        dict(_selected(fair_source="direct"), price_bucket="35-50"),
        dict(_selected(fair_source="direct"), price_bucket="50-65"),
        dict(_selected(fair_source="direct"), price_bucket="65-80"),   # no row in week 3
    ], "contrasts": []}

    note = restrict_to_selection({"t4": t4}, selection)["t4"].note
    assert "selected 5" in note
    assert "evaluated 4" in note
    assert "insufficient (< 10 clusters) 1" in note
    assert "missing (no row) 1" in note
    assert "confirmed (two-sided) 2" in note
    assert "of which on the selected direction 1" in note


def test_the_direction_count_is_printed_and_never_applied():
    """Design review C2 and D6: making the stored direction a *condition* turns the record's
    two-sided rule into a one-sided one, which is a success-threshold change under R1. The loop
    prints the number and applies nothing; the user's dated decision is what would apply it."""
    from harness.report.weekly import DIRECTION_NOTE
    from harness.report.tables import SIGNIFICANT_CELLS_REQUIRED

    assert SIGNIFICANT_CELLS_REQUIRED == 3
    assert DIRECTION_NOTE == "proposed one-sided reading, not in force"

    t4 = _confirmation_t4([
        ["direct", "20-35", "< 3 h", "nfl", "moneyline", _cell(-0.0300, 42, -0.0400, -0.0200)],
    ])
    selection = {"cells": [_selected(direction=1)], "contrasts": []}
    note = restrict_to_selection({"t4": t4}, selection)["t4"].note
    # The cell confirms on the registered two-sided rule even though it moved the other way.
    assert "confirmed (two-sided) 1" in note
    assert "of which on the selected direction 0" in note
    assert DIRECTION_NOTE in note


def test_the_contrast_note_prints_its_own_denominators():
    columns = ["variant", "tier", "basis", *BENCHMARK_TYPES, f"holm({CONTRAST_BENCHMARK})", "gate"]
    row = [PLACEHOLDER] * len(columns)
    row[0] = "wide_band"
    row[columns.index(CONTRAST_BENCHMARK)] = _cell(0.0200, 42, 0.0100, 0.0300)
    t2 = Table("Table 2 (t2): CLV per variant", "h", columns, [row])
    selection = {"cells": [], "contrasts": [
        {"variant": "wide_band", "benchmark_type": CONTRAST_BENCHMARK, "direction": 1,
         "n_clusters": 42},
        {"variant": "absent_variant", "benchmark_type": CONTRAST_BENCHMARK, "direction": -1,
         "n_clusters": 11},
    ]}
    note = restrict_to_selection({"t2": t2}, selection)["t2"].note
    assert "selected 2" in note and "evaluated 1" in note


def test_build_meta_counts_the_weeks_rows_inside_each_amendments_excluded_range(db_session,
                                                                                env_settings):
    """Addendum 0.9: per amendment, how many of *this week's* non-replay orders and signals fall
    inside its excluded run-id range. Amendment 4's range is runs 344-4327."""
    _variant(db_session, PRIMARY, "sharp_direct", "primary")
    game = _game(db_session)
    market = _market(db_session, game.id, "ELIGMKT")
    # A second market for the "outside" order: `uq_open_order` is one live order per
    # (venue, ticker, side, variant), so two open orders for the same variant need distinct
    # tickers to coexist. The eligibility query attributes an order to a run through its gap
    # snapshot's id, not through the market, so this changes nothing the test is checking.
    market2 = _market(db_session, game.id, "ELIGMKT2")
    inside = _gap(db_session, market)
    inside.run_id = 1000
    outside = _gap(db_session, market2)
    outside.run_id = 90_000
    db_session.flush()
    _order(db_session, market, PRIMARY, gap=inside)
    _order(db_session, market2, PRIMARY, gap=outside)
    _signal(db_session, inside, market, PRIMARY)
    _signal(db_session, outside, market2, PRIMARY)
    db_session.flush()

    meta = build_meta(db_session, env_settings, YEAR, WEEK, now=WEEK_START)
    eligibility = meta["eligibility"]
    # Run 1000 is inside both Amendment 2's (1-2320) and Amendment 4's (344-4327) ranges.
    assert eligibility[2]["orders"] == 1 and eligibility[2]["signals"] == 1
    assert eligibility[4]["orders"] == 1 and eligibility[4]["signals"] == 1
    # The amendments with no range are present and say so, rather than being absent.
    assert eligibility[3]["excluded_runs"] is None
    assert eligibility[3]["orders"] == 0 and eligibility[3]["signals"] == 0
    assert set(eligibility) == {a.number for a in AMENDMENTS}


def test_the_provenance_block_prints_one_eligibility_line_per_amendment(db_session,
                                                                       env_settings):
    """Addendum 0.9: "excluded by Amendment n: <orders> orders, <signals> signals", or "none"."""
    meta = build_meta(db_session, env_settings, YEAR, WEEK, now=WEEK_START)
    text = render_markdown(weekly_tables(db_session, YEAR, WEEK, env_settings, now=WEEK_START),
                           meta)
    assert "- Excluded by Amendment 4: 0 orders, 0 signals (runs 344-4327)" in text
    assert "- Excluded by Amendment 3: none" in text
    assert "- Excluded by Amendment 5: none" in text
    for amendment in AMENDMENTS:
        assert f"- Excluded by Amendment {amendment.number}:" in text
