# syntax=docker/dockerfile:1
# Nubi backend image — FastAPI + uvicorn.
# Build context: repo root (docker build -f backend/Dockerfile .)
#
# OPEN-CORE NOTE: The ee/ tree (backend/app/ee/) is EXCLUDED from this image.
# This is the community/OSS image.  EE functionality is absent and any attempt
# to import it at runtime will be silently ignored (load_ee() returns False).

# ── Stage 1: Python dependencies ────────────────────────────────────────────
FROM python:3.13-slim AS pydeps
WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime
WORKDIR /app

# libpq is required at runtime by asyncpg / adbc-driver-postgresql.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Python packages from the build stage.
COPY --from=pydeps /install /usr/local

# Backend source (app/, main.py, worker.py) — ee/ is NOT copied (OSS image).
COPY backend/app /app/backend/app
COPY backend/main.py /app/backend/main.py
COPY backend/worker.py /app/backend/worker.py

# Migrations (needed by docker-entrypoint.sh and database/migrate.py).
COPY database/ /app/database/

# Entrypoint script.
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Non-root user for security.
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
