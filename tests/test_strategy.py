from dataclasses import replace
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

import pytest

from harness.pricing.fees import KALSHI_FOOTBALL, fee_per_contract
from harness.strategy.run import LABEL_ORDER, GapRow, StrategyState, run_strategy
from harness.strategy.variants import load_variants

NOW = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)
SHIPPED = Path(__file__).parent.parent / "harness" / "variants"
CENT = Decimal("0.01")
FOUR = Decimal("0.0001")


def variant(name: str):
    return next(v for v in load_variants(SHIPPED) if v.name == name)


def maker_fee(p: Decimal) -> Decimal:
    return fee_per_contract(KALSHI_FOOTBALL, "maker", p, 100)


def expected_pricing(fair: Decimal, disagreement: Decimal, cfg: dict) -> dict:
    """Re-derive the spec formulas independently of the implementation."""
    floor_ = Decimal(str(cfg["edge_floor"]))
    ceiling = Decimal(str(cfg["edge_ceiling"]))
    raw = floor_ + Decimal(str(cfg["disagreement_mult"])) * disagreement
    edge_min = min(max(raw, floor_), ceiling)
    as_ = Decimal(str(cfg["as_seed"]))
    p0 = fair - edge_min - as_
    price_target = max((p0 - maker_fee(p0)).quantize(CENT, rounding=ROUND_FLOOR), CENT)
    fee = maker_fee(price_target)
    edge = fair - price_target - fee
    p_cond = fair - as_
    cost = price_target + fee
    f_star = (p_cond - cost) / (Decimal(1) - cost)
    bankroll = Decimal(str(cfg["bankroll"]))
    stake = Decimal(0)
    if f_star > 0:
        stake = max(Decimal(str(cfg["floor_stake"])), Decimal(str(cfg["kelly_fraction"])) * f_star * bankroll)
        stake = min(stake, Decimal(str(cfg["per_bet_cap"])) * bankroll)
    return {
        "edge_min": edge_min.quantize(FOUR),
        "price_target": price_target.quantize(FOUR),
        "fee_at_target": fee.quantize(FOUR),
        "edge": edge.quantize(FOUR),
        "stake": stake,
        "contracts": int(stake / price_target) if stake > 0 else 0,
    }


def gap_row(**over) -> GapRow:
    base = dict(
        gap_snapshot_id=1,
        venue_market_id=101,
        sport="nfl",
        game_id=7,
        market_type="moneyline",
        side_team_id=42,
        threshold=None,
        fair_p=Decimal("0.5500"),
        fair_source="direct",
        disagreement=Decimal("0.0100"),
        n_groups=2,
        staleness_s=12,
        prev_fair_p=None,
        prev_fair_ts=None,
        best_bid=Decimal("0.5000"),
        best_ask=Decimal("0.5200"),
        bid_size=400,
        ask_size=400,
        ttk_minutes=120,
        volume_24h=500,
        open_interest=900,
        venue_mid=Decimal("0.5100"),
    )
    base.update(over)
    return GapRow(**base)


# --- (a) the clean candidate ------------------------------------------------

def test_clean_row_is_a_candidate_with_the_spec_numbers():
    v = variant("sharp_direct")
    (sig,) = run_strategy([gap_row()], v, NOW)

    want = expected_pricing(Decimal("0.5500"), Decimal("0.0100"), v.config)
    assert sig.decision == "candidate"
    assert sig.rejection_reason is None
    assert all(sig.labels.values()), sig.labels
    assert list(sig.labels) == LABEL_ORDER
    assert sig.edge_min == want["edge_min"]
    assert sig.price_target == want["price_target"]
    assert sig.fee_at_target == want["fee_at_target"]
    assert sig.edge == want["edge"]
    assert sig.contracts == want["contracts"]
    assert sig.stake == want["stake"].quantize(CENT)
    assert sig.stake <= Decimal("90.00")
    assert sig.as_estimate == Decimal("0.0100")
    assert sig.side == "yes"
    assert sig.venue_market_id == 101
    assert sig.gap_snapshot_id == 1
    assert sig.venue_best_bid == Decimal("0.5000")
    assert sig.venue_best_ask == Decimal("0.5200")
    assert sig.fair_source == "direct"


def test_edge_min_uses_the_disagreement_multiplier_and_the_ceiling():
    v = variant("sharp_direct")
    (low,) = run_strategy([gap_row(disagreement=Decimal("0.0000"))], v, NOW)
    (high,) = run_strategy([gap_row(disagreement=Decimal("0.5000"))], v, NOW)
    assert low.edge_min == Decimal("0.0200")
    assert high.edge_min == Decimal("0.0600")


def test_price_target_must_undercut_the_best_ask():
    v = variant("sharp_direct")
    (sig,) = run_strategy([gap_row(best_ask=Decimal("0.4900"), venue_mid=Decimal("0.4850"))], v, NOW)
    assert sig.labels["edge"] is False
    assert sig.decision == "rejected"
    assert sig.rejection_reason == "edge"


# --- (b) sources ------------------------------------------------------------

def test_derived_source_is_rejected_by_the_primary_and_taken_by_the_secondary():
    row = gap_row(fair_source="derived")
    (rejected,) = run_strategy([row], variant("sharp_direct"), NOW)
    assert rejected.decision == "rejected"
    assert rejected.rejection_reason == "source_allowed"
    assert rejected.labels["source_allowed"] is False

    (accepted,) = run_strategy([row], variant("sharp_plus_derived"), NOW)
    assert accepted.decision == "candidate"
    assert accepted.labels["source_allowed"] is True


# --- (c) staleness ----------------------------------------------------------

def test_stale_fair_is_rejected():
    (sig,) = run_strategy([gap_row(staleness_s=600)], variant("sharp_direct"), NOW)
    assert sig.labels["not_stale"] is False
    assert sig.rejection_reason == "not_stale"
    assert sig.decision == "rejected"


def test_missing_staleness_is_rejected():
    (sig,) = run_strategy([gap_row(staleness_s=None)], variant("sharp_direct"), NOW)
    assert sig.labels["not_stale"] is False


# --- (d) price band ---------------------------------------------------------

def test_price_band_rejects_a_cheap_contract_that_the_wide_band_variant_takes():
    row = gap_row(
        fair_p=Decimal("0.2000"), venue_mid=Decimal("0.1800"),
        best_bid=Decimal("0.1700"), best_ask=Decimal("0.1900"),
    )
    (narrow,) = run_strategy([row], variant("sharp_direct"), NOW)
    assert narrow.labels["price_band"] is False
    assert narrow.rejection_reason == "price_band"

    (wide,) = run_strategy([row], variant("wide_band"), NOW)
    assert wide.labels["price_band"] is True


def test_price_band_falls_back_to_the_best_ask_when_the_mid_is_missing():
    (sig,) = run_strategy([gap_row(venue_mid=None)], variant("sharp_direct"), NOW)
    assert sig.labels["price_band"] is True


# --- other labels -----------------------------------------------------------

def test_sport_scope_ttk_spread_and_volume_labels():
    v = variant("sharp_direct")
    (ncaaf,) = run_strategy([gap_row(sport="ncaaf")], v, NOW)
    assert ncaaf.labels["sport_allowed"] is True
    (nfl_only,) = run_strategy([gap_row(sport="ncaaf")], variant("nfl_only"), NOW)
    assert nfl_only.labels["sport_allowed"] is False
    assert nfl_only.rejection_reason == "sport_allowed"

    (soon,) = run_strategy([gap_row(ttk_minutes=5)], v, NOW)
    assert soon.labels["ttk"] is False and soon.rejection_reason == "ttk"

    (wide,) = run_strategy([gap_row(best_bid=Decimal("0.4000"), best_ask=Decimal("0.5200"))], v, NOW)
    assert wide.labels["spread"] is False and wide.rejection_reason == "spread"
    # 12c is inside the NCAAF allowance of 20c
    (wide_ncaaf,) = run_strategy(
        [gap_row(sport="ncaaf", best_bid=Decimal("0.4000"), best_ask=Decimal("0.5200"))], v, NOW)
    assert wide_ncaaf.labels["spread"] is True

    (thin,) = run_strategy([gap_row(volume_24h=10)], v, NOW)
    assert thin.labels["volume"] is False and thin.rejection_reason == "volume"

    # a Pinnacle-only fair (n_groups=1) has nothing to measure disagreement against, so it is
    # not "perfect agreement" -- it fails disagreement_ok regardless of the disagreement value.
    (no_disagreement,) = run_strategy([gap_row(disagreement=None, n_groups=1)], v, NOW)
    assert no_disagreement.labels["disagreement_ok"] is False
    assert no_disagreement.rejection_reason == "disagreement_ok"


# --- (f) velocity -----------------------------------------------------------

def test_velocity_rejects_a_three_point_jump_unless_the_variant_disables_it():
    row = gap_row(prev_fair_p=Decimal("0.5200"), prev_fair_ts=NOW)
    (strict,) = run_strategy([row], variant("sharp_direct"), NOW)
    assert strict.labels["velocity"] is False
    assert strict.rejection_reason == "velocity"

    (loose,) = run_strategy([row], variant("no_velocity"), NOW)
    assert loose.labels["velocity"] is True
    assert loose.decision == "candidate"


def test_a_small_move_passes_the_velocity_filter():
    (sig,) = run_strategy([gap_row(prev_fair_p=Decimal("0.5450"), prev_fair_ts=NOW)],
                          variant("sharp_direct"), NOW)
    assert sig.labels["velocity"] is True


# --- (g) degenerate rows ----------------------------------------------------

def test_every_row_yields_exactly_one_signal_even_without_a_fair():
    rows = [
        gap_row(venue_market_id=1, fair_p=None, fair_source=None),
        gap_row(venue_market_id=2),
        gap_row(venue_market_id=3, best_bid=None, best_ask=None, venue_mid=None),
        gap_row(venue_market_id=4, ttk_minutes=None, volume_24h=None),
    ]
    signals = run_strategy(rows, variant("sharp_direct"), NOW)
    assert [s.venue_market_id for s in signals] == [1, 2, 3, 4]
    no_fair = signals[0]
    assert no_fair.labels["has_fair"] is False
    assert no_fair.rejection_reason == "has_fair"
    assert no_fair.decision == "rejected"
    assert no_fair.price_target is None and no_fair.stake is None and no_fair.contracts is None
    assert set(no_fair.labels) == set(LABEL_ORDER)
    assert signals[2].labels["spread"] is False
    assert signals[3].labels["ttk"] is False


def test_run_strategy_never_mutates_the_input_order():
    rows = [gap_row(venue_market_id=i, fair_p=Decimal("0.50") + Decimal(i) / 100) for i in range(1, 6)]
    signals = run_strategy(rows, variant("sharp_direct"), NOW)
    assert [s.venue_market_id for s in signals] == [1, 2, 3, 4, 5]


# --- (e) caps ---------------------------------------------------------------

def test_caps_are_labels_only_for_the_primary_variant():
    v = variant("sharp_direct")
    rows = [gap_row(venue_market_id=200 + i, gap_snapshot_id=200 + i) for i in range(4)]
    signals = run_strategy(rows, v, NOW)
    assert all(s.decision == "candidate" for s in signals)
    # four identical same-side rows on one game: only the first can hold the position
    assert [s.labels["cap_per_game"] for s in signals] == [True, False, False, False]


def test_constrained_variant_keeps_only_the_higher_edge_same_side_position():
    v = variant("constrained")
    assert v.config["apply_caps"] is True
    ml = gap_row(venue_market_id=1, gap_snapshot_id=1, market_type="moneyline",
                 fair_p=Decimal("0.5500"))
    spread_hi = gap_row(venue_market_id=2, gap_snapshot_id=2, market_type="spread",
                        threshold=Decimal("2.5"), fair_p=Decimal("0.5990"),
                        best_bid=Decimal("0.5000"), best_ask=Decimal("0.5600"),
                        venue_mid=Decimal("0.5300"))
    spread_lo = gap_row(venue_market_id=3, gap_snapshot_id=3, market_type="spread",
                        threshold=Decimal("6.5"), fair_p=Decimal("0.5400"))
    signals = {s.venue_market_id: s for s in run_strategy([ml, spread_hi, spread_lo], v, NOW)}

    assert signals[2].decision == "candidate"
    assert signals[2].labels["cap_per_game"] is True
    for other in (1, 3):
        assert signals[other].decision == "rejected"
        assert signals[other].labels["cap_per_game"] is False
        assert signals[other].rejection_reason == "cap_per_game"


def test_totals_are_a_separate_position_from_the_side_positions():
    v = variant("constrained")
    ml = gap_row(venue_market_id=1, gap_snapshot_id=1)
    total = gap_row(venue_market_id=2, gap_snapshot_id=2, market_type="total",
                    side_team_id=None, threshold=Decimal("44.5"))
    signals = {s.venue_market_id: s for s in run_strategy([ml, total], v, NOW)}
    assert signals[1].decision == "candidate"
    assert signals[2].decision == "candidate"


def test_the_daily_cap_and_open_order_cap_stop_further_candidates():
    v = variant("constrained")
    bankroll = Decimal(str(v.config["bankroll"]))
    state = StrategyState(daily_exposure=Decimal(str(v.config["daily_cap"])) * bankroll)
    (sig,) = run_strategy([gap_row()], v, NOW, state=state)
    assert sig.labels["cap_daily"] is False
    assert sig.rejection_reason == "cap_daily"
    assert sig.decision == "rejected"

    full = StrategyState(open_orders=v.config["max_open"])
    (sig2,) = run_strategy([gap_row()], v, NOW, state=full)
    assert sig2.labels["max_open"] is False
    assert sig2.rejection_reason == "max_open"


def test_state_accumulates_only_for_candidates():
    v = variant("sharp_direct")
    state = StrategyState()
    rows = [gap_row(venue_market_id=1), gap_row(venue_market_id=2, game_id=8, side_team_id=43),
            gap_row(venue_market_id=3, staleness_s=999)]
    signals = run_strategy(rows, v, NOW, state=state)
    taken = [s for s in signals if s.decision == "candidate"]
    assert len(taken) == 2
    assert state.open_orders == 2
    assert state.daily_exposure == sum(s.stake for s in taken)
    assert set(state.game_exposure) == {7, 8}
    assert set(state.positions) == {(7, 42), (8, 43)}


def test_caps_admit_the_highest_edge_row_first():
    v = variant("constrained")
    small = gap_row(venue_market_id=1, gap_snapshot_id=1, game_id=1, side_team_id=1,
                    fair_p=Decimal("0.5400"))
    big = gap_row(venue_market_id=2, gap_snapshot_id=2, game_id=2, side_team_id=2,
                  fair_p=Decimal("0.6500"), best_bid=Decimal("0.5800"),
                  best_ask=Decimal("0.6400"), venue_mid=Decimal("0.6100"))
    bankroll = Decimal(str(v.config["bankroll"]))
    # only room for one more bet today
    state = StrategyState(daily_exposure=Decimal(str(v.config["daily_cap"])) * bankroll - Decimal("70"))
    signals = {s.venue_market_id: s for s in run_strategy([small, big], v, NOW, state=state)}
    assert signals[2].decision == "candidate"
    assert signals[1].decision == "rejected"
    assert signals[1].rejection_reason == "cap_daily"


def test_state_is_untouched_when_none_is_passed():
    v = variant("sharp_direct")
    a = run_strategy([gap_row()], v, NOW)
    b = run_strategy([gap_row()], v, NOW)
    assert a[0].labels == b[0].labels


# --- fee model injection ----------------------------------------------------

def test_a_zero_fee_model_raises_the_price_target():
    from harness.pricing.fees import FeeModel

    v = variant("sharp_direct")
    free = FeeModel(Decimal("0"), Decimal("0"), 1)
    row = gap_row(fair_p=Decimal("0.5460"))
    (with_fee,) = run_strategy([row], v, NOW)
    (no_fee,) = run_strategy([row], v, NOW, fee_model=free)
    assert no_fee.fee_at_target == Decimal("0.0000")
    assert no_fee.price_target > with_fee.price_target


@pytest.mark.parametrize("bad", [
    dict(fair_p=Decimal("0.0000")),
    dict(fair_p=Decimal("1.0000")),
    dict(best_bid=Decimal("0.9900"), best_ask=Decimal("0.9800")),
    dict(venue_mid=Decimal("0.0000")),
])
def test_degenerate_inputs_do_not_raise(bad):
    (sig,) = run_strategy([gap_row(**bad)], variant("sharp_direct"), NOW)
    assert sig.decision in ("candidate", "rejected")
    assert set(sig.labels) == set(LABEL_ORDER)


def test_gap_row_is_a_plain_dataclass():
    row = gap_row()
    assert replace(row, venue_market_id=9).venue_market_id == 9


# --- review round 1 ---------------------------------------------------------

def test_label_order_has_seventeen_labels_with_match_confidence_after_the_sport():
    assert len(LABEL_ORDER) == 17
    assert LABEL_ORDER[LABEL_ORDER.index("sport_allowed") + 1] == "match_confidence"


def test_a_fuzzy_match_is_rejected_but_a_manual_one_is_taken():
    (fuzzy,) = run_strategy([gap_row(match_status="fuzzy")], variant("sharp_direct"), NOW)
    assert fuzzy.labels["match_confidence"] is False
    assert fuzzy.rejection_reason == "match_confidence"
    assert fuzzy.decision == "rejected"

    (manual,) = run_strategy([gap_row(match_status="manual")], variant("sharp_direct"), NOW)
    assert manual.labels["match_confidence"] is True
    assert manual.decision == "candidate"


def test_the_ttk_and_velocity_thresholds_are_strict():
    v = variant("sharp_direct")
    # spec 6.4 reads "kickoff > 20 min" and "|d fair| < 2 pts", so the boundary fails
    (exact_ttk,) = run_strategy([gap_row(ttk_minutes=v.config["min_ttk_min"])], v, NOW)
    assert exact_ttk.labels["ttk"] is False
    assert exact_ttk.rejection_reason == "ttk"
    (over_ttk,) = run_strategy([gap_row(ttk_minutes=v.config["min_ttk_min"] + 1)], v, NOW)
    assert over_ttk.labels["ttk"] is True

    exact_move = Decimal("0.5500") - Decimal(str(v.config["velocity_max_pts"]))
    (exact_velocity,) = run_strategy([gap_row(prev_fair_p=exact_move, prev_fair_ts=NOW)], v, NOW)
    assert exact_velocity.labels["velocity"] is False
    assert exact_velocity.rejection_reason == "velocity"


def test_a_non_positive_kelly_fraction_sizes_to_nothing():
    row = gap_row(fair_p=Decimal("0.0200"), venue_mid=Decimal("0.2100"),
                  best_bid=Decimal("0.2000"), best_ask=Decimal("0.2200"))
    (sig,) = run_strategy([row], variant("sharp_direct"), NOW)
    assert sig.stake == Decimal("0.00")
    assert sig.contracts == 0
    assert sig.labels["min_contracts"] is False
    assert sig.decision == "rejected"


def test_the_per_bet_cap_clamps_a_large_kelly_stake():
    v = variant("sharp_direct")
    row = gap_row(fair_p=Decimal("0.7900"), venue_mid=Decimal("0.7900"),
                  best_bid=Decimal("0.7800"), best_ask=Decimal("0.8000"))
    (sig,) = run_strategy([row], v, NOW)
    cap = Decimal(str(v.config["per_bet_cap"])) * Decimal(str(v.config["bankroll"]))
    uncapped = expected_pricing(Decimal("0.7900"), Decimal("0.0100"), dict(v.config, per_bet_cap=1))
    assert uncapped["stake"] > cap
    # the recorded stake is still clamped to the cap...
    assert sig.stake == cap.quantize(CENT)
    # ...but the label is honest about the clamp having bitten: it is measured against the
    # uncapped Kelly stake, not the (already-clamped) recorded stake, so it can be False.
    assert sig.labels["cap_per_bet"] is False
    # sharp_direct has apply_caps: false, so the False cap label is recorded but not enforced.
    assert sig.decision == "candidate"


def test_the_per_bet_cap_rejects_when_the_variant_enforces_it():
    v = variant("constrained")
    assert v.config["apply_caps"] is True
    row = gap_row(fair_p=Decimal("0.7900"), venue_mid=Decimal("0.7900"),
                  best_bid=Decimal("0.7800"), best_ask=Decimal("0.8000"))
    (sig,) = run_strategy([row], v, NOW)
    cap = Decimal(str(v.config["per_bet_cap"])) * Decimal(str(v.config["bankroll"]))
    assert sig.stake == cap.quantize(CENT)
    assert sig.labels["cap_per_bet"] is False
    assert sig.decision == "rejected"
    assert sig.rejection_reason == "cap_per_bet"


def test_a_single_group_fair_uses_the_edge_ceiling_not_the_floor():
    """A Pinnacle-only fair (n_groups < 2) must price against the strictest edge threshold,
    not the loosest one that a measured (near-zero) disagreement would otherwise imply."""
    v = variant("sharp_direct")
    (sig,) = run_strategy([gap_row(n_groups=1, disagreement=None)], v, NOW)
    assert sig.edge_min == Decimal(str(v.config["edge_ceiling"]))
    assert sig.labels["disagreement_ok"] is False
    assert sig.rejection_reason == "disagreement_ok"

    (zero_groups,) = run_strategy([gap_row(n_groups=0, disagreement=None)], v, NOW)
    assert zero_groups.edge_min == Decimal(str(v.config["edge_ceiling"]))
    assert zero_groups.labels["disagreement_ok"] is False


def test_the_same_side_position_keeps_the_highest_edge_seen():
    state = StrategyState()
    high = gap_row(venue_market_id=1, gap_snapshot_id=1, fair_p=Decimal("0.5990"),
                   best_bid=Decimal("0.5000"), best_ask=Decimal("0.5600"),
                   venue_mid=Decimal("0.5300"))
    low = gap_row(venue_market_id=2, gap_snapshot_id=2, fair_p=Decimal("0.5500"))
    signals = run_strategy([high, low], variant("sharp_direct"), NOW, state=state)
    # apply_caps is off, so both are candidates and both write to the position
    assert all(s.decision == "candidate" for s in signals)
    assert state.positions[(7, 42)] == Decimal("0.0546")

    later = StrategyState(positions={(7, 42): Decimal("0.0546")})
    middling = gap_row(venue_market_id=3, gap_snapshot_id=3, fair_p=Decimal("0.5550"))
    (sig,) = run_strategy([middling], variant("constrained"), NOW, state=later)
    assert sig.edge == Decimal("0.0506")
    assert sig.labels["cap_per_game"] is False
    assert sig.rejection_reason == "cap_per_game"
