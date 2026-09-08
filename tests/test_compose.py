"""Roadmap Carried fixes item 9 (finding F19 tuning / R17): the NAS Postgres container ran on
image defaults sized for a laptop, not a box ingesting up to 4M orderbook rows an hour. Measured
2026-09-07: 189 timed checkpoints in 16 h, `buffers_backend` 2.0M against `buffers_checkpoint`
272k, i.e. Postgres is flushing dirty pages itself far more than at checkpoints, which is what
undersized `shared_buffers`/`max_wal_size`/`checkpoint_timeout` produce. This test pins the tuned
`command` and `shm_size` for the `postgres` service so a future edit cannot silently drop them."""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).parent.parent / "docker-compose.yml"

EXPECTED_SETTINGS = {
    "shared_buffers": "512MB",
    "effective_cache_size": "1536MB",
    "maintenance_work_mem": "256MB",
    "work_mem": "16MB",
    "max_wal_size": "4GB",
    "min_wal_size": "1GB",
    "checkpoint_timeout": "15min",
    "autovacuum_vacuum_cost_limit": "1000",
    "random_page_cost": "1.1",
}


def _postgres_service() -> dict:
    doc = yaml.safe_load(COMPOSE.read_text())
    return doc["services"]["postgres"]


def _service(name: str) -> dict:
    doc = yaml.safe_load(COMPOSE.read_text())
    return doc["services"][name]


def test_postgres_command_starts_with_postgres():
    command = _postgres_service()["command"]
    assert command[0] == "postgres"


def test_postgres_command_sets_all_tuned_parameters():
    command = _postgres_service()["command"]
    # command is ["postgres", "-c", "k=v", "-c", "k=v", ...]; pull out the "k=v" tokens that
    # follow a "-c" flag and turn them into a dict for order-independent comparison.
    settings = {}
    for flag, setting in zip(command, command[1:]):
        if flag == "-c":
            key, _, value = setting.partition("=")
            settings[key] = value
    for key, value in EXPECTED_SETTINGS.items():
        assert key in settings, f"missing -c {key} in postgres command"
        assert settings[key] == value, f"{key}={settings[key]!r}, expected {value!r}"


def test_postgres_shm_size_raised_for_larger_shared_buffers():
    assert _postgres_service()["shm_size"] == "512m"


def test_postgres_healthcheck_unchanged():
    healthcheck = _postgres_service()["healthcheck"]
    assert healthcheck["test"] == ["CMD-SHELL", "pg_isready -U harness -d harness"]
    assert healthcheck["interval"] == "10s"
    assert healthcheck["timeout"] == "5s"
    assert healthcheck["retries"] == 10


def test_postgres_volume_unchanged():
    assert _postgres_service()["volumes"] == ["./pgdata:/var/lib/postgresql/data"]


def test_postgres_image_and_restart_unchanged():
    service = _postgres_service()
    assert service["image"] == "postgres:16"
    assert service["restart"] == "unless-stopped"


def test_postgres_environment_unchanged():
    assert _postgres_service()["environment"] == {
        "POSTGRES_USER": "harness",
        "POSTGRES_PASSWORD": "harness",
        "POSTGRES_DB": "harness",
    }


# --- Task 12b: the read-only Postgres data mount for host.disk_free_gb -------------------


def test_compose_app_run_pgdata_bind_is_read_only():
    volumes = _service("app-run")["volumes"]
    pgdata_binds = [v for v in volumes if v.startswith("./pgdata:")]
    assert pgdata_binds == ["./pgdata:/pgdata-ro:ro"]


def test_compose_app_run_keeps_its_other_bind_and_env():
    service = _service("app-run")
    assert "./secrets/odds_api_key:/run/secrets/odds_api_key:ro" in service["volumes"]
    # Task 14 adds the two KALSHI_*_FILE entries app-ws already carries (the limits read), so
    # this pins the odds key entry rather than the whole mapping; the Kalshi pair has its own
    # test below and test_compose_app_exec_has_no_volumes_and_no_kalshi_env still holds the
    # executor to none of it.
    assert service["environment"]["ODDS_API_KEY_FILE"] == "/run/secrets/odds_api_key"


def test_compose_app_exec_has_no_volumes_and_no_kalshi_env():
    service = _service("app-exec")
    assert "volumes" not in service
    env = service.get("environment") or {}
    assert not any(k.startswith("KALSHI_") for k in env)


def test_compose_no_other_service_mounts_pgdata_ro():
    doc = yaml.safe_load(COMPOSE.read_text())
    for name, service in doc["services"].items():
        if name == "app-run":
            continue
        volumes = service.get("volumes") or []
        assert not any("/pgdata-ro" in v for v in volumes), name


# --- Task 14: the app-backup dump sidecar, and app-run's backup + limits mounts ----------


def test_app_backup_service_exists_on_the_postgres_image():
    s = _service("app-backup")
    assert s["image"] == "postgres:16" and "build" not in s
    assert s["command"] == ["/backup/loop.sh"]
    assert s["restart"] == "unless-stopped"


def test_app_backup_runs_as_the_app_uid():
    assert _service("app-backup")["user"] == "${APP_UID:-65534}:${APP_GID:-65534}"


def test_app_backup_mounts_the_scripts_read_only_and_the_backups_read_write():
    volumes = _service("app-backup")["volumes"]
    assert "./deploy/backup:/backup:ro" in volumes
    assert "./backups:/backups:rw" in volumes


def test_app_backup_has_no_secrets_mount():
    for v in _service("app-backup")["volumes"]:
        assert "secrets" not in v


def test_app_backup_uses_pg_environment_not_the_sqlalchemy_url():
    env = _service("app-backup")["environment"]
    assert env["PGHOST"] == "postgres" and env["PGDATABASE"] == "harness"
    assert not any("postgresql+psycopg" in str(v) for v in env.values())


def test_app_run_mounts_backups_and_the_recipient():
    volumes = _service("app-run")["volumes"]
    assert "./backups:/backups:rw" in volumes
    assert "./deploy/backup_age.pub:/run/backup_age.pub:ro" in volumes


def test_app_run_mounts_the_production_kalshi_keys_for_the_limits_read():
    # C2: without these, has_kalshi_credentials() is False in the recorder container, nothing
    # ever writes a venue_requests row in production, and the paper-posture tripwire is vacuous.
    svc = _service("app-run")
    assert "./secrets/kalshi_key_id:/run/secrets/kalshi_key_id:ro" in svc["volumes"]
    assert ("./secrets/kalshi_private_key.pem:/run/secrets/kalshi_private_key.pem:ro"
            in svc["volumes"])
    assert svc["environment"]["KALSHI_KEY_ID_FILE"] == "/run/secrets/kalshi_key_id"
    assert (svc["environment"]["KALSHI_PRIVATE_KEY_FILE"]
            == "/run/secrets/kalshi_private_key.pem")


def test_no_service_mounts_the_demo_secrets():
    # C3: the demo pair reaches a container only inside the controller's own
    # `docker compose run --rm -v ...`, never as a standing mount.
    doc = yaml.safe_load(COMPOSE.read_text())
    for name, svc in doc["services"].items():
        for v in svc.get("volumes", []) or []:
            assert "kalshi_demo" not in v, name


def test_compose_app_exec_block_unchanged():
    # Conformance item 5: the executor has no volumes, no credentials, no backups mount.
    s = _service("app-exec")
    assert "volumes" not in s
    assert "environment" not in s
    assert s["command"] == ["exec"]


def test_no_service_mounts_the_age_private_key():
    doc = yaml.safe_load(COMPOSE.read_text())
    for name, svc in doc["services"].items():
        for v in svc.get("volumes", []) or []:
            assert "backup_age_key" not in v, name
