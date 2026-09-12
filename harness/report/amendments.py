"""Every recorded amendment, as data the weekly report can read from inside the container.

Addendum 0.9. Copied from `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md`, which
is the authority; `tests/test_amendments.py` reads that file's own `## Amendment <n>` headings so
the copy cannot silently fall behind. `docs/` is not in the image, which is why this is code.

What it is for: the provenance block prints, per amendment, how many of the week's own non-replay
orders and signals fall inside that amendment's excluded run-id range. A reader of a weekly
report can then see at a glance whether the week they are reading is affected by a measurement
amendment at all, instead of holding the ranges in their head.

What it is **not** for: the go-live gate. The gate is cumulative over the whole paper run and
already excludes replay rows; an epoch label excludes nothing by itself. An eligibility
*mechanism* for the gate is milestone 6A/6B's and is out of scope here (U8).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Amendment:
    """One amendment. `excluded_runs` is the inclusive `(from_run, to_run)` pre-fix range the
    record states, or `None` when the amendment excludes nothing -- a registration (Amendment 3),
    or a measurement fix whose pre-fix range is empty (Amendment 5). `tables` names the report
    tables the record says are touched; it is empty when none is."""

    number: int
    recorded: str                       # ISO-8601 date
    deploy_sha: str | None
    excluded_runs: tuple[int, int] | None
    tables: tuple[str, ...]
    what: str


AMENDMENTS: tuple[Amendment, ...] = (
    Amendment(
        1, "2026-09-07", None, None, ("t4",),
        "the pricing clock fix: signals before it carry a wrong `not_stale` label. The record "
        "states no run-id range for it, so nothing is counted here"),
    Amendment(
        2, "2026-09-07", "659ba67", (1, 2320), ("t3", "t4"),
        "feed_kind, feed_lag_s and stale_allowance_s arrive; the `not_stale` label changes. "
        "Runs 1-2320 carry NULL feed columns and the old flat rule"),
    Amendment(
        3, "2026-09-08", "ae1e86c", None, (),
        "the NO-side variant `sharp_two_sided` is registered. A registration excludes nothing"),
    Amendment(
        4, "2026-09-08", "a193fd0", (344, 4327), ("t1", "t2"),
        "pricing order and budget. In the pre-fix range the primary and the gate variant were "
        "scored on under half the ticks and the secondaries rotated by tick size"),
    Amendment(
        5, "2026-09-11", None, None, (),
        "the America/Chicago ISO week as the measurement key. The pre-fix range is empty: the "
        "only Sunday 19:00-23:59 CT provisional window before the deploy precedes the first "
        "paper order"),
)
