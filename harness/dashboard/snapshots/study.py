"""Study: is the strategy any good?

Spec §2.3. The rule that shapes this whole module is brief constraint 6: **the UI never computes
a mean.** Every table cell is read verbatim out of `report_cells` -- `text`, `estimate`,
`n_obs`, `n_clusters`, `lo`, `hi`, `flags` -- keyed `table_key -> row_key -> col_key`, and the
forest plot, the heatmap and the ledger are drawn from those cells. `text` is the exact string
`render_markdown` printed, so the surface cannot round a number differently from the report.

Two honesty requirements the reviews added:

* **The provisional label carries the cells' age.** `report_wtd` runs six-hourly
  (`report_wtd_period_s = 21_600`, and this phase does not change it), so a snapshot rebuilt
  every ten minutes can be showing cells five hours old. Saying "provisional" without saying how
  old is the kind of half-truth this design exists to avoid.
* **The equity curve carries `mtm_coverage`.** It is the one Study number that is not a stored
  cell: cash plus open positions valued at the live midpoint. A curve at 40 % coverage is a
  different number from one at 100 %, so the coverage travels per point, the sentence states it,
  and the marked line is drawn grey below `MTM_GREY_COVERAGE`.

The markdown is carried verbatim and **not** sanitized: the sanitizer strips `%`, `+`, `$`, `[`
and `]`, and applying it to a report would corrupt exactly the strings spec §1.1 says must be
shown as stored. The front end's DOM rule is the guard: the document goes into a `<pre>` as
`textContent`, with its `markdown_sha256` beside it, and no renderer is vendored.
"""

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.snapshots import base_payload, current_name, register_builder, section
from harness.report.tables import week_bounds
from harness.telemetry import sanitize_reason

log = logging.getLogger(__name__)

CADENCE_S = 600
#: Below this share of open contracts valued against a clean book, the marked line is grey.
MTM_GREY_COVERAGE = 0.5
#: The first ISO week the season has data for (spec §2.3: "ISO weeks from 37").
FIRST_WEEK = 37

STUDY_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                        "week", "year", "report_run_id", "provisional", "cell_age_s",
                        "generated_at", "cells", "equity", "annotations", "markdown",
                        "markdown_sha256", "weeks"})

_NEWEST_FINAL = text("""
    select id, generated_at, provisional, markdown, markdown_sha256, build_sha, criteria_hash
    from report_runs
    where year = :year and week = :week and provisional = false
    order by generated_at desc limit 1
""")
_NEWEST_ANY = text("""
    select id, generated_at, provisional, markdown, markdown_sha256, build_sha, criteria_hash
    from report_runs
    where year = :year and week = :week
    order by generated_at desc limit 1
""")
_CELLS = text("""
    select table_key, row_key, col_key, estimate, n_obs, n_clusters, lo, hi, text, flags
    from report_cells where report_run_id = :run_id
""")
_WEEKS = text("""
    select distinct year, week from report_runs order by year desc, week desc
""")
_NEWEST_PER_WEEK = text("""
    select distinct on (year, week) year, week, id
    from report_runs order by year, week, generated_at desc, id desc
""")
_STUDY_SNAPSHOTS = text("""
    select name, payload->>'report_run_id' as run_id from dashboard_snapshots
    where name like 'study:%'
""")
_EQUITY = text("""
    select ts, variant_id, cash, mtm_open, mtm_coverage
    from equity_snapshots
    where ts >= :start and ts < :end
    order by variant_id, ts
""")
_ANNOTATIONS = text("""
    select ts, kind, summary from operator_events
    where ts >= :start and ts < :end order by ts
""")


def parse_week(name: str) -> tuple[int, int]:
    """`study:2026-37` to `(2026, 37)`. The name has already passed the anchored pattern in
    `harness.dashboard.snapshots`, so nothing but digits reaches here."""
    year, _, week = name.partition(":")[2].partition("-")
    return int(year), int(week)


def weeks_available(session: Session) -> list[str]:
    return [f"{row.year}-{row.week}" for row in session.execute(_WEEKS)]


def stale_study_names(session: Session) -> list[str]:
    """Every `study:<year>-<week>` the scheduler should rebuild: a week whose newest
    `report_runs.id` differs from the `report_run_id` recorded inside its stored payload, and
    any week with a run and no snapshot at all.

    This is what makes addendum §0.1 hold: a closed week is rebuilt by the scheduler, not on
    demand by a page view, so spec §0.3's "no page view runs a query" has no exception. A week
    with no run has no snapshot, and the surface says so.
    """
    stored = {row.name: row.run_id for row in session.execute(_STUDY_SNAPSHOTS)}
    stale = []
    for row in session.execute(_NEWEST_PER_WEEK):
        name = f"study:{row.year}-{row.week}"
        if stored.get(name) != str(row.id):
            stale.append(name)
    return sorted(stale)


def _cells(session: Session, run_id: int) -> dict:
    out: dict[str, dict] = {}
    for row in session.execute(_CELLS, {"run_id": run_id}):
        table = out.setdefault(row.table_key, {}).setdefault(row.row_key, {})
        table[row.col_key] = {
            "estimate": float(row.estimate) if row.estimate is not None else None,
            "n_obs": row.n_obs, "n_clusters": row.n_clusters,
            "lo": float(row.lo) if row.lo is not None else None,
            "hi": float(row.hi) if row.hi is not None else None,
            # Shown as stored (spec §1.1): never sanitized, or the report's own "+3.1 %" and
            # "[-0.2, +6.4]" would come back mangled (ruling A-I5).
            "text": row.text,
            "flags": row.flags or {},
        }
    return out


def _equity(session: Session, start: datetime, end: datetime) -> dict:
    lanes: dict[str, dict] = {}
    for row in session.execute(_EQUITY, {"start": start, "end": end}):
        lane = lanes.setdefault(row.variant_id, {"variant": row.variant_id, "points": [],
                                                 "min_coverage": None})
        cash = float(row.cash)
        mtm = float(row.mtm_open) if row.mtm_open is not None else None
        coverage = float(row.mtm_coverage) if row.mtm_coverage is not None else None
        lane["points"].append([row.ts.isoformat(), cash,
                               (cash + mtm) if mtm is not None else None, coverage])
        if coverage is not None:
            lane["min_coverage"] = (coverage if lane["min_coverage"] is None
                                    else min(lane["min_coverage"], coverage))
    for lane in lanes.values():
        lane["cash"] = lane["points"][-1][1] if lane["points"] else None
        lane["mtm_grey"] = (lane["min_coverage"] is not None
                            and lane["min_coverage"] < MTM_GREY_COVERAGE)
    return {"grey_below": MTM_GREY_COVERAGE,
            "variants": [lanes[k] for k in sorted(lanes)]}


def _annotations(session: Session, start: datetime, end: datetime) -> list[dict]:
    return [{"ts": row.ts.isoformat(), "kind": row.kind,
             "summary": sanitize_reason(row.summary or "")}
            for row in session.execute(_ANNOTATIONS, {"start": start, "end": end})]


def _ledger_rows(cells: dict) -> list[dict]:
    """The variant ledger's rows as the sentence template wants them: one row key with the
    largest `n_clusters` any of its cells carries."""
    rows = []
    for row_key, columns in (cells.get("t1") or {}).items():
        clusters = [c["n_clusters"] for c in columns.values() if c["n_clusters"] is not None]
        rows.append({"row_key": row_key, "n_clusters": max(clusters) if clusters else None,
                     "text": next(iter(columns.values()))["text"] if columns else None})
    return rows


def _contrast_rows(cells: dict, benchmark: str) -> list[dict]:
    rows = []
    for row_key, columns in (cells.get("t2") or {}).items():
        cell = columns.get(benchmark)
        if cell is None:
            continue
        rows.append({"variant": row_key, "estimate": cell["estimate"], "lo": cell["lo"],
                     "hi": cell["hi"], "n_clusters": cell["n_clusters"]})
    return rows


def _declined_rows(cells: dict) -> list[dict]:
    rows = []
    for row_key, columns in (cells.get("t12") or {}).items():
        variant, _, rest = row_key.partition("/")
        kind, _, reason = rest.partition(":")
        estimator = ("clv_rejected_gap_outcomes" if kind == "rejected"
                     else "clv_skipped_intent_snapshot")
        cell = columns.get(estimator) or {}
        count = columns.get("count") or {}
        share = columns.get("share") or {}
        rows.append({"variant": variant, "kind": kind, "reason": reason,
                     "estimator": estimator,
                     "count": count.get("estimate"), "share": share.get("estimate"),
                     "estimate": cell.get("estimate"), "lo": cell.get("lo"),
                     "hi": cell.get("hi"), "n_clusters": cell.get("n_clusters")})
    return sorted(rows, key=lambda r: -(r["count"] or 0))


def build_study(session: Session, now: datetime, settings: Settings) -> dict:
    name = current_name.get() or f"study:{now.isocalendar().year}-{now.isocalendar().week}"
    year, week = parse_week(name)
    payload = base_payload(name, now, settings, CADENCE_S)
    payload["year"], payload["week"] = year, week
    payload["weeks"] = weeks_available(session)

    current = now.isocalendar()
    is_current = (year, week) == (current.year, current.week)
    run = session.execute(_NEWEST_ANY if is_current else _NEWEST_FINAL,
                          {"year": year, "week": week}).first()
    if run is None:
        payload.update({"report_run_id": None, "provisional": None, "cell_age_s": None,
                        "generated_at": None, "cells": {},
                        "equity": {"variants": [], "grey_below": MTM_GREY_COVERAGE},
                        "annotations": [], "markdown": None, "markdown_sha256": None})
        payload["sentences"] = {"ledger": sentences.study_ledger({"week": f"{year}-{week}"}),
                                "contrasts": sentences.study_contrasts({}),
                                "equity": sentences.study_equity({}),
                                "declined": sentences.study_declined({})}
        payload["readings"] = {"declined": []}
        return payload

    payload["report_run_id"] = run.id
    payload["provisional"] = bool(run.provisional)
    payload["generated_at"] = run.generated_at.isoformat()
    payload["cell_age_s"] = (now - run.generated_at).total_seconds()
    payload["markdown"] = run.markdown
    payload["markdown_sha256"] = run.markdown_sha256

    section(payload, "cells", lambda: _cells(session, run.id))
    start, end = week_bounds(year, week, settings.tz_local)
    section(payload, "equity", lambda: _equity(session, start, end))
    section(payload, "annotations", lambda: _annotations(session, start, end))

    cells = payload["cells"] if isinstance(payload["cells"], dict) else {}
    equity = payload["equity"] if isinstance(payload["equity"], dict) else {}
    declined = _declined_rows(cells)
    payload["sentences"] = {
        "ledger": sentences.study_ledger({"week": f"{year}-{week}",
                                          "provisional": payload["provisional"],
                                          "cell_age_s": payload["cell_age_s"],
                                          "rows": _ledger_rows(cells)}),
        "contrasts": sentences.study_contrasts({"benchmark": "pinnacle_t5",
                                                "rows": _contrast_rows(cells, "pinnacle_t5")}),
        "equity": sentences.study_equity(equity),
        "declined": sentences.study_declined({"rows": declined}),
    }
    payload["readings"] = {"declined": [sentences.study_declined_reading(row)
                                        for row in declined]}
    return payload


register_builder("study", build_study)
