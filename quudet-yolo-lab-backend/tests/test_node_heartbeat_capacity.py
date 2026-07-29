import unittest
from types import SimpleNamespace

from app.api.routes.nodes import _mark_node_offline, _merge_node_capabilities, _merge_reported_running_jobs, _recover_lost_job


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

    def test_lost_job_is_requeued_with_bounded_recovery_count(self) -> None:
        job = SimpleNamespace(status="RUNNING", dispatch_status="RUNNING_REMOTE", assigned_node_id="node-1", recovery_attempts=0, error_message=None)

        recovered = _recover_lost_job(job, "node-1")

        self.assertTrue(recovered)
        self.assertEqual(job.status, "PENDING_ASSIGN")
        self.assertEqual(job.dispatch_status, "RECOVERY_PENDING")
        self.assertIsNone(job.assigned_node_id)
        self.assertEqual(job.recovery_attempts, 1)

    def test_lost_job_fails_only_after_its_recovery_budget(self) -> None:
        job = SimpleNamespace(status="RUNNING", dispatch_status="RUNNING_REMOTE", assigned_node_id="node-1", recovery_attempts=2, error_message=None)

        recovered = _recover_lost_job(job, "node-1")

        self.assertFalse(recovered)
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(job.dispatch_status, "FAILED_REMOTE")


if __name__ == "__main__":
    unittest.main()
