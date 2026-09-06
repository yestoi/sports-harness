import json
from pathlib import Path

from harness.db.models import TeamAlias
from harness.matching.teams import (AMBIGUOUS_TEAM_ID, learn_alias, load_manual_aliases, resolve_fuzzy, resolve_team,
                                    seed_teams_from_espn)

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NCAAF = json.loads((FIXD / "espn_teams_ncaaf.json").read_text())


def test_seed_and_resolve_exact(db_session):
    assert seed_teams_from_espn(db_session, "nfl", NFL) == 6
    assert seed_teams_from_espn(db_session, "ncaaf", NCAAF) == 8
    tid, src = resolve_team(db_session, "nfl", "New York Giants")
    assert tid == 19 and src == "espn_display"
    tid, src = resolve_team(db_session, "nfl", "NY Giants")
    assert tid == 19 and src == "espn_display"  # normalization expands NY
    tid, src = resolve_team(db_session, "ncaaf", "San Jose St.")
    assert tid == 23 and src == "espn_location"
    assert resolve_team(db_session, "ncaaf", "Nowhere Tech") == (None, "")


def test_learn_and_manual(db_session, tmp_path):
    seed_teams_from_espn(db_session, "nfl", NFL)
    learn_alias(db_session, "nfl", "kalshi_name", "New York G", 19)
    assert resolve_team(db_session, "nfl", "New York G") == (19, "kalshi_name")
    (tmp_path / "m.yaml").write_text("nfl:\n  kalshi_name:\n    'Jints': 19\n")
    assert load_manual_aliases(db_session, tmp_path / "m.yaml") == 1
    assert resolve_team(db_session, "nfl", "Jints") == (19, "manual:kalshi_name")


def test_fuzzy(db_session):
    seed_teams_from_espn(db_session, "ncaaf", NCAAF)
    tid, ratio = resolve_fuzzy(db_session, "ncaaf", "Arkansas Pine-Bluff Golden Lions")
    assert tid == 2029 and ratio >= 0.9
    tid, ratio = resolve_fuzzy(db_session, "ncaaf", "Zzz")
    assert tid is None


def _espn_body(*teams: dict) -> dict:
    return {"sports": [{"leagues": [{"teams": [{"team": t} for t in teams]}]}]}


TROY = _espn_body(
    {"id": "2653", "displayName": "Troy Trojans", "location": "Troy", "name": "Trojans",
     "abbreviation": "TRO", "shortDisplayName": "Troy", "slug": "troy-trojans"},
    {"id": "3237", "displayName": "Troy Vikings", "location": "Troy", "name": "Vikings",
     "abbreviation": "TRV", "shortDisplayName": "Troy", "slug": "troy-vikings"},
)


def test_colliding_espn_alias_key_becomes_ambiguous_not_last_write_wins(db_session, tmp_path):
    # C3: two teams normalizing to the same ESPN alias key must make the key unusable
    # rather than silently resolving to whichever team was seeded last.
    seed_teams_from_espn(db_session, "ncaaf", TROY)
    assert resolve_team(db_session, "ncaaf", "Troy") == (None, "")
    assert resolve_team(db_session, "ncaaf", "Troy Trojans") == (2653, "espn_display")
    assert resolve_fuzzy(db_session, "ncaaf", "Troy")[0] != AMBIGUOUS_TEAM_ID
    (tmp_path / "m.yaml").write_text("ncaaf:\n  kalshi_name:\n    'Troy': 2653\n")
    load_manual_aliases(db_session, tmp_path / "m.yaml")
    assert resolve_team(db_session, "ncaaf", "Troy") == (2653, "manual:kalshi_name")


def test_ambiguous_sentinel_does_not_flip_back_on_reseed(db_session):
    seed_teams_from_espn(db_session, "ncaaf", TROY)
    seed_teams_from_espn(db_session, "ncaaf", TROY)
    row = db_session.get(TeamAlias, ("ncaaf", "espn_location", "troy"))
    assert row.team_id == AMBIGUOUS_TEAM_ID
    assert resolve_team(db_session, "ncaaf", "Troy") == (None, "")


def test_non_espn_sources_still_overwrite(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    learn_alias(db_session, "nfl", "kalshi_name", "Big Apple", 19)
    learn_alias(db_session, "nfl", "kalshi_name", "Big Apple", 17)
    assert resolve_team(db_session, "nfl", "Big Apple") == (17, "kalshi_name")


def test_abbr_name_alias_resolves_kalshi_abbreviated_title(db_session):
    # I10: Kalshi NFL event titles read "<ABBR> <Nickname>", e.g. "NO Saints".
    seed_teams_from_espn(db_session, "nfl", NFL)
    assert resolve_team(db_session, "nfl", "NO Saints") == (18, "espn_abbr_name")


def test_shipped_manual_aliases_cover_kalshi_nfl_title_conventions(db_session):
    # I10: Kalshi truncates "New York Jets"/"New York Giants" and abbreviates Jacksonville
    # and Washington differently from ESPN, so those four need manual aliases.
    shipped = Path(__file__).parent.parent / "harness" / "matching" / "aliases_manual.yaml"
    load_manual_aliases(db_session, shipped)
    for raw_name, team_id in (("New York J", 20), ("New York G", 19), ("JAC Jaguars", 30), ("WAS Commanders", 28)):
        assert resolve_team(db_session, "nfl", raw_name) == (team_id, "manual:kalshi_name"), raw_name
