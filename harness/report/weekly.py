"""Rendering, the selection artefact, and the week-3 confirmation restriction.

The report is Markdown because a human reads it on Monday morning and because it commits
cleanly beside the pre-registration record. The selection artefact is JSON because week 3 has
to read back exactly what week 2 chose, and a diff of that file is the audit trail that the
confirmation set was frozen before any week-3 data existed.

Every cell prints its own two counts, so the reader never has to trust an estimate without
knowing how many games it came from: `grey` marks a cell below 10 game clusters (outside every
BH/Holm family and outside the §9.6 count) and `flag` one below 30.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.report import CRITERIA_TEXT, criteria_hash
from harness.report.tables import (
    CONTRAST_BENCHMARK,
    HEADLINE_PANEL,
    PLACEHOLDER,
    SIGNIFICANT_CELLS_REQUIRED,
    TABLE_KEYS,
    Table,
    cell_excludes_zero,
    is_flagged,
    is_grey,
    week_bounds,
    with_rows,
)

#: The columns that identify a table-4 cell in the selection artefact, in order.
CELL_KEY = ("fair_source", "price_bucket", "ttk", "sport", "market_type")
#: And a table-2 contrast.
CONTRAST_KEY = ("variant", "benchmark_type")

CONFIRMATION_NOTE = ("confirmation set: restricted to the cells and contrasts selected in the "
                     "week-38 freeze; nothing outside that set is evaluated here.")


# --- rendering ------------------------------------------------------------------------------


def _format_cell(value: Any) -> str:
    """One cell as Markdown. Never empty: an empty cell in a scored table reads as a zero."""
    if value is None:
        return PLACEHOLDER
    if isinstance(value, tuple) and len(value) == 5:
        estimate, n_obs, n_clusters, lo, hi = value
        marker = "grey " if is_grey(value) else ("flag " if is_flagged(value) else "")
        interval = (PLACEHOLDER if _nan(lo) or _nan(hi)
                    else f"[{lo:+.4f}, {hi:+.4f}]")
        return f"{marker}{estimate:+.4f} {interval} n={n_obs} G={n_clusters}"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return PLACEHOLDER if _nan(value) else f"{value:.4f}"
    rendered = str(value).strip()
    return rendered or PLACEHOLDER


def _nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def _render_table(table: Table) -> list[str]:
    lines = [f"## {table.title}", "", table.header, ""]
    lines.append("| " + " | ".join(table.columns) + " |")
    lines.append("|" + "|".join("---" for _ in table.columns) + "|")
    for row in table.rows:
        lines.append("| " + " | ".join(_format_cell(value) for value in row) + " |")
    if table.note:
        lines += ["", f"_{table.note}_"]
    return lines + [""]


def render_markdown(tables: dict[str, Table], meta: dict) -> str:
    """The whole report: a provenance block, then the ten tables in order.

    `meta` carries `build_sha`, `criteria_hash`, the executor `config_hash` values seen in the
    week, and -- when the previous report's hash differs -- `previous_criteria_hash`, which is
    what turns the "definitions changed" line on.
    """
    year, week = meta.get("year"), meta.get("week")
    lines = [f"# Weekly report {year}-W{week:02d}" if isinstance(week, int)
             else f"# Weekly report {year}-W{week}", ""]
    lines += [
        f"- Week: {meta.get('start', PLACEHOLDER)} to {meta.get('end', PLACEHOLDER)} "
        f"(Monday 00:00 {meta.get('tz', PLACEHOLDER)}, ISO week {week})",
        f"- Build: `{meta.get('build_sha', PLACEHOLDER)}`",
        f"- Generated: {meta.get('generated_at', PLACEHOLDER)}",
        f"- Gate criteria hash: `{meta.get('criteria_hash', PLACEHOLDER)}`",
    ]
    config_hashes = meta.get("config_hashes") or []
    lines.append("- Executor `config_hash` values seen this week: "
                 + (", ".join(f"`{h}`" for h in config_hashes) if config_hashes else PLACEHOLDER))
    previous = meta.get("previous_criteria_hash")
    if previous and previous != meta.get("criteria_hash"):
        lines.append(f"- **The gate definitions changed since the previous report** "
                     f"(was `{previous}`). Every criterion below is judged on the new set.")
    if meta.get("confirmation"):
        lines.append(f"- {CONFIRMATION_NOTE}")
    lines.append("")
    for key in TABLE_KEYS:
        table = tables.get(key)
        if table is not None:
            lines += _render_table(table)
    return "\n".join(lines).rstrip() + "\n"


# --- selection ------------------------------------------------------------------------------


def select_cells(tables: dict[str, Table]) -> dict:
    """The cells and contrasts this report selects: everything its own families rejected.

    Family A and B come off table 4's `bh` column, family C off table 2's Holm column. Greyed
    cells never reach either column as "reject", so they cannot be selected.
    """
    cells = []
    t4 = tables.get("t4")
    if t4 is not None and "bh" in t4.columns:
        index = {name: t4.columns.index(name) for name in CELL_KEY}
        bh = t4.columns.index("bh")
        posterior = t4.columns.index("posterior")
        for row in t4.rows:
            if row[bh] != "reject":
                continue
            entry = {name: row[i] for name, i in index.items()}
            entry["panel"] = HEADLINE_PANEL
            entry["posterior_excludes_zero"] = cell_excludes_zero(row[posterior])
            cells.append(entry)
    contrasts = []
    t2 = tables.get("t2")
    holm_column = f"holm({CONTRAST_BENCHMARK})"
    if t2 is not None and holm_column in t2.columns:
        holm = t2.columns.index(holm_column)
        variant = t2.columns.index("variant")
        for row in t2.rows:
            if row[holm] == "reject":
                contrasts.append({"variant": row[variant], "benchmark_type": CONTRAST_BENCHMARK})
    return {"cells": cells, "contrasts": contrasts}


def write_selected(path, selection: dict) -> None:
    """Write the selection artefact. Sorted keys and a trailing newline: it is committed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(selection, indent=2, sort_keys=True, default=str) + "\n")


def read_selected(path) -> dict:
    return json.loads(Path(path).read_text())


def restrict_to_selection(tables: dict[str, Table], selection: dict) -> dict[str, Table]:
    """Tables 2 and 4 restricted to the selection's set; every other table passes through.

    This is the week-3 confirmation: only what week 2 froze is evaluated, on week-3 data. The
    §9.6 verdict is re-stated in table 4's note over the restricted set, judged on the
    posterior interval (ruling R13).
    """
    out = dict(tables)
    wanted_cells = {tuple(entry.get(name) for name in CELL_KEY)
                    for entry in selection.get("cells", [])}
    wanted_contrasts = {tuple(entry.get(name) for name in CONTRAST_KEY)
                        for entry in selection.get("contrasts", [])}

    t4 = tables.get("t4")
    if t4 is not None:
        index = [t4.columns.index(name) for name in CELL_KEY]
        rows = [row for row in t4.rows if tuple(row[i] for i in index) in wanted_cells]
        posterior = t4.columns.index("posterior")
        confirmed = sum(1 for row in rows if cell_excludes_zero(row[posterior]))
        note = (f"{CONFIRMATION_NOTE} {len(rows)} selected cell(s) evaluated; {confirmed} whose "
                f"posterior interval excludes zero (§9.6 needs {SIGNIFICANT_CELLS_REQUIRED}).")
        out["t4"] = with_rows(t4, rows, note)

    t2 = tables.get("t2")
    if t2 is not None:
        variant = t2.columns.index("variant")
        # Keyed on the benchmark too (M5): table 2 tests one contrast per variant, against
        # CONTRAST_BENCHMARK, so a selection naming another benchmark selects nothing here.
        rows = [row for row in t2.rows
                if (row[variant], CONTRAST_BENCHMARK) in wanted_contrasts]
        out["t2"] = with_rows(t2, rows,
                              f"{CONFIRMATION_NOTE} {len(rows)} selected contrast(s) evaluated.")
    return out


# --- meta -----------------------------------------------------------------------------------

_CONFIG_HASHES = text("""
    select distinct config_hash from orders
    where replay = false and placed_at >= :start and placed_at < :end
      and config_hash is not null
    order by config_hash
""")

_PREVIOUS_HASH = text("""
    select criteria_hash from gate_reports
    where evaluated_at < :start
    order by evaluated_at desc
    limit 1
""")


def build_meta(session: Session, settings, year: int, week: int, now: datetime | None = None,
               confirmation: bool = False) -> dict:
    """The provenance block: build, criteria hash, the config hashes the week's orders carry,
    and the previous report's criteria hash when one is on file."""
    start, end = week_bounds(year, week, settings.tz_local)
    window = {"start": start, "end": end}
    return {
        "year": year,
        "week": week,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "tz": settings.tz_local,
        "build_sha": settings.build_sha,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "criteria_hash": criteria_hash(CRITERIA_TEXT),
        "config_hashes": list(session.execute(_CONFIG_HASHES, window).scalars()),
        "previous_criteria_hash": session.execute(_PREVIOUS_HASH, window).scalar(),
        "confirmation": confirmation,
    }


def selection_document(tables: dict[str, Table], meta: dict) -> dict:
    """`select_cells` plus the provenance the artefact has to carry to be auditable."""
    document = select_cells(tables)
    document.update({"year": meta.get("year"), "week": meta.get("week"),
                     "criteria_hash": meta.get("criteria_hash"),
                     "build_sha": meta.get("build_sha"),
                     "generated_at": meta.get("generated_at"),
                     "panel": HEADLINE_PANEL})
    return document
