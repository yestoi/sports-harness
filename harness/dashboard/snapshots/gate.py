"""Gate: the go-live gate as evidence, in the shape it will be read in this October.

Spec §2.4. Everything here is stored: the criteria the evaluation was made against, the values
it measured, the hash of the definitions, the date. Nothing is recomputed and nothing is
projected -- the one decision this surface exists to support is a decision about evidence that
already exists, and a surface that recomputed a criterion would be showing a different gate from
the one `harness gate` stored.

The pre-registered primary is rendered in the same twelve-row shape beside the gate variant,
labelled `reported, not gated`, so the two can be read together without the primary's row ever
looking like a verdict.

**Never shown here.** A projection. A recomputed criterion. Any variant under a name other than
its registered one. A button.
"""

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.dashboard import sentences
from harness.dashboard.snapshots import base_payload, register_builder, section
#: The status vocabulary is the gate's own, imported rather than restated: `evaluate_all` writes
#: `CriterionResult.as_json()`, whose `status` is one of `"passed"`, `"failed"`,
#: `"insufficient"`, and whose sample size is `n_obs`/`n_clusters` -- there is no `n` key. A
#: surface that spelled either itself would render an always-empty failing list and an always-zero
#: pass count against a production row, while passing its own tests.
from harness.report.gate import CRITERIA, FAILED, INSUFFICIENT, PASSED

log = logging.getLogger(__name__)

#: 300 s, up from 60 by fix 31. `harness gate` writes at most one evaluation a day, so a
#: minute's freshness was never worth anything the surface could show; five minutes is still far
#: inside the staleness ladder and costs the machine a fifth of the reads.
CADENCE_S = 300
#: How far back the per-criterion history reaches (spec §2.4 item 2).
HISTORY_LIMIT = 200

#: The stored keys that are criteria, and nothing else `criteria_json` may ever carry beside
#: them (review I1). `evaluate_all` writes an `eligibility` key into the same object once the
#: dormant gate eligibility mechanism (Task 4) is switched on; without this filter that key
#: would iterate as a thirteenth criterion here and render a phantom row with every field
#: `None`. Read from `CRITERIA` rather than restated, so a future sibling key needs no change
#: here either.
_CRITERION_NAMES = frozenset(c.name for c in CRITERIA)

GATE_KEYS = frozenset({"build_sha", "now", "cadence_s", "sentences", "readings",
                       "verdict", "criteria", "variants", "history", "standing_text"})

STANDING_TEXT = (
    "Paper only. Live trading is a separate legal decision, taken by a person, and this loop "
    "never makes it. The criteria and their thresholds were fixed before the data was "
    "collected; the hash beside the date is over those definitions."
)

#: Reads only from gate_reports and from strategy_variants -- the two tables spec §2.4 names,
#: never one of the five forbidden tape tables or any order/fill/signal table. Neither takes a
#: time bound and neither needs one: `harness gate` appends at most one evaluation a day and
#: `strategy_variants` holds one row per registered variant, so both are tens to hundreds of
#: rows for a season. `_HISTORY` carries the only cap here, `HISTORY_LIMIT`, because it is the
#: one read whose row count grows with every evaluation rather than with the variant list.
_NEWEST = text("select max(evaluated_at) from gate_reports")
_ROWS_AT = text("""
    select g.variant_id, g.gate_variant, g.passed, g.criteria_json, g.criteria_hash,
           g.evaluated_at, v.name, v.tier
    from gate_reports g
    left join strategy_variants v on v.variant_id = g.variant_id
    where g.evaluated_at = :at
    order by g.gate_variant desc, v.name
""")
_HISTORY = text("""
    select g.evaluated_at, g.variant_id, g.passed, g.criteria_json
    from gate_reports g
    where g.gate_variant = true
    order by g.evaluated_at desc
    limit :limit
""")


def _criterion_rows(criteria_json: dict) -> list[dict]:
    """One row per stored criterion, in the order the evaluation stored them.

    The keys are `CriterionResult.as_json()`'s: `value`, `threshold`, `passed`, `status`,
    `n_obs`, `n_clusters`, `definition`, `fn`, `detail`. `n` is **not** among them -- the sample
    size the confidence phrase reads is `n_clusters`, games rather than bets, and `n_obs` travels
    beside it so the surface can show both.
    """
    rows = []
    for name, body in (criteria_json or {}).items():
        if name not in _CRITERION_NAMES:
            continue
        body = body if isinstance(body, dict) else {}
        rows.append({"name": name,
                     "definition": body.get("definition"),
                     "threshold": body.get("threshold"),
                     "value": body.get("value"),
                     "passed": body.get("passed"),
                     "n": body.get("n_clusters"),
                     "n_obs": body.get("n_obs"),
                     "n_clusters": body.get("n_clusters"),
                     "fn": body.get("fn"),
                     "status": body.get("status")})
    return rows


def _history(session: Session) -> dict:
    out: dict[str, list] = {}
    for row in session.execute(_HISTORY, {"limit": HISTORY_LIMIT}):
        for name, body in (row.criteria_json or {}).items():
            if name not in _CRITERION_NAMES:
                continue
            body = body if isinstance(body, dict) else {}
            out.setdefault(name, []).append({"evaluated_at": row.evaluated_at.isoformat(),
                                             "value": body.get("value"),
                                             "status": body.get("status"),
                                             "n": body.get("n_clusters")})
    return out


def _newest_rows(session: Session) -> list[dict] | None:
    """`_NEWEST` and `_ROWS_AT`, guarded together (ruling A-M2): both read small tables, but a
    failure of either must not crash the build the way an unguarded query does elsewhere in this
    module -- only `_history` was wrapped. Returns `None` for both "no evaluation stored yet"
    and a failed read; the two render identically (no verdict, no criteria, no variants), and
    the exception is logged either way so a real failure is still diagnosable."""
    try:
        at = session.execute(_NEWEST).scalar()
        if at is None:
            return None
        return [dict(row._mapping) for row in session.execute(_ROWS_AT, {"at": at})]
    except Exception as exc:  # noqa: BLE001 - a read here must not cost the whole build
        log.warning("gate newest-rows read failed: %s", type(exc).__name__)
        session.rollback()
        return None


def build_gate(session: Session, now: datetime, settings: Settings) -> dict:
    payload = base_payload("gate", now, settings, CADENCE_S)
    payload["standing_text"] = STANDING_TEXT
    rows = _newest_rows(session)
    if not rows:
        payload.update({"verdict": {}, "criteria": [], "variants": [], "history": {}})
        payload["sentences"] = {"verdict": sentences.gate_verdict({}),
                                "criteria": sentences.gate_criterion({})}
        payload["readings"] = {"criteria": []}
        return payload

    gate_row = next((r for r in rows if r["gate_variant"]), rows[0])
    criteria = _criterion_rows(gate_row["criteria_json"])
    payload["criteria"] = criteria
    payload["verdict"] = {
        "passing": bool(gate_row["passed"]),
        # The registered name, never a display name of our own (spec §2.4 never-shown).
        "variant": gate_row["name"] or gate_row["variant_id"],
        "variant_id": gate_row["variant_id"],
        "evaluated_at": gate_row["evaluated_at"].isoformat(),
        "criteria_hash": gate_row["criteria_hash"],
        "n_total": len(criteria),
        "n_pass": sum(1 for c in criteria if c["status"] == PASSED),
        "n_fail": sum(1 for c in criteria if c["status"] == FAILED),
        "n_insufficient": sum(1 for c in criteria if c["status"] == INSUFFICIENT),
    }
    payload["variants"] = [{
        "variant_id": r["variant_id"], "name": r["name"] or r["variant_id"], "tier": r["tier"],
        "gate_variant": bool(r["gate_variant"]), "passed": bool(r["passed"]),
        "note": None if r["gate_variant"] else "reported, not gated",
        "criteria": _criterion_rows(r["criteria_json"]),
    } for r in rows]
    section(session, payload, "history", lambda: _history(session))

    payload["sentences"] = {"verdict": sentences.gate_verdict(payload["verdict"]),
                            "criteria": sentences.gate_criterion({"criteria": criteria})}
    payload["readings"] = {"criteria": [sentences.gate_criterion_reading(row)
                                        for row in criteria]}
    return payload


register_builder("gate", build_gate)
