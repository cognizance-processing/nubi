<p align="center">
  <img src="docs/assets/logo.png" width="120" alt="Nubi logo">
</p>

<h1 align="center">Nubi</h1>

<p align="center">
  BI that runs in the browser — near-zero cost per dashboard view.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="License Apache-2.0">
  <img src="https://img.shields.io/badge/tests-passing-brightgreen?style=flat-square" alt="Tests passing">
  <img src="https://img.shields.io/badge/PRs-welcome-blueviolet?style=flat-square" alt="PRs welcome">
  <img src="https://img.shields.io/badge/backend-Python%203.11%20%7C%20FastAPI-informational?style=flat-square" alt="Python FastAPI">
  <img src="https://img.shields.io/badge/frontend-React%2019%20%7C%20Vite-61DAFB?style=flat-square&logo=react&logoColor=white" alt="React 19 Vite">
  <img src="https://img.shields.io/github/stars/nu-bi/nubi?style=flat-square&label=stars" alt="Stars">
</p>

<p align="center">
  <a href="docs/index.md">Docs</a> ·
  <a href="ROADMAP.md#2-positioning">Compare vs Hex/Cube</a> ·
  <a href="#-quickstart">Quickstart</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

---

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="public/docs/screenshots/queries-editor-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="public/docs/screenshots/queries-editor.png">
    <img src="public/docs/screenshots/queries-editor.png" alt="Nubi query editor — SQL workspace with live results, query library, and one-click expose-as-metric" width="100%">
  </picture>
</p>

---

## What is Nubi?

Nubi is a batteries-included BI and embedded-analytics platform. The structural bet is that **the analytics kernel runs in the user's browser by default** (DuckDB-WASM / Pyodide), so the marginal cost of a dashboard view is approximately zero — a server kernel (E2B / Modal Firecracker microVM) is only the escape hatch for native wheels and large jobs.

The data plane uses **Arrow IPC at every boundary**, so data moves between warehouse, edge, browser, and kernel with no serialization tax. The entry wedge is **embedding**: a host app signs short-lived JWTs, mounts `<nubi-dashboard>`, and gets live cross-filtering dashboards with server-enforced row-level security at near-zero cost per view.

---

## ✨ Why Nubi?

| | Hex | Cube | **Nubi** |
|---|---|---|---|
| Kernel | Python per session, their cloud ($$$) | n/a | **Pyodide in browser; on-demand server kernel only when needed** |
| Result transport | JSON via pandas | JSON / SQL API | **Arrow IPC — zero serialization tax** |
| Viz | Plotly/SVG, chokes past ~50k rows | bring-your-own | **WebGL/WebGPU on Arrow buffers, 1M+ points interactive** |
| Caching | Per-session | Pre-aggregations in Cube Store | **Content-hashed edge cache + auto pre-aggregations** |
| Modeling tax | medium | high (cubes first) | **low — point at a warehouse and go** |
| Embedding | separate product | headless only | **core surface; editor embeddable, not just output** |
| Free tier | per-seat kernel billing | infra/seat | **real free tier — compute is the user's browser** |

**Key differentiators:**

- **Arrow-native data plane** — sqlglot planner → PhysicalPlan → executor → Arrow IPC stream, with a frozen cache-key spec and conformance suite so a future Rust executor can swap in without touching call sites.
- **Content-hashed edge cache** — N viewers of the same dashboard collapse to one warehouse hit. Cache key: `sha256(canonical_json({sql, params, rls_claims}))`.
- **Auth-as-code + server-side RLS** — JWT claims carry row/column policies; the planner injects them as AST-level predicates (never string-concat). Powers internal users, multi-tenant embedding, and Google OAuth from the same primitive.
- **Governed semantic metrics** — a `MetricDefinition` encodes business logic once (measure, dimensions, time intelligence, RLS keys, derived formulas) and compiles to SQL on demand via `POST /metrics/{id}/query`. Agents and dashboards query a governed vocabulary, not raw SQL. Ships: derived/ratio measures (NULLIF-guarded formulas), full time-intelligence suite (prior-period, YoY, YTD/QTD/MTD, rolling windows, latest-snapshot), dynamic top-N with "Other" bucket, `percentile_cont`, `approx_count_distinct`.
- **Smart engine + pre-agg rollups** — `build_rollup_for_metric` materializes a rollup shaped to match the metric compiler's `__base` CTE so the router can serve derived and windowed metric queries from the rollup instead of the raw fact table. Per-board query fusion and a shared `(model, predicate, rls_hash)` cache key collapse repeated widget loads to a single scan.
- **Flows as a data-app engine** — per-cell `cpu_cores / mem_mb / timeout_s` resource requests; stochastic cells with run-level seeds for reproducibility; typed artifact channel (pickle/joblib/model blobs by handle); scenario sweep + backfill; event/webhook/downstream triggers + run-history + SLA monitoring. An SSRF-guarded `http_call` task kind lets a flow call out to a host endpoint; an `assert` task kind runs data-quality expectations (`row_count`/`not_null`/`unique`/`custom_sql`) and fails the run on violation — the flows equivalent of a SQLMesh audit. Powers the close-the-loop cycle: Flow computes → table → metric → dashboard → re-trigger. See [docs/flows.md](docs/flows.md).
- **LLM-authorable dashboards + MCP** — a dashboard is a sanitized HTML/CSS document of declarative `<nubi-kpi>`, `<nubi-table>`, and `<nubi-chart>` custom elements. LLMs and MCP agents author layout and widget attributes; they never write WebGL or fetch code. Fourteen MCP tools expose the full authoring surface to any agent. See [docs/mcp.md](docs/mcp.md) for the full host integration contract (per-org server registry, agent dispatch, Nubi-as-MCP-server JSON-RPC).
- **Data health scoring** — freshness records are pre-computed and read in O(1) (< 5 ms p99 SLO). `GET /health/score` computes weighted (freshness 50%, completeness 30%, availability 20%) health scores with per-dataset grade letters. `GET /health/estate` returns a source→raw→model→feature flow map annotated with health. The `<nubi-health>` embed component renders this in any host app. See [docs/data-health.md](docs/data-health.md) and [docs/embed-api.md](docs/embed-api.md).
- **RLS governance extensions** — policy claims support scalar (equality), list (IN), and range-dict (`gte/gt/lte/lt`) shapes. Hierarchical scope expansion resolves a parent value (e.g. `region = "Western Cape"`) to child values via the `access_hierarchy` table, **auto-applied in the live `/query` and `/metrics/{id}/query` paths** (fail-closed, capped by `NUBI_RLS_MAX_POLICY_VALUES`). `GET /auth/scope` returns a caller's resolved effective policies so an embedding host can authorize its own writes off Nubi's resolution; `/access-grants` optionally stores user→scope assignments. See [docs/governance.md](docs/governance.md).
- **Versioning, audit & drift** — metrics and flows keep an immutable spec **version history with one-call revert** (`/metrics/{id}/versions` + `/revert/{v}`, `/flows/{id}/versions` + `/revert/{v}`). A consolidated, POPIA-safe **action audit-log** records every mutation (`GET /audit`). **Schema-drift detection** surfaces added/removed/type-changed columns per dataset as a read API + `SCHEMA_DRIFT` webhook event (`GET /health/drift`). Flow-run `params_snapshot` is readable for reproducibility.
- **Bounded, honest agent loop** — the chat/agent loop has step caps, an aggregate per-turn token budget, per-turn timeout, and a dedicated chat rate-limit class (cost-DoS safe). Metric values are always deterministic SQL — the LLM is never in the value path, so numbers are never fabricated. See [docs/ai-and-mcp.md](docs/ai-and-mcp.md).
- **Per-org rate limiting + embed exemption** — query/chat/auth endpoints are token-bucketed on the caller's **verified org** (from the JWT, not IP), Redis-backed across workers; a forged `org` claim falls back to IP-keyed limiting. Verified embed tokens are exempt on metric/query read paths so a cockpit dashboard can fire its tile queries concurrently without tripping the cap. See [docs/embedding.md](docs/embedding.md#rate-limiting-and-embed-exemption).
- **Comprehensive chart library** — `<nubi-chart>` renders **17 types** (bar grouped/stacked/100%/horizontal, line, area, scatter, bubble, pie, donut, **sankey**, funnel, **waterfall**, heatmap, radar, treemap, boxplot, gauge, candlestick, and a **forecast fan chart**) with a full `config` (legend, palette, stacking, data labels, log/dual-Y axes, currency/percent/SI/date formatting, reference/target lines, annotations), theme-driven light/dark. It's a framework-agnostic web component embedding hosts drop into a panel, and the same builder powers Nubi's own dashboards. `<nubi-table>` ships client-side **CSV export**.
- **Auto-WebGL rendering** — `<nubi-chart>` switches to a regl WebGL scatter path automatically above 20,000 rows; SVG/HTML below. Up to ~1M points at interactive framerates reading Arrow columns directly.
- **Materialize + scheduled sweeps** — a Flow `materialize` cell writes a computed projection to a **named managed table** (a materialize-once, serve-many Parquet cache in front of your connectors), incrementally, queryable by metrics; a scheduled `watch_sweep` job nightly-evaluates an org's metric watches and emits `WATCH_BREACH` webhooks. See [docs/materialization.md](docs/materialization.md).
- **Files-as-code** — metrics, dashboards, queries, flows, and watches are plain files in a project directory; `nubi pull`/`push`/`diff` round-trip them, and `nubi apply` (`POST /api/v1/apply`) registers a whole bundle idempotently in one call — CI/CD-friendly, no click-ops required. See [docs/files-as-code.md](docs/files-as-code.md).
- **SQL-first connector SDK** — any `fn(plan) -> pyarrow.Table` is a first-class connector with declared capabilities. The capability gate enforces the security floor: a connector with `predicate_rls=False` is refused (501) when policies are active. Built-in connectors: `postgres` (ADBC), `duckdb` (in-memory + file-backed), `duckdb_storage` (S3/R2/MinIO/**GCS** — native `gs://` via DuckDB's `TYPE gcs` secret, HMAC or ADC), `http_json`, `mysql`, `mariadb`, `jdbc`, `snowflake`, `bigquery`, `clickhouse`, `databricks`, `athena`, `trino`/`presto`, `sqlserver`/`azuresql`/`azuresynapse`, `oracle`, `redshift`, `cockroachdb`, `cloudsql`, `sftp`, `ftp` (most via optional lazy-imported drivers). Private databases reachable via a `network_mode='bridge'` WebSocket tunnel.
- **File ingestion, auto-DDL, and column profiling** — the `file_ingest` flow task pulls from SFTP/FTP/bucket sources (incl. zip archives), normalizes to Parquet, and loads into any target connector, auto-registering/evolving the target's schema contract as it goes (`connector_write` is the write-side sibling for arbitrary flow output). `GET /datasets/{id}/profile` returns per-column `null_rate`, approx `distinct_count`, `min`/`max`, and type in a single DuckDB pass — across local, S3, and GCS-backed datasets. See [docs/flows.md](docs/flows.md#task-kinds-under-the-hood) and [docs/connectors.md](docs/connectors.md#column-profiling).
- **Real free tier** — compute is the user's browser; Hex can't match it without absorbing kernel cost.

---

## 📸 Screenshots

The hero above is the **query editor**. A few more surfaces — see the [**UI tour**](docs/ui-tour.md) for the full guided walkthrough.

<table>
  <tr>
    <td width="50%" valign="top">
      <a href="docs/dashboards.md"><img src="public/docs/screenshots/dashboard-editor.png" alt="Dashboard editor"></a>
      <sub><b>Dashboard editor</b> — drag-and-drop widgets, 17 chart types, live cross-filtering</sub>
    </td>
    <td width="50%" valign="top">
      <a href="docs/flows.md"><img src="public/docs/screenshots/flows-notebook.png" alt="Flows"></a>
      <sub><b>Flows</b> — cell-based SQL/Python orchestration (notebook + canvas views)</sub>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <a href="docs/connectors.md"><img src="public/docs/screenshots/data-browser.png" alt="Data browser"></a>
      <sub><b>Data browser</b> — explore any connected source, edit grid-style</sub>
    </td>
    <td width="50%" valign="top">
      <a href="docs/embedding.md"><img src="public/docs/screenshots/dashboard-view.png" alt="Published dashboard"></a>
      <sub><b>Published dashboard</b> — embeddable, RLS-enforced, near-zero cost per view</sub>
    </td>
  </tr>
</table>

---

## 🚀 Quickstart

### Docker Compose (fastest — one command)

The repo ships a `docker-compose.yml` with two services: `db` (postgres:16-alpine) and a
combined `app` (root `Dockerfile` — builds the Vite SPA and runs FastAPI, serving the SPA and
the `/api/v1` API on a single origin at port 8000).

```bash
# 1. Clone and start the stack
git clone https://github.com/nu-bi/nubi.git
cd nubi
make up          # docker compose up -d --build

# 2. Open the app
#    App (SPA + API): http://localhost:8000
#    API docs:        http://localhost:8000/docs (dev only)

# 3. (Optional) seed the demo workspace (superuser + org + demo data)
cd backend && DATABASE_URL=postgresql://nubi:nubi@localhost:5432/nubi python seed.py --demo
#    → admin@nubi.dev / nubi-admin-2026

# 4. Smoke test
make smoke       # scripts/smoke.sh — health + auth + query assertions
```

> The compose stack runs against a local Postgres container. To connect to [Neon](https://neon.tech) or another managed Postgres, set `DATABASE_URL` in your environment before running `make up`.

<details>
<summary>Dev path — backend + frontend separately</summary>

**Prerequisites:** Python 3.11+, Node 20+

```bash
# ── Backend ───────────────────────────────────────────────────
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# Copy and edit env — at minimum set DATABASE_URL and JWT_SECRET
cp .env.example backend/.env

# Run migrations, then start the API
python database/migrate.py
cd backend && uvicorn main:app --reload
# API:  http://localhost:8000
# Docs: http://localhost:8000/docs

# ── Frontend (new terminal, repo root) ────────────────────────
npm install
cp .env.example .env          # set VITE_BACKEND_URL=http://localhost:8000
npm run dev
# Frontend: http://localhost:5173
```

Seed the demo workspace (optional, with the venv active):

```bash
cd backend && DATABASE_URL=postgresql://user:pass@host/db python seed.py --demo
# → admin@nubi.dev / nubi-admin-2026
```
</details>

<details>
<summary>Key environment variables (.env.example)</summary>

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | `postgresql://...?sslmode=require` (Neon) or local Postgres |
| `JWT_SECRET` | Yes | HS256 signing secret — `openssl rand -hex 32` |
| `VITE_BACKEND_URL` | Frontend | Base URL of the FastAPI backend |
| `GOOGLE_CLIENT_ID` | OAuth | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | OAuth | Callback URL registered in Google Console |
| `FRONTEND_URL` | Backend | Where the backend redirects after Google OAuth |
| `CORS_ORIGINS` | Backend | Comma-separated allowed origins |
| `ENV` | Backend | `development` / `production` (disables `/docs` in prod) |
| `KERNEL_LOCAL_ENABLED` | Backend | `true` to allow local subprocess kernel (dev only, default `true`) |
| `KERNEL_REMOTE_PROVIDER` | Backend | `e2b` or `modal` for Firecracker/Modal sandboxed kernels (prod) |
| `LLM_PROVIDER` | Optional | `litellm` / `anthropic` / `openai` / `gemini`. `litellm` (one SDK, all providers + per-model cost tracking) reads `LITELLM_MODEL`; see [AI docs](docs/ai-and-mcp.md#configuring-the-llm-provider-operators). Unset ⇒ offline mode. |
| `ALLOW_UNSAFE_PUBLIC_EXPORTS` | Optional | `true` to enable no-auth CDN static exports (Mode 3b). Requires org `public_exports` gate. Default `false`. |
| `EMBED_DEV_TOKEN_ENABLED` | Dev only | `true` to enable the dev-only HS256 embed-token mint endpoint. **Never `true` in production.** |
| `NUBI_COLLECT_ROW_CAP` | Optional | Row cap for snapshot/report data collection. Default `50000`. `0` = unlimited. |
| `JOBS_SCHEDULER_ENABLED` | Optional | `true` to activate the background job scheduler tick. Default `false`. |
| `FLOWS_TICK_SECRET` | Optional | Shared secret for `POST /flows/tick` (external cron schedulers). Leave empty to disable. |
| `FX_EMERGENCY_RATE` | EE only | Emergency fallback USD→ZAR rate when no live rate is available. Default `16.26`. |
</details>

---

## 🏗️ Architecture

```mermaid
flowchart TD
  subgraph client["Browser / host page"]
    direction TB
    DASH["&lt;nubi-dashboard&gt; + widget kit<br/>kpi · table · chart · filter"]
    WASM["DuckDB-WASM kernel<br/>regl WebGL render above ~20k rows"]
    DASH --- WASM
  end

  subgraph api["FastAPI backend"]
    direction TB
    AUTH["Auth · email+pw · Google OAuth · JWKS"]
    QUERY["/query"]
    PLAN["Planner — sqlglot AST → PhysicalPlan<br/>injects row-level-security predicates"]
    CACHE["Content-hashed cache<br/>X-Nubi-Cache: HIT | MISS"]
    REG["Connector registry"]
    SVC["/ai · /jobs · REST CRUD"]
    KR["/compute/run · kernel router"]
    QUERY --> PLAN --> CACHE --> REG
  end

  subgraph data["Data sources — bring your own"]
    direction TB
    WH["Warehouses<br/>postgres · duckdb · http_json<br/>mysql · snowflake · bigquery"]
    BRIDGE["VPC bridge · WebSocket tunnel"]
    META["Metadata DB<br/>Postgres / Neon"]
  end

  subgraph compute["Compute kernel · first-party only · embed → 403"]
    direction TB
    LOCAL["LocalSubprocessRunner · dev"]
    REMOTE["E2B / Modal · Firecracker microVM"]
  end

  DASH -->|"getToken() → JWT"| AUTH
  DASH -->|"HTTPS"| QUERY
  DASH -.-> SVC
  CACHE -->|"Arrow IPC stream"| WASM
  REG -->|"Arrow IPC"| WH
  REG --> BRIDGE --> WH
  REG --> META
  KR --> LOCAL
  KR --> REMOTE

  classDef client fill:#eff6ff,stroke:#3b82f6,color:#1e3a8a;
  classDef secure fill:#fef2f2,stroke:#ef4444,color:#991b1b;
  class client client;
  class compute secure;
```

### Tech stack

| Layer | Technologies |
|---|---|
| Backend | FastAPI 0.131, Python 3.11+, uvicorn, pydantic-settings v2 |
| DB | asyncpg (connection pool, raw SQL); Postgres 16 / Neon (SSL required) |
| Auth | argon2-cffi (argon2id), PyJWT HS256, cryptography RS256/ES256 JWKS |
| Data plane | sqlglot (AST planner + RLS injection + dialect validation), pyarrow, DuckDB (in-mem + file), adbc-driver-postgresql; mysql/mariadb/jdbc connectors (optional drivers); VPC bridge tunnel |
| Cache | In-process LRU + TTL (`ContentAddressedCache`); interface is Redis-swappable |
| Compute | subprocess (dev); e2b-code-interpreter / modal (prod, lazy optional deps) |
| AI / LLM | NullProvider (default, zero network); LiteLLM in-process (recommended — one SDK, all providers + per-call cost tracking); lazy Anthropic / OpenAI / Gemini native SDKs via env |
| Frontend | React 19, Vite 7, TailwindCSS, react-router-dom |
| Viz | regl (WebGL scatter, ~1M pts), apache-arrow, @duckdb/duckdb-wasm, ECharts |
| Embed | Custom elements (`<nubi-dashboard>`, `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`), DOMPurify |
| SDK | `@nubi/sdk` — framework-agnostic ESM, wraps auth + query + resource CRUD + embed |
| CLI | Python typer (`nubi login / init / pull / push / deploy / run / diff / apply / flows / secrets`) |
| MCP | Python `mcp` SDK, stdio transport, 14 tools |
| Self-host | Docker Compose (`docker-compose.yml`); Makefile: `make up/down/migrate/smoke` |

### Monorepo layout

```
nubi/
├── backend/          FastAPI app, connectors, planner, compute, auth, AI, jobs
│   ├── app/
│   │   ├── auth/     argon2id, JWT HS256, Google PKCE, JWKS, sessions
│   │   ├── connectors/ sqlglot planner, Arrow executor, cache, pre-agg
│   │   ├── compute/  KernelRunner ABC, LocalSubprocessRunner, E2BRunner, ModalRunner
│   │   ├── ai/       LLMProvider, grounding, dashboard generation
│   │   ├── jobs/     cron + interval scheduler, executor, store
│   │   ├── repos/    asyncpg (prod) + in-memory (test) repository layer
│   │   └── routes/   auth, query, compute, embed, ai, jobs, resources
│   └── tests/        300+ test modules + conformance suite (golden Arrow + cache keys) + security/ suite
├── database/         Forward-only SQL migration runner + 13 OSS migrations + 4 EE migrations
├── src/              React 19 frontend (Vite + Tailwind) — pages, components, viz
├── embed/            Web components: <nubi-dashboard>, <nubi-kpi>, <nubi-table>, <nubi-chart>
├── sdk/              @nubi/sdk — createNubiClient ESM package
├── cli/              nubi CLI (typer): login / init / pull / push / deploy / apply / run / diff / flows / secrets
├── mcp/              MCP stdio server — 14 tools for agent authoring
├── docs/             cache-key-spec.md, conformance.md, kernel-security.md, assets/
├── Dockerfile          combined image: Vite SPA build + FastAPI (single origin)
├── docker-compose.yml   db (postgres:16) + app (SPA + API on :8000)
├── Makefile          up / down / migrate / logs / smoke
├── scripts/smoke.sh  End-to-end health + auth + query assertions
└── .env.example      All env vars with comments
```

---

## 📊 Project status

| Milestone | Status | What shipped |
|---|---|---|
| M0 — Foundation | ✅ Done | React + FastAPI rebuild on Neon Postgres, email/pw + Google OAuth, migrations |
| M1 — Connectors + conformance | ✅ Done | sqlglot planner, PhysicalPlan, Postgres/DuckDB connectors, frozen cache-key spec |
| M2 — Streaming + cache + pushdown | ✅ Done | Arrow IPC stream, content-hashed LRU cache, projection/predicate/LIMIT pushdown, pre-agg seed |
| M3 — Embed auth + `<nubi-dashboard>` | ✅ Done | HS256 + JWKS verifier, issuer registry, server-side RLS, origin pinning, web component |
| M4 — Local kernel + placement router | ✅ Done | KernelRunner ABC, LocalSubprocessRunner, ComputePlacementRouter, `POST /compute/run` |
| M4-REMOTE — E2B/Modal sandbox | ✅ Done | E2BRunner (Firecracker microVM), ModalRunner adapter |
| M5 — WebGL viz | ✅ Done | regl GPU scatter on Arrow buffers, `<nubi-chart>` auto-WebGL above 20k rows |
| M6 — REST API + SDK + CLI | ✅ Done | asyncpg repo layer, CRUD for datastores/boards/widgets/queries, `@nubi/sdk`, typer CLI |
| M7 — AI + MCP | ✅ Done | deterministic grounding, LLMProvider, MCP server (14 tools) |
| M8 — LLM-authorable dashboards | ✅ Done | `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>` widget kit, DOMPurify renderer, `POST /ai/dashboard` |
| M9 — Connector SDK + HTTP/JSON | ✅ Done | FunctionConnector, apply_rls_postfetch, HttpJsonConnector, NoSQL deliberately out of scope |
| Connector breadth | ✅ Done | Registry ships 20+ types: `postgres`, `duckdb` (in-mem + file-backed), `duckdb_storage` (S3/R2/MinIO/GCS httpfs), `http_json`, `mysql`, `mariadb`, `jdbc`, `snowflake`, `bigquery`, `clickhouse`, `databricks`, `athena`, `trino`/`presto`, `sqlserver`/`azuresql`/`azuresynapse`, `oracle`, `redshift`, `cockroachdb`, `cloudsql`, `sftp`, `ftp` (most via optional lazy-imported drivers) |
| Ingestion + auto-DDL + profiling | ✅ Done | `file_ingest`/`connector_write` flow tasks (SFTP/FTP/bucket → any target, schema auto-registered/evolved); `GET /datasets/{id}/profile` column stats |
| VPC bridge | ✅ Done | `network_mode='bridge'` opens a WebSocket TCP tunnel via `BridgeBroker`, wired into the query path (`resolve_network_async`); other modes 501 |
| Builder layer (M13–M22) | ✅ Done | Query workspace + typed params, filter/variable/route-param interactivity, TanStack table + conditional formatting, 9 chart types, exports, scheduled reports, AI-SQL, agentic chat, git sync |
| Unified editor surfaces | ✅ Done | EditorShell: Dashboard / Report / Presentation surface switch; DocCanvas (A4/Letter paginated doc) + SlideCanvas (16:9 slides + present mode); `spec.surfaces.{grid,report,slides}` schema split live |
| Semantic layer (Bet 1) | ✅ Done | `MetricDefinition` + `MetricQuery` governed compiler; derived/ratio measures (NULLIF-guarded); full time-intelligence suite (prior_period/pop/yoy/ytd/qtd/mtd/rolling_sum/rolling_avg/latest_snapshot via QUALIFY); dynamic top-N with Other bucket; percentile_cont; approx_count_distinct |
| Smart engine (Bet 2) | ✅ Done | `build_rollup_for_metric` (metric-driven pre-agg); `__base`-aware rollup router serves derived + windowed queries; per-board `DataProvider` query fusion + shared `(model, predicate, rls_hash)` cache key |
| Flows data-app engine (Axis B) | ✅ Done | Per-cell `cpu_cores / mem_mb / timeout_s`; stochastic cells + run-level seeds; typed artifact channel (pickle/joblib/bytes/json); scenario sweep + backfill; event/webhook/downstream triggers + run-history + SLA |
| M10 — Docker self-host smoke test | 🔄 In progress | docker-compose.yml ships locally (db + combined app on :8000); live-infra CI smoke test is the remaining capstone |
| M11 — Scheduled jobs | ✅ Done | cron + interval scheduler (deterministic `now`), `execute_job`, CRUD + run-now + run-history routes |
| M12 — Capability-gated RLS | ✅ Done | connector resolution via `datastore.config.type`, 501 gate when `predicate_rls=False` + active policies |
| Host-integration suite | ✅ Done | MCP (both directions incl. host custom tools), data health + score + estate + schema-drift, transformation versioning/backfill/transpile, governance (range/list/hierarchical RLS, `/auth/scope`, access-grants, cardinality cap), unified audit-log, metric/flow spec version+revert, chat cost-DoS limits, `<nubi-health>` embed component, top-level Overview/Workqueue. Tracked in [`CAPABILITIES.md`](CAPABILITIES.md) |

**Tests:** a comprehensive backend suite — **7,700+ tests across 300+ modules** (verified via `pytest --collect-only`), including the mutation-verified `security/` suite **and a black-box live-API pentest suite** (auth/IDOR/scope/injection/RLS/SSRF/DoS/secret attacks over a seeded DB) and a conformance suite (golden Arrow output + byte-identical cache keys); plus live API E2E, MCP tests, CLI tests, **~970 dashboard + ~380 embed-component tests** (`node --test` / vitest, verified counts), and browser embed E2E (Playwright). All green on `main`.

**Experimental / not production-hardened:** `LocalSubprocessRunner` (dev-grade isolation — same OS user, host network); Docker Compose stack not yet smoke-tested against live external infra (Neon SSL, E2B, real Google OAuth).

---

## 🔌 Embedding quickstart

> For the full versioned component API (all attributes, events, 25-token theme
> contract, capability gating, and bundle build instructions) see
> [**docs/embed-api.md**](docs/embed-api.md).

```html
<!-- 1. Load the widget bundle -->
<script type="module" src="https://cdn.example.com/nubi-dashboard.js"></script>

<!-- 2. Mount the component — calls getToken() before each query -->
<nubi-dashboard
  get-token="getToken"
  query="demo_sales_by_region"
  backend="https://api.example.com"
></nubi-dashboard>
```

CSS custom properties control theming: `--nubi-bg`, `--nubi-fg`, `--nubi-accent`, `--nubi-border`.

<details>
<summary>Full embed integration steps</summary>

**1. Register your issuer** via the admin API (`POST /api/v1/security/jwt-issuers`):

```json
{
  "name": "My App",
  "issuer": "https://your-app.example.com",
  "audience": "nubi:your-project-id",
  "jwks_url": "https://your-app.example.com/.well-known/jwks.json"
}
```

Issuers are DB-backed and org-scoped. Manage them via the `/security/jwt-issuers`
CRUD endpoints (see `backend/app/routes/jwt_issuers.py`). Changes are synced to
the in-process registry immediately — no restart required.

**2. Mint short-lived JWTs** (≤15 min, RS256 or ES256) from your backend:

```js
// Reference: embed/getToken.reference.js
async function getToken() {
  const { token } = await fetch('/your-api/nubi-token').then(r => r.json())
  return token  // signed JWT from your backend
}
window.getToken = getToken
```

Required JWT claims: `iss`, `sub`, `aud`, `org`, `project`, `roles[]`, `scope[]` (must include `"read:*"` or narrower), `policies` (RLS column-value pairs), `embed_origin`, `exp` (≤ now + 900), `iat`.

**3. The component handles the rest** — JWKS verification, RLS enforcement, Arrow IPC fetch, WebGL rendering.
</details>

---

## 🧪 Running tests

```bash
# Backend — in-memory repo + DuckDB fixtures; no live DB required
cd backend && pytest

# MCP server tests
cd mcp && pytest tests/

# Dashboard sanitizer (Node built-in runner)
npm run test:dash

# JS SDK tests
cd sdk && node --test src/index.test.mjs

# CLI tests
cd cli && pytest tests/
```

The backend conformance suite (`backend/tests/conformance/`) asserts the planner produces golden Arrow output and byte-identical cache keys. A future Rust executor must pass the same suite to be swappable.

---

## 📦 SDKs & tooling

| Package | Path | Description |
|---|---|---|
| `@nubi/sdk` | [`sdk/`](sdk/README.md) | Framework-agnostic ESM — `.auth`, `.query()`, `.resources.*`, `.embed.mount()` |
| `nubi` CLI | [`cli/`](cli/README.md) | `login / init / pull / push / deploy / run / diff / apply / flows / secrets` — with `--dry-run` |
| MCP server | [`mcp/`](mcp/README.md) | stdio MCP — 14 tools for agent dashboard authoring |
| Embed bundle | [`embed/`](embed/README.md) | `<nubi-dashboard>` + widget kit custom elements |

---

## 📖 Documentation

Full documentation lives in [`docs/`](docs/index.md) — **start at the [documentation index](docs/index.md)**. For integrators: [**`CAPABILITIES.md`**](CAPABILITIES.md) is the host-trackable capability matrix (what's shipped vs. roadmap, with the route/component for each) and [**`CHANGELOG.md`**](CHANGELOG.md) records host-visible changes. Highlights:

**Using Nubi**
- [Getting started](docs/getting-started.md) · [UI tour](docs/ui-tour.md)
- [Connectors](docs/connectors.md) · [Queries & parameters](docs/queries-and-params.md) · [Pre-aggregations](docs/pre-aggregations.md)
- [Dashboards](docs/dashboards.md) · [Dashboard spec reference](docs/dashboard-spec-reference.md) · [Exports & scheduled reports](docs/exports-and-jobs.md)
- [Flows](docs/flows.md) · [Notebooks](docs/notebooks.md) · [AI, chat & MCP](docs/ai-and-mcp.md)
- [Embedding](docs/embedding.md) · [Organization & settings](docs/organization-settings.md) · [Notifications & integrations](docs/notifications-and-integrations.md)
- [Semantic layer, data apps & close-the-loop](docs/semantic-and-data-apps.md) — metrics, Flows data-app engine, the full loop
- [Metrics reference](docs/metrics-reference.md) — agent/MCP reference for querying governed metrics

**Governance, health & integration**
- [Governance](docs/governance.md) — RLS shapes, hierarchical scope, `/auth/scope`, access-grants · [Data health](docs/data-health.md) — freshness/score/estate + drift
- [Transformation](docs/transformation.md) — versioning/revert, backfill, transpile
- [MCP integration](docs/mcp.md) — host server registry, agent dispatch, Nubi-as-MCP-server · [Embed API](docs/embed-api.md) — the 8-component contract

**Platform & security**
- [Architecture & Economics](docs/architecture-and-economics.md) — compute model, embedding modes, billing COGS mapping
- [Compliance](docs/compliance.md) — posture document, gaps disclosed
- [Self-host](docs/self-host.md) · [Open core](docs/open-core.md) · [Open-core architecture](docs/architecture-open-core.md)
- [Nubi Cloud](docs/cloud.md) — managed hosting, architecture, scaling · [Billing & usage](docs/billing-and-usage.md) — plans, wallet, auto-topup, FX/ZAR, invoices · [Billing model](docs/billing-model.md) — COGS mapping, tier values
- [Kernel security](docs/kernel-security.md) · [Connector security](docs/connector-security.md) · [Secrets](docs/secrets.md) · [Observability](docs/observability.md)
- [Cache-key spec](docs/cache-key-spec.md) · [Conformance](docs/conformance.md) · [Bridges](docs/bridges.md)

**Build & contribute**
- [SDK & CLI](docs/sdk-and-cli.md) · [Files-as-code](docs/files-as-code.md) (`nubi apply`) · [Git sync](docs/git-sync.md)
- [Developing Nubi](docs/development.md) · [Docs & screenshots](docs/docs-and-screenshots.md)
- [`ROADMAP.md`](ROADMAP.md) — product strategy, positioning vs Hex/Cube, milestone sequence, Rust→WASM carve-out

---

## 🤝 Contributing

PRs are welcome. Start with the contributor guides — they cover the dev stack, seeding, every test suite, and the docs/screenshot pipeline:

- [Developing Nubi](docs/development.md) — environment setup, running, testing, conventions
- [Docs & screenshots](docs/docs-and-screenshots.md) — authoring docs and regenerating product screenshots

The fastest path:

1. Fork, create a feature branch.
2. Run the test suite (`cd backend && python -m pytest tests/`).
3. If your change touches the UI or anything described in `docs/`, update the docs and run `npm run screenshots` in the same PR.
4. Open a PR — describe the problem and solution; reference any relevant milestone or doc.

Please keep commits small and focused. The conformance suite must stay green; any new connector or planner change needs a corresponding test vector.

---

## License

[Apache License 2.0](LICENSE) — see the `LICENSE` file.
