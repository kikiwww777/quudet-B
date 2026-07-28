# Node Observability API and Watcher Design

## Goal

Let operators and Codex inspect remote QuuDet agents without copying terminal
output manually. The control plane exposes a current node snapshot, a bounded
event history, and a server-sent event (SSE) stream. A PowerShell watcher
renders the stream in a terminal.

## Scope

- Reuse existing agent-to-control-plane HTTP posts: heartbeats, dispatch
  events, and provisioning events.
- Persist important node, job, and provisioning events with a timestamp.
- Expose authenticated read-only observability endpoints.
- Add a PowerShell watcher for the local control host.

No direct inbound connection to a remote agent, no WebSocket agent protocol,
and no browser dashboard are included.

## Data Model

`NodeObservationEvent` stores `id`, `node_id`, `event_type`, `severity`,
`message`, `payload`, and `created_at`. Log messages are capped before
persistence; node tokens are never stored.

Heartbeats update the existing `ComputeNode` snapshot on every call, but create
an observation event only for status/resource changes or errors. Dispatch and
provisioning events create an observation event for each meaningful update.

## API

- `GET /api/v1/observability/nodes` returns current node snapshots plus their
  latest observation.
- `GET /api/v1/observability/nodes/{node_id}/events` returns bounded event
  history and supports a cursor.
- `GET /api/v1/observability/stream` is a read-only SSE feed with periodic
  keep-alives and cursor-based resume.

Agents retain their existing node-token write endpoints. The control plane
remains the sole inbound endpoint reachable from operators.

## Watcher

`scripts/watch-nodes.ps1` follows the SSE feed and prints compact, timestamped
lines. It supports node filtering and reconnects from the latest event id.

## Acceptance Criteria

1. A provisioning failure on `node-linux-01` appears in the API and watcher
   within one polling interval.
2. A node snapshot exposes heartbeat, GPU/cache state, running jobs, and its
   latest error without opening the Linux terminal.
3. Job logs and status updates appear in the watcher in order.
4. Existing node, dispatch, and provisioning behavior remains compatible.
