#!/usr/bin/env python3
"""Fail-closed Linux shell boundary for native Claude subagents.

The fixed controller-owned MCP server invokes this host-side launcher. No host command is evaluated
here; the supplied command runs after bwrap constructs a private mount/PID/network
namespace. Git metadata stays read-only: the controller commits worker diffs.
"""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import shlex
import stat
import sys

MAIN = Path('/home/trey/dev/sports')
WORKTREES = Path('/home/trey/dev/sports-wt')
SOCKET = Path(f'/run/user/{os.getuid()}/sports-test-db')
SOCKET_DEST = Path('/run/sports-test-db')
TEST_STATE = Path('/home/trey/.cache/sports-harness/test-state')
UV_RUNTIMES = Path('/home/trey/.local/share/uv/python')
BWRAP = Path('/usr/bin/bwrap')
EMPTY_FILE = Path(__file__).absolute().with_name('worker-empty')
PROTECTED = ('.claude', 'CLAUDE.md', 'docs/superpowers/autopilot')
SECRET_NAMES = frozenset({
    'secrets', '.ssh', '.aws', '.azure', '.config', '.docker', '.kube',
    '.netrc', '.npmrc', '.pypirc', '.pgpass', '.git-credentials',
    'credentials', 'credentials.json', 'credentials.toml', '.credentials.json',
    '.gitconfig', '.gitmodules', 'settings.local.json',
})


class IsolationError(ValueError):
    pass


def real_path(path: Path) -> Path:
    """Reject symlink components; never enlarge an allowed path via resolve()."""
    if not path.is_absolute() or '..' in path.parts or path.resolve() != path:
        raise IsolationError('noncanonical or symlink path')
    return path


def task_root(path: Path) -> Path:
    real_path(path)
    if not path.is_relative_to(WORKTREES) or path == WORKTREES:
        raise IsolationError('task must be under the fixed worktree root')
    # Nested branch names are supported; the first ancestor with a .git file is the root.
    root = path
    while root != WORKTREES:
        dotgit = root / '.git'
        if dotgit.is_file() and not dotgit.is_symlink():
            value = dotgit.read_text().strip()
            if not value.startswith('gitdir: '):
                raise IsolationError('unsupported worktree git pointer')
            gitdir = real_path(Path(value[8:]))
            if gitdir.parent != MAIN / '.git/worktrees' or not gitdir.is_dir():
                raise IsolationError('worktree belongs to an unsupported repository')
            if (gitdir / 'commondir').read_text().strip() != '../..':
                raise IsolationError('unsupported worktree common directory')
            if Path((gitdir / 'gitdir').read_text().strip()) != dotgit:
                raise IsolationError('worktree backpointer mismatch')
            return root
        root = root.parent
    raise IsolationError('not a registered task worktree')


def select_context(cwd: str, command: str) -> tuple[Path, Path | None]:
    """A literal leading cd selects one task, otherwise use the trusted hook cwd.

    Shell expansion is never used for selection. A command may subsequently cd elsewhere,
    but it cannot reach any host path that was not mounted in that selected namespace.
    """
    current = real_path(Path(cwd))
    task = task_root(current) if current.is_relative_to(WORKTREES) else None
    if task is None and not current.is_relative_to(MAIN):
        raise IsolationError('unsupported worker cwd')
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()<>')
    lexer.whitespace_split = True
    # Parse only the selection prefix. Heredoc bodies are shell data, not shlex input.
    first = lexer.get_token()
    tokens = [first, lexer.get_token(), lexer.get_token()] if first == 'cd' else []
    if tokens and tokens[2] == '&&':
        candidate = Path(tokens[1])
        if candidate.is_absolute() and candidate.is_relative_to(WORKTREES):
            selected = task_root(candidate)
            if task is not None and selected != task:
                raise IsolationError('cannot switch away from the current task worktree')
            task, current = selected, candidate
    return current, task


def sensitive(name: str, parent: Path) -> bool:
    return (name in SECRET_NAMES or name.startswith('.env')
            or (name in {'config', 'config.worktree'} and '.git' in parent.parts))


def overlays(root: Path, *, writable: bool) -> list[tuple[Path, bool]]:
    """Find credential masks; reject special files and hardlinks in writable inputs.

    No symlink is followed by this traversal. A symlink escaping the mounted trees has no
    host target inside the namespace. Hidden credential trees are replaced wholesale.
    """
    masks: list[tuple[Path, bool]] = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        base = Path(parent)
        for name in list(dirs) + files:
            path = base / name
            mode = path.lstat().st_mode
            if sensitive(name, base):
                # A symlink mask must not resolve a destination outside the selected tree.
                if stat.S_ISLNK(mode):
                    raise IsolationError('credential path is a symlink; controller must remove it')
                masks.append((path, stat.S_ISDIR(mode)))
                if name in dirs:
                    dirs.remove(name)
                continue
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
                raise IsolationError('special file in mounted repository')
            if writable and stat.S_ISREG(mode) and path.stat().st_nlink != 1:
                raise IsolationError('hardlinked writable file')
    return masks


def python_runtime() -> Path | None:
    """Expose only the uv installation containing the trusted shared venv interpreter."""
    venv = real_path(MAIN / '.venv')
    interpreter = (venv / 'bin/python').resolve(strict=True)
    if interpreter.is_relative_to('/usr'):
        return None
    if not interpreter.is_relative_to(UV_RUNTIMES):
        raise IsolationError('shared venv interpreter is outside supported runtime roots')
    relative = interpreter.relative_to(UV_RUNTIMES)
    if len(relative.parts) < 3 or relative.parts[1] != 'bin':
        raise IsolationError('unsupported uv interpreter layout')
    installation = real_path(UV_RUNTIMES / relative.parts[0])
    if not installation.is_dir():
        raise IsolationError('uv installation unavailable')
    return installation


def runtime_aliases(runtime: Path) -> list[Path]:
    """Recreate only uv's top-level aliases of this exact mounted installation.

    The venv executable and pyvenv.cfg may use the minor-version alias even though
    resolve() selects a patch-version directory. Emit our own direct symlink, not the
    host link target text, so no other runtime or host tree becomes reachable.
    """
    aliases = []
    for path in sorted(real_path(UV_RUNTIMES).iterdir()):
        if not path.is_symlink():
            continue
        try:
            if path.resolve(strict=True) == runtime:
                aliases.append(path)
        except (OSError, RuntimeError):
            # Unrelated broken/looping aliases are neither mounted nor followed later.
            continue
    return aliases


def sandbox_argv(cwd: Path, task: Path | None, command: str) -> list[str]:
    if EMPTY_FILE.resolve() != EMPTY_FILE or not EMPTY_FILE.is_file() or EMPTY_FILE.stat().st_size:
        raise IsolationError('trusted empty configuration mask is unavailable')
    if sys.platform != 'linux' or not BWRAP.is_file() or not os.access(BWRAP, os.X_OK):
        raise IsolationError('Linux /usr/bin/bwrap is required; no unsandboxed fallback')
    real_path(MAIN)
    real_path(WORKTREES)
    if not MAIN.is_dir() or not (MAIN / '.git').is_dir():
        raise IsolationError('controller repository unavailable')
    reports = real_path(MAIN / '.superpowers/sdd/results')
    if not reports.is_dir():
        raise IsolationError('controller must provision the results directory')
    lock = real_path(TEST_STATE / 'test-suite.lock')
    if not lock.is_file() or lock.stat().st_nlink != 1:
        raise IsolationError('controller must provision a regular, single-link test-suite.lock file')
    if task is not None and task_root(task) != task:
        raise IsolationError('invalid task root')
    if not cwd.is_relative_to(task or MAIN):
        raise IsolationError('cwd outside selected mount')
    args = [str(BWRAP), '--unshare-all', '--unshare-user', '--die-with-parent', '--new-session',
            '--disable-userns', '--cap-drop', 'ALL', '--clearenv']
    # A new root; no /home, /srv, /run, host /proc, or broad /etc bind.
    args += ['--ro-bind', '/usr', '/usr']
    for name in ('bin', 'sbin', 'lib', 'lib64'):
        path = Path('/') / name
        if path.is_symlink():
            args += ['--symlink', os.readlink(path), str(path)]
        elif path.is_dir():
            args += ['--ro-bind', str(path), str(path)]
    for name in ('passwd', 'group', 'nsswitch.conf', 'ld.so.cache', 'localtime'):
        path = Path('/etc') / name
        if path.is_file():
            args += ['--ro-bind', str(path), str(path)]
    args += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
             '--tmpfs', '/home/worker', '--dir', '/run',
             '--ro-bind', str(MAIN), str(MAIN),
             '--bind', str(reports), str(reports),
             '--bind', str(lock), '/run/sports-test-suite.lock']
    masks = overlays(MAIN, writable=False) + overlays(reports, writable=True)
    runtime = python_runtime()
    if runtime is not None:
        args += ['--ro-bind', str(runtime), str(runtime)]
        for alias in runtime_aliases(runtime):
            args += ['--symlink', runtime.name, str(alias)]
        masks += overlays(runtime, writable=False)
    if task is not None:
        args += ['--bind', str(task), str(task)]
        # Keep the pointer and task-local authority mounted read-only too. Common Git
        # metadata is already read-only through MAIN; never bind it writable.
        for relative in ('.git', *PROTECTED):
            path = task / relative
            if path.exists():
                real_path(path)
                args += ['--ro-bind', str(path), str(path)]
        masks += overlays(task, writable=True)
    for path, directory in sorted(set(masks), key=lambda entry: str(entry[0])):
        if directory:
            args += ['--tmpfs', str(path), '--remount-ro', str(path)]
        else:
            # A device bind is nodev in this namespace; use a regular empty file.
            args += ['--ro-bind', str(EMPTY_FILE), str(path)]
    # Only this controller-provisioned Unix socket directory can reach a service. Refuse
    # unexpected entries rather than exposing a second host socket or a symlink escape.
    if SOCKET.exists():
        real_path(SOCKET)
        if not SOCKET.is_dir():
            raise IsolationError('test socket path is not a directory')
        entries = list(SOCKET.iterdir())
        if any(p.name != '.s.PGSQL.5433' or not stat.S_ISSOCK(p.lstat().st_mode) for p in entries):
            raise IsolationError('unexpected entry in test socket directory')
        args += ['--ro-bind', str(SOCKET), str(SOCKET_DEST)]
    args += ['--setenv', 'PATH', f'{MAIN}/.venv/bin:/usr/bin:/bin',
             '--setenv', 'HOME', '/home/worker', '--setenv', 'TMPDIR', '/tmp',
             '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
             '--setenv', 'GIT_CONFIG_NOSYSTEM', '1', '--setenv', 'GIT_CONFIG_GLOBAL', '/dev/null',
             '--setenv', 'GIT_OPTIONAL_LOCKS', '0', '--setenv', 'SPORTS_TEST_SOCKET', str(SOCKET_DEST),
             '--setenv', 'SPORTS_TEST_STATE_DIR', '/tmp/sports-test-state',
             '--setenv', 'SPORTS_TEST_LOCK_FILE', '/run/sports-test-suite.lock',
             '--chdir', str(cwd), '--remount-ro', '/',
             '/bin/bash', '--noprofile', '--norc', '-c', command]
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--command-b64', required=True)
    args = parser.parse_args()
    try:
        command = base64.b64decode(args.command_b64, validate=True).decode('utf-8')
        if not command or '\0' in command:
            raise IsolationError('invalid shell command')
        cwd, task = select_context(args.cwd, command)
        argv = sandbox_argv(cwd, task, command)
        # No inherited sockets, agent FDs, secrets, LD_PRELOAD or Python startup settings.
        import subprocess
        return subprocess.run(argv, env={}, stdin=subprocess.DEVNULL, close_fds=True).returncode
    except (IsolationError, OSError, ValueError) as exc:
        print(f'worker isolation refused: {exc}', file=sys.stderr)
        return 126


if __name__ == '__main__':
    raise SystemExit(main())
