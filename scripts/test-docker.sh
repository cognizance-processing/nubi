#!/usr/bin/env bash
# Comprehensive containerized test run (OrbStack / Docker).
#
# The production backend image is runtime-only (no tests/seed), so we run a
# one-off container FROM that image with the repo bind-mounted and the real
# Dockerised Postgres on the compose network. This exercises the full backend
# suite — in-memory + PG integration + security + export pipeline — against a
# clean, seeded Postgres, with the render deps installed at runtime.
#
# Usage:  bash scripts/test-docker.sh
set -uo pipefail
cd "$(dirname "$0")/.."

echo "[1/3] Fresh DB + object store (clean volumes)…"
docker compose down -v >/dev/null 2>&1 || true
docker compose up -d db minio
# wait for Postgres healthy
for _ in $(seq 1 40); do
  [ "$(docker compose ps db --format '{{.Health}}' 2>/dev/null)" = "healthy" ] && break
  sleep 2
done

echo "[2/3] Migrating + seeding the clean Postgres…"
docker compose run --rm --no-deps --user root \
  -v "$PWD:/src" -w /src/backend \
  --entrypoint bash backend -lc '
    python /src/database/migrate.py
    python seed.py --demo
  '

echo "[3/3] Installing render/test deps + running the FULL backend suite (real PG)…"
docker compose run --rm --no-deps --user root \
  -v "$PWD:/src" -w /src/backend \
  --entrypoint bash backend -lc '
    apt-get update -qq >/dev/null 2>&1 && apt-get install -y -qq libcairo2 >/dev/null 2>&1 || true
    pip install -q -r /src/requirements-dev.txt pytest pytest-asyncio >/dev/null 2>&1 || true
    python -m pytest -q -p no:cacheprovider --ignore=tests/test_git_env.py
  '
rc=$?
echo "=== docker test exit: $rc ==="
exit $rc
