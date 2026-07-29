# Node Self-Healing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use a task-by-task implementation workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a remote QuuDet agent reconnect automatically, accept audited recovery commands, and expose safe recovery actions in the node-management UI.

**Architecture:** The master persists short-lived node commands in the node's capability record so no remote shell endpoint is exposed. The runner polls and acknowledges only its own commands, and exits only for its signed restart command; systemd then starts it again. The existing heartbeat-timeout recovery remains the lease/requeue mechanism.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Python stdlib HTTP runner, static JavaScript, systemd.

---

### Task 1: Bounded reconnect backoff and immediate registration

**Files:**
- Modify: `quudet-yolo-lab-backend/app/agent/runner.py`
- Test: `quudet-yolo-lab-backend/tests/test_agent_reconnect.py`

- [ ] Write tests proving a failed control-plane request increases a bounded delay, a successful request resets it, and `run_forever` retries registration instead of exiting.
- [ ] Run `python -m unittest tests.test_agent_reconnect -v` and confirm the new tests fail before implementation.
- [ ] Add a deterministic `ReconnectBackoff` helper (base delay 2 seconds, maximum 30 seconds; jitter injectable for tests), use it for registration, heartbeat, provisioning claim, and job claim failures, and reset it after each successful request.
- [ ] Run `python -m unittest tests.test_agent_reconnect -v` and confirm all tests pass.

### Task 2: Authenticated, expiring node control commands

**Files:**
- Modify: `quudet-yolo-lab-backend/app/api/routes/nodes.py`
- Modify: `quudet-yolo-lab-backend/app/schemas/node.py`
- Modify: `quudet-yolo-lab-backend/app/agent/runner.py`
- Test: `quudet-yolo-lab-backend/tests/test_node_control_commands.py`

- [ ] Write tests for an authenticated master user creating `RECONNECT` and `RESTART` commands, rejection of an unknown action, an agent receiving only its own unexpired command, and idempotent acknowledgement.
- [ ] Run `python -m unittest tests.test_node_control_commands -v` and confirm the tests fail because the routes and poller do not exist.
- [ ] Implement master endpoints to create, retrieve, and acknowledge commands. Store command records under `capabilities["control_commands"]`, include an ID, action, requested timestamp, expiry, status, result/error, and enforce the node token on agent-facing endpoints.
- [ ] Implement runner polling: reconnect triggers registration plus heartbeat; restart acknowledges then exits with code 0; expired or acknowledged commands are ignored. No payload may contain executable shell text.
- [ ] Run `python -m unittest tests.test_node_control_commands -v` and confirm all tests pass.

### Task 3: Safe expired-work reconciliation action

**Files:**
- Modify: `quudet-yolo-lab-backend/app/api/routes/nodes.py`
- Test: `quudet-yolo-lab-backend/tests/test_node_heartbeat_capacity.py`

- [ ] Write a test proving the master-only reconciliation endpoint requeues only a running remote job assigned to an offline node, increments the recovery budget, and leaves online-node work unchanged.
- [ ] Run `python -m unittest tests.test_node_heartbeat_capacity -v` and confirm it fails before implementation.
- [ ] Extract stale-node reconciliation from `list_nodes` into a reusable function, retain list-node behavior, and add an authenticated `POST /nodes/reconcile-expired-work` endpoint returning recovered and exhausted job IDs.
- [ ] Run `python -m unittest tests.test_node_heartbeat_capacity -v` and confirm all tests pass.

### Task 4: Node-management UI actions

**Files:**
- Modify: `quudet-yolo-lab/index.html`
- Modify: `quudet-yolo-lab/app.js`
- Test: manual browser/API verification recorded in `docs/计算机集群与Linux机房部署实施方案.md`

- [ ] Add a compact operations column to the existing node table with `Reconnect now` and `Restart agent` buttons; add one table-level `Requeue expired work` button.
- [ ] Add JavaScript handlers that call the command/reconciliation endpoints, disable the clicked button while pending, surface failures with the existing alert mechanism, and refresh nodes/jobs afterwards.
- [ ] Verify with a browser against a local API: command buttons issue the expected POST body and no action exposes a token or arbitrary command text.

### Task 5: Repeatable Linux service installation and operator documentation

**Files:**
- Create: `scripts/install-linux-node-service.sh`
- Modify: `scripts/quudet-agent.service`
- Modify: `docs/计算机集群与Linux机房部署实施方案.md`
- Test: `bash -n scripts/install-linux-node-service.sh`

- [ ] Write a shell-syntax check command and document the required input environment file path and service-user arguments.
- [ ] Create an idempotent installer that validates its explicit backend directory, copies the templated unit, creates `/etc/quudet-agent/<node-id>.env` with mode 600 when supplied, runs `systemctl daemon-reload`, and enables/starts `quudet-agent@<service-user>`.
- [ ] Keep the unit's `Restart=always`, use `RestartSec=5`, and remove the nested restart loop from the launcher so systemd is the single process supervisor.
- [ ] Run `bash -n scripts/install-linux-node-service.sh` and document the Linux acceptance checklist: enable/start, control-plane restart reconnect, runner-crash restart, and interrupted job recovery.

### Task 6: Full verification and commit

**Files:**
- Verify all files above

- [ ] Run `python -m unittest discover -s tests -v` from `quudet-yolo-lab-backend`.
- [ ] Run `bash -n scripts/install-linux-node-service.sh` and `git diff --check` from the worktree.
- [ ] Review the diff against the design specification, commit the completed implementation on `feature/node-self-healing`, and report the commit plus Linux commands required for deployment.
