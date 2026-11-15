#!/usr/bin/env bash
#
# deploy.sh — build the combined EE image from THIS working tree and roll it
# out to a Fly app. The build context is the repo root (Dockerfile.ee + backend
# + SPA); migrations run via fly.toml's release_command before rollout.
#
#   NUBI_FLY_APP=my-nubi deploy/fly/deploy.sh            # deploy
#   NUBI_FLY_APP=my-nubi deploy/fly/deploy.sh --now      # extra args → flyctl deploy
#   NUBI_BUILD_LOCAL=1 ...                               # build with local Docker instead of Fly's remote builder
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CFG="$HERE/fly.toml"

command -v flyctl >/dev/null 2>&1 || { echo "deploy: flyctl not found on PATH" >&2; exit 1; }
APP="${NUBI_FLY_APP:?set NUBI_FLY_APP to the Fly app name}"

# Version stamp from git (exact tag on HEAD → "X.Y.Z"; else "X.Y.Z-dev.<sha>").
# Passed as a build-arg because .git is dockerignored.
BASE="$(python3 -c "import json;print(json.load(open('$ROOT/package.json'))['version'])")"
if TAG="$(git -C "$ROOT" describe --tags --exact-match HEAD 2>/dev/null)"; then
  NUBI_VERSION="${TAG#v}"
else
  NUBI_VERSION="${BASE}-dev.$(git -C "$ROOT" rev-parse --short=12 HEAD)"
fi
echo "deploy: building EE image v$NUBI_VERSION → app '$APP'" >&2

# The SPA build is memory-heavy and can OOM Fly's shared remote builder; set
# NUBI_BUILD_LOCAL=1 to build with your local Docker daemon instead.
BUILD_FLAG="--remote-only"
[ "${NUBI_BUILD_LOCAL:-}" = "1" ] && BUILD_FLAG="--local-only"

( cd "$ROOT" && exec flyctl deploy --config "$CFG" --app "$APP" \
    --dockerfile "$ROOT/Dockerfile.ee" --ignorefile "$ROOT/Dockerfile.ee.dockerignore" \
    --build-arg "NUBI_VERSION=$NUBI_VERSION" "$BUILD_FLAG" "$@" )
