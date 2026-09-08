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
# production database holds.  Roadmap invariant 5 is untouched: the restore target is a
# throwaway postgres:16 container with the anonymous data volume the image declares, which
# `docker run --rm` removes with the container.  No named volume, no host path, nothing on the
# production cluster created or dropped; the only production statements are the SELECTs that
# count rows.  Proof that the *ciphertext* decrypts is the Mac half and lives elsewhere: the
# private key is not on the NAS.
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
# How the production counts are read.  Read-only by construction: the only statement sent is a
# SELECT count(*).  Overridable so the drill can be pointed at an already-running psql.
PROD_PSQL="${PROD_PSQL:-docker compose exec -T postgres psql -U harness -d harness -qAt}"

BULK="raw_responses orderbook_events venue_trades venue_quotes odds_snapshots"

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

# Every ordinary, non-partition table the restored dump carries, minus the five bulk tables
# whose data was deliberately excluded (their schema restores, so they are present and empty).
tables=$(drill_psql "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace \
where n.nspname = 'public' and c.relkind = 'r' and c.relispartition = false order by 1")

if ! $PROD_PSQL -c "select 1" >/dev/null 2>&1; then
    echo "ERROR cannot read the production database for the comparison; run this from the" >&2
    echo "      stack directory on the NAS, or set PROD_PSQL" >&2
    exit 1
fi

echo "table restored production"
mismatches=0
compared=0
for t in $tables; do
    skip=0
    for b in $BULK; do
        [ "$t" != "$b" ] || skip=1
    done
    [ "$skip" -eq 0 ] || continue
    here=$(drill_psql "select count(*) from public.\"$t\"")
    there=$($PROD_PSQL -c "select count(*) from public.\"$t\"" | tr -d '\r')
    compared=$((compared + 1))
    if [ "$here" = "$there" ]; then
        echo "$t $here $there"
    else
        echo "$t $here $there MISMATCH"
        mismatches=$((mismatches + 1))
    fi
done

# The verdict is the output, not the exit status.  A dump is a point-in-time snapshot and
# production keeps recording, so a MISMATCH on a live table means the counts moved on, not that
# the restore is bad; what the operator is looking for is a table that came back empty or
# missing.  The exit status stays 0 so a difference here never reads as a failed drill --
# `harness backup-drill-record --rows-match/--no-rows-match` is where the verdict is filed.
echo "COMPARED $compared MISMATCHES $mismatches"
if [ "$compared" -gt 0 ] && [ "$mismatches" -eq 0 ]; then
    echo "ROWS_MATCH true"
else
    echo "ROWS_MATCH false"
fi
