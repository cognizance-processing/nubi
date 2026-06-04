# Nubi

Embedded analytics with Arrow-native data transport, a content-hashed edge cache, and
server-side RLS — built so a dashboard view costs near-zero marginal compute.

See [ROADMAP.md](ROADMAP.md) for the full product strategy, positioning, and milestone
sequence.

---

## What Nubi is

Nubi is a batteries-included BI and embedded-analytics platform. The structural bet is
that the analytics kernel runs in the user's browser by default (DuckDB-WASM / Pyodide),
so the marginal cost of a dashboard view is approximately zero. A server kernel
(LocalSubprocessRunner in dev; E2B/Modal Firecracker microVM in prod) is the escape hatch
for native wheels and large jobs. The data plane uses Arrow IPC at every boundary so data
moves between warehouse, edge, browser, and kernel with no serialization tax.

The entry wedge is embedding: a host application signs short-lived JWTs, mounts
`<nubi-dashboard>`, and gets live, cross-filtering dashboards with server-enforced
row-level security at near-zero cost per view.

---

## Key differentiators

- **Arrow-native data plane** — sqlglot planner → PhysicalPlan → executor → Arrow IPC
  stream, with a frozen cache-key spec and conformance suite so a future Rust executor
  can swap in without touching call sites.
- **Content-hashed edge cache** — N viewers of the same dashboard collapse to one
  warehouse hit. Cache key derives from `sha256(canonical_json({sql, params, rls_claims}))`,
  keyed on the same RLS claim set the predicate injector consumes.
- **Auth-as-code + server-side RLS** — JWT claims carry row/column policies; the planner
  injects them as AST-level predicates (never string-concat). The same primitive powers
  internal users, multi-tenant embedding, and Google OAuth.
- **LLM-authorable dashboards** — a dashboard is a sanitized HTML/CSS document composed
  of declarative `<nubi-kpi>`, `<nubi-table>`, and `<nubi-chart>` custom elements.
  DOMPurify strips `<script>`, `on*` handlers, and `javascript:` URLs. LLMs and MCP
  agents author layout + widget attributes; they never write WebGL or fetch code.
- **Auto-WebGL rendering** — `<nubi-chart>` switches to a regl WebGL scatter path
  automatically above 20,000 rows; SVG/HTML below. Up to ~1M points at interactive
  framerates reading Arrow columns directly.
- **On-demand compute kernel** — Python code runs in a subprocess (dev) or an E2B/Modal
  Firecracker microVM (prod). Embed tokens are unconditionally rejected from the compute
  endpoint.
- **MCP authoring surface** — a standalone MCP server (stdio) exposes six tools
  (`list_dashboards`, `run_query`, `list_lineage`, `propose_materialized_view`,
  `create_dashboard`, `author_dashboard`) so agents can author and inspect dashboards
  against real catalog objects.

---

## Architecture

```
                         ┌──────────────────────────────────────────────┐
                         │               Browser / Host page            │
                         │                                              │
                         │  <nubi-dashboard>  ←──  getToken()           │
                         │  <nubi-kpi> <nubi-table> <nubi-chart>        │
                         │  DuckDB-WASM  ←── Arrow IPC (streaming)     │
                         │  regl WebGL scatter (>20k rows auto-switch) │
                         └─────────────────┬────────────────────────────┘
                                           │ HTTPS / JWT
                         ┌─────────────────▼────────────────────────────┐
                         │            FastAPI backend                   │
                         │                                              │
                         │  /auth/*     email+pw / Google OAuth / JWKS │
                         │  /query      planner → cache → executor      │
                         │  /compute/run  kernel router                 │
                         │  /ai/*       grounding + dashboard gen       │
                         │  /lineage    SQL lineage graph               │
                         │  /jobs       cron + interval scheduler       │
                         │  REST CRUD   datastores/boards/queries/…     │
                         └────┬──────────────────────┬──────────────────┘
                              │                      │
              ┌───────────────▼──────┐  ┌────────────▼───────────────────┐
              │  Neon Postgres       │  │  Connector registry            │
              │  (asyncpg, SSL)      │  │  postgres (ADBC, native Arrow) │
              └──────────────────────┘  │  duckdb   (local, conformance)│
                                        │  http_json (post-fetch RLS)   │
                                        └────────────┬───────────────────┘
                                                     │ Arrow IPC
                              ┌──────────────────────▼────────────────┐
                              │  Content-addressed cache (LRU + TTL)  │
                              │  X-Nubi-Cache: HIT | MISS header      │
                              └───────────────────────────────────────┘

 Compute kernel path (separate surface — first-party only):
   LocalSubprocessRunner  (dev; KERNEL_LOCAL_ENABLED=true, ENV!=production)
   E2BRunner / ModalRunner (prod; Firecracker microVM, no host network/secrets)
```

---

## Tech stack

| Layer | Technologies |
|---|---|
| Backend | FastAPI 0.115, Python 3.11+, uvicorn, pydantic-settings v2 |
| DB driver | asyncpg (connection pool, raw SQL, no ORM); Neon Postgres (SSL required) |
| Auth | argon2-cffi (argon2id), PyJWT (HS256 access tokens), cryptography (RS256/ES256 JWKS) |
| Data plane | sqlglot (AST planner + RLS injection), pyarrow, DuckDB, adbc-driver-postgresql |
| Cache | In-process LRU + TTL (ContentAddressedCache); interface is Redis-swappable |
| Compute | subprocess (dev); e2b-code-interpreter / modal (prod, lazy optional deps) |
| AI / LLM | NullProvider (default, no network); lazy Anthropic / OpenAI / Gemini via env key |
| Jobs | croniter (cron expressions), native interval parser |
| Frontend | React 19, Vite, TailwindCSS, react-router-dom |
| Viz | regl (WebGL scatter, ~1M points), apache-arrow, @duckdb/duckdb-wasm |
| Embed | Custom elements (`<nubi-dashboard>`, `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`), DOMPurify |
| SDK | `@nubi/sdk` — framework-agnostic ESM, wraps auth + query + resource CRUD + embed |
| CLI | Python typer (`nubi login / deploy / run / diff`) |
| MCP | Python `mcp` SDK, stdio transport, 6 tools |
| Self-host | Docker Compose (`docker-compose.yml` ships; Makefile: `make up/down/migrate/smoke`) |

---

## Monorepo layout

```
nubi/
├── backend/
│   ├── main.py                    # FastAPI app factory, CORS, lifespan
│   ├── requirements.txt
│   ├── Dockerfile                 # Multi-stage, Python 3.11-slim
│   └── app/
│       ├── config.py              # pydantic-settings (all env vars)
│       ├── db.py                  # asyncpg pool init/close, query helpers
│       ├── errors.py              # AppError + global exception handlers
│       ├── auth/
│       │   ├── passwords.py       # argon2id hash / verify
│       │   ├── jwt.py             # mint / verify HS256 access tokens
│       │   ├── sessions.py        # refresh token issue / rotate / revoke / reuse-detect
│       │   ├── cookies.py         # HttpOnly refresh cookie helpers
│       │   ├── deps.py            # current_user + verified_identity FastAPI deps
│       │   ├── google.py          # PKCE generation, code exchange (httpx)
│       │   ├── verify.py          # unified HS256 + JWKS (RS256/ES256) verifier
│       │   ├── issuers.py         # issuer registry (jwks_uri, aud, allowed_origins)
│       │   ├── jwks_cache.py      # JWKS fetch + TTL cache
│       │   └── scopes.py          # scope-check helpers
│       ├── connectors/
│       │   ├── plan.py            # PhysicalPlan dataclass (language-neutral boundary)
│       │   ├── cache_key.py       # sha256 canonical cache key + test vectors
│       │   ├── planner.py         # sqlglot planner: parse → optimize → RLS inject → PhysicalPlan
│       │   ├── optimize.py        # projection prune, predicate push, LIMIT push, rollup route
│       │   ├── base.py            # Connector ABC + 7-flag capability contract + validate
│       │   ├── postgres.py        # PostgresConnector (ADBC, native Arrow)
│       │   ├── duckdb_conn.py     # DuckDBConnector (local, conformance + demo)
│       │   ├── http_json.py       # HttpJsonConnector (REST/JSON, post-fetch RLS)
│       │   ├── sdk.py             # FunctionConnector + post-fetch RLS/projection/limit helpers
│       │   ├── registry.py        # ConnectorRegistry singleton (postgres/duckdb/http_json)
│       │   ├── cache.py           # ContentAddressedCache (LRU + TTL + hit/miss stats)
│       │   ├── arrow_io.py        # Arrow IPC serialization helpers
│       │   ├── query_log.py       # In-memory ring buffer query log + groupby_sig extractor
│       │   └── preagg.py          # Pre-agg suggester + RollupRegistry + rollup routing
│       ├── compute/
│       │   ├── runner.py          # KernelRunner ABC, LocalSubprocessRunner, RemoteRunner stub
│       │   ├── remote_e2b.py      # E2BRunner (Firecracker microVM, prod path)
│       │   ├── remote_modal.py    # ModalRunner (adapter)
│       │   ├── router.py          # ComputePlacementRouter (cell → tier decision)
│       │   └── metering.py        # Kernel-seconds metering hook (stub)
│       ├── ai/
│       │   ├── provider.py        # LLMProvider ABC + NullProvider + lazy Anthropic/OpenAI/Gemini
│       │   ├── grounding.py       # Deterministic keyword retrieval over lineage catalog
│       │   └── dashboard.py       # AI dashboard HTML generation (grounded, NullProvider default)
│       ├── lineage/
│       │   ├── extract.py         # sqlglot AST lineage extractor (tables/columns/outputs)
│       │   └── graph.py           # LineageGraph builder over registered queries
│       ├── jobs/
│       │   ├── schedule.py        # next_run (cron + interval) + run_due_jobs (clock-injected)
│       │   ├── executor.py        # execute_job (query → DuckDB or python → kernel)
│       │   └── store.py           # InMemoryJobStore (tests); asyncpg store in routes
│       ├── repos/
│       │   ├── base.py            # Repo protocol
│       │   ├── memory.py          # InMemoryRepo (tests, no DB)
│       │   ├── pg.py              # AsyncpgRepo (prod)
│       │   └── provider.py        # get_repo() dependency
│       ├── queries/
│       │   └── registry.py        # QueryRegistry singleton (registered named queries)
│       └── routes/
│           ├── auth.py            # /auth/* endpoints
│           ├── query.py           # POST /query → Arrow IPC stream
│           ├── compute.py         # POST /compute/run → kernel execution
│           ├── embed.py           # GET /embed/config/{id} stub
│           ├── ai.py              # POST /ai/ask, POST /ai/dashboard
│           ├── lineage.py         # GET /lineage, GET /lineage/query/{id}
│           ├── insights.py        # GET /_cache/stats
│           ├── preagg.py          # GET /_preagg/suggestions
│           ├── jobs.py            # CRUD + run-now + runs for scheduled jobs
│           └── resources.py       # Generic CRUD: datastores / boards / widgets / queries
├── database/
│   ├── migrate.py                 # Forward-only SQL migration runner (asyncpg)
│   └── migrations/
│       ├── 0001_extensions.sql    # citext, pgcrypto
│       ├── 0002_users.sql
│       ├── 0003_oauth_accounts.sql
│       ├── 0004_sessions.sql      # refresh token families + reuse detection
│       ├── 0005_orgs.sql          # orgs + org_members (multi-tenancy spine)
│       └── 0006_domain_stubs.sql  # datastores, boards, queries, widgets, chats (minimal)
├── src/                           # React 19 frontend (Vite + TailwindCSS)
│   ├── pages/                     # Landing, Login, Register, Dashboard, Playground,
│   │                              #   DashboardViewPage (/d/:id), NotFound
│   ├── components/                # Chart.jsx (regl+Arrow), QueryCell, PythonCell,
│   │                              #   ProtectedRoute
│   ├── viz/
│   │   ├── scatterRenderer.js     # regl WebGL scatter (~1M points, clip-space input)
│   │   └── canvasFallback.js      # Canvas2D fallback when WebGL unavailable
│   ├── dashboards/
│   │   ├── DashboardView.jsx      # React wrapper for renderDashboardDoc
│   │   ├── renderDashboardDoc.js  # Sanitize → innerHTML → propagate backend/token
│   │   ├── sanitize.js            # DOMPurify config (script/on*/iframe/style blocked;
│   │   │                          #   nubi-* custom elements allowed)
│   │   └── sanitize.test.mjs      # Node --test unit tests for sanitizer
│   ├── contexts/AuthContext.jsx   # Access token in memory; refresh cookie path
│   └── lib/api.js                 # Fetch wrapper with 401-interceptor + refresh
├── embed/
│   ├── nubi-dashboard.js          # <nubi-dashboard> web component (shadow DOM,
│   │                              #   CSS-var theming, sample fallback, events)
│   ├── getToken.reference.js      # Reference getToken() implementation
│   ├── demo.html                  # Standalone host-page demo
│   └── widgets/
│       ├── nubi-kpi.js            # <nubi-kpi> metric card custom element
│       ├── nubi-table.js          # <nubi-table> HTML table custom element
│       ├── nubi-chart.js          # <nubi-chart> auto-WebGL (>20k rows) / SVG chart
│       ├── glScatter.js           # WebGL scatter backing for nubi-chart
│       ├── shared.js              # Shared Arrow fetch helpers
│       └── index.js               # registerNubiWidgets() entry point
├── sdk/
│   └── src/index.js               # createNubiClient({baseUrl, getToken}) — .auth,
│                                  #   .query, .resources.{…}, .embed.mount
├── cli/
│   └── nubi_cli/
│       └── main.py                # typer CLI: login / deploy / run / diff / pull
├── mcp/
│   └── nubi_mcp/
│       └── server.py              # MCP stdio server — 6 tools (list_dashboards,
│                                  #   run_query, list_lineage, propose_materialized_view,
│                                  #   create_dashboard, author_dashboard)
├── docs/
│   ├── cache-key-spec.md          # Frozen cache-key spec + test vectors
│   ├── conformance.md             # Conformance suite documentation
│   └── kernel-security.md        # Kernel security model (local vs remote)
├── docker-compose.yml             # db (postgres:16) + backend + frontend (nginx)
├── Makefile                       # up / down / migrate / logs / smoke
├── scripts/smoke.sh               # End-to-end smoke test against the running stack
├── .env.example                   # All required env vars with comments
├── TASKS.md                       # M0–M12 + M4-REMOTE task backlog and API contracts
└── ROADMAP.md                     # Full product strategy and milestone sequence
```

---

## Getting started

### 1. Provision Neon and get your DATABASE_URL

Create a project at [neon.tech](https://neon.tech), copy the connection string from the
dashboard, and confirm it includes `?sslmode=require`.

### 2. Backend

```bash
# Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r backend/requirements.txt

# Copy and edit the env file — at minimum set DATABASE_URL, JWT_SECRET,
# GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, FRONTEND_URL
cp .env.example backend/.env

# Run database migrations
python database/migrate.py

# Check migration status
python database/migrate.py --status

# Start the development server (run from the repo root; the .env is loaded from cwd)
cd backend && uvicorn main:app --reload
# API:  http://localhost:8000
# Docs: http://localhost:8000/docs  (disabled when ENV=production)
```

### 3. Frontend

```bash
# Install Node dependencies (from the repo root)
npm install

# Copy env and fill in VITE_BACKEND_URL
cp .env.example .env

# Start Vite dev server
npm run dev
# Frontend: http://localhost:5173
```

### 4. Self-hosted stack (Docker Compose)

A `docker-compose.yml` is present and ships with the repo. It runs three services:
`db` (postgres:16), `backend` (builds `backend/Dockerfile`), and `frontend` (builds
`frontend/Dockerfile`, served via nginx on port 8080). The backend entrypoint runs
migrations before starting uvicorn.

```bash
make up          # docker compose up -d --build
make migrate     # run migrations inside the running backend container
make smoke       # scripts/smoke.sh — end-to-end health/auth/query assertions
make down        # docker compose down -v
```

> Note: `docker-compose.yml` is present and runnable locally. It has not yet been
> exercised against live external infra (Neon, E2B, real Google OAuth) in CI — that
> is the M10 milestone goal.

---

## Feature overview

### Auth

Two authentication paths, both issuing access tokens in the JSON response body (held
in memory by the SPA, never `localStorage`) and rotating refresh tokens as
`HttpOnly; Secure; SameSite=Lax` cookies scoped to `/auth`.

**Email / password**
- Passwords hashed with argon2id (`argon2-cffi`). Timing-safe dummy hash on every
  login attempt to prevent user-existence enumeration.
- Access token: JWT HS256, 15-minute TTL (`JWT_SECRET` env var, min 32 bytes).
- Refresh token: opaque 256-bit random, stored SHA-256-hashed in the `sessions` table,
  rotated on every use. Presenting a consumed token triggers reuse detection — the
  entire session family is revoked immediately.

**Google OAuth (Authorization Code + PKCE)**
- Backend generates PKCE pair and state, stores them in short-lived HttpOnly cookies,
  redirects to Google. Callback verifies state (constant-time compare), exchanges the
  code with the PKCE verifier, and finds or creates the user by verified email only
  (unverified Google emails are rejected).

**Embed token verification (M3)**
- `verify_token` accepts either a first-party HS256 access token or a host-signed
  RS256/ES256 JWT verified via JWKS. `alg: none` is always rejected. JWKS fetched
  from the registered issuer's `jwks_uri`, cached with TTL, `alg` pinned to key type.
- Embed claims carry `scope[]`, `policies{}` (RLS), `embed_origin`, `aud`, `iss`.
  Origin pinning: if `embed_origin` is present, the request `Origin` header must match.
- Query endpoint derives RLS policies exclusively from the verified token — body claims
  are silently ignored.

**Auth endpoints** (`/api/v1` prefix):

| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/register` | `{email, password, name}` → `201 {user, access_token}` + refresh cookie |
| `POST` | `/auth/login` | `{email, password}` → `200 {user, access_token}` + refresh cookie |
| `POST` | `/auth/refresh` | Rotate refresh cookie → `200 {access_token}` + new cookie |
| `POST` | `/auth/logout` | Revoke session family, clear cookie → `204` |
| `GET` | `/auth/me` | Bearer access token required → `200 {user}` |
| `GET` | `/auth/google/start` | Begin Google OAuth → `302` to Google |
| `GET` | `/auth/google/callback` | Handle callback → `302` to `FRONTEND_URL` |
| `GET` | `/health` | Liveness + DB reachability → `{status, db}` |

---

### Data plane

**Query pipeline** (`POST /api/v1/query`)

1. Validate identity (HS256 or JWKS embed token).
2. Derive RLS policies from the verified token (body claims ignored).
3. Scope gate: `read:query`, `read:*`, or `read:dashboard:*`.
4. Allowlist gate: embed tokens must supply a `query_id`; raw SQL is rejected for embed
   callers.
5. sqlglot planner: parse SELECT → prune projection → push predicates → inject RLS as
   AST-level `col = value` equalities → push LIMIT → rewrite SQL → compute cache key.
6. Cache lookup by `sha256(canonical_json({sql, params, rls_claims}))`.
   - HIT → stream Arrow IPC bytes with `X-Nubi-Cache: HIT`.
   - MISS → execute plan on the selected connector → cache result → stream with
     `X-Nubi-Cache: MISS`.
7. If `datastore_id` is given: resolve connector via `ConnectorRegistry`; capability-gate
   RLS (if `predicate_rls=False` and policies are active → `501`). No `datastore_id`
   → DuckDB demo path.

**Cache** — in-process LRU (256 entries, 5-minute TTL). Stats via
`GET /api/v1/_cache/stats`.

**Pre-aggregation seed** — query-log ring buffer feeds a GROUP BY pattern extractor;
`GET /api/v1/_preagg/suggestions` returns rollup candidates by hit count and estimated
bytes saved. `route_to_rollup` rewrites a plan to a registered rollup table via AST.

---

### Connectors

Three connectors registered by default:

| Type | Class | Arrow path | RLS enforcement |
|---|---|---|---|
| `postgres` | `PostgresConnector` | Native (ADBC) | Planner predicate injection (AST) |
| `duckdb` | `DuckDBConnector` | Native (DuckDB Python) | Planner predicate injection |
| `http_json` | `HttpJsonConnector` | Post-conversion | Post-fetch `apply_rls_postfetch` (fail-closed) |

**Capability contract** — each connector declares 7 boolean flags:
`native_arrow`, `predicate_pushdown`, `projection_pushdown`, `partition_pushdown`,
`predicate_rls`, `column_masking`, `streaming_cdc`. The planner degrades gracefully on
missing capabilities. A connector with `predicate_rls=False` is refused by the route
when active RLS policies are present (M12 capability gate).

**`FunctionConnector`** — wraps any `fn(plan) -> pyarrow.Table` as a first-class
connector with declared capabilities. Post-fetch RLS (`apply_rls_postfetch`) is applied
automatically when `predicate_pushdown=False` and `predicate_rls=True`.
`apply_rls_postfetch` is fail-closed: if a policy column is absent from the returned
table it raises `403` rather than returning unfiltered data.

**NoSQL** — the capability contract actively refuses sources that cannot enforce RLS
server-side. No Mongo connector is registered or shipped. This is by design: NoSQL
sources need a separate RLS story before they can be supported safely.

---

### Compute kernel

`POST /api/v1/compute/run` — first-party tokens only (embed tokens → `403`).

Runner selection:
1. `KERNEL_REMOTE_PROVIDER=e2b` + `E2B_API_KEY` set → `E2BRunner` (any env, including prod).
2. `KERNEL_REMOTE_PROVIDER=modal` + Modal creds set → `ModalRunner`.
3. `ENV != production` and `KERNEL_LOCAL_ENABLED=true` → `LocalSubprocessRunner` (dev only).
4. Otherwise → `503 kernel_disabled`.

`LocalSubprocessRunner` — hardened subprocess: scrubbed env (no `DATABASE_URL`,
`JWT_SECRET`, `AWS_*`, etc.), new process group + `os.killpg` on timeout, POSIX rlimits
(CPU, AS, FSIZE, NPROC), 64 MiB output cap, 1 MiB stdout/stderr cap. **Dev-grade
isolation only** — same OS user, host network accessible. Never the prod path.

`E2BRunner` — Firecracker microVM: no host network/IMDS, no host filesystem, no host
process visibility. Prod code-execution path when `E2B_API_KEY` is configured.
`e2b-code-interpreter` is an optional dep (lazy import; not required for tests).

`ComputePlacementRouter` — table-driven tier selector:
`sql` → warehouse; small Python + Pyodide-portable → browser; native wheel / oversized /
remote configured → remote kernel; else → local kernel.

Response includes `X-Nubi-Tier` header (`local_kernel` or `remote_kernel`) and an Arrow
IPC stream of the `result` table.

---

### Embed

**`<nubi-dashboard>`** — shadow DOM custom element. Accepts `query`, `token` or
`get-token` (window function name), `backend`. CSS-var theming (`--nubi-bg`,
`--nubi-fg`, `--nubi-accent`, `--nubi-border`). Events: `nubi:ready`, `nubi:error`,
`nubi:query-run`. Graceful sample-data fallback on any error.

**Widget kit** (`embed/widgets/`) — standalone custom elements, registered via
`registerNubiWidgets()`:
- `<nubi-kpi query-id value-col label format?>` — metric card.
- `<nubi-table query-id limit? columns?>` — HTML table.
- `<nubi-chart query-id type=line|bar|scatter x y color? backend token>` — auto-upgrades
  to regl WebGL above 20,000 rows; SVG/HTML below. Shadow DOM, CSS-var theming.

**Dashboard renderer** (`src/dashboards/`) — `renderDashboardDoc(container, html, opts)`
sanitizes with DOMPurify (forbids `<script>`, `on*` handlers, `javascript:` URLs,
`<style>`, `<iframe>`, `<form>`, `<object>`, `<embed>`, `<link>`, `<base>`; allows
`nubi-kpi|nubi-table|nubi-chart` + safe layout tags) then sets `innerHTML` so custom
elements upgrade. Route `/d/:id` loads a board resource and renders its stored HTML.

**AI dashboard generation** (`POST /api/v1/ai/dashboard {question}`) — grounds the
question via the deterministic lineage catalog, generates an HTML document referencing
real registered `query_id` values and real column names. `NullProvider` returns a
templated HTML dashboard with no network call. Real providers (Anthropic / OpenAI /
Gemini) are activated by setting `LLM_PROVIDER` and the corresponding API key env var.

**Embed token contract** — host publishes a JWKS endpoint, registers the issuer with the
Nubi backend, implements `getToken() => Promise<string>`, and mounts `<nubi-dashboard>`.
Short-lived JWT (≤15 min), RS256 or ES256, with `scope`, `policies`, `embed_origin`,
`aud`, `iss`. Reference implementation: `embed/getToken.reference.js`.

---

### Lineage

`GET /api/v1/lineage` — full lineage graph over all registered queries.
`GET /api/v1/lineage/query/{id}` — lineage for a single query.

`extract_lineage(sql)` — sqlglot AST walking: tables from FROM/JOIN (aliases resolved),
column references (aliased + unqualified attribution), output aliases. Returns a dict
`{tables, columns, outputs}` and never raises on bad SQL (graceful `{error}` key).

The AI grounding step (`grounding.ground(question, catalog)`) is deterministic keyword
/ overlap ranking over the lineage catalog and registered queries — no LLM needed for
retrieval.

---

### Scheduled jobs

`POST /api/v1/jobs` — create a job (`kind: "query"|"python"`, `schedule: cron-string |
"interval:Ns"`). `POST /api/v1/jobs/{id}/run` — run immediately.
`GET /api/v1/jobs/{id}/runs` — run history.

Scheduler core is deterministic: `run_due_jobs(store, now, executor)` takes `now` as an
explicit parameter (no hidden `datetime.now()`) so tests can inject arbitrary
timestamps. Cron parsed via `croniter` (lazy import).

Migration `0007_jobs.sql` is defined in `TASKS.md` (M11 contract) but is **not yet
present** in `database/migrations/` — the jobs routes run against the domain stubs
schema (M0) and an in-memory store in tests.

---

### REST API + SDK + CLI + MCP

**REST CRUD** — org-scoped endpoints for `datastores`, `boards`, `widgets`, `queries`
(list / create / get / update / delete). Cross-org access returns `404` (no leak).
Repository layer (`repos/`) has an asyncpg (prod) and in-memory (test) implementation.

**`@nubi/sdk`** — ESM package (`sdk/`), `createNubiClient({baseUrl, getToken})` exposing
`.auth`, `.query(sqlOrId) → Arrow Table`, `.resources.{datastores,boards,widgets,queries}`,
and `.embed.mount(el, {query, token})`. Builds to `sdk/dist`.

**`nubi` CLI** — Python typer app (`cli/nubi_cli/`): `login`, `deploy <dir>`,
`run <query_id>`, `diff <dir>`, `pull <resource> <out/>`. Includes `--dry-run`.

**MCP server** — standalone stdio MCP server (`mcp/nubi_mcp/`), 6 tools:
`list_dashboards`, `run_query`, `list_lineage`, `propose_materialized_view`,
`create_dashboard`, `author_dashboard`. Imports backend modules directly (no live
backend required for the tool logic).

---

## Running tests

```bash
# Backend (pytest; no Neon required — in-memory repo + DuckDB fixtures)
cd backend && pytest

# MCP server tests
cd mcp && pytest tests/

# Dashboard sanitizer (Node built-in test runner)
npm run test:dash
# equivalent: node --test src/dashboards/*.test.mjs

# JS SDK tests
cd sdk && node --test src/index.test.mjs

# CLI tests
cd cli && pytest tests/
```

Backend conformance suite (`backend/tests/conformance/`) asserts that the planner
produces golden Arrow output and byte-identical cache keys. A future Rust executor must
pass the same suite.

---

## Embedding quickstart

### 1. Register your issuer with the Nubi backend

In the issuer registry (`app/auth/issuers.py` or a future DB-backed version), register:

```python
{
  "iss": "https://your-app.example.com",
  "jwks_uri": "https://your-app.example.com/.well-known/jwks.json",
  "aud": "nubi:your-project-id",
  "allowed_origins": ["https://your-app.example.com"],
}
```

### 2. Publish your JWKS and implement `getToken`

Your backend mints short-lived JWTs (≤15 min, RS256 or ES256) with the required claims:

```js
// embed/getToken.reference.js — copy and adapt
async function getToken() {
  const res = await fetch('/your-api/nubi-token')
  const { token } = await res.json()
  return token  // signed JWT from your backend
}
window.getToken = getToken
```

JWT required claims: `iss`, `sub`, `aud`, `org`, `project`, `roles[]`, `scope[]`
(must include `"read:*"` or narrower), `policies` (RLS column-value pairs),
`embed_origin` (exact `Origin` the embed is served from), `exp` (≤ now + 900), `iat`.

### 3. Drop in the embed bundle

```html
<!-- Load the nubi-dashboard web component bundle -->
<script type="module" src="https://cdn.example.com/nubi-dashboard.js"></script>

<!-- Mount the component — it calls getToken() before each query -->
<nubi-dashboard
  get-token="getToken"
  query="demo_sales_by_region"
  backend="https://api.example.com"
></nubi-dashboard>
```

The component runs in Shadow DOM. CSS custom properties control theming:
`--nubi-bg`, `--nubi-fg`, `--nubi-accent`, `--nubi-border`.

---

## Security highlights

- **RLS is server-side, always** — the browser receives only the rows it is authorised
  to see. Predicates are AST-injected by the planner (never string-concat) and derived
  exclusively from the verified JWT, not from the request body.
- **Fail-closed post-fetch RLS** — non-SQL sources (`http_json`, `FunctionConnector`)
  apply `apply_rls_postfetch` using `pyarrow.compute`; a missing policy column raises
  `403` rather than returning unfiltered data.
- **Capability gate** — the query route refuses execution if a connector declares
  `predicate_rls=False` and the plan carries active RLS policies (`501`).
- **Sanitized dashboards** — DOMPurify with a strict allowlist. `<script>`, `on*`
  handlers, `javascript:` URLs, `<style>`, `<iframe>`, `<form>`, `<object>`, `<embed>`,
  `<link>`, and `<base>` are always stripped. `nubi-*` custom elements are explicitly
  allowed.
- **Hardened kernel (dev)** — `LocalSubprocessRunner`: scrubbed env (no secrets),
  new process group, `os.killpg` on timeout, POSIX rlimits, 64 MiB output cap.
  **Not a production sandbox** — same OS user and host network. Prod must use E2B or Modal.
- **Embed tokens rejected from compute** — `POST /compute/run` returns `403` for any
  `kind='embed'` identity.
- **JWT hardening** — `alg: none` always rejected; HS256 and RS256/ES256 are strictly
  separated; `exp`, `aud`, `iss` validated; JWKS `alg` pinned to the key type.
- **Timing-safe logins** — dummy argon2id hash always evaluated to prevent
  user-existence and account-type enumeration on the login path.

---

## Self-hosted deployment

`docker-compose.yml` ships with the repo and is the planned one-command self-host path.
Run `make up` to build and start `db` + `backend` + `frontend`. The backend container
runs migrations on startup before launching uvicorn.

For production deployments:
- Set `ENV=production`, `COOKIE_SECURE=true`, strong `JWT_SECRET`.
- Configure `KERNEL_REMOTE_PROVIDER=e2b` + `E2B_API_KEY` (or Modal equivalents) to
  replace the local subprocess kernel.
- Configure real Google OAuth credentials and register embed issuers.
- The Docker Compose stack has not yet been smoke-tested against live external
  infrastructure (Neon SSL, E2B, real Google) — that is the M10 milestone.

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full milestone sequence (M0 through M12 +
M4-REMOTE), product strategy, positioning against Hex/Cube, and the Rust→WASM executor
carve-out design.

Milestones shipped: M0 (auth), M1 (connectors + conformance), M2 (streaming + cache +
pushdown + pre-agg), M3 (embed + JWKS), M4 (local kernel + router), M4-REMOTE (E2B/Modal
runners), M5 (WebGL viz), M6 (REST CRUD + SDK + CLI), M7 (lineage + AI + MCP), M8
(LLM-authorable dashboards + widget kit), M9 (connector SDK + http_json), M11
(scheduled jobs — routes + scheduler core; migration 0007 not yet applied), M12
(capability-gated connector resolution).

M10 (Docker Compose smoke test against live infra) is in progress.

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).
