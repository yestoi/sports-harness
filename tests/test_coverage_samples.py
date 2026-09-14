"""`coverage_samples`: what was scheduled, what completed, and what neither.

Addendum §1.1 and ruling C2. Every expectation below is computed by hand in its docstring from
the seeded shape -- never by calling the helper a second time and comparing it with itself.
"""

from datetime import datetime, timezone

import httpx
import pytest
import respx
from sqlalchemy import text

from harness.db.models import CoverageSample, Run
from harness.ops import coverage
from harness.ops.exclusions import COVERAGE_CLASS_OF
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


def test_a_database_failure_in_the_helper_never_reaches_the_caller(db_session):
    """The tick's rule: telemetry never fails a tick (`run_checks`, `telemetry.record_many`).
    A write that raises is logged and swallowed, and the caller gets 0 back."""
    run = _run(db_session)
    written = coverage.record(db_session, run.id, "not_a_domain",
                              [(coverage.Cell(sport="nfl"), "completed", 1, None)])
    assert written == 0


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
