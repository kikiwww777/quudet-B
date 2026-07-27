import tempfile
import unittest
from pathlib import Path

from app.agent.resource_provisioner import _relative_symlink_target


class ResourceProvisionerPathTests(unittest.TestCase):
    def test_builds_relative_symlink_target_without_python_312_walk_up(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content_target = root / "content" / "sha256-demo" / "VOC"
            alias_parent = root / "datasets"

            target = _relative_symlink_target(content_target, alias_parent)

        self.assertEqual(target.replace("\\", "/"), "../content/sha256-demo/VOC")


if __name__ == "__main__":
    unittest.main()
