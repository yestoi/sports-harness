"""Which class an exclusion belongs to, and which class a coverage outcome belongs to.

Addendum §1.4 (decision 4: "capacity exclusions separate from strategy rejections and
unreliable-data skips"). Two mappings over names that already exist -- nothing here decides
anything, and no executor or strategy behaviour reads this module. What it buys is that a
funnel, a report table and a coverage row can all say *why* a unit did not complete in the same
vocabulary, and that a new reason cannot be added without being classified: the exhaustiveness
tests in `tests/test_exclusion_classes.py` walk `plan.py`'s reason block and `run.py`'s
`LABEL_ORDER` and fail on a name this file does not carry.

Decision 4 names three classes. A fourth is required and is decision D5's: `kill_switch`,
`reprice`, `venue_move` and `expiry` are neither capacity, strategy nor data, and folding them
into any of the three would misattribute them. The three named classes keep their exact
membership.

**Annotations are not exclusions** (ruling I8). `drawdown_stop` labels what was true when a
signal was made and decides nothing (`ANNOTATION_LABELS`, never a `rejection_reason`), and
`no_book` is written on an order that *was placed*, recording only that the queue behind it was
unknowable (R10, `harness/execution/loop.py:1050-1059`). Both are classified -- a name with no
class is exactly what this module exists to prevent -- and both are excluded from every
exclusion total.
"""

#: The four exclusion classes, in report order.
CAPACITY = "capacity"
STRATEGY = "strategy_rejection"
DATA = "unreliable_data"
OPERATIONAL = "operational"
EXCLUSION_CLASSES: tuple[str, ...] = (CAPACITY, STRATEGY, DATA, OPERATIONAL)

#: Counted, reported and never folded into an exclusion total.
ANNOTATIONS = frozenset({"drawdown_stop", "no_book"})

#: Every executor reason (`harness/execution/plan.py:53-64`, plus the two literals ruling I8
#: promotes) and every strategy label (`harness/strategy/run.py` `LABEL_ORDER`), each in exactly
#: one class.
CLASS_OF: dict[str, str] = {
    # capacity: the shared and per-variant limits, which bind only where `apply_caps` is true.
    "exec_capacity": CAPACITY,
    "max_open": CAPACITY,
    "cap_per_bet": CAPACITY,
    "cap_per_game": CAPACITY,
    "cap_daily": CAPACITY,
    # strategy rejection: the strategy said no, or said no longer.
    "signal_rejected": STRATEGY,
    "no_target": STRATEGY,
    "edge_decay": STRATEGY,
    "post_only_reject": STRATEGY,
    "kickoff": STRATEGY,
    "source_allowed": STRATEGY,
    "sport_allowed": STRATEGY,
    "price_band": STRATEGY,
    "ttk": STRATEGY,
    "spread": STRATEGY,
    "volume": STRATEGY,
    "velocity": STRATEGY,
    "disagreement_ok": STRATEGY,
    "edge": STRATEGY,
    "min_contracts": STRATEGY,
    # unreliable data: we could not trust what we were pricing against.
    "book_dirty": DATA,
    "fair_stale": DATA,
    "unmatched": DATA,
    "has_fair": DATA,
    "not_stale": DATA,
    "match_confidence": DATA,
    # operational: ours, not the market's.
    "kill_switch": OPERATIONAL,
    "venue_move": OPERATIONAL,
    "reprice": OPERATIONAL,
    "expiry": OPERATIONAL,
    "drawdown_stop": OPERATIONAL,   # an annotation; see ANNOTATIONS
    "no_book": OPERATIONAL,         # an annotation on a placement; see ANNOTATIONS
}

#: The coverage classes (ruling C2), the vocabulary `coverage_samples.outcome` reduces to.
COVERAGE_CLASSES: tuple[str, ...] = (
    "complete", "pending", "not_scheduled", "budget", "data", "instrument")

#: Every outcome `harness/ops/coverage.py` can write, in exactly one coverage class. Task 4's
#: `coverage.COVERAGE_OUTCOMES` is `tuple(COVERAGE_CLASS_OF)`, so the writer's vocabulary and
#: this map are one list and `coverage.record` refuses anything else at the write.
COVERAGE_CLASS_OF: dict[str, str] = {
    "completed": "complete",
    "scheduled": "pending",
    # the cadence in force said nothing was due, which is not a miss
    "not_due": "not_scheduled",
    "cadence_none": "not_scheduled",
    # a budget ran out: ours, measurable, and the thing §1.5 is about
    "budget": "budget",
    "budget_stage_skipped": "budget",
    "skipped_trades": "budget",
    "skipped_ladders": "budget",
    "skipped_alternates": "budget",
    # the data was not there to work with
    "http_error": "data",
    "no_fair": "data",
    "no_gap": "data",
    # the instrument itself produced nothing, or bounded itself
    "no_signal": "instrument",
    "variant_skipped": "instrument",
    "truncated": "instrument",
}


def class_of(name: str) -> str:
    """The exclusion class of one reason or label. Raises `KeyError` naming the reason: a
    reason nobody classified is a category a funnel would drop silently, which is the failure
    this module exists to make loud."""
    try:
        return CLASS_OF[name]
    except KeyError:
        raise KeyError(f"{name!r} has no exclusion class; add it to CLASS_OF") from None


def exclusion_totals(counts: dict[str, int]) -> dict[str, int]:
    """Per-class sums over `{reason: count}`, annotations excluded (ruling I8).

    Every class is present even at zero, so a reader never has to tell "no capacity exclusions"
    from "capacity not reported".
    """
    totals = {name: 0 for name in EXCLUSION_CLASSES}
    for name, count in counts.items():
        if name in ANNOTATIONS:
            continue
        totals[class_of(name)] += int(count)
    return totals
