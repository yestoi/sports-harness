"""The quote we would have sent: the decline rules, the arithmetic, and both fee branches.

**Each fixture seeds** the `venue_markets` and `games` rows its legs resolve through and one
`fair_values` row per leg with `fair_source = 'direct'` and `created_at` inside `FAIR_MAX_AGE`,
then returns an object carrying `frame` -- the `rfq_created` frame `handle_frame` is given. What
distinguishes them: `two_game_rfq` two legs on two `KXNFLGAME` events and two games, fairs 0.60
and 0.50; `same_game_rfq` two legs whose `venue_markets.game_id` is the same;
`same_game_two_events_rfq` the same, across two different `event_ticker` values;
`derived_fair_rfq` one leg whose only `fair_values` row has `fair_source = 'derived'`;
`disagreeing_rfq` one leg whose `fair_values.disagreement` is above `DISAGREEMENT_MAX`;
`unmatched_leg_rfq` one leg whose `market_ticker` has no `venue_markets` row;
`one_leg_rfq` a single-leg `mve_selected_legs`; `big_rfq` a two-game combo whose
`target_cost_dollars` is above `rfq_collateral_cap_usd`.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from harness.venues.kalshi.rfq import handle_frame
from harness.venues.kalshi.rfq_quote import (DECLINE_COLLATERAL, DECLINE_DISAGREEMENT,
                                             DECLINE_NO_FAIR, DECLINE_SAME_GAME,
                                             DECLINE_SINGLE_LEG, DISAGREEMENT_MAX,
                                             nfl_only_independent, resolve_legs)

NOW = datetime(2026, 9, 15, 18, 0, tzinfo=timezone.utc)


def test_a_cross_game_combo_is_quoted(db_session, env_settings, two_game_rfq):
    handle_frame(db_session, two_game_rfq.frame, NOW)
    quote = db_session.execute(text(
        "select fair, yes_bid, no_bid, margin_per_leg, legs, declined_reason "
        "from rfq_quotes")).first()
    assert quote.declined_reason is None
    # fair = 0.60 * 0.50 = 0.30; margin 0.03 x 2 legs = 0.06
    assert quote.fair == Decimal("0.3000")
    assert quote.yes_bid == Decimal("0.2400")
    assert quote.no_bid == Decimal("0.6400")
    assert quote.margin_per_leg == Decimal("0.0300") and quote.legs == 2


def test_two_legs_from_one_game_are_declined(db_session, env_settings, same_game_rfq):
    """Ruling A-I2: the spec's rule is "two legs from one game". Reading it as "one event" would
    pass a spread and a total on the same game through as a cross-game combo."""
    handle_frame(db_session, same_game_rfq.frame, NOW)
    quote = db_session.execute(text(
        "select declined_reason, yes_bid, no_bid from rfq_quotes")).first()
    assert quote.declined_reason == DECLINE_SAME_GAME
    assert quote.yes_bid is None and quote.no_bid is None


def test_two_legs_on_one_game_across_two_events_are_still_declined(db_session, env_settings,
                                                                   same_game_two_events_rfq):
    handle_frame(db_session, same_game_two_events_rfq.frame, NOW)
    assert db_session.execute(text(
        "select declined_reason from rfq_quotes")).scalar() == DECLINE_SAME_GAME


def test_a_leg_with_no_direct_fair_declines_no_fair(db_session, env_settings, derived_fair_rfq):
    handle_frame(db_session, derived_fair_rfq.frame, NOW)
    assert db_session.execute(text(
        "select declined_reason from rfq_quotes")).scalar() == DECLINE_NO_FAIR


def test_a_non_football_leg_declines_no_fair_without_touching_fair_values(
        db_session, env_settings, non_football_leg_rfq, monkeypatch):
    """Fix 35: the incident's combos (journal 109) were almost entirely on non-football series
    (`KXMVECROSSCATEGORY-SHARD1-...`), and every leg of every one ran the `fair_values` lateral
    to find nothing. A leg whose `event_ticker` is not `KXNFL*`/`KXNCAAF*` can never have a
    fair, so the whole combo must decline `no_fair` from the cheap `venue_markets`-only check
    alone -- `resolve_legs` (the lateral into `fair_values`) must never run."""
    def _boom(*_a, **_k):
        raise AssertionError("resolve_legs ran for a combo with a non-football leg")

    monkeypatch.setattr("harness.venues.kalshi.rfq_quote.resolve_legs", _boom)
    handle_frame(db_session, non_football_leg_rfq.frame, NOW)
    row = db_session.execute(text("select declined_reason from rfq_quotes")).first()
    assert row is not None, ("resolve_legs raised (see above): compute_quote touched "
                             "fair_values for a leg the harness never prices")
    assert row.declined_reason == DECLINE_NO_FAIR


def test_same_game_declines_before_any_fair_read(db_session, env_settings, same_game_rfq,
                                                 monkeypatch):
    """Fix 35: `same_game` needs only `venue_markets`, so it must decline before `resolve_legs`
    ever runs -- not just before the `no_fair` check that used to follow it."""
    def _boom(*_a, **_k):
        raise AssertionError("resolve_legs ran for a same_game decline")

    monkeypatch.setattr("harness.venues.kalshi.rfq_quote.resolve_legs", _boom)
    handle_frame(db_session, same_game_rfq.frame, NOW)
    row = db_session.execute(text("select declined_reason from rfq_quotes")).first()
    assert row is not None, ("resolve_legs raised (see above): same_game must decline before "
                             "any fair read")
    assert row.declined_reason == DECLINE_SAME_GAME


def test_a_leg_over_the_disagreement_threshold_declines(db_session, env_settings,
                                                        disagreeing_rfq):
    handle_frame(db_session, disagreeing_rfq.frame, NOW)
    assert db_session.execute(text(
        "select declined_reason from rfq_quotes")).scalar() == DECLINE_DISAGREEMENT
    assert DISAGREEMENT_MAX > 0


def test_an_unresolvable_leg_falls_back_to_event_distinctness_and_is_counted(
        db_session, env_settings, unmatched_leg_rfq):
    """0.11: a leg that does not resolve falls back to `event_ticker` distinctness with
    `unmatched_legs` recorded, so the reader can see the quote rested on a weaker test."""
    handle_frame(db_session, unmatched_leg_rfq.frame, NOW)
    quote = db_session.execute(text(
        "select unmatched_legs, declined_reason from rfq_quotes")).first()
    assert quote.unmatched_legs == 1


def test_a_leg_with_two_priced_lines_uses_its_own_threshold(db_session, env_settings,
                                                            wrong_line_rfq):
    """Review C1: `venue_markets.threshold` is part of a leg's identity everywhere else in the
    harness (the `fair_values` unique key, `match_key`, the settlement/report joins). Without it
    in the lateral join, this game's more recently priced -7.5 line would win over the leg's own
    -3.5 line, and the quote would be built on the wrong number."""
    handle_frame(db_session, wrong_line_rfq.frame, NOW)
    quote = db_session.execute(text("select fair, declined_reason from rfq_quotes")).first()
    assert quote.declined_reason is None
    assert quote.fair == Decimal("0.2750")     # 0.55 (the leg's own line) * 0.50


def test_both_fee_branches_are_stored(db_session, env_settings, mixed_family_rfq):
    """F72 and ruling A-I2, and review M9: the fee is Kalshi's real maker fee per contract at our
    quoted price (`harness.pricing.fees.fee_per_contract`), not the quoting margin -- a different
    quantity, already folded into `spread`. `fee_branch_game` is the game-level independence
    answer the decline rule uses, `fee_branch_event` the event-level one F72 wrote, and the other
    branch's bids are stored so grading can be re-run either way.

    `mixed_family_rfq` fails F72's independence test under both readings (one leg is
    `KXNCAAFGAME`, not `KXNFL*`), so the real fee applies to both branches, and applies once per
    side at that side's own price -- never multiplied by leg count the way the margin is.

    By hand: fair = 0.60 x 0.50 = 0.3000; spread = 0.03 x 2 legs = 0.06; the pre-fee bids are
    0.2400 (yes) and 0.6400 (no).
    fee_yes = fee_per_contract(KALSHI_FOOTBALL, "maker", 0.2400, 1)
            = ceil_to_centicent(0.0175 x 0.2400 x 0.7600) = ceil_to_centicent(0.003192) = 0.0032
    fee_no  = fee_per_contract(KALSHI_FOOTBALL, "maker", 0.6400, 1)
            = ceil_to_centicent(0.0175 x 0.6400 x 0.3600) = ceil_to_centicent(0.004032) = 0.0041
    yes_bid = 0.2400 - 0.0032 = 0.2368; no_bid = 0.6400 - 0.0041 = 0.6359.
    Both branches fail independence identically here (one non-NFL leg fails either reading), so
    the other-branch bids match the branch taken.
    """
    handle_frame(db_session, mixed_family_rfq.frame, NOW)
    quote = db_session.execute(text(
        "select fee_branch_game, fee_branch_event, fee_subtracted, yes_bid, no_bid, "
        "yes_bid_other_branch, no_bid_other_branch from rfq_quotes")).first()
    assert quote.fee_branch_game is False and quote.fee_branch_event is False
    assert quote.fee_subtracted == Decimal("0.0032")
    assert quote.yes_bid == Decimal("0.2368") and quote.no_bid == Decimal("0.6359")
    assert quote.yes_bid_other_branch == Decimal("0.2368")
    assert quote.no_bid_other_branch == Decimal("0.6359")


@pytest.mark.parametrize("key", ["game_id", "event_ticker"])
def test_nfl_only_independent_needs_both_halves(key):
    from harness.venues.kalshi.rfq_quote import LegFair

    nfl = [LegFair("KXNFLGAME-A-DAL", "KXNFLGAME-A", 1, Decimal("0.6"), Decimal("0.001"), False),
           LegFair("KXNFLGAME-B-KC", "KXNFLGAME-B", 2, Decimal("0.5"), Decimal("0.001"), False)]
    assert nfl_only_independent(nfl, key) is True
    mixed = nfl + [LegFair("KXNCAAFGAME-C-LSU", "KXNCAAFGAME-C", 3, Decimal("0.7"),
                           Decimal("0.001"), False)]
    assert nfl_only_independent(mixed, key) is False
    repeated = [nfl[0], nfl[0]]
    assert nfl_only_independent(repeated, key) is False


def test_the_collateral_cap_compares_dollars_to_dollars(db_session, env_settings, big_rfq):
    """`rfqs.contracts_fp` is a contract quantity and `rfq_collateral_cap_usd` is dollars; the
    two have no common unit. The dollar figure the RFQ carries is `target_cost_dollars`, and
    `exposure_usd` falls back to `contracts_fp * fair` when the venue sent none."""
    handle_frame(db_session, big_rfq.frame, NOW)
    assert db_session.execute(text(
        "select declined_reason from rfq_quotes")).scalar() == DECLINE_COLLATERAL


def test_a_size_decline_is_never_filed_as_no_fair(db_session, env_settings, big_rfq):
    """H5's whole question is which RFQs we would and would not have answered and why, so t10's
    decline mix has to distinguish "we had no price" from "the size was over our cap"."""
    handle_frame(db_session, big_rfq.frame, NOW)
    assert db_session.execute(text(
        "select declined_reason from rfq_quotes")).scalar() != DECLINE_NO_FAIR


def test_a_single_leg_rfq_declines_single_leg(db_session, env_settings, one_leg_rfq):
    """A single-market RFQ is an arrival H5's denominator needs, but it is not a combo. It gets
    its own reason for the same reason a size decline does."""
    handle_frame(db_session, one_leg_rfq.frame, NOW)
    assert db_session.execute(text(
        "select declined_reason from rfq_quotes")).scalar() == DECLINE_SINGLE_LEG


def test_one_quote_per_rfq_even_on_a_repeated_frame(db_session, env_settings, two_game_rfq):
    handle_frame(db_session, two_game_rfq.frame, NOW)
    handle_frame(db_session, two_game_rfq.frame, NOW)
    assert db_session.execute(text("select count(*) from rfq_quotes")).scalar() == 1


def test_no_module_here_can_send_anything():
    """Conformance item 5 again, for the module that computes the number: it holds the quote and
    has no way to deliver it."""
    from pathlib import Path

    body = (Path(__file__).resolve().parents[1] / "harness" / "venues" / "kalshi"
            / "rfq_quote.py").read_text()
    assert ".send(" not in body and "POST" not in body.upper()


# --- fix 35 round 1 (review Important 1): the index must actually be chosen ------------------

def _index_names(node) -> set[str]:
    """Every `Index Name` anywhere in an `explain (format json)` plan tree."""
    names: set[str] = set()
    if isinstance(node, list):
        for item in node:
            names |= _index_names(item)
    elif isinstance(node, dict):
        if "Index Name" in node:
            names.add(node["Index Name"])
        if "Plan" in node:
            names |= _index_names(node["Plan"])
        if "Plans" in node:
            names |= _index_names(node["Plans"])
    return names


def test_ix_fair_leg_lookup_is_chosen_for_the_leg_query(db_session, env_settings):
    """The reviewer measured the first cut of this index unused: `_LEG`'s `is not distinct from`
    predicates are not indexable, so the planner walked `ix_fair_game_type_created` and filtered
    every one of ~300 same-(game_id, market_type) candidates by hand to find the one matching
    row. The `coalesce(...) = coalesce(...)` rewrite must fix that, not just leave the index
    present and still unused -- so this seeds the same shape (many rows sharing (game_id,
    market_type), one matching the queried leg) and reads the actual plan.

    `enable_seqscan` is off for the query so a sequential scan cannot stand in for either index
    winning on its own merits; the row count is what makes `ix_fair_leg_lookup` the cheaper of
    the two indexes rather than merely the only usable one."""
    from harness.db.models import FairValue
    from harness.venues.kalshi.rfq_quote import _LEG

    game_id = 900_001
    base = NOW - timedelta(hours=1)
    for i in range(300):
        db_session.add(FairValue(
            run_id=900_000 + i, game_id=game_id, market_type="moneyline",
            outcome_team_id=i % 50, outcome_side="home" if i % 2 == 0 else "away",
            threshold=None, fair_p=Decimal("0.5000"), fair_source="direct",
            disagreement=Decimal("0.0010"), created_at=base + timedelta(seconds=i)))
    # The leg's own row: the one combo the query below actually asks for, newest of all.
    db_session.add(FairValue(
        run_id=999_999, game_id=game_id, market_type="moneyline", outcome_team_id=7,
        outcome_side="home", threshold=None, fair_p=Decimal("0.6100"), fair_source="direct",
        disagreement=Decimal("0.0010"), created_at=base + timedelta(seconds=301)))
    db_session.flush()
    db_session.execute(text("analyze fair_values"))
    db_session.execute(text("set local enable_seqscan = off"))

    compiled = _LEG.bindparams(
        game_id=game_id, market_type="moneyline", side_team_id=7, side="home", threshold=None,
        as_of=NOW,
    ).compile(dialect=db_session.get_bind().dialect, compile_kwargs={"literal_binds": True})
    plan = db_session.execute(text(f"explain (format json) {compiled}")).scalar()
    names = _index_names(plan)
    assert "ix_fair_leg_lookup" in names, plan
