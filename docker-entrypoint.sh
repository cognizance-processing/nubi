#!/usr/bin/env bash
# Backend container entrypoint: wait for DB, run migrations, then start uvicorn.
set -euo pipefail

echo "[entrypoint] Waiting for database to be ready..."

# Wait until we can connect to postgres (max 60 attempts, 1s sleep)
MAX_RETRIES=60
ATTEMPT=0
until python - <<'PYEOF'
import os, asyncpg, asyncio, sys

async def check():
    url = os.environ["DATABASE_URL"]
    try:
        conn = await asyncpg.connect(url)
        await conn.close()
        print("[entrypoint] Database is reachable.")
        sys.exit(0)
    except Exception as e:
        print(f"[entrypoint] DB not ready: {e}", file=sys.stderr)
        sys.exit(1)

asyncio.run(check())
PYEOF
do
    ATTEMPT=$((ATTEMPT + 1))
    if [ "$ATTEMPT" -ge "$MAX_RETRIES" ]; then
        echo "[entrypoint] ERROR: Database not reachable after $MAX_RETRIES attempts. Aborting." >&2
        exit 1
    fi
    echo "[entrypoint] DB not ready (attempt $ATTEMPT/$MAX_RETRIES), retrying in 1s..."
    sleep 1
done

echo "[entrypoint] Running database migrations..."
python /app/database/migrate.py

echo "[entrypoint] Starting uvicorn on :8000..."
# Workers: default 2 (API-only; SPA is served by the nginx frontend container).
# Override via UVICORN_WORKERS env var for higher-concurrency deployments.
WORKERS="${UVICORN_WORKERS:-2}"
cd /app/backend
exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers "${WORKERS}"
