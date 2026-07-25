import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'fastapi', 'celery', 'redis', 'sqlalchemy', 'psycopg2', 'alembic'}


class AgentImportBoundaryTests(unittest.TestCase):
    def test_agent_runtime_has_no_control_plane_imports(self) -> None:
        paths = (
            ROOT / 'app' / 'agent' / 'runner.py',
            ROOT / 'app' / 'agent' / 'runtime_paths.py',
            ROOT / 'app' / 'agent' / 'resource_provisioner.py',
            ROOT / 'app' / 'shared' / 'train_metrics.py',
        )
        for path in paths:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split('.')[0])
            self.assertFalse(FORBIDDEN & imported, path)
