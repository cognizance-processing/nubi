#!/usr/bin/env bash
# scripts/screenshots.sh — regenerate all README/docs screenshots from a clean,
# freshly seeded stack, so the images always match the current version.
#
# Boots an ephemeral Postgres + backend + frontend (reusing scripts/e2e.sh),
# seeds the demo workspace, runs scripts/docs-screenshots.mjs, tears down.
#
# Usage:
#   npm run screenshots:auto        # this script
#   npm run screenshots             # capture only, against a running dev stack
#
# All e2e.sh environment overrides apply (PG_PORT, SKIP_DOCKER_PG, PY, ...).

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

E2E_RUN_CMD="node '${REPO_ROOT}/scripts/docs-screenshots.mjs'" \
  exec bash "${REPO_ROOT}/scripts/e2e.sh"
