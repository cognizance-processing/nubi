#!/usr/bin/env bash
#
# setup-fly.sh — one-time (idempotent) Fly provisioning for a Nubi deployment.
# Creates the Fly app if it doesn't exist. Nubi needs nothing else from the
# platform: two stateless process groups (app + worker); state lives in your
# Postgres and object store.
#
#   NUBI_FLY_APP=my-nubi NUBI_FLY_ORG=personal deploy/fly/setup-fly.sh
#
# Next: deploy/fly/secrets.sh <env-file> && deploy/fly/deploy.sh
set -euo pipefail

command -v flyctl >/dev/null 2>&1 || { echo "setup-fly: flyctl not found on PATH" >&2; exit 1; }
APP="${NUBI_FLY_APP:?set NUBI_FLY_APP to the Fly app name}"
ORG="${NUBI_FLY_ORG:-personal}"

echo "setup-fly: provisioning app '$APP' in org '$ORG'" >&2
if flyctl apps list --org "$ORG" 2>/dev/null | awk '{print $1}' | grep -qx "$APP"; then
  echo "  ✓ app '$APP' already exists" >&2
else
  flyctl apps create "$APP" --org "$ORG"
fi
echo "setup-fly: done — next: deploy/fly/secrets.sh .env.fly && deploy/fly/deploy.sh" >&2
