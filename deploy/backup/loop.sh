#!/bin/sh
set -eu
set -f   # no pathname expansion anywhere in this script
# =============================================================================================
# The app-backup sidecar's whole program (addendum 4.1 and 4.2).  POSIX sh on postgres:16.
#
# The container runs TZ=UTC like every other service in the stack, so the schedule is converted
# once per iteration with `date`, which is the only thing here that needs to know about
# America/Chicago and its two clock changes a year:
#
#   every day   03:30 CT   dump.sh nightly
#   Sundays     03:30 CT   dump.sh weekly, right after the nightly
#   Mondays     04:00 CT   dump.sh partition for each sealed partition of the previous ISO
#                          week that has no archive yet
#
# Nothing here deletes a file.  A failing dump is logged and the loop continues: the next
# window is more useful than a dead container, and compose would only restart it into the same
# failure.  Every skip is one line on stdout beginning "SKIP", which is what the controller
# reads with `docker compose logs app-backup`.
# =============================================================================================

SCHEDULE_TZ="${SCHEDULE_TZ:-America/Chicago}"
BACKUPS_DIR="${BACKUPS_DIR:-/backups}"
NIGHTLY_AT="${NIGHTLY_AT:-03:30}"
PARTITION_AT="${PARTITION_AT:-04:00}"
ARCHIVE_LOOKBACK_WEEKS="${ARCHIVE_LOOKBACK_WEEKS:-4}"

local_epoch() {
    # $1 = "today" | "tomorrow", $2 = "HH:MM" -- resolved in SCHEDULE_TZ, printed as UTC epoch
    # seconds.  GNU date handles the DST folds; sh arithmetic would not.
    TZ="$SCHEDULE_TZ" date -d "$1 $2" +%s
}

sleep_until() {
    _target=$1
    _now=$(date +%s)
    if [ "$_target" -gt "$_now" ]; then
        echo "INFO sleeping $((_target - _now))s until $(TZ="$SCHEDULE_TZ" date -d "@$_target" '+%F %T %Z')"
        sleep $((_target - _now))
    fi
}

# The previous ISO week's partition of each weekly-partitioned tape table.  Names match
# harness.db.schema._partition_name: <table>_y<isoyear>w<isoweek>, both from `date` (%G/%V).
#
# "that has no archive yet" is the whole reason this is a lookback and not a single week: the
# container that is down over a Monday 04:00 would otherwise leave a week with no archive and
# no second chance.  Weeks before partitioning began, and weeks already archived, cost one
# skipped line each.
archive_previous_week() {
    _w=1
    while [ "$_w" -le "$ARCHIVE_LOOKBACK_WEEKS" ]; do
        _week=$(date -u -d "$((7 * _w)) days ago" +y%Gw%V)
        for _table in orderbook_events venue_trades; do
            _part="${_table}_${_week}"
            if [ -n "$(find "$BACKUPS_DIR/partitions" -maxdepth 1 -type f \
                        -name "harness-partitions-${_part}-*.dump*" 2>/dev/null | head -n 1)" ]; then
                echo "SKIP partition $_part is already archived"
                continue
            fi
            /backup/dump.sh partition "$_table" "$_part" \
                || echo "ERROR partition archive of $_part exited $?"
        done
        _w=$((_w + 1))
    done
}

echo "INFO app-backup started; nightly $NIGHTLY_AT $SCHEDULE_TZ, weekly on Sunday, partition archive Monday $PARTITION_AT"

while :; do
    _next=$(local_epoch today "$NIGHTLY_AT")
    [ "$_next" -gt "$(date +%s)" ] || _next=$(local_epoch tomorrow "$NIGHTLY_AT")
    sleep_until "$_next"

    /backup/dump.sh nightly || echo "ERROR nightly dump exited $?"

    _dow=$(TZ="$SCHEDULE_TZ" date +%u)     # 1 = Monday ... 7 = Sunday, in the schedule's zone
    if [ "$_dow" -eq 7 ]; then
        /backup/dump.sh weekly || echo "ERROR weekly dump exited $?"
    fi
    if [ "$_dow" -eq 1 ]; then
        sleep_until "$(local_epoch today "$PARTITION_AT")"
        archive_previous_week
    fi
done
