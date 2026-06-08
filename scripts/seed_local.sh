#!/usr/bin/env bash
# scripts/seed_local.sh — migrate + seed demo data against a local Postgres DB.
#
# Usage
# -----
#   DATABASE_URL=postgresql://user:pass@localhost/nubi_dev bash scripts/seed_local.sh
#
# Requirements
# ------------
# - DATABASE_URL must be set in the environment.
# - Run from the repo root, or any directory (the script resolves paths via $SCRIPT_DIR).
# - Python (3.11+) with asyncpg and the backend dependencies installed.
#
# Behaviour
# ---------
# 1. Runs database/migrate.py    — applies all pending SQL migrations (idempotent).
# 2. Runs backend/seed.py --demo — seeds superuser + demo workspace (idempotent).
#
# The script does NOT start or stop any server processes.
# Safe to run multiple times — both migration and seed are idempotent.

set -euo pipefail

# ── Resolve absolute paths regardless of cwd ──────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MIGRATE_SCRIPT="${REPO_ROOT}/database/migrate.py"
# Superuser + comprehensive demo workspace (DuckDB datasource + queries + 10 dashboards).
SEED_SCRIPT="${REPO_ROOT}/backend/seed.py"

# ── Require DATABASE_URL ───────────────────────────────────────────────────────
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo ""
    echo "ERROR: DATABASE_URL is not set."
    echo ""
    echo "  Example:"
    echo "    DATABASE_URL=postgresql://postgres:postgres@localhost/nubi_dev \\"
    echo "      bash scripts/seed_local.sh"
    echo ""
    exit 1
fi

echo ""
echo "============================================================"
echo "  Nubi local seed"
echo "  DATABASE_URL: ${DATABASE_URL}"
echo "============================================================"
echo ""

# ── Step 1: Run migrations ────────────────────────────────────────────────────
echo ">>> Step 1/2 — Applying migrations ..."
python "${MIGRATE_SCRIPT}"
echo ""

# ── Step 2: Seed demo workspace ───────────────────────────────────────────────
echo ">>> Step 2/2 — Seeding demo workspace ..."
cd "${REPO_ROOT}/backend"
python "${SEED_SCRIPT}" --demo
echo ""

echo "Done. The demo workspace is ready."
echo ""
