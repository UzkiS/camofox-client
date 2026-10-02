from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from support import SKILL, clean_env, http_service
from client import Client
from errors import RequestError
import main
from transport import Response


class CliTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.transport.send.return_value = Response(200, 'application/json', b'{"ok":true}')

    def invoke(self, args, environ=None):
        output = io.StringIO()
        def factory(config):
            return Client(config, transport=self.transport)
        with patch.dict(os.environ, environ or {}, clear=True), patch('main.Client', side_effect=factory), \
                redirect_stdout(output):
            code = main.main(args)
        return code, output.getvalue()

    def test_cli_empty_default_requires_explicit_identity_before_request(self):
        code, output = self.invoke(['tabs'], {'CAMOFOX_USER_ID': ''})
        self.assertEqual(code, 1)
        self.assertIn('user_id', json.loads(output)['error']['message'])
        self.transport.send.assert_not_called()
        for user in ('a', 'b', 'a'):
            code, _ = self.invoke(['--user-id', user, 'tabs'], {'CAMOFOX_USER_ID': ''})
            self.assertEqual(code, 0)
            self.assertEqual(self.transport.send.call_args.kwargs['params']['userId'], user)

    def test_explicit_identity_overrides_nonempty_default(self):
        for user in ('a', 'b', 'a'):
            code, _ = self.invoke(['--user-id', user, 'tabs'], {'CAMOFOX_USER_ID': 'ambient'})
            self.assertEqual(code, 0)
            self.assertEqual(self.transport.send.call_args.kwargs['params']['userId'], user)

    def test_json_error_and_exit_code(self):
        self.transport.send.side_effect = RequestError('denied', status=403, details={'reason': 'test'})
        code, output = self.invoke(['--user-id', 'a', 'tabs'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)['error']['http_status'], 403)
        for args in ([], ['unknown'], ['--user-id', 'a', 'snapshot'], ['--access-key', 'test', 'tabs']):
            code, output = self.invoke(args)
            self.assertEqual(code, 1)
            self.assertIn('error', json.loads(output))

    def test_composite_failure_returns_result_not_transport_exception(self):
        self.transport.send.side_effect = RequestError('offline')
        code, output = self.invoke(['doctor'])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)['healthy'])
        code, output = self.invoke(['--user-id', 'a', 'click-and-observe', '--tab-id', 'tab-a', '--ref', 'e1'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)['observe']['endedBy'], 'baseline_failed')

    def test_snapshot_text_pagination_and_default_json(self):
        payload = {'url': 'https://example.com', 'snapshot': '页面内容', 'hasMore': True,
                   'nextOffset': 20, 'totalChars': 40}
        self.transport.send.return_value = Response(200, 'application/json', json.dumps(payload).encode())
        args = ['--user-id', 'a', 'snapshot', '--tab-id', 'tab-a']
        code, output = self.invoke(args)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), payload)
        code, output = self.invoke([*args, '--output-format', 'text'])
        self.assertEqual(code, 0)
        self.assertIn('页面内容', output)
        self.assertIn('--offset 20', output)
        self.transport.send.return_value = Response(200, 'application/json', b'{}')
        self.assertEqual(self.invoke([*args, '--output-format', 'text'])[0], 1)

    def test_args_file_expression_file_and_binary_output(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            args_file = directory / '参数.json'
            js_file = directory / '表达式.js'
            args_file.write_text('{"tabId":"from-file"}', encoding='utf-8-sig')
            js_file.write_text('document.title\n', encoding='utf-8-sig')
            code, _ = self.invoke(['--user-id', 'a', 'evaluate', '--args-file', str(args_file),
                                   '--tab-id', 'explicit', '--expression-file', str(js_file)])
            self.assertEqual(code, 0)
            call = self.transport.send.call_args
            self.assertEqual(call.args[1], '/tabs/explicit/evaluate')
            self.assertEqual(call.kwargs['body']['expression'], 'document.title\n')
            code, _ = self.invoke(['--user-id', 'a', 'evaluate', '--tab-id', 'tab', '--expression', '1',
                                   '--expression-file', str(js_file)])
            self.assertEqual(code, 1)
            args_file.write_text('[]', encoding='utf-8')
            self.assertEqual(self.invoke(['--user-id', 'a', 'tabs', '--args-file', str(args_file)])[0], 1)
            binary = b'\x89PNG\r\n'
            self.transport.send.return_value = Response(200, 'image/png', binary)
            args = ['--user-id', 'a', 'screenshot', '--tab-id', 'tab']
            self.assertEqual(self.invoke(args)[0], 1)
            image = directory / '图片.png'
            code, output = self.invoke([*args, '--output', str(image)])
            self.assertEqual(code, 0)
            self.assertEqual(image.read_bytes(), binary)
            self.assertEqual(json.loads(output)['file'], str(image.resolve()))

    def test_empty_default_with_inline_comment_never_sends_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.env'
            path.write_text('CAMOFOX_USER_ID= # choose explicitly\n', encoding='utf-8')
            code, output = self.invoke(['--env-file', str(path), 'tabs'])
            self.assertEqual(code, 1)
            self.assertIn('user_id', json.loads(output)['error']['message'])
            self.transport.send.assert_not_called()
            code, _ = self.invoke(['--env-file', str(path), '--user-id', 'b', 'tabs'])
            self.assertEqual(code, 0)
            self.assertEqual(self.transport.send.call_args.kwargs['params']['userId'], 'b')

    def test_invalid_output_is_rejected_before_side_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            for output in (str(Path(directory) / 'missing' / 'result.json'), directory):
                code, _ = self.invoke(['--user-id', 'a', 'create', '--output', output])
                self.assertEqual(code, 1)
            with patch('main.tempfile.TemporaryFile', side_effect=PermissionError('not writable')):
                code, _ = self.invoke(['--user-id', 'a', 'create', '--output', str(Path(directory) / 'result.json')])
                self.assertEqual(code, 1)
        self.transport.send.assert_not_called()

    def test_save_failure_preserves_create_result_and_existing_file(self):
        self.transport.send.return_value = Response(200, 'application/json', b'{"tabId":"created-tab"}')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text('original', encoding='utf-8')
            with patch('main.os.replace', side_effect=PermissionError('replace denied')):
                code, output = self.invoke(['--user-id', 'a', 'create', '--output', str(path)])
            self.assertEqual(path.read_text(encoding='utf-8'), 'original')
            self.assertEqual(list(Path(directory).iterdir()), [path])
        result = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(result['phase'], 'output')
        self.assertEqual(result['result'], {'tabId': 'created-tab'})
        self.transport.send.assert_called_once()

    def test_save_failure_preserves_click_record_without_retry(self):
        self.transport.send.side_effect = [
            Response(200, 'application/json', b'{"tabs":[{"tabId":"tab"}]}'),
            Response(200, 'application/json', b'{"ok":true}'),
            Response(200, 'application/json', b'{"tabs":[{"tabId":"tab"},{"tabId":"new"}]}'),
        ]
        with tempfile.TemporaryDirectory() as directory, patch('main.write_output', side_effect=OSError('disk full')):
            code, output = self.invoke(['--user-id', 'a', 'click-and-observe', '--tab-id', 'tab', '--ref', 'e1',
                                       '--output', str(Path(directory) / 'result.json')])
        result = json.loads(output)
        self.assertEqual(code, 1)
        self.assertTrue(result['result']['click']['executed'])
        self.assertEqual(result['result']['click']['result'], {'ok': True})
        self.assertEqual(self.transport.send.call_count, 3)

    def test_binary_save_failure_reports_metadata_not_false_success(self):
        self.transport.send.return_value = Response(200, 'application/zip', b'PK-test')
        with tempfile.TemporaryDirectory() as directory, patch('main.write_output', side_effect=OSError('disk full')):
            code, output = self.invoke(['--user-id', 'a', 'request', '--method', 'GET',
                                       '--path', '/sessions/a/traces/one.zip', '--output', str(Path(directory) / 'one.zip')])
        result = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(result['phase'], 'output')
        self.assertEqual(result['result'], {'type': 'binary', 'status': 200, 'contentType': 'application/zip',
                                          'byteLength': 7, 'saved': False, 'bodyIncluded': False})
        self.assertNotIn('PK-test', output)

    def test_copied_skill_runs_from_other_cwd_with_unicode_and_spaces(self):
        with tempfile.TemporaryDirectory(prefix='skill test ') as directory:
            root = Path(directory)
            installed = root / '安装目录 with spaces' / 'camofox-client'
            shutil.copytree(SKILL, installed, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            cwd = root / 'caller'
            cwd.mkdir()
            (cwd / '.env').write_text('CAMOFOX_USER_ID=must-not-load\n', encoding='utf-8')
            script = installed / 'scripts' / 'main.py'
            for args in (['--help'], ['snapshot', '--help'], ['click-and-observe', '--help']):
                result = subprocess.run([sys.executable, str(script), *args], cwd=cwd, env=clean_env(),
                                        capture_output=True, text=True, encoding='utf-8', timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('usage:', result.stdout)
            result = subprocess.run([sys.executable, str(script), 'tabs'], cwd=cwd, env=clean_env(),
                                    capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 1)
            self.assertIn('user_id', json.loads(result.stdout)['error']['message'])

    def test_trace_zip_download_uses_api_key_and_saves_exact_bytes(self):
        payload = b'PK\x03\x04trace-fixture'

        def respond(record):
            if record['headers'].get('Authorization') != 'Bearer fake-api':
                return 403, {}, {'error': 'Forbidden'}
            return 200, {'Content-Type': 'application/zip'}, payload

        with http_service(respond) as (url, records), tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.zip'
            env = {**clean_env(), 'CAMOFOX_API_KEY': 'fake-api'}
            result = subprocess.run(
                [sys.executable, str(SKILL / 'scripts/main.py'), '--base-url', url, '--user-id', 'a',
                 'request', '--method', 'GET', '--path', '/sessions/a/traces/one.zip', '--output', str(path)],
                cwd=directory, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(len(records), 1)

    def test_subprocess_a_b_a_requests_and_utf8_stdin(self):
        with http_service() as (url, records), tempfile.TemporaryDirectory() as cwd:
            env = {**clean_env(), 'CAMOFOX_USER_ID': 'ambient'}
            for user in ('a', 'b', 'a'):
                result = subprocess.run(
                    [sys.executable, str(SKILL / 'scripts' / 'main.py'), '--base-url', url,
                     '--user-id', user, 'snapshot', '--args-file', '-'],
                    input=json.dumps({'tabId': f'tab-{user}'}), cwd=cwd, env=env,
                    capture_output=True, text=True, encoding='utf-8', timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run(
                [sys.executable, str(SKILL / 'scripts' / 'main.py'), '--base-url', url,
                 '--user-id', 'a', 'evaluate', '--args-file', '-'],
                input=json.dumps({'tabId': 'tab-a', 'expression': '"中文"'}, ensure_ascii=False),
                cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([parse_qs(urlsplit(record['path']).query)['userId'][0] for record in records[:3]],
                         ['a', 'b', 'a'])
        self.assertEqual([urlsplit(record['path']).path for record in records[:3]],
                         ['/tabs/tab-a/snapshot', '/tabs/tab-b/snapshot', '/tabs/tab-a/snapshot'])
        self.assertEqual(records[3]['body']['expression'], '"中文"')
