"""Static analysis of the three backup shell scripts (addendum §4.1, §4.2, §4.4).

These scripts run unattended on the NAS as `app-backup`, and `dump.sh`'s retention loop is the
only rule in phase 4 that deletes a file. Nothing here starts a container or touches Postgres:
the suite runs on the Mac, where docker is not available to tests (global constraints), so the
scripts are checked by grepping their source and by a `sh -n` parse.

Paths are anchored on the repository root rather than the process's working directory so the
assertions mean the same thing however pytest is invoked.
"""

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

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


def test_ledger_and_gate_reports_are_exported_forever_as_an_encrypted_unit():
    # Fix round 1 item 3. These were plaintext CSVs, which no part of the encrypt pipeline ever
    # saw: scan_units groups only .dump/.dump.age/.meta.json, so a .csv was never a unit member
    # and sat readable on the share. They are now an ordinary unit of the same shape as a
    # nightly, so backup-encrypt encrypts them and the plaintext release rule applies.
    assert "run_dump forever forever" in DUMP
    assert "-t public.ledger" in DUMP and "-t public.gate_reports" in DUMP
    assert "forever" in DUMP


def test_no_plaintext_csv_is_written_anywhere():
    for line in DUMP.splitlines():
        if line.lstrip().startswith("#"):
            continue          # the header documents how to get CSV back on demand
        assert ".csv" not in line, line


def test_retention_is_called_for_the_two_pruned_kinds_and_nothing_else():
    # forever/ and partitions/ are kept for good, so they must never reach prune_units.
    assert re.findall(r"^\s*prune_units (\S+)", DUMP, re.M) == ["nightly", "weekly"]


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


def test_the_deploy_recipe_bootstraps_the_schema_before_the_fallback_dump():
    # C1: on the very first phase 4 deploy `backup_runs` does not exist yet, so the first
    # `backup-precheck` always fails. The fallback branch must create the schema (`init-db`)
    # before taking the dump and asking again, or the fallback dump's own `record_run` insert
    # fails the same way and the deploy aborts even though nothing is actually broken.
    mk = (ROOT / "Makefile").read_text()
    recipe = mk.split("deploy-nas:")[1].split("\ndeploy-nas-app:")[0]
    first_precheck = recipe.index("app-run backup-precheck")
    fallback_init_db = recipe.index("app-run init-db", first_precheck)
    fallback_dump = recipe.index("/backup/dump.sh nightly", first_precheck)
    assert fallback_init_db < fallback_dump


def test_the_deploy_recipe_asks_the_precheck_again_after_the_fallback_dump():
    # Fix round 1 item 1: dump.sh exits 0 when it *skips* (low disk, or the lock is held), so
    # the fallback's own exit status is not evidence of a fresh dump. The recipe must ask the
    # precheck again and abort when the answer is still no.
    mk = (ROOT / "Makefile").read_text()
    recipe = mk.split("deploy-nas:")[1].split("\ndeploy-nas-app:")[0]
    assert recipe.count("app-run backup-precheck") == 2   # invocations, not the comment
    assert "ABORT" in recipe and "exit 1" in recipe
    fallback = recipe.index("/backup/dump.sh nightly")
    assert recipe.index("app-run backup-precheck", fallback) > fallback


def test_the_deploy_recipe_starts_app_backup_with_postgres():
    mk = (ROOT / "Makefile").read_text()
    assert "up -d postgres app-backup" in mk


def test_every_init_db_in_both_deploy_recipes_is_preceded_by_stopping_the_app_writers():
    # Fix 21: `add column if not exists` needs an AccessExclusive lock that a running executor
    # loop blocks, so every schema step must stop app-exec and app-run first.
    mk = (ROOT / "Makefile").read_text()
    deploy_nas = mk.split("deploy-nas:")[1].split("\ndeploy-nas-app:")[0]
    deploy_nas_app = mk.split("\ndeploy-nas-app:")[1].split("\nstatus-nas:")[0]
    for name, recipe in [("deploy-nas", deploy_nas), ("deploy-nas-app", deploy_nas_app)]:
        init_db_count = 0
        for line in recipe.splitlines():
            if "app-run init-db" in line:
                init_db_count += 1
                assert "docker compose stop app-exec app-run &&" in line, (
                    f"{name}: init-db not preceded by stopping the writers on: {line}"
                )
        assert init_db_count > 0, name
        assert recipe.count("docker compose stop app-exec app-run &&") == init_db_count, name
    assert mk.count("app-run init-db") == 3
    assert mk.count("docker compose stop app-exec app-run &&") == 3


def test_the_precheck_runs_before_the_writers_are_stopped():
    mk = (ROOT / "Makefile").read_text()
    recipe = mk.split("deploy-nas:")[1].split("\ndeploy-nas-app:")[0]
    assert recipe.index("backup-precheck") < recipe.index("docker compose stop")


# --- the deletion rule, executed ------------------------------------------------------------
# dump.sh guards its own `main "$@"`, so bash can source it and call prune_units directly. This
# is the only file-deleting code in the phase; a substring grep is not a regression net for it.
# Nothing here starts a container or opens a database: prune_units reads the filesystem only.


def _unit(directory: Path, kind: str, stamp: str, *, dump=False, age=True, meta=True, ok=True):
    base = directory / f"harness-{kind}-{stamp}"
    if dump:
        base.with_name(base.name + ".dump").write_text("plaintext")
    if age:
        base.with_name(base.name + ".dump.age").write_text("ciphertext")
    if meta:
        base.with_name(base.name + ".meta.json").write_text("{}")
    if ok:
        base.with_name(base.name + ".ok").write_text("")
    return base


def _prune(root: Path, kind: str, keep: int) -> str:
    script = (f"BACKUPS_DIR={shlex.quote(str(root))}\n"
              f". {shlex.quote(str(DUMP_PATH))}\n"
              f"prune_units {kind} {keep}\n")
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture()
def nightly_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "nightly"
    directory.mkdir()
    return directory


def test_sourcing_dump_sh_does_not_run_a_dump(tmp_path: Path):
    done = subprocess.run(
        ["bash", "-c", f"BACKUPS_DIR={shlex.quote(str(tmp_path))}\n. {shlex.quote(str(DUMP_PATH))}\n"],
        capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "" and done.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_prune_keeps_a_unit_with_no_ok_marker(nightly_dir: Path):
    old = _unit(nightly_dir, "nightly", "20260101T000000Z", ok=False)
    _unit(nightly_dir, "nightly", "20260202T000000Z")
    out = _prune(nightly_dir.parent, "nightly", 1)
    assert "KEEP" in out and "PRUNED" not in out
    assert old.with_name(old.name + ".dump.age").exists()
    assert old.with_name(old.name + ".meta.json").exists()


def test_prune_keeps_a_unit_whose_plaintext_is_not_yet_released(nightly_dir: Path):
    old = _unit(nightly_dir, "nightly", "20260101T000000Z", dump=True)
    _unit(nightly_dir, "nightly", "20260202T000000Z")
    out = _prune(nightly_dir.parent, "nightly", 1)
    assert "unreleased plaintext" in out and "PRUNED" not in out
    assert old.with_name(old.name + ".dump").exists()
    assert old.with_name(old.name + ".dump.age").exists()


def test_prune_never_removes_a_plaintext(nightly_dir: Path):
    for day in range(1, 6):
        _unit(nightly_dir, "nightly", f"202601{day:02d}T000000Z", dump=True)
    _prune(nightly_dir.parent, "nightly", 1)
    assert len(list(nightly_dir.glob("*.dump"))) == 5


def test_prune_leaves_a_bad_ciphertext_alone(nightly_dir: Path):
    old = _unit(nightly_dir, "nightly", "20260101T000000Z")
    bad = old.with_name(old.name + ".dump.age.bad-20260105T000000Z")
    bad.write_text("rejected")
    _unit(nightly_dir, "nightly", "20260202T000000Z")
    out = _prune(nightly_dir.parent, "nightly", 1)
    assert "PRUNED" in out
    assert bad.exists()
    assert not old.with_name(old.name + ".dump.age").exists()


def test_prune_keeps_the_newest_thirty_units_and_removes_the_thirty_first_whole(nightly_dir: Path):
    stamps = [f"202601{day:02d}T000000Z" for day in range(1, 32)]
    for stamp in stamps:
        _unit(nightly_dir, "nightly", stamp)
    out = _prune(nightly_dir.parent, "nightly", 30)
    assert out.count("PRUNED") == 1
    oldest = nightly_dir / f"harness-nightly-{stamps[0]}"
    for ext in (".dump.age", ".meta.json", ".ok"):
        assert not oldest.with_name(oldest.name + ext).exists()
    for stamp in stamps[1:]:
        assert (nightly_dir / f"harness-nightly-{stamp}.dump.age").exists()


def test_a_lone_sidecar_is_not_counted_as_a_unit(nightly_dir: Path):
    # A run that died between writing the sidecar and renaming the dump leaves an orphan .meta;
    # counting it would hold a keep slot for good and prune a real unit one slot early.
    for day in range(1, 4):
        (nightly_dir / f"harness-nightly-202601{day:02d}T000000Z.meta.json").write_text("{}")
    kept = _unit(nightly_dir, "nightly", "20260210T000000Z")
    out = _prune(nightly_dir.parent, "nightly", 1)
    assert "PRUNED" not in out
    assert kept.with_name(kept.name + ".dump.age").exists()


def test_prune_does_nothing_to_a_kind_it_was_not_asked_about(tmp_path: Path):
    forever = tmp_path / "forever"
    forever.mkdir()
    _unit(forever, "forever", "20260101T000000Z")
    nightly = tmp_path / "nightly"
    nightly.mkdir()
    _unit(nightly, "nightly", "20260101T000000Z")
    _unit(nightly, "nightly", "20260202T000000Z")
    _prune(tmp_path, "nightly", 1)
    assert len(list(forever.iterdir())) == 3


def test_dump_sh_writes_table_counts_into_the_meta_sidecar():
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert "table_counts_json()" in body
    # The counts come from the database at dump time, never from a later reader.
    assert "select count(*)" in body
    # Every excluded table is left out of the counts, because its data is not in the dump.
    assert "EXCLUDED_TABLES=" in body
    assert '"counts":' in body
    assert '"counts_snapshot":' in body


def test_the_counts_share_the_dumps_own_snapshot():
    """pg_dump snapshots at start and runs for minutes; counting afterwards would describe a
    strictly newer database and every busy table would mismatch. The counts join the dump's
    snapshot instead."""
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert "pg_export_snapshot()" in body
    assert "repeatable read" in body
    assert '--snapshot="$_snap"' in body
    # The counts are taken before pg_dump returns, on the session that exported the snapshot.
    assert body.index("pg_export_snapshot()") < body.index('--snapshot="$_snap"')


def test_the_fallback_is_labelled_when_it_is_taken():
    """If the FIFO session proves unreliable under the sidecar's dash, the counts are taken
    immediately *before* pg_dump and the sidecar says so, and drill.sh keys ROWS_MATCH on
    growth rather than equality. Either way the file records which was done."""
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert '"same as dump"' in body or '"before dump"' in body


def test_dump_sh_still_passes_shellcheck_free_syntax():
    subprocess.run(["sh", "-n", str(ROOT / "deploy" / "backup" / "dump.sh")], check=True)
    subprocess.run(["sh", "-n", str(ROOT / "deploy" / "backup" / "drill.sh")], check=True)


def test_drill_sh_reads_the_meta_counts_and_never_the_live_database():
    body = (ROOT / "deploy" / "backup" / "drill.sh").read_text()
    assert ".meta.json" in body
    assert "meta_count()" in body
    assert "COUNTS_SNAPSHOT" in body
    # The live comparison is gone: no PROD_PSQL, no docker compose exec postgres psql.
    assert "PROD_PSQL" not in body
    assert "docker compose exec -T postgres psql" not in body
    assert "ROWS_MATCH true" in body and "ROWS_MATCH false" in body


def test_the_counts_pairs_use_to_json_for_double_quoted_keys():
    """format('%L', ...) is quote_literal and would emit single-quoted keys ('orders':4212),
    which is not valid JSON and which drill.sh's double-quote-anchored meta_count can never
    match. This is exactly the class of defect a hand-typed test sidecar cannot catch, which is
    why test_drill_meta_count_extraction_reads_a_real_sidecar below builds its counts the way
    the aggregate actually produces them rather than typing the JSON by hand (C1)."""
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert "format('%L:%s', t, n)" not in body
    assert "to_json(t)" in body


def test_every_write_to_the_snapshot_fifo_is_subshell_wrapped():
    """A write to fd 8 after the FIFO's reader has already exited raises SIGPIPE in the writing
    shell itself, which `2>/dev/null || true` cannot catch -- a shell killed by a signal never
    reaches the `||` to produce a status for it to inspect. Wrapping every such write in a
    subshell confines the signal to that subshell instead (C2). `trap '' PIPE` is explicitly
    not the fix: SIG_IGN is inherited by pg_dump, psql, sha256sum and cut, changing their own
    behaviour."""
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert "trap '' PIPE" not in body
    # The three writes to fd 8: the begin/timeouts/export-snapshot preamble, the counts
    # statement, and the final commit.
    assert body.count(">&8 )") >= 3


def test_the_counts_result_is_shape_validated_and_generously_bounded():
    """60s is not generous for a count(*) over `signals` (the largest dumped table) on the NAS
    under live ingest; a timed-out or errored wait must not silently write whatever text was
    last in the output file (the snapshot id itself, on a stall) as if it were the counts (C3)."""
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert "COUNTS_WAIT_S" in body
    assert body.count('grep -Eq \'^"[^"]+":[0-9]+(,"[^"]+":[0-9]+)*$\'') >= 2


def test_the_snapshot_session_has_bounded_statement_and_lock_timeouts():
    """A count(*) needs only ACCESS SHARE, but anything holding ACCESS EXCLUSIVE blocks it
    indefinitely with no statement_timeout set, and dump.sh would then never return (I1)."""
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert "set statement_timeout" in body
    assert "set lock_timeout" in body
    assert "COUNTS_LOCK_TIMEOUT_MS" in body


def test_forever_counts_only_its_own_tables_and_partition_counts_none():
    """A forever or partition dump does not carry the rest of the database, so counting every
    table against it (I2) compares a restore to numbers the dump never claimed to match, and
    would scan the whole database on every one of the (up to eight) partition-archive calls a
    single run can make (I3)."""
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert '"ledger gate_reports"' in body
    assert '_counts_snapshot="none"' in body


def test_drill_iterates_the_sidecars_own_table_list_not_the_restored_one():
    """A table the dump carried but the restore is missing entirely must be reported as a
    mismatch; it can never be found by walking the restored database's own table list, because
    it is not there to walk to (I4)."""
    body = (ROOT / "deploy" / "backup" / "drill.sh").read_text()
    assert "meta_tables()" in body
    assert "MISSING_TABLE" in body
    assert "for t in $(meta_tables)" in body


def test_meta_count_matches_the_table_name_as_a_fixed_string():
    """A quoted Postgres identifier can legally contain '.', '*' or '/' -- all sed regex
    metacharacters -- so the table name is matched with grep -F (fixed string) rather than
    interpolated into a sed pattern (M5)."""
    body = (ROOT / "deploy" / "backup" / "drill.sh").read_text()
    assert "grep -F" in body


def test_the_growth_comparison_guards_against_a_non_integer_count():
    """[ "$here" -gt "$there" ] errors under set -e if either side is not a clean integer,
    aborting the drill with an opaque `sh: bad number` instead of reporting a mismatch (M6)."""
    body = (ROOT / "deploy" / "backup" / "drill.sh").read_text()
    assert "is_int" in body


def _pg_counts_pairs(counts: dict) -> str:
    """Mirrors table_counts_sql's aggregate exactly: string_agg(format('%s:%s', to_json(t), n),
    ',' order by t). to_json() on a text key double-quotes and JSON-escapes it -- the sidecar
    this builds is therefore shaped the way the real query produces it, not hand-typed, so this
    fixture cannot stay green if dump.sh regresses to quote_literal (%L) keys (C1)."""
    return ",".join(f"{json.dumps(k)}:{v}" for k, v in sorted(counts.items()))


def test_drill_meta_count_extraction_reads_a_real_sidecar(tmp_path):
    """The extractor is plain POSIX text handling, so it is testable without a container."""
    counts_pairs = _pg_counts_pairs({"orders": 4212, "signals": 881033, "ledger": 0})
    meta = tmp_path / "harness-nightly-20260909T033000Z.meta.json"
    meta.write_text(
        '{\n  "kind": "nightly",\n  "stamp": "20260909T033000Z",\n'
        '  "path": "x.dump",\n  "sha256": "ab",\n  "bytes": 12,\n'
        '  "started": "s",\n  "finished": "f",\n  "exit_code": 0,\n'
        '  "tables": {"data_excluded":["raw_responses"],'
        f'"counts":{{{counts_pairs}}}}}\n}}\n')
    script = (ROOT / "deploy" / "backup" / "drill.sh").read_text()
    # _meta_pairs is where the "counts" object is actually parsed; meta_count and meta_tables
    # both delegate to it rather than each re-parsing the sidecar themselves.
    pairs_body = script.split("_meta_pairs()", 1)[1].split("}", 1)[0]
    assert "counts" in pairs_body

    # _shell_function always returns text ending in "\n}\n" (the function's closing brace line),
    # so the two extracted functions are concatenated directly with no ";" between them --
    # their own trailing newlines are the statement separators.
    funcs = _shell_function(script, "_meta_pairs") + _shell_function(script, "meta_count")
    out = subprocess.run(
        ["sh", "-c",
         f'META_FILE="{meta}"; ' + funcs +
         'meta_count orders; meta_count signals; meta_count ledger; meta_count nosuch'],
        capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["4212", "881033", "0", ""] or \
           out.stdout.split() == ["4212", "881033", "0"]


def test_drill_meta_tables_lists_every_counted_table(tmp_path):
    counts_pairs = _pg_counts_pairs({"orders": 4212, "signals": 881033, "ledger": 0})
    meta = tmp_path / "harness-nightly-20260909T033000Z.meta.json"
    meta.write_text(
        '{"tables": {"data_excluded":["raw_responses"],'
        f'"counts":{{{counts_pairs}}}}}}}\n')
    script = (ROOT / "deploy" / "backup" / "drill.sh").read_text()
    funcs = _shell_function(script, "_meta_pairs") + _shell_function(script, "meta_tables")
    out = subprocess.run(
        ["sh", "-c", f'META_FILE="{meta}"; ' + funcs + 'meta_tables'],
        capture_output=True, text=True, check=True)
    assert sorted(out.stdout.split()) == ["ledger", "orders", "signals"]


def _shell_function(script: str, name: str) -> str:
    """One named shell function lifted out of a script, so a test runs the real code rather
    than a copy of it. Handles both a one-liner ("name() { ...; }") and a multi-line function
    (closing brace on its own line)."""
    start = script.index(f"{name}()")
    line_end = script.index("\n", start)
    if script[start:line_end].rstrip().endswith("}"):
        return script[start:line_end + 1]
    end = script.index("\n}\n", start) + len("\n}\n")
    return script[start:end]


def _block_through_fi(script: str, start_marker: str) -> str:
    """An if/.../fi block lifted out of a script by its opening line, through the matching "fi"
    on its own line -- used to run a real conditional block against synthetic inputs rather than
    retyping its logic in the test."""
    start = script.index(start_marker)
    end = script.index("\nfi\n", start) + len("\nfi\n")
    return script[start:end]


def test_dump_sh_int_and_term_traps_exit_instead_of_resuming():
    """A trap that only runs a handler and returns does not end the script -- execution resumes
    right where the signal landed, so a single `trap close_snapshot EXIT INT TERM` would absorb
    an INT or TERM mid-dump and carry on into snapshot_counts_json, dump_forever or prune_units
    as if nothing happened. INT and TERM need their own handlers that clean up and then exit
    explicitly (N1)."""
    body = (ROOT / "deploy" / "backup" / "dump.sh").read_text()
    assert "trap close_snapshot EXIT INT TERM" not in body
    assert "trap close_snapshot EXIT" in body
    assert "trap 'close_snapshot; exit 130' INT" in body
    assert "trap 'close_snapshot; exit 143' TERM" in body


def test_drill_counts_snapshot_extraction_keeps_its_internal_spaces(tmp_path):
    """"counts_snapshot"'s value is the two-word strings "same as dump" and "before dump", whose
    spaces are content. Stripping all spaces while extracting it (an earlier version of this
    line did) squashes them to "sameasdump"/"beforedump", which then never equals the literal
    "before dump" comparison the growth-only branch depends on (N2)."""
    meta = tmp_path / "harness-nightly-20260909T033000Z.meta.json"
    meta.write_text('{"tables": {"counts":{"orders":4212},"counts_snapshot":"before dump"}}\n')
    script = (ROOT / "deploy" / "backup" / "drill.sh").read_text()
    extraction = _lines_between(
        script, 'COUNTS_SNAPSHOT=$(tr', 'COUNTS_SNAPSHOT="same as dump"')
    out = subprocess.run(
        ["sh", "-c", f'META_FILE="{meta}"; ' + extraction + 'echo "[$COUNTS_SNAPSHOT]"'],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[before dump]"


def test_drill_growth_only_branch_matches_on_a_grown_table(tmp_path):
    """A sidecar labelled "counts_snapshot":"before dump" took its counts immediately before
    pg_dump started (the labelled fallback), so a busy table can legitimately restore with MORE
    rows than the sidecar recorded -- that must count as a match, not a mismatch, and the overall
    verdict must be ROWS_MATCH true (N2's growth-only branch was unreachable before the fix,
    because the squashed COUNTS_SNAPSHOT value never equalled the literal "before dump")."""
    counts_pairs = _pg_counts_pairs({"orders": 4212})
    meta = tmp_path / "harness-nightly-20260909T033000Z.meta.json"
    meta.write_text(
        '{"tables": {"data_excluded":["raw_responses"],'
        f'"counts":{{{counts_pairs}}},"counts_snapshot":"before dump"}}}}\n')
    script = (ROOT / "deploy" / "backup" / "drill.sh").read_text()

    funcs = (
        _lines_between(script, 'COUNTS_SNAPSHOT=$(tr', 'COUNTS_SNAPSHOT="same as dump"')
        + _shell_function(script, "_meta_pairs")
        + _shell_function(script, "meta_count")
        + _shell_function(script, "is_int")
        + _shell_function(script, "compare_row")
    )
    cmd = (
        f'META_FILE="{meta}"; ' + funcs +
        # The table restored with more rows (5000) than the sidecar recorded (4212, from the
        # real meta_count) -- legitimate growth under the labelled fallback, not a mismatch.
        'there=$(meta_count orders); here=5000; compared=1; mismatches=0; '
        'compare_row orders "$here" "$there" || mismatches=$((mismatches + 1)); '
        'echo "COMPARED $compared MISMATCHES $mismatches"; '
        'if [ "$compared" -gt 0 ] && [ "$mismatches" -eq 0 ]; then '
        'echo "ROWS_MATCH true"; else echo "ROWS_MATCH false"; fi'
    )
    out = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, check=True)
    assert "orders 5000 4212 GREW" in out.stdout
    assert "ROWS_MATCH true" in out.stdout


def test_drill_reports_na_when_the_sidecar_took_no_counts():
    """A partition dump's sidecar records "counts_snapshot":"none" because dump.sh takes no
    counts for that kind at all (I2/I3): nothing to compare is not the same failure as a
    mismatch, and ROWS_MATCH false here would read as a failed restore when nothing was ever
    compared (N3)."""
    script = (ROOT / "deploy" / "backup" / "drill.sh").read_text()
    block = _block_through_fi(script, 'if [ "$COUNTS_SNAPSHOT" = "none" ]; then')
    out = subprocess.run(
        ["sh", "-c", 'COUNTS_SNAPSHOT="none"; ' + block],
        capture_output=True, text=True)
    assert out.returncode == 0
    assert "COMPARED 0 MISMATCHES 0 NO_COUNT 0" in out.stdout
    assert "ROWS_MATCH n/a" in out.stdout


def _lines_between(script: str, start_marker: str, end_line_marker: str) -> str:
    """Text from start_marker through the end of the line containing end_line_marker -- used to
    lift a short, non-function code fragment (like the COUNTS_SNAPSHOT extraction) out of a
    script for a test to run directly."""
    start = script.index(start_marker)
    end = script.index(end_line_marker, start)
    end = script.index("\n", end) + 1
    return script[start:end]
