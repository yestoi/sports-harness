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
"""
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harness.report.tables import PLACEHOLDER, TABLE_KEYS, Table
from harness.report.weekly import format_cell

#: Addendum §1.5: at most five bullets, each at most 240 characters.
BULLETS_MAX = 5
BULLET_MAX = 240

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
    `format_cell` puts in the Markdown a person reads.
    """

    text: str
    columns: dict[str, list[str]]
    cells: dict[str, list[list[str]]]


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
        rendered = [[format_cell(value) for value in row] for row in table.rows]
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
    nothing in `report_cells` marks which `col_key` that was. So it is recovered the only way
    the stored data allows: per table, from its first row, whichever `col_key`'s stored `text`
    equals that row's `row_key` exactly is the identity column, folded into a synthetic column 0
    (`row_key`'s own value, kept in `cells` and never printed, exactly like `render_for_model`'s
    column 0) and dropped from the printed "every other cell" list. A table whose first row
    matches no column this way hides nothing -- documented here rather than guessed at, since a
    table shaped so differently from the rest is worth a person's attention, not a silent guess.

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
    for key in TABLE_KEYS:
        state = tables.get(key)
        if state is None:
            continue
        row_keys = sorted(state["rows"])
        first_row = state["rows"][row_keys[0]]
        identity_col = next((c for c in state["cols"] if first_row.get(c) == row_keys[0]), None)
        other_cols = [c for c in state["cols"] if c != identity_col]
        columns[key] = [identity_col or "row_key", *other_cols]
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
        if meta is not None and meta[2]:
            lines.append(f"note: {meta[2]}")
        lines.append("")
    return ModelView(text="\n".join(lines), columns=columns, cells=cells_out)


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
