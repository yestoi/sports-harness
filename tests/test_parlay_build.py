"""The card builder: the anchor, the pool, the freshness rule, and the two card shapes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from harness.parlay.build import NoAnchorPriced, build_card, resolve_iso_week
from harness.parlay.pricing import american, decimal_from, newest_dk_price

NOW = datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)


def test_american_odds_round_trip():
    assert american(Decimal("2.50")) == 150
    assert american(Decimal("1.50")) == -200
    assert american(Decimal("2.00")) == 100


def test_resolve_iso_week_uses_chicago_not_utc():
    """Fix round 2, I4: a card built Sunday evening CT, where UTC has already rolled to Monday,
    must still resolve to the Chicago week -- the same week `mark_placed`, `show_cards` and
    `parlay_grade._settle_card` key the cap and the ledger to."""
    # Sunday 2026-09-13 20:00 CT (CDT, UTC-5) is Monday 2026-09-14 01:00 UTC. Raw UTC
    # isocalendar() gives (2026, 38); the Chicago date gives (2026, 37), which is correct.
    now = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
    assert now.isocalendar()[:2] == (2026, 38)
    assert resolve_iso_week(now, None) == (2026, 37)


def test_resolve_iso_week_honors_an_explicit_week_but_not_an_explicit_year():
    """An operator's `--week` override picks the week; the year still follows Chicago, since a
    card is never built for a week outside the one it is paid out of."""
    now = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
    assert resolve_iso_week(now, 40) == (2026, 40)


def test_decimal_from_converts_whatever_the_driver_returns():
    """The brief's Produces list declares `decimal_from`; `newest_dk_price` calls it rather than
    inlining `Decimal(str(...))` (review round 1, Important 3)."""
    assert decimal_from(Decimal("1.909")) == Decimal("1.909")
    assert decimal_from("1.909") == Decimal("1.909")
    assert decimal_from(1.5) == Decimal("1.5")


def test_a_price_older_than_thirty_minutes_is_not_a_price(db_session, seeded_dk_prices):
    """D14: the newest DraftKings row for the leg, no older than 30 minutes. A stale feed makes
    the payout arithmetic fiction."""
    fresh = newest_dk_price(db_session, seeded_dk_prices.game_id, "moneyline",
                            seeded_dk_prices.team_id, None, NOW, timedelta(minutes=30))
    assert fresh is not None
    stale = newest_dk_price(db_session, seeded_dk_prices.game_id, "moneyline",
                            seeded_dk_prices.team_id, None, NOW + timedelta(minutes=45),
                            timedelta(minutes=30))
    assert stale is None


def test_only_draftkings_prices_a_leg(db_session, seeded_pinnacle_only):
    assert newest_dk_price(db_session, seeded_pinnacle_only.game_id, "moneyline",
                           seeded_pinnacle_only.team_id, None, NOW,
                           timedelta(minutes=30)) is None


def test_a_smart_card_has_three_or_four_legs_from_different_games(db_session, env_settings,
                                                                  seeded_pool):
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)
    legs = _legs(db_session, card.id)
    assert 3 <= len(legs) <= 4
    assert len({leg.game_id for leg in legs}) == len(legs)
    assert card.stake == Decimal("25.00") and card.status == "proposed"
    assert card.correlated is False


def test_the_card_s_year_follows_chicago_not_a_passed_explicit_one(db_session, env_settings,
                                                                   seeded_pool):
    """Fix round 2, I4: `build_card` stores whichever `year` it is given rather than deriving
    `now.year` itself, which is what let the two disagree near a year boundary or in the
    Sunday-evening-CT window where UTC has already rolled over."""
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW,
                      year=2031)
    assert card.year == 2031


def test_the_smart_card_is_anchored_on_lsu_or_the_saints(db_session, env_settings, seeded_pool):
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)
    anchor = db_session.get(type(_legs(db_session, card.id)[0]), card.anchor_leg_id)
    assert anchor is not None and anchor.card_id == card.id
    assert anchor.market_type in ("ml", "spread", "total")


def test_a_total_leg_qualifies_as_the_anchor_via_the_games_home_and_away_teams(
        db_session, env_settings, seeded_total_anchor_pool):
    """Design 1.3/D14: the anchor is an LSU or Saints moneyline, spread **or total**. A total
    row's `side_team_id` is always NULL, so eligibility has to come from the game's own home and
    away teams, not the venue market's side (review round 1, Important 1)."""
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)
    anchor = db_session.get(type(_legs(db_session, card.id)[0]), card.anchor_leg_id)
    assert anchor is not None and anchor.market_type == "total"


def test_an_anchor_with_no_priced_row_ends_the_build(db_session, env_settings, seeded_pool_no_anchor):
    """D14, and the CLI turns this into exit 2 with `no anchor priced`."""
    with pytest.raises(NoAnchorPriced):
        build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)


def test_every_leg_records_the_snapshot_it_was_priced_from(db_session, env_settings,
                                                           seeded_pool):
    """Ruling A-M6: each leg's `fetched_at` is printed on the card, which needs the row id."""
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)
    assert all(leg.odds_snapshot_id is not None for leg in _legs(db_session, card.id))


def test_the_payout_is_the_product_of_the_decimal_odds(db_session, env_settings, seeded_pool):
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)
    product = Decimal("1")
    for leg in _legs(db_session, card.id):
        product *= leg.dk_decimal
    assert card.dk_payout_est == (product * card.stake).quantize(Decimal("0.01"))


def test_the_true_probability_is_the_product_of_the_sharp_fairs(db_session, env_settings,
                                                                seeded_pool):
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)
    assert 0 < card.true_prob_est < 1
    assert card.hold_est is not None


def test_a_lottery_card_takes_six_to_eight_legs_and_is_labelled(db_session, env_settings,
                                                                seeded_big_pool):
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="lottery", now=NOW)
    legs = _legs(db_session, card.id)
    assert 6 <= len(legs) <= 8
    assert card.stake == Decimal("5.00")


def test_a_lottery_card_may_carry_two_legs_from_one_game_and_says_so(db_session, env_settings,
                                                                     seeded_correlated_pool):
    """Spec §8.1: correlated legs allowed and labelled. DraftKings will quote lower than the
    independence product, and the card has to say so rather than imply a number it will not get."""
    card = build_card(db_session, env_settings, sport="ncaaf", week=38, kind="lottery", now=NOW)
    legs = _legs(db_session, card.id)
    assert len({leg.game_id for leg in legs}) < len(legs)
    assert card.correlated is True


def test_the_pool_is_the_last_six_hours_of_direct_fair_signals(db_session, env_settings,
                                                              seeded_stale_pool):
    """A seven-hour-old candidate is not a candidate: the pool window is six hours."""
    with pytest.raises(NoAnchorPriced):
        build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)


def test_no_odds_credit_is_spent(db_session, env_settings, seeded_pool, monkeypatch):
    """0.5: props are not fetched at build time in this phase. The builder reads
    `odds_snapshots` and makes no request at all."""
    import harness.feeds.http as http_module

    monkeypatch.setattr(http_module.HttpClient, "get",
                        lambda *a, **k: pytest.fail("the parlay builder made a request"))
    build_card(db_session, env_settings, sport="ncaaf", week=38, kind="smart", now=NOW)


def _legs(session, card_id):
    from harness.db.models import ParlayLeg

    return session.query(ParlayLeg).filter_by(card_id=card_id).order_by(ParlayLeg.seq).all()
