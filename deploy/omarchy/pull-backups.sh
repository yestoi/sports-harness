#!/bin/sh
# Run as trey on NAS. No deletion: NAS retains independent backup history.
set -eu
umask 077
cd /volume1/docker/sports-archive
exec 9>.pull.lock
flock -n 9 || exit 0
rsync -rt --partial-dir=.partial --timeout=120 \
  -e 'ssh -i /volume1/docker/sports-archive/access/pull-key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/volume1/docker/sports-archive/access/known_hosts -o ConnectTimeout=10' \
  trey@192.168.12.127:nightly trey@192.168.12.127:weekly \
  trey@192.168.12.127:partitions trey@192.168.12.127:forever encrypted/
date -u '+%Y-%m-%dT%H:%M:%SZ' > last-success.txt.tmp
mv last-success.txt.tmp last-success.txt
