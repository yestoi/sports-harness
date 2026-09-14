"""`harness/ops/exclusions.py`: every reason and label classified exactly once.

Addendum §1.4. Two maps, both proved exhaustive here rather than by inspection: a reason or a
label that no map carries is a category the funnel would silently drop, and a coverage outcome
that no map carries is a row `coverage.record` must refuse at the write.
"""

from harness.execution import plan as plan_module
from harness.ops.exclusions import (ANNOTATIONS, CAPACITY, CLASS_OF, COVERAGE_CLASS_OF,
                                    COVERAGE_CLASSES, DATA, EXCLUSION_CLASSES, OPERATIONAL,
                                    STRATEGY, class_of, exclusion_totals)
from harness.strategy.run import ANNOTATION_LABELS, CAP_LABELS, LABEL_ORDER

#: The reason constants `harness/execution/plan.py` defines, read off the module rather than
#: retyped: the point of the exhaustiveness test is that a constant added there cannot be added
#: without classifying it.
REASON_CONSTANTS = {name: value for name, value in vars(plan_module).items()
                    if name.isupper() and isinstance(value, str) and name not in
                    {"REJECTED", "YES", "NOT_APPLICABLE", "ROUND_HALF_UP"}}


def test_every_executor_reason_is_classified_exactly_once():
    """Expected: every reason constant in `plan.py`'s block appears in `CLASS_OF` once.

    Computed independently of the code: the block at `plan.py:53-64` holds twelve names, and
    ruling I8 adds the two literals this task promotes (`expiry`, `no_book`), so fourteen
    reasons must be classified. A fifteenth added later without a class fails here.
    """
    reasons = {value for value in REASON_CONSTANTS.values()}
    assert {"expiry", "no_book"} <= reasons
    for reason in reasons:
        assert reason in CLASS_OF, f"{reason} is not classified"
        assert CLASS_OF[reason] in EXCLUSION_CLASSES


def test_every_strategy_label_is_classified_exactly_once():
    """Expected: all eighteen `LABEL_ORDER` entries are classified, the four `CAP_LABELS` as
    `capacity` and `drawdown_stop` as `operational`.

    Computed independently: `LABEL_ORDER` is the strategy's own decision order and every entry
    is either a filter (strategy or data), a cap (capacity) or an annotation. Counting them by
    hand from `harness/strategy/run.py:29-48`: 13 filters, 4 caps, 1 annotation.
    """
    assert len(LABEL_ORDER) == 18
    for label in LABEL_ORDER:
        assert label in CLASS_OF, f"{label} is not classified"
    assert {CLASS_OF[label] for label in CAP_LABELS} == {CAPACITY}
    assert CLASS_OF["drawdown_stop"] == OPERATIONAL
    assert ANNOTATION_LABELS == ["drawdown_stop"]


def test_the_annotations_are_excluded_from_every_exclusion_total():
    """Ruling I8. `drawdown_stop` labels what was true when the signal was made and decides
    nothing; `no_book` is written on an order that **was placed** (`loop.py:1050-1059`, R10),
    recording only that the queue behind it was unknowable. Neither is an exclusion.
    """
    assert ANNOTATIONS == frozenset({"drawdown_stop", "no_book"})
    totals = exclusion_totals({"no_book": 9, "drawdown_stop": 4, "exec_capacity": 2})
    assert totals[CAPACITY] == 2
    assert totals[OPERATIONAL] == 0
    assert sum(totals.values()) == 2


def test_journal_136_s_breakdown_sums_to_the_expected_classes():
    """Addendum §1.4's expected result, computed here by hand from journal 136's real numbers.

    3,836 `book_dirty` + 2,967 `fair_stale` = 6,803 unreliable data; 344 `exec_capacity` is the
    whole of capacity; nothing in that window was a strategy rejection or an operational cancel;
    and the 9 `no_book` events are annotations on placements, counted outside every total.
    """
    totals = exclusion_totals({"book_dirty": 3836, "fair_stale": 2967,
                               "exec_capacity": 344, "no_book": 9})
    assert totals == {CAPACITY: 344, STRATEGY: 0, DATA: 6803, OPERATIONAL: 0}


def test_class_of_names_the_reason_it_cannot_classify():
    try:
        class_of("a_reason_nobody_registered")
    except KeyError as exc:
        assert "a_reason_nobody_registered" in str(exc)
    else:
        raise AssertionError("class_of accepted an unclassified reason")


def test_every_coverage_outcome_is_classified_exactly_once():
    """Ruling C2. The fifteen outcomes addendum §1.1 can write, each in exactly one of the six
    coverage classes. Task 4 derives `coverage.COVERAGE_OUTCOMES` from this map, so a new
    outcome cannot reach the table unclassified.
    """
    assert set(COVERAGE_CLASS_OF.values()) <= set(COVERAGE_CLASSES)
    assert sorted(COVERAGE_CLASS_OF) == sorted([
        "scheduled", "completed", "not_due", "cadence_none", "budget", "http_error",
        "skipped_trades", "skipped_ladders", "skipped_alternates", "no_fair", "no_gap",
        "no_signal", "budget_stage_skipped", "variant_skipped", "truncated"])
    assert COVERAGE_CLASS_OF["completed"] == "complete"
    assert COVERAGE_CLASS_OF["scheduled"] == "pending"
    assert COVERAGE_CLASS_OF["cadence_none"] == "not_scheduled"


def test_the_promoted_literals_write_the_strings_their_literals_wrote():
    """Ruling I8's whole safety property: this promotion is behaviour-neutral. `order_events`
    rows written before and after this task must be indistinguishable.
    """
    from harness.execution.plan import EXPIRY, NO_BOOK

    assert EXPIRY == "expiry"
    assert NO_BOOK == "no_book"
    loop_source = (__import__("pathlib").Path(plan_module.__file__).parent / "loop.py").read_text()
    assert 'reason="expiry"' not in loop_source
    assert 'reason="no_book"' not in loop_source
    assert "reason=EXPIRY" in loop_source and "reason=NO_BOOK" in loop_source
