#!/usr/bin/env bash
# One-shot session preflight for the autopilot. Read-only; prints labelled blocks.
# Usage: scripts/preflight.sh   (from the repo root, on the Mac)
set -u
NAS_IP=$(grep '^NAS_IP=' .env.nas | cut -d= -f2)
NAS_USER=$(grep '^NAS_USER=' .env.nas | cut -d= -f2)
NAS_STACK=$(grep '^NAS_STACK_DIR=' .env.nas | cut -d= -f2)
HOST="$NAS_USER@$NAS_IP"
say() { printf '\n== %s\n' "$1"; }

say "clock"; TZ=America/Chicago date '+%F %T %Z'; date -u '+%FT%TZ (mac utc)'
say "git"; git branch --show-current; git status --porcelain | wc -l | sed 's/^/dirty files: /'; git log --oneline -3
say "mac"; pmset -g 2>/dev/null | grep -E '^\s*sleep' || echo "pmset: n/a"; pgrep -fl caffeinate | head -1 || true
say "test db"; docker ps --format '{{.Names}} {{.Status}}' | grep harness-pg-test || echo "harness-pg-test NOT running"
printf 'pytest processes: %s\n' "$(pgrep -f pytest | wc -l | tr -d ' ')"
say "secrets"; ls -l secrets/ | awk 'NR>1{print $1, $NF}'; test -e .env.nas && echo ".env.nas present"
say "paper posture"
test ! -e secrets/legal_decision && ! grep -nE '^LIVE_TRADING=[^0]' deploy/nas.env .env.nas && ! grep -rn 'mode: *live' harness/ deploy/ && echo "paper posture intact" || echo "PAPER POSTURE CHECK FAILED"
say "tunnel"; pgrep -fl 'ssh -N -L 8180' | head -1 || echo "no tunnel process"
printf 'localhost:8180 -> %s\n' "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://localhost:8180/healthz)"
say "nas"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "date -u '+%FT%TZ (nas utc)'; cd $NAS_STACK && docker compose ps --format '{{.Name}} {{.Status}}'; curl -s http://127.0.0.1:8180/healthz; echo; df -h /volume1 | tail -1; free -m | awk 'NR==2{print \"mem available MB:\", \$7}'"
DEPLOYED=$(ssh -o BatchMode=yes "$HOST" 'curl -s http://127.0.0.1:8180/healthz' | python3 -c 'import json,sys;print(json.load(sys.stdin).get("build","?"))' 2>/dev/null)
say "deployed $DEPLOYED vs main $(git rev-parse --short main)"
git diff --stat "$DEPLOYED"..main -- . ':!docs' ':!*.md' 2>/dev/null | tail -3 || echo "(stamp not in history)"
say "game window (in_progress | kickoff -4h..+15m | nfl kickoff 60-100m) and tick ages"
ssh -o BatchMode=yes "$HOST" "cd $NAS_STACK && docker compose exec -T postgres psql -U harness -d harness -At -F ' | '" <<'SQL'
select (select count(*) from games where status='in_progress'),
       (select count(*) from games where kickoff_utc between now()-interval '4 hours' and now()+interval '15 minutes'),
       (select count(*) from games where sport='nfl' and kickoff_utc between now()+interval '60 minutes' and now()+interval '100 minutes');
select 'last real tick', id, status, round(extract(epoch from now()-started_at)) as age_s from runs where status<>'skipped' order by id desc limit 1;
select 'last heartbeat', id, status, round(extract(epoch from now()-started_at)) as age_s from runs order by id desc limit 1;
select 'ws last event age s', round(extract(epoch from now()-ts)) from orderbook_events order by id desc limit 1;
SQL
