from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from harness.dashboard.app import create_dashboard
from harness.db.models import KillSwitch
from harness.strategy.pipeline import price_and_signal
from harness.strategy.variants import load_variants, register_variants
from tests.test_pipeline import NOW, VARIANTS_DIR, _seed


def _dashboard_settings(env_settings, tmp_path, token: str | None = "supersecret", token_file_exists: bool = True):
    token_file = tmp_path / "dashboard_token"
    if token_file_exists:
        token_file.write_text(token or "")
    return env_settings.model_copy(update={"dashboard_token_file": token_file})


def _seed_full(db_session, env_settings):
    """Seed a run, markets, gap snapshots, registered ("tiny", tier=primary) variant, and signals."""
    game, run, markets = _seed(db_session)
    variants = load_variants(VARIANTS_DIR)
    register_variants(db_session, variants, NOW, prune=True)
    price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    return game, run, markets


def _client(db_session, settings, clock=lambda: NOW):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    app = create_dashboard(factory, settings, clock=clock)
    return TestClient(app)


def test_index_renders_seeded_run_and_signal(db_session, env_settings, tmp_path):
    game, run, markets = _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert str(run.id) in body
    # A signal for the primary ("tiny") variant should show up in the signals table.
    assert "tiny" in body


def test_api_summary_has_funnel_and_health_keys(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.get("/api/summary")
    assert r.status_code == 200
    body = r.json()
    assert "health" in body
    assert "funnel" in body
    assert "match_report" in body
    assert "signals" in body
    assert "unmatched_markets" in body
    assert "websocket" in body
    assert "data_quality" in body
    assert body["health"]["last_status"] == "running"  # run never finished in this fixture
    assert "sources" in body["funnel"]
    assert "signals_by_variant" in body["funnel"]
    assert len(body["signals"]) >= 1


def test_kill_flips_state_and_page_reflects_it(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    r = client.post("/kill", data={"reason": "manual pause for testing"})
    assert r.status_code == 200

    row = db_session.get(KillSwitch, 1)
    assert row is not None
    assert row.active is True
    assert row.reason == "manual pause for testing"

    page = client.get("/")
    assert "manual pause for testing" in page.text
    assert "ACTIVE" in page.text.upper()


def test_unkill_without_token_is_403(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)
    client.post("/kill", data={"reason": "x"})

    r = client.post("/unkill")
    assert r.status_code == 403

    row = db_session.get(KillSwitch, 1)
    assert row.active is True


def test_unkill_with_wrong_token_is_403(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)
    client.post("/kill", data={"reason": "x"})

    r = client.post("/unkill", headers={"X-Dashboard-Token": "wrong-token"})
    assert r.status_code == 403

    row = db_session.get(KillSwitch, 1)
    assert row.active is True


def test_unkill_with_right_token_is_200_and_inactive(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)
    client.post("/kill", data={"reason": "x"})

    r = client.post("/unkill", headers={"X-Dashboard-Token": "supersecret"})
    assert r.status_code == 200

    db_session.expire_all()
    row = db_session.get(KillSwitch, 1)
    assert row.active is False


def test_unkill_missing_token_file_is_403(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path, token_file_exists=False)
    client = _client(db_session, settings)
    client.post("/kill", data={"reason": "x"})

    r = client.post("/unkill", headers={"X-Dashboard-Token": "anything"})
    assert r.status_code == 403

    row = db_session.get(KillSwitch, 1)
    assert row.active is True


def test_healthz_keys_unchanged(db_session, env_settings, tmp_path):
    from harness.recorder.store import finish_run, start_run

    now = NOW
    run = start_run(db_session, now - timedelta(minutes=2))
    finish_run(db_session, run, "ok", n_requests=3, odds_remaining=4000, finished_at=now - timedelta(minutes=2))

    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings, clock=lambda: now)

    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"status", "last_run_at", "last_status", "seconds_since", "credits_remaining"}
    assert body["status"] == "ok" and body["last_status"] == "ok" and body["credits_remaining"] == 4000
