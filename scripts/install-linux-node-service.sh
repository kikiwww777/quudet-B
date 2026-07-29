#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <service-user> <node-id> <backend-dir>" >&2
  exit 64
fi

service_user="$1"
node_id="$2"
backend_dir="$(cd "$3" && pwd)"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_dir="/etc/quudet-agent"
env_file="$env_dir/$service_user.env"
unit_file="/etc/systemd/system/quudet-agent@.service"

[[ -d "$backend_dir/app/agent" ]] || { echo "Not a QuuDet backend: $backend_dir" >&2; exit 65; }
id "$service_user" >/dev/null

sudo install -d -m 700 "$env_dir"
if [[ ! -f "$env_file" ]]; then
  sudo install -m 600 /dev/null "$env_file"
  printf 'NODE_ID=%s\n' "$node_id" | sudo tee "$env_file" >/dev/null
  echo "Created $env_file. Add MASTER_API_BASE, NODE_TOKEN and optional NODE_NAME before starting." >&2
fi
sudo install -m 644 "$script_dir/quudet-agent.service" "$unit_file"
sudo systemctl daemon-reload
sudo systemctl enable --now "quudet-agent@$service_user"
echo "Installed quudet-agent@$service_user for node $node_id. The service reads $env_file."
