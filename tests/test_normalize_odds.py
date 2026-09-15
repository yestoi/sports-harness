import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import Game, OddsPropSnapshot, OddsSnapshot, Player
from harness.matching.games import upsert_games_from_odds
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.odds import (
    parse_odds_body, upsert_odds_rows, valid_dk_link, valid_sid,
)
from harness.normalize.players import upsert_players

FIXD = Path(__file__).parent / "fixtures"
FEAT = json.loads((FIXD / "odds_featured_nfl.json").read_text())
ALT = json.loads((FIXD / "odds_alternates_event.json").read_text())
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
T0 = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)


def test_parse_featured_rows():
    rows = parse_odds_body(FEAT, "nfl")
    assert len(rows) == 6
    h2h = [r for r in rows if r.market_type == "h2h"]
    assert {r.outcome_name for r in h2h} == {"Seattle Seahawks", "New England Patriots"}
    tot = [r for r in rows if r.market_type == "totals"]
    assert {(r.outcome_name, r.point) for r in tot} == {("Over", Decimal("44.5")), ("Under", Decimal("44.5"))}
    assert rows[0].last_update == datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)


def test_parse_alternates_dict_shape():
    rows = parse_odds_body(ALT, "nfl")
    assert rows and all(r.market_type.startswith("alternate_") for r in rows)


def test_upsert_is_idempotent_and_resolves_teams(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)  # fixture includes Seahawks(26) and Patriots(17)
    upsert_games_from_odds(db_session, "nfl", FEAT, raw_id=1)
    rows = parse_odds_body(FEAT, "nfl")
    assert upsert_odds_rows(db_session, "nfl", rows, raw_id=1, run_id=1, fetched_at=NOW).inserted == 6
    assert upsert_odds_rows(db_session, "nfl", rows, raw_id=1, run_id=1, fetched_at=NOW).inserted == 0
    snap = db_session.query(OddsSnapshot).filter_by(market_type="spreads").all()
    assert {s.point for s in snap} == {Decimal("-3.5"), Decimal("3.5")}
    assert all(s.outcome_team_id is not None and s.game_id is not None for s in snap)


def test_upsert_counts_dropped_rows_by_reason(db_session):
    # I8: a book that quoted nothing must be distinguishable from one whose rows were
    # dropped because the game or the team name was unknown.
    seed_teams_from_espn(db_session, "nfl", NFL)
    upsert_games_from_odds(db_session, "nfl", FEAT, raw_id=1)
    rows = parse_odds_body(FEAT, "nfl")
    orphan = [replace(r, event_id="no-such-event") for r in rows[:2]]
    unknown_team = [replace(r, outcome_name="Nowhere Tech") for r in rows if r.market_type == "h2h"]
    res = upsert_odds_rows(db_session, "nfl", orphan + unknown_team, raw_id=2, run_id=1, fetched_at=NOW)
    assert (res.inserted, res.dropped_unknown_game, res.dropped_unresolved_team) == (0, 2, 2)


def _seed_game(db_session, event_id: str):
    """One game with this Odds API event id, seeded the way this file already seeds: the NFL
    team fixture first, then the games upsert, both of which the file imports."""
    seed_teams_from_espn(db_session, "nfl", NFL)
    upsert_games_from_odds(db_session, "nfl", [{"id": event_id,
                                                "commence_time": "2026-09-13T23:00:00Z",
                                                "home_team": "New England Patriots",
                                                "away_team": "Seattle Seahawks"}], raw_id=1)
    db_session.flush()
    return db_session.query(Game).filter_by(odds_api_event_id=event_id).one()


PROP_BODY = {
    "id": "evt-1",
    "bookmakers": [{"key": "draftkings", "last_update": "2026-09-13T17:00:00Z", "markets": [
        {"key": "player_pass_yds", "outcomes": [
            {"name": "Over", "description": "Jayden Daniels", "point": 249.5, "price": 1.87,
             "link": "https://sportsbook.draftkings.com/event/123?outcome=abc&x=1",
             "sid": "0QA123#4567"},
            {"name": "Under", "description": "Jayden Daniels", "point": 249.5, "price": 1.95,
             "link": "javascript:alert(1)", "sid": "0QA123#4568"}]},
        {"key": "player_receptions_alternate", "outcomes": [
            {"name": "Over", "description": "Malik Nabers", "point": 5.5, "price": 2.40,
             "link": "https://evil.example.com/event/123", "sid": "bad sid!"}]},
        {"key": "player_anytime_td", "outcomes": [
            {"name": "Yes", "description": "Malik Nabers", "price": 2.10, "link": None,
             "sid": None}]}]}]}


def test_the_normalizer_maps_venue_prop_keys_to_short_internal_keys():
    """Expected: `prop:pass_yds`, `prop:receptions:alt`, `prop:anytime_td` -- never the venue's
    own key.

    Computed independently: `odds_prop_snapshots.market_type` is String(24) and
    `player_reception_yds_alternate` is 30 characters, so storing the venue key would need the
    bulk-table widening D22 refused. The longest internal key, `prop:receptions:alt`, is 19.
    """
    rows = parse_odds_body(PROP_BODY, "nfl")
    assert {r.market_type for r in rows} == {"prop:pass_yds", "prop:receptions:alt",
                                             "prop:anytime_td"}
    assert max(len(r.market_type) for r in rows) <= 24


def test_the_normalizer_keeps_the_player_description_and_the_outcome_side():
    """Expected: `description` travels to the row and `Over`/`Under`/`Yes` resolve to a side.

    A prop outcome names a player, not a team, so the team resolver must never be called for
    one (addendum §3.3): an unresolved name here would drop a real row as `dropped_unresolved_team`.
    """
    rows = {(r.market_type, r.outcome_name): r for r in parse_odds_body(PROP_BODY, "nfl")}
    assert rows[("prop:pass_yds", "Over")].description == "Jayden Daniels"
    assert rows[("prop:anytime_td", "Yes")].description == "Malik Nabers"


def test_only_an_https_draftkings_link_under_300_characters_is_stored():
    """Expected: the `javascript:` link and the wrong-host link are dropped to None; the query
    string of the good link is preserved byte for byte.

    Computed independently of the code: the rule is an allowlist, so the three cases are the
    scheme, the host and the length. The query string carries the venue's own outcome id and a
    normalizer that re-encoded it would produce a link that opens the wrong selection.
    """
    assert valid_dk_link("https://sportsbook.draftkings.com/event/123?outcome=abc&x=1") == (
        "https://sportsbook.draftkings.com/event/123?outcome=abc&x=1")
    assert valid_dk_link("https://mi.draftkings.com/event/9") == "https://mi.draftkings.com/event/9"
    assert valid_dk_link("javascript:alert(1)") is None
    assert valid_dk_link("http://sportsbook.draftkings.com/event/123") is None
    assert valid_dk_link("https://evil.example.com/event/123") is None
    assert valid_dk_link("https://sportsbook.draftkings.com/e/" + "a" * 300) is None
    assert valid_dk_link(None) is None


def test_a_sid_must_match_the_pattern_or_it_is_dropped():
    assert valid_sid("0QA123#4567") is None       # '#' is not in the class
    assert valid_sid("0QA123_45-67.a:b") == "0QA123_45-67.a:b"
    assert valid_sid("a" * 65) is None
    assert valid_sid(None) is None


COLLISION_BODY = {
    "id": "evt-1",
    "bookmakers": [{"key": "draftkings", "last_update": "2026-09-13T17:00:00Z", "markets": [
        {"key": "player_anytime_td", "outcomes": [
            {"name": "Yes", "description": "Malik Nabers", "price": 2.10},
            {"name": "Yes", "description": "Wan'Dale Robinson", "price": 3.40}]},
        {"key": "player_receptions", "outcomes": [
            {"name": "Over", "description": "Malik Nabers", "point": 3.5, "price": 1.80},
            {"name": "Over", "description": "Wan'Dale Robinson", "point": 3.5, "price": 1.95}]}]}]}


def test_prop_rows_are_stored_with_the_player_name_and_a_rejected_link_is_counted(db_session):
    """Expected: four rows stored, `link_rejected == 2`, `player_id` null (Task 3 resolves it),
    and no call to the team resolver."""
    game = _seed_game(db_session, "evt-1")
    rows = parse_odds_body(PROP_BODY, "nfl")
    result = upsert_odds_rows(db_session, "nfl", rows, raw_id=1, run_id=1, fetched_at=T0)
    assert result.inserted == 4 and result.link_rejected == 2
    stored = db_session.query(OddsPropSnapshot).filter_by(game_id=game.id).all()
    assert {row.player_name for row in stored} == {"Jayden Daniels", "Malik Nabers"}
    assert all(row.player_id is None for row in stored)
    kept = [row for row in stored if row.link is not None]
    assert len(kept) == 1
    assert kept[0].link.endswith("?outcome=abc&x=1")


def test_two_players_in_one_market_both_survive_the_upsert(db_session):
    """Expected: four rows, not two, and not one of them in `odds_snapshots` (D23).

    Computed independently of the code: inside one fetch the two `anytime_td` outcomes share
    `(raw_id, book, market_type, outcome_team_id=null, outcome_side='yes', point=null)` and the
    two `receptions` outcomes share the same tuple with `point = 3.5`. `uq_odds_snapshot_row`
    keys on exactly that tuple and carries no `where` clause, so in `odds_snapshots` one of each
    pair is dropped (or the insert raises) whatever conflict target the statement names. In
    `odds_prop_snapshots` the key is `uq_odds_prop_row`, which carries `player_name`, and all
    four survive.
    """
    _seed_game(db_session, "evt-1")
    rows = parse_odds_body(COLLISION_BODY, "nfl")
    result = upsert_odds_rows(db_session, "nfl", rows, raw_id=7, run_id=7, fetched_at=T0)
    assert result.inserted == 4
    names = [row.player_name for row in db_session.query(OddsPropSnapshot).all()]
    assert sorted(names) == ["Malik Nabers", "Malik Nabers", "Wan'Dale Robinson",
                             "Wan'Dale Robinson"]
    assert db_session.query(OddsSnapshot).count() == 0


def test_re_normalizing_the_same_body_stores_nothing_new(db_session):
    """The conflict target must still dedupe: a repeated normalize of one `raw_id` is a no-op,
    which is what `on_conflict_do_nothing` is there for."""
    _seed_game(db_session, "evt-1")
    rows = parse_odds_body(COLLISION_BODY, "nfl")
    upsert_odds_rows(db_session, "nfl", rows, raw_id=7, run_id=7, fetched_at=T0)
    again = upsert_odds_rows(db_session, "nfl", rows, raw_id=7, run_id=7, fetched_at=T0)
    assert again.inserted == 0


ROSTERED = [("4430807", "Malik Nabers"), ("4431611", "Wan'Dale Robinson")]


def test_a_prop_outcome_for_a_rostered_player_stores_its_player_id(db_session):
    """Expected: `player_id` is the `players` row's id, not the ESPN athlete id.

    Addendum §3.3: the outcome's `description` is matched to a player of the game's two teams.
    Without this, `ix_odds_prop_lookup` (partial on `player_id is not null`) indexes nothing and
    `newest_dk_prop_price` can never find a row (plan review CR-1).
    """
    game = _seed_game(db_session, "evt-1")
    upsert_players(db_session, "nfl", team_id=17, rows=[
        {"espn_id": eid, "name": name, "position": "WR"} for eid, name in ROSTERED], now=T0)
    db_session.flush()
    rows = parse_odds_body(COLLISION_BODY, "nfl")
    result = upsert_odds_rows(db_session, "nfl", rows, raw_id=9, run_id=9, fetched_at=T0)
    stored = db_session.query(OddsPropSnapshot).filter_by(game_id=game.id).all()
    assert result.prop_unmatched == 0
    assert all(row.player_id is not None for row in stored)
    nabers = db_session.query(Player).filter_by(espn_id="4430807").one()
    assert {row.player_id for row in stored if row.player_name == "Malik Nabers"} == {nabers.id}


def test_an_ambiguous_or_absent_name_stores_null_and_is_counted(db_session):
    """D14: ambiguity never picks. Two rostered players normalizing alike leave `player_id`
    null and count `prop_unmatched`; the leg is then never built (§4.2)."""
    _seed_game(db_session, "evt-1")
    upsert_players(db_session, "nfl", team_id=17, rows=[
        {"espn_id": "1", "name": "Malik Nabers", "position": "WR"},
        {"espn_id": "2", "name": "Malik Nabers Jr.", "position": "WR"}], now=T0)
    db_session.flush()
    rows = parse_odds_body(COLLISION_BODY, "nfl")
    result = upsert_odds_rows(db_session, "nfl", rows, raw_id=11, run_id=11, fetched_at=T0)
    stored = db_session.query(OddsPropSnapshot).all()
    assert result.prop_unmatched == 4          # two Nabers rows ambiguous, two Robinson absent
    assert all(row.player_id is None for row in stored)
    assert result.inserted == 4                # the rows are still stored, keyed by player_name
