import unittest

from runtime_state import RuntimeState


class RuntimeStateTests(unittest.TestCase):
    def test_bucketed_user_rate_limits_are_independent(self):
        state = RuntimeState()
        now = 1000.0
        self.assertTrue(state.allow_user_action(1, 'twitter', 10, now=now))
        self.assertFalse(state.allow_user_action(1, 'twitter', 10, now=now + 5))
        self.assertTrue(state.allow_user_action(1, 'tiktok', 10, now=now + 5))

    def test_global_limit(self):
        state = RuntimeState()
        now = 2000.0
        self.assertTrue(state.allow_global_request(2, now=now))
        self.assertTrue(state.allow_global_request(2, now=now + 1))
        self.assertFalse(state.allow_global_request(2, now=now + 2))


if __name__ == '__main__':
    unittest.main()
