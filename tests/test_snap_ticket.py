"""The Ticket builder: the between-cards state this phase ships, and the card states phase 5c
will write into."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path

import pytest

from harness.dashboard.snapshots import ticket
from harness.dashboard.snapshots.ticket import TICKET_KEYS, build_ticket
from harness.parlay.config import load_config
from harness.db.models import (Game, GameScoreEvent, ParlayCard, ParlayLedger, ParlayLeg,
                               ParlayPlacement, Team)

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)   # ISO 2026-W37, a Saturday


def _game(session, *, status="in_progress", kickoff=None):
    session.add(Team(sport="ncaaf", id=1, display_name="LSU", location="Baton Rouge",
                     name="Tigers", abbreviation="LSU", short_display_name="LSU"))
    session.add(Team(sport="ncaaf", id=2, display_name="Alabama", location="Tuscaloosa",
                     name="Tide", abbreviation="ALA", short_display_name="Bama"))
    row = Game(sport="ncaaf", home_team_id=1, away_team_id=2,
               kickoff_utc=kickoff or NOW - timedelta(hours=1), status=status)
    session.add(row)
    session.flush()
    return row


def _card(session, *, status="alive", legs=(), stake="20.00", rationale="an LSU anchor",
          built_at=None):
    card = ParlayCard(year=2026, week=37, sport="ncaaf", kind="smart",
                      built_at=built_at or NOW - timedelta(hours=6), stake=Decimal(stake),
                      dk_payout_est=Decimal("140.00"), true_prob_est=Decimal("0.101000"),
                      hold_est=Decimal("0.0800"), rationale=rationale, status=status,
                      correlated=False)
    session.add(card)
    session.flush()
    for seq, (game_id, plain, leg_status) in enumerate(legs, start=1):
        session.add(ParlayLeg(card_id=card.id, seq=seq, game_id=game_id, market_type="ml",
                              side_team_id=1, dk_american=-140, dk_decimal=Decimal("1.7143"),
                              plain_text=plain, status=leg_status))
    session.flush()
    return card


def test_with_no_cards_the_surface_shows_the_between_cards_state(db_session, env_settings):
    payload = build_ticket(db_session, NOW, env_settings)
    assert payload["cards"] == []
    # One source for the $50, `parlay.yaml`'s own `weekly_budget` (review round 1, M7).
    assert payload["between"]["budget_left"] == float(load_config().weekly_budget)
    assert payload["between"]["anchor_rule"]
    assert payload["between"]["next_build_day"] in ("Friday", "Saturday evening")
    assert "No card is live" in " ".join(payload["sentences"]["between"])


def test_the_budget_left_subtracts_this_weeks_stakes(db_session, env_settings):
    db_session.add(ParlayLedger(ts=NOW - timedelta(days=1), card_id=1, kind="stake",
                                amount=Decimal("20.00"), year=2026, week=37))
    db_session.flush()
    payload = build_ticket(db_session, NOW, env_settings)
    assert payload["between"]["budget_left"] == 30.0


def test_a_placed_card_before_kickoff_shows_every_leg_pending(db_session, env_settings):
    game = _game(db_session, status="scheduled", kickoff=NOW + timedelta(hours=3))
    card = _card(db_session, status="placed",
                 legs=[(game.id, "LSU to win", "pending")])
    db_session.add(ParlayPlacement(card_id=card.id, placed_at=NOW - timedelta(hours=2),
                                   stake_actual=Decimal("20.00"),
                                   dk_payout_actual=Decimal("142.00"), dk_odds_actual=610))
    db_session.flush()

    payload = build_ticket(db_session, NOW, env_settings)
    shown = payload["cards"][0]
    assert shown["placed"] is True and shown["payout"] == 142.0
    assert shown["legs"][0]["needs"] == "no score yet"
    assert shown["legs_remaining"] == 1


def test_a_live_card_with_a_missed_leg_is_busted_and_says_so(db_session, env_settings):
    game = _game(db_session)
    db_session.add(GameScoreEvent(game_id=game.id, ts=NOW - timedelta(minutes=2),
                                  status="final", period=4, clock="0:00",
                                  home_score=10, away_score=24))
    card = _card(db_session, status="busted",
                 legs=[(game.id, "LSU to win", "miss")])
    db_session.flush()

    shown = build_ticket(db_session, NOW, env_settings)["cards"][0]
    assert shown["status"] == "busted"
    assert shown["legs"][0]["needs"] == "did not happen"
    assert "Ouch" in " ".join(shown["sentences"])


def test_a_score_row_with_a_home_score_and_no_away_score_still_builds(db_session, env_settings):
    """Ruling A-I4: `game_score_events.home_score` and `away_score` are independently nullable
    -- a live game with a home score recorded and no away score yet must not blank the whole
    `cards` section with a `TypeError`."""
    game = _game(db_session)
    db_session.add(GameScoreEvent(game_id=game.id, ts=NOW - timedelta(minutes=2),
                                  status="in_progress", period=2, clock="5:00",
                                  home_score=10, away_score=None))
    _card(db_session, status="alive", legs=[(game.id, "LSU to win", "alive")])
    db_session.flush()

    shown = build_ticket(db_session, NOW, env_settings)["cards"][0]
    assert shown["legs"][0]["home_score"] == 10
    assert shown["legs"][0]["away_score"] is None
    assert shown["legs"][0]["needs"] == "no score yet"


def test_a_cashed_card_reads_as_cashed(db_session, env_settings):
    game = _game(db_session, status="final")
    card = _card(db_session, status="cashed", legs=[(game.id, "LSU to win", "hit")])
    db_session.add(ParlayLedger(ts=NOW, card_id=card.id, kind="return",
                                amount=Decimal("142.00"), year=2026, week=37))
    db_session.flush()

    payload = build_ticket(db_session, NOW, env_settings)
    assert payload["cards"][0]["status"] == "cashed"
    assert payload["season"]["returned"] == 142.0


def test_the_season_has_no_best_hit_and_a_zero_streak_with_no_cards(db_session, env_settings):
    """Spec §2.5 season-strip metrics: best hit and streak, with nothing settled yet."""
    payload = build_ticket(db_session, NOW, env_settings)
    assert payload["season"]["best_hit"] is None
    assert payload["season"]["streak"] == 0


def test_the_streak_and_best_hit_read_a_cashed_then_busted_sequence(db_session, env_settings):
    """The streak is signed and reads only the most recent run of verdicts (+ cashes, - busts);
    best hit is the largest amount ever returned on a cashed card, whichever week it was."""
    older_cash = _card(db_session, status="cashed", built_at=NOW - timedelta(days=3))
    db_session.add(ParlayLedger(ts=NOW - timedelta(days=3), card_id=older_cash.id, kind="return",
                                amount=Decimal("50.00"), year=2026, week=37))
    best_cash = _card(db_session, status="cashed", built_at=NOW - timedelta(days=2))
    db_session.add(ParlayLedger(ts=NOW - timedelta(days=2), card_id=best_cash.id, kind="return",
                                amount=Decimal("142.00"), year=2026, week=37))
    _card(db_session, status="busted", built_at=NOW - timedelta(days=1))
    db_session.flush()

    season = build_ticket(db_session, NOW, env_settings)["season"]
    assert season["streak"] == -1
    assert season["best_hit"] == {"card_id": best_cash.id, "week": 37, "amount": 142.0}


def test_the_season_strip_excludes_a_card_that_is_still_only_proposed(db_session, env_settings):
    """A card `parlay_grade`/the builder has not moved past `proposed` is not a ticket yet."""
    _card(db_session, status="proposed")
    payload = build_ticket(db_session, NOW, env_settings)
    assert payload["season"]["strip"] == []


def test_a_voided_card_appears_in_the_card_list_and_its_refund_is_not_a_loss(db_session,
                                                                             env_settings):
    """Review I3: a void needs no result, and the ledger's refund is not a loss. `_LIVE_CARDS`
    treats `void` exactly as `cashed`/`busted` are treated, so the card leaves the live list
    together with the strip (`_STRIP` already carried `void`); the season `net` adds the refund
    back rather than counting the stake as gone."""
    card = _card(db_session, status="void")
    db_session.add(ParlayLedger(ts=NOW - timedelta(hours=1), card_id=card.id, kind="stake",
                                amount=Decimal("20.00"), year=2026, week=37))
    db_session.add(ParlayLedger(ts=NOW, card_id=card.id, kind="void",
                                amount=Decimal("20.00"), year=2026, week=37))
    db_session.flush()

    payload = build_ticket(db_session, NOW, env_settings)
    assert payload["cards"][0]["status"] == "void"
    assert payload["season"]["net"] == 0.0


def test_one_leg_from_glory(db_session, env_settings):
    game = _game(db_session)
    db_session.add(GameScoreEvent(game_id=game.id, ts=NOW - timedelta(minutes=1),
                                  status="in_progress", period=4, clock="6:00",
                                  home_score=20, away_score=17))
    _card(db_session, legs=[(game.id, "LSU to win", "alive")])
    db_session.flush()
    shown = build_ticket(db_session, NOW, env_settings)["cards"][0]
    assert "One leg from glory" in " ".join(shown["sentences"])
    assert shown["legs"][0]["needs"] == "any win does it"


def test_a_leg_carries_its_probability_history_for_the_small_bar(db_session, env_settings):
    """Spec §2.5: "sharps say NN %" comes **with a small history bar**, which `legProbHistory`
    draws. The newest row alone would leave that component with nothing to plot."""
    from harness.db.models import ParlayLegProb

    game = _game(db_session)
    card = _card(db_session, legs=[(game.id, "LSU to win", "alive")])
    leg = db_session.query(ParlayLeg).filter(ParlayLeg.card_id == card.id).one()
    for minutes, p in ((90, "0.5100"), (45, "0.5600"), (5, "0.6400")):
        db_session.add(ParlayLegProb(leg_id=leg.id, ts=NOW - timedelta(minutes=minutes),
                                     sharp_p=Decimal(p)))
    db_session.flush()

    shown = build_ticket(db_session, NOW, env_settings)["cards"][0]["legs"][0]
    assert shown["sharp_p"] == pytest.approx(0.64)
    assert [point[1] for point in shown["sharp_p_history"]] == \
        [pytest.approx(0.51), pytest.approx(0.56), pytest.approx(0.64)]


def test_hostile_leg_text_and_rationale_are_sanitized(db_session, env_settings):
    """The writer ships in phase 5c; until then this surface is the only guard on that text."""
    game = _game(db_session)
    _card(db_session, rationale="<img onerror=alert(1) src=x>",
          legs=[(game.id, "<script>alert(1)</script>", "alive")])
    db_session.flush()

    shown = build_ticket(db_session, NOW, env_settings)["cards"][0]
    assert "<" not in shown["rationale"] and ">" not in shown["rationale"]
    assert "<" not in shown["legs"][0]["plain_text"]


def test_the_payload_carries_only_the_allowed_keys_and_no_paper_figure(db_session,
                                                                      env_settings):
    """Spec §2.5 never-shown: no paper number, no research variant, no CLV."""
    payload = build_ticket(db_session, NOW, env_settings)
    assert set(payload) == TICKET_KEYS
    for banned in ("paper", "variant", "clv", "equity", "exposure"):
        assert banned not in payload


def test_the_badge_is_the_fun_money_one(db_session, env_settings):
    payload = build_ticket(db_session, NOW, env_settings)
    assert payload["badge"] == "FUN MONEY - $50/WEEK - PLACED BY HAND"


def test_no_ticket_sql_names_a_forbidden_table():
    body = Path(ticket.__file__).read_text().lower()
    for table in ("orderbook_events", "venue_trades", "raw_responses", "odds_snapshots",
                  "venue_quotes"):
        assert table not in body


#: Sunday 2026-09-13 20:00 CT: UTC's ISO week is already 38, Chicago's is still 37.
SUNDAY_20_CT = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)


def test_the_budget_week_is_the_chicago_week_on_a_sunday_evening(db_session, env_settings):
    """Addendum §0.1: a Sunday-evening build must spend against week 37's ledger, not week 38's."""
    db_session.add(ParlayLedger(ts=SUNDAY_20_CT - timedelta(days=1), card_id=1, kind="stake",
                                amount=Decimal("20.00"), year=2026, week=37))
    db_session.flush()
    between = build_ticket(db_session, SUNDAY_20_CT, env_settings)["between"]
    assert (between["year"], between["week"]) == (2026, 37)
    assert between["budget_left"] == 30.0


# --- phase 4.6 Task 12: ideas, stat lines, provenance and corrections -------------------------
# Addendum §1.1-1.5. One fixture per state of 1.5, and a draft slip and a live slip rendered from
# the *same* card fixture (design §8.1), so the two renderings cannot drift.

import json

from harness.dashboard.sentences import (IDEA_PHRASES, idea_reason_phrase, stat_line,
                                          unknown_idea_codes)
from harness.db.models import JobState, ParlayPlacementCorrection, PlayerStatEvent
from harness.settlement.parlay_build import _REASON_INDEX, SLOTS, slot_key

#: The correction fixture's timestamp, so the payload's `ts` is asserted against a known instant
#: rather than against whatever the builder happened to read.
T_CORRECTION = NOW - timedelta(hours=1)


def _teams(session):
    """LSU and Alabama, idempotently: a test that needs a second game must not re-insert the
    same primary keys."""
    for team_id, display, abbr in ((1, "LSU", "LSU"), (2, "Alabama", "ALA")):
        session.merge(Team(sport="ncaaf", id=team_id, display_name=display, location="x",
                           name=display, abbreviation=abbr, short_display_name=abbr))
    session.flush()


def _nfl_game(session, *, status="scheduled", kickoff=None):
    session.merge(Team(sport="nfl", id=11, display_name="Saints", location="New Orleans",
                       name="Saints", abbreviation="NO", short_display_name="NO"))
    session.merge(Team(sport="nfl", id=12, display_name="Falcons", location="Atlanta",
                       name="Falcons", abbreviation="ATL", short_display_name="ATL"))
    row = Game(sport="nfl", home_team_id=11, away_team_id=12,
               kickoff_utc=kickoff if kickoff is not None else NOW + timedelta(hours=6),
               status=status)
    session.add(row)
    session.flush()
    return row


def _proposed(session, *, sport="nfl", kind="smart", correlated=False, combined_at=None,
              link_capability="selection", p_source_min="sharp", hold_est="0.0800"):
    """One `proposed` card of a slot's shape, with the phase 4.6 columns the builder fills."""
    card = ParlayCard(year=2026, week=37, sport=sport, kind=kind,
                      built_at=NOW - timedelta(hours=2), stake=Decimal("25.00"),
                      dk_payout_est=Decimal("137.50"), true_prob_est=Decimal("0.101000"),
                      hold_est=Decimal(hold_est) if hold_est is not None else None,
                      rationale="an LSU anchor", status="proposed", correlated=correlated,
                      policy_version="2026.09-1", combined_kind="calculated",
                      dk_combined_american=450,
                      dk_combined_at=combined_at if combined_at is not None
                      else NOW - timedelta(minutes=4),
                      link_capability=link_capability, p_source_min=p_source_min)
    session.add(card)
    session.flush()
    return card


def _ml_leg(session, card, game, *, seq=1, status="pending", p_source="sharp", offered=True,
            plain_text="LSU to win"):
    leg = ParlayLeg(card_id=card.id, seq=seq, game_id=game.id, market_type="ml",
                    side_team_id=game.home_team_id, dk_american=-140,
                    dk_decimal=Decimal("1.7143"), plain_text=plain_text, status=status,
                    dk_link="https://sportsbook.draftkings.com/event/1234",
                    dk_sid="0QA123", offered=offered, p_at_build=Decimal("0.6100"),
                    p_source=p_source, context_text="sharps say 61 % at build")
    session.add(leg)
    session.flush()
    return leg


def _prop_leg(session, card, game, *, seq=2, status="pending", stat="pass_yds",
              operator="over", threshold="225.0", offered=True, player_id=7, p_source="book_devig",
              plain_text="Nussmeier 225+ passing yards"):
    leg = ParlayLeg(card_id=card.id, seq=seq, game_id=game.id, market_type="prop",
                    side=operator if operator in ("over", "under") else None,
                    threshold=Decimal(threshold) if threshold is not None else None,
                    dk_american=-115, dk_decimal=Decimal("1.8696"), plain_text=plain_text,
                    status=status, player_id=player_id, stat=stat, period="game",
                    operator=operator, dk_link="https://sportsbook.draftkings.com/event/9?sid=1",
                    dk_sid="0QA987", offered=offered, p_at_build=Decimal("0.5200"),
                    p_source=p_source,
                    context_text="avg 262 · last 241 · ESPN")
    session.add(leg)
    session.flush()
    return leg


def _slot_state(session, sport, shape, *, reason=None, built=None, at=None, value=None):
    """One slot's recorded outcome, in Task 7's own encoding: a positive card id for a build,
    `_REASON_INDEX`'s pinned negative for a reason (`harness/settlement/parlay_build.py`).
    `value` writes a raw integer, for the unrecognized-encoding case."""
    if value is None:
        value = built if built is not None else _REASON_INDEX[reason]
    session.merge(JobState(key=slot_key(2026, 37, sport, shape), value=value,
                           updated_at=at if at is not None else NOW - timedelta(minutes=30)))
    session.flush()


def _slot(payload, sport, shape):
    for slot in payload["ideas"]["slots"]:
        if (slot["sport"], slot["shape"]) == (sport, shape):
            return slot
    raise AssertionError(f"no {sport}/{shape} slot in {payload['ideas']['slots']}")


def _rendered_legs(payload):
    legs = [leg for card in payload["cards"] for leg in card["legs"]]
    for slot in payload["ideas"]["slots"]:
        if slot["card"]:
            legs.extend(slot["card"]["legs"])
    return legs


def _leg_of(payload, leg):
    for rendered in _rendered_legs(payload):
        if rendered["leg_id"] == leg.id:
            return rendered
    raise AssertionError(f"leg {leg.id} is in no rendered card")


def _card_of(payload, card):
    for rendered in payload["cards"]:
        if rendered["card_id"] == card.id:
            return rendered
    raise AssertionError(f"card {card.id} is not in `cards`")


def _slot_card(payload, card):
    for slot in payload["ideas"]["slots"]:
        if slot["card"] and slot["card"]["card_id"] == card.id:
            return slot["card"]
    raise AssertionError(f"card {card.id} is in no idea slot")


def _live_prop_leg(session, *, value=208, line="225.0", age_s=40, previous=None,
                   correction=False, status="in_progress", operator="over", stat="pass_yds"):
    """A live card carrying one prop leg, with the player's newest recorded stat row."""
    _teams(session)
    game = Game(sport="ncaaf", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW - timedelta(hours=2), status=status)
    session.add(game)
    session.flush()
    card = ParlayCard(year=2026, week=37, sport="ncaaf", kind="smart",
                      built_at=NOW - timedelta(hours=6), stake=Decimal("25.00"),
                      dk_payout_est=Decimal("137.50"), true_prob_est=Decimal("0.101000"),
                      hold_est=None, rationale="an LSU anchor", status="alive",
                      correlated=False, policy_version="2026.09-1", p_source_min="book_devig")
    session.add(card)
    session.flush()
    leg = _prop_leg(session, card, game, seq=1, status="alive", operator=operator, stat=stat,
                    threshold=line)
    if previous is not None:
        session.add(PlayerStatEvent(game_id=game.id, player_id=7,
                                    ts=NOW - timedelta(seconds=age_s + 60), stat=stat,
                                    value=Decimal(str(previous)), source="espn",
                                    correction=False))
    if value is not None:
        session.add(PlayerStatEvent(game_id=game.id, player_id=7,
                                    ts=NOW - timedelta(seconds=age_s), stat=stat,
                                    value=Decimal(str(value)), source="espn",
                                    correction=correction))
    session.flush()
    return leg


def _draft(session, *, price_age_minutes=4, offered=True, inside_window=True, all_sharp=True):
    """A draft slip in the `nfl`/`smart` slot: a game line and a second leg whose `p_source`
    decides which footer sentence the card gets."""
    kickoff = NOW + timedelta(hours=6 if inside_window else 48)
    game = _nfl_game(session, kickoff=kickoff)
    card = _proposed(session, sport="nfl", kind="smart",
                     combined_at=NOW - timedelta(minutes=price_age_minutes),
                     p_source_min="sharp" if all_sharp else "none",
                     hold_est="0.0800" if all_sharp else None)
    _ml_leg(session, card, game, seq=1, p_source="sharp")
    if all_sharp:
        _ml_leg(session, card, game, seq=2, p_source="sharp", plain_text="Falcons +3.5",
                offered=offered)
    else:
        _prop_leg(session, card, game, seq=2, offered=offered, p_source="none")
    _slot_state(session, "nfl", "smart", built=card.id)
    return card


def _all_legs_hit(session, *, confirmed_return=None, correlated=False):
    _teams(session)
    game = Game(sport="ncaaf", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW - timedelta(hours=5), status="final")
    session.add(game)
    session.flush()
    card = ParlayCard(year=2026, week=37, sport="ncaaf", kind="smart",
                      built_at=NOW - timedelta(hours=8), stake=Decimal("25.00"),
                      dk_payout_est=Decimal("137.50"), true_prob_est=Decimal("0.101000"),
                      hold_est=None, rationale="an LSU anchor", status="cashed",
                      correlated=correlated, policy_version="2026.09-1")
    session.add(card)
    session.flush()
    _ml_leg(session, card, game, seq=1, status="hit")
    session.add(ParlayPlacement(card_id=card.id, placed_at=NOW - timedelta(hours=7),
                                stake_actual=Decimal("25.00"),
                                dk_payout_actual=Decimal("137.50"), dk_odds_actual=450))
    if confirmed_return is not None:
        session.add(ParlayLedger(ts=NOW, card_id=card.id, kind="return",
                                 amount=confirmed_return, year=2026, week=37,
                                 source="confirmed"))
    elif not correlated:
        # What `parlay_grade` writes: the harness's own arithmetic, awaiting confirmation. A
        # correlated card gets no computed return at all (D18).
        session.add(ParlayLedger(ts=NOW, card_id=card.id, kind="return",
                                 amount=Decimal("137.50"), year=2026, week=37,
                                 source="computed"))
    session.flush()
    return card


def _confirmed_void(session):
    card = _all_legs_hit(session)
    session.query(ParlayLedger).filter_by(card_id=card.id, kind="return").delete()
    card.status = "void"
    session.add(ParlayLedger(ts=NOW, card_id=card.id, kind="void", amount=Decimal("25.00"),
                             year=2026, week=37, source="confirmed"))
    session.flush()
    return card


def _corrected_card(session, *, stake_from="25.00", stake_to="30.00"):
    card = _all_legs_hit(session, confirmed_return=Decimal("137.50"))
    session.add(ParlayPlacementCorrection(card_id=card.id, ts=T_CORRECTION, field="stake",
                                          old_value=stake_from, new_value=stake_to))
    session.flush()
    return card


def _hung_leg(session, *, hours=7):
    """A prop leg whose game went final `hours` ago and whose stat never arrived (4.4)."""
    leg = _live_prop_leg(session, value=None, status="final")
    session.add(GameScoreEvent(game_id=leg.game_id, ts=NOW - timedelta(hours=hours),
                               status="final", period=4, clock="0:00",
                               home_score=24, away_score=21))
    session.flush()
    return leg


def test_the_ideas_section_shows_one_slot_per_sport_and_shape(db_session, env_settings):
    db_session.add(ParlayLedger(ts=NOW - timedelta(days=1), card_id=1, kind="stake",
                                amount=Decimal("25.00"), year=2026, week=37))
    _slot_state(db_session, "nfl", "smart", reason="no_props_fresh")
    payload = build_ticket(db_session, NOW, env_settings)
    slots = payload["ideas"]["slots"]
    assert len(slots) <= 6
    assert {(s["sport"], s["shape"]) for s in slots} <= set(SLOTS)
    assert payload["ideas"]["week_left"] == "25.00"      # a decimal string, never a float
    assert payload["ideas"]["week_recorded"] == "25.00"


def test_an_empty_slot_carries_one_reason_code_and_its_sentence(db_session, env_settings):
    _slot_state(db_session, "nfl", "smart", reason="no_props_fresh")
    slot = _slot(build_ticket(db_session, NOW, env_settings), "nfl", "smart")
    assert slot["card"] is None and slot["reason_code"] == "no_props_fresh"
    assert idea_reason_phrase("no_props_fresh")
    assert idea_reason_phrase("replacement_pending").startswith("A replacement is being built")
    assert slot["next_build_at"]


def test_every_reason_the_stage_can_write_now_has_a_sentence(db_session, env_settings):
    """Review round 1, I5. Both shapes that reach the slot -- a code Task 7 pins
    (`stale_price`) and a `job_state` value this harness can no longer decode, which
    `read_slot_state` reports as `unknown` -- render as prose, not as a token, and neither is a
    gap in the vocabulary any more."""
    _slot_state(db_session, "nfl", "smart", reason="stale_price")
    _slot_state(db_session, "ncaaf", "lottery", value=-9999)
    payload = build_ticket(db_session, NOW, env_settings)
    stale = _slot(payload, "nfl", "smart")
    unknown = _slot(payload, "ncaaf", "lottery")
    assert stale["reason_code"] == "stale_price"
    assert stale["reason_text"] == idea_reason_phrase("stale_price") != "stale_price"
    assert unknown["reason_code"] == "unknown"
    assert unknown["reason_text"] == idea_reason_phrase("unknown") != "unknown"
    assert payload["sentences_gaps"] == []


def test_every_reason_code_the_builder_stage_can_record_has_a_phrase():
    """The thirteen: `parlay_build._REASON_INDEX`'s twelve pinned codes plus the `unknown`
    `read_slot_state` reports for a value it cannot decode. Closed at both ends, so a code added
    to the stage without a sentence, or a sentence for a code nothing writes, fails here."""
    assert set(IDEA_PHRASES) == set(_REASON_INDEX) | {"unknown"}
    assert len(IDEA_PHRASES) == 13
    for code, phrase in IDEA_PHRASES.items():
        assert phrase and phrase != code and phrase.endswith(".")


def test_a_code_outside_the_vocabulary_still_renders_as_itself_and_is_reported():
    """The fallback stays: a code this table has never seen is shown, sanitized, and reported in
    `sentences_gaps` rather than blanked (addendum §1.2)."""
    assert idea_reason_phrase("brand_new") == "brand_new"
    assert unknown_idea_codes(["brand_new", "stale_price"]) == ["brand_new"]


def test_the_college_slot_is_not_told_to_come_back_on_saturday(db_session, env_settings):
    """M1 / design §2.2's parenthetical: college cards build Friday, the NFL's Saturday
    evening, and the sentence follows `BUILD_TIMES` rather than naming one day for both."""
    _slot_state(db_session, "ncaaf", "smart", reason="not_built_yet")
    _slot_state(db_session, "nfl", "smart", reason="not_built_yet")
    payload = build_ticket(db_session, NOW, env_settings)
    assert _slot(payload, "ncaaf", "smart")["reason_text"] == "The next card is built Friday."
    assert _slot(payload, "nfl", "smart")["reason_text"] == (
        "The next card is built Saturday evening.")


def test_the_ideas_read_is_its_own_bounded_query_and_leaves_live_cards_alone(db_session,
                                                                            env_settings):
    """B-I17: a proposed card must never appear in `cards`, which is the live/settled list."""
    card = _draft(db_session)
    payload = build_ticket(db_session, NOW, env_settings)
    assert all(c["status"] != "proposed" for c in payload["cards"])
    assert _slot_card(payload, card)["status"] == "proposed"


def test_a_prop_leg_shows_the_stat_line_from_the_newest_recorded_row(db_session, env_settings):
    leg = _live_prop_leg(db_session, value=208, line="225.0", age_s=40)
    text = _leg_of(build_ticket(db_session, NOW, env_settings), leg)["stat_line"]
    assert text == "208 of 225 passing yards · 17 to go · ESPN 40 s ago"


def test_a_player_absent_from_the_latest_update_reads_unchanged_never_zero(db_session,
                                                                          env_settings):
    """Review round 1, I6: `player_stat_events` holds one row per change, so a stat that has
    stood through a defensive drive is the common case and the figure must not vanish with it.
    The line keeps the value and the noun and says `unchanged` about them."""
    leg = _live_prop_leg(db_session, value=208, line="225.0", age_s=120)
    assert _leg_of(build_ticket(db_session, NOW, env_settings), leg)["stat_line"] == (
        "208 of 225 passing yards · unchanged · last seen 2 min ago")


def test_a_leg_with_no_row_at_all_reads_no_stat_yet(db_session, env_settings):
    leg = _live_prop_leg(db_session, value=None, line="225.0")
    assert _leg_of(build_ticket(db_session, NOW, env_settings), leg)["stat_line"] == "no stat yet"


def test_a_corrected_stat_says_so_with_its_source(db_session, env_settings):
    leg = _live_prop_leg(db_session, value=8, previous=12, correction=True, stat="receptions",
                         line="10.0")
    assert _leg_of(build_ticket(db_session, NOW, env_settings), leg)["correction_note"] == (
        "corrected from 12 to 8 · ESPN")


def test_a_prop_leg_has_no_sharp_read_and_no_history_bar(db_session, env_settings):
    leg = _live_prop_leg(db_session, value=208, line="225.0")
    rendered = _leg_of(build_ticket(db_session, NOW, env_settings), leg)
    assert rendered["sharp_p"] is None and rendered["sharp_p_history"] == []


def test_all_legs_hit_and_unconfirmed_reads_expected_and_awaits_confirmation(db_session,
                                                                            env_settings):
    card = _all_legs_hit(db_session)
    rendered = _card_of(build_ticket(db_session, NOW, env_settings), card)
    assert rendered["settlement"]["kind"] == "computed"
    assert rendered["settlement"]["amount"] == "137.50"
    assert rendered["settlement"]["text"] == (
        "all legs hit · expected $137.50 at your recorded odds · "
        "awaiting your confirmation")
    assert rendered["stamp"] is None


def test_cashed_lands_only_on_a_confirmed_return_row(db_session, env_settings):
    """D18. A computed row is the harness's arithmetic; CASHED and any sentence naming
    DraftKings belong to the owner's confirmation and to nothing else."""
    card = _all_legs_hit(db_session, confirmed_return=Decimal("137.50"))
    rendered = _card_of(build_ticket(db_session, NOW, env_settings), card)
    assert rendered["stamp"] == "CASHED"
    assert rendered["settlement"]["kind"] == "confirmed"


def test_a_confirmed_void_reads_voided_by_draftkings_and_stamps_void(db_session, env_settings):
    card = _confirmed_void(db_session)
    rendered = _card_of(build_ticket(db_session, NOW, env_settings), card)
    assert rendered["stamp"] == "VOID"
    assert "voided by DraftKings · stake returned" in rendered["sentences"]


def test_a_correlated_card_says_draftkings_will_state_the_return(db_session, env_settings):
    card = _all_legs_hit(db_session, correlated=True)
    assert "DraftKings will state the return" in _card_of(
        build_ticket(db_session, NOW, env_settings), card)["settlement"]["text"]


def test_a_corrected_placement_shows_the_mark_and_the_original_figures(db_session,
                                                                      env_settings):
    card = _corrected_card(db_session, stake_from="25.00", stake_to="30.00")
    rendered = _card_of(build_ticket(db_session, NOW, env_settings), card)
    assert rendered["corrected"] is True
    assert rendered["corrections"][0] == {"field": "stake", "old_value": "25.00",
                                          "new_value": "30.00", "ts": T_CORRECTION.isoformat()}


def test_a_hung_leg_reads_no_final_stat_pending_with_its_age(db_session, env_settings):
    leg = _hung_leg(db_session, hours=7)
    assert _leg_of(build_ticket(db_session, NOW, env_settings), leg)["stat_line"].startswith(
        "no final stat · pending")


def test_a_stale_quote_warns_and_keeps_the_actions(db_session, env_settings):
    card = _draft(db_session, price_age_minutes=45)
    rendered = _slot_card(build_ticket(db_session, NOW, env_settings), card)
    assert rendered["footer"]["age_warning"] == (
        "prices older than 30 min · repriced at the next tick")
    assert rendered["actions"] == ["open", "placed", "decline"]


def test_a_removed_prop_drops_the_open_button_to_copy_selections(db_session, env_settings):
    card = _draft(db_session, offered=False, inside_window=True, all_sharp=False)
    rendered = _slot_card(build_ticket(db_session, NOW, env_settings), card)
    assert rendered["link_capability"] == "none"
    assert "no longer offered by DraftKings" in json.dumps(rendered)


def test_outside_the_window_a_leg_reads_not_repriced(db_session, env_settings):
    card = _draft(db_session, offered=True, inside_window=False, price_age_minutes=45,
                  all_sharp=False)
    # `ensure_ascii=False`: the separator the slip speaks is a middle dot, and the
    # default `json.dumps` escapes it, which would make this assertion about the
    # escaping rather than about the sentence.
    assert "not repriced · outside the price window" in json.dumps(
        _slot_card(build_ticket(db_session, NOW, env_settings), card),
        ensure_ascii=False)


def test_the_footer_names_the_source_of_the_figure_it_shows(db_session, env_settings):
    """B-C8/D4. `sharps say` only when every leg is sharp; otherwise `no sharp read` and the
    source of the number actually shown, with `none`-source legs named and excluded."""
    card = _draft(db_session, all_sharp=True)
    sharp = _slot_card(build_ticket(db_session, NOW, env_settings), card)
    assert sharp["footer"]["chance"].startswith("sharps say ")
    assert sharp["footer"]["hold"] is not None


def test_the_footer_of_a_mixed_card_names_no_sharp_read(db_session, env_settings):
    card = _draft(db_session, all_sharp=False)
    mixed = _slot_card(build_ticket(db_session, NOW, env_settings), card)
    assert mixed["footer"]["chance"].startswith("no sharp read · ")
    assert mixed["footer"]["hold"] is None
    # Named by the same text the leg itself renders, through this page's own `_display`
    # sanitizer, so the recorded "Nussmeier 225+ passing yards" reaches both the leg and this
    # list with its `+` intact (review round 1, I4). The two must stay the same string: a
    # footer that named a leg differently from the leg would be naming a different bet.
    assert mixed["legs"][1]["plain_text"] in mixed["footer"]["chance"]
    assert "not included" in mixed["footer"]["chance"]


def test_the_ticket_payload_carries_no_paper_key(db_session, env_settings):
    """F02, the hard line. Any of these appearing here would put research on the fun surface."""
    _draft(db_session)
    _live_prop_leg(db_session, value=208, line="225.0")
    _corrected_card(db_session)
    blob = json.dumps(build_ticket(db_session, NOW, env_settings))
    for forbidden in ('"edge"', '"variant"', '"fair_p"', '"signal"', '"venue_market_id"',
                      '"clv', '"intent'):
        assert forbidden not in blob


def test_a_draft_slip_and_a_live_slip_render_from_the_same_card_fixture(db_session,
                                                                       env_settings):
    """Design §8.1: one fixture, two states, so the two renderings cannot drift."""
    card = _draft(db_session)
    draft = _slot_card(build_ticket(db_session, NOW, env_settings), card)
    card.status = "placed"
    db_session.add(ParlayPlacement(card_id=card.id, placed_at=NOW,
                                   stake_actual=Decimal("25.00"),
                                   dk_payout_actual=Decimal("137.50"), dk_odds_actual=450))
    db_session.flush()
    live = _card_of(build_ticket(db_session, NOW, env_settings), card)
    assert draft["card_id"] == live["card_id"]
    assert draft["legs"][0]["selection"] == live["legs"][0]["selection"]
    assert draft["legs"][0]["selection"]


def test_a_slot_with_a_card_names_the_next_build_a_week_out(db_session, env_settings):
    """`next_build_at` derives from the stage's own build times and the slot's `updated_at`: a
    built slot is done for the week, an empty one is retried on the settle job's next hour."""
    card = _draft(db_session)
    payload = build_ticket(db_session, NOW, env_settings)
    built = _slot(payload, "nfl", "smart")
    assert built["card"]["card_id"] == card.id and built["reason_code"] is None
    # NOW is Saturday 13:00 CT and the `nfl` build time is Saturday 18:00 CT, so the next build
    # is still this week's, whatever the slot holds.
    assert built["next_build_at"] == datetime(2026, 9, 12, 18, 0,
                                              tzinfo=ZoneInfo("America/Chicago")).isoformat()

    # After that hour, a slot that carries a card is done for the week: the next build is the
    # same weekday and hour of the next Chicago week.
    later = NOW + timedelta(hours=6)          # Saturday 19:00 CT, still ISO week 37
    after = _slot(build_ticket(db_session, later, env_settings), "nfl", "smart")
    assert after["next_build_at"] == datetime(2026, 9, 19, 18, 0,
                                              tzinfo=ZoneInfo("America/Chicago")).isoformat()


def test_an_empty_slot_is_retried_on_the_settle_jobs_next_hour(db_session, env_settings):
    """The other `next_build_at` branch: the stage runs hourly, so a slot that recorded a reason
    at 14:05 is tried again an hour after it recorded it, not next week."""
    recorded = NOW + timedelta(hours=6)       # Saturday 19:00 CT, past the build time
    _slot_state(db_session, "nfl", "smart", reason="no_props_fresh", at=recorded)
    slot = _slot(build_ticket(db_session, recorded + timedelta(minutes=5), env_settings),
                 "nfl", "smart")
    assert slot["next_build_at"] == (recorded + timedelta(hours=1)).isoformat()


def test_the_stat_line_sentence_is_composed_in_the_sentences_module():
    """Plan review IM-13: the noun and the sentence live beside every other sentence the surface
    speaks, and the `to go` half is `needs`'s own output, unchanged."""
    assert stat_line("pass_yds", Decimal("208"), Decimal("225.0"), "17 to go", 40) == (
        "208 of 225 passing yards · 17 to go · ESPN 40 s ago")


def test_the_payload_keys_gain_ideas_and_sentences_gaps(db_session, env_settings):
    payload = build_ticket(db_session, NOW, env_settings)
    assert set(payload) == TICKET_KEYS
    assert "ideas" in TICKET_KEYS and "sentences_gaps" in TICKET_KEYS


def test_the_season_shows_the_expected_return_beside_the_confirmed_one(db_session,
                                                                       env_settings):
    """Addendum §1.4: while a computed row waits for the owner's confirmation, the season shows
    `expected` beside `returned` rather than folding an unconfirmed figure into the tile."""
    _all_legs_hit(db_session)
    season = build_ticket(db_session, NOW, env_settings)["season"]
    assert season["returned"] == 137.5
    # A decimal string, like every other money figure this phase adds (review round 1, I2).
    assert season["expected"] == "137.50"


def test_a_declined_card_is_a_grey_chip_in_the_strip_and_moves_no_figure(db_session,
                                                                        env_settings):
    """Addendum §1.4: `declined` and `expired` cards are `void` with a reason and no ledger row,
    so the offered history stays visible and the figures do not move."""
    card = _proposed(db_session, sport="nfl")
    card.status, card.declined_reason = "void", "declined"
    db_session.flush()
    payload = build_ticket(db_session, NOW, env_settings)
    season = payload["season"]
    chip = [row for row in season["strip"] if row["card_id"] == card.id][0]
    assert chip["declined_reason"] == "declined"
    assert season["staked"] == 0.0 and season["net"] == 0.0
    # C1: and it is **not** a live ticket. A card the owner refused, shown among the live slips,
    # states a stake and a payout for a bet nobody made, on the one surface whose numbers are
    # real money -- while the strip beside it says the money never moved.
    assert all(shown["card_id"] != card.id for shown in payload["cards"])


def test_an_expired_card_is_a_grey_chip_too_and_is_not_a_live_ticket(db_session, env_settings):
    """The other half of C1: `expire_cards` voids a `proposed` card nobody placed and stamps
    `declined_reason = 'expired'` (addendum §2.3). No ledger row, no placement, no live slip."""
    card = _proposed(db_session, sport="nfl")
    card.status, card.declined_reason = "void", "expired"
    db_session.flush()
    payload = build_ticket(db_session, NOW, env_settings)
    chip = [row for row in payload["season"]["strip"] if row["card_id"] == card.id][0]
    assert chip["declined_reason"] == "expired"
    assert payload["cards"] == []


def test_a_confirmed_void_is_still_a_live_ticket(db_session, env_settings):
    """The predicate excludes a *declined* void, not every void: the owner's own confirmed void
    is money that moved and keeps its slip and its VOID stamp."""
    card = _confirmed_void(db_session)
    rendered = _card_of(build_ticket(db_session, NOW, env_settings), card)
    assert rendered["stamp"] == "VOID" and rendered["card_id"] == card.id


def test_a_hung_leg_older_than_the_score_window_still_reads_hung(db_session, env_settings):
    """Review round 1, I3. `_SCORES` is bounded to 12 h, so a game that went final yesterday has
    no score row inside the window; the leg must still say it is hung -- reverting to
    `no stat yet` reads as "the game has not started" for a game that has been over all night.
    The age is the one thing dropped, because this build cannot read it."""
    leg = _live_prop_leg(db_session, value=None, status="final")
    db_session.add(GameScoreEvent(game_id=leg.game_id, ts=NOW - timedelta(hours=20),
                                  status="final", period=4, clock="0:00",
                                  home_score=24, away_score=21))
    db_session.flush()
    assert _leg_of(build_ticket(db_session, NOW, env_settings), leg)["stat_line"] == (
        "no final stat · pending")


def test_a_hung_leg_inside_the_window_still_carries_its_age(db_session, env_settings):
    leg = _hung_leg(db_session, hours=7)
    assert _leg_of(build_ticket(db_session, NOW, env_settings), leg)["stat_line"] == (
        "no final stat · pending 7 h")


def test_the_display_sanitizer_keeps_the_bet_and_still_strips_markup(db_session, env_settings):
    """Review round 1, I4. `sanitize_reason`'s class drops `+` and the middle dot, which on this
    page rewrites the bet itself ("Nussmeier 225+ passing yards" -> "Nussmeier 225 passing
    yards") and runs the builder's context line together. This page's own class keeps those two
    characters and nothing else: every markup character is still stripped."""
    from harness.dashboard.snapshots.ticket import _display

    assert _display("Nussmeier 225+ passing yards") == "Nussmeier 225+ passing yards"
    assert _display("Falcons +3.5") == "Falcons +3.5"
    assert _display("avg 262 · last 241 · ESPN") == "avg 262 · last 241 · ESPN"
    for hostile in ("<script>alert(1)</script>", 'a"b', "a'b", "a{b}c", "a\\b", "a\nb", "a\rb",
                    "a\tb", "a&b", "a;b", "a=b", "a`b"):
        rendered = _display(hostile)
        for banned in ("<", ">", '"', "'", "{", "}", "\\", "\n", "\r", "\t", "&", ";", "=", "`"):
            assert banned not in rendered
    assert len(_display("x" * 500)) == 200


def test_a_prop_legs_text_reaches_the_page_with_its_line_intact(db_session, env_settings):
    """The same fix, end to end: the fan's line and the builder's context line are rendered
    through the page's sanitizer, so the market's own name survives."""
    leg = _live_prop_leg(db_session, value=208, line="225.0")
    rendered = _leg_of(build_ticket(db_session, NOW, env_settings), leg)
    assert rendered["plain_text"] == "Nussmeier 225+ passing yards"
    assert rendered["context_text"] == "avg 262 · last 241 · ESPN"


def test_hostile_leg_text_is_still_stripped_on_the_page(db_session, env_settings):
    """The guard the shipped test pins, re-checked through the new sanitizer."""
    _teams(db_session)
    game = Game(sport="ncaaf", home_team_id=1, away_team_id=2,
                kickoff_utc=NOW - timedelta(hours=1), status="in_progress")
    db_session.add(game)
    db_session.flush()
    card = _proposed(db_session, sport="ncaaf")
    card.rationale = "<img onerror=alert(1) src=x>"
    db_session.flush()
    _ml_leg(db_session, card, game, seq=1, plain_text="<script>alert(1)</script>")
    _slot_state(db_session, "ncaaf", "smart", built=card.id)
    rendered = _slot_card(build_ticket(db_session, NOW, env_settings), card)
    assert "<" not in rendered["rationale"] and ">" not in rendered["rationale"]
    assert "=" not in rendered["rationale"]
    assert "<" not in rendered["legs"][0]["plain_text"]


def test_a_leg_with_no_recorded_probability_source_is_named_and_excluded():
    """Review round 1, M2. `p_source` is read from a column with a server default, but the
    footer's rule is about a leg that carries no probability at all: a NULL source is exactly as
    unpriced as the literal `none`, and B-C8/D4 is that the figure shown names what it left
    out."""
    from types import SimpleNamespace

    from harness.dashboard.snapshots.ticket import _footer

    card = SimpleNamespace(combined_kind="calculated", dk_combined_american=450,
                           correlated=False, true_prob_est=Decimal("0.101000"),
                           p_source_min="book_devig", hold_est=None, status="proposed",
                           dk_combined_at=NOW)
    legs = [{"plain_text": "LSU to win", "p_source": "sharp"},
            {"plain_text": "Nussmeier 225+ passing yards", "p_source": None}]
    week = {"recorded": Decimal("25.00"), "left": Decimal("25.00"),
            "budget": Decimal("50.00")}
    footer = _footer(card, legs, 60.0, week, load_config())
    assert footer["chance"].startswith("no sharp read · ")
    assert "Nussmeier 225+ passing yards not included" in footer["chance"]
