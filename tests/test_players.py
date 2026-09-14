"""ESPN player stats, rosters, game logs and the identity map (addendum §4.1, §4.2).

No network: every case is a trimmed body under `tests/fixtures/`. `T0` is fixed and tz-aware,
so an age or a `updated_at` never depends on when the suite runs.
"""
import json
import logging
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
    """The measured v3 shape (Task 18a, journal 184 item 3): a top-level `names` column list and
    `seasonTypes[].categories[].events[].stats`, newest first. The fixture carries that body's
    own column order for a quarterback -- `passingYards` at index 2, `rushingYards` at 12 -- so
    a parser reading a fixed column would fail here.
    """
    log = parse_gamelog(_body("espn_gamelog_nfl.json"))
    assert log["pass_yds"][:2] == [Decimal("241"), Decimal("283")]
    assert log["rush_yds"][:2] == [Decimal("47"), Decimal("19")]
    assert parse_gamelog({"nothing": "useful"}) == {}


#: The measured receiver body's own column order (Task 18a): a different `names` order from the
#: quarterback one above, and an absent stat written `-`. One event, no athlete name in it.
WR_GAMELOG = {
    "names": ["receptions", "receivingTargets", "receivingYards", "yardsPerReception",
              "receivingTouchdowns", "longReception", "rushingAttempts", "rushingYards",
              "yardsPerRushAttempt", "longRushing", "rushingTouchdowns", "fumbles",
              "fumblesLost", "fumblesForced", "kicksBlocked"],
    "labels": ["REC", "TGTS", "YDS", "AVG", "TD", "LNG", "CAR", "YDS", "AVG", "LNG", "TD",
               "FUM", "LST", "FF", "KB"],
    "seasonTypes": [{"displayName": "2026 Regular Season", "categories": [
        {"type": "event", "events": [
            {"eventId": "401872656",
             "stats": ["8", "11", "122", "15.3", "1", "45", "0", "0", "0.0", "0", "0", "0", "0",
                       "-", "-"]}]}]}],
}


def test_a_receivers_game_log_reads_receptions_and_receiving_yards():
    """The same parser on the second measured shape: `receivingYards` is index 2 here and
    `passingYards` is absent entirely, so the stat is found by ESPN's own name and never by
    position. `-` (an absent stat) is skipped, not read as a zero.
    """
    log = parse_gamelog(WR_GAMELOG)
    assert log["receptions"] == [Decimal("8")]
    assert log["rec_yds"] == [Decimal("122")]
    assert "pass_yds" not in log
    assert log["rush_yds"] == [Decimal("0")]


def test_a_player_with_no_games_is_the_no_season_data_yet_shape():
    """The third measured shape: a player with no games answers `{"filters": [...]}` and nothing
    else -- no `names`, no `seasonTypes`. That is the expected path, not an error (addendum
    §4.1): the caller's line reads `no season data yet`.
    """
    assert parse_gamelog(_body("espn_gamelog_nfl_empty.json")) == {}


def test_an_exception_inside_the_walk_is_logged_and_yields_no_season_data(caplog):
    """The v3 host is browser-facing and less stable than the recorder's (journal 184 item 3),
    so a shape that breaks the walk is the empty mapping plus one WARNING naming the athlete --
    never an exception that reaches a tick."""
    class _Explodes(list):
        def __len__(self):
            raise RuntimeError("bad shape")

    body = {"names": ["passingYards"], "seasonTypes": [{"categories": [
        {"events": [{"stats": _Explodes()}]}]}]}
    with caplog.at_level(logging.WARNING):
        assert parse_gamelog(body, athlete_id="4426348") == {}
    assert "4426348" in caplog.text
