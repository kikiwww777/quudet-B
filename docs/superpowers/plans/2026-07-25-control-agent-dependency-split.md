# Control Plane and Agent Dependency Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the control plane optionally run a local training agent while remote agents install only execution dependencies and never require PostgreSQL, Redis, Celery, or FastAPI.

**Architecture:** Keep the current repository and HTTP protocol. Create an agent-safe runtime-path module and a shared pure metrics module, then make the runner import only agent-safe modules. Split package installation into control, agent, and legacy aggregate requirement files. Start the local agent as a separate default-on process from `restart.bat`.

**Tech Stack:** Python 3.11, unittest, Windows batch/PowerShell, PyTorch, Ultralytics, FastAPI, PostgreSQL, Redis, Celery.

---

### Task 1: Create an agent-safe runtime path module

**Files:**
- Create: `quudet-yolo-lab-backend/app/agent/runtime_paths.py`
- Modify: `quudet-yolo-lab-backend/app/agent/runner.py`
- Test: `quudet-yolo-lab-backend/tests/test_agent_runtime_paths.py`

- [ ] **Step 1: Write the failing test**

```python
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
            with patch.dict(os.environ, {
                "YOLO_WORK_DIR": str(root / "yolo"),
                "DATA_DIR": str(root / "data"),
            }, clear=False):
                paths = get_agent_paths()

            self.assertEqual(paths.yolo_work_dir, root / "yolo")
            self.assertEqual(paths.artifacts_dir, root / "data" / "artifacts")
            self.assertEqual(paths.provision_cache_dir, root / "data" / "artifacts" / "provision_cache")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_agent_runtime_paths.py -v`

Expected: FAIL because `app.agent.runtime_paths` does not exist.

- [ ] **Step 3: Implement the minimal module**

```python
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class AgentPaths:
    yolo_work_dir: Path
    data_dir: Path
    artifacts_dir: Path
    provision_cache_dir: Path


def get_agent_paths() -> AgentPaths:
    backend_dir = Path(__file__).resolve().parents[2]
    repo_root = backend_dir.parent
    yolo_work_dir = Path(os.getenv("YOLO_WORK_DIR") or repo_root).resolve()
    data_dir = Path(os.getenv("DATA_DIR") or backend_dir / "data").resolve()
    artifacts_dir = data_dir / "artifacts"
    return AgentPaths(
        yolo_work_dir=yolo_work_dir,
        data_dir=data_dir,
        artifacts_dir=artifacts_dir,
        provision_cache_dir=artifacts_dir / "provision_cache",
    )
```

Replace every `get_settings()` use in `app/agent/runner.py` with `get_agent_paths()` and its matching path property. Remove the `from app.config import get_settings` import.

- [ ] **Step 4: Run the focused tests**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_agent_runtime_paths.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quudet-yolo-lab-backend/app/agent/runtime_paths.py quudet-yolo-lab-backend/app/agent/runner.py quudet-yolo-lab-backend/tests/test_agent_runtime_paths.py
git commit -m "Decouple agent paths from control settings"
```

### Task 2: Move agent metrics to a pure shared module

**Files:**
- Create: `quudet-yolo-lab-backend/app/shared/__init__.py`
- Create: `quudet-yolo-lab-backend/app/shared/train_metrics.py`
- Modify: `quudet-yolo-lab-backend/app/services/train_metrics.py`
- Modify: `quudet-yolo-lab-backend/app/agent/runner.py`
- Test: `quudet-yolo-lab-backend/tests/test_shared_train_metrics.py`

- [ ] **Step 1: Write the failing test**

```python
import tempfile
import unittest
from pathlib import Path

from app.shared.train_metrics import epoch_progress, parse_results_csv


class SharedTrainMetricsTests(unittest.TestCase):
    def test_parses_csv_and_reports_running_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "results.csv"
            csv_path.write_text("epoch,metrics/mAP50(B)\\n0,0.2\\n1,0.4\\n", encoding="utf-8")
            metrics = parse_results_csv(csv_path)

        self.assertEqual(metrics["x"], [0, 1])
        self.assertEqual(metrics["series"]["metrics/mAP50(B)"], [0.2, 0.4])
        self.assertEqual(epoch_progress(metrics["x"], 10, status="RUNNING")["progress_percent"], 20)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_shared_train_metrics.py -v`

Expected: FAIL because `app.shared.train_metrics` does not exist.

- [ ] **Step 3: Extract pure functions**

Move these functions, plus only their standard-library helpers, from `app/services/train_metrics.py` to `app/shared/train_metrics.py`:

```python
parse_results_csv(path: Path) -> dict[str, Any] | None
epoch_progress(x: list[Any], total_epochs: int, *, status: str = "") -> dict[str, int]
resolve_results_csv_for_train(*, payload: dict[str, Any], work_dir: Path, job_dir: Path, started_at: datetime | None = None, log_text: str | None = None, job_type: str | None = None) -> Path | None
```

Keep database-specific functions such as `job_started_timestamp`, `build_results_csv_candidates(job: JobRecord, ...)`, and `resolve_results_csv(job: JobRecord, ...)` in `app/services/train_metrics.py`. Import the extracted pure functions from `app.shared.train_metrics` there. Change `app/agent/runner.py` to import its metrics functions from `app.shared.train_metrics`.

- [ ] **Step 4: Run focused tests**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_shared_train_metrics.py" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quudet-yolo-lab-backend/app/shared quudet-yolo-lab-backend/app/services/train_metrics.py quudet-yolo-lab-backend/app/agent/runner.py quudet-yolo-lab-backend/tests/test_shared_train_metrics.py
git commit -m "Extract agent-safe training metrics"
```

### Task 3: Define control, agent, and aggregate requirements

**Files:**
- Create: `quudet-yolo-lab-backend/requirements-control.txt`
- Create: `quudet-yolo-lab-backend/requirements-agent.txt`
- Modify: `quudet-yolo-lab-backend/requirements.txt`
- Test: `quudet-yolo-lab-backend/tests/test_requirement_profiles.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RequirementProfileTests(unittest.TestCase):
    def test_agent_profile_excludes_control_plane_services(self) -> None:
        lines = (ROOT / "requirements-agent.txt").read_text(encoding="utf-8").lower()
        for forbidden in ("fastapi", "uvicorn", "celery", "redis", "psycopg2", "alembic"):
            self.assertNotIn(forbidden, lines)

    def test_legacy_requirements_installs_both_profiles(self) -> None:
        contents = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("-r requirements-control.txt", contents)
        self.assertIn("-r requirements-agent.txt", contents)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_requirement_profiles.py -v`

Expected: FAIL because the two profile files do not exist.

- [ ] **Step 3: Create profile files**

`requirements-control.txt` contains the existing server-only pins:

```text
fastapi==0.115.6
uvicorn[standard]==0.34.0
sqlalchemy==2.0.36
python-multipart==0.0.20
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
pydantic-settings==2.7.0
email-validator>=2.2.0
celery[redis]==5.4.0
redis==5.2.1
psycopg2-binary==2.9.10
alembic==1.14.1
```

`requirements-agent.txt` contains:

```text
ultralytics>=8.3.0
torch>=2.0.0
psutil>=5.9.0
```

Replace `requirements.txt` with:

```text
-r requirements-control.txt
-r requirements-agent.txt
```

This preserves the existing one-command local developer install while allowing a remote agent to install only `requirements-agent.txt`.

- [ ] **Step 4: Run focused tests**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_requirement_profiles.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quudet-yolo-lab-backend/requirements-control.txt quudet-yolo-lab-backend/requirements-agent.txt quudet-yolo-lab-backend/requirements.txt quudet-yolo-lab-backend/tests/test_requirement_profiles.py
git commit -m "Split control and agent dependency profiles"
```

### Task 4: Start a local training agent by default

**Files:**
- Create: `quudet-yolo-lab-backend/scripts/start-local-agent.ps1`
- Modify: `restart.bat`
- Test: `quudet-yolo-lab-backend/tests/test_local_agent_launcher.py`

- [ ] **Step 1: Write the failing test**

```python
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalAgentLauncherTests(unittest.TestCase):
    def test_launcher_sets_local_node_identity_and_logs(self) -> None:
        launcher = (ROOT / "scripts" / "start-local-agent.ps1").read_text(encoding="utf-8")
        self.assertIn("NODE_ID", launcher)
        self.assertIn("control-gpu-01", launcher)
        self.assertIn("NODE_KIND", launcher)
        self.assertIn("local", launcher)
        self.assertIn("app.agent.runner", launcher)

    def test_restart_starts_the_local_agent_launcher(self) -> None:
        restart = (ROOT.parent / "restart.bat").read_text(encoding="utf-8")
        self.assertIn("start-local-agent.ps1", restart)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_local_agent_launcher.py -v`

Expected: FAIL because the launcher file does not exist.

- [ ] **Step 3: Implement idempotent local-agent startup**

`start-local-agent.ps1` must:

```powershell
$backendRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $backendRoot '.venv\\Scripts\\python.exe'
$logDir = Join-Path $backendRoot 'service-logs'
$logPath = Join-Path $logDir 'control-agent.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$running = Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -match 'app\.agent\.runner' -and $_.CommandLine -match 'control-gpu-01'
}
if ($running) { exit 0 }

$env:MASTER_API_BASE = 'http://127.0.0.1:8000'
$env:NODE_ID = 'control-gpu-01'
$env:NODE_NAME = 'Control GPU 01'
$env:NODE_KIND = 'local'
$env:NODE_MAX_CONCURRENCY = '1'
Start-Process -FilePath $python -ArgumentList '-m', 'app.agent.runner' -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput $logPath -RedirectStandardError $logPath
```

Change `restart.bat` after API startup to call:

```bat
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\quudet-yolo-lab-backend\scripts\start-local-agent.ps1"
```

Do not change user-managed existing API, frontend, database, or Celery behavior.

- [ ] **Step 4: Run focused tests**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_local_agent_launcher.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add quudet-yolo-lab-backend/scripts/start-local-agent.ps1 restart.bat quudet-yolo-lab-backend/tests/test_local_agent_launcher.py
git commit -m "Start local training agent with control plane"
```

### Task 5: Prove the dependency boundary and document deployment

**Files:**
- Modify: `quudet-yolo-lab-backend/tests/test_agent_manifest_url.py`
- Create: `quudet-yolo-lab-backend/tests/test_agent_import_boundary.py`
- Modify: `docs/QUUDET_LINUX_NODE_DEPLOYMENT_2026-07-09.md`

- [ ] **Step 1: Write the failing import-boundary test**

```python
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"fastapi", "celery", "redis", "sqlalchemy", "psycopg2", "alembic"}


class AgentImportBoundaryTests(unittest.TestCase):
    def test_agent_runtime_has_no_control_plane_imports(self) -> None:
        paths = [
            ROOT / "app" / "agent" / "runner.py",
            ROOT / "app" / "agent" / "runtime_paths.py",
            ROOT / "app" / "agent" / "resource_provisioner.py",
            ROOT / "app" / "shared" / "train_metrics.py",
        ]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            modules = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in (node.names if isinstance(node, ast.Import) else [node])
            }
            self.assertFalse(FORBIDDEN & modules, path)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -p test_agent_import_boundary.py -v`

Expected: FAIL until Tasks 1 and 2 remove the current indirect imports.

- [ ] **Step 3: Update deployment documentation**

Document these exact agent commands:

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-agent.txt
MASTER_API_BASE=http://10.120.78.70:8000 \\
NODE_ID=node-linux-01 \\
NODE_KIND=remote \\
NODE_TOKEN='<node-secret>' \\
.venv/bin/python -m app.agent.runner
```

Document that PostgreSQL, Redis, Celery, Alembic, and FastAPI are control-plane-only. Document that a control-plane GPU host installs both profiles and starts the local agent from `restart.bat`.

- [ ] **Step 4: Run the complete test suite**

Run: `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`

Expected: PASS, including manifest URL, runtime paths, shared metrics, requirements, launcher, and import-boundary tests.

- [ ] **Step 5: Commit**

```bash
git add quudet-yolo-lab-backend/tests docs/QUUDET_LINUX_NODE_DEPLOYMENT_2026-07-09.md
git commit -m "Document and verify agent-only deployment"
```