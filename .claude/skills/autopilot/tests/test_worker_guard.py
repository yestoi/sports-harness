"""Pure policy/argv tests; these do not claim kernel or live Claude hook validation."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shell = load('worker_shell_test', ROOT / 'scripts/worker-shell.py')
guard = load('worker_guard_test', ROOT / '.claude/skills/autopilot/scripts/worker_guard.py')
suite = load('test_suite_runner', ROOT / 'scripts/test-suite.py')


class WorkerGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.main = self.base / 'sports'
        self.worktrees = self.base / 'sports-wt'
        self.task = self.worktrees / 'recovery/task'
        self.results = self.main / '.superpowers/sdd/results'
        self.gitdir = self.main / '.git/worktrees/task'
        for directory in (self.results, self.gitdir, self.task, self.main / '.venv',
                          self.task / '.claude', self.task / 'docs/superpowers/autopilot'):
            directory.mkdir(parents=True, exist_ok=True)
        (self.main / '.venv/bin').mkdir()
        (self.main / '.venv/bin/python').symlink_to('/usr/bin/python3')
        (self.task / '.git').write_text(f'gitdir: {self.gitdir}\n')
        (self.gitdir / 'commondir').write_text('../..\n')
        (self.gitdir / 'gitdir').write_text(str(self.task / '.git') + '\n')
        (self.task / 'CLAUDE.md').write_text('authority')
        (self.task / '.venv').symlink_to(self.main / '.venv')
        self.bwrap = self.base / 'bwrap'
        self.bwrap.write_text('not executed')
        self.bwrap.chmod(0o755)
        self.sock = self.base / 'sports-test-db'
        self.state = self.base / 'test-state'
        self.state.mkdir()
        self.lockfile = self.state / 'test-suite.lock'
        self.lockfile.touch()
        for owner, name, value in ((shell, 'MAIN', self.main),
                                    (shell, 'WORKTREES', self.worktrees),
                                    (shell, 'BWRAP', self.bwrap), (shell, 'SOCKET', self.sock),
                                    (shell, 'TEST_STATE', self.state),
                                    (guard, 'ROOT', self.main),
                                    (guard, 'LAUNCHER', self.main / 'scripts/worker-shell.py')):
            patcher = patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(shell.sys, 'platform', 'linux')
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(guard, 'load_launcher', return_value=shell)
        patcher.start()
        self.addCleanup(patcher.stop)

    def event(self, command='printf ok', **changes):
        event = {'agent_id': 'agent-1', 'hook_event_name': 'PreToolUse',
                 'tool_name': 'Bash', 'cwd': str(self.main), 'tool_input': {'command': command}}
        event.update(changes)
        return event

    def decision(self, event):
        return guard.decide(event)['hookSpecificOutput']

    def test_parent_is_unchanged_even_without_linux_or_valid_input(self):
        self.bwrap.unlink()
        self.assertEqual(guard.decide({'tool_name': 'Bash', 'tool_input': {'command': 'anything'}}), {})

    def test_all_worker_non_bash_tools_are_denied(self):
        for tool in ('Read', 'Edit', 'Write', 'Glob', 'Grep', 'NotebookEdit', 'Agent',
                     'Task', 'TaskOutput', 'WebFetch', 'mcp__ssh__exec', 'Unknown'):
            with self.subTest(tool=tool):
                self.assertEqual(self.decision(self.event(tool_name=tool))['permissionDecision'], 'deny')

    def test_native_paths_never_bypass_the_namespace_even_for_allowed_reports(self):
        paths = ('/srv/sports/.env', '/home/trey/.ssh/id_ed25519',
                 '/home/trey/.config/credentials', str(self.main / 'harness/source.py'),
                 str(self.results / 'report.md'), str(self.task / 'source.py'))
        for tool in ('Read', 'Write', 'Edit', 'Grep', 'Glob'):
            for path in paths:
                decision = self.decision(self.event(tool_name=tool,
                                         tool_input={'file_path': path, 'path': path}))
                self.assertEqual(decision['permissionDecision'], 'deny')

    def test_native_read_accepts_only_controller_image_headers(self):
        screenshots = self.main / '.superpowers/sdd/screenshots'
        screenshots.mkdir()
        for name, data in (('pulse.png', b'\x89PNG\r\n\x1a\n' + b'header'),
                           ('floor.jpg', b'\xff\xd8\xff\xe0' + b'header'),
                           ('ticket.JPEG', b'\xff\xd8\xff\xe1' + b'header')):
            image = screenshots / name
            image.write_bytes(data)
            event = self.event(tool_name='Read', tool_input={'file_path': str(image)})
            self.assertEqual(self.decision(event)['permissionDecision'], 'allow')
            for field in ('offset', 'limit', 'pages', 'path', 'unknown'):
                event['tool_input'][field] = 1
                self.assertEqual(self.decision(event)['permissionDecision'], 'deny')
                del event['tool_input'][field]
            for tool in ('Write', 'Edit', 'Grep', 'Glob'):
                event['tool_name'] = tool
                self.assertEqual(self.decision(event)['permissionDecision'], 'deny')

    def test_native_screenshot_read_rejects_escape_wrong_headers_and_nonregular_files(self):
        screenshots = self.main / '.superpowers/sdd/screenshots'
        screenshots.mkdir()
        real = screenshots / 'real.png'
        real.write_bytes(b'\x89PNG\r\n\x1a\n')
        alias = screenshots / 'alias.png'
        alias.symlink_to(real)
        hardlink = screenshots / 'hard.png'
        os.link(real, hardlink)
        bad = screenshots / 'credentials.png'
        bad.write_text('not a screenshot')
        text = screenshots / 'image.py'
        text.write_bytes(b'\x89PNG\r\n\x1a\n')
        wrong_extension = screenshots / 'wrong.jpg'
        wrong_extension.write_bytes(b'\x89PNG\r\n\x1a\n')
        fifo = screenshots / 'pipe.png'
        os.mkfifo(fifo)
        outside = self.results / 'worker.png'
        outside.write_bytes(b'\x89PNG\r\n\x1a\n')
        paths = [str(path) for path in (real, alias, hardlink, bad, text, wrong_extension,
                                       fifo, outside, screenshots / 'missing.png', screenshots)]
        paths += [str(screenshots) + '/../screenshots/credentials.png',
                  str(screenshots) + '/./credentials.png', 'relative.png',
                  '/etc/passwd', str(self.main / 'harness/code.png')]
        for path in paths:
            result = self.decision(self.event(tool_name='Read', tool_input={'file_path': path}))
            self.assertEqual(result['permissionDecision'], 'deny', path)

    def test_native_read_rejects_a_symlinked_screenshot_directory(self):
        screenshots = self.main / '.superpowers/sdd/screenshots'
        screenshots.symlink_to(self.results)
        image = screenshots / 'worker.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n')
        result = self.decision(self.event(tool_name='Read', tool_input={'file_path': str(image)}))
        self.assertEqual(result['permissionDecision'], 'deny')

    def test_invalid_envelopes_and_bypass_fields_fail_closed(self):
        cases = [self.event(agent_id=''), self.event(hook_event_name='Other'),
                 self.event(cwd='/srv/sports'), self.event(cwd='relative'),
                 self.event(tool_input={'command': 'ok', 'dangerouslyDisableSandbox': True}),
                 self.event(tool_input={'command': 'ok', 'run_in_background': True}),
                 self.event(tool_input={'command': 'ok', 'cwd': '/srv'}),
                 self.event(command=''), self.event(command='\0')]
        for event in cases:
            self.assertEqual(self.decision(event)['permissionDecision'], 'deny', event)
        self.assertEqual(guard.decide([])['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_command_is_encoded_without_host_shell_interpolation(self):
        original = f"cd '{self.task}' && printf '%s' '$(cat /srv/secret)' `uname`; echo hi"
        event = self.event(original)
        event['tool_input'].update(timeout=1000, description='test')
        result = self.decision(event)
        self.assertEqual(result['permissionDecision'], 'allow')
        updated = result['updatedInput']
        argv = shlex.split(updated['command'])
        self.assertEqual(argv[:6], ['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin',
                                    '/usr/bin/python3', '-I', str(guard.LAUNCHER)])
        self.assertEqual(base64.b64decode(argv[-1]).decode(), original)
        self.assertNotIn('$(cat', updated['command'])
        self.assertEqual(updated['timeout'], 1000)
        self.assertFalse(updated['dangerouslyDisableSandbox'])

    def test_literal_cd_selects_only_one_registered_worktree(self):
        cwd, task = shell.select_context(str(self.main), f'cd {self.task} && git diff')
        self.assertEqual((cwd, task), (self.task, self.task))
        (self.task / 'sub').mkdir()
        self.assertEqual(shell.select_context(str(self.task / 'sub'), 'pwd'),
                         (self.task / 'sub', self.task))
        for command in (f'echo cd {self.task} && true', f'cd /tmp && cd {self.task}',
                        f'x={self.task}; cd "$x"', 'cd "$HOME/dev/sports-wt/task" && true'):
            self.assertIsNone(shell.select_context(str(self.main), command)[1])
        with self.assertRaises(shell.IsolationError):
            shell.select_context(str(self.main), f'cd {self.worktrees}/missing && true')

    def test_heredoc_body_is_not_parsed_as_the_selection_prefix(self):
        command = f"cd {self.task} && cat <<'EOF'\nunmatched ' is data\nEOF"
        self.assertEqual(shell.select_context(str(self.main), command), (self.task, self.task))
        self.assertEqual(self.decision(self.event(command))['permissionDecision'], 'allow')

    def test_symlink_worktree_and_invalid_git_backpointer_are_denied(self):
        alias = self.worktrees / 'alias'
        alias.symlink_to(self.task)
        with self.assertRaises(shell.IsolationError):
            shell.select_context(str(self.main), f'cd {alias} && pwd')
        (self.gitdir / 'gitdir').write_text('/srv/.git')
        with self.assertRaises(shell.IsolationError):
            shell.select_context(str(self.main), f'cd {self.task} && pwd')

    def test_namespace_mounts_and_environment_do_not_expose_host(self):
        args = shell.sandbox_argv(self.task, self.task, 'env; git status')
        for flag in ('--unshare-all', '--unshare-user', '--die-with-parent', '--new-session', '--disable-userns',
                     '--clearenv', '--cap-drop'):
            self.assertIn(flag, args)
        binds = [(args[i+1], args[i+2]) for i, token in enumerate(args) if token == '--bind']
        self.assertEqual(binds, [(str(self.results), str(self.results)),
                                 (str(self.lockfile), '/run/sports-test-suite.lock'),
                                 (str(self.task), str(self.task))])
        ro_binds = [(args[i+1], args[i+2]) for i, token in enumerate(args) if token == '--ro-bind']
        self.assertIn((str(self.main), str(self.main)), ro_binds)
        for relative in ('.git', '.claude', 'CLAUDE.md', 'docs/superpowers/autopilot'):
            self.assertIn((str(self.task / relative), str(self.task / relative)), ro_binds)
        self.assertNotIn(('/home', '/home'), ro_binds)
        self.assertNotIn(('/etc', '/etc'), ro_binds)
        self.assertNotIn('/srv', args)
        self.assertNotIn('/run/docker.sock', args)
        self.assertNotIn('SSH_AUTH_SOCK', args)
        self.assertEqual(args[-5:], ['/bin/bash', '--noprofile', '--norc', '-c', 'env; git status'])
        self.assertIn('SPORTS_TEST_SOCKET', args)
        self.assertIn('SPORTS_TEST_STATE_DIR', args)
        self.assertIn('SPORTS_TEST_LOCK_FILE', args)
        self.assertIn(str(self.main / '.venv') + '/bin:/usr/bin:/bin', args)

    def test_uv_mount_is_only_the_resolved_interpreter_installation(self):
        uv = self.base / 'uv/python'
        installation = uv / 'cpython-3.12-linux'
        (installation / 'bin').mkdir(parents=True)
        interpreter = installation / 'bin/python3.12'
        interpreter.write_text('not executed')
        link = self.main / '.venv/bin/python'
        link.unlink()
        link.symlink_to(interpreter)
        with patch.object(shell, 'UV_RUNTIMES', uv):
            args = shell.sandbox_argv(self.main, None, 'python -V')
        idx = args.index(str(installation))
        self.assertEqual(args[idx-1:idx+2], ['--ro-bind', str(installation), str(installation)])
        self.assertNotIn(str(uv), args)
        link.unlink()
        link.symlink_to('/srv/production/python')
        with self.assertRaises((shell.IsolationError, FileNotFoundError)):
            shell.sandbox_argv(self.main, None, 'python -V')

    def test_uv_aliases_preserve_venv_paths_without_exposing_other_installations(self):
        uv = self.base / 'uv/python'
        installation = uv / 'cpython-3.12.14-linux-x86_64-gnu'
        (installation / 'bin').mkdir(parents=True)
        (installation / 'bin/python3.12').write_text('not executed')
        alias = uv / 'cpython-3.12-linux-x86_64-gnu'
        alias.symlink_to(installation.name)
        chained = uv / 'cpython-current'
        chained.symlink_to(alias.name)
        unrelated = uv / 'other-installation'
        unrelated.mkdir()
        (uv / 'other-alias').symlink_to(unrelated.name)
        (uv / 'outside-alias').symlink_to('/srv')
        (uv / 'broken-alias').symlink_to('missing')
        link = self.main / '.venv/bin/python'
        link.unlink()
        link.symlink_to(alias / 'bin/python3.12')
        (self.main / '.venv/pyvenv.cfg').write_text(f'home = {alias}/bin\n')
        with patch.object(shell, 'UV_RUNTIMES', uv):
            args = shell.sandbox_argv(self.main, None, 'python -V')
        for path in (alias, chained):
            idx = args.index(str(path))
            self.assertEqual(args[idx-2:idx+1], ['--symlink', installation.name, str(path)])
        for path in (uv, unrelated, uv / 'other-alias', uv / 'outside-alias', uv / 'broken-alias'):
            self.assertNotIn(str(path), args)
        self.assertIn(['--ro-bind', str(installation), str(installation)],
                      [args[i:i+3] for i in range(len(args)-2)])

    def test_review_main_cannot_write_any_source_or_git_metadata(self):
        args = shell.sandbox_argv(self.main, None, 'git diff')
        binds = [(args[i+1], args[i+2]) for i, token in enumerate(args) if token == '--bind']
        self.assertEqual(binds, [(str(self.results), str(self.results)),
                                 (str(self.lockfile), '/run/sports-test-suite.lock')])
        self.assertNotIn(str(self.worktrees), args)

    def test_worker_receipts_are_private_and_only_lock_inode_is_exposed(self):
        receipt = self.state / 'test-harness_test_main.json'
        receipt.write_text('controller receipt')
        args = shell.sandbox_argv(self.main, None, 'true')
        mounts = [(args[i+1], args[i+2]) for i, token in enumerate(args)
                  if token in ('--bind', '--ro-bind')]
        self.assertIn((str(self.lockfile), '/run/sports-test-suite.lock'), mounts)
        # Neither the receipt nor any ancestor of the controller cache is exposed.
        self.assertFalse(any(self.state.is_relative_to(Path(source)) for source, _ in mounts))
        self.assertNotIn(str(receipt), args)
        self.assertNotIn('/run/sports-test-state', args)
        env = {args[i+1]: args[i+2] for i, token in enumerate(args) if token == '--setenv'}
        self.assertEqual(env['SPORTS_TEST_STATE_DIR'], '/tmp/sports-test-state')
        self.assertEqual(env['SPORTS_TEST_LOCK_FILE'], '/run/sports-test-suite.lock')
        self.assertIn(['--remount-ro', '/'], [args[i:i+2] for i in range(len(args)-1)])
        # /run remains part of the readonly root: no writable directory mount can unlink
        # or replace the bound lock inode. Actual kernel denial is an Omarchy smoke check.
        writable = [destination for source, destination in mounts
                    if args[args.index(source)-1] == '--bind']
        self.assertNotIn('/run', writable)
        self.assertNotIn('/', writable)

    def test_symlink_or_hardlinked_lock_cannot_be_mounted(self):
        self.lockfile.unlink()
        receipt = self.state / 'test-harness_test_main.json'
        receipt.write_text('controller receipt')
        self.lockfile.symlink_to(receipt)
        with self.assertRaises(shell.IsolationError):
            shell.sandbox_argv(self.main, None, 'true')
        self.lockfile.unlink()
        os.link(receipt, self.lockfile)
        with self.assertRaises(shell.IsolationError):
            shell.sandbox_argv(self.main, None, 'true')

    def test_suite_explicit_lock_is_inherited_and_receipt_uses_private_state(self):
        private = self.base / 'private-worker-state'
        controller_receipt = self.state / 'test-harness_test_main.json'
        controller_receipt.write_text('controller receipt')
        recorded = {}

        def start_child(command, **kwargs):
            fd, = kwargs['pass_fds']
            recorded['inode'] = os.fstat(fd).st_ino
            recorded['environment'] = kwargs['env']
            recorded['session'] = kwargs['start_new_session']
            child = unittest.mock.Mock()
            child.wait.return_value = 0
            return child

        with patch.dict(os.environ, {'SPORTS_TEST_STATE_DIR': str(private),
                                     'SPORTS_TEST_LOCK_FILE': str(self.lockfile)}, clear=True), \
                patch.object(suite.sys, 'argv', ['test-suite', '--', 'tests/test_example.py']), \
                patch.object(suite.subprocess, 'check_output', side_effect=[
                    'main', 'abc123', 'tree123', '100644 blob 1 x\tharness/x.py\n', '', 'postgresql://test-only', 'abc123', '']), \
                patch.object(suite.subprocess, 'Popen', side_effect=start_child), \
                patch.object(suite.signal, 'signal'), \
                patch.object(suite.sys, 'stdout', io.StringIO()):
            self.assertEqual(suite.main(), 0)
        self.assertEqual(recorded['inode'], self.lockfile.stat().st_ino)
        self.assertTrue(recorded['session'])
        self.assertEqual(recorded['environment']['DATABASE_URL_TEST'], 'postgresql://test-only')
        self.assertEqual(controller_receipt.read_text(), 'controller receipt')
        self.assertTrue((private / 'test-harness_test_main-scoped.json').is_file())
        self.assertFalse((private / 'test-suite.lock').exists())

    def test_sensitive_files_are_hidden_in_main_and_task(self):
        (self.main / '.env').write_text('do not disclose')
        (self.main / '.git/config').write_text('credential data')
        (self.task / 'secrets').mkdir()
        (self.task / 'secrets/key').write_text('secret')
        (self.task / 'sub').mkdir()
        (self.task / 'sub/.env.production').write_text('secret')
        args = shell.sandbox_argv(self.task, self.task, 'true')
        for file in (self.main / '.env', self.main / '.git/config', self.task / 'sub/.env.production'):
            idx = args.index(str(file))
            self.assertEqual(args[idx-2:idx], ['--ro-bind', str(shell.EMPTY_FILE)])
        idx = args.index(str(self.task / 'secrets'))
        self.assertEqual(args[idx-1], '--tmpfs')
        self.assertEqual(args[idx+1:idx+3], ['--remount-ro', str(self.task / 'secrets')])

    def test_tracked_env_templates_and_secrets_gitkeep_are_not_masked(self):
        # `.env.example` / `.env.nas.example` are committed templates and `secrets/.gitkeep` is a
        # committed empty file: hiding them shows every worker a dirty tree it did not touch and
        # empties the receipt's dirty_before/dirty_after signal. Real `.env*` files stay masked.
        (self.task / '.env').write_text('do not disclose')
        (self.task / '.env.example').write_text('DATABASE_URL=postgresql://u:p@h/db\n')
        (self.task / '.env.nas.example').write_text('NAS_IP=\n')
        (self.task / 'secrets').mkdir()
        (self.task / 'secrets/.gitkeep').touch()
        (self.task / 'secrets/key').write_text('secret')
        args = shell.sandbox_argv(self.task, self.task, 'true')
        for name in ('.env.example', '.env.nas.example'):
            self.assertNotIn(str(self.task / name), args)
        idx = args.index(str(self.task / '.env'))
        self.assertEqual(args[idx-2:idx], ['--ro-bind', str(shell.EMPTY_FILE)])
        secrets = str(self.task / 'secrets')
        idx = args.index(secrets)
        self.assertEqual(args[idx-1], '--tmpfs')
        self.assertEqual(args[idx+1:idx+6], ['--ro-bind', str(shell.EMPTY_FILE), secrets + '/.gitkeep',
                                              '--remount-ro', secrets])
        self.assertNotIn(secrets + '/key', args)

    def test_hardlinks_special_files_and_secret_symlinks_fail_closed(self):
        origin = self.main / 'source'
        origin.write_text('main code')
        link = self.task / 'hardlink'
        os.link(origin, link)
        with self.assertRaises(shell.IsolationError):
            shell.sandbox_argv(self.task, self.task, 'true')
        link.unlink()
        fifo = self.task / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(shell.IsolationError):
            shell.sandbox_argv(self.task, self.task, 'true')
        fifo.unlink()
        (self.task / '.env').symlink_to(origin)
        with self.assertRaises(shell.IsolationError):
            shell.sandbox_argv(self.task, self.task, 'true')

    def test_only_expected_test_socket_is_mounted(self):
        self.sock.mkdir()
        proxy = socket.socket(socket.AF_UNIX)
        self.addCleanup(proxy.close)
        proxy.bind(str(self.sock / '.s.PGSQL.5433'))
        args = shell.sandbox_argv(self.main, None, 'true')
        idx = args.index(str(self.sock))
        self.assertEqual(args[idx-1], '--ro-bind')
        (self.sock / 'credentials').write_text('bad')
        with self.assertRaises(shell.IsolationError):
            shell.sandbox_argv(self.main, None, 'true')

    def test_missing_bwrap_non_linux_missing_reports_fail_closed(self):
        with patch.object(shell.sys, 'platform', 'darwin'):
            self.assertEqual(self.decision(self.event())['permissionDecision'], 'deny')
        self.lockfile.unlink()
        self.assertEqual(self.decision(self.event())['permissionDecision'], 'deny')
        self.lockfile.touch()
        self.results.rmdir()
        self.assertEqual(self.decision(self.event())['permissionDecision'], 'deny')
        self.results.mkdir()
        self.bwrap.unlink()
        self.assertEqual(self.decision(self.event())['permissionDecision'], 'deny')

    def test_launcher_drops_inherited_environment_and_file_descriptors(self):
        encoded = base64.b64encode(b'printf ok').decode()
        with patch.object(shell.sys, 'argv', ['worker-shell', '--cwd', str(self.main),
                                           '--command-b64', encoded]), \
                patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(shell.main(), 0)
        self.assertEqual(run.call_args.kwargs, {'env': {}, 'stdin': subprocess.DEVNULL, 'close_fds': True})
        self.assertEqual(run.call_args.args[0][0], str(self.bwrap))

    def test_malformed_hook_json_emits_deny(self):
        output = io.StringIO()
        with patch.object(guard.sys, 'stdin', io.StringIO('{broken')), \
                patch.object(guard.sys, 'stdout', output):
            guard.main()
        self.assertEqual(json.loads(output.getvalue())['hookSpecificOutput']['permissionDecision'], 'deny')


if __name__ == '__main__':
    unittest.main()
