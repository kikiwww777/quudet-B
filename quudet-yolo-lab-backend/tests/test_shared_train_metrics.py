import tempfile
import unittest
from pathlib import Path

from app.shared.train_metrics import epoch_progress, parse_results_csv


class SharedTrainMetricsTests(unittest.TestCase):
    def test_parses_csv_and_reports_running_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "results.csv"
            csv_path.write_text("epoch,metrics/mAP50(B)\n0,0.2\n1,0.4\n", encoding="utf-8")
            metrics = parse_results_csv(csv_path)

        self.assertEqual(metrics["x"], [0, 1])
        self.assertEqual(metrics["series"]["metrics/mAP50(B)"], [0.2, 0.4])
        self.assertEqual(epoch_progress(metrics["x"], 10, status="RUNNING")["progress_percent"], 20)


if __name__ == "__main__":
    unittest.main()
