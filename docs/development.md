# Developing Nubi

This guide is for contributors working on Nubi itself — the backend, frontend,
CLI, or docs. If you just want to *run* Nubi, see
[Self-hosting](/docs/self-host) instead.

## Repo layout

```
nubi/
├── backend/          FastAPI app (Python 3.11+)
│   ├── app/          flows/, connectors/, routes/, auth/, secrets/,
│   │                 bridges/, lakehouse/, storage/, ee/ (enterprise)
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
enterprise; the rest is Apache-2.0 core. Keep new cloud/billing code in `ee/`
— see [Open core](/docs/open-core).

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
| Frontend units | `npm run test:dash`                            | node only                   |
| Lint           | `npm run lint`                                 | node only                   |
| End-to-end     | `bash scripts/e2e.sh`                          | docker, node, python        |

`scripts/e2e.sh` is fully self-contained: it starts an ephemeral Postgres
container on a free port, migrates, seeds the demo workspace, boots both
servers, runs `npx playwright test`, and tears everything down. Useful knobs:

```bash
PLAYWRIGHT_ARGS="--headed e2e/flows.spec.js" bash scripts/e2e.sh   # one spec, headed
SKIP_DOCKER_PG=1 DATABASE_URL=... bash scripts/e2e.sh              # reuse a DB
```

The same script powers the screenshot pipeline via the `E2E_RUN_CMD` override
— see [Docs & screenshots](/docs/docs-and-screenshots).

## Conventions

- **Migrations** are plain SQL files in `database/migrations/`, applied in
  filename order by `migrate.py`. Never edit an applied migration — add a new
  one.
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
