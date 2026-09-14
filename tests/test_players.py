"""ESPN player stats, rosters, game logs and the identity map (addendum §4.1, §4.2).

No network: every case is a trimmed body under `tests/fixtures/`. `T0` is fixed and tz-aware,
so an age or a `updated_at` never depends on when the suite runs.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from harness.db.models import Player
from harness.normalize.players import (StatLine, match_player, normalize_name, parse_gamelog,
                                       parse_roster, parse_scoring_tds, parse_summary_stats,
                                       upsert_players)

T0 = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _body(name):
    return json.loads((FIXTURES / name).read_text())


def test_the_summary_yields_one_stat_line_per_carded_stat():
    """Expected: passing yards, rushing yards, receptions and receiving yards come out as
    `StatLine`s keyed by the athlete's own ESPN id.

    Computed independently of the code: the fixture's `boxscore.players[0].statistics` block
    lists `passing` with `YDS` at index 1 of its `keys`, so the value is the athlete's `stats[1]`
    -- the parser must read the category's own key order, never a fixed column.
    """
    lines = {(l.player_espn_id, l.stat): l.value for l in parse_summary_stats(_body("espn_summary_nfl.json"))}
    assert lines[("4426348", "pass_yds")] == Decimal("208")
    assert lines[("4430807", "rec_yds")] == Decimal("64")
    assert lines[("4430807", "receptions")] == Decimal("5")


def test_a_return_touchdown_counts_and_a_passing_touchdown_does_not():
    """Addendum §4.3 and the recorded `market_defs.anytime_td` rule (D19): any touchdown the
    player scores counts -- rushing, receiving, return or recovery -- and a passing touchdown
    counts for the receiver, never the passer."""
    scored = parse_scoring_tds(_body("espn_summary_nfl.json"))
    assert scored["4430807"] == 1          # the receiver of the touchdown pass
    assert "4426348" not in scored          # the passer
    assert scored["4241479"] == 1           # the kick returner


def test_an_undecidable_play_is_reported_rather_than_scored():
    """A play whose type the parser cannot decide leaves the leg pending, never a miss
    (addendum §4.3). The sentinel is a count, so the caller can log it."""
    body = _body("espn_summary_nfl.json")
    body["scoringPlays"].append({"type": {"abbreviation": "??"}, "text": "unclear",
                                 "athlete": {"id": "9999"}})
    scored = parse_scoring_tds(body)
    assert scored["__undecidable__"] == 1
    assert "9999" not in scored


@pytest.mark.parametrize("raw,expected", [
    ("Ja'Marr Chase", "jamarr chase"),
    ("Odell Beckham Jr.", "odell beckham"),
    ("Robert Griffin III", "robert griffin"),
    ("Amon-Ra St. Brown", "amon ra st brown"),
    ("JOSÉ MARÍA", "jose maria"),
])
def test_the_name_normalizer_folds_case_diacritics_punctuation_and_suffixes(raw, expected):
    assert normalize_name(raw) == expected


def test_exactly_one_candidate_matches_or_the_outcome_is_unmatched():
    """Expected: an initial matches a first name, and an ambiguity never picks (D14).

    Computed independently: the rule is "exactly one candidate matches or the outcome is
    `player_unmatched`", so two rostered players normalizing to the same key must return None
    even though one of them is certainly the right answer. Ambiguity never picks.
    """
    roster = [(1, "Malik Nabers"), (2, "Marvin Harrison Jr."), (3, "M. Nabers")]
    assert match_player("Malik Nabers", roster[:2]) == 1
    assert match_player("M. Harrison", roster[:2]) == 2
    assert match_player("Malik Nabers", roster) is None      # two candidates normalize alike
    assert match_player("Nobody At All", roster) is None


def test_the_roster_upsert_is_idempotent_on_sport_and_espn_id(db_session):
    rows = parse_roster(_body("espn_roster_nfl.json"))
    assert upsert_players(db_session, "nfl", team_id=17, rows=rows, now=T0) == len(rows)
    assert upsert_players(db_session, "nfl", team_id=17, rows=rows, now=T0) == len(rows)
    assert db_session.query(Player).filter_by(sport="nfl").count() == len(rows)


def test_the_game_log_parser_returns_per_stat_values_newest_first():
    """Until Task 18a records the endpoint's real shape, an unrecognised body yields `{}` and
    the caller's context line reads `no season data yet` (addendum §4.1). The fixture here is
    the shape the report will confirm or replace."""
    log = parse_gamelog(_body("espn_gamelog_nfl.json"))
    assert log["pass_yds"][:2] == [Decimal("241"), Decimal("283")]
    assert parse_gamelog({"nothing": "useful"}) == {}
