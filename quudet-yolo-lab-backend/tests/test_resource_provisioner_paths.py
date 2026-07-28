import tempfile
import unittest
from pathlib import Path

from app.schemas.provisioning import ManifestDelivery
from app.agent.resource_provisioner import _relative_symlink_target


class ResourceProvisionerPathTests(unittest.TestCase):
    def test_builds_relative_symlink_target_without_python_312_walk_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content_target = root / "content" / "sha256-demo" / "VOC"
            alias_parent = root / "datasets"

            target = _relative_symlink_target(content_target, alias_parent)

        self.assertEqual(target.replace("\\", "/"), "../content/sha256-demo/VOC")

    def test_manifest_delivery_keeps_generic_output_contract(self) -> None:
        delivery = ManifestDelivery(
            target_relative_path="datasets/custom",
            preparer_kind="custom",
            output_data_yaml_path="prepared/data.yaml",
            preparer_options={"script": "prepare.py"},
        )

        self.assertEqual(delivery.output_data_yaml_path, "prepared/data.yaml")
        self.assertEqual(delivery.preparer_kind, "custom")


if __name__ == "__main__":
    unittest.main()
