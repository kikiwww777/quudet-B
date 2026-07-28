#!/usr/bin/env bash
set -Eeuo pipefail

# Keep the QuuDet node agent alive if a download/training runner exits.
ROOT="${QUUDET_BACKEND_DIR:-$HOME/quudet-B/quudet-yolo-lab-backend}"
PYTHON_BIN="${QUUDET_AGENT_PYTHON:-$ROOT/.venv/bin/python}"
RESTART_DELAY_SECONDS="${AGENT_RESTART_DELAY_SECONDS:-5}"

cd "$ROOT"
while true; do
  "$PYTHON_BIN" -m app.agent.runner
  status=$?
  printf '[quudet-agent] runner exited with %s; restarting in %ss\n' "$status" "$RESTART_DELAY_SECONDS" >&2
  sleep "$RESTART_DELAY_SECONDS"
done
