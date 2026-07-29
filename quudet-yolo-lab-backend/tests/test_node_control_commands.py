import unittest
from types import SimpleNamespace

from app.api.routes.nodes import _acknowledge_control_command, _create_control_command, _next_control_command


class NodeControlCommandTests(unittest.TestCase):
    def test_command_is_owned_expiring_and_acknowledged_once(self) -> None:
        node = SimpleNamespace(id="node-a", capabilities={})
        command = _create_control_command(node, "RECONNECT", requester="operator")

        self.assertEqual(command["action"], "RECONNECT")
        self.assertEqual(_next_control_command(node)["id"], command["id"])
        self.assertTrue(_acknowledge_control_command(node, command["id"], result="registered"))
        self.assertFalse(_acknowledge_control_command(node, command["id"], result="registered"))
        self.assertIsNone(_next_control_command(node))

    def test_unknown_command_action_is_rejected(self) -> None:
        node = SimpleNamespace(id="node-a", capabilities={})

        with self.assertRaises(ValueError):
            _create_control_command(node, "SHELL", requester="operator")


if __name__ == "__main__":
    unittest.main()
