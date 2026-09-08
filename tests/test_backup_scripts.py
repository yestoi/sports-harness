"""Static analysis of the three backup shell scripts (addendum §4.1, §4.2, §4.4).

These scripts run unattended on the NAS as `app-backup`, and `dump.sh`'s retention loop is the
only rule in phase 4 that deletes a file. Nothing here starts a container or touches Postgres:
the suite runs on the Mac, where docker is not available to tests (global constraints), so the
scripts are checked by grepping their source and by a `sh -n` parse.

Paths are anchored on the repository root rather than the process's working directory so the
assertions mean the same thing however pytest is invoked.
"""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOOP_PATH = ROOT / "deploy/backup/loop.sh"
DUMP_PATH = ROOT / "deploy/backup/dump.sh"
DRILL_PATH = ROOT / "deploy/backup/drill.sh"

DUMP = DUMP_PATH.read_text()
LOOP = LOOP_PATH.read_text()
DRILL = DRILL_PATH.read_text()
BULK = ["raw_responses", "orderbook_events", "venue_trades", "venue_quotes", "odds_snapshots"]


def test_every_script_is_strict_and_executable():
    for path in (LOOP_PATH, DUMP_PATH, DRILL_PATH):
        assert path.read_text().startswith("#!/bin/sh\nset -eu")
        assert os.access(path, os.X_OK)


def test_every_script_parses():
    # The scripts never run in CI, so a parse error would first surface at 03:30 on the NAS.
    for path in (LOOP_PATH, DUMP_PATH, DRILL_PATH):
        done = subprocess.run(["/bin/sh", "-n", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, f"{path.name}: {done.stderr}"


def test_dump_excludes_exactly_the_five_bulk_tables_and_their_partitions():
    for t in BULK:
        assert f"--exclude-table-data=public.{t}" in DUMP
        assert f"--exclude-table-data=public.{t}_y*" in DUMP
    # and nothing else is excluded
    excluded = set(re.findall(r"--exclude-table-data=public\.(\w+)\b", DUMP))
    # `public.<t>_y*` matches the \w+ above too, capturing "<t>_y"; fold those partition
    # patterns back onto their parent so this compares which *tables* are excluded rather
    # than how many argument forms name each one.
    excluded = {name[:-2] if name.endswith("_y") else name for name in excluded}
    assert excluded == set(BULK)


def test_dump_uses_the_custom_format_with_native_zstd():
    assert "-Fc" in DUMP and "--compress=zstd:3" in DUMP
    assert "| zstd" not in DUMP and "zstd -" not in DUMP     # B-I7: no separate binary


def test_dump_writes_a_tmp_file_and_renames_on_success():
    assert ".dump.tmp" in DUMP and "mv " in DUMP


def test_dump_writes_a_meta_sidecar_with_every_field():
    for field in ("sha256", "bytes", "started", "finished", "exit_code", "tables"):
        assert field in DUMP


def test_every_dump_kind_carries_the_thirty_percent_free_space_guard():
    # B-I10: nightly, weekly and partition all skip and journal below 30 % free.
    assert DUMP.count("MIN_FREE_PCT") >= 1 and "30" in DUMP
    assert "skip" in DUMP.lower()
    # one guard call per kind, not one guard mentioned once
    assert DUMP.count("require_free_space") >= 4


def test_retention_keeps_thirty_nightly_and_eight_weekly_units():
    assert "KEEP_NIGHTLY=30" in DUMP and "KEEP_WEEKLY=8" in DUMP


def test_retention_never_deletes_an_age_without_an_ok_marker():
    assert "${BASE}.ok" in DUMP          # A-I9, and the one marker name (M4)


def test_retention_never_deletes_a_plaintext_dump():
    body = DUMP.split("retention")[-1]
    assert ".dump.age" in body and ".meta.json" in body
    assert 'rm -f "${BASE}.dump"' not in body


def test_partitions_and_forever_have_no_retention():
    assert "partitions" in DUMP and "forever" in DUMP
    body = DUMP.split("retention")[-1]
    assert "partitions" not in body and "forever" not in body


def test_ledger_and_gate_reports_are_exported_forever_as_csv():
    assert "COPY" in DUMP and "ledger" in DUMP and "gate_reports" in DUMP
    assert "forever" in DUMP


def test_no_script_removes_a_tree():
    # The only thing any of these may delete is a file it wrote itself.
    for text in (DUMP, LOOP, DRILL):
        assert "rm -r" not in text and "rm -f -r" not in text


def test_loop_runs_nightly_at_0330_central_and_weekly_on_sunday():
    assert "America/Chicago" in LOOP and "03:30" in LOOP
    assert "dump.sh nightly" in LOOP and "dump.sh weekly" in LOOP


def test_loop_archives_partitions_on_monday_at_0400():
    assert "04:00" in LOOP and "dump.sh partition" in LOOP


def test_drill_uses_a_throwaway_container_with_an_anonymous_volume():
    # A-I10: nothing on the production cluster is created or dropped.
    assert "docker run --rm" in DRILL and "postgres:16" in DRILL
    assert "-v " not in DRILL.replace("-v /", "")     # no named or host volume
    assert "pg_restore" in DRILL


def test_drill_prints_a_row_comparison_verdict():
    assert "ROWS_MATCH" in DRILL


def test_no_script_contains_a_destructive_statement_against_the_production_database():
    for text in (DUMP, LOOP, DRILL):
        lowered = text.lower()
        for word in ("drop database", "drop table", "truncate", "delete from",
                     "createdb harness", "dropdb"):
            assert word not in lowered


def test_no_script_reads_a_file_under_secrets():
    for text in (DUMP, LOOP, DRILL):
        assert "secrets/" not in text


def test_makefile_pushes_the_backup_scripts_and_the_public_key():
    mk = (ROOT / "Makefile").read_text()
    assert mk.count("deploy/backup ") + mk.count("deploy/backup\\") >= 2   # both tar lists
    assert "backup_age.pub" in mk
    assert "backup_age_key" not in mk.replace(
        "# secrets/backup_age_key is the age *private* key and is never pushed", "")


def test_makefile_creates_the_backups_tree():
    mk = (ROOT / "Makefile").read_text()
    assert "backups/nightly" in mk and "backups/weekly" in mk
    assert "backups/partitions" in mk and "backups/forever" in mk


def test_the_deploy_recipe_never_chowns_the_backups_tree():
    # I5: the ssh runs as the NAS user, who already owns the stack directory, and the compose
    # `user:` matches it. A chown -R would fail for a non-root user and abort the deploy.
    mk = (ROOT / "Makefile").read_text()
    assert "chown" not in mk and "chgrp" not in mk


def test_the_deploy_recipe_runs_the_precheck_before_the_schema_step():
    mk = (ROOT / "Makefile").read_text()
    assert "backup-precheck" in mk
    assert "docker compose exec -T app-backup /backup/dump.sh nightly" in mk
    assert mk.index("backup-precheck") < mk.index("app-run init-db")


def test_the_deploy_recipe_starts_app_backup_with_postgres():
    mk = (ROOT / "Makefile").read_text()
    assert "up -d postgres app-backup" in mk
