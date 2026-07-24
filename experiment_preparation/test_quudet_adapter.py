import unittest
from unittest.mock import patch

from experiment_preparation import quudet_adapter


class TestQuuDetPreparationGate(unittest.TestCase):
    def test_collects_dataset_and_weight_resources(self):
        resource_ids = quudet_adapter._collect_resource_ids(
            "voc",
            [{"payload": {"data": "voc.yaml", "model": "yolo11n.pt"}}],
            None,
        )

        self.assertEqual(
            resource_ids,
            {"dataset:voc:2012-yolo", "weight:ultralytics:yolo11n"},
        )

    def test_backend_unavailable_blocks_resource_managed_experiment(self):
        with patch.object(quudet_adapter, "_get_backend_session", return_value=None):
            result = quudet_adapter.check_experiment_group(
                {
                    "dataset_name": "voc",
                    "runs": [{"payload": {"data": "voc.yaml"}}],
                }
            )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "blocked")

    def test_conflicting_explicit_targets_are_blocked(self):
        result = quudet_adapter.check_experiment_group(
            {
                "dataset_name": "voc",
                "runs": [
                    {"target_node_id": "node-a", "payload": {"data": "voc.yaml"}},
                    {"target_node_id": "node-b", "payload": {"data": "voc.yaml"}},
                ],
            }
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "blocked")

    def test_unmanaged_resources_do_not_block_multi_node_group(self):
        result = quudet_adapter.check_experiment_group(
            {
                "dataset_name": "custom-dataset",
                "runs": [
                    {"target_node_id": "node-a", "payload": {"data": "custom.yaml"}},
                    {"target_node_id": "node-b", "payload": {"data": "custom.yaml"}},
                ],
            }
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["status"], "ready")

    def test_gpu_requirement_is_detected_from_payload(self):
        self.assertTrue(
            quudet_adapter._requires_gpu(
                [{"payload": {"device": "cuda:0"}}],
                None,
            )
        )

    def test_select_target_requires_common_node_and_prefers_cache_hits(self):
        resource_plans = [
            {
                "nodes": [
                    {"node_id": "node-a", "cache_hit": True, "free_capacity": 1},
                    {"node_id": "node-b", "cache_hit": False, "free_capacity": 3},
                ]
            },
            {
                "nodes": [
                    {"node_id": "node-a", "cache_hit": True, "free_capacity": 1},
                    {"node_id": "node-b", "cache_hit": True, "free_capacity": 3},
                ]
            },
        ]

        self.assertEqual(quudet_adapter._select_target_node(resource_plans), "node-a")

    def test_select_target_rejects_disjoint_resource_nodes(self):
        resource_plans = [
            {"nodes": [{"node_id": "node-a", "cache_hit": True, "free_capacity": 1}]},
            {"nodes": [{"node_id": "node-b", "cache_hit": True, "free_capacity": 1}]},
        ]

        self.assertIsNone(quudet_adapter._select_target_node(resource_plans))


if __name__ == "__main__":
    unittest.main()
