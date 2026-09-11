"""README §7 against the coded gate criteria (addendum 0.7).

The README is the document a person reads to find out what "going live" means. The gate is what
the code actually checks. The two had drifted: §7 listed nine plain sentences against twelve
coded criteria, two of the nine were not gate tests at all, and nothing connected a sentence to
the criterion it described. These tests are the connection.

R1: reconciling the prose changes no definition, so `criteria_hash()` cannot move. The value is
pinned here rather than recomputed, because a test that recomputes the thing it is checking
proves nothing.
"""
from pathlib import Path

from harness.report.gate import CRITERIA, criteria_hash

README = Path(__file__).resolve().parents[1] / "README.md"

#: The hash on 2026-09-11, before and after this reconciliation.
CRITERIA_HASH = "5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5"

#: The heading that separates the coded criteria from the duties around them.
DUTIES_HEADING = "Duties around the gate, not gate tests"


def _section_7() -> str:
    body = README.read_text()
    assert "## 7. The tests for going live" in body
    return body.split("## 7. The tests for going live", 1)[1].split("\n## 8.", 1)[0]


def test_every_coded_criterion_is_named_in_readme_section_7():
    section = _section_7()
    missing = [c.name for c in CRITERIA if f"`{c.name}`" not in section]
    assert missing == [], f"README section 7 names no line for {missing}"


def test_readme_section_7_names_no_criterion_the_code_does_not_have():
    """The other direction: a backticked name in §7 that is not a `Criterion.name` is prose
    claiming to be a gate test."""
    import re

    section = _section_7()
    coded = {c.name for c in CRITERIA}
    listed = {name for name in re.findall(r"`([a-z_]+)`", section) if "_" in name}
    assert listed - coded == set()


def test_the_criteria_hash_is_unchanged_by_the_reconciliation():
    assert criteria_hash() == CRITERIA_HASH


def test_the_duties_are_listed_apart_from_the_criteria():
    section = _section_7()
    assert DUTIES_HEADING in section
    duties = section.split(DUTIES_HEADING, 1)[1]
    assert "within 2 %" in duties
    assert "Three weeks in a row" in duties


def test_no_duty_sits_inside_the_criteria_list():
    """The two sentences that are not gate tests: the replay reproduction (operator duty R14)
    and the three-week run (the calendar). Neither is in `CRITERIA`, and neither may sit in the
    list a reader takes for the coded set."""
    criteria_part = _section_7().split(DUTIES_HEADING, 1)[0]
    assert "within 2 %" not in criteria_part
    assert "Three weeks in a row" not in criteria_part
