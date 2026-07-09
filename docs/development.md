# Developing Nubi

This guide is for contributors working on Nubi itself — the backend, frontend,
CLI, or docs. If you just want to *run* Nubi, see
[Self-hosting](/docs/self-host) instead.

## Repo layout

```
nubi/
├── backend/          FastAPI app (Python 3.11+)
│   ├── app/          flows/, connectors/, routes/, auth/, secrets/,
│   │                 bridges/, lakehouse/, storage/, ee/ (Cloud billing code)
│   ├── tests/        pytest suite (in-memory DB fakes — no live DB needed)
│   ├── main.py       API entrypoint
│   ├── worker.py     flows worker entrypoint (scheduler + task pool)
│   └── seed.py       superuser + demo workspace seeding
├── database/         SQL migrations + migrate.py runner
├── src/              React 19 frontend (Vite)
│   └── docs/         registry.js — the in-app docs navigation
├── docs/             product + contributor docs (markdown, rendered in-app)
├── public/docs/      static docs assets, incl. generated screenshots
├── cli/              `nubi` Python CLI (pip package)
├── sdk/              `@nubi/sdk` JavaScript SDK
├── e2e/              Playwright end-to-end tests
└── scripts/          dev tooling (e2e.sh, docs-screenshots.mjs, …)
```

Open-core boundary: everything under `backend/app/ee/` (billing, licensing) is
Nubi Cloud billing code — it ships in the repo like everything else, but only
activates when Nubi's own Cloud deployment sets `NUBI_LICENSE_KEY`. There is
no separate self-hosted paid edition. Keep new cloud/billing code in `ee/` —
see [Open core](/docs/open-core).

## Running the dev stack

Two options.

**Option A — Docker Compose (one command, slower edit loop):**

```bash
make up        # build + start app, API, Postgres, MinIO; migrates + seeds
make logs      # stream logs
make smoke     # health + auth + query smoke test (needs curl, jq)
make down      # stop and wipe volumes
```

**Option B — dev servers (fast refresh; what most contributors use):**

```bash
# One-time setup
python3 -m venv .venv-backend
.venv-backend/bin/python -m pip install -r requirements.txt
npm install

# Database: any Postgres works. Easiest is a throwaway container:
docker run -d --name nubi-pg -e POSTGRES_USER=nubi -e POSTGRES_PASSWORD=nubi \
  -e POSTGRES_DB=nubi -p 5432:5432 postgres:16-alpine
export DATABASE_URL='postgresql://nubi:nubi@localhost:5432/nubi?sslmode=disable'

# Migrate + seed the demo workspace (superuser + demo connector/queries/boards)
cd database && ../.venv-backend/bin/python migrate.py && cd ..
cd backend  && ../.venv-backend/bin/python reset_db.py --demo && cd ..

# Run both servers (API :8000, Vite :5173)
npm run dev:full
```

Dev login: `admin@nubi.dev` / `nubi-admin-2026` (created by the seed; override
with `NUBI_ADMIN_EMAIL` / `NUBI_ADMIN_PASSWORD`).

| Service  | Port | Notes                                   |
|----------|------|-----------------------------------------|
| Frontend | 5173 | Vite; proxies `/api` to the backend so auth cookies stay same-origin |
| API      | 8000 | FastAPI + Uvicorn                       |
| Postgres | 5432 | compose / container                     |
| MinIO    | 9000 | optional S3-compatible storage (`scripts/minio-dev.sh`) |

`npm run db:reset:demo` re-seeds from scratch whenever your local data gets
into a weird state.

## Testing

| Suite          | Command                                        | Needs                       |
|----------------|------------------------------------------------|-----------------------------|
| Backend        | `cd backend && python -m pytest tests/`        | venv only — DB is faked in-memory (`tests/conftest.py`) |
| MCP server     | `cd mcp && pytest tests/`                      | venv only                   |
| CLI            | `cd cli && pytest tests/`                      | venv only                   |
| Frontend units | `npm run test:dash`                            | node only                   |
| Embed units    | `npm run test:embed`                           | node only (vitest)          |
| Embed E2E      | `npm run test:e2e:embed`                       | node, Playwright (requires the embed bundle built: `npm run build:embed`) |
| Lint           | `npm run lint`                                 | node only                   |
| End-to-end     | `bash scripts/e2e.sh`                          | docker, node, python        |
| API E2E        | `npm run test:e2e:api`                         | python, live Postgres + DuckDB |

`scripts/e2e.sh` is fully self-contained: it starts an ephemeral Postgres
container on a free port, migrates, seeds the demo workspace, boots both
servers, runs `npx playwright test`, and tears everything down. Useful knobs:

```bash
PLAYWRIGHT_ARGS="--headed e2e/flows.spec.js" bash scripts/e2e.sh   # one spec, headed
SKIP_DOCKER_PG=1 DATABASE_URL=... bash scripts/e2e.sh              # reuse a DB
```

The same script powers the screenshot pipeline via the `E2E_RUN_CMD` override
— see [Docs & screenshots](/docs/docs-and-screenshots).

## End-to-end API tests

`backend/tests/e2e/` contains a live HTTP E2E suite that drives a real uvicorn
process against real Postgres and local parquet files (no mocks, no monkeypatches).

**Pre-requisites**

1. Demo workspace seeded: `npm run db:reset:demo` (one-time; re-run after `db:reset`)
2. Postgres running (default: `postgresql://nubi:nubi@localhost:5432/nubi`)
3. `.env` present with `JWT_SECRET` and `NUBI_SECRETS_KEY`

**Run**

```bash
# Quick
export RUN_E2E=1
cd backend && python -m pytest tests/e2e -q

# Or via npm
npm run test:e2e:api
```

Without `RUN_E2E=1` the entire suite is skipped with a clear message — safe to
run in CI pipelines that don't have a live database.

**Coverage** (52 tests)

| File | Area |
|------|------|
| `test_auth_tenancy.py` | JWT scopes, org isolation, 401/403/404 |
| `test_query.py` | Raw SQL (`author:sql` scope), registered queries, Arrow IPC |
| `test_metric_query.py` | Governed metrics — dimensions, filters, top_n, ordering |
| `test_kpi_targets.py` | Green/amber/red RAG columns (`_target`, `_vs_target`, `_pct_to_goal`, `_rag`) |
| `test_webhooks.py` | CRUD, SSRF guard (localhost/RFC1918/cloud-metadata/file/ftp blocked) |
| `test_provisioning.py` | `/apply` portability bundles — idempotent, dry_run |
| `test_rls.py` | Row-level security via JWT `policies` claim |
| `test_watches.py` | Threshold watches — create, evaluate, breach detection |

**Known limitations**

- `retail_nsv`'s `time_dimension.column` (`month`) is a pre-bucketed VARCHAR
  (`'2025-06'`). DuckDB's `DATE_TRUNC` requires a `DATE`/`TIMESTAMP` input, so
  `time_grain` on this metric raises a type error. The E2E tests work around
  this where needed. Fixing the product requires migrating the column to
  `DATE` type (tracked separately).

## Conventions

- **Migrations** use an **in-place convention**: every SQL file is written so
  the full schema for its tables is expressed using `CREATE TABLE IF NOT EXISTS`
  (and `CREATE INDEX IF NOT EXISTS`). When a table needs a new column, the
  column is added to the `CREATE TABLE` statement in that file **and** the
  database is reset from scratch (`npm run db:reset:demo`). There are **no
  `ALTER TABLE` or `DROP TABLE` statements** in migration files — the schema is
  always reconstructed from the current state of the files, not accumulated
  incrementally. Never add a new migration file just to `ALTER` an existing
  table; instead fold the change into the relevant existing file and reset.
- **Secrets never round-trip**: connector credentials are AES-256-GCM
  encrypted at rest and come back blank from the API. See
  [Secrets](/docs/secrets).
- **Lazy connector imports**: heavy drivers (BigQuery, Snowflake, …) must be
  imported inside the connector factory, not at module top, so the core app
  starts without optional dependencies installed.
- **Docs are part of the product** — they render in-app at `/docs`. If your
  change alters UI or behavior described in `docs/*.md`, update the doc in the
  same PR, and regenerate screenshots if the UI changed visibly
  ([how](/docs/docs-and-screenshots)).
