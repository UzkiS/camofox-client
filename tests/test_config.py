from dataclasses import FrozenInstanceError, fields
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import support  # noqa: F401 -- makes the installed-style script modules importable
from config import Config, build_config, env_names, load_config, read_env_file, resolve_config


class ConfigTests(unittest.TestCase):
    def test_empty_values_and_comments_preserve_identity_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.env'
            path.write_text('CAMOFOX_USER_ID= # choose explicitly\n'
                            'CAMOFOX_ACCESS_KEY=  # no default key\n'
                            'CAMOFOX_API_KEY="" # compare "another key"\n', encoding='utf-8')
            config, _, _ = build_config(env_file=path, environ={})
        self.assertEqual((config.user_id, config.access_key, config.api_key), ('', '', ''))

    def test_quoted_values_stop_before_comment_quotes(self):
        cases = [
            ('"profile-a" # compare "profile-b"', 'profile-a'),
            ("'profile-a' # compare 'profile-b'", 'profile-a'),
            ('  "profile-a"  ', 'profile-a'),
            ('a#b # comment', 'a#b'),
            ('#literal', '#literal'),
            ('"a # b" # comment', 'a # b'),
            ('"${USER}"', '${USER}'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.env'
            for value, expected in cases:
                with self.subTest(value=value):
                    path.write_text('CAMOFOX_USER_ID=' + value, encoding='utf-8')
                    self.assertEqual(read_env_file(path)['CAMOFOX_USER_ID'], expected)
            for value in ('"not closed', '"a" trailing', "'a' extra 'b'"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    path.write_text('CAMOFOX_USER_ID=' + value, encoding='utf-8')
                    read_env_file(path)

    def test_defaults_and_immutable_identity(self):
        with patch.dict(os.environ, {'CAMOFOX_USER_ID': 'ambient'}, clear=True):
            config = Config()
        self.assertEqual(config.user_id, '')
        self.assertEqual(config.session_key, 'default')
        self.assertEqual(config.base_url, 'http://localhost:9377')
        with self.assertRaises(FrozenInstanceError):
            config.user_id = 'other'

    def test_precedence_sources_and_empty_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'explicit.env'
            path.write_text('CAMOFOX_USER_ID=file\nCAMOFOX_TIMEOUT=12\nCAMOFOX_SESSION_KEY=group\n', encoding='utf-8')
            env = {'CAMOFOX_USER_ID': 'env', 'CAMOFOX_TIMEOUT': '8'}
            config, sources, _ = build_config({'user_id': 'explicit'}, env_file=path, environ=env)
            self.assertEqual((config.user_id, config.timeout, config.session_key), ('explicit', 8, 'group'))
            self.assertEqual(sources['user_id'], 'cli')
            self.assertEqual(sources['timeout'], 'env')
            self.assertEqual(sources['session_key'], 'file')
            self.assertEqual(sources['base_url'], 'default')
            empty, _, _ = build_config(env_file=path, environ={'CAMOFOX_USER_ID': ''})
            self.assertEqual(empty.user_id, '')
            empty, _, _ = build_config({'user_id': ''}, env_file=path, environ=env)
            self.assertEqual(empty.user_id, '')

    def test_only_explicit_file_is_read(self):
        with patch('config.read_env_file', side_effect=AssertionError('implicit file read')):
            self.assertEqual(build_config(environ={})[0], Config())
        with patch.dict(os.environ, {'CAMOFOX_USER_ID': 'environment'}, clear=True):
            self.assertEqual(load_config().user_id, 'environment')

    def test_env_syntax_and_no_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '配置.env'
            path.write_text('﻿# comment\nexport CAMOFOX_USER_ID="user a" # comment\n'
                            "CAMOFOX_SESSION_KEY='${USER}'\nCAMOFOX_TIMEOUT=3 # seconds\n", encoding='utf-8')
            self.assertEqual(read_env_file(path), {'CAMOFOX_USER_ID': 'user a',
                             'CAMOFOX_SESSION_KEY': '${USER}', 'CAMOFOX_TIMEOUT': '3'})
            path.write_text('CAMOFOX_USER_ID="unterminated', encoding='utf-8')
            with self.assertRaises(ValueError):
                read_env_file(path)

    def test_unknown_names_are_hints_not_values(self):
        config, _, hints = build_config(environ={'CAMOFOX_UNKNOWN': 'unused', 'OTHER_APP_USER': 'other'})
        self.assertEqual(config.base_url, Config().base_url)
        self.assertEqual(config.user_id, '')
        self.assertEqual(len(hints), 1)
        self.assertIn('CAMOFOX_UNKNOWN', hints[0])
        self.assertNotIn('OTHER_APP', hints[0])

    def test_bad_config(self):
        for overrides in ({'timeout': True}, {'timeout': 0}, {'timeout': float('nan')},
                          {'timeout': float('inf')}, {'timeout': '3'}, {'session_key': ' '},
                          {'user_id': 'a\nb'}, {'unknown': 'x'}):
            with self.subTest(overrides=overrides), self.assertRaises((ValueError, TypeError)):
                build_config(overrides, environ={})
        for url in ('file:///tmp', 'http://u:p@host', 'http://host?query', 'http://host#fragment',
                    'http://host:99999', 'http://host name', 'relative'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Config(base_url=url)
        self.assertEqual(Config(base_url='http://localhost:9377/').base_url, 'http://localhost:9377')

    def test_resolution_does_not_reapply_overrides(self):
        values, sources, _ = resolve_config({'timeout': 4}, environ={'CAMOFOX_TIMEOUT': '9'})
        self.assertEqual(values, {'timeout': 4})
        self.assertEqual(sources['timeout'], 'cli')
        self.assertEqual(build_config({'timeout': 4}, environ={})[0].timeout, 4)

    def test_config_drives_names_and_secret_repr(self):
        self.assertEqual(env_names(), {'CAMOFOX_' + field.name.upper() for field in fields(Config)})
        config = Config(access_key='fake-access-token', api_key='fake-api-token')
        self.assertNotIn('fake-access-token', repr(config))
        self.assertNotIn('fake-api-token', repr(config))
        self.assertEqual({field.name for field in fields(config) if field.metadata.get('secret')},
                         {'access_key', 'api_key'})
