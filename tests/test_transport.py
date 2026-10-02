import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from support import clean_env, http_service
from errors import RequestError
from transport import HttpTransport, Response


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, clean_env(), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_wire_encoding_headers_and_json(self):
        with http_service() as (url, records):
            transport = HttpTransport(url, 3)
            result = transport.send('GET', '/tabs', params={'userId': '用户 a', 'enabled': True, 'none': None},
                                    token='fake-token')
            transport.send('POST', '/tabs', body={'userId': '用户 b'})
        self.assertEqual(result.json(), {'ok': True})
        self.assertEqual(parse_qs(urlsplit(records[0]['path']).query), {'userId': ['用户 a'], 'enabled': ['true']})
        self.assertEqual(records[0]['headers']['Authorization'], 'Bearer fake-token')
        self.assertEqual(records[1]['body'], {'userId': '用户 b'})
        self.assertTrue(records[1]['headers']['Content-Type'].startswith('application/json'))

    def test_invalid_paths_never_reach_network(self):
        transport = HttpTransport('http://localhost:9377', 3)
        transport.opener = Mock()
        for path in ('https://other/tabs', '//other/tabs', '/tabs?query', '/tabs#fragment',
                     '/a/../tabs', '/a/%2e%2e/tabs', '/a/%5cb', '/tabs\n', 'tabs'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                transport.send('GET', path)
        transport.opener.open.assert_not_called()

    def test_redirect_is_not_followed_and_token_not_forwarded(self):
        with http_service() as (destination, leaked):
            def redirect(record):
                return 302, {'Location': destination + '/target'}, {'error': 'redirect'}

            with http_service(redirect) as (source, records):
                with self.assertRaises(RequestError) as caught:
                    HttpTransport(source, 3).send('GET', '/tabs', token='fake-token')
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(len(records), 1)
        self.assertEqual(leaked, [])

    def test_http_error_status_and_details(self):
        for payload in ({'error': 'unavailable'}, b'plain failure'):
            with self.subTest(payload=payload):
                with http_service(lambda record: (503, {}, payload)) as (url, records):
                    with self.assertRaises(RequestError) as caught:
                        HttpTransport(url, 3).send('GET', '/health')
                self.assertEqual(caught.exception.status, 503)
                self.assertEqual(caught.exception.details, payload if isinstance(payload, dict) else 'plain failure')
                self.assertEqual(len(records), 1)

    def test_connection_error_and_timeout_override(self):
        transport = HttpTransport('http://localhost:9377', 3)
        transport.opener = Mock()
        transport.opener.open.side_effect = URLError('offline')
        with self.assertRaises(RequestError):
            transport.send('GET', '/health', timeout=0.25)
        self.assertEqual(transport.opener.open.call_args.kwargs['timeout'], 0.25)
        transport.opener.open.assert_called_once()

    def test_json_bom_and_invalid_json(self):
        self.assertEqual(Response(200, 'application/json', b'\xef\xbb\xbf{"ok":true}').json(), {'ok': True})
        for body in (b'not-json', b'\xff'):
            with self.subTest(body=body), self.assertRaises(RequestError):
                Response(200, 'application/json', body).json()
