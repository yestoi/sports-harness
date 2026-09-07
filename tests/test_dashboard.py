from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from harness.dashboard.app import create_dashboard
from harness.db.models import KillSwitch, StrategyVariant
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


def test_page_and_summary_survive_two_active_primary_variants(db_session, env_settings, tmp_path):
    """Two active primary rows should never happen (load_variants enforces at most one at
    load time), but a hand-edited row or a registration race could still produce it. The
    dashboard must degrade to a deterministic choice instead of 500ing."""
    _seed_full(db_session, env_settings)
    duplicate_primary = next(v for v in load_variants(VARIANTS_DIR) if v.tier == "primary")
    db_session.add(StrategyVariant(
        variant_id="dup000000001",
        name="tiny_dup_primary",
        tier="primary",
        config_json=duplicate_primary.config,
        registered_at=NOW,
        active=True,
    ))
    db_session.commit()

    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    page = client.get("/")
    assert page.status_code == 200

    summary = client.get("/api/summary")
    assert summary.status_code == 200


def test_build_stamp_renders_from_settings(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path).model_copy(
        update={"build_sha": "abc1234", "build_time": "2026-09-07T06:00:00Z"}
    )
    client = _client(db_session, settings)

    page = client.get("/")
    assert page.status_code == 200
    assert "build abc1234" in page.text
    assert "deployed 2026-09-07T06:00:00Z" in page.text

    summary = client.get("/api/summary").json()
    assert summary["build"]["sha"] == "abc1234"

    healthz = client.get("/healthz").json()
    assert healthz["build"] == "abc1234"


def test_build_stamp_defaults_to_dev(db_session, env_settings, tmp_path):
    _seed_full(db_session, env_settings)
    settings = _dashboard_settings(env_settings, tmp_path)
    client = _client(db_session, settings)

    page = client.get("/")
    assert page.status_code == 200
    assert "build dev" in page.text
    assert "deployed" not in page.text

    healthz = client.get("/healthz").json()
    assert healthz["build"] == "dev"


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
    # U1 (2026-09-07): compute_health now also reports credits_budget and credits_low.
    assert set(body.keys()) == {"status", "last_run_at", "last_status", "seconds_since", "credits_remaining",
                                 "credits_budget", "credits_low", "build"}
    assert body["status"] == "ok" and body["last_status"] == "ok" and body["credits_remaining"] == 4000
    assert body["build"] == "dev"


def test_a_failing_section_does_not_take_the_page_down(monkeypatch, db_session, env_settings, tmp_path):
    from sqlalchemy.exc import OperationalError

    import harness.dashboard.app as dash

    _seed_full(db_session, env_settings)

    def boom(session, now):
        raise OperationalError("select 1", {}, Exception("canceling statement due to statement timeout"))

    monkeypatch.setattr(dash, "_websocket", boom)
    monkeypatch.setattr(dash, "_data_quality", boom)
    client = _client(db_session, _dashboard_settings(env_settings, tmp_path))

    page = client.get("/")
    assert page.status_code == 200
    assert "unavailable: OperationalError" in page.text
    summary = client.get("/api/summary").json()
    assert summary["websocket"] == {"error": "OperationalError"}
    assert summary["data_quality"] == {"error": "OperationalError"}
    assert "error" not in summary["funnel"]
    assert "tiny" in page.text  # the other sections still rendered after the rollback
