# Recorded from live Kalshi data captured into tests/fixtures/kalshi_markets_typed.json
# (Task 1, 2026-09-06):
#
# Total-market `title` wording differs by sport and does NOT include team names:
#   - KXNFLTOTAL:    "Will there be over 63.5 points scored?"
#                    "Will there be over 60.5 points scored?"
#   - KXNCAAFTOTAL:  "Over 73.5 points scored"
#                    "Over 70.5 points scored"
#
# Totals markets do NOT carry a `custom_strike` field (absent entirely; the strike
# is only encoded in the ticker suffix, e.g. "-64", and in the title's point number).
# By contrast, KXNFLGAME/KXNFLSPREAD/KXNCAAFGAME/KXNCAAFSPREAD markets DO carry
# `custom_strike": {"football_team": "<uuid>"}` identifying the team side.
#
# Task 5 fills in the matching tests below.


import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import Game
from harness.matching.kalshi import classify_market, match_event, parse_event_title, side_team_id_for
from harness.matching.teams import seed_teams_from_espn

FIXD = Path(__file__).parent / "fixtures"
NCAAF = json.loads((FIXD / "espn_teams_ncaaf.json").read_text())
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())


def _espn_body(*teams: dict) -> dict:
    return {"sports": [{"leagues": [{"teams": [{"team": t} for t in teams]}]}]}


# Teams the shipped NFL fixture doesn't carry, needed by the ticker-code matching tests
# below (match-report 2026-09-07, gap: colliding "Los Angeles"/"New York" city aliases).
# The fixture already has the Rams (14), Chargers (24), Giants (19) and Jets (20).
EXTRA_NFL = _espn_body(
    {"id": "25", "displayName": "San Francisco 49ers", "location": "San Francisco", "name": "49ers",
     "abbreviation": "SF", "shortDisplayName": "49ers", "slug": "san-francisco-49ers"},
    {"id": "6", "displayName": "Dallas Cowboys", "location": "Dallas", "name": "Cowboys",
     "abbreviation": "DAL", "shortDisplayName": "Cowboys", "slug": "dallas-cowboys"},
    {"id": "23", "displayName": "Pittsburgh Steelers", "location": "Pittsburgh", "name": "Steelers",
     "abbreviation": "PIT", "shortDisplayName": "Steelers", "slug": "pittsburgh-steelers"},
    {"id": "9", "displayName": "Green Bay Packers", "location": "Green Bay", "name": "Packers",
     "abbreviation": "GB", "shortDisplayName": "Packers", "slug": "green-bay-packers"},
    {"id": "16", "displayName": "Minnesota Vikings", "location": "Minnesota", "name": "Vikings",
     "abbreviation": "MIN", "shortDisplayName": "Vikings", "slug": "minnesota-vikings"},
)


def test_parse_event_title():
    assert parse_event_title("Alabama St. vs Troy") == ("Alabama St.", "Troy")
    assert parse_event_title("NY Giants vs LA Rams") == ("NY Giants", "LA Rams")
    assert parse_event_title("Just one") is None
    assert parse_event_title("Denver vs Kansas City: Spread") == ("Denver", "Kansas City")
    assert parse_event_title("Alabama vs Kentucky: Total") == ("Alabama", "Kentucky")


def test_classify_market_from_fixture():
    ms = json.loads((FIXD / "kalshi_markets_typed.json").read_text())["markets"]
    by_series = {m["event_ticker"].split("-")[0]: m for m in ms}
    mc = classify_market(by_series["KXNFLSPREAD"])
    assert mc.market_type == "spread" and mc.threshold == Decimal(str(by_series["KXNFLSPREAD"]["floor_strike"]))
    assert mc.side_kind == "team" and mc.side_name
    mc = classify_market(by_series["KXNFLTOTAL"])
    assert mc.market_type == "total" and mc.side_kind == "over" and mc.threshold is not None
    mc = classify_market(by_series["KXNCAAFGAME"])
    assert mc.market_type == "moneyline" and mc.side_name == by_series["KXNCAAFGAME"]["yes_sub_title"]
    assert classify_market({"event_ticker": "KXWEIRD-1", "ticker": "x"}) is None


def test_match_event_exact_and_learns_alias(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    kick = datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)
    g = Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=kick, odds_api_event_id="ev1")
    db_session.add(g)
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "title": "NY Giants vs LA Rams"}, date(2026, 9, 21))
    assert em.game_id == g.id and em.confidence == Decimal("1.00") and em.reason.startswith("pair+date exact")
    from harness.matching.teams import resolve_team
    assert resolve_team(db_session, "nfl", "NY Giants") == (19, "kalshi_name")


def test_match_event_unresolved_and_ambiguous(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    em = match_event(db_session, "nfl", {"event_ticker": "X-26SEP21AB", "title": "NY Giants vs Nowhere"}, date(2026, 9, 21))
    assert em.game_id is None and "unresolved" in em.reason
    kick = datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)
    db_session.add_all([Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=kick),
                        Game(sport="nfl", home_team_id=19, away_team_id=14, kickoff_utc=kick)])
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "X-26SEP21AB", "title": "NY Giants vs LA Rams"}, date(2026, 9, 21))
    assert em.game_id is None and em.reason.startswith("ambiguous")


def test_side_team_id_for_uses_game_teams_only(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    kick = datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)
    g = Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=kick)
    db_session.add(g)
    db_session.flush()
    from harness.matching.teams import learn_alias, resolve_team
    # truncated Kalshi name resolves by prefix against the game's two teams
    mc = classify_market({"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "ticker": "KXNFLGAME-26SEP21NYGLAR-NYG",
                          "yes_sub_title": "New York G", "title": "New York G wins"})
    assert side_team_id_for(db_session, "nfl", mc, g) == 19
    assert resolve_team(db_session, "nfl", "New York G") == (19, "kalshi_name")  # learned
    # a name that matches neither team returns None; a known uuid alias wins
    mc2 = classify_market({"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "ticker": "x", "yes_sub_title": "Jints",
                           "title": "Jints wins", "custom_strike": {"football_team": "uuid-giants"}})
    assert side_team_id_for(db_session, "nfl", mc2, g) is None
    learn_alias(db_session, "nfl", "kalshi_uuid", "uuid-giants", 19)
    assert side_team_id_for(db_session, "nfl", mc2, g) == 19
    # a prefix shared by both teams is ambiguous
    g2 = Game(sport="nfl", home_team_id=19, away_team_id=17, kickoff_utc=kick)  # Giants vs Patriots: "new " prefixes both
    db_session.add(g2)
    db_session.flush()
    mc3 = classify_market({"event_ticker": "KXNFLGAME-26SEP21NYGNE", "ticker": "y", "yes_sub_title": "New Yo", "title": "New Yo wins"})
    assert side_team_id_for(db_session, "nfl", mc3, g2) == 19  # "new yo" prefixes only the Giants
    mc4 = classify_market({"event_ticker": "KXNFLGAME-26SEP21NYGNE", "ticker": "z", "yes_sub_title": "New En", "title": "New En wins"})
    assert side_team_id_for(db_session, "nfl", mc4, g2) == 17


def test_match_event_resolves_colliding_city_via_ticker_code(db_session):
    # I9 (2026-09-07 match-report gap): "Los Angeles" is the ESPN location of both the
    # Rams (14) and the Chargers (24), so it resolves to nothing on its own. The event
    # ticker's code segment ("SFLAR") carries the answer; a Chargers game on the same
    # date makes the pair genuinely ambiguous without it.
    seed_teams_from_espn(db_session, "nfl", NFL)
    seed_teams_from_espn(db_session, "nfl", EXTRA_NFL)
    kick = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    rams_game = Game(sport="nfl", home_team_id=25, away_team_id=14, kickoff_utc=kick)
    chargers_game = Game(sport="nfl", home_team_id=25, away_team_id=24, kickoff_utc=kick)
    db_session.add_all([rams_game, chargers_game])
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "KXNFLSPREAD-26SEP10SFLAR",
                                          "title": "San Francisco vs Los Angeles: Spread"}, date(2026, 9, 10))
    assert em.game_id == rams_game.id
    assert em.confidence == Decimal("1.00")
    assert em.reason == "pair+date exact (code)"


def test_match_event_resolves_colliding_city_via_ticker_code_multiple_events(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    seed_teams_from_espn(db_session, "nfl", EXTRA_NFL)
    kick = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
    cowboys_giants = Game(sport="nfl", home_team_id=19, away_team_id=6, kickoff_utc=kick)
    steelers_jets = Game(sport="nfl", home_team_id=20, away_team_id=23, kickoff_utc=kick)
    db_session.add_all([cowboys_giants, steelers_jets])
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "KXNFLGAME-26SEP13DALNYG",
                                          "title": "Dallas vs New York"}, date(2026, 9, 13))
    assert em.game_id == cowboys_giants.id and em.reason == "pair+date exact (code)"
    em2 = match_event(db_session, "nfl", {"event_ticker": "KXNFLGAME-26SEP13PITNYJ",
                                           "title": "Pittsburgh vs New York"}, date(2026, 9, 13))
    assert em2.game_id == steelers_jets.id and em2.reason == "pair+date exact (code)"


def test_match_event_code_resolution_survives_title_code_order_mismatch(db_session):
    # Title order and ticker-code order disagree here; the side that already resolved by
    # name ("San Francisco") pins its own code, and the bare city takes what's left.
    seed_teams_from_espn(db_session, "nfl", NFL)
    seed_teams_from_espn(db_session, "nfl", EXTRA_NFL)
    kick = datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc)
    rams_game = Game(sport="nfl", home_team_id=25, away_team_id=14, kickoff_utc=kick)
    chargers_game = Game(sport="nfl", home_team_id=25, away_team_id=24, kickoff_utc=kick)
    db_session.add_all([rams_game, chargers_game])
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "KXNFLSPREAD-26SEP10SFLAR",
                                          "title": "Los Angeles vs San Francisco: Spread"}, date(2026, 9, 10))
    assert em.game_id == rams_game.id
    assert em.reason == "pair+date exact (code)"


def test_match_event_unknown_code_stays_unresolved(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    seed_teams_from_espn(db_session, "nfl", EXTRA_NFL)
    kick = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
    db_session.add(Game(sport="nfl", home_team_id=19, away_team_id=6, kickoff_utc=kick))
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "KXNFLGAME-26SEP13DALXYZ",
                                          "title": "Dallas vs New York"}, date(2026, 9, 13))
    assert em.game_id is None and em.reason == "unresolved: New York"


def test_match_event_unambiguous_title_never_consults_ticker_code(db_session):
    # Green Bay and Minnesota both resolve cleanly by name; even a ticker whose code
    # segment is garbage (or would resolve to the wrong teams) must not be consulted.
    seed_teams_from_espn(db_session, "nfl", NFL)
    seed_teams_from_espn(db_session, "nfl", EXTRA_NFL)
    kick = datetime(2026, 9, 14, 17, 0, tzinfo=timezone.utc)
    g = Game(sport="nfl", home_team_id=9, away_team_id=16, kickoff_utc=kick)
    db_session.add(g)
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "KXNFLGAME-26SEP14SFLAR",
                                          "title": "Green Bay vs Minnesota"}, date(2026, 9, 14))
    assert em.game_id == g.id and em.reason == "pair+date exact"


def test_match_event_code_resolution_requires_a_unique_split(db_session):
    # I9 fix round 1: if the ticker's code string admits more than one valid partition
    # into two known codes, that's a new ambiguity, not a resolution -- the code path
    # must not silently pick one. "ABCDE" splits both as "AB"|"CDE" and "ABC"|"DE"; here
    # both "AB" and "ABC" resolve to the same known team (Alpha, so the already-name-
    # resolved left side is consistent either way) but "CDE" and "DE" resolve to two
    # DIFFERENT teams, so the right side (bare, unresolved by name) has two conflicting
    # candidates. A pre-fix implementation would return the first split's game at
    # confidence 1.00; this must instead fall back to the pre-code-path "unresolved"
    # result and learn nothing.
    from harness.matching.teams import learn_alias, resolve_team

    learn_alias(db_session, "nfl", "kalshi_name", "Alpha", 901)
    learn_alias(db_session, "nfl", "kalshi_code", "AB", 901)
    learn_alias(db_session, "nfl", "kalshi_code", "ABC", 901)
    learn_alias(db_session, "nfl", "kalshi_code", "CDE", 902)
    learn_alias(db_session, "nfl", "kalshi_code", "DE", 903)
    kick = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
    # The trap: a pre-fix implementation tries the "AB"|"CDE" split first and would
    # confidently return this game.
    trap_game = Game(sport="nfl", home_team_id=901, away_team_id=902, kickoff_utc=kick)
    db_session.add(trap_game)
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "KXNFLGAME-26SEP13ABCDE",
                                          "title": "Alpha vs Bravo"}, date(2026, 9, 13))
    assert em.game_id is None
    assert em.reason == "unresolved: Bravo"
    assert resolve_team(db_session, "nfl", "Bravo") == (None, "")
