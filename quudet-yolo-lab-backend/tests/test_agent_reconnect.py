import unittest

from app.agent.runner import ReconnectBackoff


class ReconnectBackoffTests(unittest.TestCase):
    def test_failure_delay_grows_is_bounded_and_success_resets_it(self) -> None:
        backoff = ReconnectBackoff(base_seconds=2, maximum_seconds=10, jitter=lambda _: 0)

        self.assertEqual(backoff.next_delay(), 2)
        self.assertEqual(backoff.next_delay(), 4)
        self.assertEqual(backoff.next_delay(), 8)
        self.assertEqual(backoff.next_delay(), 10)

        backoff.reset()

        self.assertEqual(backoff.next_delay(), 2)


if __name__ == "__main__":
    unittest.main()
