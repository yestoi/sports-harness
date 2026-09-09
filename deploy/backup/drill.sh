#!/bin/sh
set -eu
set -f   # no pathname expansion anywhere in this script
# =============================================================================================
# The NAS half of the restore drill (addendum 4.4).  Run by the controller from the stack
# directory on the NAS, not by the sidecar:
#
#     deploy/backup/drill.sh backups/nightly/harness-nightly-20260908T083000Z.dump
#
# It proves the plaintext restores and that the tables it carries hold the row counts the
# sidecar recorded when the dump was taken. The production database is not read at all.
# Roadmap invariant 5 is untouched: the restore target is a
# throwaway postgres:16 container with the anonymous data volume the image declares, which
# `docker run --rm` removes with the container.  No named volume, no host path, nothing on the
# production cluster created, dropped or read.  Proof that the *ciphertext* decrypts is the Mac
# half and lives elsewhere: the private key is not on the NAS.
# =============================================================================================

DUMP_FILE="${1:-}"
if [ -z "$DUMP_FILE" ]; then
    echo "usage: drill.sh <nightly .dump file>" >&2
    exit 2
fi
if [ ! -f "$DUMP_FILE" ]; then
    echo "ERROR no such dump file: $DUMP_FILE" >&2
    exit 1
fi

MIN_FREE_PCT="${MIN_FREE_PCT:-30}"
DRILL_DB="${DRILL_DB:-drill}"
DRILL_USER="${DRILL_USER:-drill}"

# The dump-time counts sidecar (addendum §0.7).  The comparison is against these, never against
# the live database: production keeps recording after the dump, so a live comparison reports a
# MISMATCH on every busy table and the verdict line means nothing.
META_FILE="${DUMP_FILE%.dump}.meta.json"
if [ ! -f "$META_FILE" ]; then
    echo "ERROR no sidecar beside the dump: $META_FILE" >&2
    echo "      the drill compares against dump-time counts, so it cannot run without one" >&2
    exit 1
fi

# The sidecar's "counts" object, one pair per line ("key":value), computed once so meta_count
# and meta_tables don't each re-parse the file.  POSIX sed/tr/grep only: the throwaway container
# has no jq and the NAS is not asked to grow one.
_meta_pairs() {
    tr -d ' \n' < "$META_FILE" \
        | sed -n 's/.*"counts":{\([^}]*\)}.*/\1/p' \
        | tr ',' '\n'
}

# One table's count out of the sidecar's "counts" object.  Prints nothing when the table is not
# in it (an excluded table, or a table created after the dump).  $1 is matched with grep -F
# (fixed string), not interpolated into a sed regex: a quoted Postgres identifier can legally
# contain characters -- '.', '*', '/' -- that are regex metacharacters (M5).
meta_count() {
    _meta_pairs | grep -F "\"$1\":" | sed 's/^"[^"]*"://'
}

# Every table name the sidecar recorded a count for -- what the dump actually carried, which is
# not necessarily every table the restore produced (a table missing from the restore is exactly
# what this drill exists to catch; see the comparison loop below).
meta_tables() {
    _meta_pairs | sed -n 's/^"\([^"]*\)":.*/\1/p'
}

COUNTS_SNAPSHOT=$(tr -d ' \n' < "$META_FILE" \
    | sed -n 's/.*"counts_snapshot":"\([^"]*\)".*/\1/p')
[ -n "$COUNTS_SNAPSHOT" ] || COUNTS_SNAPSHOT="same as dump"
echo "INFO counts_snapshot=$COUNTS_SNAPSHOT"

free_pct=$(df -P "$(dirname "$DUMP_FILE")" | awk 'NR == 2 { gsub(/%/, "", $5); print 100 - $5 }')
if [ -z "$free_pct" ] || [ "$free_pct" -lt "$MIN_FREE_PCT" ]; then
    echo "SKIP drill free=${free_pct}% below MIN_FREE_PCT=${MIN_FREE_PCT}%"
    exit 0
fi

CID=""
cleanup() {
    if [ -n "$CID" ]; then
        docker stop "$CID" >/dev/null 2>&1 || true
        CID=""
    fi
}
trap cleanup EXIT HUP INT TERM

DRILL_PASSWORD=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
CID=$(docker run --rm -d \
    -e POSTGRES_PASSWORD="$DRILL_PASSWORD" \
    -e POSTGRES_USER="$DRILL_USER" \
    -e POSTGRES_DB="$DRILL_DB" \
    postgres:16)
echo "INFO throwaway container ${CID}"

ready=0
i=0
while [ "$i" -lt 90 ]; do
    if docker exec "$CID" pg_isready -U "$DRILL_USER" -d "$DRILL_DB" >/dev/null 2>&1; then
        ready=1
        break
    fi
    i=$((i + 1))
    sleep 2
done
if [ "$ready" -ne 1 ]; then
    echo "ERROR the throwaway container never became ready" >&2
    exit 1
fi

docker cp "$DUMP_FILE" "$CID:/tmp/drill.dump"
# --no-owner/--no-privileges: the harness role does not exist in the throwaway.  No --clean and
# no --create, so pg_restore only adds objects to the empty database the image made.
if docker exec "$CID" pg_restore --no-owner --no-privileges \
        -U "$DRILL_USER" -d "$DRILL_DB" /tmp/drill.dump; then
    echo "RESTORE_OK $DUMP_FILE"
else
    echo "ERROR pg_restore failed for $DUMP_FILE" >&2
    exit 1
fi

drill_psql() { docker exec "$CID" psql -U "$DRILL_USER" -d "$DRILL_DB" -qAt -c "$1"; }

# A count comes back as a plain string from both the sidecar and psql; guarding it as a digit
# string before using it in an arithmetic test avoids an opaque "sh: bad number" abort under
# `set -e` if either one is ever not a clean integer (M6).
is_int() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }

# Iterate the sidecar's own count keys, not the tables the restore happens to produce (I4): a
# table the dump carried but the restore is missing entirely must be a mismatch, and it can
# never appear by walking the restored database's own table list -- it is not there to walk to.
echo "table restored dump_time"
mismatches=0
compared=0
missing=0
for t in $(meta_tables); do
    there=$(meta_count "$t")
    if [ -z "$there" ]; then
        # Not reachable in practice (t came from the same object meta_count reads), kept as a
        # defensive branch so a future sidecar shape change fails as NO_COUNT, not silently.
        echo "$t - - NO_DUMP_TIME_COUNT"
        missing=$((missing + 1))
        continue
    fi
    _exists=$(drill_psql "select to_regclass('public.$t') is not null")
    if [ "$_exists" != "t" ]; then
        echo "$t MISSING $there MISSING_TABLE"
        compared=$((compared + 1))
        mismatches=$((mismatches + 1))
        continue
    fi
    here=$(drill_psql "select count(*) from public.\"$t\"")
    compared=$((compared + 1))
    if [ "$here" = "$there" ]; then
        echo "$t $here $there"
    elif [ "$COUNTS_SNAPSHOT" = "before dump" ] && is_int "$here" && is_int "$there" \
            && [ "$here" -gt "$there" ]; then
        # The labelled fallback: the counts predate the dump's own snapshot, so a busy table
        # legitimately restores with more rows than the sidecar records -- never fewer.
        echo "$t $here $there GREW"
    else
        echo "$t $here $there MISMATCH"
        mismatches=$((mismatches + 1))
    fi
done

# The verdict is the output, not the exit status.  It is now a real verdict: both numbers are
# from the same instant, so a MISMATCH means the restore does not carry what the dump carried,
# which is exactly what the drill exists to catch.  A table with no dump-time count is reported
# and excluded from the verdict rather than silently passed.
echo "COMPARED $compared MISMATCHES $mismatches NO_COUNT $missing"
if [ "$compared" -gt 0 ] && [ "$mismatches" -eq 0 ]; then
    echo "ROWS_MATCH true"
else
    echo "ROWS_MATCH false"
fi
