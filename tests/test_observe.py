import unittest
from unittest.mock import Mock

from support import Clock
from config import Config
from errors import RequestError
from observe import click_and_observe


BASELINE = {'tabs': [{'tabId': 'a', 'url': 'https://source', 'listItemId': 'group-a'}]}


class ObserveTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.client = Mock(config=Config(user_id='profile-a', timeout=30))

    def observe(self, **kwargs):
        return click_and_observe(self.client, tabId='a', ref='e1',
                                 clock=self.clock, sleep=self.clock.sleep, **kwargs)

    def test_baseline_failure_never_clicks(self):
        self.client.call.side_effect = RequestError('baseline unavailable')
        result = self.observe()
        self.assertEqual(result['observe']['endedBy'], 'baseline_failed')
        self.assertFalse(result['click']['executed'])
        self.assertEqual(self.client.call.call_count, 1)
        self.assertEqual(self.client.call.call_args.args, ('tabs',))

    def test_click_once_read_only_candidates_and_no_group_filter(self):
        after = {'tabs': [*BASELINE['tabs'], {'tabId': 'b', 'url': 'https://auth', 'listItemId': 'other-group'},
                          {'tabId': 'c', 'url': 'https://unrelated'}]}
        self.client.call.side_effect = [BASELINE, {'ok': True}, after]
        result = self.observe(expectUrlPrefix='https://auth')
        self.assertEqual(result['observe']['endedBy'], 'new_tabs_found')
        self.assertEqual([entry['tabId'] for entry in result['candidates']], ['b', 'c'])
        self.assertEqual([entry['matchesExpectPrefix'] for entry in result['candidates']], [True, False])
        self.assertEqual([call.args[0] for call in self.client.call.call_args_list], ['tabs', 'click', 'tabs'])
        self.assertEqual(result['click']['result'], {'ok': True})

    def test_click_error_is_uncertain_and_never_retried(self):
        self.client.call.side_effect = [BASELINE, RequestError('click timed out'), RequestError('observe failed')]
        result = self.observe()
        self.assertTrue(result['click']['executed'])
        self.assertTrue(result['click']['uncertain'])
        self.assertIn('click timed out', result['click']['error']['message'])
        self.assertEqual(result['observe']['endedBy'], 'observe_error')
        self.assertEqual([call.args[0] for call in self.client.call.call_args_list], ['tabs', 'click', 'tabs'])

    def test_slow_click_gets_a_separate_observation_budget(self):
        def call(action, **kwargs):
            if action == 'click':
                self.clock.sleep(20)
                return {'ok': True}
            return BASELINE

        self.client.call.side_effect = call
        result = self.observe(observeTimeout=1, pollInterval=0.25, requestTimeout=0.5)
        self.assertEqual(result['observe']['endedBy'], 'timeout')
        self.assertEqual(self.clock.now, 21)
        self.assertEqual(result['polls'], 4)
        calls = self.client.call.call_args_list
        self.assertEqual(calls[0].kwargs['timeout'], 0.5)
        self.assertNotIn('timeout', calls[1].kwargs)
        self.assertEqual([call.kwargs['timeout'] for call in calls[2:]], [0.5, 0.5, 0.5, 0.25])

    def test_direct_call_validates_before_any_request(self):
        for key in ('observeTimeout', 'pollInterval', 'requestTimeout'):
            for value in (0, float('nan'), float('inf'), True):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.observe(**{key: value})
        self.client.call.assert_not_called()

    def test_malformed_tab_elements_are_contained_before_and_after_click(self):
        for tab in (None, 'not-an-object', {}, {'tabId': None}, {'tabId': []}, {'tabId': '  '}):
            for after_click in (False, True):
                with self.subTest(tab=tab, after_click=after_click):
                    self.client.reset_mock()
                    replies = [BASELINE, {'ok': True}] if after_click else []
                    self.client.call.side_effect = [*replies, {'tabs': [tab]}]
                    result = self.observe()
                    self.assertEqual(result['observe']['endedBy'], 'observe_error' if after_click else 'baseline_failed')
                    self.assertEqual(result['click']['executed'], after_click)
                    self.assertEqual(self.client.call.call_count, 3 if after_click else 1)
                    if after_click:
                        self.assertEqual(result['click']['result'], {'ok': True})

    def test_late_candidate_is_not_accepted_after_deadline(self):
        polls = 0

        def call(action, **kwargs):
            nonlocal polls
            if action == 'click':
                return {'ok': True}
            polls += 1
            if polls == 1:
                return BASELINE
            self.clock.sleep(2)
            return {'tabs': [*BASELINE['tabs'], {'tabId': 'late'}]}

        self.client.call.side_effect = call
        result = self.observe(observeTimeout=1)
        self.assertEqual(result['observe']['endedBy'], 'timeout')
        self.assertEqual(result['candidates'], [])
        self.assertTrue(result['click']['executed'])

    def test_bad_tabs_shape_is_a_baseline_failure(self):
        self.client.call.return_value = {'notTabs': []}
        result = self.observe()
        self.assertEqual(result['observe']['endedBy'], 'baseline_failed')
        self.assertFalse(result['click']['executed'])
