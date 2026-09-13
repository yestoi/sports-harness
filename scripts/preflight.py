#!/usr/bin/env python3
"""Read-only Linux controller checks; no secrets or automatic service recovery."""
import importlib.util
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone


def main():
    spec = importlib.util.spec_from_file_location('release', Path(__file__).with_name('release-omarchy.py'))
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    print(datetime.now(timezone.utc).isoformat(), flush=True)
    for command in (['git','status','--short'], ['git','log','-3','--oneline'],
                    ['git','worktree','list'], ['free','-m'], ['df','-h','/srv/sports-harness'],
                    ['docker','inspect','harness-pg-test','--format',
                     'test-db state={{.State.Status}} memory={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCpus}}'],
                    ['/srv/sports-harness/sports-compose','ps','--all']):
        subprocess.run(command, check=True)
    command=release.compose(release.RUNTIME/'docker-compose.yml', release.RUNTIME/'compose.omarchy.yml')
    config=json.loads(release.run([*command,'config','--format','json'], capture=True))
    release.validate_config(config)
    print('paper posture intact')
    print(json.dumps(release.health()))
    print(release.sql("""select version_num from alembic_version;
      select id,started_at,status,build_sha from runs order by id desc limit 3;
      select last_loop_at,last_loop_ms,p95_loop_ms,last_error from exec_heartbeat;
      select id,ts from orderbook_events order by id desc limit 1;
      select sport,status,count(*),min(kickoff_utc),max(kickoff_utc) from games
      where kickoff_utc between now()-interval '4 hours' and now()+interval '24 hours'
      group by sport,status;"""))
    due=Path.home()/'.cache/sports-harness/reminders'
    if due.exists():
        for p in sorted(due.glob('*.txt')):
            print('Durable reminder:', p.name, p.read_text().strip())
    print('Provider auth, native wakeups, browser and worker-isolation smoke must be checked in the controller session.')


if __name__ == '__main__':
    main()
