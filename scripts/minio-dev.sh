#!/usr/bin/env bash
# scripts/minio-dev.sh
#
# Standalone MinIO dev helper — no docker-compose needed.
# Spins up a MinIO container, waits for it to be healthy, creates the 'nubi'
# bucket, then prints the S3 env vars the DuckDB connector needs.
#
# Usage:
#   ./scripts/minio-dev.sh            # start MinIO + create bucket
#   ./scripts/minio-dev.sh stop       # stop + remove the container
#   ./scripts/minio-dev.sh logs       # tail container logs
#
# Requirements: Docker (or Podman aliased as docker)
set -euo pipefail

CONTAINER_NAME="nubi-minio-dev"
IMAGE="quay.io/minio/minio:latest"
MC_IMAGE="quay.io/minio/mc:latest"

MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"
S3_PORT="${S3_PORT:-9000}"
CONSOLE_PORT="${CONSOLE_PORT:-9001}"
BUCKET="${S3_BUCKET:-nubi}"
REGION="${S3_REGION:-us-east-1}"

# ── Helpers ──────────────────────────────────────────────────────────────────

log()  { printf '\033[1;34m[minio-dev]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[minio-dev]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[minio-dev]\033[0m %s\n' "$*" >&2; }

require_docker() {
  if ! command -v docker &>/dev/null; then
    err "docker is not installed or not in PATH."
    exit 1
  fi
}

# ── Sub-commands ─────────────────────────────────────────────────────────────

cmd_stop() {
  log "Stopping and removing container '$CONTAINER_NAME' ..."
  docker rm -f "$CONTAINER_NAME" 2>/dev/null && ok "Done." || log "Container was not running."
}

cmd_logs() {
  docker logs -f "$CONTAINER_NAME"
}

cmd_start() {
  require_docker

  # Remove stale container if it exists (might be stopped/crashed).
  if docker inspect "$CONTAINER_NAME" &>/dev/null; then
    log "Removing existing container '$CONTAINER_NAME' ..."
    docker rm -f "$CONTAINER_NAME" >/dev/null
  fi

  log "Starting MinIO container '$CONTAINER_NAME' ..."
  docker run -d \
    --name "$CONTAINER_NAME" \
    -p "${S3_PORT}:9000" \
    -p "${CONSOLE_PORT}:9001" \
    -e MINIO_ROOT_USER="$MINIO_ROOT_USER" \
    -e MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
    "$IMAGE" \
    server /data --console-address ":9001" \
    >/dev/null

  log "Waiting for MinIO to be ready ..."
  local endpoint="http://localhost:${S3_PORT}"
  local deadline=$(( $(date +%s) + 60 ))
  until curl -sf "${endpoint}/minio/health/live" >/dev/null 2>&1; do
    if (( $(date +%s) > deadline )); then
      err "Timed out waiting for MinIO at ${endpoint}"
      docker logs "$CONTAINER_NAME" >&2
      exit 1
    fi
    sleep 1
  done
  ok "MinIO is up at ${endpoint}"

  log "Creating bucket '${BUCKET}' ..."
  docker run --rm \
    --network "container:${CONTAINER_NAME}" \
    -e MC_HOST_local="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000" \
    "$MC_IMAGE" \
    mb --ignore-existing "local/${BUCKET}" \
    >/dev/null
  ok "Bucket '${BUCKET}' is ready."

  # ── Print the env vars the backend / DuckDB connector needs ──────────────
  cat <<ENV

  ╔══════════════════════════════════════════════════════════════╗
  ║  MinIO dev environment — add to your shell or .env.local    ║
  ╚══════════════════════════════════════════════════════════════╝

  export S3_ENDPOINT_URL="http://localhost:${S3_PORT}"
  export S3_ACCESS_KEY="${MINIO_ROOT_USER}"
  export S3_SECRET_KEY="${MINIO_ROOT_PASSWORD}"
  export S3_REGION="${REGION}"
  export S3_BUCKET="${BUCKET}"
  export S3_FORCE_PATH_STYLE="true"

  MinIO console: http://localhost:${CONSOLE_PORT}
    user:     ${MINIO_ROOT_USER}
    password: ${MINIO_ROOT_PASSWORD}

ENV
}

# ── Dispatch ─────────────────────────────────────────────────────────────────

ACTION="${1:-start}"
case "$ACTION" in
  start)  cmd_start ;;
  stop)   cmd_stop  ;;
  logs)   cmd_logs  ;;
  *)
    err "Unknown action '$ACTION'.  Usage: $0 [start|stop|logs]"
    exit 1
    ;;
esac
