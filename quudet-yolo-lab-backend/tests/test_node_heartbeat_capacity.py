import unittest
from types import SimpleNamespace

from app.api.routes.nodes import _mark_node_offline, _merge_node_capabilities, _merge_reported_running_jobs


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

    def test_idle_duplicate_agent_cannot_erase_active_runtime(self) -> None:
        current = {"agent_runtime": {"active_job_id": "job-1", "active_pid": 123}}
        reported = {"agent_runtime": {"active_job_id": None, "active_pid": None}}

        merged = _merge_node_capabilities(current, reported, preserve_active_runtime=True)

        self.assertEqual(merged["agent_runtime"]["active_job_id"], "job-1")

    def test_idle_heartbeat_clears_runtime_after_slot_is_released(self) -> None:
        current = {"agent_runtime": {"active_job_id": "job-1", "active_pid": 123}}
        reported = {"agent_runtime": {"active_job_id": None, "active_pid": None}}

        merged = _merge_node_capabilities(current, reported, preserve_active_runtime=False)

        self.assertIsNone(merged["agent_runtime"]["active_job_id"])


if __name__ == "__main__":
    unittest.main()
