# Node Observability Implementation Plan

> **For agentic workers:** Execute tasks inline with verification after each task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose current remote-node state, retained event history, and a live SSE feed so failures can be diagnosed from the control host.

**Architecture:** Persist bounded `NodeObservationEvent` rows on existing heartbeat, dispatch-event, and provisioning-event write paths. A new read-only FastAPI router provides snapshots, history, and a cursor-resumable SSE stream. `watch-nodes.ps1` consumes the stream with reconnect support.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, Server-Sent Events, PowerShell 7.

---

### Task 1: Add the observation event persistence layer

**Files:**
- Create: `quudet-yolo-lab-backend/app/models/node_observation_event.py`
- Modify: `quudet-yolo-lab-backend/app/models/__init__.py`
- Modify: `quudet-yolo-lab-backend/alembic/env.py`
- Create: `quudet-yolo-lab-backend/alembic/versions/005_add_node_observation_events.py`
- Create: `quudet-yolo-lab-backend/tests/test_node_observation_event.py`

- [ ] Write a failing unit test that creates an event, checks message truncation, and verifies newest-first retrieval by node id.
- [ ] Run `python -m unittest tests.test_node_observation_event -v`; confirm the test fails because the model/helper does not exist.
- [ ] Implement a model with UUID id, indexed node id, event type, severity, capped message, JSON payload, and UTC timestamp; add a small helper that removes `token` keys recursively and truncates text to 8 KiB.
- [ ] Register the model in `app.models` and Alembic metadata, then add a migration creating `node_observation_events` with node/time indexes.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Record existing agent updates as observations

**Files:**
- Create: `quudet-yolo-lab-backend/app/services/node_observability.py`
- Modify: `quudet-yolo-lab-backend/app/api/routes/nodes.py`
- Modify: `quudet-yolo-lab-backend/app/api/routes/dispatch.py`
- Modify: `quudet-yolo-lab-backend/app/api/routes/provisioning.py`
- Create: `quudet-yolo-lab-backend/tests/test_node_observability_service.py`

- [ ] Write failing tests for: heartbeat changes produce one event, dispatch log/status events preserve node and job context, and provisioning failures preserve the error message and provision id.
- [ ] Run `python -m unittest tests.test_node_observability_service -v`; confirm the expected recorder import/assertions fail.
- [ ] Implement `record_observation(db, *, node_id, event_type, severity, message, payload)` and a heartbeat fingerprint helper. Record heartbeats only when status/resource values change; record dispatch/provision status, failure, metrics, and logs on their existing authenticated routes.
- [ ] Do not alter accepted agent request payloads or node-token authentication. Event payloads must exclude `token` and cap log text.
- [ ] Re-run the focused service tests and confirm all pass.

### Task 3: Add read-only snapshot, history, and SSE APIs

**Files:**
- Create: `quudet-yolo-lab-backend/app/api/routes/observability.py`
- Create: `quudet-yolo-lab-backend/app/schemas/observability.py`
- Modify: `quudet-yolo-lab-backend/app/main.py`
- Create: `quudet-yolo-lab-backend/tests/test_observability_routes.py`

- [ ] Write failing route tests covering: node snapshot includes latest observation, event history filters by node and cursor, and SSE emits ordered JSON events plus a keep-alive comment.
- [ ] Run `python -m unittest tests.test_observability_routes -v`; confirm the router/endpoints are absent.
- [ ] Define response schemas and add the authenticated router:
  - `GET /api/v1/observability/nodes`
  - `GET /api/v1/observability/nodes/{node_id}/events?after_id=&limit=`
  - `GET /api/v1/observability/stream?after_id=&node_id=`
- [ ] Implement the SSE generator as database cursor polling every second, emitting `id`, `event`, and JSON `data` fields. Limit each poll and send a keep-alive every 15 seconds.
- [ ] Register the router in `app/main.py`, then re-run the route tests and confirm all pass.

### Task 4: Add the operator watcher command

**Files:**
- Create: `scripts/watch-nodes.ps1`
- Create: `quudet-yolo-lab-backend/tests/test_watch_nodes_script.py`
- Modify: `docs/superpowers/specs/2026-07-27-node-observability-design.md`

- [ ] Write a failing script-content test that requires `BaseUrl`, optional `NodeId`, reconnect handling, `Last-Event-ID`, and an SSE `Accept` header.
- [ ] Run `python -m unittest tests.test_watch_nodes_script -v`; confirm it fails while the script is absent.
- [ ] Implement the watcher using `HttpClient` streaming. Print timestamp, node id, event type, severity, and message; update the cursor from SSE `id:` lines and reconnect after a short delay.
- [ ] Add concise usage and endpoint notes to the design document.
- [ ] Re-run the script test and confirm it passes.

### Task 5: Apply and verify the live control plane

**Files:**
- Modify: generated database schema only through Alembic migration.

- [ ] Run `alembic upgrade head` from `quudet-yolo-lab-backend` against the control-plane database.
- [ ] Run all focused tests from Tasks 1–4.
- [ ] Start the watcher against `http://localhost:8000`, trigger or inspect a known failed provision, and verify the failure appears with node id and provision id.
- [ ] Call the node snapshot endpoint and confirm it reports `node-linux-01`, recent heartbeat data, resource cache, and latest event.
- [ ] Run `git diff --check` and report the validation output; do not create a commit unless explicitly requested.
