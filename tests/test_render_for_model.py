"""The model's view of the weekly report, and the two checks a bullet has to pass.

The renderer's contract is negative as much as positive: the model sees cells, headers and row
*indices*, and never a ticker, a variant name or a market title. The checker's contract is that
a bullet survives only if it cites a real cell and invents no number.
"""
import re
from collections import namedtuple

import pytest

from harness.report.render_for_model import (BULLET_MAX, BULLETS_MAX, CITATION_RE, ModelView,
                                             check_bullet, numbers_in, render_for_model,
                                             render_from_cells, resolve_citation)
from harness.report.tables import Table
from harness.report.weekly import format_cell

#: A `report_cells` row, or enough of one: `render_from_cells` reads only these four fields.
_Cell = namedtuple("_Cell", "table_key row_key col_key text")


def _stored_cells(tables: dict) -> list:
    """Cells stored the way `harness/report/weekly.py::persist_report` stores them, read back
    ordered by `(table_key, row_key, col_key)` -- without touching a database, so this file
    stays a pure-function test like the rest of it."""
    cells = []
    for table_key, table in tables.items():
        seen: dict[str, int] = {}
        for row in table.rows:
            base = table.row_key(row)[:60]
            seen[base] = seen.get(base, 0) + 1
            row_key = base if seen[base] == 1 else f"{base}#{seen[base]}"
            for col_key, value in zip(table.columns, row):
                cells.append(_Cell(table_key, row_key, col_key, format_cell(value)[:64]))
    cells.sort(key=lambda c: (c.table_key, c.row_key, c.col_key))
    return cells


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


# --- render_from_cells (fix 36) ----------------------------------------------------------------
#
# Built with rows and non-identity columns already in the order `render_from_cells` recovers
# them in (row key ascending, other columns alphabetical), so a direct comparison against
# `render_for_model`'s output is meaningful rather than an artefact of two different orderings
# that both happen to be "deterministic".


def _cell_tables():
    return {
        "t1": Table(
            title="Table 1 (t1): order lifecycle",
            header="one row per variant",
            columns=["variant", "a_metric", "b_metric"],
            rows=[["alpha_variant", 5, 6], ["beta_variant", 7, 8]],
        ),
    }


def test_render_from_cells_reproduces_columns_and_cells():
    tables = _cell_tables()
    expected = render_for_model(tables)
    view = render_from_cells(_stored_cells(tables))
    assert view.columns == expected.columns
    assert view.cells == expected.cells


def test_render_from_cells_keeps_the_row_key_out_of_the_rendered_line():
    """B-I5/F60: the column that duplicates `row_key` (here `variant`) is recovered by value,
    from the first row, and kept out of the printed line even though `report_cells` stores it
    as an ordinary column too."""
    view = render_from_cells(_stored_cells(_cell_tables()))
    assert "alpha_variant" not in view.text
    assert "beta_variant" not in view.text
    assert view.cells["t1"][0][0] == "alpha_variant"   # kept, for resolution
    assert resolve_citation(view, "t1[0,0]") == "alpha_variant"


def test_render_from_cells_skips_missing_tables_in_table_keys_order():
    view = render_from_cells(_stored_cells(_cell_tables()))
    assert list(view.columns) == ["t1"]


def test_render_from_cells_defaults_to_no_title_or_header():
    view = render_from_cells(_stored_cells(_cell_tables()))
    assert "== t1 ==" in view.text
    assert "order lifecycle" not in view.text


def test_render_from_cells_accepts_titles():
    view = render_from_cells(_stored_cells(_cell_tables()),
                             titles={"t1": ("Table 1 (t1): order lifecycle",
                                            "one row per variant", "a note")})
    assert "Table 1 (t1): order lifecycle" in view.text
    assert "one row per variant" in view.text
    assert "note: a note" in view.text


def test_render_from_cells_falls_back_to_no_hidden_column_without_a_match():
    """A table whose first row matches no column by value (should not happen for a real report
    table, whose row key is always its own first column) hides nothing rather than guessing."""
    cells = [
        _Cell("t9", "row-a", "alpha", "10"),
        _Cell("t9", "row-a", "beta", "20"),
    ]
    view = render_from_cells(cells)
    assert view.columns["t9"] == ["row_key", "alpha", "beta"]
    assert view.cells["t9"][0] == ["row-a", "10", "20"]


def test_render_from_cells_treats_a_null_stored_text_as_the_placeholder():
    cells = [
        _Cell("t9", "row-a", "row-a", "row-a"),
        _Cell("t9", "row-a", "value", None),
    ]
    view = render_from_cells(cells)
    from harness.report.tables import PLACEHOLDER

    assert view.cells["t9"][0][1] == PLACEHOLDER


def test_render_from_cells_orders_tables_by_table_keys_not_by_input_order():
    """`TABLE_KEYS` orders `t9` before `t10`, the opposite of their alphabetical (and thus SQL
    `order by table_key`) order -- a real query's own row order, so this is what a caller's
    `_CELLS`-shaped query actually hands the function."""
    cells = [
        _Cell("t10", "row-a", "row-a", "row-a"),
        _Cell("t10", "row-a", "n", "1"),
        _Cell("t9", "row-a", "row-a", "row-a"),
        _Cell("t9", "row-a", "n", "2"),
    ]
    view = render_from_cells(cells)
    assert list(view.columns) == ["t9", "t10"]
