# syntax=docker/dockerfile:1
# Combined Nubi image: builds the Vite frontend AND runs the FastAPI backend,
# which serves the built SPA from /app/dist on the same origin as the API.
# Build context: repo root →  docker build -t nubi .

# ── Stage 1: build the frontend (Vite) ──────────────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /app

# Backend URL baked into the bundle. Empty = same-origin (combined image).
ARG VITE_BACKEND_URL=""
ENV VITE_BACKEND_URL=${VITE_BACKEND_URL}

COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Production build uses the 'main' mode (.env.main); falls back to defaults if absent.
RUN npm run build

# ── Stage 2: python dependencies ────────────────────────────────────────────
FROM python:3.13-slim AS pydeps
WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 3: runtime (backend + static frontend) ────────────────────────────
FROM python:3.13-slim AS runtime
WORKDIR /app

# Python packages from the build stage
COPY --from=pydeps /install /usr/local

# Backend source + migrations + entrypoint
COPY backend/ /app/backend/
COPY database/ /app/database/
COPY docker-entrypoint.sh /app/docker-entrypoint.sh

# Built SPA — served by FastAPI via STATIC_DIR
COPY --from=frontend /app/dist /app/dist
ENV STATIC_DIR=/app/dist

RUN chmod +x /app/docker-entrypoint.sh \
    && useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
