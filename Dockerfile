# syntax=docker/dockerfile:1
# Nubi combined image — FastAPI backend + embedded SPA in ONE image.
# Build context: repo root (docker build -f Dockerfile .)
#
# This is the image deployed to Fly.io (see fly.toml). The same image runs as
# two Fly processes:
#   app    — uvicorn serving the API *and* the built SPA (STATIC_DIR=/app/dist,
#            see the static-SPA block in backend/main.py)
#   worker — `python worker.py` (flows scheduler loop + worker pool)
#
# backend/Dockerfile remains the backend-only image used by docker-compose;
# do not conflate the two.
#
# OPEN-CORE NOTE: backend/app/ee/ is EXCLUDED via .dockerignore (OSS backend).
# src/ee/ IS included in the build context because the SPA entry (src/App.jsx)
# imports the EE registry seam; EE frontend features stay feature-gated at
# runtime and inert without a backend EE layer.

# ── Stage 1: frontend build (Vite SPA) ──────────────────────────────────────
# No VITE_BACKEND_URL on purpose: the SPA is served by the backend itself, so
# all /api/v1 calls are same-origin.
FROM node:20-alpine AS frontend
WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

# Source needed by `vite build` (docs/ is bundled by src/docs/registry.js).
COPY index.html vite.config.js tailwind.config.js postcss.config.js ./
COPY public/ public/
COPY src/ src/
COPY docs/ docs/

RUN npm run build

# Embed bundles (<nubi-dashboard> / <nubi-widgets> drop-in scripts) → dist-embed/.
# Served by the backend at /embed/* (see the embed block in backend/main.py).
COPY vite.embed.config.js vite.widgets.config.js ./
COPY embed/ embed/
RUN npm run build:embed && npm run build:widgets

# ── Stage 2: Python dependencies ────────────────────────────────────────────
FROM python:3.13-slim AS pydeps
WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 3: runtime ────────────────────────────────────────────────────────
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

# Seed sources needed at runtime by the onboarding demo bundle
# (app/demo_bundle.py imports seed_data.generators and seed_data_duckdb;
# parquet/duckdb artefacts are regenerated on demand, see .dockerignore).
COPY backend/seed.py /app/backend/seed.py
COPY backend/seed_data_duckdb.py /app/backend/seed_data_duckdb.py
COPY backend/seed_data/ /app/backend/seed_data/

# Migrations (run on Fly via `release_command`, see fly.toml).
COPY database/ /app/database/

# Built SPA — backend/main.py serves it when STATIC_DIR points here.
COPY --from=frontend /build/dist /app/dist
ENV STATIC_DIR=/app/dist

# Embed bundles — backend/main.py mounts them at /embed/* when present.
COPY --from=frontend /build/dist-embed /app/dist-embed
ENV EMBED_STATIC_DIR=/app/dist-embed

# Non-root user for security.
RUN useradd --no-create-home --shell /bin/false appuser
# demo_bundle regenerates parquet/duckdb seed artefacts under seed_data/.
RUN chown -R appuser /app/backend/seed_data
USER appuser

# Both process commands (fly.toml [processes]) run from this directory.
WORKDIR /app/backend

EXPOSE 8000
# Default command = the web process; fly.toml overrides per process.
# Migrations are NOT run here — Fly's release_command handles them.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
