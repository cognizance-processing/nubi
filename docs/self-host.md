# Self-Hosting Nubi (Community / OSS Edition)

This guide explains how to run the full Nubi stack on your own infrastructure
using Docker Compose.

---

## Architecture

```
Browser
  │
  ▼  :8080
┌─────────────────────────────────┐
│  frontend  (nginx)              │
│  - serves the Vite SPA          │
│  - proxies /api/* → backend     │
│  - proxies /health → backend    │
└──────────────┬──────────────────┘
               │  :8000 (internal)
               ▼
┌─────────────────────────────────┐
│  backend  (FastAPI / uvicorn)   │
│  - REST API at /api/v1          │
│  - Flows engine + scheduler     │
│  - Runs DB migrations on boot   │
└──────────┬────────────┬─────────┘
           │            │
           ▼            ▼
┌──────────────┐  ┌─────────────────────────────────┐
│  db          │  │  minio  (S3-compatible storage)  │
│ (postgres    │  │  - object storage for exports,   │
│  16-alpine)  │  │    datasets, cache files         │
│  pg_data vol │  │  - minio_data volume             │
└──────────────┘  └─────────────────────────────────┘
```

All services share the internal `nubi` Docker network.  Only the
frontend port (8080) and the MinIO ports (9000 S3 API, 9001 web console)
are published to the host.

---

## Open-Core

Nubi is **open-core** (GitLab CE/EE style).

| Tree            | Included in community image? | Description                       |
|-----------------|------------------------------|-----------------------------------|
| `backend/app/`  | Yes (minus `ee/`)            | Core API, flows, connectors, query |
| `backend/app/ee/` | **No**                     | EE billing, paid-tier enforcement  |
| `src/`          | Yes (minus `ee/`)            | Core React SPA                     |
| `src/ee/`       | **No**                       | EE commercial UI extensions        |

The `ee/` trees are excluded at build time via `.dockerignore`.  If the EE
tree is absent, `load_ee()` returns `False` and all commercial features are
silently disabled — the OSS build runs fully without them.

---

## Prerequisites

- Docker >= 24 with the Compose plugin (`docker compose version`)
- `make` (optional but recommended)
- `curl` and `jq` (for the smoke test)

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/your-org/nubi.git
cd nubi
cp .env.compose .env.compose.local
```

Edit `.env.compose.local` and fill in the required secrets (see the
[Configuration](#configuration) section below).

### 2. Start the stack

```bash
make up
# or: docker compose up -d --build
```

The first run can take several minutes while:
- `npm ci` + `vite build` compile the frontend
- `pip install` installs Python dependencies
- Postgres and MinIO initialise their data directories
- The entrypoint runs database migrations

### 3. Open Nubi

Navigate to **http://localhost:8080** in your browser.

### 4. Run the smoke test

```bash
make smoke
# or: bash scripts/smoke.sh
```

The smoke test brings up the stack, waits for health, runs five API checks,
then tears down.

---

## Configuration

All configuration is passed via environment variables.  The compose stack
reads `.env.compose` (committed, safe placeholder values) and you can
override any variable by creating `.env.compose.local` or by setting
environment variables in your shell before running `docker compose`.

### Required for production

| Variable | Description | How to generate |
|---|---|---|
| `JWT_SECRET` | HS256 signing key (≥ 32 bytes) | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | [Google Cloud Console](https://console.cloud.google.com) → Credentials |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | Same as above |
| `GOOGLE_REDIRECT_URI` | OAuth callback URL | `https://<your-domain>/api/v1/auth/google/callback` |

### Recommended for production

| Variable | Description | How to generate |
|---|---|---|
| `NUBI_SECRETS_KEY` | Fernet key for named-secrets encryption | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `CONNECTOR_SECRET_KEY` | AES-256 key for connector credential encryption | `python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `COOKIE_SECURE` | Set `true` when serving over HTTPS | — |
| `MINIO_ROOT_USER` | MinIO / S3 access key | Choose a strong username |
| `MINIO_ROOT_PASSWORD` | MinIO / S3 secret key | `python -c "import secrets; print(secrets.token_hex(24))"` |

### Runtime tunables

| Variable | Default | Description |
|---|---|---|
| `ENV` | `production` | Runtime environment tag |
| `CORS_ORIGINS` | `http://localhost:8080` | Comma-separated allowed CORS origins |
| `FRONTEND_URL` | `http://localhost:8080` | Public URL of the frontend (used in emails / redirects) |
| `KERNEL_LOCAL_ENABLED` | `true` | Allow local subprocess kernel (set `false` in production for isolation) |
| `FLOWS_INPROCESS_WORKER` | `true` | Run flows scheduler inside the API process |
| `FLOWS_WORKER_ENABLED` | `true` | Enable the flows task worker entirely |
| `UVICORN_WORKERS` | `2` | Number of uvicorn worker processes |
| `S3_ENDPOINT_URL` | `http://minio:9000` | S3-compatible endpoint for object storage |
| `S3_BUCKET` | `nubi` | S3 bucket for exports, datasets, and cache |

---

## Makefile targets

| Target | Description |
|---|---|
| `make up` | Build images and start the stack in the background |
| `make down` | Stop all services and remove all volumes (Postgres + MinIO data) |
| `make logs` | Stream logs from all services (Ctrl-C to stop) |
| `make migrate` | Apply pending migrations in the running backend container |
| `make migrate-status` | Show applied vs pending migrations |
| `make smoke` | Run the end-to-end smoke test |
| `make config-check` | Validate docker-compose.yml syntax |

---

## Database migrations

Migrations run automatically when the backend container starts (via
`docker-entrypoint.sh`).  They are forward-only SQL files under
`database/migrations/`, applied in lexical order and tracked in the
`schema_migrations` ledger table.

**Open-source migrations** (`database/migrations/*.sql`) are applied by
default.  EE/cloud migrations (`database/migrations/ee/*.sql`) — covering
billing, FX rates, wallet, and invoices — are skipped unless you pass
`--ee` or set `NUBI_CLOUD=1` / `NUBI_EE=1` in the environment.  Self-hosted
OSS deployments never need the EE migrations.

To run migrations manually (e.g. after upgrading):

```bash
make migrate
# or: docker compose exec backend python /app/database/migrate.py
```

To inspect migration state:

```bash
make migrate-status
```

---

## Upgrading

```bash
git pull
make up        # rebuilds images and restarts services
```

Migrations are applied automatically on each restart.

---

## Ports

| Port | Service | Notes |
|---|---|---|
| `8080` | frontend (nginx) | Public-facing; proxies /api to the backend |
| `8000` | backend (uvicorn) | Internal only; proxied via nginx |
| `5432` | db (postgres) | Internal only; not published |
| `9000` | minio (S3 API) | Published; used for object storage access |
| `9001` | minio (web console) | Published; MinIO admin UI |

To run on a different port, set the `ports` mapping in `docker-compose.yml`
or override with a `docker-compose.override.yml`.

---

## Reverse proxy / TLS

To put Nubi behind a reverse proxy (e.g. nginx, Caddy, Traefik):

1. Point the reverse proxy at port `8080` (the nginx frontend).
2. Terminate TLS at the reverse proxy.
3. Set `COOKIE_SECURE=true` in your env.
4. Update `CORS_ORIGINS` and `FRONTEND_URL` to your public domain.
5. Update `GOOGLE_REDIRECT_URI` to `https://<your-domain>/api/v1/auth/google/callback`.

Example Caddy snippet:

```
your-domain.com {
    reverse_proxy localhost:8080
}
```

---

## Troubleshooting

**Backend does not start / keeps restarting**

Check logs: `docker compose logs backend`

Common causes:
- `DATABASE_URL` unreachable (the entrypoint retries 60 times, 1s apart).
- `JWT_SECRET` shorter than 32 bytes.
- Missing required env vars (`GOOGLE_CLIENT_ID`, etc.).

**Migrations fail**

```bash
docker compose logs backend | grep -i migration
```

Each migration runs in its own transaction and rolls back on failure.  Fix
the error, then re-run `make migrate`.

**Frontend shows "Cannot connect to API"**

Verify that nginx is proxying correctly:

```bash
curl http://localhost:8080/health
```

If that fails, check `docker compose logs frontend`.

**"no access_token" in smoke test**

The smoke test registers a new user on each run.  If registration is
disabled or the backend is unhealthy this will fail.  Check backend logs.

---

## Community vs EE

The community image is built with `backend/app/ee/` and `src/ee/` excluded
(via `.dockerignore`).  The feature-flag system (`backend/app/features.py`,
`src/lib/features.js`) defaults all commercial features to **disabled**.

If you have an EE licence and the EE tree, build with:

```bash
# Remove the ee/ exclusions from .dockerignore, then:
docker compose build
```

The EE init code (`backend/app/ee/__init__.py → load_ee()`) registers
commercial features at startup; the OSS core never imports `ee/` directly.
