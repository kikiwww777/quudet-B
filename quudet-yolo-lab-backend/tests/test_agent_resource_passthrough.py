import unittest

from app.schemas.experiment import ExperimentGroupCreate, ExperimentRunCreate


class AgentResourcePassthroughTests(unittest.TestCase):
    def test_experiment_payload_keeps_explicit_resource_manifest(self) -> None:
        resource = {
            "resource_id": "dataset:example:2026",
            "source": {"url": "https://example.test/dataset.zip"},
            "delivery": {"output_data_yaml_path": "data.yaml"},
        }
        group = ExperimentGroupCreate(
            name="generic resource test",
            resources=[resource],
            runs=[ExperimentRunCreate(payload={"model": "yolo11n.pt", "data": "cache://datasets/example"})],
        )

        self.assertEqual(group.model_dump()["resources"][0]["source"]["url"], resource["source"]["url"])


if __name__ == "__main__":
    unittest.main()
