#!/usr/bin/env bash
#
# setup-fly.sh — one-time (idempotent) Fly provisioning for a Nubi environment.
#
# Creates the Fly app (if it doesn't already exist). That's all the platform
# setup Nubi needs now: it runs two stateless process groups (app + worker) and
# pushes queries down to the customer's warehouse (or runs them in the browser
# kernel) — there is no server-side query pool to provision.
#
# Safe to re-run: skips the app if it already exists.
#
# Environments:
#   main | prod    → app "nubi"       (production)   [default]
#   dev  | staging → app "nubi-dev"   (testing env, mirrors prod)
#
# Full first-run sequence:
#   scripts/setup-fly.sh dev     # create the app
#   scripts/secrets.sh   dev     # push secrets
#   scripts/deploy.sh    dev     # build + deploy
set -euo pipefail

command -v flyctl >/dev/null 2>&1 || { echo "setup-fly: flyctl not found on PATH" >&2; exit 1; }

APP="nubi"
ORG="nubi-142"   # the Nubi Fly organization (slug)
case "${1:-main}" in
  main|prod|production) APP="nubi" ;;
  dev|staging)          APP="nubi-dev" ;;
  *) echo "setup-fly: unknown environment '$1' (use: main | dev)" >&2; exit 1 ;;
esac

echo "setup-fly: provisioning app '$APP' in org '$ORG'" >&2

if flyctl apps list --org "$ORG" 2>/dev/null | awk '{print $1}' | grep -qx "$APP"; then
  echo "  ✓ app '$APP' already exists" >&2
else
  echo "  → creating app '$APP'" >&2
  flyctl apps create "$APP" --org "$ORG"
fi

echo "setup-fly: done for '$APP' — next: scripts/secrets.sh $1 && scripts/deploy.sh $1" >&2
