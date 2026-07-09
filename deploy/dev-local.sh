#!/usr/bin/env bash
#
# dev-local.sh — run the whole Nubi cloud build locally as ONE instance (+ local
# Postgres & MinIO), from your local nubi working tree, configured via .env.local.
#
#   cp .env.local.example .env.local   # fill in NUBI_LICENSE_KEY, JWT_SECRET, …
#   scripts/dev-local.sh               # build + up (Ctrl-C to stop)
#   scripts/dev-local.sh down -v       # tear down and wipe volumes
#   scripts/dev-local.sh logs -f       # any other arg → passed to docker compose
#
# App:  http://localhost:8000        MinIO console: http://localhost:9001
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="$HERE/docker-compose.local.yml"

command -v docker >/dev/null 2>&1 || { echo "dev-local: docker not found on PATH" >&2; exit 1; }

case "${1:-up}" in
  up|"")
    [ -f "$HERE/.env.local" ] || { echo "dev-local: copy .env.local.example → .env.local first" >&2; exit 1; }
    exec docker compose -f "$COMPOSE" up --build ;;
  down)
    shift; exec docker compose -f "$COMPOSE" down "$@" ;;
  *)
    exec docker compose -f "$COMPOSE" "$@" ;;
esac
