"""The model's view of the weekly report, and the two checks a bullet has to pass.

The renderer's contract is negative as much as positive: the model sees cells, headers and row
*indices*, and never a ticker, a variant name or a market title. The checker's contract is that
a bullet survives only if it cites a real cell and invents no number.
"""
import re

import pytest

from harness.report.render_for_model import (BULLET_MAX, BULLETS_MAX, CITATION_RE, ModelView,
                                             check_bullet, numbers_in, render_for_model,
                                             resolve_citation)
from harness.report.tables import Table


def _tables():
    return {
        "t1": Table(
            title="Table 1 (t1): order lifecycle",
            header="one row per variant; fill rate is fills over orders",
            columns=["variant", "orders", "fill_rate"],
            rows=[["sharp_direct", 412, 0.31], ["constrained", 98, 0.22]],
            note="replay excluded",
        ),
        "t11": Table(
            title="Table 11 (t11): venue requests",
            header="authenticated traffic only",
            columns=["ticker", "requests"],
            rows=[["KXNFLGAME-26SEP14DALNYG-DAL", 3]],
        ),
    }


def test_the_view_carries_no_row_key_text():
    """B-I5: every venue-sourced string is replaced by its row index. `sharp_direct` is a
    variant name and the ticker is the venue's; neither may reach the rendered text."""
    view = render_for_model(_tables())
    assert "sharp_direct" not in view.text
    assert "KXNFLGAME" not in view.text


def test_rows_are_addressed_by_index():
    view = render_for_model(_tables())
    assert "[0]" in view.text and "[1]" in view.text
    assert view.cells["t1"][0][1] == "412"
    assert view.cells["t1"][1][0] == "constrained"   # stored, so a citation can resolve it
    assert view.columns["t1"] == ["variant", "orders", "fill_rate"]


def test_the_first_column_is_rendered_as_the_index_but_kept_for_resolution():
    """The model is shown `[1] orders=98`; a citation of column 0 still resolves to the stored
    text, so a bullet that cites the name column can be checked rather than crashing."""
    view = render_for_model(_tables())
    lines = [l for l in view.text.splitlines() if l.startswith("[1]")]
    assert lines and "constrained" not in lines[0]
    assert resolve_citation(view, "t1[1,0]") == "constrained"


def test_headers_and_titles_are_included():
    view = render_for_model(_tables())
    assert "one row per variant" in view.text
    assert "Table 1 (t1): order lifecycle" in view.text


def test_a_missing_table_is_simply_absent():
    view = render_for_model({})
    assert view.text.strip() != ""      # the preamble still renders
    assert view.cells == {}


@pytest.mark.parametrize("citation,expected", [
    ("t1[0,1]", "412"),
    ("t1[1,2]", "0.2200"),
    ("t4[0,0]", None),          # no such table
    ("t1[9,0]", None),          # no such row
    ("t1[0,9]", None),          # no such column
])
def test_resolve_citation(citation, expected):
    assert resolve_citation(render_for_model(_tables()), citation) == expected


def test_the_citation_pattern_accepts_t4b():
    assert CITATION_RE.fullmatch("t4b[2,3]")
    assert CITATION_RE.fullmatch("t12[0,0]")
    assert not CITATION_RE.fullmatch("t[0,0]")


def test_numbers_in_finds_signed_decimals_and_percents():
    assert numbers_in("up 0.31 from -0.0042 on 412 orders") == ["0.31", "-0.0042", "412"]
    assert numbers_in("no numbers here") == []


def test_a_bullet_with_no_citation_is_dropped():
    view = render_for_model(_tables())
    assert check_bullet(view, "Fill rates improved this week.") == "no citation"


def test_a_bullet_whose_citation_does_not_resolve_is_dropped():
    view = render_for_model(_tables())
    assert check_bullet(view, "Fill rate was 0.31 t4[0,0].") == "no resolving citation"


def test_a_bullet_with_a_number_absent_from_every_cited_cell_is_dropped():
    view = render_for_model(_tables())
    reason = check_bullet(view, "Fill rate was 0.99 on 412 orders t1[0,1].")
    assert reason == "number 0.99 is in no cited cell"


def test_a_bullet_that_cites_and_quotes_correctly_survives():
    view = render_for_model(_tables())
    assert check_bullet(view, "412 orders on the first row t1[0,1].") is None


def test_a_number_may_come_from_any_of_several_cited_cells():
    view = render_for_model(_tables())
    assert check_bullet(view, "412 and 98 t1[0,1] t1[1,1].") is None


def test_a_bullet_over_the_length_cap_is_dropped():
    view = render_for_model(_tables())
    long = "412 orders t1[0,1] " + "x" * BULLET_MAX
    assert check_bullet(view, long) == "over 240 characters"


def test_the_bullet_caps_are_the_addendum_s():
    assert BULLET_MAX == 240 and BULLETS_MAX == 5


def test_format_cell_is_public_and_is_the_one_formatter():
    """The annotator must see exactly the string the human reader sees, so there is one
    formatter, not two. This is the rename the task makes."""
    from harness.report import weekly

    assert weekly.format_cell(0.31) == "0.3100"
    assert not hasattr(weekly, "_format_cell")
