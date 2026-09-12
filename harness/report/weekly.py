"""Rendering, the selection artefact, and the week-3 confirmation restriction.

The report is Markdown because a human reads it on Monday morning and because it commits
cleanly beside the pre-registration record. The selection artefact is JSON because week 3 has
to read back exactly what week 2 chose, and a diff of that file is the audit trail that the
confirmation set was frozen before any week-3 data existed.

Every cell prints its own two counts, so the reader never has to trust an estimate without
knowing how many games it came from: `grey` marks a cell below 10 game clusters (outside every
BH/Holm family and outside the §9.6 count) and `flag` one below 30.
"""

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.db.models import ReportCell, ReportRun
from harness.report import criteria_hash
from harness.report.amendments import AMENDMENTS
from harness.report.tables import (
    CONTRAST_BENCHMARK,
    GREY_CLUSTERS,
    HEADLINE_PANEL,
    NOT_COLLECTED,
    PLACEHOLDER,
    RENDER_ORDER,
    SIGNIFICANT_CELLS_REQUIRED,
    TABLE_KEYS,
    Table,
    is_cell,
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

#: Addendum 0.8 and design review C2. The direction count is printed **beside** the registered
#: two-sided count and is not a condition. The record's §9.6 rule is "at least three cells whose
#: 90 % CI excludes zero after shrinkage" (Analysis plan line 68), judged on the posterior
#: interval, two-sided. Making the stored direction a condition turns that into a one-sided
#: rule, which is a success-threshold change under R1 and only a dated user decision makes it.
#: The phase report's Needs-you carries the decision; this module prints the number and applies
#: nothing. D6's reversal is therefore the right way round: the decision is needed to *add* the
#: condition, not to remove it.
DIRECTION_NOTE = "proposed one-sided reading, not in force"

#: R:232-234: the bullets live inside a fenced block that says what they are. The Monday duty
#: acts on the tables, never on the bullets, and the fence is what makes that visible on the page
#: rather than only in a runbook.
ANNOTATION_HEADER = "## Model notes (model-written, unverified)"


# --- rendering ------------------------------------------------------------------------------


def format_cell(value: Any) -> str:
    """One cell as Markdown. Never empty: an empty cell in a scored table reads as a zero.

    Public since phase 5: `harness/report/render_for_model.py` renders the same cells for the
    weekly annotator, and the annotator's citation check compares a bullet's numbers against a
    cell's rendered text. Two formatters would let the model be checked against a string the
    reader never sees, so there is one.
    """
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
        lines.append("| " + " | ".join(format_cell(value) for value in row) + " |")
    if table.note:
        lines += ["", f"_{table.note}_"]
    return lines + [""]


def render_markdown(tables: dict[str, Table], meta: dict) -> str:
    """The whole report: a provenance block, then the tables in order.

    `meta` carries `build_sha`, `criteria_hash`, the executor `config_hash` values seen in the
    week, and -- when the previous report's hash differs -- `previous_criteria_hash`, which is
    what turns the "definitions changed" line on. `meta["eligibility"]` (addendum 0.9) prints one
    "Excluded by Amendment n" line per recorded amendment.
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
    # Addendum 0.9: one line per amendment, including the ones that exclude nothing. A reader
    # who sees four lines against five amendments cannot tell clean from forgotten.
    eligibility = meta.get("eligibility") or {}
    for number in sorted(eligibility):
        entry = eligibility[number]
        runs = entry.get("excluded_runs")
        if not runs:
            lines.append(f"- Excluded by Amendment {number}: none ({entry.get('what', '')})")
        else:
            lines.append(f"- Excluded by Amendment {number}: {entry.get('orders', 0)} orders, "
                         f"{entry.get('signals', 0)} signals (runs {runs[0]}-{runs[1]}); "
                         f"the order count omits orders with no gap snapshot, which cannot be "
                         f"attributed to a run (Minor M4)")
    lines.append("")
    annotation = meta.get("annotation")
    if annotation:
        lines += [ANNOTATION_HEADER, "",
                  "Written by `claude-opus-5` from the tables below. Every bullet cites a cell "
                  "and no number in one is absent from a cited cell, but nothing here has been "
                  "checked by a person. The Monday duty acts on the tables, never on these.",
                  "", "```"]
        lines += [f"- {bullet}" for bullet in annotation]
        lines += ["```", ""]
    # `RENDER_ORDER`, not `TABLE_KEYS`: the operational diagnostic is what the Monday duty reads
    # first (addendum 0.3). `render_for_model` keeps `TABLE_KEYS` order on purpose.
    for key in RENDER_ORDER:
        table = tables.get(key)
        if table is not None:
            lines += _render_table(table)
    return "\n".join(lines).rstrip() + "\n"


# --- selection ------------------------------------------------------------------------------


def _direction(value: Any) -> int | None:
    """`+1` or `-1` for the sign of a cell's estimate, `None` when it has no signed estimate.

    Stored at selection (addendum 0.8). A zero or NaN estimate has no direction and says so,
    rather than being rounded into one.
    """
    if not is_cell(value):
        return None
    estimate = value[0]
    if estimate != estimate or estimate == 0:
        return None
    return 1 if estimate > 0 else -1


def _n_clusters(value: Any) -> int | None:
    """A cell's game-cluster count, stored beside the direction so the confirmation report can
    say why a selected cell was insufficient without re-deriving it."""
    return value[2] if is_cell(value) else None


def _has_floor(value: Any) -> bool:
    """Whether a week-3 cell clears the registered ten-game-cluster floor.

    A **correction**, not a new rule (design review C2): the pre-registration record's Analysis
    plan already greys a cell below `GREY_CLUSTERS` game clusters (line 63) and excludes greyed
    cells from every family (line 66). The confirmation count had not been applying it, so a
    week-3 cell resting on four games could confirm a selection made on forty.
    """
    return is_cell(value) and not is_grey(value)


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
            entry["direction"] = _direction(row[posterior])
            entry["n_clusters"] = _n_clusters(row[posterior])
            cells.append(entry)
    contrasts = []
    t2 = tables.get("t2")
    holm_column = f"holm({CONTRAST_BENCHMARK})"
    if t2 is not None and holm_column in t2.columns:
        holm = t2.columns.index(holm_column)
        variant = t2.columns.index("variant")
        # The contrast's effect lives in the benchmark's own column; the Holm column is the
        # decision, not the estimate, so the direction is read off the effect.
        effect = t2.columns.index(CONTRAST_BENCHMARK)
        for row in t2.rows:
            if row[holm] == "reject":
                contrasts.append({"variant": row[variant],
                                  "benchmark_type": CONTRAST_BENCHMARK,
                                  "direction": _direction(row[effect]),
                                  "n_clusters": _n_clusters(row[effect])})
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

    Table 4's note prints every denominator behind that verdict -- selected, evaluated,
    insufficient, no estimate, missing, confirmed -- so a reader can see why the count is what
    it is. A week-3 cell below the registered ten-game-cluster floor is counted `insufficient`
    and never confirmed (`_has_floor`; a restoration of the record's own rule, not a new one).
    A selected cell with no posterior at all (`PLACEHOLDER` or `NOT_COLLECTED`) is a different
    population from one that has a posterior but is below the floor, so it is counted under its
    own `no estimate` label rather than folded into `insufficient` (Minor M2). The count of
    confirmed cells lying on the stored direction is printed beside the registered two-sided
    count and is applied nowhere: making it a condition would be a success-threshold change
    under R1 (see `DIRECTION_NOTE`).
    """
    out = dict(tables)
    wanted_cells = {tuple(entry.get(name) for name in CELL_KEY)
                    for entry in selection.get("cells", [])}
    wanted_contrasts = {tuple(entry.get(name) for name in CONTRAST_KEY)
                        for entry in selection.get("contrasts", [])}

    t4 = tables.get("t4")
    if t4 is not None:
        index = [t4.columns.index(name) for name in CELL_KEY]
        posterior = t4.columns.index("posterior")
        directions = {tuple(entry.get(name) for name in CELL_KEY): entry.get("direction")
                      for entry in selection.get("cells", [])}
        rows = [row for row in t4.rows if tuple(row[i] for i in index) in wanted_cells]
        present = {tuple(row[i] for i in index) for row in t4.rows}
        # Minor M3: `selected` and `missing` are counted over the deduplicated `wanted_cells`
        # set, not a list built straight from `selection["cells"]`, so a duplicate cell key
        # there cannot inflate `selected` past `evaluated + insufficient + no estimate +
        # missing`.
        missing = sum(1 for key in wanted_cells if key not in present)
        eligible = [row for row in rows if _has_floor(row[posterior])]
        no_estimate = sum(1 for row in rows if not is_cell(row[posterior]))
        insufficient = len(rows) - len(eligible) - no_estimate
        confirmed = [row for row in eligible if cell_excludes_zero(row[posterior])]
        on_direction = sum(
            1 for row in confirmed
            if directions.get(tuple(row[i] for i in index)) is not None
            and directions[tuple(row[i] for i in index)] == _direction(row[posterior]))
        note = (f"{CONFIRMATION_NOTE} "
                f"selected {len(wanted_cells)} / evaluated {len(rows)} / "
                f"insufficient (< {GREY_CLUSTERS} clusters) {insufficient} / "
                f"no estimate {no_estimate} / "
                f"missing (no row) {missing} / "
                f"confirmed (two-sided) {len(confirmed)} "
                f"(§9.6 needs {SIGNIFICANT_CELLS_REQUIRED}) / "
                f"of which on the selected direction {on_direction} "
                f"({DIRECTION_NOTE}).")
        out["t4"] = with_rows(t4, rows, note)

    t2 = tables.get("t2")
    if t2 is not None:
        variant = t2.columns.index("variant")
        # Keyed on the benchmark too (M5): table 2 tests one contrast per variant, against
        # CONTRAST_BENCHMARK, so a selection naming another benchmark selects nothing here.
        rows = [row for row in t2.rows
                if (row[variant], CONTRAST_BENCHMARK) in wanted_contrasts]
        out["t2"] = with_rows(
            t2, rows,
            f"{CONFIRMATION_NOTE} selected {len(selection.get('contrasts', []))} / "
            f"evaluated {len(rows)} contrast(s).")
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

#: Addendum 0.9. Bound: the same week window on `orders.placed_at` that `_CONFIG_HASHES` above
#: already uses, with `market_gap_snapshots` reached by primary key -- `orders` carries no
#: `run_id`, so the run an order came from is its gap snapshot's. An order with no gap snapshot
#: cannot be attributed to a run and is not counted, which is the honest answer rather than a
#: guess.
_ELIGIBILITY_ORDERS = text("""
    select count(*) from orders o
    join market_gap_snapshots g on g.id = o.gap_snapshot_id
    where o.replay = false and o.placed_at >= :start and o.placed_at < :end
      and g.run_id between :lo and :hi
""")

#: Bound: the same week window `_T1_SIGNALS` (`harness/report/tables.py:298`) and `_T1_COVERAGE`
#: (`:336`) already read `signals` on. `signals`' indexes lead on variant and on market, not on
#: time, so a bare `created_at` window **is** a scan of that table -- but it is a scan the weekly
#: render and the hourly provisional stage that shares it already perform twice. This adds two
#: more of the same scan per render (one per amendment that carries a range), not a new kind of
#: read. Making it an index seek is a schema change and is out of scope (§2, no DDL).
_ELIGIBILITY_SIGNALS = text("""
    select count(*) from signals
    where replay = false and created_at >= :start and created_at < :end
      and run_id between :lo and :hi
""")

#: The annotator writes one `report_annotations` row per `report_runs` row, keyed off whichever
#: run was `pending_report` (`harness/research/annotate.py`) at the time it ran -- never the
#: render this call is producing, which does not exist yet when `build_meta` runs ahead of
#: `persist_report` (Task 12b, `harness/cli.py`'s report command). So this joins on `(year, week)`
#: rather than a specific `report_run_id`: a re-render of an already-annotated week still finds
#: the bullets an earlier render of the same week collected (fix round 2, C2).
_LATEST_ANNOTATION = text("""
    select a.bullets
    from report_annotations a
    join report_runs r on r.id = a.report_run_id
    where r.year = :year and r.week = :week
    order by a.created_at desc
    limit 1
""")


def _eligibility(session: Session, window: dict) -> dict[int, dict]:
    """Per amendment, how many of this week's non-replay orders and signals sit inside its
    excluded run-id range (addendum 0.9).

    Every amendment appears, including the ones that exclude nothing: a reader who sees four
    lines and five amendments cannot tell whether the fifth was clean or forgotten.

    This does not change the gate. The gate is cumulative over the whole paper run and already
    excludes replay rows, and an epoch label excludes nothing by itself (U8, 6A). An eligibility
    *mechanism* for the gate is 6A/6B's.
    """
    out: dict[int, dict] = {}
    for amendment in AMENDMENTS:
        orders = signals = 0
        if amendment.excluded_runs is not None:
            lo, hi = amendment.excluded_runs
            params = dict(window, lo=lo, hi=hi)
            orders = int(session.execute(_ELIGIBILITY_ORDERS, params).scalar() or 0)
            signals = int(session.execute(_ELIGIBILITY_SIGNALS, params).scalar() or 0)
        out[amendment.number] = {
            "orders": orders, "signals": signals,
            "excluded_runs": list(amendment.excluded_runs) if amendment.excluded_runs else None,
            "recorded": amendment.recorded, "what": amendment.what}
    return out


def build_meta(session: Session, settings, year: int, week: int, now: datetime | None = None,
               confirmation: bool = False) -> dict:
    """The provenance block: build, criteria hash, the config hashes the week's orders carry,
    the previous report's criteria hash when one is on file, the per-amendment eligibility
    counts (addendum 0.9; `meta["eligibility"]`, keyed by amendment number), and the annotator's
    bullets when the week has a surviving set (addendum §1.5; `render_markdown` renders
    `meta["annotation"]` inside the fenced "model-written, unverified" block when it is set)."""
    start, end = week_bounds(year, week, settings.tz_local)
    window = {"start": start, "end": end}
    meta = {
        "year": year,
        "week": week,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "tz": settings.tz_local,
        "build_sha": settings.build_sha,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "criteria_hash": criteria_hash(),
        "config_hashes": list(session.execute(_CONFIG_HASHES, window).scalars()),
        "previous_criteria_hash": session.execute(_PREVIOUS_HASH, window).scalar(),
        "confirmation": confirmation,
    }
    meta["eligibility"] = _eligibility(session, window)
    bullets = session.execute(_LATEST_ANNOTATION, {"year": year, "week": week}).scalar()
    if bullets:
        meta["annotation"] = list(bullets)
    return meta


def _cell_fields(value: Any) -> dict:
    """One cell's `report_cells` columns (Task 12b): the CI quintet when the cell is one,
    `text` always the same rendered string `render_markdown` shows, and `flags` the same
    greyed/flagged/not_collected rules that markdown already carries as a marker or a note."""
    if is_cell(value):
        estimate, n_obs, n_clusters, lo, hi = value
        return {"estimate": estimate, "n_obs": n_obs, "n_clusters": n_clusters, "lo": lo,
               "hi": hi, "text": format_cell(value)[:64],
               "flags": {"greyed": is_grey(value), "flagged": is_flagged(value),
                        "not_collected": False}}
    return {"estimate": None, "n_obs": None, "n_clusters": None, "lo": None, "hi": None,
           "text": format_cell(value)[:64],
           "flags": {"greyed": False, "flagged": False, "not_collected": value == NOT_COLLECTED}}


def persist_report(session: Session, tables: dict[str, Table], meta: dict, year: int, week: int,
                   provisional: bool, markdown: str | None) -> int:
    """Write one `report_runs` row and every cell of every table, in the caller's own
    transaction (`harness report` commits it beside the markdown write; `report_wtd` commits it
    beside its `job_state` bookkeeping). Returns the new `report_runs.id`.

    A re-run of the same week is a new row, never an overwrite (design spec §3.7): the UI reads
    the newest non-provisional run for a closed week, and the provisional hourly runs are their
    own trail of what the report looked like as the week went on.
    """
    generated_at = meta.get("generated_at")
    if isinstance(generated_at, str):
        generated_at = datetime.fromisoformat(generated_at)
    run = ReportRun(
        year=year, week=week, generated_at=generated_at or datetime.now(timezone.utc),
        provisional=provisional, build_sha=meta.get("build_sha") or "",
        criteria_hash=meta.get("criteria_hash") or "",
        config_hashes=list(meta.get("config_hashes") or []),
        markdown=markdown,
        markdown_sha256=hashlib.sha256(markdown.encode()).hexdigest() if markdown else None,
    )
    session.add(run)
    session.flush()
    n = 0
    for table_key, table in tables.items():
        # `Table.row_key` is only the first column, and several tables (t4's price-bucket x
        # ttk x sport x market_type grid, in particular) repeat that column across many rows --
        # so a run of the same base key within one table is disambiguated with a suffix, which
        # keeps the composite primary key (report_run_id, table_key, row_key, col_key) unique
        # without changing what `Table.row_key` itself means.
        seen: dict[str, int] = {}
        for row in table.rows:
            base = table.row_key(row)[:60]
            seen[base] = seen.get(base, 0) + 1
            row_key = base if seen[base] == 1 else f"{base}#{seen[base]}"
            for col_key, value in zip(table.columns, row):
                session.add(ReportCell(report_run_id=run.id, table_key=table_key,
                                       row_key=row_key, col_key=col_key, **_cell_fields(value)))
                n += 1
    session.flush()
    return run.id


def selection_document(tables: dict[str, Table], meta: dict) -> dict:
    """`select_cells` plus the provenance the artefact has to carry to be auditable."""
    document = select_cells(tables)
    document.update({"year": meta.get("year"), "week": meta.get("week"),
                     "criteria_hash": meta.get("criteria_hash"),
                     "build_sha": meta.get("build_sha"),
                     "generated_at": meta.get("generated_at"),
                     "panel": HEADLINE_PANEL})
    return document
