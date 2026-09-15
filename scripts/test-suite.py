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
directory, from pytest's junit output; the committed `tests/test-durations.json` seeds a
host with no record), by test count until a record exists. A file heavier than one shard's
share is split into its recorded slowest tests, each launched by node id, plus the rest of
the file with those nodes deselected, so a new test in that file still runs.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_tree import release_tree  # noqa: E402  (scripts/release_tree.py, shared with the release script)

LOCK_SPAN = 1 << 20
# Linux open-file-description lock commands (fcntl exports them only when its build saw them).
F_OFD_GETLK, F_OFD_SETLK, F_OFD_SETLKW = 36, 37, 38
DEFAULT_SHARDS = 6
# Files that need the base database's fixture grant (UPDATE (indisvalid) ON pg_catalog.pg_index):
# the shard databases carry no grant, so these never move off `<database>` (fix 71 learned it).
PINNED = ('tests/test_schema.py', 'tests/test_alembic.py', 'tests/test_migrate_heal.py')
SEED = Path('tests/test-durations.json')
TEST_DEF = re.compile(r'^\s*(?:async\s+)?def\s+test_', re.M)
SUMMARY = re.compile(r'^=+ (.*) in ([0-9.]+)s.*=+$', re.M)
COUNT = re.compile(r'(\d+) (\w+)')


class Shard(NamedTuple):
    database: str
    files: list  # pytest argument units, one string each (a unit may carry --deselect flags)


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


def split_units(path, cost, nodes, threshold):
    """One unit per file, unless the file outweighs a shard's share and its tests are recorded:
    then its slowest tests peel off by node id until the rest fits, and the rest runs with
    those nodes deselected."""
    own = sorted(((seconds, node) for node, seconds in nodes.items() if node.split('::')[0] == path),
                 reverse=True)
    peeled, remaining = [], cost
    for seconds, node in own:
        if remaining <= threshold:
            break
        peeled.append((node, seconds))
        remaining -= seconds
    if not peeled:
        return {path: cost}
    units = dict(peeled)
    units[' '.join([path, *(f'--deselect {node}' for node, _ in peeled)])] = max(remaining, 0.0)
    return units


def plan_shards(counts, shards, base, weights=None, nodes=None):
    """Pinned files on the base database, the rest balanced greedily (longest first) by
    recorded duration where one exists, else by test count scaled to the recorded mean."""
    weights, nodes = weights or {}, nodes or {}
    if len(base) > 60:
        raise SystemExit(f'test database name too long for shard suffixes: {base}')
    known = [weights[path] for path in counts if path in weights]
    per_test = (sum(known) / max(1, sum(counts[path] for path in counts if path in weights))) if known else 1.0
    # A file's cost is never less than its recorded tests add up to (a record written by an
    # older runner kept only the last shard's share of a split file).
    by_file = {}
    for node, seconds in nodes.items():
        by_file[node.split('::')[0]] = by_file.get(node.split('::')[0], 0.0) + seconds
    cost = {path: max(weights.get(path, counts[path] * per_test), by_file.get(path, 0.0)) for path in counts}
    shards = max(1, min(shards, len(counts)))
    threshold = sum(cost.values()) / shards
    plan = [Shard(base if k == 0 else f'{base}_p{k + 1}', []) for k in range(shards)]
    loads = [0.0] * shards
    for path in PINNED:
        if path in counts:
            plan[0].files.append(path)
            loads[0] += cost[path]
    units = {}
    for path in counts:
        if path not in PINNED:
            units.update(split_units(path, cost[path], nodes, threshold))
    for unit in sorted(units, key=lambda u: (-units[u], u)):
        k = loads.index(min(loads))
        plan[k].files.append(unit)
        loads[k] += units[unit]
    return [shard for shard in plan if shard.files]


def _numeric(table):
    return {k: float(v) for k, v in table.items() if isinstance(v, (int, float))} if isinstance(table, dict) else {}


def load_record(record, seed=SEED):
    """(per-file seconds, per-node seconds) from the state record, else the committed seed.
    A legacy flat record is per-file only."""
    for path in (record, seed):
        if path is None:
            continue
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if 'files' in data or 'nodes' in data:
            return _numeric(data.get('files', {})), _numeric(data.get('nodes', {}))
        return _numeric(data), {}
    return {}, {}


def record_durations(junit_paths, record):
    """Sum each shard's junit test times per file and per node, merged over the previous record."""
    files, nodes = load_record(record, seed=None)
    fresh = {}  # a split file's tests land in several shards' junit files: add them all up
    for path in junit_paths:
        try:
            root = ElementTree.parse(path).getroot()
        except (OSError, ElementTree.ParseError):
            continue
        for case in root.iter('testcase'):
            module = case.get('classname', '').split('.')
            classes = []
            while module and module[-1][:1].isupper():
                classes.insert(0, module.pop())
            if module:
                key = '/'.join(module) + '.py'
                seconds = float(case.get('time', 0) or 0)
                fresh[key] = fresh.get(key, 0.0) + seconds
                nodes['::'.join([key, *classes, case.get('name', '')])] = seconds
    files.update(fresh)
    record = Path(record)
    temporary = record.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'files': files, 'nodes': nodes}, indent=1, sort_keys=True) + '\n')
    os.replace(temporary, record)


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
    if args:
        shards = [Shard(name, args)]
    else:
        weights, nodes = load_record(durations)
        shards = plan_shards(discover(), int(os.environ.get('TEST_SHARDS', DEFAULT_SHARDS)), name, weights, nodes)
    with lockpath.open('a+') as lock:
        print(f'Waiting for the test-database slot(s): {", ".join(s.database for s in shards)}', flush=True)
        for shard in shards:
            acquire(lock.fileno(), shard.database)
        receipt = {'branch': branch, 'database': name,
                   'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                   'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip(),
                   'release_tree': release_tree(run=subprocess.check_output),
                   'started_at': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(),
                   'dirty_before': subprocess.check_output(['git', 'status', '--porcelain'], text=True),
                   'scope': args, 'pytest_addopts': os.environ.get('PYTEST_ADDOPTS', ''),
                   'pytest_plugins': os.environ.get('PYTEST_PLUGINS', '')}
        print(json.dumps(receipt), flush=True)
        children, logs, junits = [], [], []

        def forward(sig, _frame):
            for child in children:
                os.killpg(child.pid, sig)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, forward)
        for k, shard in enumerate(shards):
            url = subprocess.check_output([sys.executable, 'scripts/testdb.py', shard.database], text=True).strip()
            command = [sys.executable, '-m', 'pytest', '-o', 'addopts=', '-ra']
            if len(shards) > 1:
                junits.append(cache / f'test-{name}-shard{k + 1}.xml')
                command += ['--junit-xml', str(junits[-1])]
            for unit in shard.files:
                command += unit.split(' ')
            env = {**os.environ, 'DATABASE_URL_TEST': url, 'PYTHONPATH': '.'}
            if len(shards) == 1:
                stdout = None
            else:
                stdout = (cache / f'test-{name}-shard{k + 1}.log').open('w')
                logs.append(stdout)
            children.append(subprocess.Popen(command, env=env, stdout=stdout, stderr=stdout,
                                             start_new_session=True, pass_fds=(lock.fileno(),)))
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
            # pytest exits 0 (passed) or 1 (failures) after a complete run; anything else was
            # interrupted or crashed and its partial timings would skew the next plan.
            if all(code in (0, 1) for code in codes):
                record_durations(junits, durations)
        # A scoped run keeps its own receipt so it never replaces the full-suite (release) receipt.
        (cache / f'test-{name}{"-scoped" if args else ""}.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps({k: v for k, v in receipt.items() if k != 'shards'}), flush=True)
        if len(shards) > 1:
            print(combined_summary(outputs, len(shards)), flush=True)
        return receipt['exit_code']


if __name__ == '__main__':
    sys.exit(main())
