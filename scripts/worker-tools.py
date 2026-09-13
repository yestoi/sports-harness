#!/usr/bin/env python3
"""Fixed stdio MCP capabilities for sports workers; no native shell fallback.

Only the trusted launcher executes shell requests. Hook timeouts cannot turn this
MCP request into a native Bash call. Four concurrent calls keep independent workers
responsive; the shared test-suite lock still serializes database suites.
Cancellation notifications do not interrupt calls; each shell call has its bounded
deadline, and server shutdown terminates tracked launcher processes.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading

MAIN = Path('/home/trey/dev/sports')
LAUNCHER = MAIN / 'scripts/worker-shell.py'
GUARD = MAIN / '.claude/skills/autopilot/scripts/worker_guard.py'
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 1800
MAX_OUTPUT = 64 * 1024
MAX_IMAGE = 10 * 1024 * 1024
MAX_REQUEST = 128 * 1024
PROTOCOL = '2025-06-18'
SUPPORTED_PROTOCOLS = {'2024-11-05', '2025-03-26', PROTOCOL}
_CHILDREN: set[subprocess.Popen] = set()
_CHILD_LOCK = threading.Lock()
_STOPPING = threading.Event()

TOOLS = [
    {'name': 'shell', 'description': 'Run a foreground command only in the fixed Linux worker sandbox. '
     'Use the assigned worktree cwd or a leading literal cd into it. No network or production access. '
     'Output is the final 64 KiB. Default timeout 120 seconds, maximum 1800; controller normally runs full suites.',
     'inputSchema': {'type': 'object', 'properties': {
         'cwd': {'type': 'string', 'minLength': 1},
         'command': {'type': 'string', 'minLength': 1, 'maxLength': 65536},
         'timeout_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': MAX_TIMEOUT,
                             'default': DEFAULT_TIMEOUT}},
         'required': ['cwd', 'command'], 'additionalProperties': False}},
    {'name': 'screenshot', 'description': 'View a controller-provided PNG/JPEG directly inside '
     '/home/trey/dev/sports/.superpowers/sdd/screenshots. No general file access.',
     'inputSchema': {'type': 'object', 'properties': {'file_path': {'type': 'string', 'minLength': 1}},
                     'required': ['file_path'], 'additionalProperties': False}},
]


def tool_error(message: str) -> dict:
    return {'content': [{'type': 'text', 'text': message}], 'isError': True}


def kill_child(child) -> None:
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class OutputTail:
    """Drain all output while retaining only a fixed byte tail; never spool to disk."""
    def __init__(self):
        self.data = bytearray()
        self.total = 0
        self.error = None

    def drain(self, stream):
        try:
            while chunk := stream.read(16 * 1024):
                self.total += len(chunk)
                self.data.extend(chunk)
                if len(self.data) > MAX_OUTPUT:
                    del self.data[:-MAX_OUTPUT]
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            stream.close()


def shell_tool(arguments: dict) -> dict:
    if set(arguments) - {'cwd', 'command', 'timeout_seconds'} or not {'cwd', 'command'} <= set(arguments):
        return tool_error('shell requires only cwd, command and optional timeout_seconds')
    cwd, command = arguments['cwd'], arguments['command']
    timeout = arguments.get('timeout_seconds', DEFAULT_TIMEOUT)
    if (not isinstance(cwd, str) or not cwd or '\0' in cwd or not Path(cwd).is_absolute()
            or not isinstance(command, str) or not command or '\0' in command or len(command) > 65536):
        return tool_error('cwd must be an absolute path and command a nonempty string')
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT):
        return tool_error(f'timeout_seconds must be greater than zero and at most {MAX_TIMEOUT}')
    argv = ['/usr/bin/python3', '-I', str(LAUNCHER), '--cwd', cwd,
            '--command-b64', base64.b64encode(command.encode('utf-8')).decode('ascii')]
    child = reader = None
    tail = OutputTail()
    try:
        # No caller-controlled executable, env, host cwd, shell=True or alternate exec path.
        with _CHILD_LOCK:
            if _STOPPING.is_set():
                return tool_error('Worker server is shutting down')
            child = subprocess.Popen(argv, cwd=str(MAIN), env={}, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     close_fds=True, start_new_session=True)
            _CHILDREN.add(child)
        reader = threading.Thread(target=tail.drain, args=(child.stdout,), daemon=True)
        reader.start()
        timed_out = False
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_child(child)
            code = child.wait(timeout=5)
        reader.join(timeout=5)
        if reader.is_alive() or tail.error:
            return tool_error('Worker output did not close cleanly; no fallback executed')
        summary = {'exit_code': code, 'timed_out': timed_out,
                   'output_truncated': tail.total > MAX_OUTPUT,
                   'output': tail.data.decode('utf-8', errors='replace')}
        return {'content': [{'type': 'text', 'text': json.dumps(summary)}],
                'isError': timed_out or code != 0}
    except Exception as exc:
        return tool_error(f'Worker launcher failed ({type(exc).__name__}); no fallback executed')
    finally:
        if child is not None:
            kill_child(child)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            with _CHILD_LOCK:
                _CHILDREN.discard(child)
        if reader is not None:
            reader.join(timeout=5)


def load_guard():
    spec = importlib.util.spec_from_file_location('sports_worker_guard', GUARD)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


def screenshot_tool(arguments: dict) -> dict:
    if set(arguments) != {'file_path'} or not isinstance(arguments['file_path'], str):
        return tool_error('screenshot requires only file_path')
    try:
        decision = load_guard().screenshot_read(arguments)
        if decision.get('hookSpecificOutput', {}).get('permissionDecision') != 'allow':
            return tool_error('Screenshot path or header refused by the fixed image policy')
        path = Path(arguments['file_path'])
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as image:
            info = os.fstat(image.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_IMAGE:
                return tool_error('Screenshot must be a single-link regular file no larger than 10 MiB')
            data = image.read(MAX_IMAGE + 1)
        png = path.suffix.lower() == '.png'
        if len(data) > MAX_IMAGE or not data.startswith(b'\x89PNG\r\n\x1a\n' if png else b'\xff\xd8\xff'):
            return tool_error('Screenshot changed or has an invalid image header')
        return {'content': [{'type': 'image', 'data': base64.b64encode(data).decode('ascii'),
                             'mimeType': 'image/png' if png else 'image/jpeg'}]}
    except Exception as exc:
        return tool_error(f'Screenshot unavailable ({type(exc).__name__})')


def rpc_error(request_id, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}}


class Protocol:
    def __init__(self):
        self.initialized = False

    def handle(self, request) -> dict | None:
        if (not isinstance(request, dict) or request.get('jsonrpc') != '2.0'
                or not isinstance(request.get('method'), str)):
            return rpc_error(None, -32600, 'Invalid request')
        request_id = request.get('id')
        if 'id' not in request:
            # Initialized/cancelled notifications and unknown notifications never execute tools.
            return None
        if request_id is None or isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return rpc_error(None, -32600, 'Invalid request id')
        params = request.get('params', {})
        if not isinstance(params, dict):
            return rpc_error(request_id, -32602, 'Invalid params')
        method = request['method']
        if method == 'initialize':
            version = params.get('protocolVersion')
            if not isinstance(version, str):
                return rpc_error(request_id, -32602, 'protocolVersion is required')
            self.initialized = True
            result = {'protocolVersion': version if version in SUPPORTED_PROTOCOLS else PROTOCOL,
                      'capabilities': {'tools': {'listChanged': False}},
                      'serverInfo': {'name': 'sports-worker', 'version': '1.0.0'}}
        elif method == 'ping':
            result = {}
        elif not self.initialized:
            return rpc_error(request_id, -32002, 'Initialize before using worker tools')
        elif method == 'tools/list':
            result = {'tools': TOOLS}
        elif method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments', {})
            if not isinstance(arguments, dict):
                return rpc_error(request_id, -32602, 'Tool arguments must be an object')
            if name == 'shell':
                result = shell_tool(arguments)
            elif name == 'screenshot':
                result = screenshot_tool(arguments)
            else:
                return rpc_error(request_id, -32602, 'Unknown worker tool')
        else:
            return rpc_error(request_id, -32601, 'Method not found')
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def serve(reader, writer) -> None:
    protocol = Protocol()
    output_lock = threading.Lock()
    pending = threading.BoundedSemaphore(16)
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='sports-worker')
    _STOPPING.clear()

    def emit(response):
        if response is not None:
            with output_lock:
                writer.write(json.dumps(response, allow_nan=False) + '\n')
                writer.flush()

    def completed(future, request_id):
        try:
            emit(future.result())
        except Exception:
            emit(rpc_error(request_id, -32603, 'Worker tool failed'))
        finally:
            pending.release()

    try:
        while True:
            line = reader.readline(MAX_REQUEST + 1)
            if not line:
                break
            if len(line) > MAX_REQUEST:
                while line and not line.endswith('\n'):
                    line = reader.readline(MAX_REQUEST + 1)
                emit(rpc_error(None, -32600, 'Request too large'))
                continue
            try:
                request = json.loads(line)
            except (ValueError, RecursionError):
                emit(rpc_error(None, -32700, 'Parse error'))
                continue
            if (isinstance(request, dict) and request.get('method') == 'tools/call'
                    and 'id' in request and protocol.initialized):
                if not pending.acquire(blocking=False):
                    emit(rpc_error(request.get('id'), -32000, 'Worker server busy'))
                    continue
                future = executor.submit(protocol.handle, request)
                future.add_done_callback(lambda done, rid=request.get('id'): completed(done, rid))
            else:
                emit(protocol.handle(request))
    finally:
        _STOPPING.set()
        with _CHILD_LOCK:
            for child in list(_CHILDREN):
                kill_child(child)
        executor.shutdown(wait=True, cancel_futures=True)


def main() -> None:
    # Raise into serve's cleanup on an orderly server termination; bwrap is never detached.
    def terminate(_signal, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)
    serve(sys.stdin, sys.stdout)


if __name__ == '__main__':
    main()
