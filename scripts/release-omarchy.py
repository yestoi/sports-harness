#!/usr/bin/env python3
"""Deploy a clean committed main on Omarchy, preserving runtime config and image rollback.

Run locally on Omarchy. `--plan` validates eligibility/config without changing services.
PostgreSQL/backup service changes require a separate reviewed infrastructure procedure.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo
import yaml

RUNTIME = Path('/srv/sports-harness')
APPS = ['app-run', 'app-serve', 'app-exec', 'app-research']
FULL_PATHS = ['harness/recorder/ws_sink.py', 'harness/venues/kalshi/ws.py',
              'harness/venues/kalshi/rfq*.py', 'harness/matching',
              'harness/db', 'harness/variants', 'migrations', 'docker-compose.yml', 'Dockerfile',
              'pyproject.toml', 'constraints.txt', 'deploy/backup']


def run(args, *, capture=False, cwd=None, input=None):
    return subprocess.run([str(a) for a in args], check=True, cwd=cwd, input=input,
                          text=True, stdout=subprocess.PIPE if capture else None).stdout


def git(*args):
    return run(['git', *args], capture=True).strip()


def health():
    try:
        response = urllib.request.urlopen('http://127.0.0.1:8180/healthz', timeout=15)
    except urllib.error.HTTPError as error:
        if error.code != 503:
            raise
        response = error
    with response:
        data = json.load(response)
    if not isinstance(data.get('build'), str) or not data['build']:
        raise RuntimeError('No deployment stamp available')
    return data


def compose(base, override):
    return ['docker', 'compose', '--project-name', 'sports-harness',
            '--project-directory', str(RUNTIME), '--env-file', str(RUNTIME / '.env'),
            '-f', str(base), '-f', str(override)]


def sql(query):
    return run([RUNTIME/'sports-compose', 'exec', '-T', 'postgres', 'psql', '-XqAt',
                '-v', 'ON_ERROR_STOP=1', '-U', 'harness', '-d', 'harness'], capture=True,
               input="BEGIN READ ONLY; SET LOCAL statement_timeout='10s';\n" + query + '\nCOMMIT;\n')


def window_allowed(mode, now, row):
    # Journal 128: app-only college windows Thu/Fri/Sat; never an NFL window.
    college_exception = mode == 'app' and now.astimezone(ZoneInfo('America/Chicago')).weekday() in (3, 4, 5)
    return not row['blocked'] or (college_exception and not row['nfl_blocked'])


def game_window(mode):
    row = json.loads(sql("""select json_build_object(
      'blocked', exists(select 1 from games where status='in_progress'
        or kickoff_utc between now()-interval '4 hours' and now()+interval '15 minutes'
        or (sport='nfl' and kickoff_utc between now()+interval '60 minutes' and now()+interval '100 minutes')),
      'nfl_blocked', exists(select 1 from games where sport='nfl' and
        (status='in_progress' or kickoff_utc between now()-interval '4 hours' and now()+interval '15 minutes'
         or kickoff_utc between now()+interval '60 minutes' and now()+interval '100 minutes')));"""))
    if not window_allowed(mode, datetime.now(timezone.utc), row):
        raise RuntimeError(f'Deploy window closed: {row}')
    return row


def validate_config(config):
    services = config['services']
    for name in APPS + ['app-ws']:
        env = services[name].get('environment', {})
        if str(env.get('LIVE_TRADING')) != '0' or env.get('HARNESS_MODE') != 'paper':
            raise RuntimeError(f'Paper posture invalid for {name}')
        if str(env.get('DB_BUDGET_GB')) != '600':
            raise RuntimeError(f'Runtime DB capacity alert budget changed for {name}')
        if str(env.get('RFQ_LISTENER_ENABLED')) != '0':
            raise RuntimeError(f'RFQ must stay disabled for {name}')
    if '@sha256:' not in services['postgres']['image']:
        raise RuntimeError('PostgreSQL image must remain digest-pinned')
    if (RUNTIME/'migration-retired').exists() or not (RUNTIME/'migration/production-enabled').exists():
        raise RuntimeError('Runtime is not the enabled Omarchy production stack')
    free = shutil.disk_usage(RUNTIME)
    if free.free / free.total < .25:
        raise RuntimeError('Runtime filesystem is below 25% free')


def expected_services(config, services):
    result = {}
    for service in services:
        entry = config['services'][service]
        result[service] = {'image': run(['docker','image','inspect',entry['image'],
                              '--format','{{.Id}}'], capture=True).strip(),
                           'build': entry.get('environment', {}).get('BUILD_SHA')}
    return result


def wait_healthy(sha, services, timeout=360, expected=None):
    if expected is None:
        config = json.loads(run([*compose(RUNTIME/'docker-compose.yml', RUNTIME/'compose.omarchy.yml'),
                                 'config','--format','json'], capture=True))
        expected = expected_services(config, services)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            h = health()
            output = run([RUNTIME/'sports-compose', 'ps', '--format', 'json'], capture=True)
            rows = json.loads(output) if output.lstrip().startswith('[') else [json.loads(l) for l in output.splitlines() if l]
            by_name = {r['Service']: r for r in rows}
            ready = all(by_name.get(s, {}).get('State') == 'running' for s in services)
            ready &= all(by_name.get(s, {}).get('Health') == 'healthy' for s in ('app-exec', 'app-serve', 'postgres'))
            for service in services:
                cid = run([RUNTIME/'sports-compose', 'ps', '-q', service], capture=True).strip()
                container = json.loads(run(['docker','inspect',cid], capture=True))[0]
                env = dict(item.split('=',1) for item in container['Config']['Env'] if '=' in item)
                ready &= env.get('BUILD_SHA') == expected[service]['build']
                ready &= container['Image'] == expected[service]['image']
            tick = json.loads(sql("select row_to_json(r) from (select build_sha,status from runs where status in ('ok','skipped') and finished_at is not null order by id desc limit 1) r;"))
            ready &= isinstance(tick, dict) and tick.get('build_sha') == sha
            if h.get('build') == sha and h.get('status') == 'ok' and ready:
                return h
        except (OSError, ValueError, subprocess.CalledProcessError):
            pass
        time.sleep(5)
    raise RuntimeError('New release did not reach expected stamp and health in six minutes')


def atomic_copy(source, dest):
    temporary = dest.with_suffix(dest.suffix + '.next')
    shutil.copy2(source, temporary)
    os.replace(temporary, dest)


def deploy(mode, plan=False):
    if git('branch', '--show-current') != 'main' or git('status', '--porcelain'):
        raise RuntimeError('Release requires clean main (including untracked files)')
    sha = git('rev-parse', '--short', 'HEAD')
    head = git('rev-parse', 'HEAD')
    old = health()['build']
    if old == sha and not plan:
        raise RuntimeError('This SHA is already deployed; verify it instead of redeploying')
    git('cat-file', '-e', f'{old}^{{commit}}')
    infrastructure = git('diff', '--name-only', f'{old}..HEAD', '--', 'deploy/backup', 'deploy/backup_age.pub').splitlines()
    if infrastructure:
        raise RuntimeError(f'Backup bind-mount changes need a separate infrastructure release: {infrastructure}')
    touched = git('diff', '--name-only', f'{old}..HEAD', '--', *FULL_PATHS).splitlines()
    if mode == 'app' and touched:
        raise RuntimeError(f'Full release required by: {touched}')
    existing = compose(RUNTIME/'docker-compose.yml', RUNTIME/'compose.omarchy.yml')
    before = json.loads(run([*existing, 'config', '--format', 'json'], capture=True))
    validate_config(before)
    window = game_window(mode)
    changed = APPS + (['app-ws'] if mode == 'full' else [])
    print(json.dumps({'sha': sha, 'previous': old, 'mode': mode, 'services': changed,
                      'window': window, 'full_paths': touched}), flush=True)
    if plan:
        return
    test = json.loads((Path.home()/'.cache/sports-harness/test-state/test-harness_test_main.json').read_text())
    if (test.get('head') != head or test.get('head_after') != head or test.get('exit_code') != 0
            or test.get('scope') != [] or test.get('dirty_before') or test.get('dirty_after')
            or test.get('pytest_addopts') != '' or test.get('pytest_plugins') != ''):
        raise RuntimeError('A clean full-suite receipt at this exact main SHA is required')
    receipt_dir = RUNTIME/'releases'/f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{sha}'
    receipt_dir.mkdir(parents=True, mode=0o700)
    receipt = {'head': head, 'sha': sha, 'previous': old, 'mode': mode, 'services': changed,
               'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'preparing'}
    def checkpoint(status):
        receipt['status'] = status
        (receipt_dir/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
        print(f'[release] {status}', flush=True)
    checkpoint('preparing')
    for name in ('docker-compose.yml', 'compose.omarchy.yml'):
        shutil.copy2(RUNTIME/name, receipt_dir/f'previous-{name}')
    source = receipt_dir/'source'
    source.mkdir()
    archive = receipt_dir/'source.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git', 'archive', head], stdout=stream, check=True)
    with tarfile.open(archive) as stream:
        stream.extractall(source, filter='data')
    image = f'sports-release/app:{sha}'
    if subprocess.run(['docker','image','inspect',image], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError('Candidate image tag already exists; preserve it and investigate the prior release receipt')
    run(['docker', 'build', '-t', image, source])
    overlay = yaml.safe_load((RUNTIME/'compose.omarchy.yml').read_text())
    # Preserve existing override settings; never serialize the rendered environment/secrets.
    for name in ('postgres', 'app-backup', *APPS, 'app-ws'):
        entry = overlay['services'].setdefault(name, {})
        entry['image'] = before['services'][name]['image']
        if name in changed:
            entry['image'] = image
            entry.setdefault('environment', {}).update({'BUILD_SHA': sha,
                     'BUILD_TIME': datetime.now(timezone.utc).isoformat()})
        elif name == 'app-ws':
            env = before['services'][name]['environment']
            entry.setdefault('environment', {}).update({k: env[k] for k in ('BUILD_SHA','BUILD_TIME') if k in env})
        overlay['services'][name] = entry
    candidate = receipt_dir/'candidate-override.json'
    candidate.write_text(json.dumps(overlay, indent=2)+'\n')
    command = compose(source/'docker-compose.yml', candidate)
    after = json.loads(run([*command, 'config', '--format', 'json'], capture=True))
    validate_config(after)
    for name in ('postgres', 'app-backup'):
        if before['services'][name] != after['services'][name]:
            raise RuntimeError(f'{name} config changed: use a separate reviewed infrastructure procedure')
    if mode == 'app' and before['services']['app-ws'] != after['services']['app-ws']:
        raise RuntimeError('App-only release would change app-ws configuration')
    # Require an existing good backup before any stop/schema write; a skipped dump cannot pass.
    run([*existing, 'run', '--rm', '--no-deps', '-T', 'app-run', 'backup-precheck'])
    game_window(mode)  # building may have crossed a boundary
    if git('rev-parse', 'HEAD') != head or git('status', '--porcelain'):
        raise RuntimeError('Source changed while building')
    receipt['expected_services'] = expected_services(after, changed)
    receipt['previous_services'] = expected_services(before, changed)
    checkpoint('validated')
    stopped = False
    variants_attempted = False
    try:
        stopped = True  # stop may partially succeed, so failure must restart old services
        checkpoint('stopping')
        run([*existing, 'stop', *changed])
        if mode == 'full':
            checkpoint('migrating')
            run([*command, 'run', '--rm', '--no-deps', '-T', 'app-run', 'migrate', 'ensure'])
            run([*command, 'run', '--rm', '--no-deps', '-T', 'app-run', 'init-db'])
            variants_attempted = True  # registration can commit before a later failure
            run([*command, 'run', '--rm', '--no-deps', '-T', 'app-run', 'variants', 'register'])
            try:
                run([*command, 'run', '--rm', '--no-deps', '-T', 'app-run', 'seed-teams'])
            except subprocess.CalledProcessError:
                receipt.setdefault('warnings', []).append('seed-teams failed; existing teams retained; controller must retry once after five minutes')
                print('[release] WARNING: seed-teams failed; inspect receipt and retry once', flush=True)
        # App-only code changes have no DB/model/migration diff; no unnecessary schema work.
        checkpoint('promoting')
        atomic_copy(source/'docker-compose.yml', RUNTIME/'docker-compose.yml')
        atomic_copy(candidate, RUNTIME/'compose.omarchy.yml')
        run([RUNTIME/'sports-compose', 'up', '-d', '--no-build', '--no-deps', *changed])
        checkpoint('checking')
        receipt['health'] = wait_healthy(sha, changed, expected=receipt['expected_services'])
        checkpoint('healthy')
    except BaseException as original_error:
        receipt['original_error'] = type(original_error).__name__
        checkpoint('rolling-back-apps')
        try:
            atomic_copy(receipt_dir/'previous-docker-compose.yml', RUNTIME/'docker-compose.yml')
            atomic_copy(receipt_dir/'previous-compose.omarchy.yml', RUNTIME/'compose.omarchy.yml')
            if variants_attempted:
                # Reactivate the previous image's registry before any writer restarts.
                run([*existing, 'run', '--rm', '--no-deps', '-T', 'app-run', 'variants', 'register'])
            if stopped:
                run([RUNTIME/'sports-compose', 'up', '-d', '--no-build', '--no-deps', *changed])
            receipt['rollback_health'] = wait_healthy(old, changed, expected=receipt['previous_services'])
        except BaseException as rollback_error:
            receipt['rollback_error'] = type(rollback_error).__name__
            checkpoint('rollback-failed')
            raise original_error from rollback_error
        checkpoint('failed-old-apps-restored')
        # Additive schema changes are retained; rollback never drops production data.
        raise
    finally:
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        (receipt_dir/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('app','full'), required=True)
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or not RUNTIME.is_dir():
        parser.error('Run this command in the Omarchy development checkout')
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)
    prior = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        with (RUNTIME/'migration/release.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            deploy(args.mode, args.plan)
    finally:
        for sig, handler in prior.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    main()
