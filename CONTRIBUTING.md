# Contributing to Nubi

Thank you for contributing! This file is the quick-start. The full contributor
guide is in [docs/development.md](docs/development.md) — read it before opening
a pull request.

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/nu-bi/nubi.git
cd nubi

# Python (backend + CLI + MCP)
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Node (frontend + embed + SDK)
npm install
```

### 2. Start a local Postgres and migrate

```bash
docker run -d --name nubi-pg \
  -e POSTGRES_USER=nubi -e POSTGRES_PASSWORD=nubi -e POSTGRES_DB=nubi \
  -p 5432:5432 postgres:16-alpine

export DATABASE_URL='postgresql://nubi:nubi@localhost:5432/nubi?sslmode=disable'
python database/migrate.py
```

### 3. Seed demo data and run the dev servers

```bash
# Seed a superuser + demo workspace (optional but recommended)
cd backend && python seed.py --demo && cd ..

# Run API (:8000) + Vite SPA (:5173) together
npm run dev:full
```

Dev login: `admin@nubi.dev` / `nubi-admin-2026`.

> **Reset to a clean state**: `npm run db:reset:demo` re-seeds from scratch.

---

## Test suites

| Suite | Command | Requires |
|---|---|---|
| Backend | `cd backend && python -m pytest tests/` | venv only — DB faked in-memory |
| MCP server | `cd mcp && pytest tests/` | venv only |
| CLI | `cd cli && pytest tests/` | venv only |
| Dashboard sanitizer | `npm run test:dash` | node only |
| Embed unit tests | `npm run test:embed` | node only (vitest) |
| Embed E2E | `npm run test:e2e:embed` | node, Playwright; build embed first: `npm run build:embed` |
| API E2E | `npm run test:e2e:api` | python, live Postgres + DuckDB; requires `RUN_E2E=1` |
| Full E2E | `bash scripts/e2e.sh` | docker, node, python |

The API E2E suite (`backend/tests/e2e/`) drives a real uvicorn server against
real Postgres and local parquet files. Gated by `RUN_E2E=1`. Seed the demo
workspace first: `npm run db:reset:demo`.

The backend conformance suite (`backend/tests/conformance/`) asserts that the
planner produces golden Arrow output and byte-identical cache keys. It must stay
green; any connector or planner change needs a corresponding test vector.

---

## Migration convention (IN-PLACE, not incremental)

Nubi uses an **in-place migration convention**:

- Every `.sql` file in `database/migrations/` uses `CREATE TABLE IF NOT EXISTS`
  and `CREATE INDEX IF NOT EXISTS` — the full schema for every table is declared
  once in one file.
- When a table needs a new column, **add it to the `CREATE TABLE` statement in
  the existing file** and reset the DB (`npm run db:reset:demo`).
- **Do NOT add `ALTER TABLE` or `DROP TABLE` statements.** There are no
  incremental delta migrations. The schema is always reconstructed from scratch.
- EE migrations live in `database/migrations/ee/` and follow the same pattern.
  Apply them with `python database/migrate.py --ee`.

---

## Capability matrix & changelog (definition-of-done)

Embedding hosts and integrators track [`CAPABILITIES.md`](./CAPABILITIES.md) and
[`CHANGELOG.md`](./CHANGELOG.md) to know what's shipped — so keeping them current
is part of done, not an afterthought:

- **Any PR that adds or changes a host-visible capability** (a public route, an
  embed component, an auth/RLS behaviour, a tool contract) must:
  1. Update the matching row in `CAPABILITIES.md` — status (✅/🟡/🗓️/⛔),
     contract (route/component), and docs link.
  2. Add an entry to `CHANGELOG.md` under `[Unreleased]` in the right group
     (Added / Changed / Fixed / Security / Deprecated / Removed).
- A capability is only **✅ Shipped** when its public surface exists, is
  documented, and is covered by tests. Use **🟡 Partial** with the caveat spelled
  out otherwise.
- On release, stamp `[Unreleased]` with a version + date and bump the
  "Last reviewed" line in `CAPABILITIES.md`.

---

## Issuer registration

Embed JWT issuers are **DB-backed and org-scoped**. Manage them via the
`/api/v1/security/jwt-issuers` CRUD endpoints (see
`backend/app/routes/jwt_issuers.py`). Do **not** edit
`backend/app/auth/issuers.py` to add issuers — that file is the in-process
registry implementation, not a configuration list. Changes written to the DB
sync to the in-process registry immediately — no restart required.

---

## Open-core boundary

Everything under `backend/app/ee/` and `src/ee/` is Nubi's **paid-tier code**
(billing, Paystack, licensing). It ships **open** under the repo's Apache-2.0
license — it is not a sold "edition"; it just activates in Nubi Cloud. Core code
must **never** import from `app.ee` or `src/ee/`. Use the feature-gate instead:

```python
from app.features import feature_enabled
if feature_enabled("billing"):
    ...  # EE only
```

See [docs/open-core.md](docs/open-core.md) and
[docs/architecture-open-core.md](docs/architecture-open-core.md) for the full
CE/EE split.

---

## How to add a connector

1. Create `backend/app/connectors/<name>_conn.py` with a class that implements
   the connector protocol: a `connect(config)` method returning a callable
   `fn(plan) -> pyarrow.Table`.
2. Declare the connector's **capability flags** (e.g. `predicate_rls`,
   `streaming`, `dialect`). The capability gate enforces the security floor:
   a connector with `predicate_rls=False` is refused (501) when RLS policies
   are active.
3. Register it in `backend/app/connectors/__init__.py` (the connector registry).
4. Use a **lazy import** for heavy drivers (BigQuery, Snowflake, …) — import
   inside the connector factory, not at module top, so the core app starts
   without optional dependencies installed.
5. Add at least one test vector in `backend/tests/` and a golden output in
   `backend/tests/conformance/` if the connector changes planner behaviour.
6. Document the new connector type in `docs/connectors.md`.

---

## How to add a metric

Metrics are declared as `MetricDefinition` objects and registered via
`POST /api/v1/metrics` or the MCP `create_metric` tool. See
[docs/metrics-reference.md](docs/metrics-reference.md) and
[docs/semantic-and-data-apps.md](docs/semantic-and-data-apps.md) for the full
schema and worked examples.

To add a new **compiler feature** (e.g. a new time-intelligence function):

1. Extend `MetricDefinition` / `MetricQuery` in
   `backend/app/metrics/models.py`.
2. Add the compilation logic in `backend/app/metrics/compile.py`.
3. Add tests in `backend/tests/` (including governance-rejection cases).
4. Update `docs/metrics-reference.md` and `docs/semantic-and-data-apps.md`.

---

## How to add an embed web component

Web components live in `embed/widgets/`. To add a new one:

1. Create `embed/widgets/nubi-<name>.js` extending the `NubiBaseElement`
   foundation (`embed/widgets/shared.js`).
2. Register it in `embed/nubi-embed-entry.js` (the bundle entry point).
3. Add unit tests in `embed/__tests__/nubi-<name>.test.js` (vitest).
4. Add E2E tests in `embed/e2e/specs/` (Playwright).
5. Document the component's attribute/event contract in
   [docs/embed-api.md](docs/embed-api.md).

---

## Docs are part of the product

Docs render in-app at `/docs`. If your change alters UI or behaviour described
in `docs/*.md`, update the doc in the same PR. Regenerate screenshots if the UI
changed visibly — see [docs/docs-and-screenshots.md](docs/docs-and-screenshots.md).

---

## Pull request checklist

- [ ] Test suite green (`cd backend && pytest`, `npm run test:embed`, `npm run test:dash`)
- [ ] Conformance suite green if planner or connector changed
- [ ] Docs updated (and screenshots regenerated if UI changed)
- [ ] No `app.ee` / `src/ee/` imports from core code
- [ ] No `ALTER TABLE` / `DROP TABLE` in migrations (use in-place convention)
- [ ] PR description explains the problem and solution
