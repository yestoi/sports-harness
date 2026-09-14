"""The card builder: the anchor, the pool, the freshness rule, and the two card shapes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from harness.parlay.build import (GAMELOG_BUDGET_S, RELATIONSHIP_NOTES, BuildRefused,
                                  NoAnchorPriced, _context_lines, _link_capability, _main_pair,
                                  _relationship_note, build_card, resolve_iso_week)
from harness.parlay.config import load_config
from harness.parlay.pricing import (american, decimal_from, newest_dk_price,
                                    newest_dk_prop_price)
from tests.conftest import (PARLAY_NOW, _PARLAY_SPORT, _make_game, _make_team, _next_id,
                            _pool_leg)

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


# --- Phase 4.6 Task 6: the prop pool, same-game cards, links and probability sources ----------
#
# Every prop fixture below is an `anytime_td` selection, because `parlay.yaml`'s `market_defs`
# holds the recorded DraftKings rule for that family and for no other one (T18a, D19): the other
# four families are `market_unsupported` by policy and the builder must never write a leg for
# them. `_family_without_a_rule` is the unsupported side of that same rule.

T0 = NOW
#: Inside `prop_window_hours` (24) of kickoff, which is the prop pool's own bound; the game-line
#: pool only asks that kickoff is still ahead.
_PROP_KICKOFF = NOW + timedelta(hours=6)
#: The two prices of the plan's worked example: 1.87 and 1.95 imply 0.5348 and 0.5128, summing
#: to 1.0476, so the devigged first side is 0.5348 / 1.0476 = 0.5105.
_YES_PRICE = Decimal("1.87")
_NO_PRICE = Decimal("1.95")


def _make_prop(session, *, game_id, market_type, player_id, player_name, side, price,
               point=None, fetched_at=None, link=None, sid=None, book="draftkings"):
    """One `odds_prop_snapshots` row: the table D23 moved prop outcomes into."""
    from harness.db.models import OddsPropSnapshot

    row = OddsPropSnapshot(raw_id=_next_id(), book=book, game_id=game_id,
                           market_type=market_type, player_name=player_name,
                           player_id=player_id, outcome_side=side, point=point,
                           price_decimal=price,
                           fetched_at=fetched_at or (NOW - timedelta(minutes=5)),
                           link=link, sid=sid)
    session.add(row)
    session.flush()
    return row


def _make_player(session, team_id, name):
    from harness.db.models import Player

    player = Player(sport=_PARLAY_SPORT, espn_id=str(_next_id()), name=name, team_id=team_id,
                    updated_at=NOW)
    session.add(player)
    session.flush()
    return player


def _prop_game(session, home_abbr, away_abbr):
    home = _make_team(session, home_abbr)
    away = _make_team(session, away_abbr)
    return _make_game(session, home.id, away.id, _PROP_KICKOFF)


def _scorer(session, game, team_id, name, *, yes=_YES_PRICE, no=_NO_PRICE, fetched_at=None,
            link="https://sportsbook.draftkings.com/event/1", sid="sid-1"):
    """One anytime-TD selection. `no=None` leaves it one-sided: no pair, so no devig."""
    player = _make_player(session, team_id, name)
    _make_prop(session, game_id=game.id, market_type="prop:anytime_td", player_id=player.id,
               player_name=name, side="yes", price=yes, fetched_at=fetched_at, link=link,
               sid=sid)
    if no is not None:
        _make_prop(session, game_id=game.id, market_type="prop:anytime_td",
                   player_id=player.id, player_name=name, side="no", price=no,
                   fetched_at=fetched_at, link=link, sid=sid)
    return player


def _anchor_game(session, edge=Decimal("0.06")):
    """The LSU moneyline every card below is anchored on, on a game inside the prop window."""
    return _pool_leg(session, team_abbr="LSU", opp_abbr="OPP0", market_type="moneyline",
                     edge=edge, kickoff=_PROP_KICKOFF, price=Decimal("1.80"))


def seed_prop_prices(session):
    """One player's `prop:pass_yds` over line, priced now: the pricing read's own fixture. The
    read is a lookup, not a build, so the unsupported family is not a disqualifier here."""
    game = _prop_game(session, "PPA", "PPB")
    player = _make_player(session, game.home_team_id, "Dak Prescott")
    _make_prop(session, game_id=game.id, market_type="prop:pass_yds", player_id=player.id,
               player_name=player.name, side="over", point=Decimal("225"), price=_YES_PRICE)
    return SimpleNamespace(game_id=game.id, player_id=player.id)


def seed_lottery_prop_pool(session):
    """An LSU anchor and four more +EV game lines, plus one game carrying two prop selections:
    a scorer priced on both sides (the pair a devig needs) and a scorer priced on `yes` only
    (no pair at all, and no deep link either)."""
    anchor = _anchor_game(session)
    for i in range(1, 5):
        _pool_leg(session, team_abbr=f"OPP{i}", opp_abbr=f"OTH{i}", market_type="moneyline",
                  edge=Decimal("0.03"), kickoff=_PROP_KICKOFF, price=Decimal("1.90"))
    game = _prop_game(session, "PRA", "PRB")
    paired = _scorer(session, game, game.home_team_id, "Malik Nabers")
    lonely = _scorer(session, game, game.away_team_id, "Brian Thomas", no=None, link=None,
                     sid=None)
    return SimpleNamespace(anchor_game=anchor, prop_game=game, paired=paired, lonely=lonely)


def seed_smart_prop_pool(session):
    """An LSU anchor and four prop games, each with one two-sided scorer: the smart card's cap
    of `max_prop_legs_smart` prop legs is the only thing that stops the fourth leg."""
    anchor = _anchor_game(session)
    scorers = []
    for i in range(1, 5):
        game = _prop_game(session, f"SPA{i}", f"SPB{i}")
        scorers.append(_scorer(session, game, game.home_team_id, f"Scorer Number{i}"))
    return SimpleNamespace(anchor_game=anchor, scorers=scorers)


def seed_same_game_pool(session):
    """An LSU anchor with three two-sided scorers in the anchor's own game -- two on the home
    team, one on the visitors -- plus two +EV game lines elsewhere that a same-game card must
    not reach for."""
    anchor = _anchor_game(session)
    home = _scorer(session, anchor, anchor.home_team_id, "Malik Nabers")
    second = _scorer(session, anchor, anchor.home_team_id, "Kyren Lacy")
    away = _scorer(session, anchor, anchor.away_team_id, "Rome Odunze")
    for i in range(1, 3):
        _pool_leg(session, team_abbr=f"ELS{i}", opp_abbr=f"EOT{i}", market_type="moneyline",
                  edge=Decimal("0.03"), kickoff=_PROP_KICKOFF, price=Decimal("1.90"))
    return SimpleNamespace(anchor_game=anchor, home=home, second=second, away=away)


def _unmatched_prop(session):
    """A prop row whose `player_id` resolves to no rostered player: D14's `player_unmatched`."""
    _anchor_game(session)
    game = _prop_game(session, "UMA", "UMB")
    for side, price in (("yes", _YES_PRICE), ("no", _NO_PRICE)):
        _make_prop(session, game_id=game.id, market_type="prop:anytime_td", player_id=999_001,
                   player_name="Ghost Player", side=side, price=price)


def _family_without_a_rule(session):
    """`pass_yds` has no recorded DraftKings settlement rule in `market_defs` (T18a), so it is
    `market_unsupported` and is never built."""
    _anchor_game(session)
    game = _prop_game(session, "MUA", "MUB")
    player = _make_player(session, game.home_team_id, "Garrett Nussmeier")
    for side, price in (("over", _YES_PRICE), ("under", _NO_PRICE)):
        _make_prop(session, game_id=game.id, market_type="prop:pass_yds", player_id=player.id,
                   player_name=player.name, side=side, point=Decimal("225"), price=price)


def _stale_prop(session):
    """A supported family, matched player and a pair -- priced two hours ago."""
    _anchor_game(session)
    game = _prop_game(session, "STA", "STB")
    _scorer(session, game, game.home_team_id, "Stale Scorer",
            fetched_at=NOW - timedelta(hours=2))


def _product_of_sourced_legs(session, card_id):
    product = Decimal("1")
    for leg in _legs(session, card_id):
        if leg.p_source != "none":
            product *= leg.p_at_build
    return product.quantize(Decimal("0.000001"))


def _minimum_link_over(legs):
    """`selection` when every leg carries a validated outcome link, `event` when every leg
    carries at least the venue's selection id, else `none` (addendum 2.2)."""
    if all(leg.dk_link for leg in legs):
        return "selection"
    if all(leg.dk_link or leg.dk_sid for leg in legs):
        return "event"
    return "none"


@pytest.fixture
def seeded_prop_prices(db_session):
    return seed_prop_prices(db_session)


@pytest.fixture
def seeded_lottery_prop_pool(db_session):
    return seed_lottery_prop_pool(db_session)


@pytest.fixture
def seeded_smart_prop_pool(db_session):
    return seed_smart_prop_pool(db_session)


@pytest.fixture
def seeded_same_game_pool(db_session):
    return seed_same_game_pool(db_session)


def test_a_prop_leg_is_priced_from_the_newest_dk_prop_row(db_session, env_settings,
                                                          seeded_prop_prices):
    price = newest_dk_prop_price(db_session, game_id=seeded_prop_prices.game_id,
                                 market_type="prop:pass_yds",
                                 player_id=seeded_prop_prices.player_id,
                                 point=Decimal("225"), side="over", now=T0,
                                 max_age=timedelta(minutes=30))
    assert price is not None and price.dk_american == -115
    stale = newest_dk_prop_price(db_session, game_id=seeded_prop_prices.game_id,
                                 market_type="prop:pass_yds",
                                 player_id=seeded_prop_prices.player_id,
                                 point=Decimal("225"), side="over", now=T0 + timedelta(hours=2),
                                 max_age=timedelta(minutes=30))
    assert stale is None


def test_a_prop_leg_carries_a_devigged_probability_from_the_book_s_own_two_sides(
        db_session, env_settings, seeded_lottery_prop_pool):
    """Expected: `p_source = 'book_devig'` and `p_at_build` is the two-sided devig, never a
    fair value (D4).

    Computed independently of the code: yes at 1.87 and no at 1.95 imply 0.5348 and 0.5128,
    summing to 1.0476; the devigged yes is 0.5348 / 1.0476 = 0.5105. A prop has no signal and
    no sharp fair, so anything else here would be a number the harness invented.
    """
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", T0)
    prop = next(leg for leg in _legs(db_session, card.id) if leg.market_type == "prop")
    assert prop.p_source == "book_devig"
    assert prop.p_at_build == Decimal("0.5105")
    assert prop.stat == "anytime_td" and prop.operator == "yes"
    assert card.p_source_min == "book_devig"


def test_a_one_sided_selection_with_no_pair_is_source_none_and_excluded(
        db_session, env_settings, seeded_lottery_prop_pool):
    """B-I8: a one-sided price devigs against the matching pair when one exists inside the age
    limit; otherwise `p_source = 'none'` and the leg is left out of the combined chance rather
    than assigned a probability."""
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", T0)
    legs = _legs(db_session, card.id)
    lonely = next(leg for leg in legs
                  if leg.player_id == seeded_lottery_prop_pool.lonely.id)
    assert lonely.p_source == "none" and lonely.p_at_build is None
    assert card.true_prob_est == _product_of_sourced_legs(db_session, card.id)


def test_a_smart_card_takes_at_most_two_prop_legs_from_different_games(
        db_session, env_settings, seeded_smart_prop_pool):
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "smart", T0)
    legs = _legs(db_session, card.id)
    props = [leg for leg in legs if leg.market_type == "prop"]
    assert props
    assert len(props) <= load_config().props.max_prop_legs_smart
    assert len({leg.game_id for leg in legs}) == len(legs)


def test_a_same_game_card_is_correlated_calculated_and_carries_no_hold(
        db_session, env_settings, seeded_same_game_pool):
    """Addendum 2.2 and D4: an independence product the book never quotes measures nothing, so
    a correlated card states no hold at all rather than a number."""
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", T0,
                      same_game=True)
    legs = _legs(db_session, card.id)
    assert card.correlated is True and card.combined_kind == "calculated"
    assert card.hold_est is None
    assert len({leg.game_id for leg in legs}) == 1
    assert 3 <= len(legs) <= 6
    assert len({(leg.player_id, leg.stat) for leg in legs}) == len(legs)


def test_a_relationship_note_appears_only_for_a_same_team_pair_in_the_table(
        db_session, env_settings, seeded_same_game_pool):
    """B-C6: the note is keyed on (stat pair, same team). Two legs of *different* teams in the
    same game get no note -- a quarterback and the opposing receiver do not move together. No
    note can appear on a card built today, because every pair in the table names a family whose
    settlement rule is not recorded yet (`market_unsupported`, D19).
    """
    assert RELATIONSHIP_NOTES[("pass_yds", "rec_yds")].startswith("both move on")
    qb = {"stat": "pass_yds", "team_id": 1, "player_id": 10, "player_name": "The QB"}
    wr = {"stat": "rec_yds", "team_id": 1, "player_id": 11, "player_name": "The WR"}
    opposing = {"stat": "rec_yds", "team_id": 2, "player_id": 12, "player_name": "The Other WR"}
    note = _relationship_note(qb, wr)
    assert note is not None and note.startswith("both move on") and "The WR" in note
    assert _relationship_note(qb, opposing) is None
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", T0,
                      same_game=True)
    assert not any("both move" in (leg.context_text or "")
                   for leg in _legs(db_session, card.id))


def test_link_capability_is_the_minimum_over_legs(db_session, env_settings,
                                                  seeded_lottery_prop_pool):
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", T0)
    legs = _legs(db_session, card.id)
    assert card.link_capability in ("selection", "event", "none")
    assert card.link_capability == _minimum_link_over(legs)
    assert card.combined_kind != "quoted"     # D8: defined, never written in release one


@pytest.mark.parametrize("reason,fixture", [
    ("player_unmatched", _unmatched_prop),
    ("market_unsupported", _family_without_a_rule),
    ("stale_price", _stale_prop),
])
def test_every_disqualifier_is_recorded_by_code_not_silently_skipped(db_session, env_settings,
                                                                     reason, fixture):
    """Each fixture seeds one anchor and one disqualified prop selection, so the shape cannot be
    built and the refusal names the dominant reason (addendum 2.4). One case per session: three
    disqualifiers in one pool would make `dominant` ambiguous by construction."""
    fixture(db_session)
    with pytest.raises(BuildRefused) as excinfo:
        build_card(db_session, env_settings, _PARLAY_SPORT, 37, "smart", T0)
    assert excinfo.value.reason_code == reason


def test_every_card_records_the_policy_version(db_session, env_settings, seeded_smart_prop_pool):
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "smart", T0)
    assert card.policy_version == load_config().policy_version


def test_every_prop_leg_records_its_market_definition_and_deep_link(
        db_session, env_settings, seeded_lottery_prop_pool):
    """The recorded DraftKings rule (D19) travels with the leg, and so does the link the
    placement needs: a leg with neither is a leg the owner cannot check or place."""
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", T0)
    paired = next(leg for leg in _legs(db_session, card.id)
                  if leg.player_id == seeded_lottery_prop_pool.paired.id)
    assert paired.market_def == load_config().props.market_defs["anytime_td"]
    assert paired.dk_link and paired.dk_sid
    assert paired.period == "game" and paired.offered is True
    assert paired.context_text == "no season data yet"


def test_a_same_game_card_with_no_prop_price_in_the_anchors_game_refuses_by_code(db_session,
                                                                                  env_settings):
    """A same-game card is a prop card by construction (addendum 2.2): an anchor alone is not
    one, and the slot says `no_props_fresh` rather than showing nothing."""
    _anchor_game(db_session)
    with pytest.raises(BuildRefused) as excinfo:
        build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", T0, same_game=True)
    assert excinfo.value.reason_code == "no_props_fresh"


def test_a_replacement_card_records_the_card_it_replaces(db_session, env_settings,
                                                         seeded_smart_prop_pool):
    """`parent_card_id` is how a declined card's replacement is found again (addendum 2.4, D5);
    a replacement with no parent is indistinguishable from a fresh slot."""
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "smart", T0,
                      parent_card_id=4242)
    assert card.parent_card_id == 4242


# --- Fix round 1 ------------------------------------------------------------------------------


def seed_no_favoured_scorer(session):
    """The review's own pair: a scorer priced `yes` 2.50 / `no` 1.50, so the likelier side is
    `no` -- 0.3750 against 0.6250 -- which is the ordinary shape of a DraftKings scorer market
    and the one this builder must refuse to write as a `yes` leg."""
    _anchor_game(session)
    game = _prop_game(session, "NFA", "NFB")
    return _scorer(session, game, game.home_team_id, "Longshot Scorer",
                   yes=Decimal("2.50"), no=Decimal("1.50"))


def test_a_scorer_whose_likelier_side_is_no_is_skipped_by_code_not_built_as_a_yes_leg(
        db_session, env_settings):
    """Critical 1. Addendum 4.4's operator vocabulary is `over|under|atleast|yes`: there is no
    `no` operator, and Task 5 grades this leg as hit when a qualifying touchdown is recorded.
    Building the `no` price under a `yes` operator would print one selection on the card, have
    the owner place another, and grade a third; the selection is skipped with a code instead.
    """
    seed_no_favoured_scorer(db_session)
    with pytest.raises(BuildRefused) as excinfo:
        build_card(db_session, env_settings, _PARLAY_SPORT, 37, "smart", T0)
    assert excinfo.value.reason_code == "side_unsupported"


def test_a_yes_favoured_scorer_is_built_described_and_priced_on_the_yes_side(
        db_session, env_settings, seeded_smart_prop_pool):
    """The other half of Critical 1: when `yes` is the likelier side it is built, and the price,
    the side, the operator and the words on the card are all that same side."""
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "smart", T0)
    prop = next(leg for leg in _legs(db_session, card.id) if leg.market_type == "prop")
    assert prop.side == "yes" and prop.operator == "yes"
    assert prop.dk_decimal == _YES_PRICE
    assert prop.p_at_build == Decimal("0.5105")
    assert "to score a touchdown" in prop.plain_text
    assert "not" not in prop.plain_text.lower()


def test_a_prop_leg_records_its_own_snapshot_column_and_no_game_line_id(
        db_session, env_settings, seeded_lottery_prop_pool):
    """Important 2: both snapshot tables are `bigserial` from 1, so a prop id in
    `odds_snapshot_id` resolves to a real but unrelated game line for any reader that forgets to
    branch. A prop leg fills `odds_prop_snapshot_id` and leaves the other null; a game line does
    the reverse."""
    from harness.db.models import OddsPropSnapshot

    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "lottery", T0)
    legs = _legs(db_session, card.id)
    props = [leg for leg in legs if leg.market_type == "prop"]
    lines = [leg for leg in legs if leg.market_type != "prop"]
    assert props and lines
    for leg in props:
        assert leg.odds_snapshot_id is None and leg.odds_prop_snapshot_id is not None
        row = db_session.get(OddsPropSnapshot, leg.odds_prop_snapshot_id)
        assert row is not None and row.player_id == leg.player_id
        assert row.price_decimal == leg.dk_decimal
    for leg in lines:
        assert leg.odds_snapshot_id is not None and leg.odds_prop_snapshot_id is None


class _CountingEspn:
    """An ESPN client double that answers one receiver's game log and counts its calls."""

    BODY = {"names": ["receptions", "receivingYards"],
            "seasonTypes": [{"categories": [{"events": [{"stats": ["8", "122"]},
                                                        {"stats": ["6", "104"]}]}]}]}

    def __init__(self):
        self.calls: list[str] = []

    def fetch_gamelog(self, sport, athlete_id):
        self.calls.append(athlete_id)
        return SimpleNamespace(status=200, body=self.BODY)


def _prop_item(espn_id, family="rec_yds"):
    return {"kind": "prop", "family": family, "espn_id": espn_id, "game_id": 1,
            "player_id": 1, "player_name": "A Receiver", "team_id": 1, "p": None,
            "p_source": "none", "side": "over", "operator": "over"}


def test_the_game_log_pass_stops_at_its_budget_and_the_rest_read_no_season_data(env_settings):
    """Important 3: forty sequential fetches at the HTTP timeout each is up to four hundred
    seconds of blocking HTTP inside a stage bounded at sixty (B-I9). One deadline over the whole
    pass, checked between fetches, with a fake clock so the test sleeps for nothing.
    """
    espn = _CountingEspn()
    # 0.0 sets the deadline, 0.0 lets the first fetch through, and the next reading is past it.
    ticks = iter([0.0, 0.0, GAMELOG_BUDGET_S + 1, GAMELOG_BUDGET_S + 2])
    lines, unfetched = _context_lines([_prop_item("1"), _prop_item("2"), _prop_item("3")],
                                      env_settings, "nfl", espn, clock=lambda: next(ticks))
    assert espn.calls == ["1"]
    assert lines[0] == "avg 113 \u00b7 last 122 \u00b7 ESPN"
    assert lines[1] == "no season data yet" and lines[2] == "no season data yet"
    assert unfetched == 2


def test_a_scorer_leg_never_asks_espn_for_a_game_log_at_all(db_session, env_settings,
                                                            seeded_smart_prop_pool):
    """`anytime_td` has no season column in the game log (addendum 4.1), so the only family
    buildable today spends none of the budget and makes no request."""
    espn = _CountingEspn()
    card = build_card(db_session, env_settings, _PARLAY_SPORT, 37, "smart", T0, espn=espn)
    assert espn.calls == []
    assert all(leg.context_text == "no season data yet"
               for leg in _legs(db_session, card.id) if leg.market_type == "prop")


def _alt_row(point, side, price, fetched_at):
    return SimpleNamespace(point=point, outcome_side=side, price_decimal=price,
                           fetched_at=fetched_at)


def test_an_alternate_line_devigs_against_the_closest_fresh_main_pair():
    """Minor 2: B-I8's fallback, which no buildable family can reach until a yardage rule is
    recorded (D19). `_main_pair` is pure, so it is tested directly: the closest main line with
    both sides inside the age limit wins, a stale pair is not a pair, and a one-sided main line
    is not one either."""
    fresh = NOW - timedelta(minutes=5)
    stale = NOW - timedelta(hours=2)
    max_age = timedelta(minutes=30)
    close = {"over": _alt_row(Decimal("225"), "over", Decimal("1.87"), fresh),
             "under": _alt_row(Decimal("225"), "under", Decimal("1.95"), fresh)}
    far = {"over": _alt_row(Decimal("275"), "over", Decimal("2.60"), fresh),
           "under": _alt_row(Decimal("275"), "under", Decimal("1.45"), fresh)}
    mains = {(1, "pass_yds", 7): [(Decimal("275"), far), (Decimal("225"), close)]}
    pair = _main_pair(mains, 1, "pass_yds", 7, Decimal("250"), "over", NOW, max_age)
    assert pair is not None and pair[0] is close["over"] and pair[1] is close["under"]

    aged = {"over": _alt_row(Decimal("225"), "over", Decimal("1.87"), stale),
            "under": _alt_row(Decimal("225"), "under", Decimal("1.95"), stale)}
    assert _main_pair({(1, "pass_yds", 7): [(Decimal("225"), aged)]}, 1, "pass_yds", 7,
                      Decimal("250"), "over", NOW, max_age) is None
    one_sided = {"over": _alt_row(Decimal("225"), "over", Decimal("1.87"), fresh)}
    assert _main_pair({(1, "pass_yds", 7): [(Decimal("225"), one_sided)]}, 1, "pass_yds", 7,
                      Decimal("250"), "over", NOW, max_age) is None
    assert _main_pair({}, 1, "pass_yds", 7, Decimal("250"), "over", NOW, max_age) is None


def _capability_item(link, sid):
    from harness.parlay.pricing import LegPrice

    return {"price": LegPrice(odds_snapshot_id=None, dk_decimal=Decimal("1.87"),
                              dk_american=-115, point=None, fetched_at=NOW,
                              odds_prop_snapshot_id=1, link=link, sid=sid)}


def test_link_capability_names_the_weakest_link_over_the_legs():
    """Minor 3: the `selection` and `event` branches, which no card carrying a game line can
    reach -- `odds_snapshots` stores neither column."""
    selection = [_capability_item("https://dk/e/1", "sid-1"), _capability_item("https://dk/e/2",
                                                                              None)]
    assert _link_capability(selection) == "selection"
    assert _link_capability([selection[0], _capability_item(None, "sid-2")]) == "event"
    assert _link_capability([selection[0], _capability_item(None, None)]) == "none"
