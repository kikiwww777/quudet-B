# QuuDet Node Self-Healing Design

## Goal

Make remote Linux agents recover from transient control-plane failures and process crashes without manual terminal intervention, while preventing duplicate training or unsafe browser-initiated remote execution.

## Existing Baseline

The agent already retries failed heartbeat and claim requests, and the repository contains a `systemd` unit with `Restart=always`. The missing behavior is a complete recovery contract: the service is not installed through one repeatable path, node failure currently fails running jobs instead of making them recoverable, and the web UI cannot request a safe reconciliation or agent restart.

## Recovery Contract

1. The Linux agent runs as a `systemd` service, starts at boot, and restarts after a runner crash.
2. The agent uses bounded exponential backoff with jitter for registration, heartbeat, provisioning claims, and job claims. A successful request resets the backoff.
3. A claimed running job has a lease renewed by its job events. When the lease expires, the control plane changes it to a recoverable pending state instead of failing it immediately.
4. When an agent restarts, it reports its persisted runtime record. The control plane permits a resume only when the same job ID and checkpoint/artifact directory are present. Otherwise it requeues the job with a bounded retry count.
5. A job is terminally failed only after its retry budget is exhausted or its recovery validation is unsafe.

## Frontend-Controlled Operations

The frontend exposes node actions: `Reconnect now`, `Requeue expired work`, and `Restart agent`. They create authenticated, audited control commands on the master; they do not SSH to a Linux host.

The agent polls for commands as part of its existing control-plane loop. `Reconnect now` triggers an immediate registration and heartbeat. `Requeue expired work` invokes master-side reconciliation. `Restart agent` is accepted only for the node's own signed command, writes an acknowledgement, then exits; `systemd` restarts it. Commands are idempotent, expire quickly, and record requester, timestamps, result, and error.

## Safety

Resume never starts a second process while the recovered agent reports the original job PID as alive. Requeueing is limited to jobs whose lease is expired and whose owner node is offline. The server retains a per-job retry counter and an audit event for every reclaim, resume, and terminal failure. Token values and arbitrary shell commands never enter the API, UI, or event log.

## Validation

Tests cover backoff reset, command idempotency and ownership, expired-lease requeue, retry-budget exhaustion, and restart-command acknowledgement. A Linux acceptance checklist verifies `systemctl enable --now quudet-agent`, master restart while the agent reconnects, runner crash auto-restart, and one interrupted checkpointed job resuming without duplicate processes.
