#!/usr/bin/env python3
"""Suite runner: one lock per test database, sharded full suites, one receipt per run.

The lock is a byte range of the shared lock inode keyed by the database name (open file
description locks, so the pytest child inherits it through the passed descriptor and keeps
it if this supervisor is killed). Runs on different branch databases proceed concurrently;
two runs on the same database serialize. A full suite (no arguments) splits the test files
across TEST_SHARDS processes (default 6): shard 1 keeps the branch database and always owns
the schema and Alembic tests, which need that database's fixture grant and sibling
databases; the others use `<database>_p<k>`, created on demand. Files are balanced by the
per-file durations the previous sharded run recorded (`test-durations.json` in the state
directory, from pytest's junit output), by test count until a record exists.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
import zlib
from datetime import datetime, timezone
from typing import NamedTuple

LOCK_SPAN = 1 << 20
# Linux open-file-description lock commands (fcntl exports them only when its build saw them).
F_OFD_GETLK, F_OFD_SETLK, F_OFD_SETLKW = 36, 37, 38
DEFAULT_SHARDS = 6
PINNED = ('tests/test_schema.py', 'tests/test_alembic.py')
TEST_DEF = re.compile(r'^\s*(?:async\s+)?def\s+test_', re.M)
SUMMARY = re.compile(r'^=+ (.*) in ([0-9.]+)s.*=+$', re.M)
COUNT = re.compile(r'(\d+) (\w+)')


class Shard(NamedTuple):
    database: str
    files: list


def lock_offset(name):
    return zlib.crc32(name.encode()) % LOCK_SPAN


def acquire(fd, name):
    """Block until this database's byte of the lock file is ours (open-file-description lock)."""
    fcntl.fcntl(fd, F_OFD_SETLKW,
                struct.pack('hhqqi', fcntl.F_WRLCK, os.SEEK_SET, lock_offset(name), 1, 0) + b'\0' * 4)


def count_tests(path):
    return len(TEST_DEF.findall(Path(path).read_text()))


def discover():
    return {str(path): count_tests(path) for path in sorted(Path('tests').glob('test_*.py'))}


def plan_shards(counts, shards, base, weights=None):
    """Pinned files on the base database, the rest balanced greedily (longest first) by
    recorded duration where one exists, else by test count scaled to the recorded mean."""
    weights = weights or {}
    known = [weights[path] for path in counts if path in weights]
    per_test = (sum(known) / max(1, sum(counts[path] for path in counts if path in weights))) if known else 1.0
    cost = {path: weights.get(path, counts[path] * per_test) for path in counts}
    shards = max(1, min(shards, len(counts)))
    plan = [Shard(base if k == 0 else f'{base}_p{k + 1}'[:63], []) for k in range(shards)]
    loads = [0.0] * shards
    for path in PINNED:
        if path in counts:
            plan[0].files.append(path)
            loads[0] += cost[path]
    rest = sorted((path for path in counts if path not in PINNED), key=lambda p: (-cost[p], p))
    for path in rest:
        k = loads.index(min(loads))
        plan[k].files.append(path)
        loads[k] += cost[path]
    return [shard for shard in plan if shard.files]


def load_weights(record):
    try:
        weights = json.loads(Path(record).read_text())
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in weights.items() if isinstance(v, (int, float))}


def record_durations(junit_paths, record):
    """Sum each shard's junit test times per file and merge them over the previous record."""
    durations = load_weights(record)
    for path in junit_paths:
        try:
            root = ElementTree.parse(path).getroot()
        except (OSError, ElementTree.ParseError):
            continue
        fresh = {}
        for case in root.iter('testcase'):
            module = case.get('classname', '').split('.')
            while module and module[-1][:1].isupper():
                module.pop()
            if module:
                key = '/'.join(module) + '.py'
                fresh[key] = fresh.get(key, 0.0) + float(case.get('time', 0) or 0)
        durations.update(fresh)
    Path(record).write_text(json.dumps(durations, indent=2, sort_keys=True) + '\n')


def combined_summary(outputs, shards):
    totals, seconds = {}, 0.0
    for text in outputs:
        found = SUMMARY.findall(text)
        if not found:
            totals['unsummarized shard'] = totals.get('unsummarized shard', 0) + 1
            continue
        counts, elapsed = found[-1]
        seconds = max(seconds, float(elapsed))
        for number, label in COUNT.findall(counts):
            totals[label] = totals.get(label, 0) + int(number)
    order = ['failed', 'error', 'errors', 'passed', 'skipped', 'xfailed', 'xpassed']
    parts = [f'{totals[k]} {k}' for k in order if k in totals]
    parts += [f'{v} {k}' for k, v in totals.items() if k not in order]
    return f"===== {', '.join(parts)} in {seconds:.2f}s ({shards} shards, wall of the slowest) ====="


def main():
    cache = Path(os.environ.get('SPORTS_TEST_STATE_DIR', str(Path.home() / '.cache/sports-harness/test-state')))
    cache.mkdir(parents=True, exist_ok=True)
    # Workers get only a bind of this existing inode; receipts live in their private tmpfs.
    # The controller keeps both defaults in its own inaccessible cache directory.
    lockpath = Path(os.environ.get('SPORTS_TEST_LOCK_FILE', str(cache / 'test-suite.lock')))
    args = sys.argv[1:]
    if args[:1] == ['--']:
        args = args[1:]
    branch = subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip()
    if not branch:
        raise SystemExit('Tests require a named branch')
    name = os.environ.get('TEST_DB') or 'harness_test_' + ''.join(
        c if c in 'abcdefghijklmnopqrstuvwxyz0123456789' else '_' for c in branch)
    durations = cache / 'test-durations.json'
    shards = [Shard(name, args)] if args else plan_shards(
        discover(), int(os.environ.get('TEST_SHARDS', DEFAULT_SHARDS)), name, load_weights(durations))
    with lockpath.open('a+') as lock:
        print(f'Waiting for the test-database slot(s): {", ".join(s.database for s in shards)}', flush=True)
        for shard in shards:
            acquire(lock.fileno(), shard.database)
        receipt = {'branch': branch, 'database': name,
                   'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                   'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
                   'started_at': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(),
                   'dirty_before': subprocess.check_output(['git', 'status', '--porcelain'], text=True),
                   'scope': args, 'pytest_addopts': os.environ.get('PYTEST_ADDOPTS', ''),
                   'pytest_plugins': os.environ.get('PYTEST_PLUGINS', '')}
        print(json.dumps(receipt), flush=True)
        children, logs, junits = [], [], []
        for k, shard in enumerate(shards):
            url = subprocess.check_output([sys.executable, 'scripts/testdb.py', shard.database], text=True).strip()
            command = [sys.executable, '-m', 'pytest', '-o', 'addopts=', '-ra']
            if len(shards) > 1:
                junits.append(cache / f'test-{name}-shard{k + 1}.xml')
                command += ['--junit-xml', str(junits[-1])]
            command += shard.files
            env = {**os.environ, 'DATABASE_URL_TEST': url, 'PYTHONPATH': '.'}
            if len(shards) == 1:
                stdout = None
            else:
                stdout = (cache / f'test-{name}-shard{k + 1}.log').open('w')
                logs.append(stdout)
            children.append(subprocess.Popen(command, env=env, stdout=stdout, stderr=stdout,
                                             start_new_session=True, pass_fds=(lock.fileno(),)))

        def forward(sig, _frame):
            for child in children:
                os.killpg(child.pid, sig)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, forward)
        codes = [child.wait() for child in children]
        receipt['exit_code'] = next((code for code in codes if code), 0)
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        receipt['head_after'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
        receipt['dirty_after'] = subprocess.check_output(['git', 'status', '--porcelain'], text=True)
        if len(shards) > 1:
            outputs = []
            for k, (shard, code, log) in enumerate(zip(shards, codes, logs)):
                log.close()
                text = Path(log.name).read_text()
                outputs.append(text)
                print(f'===== shard {k + 1}/{len(shards)} {shard.database} exit {code}: {" ".join(shard.files)}')
                print(text, end='' if text.endswith('\n') else '\n')
            receipt['shards'] = [{'database': shard.database, 'files': shard.files, 'exit_code': code,
                                  'log': log.name} for shard, code, log in zip(shards, codes, logs)]
            record_durations(junits, durations)
        (cache / f'test-{name}.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps({k: v for k, v in receipt.items() if k != 'shards'}), flush=True)
        if len(shards) > 1:
            print(combined_summary(outputs, len(shards)), flush=True)
        return receipt['exit_code']


if __name__ == '__main__':
    sys.exit(main())
