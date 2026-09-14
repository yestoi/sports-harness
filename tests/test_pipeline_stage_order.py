"""Fix 48: the pricing pass's stage order, and that reordering it changed nothing else.

On the NAS from 2026-09-11 16:47 CT every pricing block read
`{"gaps": 0, "order": [], "signals": {}, "fair_derived": 3079, "variants_run": [],
"budget_exhausted": true}` -- 172 runs in eight hours, no gap snapshot, no signal, no intent --
because stage 1 computed direct *and* derived fair values before anything else and that alone
outlasted the 45 s budget. `price_and_signal` now prices the direct fair values, snapshots the
gaps they cover, scores the variants that only consume direct rows, and only then pays for the
margin model.

The load-bearing test here is the parity one: with budget to spare, the six-stage pipeline has to
produce exactly the rows the single pass produced. `_single_pass` below is that single pass,
kept as the reference implementation it is.
"""

import itertools

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import FairValue, Game, MarketGapSnapshot, OddsSnapshot, Run, Signal, VenueMarket, VenueQuote
from harness.matching.teams import seed_teams_from_espn
from harness.pricing.fair import compute_fair_values
from harness.pricing.gaps import build_gap_snapshots
from harness.strategy import pipeline as pipeline_module
from harness.strategy.pipeline import (
    STAGE_NAMES,
    _insert_signals,
    _load_gap_rows,
    consumes_derived,
    price_and_signal,
    pricing_order,
)
from harness.strategy.run import run_strategy
from harness.strategy.variants import active_variants, load_variants, register_variants

import json

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
ROWS = json.loads((FIXD / "odds_lines_game.json").read_text())
#: The seven registered production variants, read-only. Six are `sources_allowed: [direct]`;
#: only `sharp_plus_derived` takes derived rows, and it is a secondary -- so the gate variant and
#: the primary are both scored in stage 3, which is the whole point of the reorder.
PROD_VARIANTS = Path(__file__).resolve().parents[1] / "harness" / "variants"

HOME, AWAY = 14, 19  # Rams, Giants
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def _single_pass(session, run_id, now, settings, budget_s):
    from tests.pricing_baseline import baseline_pipeline

    return baseline_pipeline().price_and_signal(session, run_id, now, settings, budget_s)


def _vm(game_id, i, market_type, threshold=None, side_team_id=None, side=None, match_status="matched"):
    return VenueMarket(
        venue="kalshi", ticker=f"KXNFL-S-{i}", event_ticker="KXNFL-EVT-S", series_ticker="KXNFL",
        game_id=game_id, market_type=market_type,
        threshold=Decimal(str(threshold)) if threshold is not None else None,
        side_team_id=side_team_id, side=side,
        match_confidence=Decimal("1.00") if match_status != "unmatched" else Decimal("0.00"),
        match_status=match_status, first_seen_raw_id=1, last_seen_at=NOW,
    )


_raw = itertools.count(1)


def _seed_run(session, game, markets, now, run=None):
    """One run over an existing game: its own odds snapshots and its own quotes.

    Two runs seeded this way and priced at the same `now` are independent: the previous-fair
    lookback takes rows with `created_at < now`, and both runs stamp theirs at `now` exactly, so
    neither can see the other's.
    """
    if run is None:
        run = Run(started_at=now, status="running")
        session.add(run)
        session.flush()
    # Keep each source quote's age: the fixture includes an older conflicting lowvig
    # moneyline. Flattening timestamps would make latest_book_lines choose an arbitrary tie.
    for r in ROWS:
        fetched_at = now + (datetime.fromisoformat(r["fetched_at"]) - NOW)
        book_last_update = now + (datetime.fromisoformat(r["book_last_update"]) - NOW)
        session.add(OddsSnapshot(
            raw_id=next(_raw), run_id=run.id, book=r["book"], game_id=game.id,
            market_type=r["market_type"], outcome_team_id=r["outcome_team_id"],
            outcome_side=r["outcome_side"],
            point=Decimal(str(r["point"])) if r["point"] is not None else None,
            price_decimal=Decimal(str(r["price_decimal"])),
            book_last_update=book_last_update, fetched_at=fetched_at,
        ))
    for m in markets:
        session.add(VenueQuote(
            raw_id=next(_raw), run_id=run.id, venue_market_id=m.id,
            yes_bid=Decimal("0.50"), yes_ask=Decimal("0.52"), no_bid=Decimal("0.48"),
            no_ask=Decimal("0.50"), yes_bid_size=Decimal("100"), yes_ask_size=Decimal("120"),
            volume=Decimal("50"), volume_24h=Decimal("500"), open_interest=Decimal("100"),
            fetched_at=now,
        ))
    session.commit()
    return run


def _seed(db_session, suffix="", sport="nfl"):
    seed_teams_from_espn(db_session, "nfl", NFL)
    game = Game(sport=sport, home_team_id=HOME, away_team_id=AWAY, kickoff_utc=NOW + timedelta(days=2))
    db_session.add(game)
    db_session.flush()
    markets = [
        _vm(game.id, 1, "moneyline", side_team_id=HOME),
        _vm(game.id, 2, "moneyline", side_team_id=AWAY),
        _vm(game.id, 3, "spread", threshold="3.5", side_team_id=HOME),
        _vm(game.id, 4, "spread", threshold="6.5", side_team_id=HOME),
        _vm(game.id, 5, "spread", threshold="9.5", side_team_id=HOME),
        _vm(game.id, 6, "total", threshold="44.5", side="over"),
        _vm(game.id, 7, "total", threshold="47.5", side="over"),
        # matched, but a shape no fair value is ever produced for -> a "no fair" gap row.
        _vm(game.id, 8, "draw"),
        # match_status excludes this one from gap snapshots even though it has a quote.
        _vm(game.id, 9, "moneyline", side_team_id=HOME, match_status="unmatched"),
        # same shape as the HOME moneyline but only fuzzy-matched.
        _vm(game.id, 10, "moneyline", side_team_id=HOME, match_status="fuzzy"),
    ]
    for market in markets:
        market.ticker += suffix
    db_session.add_all(markets)
    db_session.commit()
    return game, markets


def _tie_inputs(rows, markets):
    # Explicit seeded IDs: matched HOME moneyline and the derived HOME 9.5 spread.
    # The fuzzy HOME moneyline must never become an input through database row order.
    direct = next(row for row in rows if row.venue_market_id == markets[0].id)
    derived = next((row for row in rows if row.venue_market_id == markets[4].id), None)
    assert (direct.match_status, direct.fair_source, direct.market_type,
            direct.side_team_id) == ("matched", "direct", "moneyline", HOME)
    if derived is not None:
        assert (derived.match_status, derived.fair_source, derived.market_type,
                derived.side_team_id, derived.threshold) == (
                    "matched", "derived", "spread", HOME, Decimal("9.5"))
    return direct, derived


def _gap_rows(session, run_id):
    # Only run-specific identities differ; include every scientific field and annotation.
    return {snap.venue_market_id: {
        column.name: getattr(snap, column.name) for column in MarketGapSnapshot.__table__.columns
        if column.name not in {"id", "run_id", "fair_value_id"}}
        for snap in session.query(MarketGapSnapshot).filter_by(run_id=run_id)}


def _signal_rows(session, run_id):
    return {(signal.variant_id, signal.venue_market_id, signal.side): {
        column.name: getattr(signal, column.name) for column in Signal.__table__.columns
        if column.name not in {"id", "run_id", "gap_snapshot_id"}}
        for signal in session.query(Signal).filter_by(run_id=run_id)}


def _fair_rows(session, run_id):
    return {(row.game_id, row.market_type, row.outcome_team_id, row.outcome_side,
             row.threshold, row.fair_source): {
        column.name: getattr(row, column.name) for column in FairValue.__table__.columns
        if column.name not in {"id", "run_id"}}
        for row in session.query(FairValue).filter_by(run_id=run_id)}


def _candidate_rows(session, run_id):
    """The parity population after D12: `decision = 'candidate'` rows, with every field
    `_signal_rows` already compares -- `edge`, `stake`, `contracts`, `labels`, the prices and
    the reasons among them."""
    return {key: row for key, row in _signal_rows(session, run_id).items()
            if row["decision"] == "candidate"}


@pytest.mark.parametrize("gate", ["sharp_direct", "sharp_two_sided", "sharp_plus_derived"])
def test_the_stage_order_produces_exactly_what_the_single_pass_produced(
        env_settings, db_session, monkeypatch, gate):
    """Fix 48's parity contract: with budget to spare, the six stages write the single pass's rows.

    Same game, same books, same quotes, same `now`, two runs: one through the reference single
    pass, one through the reordered pipeline. Every gap snapshot and every signal has to match,
    field for field, and so do the counts and the variant order.
    """
    from tests.pricing_baseline import baseline_pipeline

    env_settings.gate_variant = gate
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    annotations = {(sport, bucket, side): Decimal("0.0123")
                   for sport in ("nfl", "ncaaf") for bucket in range(0, 100, 5)
                   for side in ("yes", "no")}
    stopped = {v.variant_id for v in active_variants(db_session)}
    for module in (pipeline_module, baseline_pipeline()):
        monkeypatch.setattr(module, "as_measured_table", lambda *a: annotations)
        monkeypatch.setattr(module, "stopped_variants", lambda *a: stopped)
    second_game, second_markets = _seed(db_session, suffix="-second", sport="ncaaf")
    old_run = _seed_run(db_session, game, markets, NOW)
    _seed_run(db_session, second_game, second_markets, NOW, run=old_run)
    # `pricing_order` rotates the secondary tail by `run_id % len(tail)` (Amendment 4), so two
    # runs only see the same order when their ids agree modulo the tail length. Filler runs make
    # them agree; comparing the two orders is the point of the assertion below.
    tail = len(active_variants(db_session)) - len({gate, "sharp_direct"})
    while True:
        new_run = _seed_run(db_session, game, markets, NOW)
        if new_run.id % tail == old_run.id % tail:
            break
    _seed_run(db_session, second_game, second_markets, NOW, run=new_run)

    old = _single_pass(db_session, old_run.id, NOW, env_settings, budget_s=600)
    new = price_and_signal(db_session, new_run.id, NOW, env_settings, budget_s=600)

    assert new["budget_exhausted"] is False
    for key in ("fair_direct", "fair_derived", "no_sharp", "gaps", "order"):
        assert new[key] == old[key], key

    # D12 amends fix 48's parity contract. What remains identical is what made the reorder
    # scientifically safe: no candidate, price, size, label or order moves.
    assert _fair_rows(db_session, new_run.id) == _fair_rows(db_session, old_run.id)
    assert _gap_rows(db_session, new_run.id) == _gap_rows(db_session, old_run.id)
    assert _candidate_rows(db_session, new_run.id) == _candidate_rows(db_session, old_run.id)
    assert ({name: counts["candidate"] for name, counts in new["signals"].items()}
            == {name: counts["candidate"] for name, counts in old["signals"].items()})

    # ... and what changed is asserted separately, against the suppressed set the pipeline
    # recorded. Per variant and in total: the rows the new run did not store are exactly the
    # derived-row rejections of the direct-only variants, and it stored nothing the old run
    # did not.
    old_rows = _signal_rows(db_session, old_run.id)
    new_rows = _signal_rows(db_session, new_run.id)
    assert set(new_rows) - set(old_rows) == set()
    missing = set(old_rows) - set(new_rows)
    assert all(old_rows[key]["decision"] == "rejected" for key in missing)
    names = {v.variant_id: v.name for v in active_variants(db_session)}
    by_variant = {}
    for variant_id, _market_id, _side in missing:
        by_variant[names[variant_id]] = by_variant.get(names[variant_id], 0) + 1
    assert by_variant == new["rescore_suppressed"]
    assert sum(by_variant.values()) == sum(new["rescore_suppressed"].values())
    assert (len(old_rows) - len(new_rows)) == sum(new["rescore_suppressed"].values())


def test_every_stage_records_what_it_cost(env_settings, db_session, monkeypatch):
    """Rule 5: `notes->'pricing'->'stages'` is an ordered list of what each stage cost and
    whether it ran, so the next budget conversation has numbers rather than a guess."""
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, markets, NOW)

    calls = []

    def watch(name, fn):
        def observed(*args, **kwargs):
            calls.append(name if name != "gaps" else f"gaps_{kwargs['phase']}")
            return fn(*args, **kwargs)
        return observed

    for attr, name in (("compute_direct_fair_values", "fair_direct"),
                       ("compute_derived_fair_values", "fair_derived"),
                       ("build_gap_snapshots", "gaps"), ("run_strategy", "variant")):
        monkeypatch.setattr(pipeline_module, attr, watch(name, getattr(pipeline_module, attr)))
    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)
    derived_at = calls.index("fair_derived")
    assert calls[:2] == ["fair_direct", "gaps_direct"]
    assert calls[2:derived_at] and set(calls[2:derived_at]) == {"variant"}
    assert calls[derived_at + 1] == "gaps_derived"
    assert calls[derived_at + 2:] and set(calls[derived_at + 2:]) == {"variant"}

    stages = result["stages"]
    assert [s["name"] for s in stages] == list(STAGE_NAMES)
    assert all(s["status"] == "ran" for s in stages), stages
    assert all(isinstance(s["elapsed_ms"], int) and s["elapsed_ms"] >= 0 for s in stages)
    assert result["fair_derived_skipped"] is False


def test_a_budget_that_dies_after_the_direct_variants_still_scored_the_gate_and_the_primary(
        env_settings, db_session, monkeypatch):
    """Rule 4, and the reason fix 48 exists.

    The deadline is tripped after stage 3. The gate variant and the primary have been scored,
    there are gap snapshots to score them against, the derived consumer is named as skipped, and
    `fair_derived_skipped` says the 0 in `fair_derived` means "not computed" rather than "none".
    Before the reorder this same budget produced nothing at all.
    """
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, markets, NOW)

    # The Nth call to time.monotonic() returns N. price_and_signal calls it once for the
    # deadline, once per ok() check, and twice per variant it scores (Amendment 4's timing).
    # Six direct-only variants then consume calls 3..20, and the check after the loop is call
    # 21: budget_s=20 keeps every check inside the deadline until that one.
    counter = itertools.count()
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: next(counter))

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)

    direct_only = [v.name for v in active_variants(db_session) if not consumes_derived(v)]
    assert result["budget_exhausted"] is True
    assert result["gaps"] > 0
    assert sorted(result["variants_run"]) == sorted(direct_only)
    assert result["variants_skipped"] == ["sharp_plus_derived"]
    assert result["gate_variant_missing"] is False
    assert result["order"][0] == env_settings.gate_variant
    assert result["variants_run"][0] == env_settings.gate_variant
    assert result["fair_derived"] == 0
    assert result["fair_derived_skipped"] is True
    assert db_session.query(Signal).filter_by(run_id=run.id).count() > 0

    by_name = {s["name"]: s for s in result["stages"]}
    assert [by_name[n]["status"] for n in STAGE_NAMES] == [
        "ran", "ran", "ran", "skipped", "skipped", "skipped"]
    assert [by_name[n]["cause"] for n in ("fair_derived", "gaps_derived", "variants_derived")] == [
        "budget", "budget", "budget"]
    assert by_name["variants_direct"]["units"] > 0


def test_a_suppressed_direct_only_variant_is_marked_complete(env_settings, db_session):
    """Ruling I10. `record_order` computes `variants_partial` as `scored - complete`, and it is
    today's stage-6 `full=True` pass that moves the six direct-only variants out of it: stage 3
    scores with `full=direct_complete`, and `direct_complete` is False whenever any quoted
    matched market has no direct fair (`no_sharp: 2140` in production). So the branch that
    suppresses a direct-only variant marks it complete in the same place -- its direct universe
    **is** its full universe -- and `variants_partial` stays empty.

    Computed independently of the code: the fixture seeds a market whose shape no fair value is
    ever produced for (`_vm(..., "draw")`), so `direct_complete` is False, and every registered
    variant but `sharp_plus_derived` is direct-only.
    """
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, markets, NOW)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)

    direct_only = [v.name for v in active_variants(db_session) if not consumes_derived(v)]
    assert result["variants_partial"] == []
    assert sorted(result["variants_run"]) == sorted(v.name for v in active_variants(db_session))
    assert set(result["rescore_suppressed"]) <= set(direct_only)


def test_nothing_is_re_scored_when_the_derived_phase_adds_no_rows(env_settings, db_session):
    """The control case: stage 5 adds nothing, so there is nothing to suppress and
    `rescore_suppressed` is empty. `cause` says why the stage did no work, which is the
    distinction `status: skipped` alone could not make (§1.5(a))."""
    game, markets = _seed(db_session)
    direct_markets = [m for m in markets if m.market_type == "moneyline"]
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, direct_markets, NOW)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)

    assert result["rescore_suppressed"] == {}
    stage = {s["name"]: s for s in result["stages"]}["variants_derived"]
    assert stage["cause"] in (None, "nothing_to_do")


def test_every_stage_entry_carries_units_remaining_and_cause(env_settings, db_session):
    """§1.5(a): ms alone cannot separate an expensive stage from a busy one, and a skipped stage
    with no cause beyond "budget" cannot be told from one with nothing to do.

    Computed independently: with budget to spare all six stages run, so every `cause` is null
    and every `remaining_ms` is positive; `units` is the work each stage did -- fair rows, gap
    rows, or scored (variant, row) pairs -- so the three variant stages' units are multiples of
    the gap-row count and the two fair stages' units match `fair_direct`/`fair_derived`.
    """
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, markets, NOW)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)

    stages = {s["name"]: s for s in result["stages"]}
    assert [s["name"] for s in result["stages"]] == list(STAGE_NAMES)
    for entry in result["stages"]:
        assert set(entry) == {"name", "elapsed_ms", "status", "units", "remaining_ms", "cause"}
        assert isinstance(entry["units"], int) and entry["units"] >= 0
        assert isinstance(entry["remaining_ms"], int)
        assert entry["cause"] is None
    assert stages["fair_direct"]["units"] == result["fair_direct"]
    assert stages["fair_derived"]["units"] == result["fair_derived"]
    assert stages["gaps_direct"]["units"] + stages["gaps_derived"]["units"] == result["gaps"]
    assert result["variant_ms_rescore"].keys() <= result["variant_ms"].keys()


def test_the_second_gap_call_never_duplicates_the_first_ones_rows(env_settings, db_session):
    """Rule 2(e): one gap row per market per run, whichever phase wrote it."""
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, markets, NOW)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)

    snaps = db_session.query(MarketGapSnapshot).filter_by(run_id=run.id).all()
    assert len(snaps) == result["gaps"]
    assert len({s.venue_market_id for s in snaps}) == len(snaps)
    # Both phases contributed: a direct-fair market and a market with no fair at all.
    assert {s.fair_source for s in snaps} >= {"direct", "derived"}
    assert any(s.no_fair_reason is not None for s in snaps)

    # And a second whole pass over the same run adds nothing.
    again = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)
    assert again["gaps"] == 0
    assert db_session.query(MarketGapSnapshot).filter_by(run_id=run.id).count() == len(snaps)


def test_only_sharp_plus_derived_consumes_derived_fair_values(db_session):
    """The (c)/(f) split is read off the registered variants' own `sources_allowed`, so it is
    pinned here rather than assumed anywhere else."""
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    variants = active_variants(db_session)

    assert [v.name for v in variants if consumes_derived(v)] == ["sharp_plus_derived"]
    assert next(v for v in variants if v.name == "sharp_plus_derived").tier == "secondary"


@pytest.mark.parametrize("gate", ["sharp_two_sided", "sharp_plus_derived"])
def test_configured_gate_and_primary_score_before_derived_budget_expires(
        env_settings, db_session, monkeypatch, gate):
    env_settings.gate_variant = gate
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, markets, NOW)
    expired = False
    seen = []
    real_strategy = pipeline_module.run_strategy

    def score(rows, variant, *args, **kwargs):
        nonlocal expired
        seen.append(variant.name)
        signals = real_strategy(rows, variant, *args, **kwargs)
        if variant.tier == "primary":
            expired = True
        return signals

    monkeypatch.setattr(pipeline_module, "run_strategy", score)
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: 1000 if expired else 0)
    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)
    assert seen == [gate, "sharp_direct"]
    assert result["variants_run"] == seen
    assert result["variants_partial"] == seen
    assert result["fair_derived_skipped"] and result["no_sharp_skipped"]
    assert result["gaps"] > 0
    assert all(result["signals"][name]["candidate"] + result["signals"][name]["rejected"] > 0
               for name in seen)


def test_derived_consumer_alone_still_scores_direct_gaps_when_no_derived_rows_are_added(
        env_settings, db_session):
    game, markets = _seed(db_session)
    # Only directly priced moneyline markets have quotes on this run.
    direct_markets = [m for m in markets if m.market_type == "moneyline"]
    variants = [v for v in load_variants(PROD_VARIANTS) if v.name == "sharp_plus_derived"]
    register_variants(db_session, variants, NOW, prune=True)
    run = _seed_run(db_session, game, direct_markets, NOW)
    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)
    assert result["gaps"] > 0
    assert result["variants_run"] == ["sharp_plus_derived"]
    assert result["variants_partial"] == []
    assert db_session.query(Signal).filter_by(run_id=run.id).count() == result["gaps"]


def test_equal_edge_scoring_uses_captured_market_order_and_reconciles_mixed_priority_rows(
        env_settings, db_session):
    from dataclasses import replace
    from harness.pricing.fair import compute_direct_fair_values, compute_derived_fair_values

    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, markets, NOW)
    direct = compute_direct_fair_values(db_session, run.id, NOW, env_settings)
    captured = []
    build_gap_snapshots(db_session, run.id, NOW, env_settings.tz_local,
                        phase="direct", market_order=captured)
    compute_derived_fair_values(db_session, direct.pending, run.id, NOW, env_settings)
    build_gap_snapshots(db_session, run.id, NOW, env_settings.tz_local, phase="derived")
    rows = _load_gap_rows(db_session, run.id)
    d, m = _tie_inputs(rows, markets)
    assert m is not None
    assert d.gap_snapshot_id < m.gap_snapshot_id
    assert m.venue_market_id in captured  # the capture occurs before the direct filter
    traversal = [m.venue_market_id, d.venue_market_id]
    ordered = _load_gap_rows(db_session, run.id, traversal)
    assert [r.venue_market_id for r in ordered[:2]] == traversal
    # Force a scientific tie independently of the shape model, with both rows eligible.
    tied = [replace(r, fair_p=Decimal("0.50"), disagreement=Decimal("0"), n_groups=2,
                    staleness_s=0, prev_fair_p=None, market_type="spread", side_team_id=HOME)
            for r in ordered[:2]]
    variant = next(v for v in active_variants(db_session) if v.name == "sharp_plus_derived")
    partial = run_strategy([tied[1]], variant, NOW)
    assert partial[0].decision == "candidate" and partial[0].labels["cap_per_game"]
    _insert_signals(db_session, run.id, variant, NOW, partial)
    db_session.commit()
    original_id = db_session.query(Signal.id).filter_by(
        run_id=run.id, variant_id=variant.variant_id, venue_market_id=d.venue_market_id).scalar()
    complete = run_strategy(tied, variant, NOW)
    assert complete[1].decision == "candidate" and not complete[1].labels["cap_per_game"]
    _insert_signals(db_session, run.id, variant, NOW, complete, replace_existing=True)
    db_session.commit()
    db_session.expire_all()
    persisted = db_session.query(Signal).filter_by(id=original_id).one()
    assert persisted.labels == complete[1].labels
    assert db_session.query(Signal).filter_by(run_id=run.id).count() == 2
    # Direct-only variants also need traversal preserved on the rejected derived row.
    direct_variant = next(v for v in active_variants(db_session) if v.name == "sharp_direct")
    old_order = run_strategy(tied, direct_variant, NOW)
    phase_order = run_strategy(list(reversed(tied)), direct_variant, NOW)
    assert old_order[0].labels["cap_per_game"]
    assert not phase_order[1].labels["cap_per_game"]


def test_pipeline_refreshes_mixed_gate_labels_after_derived_competition(
        env_settings, db_session, monkeypatch):
    from dataclasses import replace
    from tests.pricing_baseline import baseline_pipeline

    env_settings.gate_variant = "sharp_plus_derived"
    game, markets = _seed(db_session)
    variants = [v for v in load_variants(PROD_VARIANTS) if v.name == env_settings.gate_variant]
    register_variants(db_session, variants, NOW, prune=True)
    old_run = _seed_run(db_session, game, markets, NOW)
    new_run = _seed_run(db_session, game, markets, NOW)
    real_load = _load_gap_rows

    def tied_rows(session, run_id, *args):
        rows = real_load(session, run_id)
        direct, derived = _tie_inputs(rows, markets)
        # A controlled tie and traversal isolates pipeline reconciliation from model rounding.
        selected = [derived, direct] if derived is not None else [direct]
        return [replace(row, fair_p=Decimal("0.50"), disagreement=Decimal("0"), n_groups=2,
                        staleness_s=0, prev_fair_p=None, market_type="spread", side_team_id=HOME)
                for row in selected]

    monkeypatch.setattr(pipeline_module, "_load_gap_rows", tied_rows)
    monkeypatch.setattr(baseline_pipeline(), "_load_gap_rows", tied_rows)
    _single_pass(db_session, old_run.id, NOW, env_settings, budget_s=600)
    result = price_and_signal(db_session, new_run.id, NOW, env_settings, budget_s=600)
    assert result["variants_partial"] == []
    expected = _signal_rows(db_session, old_run.id)
    assert any(row["fair_source"] == "direct" and not row["labels"]["cap_per_game"]
               for row in expected.values())
    assert _signal_rows(db_session, new_run.id) == expected
