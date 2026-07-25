import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RequirementProfileTests(unittest.TestCase):
    def test_agent_profile_excludes_control_plane_services(self) -> None:
        contents = (ROOT / 'requirements-agent.txt').read_text(encoding='utf-8').lower()
        for forbidden in ('fastapi', 'uvicorn', 'celery', 'redis', 'psycopg2', 'alembic'):
            self.assertNotIn(forbidden, contents)

    def test_legacy_requirements_installs_both_profiles(self) -> None:
        contents = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
        self.assertIn('-r requirements-control.txt', contents)
        self.assertIn('-r requirements-agent.txt', contents)
