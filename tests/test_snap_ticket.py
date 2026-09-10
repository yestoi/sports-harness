"""The Ticket builder: the between-cards state this phase ships, and the card states phase 5c
will write into."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from harness.dashboard.snapshots import ticket
from harness.dashboard.snapshots.ticket import TICKET_KEYS, WEEKLY_BUDGET, build_ticket
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
    assert payload["between"]["budget_left"] == float(WEEKLY_BUDGET)
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
