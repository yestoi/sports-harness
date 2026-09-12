#!/usr/bin/env python3
"""Record six hours of migration health locally; never restart services."""
import datetime
import json
from pathlib import Path
import subprocess
import time
import urllib.request

root = Path('/srv/sports-harness')
output = root / 'migration/observation.jsonl'
deadline = time.monotonic() + 6 * 3600
while time.monotonic() < deadline:
    record = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        with urllib.request.urlopen('http://127.0.0.1:8180/healthz', timeout=15) as response:
            record['health'] = json.load(response)
    except Exception as exc:
        record['health_error'] = str(exc)
    for name, command in {
        'containers': ['docker', 'stats', '--no-stream', '--format', '{{json .}}'],
        'memory': ['free', '-m'],
        'disk': ['df', '-B1', str(root)],
        'executor': [str(root/'sports-compose'), 'exec', '-T', 'postgres', 'psql', '-XqAt',
                     '-U', 'harness', '-d', 'harness', '-c',
                     "SET statement_timeout='5s'; select row_to_json(h) from exec_heartbeat h"],
    }.items():
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=20, cwd=root)
            record[name] = {'status': result.returncode, 'output': result.stdout,
                            'error': result.stderr}
        except Exception as exc:
            record[name] = {'error': str(exc)}
    with output.open('a') as handle:
        handle.write(json.dumps(record) + '\n')
    time.sleep(min(300, max(0, deadline-time.monotonic())))
