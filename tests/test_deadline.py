import os
import socket
import ssl
import time
import unittest
from unittest.mock import Mock, patch

from support import Clock, clean_env, http_service
from client import Client
from config import Config
from deadline import Deadline, _HTTPConnection, _HTTPSConnection
from errors import RequestError
from observe import click_and_observe
from transport import HttpTransport


def slow_response(handler, *, status=200, slow_headers=False):
    if slow_headers:
        handler.wfile.write(b'HTTP/1.1 200 OK\r\nX-Slow: ')
        handler.wfile.flush()
        payload = b'x' * 40 + b'\r\nContent-Length: 0\r\n\r\n'
    else:
        payload = b'{"tabs":[],"padding":"' + b'x' * 40 + b'"}'
        handler.send_response(status)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(payload)))
        handler.end_headers()
    for byte in payload:
        handler.wfile.write(bytes([byte]))
        handler.wfile.flush()
        time.sleep(0.04)


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, clean_env(), clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_slow_headers_body_and_error_body_share_a_total_budget(self):
        for status, headers in ((200, False), (200, True), (503, False)):
            with self.subTest(status=status, headers=headers):
                with http_service(write_response=lambda record, handler: slow_response(
                        handler, status=status, slow_headers=headers)) as (url, records):
                    transport = HttpTransport(url, 0.25)
                    started = time.monotonic()
                    with self.assertRaises(RequestError) as caught:
                        transport.send('GET', '/health')
                    elapsed = time.monotonic() - started
                self.assertLess(elapsed, 1.25)  # Slow stream takes >2s without an absolute network budget.
                self.assertEqual(len(records), 1)
                if not headers:
                    self.assertEqual(caught.exception.status, status)

    def test_observation_deadline_retains_click_without_late_candidates(self):
        reads = 0

        def respond(record, handler):
            nonlocal reads
            if record['method'] == 'GET':
                reads += 1
                if reads > 1:
                    return slow_response(handler)
                payload = b'{"tabs":[{"tabId":"a"}]}'
            else:
                payload = b'{"ok":true}'
            handler.send_response(200)
            handler.send_header('Content-Type', 'application/json')
            handler.send_header('Content-Length', str(len(payload)))
            handler.end_headers()
            handler.wfile.write(payload)

        with http_service(write_response=respond) as (url, records):
            client = Client(Config(base_url=url, user_id='a'))
            started = time.monotonic()
            result = click_and_observe(client, tabId='a', ref='e1', observeTimeout=0.25, requestTimeout=1)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.25)
        self.assertEqual(result['observe']['endedBy'], 'timeout')
        self.assertTrue(result['click']['executed'])
        self.assertEqual(result['click']['result'], {'ok': True})
        self.assertEqual(result['candidates'], [])
        self.assertEqual([record['method'] for record in records], ['GET', 'POST', 'GET'])

    def test_address_attempts_use_remaining_budget(self):
        clock = Clock()
        first, second = Mock(), Mock()

        def fail_connect(address):
            clock.sleep(0.7)
            raise OSError('first address unavailable')

        first.connect.side_effect = fail_connect
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port)) for port in (1, 2)]
        with patch('deadline.time.monotonic', clock), patch('deadline.socket.getaddrinfo', return_value=addresses), \
                patch('deadline.socket.socket', side_effect=[first, second]):
            connection = _HTTPConnection('example.invalid', deadline=Deadline(1))
            self.assertIs(connection._connect_socket(('example.invalid', 80)), second)
        first.close.assert_called_once()
        self.assertAlmostEqual(first.settimeout.call_args_list[0].args[0], 1)
        self.assertAlmostEqual(second.settimeout.call_args_list[0].args[0], 0.3)

    def test_expired_dns_result_does_not_start_a_connection(self):
        clock = Clock()

        def resolve(*args):
            clock.sleep(2)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]

        with patch('deadline.time.monotonic', clock), patch('deadline.socket.getaddrinfo', side_effect=resolve), \
                patch('deadline.socket.socket') as create_socket:
            connection = _HTTPConnection('example.invalid', deadline=Deadline(1))
            with self.assertRaises(TimeoutError):
                connection._connect_socket(('example.invalid', 80))
            create_socket.assert_not_called()

    def test_http_proxy_receives_the_absolute_target_url(self):
        with http_service() as (proxy, records), patch.dict(os.environ, {'http_proxy': proxy}), \
                patch('urllib.request.proxy_bypass', return_value=False):
            response = HttpTransport('http://example.invalid', 1).send('GET', '/health')
        self.assertEqual(response.json(), {'ok': True})
        self.assertEqual(records[0]['path'], 'http://example.invalid/health')

    def test_slow_https_proxy_connect_headers_use_remaining_budget(self):
        def respond(record, handler):
            slow_response(handler, slow_headers=True)

        with http_service(write_response=respond) as (proxy, records), \
                patch.dict(os.environ, {'https_proxy': proxy}), \
                patch('urllib.request.proxy_bypass', return_value=False):
            transport = HttpTransport('https://example.invalid', 0.25)
            started = time.monotonic()
            with self.assertRaises(RequestError):
                transport.send('GET', '/health')
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.25)
        self.assertEqual(records[0]['method'], 'CONNECT')
        self.assertEqual(records[0]['path'], 'example.invalid:443')

    def test_tls_verification_defaults_are_preserved(self):
        connection = _HTTPSConnection('example.invalid', deadline=Deadline(1))
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(connection._context.check_hostname)

    def test_invalid_deadlines_are_rejected(self):
        for value in (0, -1, True, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Deadline(value)
