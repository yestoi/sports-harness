"""The eligible game-day denominator, and the evidence a normalizer split would be judged on.

Addendum §1.3. The 109/243 exhaustion figure was over *priced runs* on a day with 2,220 runs and
562 non-skipped: three different denominators, one of which is the right one. This file pins
which.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from harness.db.models import NormalizeState, RawResponse, Run
from harness.ops import coverage

NOW = datetime(2026, 9, 13, 20, 0, tzinfo=timezone.utc)
WINDOW = {"start": NOW - timedelta(days=1), "end": NOW}


def _seed_day(session):
    """A seeded day of 20 runs: 12 skipped, 8 priced, 2 of them budget-exhausted.

    Deliberately *not* 20 = 12 + 8 by construction in the helper's own arithmetic: each run is
    written with the status and the notes a real tick would have written, and the counts below
    are read back out of the table.
    """
    for i in range(12):
        session.add(Run(started_at=NOW - timedelta(minutes=i + 1), status="skipped", notes={}))
    for i in range(8):
        pricing = {"gaps": 10, "signals": {}, "budget_exhausted": i < 2}
        session.add(Run(started_at=NOW - timedelta(minutes=30 + i), status="ok",
                        notes={"pricing": pricing}))
    session.flush()


def test_eligible_runs_returns_the_three_counts_with_priced_last(db_session):
    """Expected: (20, 8, 8).

    Computed by hand from the seeded day: 20 runs in the window; 8 are not `skipped`; 8 carry a
    `pricing` block. The three are reported together precisely because they differ on a real
    day -- 2,220 / 562 / 243 on 2026-09-11 -- and a reader who sees only one of them cannot tell
    which claim it supports.
    """
    _seed_day(db_session)
    assert coverage.eligible_runs(db_session, WINDOW) == (20, 8, 8)


def test_the_exhaustion_share_is_over_priced_runs_and_never_over_the_whole_day(db_session):
    """Expected: 2/8 = 25 %, never 2/20 = 10 %.

    Computed by hand: two of the eight priced runs carry `budget_exhausted`. The wrong
    denominator here is exactly the 10 % figure §0.6 refuses to let anyone claim.
    """
    _seed_day(db_session)
    exhausted, priced, share = coverage.exhaustion_share(db_session, WINDOW)
    assert (exhausted, priced) == (2, 8)
    assert round(share, 4) == 0.25


def test_the_share_is_not_over_non_skipped_runs_either(db_session):
    """Expected: (22, 10, 8) and 2/8 = 25 %, never 2/10 = 20 % and never 2/22 = 9.1 %.

    The addendum's seeded day has `non_skipped == priced == 8`, so the two cases above pass
    unchanged if an implementation divides by `non_skipped_runs`: they cannot fail on the exact
    confusion §1.3(b) names (2,220 / 562 / 243 are three different numbers on a real day).
    This case seeds the middle count separately -- two runs that ran, errored and never priced,
    written with the notes a real tick writes -- so all three differ and only `priced_runs`
    gives 25 %.
    """
    for i in range(12):
        db_session.add(Run(started_at=NOW - timedelta(minutes=i + 1), status="skipped", notes={}))
    for i in range(2):
        db_session.add(Run(started_at=NOW - timedelta(minutes=20 + i), status="error",
                           notes={"errors": [{"tick": "boom"}], "skipped_trades": 0}))
    for i in range(8):
        db_session.add(Run(started_at=NOW - timedelta(minutes=30 + i), status="ok",
                           notes={"pricing": {"gaps": 10, "budget_exhausted": i < 2}}))
    db_session.flush()
    assert coverage.eligible_runs(db_session, WINDOW) == (22, 10, 8)
    exhausted, priced, share = coverage.exhaustion_share(db_session, WINDOW)
    assert (exhausted, priced) == (2, 8)
    assert round(share, 4) == 0.25


def test_the_read_is_capped_then_filtered_and_puts_no_predicate_on_started_at(db_session):
    """Ruling I5: `runs` carries no index on `started_at`, so the SQL carries the cap and the
    window is applied to the capped rows. Asserted on the statement text, which is where the
    property lives."""
    assert "order by id desc" in coverage._ELIGIBLE_RUNS.text.lower()
    assert "limit :cap" in coverage._ELIGIBLE_RUNS.text.lower()
    assert coverage.COVERAGE_RUN_CAP == 25_000


def test_a_run_that_priced_but_produced_no_gap_row_is_one_no_gap_unit_per_variant(db_session):
    """§1.3(a) and its expected result: "a run whose markets produced no gap row contributes
    exactly one `no_gap` unit per scheduled variant".

    Computed by hand: three markets, two variants, no gap rows at all -> 6 scheduled units and
    6 `no_gap` completion units, one per (market, variant) pair.
    """
    cells = {i: coverage.Cell(sport="nfl", market_type="moneyline") for i in (1, 2, 3)}
    rows = coverage.evaluation_completion_rows(
        cells, ("aaaaaaaaaaaa", "bbbbbbbbbbbb"), {}, gapped=set(), scored={"aaaaaaaaaaaa",
                                                                          "bbbbbbbbbbbb"},
        budget_exhausted=False, overdue_ms=10)
    totals = {}
    for _cell, outcome, n, _ms in rows:
        totals[outcome] = totals.get(outcome, 0) + n
    assert totals == {"no_gap": 6}


def test_the_backlog_samples_are_per_family_and_read_only_this_run_s_rows(db_session):
    """§1.3(c): `normalize.backlog_ids` is `max(raw_responses.id)` this tick wrote minus
    `normalize_state.last_raw_id`, per family, computed from the ids the tick itself inserted --
    the read is bounded by `run_id` on `ix_raw_run` and scans nothing else.

    Computed by hand: this run wrote raw ids 1..4 for `kalshi_markets` and the family's
    watermark sits at 2, so the backlog is 4 - 2 = 2 rows; `espn` wrote id 5 with its watermark
    at 5, so its backlog is 0 and is still reported -- a family at zero is evidence too.
    """
    from harness.normalize.runner import backlog_samples

    run = Run(started_at=NOW, status="running")
    db_session.add(run)
    db_session.flush()
    for i in range(4):
        db_session.add(RawResponse(run_id=run.id, source="kalshi", endpoint="/markets",
                                   params={"series_ticker": "KXNFLGAME"}, http_status=200,
                                   body={}, fetched_at=NOW))
    db_session.add(RawResponse(run_id=run.id, source="espn", endpoint="/nfl/scoreboard",
                               params={}, http_status=200, body={}, fetched_at=NOW))
    db_session.flush()
    ids = db_session.execute(text(
        "select endpoint, max(id) from raw_responses where run_id = :run group by 1"),
        {"run": run.id}).all()
    newest = {endpoint: max_id for endpoint, max_id in ids}
    db_session.add(NormalizeState(family="kalshi_markets",
                                  last_raw_id=newest["/markets"] - 2))
    db_session.add(NormalizeState(family="espn", last_raw_id=newest["/nfl/scoreboard"]))
    db_session.flush()

    samples = {(name, labels["family"]): value
               for name, value, labels in backlog_samples(db_session, run.id, NOW)
               if name == "normalize.backlog_ids"}
    assert samples[("normalize.backlog_ids", "kalshi_markets")] == 2
    assert samples[("normalize.backlog_ids", "espn")] == 0


def test_family_of_agrees_with_the_normalizer_s_own_filter():
    """`family_of` is the Python side of `_family_filter`, and the two must name the same family
    for the same row or the backlog number is about a different queue than the one that drains.
    One representative endpoint per family, written by hand from `harness/recorder/tick.py`'s
    own `store_raw` call sites."""
    from harness.normalize.runner import FAMILIES, family_of

    cases = {
        ("espn", "/nfl/scoreboard"): "espn",
        ("odds_api", "/sports/americanfootball_nfl/odds"): "odds_featured",
        ("odds_api", "/sports/americanfootball_nfl/events/abc/odds"): "odds_alternates",
        ("kalshi", "/events"): "kalshi_events",
        ("kalshi", "/markets"): "kalshi_markets",
        ("kalshi", "/markets/KXNFL-T/orderbook"): "kalshi_orderbook",
        ("kalshi", "/markets/trades"): "kalshi_trades",
        ("kalshi", "/series/KXNFLGAME"): "kalshi_series",
    }
    assert set(cases.values()) == set(FAMILIES)
    for (source, endpoint), family in cases.items():
        assert family_of(source, endpoint) == family
    assert family_of("kalshi", "/portfolio/balance") is None


def test_the_oldest_unprocessed_read_prints_its_plan(db_session, capsys):
    """§1.3(c), the same discipline ruling I2 applies to `duplicate_trades`: the id-lag number
    costs nothing, and the age number costs a read whose plan has to be looked at before it is
    kept. If the plan is not an ordered index scan returning one row, set
    `NORMALIZE_AGE_ENABLED = False` and keep the id lag alone -- the proposal's decision rule
    (§1.3d) then reads the id lag against the cadence in force, which is the same comparison one
    step less directly.
    """
    from harness.normalize.runner import _OLDEST_UNPROCESSED

    plan = "\n".join(row[0] for row in db_session.execute(text(
        f"explain (analyze, buffers) {_OLDEST_UNPROCESSED.text}"), {"cursor": 0}).all())
    with capsys.disabled():
        print("\n-- EXPLAIN (ANALYZE, BUFFERS), oldest unprocessed raw row --\n" + plan)
    assert "raw_responses" in plan.lower()


def test_a_real_tick_writes_the_phase_split_and_the_backlog_samples(env_settings, db_session):
    """The wiring, not the units: Task 6's fix round 1 was about samples that existed as
    functions and reached no table. One mocked tick through `maybe_tick`, and the three
    `recorder.phase_ms` rows and the per-family `normalize.backlog_ids` rows are read back out
    of `metric_samples`.

    Both live inside the tick's existing `try` around `telemetry.record_many`, so this also
    proves the guarded path writes rather than swallowing.
    """
    import httpx
    import respx

    from harness.db.models import MetricSample
    from harness.normalize import runner as runner_mod
    from tests.test_tick import ESPN, KM, ODDS, _recorder

    runner_mod._EVENTS.clear()
    try:
        with respx.mock:
            respx.get(url__regex=r"https://k/series/.*").mock(
                return_value=httpx.Response(200, json={"series": {}}))
            respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=ESPN))
            respx.get("https://e/college-football/scoreboard").mock(
                return_value=httpx.Response(200, json={"events": []}))
            respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(
                return_value=httpx.Response(200, json=ODDS))
            respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(
                return_value=httpx.Response(200, json={}))
            respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
            respx.get("https://k/events").mock(
                return_value=httpx.Response(200, json={"cursor": "", "events": []}))
            respx.get("https://k/markets/trades").mock(
                return_value=httpx.Response(200, json={"trades": []}))
            respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(
                return_value=httpx.Response(200, json={"orderbook_fp": {}}))
            rec, _clock = _recorder(env_settings, db_session)
            run = rec.maybe_tick()
    finally:
        runner_mod._EVENTS.clear()

    samples = db_session.query(MetricSample).filter(
        MetricSample.name.in_(("recorder.phase_ms", "normalize.backlog_ids"))).all()
    phases = {s.labels["phase"] for s in samples if s.name == "recorder.phase_ms"}
    assert phases == {"fetch", "normalize", "pricing"}, run.notes
    families = {s.labels["family"] for s in samples if s.name == "normalize.backlog_ids"}
    assert {"espn", "odds_featured", "kalshi_markets"} <= families, families
