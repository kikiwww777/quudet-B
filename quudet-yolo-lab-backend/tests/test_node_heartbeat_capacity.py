import unittest
from types import SimpleNamespace

from app.api.routes.nodes import _mark_node_offline, _merge_reported_running_jobs


class NodeHeartbeatCapacityTests(unittest.TestCase):
    def test_idle_agent_heartbeat_cannot_clear_a_reserved_slot(self) -> None:
        self.assertEqual(_merge_reported_running_jobs(current=1, reported=0), 1)

    def test_agent_heartbeat_can_raise_the_recorded_running_count(self) -> None:
        self.assertEqual(_merge_reported_running_jobs(current=0, reported=1), 1)

    def test_marking_node_offline_releases_reserved_slots(self) -> None:
        node = SimpleNamespace(status="ONLINE", running_jobs=1)

        _mark_node_offline(node)

        self.assertEqual(node.status, "OFFLINE")
        self.assertEqual(node.running_jobs, 0)


if __name__ == "__main__":
    unittest.main()
