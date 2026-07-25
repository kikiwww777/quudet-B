import tempfile
import unittest
from pathlib import Path

from app.services.yolo_runner import build_command


class YoloCommandPathTests(unittest.TestCase):
    def test_uses_explicit_work_dir_for_detect_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)
            default_asset = work_dir / "ultralytics-main" / "ultralytics" / "assets" / "bus.jpg"
            default_asset.parent.mkdir(parents=True)
            default_asset.touch()
            command = build_command(
                "detect",
                {},
                work_dir / "artifacts" / "job-1",
                work_dir=work_dir,
            )

        self.assertIn(
            f"source={work_dir / 'ultralytics-main' / 'ultralytics' / 'assets' / 'bus.jpg'}",
            command,
        )


if __name__ == "__main__":
    unittest.main()
