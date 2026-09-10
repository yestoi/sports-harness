"""The stadium roster: coverage, the roof vocabulary, neutral sites, and the lookup order."""
from datetime import date

import pytest

from harness.weather.stadiums import (ROOFS, Stadium, is_outdoor, load_missing, load_neutral_sites,
                                      load_roster, load_stadiums, stadium_for)


def test_every_roster_team_is_either_placed_or_explained():
    """The addendum's union: a team is in stadiums.yaml or in stadiums_missing.txt with a
    reason, and never in both. A team in neither is a game that would be silently skipped
    forever with nothing recording why."""
    roster = {(sport, team_id) for sport, team_id, _, _ in load_roster()}
    placed = set(load_stadiums())
    missing = set(load_missing())
    assert placed & missing == set(), sorted(placed & missing)
    uncovered = roster - placed - missing
    assert uncovered == set(), sorted(uncovered)
    assert (placed | missing) - roster == set(), sorted((placed | missing) - roster)


def test_every_missing_row_carries_a_reason():
    for key, reason in load_missing().items():
        assert reason.strip(), f"{key} has no reason"


def test_every_stadium_row_is_well_formed():
    for key, stadium in load_stadiums().items():
        assert stadium.roof in ROOFS, f"{key} roof={stadium.roof!r}"
        assert -90.0 <= stadium.lat <= 90.0, f"{key} lat={stadium.lat}"
        assert -180.0 <= stadium.lon <= 180.0, f"{key} lon={stadium.lon}"
        assert stadium.source.startswith("http"), f"{key} source={stadium.source!r}"
        assert stadium.name.strip() and stadium.abbreviation.strip()


def test_the_united_states_bounding_box_catches_a_transposed_coordinate():
    """A swapped lat/lon is the failure mode a hand-written table actually has, and it puts a
    US stadium in the Indian Ocean without tripping any range check."""
    for key, stadium in load_stadiums().items():
        assert 18.0 <= stadium.lat <= 72.0, f"{key} lat={stadium.lat} is outside the US box"
        assert -180.0 <= stadium.lon <= -66.0, f"{key} lon={stadium.lon} is outside the US box"


def test_a_neutral_site_overrides_the_home_team_s_stadium():
    neutral = load_neutral_sites()
    assert neutral, "neutral_sites.yaml is empty; at least the season's known ones belong in it"
    (sport, home, away, day), site = next(iter(sorted(neutral.items())))
    assert stadium_for(sport, home, away, day) == site


def test_the_lookup_falls_back_to_the_home_team_on_any_other_date():
    stadiums = load_stadiums()
    (sport, team_id), home = next(iter(sorted(stadiums.items())))
    assert stadium_for(sport, team_id, 999_999, date(2026, 12, 25)) == home


def test_an_unknown_team_resolves_to_none():
    assert stadium_for("nfl", 999_999, 888_888, date(2026, 9, 20)) is None


@pytest.mark.parametrize("roof,outdoor", [("open", True), ("retractable", True), ("dome", False)])
def test_a_dome_is_not_outdoor_and_a_retractable_roof_is(roof, outdoor):
    """D2: a dome is skipped, a retractable roof is fetched and labelled. The label is what a
    later reader needs to tell a 40-degree open-air game from a 40-degree forecast over a closed
    roof."""
    stadium = Stadium(sport="nfl", team_id=1, abbreviation="X", name="X", lat=30.0, lon=-90.0,
                      roof=roof, source="https://example.org")
    assert is_outdoor(stadium) is outdoor


def test_the_yaml_files_ship_inside_the_package():
    """`make deploy-nas` tars `harness`, and the Dockerfile COPYs it, so a YAML under
    harness/weather/ reaches the image -- but only if package-data lists it, or `pip install .`
    drops it."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    entries = data["tool"]["setuptools"]["package-data"]["harness"]
    assert "weather/*.yaml" in entries
    assert "weather/*.txt" in entries
