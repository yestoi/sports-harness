"""Coverage as recorded data: what was scheduled, what completed, and what neither.

Addendum §1.1 and ruling C2. One bounded table, written by the writer that made the decision at
the moment it made it, in two phases:

  1. **scheduled** -- one row per cell the moment the set is enumerated, before the work is
     attempted: in the recorder once `Recorder._due` has resolved each source against
     `source_state` and the cadence in force; in the pipeline once stages 1 and 2 have
     enumerated the priceable venue markets and `pricing_order` the active variants.
  2. **completion** -- one row per `(cell, outcome)` with its count and its interval, after the
     work.

Scheduled is therefore a recorded fact and never the sum of the completions, so a stage never
entered, a pricing block that raised and a tick that died are all visible as scheduled rows
nothing closes. §3 row 2's reconciliation query is the read that names them, and its result is
the contract's "zero unexplained omissions".

**The cell is fixed at enumeration and reused at completion.** §3 row 2 matches all five cell
columns with `is not distinct from`, so a completion row that re-derived `feed` from the gap row
it ended up with would leave its own scheduled row looking unclosed. `feed` is therefore
`market_gap_snapshots.feed_kind` *as it stood when the unit was enumerated*, and NULL for a
market that had no gap row yet at stage 2.

**Nothing here may fail a tick.** The write runs in its own savepoint and a database failure is
logged and swallowed (`run_checks`' and `telemetry.record_many`'s rule). An unclassified outcome
is a different thing -- a programming error -- and raises before the savepoint is opened, which
is what makes "no outcome reaches the table unclassified" true at the write.
"""

import logging
from typing import Iterable, NamedTuple

from sqlalchemy import insert
from sqlalchemy.orm import Session

from harness import telemetry
from harness.db.models import CoverageSample
from harness.ops.exclusions import COVERAGE_CLASS_OF

log = logging.getLogger(__name__)

DOMAIN_COLLECTION = "collection"
DOMAIN_EVALUATION = "evaluation"
DOMAINS = (DOMAIN_COLLECTION, DOMAIN_EVALUATION)

SCHEDULED = "scheduled"
COMPLETED = "completed"
TRUNCATED = "truncated"
#: The outcomes a coverage row may carry, derived from the class map so the two cannot drift
#: (M9): a new outcome has to be classified in `harness/ops/exclusions.py` before it can be
#: written here.
COVERAGE_OUTCOMES: tuple[str, ...] = tuple(COVERAGE_CLASS_OF)
#: The two outcomes that carry no interval, because there is nothing yet to be late about.
_NO_INTERVAL = (SCHEDULED, COMPLETED)

#: Ruling I6's cardinality bound, re-derived in the plan and in
#: `tests/test_coverage_samples.py`: 2 sports x 4 ttk buckets x 2 feed kinds x 3 market types x
#: 7 registered variants = 336 evaluation cells; one scheduled row per cell plus at most one row
#: per (cell, outcome) over the six evaluation outcomes bounds a priced tick at 2,352 rows (the
#: plan wrote 2,688, which is 336 x 8: one scheduled row plus seven outcomes, and there are
#: six -- the true bound is the smaller one, so the cap is if anything roomier than intended). The
#: cap sits above that worst case so it is a backstop rather than the routine case -- at 512 it
#: would bind on every real slate and `truncated` would become the normal outcome. 3,072 rows x
#: 12 bound parameters is 36,864 parameters, inside psycopg's 65,535 limit, so the write stays
#: one statement (`SIGNAL_INSERT_CHUNK`'s reasoning, `harness/strategy/pipeline.py:48-52`).
COVERAGE_ROW_CAP = 3072

#: The ttk boundaries already in the code: `min_ttk_min: 20` in every registered variant YAML,
#: the 3 h ladder window (`cadence.select_ladders`), the 36 h alternates window
#: (`Settings.odds_alt_window_h`).
_TTK_EDGES = ((20, "lt20m"), (180, "20m_3h"), (2160, "3h_36h"))
_TTK_LAST = "gt36h"

#: The six source families the recorder gates on a cadence key, with the scope their key is
#: built from and the interval rule that decides due-ness. `None` means
#: `cadence.interval_for(sport, ...)` -- the cadence in force -- rather than a fixed period.
#: `odds_alternates`, `kalshi_trades` and `kalshi_orderbook` are **not** here: their due set is
#: the selection the fetch itself makes, so they are enumerated inside the fetch phase and
#: recorded from the count captured at selection time (`ctx["coverage_selected"]`).
COLLECTION_FAMILIES = (
    ("espn", "sport", 900),
    ("odds_featured", "sport", None),
    ("kalshi_markets", "series", None),
    ("kalshi_events", "series", 900),
    ("kalshi_settled", "series", 3600),
    ("kalshi_series", "series", 86400),
)


class Cell(NamedTuple):
    """One coverage cell. Every field is nullable and every field means exactly one thing."""

    sport: str | None = None
    ttk_bucket: str | None = None
    feed: str | None = None
    market_type: str | None = None
    variant_id: str | None = None
    #: The collection domain's own (M7, and this plan's choice 1): NULL in the evaluation
    #: domain, where every verification query lives.
    source: str | None = None


def ttk_bucket(minutes: int | None) -> str | None:
    """`lt20m | 20m_3h | 3h_36h | gt36h`, or None when the row carries no time to kickoff."""
    if minutes is None:
        return None
    for edge, name in _TTK_EDGES:
        if minutes < edge:
            return name
    return _TTK_LAST


def _validate(outcome: str, n: int, overdue_ms: int | None) -> None:
    """One coverage row's programming-error checks, shared by the caller's rows and by the
    `truncated` row the cap appends. Raises before anything is written."""
    if outcome not in COVERAGE_CLASS_OF:
        raise ValueError(f"coverage outcome {outcome!r} has no class; "
                         f"add it to COVERAGE_CLASS_OF")
    if int(n) < 0:
        raise ValueError(f"coverage row for {outcome!r} carries n = {n}")
    if outcome in _NO_INTERVAL and overdue_ms is not None:
        raise ValueError(f"{outcome!r} must carry no overdue_ms")
    if outcome not in _NO_INTERVAL and overdue_ms is None:
        raise ValueError(f"{outcome!r} must carry an overdue_ms")


def record(session: Session, run_id: int, domain: str, rows) -> int:
    """One multi-row insert of `(cell, outcome, n, overdue_ms)` tuples. Returns rows written.

    Refuses, before touching the database: a domain that is not one of the two, an outcome the
    class map does not carry, a negative `n`, an interval on `scheduled`/`completed`, and a
    missing interval on anything else. Each is a programming error in a call site, and each
    would otherwise become a row no reader could interpret.

    Above `COVERAGE_ROW_CAP` the helper writes the first `COVERAGE_ROW_CAP` rows plus one
    `truncated` row carrying how many were dropped, and emits a `coverage.truncated` sample, so
    a cap that binds is visible rather than silent (D3).
    """
    rows = list(rows)
    if not rows:
        return 0
    # Every programming error is refused here, before the database is touched, so the docstring
    # above is true of the domain as well as of the rows (review rev-6d-t4 Minor 2).
    if domain not in DOMAINS:
        raise ValueError(f"coverage domain {domain!r}")
    for _cell, outcome, n, overdue_ms in rows:
        _validate(outcome, n, overdue_ms)
    dropped = 0
    if len(rows) > COVERAGE_ROW_CAP:
        dropped = len(rows) - COVERAGE_ROW_CAP
        rows = rows[:COVERAGE_ROW_CAP]
        # Validated like every other row rather than trusted because it is ours (Minor 4): the
        # row that reports a cut must not be the one row that could carry an uninterpretable one.
        _validate(TRUNCATED, dropped, 0)
        rows.append((Cell(), TRUNCATED, dropped, 0))
    now = telemetry._ts(None)
    values = [{"run_id": run_id, "ts": now, "domain": domain, "outcome": outcome,
               "n": int(n), "overdue_ms": None if overdue_ms is None else int(overdue_ms),
               **cell._asdict()}
              for cell, outcome, n, overdue_ms in rows]
    # Outside the guard below, deliberately (review rev-6d-t4 Important 2):
    # `Session.begin_nested()` flushes the session's pending ORM state before it takes its
    # snapshot, and a failure in the *caller's* pending rows is not a coverage failure to
    # swallow -- logging it as `coverage.record failed` would hide the caller's own bug and
    # leave the outer transaction aborted with nobody told. Only the two statements inside the
    # savepoint are the helper's to swallow.
    session.flush()
    try:
        with session.begin_nested():
            session.execute(insert(CoverageSample).values(values))
            if dropped:
                # `record_many`, not `record`: one INSERT statement rather than pending ORM
                # state a later `session.expunge_all()` could drop unflushed (Minor 4).
                telemetry.record_many(session, "recorder",
                                      [("coverage.truncated", dropped, {"domain": domain})],
                                      ts=now)
    except Exception:  # noqa: BLE001 - coverage never fails a tick
        log.exception("coverage.record failed for run %s domain %s", run_id, domain)
        return 0
    return len(values)


def evaluation_cells(gap_rows, market_order) -> dict[int, Cell]:
    """`venue_market_id -> Cell` for the evaluation domain's scheduled set, fixed here and
    reused at completion.

    `gap_rows` is what `_load_gap_rows` returned after stage 2; `market_order` is every quoted
    matched market the direct gap build enumerated, including the ones no fair value exists for
    yet (`harness/pricing/gaps.py` captures it before the no-fair filter). A market with
    a gap row takes its attributes from that row; a market without one is enumerated all the
    same, under an all-null cell, because leaving it out of the scheduled set is exactly the
    silent omission this table exists to prevent.
    """
    by_market = {row.venue_market_id: Cell(sport=row.sport, ttk_bucket=ttk_bucket(row.ttk_minutes),
                                           feed=row.feed_kind, market_type=row.market_type)
                 for row in gap_rows}
    return {market_id: by_market.get(market_id, Cell()) for market_id in
            dict.fromkeys(list(market_order) + list(by_market))}


def evaluation_scheduled_rows(cells: dict[int, Cell], variant_ids: Iterable[str]) -> list[tuple]:
    """One `scheduled` row per `(cell, variant)`, `n` = the units due in it."""
    counts: dict[Cell, int] = {}
    for variant_id in variant_ids:
        for cell in cells.values():
            key = cell._replace(variant_id=variant_id)
            counts[key] = counts.get(key, 0) + 1
    return [(cell, SCHEDULED, n, None) for cell, n in counts.items()]


def evaluation_completion_rows(cells: dict[int, Cell], variant_ids: Iterable[str],
                               outcomes: dict[tuple[str, int], str], *, gapped: set[int],
                               scored: set[str], budget_exhausted: bool,
                               overdue_ms: int) -> list[tuple]:
    """One row per `(cell, outcome)`, accounting for exactly the units `evaluation_scheduled_rows`
    scheduled.

    The rules, in the order they are applied to each `(variant, market)` unit:

    * the variant was never scored -- `budget_stage_skipped` when the budget tripped this run,
      `variant_skipped` otherwise (the two are different quantities and §3 row 3 reports them
      apart);
    * the variant scored and wrote a row for this market -- `completed`, or `no_fair` when the
      row's own rejection reason was `has_fair`, which is a market with no fair value at all;
    * the market never received a gap row this run -- `no_gap`;
    * otherwise -- `no_signal`: the instrument ran and recorded nothing for this unit, which
      decision 4's second bullet counts as missing rather than as complete.
    """
    counts: dict[tuple[Cell, str], int] = {}
    for variant_id in variant_ids:
        for market_id, cell in cells.items():
            if variant_id not in scored:
                outcome = "budget_stage_skipped" if budget_exhausted else "variant_skipped"
            elif (variant_id, market_id) in outcomes:
                outcome = outcomes[(variant_id, market_id)]
            elif market_id not in gapped:
                outcome = "no_gap"
            else:
                outcome = "no_signal"
            key = (cell._replace(variant_id=variant_id), outcome)
            counts[key] = counts.get(key, 0) + 1
    return [(cell, outcome, n, None if outcome in _NO_INTERVAL else overdue_ms)
            for (cell, outcome), n in counts.items()]
