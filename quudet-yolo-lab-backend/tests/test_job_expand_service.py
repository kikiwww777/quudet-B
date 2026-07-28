import unittest

from app.schemas.experiment import ExperimentGroupCreate
from app.services.job_expand_service import expand_runs


class TestJobExpandService(unittest.TestCase):
    def test_augmentation_comparison_disables_unsafe_close_mosaic_default(self):
        group = ExperimentGroupCreate.model_validate(
            {
                "name": "augmentation-check",
                "runs": [
                    {"role": "baseline", "payload": {"epochs": 10, "mosaic": 1.0, "mixup": 0.0}},
                    {"role": "variant", "payload": {"epochs": 10, "mosaic": 0.5, "mixup": 0.1}},
                ],
            }
        )

        runs = expand_runs(group)

        self.assertEqual([run["payload"]["close_mosaic"] for run in runs], [0, 0])
