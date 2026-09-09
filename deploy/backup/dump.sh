#!/bin/sh
set -eu
set -f   # no pathname expansion: the --exclude-table-data patterns below stay literal
# =============================================================================================
# One dump of the harness database, run by loop.sh inside the app-backup sidecar (addendum
# 4.1 and 4.2).  POSIX sh: this runs on postgres:16, whose /bin/sh is dash.
#
#   dump.sh nightly                     schema-and-non-bulk-data dump, retained 30 units,
#                                       followed by the forever dump below
#   dump.sh weekly                      the same dump, retained 8 units
#   dump.sh forever                     ledger + gate_reports, never pruned
#   dump.sh partition <table> <part>    one sealed weekly partition, kept forever
#
# The sidecar is the only place pg_dump 16 exists in this stack (the app image has none and
# gains no packages).  It reaches Postgres over the compose network with PGHOST/PGUSER/
# PGDATABASE/PGPASSWORD from its service environment, because libpq cannot parse the
# postgresql+psycopg:// dialect URL the application uses.
#
# Every kind writes the same shape -- <base>.dump plus <base>.meta.json -- because that shape is
# what `harness backup-encrypt` recognises as a unit.  So every kind, forever/ included, is
# encrypted to <base>.dump.age with the age recipient, structure-checked, marked with
# <base>.ok, and released from its plaintext only after a Mac-side decrypt drill.  Nothing
# here is left readable to whoever reaches the NAS share.
#
# The forever export is therefore a custom-format dump, not a .csv.  To get CSV back from one:
#
#   pg_restore --data-only -t ledger -f - <base>.dump      # COPY + tab-separated rows
#
# or, for true CSV, restore it into a throwaway with deploy/backup/drill.sh and then, in that
# container, COPY (SELECT * FROM ledger) TO STDOUT WITH (FORMAT csv, HEADER true).
#
# What this script may delete: a ".tmp" it wrote itself in this run, and -- in prune_units at
# the bottom -- the ciphertext, sidecar and marker of a unit that is past the keep window AND
# carries the ".ok" marker `harness backup-encrypt` writes beside a verified ciphertext.  It
# never deletes a plaintext ".dump" (delete_verified_plaintexts owns those, and only after a
# Mac-side decrypt drill), and it is never called for the two kinds that are kept forever.
# =============================================================================================

BACKUPS_DIR="${BACKUPS_DIR:-/backups}"

# Ruling B-I10.  /backups is the bind mount of /volume1/docker/sports-harness/backups, so its
# filesystem free percentage is /volume1's.  Every dump kind checks it and skips below 30 %.
MIN_FREE_PCT="${MIN_FREE_PCT:-30}"

# One dump at a time.  The scheduled loop and the deploy recipe's fallback dump run in the same
# container, and two runs starting in the same second would collide on the stamp.
LOCK_FILE="${LOCK_FILE:-$BACKUPS_DIR/.dump.lock}"
LOCK_WAIT_S="${LOCK_WAIT_S:-900}"

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

# The same five names as EXCLUDED_JSON, as a space-separated list the counter skips: their data
# is not in the dump, so counting them would record a number the restore can never match.
EXCLUDED_TABLES="raw_responses orderbook_events venue_trades venue_quotes odds_snapshots"

now_stamp()  { date -u +%Y%m%dT%H%M%SZ; }
now_iso()    { date -u +%Y-%m-%dT%H:%M:%SZ; }

# --- the backup_runs row -----------------------------------------------------------------
# backup-precheck reads the newest kind='nightly' status='ok' row, so the sidecar writes it;
# `harness backup-encrypt` writes only the kind='encrypt' rows.  A failure to record is logged
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

# --- one dump at a time ----------------------------------------------------------------------

lock_or_skip() {
    if ! command -v flock >/dev/null 2>&1; then
        echo "WARN flock is unavailable; running $1 without the dump lock"
        return 0
    fi
    if ! : >> "$LOCK_FILE" 2>/dev/null; then
        echo "WARN cannot open $LOCK_FILE; running $1 without the dump lock"
        return 0
    fi
    exec 9>> "$LOCK_FILE"
    if ! flock -w "$LOCK_WAIT_S" 9; then
        echo "SKIP $1 another dump.sh run holds $LOCK_FILE"
        return 1
    fi
    return 0
}

# --- the free-space guard ------------------------------------------------------------------
# Fails closed: anything but a plain integer percentage is treated as no space at all, because
# the alternative is filling /volume1 and taking the recorder down with it.

free_pct() {
    # df -P prints one line per filesystem: capacity ($5) is the *used* percentage.
    df -P "$1" | awk 'NR == 2 { gsub(/%/, "", $5); print 100 - $5 }'
}

require_free_space() {
    _kind=$1
    _pct=$(free_pct "$BACKUPS_DIR" || true)
    case "$_pct" in
        ''|*[!0-9]*)
            echo "SKIP $_kind could not read a free-space percentage for $BACKUPS_DIR"
            record_run "$_kind" skipped "" "" "" "$(now_iso)" "$(now_iso)" \
                '{"reason":"free space unreadable"}'
            return 1 ;;
    esac
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
# never visible to `harness backup-encrypt` without the .meta.json it needs (scan_units ignores
# both ".tmp" names).  The sidecar carries what a later reader cannot recompute from a deleted
# plaintext: its sha256, its size, the window it covers and pg_dump's own exit code.

# --- dump-time row counts (addendum §0.7) ----------------------------------------------------
# The restore drill's comparison has to be against the counts as they were when the dump was
# taken: production keeps recording, so comparing a restore against the live database reports a
# MISMATCH on every busy table and the ROWS_MATCH line says nothing.  One scan of each dumped
# table, in the same 03:30 CT window; `signals` is the largest and is the cost -- and only for
# nightly/weekly (ruling I2/I3): `forever` counts only its own two tables and `partition` takes
# no counts at all, because those kinds' dumps do not carry the rest of the database.
#
# Partitioned parents are counted through the parent, which sums its children -- the same number
# pg_restore's data produces.  Excluded tables are skipped entirely.  A psql failure yields an
# empty object rather than a partial one: a sidecar that records some counts and silently drops
# others is worse than one that records none, because the drill would compare fewer tables and
# still print ROWS_MATCH true.
#
# The key uses to_json(), never format('%L', ...): %L is quote_literal and emits single-quoted
# text ('orders'), which is not valid JSON and which drill.sh's double-quote-anchored meta_count
# can never match (C1).  to_json() on text double-quotes and JSON-escapes it correctly.
table_counts_sql() {
    # $1, if given, is a space-separated explicit set of tables to count (forever's own two
    # tables).  Empty means "every table except the five excluded bulk tables" (nightly/weekly).
    _only="${1:-}"
    if [ -n "$_only" ]; then
        _in_pattern=""
        for _t in $_only; do
            _in_pattern="$_in_pattern'$_t',"
        done
        _filter="c.relname in (${_in_pattern}'')"
    else
        _skip_pattern=""
        for _t in $EXCLUDED_TABLES; do
            _skip_pattern="$_skip_pattern'$_t',"
        done
        _filter="c.relname not in (${_skip_pattern}'')"
    fi
    printf '%s' "select string_agg(format('%s:%s', to_json(t), n), ',' order by t) from (
              select c.relname as t,
                     (xpath('/row/c/text()',
                            query_to_xml(format('select count(*) as c from public.%I', c.relname),
                                         false, true, '')))[1]::text::bigint as n
              from pg_class c
              join pg_namespace ns on ns.oid = c.relnamespace
              where ns.nspname = 'public'
                and c.relkind in ('r', 'p')
                and c.relispartition = false
                and $_filter
          ) s;"
}

# The standalone form, used only by the labelled fallback below: its own connection, its own
# snapshot.  The snapshot path sends `table_counts_sql` down the FIFO instead, so the counts and
# the dump see the same rows.  stderr is not redirected here: a real psql failure belongs in the
# container log, not silently swallowed (M1) -- only a validated-bad or empty *result* warns.
table_counts_json() {
    _only="${1:-}"
    _rc=0
    _pairs=$(psql -v ON_ERROR_STOP=1 -qAt -c "$(table_counts_sql "$_only")") || _rc=$?
    if [ "$_rc" -ne 0 ]; then
        echo "WARN could not read dump-time table counts (psql exited $_rc)" >&2
        printf '{}'
        return 0
    fi
    # A database with nothing left to count (string_agg over zero rows) legitimately returns an
    # empty string, not a failure (M2) -- so empty is accepted outright and only a non-empty,
    # non-matching shape warns.  `printf '%s'` on an empty string writes zero bytes, and grep
    # never matches "^$" against zero bytes (there is no line to test), so emptiness is checked
    # directly rather than folded into the regex.
    if [ -n "$_pairs" ] && ! printf '%s\n' "$_pairs" | grep -Eq '^"[^"]+":[0-9]+(,"[^"]+":[0-9]+)*$'; then
        echo "WARN dump-time counts unreadable or malformed; writing an empty counts object" >&2
        _pairs=""
    fi
    printf '{%s}' "$_pairs"
}

# --- one snapshot for the dump and the counts -------------------------------------------------
# pg_dump takes its repeatable-read snapshot when it starts and a nightly run takes minutes, in
# which signals, orders, fills, metric_samples and order_events all keep taking writes.  Counting
# after the dump returns would describe a strictly newer database, so every busy table would
# mismatch and ROWS_MATCH would mean nothing -- the failure this whole change exists to remove.
# So: export a snapshot, hand it to pg_dump, run the counts on the same session, then commit.
# The session must stay open for the whole dump or the snapshot is released, which is why psql
# reads from a FIFO rather than from a heredoc.
#
# COUNTS_WAIT_S bounds three things to the same budget: how long the client waits for the counts
# to land, the session's own statement_timeout (so a blocked count fails loudly instead of
# hanging), and how long close_snapshot waits before killing a stuck session (I1).
COUNTS_WAIT_S="${COUNTS_WAIT_S:-600}"
COUNTS_LOCK_TIMEOUT_MS="${COUNTS_LOCK_TIMEOUT_MS:-30000}"

open_snapshot() {
    _snap_ctl=$(mktemp -u)
    _snap_out=$(mktemp)
    # Prove psql can connect before opening the FIFO: `exec 8> "$_snap_ctl"` blocks until a
    # reader arrives, so a psql that cannot start would hang the nightly dump forever instead of
    # taking the labelled fallback below.  Each early return here cleans up the two temporaries
    # this call already made (I5) -- the EXIT/INT/TERM trap in main() is the backstop for every
    # other exit, not the only line of defence.
    if ! psql -X -qAt -c 'select 1' >/dev/null 2>&1; then
        rm -f -- "$_snap_ctl" "$_snap_out"
        return 1
    fi
    if ! mkfifo "$_snap_ctl"; then
        rm -f -- "$_snap_ctl" "$_snap_out"
        return 1
    fi
    # mktemp -u then mkfifo is a TOCTOU on a shared directory; the sidecar's /tmp is private to
    # this container, so it is not reachable here, but the pattern should not be copied where
    # it is (M4).
    ( psql -X -v ON_ERROR_STOP=1 -qAt -f - < "$_snap_ctl" > "$_snap_out" ) &
    _snap_pid=$!
    # Hold the write end open ourselves, so psql does not see EOF between statements.
    exec 8> "$_snap_ctl"
    # A write to fd 8 after the reader has already exited raises SIGPIPE in this shell itself,
    # which `2>/dev/null || true` cannot catch (a dead shell produces no exit status for `||` to
    # inspect).  Wrapping the write in a subshell confines the signal to that subshell (C2).
    # statement_timeout/lock_timeout bound the counts query itself (I1); set before the snapshot
    # is exported so they cover every statement this session runs, counts included.
    ( printf 'begin isolation level repeatable read;\nset statement_timeout = %s;\nset lock_timeout = %s;\nselect pg_export_snapshot();\n' \
          "$((COUNTS_WAIT_S * 1000))" "$COUNTS_LOCK_TIMEOUT_MS" >&8 ) 2>/dev/null || true
    # Wait for the snapshot id to appear, up to 10 seconds.
    _i=0
    while [ ! -s "$_snap_out" ] && [ "$_i" -lt 100 ]; do _i=$((_i + 1)); sleep 0.1; done
    _snap=$(head -1 "$_snap_out")
    case "$_snap" in
        [0-9]*-[0-9]*-[0-9]*) return 0 ;;
        *) echo "WARN could not export a dump snapshot; counts fall back to before-dump" >&2
           close_snapshot; return 1 ;;
    esac
}

# The counts, on the session that holds the snapshot: the statement goes down the FIFO and the
# answer comes back out of the same output file, appended after the snapshot id psql already
# wrote.  The file's contents are never wiped: psql holds it open and keeps its own write
# offset, so `: > "$_snap_out"` would leave the counts sitting behind a run of NUL padding and
# `head -1` would read the NULs.  Wait for a new line instead, and read the last one.
#
# psql (16) flushes its output file after every query result (PrintQueryTuples flushes fout), so
# waiting on the file's own line count -- rather than trusting a fixed sleep -- observes the
# counts as soon as they land (I6).
_snap_lines() { wc -l < "$_snap_out" | tr -d ' '; }
snapshot_counts_json() {
    # $1, if given, is the explicit table list forwarded to table_counts_sql (forever's own two
    # tables); empty means "every non-excluded table" (nightly/weekly).
    _only="${1:-}"
    _before=$(_snap_lines)
    ( printf '%s\n' "$(table_counts_sql "$_only")" >&8 ) 2>/dev/null || true
    _i=0
    _wait_ticks=$((COUNTS_WAIT_S * 10))
    while [ "$(_snap_lines)" -le "$_before" ] && [ "$_i" -lt "$_wait_ticks" ]; do
        _i=$((_i + 1)); sleep 0.1
    done
    if [ "$_i" -ge "$_wait_ticks" ]; then
        echo "WARN dump-time counts did not appear within ${COUNTS_WAIT_S}s" >&2
    fi
    _pairs=$(tail -1 "$_snap_out")
    # Reject anything that is not "key":int[,"key":int]... (or empty): a stalled write leaves the
    # snapshot id itself as the last line, and a query that errored under statement_timeout
    # leaves whatever partial text psql printed -- either would otherwise be written into
    # .meta.json as the counts with no warning at all (C3).  Emptiness itself is legitimate (a
    # database with nothing left to count) and is checked directly, not through the regex:
    # `printf '%s'` on an empty string writes zero bytes, and grep never matches "^$" against
    # zero bytes.
    if [ -n "$_pairs" ] && ! printf '%s\n' "$_pairs" | grep -Eq '^"[^"]+":[0-9]+(,"[^"]+":[0-9]+)*$'; then
        echo "WARN dump-time counts unreadable or malformed; writing an empty counts object" >&2
        _pairs=""
    fi
    if [ -z "$_pairs" ]; then printf '{}'; else printf '{%s}' "$_pairs"; fi
}

close_snapshot() {
    if [ -n "${_snap_pid:-}" ]; then
        ( printf 'commit;\n' >&8 ) 2>/dev/null || true
        exec 8>&- 2>/dev/null || true
        # Bounded by the same budget as the counts wait (I1): a session stuck behind a lock the
        # counts query cannot break (an ACCESS EXCLUSIVE holder outlasting lock_timeout retries,
        # or a commit that never returns) must not hang dump.sh forever.
        _i=0
        while kill -0 "$_snap_pid" 2>/dev/null && [ "$_i" -lt "$COUNTS_WAIT_S" ]; do
            _i=$((_i + 1)); sleep 1
        done
        if kill -0 "$_snap_pid" 2>/dev/null; then
            echo "WARN counts session did not exit within ${COUNTS_WAIT_S}s; killing it" >&2
            kill "$_snap_pid" 2>/dev/null || true
        fi
        wait "$_snap_pid" 2>/dev/null || true
        _snap_pid=""
    fi
    rm -f -- "${_snap_ctl:-}" "${_snap_out:-}"     # the two temporaries this run made, and nothing else
}

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
    # kind dir_name tables_json name_extra [pg_dump args...]
    _kind=$1; _dir_name=$2; _tables_json=$3; _name_extra=$4
    shift 4

    _dir="$BACKUPS_DIR/$_dir_name"
    mkdir -p "$_dir"
    _stamp=$(now_stamp)
    _base="$_dir/harness-$_dir_name-$_name_extra$_stamp"
    _tmp="$_base.dump.tmp"
    _started=$(now_iso)

    # Ruling I2/I3: counts are taken only for the tables this kind's dump actually carries, and
    # only nightly/weekly pay for a full-database scan.  `partition` archives one sealed weekly
    # partition and nothing else, so it takes no counts at all -- a full scan on every one of the
    # (up to eight) partition-archive calls loop.sh can make in a run was the bug I2/I3 named.
    _rc=0
    case "$_kind" in
        partition)
            _counts_snapshot="none"
            _counts="{}"
            pg_dump -Fc --compress=zstd:3 "$@" -f "$_tmp" || _rc=$?
            ;;
        *)
            _count_tables=""
            [ "$_kind" != "forever" ] || _count_tables="ledger gate_reports"
            _counts_snapshot="same as dump"
            if open_snapshot; then
                pg_dump -Fc --compress=zstd:3 --snapshot="$_snap" "$@" -f "$_tmp" || _rc=$?
                _counts=$(snapshot_counts_json "$_count_tables")
                close_snapshot
                # snapshot_counts_json's own C3 validation only warns; an empty result reaching
                # here means the counts could not be trusted, so the sidecar says so rather than
                # claiming "same as dump" over nothing.
                [ "$_counts" != "{}" ] || _counts_snapshot="before dump"
            else
                # Labelled fallback: the counts are taken immediately *before* the dump, so a
                # busy table can legitimately restore with MORE rows than the sidecar records,
                # never fewer.
                _counts_snapshot="before dump"
                _counts=$(table_counts_json "$_count_tables")
                pg_dump -Fc --compress=zstd:3 "$@" -f "$_tmp" || _rc=$?
            fi
            ;;
    esac
    _finished=$(now_iso)

    if [ "$_rc" -ne 0 ]; then
        echo "ERROR $_kind pg_dump exited $_rc"
        rm -f -- "$_tmp"          # the only removal here: the incomplete file this run wrote
        record_run "$_kind" error "$_base.dump" "" "" "$_started" "$_finished" \
            "{\"exit_code\":$_rc}"
        return 1
    fi

    # No pipefail in POSIX sh, so cut would mask a sha256sum failure and the sidecar would
    # record the empty string in the one field an operator trusts years later. Check instead.
    _bytes=$(wc -c < "$_tmp" | tr -d ' ')
    _sha=$(sha256sum "$_tmp" | cut -d' ' -f1)
    _bad=0
    case "$_bytes" in ''|*[!0-9]*) _bad=1 ;; esac
    case "$_sha" in ''|*[!0-9a-f]*) _bad=1 ;; esac
    [ "${#_sha}" -eq 64 ] || _bad=1
    if [ "$_bad" -ne 0 ]; then
        echo "ERROR $_kind could not size or hash $_tmp"
        rm -f -- "$_tmp"          # the only removal here: the file this run wrote and cannot describe
        record_run "$_kind" error "$_base.dump" "" "" "$_started" "$_finished" \
            '{"error":"sha256 or size unavailable"}'
        return 1
    fi

    # _tables_json is always a one-key object ({"data_excluded":[...]} or {"included":[...]}),
    # so this sed appends two keys to it: the result is
    # {"data_excluded":[...],"counts":{...},"counts_snapshot":"same as dump"}.
    # $_counts is validated by table_counts_json/snapshot_counts_json (C3) and never contains
    # anything but digits, commas, colons and quotes today -- but $_counts is used as a sed
    # *replacement*, where "&" and "\" are metacharacters, so it is escaped anyway (M3) rather
    # than relying on that validation to hold forever.
    _counts_escaped=$(printf '%s' "$_counts" | sed 's/[&\]/\\&/g')
    write_meta "$_base" "$_kind" "$_stamp" "$_sha" "$_bytes" "$_started" "$_finished" "$_rc" \
        "$(printf '%s' "$_tables_json" \
           | sed "s/}$/,\"counts\":$_counts_escaped,\"counts_snapshot\":\"$_counts_snapshot\"}/")"
    mv -- "$_tmp" "$_base.dump"
    record_run "$_kind" ok "$_base.dump" "$_bytes" "$_sha" "$_started" "$_finished" \
        "{\"exit_code\":0}"
    echo "OK $_kind $_base.dump bytes=$_bytes sha256=$_sha"
    return 0
}

# --- the export that outlives every other unit ----------------------------------------------
# ledger and gate_reports are small, are the two tables a human will want years from now, and
# are never pruned.  Same custom format and same unit shape as a nightly, so the same encrypt,
# structure check, marker and release rule apply to them.

dump_forever() {
    require_free_space forever || return 0
    run_dump forever forever '{"included":["ledger","gate_reports"]}' "" \
        -t public.ledger -t public.gate_reports
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
# `docker compose logs app-backup`) as well as into backup_runs.  Only nightly and weekly are
# ever pruned; forever/ and partitions/ are kept for good.

main() {
    mkdir -p "$BACKUPS_DIR"
    # Backstop for the FIFO/snapshot session (I5): close_snapshot is a no-op when nothing is
    # open (guarded on `_snap_pid`), so this is safe to fire on every ordinary exit as well as an
    # abnormal one -- a `docker compose down` inside its grace period, or a signal mid-dump,
    # otherwise leaks the FIFO and the two temp files for good.
    #
    # EXIT alone on the trap below: a signal trap that only runs a handler and returns does not
    # end the script -- execution resumes right where the signal landed, so an INT or TERM mid-
    # dump would clean up the snapshot and then carry on into snapshot_counts_json, dump_forever
    # or prune_units as if nothing happened (N1).  INT and TERM get their own handlers that clean
    # up and then exit explicitly, with the conventional 128+signal status.
    trap close_snapshot EXIT
    trap 'close_snapshot; exit 130' INT
    trap 'close_snapshot; exit 143' TERM
    lock_or_skip "${1:-dump}" || return 0
    case "${1:-}" in
        nightly)
            require_free_space nightly || return 0
            run_dump nightly nightly "{\"data_excluded\":$EXCLUDED_JSON}" "" $EXCLUDE_ARGS
            dump_forever
            prune_units nightly "$KEEP_NIGHTLY"
            ;;
        weekly)
            require_free_space weekly || return 0
            run_dump weekly weekly "{\"data_excluded\":$EXCLUDED_JSON}" "" $EXCLUDE_ARGS
            prune_units weekly "$KEEP_WEEKLY"
            ;;
        forever)
            dump_forever
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
            echo "usage: dump.sh nightly|weekly|forever|partition <table> <partition>" >&2
            return 2
            ;;
    esac
}

# --- unit retention (ruling A-I9) -------------------------------------------------------------
# A unit is a stamp that has a plaintext, a ciphertext, or both.  A lone sidecar is not a unit:
# it is the crash orphan of a run that died between writing the sidecar and renaming the dump,
# and counting it would let it hold a keep slot for good.  Units are taken newest-first (the
# basic-format UTC stamp sorts lexically) and only those past the keep window are considered.
#
# A unit is released only when the marker beside it exists.  The marker is written by
# `harness backup-encrypt` after the ciphertext passed its structure check, and it is the whole
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

    _stamps=$(find "$_dir" -maxdepth 1 -type f \
        \( -name "harness-$_kind-*.dump" -o -name "harness-$_kind-*.dump.age" \) \
        | sed -e 's#^.*/##' -e "s#^harness-$_kind-##" -e 's#\.dump\.age$##' -e 's#\.dump$##' \
        | sort -ru)

    _n=0
    for _stamp in $_stamps; do
        _n=$((_n + 1))
        [ "$_n" -gt "$_keep" ] || continue
        BASE="$_dir/harness-$_kind-$_stamp"
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

# Sourceable: the deletion rule above is the only file-removing code in this phase, and a test
# that reaches it directly is worth more than a grep. In bash, BASH_SOURCE is the file being
# read and differs from $0 when the file is sourced; in dash it is unset, so the fallback makes
# this always true and the script runs as it always did.
if [ "${BASH_SOURCE:-$0}" = "$0" ]; then
    main "$@"
fi
