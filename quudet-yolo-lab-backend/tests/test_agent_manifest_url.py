import unittest
from urllib.parse import parse_qs

from app.agent import runner


class ManifestQueryEncodingTests(unittest.TestCase):
    def test_manifest_auth_query_percent_encodes_unicode_credentials(self) -> None:
        node_id = "node-\u4e2d\u6587"
        token = "\u4ee4\u724c-\u4e2d\u6587"

        query = runner._build_node_auth_query(node_id, token)

        self.assertNotIn("\u4e2d\u6587", query)
        self.assertEqual(
            parse_qs(query),
            {"node_id": [node_id], "token": [token]},
        )


if __name__ == "__main__":
    unittest.main()
