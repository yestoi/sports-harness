"""§1.9(b): the episode rule, frozen before the run and stored per row.

Every number here is the addendum's own: `gap_rule_s = max(600, 3 x cadence_in_force)`, and
§1.9's expected result -- sightings at 12:00, 12:02, 12:04 and 14:00 on a 120 s cadence give
**2** episodes and 4 candidate rows.
"""
from datetime import datetime, timedelta, timezone

import pytest

from harness.experiments.execution_viability import episodes

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def at(minutes=0, seconds=0):
    return NOW + timedelta(minutes=minutes, seconds=seconds)


@pytest.mark.parametrize("cadence,expected", [
    (120, 600),      # 3 x 120 = 360, under the floor: the floor wins
    (200, 600),      # exactly at the floor
    (201, 603),      # the first cadence where three intervals beat it
    (300, 900),
    (900, 2700),
])
def test_the_gap_rule_is_the_floor_or_three_cadences(cadence, expected):
    assert episodes.gap_rule_s(cadence) == expected


def test_the_addendums_own_expected_result():
    # 12:00, 12:02 and 12:04 are 120 s apart, inside the 600 s rule; the 116-minute hole to
    # 14:00 exceeds it and opens a second episode. Four candidate rows either way.
    found = episodes.episodes_for([at(0), at(2), at(4), at(120)], cadence_in_force=120)
    assert len(found) == 2
    assert sum(episode.candidates for episode in found) == 4
    assert [episode.started_at for episode in found] == [at(0), at(120)]
    assert [episode.ended_at for episode in found] == [at(4), at(120)]


def test_a_re_entry_inside_the_hole_is_the_same_episode_and_after_it_a_new_one():
    inside = episodes.episodes_for(
        [{"at": at(0), "kind": "candidate"}, {"at": at(0, 30), "kind": "cancel"},
         {"at": at(9), "kind": "re_entry"}], cadence_in_force=120)     # 540 s < 600 s
    assert len(inside) == 1 and inside[0].re_entries == 1
    outside = episodes.episodes_for(
        [{"at": at(0), "kind": "candidate"}, {"at": at(0, 30), "kind": "cancel"},
         {"at": at(11), "kind": "re_entry"}], cadence_in_force=120)    # 630 s > 600 s
    assert len(outside) == 2
    assert [episode.re_entries for episode in outside] == [0, 1]


def test_the_hole_is_measured_from_the_previous_sighting_not_from_the_episode_start():
    # Three 9-minute steps: every gap is inside the 600 s rule, so one episode spans 27
    # minutes even though its span is far longer than the rule itself.
    found = episodes.episodes_for([at(0), at(9), at(18), at(27)], cadence_in_force=120)
    assert len(found) == 1 and found[0].candidates == 4
    assert found[0].ended_at - found[0].started_at == timedelta(minutes=27)


def test_a_hole_exactly_the_length_of_the_rule_extends_the_episode():
    # "extends while the previous sighting is within gap_rule_s": at exactly 600 s it is still
    # within, and one second later it is not.
    assert len(episodes.episodes_for([at(0), at(10)], cadence_in_force=120)) == 1
    assert len(episodes.episodes_for([at(0), at(10, 1)], cadence_in_force=120)) == 2


def test_every_episode_carries_the_rule_and_its_parameters():
    # §1.9(b): stored per row, so a re-count under another rule needs no new data.
    [episode] = episodes.episodes_for([at(0), at(2)], cadence_in_force=300)
    assert episode.cadence_in_force == 300
    assert episode.gap_rule_s == 900 == episodes.gap_rule_s(300)
    assert episode.sightings == (at(0), at(2))


def test_sightings_are_read_in_their_own_stamp_order():
    out_of_order = episodes.episodes_for([at(120), at(2), at(0), at(4)], cadence_in_force=120)
    assert [episode.started_at for episode in out_of_order] == [at(0), at(120)]


def test_no_sighting_is_no_episode():
    assert episodes.episodes_for([], cadence_in_force=120) == []


def test_a_cadence_that_is_not_in_force_is_refused():
    # The rule's parameter is the cadence that was actually scheduled; zero or negative is not
    # a cadence and would silently collapse the rule to its floor.
    with pytest.raises(ValueError):
        episodes.gap_rule_s(0)
