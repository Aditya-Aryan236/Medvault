#!/usr/bin/env bash
# Create the Docker network used by net-federated-bridge (docker-compose.base.yml).
# Safe to run multiple times.
set -euo pipefail

NET_NAME="${MEDVAULT_FEDERATED_NETWORK:-medvault-federated}"

if docker network inspect "$NET_NAME" >/dev/null 2>&1; then
  echo "Network '$NET_NAME' already exists."
  exit 0
fi

docker network create "$NET_NAME"
echo "Created Docker network '$NET_NAME'."
