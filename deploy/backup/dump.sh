#!/bin/sh
set -eu
set -f   # no pathname expansion: the --exclude-table-data patterns below stay literal
# =============================================================================================
# One dump of the harness database, run by loop.sh inside the app-backup sidecar (addendum
# 4.1 and 4.2).  POSIX sh: this runs on postgres:16, whose /bin/sh is dash.
#
#   dump.sh nightly                     schema-and-non-bulk-data dump, retained 30 units
#   dump.sh weekly                      the same dump, retained 8 units
#   dump.sh partition <table> <part>    one sealed weekly partition, kept forever
#
# The sidecar is the only place pg_dump 16 exists in this stack (the app image has none and
# gains no packages).  It reaches Postgres over the compose network with PGHOST/PGUSER/
# PGDATABASE/PGPASSWORD from its service environment, because libpq cannot parse the
# postgresql+psycopg:// dialect URL the application uses.
#
# What this script may delete: a ".tmp" it wrote itself in this run, and -- in prune_units at
# the bottom -- the ciphertext, sidecar and marker of a unit that is past the keep window AND
# carries the ".ok" marker harness backup-encrypt writes beside a verified ciphertext.  It
# never deletes a plaintext ".dump" (delete_verified_plaintexts owns those, and only after a
# Mac-side decrypt drill), and it never touches partitions/ or forever/.
# =============================================================================================

BACKUPS_DIR="${BACKUPS_DIR:-/backups}"

# Ruling B-I10.  /backups is the bind mount of /volume1/docker/sports-harness/backups, so its
# filesystem free percentage is /volume1's.  Every dump kind checks it and skips below 30 %.
MIN_FREE_PCT="${MIN_FREE_PCT:-30}"

# Retention, counted in units (dump + ciphertext + sidecar + marker), never in files.
KEEP_NIGHTLY=30
KEEP_WEEKLY=8

# The five bulk tables (decision 8): their schema is dumped, their data is not.  Three of them
# are weekly-partitioned (<table>_y<isoyear>w<isoweek>), so each name is excluded twice: the
# parent by name and every partition by pattern.  "set -f" above keeps the "*" literal.
EXCLUDE_ARGS="--exclude-table-data=public.raw_responses --exclude-table-data=public.raw_responses_y*"
EXCLUDE_ARGS="$EXCLUDE_ARGS --exclude-table-data=public.orderbook_events --exclude-table-data=public.orderbook_events_y*"
EXCLUDE_ARGS="$EXCLUDE_ARGS --exclude-table-data=public.venue_trades --exclude-table-data=public.venue_trades_y*"
EXCLUDE_ARGS="$EXCLUDE_ARGS --exclude-table-data=public.venue_quotes --exclude-table-data=public.venue_quotes_y*"
EXCLUDE_ARGS="$EXCLUDE_ARGS --exclude-table-data=public.odds_snapshots --exclude-table-data=public.odds_snapshots_y*"

EXCLUDED_JSON='["raw_responses","orderbook_events","venue_trades","venue_quotes","odds_snapshots"]'

now_stamp()  { date -u +%Y%m%dT%H%M%SZ; }
now_iso()    { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- the backup_runs row -----------------------------------------------------------------
# backup-precheck reads the newest kind='nightly' status='ok' row, so the sidecar writes it;
# harness backup-encrypt writes only the kind='encrypt' rows.  A failure to record is logged
# and never fails the dump: the file on disk is the backup, the row is the report of it.

sql_quote() { printf '%s' "$1" | sed "s/'/''/g"; }
sql_text()  { if [ -z "$1" ]; then printf 'NULL'; else printf "'%s'" "$(sql_quote "$1")"; fi; }
sql_num()   { if [ -z "$1" ]; then printf 'NULL'; else printf '%s' "$1"; fi; }
sql_json()  { if [ -z "$1" ]; then printf 'NULL'; else printf "'%s'::jsonb" "$(sql_quote "$1")"; fi; }

record_run() {
    _kind=$1; _status=$2; _path=$3; _bytes=$4; _sha=$5; _started=$6; _finished=$7; _notes=$8
    if ! psql -v ON_ERROR_STOP=1 -q -c "insert into backup_runs \
(kind, build_sha, path, bytes, plaintext_sha256, status, started_at, finished_at, notes) values \
($(sql_text "$_kind"), $(sql_text "${BUILD_SHA:-}"), $(sql_text "$_path"), $(sql_num "$_bytes"), \
$(sql_text "$_sha"), $(sql_text "$_status"), $(sql_text "$_started"), $(sql_text "$_finished"), \
$(sql_json "$_notes"))" >/dev/null; then
        echo "WARN could not record a backup_runs row for $_kind/$_status"
    fi
}

# --- the free-space guard ------------------------------------------------------------------

free_pct() {
    # df -P prints one line per filesystem: capacity ($5) is the *used* percentage.
    df -P "$1" | awk 'NR == 2 { gsub(/%/, "", $5); print 100 - $5 }'
}

require_free_space() {
    _kind=$1
    _pct=$(free_pct "$BACKUPS_DIR" || true)
    if [ -z "$_pct" ]; then
        echo "SKIP $_kind could not read free space on $BACKUPS_DIR"
        record_run "$_kind" skipped "" "" "" "$(now_iso)" "$(now_iso)" \
            '{"reason":"free space unreadable"}'
        return 1
    fi
    if [ "$_pct" -lt "$MIN_FREE_PCT" ]; then
        echo "SKIP $_kind free=${_pct}% below MIN_FREE_PCT=${MIN_FREE_PCT}% on $BACKUPS_DIR"
        record_run "$_kind" skipped "" "" "" "$(now_iso)" "$(now_iso)" \
            "{\"reason\":\"low free space\",\"free_pct\":$_pct,\"min_free_pct\":$MIN_FREE_PCT}"
        return 1
    fi
    return 0
}

# --- one dump ------------------------------------------------------------------------------
# Writes <base>.dump.tmp, then the sidecar, then renames the plaintext into place, so a unit is
# never visible to harness backup-encrypt without the .meta.json it needs (scan_units ignores
# both ".tmp" names).  The sidecar carries what a later reader cannot recompute from a deleted
# plaintext: its sha256, its size, the window it covers and pg_dump's own exit code.

write_meta() {
    # base kind stamp sha bytes started finished exit_code tables_json
    cat > "$1.meta.json.tmp" <<META
{
  "kind": "$2",
  "stamp": "$3",
  "path": "$1.dump",
  "sha256": "$4",
  "bytes": $5,
  "started": "$6",
  "finished": "$7",
  "exit_code": $8,
  "tables": $9
}
META
    mv -- "$1.meta.json.tmp" "$1.meta.json"
}

run_dump() {
    # kind dir_name tables_json  [extra pg_dump args...]
    _kind=$1; _dir_name=$2; _tables_json=$3; _name_extra=$4
    shift 4

    _dir="$BACKUPS_DIR/$_dir_name"
    mkdir -p "$_dir"
    _stamp=$(now_stamp)
    _base="$_dir/harness-$_dir_name-$_name_extra$_stamp"
    _tmp="$_base.dump.tmp"
    _started=$(now_iso)

    _rc=0
    pg_dump -Fc --compress=zstd:3 "$@" -f "$_tmp" || _rc=$?
    _finished=$(now_iso)

    if [ "$_rc" -ne 0 ]; then
        echo "ERROR $_kind pg_dump exited $_rc"
        rm -f -- "$_tmp"          # the only removal here: the incomplete file this run wrote
        record_run "$_kind" error "$_base.dump" "" "" "$_started" "$_finished" \
            "{\"exit_code\":$_rc}"
        return 1
    fi

    _bytes=$(wc -c < "$_tmp" | tr -d ' ')
    _sha=$(sha256sum "$_tmp" | cut -d' ' -f1)
    write_meta "$_base" "$_kind" "$_stamp" "$_sha" "$_bytes" "$_started" "$_finished" "$_rc" \
        "$_tables_json"
    mv -- "$_tmp" "$_base.dump"
    record_run "$_kind" ok "$_base.dump" "$_bytes" "$_sha" "$_started" "$_finished" \
        "{\"exit_code\":0}"
    echo "OK $_kind $_base.dump bytes=$_bytes sha256=$_sha"
    return 0
}

# --- the CSV exports that outlive every dump ------------------------------------------------
# ledger and gate_reports are small, are the two tables a human will want years from now, and
# are never pruned.  COPY ... TO STDOUT is a plain read; the file is written .tmp and renamed.

export_forever() {
    _dir="$BACKUPS_DIR/forever"
    mkdir -p "$_dir"
    _stamp=$1
    for _t in ledger gate_reports; do
        _out="$_dir/$_t-$_stamp.csv"
        if psql -v ON_ERROR_STOP=1 -q \
            -c "COPY (SELECT * FROM public.$_t) TO STDOUT WITH (FORMAT csv, HEADER true)" \
            > "$_out.tmp"; then
            mv -- "$_out.tmp" "$_out"
            echo "OK forever $_out"
        else
            echo "WARN forever export of $_t failed"
            rm -f -- "$_out.tmp"  # the only removal here: the incomplete file this run wrote
        fi
    done
}

# --- one sealed weekly partition, archived once and then never touched ----------------------

archive_partition() {
    _table=$1
    _part=$2
    case "$_part" in
        "$_table"_y[0-9][0-9][0-9][0-9]w[0-9][0-9]) : ;;
        *) echo "ERROR partition name $_part is not a weekly partition of $_table" >&2
           return 2 ;;
    esac
    _exists=$(psql -v ON_ERROR_STOP=1 -qAt \
        -c "select to_regclass('public.$_part') is not null") || _exists=""
    if [ "$_exists" != "t" ]; then
        echo "SKIP partition $_part does not exist"
        return 0
    fi
    run_dump partition partitions "{\"included\":[\"$_part\"]}" "$_part-" -t "public.$_part"
}

# --- dispatch --------------------------------------------------------------------------------
# Every kind guards on free space first and journals its skip on stdout (the controller reads
# `docker compose logs app-backup`) as well as into backup_runs.

main() {
    mkdir -p "$BACKUPS_DIR"
    case "${1:-}" in
        nightly)
            require_free_space nightly || return 0
            run_dump nightly nightly "{\"data_excluded\":$EXCLUDED_JSON}" "" $EXCLUDE_ARGS
            export_forever "$(now_stamp)"
            prune_units nightly "$KEEP_NIGHTLY"
            ;;
        weekly)
            require_free_space weekly || return 0
            run_dump weekly weekly "{\"data_excluded\":$EXCLUDED_JSON}" "" $EXCLUDE_ARGS
            prune_units weekly "$KEEP_WEEKLY"
            ;;
        partition)
            if [ $# -ne 3 ]; then
                echo "usage: dump.sh partition <table> <partition>" >&2
                return 2
            fi
            require_free_space partition || return 0
            archive_partition "$2" "$3"
            ;;
        *)
            echo "usage: dump.sh nightly|weekly|partition <table> <partition>" >&2
            return 2
            ;;
    esac
}

# --- unit retention (ruling A-I9) -------------------------------------------------------------
# Units are counted newest-first by their sidecar (exactly one per unit, and the basic-format
# UTC stamp sorts lexically), and only units past the keep window are considered at all.
#
# A unit is released only when the marker beside it exists.  The marker is written by
# harness backup-encrypt after the ciphertext passed its structure check, and it is the whole
# reason this loop is allowed to delete anything: a POSIX-sh sidecar cannot query backup_runs
# without authenticating to Postgres, so the marker file is the ok row's stand-in on disk.
# No marker, or a plaintext still sitting beside the ciphertext (not yet released by a
# Mac-side drill), means the unit is kept and the reason is journalled.
#
# The marker of a unit being kept is never removed, a plaintext is never removed here, and a
# ciphertext renamed aside as ".dump.age.bad-<stamp>" by a failed structure check is not part
# of any of the three names below, so it survives too.
prune_units() {
    _kind=$1
    _keep=$2
    _dir="$BACKUPS_DIR/$_kind"
    [ -d "$_dir" ] || return 0

    _n=0
    for _meta in $(find "$_dir" -maxdepth 1 -type f -name "harness-$_kind-*.meta.json" | sort -r)
    do
        _n=$((_n + 1))
        [ "$_n" -gt "$_keep" ] || continue
        BASE="${_meta%.meta.json}"
        if [ ! -f "${BASE}.ok" ]; then
            echo "KEEP ${BASE} past the newest $_keep but has no ${BASE}.ok marker"
            continue
        fi
        if [ -f "${BASE}.dump" ]; then
            echo "KEEP ${BASE} still has an unreleased plaintext beside its ciphertext"
            continue
        fi
        rm -f -- "${BASE}.dump.age" "${BASE}.meta.json" "${BASE}.ok"
        echo "PRUNED ${BASE}"
    done
}

main "$@"
