# syntax=docker/dockerfile:1
# Build context: repo root (so both backend/ and database/ are available)
# docker build -f backend/Dockerfile .

# ── Build stage: install Python dependencies ──────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies for asyncpg (C extension)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source (backend/)
COPY backend/ .

# Copy database migrations and runner into /app/database/
COPY database/ /app/database/

# Make entrypoint executable
RUN chmod +x /app/docker-entrypoint.sh

# Non-root user for security
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
