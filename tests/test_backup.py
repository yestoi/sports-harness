"""Backup keygen, streaming age encryption, drill records, and the plaintext release rule
(addendum §4.3, ruling A-C4, ruling A-I9)."""

import hashlib
import io
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from harness.cli import app
from harness.config.settings import get_settings
from harness.db.models import BackupRun
from harness.ops.agefmt import encrypt
from harness.ops.backup import (
    decrypt_file,
    delete_verified_plaintexts,
    encrypt_pending,
    keygen,
    marker_path,
    newest_nightly_ok,
    record_drill,
    scan_units,
    unencrypted_unit_count,
    verify_ciphertext_structure,
)
from harness.scheduler import build_scheduler

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

runner = CliRunner()


@pytest.fixture
def cli_runner():
    return runner


@pytest.fixture
def cli_settings(monkeypatch, db_session):
    """Point `harness.cli`'s own engine at the same Postgres database `db_session` uses (same
    convention as tests/test_gate.py, tests/test_report.py)."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _unit(tmp_path, kind, stamp, payload=b"x" * 1000, meta=True):
    """A retention unit's plaintext (and, unless `meta=False`, its sidecar) under `tmp_path`,
    matching the `<kind>/harness-<kind>-<stamp>.<ext>` naming `scan_units` groups on."""
    kind_dir = tmp_path / kind
    kind_dir.mkdir(exist_ok=True)
    (kind_dir / f"harness-{kind}-{stamp}.dump").write_bytes(payload)
    if meta:
        (kind_dir / f"harness-{kind}-{stamp}.meta.json").write_text("{}")


def _recipient(tmp_path):
    """A fresh age recipient's public key file, off to the side of any unit directories."""
    ident = tmp_path / "_test_identity"
    pub = tmp_path / "_test_recipient.pub"
    keygen(ident, pub)
    return pub


def _backup_run(session, kind, status, finished_at, build_sha=None, notes=None, **kwargs):
    row = BackupRun(kind=kind, status=status, build_sha=build_sha, notes=notes,
                    started_at=finished_at, finished_at=finished_at, **kwargs)
    session.add(row)
    session.commit()
    return row


def _recorder():
    return SimpleNamespace(maybe_tick=lambda: None)


# --- keygen -----------------------------------------------------------------------------------


def test_keygen_writes_a_0600_identity_and_a_public_recipient(tmp_path):
    ident, pub = tmp_path / "key", tmp_path / "pub"
    recipient = keygen(ident, pub)
    assert oct(ident.stat().st_mode)[-3:] == "600"
    assert ident.read_text().startswith("AGE-SECRET-KEY-1")
    assert pub.read_text().strip() == recipient and recipient.startswith("age1")


def test_keygen_refuses_to_overwrite_an_existing_identity(tmp_path):
    ident, pub = tmp_path / "key", tmp_path / "pub"
    keygen(ident, pub)
    with pytest.raises(FileExistsError):
        keygen(ident, pub)


# --- encrypt_pending ----------------------------------------------------------------------------


def test_encrypt_pending_encrypts_a_unit_and_records_an_ok_row(db_session, tmp_path):
    _unit(tmp_path, "nightly", "20260908T083000Z", payload=b"z" * 200000)
    pub = _recipient(tmp_path)
    rows = encrypt_pending(db_session, tmp_path, pub, "abc1234", NOW)
    assert len(rows) == 1 and rows[0].status == "ok" and rows[0].kind == "encrypt"
    assert rows[0].plaintext_sha256 and rows[0].ciphertext_sha256
    assert rows[0].bytes == 200000
    assert (tmp_path / "nightly" / "harness-nightly-20260908T083000Z.dump.age").exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_encrypt_pending_is_idempotent(db_session, tmp_path):
    _unit(tmp_path, "nightly", "S1")
    pub = _recipient(tmp_path)
    encrypt_pending(db_session, tmp_path, pub, "abc1234", NOW)
    assert encrypt_pending(db_session, tmp_path, pub, "abc1234", NOW) == []


def test_encrypt_pending_skips_a_unit_without_a_meta_sidecar(db_session, tmp_path):
    _unit(tmp_path, "nightly", "S1", meta=False)
    assert encrypt_pending(db_session, tmp_path, _recipient(tmp_path), "abc", NOW) == []


def test_without_a_recipient_it_records_skipped_and_touches_nothing(db_session, tmp_path):
    _unit(tmp_path, "nightly", "S1")
    rows = encrypt_pending(db_session, tmp_path, tmp_path / "absent.pub", "abc", NOW)
    assert len(rows) == 1 and rows[0].status == "skipped"
    assert rows[0].notes["reason"] == "no recipient"
    assert not list(tmp_path.rglob("*.age"))
    assert (tmp_path / "nightly" / "harness-nightly-S1.dump").exists()


def test_a_recipient_path_that_is_a_directory_is_also_no_recipient(db_session, tmp_path):
    # I6: Compose creates an empty directory for a missing bind source. exists() is True;
    # is_file() is what the job tests, so this must skip rather than error.
    _unit(tmp_path, "nightly", "S1")
    (tmp_path / "pub_dir").mkdir()
    rows = encrypt_pending(db_session, tmp_path, tmp_path / "pub_dir", "abc", NOW)
    assert len(rows) == 1 and rows[0].status == "skipped"
    assert rows[0].notes["reason"] == "no recipient"
    assert not list(tmp_path.rglob("*.age"))


def test_encrypt_writes_the_ok_marker_beside_the_ciphertext(db_session, tmp_path):
    _unit(tmp_path, "nightly", "S1")
    encrypt_pending(db_session, tmp_path, _recipient(tmp_path), "abc1234", NOW)
    assert (tmp_path / "nightly" / "harness-nightly-S1.ok").exists()


def test_the_ciphertext_round_trips_through_the_private_key(db_session, tmp_path):
    ident, pub = tmp_path / "key", tmp_path / "pub"
    keygen(ident, pub)
    payload = b"payload" * 20000
    _unit(tmp_path, "nightly", "S1", payload=payload)
    encrypt_pending(db_session, tmp_path, pub, "abc", NOW)
    out = tmp_path / "out.bin"
    sha = decrypt_file(tmp_path / "nightly" / "harness-nightly-S1.dump.age", out, ident)
    assert out.read_bytes() == payload
    assert sha == hashlib.sha256(payload).hexdigest()


def test_ciphertext_structure_check_catches_a_truncated_file(tmp_path):
    ident, pub = tmp_path / "key", tmp_path / "pub"
    recipient = keygen(ident, pub)
    payload = b"z" * 200000
    age_path = tmp_path / "out.dump.age"
    with io.BytesIO(payload) as src, age_path.open("wb") as dst:
        encrypt(src, dst, recipient)

    data = age_path.read_bytes()
    age_path.write_bytes(data[:-100])
    assert verify_ciphertext_structure(age_path, plaintext_bytes=len(payload)) is False


# --- the plaintext deletion rule (A-C4) ----------------------------------------------------------


def test_a_plaintext_survives_without_a_drill_row(db_session, tmp_path):
    _unit(tmp_path, "nightly", "S1")
    encrypt_pending(db_session, tmp_path, _recipient(tmp_path), "abc1234", NOW)
    assert delete_verified_plaintexts(db_session, tmp_path, "abc1234", NOW) == []
    assert (tmp_path / "nightly" / "harness-nightly-S1.dump").exists()


def test_a_drill_row_for_another_build_does_not_release_the_plaintext(db_session, tmp_path):
    _unit(tmp_path, "nightly", "S1")
    encrypt_pending(db_session, tmp_path, _recipient(tmp_path), "abc1234", NOW)
    record_drill(db_session, "OTHER99", True, "sha", True, NOW, {})
    assert delete_verified_plaintexts(db_session, tmp_path, "abc1234", NOW) == []


def test_a_matching_drill_row_releases_only_the_plaintext(db_session, tmp_path):
    _unit(tmp_path, "nightly", "S1")
    encrypt_pending(db_session, tmp_path, _recipient(tmp_path), "abc1234", NOW)
    record_drill(db_session, "abc1234", True, "sha", True, NOW, {})
    deleted = delete_verified_plaintexts(db_session, tmp_path, "abc1234", NOW)
    assert len(deleted) == 1
    assert not (tmp_path / "nightly" / "harness-nightly-S1.dump").exists()
    assert (tmp_path / "nightly" / "harness-nightly-S1.dump.age").exists()
    assert (tmp_path / "nightly" / "harness-nightly-S1.meta.json").exists()
    assert (tmp_path / "nightly" / "harness-nightly-S1.ok").exists()


def test_a_failed_drill_never_releases_a_plaintext(db_session, tmp_path):
    _unit(tmp_path, "nightly", "S1")
    encrypt_pending(db_session, tmp_path, _recipient(tmp_path), "abc1234", NOW)
    record_drill(db_session, "abc1234", False, "sha", False, NOW, {})
    assert delete_verified_plaintexts(db_session, tmp_path, "abc1234", NOW) == []


# --- units and the precheck ------------------------------------------------------------------------


def test_scan_units_groups_the_three_files_by_stamp(tmp_path):
    _unit(tmp_path, "nightly", "S1")
    _unit(tmp_path, "weekly", "S2")
    units = {u.stamp: u for u in scan_units(tmp_path)}
    assert set(units) == {"S1", "S2"}
    assert units["S1"].kind == "nightly" and units["S1"].meta is not None


def test_marker_path_is_named_after_the_unit_beside_the_ciphertext(tmp_path):
    _unit(tmp_path, "nightly", "S1")
    unit = next(u for u in scan_units(tmp_path) if u.stamp == "S1")
    assert marker_path(unit) == tmp_path / "nightly" / "harness-nightly-S1.ok"


def test_unencrypted_unit_count(tmp_path, db_session):
    _unit(tmp_path, "nightly", "S1")
    _unit(tmp_path, "nightly", "S2")
    encrypt_pending(db_session, tmp_path, _recipient(tmp_path), "abc", NOW)
    assert unencrypted_unit_count(tmp_path) == 0


def test_newest_nightly_ok_respects_the_26_hour_bound(db_session):
    _backup_run(db_session, kind="nightly", status="ok",
                finished_at=NOW - timedelta(hours=27))
    assert newest_nightly_ok(db_session, NOW, 26) is None
    _backup_run(db_session, kind="nightly", status="ok",
                finished_at=NOW - timedelta(hours=2))
    assert newest_nightly_ok(db_session, NOW, 26) is not None


def test_newest_nightly_ok_ignores_an_error_row(db_session):
    _backup_run(db_session, kind="nightly", status="error", finished_at=NOW)
    assert newest_nightly_ok(db_session, NOW, 26) is None


# --- the scheduler job and the CLI -----------------------------------------------------------------


def test_the_encrypt_job_is_registered_every_ten_minutes(env_settings):
    # The guard is `if backup_encrypt is not None and backup_period_s`, so the callable is
    # required: passing only the period registers nothing.
    sched = build_scheduler(_recorder(), 30, backup_encrypt=lambda: None,
                            backup_period_s=env_settings.backup_encrypt_period_s)
    job = sched.get_job("backup_encrypt")
    assert job is not None and job.trigger.interval.total_seconds() == 600


def test_no_encrypt_job_without_a_callable_or_a_period(env_settings):
    assert build_scheduler(_recorder(), 30).get_job("backup_encrypt") is None
    assert build_scheduler(_recorder(), 30,
                           backup_encrypt=lambda: None,
                           backup_period_s=0).get_job("backup_encrypt") is None


def test_backup_precheck_exits_1_without_a_fresh_nightly(cli_runner, cli_settings):
    assert cli_runner.invoke(app, ["backup-precheck"]).exit_code == 1


def test_backup_precheck_exits_0_with_a_fresh_nightly(cli_runner, cli_settings, db_session):
    _backup_run(db_session, kind="nightly", status="ok",
                finished_at=datetime.now(timezone.utc) - timedelta(hours=1))
    result = cli_runner.invoke(app, ["backup-precheck"])
    assert result.exit_code == 0, result.output


def test_backup_keygen_prints_the_copy_out_instruction(cli_runner, tmp_path, monkeypatch):
    # Never writes into the working tree: the CLI reads both paths from Settings, and this
    # test points them at tmp_path. A real run here would drop secrets/backup_age_key into the
    # checkout and keygen's refuse-to-overwrite rule would then break the next run.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("BACKUP_IDENTITY_FILE", str(tmp_path / "key"))
    monkeypatch.setenv("BACKUP_RECIPIENT_FILE", str(tmp_path / "pub"))
    get_settings.cache_clear()
    result = cli_runner.invoke(app, ["backup-keygen"])
    assert result.exit_code == 0, result.output
    assert "copy" in result.output.lower() and "backup_age_key" in result.output
    assert "AGE-SECRET-KEY" not in result.output      # never print the private key
    from pathlib import Path

    assert not Path("secrets/backup_age_key").exists()
    assert not Path("deploy/backup_age.pub").exists()
    get_settings.cache_clear()


def test_backup_keygen_refuses_when_backup_dir_exists(cli_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))     # a real backup_dir means this is the NAS
    monkeypatch.setenv("BACKUP_IDENTITY_FILE", str(tmp_path / "key"))
    monkeypatch.setenv("BACKUP_RECIPIENT_FILE", str(tmp_path / "pub"))
    get_settings.cache_clear()
    result = cli_runner.invoke(app, ["backup-keygen"])
    assert result.exit_code == 1
    assert not (tmp_path / "key").exists()
    get_settings.cache_clear()


def test_backup_drill_record_cli_writes_a_drill_row(cli_runner, cli_settings, db_session):
    result = cli_runner.invoke(app, [
        "backup-drill-record", "--build-sha", "abc1234", "--decrypt-ok",
        "--plaintext-sha256", "deadbeef", "--rows-match",
    ])
    assert result.exit_code == 0, result.output
    row = db_session.query(BackupRun).filter_by(kind="drill").one()
    assert row.build_sha == "abc1234" and row.status == "ok"
    assert row.notes["decrypt_ok"] is True and row.rows_match is True


def test_backup_encrypt_cli_reports_a_row_count(cli_runner, cli_settings, tmp_path, monkeypatch):
    _unit(tmp_path, "nightly", "S1")
    pub = _recipient(tmp_path)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setenv("BACKUP_RECIPIENT_FILE", str(pub))
    get_settings.cache_clear()
    result = cli_runner.invoke(app, ["backup-encrypt"])
    assert result.exit_code == 0, result.output
    assert "rows=1" in result.output
    get_settings.cache_clear()


def test_backup_decrypt_cli_prints_the_sha256(cli_runner, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    ident = tmp_path / "key"
    pub = tmp_path / "pub"
    keygen(ident, pub)
    payload = b"payload" * 20000
    with io.BytesIO(payload) as src, (tmp_path / "out.dump.age").open("wb") as dst:
        encrypt(src, dst, pub.read_text().strip())
    monkeypatch.setenv("BACKUP_IDENTITY_FILE", str(ident))
    get_settings.cache_clear()

    result = cli_runner.invoke(app, [
        "backup-decrypt", str(tmp_path / "out.dump.age"), "--out", str(tmp_path / "plain.bin"),
    ])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / "plain.bin").read_bytes() == payload
    get_settings.cache_clear()


def test_backup_decrypt_cli_exits_1_on_an_unparseable_identity(cli_runner, tmp_path, monkeypatch):
    # decrypt() raises a bare ValueError for a malformed identity string (Task 12 fact b), not
    # AgeError -- the CLI must catch both. The ciphertext itself is a real, validly headed age
    # file, so the failure is unambiguously in identity parsing, not header parsing.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    recipient = keygen(tmp_path / "real_key", tmp_path / "real_pub")
    with io.BytesIO(b"hello world") as src, (tmp_path / "out.dump.age").open("wb") as dst:
        encrypt(src, dst, recipient)

    bad_ident = tmp_path / "bad_key"
    bad_ident.write_text("not-a-real-identity")
    monkeypatch.setenv("BACKUP_IDENTITY_FILE", str(bad_ident))
    get_settings.cache_clear()

    result = cli_runner.invoke(app, [
        "backup-decrypt", str(tmp_path / "out.dump.age"), "--out", str(tmp_path / "plain.bin"),
    ])
    assert result.exit_code == 1
    get_settings.cache_clear()
