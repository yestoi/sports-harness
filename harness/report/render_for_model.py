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
"""
import re
from dataclasses import dataclass

from harness.report.tables import TABLE_KEYS, Table
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
