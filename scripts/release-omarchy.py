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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_tree import EXCLUDED, release_tree  # noqa: E402  (scripts/release_tree.py, shared with the suite runner)
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo
import yaml

RUNTIME = Path('/srv/sports-harness')
APPS = ['app-run', 'app-serve', 'app-exec', 'app-research']
#: Phase 4.6 (addendum §6): the home-network listener, behind the `lan` compose profile.
LAN_APP = 'app-serve-lan'
FULL_PATHS = ['harness/recorder/ws_sink.py', 'harness/venues/kalshi/ws.py',
              'harness/venues/kalshi/rfq*.py', 'harness/matching',
              'harness/db', 'harness/variants', 'migrations', 'docker-compose.yml', 'Dockerfile',
              'pyproject.toml', 'constraints.txt', 'deploy/backup']


def lan_active(runtime=RUNTIME):
    """True when all three LAN files are non-empty regular files.

    `is_file()` and a size check, never `exists()`: a directory, a dangling symlink or a
    zero-byte placeholder must not switch a listener on. The script creates, copies and reads
    none of them -- it only asks the filesystem whether the user has placed them (D7).

    This is a second copy of `harness.dashboard.auth.lan_active`: that one runs in the image,
    this one runs on the host, and this script imports no `harness` module. The same five-case
    table runs over both (tests/test_dashboard_auth.py and tests/test_omarchy_release.py).
    """
    return all((runtime / 'secrets' / name).is_file()
               and (runtime / 'secrets' / name).stat().st_size > 0
               for name in ('owner_password_hash', 'lan_tls.crt', 'lan_tls.key'))


def apps_for(runtime=RUNTIME):
    """The app services a release touches: the standing four, plus the LAN listener when the
    user's three files are in place. The overlay image loop, `expected_services`,
    `wait_healthy` and the receipt all follow this one list, so the listener is deployed,
    waited for and recorded only when it exists."""
    return APPS + ([LAN_APP] if lan_active(runtime) else [])


def compose_profiles_state(env_path):
    """The runtime `.env`'s `COMPOSE_PROFILES` line, verbatim and with its position, or
    `(None, None)`.

    A whole-variable match on the assignment's name, never a substring search:
    `# COMPOSE_PROFILES=lan` and `OLD_COMPOSE_PROFILES=x` are not this line, and a rollback that
    mistook either for one would write a profile the owner never set (review I4). The index
    comes back too so a restore puts the operator's own line back where it was, not at the end.
    """
    for index, line in enumerate(env_path.read_text().splitlines(keepends=True)):
        if line.split('=', 1)[0].strip() == 'COMPOSE_PROFILES':
            return index, (line if line.endswith('\n') else line + '\n')
    return None, None


def restore_compose_profiles(env_path, index, line):
    """Put the captured `COMPOSE_PROFILES` line back exactly as it was -- same text, same
    position -- or leave the file without one when there was none. Written only when the text
    actually changes, so a release that never flipped the switch never touches the file."""
    original = env_path.read_text()
    lines = [entry for entry in original.splitlines(keepends=True)
             if entry.split('=', 1)[0].strip() != 'COMPOSE_PROFILES']
    if line is not None:
        lines.insert(index, line)
    text = ''.join(lines)
    if text != original:
        env_path.write_text(text)


def profiles_of(line):
    """The profile names a `COMPOSE_PROFILES` line enables. `None`, an empty value and a
    commented-out line all mean no profile, so none of them counts as the LAN switch."""
    return [name for name in (line or '').partition('=')[2].strip().split(',') if name]


def set_compose_profiles(env_path, active):
    """Add, keep or remove the runtime `.env`'s `COMPOSE_PROFILES=lan` line.

    An existing line is replaced in place rather than dropped and appended, so turning the
    switch on moves nothing else in the operator's file (and `restore_compose_profiles` can
    put the previous line back at the same index).

    Compose reads `COMPOSE_PROFILES` from the env file, so this single line is what makes
    `app-serve-lan` exist for every later compose call; the runtime `sports-compose` wrapper
    needs no change. The file is rewritten only when the text actually changes, so a host
    without the LAN files keeps a byte-identical `.env` across releases, and no other line is
    reordered, reformatted or read for its value.
    """
    original = env_path.read_text()
    wanted = 'COMPOSE_PROFILES=lan\n'
    lines, replaced = [], False
    for line in original.splitlines(keepends=True):
        if line.split('=', 1)[0].strip() != 'COMPOSE_PROFILES':
            lines.append(line)
        elif active and not replaced:
            lines.append(wanted)      # in place: a restored line goes back where it was
            replaced = True
    if active and not replaced:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append(wanted)
    text = ''.join(lines)
    if text != original:
        env_path.write_text(text)


def remove_lan_service(compose_command, receipt, key):
    """Stop and delete the LAN container, recording the outcome; never fatal.

    Two callers: a rollback, where the original error must survive this, and a healthy
    deactivation, where a removal problem must not undo a good release (the `seed-teams`
    precedent). `--profile lan` is explicit because the `.env` line is already gone by then.
    `rm -sf` names one service: never `down`, never `--remove-orphans`, no other container is
    touched, and no file of the user's is read or deleted.
    """
    try:
        run([*compose_command, '--profile', 'lan', 'rm', '-sf', LAN_APP])
        receipt.setdefault('stopped_services', []).append(LAN_APP)
    except (subprocess.CalledProcessError, OSError) as error:
        receipt.setdefault(key, []).append(f'{LAN_APP} removal failed: {type(error).__name__}')
        print(f'[release] WARNING: {LAN_APP} may still be published; check for a container on 8443',
              flush=True)


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


#: A published entry with one of these host addresses is on every interface, not on the home
#: network: Docker publishes ahead of ufw, so this is the boundary the release has to check.
FORBIDDEN_HOSTS = ('', '0.0.0.0', '::', '*')


def published_ports(service):
    """Every published entry of a rendered service as `(host_ip, published, target)` strings.

    `docker compose config` renders the long mapping form; the short string form is parsed too,
    so a hand-edited override cannot slip past the check below unread.
    """
    for entry in service.get('ports', []) or []:
        if isinstance(entry, dict):
            yield (str(entry.get('host_ip', '')), str(entry.get('published', '')),
                   str(entry.get('target', '')))
        else:
            parts = str(entry).split(':')
            host = parts[0] if len(parts) == 3 else ''
            yield host, (parts[-2] if len(parts) > 1 else ''), parts[-1]


def validate_lan_binding(service):
    """The LAN listener publishes exactly `LAN_ADDR:LAN_PORT -> 8443` and nothing else.

    The compose file cannot be trusted alone here: the host address is interpolated from the
    runtime `.env`, and `LAN_ADDR=0.0.0.0` there would publish the dashboard on every
    interface -- WireGuard and the Docker bridges included -- while every repository test still
    passed (review I3). The rendered binding is also required to agree with the `LAN_ADDR` and
    `LAN_PORT` the container itself sees, because those are what the write routes compare an
    `Origin` against (design §5.5): a listener published somewhere its own settings do not name
    is misconfigured in one direction or the other.
    """
    env = service.get('environment', {})
    address, port = str(env.get('LAN_ADDR', '')), str(env.get('LAN_PORT', ''))
    if address in FORBIDDEN_HOSTS or port == '':
        raise RuntimeError(f'LAN listener must name a home-network address and port, not {address!r}:{port!r}')
    entries = list(published_ports(service))
    if entries != [(address, port, '8443')]:
        raise RuntimeError(f'LAN listener must publish exactly {address}:{port}->8443, not {entries}')


def validate_config(config):
    services = config['services']
    # The LAN listener is checked exactly like every other app whenever the rendered config
    # carries it, i.e. whenever its profile is on; a host without the user's three files
    # renders, and is validated against, exactly the config it renders today.
    for name in APPS + ['app-ws'] + ([LAN_APP] if LAN_APP in services else []):
        env = services[name].get('environment', {})
        if str(env.get('LIVE_TRADING')) != '0' or env.get('HARNESS_MODE') != 'paper':
            raise RuntimeError(f'Paper posture invalid for {name}')
        if str(env.get('DB_BUDGET_GB')) != '600':
            raise RuntimeError(f'Runtime DB capacity alert budget changed for {name}')
        if str(env.get('RFQ_LISTENER_ENABLED')) != '0':
            raise RuntimeError(f'RFQ must stay disabled for {name}')
    if LAN_APP in services:
        validate_lan_binding(services[LAN_APP])
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
            # The LAN listener has its own healthcheck (TLS on 8443); when this release
            # includes it, an unhealthy one fails the release like any other serving process.
            healthy = ('app-exec', 'app-serve', 'postgres') + ((LAN_APP,) if LAN_APP in services else ())
            ready &= all(by_name.get(s, {}).get('Health') == 'healthy' for s in healthy)
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


MATCH_RANK = {'head': 2, 'tree': 1, 'release_tree': 0}


def full_suite_receipt(head, tree, release):
    """The controller's clean, unfiltered, passing receipt for this exact commit, this exact
    tree, or this release tree (the tree with docs/superpowers/autopilot left out; see
    scripts/release_tree.py).

    A branch rebased onto main and fast-forwarded has the same tree as main, so its pre-merge
    suite is the release evidence; a journal or state commit after that merge changes the tree
    but not the release tree, so the same receipt still stands. Exact commit beats tree beats
    release tree; the returned receipt carries the match kind under `match`.
    """
    state = Path(os.environ.get('SPORTS_TEST_STATE_DIR', str(Path.home()/'.cache/sports-harness/test-state')))
    candidates = []
    for path in sorted(state.glob('test-*.json')):
        try:
            test = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        clean = (test.get('exit_code') == 0 and test.get('scope') == [] and not test.get('dirty_before')
                 and not test.get('dirty_after') and test.get('pytest_addopts') == ''
                 and test.get('pytest_plugins') == '' and test.get('head') == test.get('head_after')
                 and all(shard.get('exit_code') == 0 for shard in test.get('shards', [])))
        if not clean:
            continue
        if test.get('head') == head:
            candidates.append(dict(test, match='head'))
        elif test.get('tree') and test['tree'] == tree:
            candidates.append(dict(test, match='tree'))
        elif test.get('release_tree') and test['release_tree'] == release:
            candidates.append(dict(test, match='release_tree'))
    if not candidates:
        return None
    return max(candidates, key=lambda test: (MATCH_RANK[test['match']], test.get('finished_at', '')))


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
    apps = apps_for(RUNTIME)      # APPS, plus app-serve-lan when the user's three files are there
    changed = apps + (['app-ws'] if mode == 'full' else [])
    # A service this release adds -- app-serve-lan on the first release after the files appear
    # -- has no previous container or image to restore, and the rolled-back compose file does
    # not define it. Identical to `changed` on every other release.
    restored = [service for service in changed if service in before['services']]
    # The LAN profile line lives in the runtime `.env`, which every `env_file:` service reads,
    # so `docker compose config` resolves it into app-ws's rendered environment too: flipping
    # the line during an app-only release would trip the app-ws drift guard with a message
    # about the recorder (review I2). A flip is therefore a full release, decided here, before
    # the plan output and before anything on disk is touched.
    profile_index, profile_line = compose_profiles_state(RUNTIME/'.env')
    lan_flip = ('lan' in profiles_of(profile_line)) != (LAN_APP in changed)
    if lan_flip and mode == 'app':
        raise RuntimeError('LAN activation or deactivation requires a full release')
    print(json.dumps({'sha': sha, 'previous': old, 'mode': mode, 'services': changed,
                      'window': window, 'full_paths': touched}), flush=True)
    if plan:
        return
    test = full_suite_receipt(head, git('rev-parse', 'HEAD^{tree}'),
                              release_tree(run=lambda args, **kwargs: git(*args[1:])))
    if test is None:
        raise RuntimeError('A clean full-suite receipt at this exact main SHA, its exact tree, or its release tree '
                           f'(everything outside {", ".join(EXCLUDED)}) is required')
    receipt_dir = RUNTIME/'releases'/f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{sha}'
    receipt_dir.mkdir(parents=True, mode=0o700)
    receipt = {'head': head, 'sha': sha, 'previous': old, 'mode': mode, 'services': changed,
               'suite_receipt': {key: test.get(key) for key in ('database', 'branch', 'head', 'tree', 'release_tree', 'finished_at', 'match')},
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
    for name in ('postgres', 'app-backup', *apps, 'app-ws'):
        entry = overlay['services'].setdefault(name, {})
        # Only the LAN app may be absent from the previous stack (the release that adds it);
        # a standing app missing there still raises before anything is stopped (review m3).
        if name != LAN_APP or name in before['services']:
            entry['image'] = before['services'][name]['image']
        if name in changed:
            entry['image'] = image
            entry.setdefault('environment', {}).update({'BUILD_SHA': sha,
                     'BUILD_TIME': datetime.now(timezone.utc).isoformat()})
        elif name == 'app-ws':
            env = before['services'][name]['environment']
            entry.setdefault('environment', {}).update({k: env[k] for k in ('BUILD_SHA','BUILD_TIME') if k in env})
        overlay['services'][name] = entry
    if LAN_APP not in changed:
        # No pinned image for a service this release does not deploy: a stale entry would let a
        # hand-run `COMPOSE_PROFILES=lan up -d app-serve-lan` start an old image (review m2).
        overlay['services'].pop(LAN_APP, None)
    candidate = receipt_dir/'candidate-override.json'
    candidate.write_text(json.dumps(overlay, indent=2)+'\n')
    # Compose reads COMPOSE_PROFILES from the runtime env file, so the line must be in place
    # before the candidate config is rendered, validated and brought up -- and after the
    # `before` render, which has to keep describing the stack as it is running now. Every
    # check between here and the rollback's own `try` can raise, so they run inside this one:
    # an abort must never leave the switch flipped for the next `up -d` -- the boot unit, a
    # manual restart or the restart runbook would then start a listener no release validated,
    # health-waited or recorded (review I1).
    try:
        if lan_flip:
            set_compose_profiles(RUNTIME/'.env', LAN_APP in changed)
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
        receipt['previous_services'] = expected_services(before, restored)
        checkpoint('validated')
    except BaseException:
        if lan_flip:
            restore_compose_profiles(RUNTIME/'.env', profile_index, profile_line)
        raise
    stopped = False
    variants_attempted = False
    try:
        stopped = True  # stop may partially succeed, so failure must restart old services
        checkpoint('stopping')
        run([*existing, 'stop', *restored])
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
        if lan_flip and LAN_APP not in changed:
            # The owner removed one of the three files, so the profile line is gone -- but a
            # container already running keeps the hash and key material it bind-mounted, by
            # inode, after the host files are deleted. Removing a file has to be the off
            # switch on its own, not "and remember to run a second command" (review ruling 1).
            remove_lan_service(command, receipt, 'warnings')
    except BaseException as original_error:
        receipt['original_error'] = type(original_error).__name__
        checkpoint('rolling-back-apps')
        try:
            if LAN_APP in changed and LAN_APP not in restored:
                # This release published 8443 from a build that is being rejected, and the
                # previous stack has no such service to restore it to. Removed here, before the
                # compose files go back: the previous file may not define the service at all,
                # so `rm` against it would fail and the container would survive (review C1).
                remove_lan_service(command, receipt, 'rollback_warnings')
            atomic_copy(receipt_dir/'previous-docker-compose.yml', RUNTIME/'docker-compose.yml')
            atomic_copy(receipt_dir/'previous-compose.omarchy.yml', RUNTIME/'compose.omarchy.yml')
            if lan_flip:
                restore_compose_profiles(RUNTIME/'.env', profile_index, profile_line)
            if variants_attempted:
                # Reactivate the previous image's registry before any writer restarts.
                run([*existing, 'run', '--rm', '--no-deps', '-T', 'app-run', 'variants', 'register'])
            if stopped:
                run([RUNTIME/'sports-compose', 'up', '-d', '--no-build', '--no-deps', *restored])
            receipt['rollback_health'] = wait_healthy(old, restored, expected=receipt['previous_services'])
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
