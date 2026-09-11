"""The order-audit register: which stored orders are under audit, and what their status is.

Addendum 0.4 and D4. A **code constant**, not a document, for one reason: `docs/` is not copied
into the container image, and the weekly report is rendered inside the container. A register the
report cannot read is a register that silently reports nothing.

The register labels, it never excludes. `harness/report/gate.py`'s criteria are untouched by an
entry here (R1): a pending audit appears as a t13 row and as a count of the gate variant's
actual filled orders that are under audit, and the gate's own definition of a fill event is
exactly what it was. Milestone 6B publishes the order-157 tape audit and edits the status here.
"""
from dataclasses import dataclass

#: `pending` -- under audit, no verdict yet. `validated` -- the stored row matches the tape.
#: `corrected` -- the row was wrong and has been corrected, with the correction recorded in the
#: journal. `unverifiable` -- the tape cannot settle it either way, which is a finding, not a
#: pass.
AUDIT_STATUSES = ("pending", "validated", "corrected", "unverifiable")


@dataclass(frozen=True)
class Audit:
    """One order's audit state. `since` is an ISO-8601 date (`YYYY-MM-DD`), pinned rather than
    left to the writer (design review Minor 5), so t13's rendered note is stable."""

    status: str
    note: str
    since: str


#: U8's "order 157". Seeded with the one order the roadmap names; 6B replaces the status.
ORDER_AUDITS: dict[int, Audit] = {
    157: Audit("pending",
               "fill history not uniquely identified; tape audit is milestone 6B",
               "2026-09-11"),
}
