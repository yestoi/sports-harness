#!/usr/bin/env python3
"""Defense in depth for legacy worker calls, not the primary isolation boundary.

The required sports-worker agent has only the fixed MCP tool allowlist. Claude may
continue after a command-hook timeout, so a hook alone cannot enforce isolation.
The fixed server uses screenshot_read independently to validate controller images.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import sys

ROOT = Path(__file__).resolve().parents[4]
LAUNCHER = ROOT / 'scripts/worker-shell.py'


def deny(reason):
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny', 'permissionDecisionReason': reason}}


def load_launcher():
    spec = importlib.util.spec_from_file_location('worker_shell', LAUNCHER)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    return launcher


def screenshot_read(supplied):
    """Only controller-provided screenshots, never a general native-file capability."""
    refused = deny('Native Read is limited to canonical controller PNG/JPEG screenshots; '
                   'use sandboxed Bash for other reads.')
    if not isinstance(supplied, dict) or set(supplied) != {'file_path'}:
        return refused
    value = supplied['file_path']
    if not isinstance(value, str) or not value or '\0' in value:
        return refused
    try:
        if ROOT != load_launcher().MAIN:
            return refused
        directory = ROOT / '.superpowers/sdd/screenshots'
        path = Path(value)
        if (not path.is_absolute() or str(path) != value or '..' in path.parts
                or path.parent != directory or not directory.is_dir()
                or directory.resolve(strict=True) != directory
                or path.resolve(strict=True) != path):
            return refused
        extension = path.suffix.lower()
        if extension not in {'.png', '.jpg', '.jpeg'}:
            return refused
        # The directory is immutable to workers. Also refuse final-component link races
        # and special files at open time; the decoder receives only the original path.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return refused
            header = stream.read(16)
        valid = (header.startswith(b'\x89PNG\r\n\x1a\n') if extension == '.png'
                 else header.startswith(b'\xff\xd8\xff'))
        if not valid:
            return refused
    except (OSError, ValueError, RuntimeError):
        return refused
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow'}}


def decide(payload):
    if not isinstance(payload, dict):
        return deny('Invalid hook payload; worker isolation fails closed.')
    # This field belongs to the trusted Claude hook envelope, never tool_input.
    if 'agent_id' not in payload:
        return {}
    if not isinstance(payload['agent_id'], str) or not payload['agent_id']:
        return deny('Invalid subagent identity.')
    if payload.get('hook_event_name') != 'PreToolUse':
        return deny('Unsupported worker hook context.')
    if payload.get('tool_name') == 'Read':
        return screenshot_read(payload.get('tool_input'))
    if payload.get('tool_name') in {'mcp__sports_worker__shell', 'mcp__sports_worker__screenshot'}:
        return {}  # fixed server enforces isolation, independently of this hook
    if payload.get('tool_name') != 'Bash':
        return deny('Workers must use Bash through the OS sandbox, except validated controller '
                    'screenshot Read. Other native file, MCP and agent tools are disabled.')
    supplied = payload.get('tool_input')
    if not isinstance(supplied, dict):
        return deny('Invalid Bash input.')
    allowed = {'command', 'description', 'timeout', 'run_in_background', 'dangerouslyDisableSandbox'}
    if set(supplied) - allowed:
        return deny('Unsupported Bash fields; isolation fails closed.')
    if supplied.get('dangerouslyDisableSandbox') or supplied.get('run_in_background'):
        return deny('Workers cannot disable sandboxing or detach shell lifetime.')
    command = supplied.get('command')
    if not isinstance(command, str) or not command or '\0' in command:
        return deny('Invalid Bash command.')
    try:
        launcher = load_launcher()
        if ROOT != launcher.MAIN:
            raise ValueError('unsupported installation path')
        cwd, task = launcher.select_context(payload.get('cwd', ''), command)
        # Validate the mount layout before returning allow; launcher repeats checks at exec.
        launcher.sandbox_argv(cwd, task, command)
    except Exception:
        return deny('Worker sandbox unavailable or unsupported path/context. '
                    'Controller must provision Linux bwrap and the approved worktree/results layout.')
    encoded = base64.b64encode(command.encode()).decode('ascii')
    wrapped = shlex.join(['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', '/usr/bin/python3', '-I',
                          str(LAUNCHER), '--cwd', payload['cwd'], '--command-b64', encoded])
    updated = dict(supplied, command=wrapped, dangerouslyDisableSandbox=False)
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
            'permissionDecision': 'allow', 'updatedInput': updated}}


def main():
    try:
        payload = json.load(sys.stdin)
        result = decide(payload)
    except Exception:
        result = deny('Malformed hook input; worker isolation fails closed.')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
