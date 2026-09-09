"""`/api/snap`, its ETag contract, the anchored name pattern, the static mount's headers, the
scheduler's cadences, and the frozen `/api/summary`."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from harness.dashboard.app import create_dashboard
from harness.dashboard.scheduler import (FLOOR_BACKOFF_S, FLOOR_IN_WINDOW_S, FLOOR_OUT_S,
                                         SnapshotScheduler)
from harness.db.models import DashboardSnapshot, Game, MetricSample

NOW = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _settings(env_settings, tmp_path, snapshots=False):
    token = tmp_path / "dashboard_token"
    token.write_text("supersecret")
    return env_settings.model_copy(update={"dashboard_token_file": token,
                                           "snapshots_enabled": snapshots})


def _client(db_session, settings):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    return TestClient(create_dashboard(factory, settings, clock=lambda: NOW))


def _row(db_session, name="pulse", **fields):
    row = DashboardSnapshot(name=name, generated_at=fields.pop("generated_at", NOW),
                            elapsed_ms=fields.pop("elapsed_ms", 42),
                            payload=fields.pop("payload", {"cadence_s": 30, "status": {}}),
                            error=fields.pop("error", None))
    db_session.merge(row)
    # Committed, not flushed: the request handlers run on their own sessions off the same
    # engine, so an uncommitted row would be invisible to them. The `db_session` fixture
    # truncates every table after the test, so committing here leaves nothing behind.
    db_session.commit()
    return row


def test_snap_index_lists_every_row_with_its_age(db_session, env_settings, tmp_path):
    _row(db_session, "pulse", generated_at=NOW - timedelta(seconds=10))
    _row(db_session, "gate", generated_at=NOW - timedelta(seconds=90))
    body = _client(db_session, _settings(env_settings, tmp_path)).get("/api/snap").json()
    names = {row["name"]: row for row in body["snapshots"]}
    assert names["pulse"]["age_s"] == pytest.approx(10, abs=1)
    assert set(names["pulse"]) == {"name", "generated_at", "age_s", "cadence_s", "elapsed_ms",
                                   "error"}


def test_the_index_reads_the_cadence_out_of_the_payload_without_loading_it(
        db_session, env_settings, tmp_path):
    """The index selects named columns plus `payload ->> 'cadence_s'`, so a first build that
    failed -- `payload = {}` and an `error` class name -- must still list, with a null cadence
    rather than a 500."""
    _row(db_session, "floor", payload={}, error="OperationalError", elapsed_ms=1999)
    body = _client(db_session, _settings(env_settings, tmp_path)).get("/api/snap").json()
    row = {r["name"]: r for r in body["snapshots"]}["floor"]
    assert row["cadence_s"] is None
    assert row["error"] == "OperationalError"
    assert row["elapsed_ms"] == 1999


def test_snap_returns_the_payload_with_an_etag(db_session, env_settings, tmp_path):
    _row(db_session)
    response = _client(db_session, _settings(env_settings, tmp_path)).get("/api/snap/pulse")
    assert response.status_code == 200
    assert response.headers["etag"] == f'"pulse:{NOW.isoformat()}"'
    assert response.headers["cache-control"] == "no-cache"
    assert response.json()["payload"]["cadence_s"] == 30


def test_a_matching_if_none_match_answers_304_with_no_body(db_session, env_settings, tmp_path):
    _row(db_session)
    client = _client(db_session, _settings(env_settings, tmp_path))
    etag = client.get("/api/snap/pulse").headers["etag"]
    again = client.get("/api/snap/pulse", headers={"If-None-Match": etag})
    assert again.status_code == 304 and not again.content


def test_a_stale_if_none_match_answers_200(db_session, env_settings, tmp_path):
    _row(db_session)
    client = _client(db_session, _settings(env_settings, tmp_path))
    assert client.get("/api/snap/pulse",
                      headers={"If-None-Match": '"pulse:2020-01-01T00:00:00+00:00"'}
                      ).status_code == 200


def test_an_unknown_name_is_404_and_a_malformed_one_never_reaches_the_table(
        db_session, env_settings, tmp_path):
    client = _client(db_session, _settings(env_settings, tmp_path))
    assert client.get("/api/snap/nosuch").status_code == 404
    for bad in ("study:26-3", "study:2026-370", "PULSE", "pulse%20"):
        assert client.get(f"/api/snap/{bad}").status_code == 404


def test_a_study_name_is_served_by_primary_key(db_session, env_settings, tmp_path):
    _row(db_session, "study:2026-37", payload={"cadence_s": 600, "week": 37})
    client = _client(db_session, _settings(env_settings, tmp_path))
    assert client.get("/api/snap/study:2026-37").json()["payload"]["week"] == 37


def test_no_request_handler_reaches_a_builder(db_session, env_settings, tmp_path, monkeypatch):
    """Spec §0.3, structurally: a page view runs no query but the primary-key read."""
    from harness.dashboard import snapshots

    def explode(*args, **kwargs):
        raise AssertionError("a request handler called run_builder")

    monkeypatch.setattr(snapshots, "run_builder", explode)
    _row(db_session)
    client = _client(db_session, _settings(env_settings, tmp_path))
    assert client.get("/api/snap/pulse").status_code == 200
    assert client.get("/api/snap").status_code == 200


def test_the_ui_mount_serves_the_shell_with_no_cache(db_session, env_settings, tmp_path):
    client = _client(db_session, _settings(env_settings, tmp_path))
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-cache"
    assert client.get("/ui/js/app.mjs").headers["cache-control"] == "no-cache"


def test_the_legacy_routes_are_untouched(db_session, env_settings, tmp_path):
    client = _client(db_session, _settings(env_settings, tmp_path))
    assert client.get("/").status_code == 200
    assert client.get("/healthz").status_code in (200, 503)
    assert client.get("/api/summary").status_code == 200


def test_api_summary_matches_the_frozen_contract(db_session, env_settings, tmp_path):
    """Spec §0.1: the nine checked keys and the section names are frozen. This phase adds
    surfaces; it never reshapes the page other tooling reads."""
    contract = json.loads((ROOT / "tests" / "fixtures" /
                           "api_summary_contract.json").read_text())
    body = _client(db_session, _settings(env_settings, tmp_path)).get("/api/summary").json()
    assert sorted(body) == sorted(contract["top_level_keys"])
    for key in contract["checked_keys"]:
        assert key in body


def test_the_dashboard_imports_no_venue_client():
    """Conformance item 5: the dashboard has no path to a venue."""
    import harness.dashboard.app as dash

    body = Path(dash.__file__).read_text()
    assert "harness.venues" not in body
    for module in ("app", "scheduler", "queries", "window", "sentences"):
        text = (Path(dash.__file__).parent / f"{module}.py").read_text()
        assert "harness.venues" not in text


def test_every_registered_name_resolves_after_importing_the_scheduler():
    """Registration is an import side effect, so the scheduler module is the one place that has
    to import all five builder modules: without them nothing is registered in production and
    every job raises `KeyError` on its first tick, while the per-builder tests still pass
    because each imports its own module."""
    import importlib

    importlib.import_module("harness.dashboard.scheduler")
    from harness.dashboard import snapshots

    for name in ("pulse", "floor", "gate", "ticket", "study"):
        assert snapshots.builder_for(name) is not None, name
    assert snapshots.builder_for("study:2026-37") is not None


def test_the_scheduler_is_not_started_when_snapshots_are_disabled(db_session, env_settings,
                                                                 tmp_path):
    settings = _settings(env_settings, tmp_path, snapshots=False)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    app = create_dashboard(factory, settings, clock=lambda: NOW)
    with TestClient(app) as client:            # enters the lifespan
        assert client.get("/api/snap").status_code == 200
        assert getattr(app.state, "snapshot_scheduler", None) is None


def test_the_floor_cadence_follows_the_game_window(db_session, env_settings, tmp_path):
    settings = _settings(env_settings, tmp_path)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = SnapshotScheduler(factory, settings)
    assert scheduler.cadence_for("floor", db_session, NOW) == FLOOR_OUT_S
    db_session.add(Game(sport="nfl", home_team_id=1, away_team_id=2,
                        kickoff_utc=NOW - timedelta(minutes=30), status="in_progress"))
    db_session.flush()
    assert scheduler.cadence_for("floor", db_session, NOW) == FLOOR_IN_WINDOW_S


def test_the_floor_cadence_backs_off_when_the_p95_exceeds_its_budget(db_session, env_settings,
                                                                    tmp_path):
    """Ruling A-I9: the CPU budget is 2 s per minute across about eight builds, so floor's share
    is 250 ms. Over it, the in-window cadence halves its rate rather than the surface silently
    eating the budget."""
    settings = _settings(env_settings, tmp_path)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = SnapshotScheduler(factory, settings)
    db_session.add(Game(sport="nfl", home_team_id=1, away_team_id=2,
                        kickoff_utc=NOW - timedelta(minutes=30), status="in_progress"))
    for _ in range(20):
        db_session.add(MetricSample(ts=NOW - timedelta(minutes=1), source="serve",
                                    name="serve.snapshot_ms", value=400,
                                    labels={"name": "floor"}))
    db_session.flush()
    assert scheduler.cadence_for("floor", db_session, NOW) == FLOOR_BACKOFF_S


def test_the_ticket_cadence_needs_both_a_window_and_a_live_card(db_session, env_settings,
                                                                tmp_path):
    """Between cards there is nothing to update, and a card that is still `proposed` is waiting
    on a hand placement, so neither is a reason to poll every 15 seconds."""
    from harness.db.models import ParlayCard
    from harness.dashboard.scheduler import TICKET_IN_WINDOW_S, TICKET_OUT_S

    settings = _settings(env_settings, tmp_path)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = SnapshotScheduler(factory, settings)
    db_session.add(Game(sport="nfl", home_team_id=1, away_team_id=2,
                        kickoff_utc=NOW - timedelta(minutes=30), status="in_progress"))
    db_session.flush()
    assert scheduler.cadence_for("ticket", db_session, NOW) == TICKET_OUT_S

    card = ParlayCard(year=2026, week=37, sport="nfl", kind="smart", built_at=NOW,
                      stake=10, status="proposed", correlated=False)
    db_session.add(card)
    db_session.flush()
    assert scheduler.cadence_for("ticket", db_session, NOW) == TICKET_OUT_S

    card.status = "alive"
    db_session.flush()
    assert scheduler.cadence_for("ticket", db_session, NOW) == TICKET_IN_WINDOW_S


def test_every_job_is_configured_with_its_own_cadence_as_its_grace_time(env_settings, tmp_path):
    """`max_instances=1`, `coalesce=True` and `misfire_grace_time` equal to the cadence, on two
    threads. `start` is stubbed out so nothing runs: the jobs are read as pending, which is the
    only way to assert the wiring without a background thread writing to the test database.

    Floor and Ticket are registered at their *out-of-window* rate; their first run reschedules
    them. Starting slow costs one late build, starting fast spends the budget before anything
    has measured it.
    """
    from harness.dashboard.scheduler import (CADENCES, EXECUTOR_THREADS, FLOOR_OUT_S,
                                             TICKET_OUT_S)

    scheduler = SnapshotScheduler(sessionmaker(), _settings(env_settings, tmp_path))
    scheduler._scheduler.start = lambda *a, **k: None
    scheduler.start()
    try:
        jobs = {job.id: job for job in scheduler._scheduler.get_jobs()}
        assert set(jobs) == {"pulse", "floor", "gate", "ticket", "study"}
        for name, job in jobs.items():
            assert job.max_instances == 1, name
            assert job.coalesce is True, name
            assert job.misfire_grace_time == CADENCES[name], name
            assert job.trigger.interval.total_seconds() == CADENCES[name], name
        assert jobs["floor"].misfire_grace_time == FLOOR_OUT_S
        assert jobs["ticket"].misfire_grace_time == TICKET_OUT_S
        pool = scheduler._scheduler._lookup_executor("default")._pool
        assert pool._max_workers == EXECUTOR_THREADS
    finally:
        scheduler.shutdown()


def test_shutting_down_a_scheduler_that_never_started_is_a_no_op(env_settings, tmp_path):
    SnapshotScheduler(sessionmaker(), _settings(env_settings, tmp_path)).shutdown()


def test_run_once_writes_a_row_for_every_registered_name(db_session, env_settings, tmp_path):
    settings = _settings(env_settings, tmp_path)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = SnapshotScheduler(factory, settings)
    for name in ("pulse", "floor", "gate", "ticket"):
        scheduler.run_once(name, now=NOW)
    names = {row.name for row in db_session.query(DashboardSnapshot).all()}
    assert {"pulse", "floor", "gate", "ticket"} <= names


def test_the_study_job_builds_the_current_week_and_every_stale_week(db_session, env_settings,
                                                                   tmp_path):
    from harness.db.models import ReportRun

    db_session.add(ReportRun(year=2026, week=37, generated_at=NOW - timedelta(days=3),
                             provisional=False, build_sha="abc", criteria_hash="h",
                             config_hashes=[], markdown="# w37", markdown_sha256="s"))
    db_session.commit()
    settings = _settings(env_settings, tmp_path)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    SnapshotScheduler(factory, settings).run_study(now=NOW)
    names = {row.name for row in db_session.query(DashboardSnapshot).all()}
    assert "study:2026-37" in names
    assert f"study:{NOW.isocalendar().year}-{NOW.isocalendar().week}" in names
