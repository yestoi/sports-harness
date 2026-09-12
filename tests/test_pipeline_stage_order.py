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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import Game, MarketGapSnapshot, OddsSnapshot, Run, Signal, VenueMarket, VenueQuote
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
    """The pipeline as it stood before fix 48, as the parity reference.

    Deliberately a copy rather than an import: the thing being tested is that the new order
    produces the old order's rows, and a reference that shared code with the implementation
    would not be able to tell.
    """
    counts = compute_fair_values(session, run_id, now, settings)
    gaps = build_gap_snapshots(session, run_id, now, tz=settings.tz_local,
                               errored_game_ids=counts.errored_game_ids)
    variants = active_variants(session)
    rows = _load_gap_rows(session, run_id)
    ordered, _ = pricing_order(variants, settings.gate_variant, run_id)
    signals_out = {}
    for variant in ordered:
        signals = run_strategy(rows, variant, now)
        _insert_signals(session, run_id, variant, now, signals)
        session.commit()
        candidate = sum(1 for s in signals if s.decision == "candidate")
        signals_out[variant.name] = {"candidate": candidate, "rejected": len(signals) - candidate}
    return {"fair_direct": counts.direct, "fair_derived": counts.derived,
            "no_sharp": counts.no_sharp, "gaps": gaps, "signals": signals_out,
            "order": [v.name for v in ordered]}


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


def _seed_run(session, game, markets, now):
    """One run over an existing game: its own odds snapshots and its own quotes.

    Two runs seeded this way and priced at the same `now` are independent: the previous-fair
    lookback takes rows with `created_at < now`, and both runs stamp theirs at `now` exactly, so
    neither can see the other's.
    """
    run = Run(started_at=now, status="running")
    session.add(run)
    session.flush()
    fetched_at = now - timedelta(minutes=2)
    book_last_update = now - timedelta(minutes=3)
    for r in ROWS:
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


def _seed(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    game = Game(sport="nfl", home_team_id=HOME, away_team_id=AWAY, kickoff_utc=NOW + timedelta(days=2))
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
    db_session.add_all(markets)
    db_session.commit()
    return game, markets


def _gap_rows(session, run_id):
    """A run's gap snapshots keyed by market, with the ids the two runs cannot share dropped."""
    out = {}
    for snap in session.query(MarketGapSnapshot).filter_by(run_id=run_id):
        out[snap.venue_market_id] = {
            "fair_source": snap.fair_source, "fair_p": snap.fair_p,
            "no_fair_reason": snap.no_fair_reason, "prev_fair_p": snap.prev_fair_p,
            "gap_mid": snap.gap_mid, "gap_taker_net": snap.gap_taker_net,
            "gap_maker_net": snap.gap_maker_net, "n_groups": snap.n_groups,
            "disagreement": snap.disagreement, "staleness_s": snap.staleness_s,
            "feed_kind": snap.feed_kind, "stale_allowance_s": snap.stale_allowance_s,
            "soft_minus_sharp": snap.soft_minus_sharp, "ttk_minutes": snap.ttk_minutes,
        }
    return out


def _signal_rows(session, run_id):
    """A run's signals keyed by (variant, market, side), with run-local ids dropped."""
    out = {}
    for s in session.query(Signal).filter_by(run_id=run_id):
        out[(s.variant_id, s.venue_market_id, s.side)] = {
            "fair_p": s.fair_p, "fair_source": s.fair_source, "price_target": s.price_target,
            "fee_at_target": s.fee_at_target, "as_estimate": s.as_estimate, "edge": s.edge,
            "edge_min": s.edge_min, "stake": s.stake, "contracts": s.contracts,
            "decision": s.decision, "rejection_reason": s.rejection_reason, "labels": s.labels,
            "venue_best_bid": s.venue_best_bid, "venue_best_ask": s.venue_best_ask,
        }
    return out


def test_the_stage_order_produces_exactly_what_the_single_pass_produced(env_settings, db_session):
    """Fix 48's parity contract: with budget to spare, the six stages write the single pass's rows.

    Same game, same books, same quotes, same `now`, two runs: one through the reference single
    pass, one through the reordered pipeline. Every gap snapshot and every signal has to match,
    field for field, and so do the counts and the variant order.
    """
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    old_run = _seed_run(db_session, game, markets, NOW)
    # `pricing_order` rotates the secondary tail by `run_id % len(tail)` (Amendment 4), so two
    # runs only see the same order when their ids agree modulo the tail length. Filler runs make
    # them agree; comparing the two orders is the point of the assertion below.
    tail = len(active_variants(db_session)) - 1
    while True:
        new_run = _seed_run(db_session, game, markets, NOW)
        if new_run.id % tail == old_run.id % tail:
            break

    old = _single_pass(db_session, old_run.id, NOW, env_settings, budget_s=600)
    new = price_and_signal(db_session, new_run.id, NOW, env_settings, budget_s=600)

    assert new["budget_exhausted"] is False
    for key in ("fair_direct", "fair_derived", "no_sharp", "gaps", "order", "signals"):
        assert new[key] == old[key], key

    assert _gap_rows(db_session, new_run.id) == _gap_rows(db_session, old_run.id)
    assert _signal_rows(db_session, new_run.id) == _signal_rows(db_session, old_run.id)


def test_every_stage_records_what_it_cost(env_settings, db_session):
    """Rule 5: `notes->'pricing'->'stages'` is an ordered list of what each stage cost and
    whether it ran, so the next budget conversation has numbers rather than a guess."""
    game, markets = _seed(db_session)
    register_variants(db_session, load_variants(PROD_VARIANTS), NOW, prune=True)
    run = _seed_run(db_session, game, markets, NOW)

    result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=600)

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
