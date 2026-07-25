import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent.runtime_paths import get_agent_paths


class AgentRuntimePathsTests(unittest.TestCase):
    def test_uses_agent_environment_without_control_plane_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(
                os.environ,
                {
                    "YOLO_WORK_DIR": str(root / "yolo"),
                    "DATA_DIR": str(root / "data"),
                },
                clear=False,
            ):
                paths = get_agent_paths()

        self.assertEqual(paths.yolo_work_dir, root / "yolo")
        self.assertEqual(paths.artifacts_dir, root / "data" / "artifacts")
        self.assertEqual(
            paths.provision_cache_dir,
            root / "data" / "artifacts" / "provision_cache",
        )


if __name__ == "__main__":
    unittest.main()
