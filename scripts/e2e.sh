#!/usr/bin/env bash
# scripts/e2e.sh — Orchestrate a full Nubi e2e test run from a clean state.
#
# What it does (in order):
#   1.  Pick a free port for an ephemeral Postgres container.
#   2.  Start postgres:16-alpine via Docker on that port.
#   3.  Export DATABASE_URL and all other required env vars.
#   4.  Run database/migrate.py  (schema migration).
#   5.  Run backend/seed.py --demo (superuser + demo workspace).
#   6.  Start the backend uvicorn server.
#   7.  Start the Vite dev server  (npm run dev).
#   8.  Wait for both to pass their health checks.
#   9.  Run  npx playwright test  (the full e2e suite).
#  10.  Tear everything down (trap on EXIT / ERR / INT).
#
# Usage:
#   bash scripts/e2e.sh
#
# Environment overrides (all optional):
#   E2E_BASE_URL        Frontend URL  (default: http://localhost:5173)
#   BACKEND_URL         Backend URL   (default: http://localhost:8000)
#   PG_PORT             Port for the throwaway Postgres (default: auto-free)
#   PG_CONTAINER        Docker container name (default: nubi_e2e_pg)
#   JWT_SECRET          Min 32-byte secret (default: dev-secret below)
#   SKIP_DOCKER_PG      Set to "1" to use an already-running DATABASE_URL
#   DATABASE_URL        Required when SKIP_DOCKER_PG=1
#   PLAYWRIGHT_ARGS     Extra args forwarded to "playwright test"
#                       e.g. PLAYWRIGHT_ARGS="--headed" bash scripts/e2e.sh
#
# Requirements:
#   docker, python3, npm, npx
#   (playwright browsers installed: npx playwright install chromium)
#
# Exit codes:
#   0  All tests passed.
#   1  Setup or teardown failure.
#   2+ Playwright exit code propagated on test failure.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Python interpreter that has the backend deps installed. Override with PY=...
PY="${PY:-python3}"

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { printf "${GREEN}[e2e]${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW}[e2e]${NC}  %s\n" "$*"; }
error() { printf "${RED}[e2e]${NC}  %s\n" "$*" >&2; }

# ── PID / container tracking for cleanup ─────────────────────────────────────
_BACKEND_PID=""
_FRONTEND_PID=""
_PG_CONTAINER="${PG_CONTAINER:-nubi_e2e_pg}"
_START_DOCKER_PG=0

cleanup() {
  info "Tearing down..."
  [ -n "${_BACKEND_PID}" ]  && kill "${_BACKEND_PID}"  2>/dev/null || true
  [ -n "${_FRONTEND_PID}" ] && kill "${_FRONTEND_PID}" 2>/dev/null || true
  if [ "${_START_DOCKER_PG}" -eq 1 ]; then
    info "Stopping Postgres container ${_PG_CONTAINER}..."
    docker rm -f "${_PG_CONTAINER}" 2>/dev/null || true
  fi
  info "Done."
}

trap cleanup EXIT
trap 'error "Caught ERR — aborting."; exit 1' ERR
trap 'error "Interrupted."; exit 1'            INT TERM

# ── Find a free TCP port ──────────────────────────────────────────────────────
find_free_port() {
  python3 -c "
import socket, contextlib
with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
    s.bind(('', 0))
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    print(s.getsockname()[1])
"
}

# ── 1. Postgres ───────────────────────────────────────────────────────────────
if [ "${SKIP_DOCKER_PG:-0}" = "1" ]; then
  warn "SKIP_DOCKER_PG=1 — using existing DATABASE_URL=${DATABASE_URL:-<not set>}"
  if [ -z "${DATABASE_URL:-}" ]; then
    error "DATABASE_URL must be set when SKIP_DOCKER_PG=1"
    exit 1
  fi
else
  PG_PORT="${PG_PORT:-$(find_free_port)}"
  _START_DOCKER_PG=1

  info "Starting Postgres container (postgres:16-alpine) on port ${PG_PORT}..."
  docker run -d \
    --name "${_PG_CONTAINER}" \
    -e POSTGRES_USER=nubi \
    -e POSTGRES_PASSWORD=nubi \
    -e POSTGRES_DB=nubi \
    -p "${PG_PORT}:5432" \
    --rm \
    postgres:16-alpine \
    > /dev/null

  # Local docker Postgres never speaks SSL; disable the probe (avoids asyncpg's
  # 'unexpected connection_lost()' during the sslmode=prefer upgrade).
  export DATABASE_URL="postgresql://nubi:nubi@localhost:${PG_PORT}/nubi?sslmode=disable"
  info "DATABASE_URL=${DATABASE_URL}"

  # Wait until Postgres is ready (up to 30 s)
  info "Waiting for Postgres to be ready..."
  for i in $(seq 1 30); do
    if docker exec "${_PG_CONTAINER}" pg_isready -U nubi -d nubi -q 2>/dev/null; then
      info "Postgres ready after ${i}s."
      sleep 1  # brief settle so the first real connection isn't raced
      break
    fi
    [ "${i}" -eq 30 ] && { error "Postgres did not become ready in 30s."; exit 1; }
    sleep 1
  done
fi

# ── 2. Required env vars ──────────────────────────────────────────────────────
# Hermetic: never load the dev root .env (it would override DATABASE_URL etc.).
export ENV_FILE="${ENV_FILE:-/nonexistent/nubi-e2e.env}"
export JWT_SECRET="${JWT_SECRET:-dev-e2e-secret-key-minimum-32-bytes-here}"
export ENV="${ENV:-test}"
export COOKIE_SECURE="${COOKIE_SECURE:-false}"
export GOOGLE_CLIENT_ID="${GOOGLE_CLIENT_ID:-dummy-google-client-id}"
export GOOGLE_CLIENT_SECRET="${GOOGLE_CLIENT_SECRET:-dummy-google-client-secret}"

_BACKEND_PORT=8000
_FRONTEND_PORT=5173
export BACKEND_URL="${BACKEND_URL:-http://localhost:${_BACKEND_PORT}}"
export GOOGLE_REDIRECT_URI="${GOOGLE_REDIRECT_URI:-${BACKEND_URL}/api/v1/auth/google/callback}"
export FRONTEND_URL="${FRONTEND_URL:-http://localhost:${_FRONTEND_PORT}}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:${_FRONTEND_PORT}}"

# ── 3. Migrate ────────────────────────────────────────────────────────────────
info "Running database migrations..."
(cd "${REPO_ROOT}/database" && "${PY}" migrate.py)
info "Migrations complete."

# ── 4. Seed demo data ─────────────────────────────────────────────────────────
# seed.py provisions the superuser; --demo also seeds the full demo workspace
# (DuckDB datasource + queries + 10 dashboards from seed_data/demo/*.json).
if [ -f "${REPO_ROOT}/backend/seed.py" ]; then
  info "Seeding superuser + demo data via seed.py --demo..."
  (cd "${REPO_ROOT}/backend" && "${PY}" seed.py --demo)
  info "Seed complete."
else
  warn "No seed script found — skipping seeding."
fi

# ── 5. Start backend ──────────────────────────────────────────────────────────
info "Starting backend (uvicorn) on port ${_BACKEND_PORT}..."
( cd "${REPO_ROOT}/backend" && \
  KERNEL_LOCAL_ENABLED="${KERNEL_LOCAL_ENABLED:-true}" \
  exec "${PY}" -m uvicorn main:app \
    --host 0.0.0.0 \
    --port "${_BACKEND_PORT}" \
    --log-level warning ) &
_BACKEND_PID=$!
info "Backend PID: ${_BACKEND_PID}"

# ── 6. Start frontend ─────────────────────────────────────────────────────────
info "Starting Vite dev server on port ${_FRONTEND_PORT}..."
VITE_BACKEND_URL="${BACKEND_URL}" \
npm run dev -- --port "${_FRONTEND_PORT}" --strictPort &
_FRONTEND_PID=$!
info "Frontend PID: ${_FRONTEND_PID}"

# ── 7. Wait for health ────────────────────────────────────────────────────────
wait_for_http() {
  local url="$1"
  local label="$2"
  local max="${3:-60}"
  info "Waiting for ${label} at ${url} (up to ${max}s)..."
  for i in $(seq 1 "${max}"); do
    if curl -sf "${url}" -o /dev/null 2>/dev/null; then
      info "${label} is up after ${i}s."
      return 0
    fi
    sleep 1
  done
  error "${label} did not respond at ${url} within ${max}s."
  return 1
}

wait_for_http "${BACKEND_URL}/health" "backend" 60
wait_for_http "${FRONTEND_URL:-http://localhost:${_FRONTEND_PORT}}/" "frontend" 60

# ── 8. Run Playwright (or an override command, e.g. screenshot capture) ──────
export E2E_BASE_URL="${E2E_BASE_URL:-http://localhost:${_FRONTEND_PORT}}"
if [ -n "${E2E_RUN_CMD:-}" ]; then
  # scripts/screenshots.sh sets this to reuse the boot/seed/teardown logic
  # while running the screenshot capture instead of the test suite.
  info "Running override command against ${E2E_BASE_URL}: ${E2E_RUN_CMD}"
  bash -c "${E2E_RUN_CMD}"
  PW_EXIT=$?
else
  info "Running Playwright tests against ${E2E_BASE_URL}..."
  info "  Playwright args: ${PLAYWRIGHT_ARGS:-<none>}"
  # shellcheck disable=SC2086
  npx playwright test ${PLAYWRIGHT_ARGS:-}
  PW_EXIT=$?
fi

if [ "${PW_EXIT}" -eq 0 ]; then
  info "Run PASSED."
else
  error "Run exited with code ${PW_EXIT}."
fi

# Cleanup is handled by the EXIT trap.
exit "${PW_EXIT}"
