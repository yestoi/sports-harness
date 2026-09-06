import json
from pathlib import Path

from harness.matching.teams import learn_alias, load_manual_aliases, resolve_fuzzy, resolve_team, seed_teams_from_espn

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
