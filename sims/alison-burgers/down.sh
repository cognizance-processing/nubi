#!/usr/bin/env bash
# Stop the Alison Burgers simulator. Data survives; ./up.sh brings it straight
# back. Pass --destroy to delete the container and its data as well.
set -euo pipefail
CONTAINER=alison-burgers-sim
if [[ "${1:-}" == "--destroy" ]]; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  echo "removed $CONTAINER and its data"
else
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
  echo "stopped $CONTAINER (data kept — ./up.sh to resume)"
fi
