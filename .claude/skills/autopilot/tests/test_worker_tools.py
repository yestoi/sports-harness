"""Pure MCP/protocol/error checks; no Linux sandbox, database or remote access."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tools = load('worker_tools_test', ROOT / 'scripts/worker-tools.py')
guard = load('guard_for_mcp_test', ROOT / '.claude/skills/autopilot/scripts/worker_guard.py')


def request(method, request_id=1, **params):
    return {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}


class WorkerToolsTests(unittest.TestCase):
    def setUp(self):
        tools._STOPPING.clear()
        self.protocol = tools.Protocol()
        self.protocol.handle(request('initialize', protocolVersion='2025-06-18'))

    def call(self, name, arguments):
        return self.protocol.handle(request('tools/call', name=name, arguments=arguments))

    def test_initialize_list_ping_and_notification_contract(self):
        protocol = tools.Protocol()
        self.assertIn('error', protocol.handle(request('tools/list')))
        response = protocol.handle(request('initialize', protocolVersion='2024-11-05'))
        self.assertEqual(response['result']['protocolVersion'], '2024-11-05')
        self.assertEqual(response['result']['capabilities'], {'tools': {'listChanged': False}})
        self.assertIsNone(protocol.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        self.assertEqual(protocol.handle(request('ping'))['result'], {})
        offered = protocol.handle(request('tools/list'))['result']['tools']
        self.assertEqual([tool['name'] for tool in offered], ['shell', 'screenshot'])
        self.assertTrue(all(tool['inputSchema']['additionalProperties'] is False for tool in offered))
        self.assertEqual(offered[0]['inputSchema']['properties']['timeout_seconds']['maximum'], 1800)
        self.assertEqual(protocol.handle(request('initialize', protocolVersion='future'))[
            'result']['protocolVersion'], tools.PROTOCOL)

    def test_invalid_requests_and_unknown_capabilities_never_execute(self):
        with patch.object(tools.subprocess, 'Popen') as start:
            for value in ([], {}, request('tools/call', True), request('tools/call', None)):
                self.assertIn('error', self.protocol.handle(value))
            self.assertIn('error', self.call('Bash', {'command': 'anything'}))
            self.assertIn('error', self.call('shell', []))
            self.assertIn('error', self.protocol.handle(request('exec')))
            self.assertIsNone(self.protocol.handle({'jsonrpc': '2.0', 'method': 'tools/call',
                                                     'params': {'name': 'shell', 'arguments': {}}}))
            start.assert_not_called()

    def test_shell_invalid_arguments_fail_without_launcher(self):
        invalid = [{}, {'cwd': '.', 'command': 'ok'}, {'cwd': '/x', 'command': []},
                   {'cwd': '/x', 'command': 'ok', 'dangerouslyDisableSandbox': True},
                   {'cwd': '/x', 'command': 'ok', 'run_in_background': True},
                   {'cwd': '/x', 'command': '\0'}, {'cwd': '/x', 'command': ''}]
        for timeout in (0, -1, 1801, True, '120', float('nan'), float('inf')):
            invalid.append({'cwd': '/x', 'command': 'ok', 'timeout_seconds': timeout})
        with patch.object(tools.subprocess, 'Popen') as start:
            for arguments in invalid:
                self.assertTrue(self.call('shell', arguments)['result']['isError'], arguments)
            start.assert_not_called()

    def test_shell_uses_only_fixed_launcher_and_bounded_tail(self):
        captured = {}
        original = 'printf "%s" "$(cat /srv/secret)"; python3 <<\'PY\'\nprint("hello")\nPY'
        payload = b'x' * (tools.MAX_OUTPUT + 100) + b'END'

        def start(argv, **kwargs):
            captured.update(argv=argv, kwargs=kwargs)
            class Child:
                stdout = io.BytesIO(payload)
                pid = 123
                def wait(self, **_):
                    return 0
                def poll(self):
                    return 0
            return Child()

        with patch.object(tools.subprocess, 'Popen', side_effect=start):
            result = self.call('shell', {'cwd': '/home/trey/dev/sports', 'command': original})['result']
        self.assertFalse(result['isError'])
        argv = captured['argv']
        self.assertEqual(argv[:3], ['/usr/bin/python3', '-I', str(tools.LAUNCHER)])
        self.assertEqual(base64.b64decode(argv[-1]).decode(), original)
        self.assertNotIn(original, argv)
        self.assertEqual(captured['kwargs']['env'], {})
        self.assertEqual(captured['kwargs']['cwd'], str(tools.MAIN))
        self.assertEqual(captured['kwargs']['stdin'], subprocess.DEVNULL)
        self.assertEqual(captured['kwargs']['stdout'], subprocess.PIPE)
        self.assertTrue(captured['kwargs']['close_fds'])
        self.assertTrue(captured['kwargs']['start_new_session'])
        self.assertNotIn('shell', captured['kwargs'])
        summary = json.loads(result['content'][0]['text'])
        self.assertEqual(len(summary['output']), tools.MAX_OUTPUT)
        self.assertTrue(summary['output'].endswith('END'))
        self.assertTrue(summary['output_truncated'])

    def test_large_output_is_drained_without_unbounded_ram_or_disk(self):
        tail = tools.OutputTail()
        test = self
        class Stream:
            remaining = 1024
            closed = False
            def read(self, limit):
                test.assertLessEqual(len(tail.data), tools.MAX_OUTPUT)
                if not self.remaining:
                    return b''
                self.remaining -= 1
                return b'x' * limit
            def close(self):
                self.closed = True
        stream = Stream()
        tail.drain(stream)
        self.assertEqual(tail.total, 16 * 1024 * 1024)
        self.assertEqual(len(tail.data), tools.MAX_OUTPUT)
        self.assertTrue(stream.closed)
        self.assertIsNone(tail.error)

    def test_timeout_kills_wrapper_group_and_never_runs_an_alternative(self):
        class Child:
            pid = 12345
            code = None
            waits = []
            stdout = io.BytesIO(b'partial output')

            def poll(self):
                return self.code

            def wait(self, timeout):
                self.waits.append(timeout)
                if len(self.waits) == 1:
                    raise subprocess.TimeoutExpired('fixed wrapper', timeout)
                self.code = -9
                return self.code

        child = Child()
        with patch.object(tools.subprocess, 'Popen', return_value=child) as start, \
                patch.object(tools.os, 'killpg') as kill:
            result = self.call('shell', {'cwd': '/safe', 'command': 'anything',
                                        'timeout_seconds': 1800})['result']
        self.assertTrue(result['isError'])
        self.assertTrue(json.loads(result['content'][0]['text'])['timed_out'])
        self.assertEqual(child.waits[0], 1800)
        kill.assert_called_once_with(child.pid, tools.signal.SIGKILL)
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0][2], str(tools.LAUNCHER))

    def test_launcher_failure_is_only_a_tool_error(self):
        with patch.object(tools.subprocess, 'Popen', side_effect=FileNotFoundError) as start:
            result = self.call('shell', {'cwd': '/safe', 'command': 'touch /srv/never'})['result']
        self.assertTrue(result['isError'])
        self.assertIn('no fallback', result['content'][0]['text'])
        start.assert_called_once()
        self.assertEqual(tools._CHILDREN, set())

    def test_screenshot_uses_existing_policy_and_returns_image_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            directory = root / '.superpowers/sdd/screenshots'
            directory.mkdir(parents=True)
            png = directory / 'pulse.png'
            data = b'\x89PNG\r\n\x1a\n' + b'controller image'
            png.write_bytes(data)
            with patch.object(tools, 'load_guard', return_value=guard), \
                    patch.object(guard, 'ROOT', root), \
                    patch.object(guard, 'load_launcher', return_value=SimpleNamespace(MAIN=root)):
                response = self.call('screenshot', {'file_path': str(png)})['result']
                image, = response['content']
                self.assertEqual(image['type'], 'image')
                self.assertEqual(image['mimeType'], 'image/png')
                self.assertEqual(base64.b64decode(image['data']), data)
                jpg = directory / 'floor.jpeg'
                jpg.write_bytes(b'\xff\xd8\xff\xe1JPEG')
                self.assertEqual(self.call('screenshot', {'file_path': str(jpg)})[
                    'result']['content'][0]['mimeType'], 'image/jpeg')
                for path in ('/srv/secret.png', str(root / '.env'), str(directory / '../pulse.png')):
                    self.assertTrue(self.call('screenshot', {'file_path': path})['result']['isError'])
                self.assertTrue(self.call('screenshot', {'file_path': str(png), 'pages': '1'})[
                    'result']['isError'])
                with patch.object(tools, 'MAX_IMAGE', 4):
                    self.assertTrue(self.call('screenshot', {'file_path': str(png)})['result']['isError'])

    def test_protocol_parse_errors_are_json_only_and_notifications_have_no_response(self):
        reader = io.StringIO('{bad\n' + json.dumps(request('initialize', protocolVersion=tools.PROTOCOL))
                             + '\n' + json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
                             + '\n' + json.dumps(request('tools/list', 2)) + '\n')
        writer = io.StringIO()
        tools.serve(reader, writer)
        responses = [json.loads(line) for line in writer.getvalue().splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[0]['error']['code'], -32700)
        self.assertEqual(responses[-1]['id'], 2)

    def test_list_and_second_tool_remain_responsive_during_blocked_tool(self):
        messages = queue.Queue()
        outputs = queue.Queue()
        blocked = threading.Event()
        release = threading.Event()

        class Reader:
            def readline(self, _limit):
                return messages.get(timeout=5)

        class Writer:
            def write(self, value):
                outputs.put(json.loads(value))

            def flush(self):
                pass

        def shell(arguments):
            if arguments['command'] == 'blocked':
                blocked.set()
                if not release.wait(3):
                    raise RuntimeError('test tool was not released')
            return {'content': [{'type': 'text', 'text': arguments['command']}]}

        with patch.object(tools, 'shell_tool', side_effect=shell):
            server = threading.Thread(target=tools.serve, args=(Reader(), Writer()))
            server.start()
            try:
                messages.put(json.dumps(request('initialize', protocolVersion=tools.PROTOCOL)) + '\n')
                self.assertEqual(outputs.get(timeout=2)['id'], 1)
                messages.put(json.dumps(request('tools/call', 2, name='shell',
                                                arguments={'command': 'blocked'})) + '\n')
                self.assertTrue(blocked.wait(2))
                messages.put(json.dumps(request('tools/list', 3)) + '\n')
                messages.put(json.dumps(request('tools/call', 4, name='shell',
                                                arguments={'command': 'quick'})) + '\n')
                self.assertEqual({outputs.get(timeout=2)['id'], outputs.get(timeout=2)['id']}, {3, 4})
                release.set()
                self.assertEqual(outputs.get(timeout=2)['id'], 2)
            finally:
                release.set()
                messages.put('')
                server.join(timeout=3)
            self.assertFalse(server.is_alive())

    def test_agent_and_project_config_expose_only_fixed_capabilities(self):
        agent = (ROOT / '.claude/agents/sports-worker.md').read_text()
        tools_line = next(line for line in agent.splitlines() if line.startswith('tools:'))
        self.assertEqual(tools_line, 'tools: mcp__sports_worker__shell, mcp__sports_worker__screenshot')
        config = json.loads((ROOT / '.mcp.json').read_text())
        server = config['mcpServers']['sports_worker']
        self.assertEqual(server['command'], '/usr/bin/python3')
        self.assertEqual(server['args'], ['-I', '/home/trey/dev/sports/scripts/worker-tools.py'])
        self.assertEqual(set(config['mcpServers']), {'sports_worker'})


if __name__ == '__main__':
    unittest.main()
