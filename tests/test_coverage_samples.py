"""`coverage_samples`: what was scheduled, what completed, and what neither.

Addendum §1.1 and ruling C2. Every expectation below is computed by hand in its docstring from
the seeded shape -- never by calling the helper a second time and comparing it with itself.
"""

from datetime import datetime, timezone
from typing import NamedTuple

import httpx
import pytest
import respx
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from harness.db.models import CoverageSample, Run
from harness.ops import coverage
from harness.ops.exclusions import COVERAGE_CLASS_OF
from harness.pricing import gaps
from harness.recorder.tick import Recorder
from harness.strategy import pipeline as pipeline_module
from harness.strategy.variants import load_variants, register_variants
from tests.test_pipeline import NOW as PIPELINE_NOW
from tests.test_pipeline import VARIANTS_DIR, _seed
from tests.test_tick import ESPN, KM, ODDS, _recorder

NOW = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)

#: §1.1's seeded tick: two sports, three market types each, two variants.
SPORTS = ("nfl", "ncaaf")
MARKET_TYPES = ("moneyline", "spread", "total")
VARIANTS = ("aaaaaaaaaaaa", "bbbbbbbbbbbb")


def _run(session) -> Run:
    row = Run(started_at=NOW, status="running")
    session.add(row)
    session.flush()
    return row


def _cells() -> dict[int, coverage.Cell]:
    """Six markets -- two sports x three market types -- each on the same feed and ttk bucket,
    keyed by a synthetic `venue_market_id`."""
    out = {}
    for i, sport in enumerate(SPORTS):
        for j, market_type in enumerate(MARKET_TYPES):
            out[100 + i * 10 + j] = coverage.Cell(
                sport=sport, ttk_bucket="20m_3h", feed="featured", market_type=market_type)
    return out


def test_the_scheduled_set_is_twelve_units_and_is_written_before_the_work(db_session):
    """Expected: 12 `scheduled` rows' worth of units, written at enumeration.

    Computed by hand from §1.1: 2 sports x 3 market types x 2 variants = 12 scheduled units.
    The rows are aggregated per cell, and a cell here is one (sport, market_type, variant), so
    there are 12 rows of n = 1. Every one carries `overdue_ms is null` -- a scheduled row has
    nothing to be overdue about yet, which is also what §2's invariant query requires.
    """
    run = _run(db_session)
    rows = coverage.evaluation_scheduled_rows(_cells(), VARIANTS)
    assert sum(n for _cell, _outcome, n, _ms in rows) == 12
    written = coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION, rows)
    db_session.commit()
    assert written == len(rows)
    stored = db_session.query(CoverageSample).filter_by(run_id=run.id).all()
    assert {s.outcome for s in stored} == {"scheduled"}
    assert sum(s.n for s in stored) == 12
    assert all(s.overdue_ms is None for s in stored)
    assert all(s.domain == "evaluation" and s.source is None for s in stored)


def test_the_completion_rows_account_for_the_same_twelve_units(db_session):
    """Expected: 4 `completed`, 2 `no_fair`, 6 `variant_skipped` (ruling I4).

    Computed by hand: variant B is skipped by the budget, so it loses all six of its cells; one
    market type ("total") has no fair value **in either sport**, so it costs the surviving
    variant A both of its cells in that market type; four of A's cells complete.
    4 + 2 + 6 = 12, which is the scheduled count above. The arithmetic is the point: a
    completion set that does not add up to the scheduled set is an omission, and §3 row 2's
    reconciliation query is what names it by cell.
    """
    run = _run(db_session)
    cells = _cells()
    a, b = VARIANTS
    outcomes = {}
    for market_id, cell in cells.items():
        outcomes[(a, market_id)] = "no_fair" if cell.market_type == "total" else "completed"
    rows = coverage.evaluation_completion_rows(
        cells, VARIANTS, outcomes, gapped=set(cells), scored={a},
        budget_exhausted=False, overdue_ms=1_500)
    coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION,
                    coverage.evaluation_scheduled_rows(cells, VARIANTS))
    coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION, rows)
    db_session.commit()

    totals = dict(db_session.execute(text(
        "select outcome, sum(n) from coverage_samples where run_id = :run and outcome <> 'scheduled'"
        " group by 1"), {"run": run.id}).all())
    assert totals == {"completed": 4, "no_fair": 2, "variant_skipped": 6}
    assert sum(totals.values()) == 12
    overdue = db_session.execute(text(
        "select count(*) from coverage_samples where run_id = :run and outcome <> 'scheduled'"
        " and outcome <> 'completed' and overdue_ms is null"), {"run": run.id}).scalar()
    assert overdue == 0


def test_the_reconciliation_query_returns_no_rows_when_every_cell_is_closed(db_session):
    """§3 row 2, run verbatim against the rows above: every scheduled cell was closed inside one
    cadence period, so the query names nothing."""
    run = _run(db_session)
    cells = _cells()
    outcomes = {(v, market_id): "completed" for v in VARIANTS for market_id in cells}
    coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION,
                    coverage.evaluation_scheduled_rows(cells, VARIANTS))
    coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION,
                    coverage.evaluation_completion_rows(
                        cells, VARIANTS, outcomes, gapped=set(cells), scored=set(VARIANTS),
                        budget_exhausted=False, overdue_ms=900))
    db_session.commit()
    assert _unclosed(db_session) == []


def test_a_pricing_block_that_raised_leaves_its_scheduled_rows_unclosed(db_session):
    """The failure mode the contract exists to detect. The scheduled rows are written, the work
    raises, nothing closes them, and the reconciliation query names exactly those cells.

    Computed by hand: 12 scheduled units over 12 cells, no completion rows at all, so the query
    returns 12 rows -- one per cell.
    """
    run = _run(db_session)
    coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION,
                    coverage.evaluation_scheduled_rows(_cells(), VARIANTS))
    db_session.commit()
    unclosed = _unclosed(db_session)
    assert len(unclosed) == 12
    assert {row.variant_id for row in unclosed} == set(VARIANTS)


def _unclosed(session):
    """§3 row 2's reconciliation query, verbatim apart from its time bounds: this test's rows
    are stamped `now()`, so the 16-minute settling bound of the production row would exclude
    them. The correlated NOT EXISTS -- the part under test -- is unchanged."""
    return session.execute(text("""
        select s.run_id, s.sport, s.ttk_bucket, s.feed, s.market_type, s.variant_id, s.ts
        from coverage_samples s
        where s.domain = 'evaluation' and s.outcome = 'scheduled'
          and s.ts > now() - interval '24 hours'
          and not exists (
            select 1 from coverage_samples c
            where c.run_id = s.run_id and c.domain = s.domain and c.outcome <> 'scheduled'
              and c.sport is not distinct from s.sport
              and c.ttk_bucket is not distinct from s.ttk_bucket
              and c.feed is not distinct from s.feed
              and c.market_type is not distinct from s.market_type
              and c.variant_id is not distinct from s.variant_id)
    """)).all()


class _Row(NamedTuple):
    """The five attributes `evaluation_cells` reads off a `GapRow` (`harness/strategy/run.py:83`)
    and nothing else: a full twenty-six-field `GapRow` here would say no more and drift sooner."""

    venue_market_id: int
    sport: str | None
    market_type: str | None
    ttk_minutes: int | None
    feed_kind: str | None


def test_a_market_with_no_gap_row_takes_the_cell_the_enumeration_knew():
    """Docket item 21 step 1, computed by hand from the three inputs below.

    Market 601 has a gap row, so its cell is that row's -- the gap row wins, and it agrees with
    the capture anyway, both being `game.sport`, `market.market_type` and the one `ttk_minutes`
    formula. Markets 602 and 603 have none, so each takes the facts captured at enumeration:
    `ttk_bucket` from `ttk_bucket(45) == "20m_3h"` and `ttk_bucket(2880) == "gt36h"`, and `feed`
    stays None, because `feed` is `market_gap_snapshots.feed_kind` and there is no snapshot.
    """
    gap_rows = [_Row(601, "nfl", "moneyline", 2880, "featured")]
    facts = {601: gaps.MarketFacts("nfl", "moneyline", 2880),
             602: gaps.MarketFacts("ncaaf", "spread", 45),
             603: gaps.MarketFacts("nfl", "total", 2880)}
    cells = coverage.evaluation_cells(gap_rows, [601, 602, 603], facts)
    assert cells[601] == coverage.Cell(sport="nfl", ttk_bucket="gt36h", feed="featured",
                                       market_type="moneyline")
    assert cells[602] == coverage.Cell(sport="ncaaf", ttk_bucket="20m_3h", market_type="spread")
    assert cells[603] == coverage.Cell(sport="nfl", ttk_bucket="gt36h", market_type="total")
    assert all(cell.sport is not None for cell in cells.values())
    assert [cell.feed for cell in cells.values()] == ["featured", None, None]


def test_without_captured_facts_the_cell_is_still_all_null():
    """The argument is optional and the old behaviour is what a caller that captures nothing
    gets: `_load_gap_rows`' other callers and the derived-phase build pass no facts, and a unit
    with no facts must still be scheduled rather than dropped."""
    cells = coverage.evaluation_cells([], [701, 702])
    assert cells == {701: coverage.Cell(), 702: coverage.Cell()}


def test_populating_the_cells_moves_no_unit_between_scheduled_and_completed():
    """The ruling's "the completed/scheduled totals are unchanged", asserted both ways round.

    Computed by hand: three markets and two variants are six scheduled units whichever cells
    they fall in. One market has a gap row and both variants completed it; the other two never
    got one, so they close as `no_gap` -- 2 completed + 4 no_gap = 6, before and after.
    """
    gap_rows = [_Row(601, "nfl", "moneyline", 2880, "featured")]
    order = [601, 602, 603]
    facts = {601: gaps.MarketFacts("nfl", "moneyline", 2880),
             602: gaps.MarketFacts("nfl", "spread", 2880),
             603: gaps.MarketFacts("ncaaf", "total", 15)}
    outcomes = {(variant, 601): "completed" for variant in VARIANTS}
    totals = {}
    for populated in (False, True):
        cells = coverage.evaluation_cells(gap_rows, order, facts if populated else None)
        scheduled = coverage.evaluation_scheduled_rows(cells, VARIANTS)
        assert sum(n for _cell, _outcome, n, _ms in scheduled) == len(order) * len(VARIANTS) == 6
        completion = coverage.evaluation_completion_rows(
            cells, VARIANTS, outcomes, gapped={601}, scored=set(VARIANTS),
            budget_exhausted=False, overdue_ms=1_200)
        by_outcome: dict[str, int] = {}
        for _cell, outcome, n, _ms in completion:
            by_outcome[outcome] = by_outcome.get(outcome, 0) + n
        totals[populated] = by_outcome
    assert totals[False] == totals[True] == {"completed": 2, "no_gap": 4}
    # And the populated run says *where* the four missing units were, which is the point.
    cells = coverage.evaluation_cells(gap_rows, order, facts)
    assert {cell.market_type for market_id, cell in cells.items() if market_id != 601} == \
        {"spread", "total"}


def test_the_populated_cells_fill_i6s_grid_and_the_null_feed_slice():
    """Ruling I6, re-derived here over a slate built to occupy every cell it can.

    Computed by hand: 2 sports x 4 ttk buckets x 3 market types is 24 triples; each is seeded
    three times -- once gapped on each of the two `feed_kind` values and once with no gap row at
    all -- for 72 markets and 72 distinct cells. The 48 with a feed, over the 7 registered
    variants, are exactly ruling I6's 2 x 4 x 2 x 3 x 7 = 336. The 24 without one are the
    NULL-`feed` slice the table already carries: `market_gap_snapshots.feed_kind` is NULL
    whenever the fair value behind the row had none, which is 11,564 of the 17,955 NULL-feed
    evaluation rows measured in the 24 h to 2026-09-18 07:43 CT. So this task adds no value to
    any cell column; it moves units out of the all-null cell into cells the grid already had.
    """
    buckets = {"lt20m": 10, "20m_3h": 45, "3h_36h": 600, "gt36h": 2880}
    order: list[int] = []
    gap_rows: list[_Row] = []
    facts: dict[int, object] = {}
    market_id = 0
    for sport in ("nfl", "ncaaf"):
        for minutes in buckets.values():
            for market_type in ("moneyline", "spread", "total"):
                for feed in ("featured", "alternate"):
                    market_id += 1
                    order.append(market_id)
                    gap_rows.append(_Row(market_id, sport, market_type, minutes, feed))
                    facts[market_id] = gaps.MarketFacts(sport, market_type, minutes)
                market_id += 1
                order.append(market_id)
                facts[market_id] = gaps.MarketFacts(sport, market_type, minutes)

    cells = coverage.evaluation_cells(gap_rows, order, facts)
    assert len(cells) == 72 and len(set(cells.values())) == 72
    with_feed = {cell for cell in cells.values() if cell.feed is not None}
    assert len(with_feed) == 48
    assert len(with_feed) * 7 == 2 * 4 * 2 * 3 * 7 == 336
    assert len(set(cells.values())) - len(with_feed) == 24
    assert {coverage.ttk_bucket(m) for m in buckets.values()} == set(buckets)

    variants = [f"v{i:011d}" for i in range(7)]
    scheduled = coverage.evaluation_scheduled_rows(cells, variants)
    assert sum(n for _cell, _outcome, n, _ms in scheduled) == 72 * 7 == 504


def test_every_scheduled_evaluation_cell_names_its_sport_ttk_and_market_type(
        env_settings, db_session):
    """Docket item 21 step 1 through the real pipeline: no scheduled evaluation row carries a
    null `sport` any more, and the `no_fair` row names the cell it happened in.

    Computed by hand from `_seed` (`tests/test_pipeline.py`) and the one variant in
    `tests/fixtures/variants`: ten markets are quoted, one `unmatched` and therefore never
    enumerated, so `market_order` is 9 and the scheduled set is 1 x 9 = 9 units. Six have a
    direct gap row when the set is enumerated -- the two moneylines, the fuzzy moneyline, the
    3.5 spread, the 6.5 spread (whose fair comes off the alternates feed) and the 44.5 total --
    and the 9.5 spread, the 47.5 total and the `draw` market get theirs only in stage 5. The
    game kicks off `NOW + 2 days`, so every cell is `gt36h`, and the nine units fall in seven
    cells: moneyline/featured 3, spread/featured 1, spread/alternate 1, total/featured 1, and
    one each for spread, total and draw with no feed. Before this task the last three collapsed
    into a single all-null cell of n = 3; the unit total is 9 either way, and the completion set
    is still the 8 completed and 1 no_fair the D4 case above computes.
    """
    _game, run, _markets = _seed(db_session)
    register_variants(db_session, load_variants(VARIANTS_DIR), PIPELINE_NOW, prune=True)
    db_session.commit()

    pipeline_module.price_and_signal(db_session, run.id, PIPELINE_NOW, env_settings, budget_s=600)
    db_session.commit()

    rows = db_session.execute(text(
        "select sport, ttk_bucket, coalesce(feed, ''), market_type, sum(n)::int from coverage_samples"
        " where run_id = :run and domain = 'evaluation' and outcome = 'scheduled'"
        " group by 1, 2, 3, 4 order by 4, 3"), {"run": run.id}).all()
    assert [tuple(row) for row in rows] == [
        ("nfl", "gt36h", "", "draw", 1),
        ("nfl", "gt36h", "featured", "moneyline", 3),
        ("nfl", "gt36h", "", "spread", 1),
        ("nfl", "gt36h", "alternate", "spread", 1),
        ("nfl", "gt36h", "featured", "spread", 1),
        ("nfl", "gt36h", "", "total", 1),
        ("nfl", "gt36h", "featured", "total", 1),
    ]
    assert sum(row[4] for row in rows) == 9
    assert db_session.execute(text(
        "select count(*) from coverage_samples where run_id = :run and domain = 'evaluation'"
        " and sport is null"), {"run": run.id}).scalar() == 0
    assert db_session.execute(text(
        "select sport, ttk_bucket, market_type from coverage_samples where run_id = :run"
        " and outcome = 'no_fair'"), {"run": run.id}).all() == [("nfl", "gt36h", "draw")]


def test_record_refuses_an_outcome_the_class_map_does_not_carry(db_session):
    """Ruling C2: no outcome reaches the table unclassified, refused at the **write** -- so the
    `outcome not in (...)` integrity count of §3 row 2 is a check on the table rather than the
    only line of defence."""
    run = _run(db_session)
    with pytest.raises(ValueError, match="mystery_outcome"):
        coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION,
                        [(coverage.Cell(sport="nfl"), "mystery_outcome", 1, 5)])


def test_an_outcome_that_needs_an_interval_is_refused_without_one(db_session):
    """§1.1: `overdue_ms` is null on `scheduled` and `completed` and **required** on every other
    outcome. Both halves are refused at the write, which is what makes §2's invariant query a
    statement about the table rather than a hope."""
    run = _run(db_session)
    with pytest.raises(ValueError, match="overdue_ms"):
        coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION,
                        [(coverage.Cell(sport="nfl"), "no_gap", 1, None)])
    with pytest.raises(ValueError, match="overdue_ms"):
        coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION,
                        [(coverage.Cell(sport="nfl"), "completed", 1, 5)])


def test_the_class_map_covers_every_outcome_the_writer_can_write():
    """M9: `COVERAGE_OUTCOMES` is derived from `COVERAGE_CLASS_OF`, so the two are one list and
    a new outcome cannot be added without a class."""
    assert coverage.COVERAGE_OUTCOMES == tuple(COVERAGE_CLASS_OF)
    assert len(set(coverage.COVERAGE_OUTCOMES)) == 15


def test_the_cap_writes_a_truncated_row_and_a_metric_instead_of_a_silent_cut(db_session):
    """Ruling I6/D3. `COVERAGE_ROW_CAP` is 3,072, above §1.1's worst case, so it is a
    backstop rather than the routine case. When it does bind the cut is visible: the first
    3,072 rows, one `truncated` row carrying how many were dropped, and a `coverage.truncated`
    sample.

    Computed by hand: 3,100 rows offered, 3,072 written, 28 dropped, so the truncated row's `n`
    is 28 and the table holds 3,073 rows for this run.
    """
    run = _run(db_session)
    rows = [(coverage.Cell(sport="nfl", market_type=f"m{i}"), "completed", 1, None)
            for i in range(3_100)]
    coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION, rows)
    db_session.commit()
    stored = db_session.execute(text(
        "select outcome, count(*), sum(n) from coverage_samples where run_id = :run group by 1"),
        {"run": run.id}).all()
    by_outcome = {outcome: (count, total) for outcome, count, total in stored}
    assert by_outcome["completed"][0] == coverage.COVERAGE_ROW_CAP
    assert by_outcome["truncated"] == (1, 28)
    samples = db_session.execute(text(
        "select count(*) from metric_samples where name = 'coverage.truncated'")).scalar()
    assert samples == 1


def test_a_database_failure_in_the_helper_never_reaches_the_caller(db_session, monkeypatch):
    """The tick's rule: telemetry never fails a tick (`run_checks`, `telemetry.record_many`).
    A write that raises is logged and swallowed, the caller gets 0 back, and the session is
    still usable afterwards -- the savepoint is what makes the last part true.

    The failure is a real one: the INSERT itself raises `OperationalError`, the way a lost
    connection or a full disk would present (review rev-6d-t4 Minor 1; the earlier version of
    this case passed a bad domain, which proved the domain guard instead).
    """
    run = _run(db_session)
    db_session.commit()
    run_id = run.id   # read before the patch: an expired attribute would refresh through `execute`
    real_execute = db_session.execute

    def boom(statement, *args, **kwargs):
        raise OperationalError("INSERT INTO coverage_samples", {}, Exception("connection lost"))

    monkeypatch.setattr(db_session, "execute", boom)
    written = coverage.record(db_session, run_id, coverage.DOMAIN_EVALUATION,
                              [(coverage.Cell(sport="nfl"), "completed", 1, None)])
    assert written == 0
    monkeypatch.setattr(db_session, "execute", real_execute)
    # The savepoint rolled back, the transaction did not: the next write goes through.
    assert coverage.record(db_session, run_id, coverage.DOMAIN_EVALUATION,
                           [(coverage.Cell(sport="nfl"), "completed", 1, None)]) == 1
    db_session.commit()
    assert db_session.query(CoverageSample).filter_by(run_id=run_id).count() == 1


def test_record_refuses_a_domain_that_is_not_one_of_the_two(db_session):
    """Minor 2: the domain is a programming error like every other, refused before the database
    is touched rather than swallowed inside the savepoint -- which is what the docstring on
    `record` has always claimed."""
    run = _run(db_session)
    with pytest.raises(ValueError, match="not_a_domain"):
        coverage.record(db_session, run.id, "not_a_domain",
                        [(coverage.Cell(sport="nfl"), "completed", 1, None)])


def test_the_callers_own_pending_failure_is_not_reported_as_a_coverage_failure(db_session):
    """Important 2: `Session.begin_nested()` flushes the session's pending ORM state before it
    takes its snapshot. That flush is the *caller's*, so it happens before the helper's guard:
    a caller with a broken pending row gets its own error, not `coverage.record failed` and a
    silent 0 with an aborted transaction underneath.

    The pending row here violates `runs.status NOT NULL`, so the flush raises `IntegrityError`.
    """
    run = _run(db_session)
    db_session.commit()
    db_session.add(Run(started_at=NOW, status=None))
    with pytest.raises(Exception) as caught:
        coverage.record(db_session, run.id, coverage.DOMAIN_EVALUATION,
                        [(coverage.Cell(sport="nfl"), "completed", 1, None)])
    assert "status" in str(caught.value)
    assert not isinstance(caught.value, ValueError)   # not one of the helper's own refusals


def test_ttk_buckets_are_the_boundaries_already_in_the_code():
    """§1.1: `min_ttk_min: 20` in every registered YAML, the 3 h ladder window, the 36 h
    alternates window. Computed by hand at each boundary, which is where an off-by-one lives."""
    assert coverage.ttk_bucket(None) is None
    assert coverage.ttk_bucket(19) == "lt20m"
    assert coverage.ttk_bucket(20) == "20m_3h"
    assert coverage.ttk_bucket(179) == "20m_3h"
    assert coverage.ttk_bucket(180) == "3h_36h"
    assert coverage.ttk_bucket(2159) == "3h_36h"
    assert coverage.ttk_bucket(2160) == "gt36h"


def test_the_cardinality_bound_holds_for_the_registered_grid():
    """Ruling I6, computed by hand: 2 sports x 4 ttk buckets x 2 feed kinds x 3 market types x
    7 registered variants = 336 evaluation cells; one scheduled row per cell plus at most one
    row per (cell, outcome) over the six evaluation outcomes bounds a priced tick at
    336 + 336 x 6 = 2,352 rows, which is under `COVERAGE_ROW_CAP`.

    The plan wrote that product as 2,688, which is 336 x 8 -- one scheduled row plus *seven*
    completion outcomes, and there are six. The correction is upwards-safe in the only direction
    that matters: the true worst case is smaller than the figure the cap was chosen against, so
    3,072 remains a backstop and `truncated` remains off the routine path. The count is asserted
    here rather than taken on trust because the disk estimate rests on it.
    """
    cells = 2 * 4 * 2 * 3 * 7
    assert cells == 336
    assert cells + cells * 6 == 2_352
    assert coverage.COVERAGE_ROW_CAP == 3_072 > 2_352


# --- the collection domain, through a real tick ------------------------------------------------
#
# The evaluation domain is proved above against seeded shapes; the collection domain's one
# genuinely fragile fact is *when* due-ness is read, so it is proved against `Recorder.maybe_tick`
# itself rather than against a hand-built ctx.

@respx.mock
def test_a_tick_reads_espn_due_ness_before_its_own_fetch_moves_the_source_state(
        env_settings, db_session):
    """Scheduled is what was true **before** the work (the contract's first load-bearing claim).

    ESPN is the one family the recorder must fetch before the coverage plan can be written, because
    `interval_for` reads the day's kickoffs -- so `_espn` has already called `set_source_state(now)`
    by the time `_coverage_plan` runs, and a plan that re-read the table would find
    `is_due(now, now, 900) is False` and record the fetch it just made as `not_due`. `maybe_tick`
    snapshots the two ESPN keys before the fetch instead.

    Computed by hand: `SPORTS` is two sports and a first tick has no `source_state` at all, so ESPN
    is 2 scheduled units and, both fetches returning 200, 2 completed ones -- and no `not_due` row
    anywhere in the family. Every other scheduled (source, sport) is closed by a completion row in
    the same run, which is the collection domain's half of §3 row 2.
    """
    respx.get(url__regex=r"https://k/series/.*").mock(return_value=httpx.Response(200, json={"series": {}}))
    respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(
        return_value=httpx.Response(200, json=ODDS,
                                    headers={"x-requests-last": "3", "x-requests-remaining": "100"}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(
        return_value=httpx.Response(200, json={},
                                    headers={"x-requests-last": "2", "x-requests-remaining": "98"}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(
        return_value=httpx.Response(200, json={"orderbook_fp": {}}))

    rec, _clock = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "ok", run.notes

    rows = db_session.execute(text(
        "select source, sport, outcome, sum(n) from coverage_samples"
        " where run_id = :run and domain = 'collection' group by 1, 2, 3"), {"run": run.id}).all()
    totals = {(source, sport, outcome): n for source, sport, outcome, n in rows}
    espn = {(sport, outcome): n for (source, sport, outcome), n in totals.items() if source == "espn"}
    assert espn == {("nfl", "scheduled"): 1, ("ncaaf", "scheduled"): 1,
                    ("nfl", "completed"): 1, ("ncaaf", "completed"): 1}

    scheduled = {(source, sport) for (source, sport, outcome) in totals if outcome == "scheduled"}
    closed = {(source, sport) for (source, sport, outcome) in totals if outcome != "scheduled"}
    assert scheduled and scheduled <= closed


# --- durability: scheduled is written *and committed* before the work (Important 1) -----------

def test_the_pipelines_scheduled_rows_survive_the_rollback_of_the_work_that_followed(
        env_settings, db_session, monkeypatch):
    """The recorder rolls the tick's transaction back when `price_and_signal` raises
    (`tick.py`: `except Exception: ... session.rollback()`). If the scheduled write were still
    pending at that moment it would be discarded, and the run would show neither a scheduled row
    nor a completion row -- the omission would be invisible to §3 row 2, which looks for
    scheduled rows nothing closed.

    Computed by hand: `tests/fixtures/variants` holds one variant (`tiny.yaml`) and `_seed` quotes
    ten venue markets, one of them `match_status="unmatched"` and therefore never enumerated, so
    the direct gap build's `market_order` is 9 and the scheduled set is 1 x 9 = 9 units.
    `run_strategy` then raises inside the first `score`, so no completion row is ever written and
    every one of those units is still outstanding after the rollback. (The rows are aggregated per
    cell, so the reconciliation read returns cells rather than units; what it must name is the
    variant that never finished.)
    """
    _game, run, _markets = _seed(db_session)
    variants = load_variants(VARIANTS_DIR)
    register_variants(db_session, variants, PIPELINE_NOW, prune=True)
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("stage 3 died before any variant committed")

    monkeypatch.setattr(pipeline_module, "run_strategy", boom)
    with pytest.raises(RuntimeError):
        pipeline_module.price_and_signal(db_session, run.id, PIPELINE_NOW, env_settings,
                                         budget_s=30)
    db_session.rollback()

    rows = db_session.execute(text(
        "select outcome, sum(n) from coverage_samples where run_id = :run"
        " and domain = 'evaluation' group by 1"), {"run": run.id}).all()
    assert dict(rows) == {"scheduled": 9}
    unclosed = _unclosed(db_session)
    assert unclosed and {row.variant_id for row in unclosed} == {variants[0].variant_id}


def test_a_suppressed_direct_only_variant_still_closes_its_coverage_units(
        env_settings, db_session):
    """Review Critical 1. Stage 6 no longer re-scores a direct-only variant over the rows stage 5
    added (D4), but `evaluation_cells` still *schedules* every market in `market_order` for every
    variant, so those units have to be closed with the verdict the suppressed pass would have
    given. Left open they fall through `evaluation_completion_rows` to `no_signal` -- an
    `instrument`-class outcome counted as missing -- and §3 row 1's completed/scheduled ratio for
    the gate variant and the primary, both direct-only, collapses on the first post-deploy verify.

    Computed by hand from the seeded shape: `tests/fixtures/variants` holds one variant
    (`tiny.yaml`, `sources_allowed: [direct]`) and `_seed` quotes ten venue markets, one of them
    `match_status="unmatched"` and therefore never enumerated, so the scheduled set is 1 x 9 = 9
    units. Of those nine markets eight end the run with a fair value (six priced directly, two by
    the margin model) and one is the `draw` market no fair value is ever produced for, so the
    accounting is `completed 8, no_fair 1` -- the same distribution the pre-D4 two-pass build
    wrote -- and `no_signal` never appears. The three rows stage 5 added are still counted in
    `rescore_suppressed`, which is the *signal* population's bridge and a different quantity.
    """
    _game, run, _markets = _seed(db_session)
    register_variants(db_session, load_variants(VARIANTS_DIR), PIPELINE_NOW, prune=True)
    db_session.commit()

    result = pipeline_module.price_and_signal(
        db_session, run.id, PIPELINE_NOW, env_settings, budget_s=600)
    db_session.commit()

    assert result["rescore_suppressed"] == {"tiny": 3}
    rows = db_session.execute(text(
        "select outcome, sum(n) from coverage_samples where run_id = :run"
        " and domain = 'evaluation' group by 1 order by 1"), {"run": run.id}).all()
    assert [(outcome, int(n)) for outcome, n in rows] == [
        ("completed", 8), ("no_fair", 1), ("scheduled", 9)]
    assert all(COVERAGE_CLASS_OF[outcome] != "instrument" for outcome, _n in rows)


@respx.mock
def test_the_recorders_scheduled_rows_survive_a_rollback_in_the_same_tick(
        env_settings, db_session, monkeypatch):
    """The recorder's half of Important 1. A source that raises sends the tick to its handler,
    and a `normalize` failure right after it calls `session.rollback()` -- which, before the
    plan's write was committed, discarded the scheduled rows while `_coverage_close` went on to
    write the completion rows, leaving completions with no scheduled row for the run at all.

    Computed by hand: ESPN is fetched and planned first, so its 2 units (one per sport) are
    scheduled; `_odds` then raises before any later `_checkpoint` can commit anything, and
    `normalize` raises after it. The 2 ESPN scheduled rows must still be there.
    """
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={}))

    def boom_odds(self, *args, **kwargs):
        raise RuntimeError("odds died before the next checkpoint")

    def boom_normalize(*args, **kwargs):
        raise RuntimeError("normalize died with the tick's work still pending")

    monkeypatch.setattr(Recorder, "_odds", boom_odds)
    monkeypatch.setattr("harness.recorder.tick.normalize_new", boom_normalize)

    rec, _clock = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "error"

    scheduled = db_session.execute(text(
        "select coalesce(sum(n), 0) from coverage_samples where run_id = :run"
        " and domain = 'collection' and source = 'espn' and outcome = 'scheduled'"),
        {"run": run.id}).scalar()
    assert scheduled == 2
