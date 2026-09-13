#!/usr/bin/env python3
"""One host-wide suite slot, retained by pytest even if its supervisor is killed."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from datetime import datetime, timezone


def main():
    cache = Path(os.environ.get('SPORTS_TEST_STATE_DIR', str(Path.home() / '.cache/sports-harness/test-state')))
    cache.mkdir(parents=True, exist_ok=True)
    # Workers get only a bind of this existing inode; receipts live in their private tmpfs.
    # The controller keeps both defaults in its own inaccessible cache directory.
    lockpath = Path(os.environ.get('SPORTS_TEST_LOCK_FILE', str(cache / 'test-suite.lock')))
    with lockpath.open('a+') as lock:
        print('Waiting for the shared test-suite slot', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        branch = subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip()
        if not branch:
            raise SystemExit('Tests require a named branch')
        name = os.environ.get('TEST_DB') or 'harness_test_' + ''.join(
            c if c in 'abcdefghijklmnopqrstuvwxyz0123456789' else '_' for c in branch)
        url = subprocess.check_output([sys.executable, 'scripts/testdb.py', name], text=True).strip()
        receipt = {'branch': branch, 'database': name, 'head': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'started_at': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(),
            'dirty_before': subprocess.check_output(['git', 'status', '--porcelain'], text=True)}
        print(json.dumps(receipt), flush=True)
        args = sys.argv[1:]
        if args[:1] == ['--']:
            args = args[1:]
        receipt['scope'] = args
        receipt['pytest_addopts'] = os.environ.get('PYTEST_ADDOPTS', '')
        receipt['pytest_plugins'] = os.environ.get('PYTEST_PLUGINS', '')
        command = [sys.executable, '-m', 'pytest', '-o', 'addopts=', '-ra', *args]
        child = subprocess.Popen(command, env={**os.environ, 'DATABASE_URL_TEST': url,
                                               'PYTHONPATH': '.'},
                                 start_new_session=True, pass_fds=(lock.fileno(),))
        def forward(sig, _frame):
            os.killpg(child.pid, sig)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, forward)
        receipt['exit_code'] = child.wait()
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        receipt['head_after'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        receipt['dirty_after'] = subprocess.check_output(['git', 'status', '--porcelain'], text=True)
        (cache / f'test-{name}.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(receipt), flush=True)
        return receipt['exit_code']


if __name__ == '__main__':
    sys.exit(main())
