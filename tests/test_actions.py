import unittest

import support  # noqa: F401
from actions import ACTIONS, COMPOSITE, Field
from main import COMPOSITE_HANDLERS, build_parser, flag


class ActionTests(unittest.TestCase):
    def test_required_unknown_and_target_parameters(self):
        for name, args in [('click', {}), ('click', {'tabId': 'tab'}),
                           ('tabs', {'userId': 'other'}), ('snapshot', {'tabId': 'tab', 'offset': -1}),
                           ('snapshot', {'tabId': 'tab', 'offset': True})]:
            with self.subTest(action=name, args=args), self.assertRaises(ValueError):
                ACTIONS[name].validate(args)
        ACTIONS['click'].validate({'tabId': 'tab', 'ref': 'e1'})
        ACTIONS['type'].validate({'tabId': 'tab', 'mode': 'keyboard', 'text': ''})

    def test_navigation_requires_exactly_one_destination(self):
        for args in ({'tabId': 'tab'}, {'tabId': 'tab', 'url': 'https://example.com', 'macro': 'x'},
                     {'tabId': 'tab', 'url': 'file:///etc/passwd'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ACTIONS['navigate'].validate(args)
        ACTIONS['navigate'].validate({'tabId': 'tab', 'url': 'https://example.com'})
        ACTIONS['create'].validate({})

    def test_float_constraints_do_not_accept_nan_infinity_or_bool(self):
        spec = COMPOSITE['click-and-observe']
        for value in (0, -1, True, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                spec.validate({'tabId': 'tab', 'ref': 'e1', 'observeTimeout': value})
        spec.validate({'tabId': 'tab', 'ref': 'e1', 'observeTimeout': 1})
        with self.assertRaises(ValueError):
            Field(choices=('text', 'json')).validate('format', 'html')

    def test_registry_and_generated_parser_are_complete(self):
        self.assertEqual(set(COMPOSITE_HANDLERS), set(COMPOSITE))
        self.assertFalse(set(ACTIONS) & set(COMPOSITE))
        parser = build_parser()
        subparsers = next(action for action in parser._actions if action.dest == 'action')
        self.assertEqual(set(subparsers.choices), set(ACTIONS) | set(COMPOSITE))
        for name, spec in {**ACTIONS, **COMPOSITE}.items():
            options = subparsers.choices[name]._option_string_actions
            for field in spec.fields:
                self.assertIn(flag(field), options)
        self.assertNotIn('--access-key', parser._option_string_actions)
        self.assertNotIn('--api-key', parser._option_string_actions)
