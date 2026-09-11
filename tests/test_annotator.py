"""The annotator: its trigger, its checks, its render source, its backoff, and the fence its
output renders inside."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import harness.report.render_for_model as render_module
import harness.report.tables as tables_module
import harness.research.annotate as annotate_module
from harness.db.models import ReportRun
from harness.report.tables import Table
from harness.report.weekly import persist_report
from harness.research.annotate import (BACKOFF_LONG, BACKOFF_SHORT, BACKOFF_STRIKES,
                                       BULLETS_MAX, EFFORT, PROMPT_HASH, annotate_pass,
                                       pending_report)
from harness.research.client import CallResult
from harness.research.spend import Usage

NOW = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)   # Monday of ISO week 39

#: Review round 1: the week rolls over on America/Chicago's clock, not UTC's. 04:30 UTC Monday
#: is still 23:30 CT Sunday (week 38); 05:30 UTC Monday is 00:30 CT Monday (week 39).
NOW_SUNDAY_CT = datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc)
NOW_MONDAY_CT = datetime(2026, 9, 21, 5, 30, tzinfo=timezone.utc)

_EMPTY_COUNTS = {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 0,
                 "tables_omitted": 0}


# --- fixtures ---------------------------------------------------------------------------------
#
# `weekly_tables` recomputes tables 1-12 from orders, signals and fills; that machinery is
# already exercised by `tests/test_report.py`, and fix 36 means `annotate_pass` never calls it
# at all. What this file tests is the trigger, the citation and number checks, the backoff, and
# the spend/registration wiring -- so every fixture here stores one small table's cells the way
# `harness/report/weekly.py::persist_report` does (`report_run_id, table_key, row_key, col_key,
# text`) rather than seeding a week's worth of order data. The table has one column besides the
# row key (`orders`) so its reconstructed position is unambiguous: `render_from_cells` orders
# the columns it recovers from `report_cells` alphabetically by name, which only matters when
# there is more than one non-identity column to order. Row 0's `orders` value is the same
# `4120356789` T17's own fixture used, chosen so its rendered text carries "412" as a contiguous
# substring -- the property `test_at_most_five_bullets_survive` needs.


def _stub_tables():
    return {
        "t1": Table(
            title="Table 1 (t1): order lifecycle",
            header="one row per variant; orders is the week's non-replay order count",
            columns=["variant", "orders"],
            rows=[["sharp_direct", "4120356789"]],
        ),
    }


def _run(session, year, week, provisional, generated_at) -> ReportRun:
    """A bare `report_runs` row, no cells -- enough for `pending_report`, which never reads
    `report_cells`."""
    run = ReportRun(year=year, week=week, generated_at=generated_at, provisional=provisional,
                    build_sha="0" * 40, criteria_hash="0" * 64, config_hashes=[])
    session.add(run)
    session.flush()
    return run


def _persisted(session, year, week, provisional, generated_at, tables=None) -> int:
    """A `report_runs` row and its `report_cells`, written the same way `harness report` does."""
    run_id = persist_report(session, tables if tables is not None else _stub_tables(),
                            {"generated_at": generated_at}, year, week, provisional,
                            markdown=None)
    session.flush()
    return run_id


class _FakeClient:
    """A `ResearchClient` double: `call(...)` records its kwargs and answers `{"bullets": ...}`
    wrapped in a real `CallResult`, so `write_notes` and `release_spend` see exactly the shape
    the production client returns."""

    def __init__(self, bullets: list[str]) -> None:
        self._bullets = bullets
        self.calls: list[dict] = []

    def call(self, **kwargs) -> CallResult:
        self.calls.append(kwargs)
        return CallResult(model=kwargs["model"], output={"bullets": self._bullets},
                          usage=Usage(input_tokens=100, output_tokens=50), stop_reason="end_turn",
                          request_id="fake-request-id", latency_ms=5, tool_calls=[],
                          snippets={"items": [], "truncated": False}, error=None)


class _ErrorClient:
    """A call that returns, but as a captured error -- `harness/research/veto.py`'s
    `veto_error` shape -- rather than raising."""

    def call(self, **kwargs) -> CallResult:
        return CallResult(model=kwargs["model"], output=None,
                          usage=Usage(input_tokens=10, output_tokens=0), stop_reason="pause_turn",
                          request_id="fake-request-id", latency_ms=5, tool_calls=[],
                          snippets={"items": [], "truncated": False}, error="pause_turn")


class _RaisingClient:
    """A call that raises, standing in for a transport failure."""

    def call(self, **kwargs) -> CallResult:
        raise ConnectionError("boom")


def _client(bullets: list[str]) -> _FakeClient:
    return _FakeClient(bullets)


@pytest.fixture
def keyed_settings(env_settings, tmp_path):
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    return env_settings.model_copy(update={"anthropic_api_key_file": key})


@pytest.fixture
def seeded_reports(db_session):
    """One final run for last week, one provisional run this week (proving `provisional` is
    excluded even alongside a real row for the current week) and exactly one final run this
    week, with its cells stored -- the single row `pending_report` must find, and the only row
    left un-annotated once it has been annotated once
    (`test_an_already_annotated_run_is_not_annotated_again`)."""
    _run(db_session, 2026, 38, False, datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc))
    _run(db_session, 2026, 39, True, datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc))
    final_id = _persisted(db_session, 2026, 39, False,
                          datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    return SimpleNamespace(final_this_week=final_id)


@pytest.fixture
def seeded_provisional_only(db_session):
    _run(db_session, 2026, 39, True, datetime(2026, 9, 19, 9, 0, tzinfo=timezone.utc))


@pytest.fixture
def seeded_last_week_only(db_session):
    _run(db_session, 2026, 38, False, datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc))


@pytest.fixture
def seeded_sunday_final(db_session) -> ReportRun:
    """One final report for week 38, generated Sunday evening CT -- the boundary review round 1
    flagged: for about five hours UTC has rolled to Monday while America/Chicago has not."""
    return _run(db_session, 2026, 38, False, datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc))


def _job_state_value(session, key):
    return session.execute(text("select value from job_state where key = :k"),
                           {"k": key}).scalar()


# --- the trigger --------------------------------------------------------------------------------


def test_the_trigger_is_the_week_s_final_report(db_session, seeded_reports):
    """Review B, C2: data-triggered, not clock-triggered. The newest non-provisional run of the
    current ISO week that has no annotation."""
    run = pending_report(db_session, NOW)
    assert run is not None and run.id == seeded_reports.final_this_week


def test_a_provisional_run_is_never_annotated(db_session, seeded_provisional_only):
    assert pending_report(db_session, NOW) is None


def test_an_already_annotated_run_is_not_annotated_again(db_session, keyed_settings,
                                                         seeded_reports):
    annotate_pass(db_session, NOW, keyed_settings,
                  client=_client(["412 orders on the first row t1[0,1]."]))
    assert pending_report(db_session, NOW) is None


def test_last_week_s_report_is_not_annotated_this_week(db_session, seeded_last_week_only):
    assert pending_report(db_session, NOW) is None


def test_a_sunday_evening_ct_final_report_is_found_before_midnight_ct(db_session,
                                                                      seeded_sunday_final):
    """Review round 1: at Sunday 23:30 CT (04:30 UTC Monday) the week is still 38 in Chicago,
    so the just-written week-38 final report is still "this week"."""
    run = pending_report(db_session, NOW_SUNDAY_CT)
    assert run is not None and run.id == seeded_sunday_final.id


def test_the_same_report_is_not_found_after_midnight_ct(db_session, seeded_sunday_final):
    """At Monday 00:30 CT (05:30 UTC) the Chicago week has turned to 39; the week-38 report is
    behind "current" for good and needs a week-39 report of its own to be found."""
    assert pending_report(db_session, NOW_MONDAY_CT) is None


# --- rendering from stored cells, not `weekly_tables` (fix 36) --------------------------------


def test_annotate_pass_never_calls_weekly_tables(db_session, keyed_settings, seeded_reports,
                                                 monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("weekly_tables must not be called (fix 36)")

    monkeypatch.setattr(tables_module, "weekly_tables", _boom)
    counts = annotate_pass(db_session, NOW, keyed_settings,
                           client=_client(["412 orders on the first row t1[0,1]."]))
    assert counts["annotated"] == 1
    # The monkeypatch above only catches a call made *through* the module. The stronger claim is
    # that the name is not bound in the pass's own namespace at all, so a future `from
    # harness.report.tables import weekly_tables` re-added here fails this test rather than
    # slipping past a patch that would no longer see it.
    assert not hasattr(annotate_module, "weekly_tables")


def test_a_run_with_no_cells_is_skipped_and_left_pending(db_session, keyed_settings):
    _run(db_session, 2026, 39, False, datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    counts = annotate_pass(db_session, NOW, keyed_settings, client=_client(["x"]))
    assert counts == _EMPTY_COUNTS
    assert pending_report(db_session, NOW) is not None


# --- the checks and the store --------------------------------------------------------------------


def test_a_surviving_bullet_is_stored(db_session, keyed_settings, seeded_reports):
    counts = annotate_pass(db_session, NOW, keyed_settings,
                           client=_client(["412 orders on the first row t1[0,1]."]))
    assert counts == {"annotated": 1, "bullets": 1, "dropped": 0, "backed_off": 0,
                     "tables_omitted": 0}
    row = db_session.execute(text(
        "select model, prompt_hash, bullets, cost_usd from report_annotations")).first()
    assert row.model == "claude-opus-5" and row.prompt_hash == PROMPT_HASH
    assert row.bullets == ["412 orders on the first row t1[0,1]."]
    assert row.cost_usd >= 0


def test_a_bullet_with_no_citation_is_dropped(db_session, keyed_settings, seeded_reports):
    counts = annotate_pass(db_session, NOW, keyed_settings,
                           client=_client(["Fill rates improved this week."]))
    assert counts["bullets"] == 0 and counts["dropped"] == 1
    assert db_session.execute(text("select bullets from report_annotations")).scalar() == []


def test_a_bullet_that_invents_a_number_is_dropped(db_session, keyed_settings, seeded_reports):
    counts = annotate_pass(db_session, NOW, keyed_settings,
                           client=_client(["Fill rate hit 0.99 t1[0,1]."]))
    assert counts["dropped"] == 1


def test_at_most_five_bullets_survive(db_session, keyed_settings, seeded_reports):
    bullets = [f"412 orders t1[0,1]. #{i}" for i in range(9)]
    counts = annotate_pass(db_session, NOW, keyed_settings, client=_client(bullets))
    assert counts["bullets"] == BULLETS_MAX == 5


def test_the_prompt_carries_no_row_key(db_session, keyed_settings, seeded_reports):
    """B-I5: the model is shown row indices. A variant name and a ticker are venue-sourced
    strings and F60 keeps them out of a prompt -- `render_from_cells` recovers which stored
    column duplicates the row key and keeps that one out of the rendered line too."""
    client = _client(["412 orders t1[0,1]."])
    annotate_pass(db_session, NOW, keyed_settings, client=client)
    assert "sharp_direct" not in client.calls[0]["user"]


def test_the_prompt_never_carries_a_row_key_over_60_characters(db_session, keyed_settings):
    """Fix round 1, Critical 1, through the real write path: `persist_report`'s own truncations
    (`row_key` at 60 characters, the matching cell's `text` at 64) are exactly what broke the
    old value-matching recovery, so this seeds a report through `persist_report` -- the way
    `harness report` really writes one -- with a table 12-shaped first column over 60
    characters, the case the review found leaking on real data."""
    key = ("sharp_direct_nfl_spread/rejected:benchmark_stale_beyond_ten_minutes_here")[:62]
    assert len(key) == 62
    tables = {
        "t12": Table(title="Table 12 (t12): declined candidates", header="h",
                    columns=["variant/reason", "kind", "count", "share"],
                    rows=[[key, "rejected", 3, 0.5]]),
    }
    _persisted(db_session, 2026, 39, False, datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
              tables=tables)
    client = _client(["3 rejected t12[0,2]."])
    annotate_pass(db_session, NOW, keyed_settings, client=client)
    assert key not in client.calls[0]["user"]
    assert key[:60] not in client.calls[0]["user"]


def test_the_call_is_high_effort_and_toolless(db_session, keyed_settings, seeded_reports):
    client = _client(["412 orders t1[0,1]."])
    annotate_pass(db_session, NOW, keyed_settings, client=client)
    assert client.calls[0]["effort"] == EFFORT == "high"
    assert client.calls[0]["tools"] == ()


# --- backoff (fix 36) ---------------------------------------------------------------------------


def test_a_raising_call_backs_the_report_off_without_raising(db_session, keyed_settings,
                                                              seeded_reports):
    """Fix round 1, Critical 2: `annotate_pass` no longer re-raises after a failure -- it used
    to, and that left `release_spend`'s own uncommitted change sitting in the session for
    `ResearchWorker.run_once`'s rollback to erase, restoring the reservation as a phantom. The
    backoff itself is still committed in its own transaction (`_record_failure`), so it survives
    even a rollback nothing here triggers on this path any more."""
    run_id = seeded_reports.final_this_week
    counts = annotate_pass(db_session, NOW, keyed_settings, client=_RaisingClient())
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 1,
                     "tables_omitted": 0}
    db_session.rollback()   # proves the backoff below does not depend on a caller's own commit

    until = _job_state_value(db_session, f"annotate:{run_id}")
    assert until is not None
    assert until == pytest.approx(int((NOW + BACKOFF_SHORT).timestamp()), abs=2)
    assert _job_state_value(db_session, f"annotate:{run_id}:attempts") == 1


def test_the_next_sweep_skips_a_backed_off_report(db_session, keyed_settings, seeded_reports):
    annotate_pass(db_session, NOW, keyed_settings, client=_RaisingClient())
    assert pending_report(db_session, NOW) is None
    counts = annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 t1[0,1]."]))
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 1,
                     "tables_omitted": 0}


def test_a_report_is_no_longer_backed_off_once_its_hour_is_up(db_session, keyed_settings,
                                                              seeded_reports):
    annotate_pass(db_session, NOW, keyed_settings, client=_RaisingClient())
    later = NOW + BACKOFF_SHORT + timedelta(minutes=1)
    run = pending_report(db_session, later)
    assert run is not None and run.id == seeded_reports.final_this_week


def test_three_failed_attempts_back_off_a_day(db_session, keyed_settings, seeded_reports):
    """Each attempt only happens once its predecessor's backoff has elapsed -- a report backed
    off an hour is not retried a second time within that hour -- so the three strikes span three
    sweeps, each just past the last one's `next_attempt_at`."""
    run_id = seeded_reports.final_this_week
    when = NOW
    for _ in range(BACKOFF_STRIKES):
        annotate_pass(db_session, when, keyed_settings, client=_RaisingClient())
        when = when + BACKOFF_SHORT + timedelta(minutes=1)

    assert _job_state_value(db_session, f"annotate:{run_id}:attempts") == BACKOFF_STRIKES
    until = _job_state_value(db_session, f"annotate:{run_id}")
    last_attempt_at = when - BACKOFF_SHORT - timedelta(minutes=1)
    assert until == pytest.approx(int((last_attempt_at + BACKOFF_LONG).timestamp()), abs=2)


def test_a_failure_after_the_call_still_backs_off_and_leaks_no_reservation(
        db_session, keyed_settings, seeded_reports, monkeypatch):
    """Fix round 1, Critical 2. The original bug: only `client.call`'s own exception was backed
    off, so a deterministic failure downstream of a successful (and paid-for) call -- here,
    `write_notes` -- retried the whole pass, including a fresh Anthropic call, every sweep,
    while `research_spend.usd_reserved` leaked a reservation each time and `usd` never recorded
    what was actually spent."""
    def _boom(*_a, **_k):
        raise RuntimeError("write_notes exploded")

    monkeypatch.setattr(annotate_module, "write_notes", _boom)
    run_id = seeded_reports.final_this_week

    counts = annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 t1[0,1]."]))
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 1,
                     "tables_omitted": 0}

    assert _job_state_value(db_session, f"annotate:{run_id}") is not None
    row = db_session.execute(text(
        "select usd_reserved, usd from research_spend")).first()
    assert row.usd_reserved == Decimal("0.0000")
    assert row.usd > 0
    assert db_session.execute(text("select count(*) from report_annotations")).scalar() == 0

    # The next sweep makes no call: the report is backed off.
    monkeypatch.undo()
    client = _client(["412 t1[0,1]."])
    counts = annotate_pass(db_session, NOW, keyed_settings, client=client)
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 1,
                     "tables_omitted": 0}
    assert client.calls == []


def test_a_raw_statement_error_at_the_insert_still_backs_off(db_session, keyed_settings,
                                                              seeded_reports, monkeypatch):
    """Fix round 2, new defect 1. The round-1 fix left a gap: a *database* failure -- not a
    plain Python exception like the `RuntimeError` above, but one that aborts the session's own
    transaction -- left `_record_failure`'s own SQL running inside that aborted transaction, so
    it raised in turn and escaped with no backoff written at all. `cost_usd` is monkeypatched to
    run a real failing statement (`select 1/0`, a genuine `DivisionByZero` from Postgres) on the
    same session `annotate_pass` uses, from inside the annotation-insert stage -- the exact
    shape the re-review reproduced, just moved one stage later since `write_notes` already has
    its own coverage above. `write_notes` itself already committed by this point, so its row
    survives."""
    run_id = seeded_reports.final_this_week

    def _bad_statement(*_a, **_k):
        db_session.execute(text("select 1/0"))
        return Decimal("0")   # unreached; the statement above always raises

    monkeypatch.setattr(annotate_module, "cost_usd", _bad_statement)
    counts = annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 t1[0,1]."]))
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 1,
                     "tables_omitted": 0}

    # This pass's own note, not merely some row: the audit of the call that was paid for.
    note = db_session.execute(text("select kind, subject_id from research_notes")).all()
    assert [(r.kind, r.subject_id) for r in note] == [("annotate", str(run_id))]
    assert _job_state_value(db_session, f"annotate:{run_id}") is not None
    row = db_session.execute(text("select usd_reserved, usd from research_spend")).first()
    assert row.usd_reserved == Decimal("0.0000")
    assert row.usd > 0
    assert db_session.execute(text("select count(*) from report_annotations")).scalar() == 0

    monkeypatch.undo()
    client = _client(["412 t1[0,1]."])
    annotate_pass(db_session, NOW, keyed_settings, client=client)
    assert client.calls == []


def test_an_orm_flush_violation_at_the_insert_still_backs_off(db_session, keyed_settings,
                                                               seeded_reports, monkeypatch):
    """Fix round 2, new defect 1's other trigger: a real ORM flush violation, here a NOT NULL
    constraint on `report_annotations.cost_usd` rather than the raw statement error above, from
    inside the same annotation-insert stage."""
    run_id = seeded_reports.final_this_week
    monkeypatch.setattr(annotate_module, "cost_usd", lambda *_a, **_k: None)

    counts = annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 t1[0,1]."]))
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 1,
                     "tables_omitted": 0}

    # This pass's own note, not merely some row: the audit of the call that was paid for.
    note = db_session.execute(text("select kind, subject_id from research_notes")).all()
    assert [(r.kind, r.subject_id) for r in note] == [("annotate", str(run_id))]
    assert _job_state_value(db_session, f"annotate:{run_id}") is not None
    row = db_session.execute(text("select usd_reserved, usd from research_spend")).first()
    assert row.usd_reserved == Decimal("0.0000")
    assert row.usd > 0
    assert db_session.execute(text("select count(*) from report_annotations")).scalar() == 0

    monkeypatch.undo()
    client = _client(["412 t1[0,1]."])
    annotate_pass(db_session, NOW, keyed_settings, client=client)
    assert client.calls == []


def test_a_failed_insert_still_reports_bullets_already_dropped_this_sweep(
        db_session, keyed_settings, seeded_reports, monkeypatch):
    """Fix round 2, new defect 3: the failure return used to build a fresh, zeroed `counts`
    literal, discarding a real `dropped` count this same sweep had already, correctly,
    produced."""
    monkeypatch.setattr(annotate_module, "cost_usd", lambda *_a, **_k: None)
    bullets = ["412 t1[0,1].", "Fill rates improved this week."]   # the second has no citation
    counts = annotate_pass(db_session, NOW, keyed_settings, client=_client(bullets))
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 1, "backed_off": 1,
                     "tables_omitted": 0}


def test_a_captured_model_error_backs_off_without_writing_an_annotation(
        db_session, keyed_settings, seeded_reports):
    """A `veto_error`-class outcome (`result.error` set, e.g. a `pause_turn`) is a failure that
    must retry later, not a success recorded as zero bullets forever."""
    counts = annotate_pass(db_session, NOW, keyed_settings, client=_ErrorClient())
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 0, "backed_off": 1,
                     "tables_omitted": 0}
    assert db_session.execute(text("select count(*) from report_annotations")).scalar() == 0
    assert pending_report(db_session, NOW) is None

    # The partial state this leaves is the intended one, and it is `_record_failure`'s own
    # commit that makes it durable: the errored call still wrote its `research_notes` row
    # (notes.py: "an errored call writes its row too", so `research_spend` stays
    # reconcilable), and no annotation was written. The rollback proves the note does not
    # depend on a caller's own commit, the same way the backoff tests above do.
    db_session.rollback()
    assert db_session.execute(text(
        "select count(*) from research_notes where kind = 'annotate'")).scalar() == 1


def test_success_clears_a_prior_backoff(db_session, keyed_settings, seeded_reports):
    run_id = seeded_reports.final_this_week
    db_session.execute(text(
        "insert into job_state (key, value, updated_at) values (:k, :v, :now)"),
        {"k": f"annotate:{run_id}", "v": int(NOW.timestamp()) - 10, "now": NOW})
    db_session.execute(text(
        "insert into job_state (key, value, updated_at) values (:k, :v, :now)"),
        {"k": f"annotate:{run_id}:attempts", "v": 2, "now": NOW})

    annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 orders t1[0,1]."]))

    remaining = db_session.execute(text(
        "select count(*) from job_state where key like :p"), {"p": f"annotate:{run_id}%"}
    ).scalar()
    assert remaining == 0


def test_no_pending_report_is_not_counted_as_a_backoff(db_session, keyed_settings,
                                                        seeded_provisional_only):
    assert annotate_pass(db_session, NOW, keyed_settings, client=_client([])) == _EMPTY_COUNTS


def test_a_table_omitted_for_an_unrecoverable_identity_column_is_counted_in_the_sweep(
        db_session, keyed_settings, seeded_reports, monkeypatch):
    """Fix round 2, new defect 2: `render_from_cells`'s per-table omission (fail closed, not
    open) reaches the sweep's own counts, not only `ModelView.tables_omitted`. The pass still
    completes -- an omitted table is not a failure, just a smaller view -- so this is a
    successful sweep that also reports one table left out."""
    monkeypatch.setitem(render_module.IDENTITY_COLUMNS, "t1", "not_the_real_column")
    counts = annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 t1[0,1]."]))
    assert counts["tables_omitted"] == 1
    assert counts["annotated"] == 1
    # t1 is the only table this fixture stores, and it is the one omitted -- so the bullet's
    # citation resolves against nothing and it is dropped, never stored.
    assert counts["dropped"] == 1
    assert counts["bullets"] == 0


# --- spend ----------------------------------------------------------------------------------------


def test_the_reservation_is_released(db_session, keyed_settings, seeded_reports):
    annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 orders t1[0,1]."]))
    assert db_session.execute(text(
        "select coalesce(sum(usd_reserved), 0) from research_spend")).scalar() == Decimal("0.0000")


def test_a_budget_refusal_writes_no_annotation_and_leaves_the_trigger_pending(
        db_session, keyed_settings, seeded_reports):
    settings = keyed_settings.model_copy(update={"veto_daily_usd_cap": Decimal("0.001")})
    counts = annotate_pass(db_session, NOW, settings, client=_client(["412 orders t1[0,1]."]))
    assert counts == _EMPTY_COUNTS
    assert pending_report(db_session, NOW) is not None


def test_the_refused_reservation_does_not_keep_the_week_lock(db_session, keyed_settings,
                                                             seeded_reports):
    """Fix round 2, I1, same shape as the veto's own
    `test_the_refused_reservation_does_not_keep_the_week_lock`: `reserve_spend` takes the
    ISO-week advisory lock before it checks the caps, so a refusal must still end the
    transaction, or the lock outlives the pass and blocks every other reservation on the week."""
    settings = keyed_settings.model_copy(update={"veto_daily_usd_cap": Decimal("0.001")})
    annotate_pass(db_session, NOW, settings, client=_client(["412 orders t1[0,1]."]))
    held = db_session.execute(text(
        "select count(*) from pg_locks where locktype = 'advisory' "
        "and pid = pg_backend_pid()")).scalar()
    assert held == 0


def test_the_reservation_does_not_keep_the_week_lock_across_the_call(db_session, keyed_settings,
                                                                     seeded_reports):
    """Fix round 2, I1: once a week, the annotator's reservation used to hold the ISO-week
    advisory lock for as long as the live call took (up to `REQUEST_TIMEOUT_S`), blocking any
    other `reserve_spend` on the week -- in practice `harness parlay build`, an operator sitting
    at a terminal watching nothing happen. `annotate_pass` now commits the instant the
    reservation returns, so no lock survives even a successful call."""
    annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 orders t1[0,1]."]))
    held = db_session.execute(text(
        "select count(*) from pg_locks where locktype = 'advisory' "
        "and pid = pg_backend_pid()")).scalar()
    assert held == 0


def test_the_pass_is_dormant_without_a_key(db_session, env_settings, seeded_reports):
    assert annotate_pass(db_session, NOW, env_settings, client=None) == _EMPTY_COUNTS


# --- the fence --------------------------------------------------------------------------------


def test_the_report_renders_the_bullets_inside_a_fence():
    """R:232-234: a fenced "model-written, unverified" block. The Monday duty acts on the
    tables, and the fence is what makes that visible to the person reading."""
    from harness.report.weekly import ANNOTATION_HEADER, render_markdown

    document = render_markdown({}, {"year": 2026, "week": 38,
                                    "annotation": ["412 orders t1[0,1]."]})
    assert ANNOTATION_HEADER in document
    assert "```" in document
    assert "412 orders t1[0,1]." in document
    assert document.index(ANNOTATION_HEADER) < document.index("412 orders")


def test_a_report_with_no_annotation_renders_no_fence():
    from harness.report.weekly import ANNOTATION_HEADER, render_markdown

    assert ANNOTATION_HEADER not in render_markdown({}, {"year": 2026, "week": 38})


# --- registration -----------------------------------------------------------------------------


def test_the_pass_is_registered():
    import harness.research.annotate  # noqa: F401
    from harness.research import worker

    assert "harness.research.annotate" in worker.PASS_MODULES
    assert "annotate" in [name for name, _ in worker.load_passes()]
