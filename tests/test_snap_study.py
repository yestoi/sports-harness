"""The Study builder: cells verbatim, the provisional label with its age, the equity curve's
coverage, and the markdown as text."""

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from harness.dashboard import snapshots
from harness.dashboard.snapshots import study
from harness.dashboard.snapshots.study import (MTM_GREY_COVERAGE, STUDY_KEYS, build_study,
                                               parse_week, stale_study_names, weeks_available)
from harness.db.models import (DashboardSnapshot, EquitySnapshot, OperatorEvent, ReportCell,
                               ReportRun)

NOW = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)   # ISO 2026-W38, a Monday


def _run(session, *, year=2026, week=37, provisional=False, generated_at=None, markdown="# w37"):
    row = ReportRun(year=year, week=week, generated_at=generated_at or NOW - timedelta(hours=2),
                    provisional=provisional, build_sha="abc", criteria_hash="h",
                    config_hashes=[], markdown=markdown,
                    markdown_sha256=hashlib.sha256(markdown.encode()).hexdigest())
    session.add(row)
    session.flush()
    return row


def _cell(session, run, table_key, row_key, col_key, **fields):
    session.add(ReportCell(report_run_id=run.id, table_key=table_key, row_key=row_key,
                           col_key=col_key, flags=fields.pop("flags", {}), **fields))
    session.flush()


def _build(session, settings, name):
    token = snapshots.current_name.set(name)
    try:
        return build_study(session, NOW, settings)
    finally:
        snapshots.current_name.reset(token)


def test_parse_week_reads_the_name():
    assert parse_week("study:2026-37") == (2026, 37)
    assert parse_week("study:2026-7") == (2026, 7)


def test_a_week_with_no_run_says_so(db_session, env_settings):
    payload = _build(db_session, env_settings, "study:2026-37")
    assert payload["report_run_id"] is None
    assert payload["cells"] == {}
    assert "No report has been stored" in " ".join(payload["sentences"]["ledger"])


def test_a_closed_week_takes_the_newest_non_provisional_run(db_session, env_settings):
    _run(db_session, provisional=True, generated_at=NOW - timedelta(minutes=10))
    final = _run(db_session, provisional=False, generated_at=NOW - timedelta(hours=3))
    payload = _build(db_session, env_settings, "study:2026-37")
    assert payload["report_run_id"] == final.id
    assert payload["provisional"] is False


def test_the_current_week_takes_the_newest_provisional_run_with_its_cell_age(db_session,
                                                                            env_settings):
    """Ruling B-I4: report_wtd is six-hourly, so a 10-minute snapshot can carry cells hours
    old. The label carries the age."""
    run = _run(db_session, year=2026, week=38, provisional=True,
               generated_at=NOW - timedelta(hours=5))
    payload = _build(db_session, env_settings, "study:2026-38")
    assert payload["report_run_id"] == run.id
    assert payload["provisional"] is True
    assert payload["cell_age_s"] == pytest.approx(5 * 3600, abs=5)


def test_cells_are_carried_verbatim_and_never_recomputed(db_session, env_settings):
    run = _run(db_session)
    _cell(db_session, run, "t1", "sharp_direct", "fill rate", estimate=Decimal("0.123456"),
          n_obs=200, n_clusters=44, lo=Decimal("0.100000"), hi=Decimal("0.150000"),
          text="0.123 [0.100, 0.150]", flags={"greyed": False, "flagged": False,
                                              "not_collected": False})
    payload = _build(db_session, env_settings, "study:2026-37")
    cell = payload["cells"]["t1"]["sharp_direct"]["fill rate"]
    assert cell["text"] == "0.123 [0.100, 0.150]"
    assert cell["estimate"] == 0.123456 and cell["n_clusters"] == 44
    assert cell["flags"] == {"greyed": False, "flagged": False, "not_collected": False}
    body = Path(study.__file__).read_text()
    assert "cluster_ci" not in body and "mean(" not in body


def test_a_cell_text_is_shown_as_stored_and_never_sanitized(db_session, env_settings):
    """Ruling A-I5: the sanitizer strips %, +, $, [ and ], so applying it to a stored cell would
    corrupt the string spec §1.1 requires be shown exactly as the report printed it."""
    run = _run(db_session)
    _cell(db_session, run, "t1", "sharp_direct", "clv", text="+3.1 % [-0.2, +6.4]")
    payload = _build(db_session, env_settings, "study:2026-37")
    assert payload["cells"]["t1"]["sharp_direct"]["clv"]["text"] == "+3.1 % [-0.2, +6.4]"


def test_the_markdown_is_carried_with_its_sha_and_not_sanitized(db_session, env_settings):
    hostile = "# w37\n\n<script>alert(1)</script>\n<img onerror=alert(2) src=x>\n"
    run = _run(db_session, markdown=hostile)
    payload = _build(db_session, env_settings, "study:2026-37")
    assert payload["markdown"] == hostile
    assert payload["markdown_sha256"] == run.markdown_sha256
    assert payload["markdown_sha256"] == hashlib.sha256(hostile.encode()).hexdigest()


def test_equity_carries_coverage_per_point_and_greys_the_marked_line(db_session, env_settings):
    """Ruling B-(d): a marked-to-market curve at 40 % coverage is a different number from one
    at 100 %, and the surface has to say so."""
    _run(db_session)
    # Inside week 37, which ends at Monday 2026-09-14 00:00 CT -- NOW is that Monday afternoon,
    # so an offset in minutes would land in week 38 and `_equity` would return nothing.
    for days, coverage in ((5, Decimal("0.9000")), (3, Decimal("0.3000"))):
        db_session.add(EquitySnapshot(ts=NOW - timedelta(days=days),
                                      variant_id="sharp_direct", cash=Decimal("1000.00"),
                                      open_stake=Decimal("50.00"), mtm_open=Decimal("60.00"),
                                      mtm_coverage=coverage, n_open_positions=2,
                                      n_open_orders=1))
    db_session.flush()
    equity = _build(db_session, env_settings, "study:2026-37")["equity"]
    lane = equity["variants"][0]
    assert lane["min_coverage"] == pytest.approx(0.3)
    assert lane["mtm_grey"] is True
    assert all(len(point) == 4 for point in lane["points"])   # ts, cash, cash+mtm, coverage
    assert equity["grey_below"] == MTM_GREY_COVERAGE
    assert "under half" in " ".join(
        _build(db_session, env_settings, "study:2026-37")["sentences"]["equity"])


def test_annotations_come_from_operator_events_in_the_week(db_session, env_settings):
    _run(db_session)
    db_session.add(OperatorEvent(ts=NOW - timedelta(days=3), kind="deploy",
                                 summary="<b>deploy</b> 6e3b33f", ref={}))
    db_session.flush()
    notes = _build(db_session, env_settings, "study:2026-37")["annotations"]
    assert notes and "<" not in notes[0]["summary"]


def test_the_week_list_comes_from_report_runs(db_session, env_settings):
    _run(db_session, week=37)
    _run(db_session, week=38, provisional=True)
    assert weeks_available(db_session) == ["2026-38", "2026-37"]


def test_stale_study_names_lists_a_week_whose_report_moved_on(db_session, env_settings):
    """Addendum §0.1: the scheduler's `study` job rebuilds any week whose newest report_runs.id
    differs from the one recorded inside its stored payload, so no request handler ever builds."""
    run = _run(db_session)
    db_session.add(DashboardSnapshot(name="study:2026-37", generated_at=NOW, elapsed_ms=10,
                                     payload={"report_run_id": run.id}, error=None))
    db_session.flush()
    assert stale_study_names(db_session, NOW) == []

    newer = _run(db_session, generated_at=NOW - timedelta(minutes=1))
    assert stale_study_names(db_session, NOW) == ["study:2026-37"]
    assert newer.id != run.id


def test_stale_study_names_lists_a_week_with_no_snapshot_at_all(db_session, env_settings):
    _run(db_session)
    assert stale_study_names(db_session, NOW) == ["study:2026-37"]


def test_stale_study_names_leaves_a_closed_week_that_has_only_provisional_runs(db_session,
                                                                              env_settings):
    """Fix round 1, I1: the stale predicate mirrors `build_study`'s own choice. A closed week
    whose newest run is provisional -- the normal state between the ISO rollover and `harness
    report` -- has no non-provisional run to build from, so listing it would make the scheduler
    rebuild the same empty payload on every ten-minute tick forever."""
    _run(db_session, week=36, provisional=True, generated_at=NOW - timedelta(days=8))
    assert stale_study_names(db_session, NOW) == []

    # ...while the *current* week does take the newest run of either kind, so a provisional-only
    # current week is still built.
    _run(db_session, week=38, provisional=True, generated_at=NOW - timedelta(hours=5))
    assert stale_study_names(db_session, NOW) == ["study:2026-38"]


def test_stale_study_names_ignores_a_provisional_run_newer_than_the_final_one(db_session,
                                                                             env_settings):
    """The closed week's payload is built from the final run, so the stale check must compare
    against that same id and not against a provisional run written later in the week."""
    final = _run(db_session, week=36, provisional=False, generated_at=NOW - timedelta(days=9))
    _run(db_session, week=36, provisional=True, generated_at=NOW - timedelta(days=8))
    db_session.add(DashboardSnapshot(name="study:2026-36", generated_at=NOW, elapsed_ms=10,
                                     payload={"report_run_id": final.id}, error=None))
    db_session.flush()
    assert stale_study_names(db_session, NOW) == []


def test_the_payload_carries_only_the_allowed_keys(db_session, env_settings):
    _run(db_session)
    payload = _build(db_session, env_settings, "study:2026-37")
    assert set(payload) == STUDY_KEYS


def test_declined_counts_come_from_the_stored_text_and_order_the_rows(db_session, env_settings):
    """Fix round 1, C1: t12's `count` is a plain int and `share` a plain float, so
    `_cell_fields` leaves every numeric column NULL and puts the number in `text` alone. Reading
    `estimate` alone made the surface say "0 chances were turned down" over a full table."""
    run = _run(db_session)
    for row_key, count, share in (("sharp_direct/rejected:edge", "42", "0.7000"),
                                  ("sharp_direct/skipped:kickoff", "9", "0.3000")):
        _cell(db_session, run, "t12", row_key, "count", text=count)
        _cell(db_session, run, "t12", row_key, "share", text=share)
    payload = _build(db_session, env_settings, "study:2026-37")

    assert "51 chances were turned down" in payload["sentences"]["declined"][0]
    readings = payload["readings"]["declined"]
    assert "42 times (70 %)" in readings[0]          # biggest first
    assert "9 times (30 %)" in readings[1]
    assert "this variant's own target price" in readings[0]      # the rejected estimator
    assert "the price snapshot the intent was made on" in readings[1]


def test_an_unknown_declined_reason_lands_in_sentences_gaps_sanitized(db_session, env_settings):
    """Ruling A-I2: a reason code the vocabulary has never seen must not be silently lost."""
    run = _run(db_session)
    _cell(db_session, run, "t12", "sharp_direct/rejected:<script>brand_new</script>", "count",
         text="4")
    _cell(db_session, run, "t12", "sharp_direct/rejected:<script>brand_new</script>", "share",
         text="1.0000")
    payload = _build(db_session, env_settings, "study:2026-37")
    assert payload["sentences_gaps"] == ["scriptbrand_new/script"]


def test_sentences_gaps_is_empty_when_no_report_run_exists(db_session, env_settings):
    payload = _build(db_session, env_settings, "study:2026-37")
    assert payload["sentences_gaps"] == []


def test_a_declined_share_of_double_dash_reads_as_no_number(db_session, env_settings):
    """`PLACEHOLDER` is "--" when the kind's total is zero. It must read as None, not raise."""
    run = _run(db_session)
    _cell(db_session, run, "t12", "sharp_direct/skipped:kickoff", "count", text="4")
    _cell(db_session, run, "t12", "sharp_direct/skipped:kickoff", "share", text="--")
    payload = _build(db_session, env_settings, "study:2026-37")
    assert "4 times (--)" in payload["readings"]["declined"][0]


def test_the_contrast_benchmark_is_imported_from_the_report(db_session, env_settings):
    """Fix round 1, I2: a rename in `harness/report/tables.py` must not silently empty the
    contrasts sentence."""
    from harness.report.tables import CONTRAST_BENCHMARK

    run = _run(db_session)
    _cell(db_session, run, "t2", "sharp_direct", CONTRAST_BENCHMARK,
          estimate=Decimal("0.020000"), n_obs=100, n_clusters=40, lo=Decimal("0.010000"),
          hi=Decimal("0.030000"), text="+0.0200 [+0.0100, +0.0300]")
    payload = _build(db_session, env_settings, "study:2026-37")
    joined = " ".join(payload["sentences"]["contrasts"])
    assert CONTRAST_BENCHMARK in joined and "sharp_direct" in joined
    assert '"pinnacle_t5"' not in Path(study.__file__).read_text()


def test_no_study_sql_names_a_forbidden_table():
    body = Path(study.__file__).read_text().lower()
    for table in ("orderbook_events", "venue_trades", "raw_responses", "odds_snapshots",
                  "venue_quotes"):
        assert table not in body
