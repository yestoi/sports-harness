"""`/api/snap`, its ETag contract, the anchored name pattern, the static mount's headers, the
scheduler's cadences, and the frozen `/api/summary`."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from harness.dashboard import snapshots
from harness.dashboard.app import create_dashboard
from harness.dashboard.scheduler import (FLOOR_BACKOFF_S, FLOOR_IN_WINDOW_S, FLOOR_OUT_S,
                                         SnapshotScheduler)
from harness.db.models import DashboardSnapshot, Game, MetricSample, OperatorEvent

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


@pytest.fixture(autouse=True)
def _no_disabled_builder_leaks():
    """The disabled set lives for the life of the process, which is exactly right in production
    -- only a restart of `app-serve` clears it -- and exactly wrong between two tests."""
    snapshots.reset_disabled_builders()
    yield
    snapshots.reset_disabled_builders()


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


def test_a_weak_if_none_match_answers_304(db_session, env_settings, tmp_path):
    """Ruling A-M3: a caching proxy may send a weak validator; the `W/` prefix must be stripped
    before comparing, or it never gets a 304."""
    _row(db_session)
    client = _client(db_session, _settings(env_settings, tmp_path))
    etag = client.get("/api/snap/pulse").headers["etag"]
    again = client.get("/api/snap/pulse", headers={"If-None-Match": f"W/{etag}"})
    assert again.status_code == 304 and not again.content


def test_a_comma_separated_if_none_match_answers_304(db_session, env_settings, tmp_path):
    """Ruling A-M3: a client may send a list of candidate tags; any match must 304."""
    _row(db_session)
    client = _client(db_session, _settings(env_settings, tmp_path))
    etag = client.get("/api/snap/pulse").headers["etag"]
    again = client.get("/api/snap/pulse", headers={"If-None-Match": f'"x", {etag}'})
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


def test_the_study_job_builds_the_current_week(db_session, env_settings, tmp_path):
    """The current ISO week is built on the clock whatever else is stale; the cap on the stale
    closed weeks beside it is asserted separately below."""
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


# --- fix 31: the cadences, the self-guard, the Study cap and the startup stagger -------------

def test_every_cadence_comes_from_the_builder_that_writes_it_into_its_own_payload():
    """One home per number. The scheduler imports each cadence from the builder module whose
    payload states it, and Pulse judges staleness against the same constants -- so the interval
    a job runs at, the `cadence_s` a surface reads, and the ladder a rule fires on cannot drift.

    The literals are asserted too, because `docs/runbooks/dashboard.md` and the Phase 4.5 block
    of `docs/superpowers/autopilot/verify.md` state them in prose to a person reading at 2 a.m.,
    and a doc that disagrees with the code is worse than no doc.
    """
    from harness.dashboard import scheduler as sched
    from harness.dashboard.snapshots import floor as floor_mod
    from harness.dashboard.snapshots import gate as gate_mod
    from harness.dashboard.snapshots import pulse as pulse_mod
    from harness.dashboard.snapshots import study as study_mod
    from harness.dashboard.snapshots import ticket as ticket_mod

    assert (sched.PULSE_S, sched.GATE_S, sched.STUDY_S) == (
        pulse_mod.CADENCE_S, gate_mod.CADENCE_S, study_mod.CADENCE_S)
    assert (sched.FLOOR_IN_WINDOW_S, sched.FLOOR_OUT_S) == (
        floor_mod.CADENCE_IN_WINDOW_S, floor_mod.CADENCE_OUT_S)
    assert (sched.TICKET_IN_WINDOW_S, sched.TICKET_OUT_S) == (
        ticket_mod.CADENCE_IN_WINDOW_S, ticket_mod.CADENCE_OUT_S)
    # Fix 31's numbers: out of window Pulse 60, Floor 120, Ticket 120, Gate 300, Study 600; in a
    # game window Floor and Ticket 30; the back-off is half the in-window rate, never a third
    # number to tune.
    assert sched.CADENCES == {"pulse": 60, "floor": 120, "gate": 300, "ticket": 120,
                              "study": 600}
    assert sched.FLOOR_IN_WINDOW_S == sched.TICKET_IN_WINDOW_S == 30
    assert sched.FLOOR_BACKOFF_S == sched.FLOOR_IN_WINDOW_S * 2
    assert pulse_mod.JUDGED_CADENCES == {"pulse": 60, "floor": 120, "gate": 300,
                                         "ticket": 120, "study": 600}


def test_the_first_runs_are_spread_over_two_minutes_with_pulse_before_floor(env_settings,
                                                                           tmp_path):
    """Five builders firing in the same second is the worst minute the machine sees, and it is
    the minute right after a deploy. Pulse goes first because every surface's header borrows its
    status word; Floor goes second so the expensive builder meets a cache Pulse has warmed."""
    from harness.dashboard.scheduler import STARTUP_OFFSETS, STARTUP_SPREAD_S

    assert STARTUP_OFFSETS["pulse"] == 0
    assert STARTUP_OFFSETS["floor"] > STARTUP_OFFSETS["pulse"]
    assert sorted(STARTUP_OFFSETS.values()) == sorted(set(STARTUP_OFFSETS.values()))
    assert max(STARTUP_OFFSETS.values()) == STARTUP_SPREAD_S

    scheduler = SnapshotScheduler(sessionmaker(), _settings(env_settings, tmp_path))
    scheduler._scheduler.start = lambda *a, **k: None
    scheduler.start()
    try:
        jobs = {job.id: job for job in scheduler._scheduler.get_jobs()}
        first = {name: job.next_run_time for name, job in jobs.items()}
        base = min(first.values())
        offsets = {name: round((when - base).total_seconds()) for name, when in first.items()}
        assert offsets == STARTUP_OFFSETS
    finally:
        scheduler.shutdown()


def test_a_builder_over_ten_times_its_budget_three_times_running_is_disabled(db_session,
                                                                            env_settings,
                                                                            tmp_path):
    """Two samples are a checkpoint or a backup sidecar; three in a row is a builder whose work
    no longer fits the machine. On 2026-09-10 the answer to that was a person noticing twelve
    minutes later, which is what this guard replaces."""
    from harness.dashboard.snapshots import SNAPSHOT_DISABLE_MS

    settings = _settings(env_settings, tmp_path)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = SnapshotScheduler(factory, settings)
    scheduler._scheduler.start = lambda *a, **k: None
    scheduler.start()
    try:
        scheduler._note_timing("floor", SNAPSHOT_DISABLE_MS + 1)
        scheduler._note_timing("floor", SNAPSHOT_DISABLE_MS + 1)
        assert snapshots.disabled_builders() == frozenset()

        scheduler._note_timing("floor", SNAPSHOT_DISABLE_MS + 1)
        assert snapshots.disabled_builders() == frozenset({"floor"})
        assert {job.id for job in scheduler._scheduler.get_jobs()
                if job.next_run_time is None} == {"floor"}, "the job is paused, not merely slow"
    finally:
        scheduler.shutdown()

    event = db_session.query(OperatorEvent).filter(
        OperatorEvent.kind == "snapshot_disabled").one()
    assert "floor" in event.summary and str(SNAPSHOT_DISABLE_MS) in event.summary
    assert event.ref["builder"] == "floor"
    assert event.ref["elapsed_ms"] == [SNAPSHOT_DISABLE_MS + 1] * 3
    # A disabled builder does not build, even if a job fires before the pause lands.
    assert scheduler.run_once("floor", now=NOW) == {}


def test_one_build_inside_the_budget_clears_the_run(db_session, env_settings, tmp_path):
    """The three samples must be *consecutive*. A slow build between two fast ones is the
    machine being busy, and pausing a surface over that would be its own outage."""
    from harness.dashboard.snapshots import SNAPSHOT_DISABLE_MS

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = SnapshotScheduler(factory, _settings(env_settings, tmp_path))
    for elapsed in (SNAPSHOT_DISABLE_MS + 1, SNAPSHOT_DISABLE_MS + 1, SNAPSHOT_DISABLE_MS,
                    SNAPSHOT_DISABLE_MS + 1, SNAPSHOT_DISABLE_MS + 1):
        scheduler._note_timing("floor", elapsed)
    assert snapshots.disabled_builders() == frozenset()


def test_the_guard_is_keyed_on_the_builder_so_three_study_weeks_trip_it(db_session,
                                                                       env_settings, tmp_path):
    """`study:2026-35`, `study:2026-36` and `study:2026-37` are three builds of one builder on
    one job, and it is the job that has to stop."""
    from harness.dashboard.snapshots import SNAPSHOT_DISABLE_MS

    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    scheduler = SnapshotScheduler(factory, _settings(env_settings, tmp_path))
    for week in (35, 36, 37):
        scheduler._note_timing(f"study:2026-{week}", SNAPSHOT_DISABLE_MS + 1)
    assert snapshots.disabled_builders() == frozenset({"study"})
    assert scheduler.run_study(now=NOW) == []


def test_the_study_job_builds_the_current_week_and_at_most_two_stale_ones(db_session,
                                                                         env_settings,
                                                                         tmp_path):
    """Final review M8. Unbounded, the first tick after a boot rebuilt every week with a final
    report run at once, each one a whole week of `equity_snapshots`."""
    from harness.dashboard.scheduler import STUDY_STALE_PER_TICK
    from harness.db.models import ReportRun

    for week in (30, 31, 32, 33, 34):
        db_session.add(ReportRun(year=2026, week=week, generated_at=NOW - timedelta(days=30),
                                 provisional=False, build_sha="abc", criteria_hash="h",
                                 config_hashes=[], markdown=f"# w{week}", markdown_sha256="s"))
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    names = SnapshotScheduler(factory, _settings(env_settings, tmp_path)).run_study(now=NOW)

    current = f"study:{NOW.isocalendar().year}-{NOW.isocalendar().week}"
    assert names[0] == current
    assert len(names) == 1 + STUDY_STALE_PER_TICK
    built = {row.name for row in db_session.query(DashboardSnapshot).all()}
    assert set(names) <= built
    assert len(built) == 1 + STUDY_STALE_PER_TICK
