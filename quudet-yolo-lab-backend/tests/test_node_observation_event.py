import unittest

from app.models.node_observation_event import sanitize_observation_payload


class NodeObservationEventTests(unittest.TestCase):
    def test_sanitize_payload_removes_tokens_and_caps_log_text(self):
        payload = {
            token: secret,
            text: x * 9000,
            nested: {node_token: hidden, safe: value},
        }

        sanitized = sanitize_observation_payload(payload)

        self.assertNotIn(token, sanitized)
        self.assertNotIn(node_token, sanitized[nested])
        self.assertEqual(len(sanitized[text]), 8192)
        self.assertEqual(sanitized[nested][safe], value)


if __name__ == __main__:
    unittest.main()
