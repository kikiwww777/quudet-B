import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LocalAgentLauncherTests(unittest.TestCase):
    def test_launcher_declares_local_agent_identity(self) -> None:
        launcher = (ROOT / 'scripts' / 'start-local-agent.ps1').read_text(encoding='utf-8')
        for value in ('control-gpu-01', 'NODE_KIND', 'local', 'app.agent.runner'):
            self.assertIn(value, launcher)

    def test_restart_calls_local_agent_launcher(self) -> None:
        restart = (ROOT.parent / 'restart.bat').read_text(encoding='utf-8')
        self.assertIn('start-local-agent.ps1', restart)
