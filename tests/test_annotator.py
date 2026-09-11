"""The annotator: its trigger, its checks, and the fence its output renders inside."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import harness.research.annotate as annotate_module
from harness.db.models import ReportRun
from harness.report.tables import Table
from harness.research.annotate import (BULLETS_MAX, EFFORT, PROMPT_HASH, annotate_pass,
                                       pending_report)
from harness.research.client import CallResult
from harness.research.spend import Usage

NOW = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)   # Monday of ISO week 39

#: Review round 1: the week rolls over on America/Chicago's clock, not UTC's. 04:30 UTC Monday
#: is still 23:30 CT Sunday (week 38); 05:30 UTC Monday is 00:30 CT Monday (week 39).
NOW_SUNDAY_CT = datetime(2026, 9, 21, 4, 30, tzinfo=timezone.utc)
NOW_MONDAY_CT = datetime(2026, 9, 21, 5, 30, tzinfo=timezone.utc)


# --- fixtures ---------------------------------------------------------------------------------
#
# `weekly_tables` recomputes tables 1-12 from orders, signals and fills; that machinery is
# already exercised by `tests/test_report.py`. What this file tests is the trigger, the citation
# and number checks, and the spend/registration wiring around it -- so every fixture here
# monkeypatches `harness.research.annotate.weekly_tables` to a single fixed table rather than
# seeding a week's worth of order data. The table is deliberately the same shape T17 already
# tests against (`tests/test_render_for_model.py`): a variant name in column 0 (never rendered
# to the model, per B-I5) and an "orders" count of `4120356789` in column 1, whose rendered text
# carries "412" as a contiguous substring and every digit 0-9 somewhere else in the string -- the
# second property only matters to `test_at_most_five_bullets_survive` below, which appends a
# bare digit to each bullet's text.


def _stub_tables():
    return {
        "t1": Table(
            title="Table 1 (t1): order lifecycle",
            header="one row per variant; orders is the week's non-replay order count",
            columns=["variant", "orders", "fill_rate"],
            rows=[["sharp_direct", "4120356789", 0.31]],
        ),
    }


def _run(session, year, week, provisional, generated_at) -> ReportRun:
    run = ReportRun(year=year, week=week, generated_at=generated_at, provisional=provisional,
                    build_sha="0" * 40, criteria_hash="0" * 64, config_hashes=[])
    session.add(run)
    session.flush()
    return run


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


def _client(bullets: list[str]) -> _FakeClient:
    return _FakeClient(bullets)


@pytest.fixture
def keyed_settings(env_settings, tmp_path):
    key = tmp_path / "anthropic_api_key"
    key.write_text("sk-ant-not-a-real-key")
    return env_settings.model_copy(update={"anthropic_api_key_file": key})


@pytest.fixture
def seeded_reports(db_session, monkeypatch):
    """One final run for last week, one provisional run this week (proving `provisional` is
    excluded even alongside a real row for the current week) and exactly one final run this
    week -- the single row `pending_report` must find, and the only row left un-annotated once
    it has been annotated once (`test_an_already_annotated_run_is_not_annotated_again`)."""
    monkeypatch.setattr(annotate_module, "weekly_tables", lambda *a, **k: _stub_tables())
    _run(db_session, 2026, 38, False, datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc))
    _run(db_session, 2026, 39, True, datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc))
    final = _run(db_session, 2026, 39, False, datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    return SimpleNamespace(final_this_week=final.id)


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


# --- the checks and the store --------------------------------------------------------------------


def test_a_surviving_bullet_is_stored(db_session, keyed_settings, seeded_reports):
    counts = annotate_pass(db_session, NOW, keyed_settings,
                           client=_client(["412 orders on the first row t1[0,1]."]))
    assert counts == {"annotated": 1, "bullets": 1, "dropped": 0}
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
    strings and F60 keeps them out of a prompt."""
    client = _client(["412 orders t1[0,1]."])
    annotate_pass(db_session, NOW, keyed_settings, client=client)
    assert "sharp_direct" not in client.calls[0]["user"]


def test_the_call_is_high_effort_and_toolless(db_session, keyed_settings, seeded_reports):
    client = _client(["412 orders t1[0,1]."])
    annotate_pass(db_session, NOW, keyed_settings, client=client)
    assert client.calls[0]["effort"] == EFFORT == "high"
    assert client.calls[0]["tools"] == ()


# --- spend ----------------------------------------------------------------------------------------


def test_the_reservation_is_released(db_session, keyed_settings, seeded_reports):
    annotate_pass(db_session, NOW, keyed_settings, client=_client(["412 orders t1[0,1]."]))
    assert db_session.execute(text(
        "select coalesce(sum(usd_reserved), 0) from research_spend")).scalar() == Decimal("0.0000")


def test_a_budget_refusal_writes_no_annotation_and_leaves_the_trigger_pending(
        db_session, keyed_settings, seeded_reports):
    settings = keyed_settings.model_copy(update={"veto_daily_usd_cap": Decimal("0.001")})
    counts = annotate_pass(db_session, NOW, settings, client=_client(["412 orders t1[0,1]."]))
    assert counts == {"annotated": 0, "bullets": 0, "dropped": 0}
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
    assert annotate_pass(db_session, NOW, env_settings, client=None) == {
        "annotated": 0, "bullets": 0, "dropped": 0}


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
