"""The weekly report as the annotator sees it, and the two checks its bullets have to pass.

Ruling B-I5. The model is shown cells, headers and **row indices** -- never a row key. Several
tables key their rows on a variant name, a market ticker or a venue-written title, and F60 keeps
venue text out of a prompt, so a row is addressed as `[2]` and a cell as `t4[2,5]`.

Ruling B-I4. A bullet survives only if (a) it carries at least one citation that resolves to a
real cell of this week's tables, and (b) every number it contains appears in the rendered text
of one of the cells it cited. A bullet that fails either is **dropped**, never repaired: an
annotation that is wrong about a number is worse than no annotation, and the report says in as
many words that the block is model-written and unverified.

Nothing here calls a model, reads a key, opens a socket or touches the database. It is a pure
function of the tables and a string, which is what makes the honesty rule testable.

Fix 36. `render_for_model` recomputes every table from the week's rows -- fine for `harness
report`, which runs once, but the annotator used to call it on every 30 s research-worker sweep
until an annotation landed, so a report the weekly render cannot finish inside the statement
timeout locked the worker into a heavy query stream forever (journal 109). `render_from_cells`
is the other constructor: it builds the same `ModelView` from `report_cells`, the rows
`harness/report/weekly.py::persist_report` already wrote when the report was generated, so the
annotator renders what was published rather than recomputing it.

Fix 41 (journal 112). Both constructors cap each table at `ROWS_MAX` rows in the rendered
block, because the prompt this builds has to fit inside the reservation
`harness/research/spend.py`'s `WORST_CASE_INPUT_TOKENS` prices, and a heavy week's table is
otherwise unbounded.
"""
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harness.report.tables import PLACEHOLDER, TABLE_KEYS, Table
from harness.report.weekly import format_cell

log = logging.getLogger(__name__)

#: Addendum §1.5: at most five bullets, each at most 240 characters.
BULLETS_MAX = 5
BULLET_MAX = 240

#: Fix 41 (journal 112): a heavy week's table used to grow the prompt -- and the reservation
#: (`WORST_CASE_INPUT_TOKENS`, `harness/research/spend.py`) it has to fit inside -- without
#: bound. Both constructors below keep only the first `ROWS_MAX` rows of each table in the
#: rendered block, by the same order they would otherwise render in full (`table.rows`'s own
#: order for `render_for_model`, ascending `row_key` for `render_from_cells`), and append a
#: `(<n> more rows omitted)` line to that table's block when any were cut. `columns`/`cells` on
#: the returned `ModelView` keep only the rows actually rendered, so a bullet's `t<k>[<row>,...]`
#: citation always resolves to a real, shown row -- `check_bullet`'s own contract (ruling B-I4)
#: never has to know this cap exists.
ROWS_MAX = 60

#: `Table.columns[0]` for every table key `TABLE_KEYS` names (fix round 1, Critical 1) -- the
#: row-key column `render_for_model` keeps out of the printed line (F60/B-I5). `report_cells`
#: stores no column-position field, only names, so `render_from_cells` cannot read this off a
#: live `Table` the way `render_for_model` does; these are `harness/report/tables.py`'s own
#: literals, copied here because none of them is exported: `_T1_COLUMNS[0]` through
#: `_T12_COLUMNS[0]` for the tables that have a named constant, `_table2`'s inline
#: `["variant", "tier", "basis", *BENCHMARK_TYPES, ...]` for t2, and `_not_collected`'s
#: `["item", "status"]` for t9. A first column's *value* was tried instead of its *name*
#: (matching it against the stored `row_key` by equality) and failed on real data: `row_key` is
#: truncated at 60 characters and the matching cell's `text` at 64, so any first-column value
#: over 60 characters -- t12's `f"{variant}/{kind}:{reason}"` routinely is -- matched nothing,
#: and the venue-sourced value it should have hidden was printed straight into the model's
#: prompt instead. A name never truncates, so this does not have that failure mode; the risk it
#: trades in is drift from `tables.py` if a table's first column is ever renamed, which
#: `tests/test_render_for_model.py::test_identity_columns_match_every_table_s_real_first_column`
#: catches by comparing this dict against `tables.py`'s own constants directly.
IDENTITY_COLUMNS: dict[str, str] = {
    "t1": "variant", "t2": "variant", "t3": "variant", "t4": "fair_source",
    "t4b": "market_type", "t5": "sport", "t6": "variant", "t7": "decision", "t8": "metric",
    "t9": "item", "t10": "group", "t11": "env", "t12": "variant/reason", "t13": "item",
}

#: `t4b` and `t12` both exist, so the table part is digits with an optional trailing `b`.
CITATION_RE = re.compile(r"t(\d+b?)\[(\d+),(\d+)\]")

#: A number the model might write: an optional sign, digits, an optional decimal part. Percent
#: signs and currency symbols are not part of the match, so "31%" yields "31" and is checked
#: against a cell that renders 31.
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

_PREAMBLE = (
    "Below is one week of the harness's fixed report tables. Rows are addressed by index only.\n"
    "Cite a cell as t<table>[<row>,<column>], for example t4[2,5].\n"
    "Every number you write must appear in a cell you cite.\n"
)


@dataclass(frozen=True)
class ModelView:
    """What the annotator is shown, and what its bullets are checked against.

    `text` is the prompt's data block. `columns[table_key]` is that table's column names in
    order, and `cells[table_key][row][col]` is one cell's rendered text -- the same string
    `format_cell` puts in the Markdown a person reads. `tables_omitted` (fix round 2, new defect
    2) is how many of `TABLE_KEYS` `render_from_cells` left out because it could not recover
    their identity column -- always `0` for `render_for_model`, which has no such failure mode.
    """

    text: str
    columns: dict[str, list[str]]
    cells: dict[str, list[list[str]]]
    tables_omitted: int = 0


def render_for_model(tables: dict[str, Table]) -> ModelView:
    """The tables as indices and cells. Rendered in `TABLE_KEYS` order, so the block a model
    sees is in the same order as the report."""
    lines = [_PREAMBLE]
    columns: dict[str, list[str]] = {}
    cells: dict[str, list[list[str]]] = {}
    for key in TABLE_KEYS:
        table = tables.get(key)
        if table is None:
            continue
        columns[key] = list(table.columns)
        all_rendered = [[format_cell(value) for value in row] for row in table.rows]
        rendered = all_rendered[:ROWS_MAX]
        cells[key] = rendered
        lines.append(f"== {key} ==")
        lines.append(table.title)
        lines.append(table.header)
        lines.append("columns: " + ", ".join(f"{i}:{name}"
                                             for i, name in enumerate(table.columns)))
        for index, row in enumerate(rendered):
            # Column 0 is deliberately omitted from the *rendered* line and kept in `cells`:
            # it is the row key, which for several tables is a ticker or a variant name.
            body = " ".join(f"{i}={value}" for i, value in enumerate(row) if i > 0)
            lines.append(f"[{index}] {body}")
        omitted_rows = len(all_rendered) - len(rendered)
        if omitted_rows > 0:
            lines.append(f"({omitted_rows} more rows omitted)")
        if table.note:
            lines.append(f"note: {table.note}")
        lines.append("")
    return ModelView(text="\n".join(lines), columns=columns, cells=cells)


def render_from_cells(cells: Sequence[Any],
                      titles: Mapping[str, tuple[str, str, str | None]] | None = None
                      ) -> ModelView:
    """The tables as indices and cells, built from stored `report_cells` rows (fix 36) instead
    of a fresh `weekly_tables` computation. `cells` is every row of one `report_runs.id`, read
    ordered by `(table_key, row_key, col_key)` -- the caller's query does that, never this
    function, since it is what fixes the deterministic order below. Each row needs `table_key`,
    `row_key`, `col_key` and `text` (a `report_cells` row, or anything shaped like one).

    **Column order.** `report_cells` has no column-position field, only names, so the order is
    recovered from the scan itself: the position a `col_key` first appears in, reading rows
    ordered as above. Every row of a table carries the same column set, and ordering by
    `row_key` before `col_key` means the very first row already contains that whole set in
    `col_key`'s own ascending order -- so in practice this is lexicographic order by column
    name, not `Table.columns`'s left-to-right order (`render_for_model` has the live `Table` to
    read that from; this does not).

    **Column 0, and F60/B-I5.** `render_for_model` keeps `Table.columns[0]` -- a variant, a
    ticker, a stratum -- out of the rendered line and only in `cells`, because it is exactly
    what `Table.row_key` stores as `report_cells.row_key`. That value is *also* stored again as
    an ordinary cell (`persist_report` zips every column, including the first, into its own
    `ReportCell` row -- the dashboard's Study surface needs the full row to show a person), and
    nothing in `report_cells` marks which `col_key` that was. This function looks it up by
    **name** in `IDENTITY_COLUMNS` -- `Table.columns[0]` is a structural fact about each table
    builder, unconditional and never data-dependent, so the table key is enough to know it.
    That column's value is folded into a synthetic column 0 (`row_key`'s own value, kept in
    `cells` and never printed, exactly like `render_for_model`'s column 0) and dropped from the
    printed "every other cell" list.

    **Fails closed (fix round 2, new defect 2).** A table key `IDENTITY_COLUMNS` does not name,
    or whose mapped column name is not among that table's actual stored `col_key`s, is left out
    of `columns`, `cells` and the printed text entirely -- its `== <key> ==` block never
    appears, and its identity value is never rendered, anywhere. This should not happen for a
    real `report_cells` row of any key in `TABLE_KEYS` (`IDENTITY_COLUMNS` is tested against
    `tables.py`'s own literals), which is exactly why it fails closed rather than open: the
    previous version of this function, when it could not find the identity column, rendered the
    table with every column visible -- including the one it should have hidden -- and F60/B-I5
    is a hard rule, not a best effort. Each omission is logged once at ERROR with the table key
    and counted in the returned `ModelView.tables_omitted`.

    `titles`, keyed by table key, is `(title, header, note)`; omitted (the default) because
    `harness/report/tables.py` keeps those as literals inside each table builder rather than in
    an importable table, and duplicating them here as a second copy is how the two drift. Without
    it, this renders `== <key> ==` and the columns line only, with no title, header or note.
    """
    tables: dict[str, dict] = {}
    for row in cells:
        state = tables.setdefault(row.table_key, {"cols": [], "rows": {}})
        if row.col_key not in state["cols"]:
            state["cols"].append(row.col_key)
        text = row.text if row.text is not None else PLACEHOLDER
        state["rows"].setdefault(row.row_key, {})[row.col_key] = text

    lines = [_PREAMBLE]
    columns: dict[str, list[str]] = {}
    cells_out: dict[str, list[list[str]]] = {}
    omitted = 0
    for key in TABLE_KEYS:
        state = tables.get(key)
        if state is None:
            continue
        identity_col = IDENTITY_COLUMNS.get(key)
        if identity_col is None or identity_col not in state["cols"]:
            # Fails closed: the identity column (F60/B-I5's hidden row key) cannot be
            # recovered, so the table is left out entirely rather than shown with it visible.
            log.error("render_from_cells: table %s's identity column is unrecoverable; "
                     "omitting the table rather than risk printing its row key", key)
            omitted += 1
            continue
        all_row_keys = sorted(state["rows"])
        row_keys = all_row_keys[:ROWS_MAX]
        other_cols = [c for c in state["cols"] if c != identity_col]
        columns[key] = [identity_col, *other_cols]
        rendered = [[row_key, *(state["rows"][row_key].get(c, PLACEHOLDER) for c in other_cols)]
                   for row_key in row_keys]
        cells_out[key] = rendered
        lines.append(f"== {key} ==")
        meta = titles.get(key) if titles else None
        if meta is not None:
            lines.append(meta[0])
            lines.append(meta[1])
        lines.append("columns: " + ", ".join(f"{i}:{name}"
                                             for i, name in enumerate(columns[key])))
        for index, row in enumerate(rendered):
            body = " ".join(f"{i}={value}" for i, value in enumerate(row) if i > 0)
            lines.append(f"[{index}] {body}")
        omitted_rows = len(all_row_keys) - len(row_keys)
        if omitted_rows > 0:
            lines.append(f"({omitted_rows} more rows omitted)")
        if meta is not None and meta[2]:
            lines.append(f"note: {meta[2]}")
        lines.append("")
    return ModelView(text="\n".join(lines), columns=columns, cells=cells_out,
                     tables_omitted=omitted)


#: `_render_table` (`harness/report/weekly.py`) always writes a table's heading as
#: `## Table <n> (t<key>): <name>` -- every builder in `harness/report/tables.py` follows the
#: same convention, `_not_collected` included. The parenthesised key is what ties a heading back
#: to a `TABLE_KEYS` entry.
_TABLE_HEADING_RE = re.compile(r"^## .*\((t\d+b?)\)")


def titles_from_markdown(markdown: str) -> dict[str, tuple[str, str, str | None]]:
    """Recovers each table's `(title, header, note)` from a report's own stored Markdown
    (`report_runs.markdown`) -- the `titles` argument `render_from_cells` takes, so the
    annotator's model reads the same title, header and note a person does (fix round 1,
    Important 1) rather than a bare `== t4 ==` and a columns line.

    Parses `_render_table`'s own layout (`harness/report/weekly.py`): `## <title>`, a blank
    line, `<header>`, a blank line, the Markdown table itself, then optionally a blank line and
    `_<note>_`. This is the one place those three strings are written; recovering them from here
    means there is nothing to drift, unlike copying the literals into a second place the way
    `IDENTITY_COLUMNS` above has to.

    The Markdown is the harness's own render, but one block inside it is not: `render_markdown`
    writes a previous annotation's bullets into a fenced "model notes" block *above* the tables,
    and a re-run of an already-annotated week stores that block in the pending run's own
    `markdown`. This parser tracks no fences and does not need to: a stored bullet has been
    through `sanitize_model_text`, which collapses every run of whitespace (newlines included)
    into a single space, and `render_markdown` writes each one as `- {bullet}` -- so no
    model-written line can begin with `## `, which is what `_TABLE_HEADING_RE` anchors on. The
    fence also precedes every real heading, so even a matching line there would be overwritten
    by the real one. Both invariants live elsewhere; if either moves, this parser needs a fence
    skip, because a title and a header go straight into the next prompt.
    """
    titles: dict[str, tuple[str, str, str | None]] = {}
    lines = markdown.splitlines()
    i, n = 0, len(lines)
    while i < n:
        match = _TABLE_HEADING_RE.match(lines[i])
        if match is None:
            i += 1
            continue
        key = match.group(1)
        title = lines[i][3:].strip()
        i += 1
        while i < n and not lines[i].strip():
            i += 1
        header = lines[i].strip() if i < n else ""
        i += 1
        # The Markdown table itself: a columns row, a separator row, then the data rows, every
        # one of them starting with `|` (`_render_table`'s own format).
        while i < n and not lines[i].lstrip().startswith("|"):
            i += 1
        while i < n and lines[i].lstrip().startswith("|"):
            i += 1
        note = None
        j = i
        while j < n and not lines[j].strip():
            j += 1
        stripped = lines[j].strip() if j < n else ""
        if len(stripped) > 1 and stripped.startswith("_") and stripped.endswith("_"):
            note = stripped[1:-1]
            i = j + 1
        titles[key] = (title, header, note)
    return titles


def resolve_citation(view: ModelView, citation: str) -> str | None:
    """The rendered text of the cell a citation names, or None when it names none."""
    match = CITATION_RE.fullmatch(citation.strip())
    if match is None:
        return None
    key, row, col = f"t{match.group(1)}", int(match.group(2)), int(match.group(3))
    rows = view.cells.get(key)
    if rows is None or row >= len(rows):
        return None
    cells = rows[row]
    return cells[col] if col < len(cells) else None


def numbers_in(text: str) -> list[str]:
    """Every number-shaped token in a string, in order. Used only by `check_bullet`."""
    return _NUMBER_RE.findall(text)


def check_bullet(view: ModelView, bullet: str) -> str | None:
    """Why this bullet is dropped, or None when it survives (ruling B-I4).

    The reason string is stored in the task report and in nothing that renders, so it may name
    the offending number.
    """
    if len(bullet) > BULLET_MAX:
        return f"over {BULLET_MAX} characters"
    citations = [f"t{m.group(1)}[{m.group(2)},{m.group(3)}]"
                 for m in CITATION_RE.finditer(bullet)]
    if not citations:
        return "no citation"
    resolved = [text for text in (resolve_citation(view, c) for c in citations)
                if text is not None]
    if not resolved:
        return "no resolving citation"
    # The citations themselves carry digits (`t1[0,1]`), so they are removed before the
    # numbers are read: otherwise every bullet would "contain" its own row and column indices.
    body = CITATION_RE.sub(" ", bullet)
    for number in numbers_in(body):
        if not any(number in cell for cell in resolved):
            return f"number {number} is in no cited cell"
    return None
