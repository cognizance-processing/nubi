#!/usr/bin/env bash
#
# deploy.sh — build the EE image from THIS repo and roll it out to a Fly app.
#
# The deploy config lives in the nubi repo itself (deploy/), so there is no
# separate ops repo, no version pin, and no checkout indirection — the build
# context is just the current working tree (Dockerfile.ee + backend + SPA).
#
# Environments (same config, different app + secrets):
#   main | prod    → app "nubi"       (production)   [default]
#   dev  | staging → app "nubi-dev"   (testing env, mirrors prod)
#
# Extra args after the environment are forwarded to `flyctl deploy`.
#   deploy/deploy.sh              # deploy prod (nubi)
#   deploy/deploy.sh dev          # deploy the dev env (nubi-dev)
#   deploy/deploy.sh dev --now    # extra flyctl args pass through
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CFG="$HERE/fly.toml"

command -v flyctl >/dev/null 2>&1 || { echo "deploy: flyctl not found on PATH" >&2; exit 1; }

APP="nubi"
case "${1:-main}" in
  main|prod|production) APP="nubi" ;;
  dev|staging)          APP="nubi-dev" ;;
  *) echo "deploy: unknown environment '$1' (use: main | dev)" >&2; exit 1 ;;
esac
[ $# -gt 0 ] && shift || true

# Version stamp from this repo's git (exact tag on HEAD → clean "X.Y.Z";
# otherwise "X.Y.Z-dev.<sha>"). Passed as a build-arg since .git is dockerignored.
BASE="$(python3 -c "import json;print(json.load(open('$ROOT/package.json'))['version'])")"
if TAG="$(git -C "$ROOT" describe --tags --exact-match HEAD 2>/dev/null)"; then
  NUBI_VERSION="${TAG#v}"
else
  NUBI_VERSION="${BASE}-dev.$(git -C "$ROOT" rev-parse --short=12 HEAD)"
fi

echo "deploy: building EE image v$NUBI_VERSION → app '$APP'" >&2

# Remote builder by default (works in CI, no local Docker needed). But the SPA
# build is memory-heavy (mermaid/rehype) and OOM-kills Fly's shared remote
# builder, so allow a LOCAL build (NUBI_BUILD_LOCAL=1) which uses the local
# Docker daemon — set it when the remote build gets SIGKILLed.
BUILD_FLAG="--remote-only"
if [ "${NUBI_BUILD_LOCAL:-}" = "1" ]; then
  BUILD_FLAG="--local-only"
  echo "deploy: building LOCALLY (NUBI_BUILD_LOCAL=1) — needs a running Docker daemon" >&2
fi

# Build context = the repo root. flyctl resolves a fly.toml `dockerfile` relative
# to the CONFIG file's dir (deploy/), so pass Dockerfile.ee + its ignorefile
# explicitly from the repo root where they actually live.
( cd "$ROOT" && exec flyctl deploy --config "$CFG" --app "$APP" \
    --dockerfile "$ROOT/Dockerfile.ee" --ignorefile "$ROOT/Dockerfile.ee.dockerignore" \
    --build-arg "NUBI_VERSION=$NUBI_VERSION" "$BUILD_FLAG" "$@" )
