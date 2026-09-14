import itertools
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import yaml
from sqlalchemy import text

from harness.db.models import Game, OddsSnapshot, Run, Signal, VenueMarket, VenueQuote
from harness.matching.teams import seed_teams_from_espn
from harness.strategy import pipeline as pipeline_module
from harness.strategy.pipeline import (
    _insert_signals,
    _load_gap_rows,
    price_and_signal,
    pricing_order,
)
from harness.strategy.run import run_strategy
from harness.strategy.variants import (
    Variant,
    active_variants,
    load_variants,
    register_variants,
    variant_from_config,
)

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
ROWS = json.loads((FIXD / "odds_lines_game.json").read_text())
VARIANTS_DIR = FIXD / "variants"
ROTATION_VARIANTS_DIR = FIXD / "variants_rotation"

HOME, AWAY = 14, 19  # Rams, Giants
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def _vm(game_id, i, market_type, threshold=None, side_team_id=None, side=None, match_status="matched"):
    return VenueMarket(
        venue="kalshi",
        ticker=f"KXNFL-P-{i}",
        event_ticker="KXNFL-EVT-P",
        series_ticker="KXNFL",
        game_id=game_id,
        market_type=market_type,
        threshold=Decimal(str(threshold)) if threshold is not None else None,
        side_team_id=side_team_id,
        side=side,
        match_confidence=Decimal("1.00") if match_status != "unmatched" else Decimal("0.00"),
        match_status=match_status,
        first_seen_raw_id=1,
        last_seen_at=NOW,
    )


def _seed(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    game = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY, kickoff_utc=NOW + timedelta(days=2))
    db_session.add(game)
    db_session.flush()

    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()

    fetched_at = NOW - timedelta(minutes=2)
    book_last_update = NOW - timedelta(minutes=3)
    for i, r in enumerate(ROWS):
        db_session.add(OddsSnapshot(
            raw_id=i + 1,
            run_id=run.id,
            book=r["book"],
            game_id=game.id,
            market_type=r["market_type"],
            outcome_team_id=r["outcome_team_id"],
            outcome_side=r["outcome_side"],
            point=Decimal(str(r["point"])) if r["point"] is not None else None,
            price_decimal=Decimal(str(r["price_decimal"])),
            book_last_update=book_last_update,
            fetched_at=fetched_at,
        ))

    markets = [
        _vm(game.id, 1, "moneyline", side_team_id=HOME),
        _vm(game.id, 2, "moneyline", side_team_id=AWAY),
        _vm(game.id, 3, "spread", threshold="3.5", side_team_id=HOME),
        _vm(game.id, 4, "spread", threshold="6.5", side_team_id=HOME),
        _vm(game.id, 5, "spread", threshold="9.5", side_team_id=HOME),
        _vm(game.id, 6, "total", threshold="44.5", side="over"),
        _vm(game.id, 7, "total", threshold="47.5", side="over"),
        # matched but a shape compute_fair_values never produces a FairValue for -> "no fair" signal.
        _vm(game.id, 8, "draw"),
        # match_status excludes this one from gap snapshots even though it has a quote.
        _vm(game.id, 9, "moneyline", side_team_id=HOME, match_status="unmatched"),
        # same shape as the HOME moneyline (direct fair) but only fuzzy-matched, so the strategy
        # must reject it specifically on match_confidence rather than on missing fair/source.
        _vm(game.id, 10, "moneyline", side_team_id=HOME, match_status="fuzzy"),
    ]
    db_session.add_all(markets)
    db_session.flush()

    for i, m in enumerate(markets):
        db_session.add(VenueQuote(
            raw_id=1000 + i,
            run_id=run.id,
            venue_market_id=m.id,
            yes_bid=Decimal("0.50"),
            yes_ask=Decimal("0.52"),
            no_bid=Decimal("0.48"),
            no_ask=Decimal("0.50"),
            yes_bid_size=Decimal("100"),
            yes_ask_size=Decimal("120"),
            volume=Decimal("50"),
            volume_24h=Decimal("500"),
            open_interest=Decimal("100"),
            fetched_at=NOW,
        ))
    db_session.commit()
    return game, run, markets


def test_price_and_signal_end_to_end(env_settings, db_session):
    game, run, markets = _seed(db_session)
    variants = load_variants(VARIANTS_DIR)
    register_variants(db_session, variants, NOW, prune=True)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)

    assert result["fair_direct"] == 5
    assert result["fair_derived"] == 2
    assert result["fair_errors"] == 0
    assert result["gaps"] == 9  # 10 markets seeded, minus the 1 unmatched
    assert result["budget_exhausted"] is False

    assert set(result["signals"]) == {"tiny"}
    tiny = result["signals"]["tiny"]
    # One signal per *direct* gap snapshot. `tiny` is `sources_allowed: [direct]`, and since 6D
    # decision D4 stage 6 no longer re-scores such a variant over the three rows stage 5 added
    # (two derived fairs and the shape with no fair at all): those rows could only ever be
    # `source_allowed`/`has_fair` rejections. They are counted in `rescore_suppressed` instead.
    assert tiny["candidate"] + tiny["rejected"] == 6
    assert result["rescore_suppressed"] == {"tiny": 3}

    signals = db_session.query(Signal).filter_by(run_id=run.id).all()
    assert len(signals) == 6

    fuzzy_market = next(m for m in markets if m.match_status == "fuzzy")
    fuzzy_signal = db_session.query(Signal).filter_by(run_id=run.id, venue_market_id=fuzzy_market.id).one()
    assert fuzzy_signal.decision == "rejected"
    assert fuzzy_signal.rejection_reason == "match_confidence"

    # Idempotent for gap snapshots. The signal count rises to 9 on a second call, and that is
    # not a second insert of the same rows: D4 suppresses only what stage *5* adds inside the
    # same call, and a re-priced run already carries its derived gap rows when stage 3 loads
    # the universe, so the three rows the first call suppressed are scored and stored now.
    # `price_and_signal` runs once per run in the tick; this is the re-pricing path only.
    result2 = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    assert result2["gaps"] == 0
    assert result2["rescore_suppressed"] == {}
    assert db_session.query(Signal).filter_by(run_id=run.id).count() == 9


def test_the_suppressed_count_is_exactly_the_derived_rows_the_direct_only_variant_skipped(
        env_settings, db_session):
    """§1.5's expected result, computed by hand: on a fixture with 3 direct gap rows, 2 derived
    gap rows, one direct-only variant and one derived consumer, the rejected-signal row count
    falls by exactly 2 -- the direct-only variant's two derived rejections -- and
    `rescore_suppressed` reads `{"<direct_only>": 2}`.

    Written against whatever the seeding helper in this file produces: assert the identity
    `rescore_suppressed[name] == len(all gap rows) - len(direct gap rows)` for each direct-only
    variant that was scored, which is the same statement at any fixture size.
    """
    game, run, markets = _seed(db_session)
    # `tiny` is `sources_allowed: [direct]`; its copy takes derived rows too, so stage 6 has
    # both a variant to suppress and one still to score.
    tiny = yaml.safe_load((VARIANTS_DIR / "tiny.yaml").read_text())
    derived_consumer = dict(tiny, name="tiny_derived", tier="secondary",
                            sources_allowed=["direct", "derived"])
    register_variants(db_session, [variant_from_config(tiny),
                                   variant_from_config(derived_consumer)], NOW, prune=True)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)

    # Read back from the database rather than recomputing the pipeline's arithmetic: the direct
    # phase writes a gap row only for a market that already has a direct fair value
    # (`gaps.py`'s `phase == "direct" and fair is None: continue`), so the direct universe is
    # exactly the `fair_source = 'direct'` rows and everything else is what stage 5 added.
    rows = db_session.execute(text(
        "select fair_source, count(*) from market_gap_snapshots where run_id = :run_id "
        "group by fair_source"), {"run_id": run.id}).all()
    gaps_total = sum(count for _source, count in rows)
    direct_gap_count = sum(count for source, count in rows if source == "direct")
    assert (gaps_total, direct_gap_count) == (9, 6)  # this fixture, counted once
    assert gaps_total == result["gaps"] > direct_gap_count > 0

    direct_only = [v.name for v in active_variants(db_session)
                   if "derived" not in (v.config.get("sources_allowed") or ())]
    assert direct_only == ["tiny"]
    for name in direct_only:
        assert name in result["variants_run"]
        assert result["rescore_suppressed"][name] == gaps_total - direct_gap_count
    assert set(result["rescore_suppressed"]) == set(direct_only)
    # The derived consumer is still scored over the whole universe, and only it.
    assert db_session.query(Signal).filter_by(run_id=run.id).count() == (
        direct_gap_count + gaps_total)
    assert set(result["variant_ms_rescore"]) == {"tiny_derived"}


GRID = [{"start": "0.0000", "end": "1.0000", "step": "0.0001"}]


def test_pipeline_persists_no_side_signals(env_settings, db_session):
    game, run, markets = _seed(db_session)
    markets[0].price_ranges = GRID
    db_session.commit()

    config = dict(yaml.safe_load((VARIANTS_DIR / "tiny.yaml").read_text()),
                  name="tiny_two_sided", sides=["yes", "no"])
    register_variants(db_session, [variant_from_config(config)], NOW, prune=True)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    assert result["gaps"] == 9
    counts = result["signals"]["tiny_two_sided"]
    # 6 direct gap snapshots, both sides (6D D4: the three rows stage 5 added are no longer
    # re-scored for a direct-only variant, and a two-sided variant suppresses two rows each).
    assert counts["candidate"] + counts["rejected"] == 12
    assert result["rescore_suppressed"] == {"tiny_two_sided": 6}

    # `_load_gap_rows` carries the venue's price grid through to the strategy
    rows = {r.venue_market_id: r for r in _load_gap_rows(db_session, run.id)}
    assert rows[markets[0].id].price_ranges == GRID
    assert rows[markets[1].id].price_ranges is None

    signals = db_session.query(Signal).filter_by(run_id=run.id).all()
    assert len(signals) == 12
    assert {s.side for s in signals} == {"yes", "no"}
    # uq_signal_key holds both sides of the same market in the same run
    assert set(Counter(s.venue_market_id for s in signals).values()) == {2}

    # Re-running the same run inserts no duplicate of a side already stored; the count rises
    # to 18 because the re-priced run's stage 3 already sees the derived gap rows the first
    # call's stage 6 suppressed (see `test_price_and_signal_end_to_end`).
    price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    assert db_session.query(Signal).filter_by(run_id=run.id).count() == 18
    assert set(Counter(
        s.venue_market_id for s in db_session.query(Signal).filter_by(run_id=run.id)
    ).values()) == {2}


def test_price_and_signal_stops_when_budget_is_spent(env_settings, db_session):
    game, run, markets = _seed(db_session)
    variants = load_variants(VARIANTS_DIR)
    register_variants(db_session, variants, NOW, prune=True)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=0)

    assert result["budget_exhausted"] is True
    # Stage 1 (direct fair values) always runs, regardless of the budget -- that is the half the
    # gate variant and the primary are scored on. Fix 48 moved the derived pass to stage 4, so a
    # spent budget now skips it: `fair_derived` is 0 and `fair_derived_skipped` says why, which
    # is what lets a coverage reader tell "not computed" from "computed, none found".
    assert result["fair_direct"] == 5
    assert result["fair_derived"] == 0
    assert result["fair_derived_skipped"] is True
    assert result["gaps"] == 0
    assert result["signals"] == {}
    assert result["variants_run"] == []
    assert db_session.query(Signal).filter_by(run_id=run.id).count() == 0


# --- final fix wave: variant rotation under budget ----------------------------
# Re-pinned by Amendment 4 (2026-09-08): the head of the order no longer rotates. A budget that
# only allows one variant now always spends it on the gate variant, then the primary; only the
# secondary tail still rotates by run_id (pinned by test_pricing_order_rotates_only_the_tail).

def test_variant_order_leads_with_the_gate_variant_and_the_skip_is_recorded(
        env_settings, db_session, monkeypatch):
    """A budget that only allows one variant must spend it on the variant the phase gate is
    judged on, on every run_id, and the variants it skipped must be visible in the result
    rather than silently missing."""
    variants = load_variants(ROTATION_VARIANTS_DIR)
    register_variants(db_session, variants, NOW, prune=True)
    assert [v.name for v in variants] == ["tiny", "tiny2"]  # sorted; active_variants matches
    assert [v.tier for v in variants] == ["primary", "secondary"]

    # A deterministic fake clock: the Nth call to time.monotonic() returns N. price_and_signal
    # calls it once to set the deadline, then once per ok() check (after fair values, after
    # gaps, then once per variant before scoring), and twice more per variant it scores (the
    # Amendment 4 per-variant timing). budget_s=4 keeps the first three checks (t=1,2,3) inside
    # the deadline (0+4) and expires on the second variant's check.
    gate_settings = env_settings.model_copy(update={"gate_variant": "tiny2"})
    counter = itertools.count()
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: next(counter))

    result_run1 = price_and_signal(db_session, 1001, NOW, gate_settings, budget_s=4)
    assert result_run1["budget_exhausted"] is True
    assert result_run1["gate_variant_missing"] is False
    assert result_run1["variants_run"] == ["tiny2"]
    assert result_run1["variants_skipped"] == ["tiny"]
    assert result_run1["order"] == ["tiny2", "tiny"]

    # 1001 % 2 == 1 and 1002 % 2 == 0 used to swap the first variant; the gate variant leads
    # on both now.
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: next(counter))
    result_run2 = price_and_signal(db_session, 1002, NOW, gate_settings, budget_s=4)
    assert result_run2["budget_exhausted"] is True
    assert result_run2["variants_run"] == ["tiny2"]
    assert result_run2["variants_skipped"] == ["tiny"]

    # With no registered row carrying the gate variant's name the primary leads instead, and
    # the run says so rather than silently demoting the ordering (the Amendment 3 incident).
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: next(counter))
    result_run3 = price_and_signal(db_session, 1003, NOW, env_settings, budget_s=4)
    assert result_run3["gate_variant_missing"] is True
    assert result_run3["gate_variant_id"] is None
    assert result_run3["variants_run"] == ["tiny"]
    assert result_run3["variants_skipped"] == ["tiny2"]


def test_pricing_clock_for_run_uses_finished_at_then_start_plus_budget():
    from datetime import timedelta

    from harness.strategy.pipeline import pricing_clock_for_run

    start = NOW
    done = SimpleNamespace(started_at=start, finished_at=start + timedelta(seconds=37))
    assert pricing_clock_for_run(done, 100) == start + timedelta(seconds=37)
    unfinished = SimpleNamespace(started_at=start, finished_at=None)
    assert pricing_clock_for_run(unfinished, 100) == start + timedelta(seconds=100)


# --- hotfix A2: the signals insert must survive more rows than psycopg can bind ---------

def _bulk_signal(i: int) -> SimpleNamespace:
    """The lightweight shape `price_and_signal` hands `_insert_signals` (see `run_strategy`)."""
    return SimpleNamespace(
        gap_snapshot_id=1,
        venue_market_id=i + 1,
        side="yes",
        fair_p=Decimal("0.5000"),
        fair_source="direct",
        venue_best_bid=Decimal("0.4900"),
        venue_best_ask=Decimal("0.5100"),
        price_target=Decimal("0.5100"),
        fee_at_target=Decimal("0.0100"),
        as_estimate=Decimal("0.0000"),
        edge=Decimal("0.0100"),
        edge_min=Decimal("0.0050"),
        stake=Decimal("1.00"),
        contracts=2,
        decision="rejected",
        rejection_reason="edge_floor",
        labels={"i": i},
        as_measured=None,
    )


def test_insert_signals_survives_more_rows_than_psycopg_can_bind(db_session):
    """A2: one INSERT ... VALUES per signal binds 21 parameters a row, so a single statement
    dies above 3120 rows ("number of parameters must be between 0 and 65535"). With 4218
    matched venue markets a variant clears that every tick, so the insert must be chunked.
    (`signals` declares no foreign keys, so only the run row needs to be real.)"""
    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()
    variant = Variant(name="bulk", tier="primary", config={}, variant_id="0123456789ab")

    inserted = _insert_signals(db_session, run.id, variant, NOW, [_bulk_signal(i) for i in range(3200)])

    assert inserted == 3200
    assert db_session.query(Signal).filter_by(run_id=run.id).count() == 3200


def test_insert_signals_inserts_every_chunk_including_the_short_last_one(db_session, monkeypatch):
    """The loop boundary itself: 5 rows in chunks of 2 is three statements, and all 5 land."""
    monkeypatch.setattr(pipeline_module, "SIGNAL_INSERT_CHUNK", 2)
    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()
    variant = Variant(name="bulk2", tier="primary", config={}, variant_id="ba9876543210")

    inserted = _insert_signals(db_session, run.id, variant, NOW, [_bulk_signal(i) for i in range(5)])

    assert inserted == 5
    assert db_session.query(Signal).filter_by(run_id=run.id).count() == 5


# --- Task 9 fix round 1, Important 5: pipeline must not pull in a settlement stage ------

def test_importing_pipeline_registers_no_settlement_stage(monkeypatch):
    """`pipeline.py` reads the D12 `as_measured` table on every pricing tick. It must do that
    through `harness.strategy.as_measured`, a plain query module, never through
    `harness.settlement.markouts` -- that module's body calls `register_stage("markouts", ...)`
    at import time, and `harness.settlement.job.STAGE_MODULES` is what is supposed to control
    registration order, not whichever module the recorder's tick happens to import first.

    A plain `importlib.reload(pipeline_module)` would not catch a regression here: by the time
    this test runs, `harness.settlement.markouts` is almost certainly already cached in
    `sys.modules` (other test modules import it directly), so re-executing only `pipeline.py`
    would not re-run `markouts.py`'s `register_stage` call even if `pipeline.py` still imported
    it. Both modules -- and `harness.strategy.as_measured`, so the real import path is exercised
    too -- are evicted from `sys.modules` first, and `STAGES` is emptied, so a fresh import of
    `pipeline` that goes anywhere near `harness.settlement.markouts` shows up as a fresh append.
    """
    import importlib
    import sys

    import harness.settlement.job as job_module

    monkeypatch.setattr(job_module, "STAGES", [])
    for name in ("harness.settlement.markouts", "harness.strategy.as_measured",
                "harness.strategy.pipeline"):
        monkeypatch.delitem(sys.modules, name, raising=False)

    importlib.import_module("harness.strategy.pipeline")

    assert job_module.STAGES == []


# --- Amendment 4: the gate variant and the primary are scored on every tick --------------------


def _pv(name, tier="secondary"):
    return Variant(name=name, tier=tier, config={}, variant_id=name[:12])


def test_pricing_order_puts_gate_variant_then_primary_first():
    variants = [_pv("alpha"), _pv("beta"), _pv("sharp_direct", "primary"), _pv("sharp_two_sided")]
    ordered, missing = pricing_order(variants, "sharp_two_sided", run_id=0)
    assert missing is False
    assert [v.name for v in ordered][:2] == ["sharp_two_sided", "sharp_direct"]
    assert sorted(v.name for v in ordered) == sorted(v.name for v in variants)


def test_pricing_order_rotates_only_the_tail():
    variants = [_pv("alpha"), _pv("beta"), _pv("gamma"),
                _pv("sharp_direct", "primary"), _pv("sharp_two_sided")]
    heads = set()
    tails = []
    for run_id in range(6):
        ordered, _ = pricing_order(variants, "sharp_two_sided", run_id)
        heads.add(tuple(v.name for v in ordered[:2]))
        tails.append([v.name for v in ordered[2:]])
    assert heads == {("sharp_two_sided", "sharp_direct")}
    assert len({tuple(t) for t in tails}) == 3          # alpha, beta, gamma rotate
    assert all(sorted(t) == ["alpha", "beta", "gamma"] for t in tails)


def test_pricing_order_reports_a_missing_gate_variant():
    variants = [_pv("alpha"), _pv("sharp_direct", "primary")]
    ordered, missing = pricing_order(variants, "sharp_two_sided", run_id=0)
    assert missing is True
    assert ordered[0].name == "sharp_direct"


def test_pricing_order_without_a_primary_still_leads_with_the_gate_variant():
    variants = [_pv("alpha"), _pv("sharp_two_sided")]
    ordered, missing = pricing_order(variants, "sharp_two_sided", run_id=1)
    assert missing is False
    assert ordered[0].name == "sharp_two_sided"


def test_pricing_order_does_not_repeat_a_gate_variant_that_is_the_primary():
    variants = [_pv("alpha"), _pv("sharp_direct", "primary")]
    ordered, missing = pricing_order(variants, "sharp_direct", run_id=0)
    assert missing is False
    assert [v.name for v in ordered] == ["sharp_direct", "alpha"]


def test_price_and_signal_records_order_and_per_variant_ms(env_settings, db_session):
    game, run, markets = _seed(db_session)
    register_variants(db_session, load_variants(VARIANTS_DIR), NOW, prune=True)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=30)

    assert result["order"] == result["variants_run"] + result["variants_skipped"]
    assert set(result["variant_ms"]) == set(result["variants_run"])
    assert all(isinstance(ms, int) and ms >= 0 for ms in result["variant_ms"].values())
    assert result["gate_variant_missing"] is True   # the fixture registers no gate variant
    assert result["gate_variant_id"] is None


def test_price_and_signal_records_the_resolved_gate_variant_id(env_settings, db_session, caplog):
    # B-I3: the run note carries the id, not only the name.
    game, run, markets = _seed(db_session)
    config = dict(yaml.safe_load((VARIANTS_DIR / "tiny.yaml").read_text()),
                  name=env_settings.gate_variant)
    gate = variant_from_config(config)
    register_variants(db_session, [gate], NOW, prune=True)

    with caplog.at_level(logging.WARNING, logger=pipeline_module.__name__):
        result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=30)

    assert result["gate_variant_missing"] is False
    assert result["gate_variant_id"] == gate.variant_id
    assert result["order"][0] == env_settings.gate_variant
    assert caplog.records == []


# --- Task 11: the risk gate's annotation ------------------------------------------------------


def _stop_the_variant(db_session, name="tiny", stop=True):
    """A tripped (or recovered) risk-gate verdict on `name`'s newest equity snapshot."""
    from harness.db.models import EquitySnapshot

    variant_id = db_session.execute(
        text("select variant_id from strategy_variants where name = :n"), {"n": name}).scalar_one()
    db_session.add(EquitySnapshot(
        ts=NOW - timedelta(minutes=5), variant_id=variant_id, cash=Decimal("2250.00"),
        open_stake=Decimal("0"), mtm_open=None, mtm_coverage=None, n_open_positions=0,
        n_open_orders=0, peak_equity_7d=Decimal("3000.00"), drawdown_pct=Decimal("-0.2500"),
        drawdown_stop=stop))
    db_session.flush()
    return variant_id


def test_price_and_signal_annotates_a_stopped_variant_and_decides_nothing_differently(
        env_settings, db_session):
    """Spec §9.3 in the pipeline: `drawdown_stop` rides on every signal of a stopped variant
    and changes not one decision (ruling A-C2/B-C1).

    The comparison is against the *unstopped* strategy run over the very rows the pipeline
    priced, so what is asserted is the whole recorded outcome -- decision, reason, price, stake
    and size -- and not merely that some count came out the same.
    """
    game, run, markets = _seed(db_session)
    register_variants(db_session, load_variants(VARIANTS_DIR), NOW, prune=True)
    _stop_the_variant(db_session)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    assert set(result["signals"]) == {"tiny"}

    rows = db_session.query(Signal).filter_by(run_id=run.id).all()
    assert rows
    assert all(s.labels["drawdown_stop"] is True for s in rows)
    assert all(s.rejection_reason != "drawdown_stop" for s in rows)

    variant = next(v for v in active_variants(db_session) if v.name == "tiny")
    # The reference run has to be over the universe the pipeline actually priced: since 6D
    # decision D4 a direct-only variant is scored on the direct gap rows alone, and the rows
    # stage 5 added are counted in `rescore_suppressed` rather than stored as rejections.
    priced = [r for r in _load_gap_rows(db_session, run.id) if r.fair_source == "direct"]
    assert result["rescore_suppressed"] == {"tiny": result["gaps"] - len(priced)}
    unstopped = run_strategy(priced, variant, NOW)
    want = {(s.venue_market_id, s.side): (s.decision, s.rejection_reason, s.price_target,
                                          s.edge, s.stake, s.contracts) for s in unstopped}
    got = {(s.venue_market_id, s.side): (s.decision, s.rejection_reason, s.price_target,
                                         s.edge, s.stake, s.contracts) for s in rows}
    assert got == want


def test_a_recovered_variant_is_not_annotated(env_settings, db_session):
    game, run, markets = _seed(db_session)
    register_variants(db_session, load_variants(VARIANTS_DIR), NOW, prune=True)
    _stop_the_variant(db_session, stop=False)

    price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    rows = db_session.query(Signal).filter_by(run_id=run.id).all()
    assert rows and all(s.labels["drawdown_stop"] is False for s in rows)
