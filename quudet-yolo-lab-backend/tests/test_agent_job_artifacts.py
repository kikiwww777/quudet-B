import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent import runner


class TestAgentJobArtifacts(unittest.TestCase):
    def test_each_job_uses_a_unique_artifact_output_directory(self):
        paths = runner.get_agent_paths()
        job_id = "job-unique-output"
        payload = {
            "model": "yolo11n.pt",
            "data": "VOC.yaml",
            "epochs": 1,
            "batch": 2,
            "imgsz": 64,
            "device": "cuda",
            "project": "shared-project",
            "name": "shared-name",
        }
        captured = {}

        def build(job_type, command_payload, job_dir, **_kwargs):
            captured["payload"] = dict(command_payload)
            captured["job_dir"] = job_dir
            return ["python", "-c", "pass"]

        process = type("Process", (), {"stdout": None, "poll": lambda self: 0, "wait": lambda self: 0, "pid": 1})()
        job = {"id": job_id, "job_type": "train", "payload": payload}

        with (
            patch.object(runner, "get_agent_paths", return_value=paths),
            patch.object(runner, "build_command", side_effect=build),
            patch.object(runner, "subprocess") as subprocess_module,
            patch.object(runner, "_emit_event"),
        ):
            subprocess_module.Popen.return_value = process
            runner.execute_job(job)

        self.assertEqual(captured["payload"]["project"], str(paths.artifacts_dir))
        self.assertEqual(captured["payload"]["name"], job_id)
        self.assertEqual(captured["job_dir"], paths.artifacts_dir / job_id)
