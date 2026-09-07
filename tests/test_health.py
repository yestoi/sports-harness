from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from harness.health import compute_health, create_app
from harness.recorder.store import finish_run, start_run

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_healthz_reports_last_run(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=2))
    finish_run(db_session, run, "ok", n_requests=3, odds_remaining=4000, finished_at=NOW - timedelta(minutes=2))
    client = TestClient(create_app(factory, clock=lambda: NOW))
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["last_status"] == "ok" and body["credits_remaining"] == 4000
    assert 119 <= body["seconds_since"] <= 121


def test_healthz_stale_is_503(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=30))
    finish_run(db_session, run, "ok", finished_at=NOW - timedelta(minutes=30))
    client = TestClient(create_app(factory, clock=lambda: NOW))
    assert client.get("/healthz").status_code == 503


def test_healthz_degraded_run_is_green_but_visible(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=2))
    finish_run(db_session, run, "degraded", finished_at=NOW - timedelta(minutes=2))
    client = TestClient(create_app(factory, clock=lambda: NOW))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["last_status"] == "degraded"


def test_healthz_error_run_is_503(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=2))
    finish_run(db_session, run, "error", finished_at=NOW - timedelta(minutes=2))
    client = TestClient(create_app(factory, clock=lambda: NOW))
    r = client.get("/healthz")
    assert r.status_code == 503 and r.json()["status"] == "error"


def test_credits_low_true_below_20_percent_of_budget(db_session):
    # U1 (2026-09-07): credits_low flips on once remaining credits drop below 20% of budget.
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=2))
    finish_run(db_session, run, "ok", odds_remaining=900_000, finished_at=NOW - timedelta(minutes=2))
    body, _ = compute_health(factory, NOW, credits_budget=5_000_000)
    assert body["credits_budget"] == 5_000_000
    assert body["credits_low"] is True


def test_credits_low_false_above_20_percent_of_budget(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=2))
    finish_run(db_session, run, "ok", odds_remaining=1_200_000, finished_at=NOW - timedelta(minutes=2))
    body, _ = compute_health(factory, NOW, credits_budget=5_000_000)
    assert body["credits_budget"] == 5_000_000
    assert body["credits_low"] is False


def test_credits_low_false_when_credits_remaining_is_unknown(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=2))
    finish_run(db_session, run, "ok", finished_at=NOW - timedelta(minutes=2))  # odds_remaining defaults to None
    body, _ = compute_health(factory, NOW, credits_budget=5_000_000)
    assert body["credits_remaining"] is None
    assert body["credits_low"] is False
    assert body["credits_budget"] == 5_000_000
