"""The amendment list against the record it is copied from (addendum 0.9, 1.7).

The list is code because the weekly report is rendered inside the container and `docs/` is not
in the image. That makes drift the risk, so the test reads the record's own headings.
"""
import re
from pathlib import Path

from harness.report.amendments import AMENDMENTS, Amendment

RECORD = (Path(__file__).resolve().parents[1] / "docs" / "superpowers" / "reviews"
          / "2026-09-07-phase2-preregistration.md")


def _recorded_numbers() -> set[int]:
    """Every `## Amendment <n>` heading in the record.

    `\\d{1,2}`, not `\\d+`: Amendment 1's heading is `## Amendment 2026-09-07`, and a greedy digit
    run would read that date as amendment 2026. `## Amendment numbering` and `## Amendment
    protocol` are prose sections, not amendments, and do not match either.
    """
    return {int(m) for m in re.findall(r"^## Amendment (\d{1,2})\b", RECORD.read_text(),
                                       flags=re.MULTILINE)}


def test_the_heading_regex_does_not_read_a_date_as_an_amendment_number():
    """Amendment 1's heading carries a date where the others carry a number. The record's own
    `## Amendment numbering` section is what says that section is Amendment 1."""
    assert "## Amendment 2026-09-07" in RECORD.read_text()
    assert 2026 not in _recorded_numbers()
    assert 1 not in _recorded_numbers()


def test_every_recorded_amendment_has_an_entry():
    missing = _recorded_numbers() - {a.number for a in AMENDMENTS}
    assert missing == set(), f"the record has Amendment {sorted(missing)} and the code does not"


def test_the_list_invents_no_amendment_the_record_does_not_have():
    """Amendment 1 is the exception the record names in as many words: its heading is
    `## Amendment 2026-09-07`, and `## Amendment numbering` says that section is Amendment 1."""
    extra = {a.number for a in AMENDMENTS} - _recorded_numbers()
    assert extra == {1}
    assert "is Amendment 1" in RECORD.read_text()


def test_the_numbers_are_unique_and_in_order():
    numbers = [a.number for a in AMENDMENTS]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == len(numbers)


def test_amendment_4_carries_the_run_id_range_the_record_states():
    four = next(a for a in AMENDMENTS if a.number == 4)
    assert four.excluded_runs == (344, 4327)
    assert four.deploy_sha == "a193fd0"
    assert "t2" in four.tables
    assert "344" in RECORD.read_text() and "4327" in RECORD.read_text()


def test_amendment_3_is_a_registration_and_excludes_nothing():
    three = next(a for a in AMENDMENTS if a.number == 3)
    assert three.excluded_runs is None
    assert three.tables == ()


def test_every_entry_is_frozen():
    import dataclasses

    assert dataclasses.is_dataclass(Amendment)
    for amendment in AMENDMENTS:
        try:
            amendment.number = 99            # noqa: B010 - the point of the test
        except dataclasses.FrozenInstanceError:
            continue
        raise AssertionError("Amendment must be frozen: the record is append-only")
