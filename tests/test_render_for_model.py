"""The model's view of the weekly report, and the two checks a bullet has to pass.

The renderer's contract is negative as much as positive: the model sees cells, headers and row
*indices*, and never a ticker, a variant name or a market title. The checker's contract is that
a bullet survives only if it cites a real cell and invents no number.
"""
import re
from collections import namedtuple

import pytest

import harness.report.render_for_model as render_module
from harness.report.render_for_model import (BULLET_MAX, BULLETS_MAX, CITATION_RE,
                                             IDENTITY_COLUMNS, ModelView, check_bullet,
                                             numbers_in, render_for_model, render_from_cells,
                                             resolve_citation, titles_from_markdown)
from harness.report.tables import TABLE_KEYS, Table
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


def test_render_from_cells_omits_a_table_with_no_mapped_identity_column(monkeypatch):
    """Fix round 2, new defect 2: a table key `IDENTITY_COLUMNS` does not name (should not
    happen for anything in `TABLE_KEYS` --
    `test_identity_columns_match_every_table_s_real_first_column` guards that) is left out of
    the view entirely -- fails closed -- rather than shown with every column visible, including
    the one it should have hidden."""
    monkeypatch.delitem(render_module.IDENTITY_COLUMNS, "t9")
    cells = [
        _Cell("t9", "row-a", "alpha", "10"),
        _Cell("t9", "row-a", "beta", "20"),
    ]
    view = render_from_cells(cells)
    assert "t9" not in view.columns
    assert "t9" not in view.cells
    assert "== t9 ==" not in view.text
    assert view.tables_omitted == 1


def test_render_from_cells_omits_a_table_whose_stored_column_was_renamed():
    """Fix round 2, new defect 2's other form: `IDENTITY_COLUMNS["t1"]` is `"variant"`, and this
    table's stored `col_key` is `"variant_renamed"` instead -- a table builder renamed after
    `IDENTITY_COLUMNS` last updated, the case the review reproduced. Fails closed: the table (and
    the value that would have leaked) is absent from the view, not shown with every column
    visible."""
    cells = [
        _Cell("t1", "sharp_direct", "variant_renamed", "sharp_direct"),
        _Cell("t1", "sharp_direct", "orders", "412"),
    ]
    view = render_from_cells(cells)
    assert "t1" not in view.columns
    assert "t1" not in view.cells
    assert "== t1 ==" not in view.text
    assert "sharp_direct" not in view.text
    assert view.tables_omitted == 1


def test_render_from_cells_treats_a_null_stored_text_as_the_placeholder():
    """`t9`'s real shape (`_not_collected`'s `["item", "status"]`), so the identity column is
    recognised by name and the placeholder assertion below is about the *other* column."""
    cells = [
        _Cell("t9", "row-a", "item", "row-a"),
        _Cell("t9", "row-a", "status", None),
    ]
    view = render_from_cells(cells)
    from harness.report.tables import PLACEHOLDER

    assert view.cells["t9"][0][1] == PLACEHOLDER


def test_render_from_cells_orders_tables_by_table_keys_not_by_input_order():
    """`TABLE_KEYS` orders `t9` before `t10`, the opposite of their alphabetical (and thus SQL
    `order by table_key`) order -- a real query's own row order, so this is what a caller's
    `_CELLS`-shaped query actually hands the function. Real identity column names (`item` for
    t9, `group` for t10), so neither table is omitted (fails closed otherwise, fix round 2)."""
    cells = [
        _Cell("t10", "row-a", "group", "row-a"),
        _Cell("t10", "row-a", "n", "1"),
        _Cell("t9", "row-a", "item", "row-a"),
        _Cell("t9", "row-a", "n", "2"),
    ]
    view = render_from_cells(cells)
    assert list(view.columns) == ["t9", "t10"]


# --- IDENTITY_COLUMNS (fix round 1, Critical 1) -------------------------------------------------


def test_identity_columns_match_every_table_s_real_first_column():
    """`IDENTITY_COLUMNS` duplicates `harness/report/tables.py`'s own first-column literals,
    since `report_cells` carries no column-position field for `render_from_cells` to read one
    off directly. This compares the copy against the real source: the eleven tables with a named
    `_T*_COLUMNS` module constant, t2's inline list (built from the same `BENCHMARK_TYPES` the
    real `_table2` uses), and t9's real shape (`_not_collected`, called directly -- pure, no
    session needed)."""
    import harness.report.tables as tables_module
    from harness.settlement.benchmarks import BENCHMARK_TYPES

    named = {
        "t1": tables_module._T1_COLUMNS, "t3": tables_module._T3_COLUMNS,
        "t4": tables_module._T4_COLUMNS, "t4b": tables_module._T4B_COLUMNS,
        "t5": tables_module._T5_COLUMNS, "t6": tables_module._T6_COLUMNS,
        "t7": tables_module._T7_COLUMNS, "t8": tables_module._T8_COLUMNS,
        "t10": tables_module._T10_COLUMNS, "t11": tables_module._T11_COLUMNS,
        "t12": tables_module._T12_COLUMNS,
    }
    for key, columns in named.items():
        assert IDENTITY_COLUMNS[key] == columns[0]

    t2_columns = ["variant", "tier", "basis", *BENCHMARK_TYPES]
    assert IDENTITY_COLUMNS["t2"] == t2_columns[0]

    t9 = tables_module._not_collected("t9", "placeholder title", "placeholder reason")
    assert IDENTITY_COLUMNS["t9"] == t9.columns[0]

    assert set(IDENTITY_COLUMNS) == set(TABLE_KEYS)


def test_render_from_cells_hides_column_0_for_every_table_key():
    """Every table in `TABLE_KEYS` has its identity column recovered and kept out of the printed
    line, not only the ones exercised elsewhere in this file."""
    for key in TABLE_KEYS:
        identity_col = IDENTITY_COLUMNS[key]
        secret = f"secret-value-for-{key}"
        cells = [
            _Cell(key, "row-0", identity_col, secret),
            _Cell(key, "row-0", "other", "42"),
        ]
        view = render_from_cells(cells)
        assert secret not in view.text, f"{key} printed its identity column"
        assert view.cells[key][0][0] == "row-0"   # kept, for resolution


def test_render_from_cells_hides_a_row_key_over_60_characters():
    """Fix round 1, Critical 1. `report_cells.row_key` truncates at 60 characters and the
    matching cell's stored `text` at 64 (`Table.row_key`, `harness/report/weekly.py::
    _cell_fields`) -- two different lengths from the same value -- so recovering the identity
    column by matching `text == row_key` silently failed for any first-column value over 60
    characters and printed it straight into the model's prompt. Table 12's
    `f"{variant}/{kind}:{reason}"[:64]` is routinely that long. `IDENTITY_COLUMNS` recovers t12's
    identity column by name, so this passes regardless of length."""
    key = ("sharp_direct_nfl_spread/rejected:benchmark_stale_beyond_ten_minutes_here")[:62]
    assert len(key) == 62
    tables = {
        "t12": Table(title="Table 12 (t12): declined candidates", header="h",
                    columns=["variant/reason", "kind", "count", "share"],
                    rows=[[key, "rejected", 3, 0.5]]),
    }
    view = render_from_cells(_stored_cells(tables))
    assert key not in view.text
    assert key[:60] not in view.text   # the stored (truncated) row_key must not leak either
    assert view.cells["t12"][0][0] == key[:60]   # kept, for resolution


# --- titles_from_markdown (fix round 1, Important 1) ---------------------------------------------


def test_titles_from_markdown_recovers_title_header_and_note():
    from harness.report.weekly import render_markdown

    markdown = render_markdown(_tables(), {"year": 2026, "week": 38})
    titles = titles_from_markdown(markdown)
    assert titles["t1"] == ("Table 1 (t1): order lifecycle",
                            "one row per variant; fill rate is fills over orders",
                            "replay excluded")
    assert titles["t11"] == ("Table 11 (t11): venue requests", "authenticated traffic only",
                             None)


def test_render_from_cells_uses_recovered_titles():
    from harness.report.weekly import render_markdown

    tables = _tables()
    markdown = render_markdown(tables, {"year": 2026, "week": 38})
    titles = titles_from_markdown(markdown)
    view = render_from_cells(_stored_cells(tables), titles=titles)
    assert "Table 1 (t1): order lifecycle" in view.text
    assert "one row per variant; fill rate is fills over orders" in view.text
    assert "note: replay excluded" in view.text
