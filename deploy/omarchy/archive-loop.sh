#!/bin/sh
set -u
while :; do
    if /usr/local/bin/pull-backups > /volume1/docker/sports-archive/last-pull.log 2>&1; then
        printf '%s backup pull completed\n' "$(date -u '+%FT%TZ')"
    else
        printf '%s backup pull FAILED; see last-pull.log\n' "$(date -u '+%FT%TZ')" >&2
    fi
    sleep 900 &
    wait "$!"
done
