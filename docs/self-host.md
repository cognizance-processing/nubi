# Self-Hosting Nubi

![Services, volumes, and traffic flow for a self-hosted Nubi deployment](illustration:SelfHostTopology)

This guide covers everything you need to run Nubi on your own infrastructure using Docker Compose: services, configuration, kernels, migrations, upgrades, and backups.

---

## Stack overview

The compose file (`docker-compose.yml`) defines five services on an internal `nubi` Docker network:

| Service | Image / runtime | Role | Ports & volumes |
|---|---|---|---|
| `frontend` | nginx 1.27 | Serves the Vite SPA; proxies `/api/*` → `backend:8000` and `/health` → backend | `:8080` published |
| `backend` | FastAPI / uvicorn | REST API at `/api/v1`; flows engine + scheduler; runs DB migrations on boot | `:8000` internal only |
| `db` | postgres:16 | Application database | `:5432` internal; `pg_data` volume |
| `minio` | MinIO (S3-compatible) | Exports, datasets, cache | `:9000` S3 API, `:9001` console; `minio_data` volume |
| `minio-init` | one-shot | Creates the `nubi` bucket, then exits | — |

The browser talks only to the frontend on `:8080`; the frontend proxies API traffic to the backend, which fans out to Postgres and MinIO.

`minio-init` is a one-shot init container that creates the `nubi` bucket after MinIO is healthy; it exits cleanly after that single step. Only ports `8080` (frontend), `9000` (MinIO S3 API), and `9001` (MinIO console) are published to the host. The backend (`8000`) and Postgres (`5432`) are internal only.

---

## Open-core boundaries

Nubi is open-core (GitLab CE/EE style). The community image excludes EE trees at build time via `.dockerignore`.

| Path | In community image | Contents |
|---|---|---|
| `backend/app/` (minus `ee/`) | Yes | Core API, flows, connectors, queries |
| `backend/app/ee/` | **No** | Billing, paid-tier enforcement |
| `src/` (minus `ee/`) | Yes | Core React SPA |
| `src/ee/` | **No** | Commercial UI extensions |
| `database/migrations/*.sql` | Yes | Core schema (always applied) |
| `database/migrations/ee/*.sql` | **No** | Billing, FX, wallet, invoices (requires `--ee`) |

When the EE tree is absent, `load_ee()` in `backend/app/ee/__init__.py` returns `False` silently. The feature-flag module (`backend/app/features.py`) defaults the commercial features `billing` and `paid_tiers` to disabled. The OSS build runs fully without them and is never usage-limited — quota enforcement is an EE concern.

---

## Prerequisites

- Docker >= 24 with the Compose plugin (`docker compose version`)
- `make` (optional but convenient)
- `curl` and `jq` (for the smoke test)

---

## Quick start

### 1. Clone and configure

```bash
git clone https://github.com/your-org/nubi.git
cd nubi
cp .env.compose .env.compose.local
```

Edit `.env.compose.local` and fill in at minimum the required secrets listed in the [Configuration](#configuration) section. The committed `.env.compose` contains only safe placeholder values and is never deployed as-is.

### 2. Start the stack

```bash
make up
# equivalent: docker compose up -d --build
```

The first run takes several minutes: `npm ci` + `vite build`, `pip install`, Postgres and MinIO data-directory initialisation, and migration execution all happen before the backend is healthy.

### 3. Open Nubi

Navigate to **http://localhost:8080**.

### 4. Smoke test

```bash
make smoke
# equivalent: bash scripts/smoke.sh
```

The smoke script starts the stack, waits for the health endpoint, runs five API checks, then tears down.

---

## Configuration

All configuration is passed as environment variables. The compose stack loads `.env.compose` via `env_file`; override by creating `.env.compose.local` or by exporting variables in your shell before running `docker compose`. Process-environment variables always take precedence over the file.

### Required for production

| Variable | Description | How to generate |
|---|---|---|
| `JWT_SECRET` | HS256 signing key (minimum 32 bytes; enforced at startup) | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID | [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret | Same |
| `GOOGLE_REDIRECT_URI` | OAuth callback URL | `https://<your-domain>/api/v1/auth/google/callback` |

The backend validates `JWT_SECRET` at startup and refuses to start if it is shorter than 32 bytes (RFC 7518 §3.2).

### Recommended for production

| Variable | Description | How to generate |
|---|---|---|
| `CONNECTOR_SECRET_KEY` | AES-256-GCM key for connector credentials at rest | `python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `MINIO_ROOT_USER` | MinIO / S3 access key | Choose a strong username |
| `MINIO_ROOT_PASSWORD` | MinIO / S3 secret key | `python -c "import secrets; print(secrets.token_hex(24))"` |
| `COOKIE_SECURE` | Set `true` when serving over HTTPS | — |

`CONNECTOR_SECRET_KEY` is required to use any database connectors (PostgreSQL, MySQL, BigQuery, etc.). Only ciphertext + nonce + key version are stored in the database; the key itself never touches Postgres.

### Runtime tunables

| Variable | Default | Description |
|---|---|---|
| `ENV` | `production` | Runtime label. `development` enables OpenAPI docs (`/docs`, `/redoc`) and the local subprocess kernel. |
| `CORS_ORIGINS` | `http://localhost:8080` | Comma-separated allowed origins |
| `FRONTEND_URL` | `http://localhost:8080` | Public URL used in OAuth redirects |
| `UVICORN_WORKERS` | `2` | Uvicorn worker processes |
| `FLOWS_WORKER_ENABLED` | `true` | Enable the in-process flows task worker |
| `S3_ENDPOINT_URL` | `http://minio:9000` | S3-compatible endpoint |
| `S3_BUCKET` | `nubi` | S3 bucket for exports, datasets, and cache |
| `S3_FORCE_PATH_STYLE` | `true` | Required for MinIO and most self-hosted S3 clones |

### Connector key rotation

For key rotation, supply a JSON map of version → base64-key and set the new key as the highest version:

```bash
CONNECTOR_SECRET_KEYS='{"1":"<old-b64-key>","2":"<new-b64-key>"}'
```

The highest numeric version is used for new encryptions; existing secrets encrypted under older versions are decrypted using their stored key-version until re-encrypted.

---

## Kernels

Nubi has two kernel tiers that complement each other.

### Browser kernel (DuckDB-WASM)

SQL cells execute entirely in the browser via DuckDB-WASM. No server-side compute is involved and no configuration is needed — it works out of the box.

### Server kernel (Python execution)

The browser does not run Python — every Python cell is routed to a server kernel via `POST /api/v1/compute/run`. The runner is selected in priority order (`backend/app/routes/compute.py`):

1. `KERNEL_REMOTE_PROVIDER=e2b` + `E2B_API_KEY` set → E2B Firecracker microVM (recommended for production)
2. `KERNEL_REMOTE_PROVIDER=modal` + `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` set → Modal container (**not yet implemented** — `run()` raises 503; use E2B for production)
3. `ENV != production` **and** `KERNEL_LOCAL_ENABLED=true` → local subprocess runner (development only)
4. None of the above → `/compute/run` returns `503 kernel_disabled`

**The local subprocess runner is explicitly blocked when `ENV=production`**, regardless of `KERNEL_LOCAL_ENABLED`. It offers only dev-grade isolation: same OS user, shared network namespace, no cgroup separation. Native wheels and large compute jobs require a remote runner in production.

**E2B setup:**

```bash
KERNEL_REMOTE_PROVIDER=e2b
E2B_API_KEY=e2b-your-key-here
```

**Modal setup:**

> **Note:** Modal execution is not yet implemented. The runner is selected by the router but always returns 503. Use E2B for production remote execution.

```bash
KERNEL_REMOTE_PROVIDER=modal
MODAL_TOKEN_ID=ak-...
MODAL_TOKEN_SECRET=as-...
```

If you do not need Python compute (SQL-only deployments), leave `KERNEL_REMOTE_PROVIDER` unset. Users will receive a clear `503 kernel_disabled` error if they attempt to run a Python cell.

---

## Database migrations

`database/migrate.py` is a forward-only runner (asyncpg). The entrypoint (`docker-entrypoint.sh`) runs it automatically before uvicorn starts.

**Core migrations** (`database/migrations/*.sql`) are applied in lexical order and tracked in the `schema_migrations` ledger table. Each migration runs inside its own transaction; failure rolls back that migration and stops the runner cleanly.

**EE migrations** (`database/migrations/ee/*.sql`) cover billing, FX rates, wallet, and invoices. They are skipped unless you pass `--ee` or set `NUBI_CLOUD=1` / `NUBI_EE=1`. OSS self-hosted deployments never need them.

Concurrent runners (multi-replica deploys, CI overlapping a manual run) serialize on Postgres advisory lock `727274`, so only one runner applies pending migrations at a time.

### Migration commands

Apply pending migrations without restarting the container:

```bash
make migrate
# equivalent: docker compose exec backend python /app/database/migrate.py
```

Inspect migration state (read-only):

```bash
make migrate-status
# equivalent: docker compose exec backend python /app/database/migrate.py --status
```

Include EE migration state:

```bash
docker compose exec backend python /app/database/migrate.py --status --ee
```

---

## Makefile targets

| Target | Description |
|---|---|
| `make up` | Build images (if needed) and start the stack in the background |
| `make down` | Stop all services **and remove volumes** (wipes Postgres + MinIO data) |
| `make logs` | Stream logs from all services (`Ctrl-C` to stop) |
| `make migrate` | Apply pending migrations in the running backend container |
| `make migrate-status` | Show applied vs pending migrations |
| `make smoke` | Run the end-to-end smoke test |
| `make config-check` | Validate `docker-compose.yml` syntax |

`make down` passes `-v` to `docker compose down`, which removes the `pg_data` and `minio_data` volumes. To stop services while keeping data: `docker compose stop` or `docker compose down` (without `-v`).

---

## Ports

| Port | Service | Visibility |
|---|---|---|
| `8080` | frontend (nginx) | Published — reverse-proxy this in production |
| `8000` | backend (uvicorn) | Internal only |
| `5432` | db (Postgres 16) | Internal only |
| `9000` | minio (S3 API) | Published |
| `9001` | minio (console) | Published — restrict access in production |

To run on a different public port, change the `ports` mapping in `docker-compose.yml` or add a `docker-compose.override.yml`.

---

## Reverse proxy and TLS

Point your reverse proxy at port `8080`. The nginx frontend already proxies `/api/*` and `/health` to the backend, so your proxy only needs to forward traffic to port `8080`.

Example Caddy configuration:

```
your-domain.com {
    reverse_proxy localhost:8080
}
```

After switching to a custom domain, update these env vars:

```bash
COOKIE_SECURE=true
FRONTEND_URL=https://your-domain.com
CORS_ORIGINS=https://your-domain.com
GOOGLE_REDIRECT_URI=https://your-domain.com/api/v1/auth/google/callback
```

For split-host deployments (frontend and backend on separate domains), rebuild the frontend image with:

```bash
docker compose build \
  --build-arg VITE_BACKEND_URL=https://api.your-domain.com \
  frontend
```

The default `VITE_BACKEND_URL=""` (empty) means the SPA calls `/api` on the same origin it is served from — correct for single-host deployments where nginx handles the proxy.

---

## Upgrading

```bash
git pull
make up   # rebuilds images and restarts services
```

Migrations run automatically on each restart. To apply migrations without a full restart: `make migrate`.

---

## Backups

There is no built-in backup tooling. Two data sources need protection:

**Postgres** (users, orgs, connectors, dashboards, flows, all application state):

```bash
docker compose exec db pg_dump -U nubi nubi > backup-$(date +%Y%m%d).sql
```

**MinIO** (exports, datasets, parquet cache — the `minio_data` volume):

Use the MinIO Client (`mc`) or the MinIO console at `http://localhost:9001` to replicate or export the `nubi` bucket. For production, configure MinIO replication to a second site, or replace the bundled MinIO service with an external S3-compatible bucket (set `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and `S3_BUCKET` accordingly).

---

## Troubleshooting

**Backend keeps restarting**

```bash
docker compose logs backend
```

Common causes:
- `DATABASE_URL` unreachable — the entrypoint retries up to 60 times (1 s apart) before aborting.
- `JWT_SECRET` shorter than 32 bytes — the backend raises a `ValueError` on startup.
- Missing required env vars (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`).

**Migrations fail**

```bash
docker compose logs backend | grep -i migrat
```

Each migration is transactional and rolls back cleanly on failure. Fix the root cause, then re-run `make migrate`.

**Frontend shows "Cannot connect to API"**

```bash
curl http://localhost:8080/health
```

If that returns a non-200, check `docker compose logs frontend` for nginx errors and `docker compose logs backend` for startup failures.

**Python cells return `kernel_disabled` (503)**

No server kernel is configured. Set `KERNEL_REMOTE_PROVIDER=e2b` and `E2B_API_KEY` for production, or set `ENV=development` and `KERNEL_LOCAL_ENABLED=true` for local development. SQL cells run in the browser (DuckDB-WASM) and never require a server kernel; only Python cells do.

**MinIO bucket not created**

```bash
docker compose logs minio-init
```

If it exited before MinIO was healthy, `make up` will re-run the init container (it has `restart: "no"`, so it only runs once per compose up cycle).

---

## Community vs EE

The community image is built with `backend/app/ee/` and `src/ee/` excluded via `.dockerignore`. The feature-flag module (`backend/app/features.py`) defaults all commercial features (`billing`, `paid_tiers`) to disabled, and no quota checker is registered — so OSS self-host is never usage-limited.

If you hold an EE licence and have the EE source tree, remove the `ee/` exclusions from `.dockerignore` and rebuild:

```bash
docker compose build
```

`backend/app/ee/__init__.py → load_ee()` registers commercial features at startup. Core code never imports from `app.ee` directly. Apply EE migrations with:

```bash
NUBI_EE=1 docker compose exec backend python /app/database/migrate.py
```

See [Architecture — Open Core](/docs/architecture-open-core) for a detailed treatment of the CE/EE split.
