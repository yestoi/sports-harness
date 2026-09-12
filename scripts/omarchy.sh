#!/usr/bin/env bash
# Read-only operational commands for the migrated runtime.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/omarchy/host.env
host="$NAS_USER@$NAS_IP"
case "${1:-status}" in
  status)
    ssh -o BatchMode=yes "$host" "cd $NAS_STACK_DIR && ./sports-compose ps --all; curl -sS --max-time 30 -w ' [%{http_code}]\n' http://127.0.0.1:8180/healthz"
    ;;
  logs)
    exec ssh -o BatchMode=yes "$host" "cd $NAS_STACK_DIR && ./sports-compose logs -f --tail 50"
    ;;
  tunnel)
    exec ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -N -L 8180:127.0.0.1:8180 "$host"
    ;;
  ssh)
    exec ssh -t "$host" "cd $NAS_STACK_DIR && exec bash -l"
    ;;
  sql)
    exec ssh -o BatchMode=yes "$host" "cd $NAS_STACK_DIR && ./sports-compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U harness -d harness"
    ;;
  preflight)
    ssh -o BatchMode=yes "$host" "date -u; findmnt /srv/sports-harness; df -h /srv/sports-harness; free -m; cd $NAS_STACK_DIR && ./sports-compose ps --all"
    "$0" sql <<'SQL'
SET statement_timeout='30s';
select version_num from alembic_version;
select count(*) as live_games from games where status='in_progress';
select id, started_at, status, build_sha from runs order by id desc limit 5;
select last_loop_at, last_loop_ms, last_error from exec_heartbeat;
select id, ts from orderbook_events order by id desc limit 1;
SQL
    ;;
  *) echo "usage: $0 {status|logs|tunnel|ssh|sql|preflight}" >&2; exit 2 ;;
esac
