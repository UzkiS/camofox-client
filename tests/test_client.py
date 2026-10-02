import json
import unittest
from unittest.mock import Mock
from urllib.parse import quote

import support  # noqa: F401
from client import Client
from config import Config
from errors import RequestError
from transport import Response


def response(value, status=200):
    return Response(status, 'application/json', json.dumps(value).encode())


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.transport.send.return_value = response({'ok': True})
        self.client = Client(Config(user_id='a', session_key='group-a', access_key='fake-a'),
                             transport=self.transport)

    def test_a_b_a_clients_keep_identity_group_auth_and_tab(self):
        other = Client(Config(user_id='b', session_key='group-b', access_key='fake-b'),
                       transport=self.transport)
        for client, tab in ((self.client, 'tab-a'), (other, 'tab-b'), (self.client, 'tab-a')):
            client.call('create')
            client.call('snapshot', tabId=tab)
        for index, (user, group, token, tab) in enumerate([
                ('a', 'group-a', 'fake-a', 'tab-a'), ('b', 'group-b', 'fake-b', 'tab-b'),
                ('a', 'group-a', 'fake-a', 'tab-a')]):
            create, snapshot = self.transport.send.call_args_list[index * 2:index * 2 + 2]
            self.assertEqual(create.args, ('POST', '/tabs'))
            self.assertEqual(create.kwargs['body'], {'userId': user, 'sessionKey': group})
            self.assertEqual(create.kwargs['token'], token)
            self.assertEqual(snapshot.args, ('GET', f'/tabs/{tab}/snapshot'))
            self.assertEqual(snapshot.kwargs['params'], {'userId': user})
            self.assertEqual(snapshot.kwargs['token'], token)

    def test_no_default_user_fails_before_transport(self):
        client = Client(Config(), transport=self.transport)
        for action, args in [('tabs', {}), ('create', {}), ('request', {'method': 'GET', 'path': '/tabs'})]:
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'user_id'):
                client.call(action, **args)
        self.transport.send.assert_not_called()
        client.call('health')
        client.call('request', method='GET', path='/openapi.json')
        self.assertEqual(self.transport.send.call_count, 2)

    def test_cannot_override_identity_or_other_session_user(self):
        for args in ({'params': {'userId': 'b'}}, {'body': {'sessionKey': 'other'}}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.client.call('request', method='POST', path='/tabs', **args)
        with self.assertRaises(ValueError):
            self.client.call('request', method='POST', path='/sessions/b/cookies')
        self.transport.send.assert_not_called()

    def test_cookie_auth_and_encoded_identity(self):
        client = Client(Config(user_id='user / 中文', access_key='fake-access', api_key='fake-api'),
                        transport=self.transport)
        path = '/sessions/' + quote(client.config.user_id, safe='') + '/cookies'
        client.call('request', method='POST', path=path, body={'cookies': []})
        self.assertEqual(self.transport.send.call_args.kwargs['token'], 'fake-api')
        self.assertEqual(self.transport.send.call_args.kwargs['body']['userId'], 'user / 中文')
        self.client.call('request', method='POST', path='/sessions/a/cookies', body={'cookies': []})
        self.assertEqual(self.transport.send.call_args.kwargs['token'], 'fake-a')

    def test_trace_credentials_follow_endpoint_and_global_gate(self):
        for access, api, expected in [('fake-access', 'fake-api', 'fake-access'),
                                      ('', 'fake-api', 'fake-api'), ('fake-access', '', 'fake-access'), ('', '', '')]:
            client = Client(Config(user_id='a', access_key=access, api_key=api), transport=self.transport)
            for method, path in [('GET', '/sessions/a/traces'), ('GET', '/sessions/a/traces/one.zip'),
                                 ('DELETE', '/sessions/a/traces/one.zip')]:
                with self.subTest(access=bool(access), api=bool(api), method=method, path=path):
                    client.call('request', method=method, path=path)
                    self.assertEqual(self.transport.send.call_args.kwargs['token'], expected)
            for method, path in [('POST', '/sessions/a/traces'), ('DELETE', '/sessions/a/traces'),
                                 ('GET', '/sessions/a/traces-extra'), ('GET', '/sessions/a/traces/one/extra'),
                                 ('GET', '/health')]:
                client.call('request', method=method, path=path)
                self.assertEqual(self.transport.send.call_args.kwargs['token'], access)

    def test_trace_zip_is_returned_as_binary(self):
        binary = Response(200, 'application/zip', b'PK\x03\x04')
        self.transport.send.return_value = binary
        result = self.client.call('request', method='GET', path='/sessions/a/traces/one.zip')
        self.assertIs(result, binary)

    def test_response_contract(self):
        for value in ({'hello': 'world'}, [1, 2], None):
            self.transport.send.return_value = response(value)
            self.assertEqual(self.client.call('tabs'), value)
        self.transport.send.return_value = Response(204, '', b'')
        self.assertIsNone(self.client.call('close', tabId='tab-a'))
        binary = Response(200, 'image/png', b'png')
        self.transport.send.return_value = binary
        self.assertIs(self.client.call('screenshot', tabId='tab-a'), binary)
        for reply in (response({'ok': False}), response({'error': 'failed'}),
                      Response(200, 'text/html', b'<html>'), Response(200, 'application/json', b'bad')):
            self.transport.send.return_value = reply
            with self.subTest(response=reply), self.assertRaises(RequestError):
                self.client.call('tabs')

    def test_tab_encoding_timeout_and_input_not_mutated(self):
        self.client.call('snapshot', tabId='a/b', timeout=0.5)
        self.assertEqual(self.transport.send.call_args.args[1], '/tabs/a%2Fb/snapshot')
        self.assertEqual(self.transport.send.call_args.kwargs['timeout'], 0.5)
        body = {'url': 'https://example.com'}
        self.client.call('request', method='POST', path='/tabs', body=body)
        self.assertEqual(body, {'url': 'https://example.com'})
        with self.assertRaises(ValueError):
            self.client.call('request', method='GET', path='/tabs', body={})

    def test_transport_failure_is_not_retried(self):
        self.transport.send.side_effect = RequestError('timeout')
        with self.assertRaises(RequestError):
            self.client.call('click', tabId='tab-a', ref='e1')
        self.transport.send.assert_called_once()
