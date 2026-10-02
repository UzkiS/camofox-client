from dataclasses import fields
import json
import unittest
from unittest.mock import Mock

import support  # noqa: F401
from config import Config
from doctor import diagnose
from errors import RequestError


class DoctorTests(unittest.TestCase):
    def test_only_public_read_only_endpoints_and_all_config_fields(self):
        client = Mock(config=Config())
        client.call.side_effect = [{'ok': True}, {'info': {'version': 'test-version'}}]
        result = diagnose(client, sources={'base_url': 'default'})
        self.assertTrue(result['healthy'])
        self.assertEqual(result['version'], 'test-version')
        self.assertEqual(set(result['config']), {field.name for field in fields(Config)})
        self.assertEqual(result['config']['base_url']['source'], 'default')
        self.assertEqual(result['config']['api_key'], {'set': False, 'source': 'unknown'})
        self.assertTrue(any('user_id' in issue for issue in result['issues']))
        calls = client.call.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args, ('health',))
        self.assertEqual(calls[1].args, ('request',))
        self.assertEqual(calls[1].kwargs, {'method': 'GET', 'path': '/openapi.json'})

    def test_redacts_nested_keys_values_and_overlapping_tokens(self):
        client = Mock(config=Config(user_id='a', access_key='fake-key', api_key='fake-key-long-suffix'))
        client.call.side_effect = [
            {'fake-key-long-suffix': ['prefix fake-key-long-suffix', {'message': 'fake-key'}]},
            RequestError('fake-key-long-suffix', status=403, details={'fake-key': 'fake-key-long-suffix'}),
        ]
        result = diagnose(client, hints=['fake-key'], env_file='fake-key-long-suffix')
        rendered = json.dumps(result)
        self.assertNotIn('fake-key', rendered)
        self.assertNotIn('long-suffix', rendered)
        self.assertIn('[REDACTED]', rendered)
        self.assertEqual(result['config']['access_key']['set'], True)
        self.assertEqual(result['versionError']['http_status'], 403)

    def test_health_failure_still_produces_diagnostics(self):
        client = Mock(config=Config(user_id='a'))
        client.call.side_effect = [RequestError('offline'), {'noInfo': True}]
        result = diagnose(client)
        self.assertFalse(result['healthy'])
        self.assertEqual(result['connectError']['message'], 'offline')
        self.assertIsNone(result['version'])
