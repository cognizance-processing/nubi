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
  <img src="https://img.shields.io/github/stars/imranparuk/nubi?style=flat-square&label=stars" alt="Stars">
</p>

<p align="center">
  <a href="docs/cache-key-spec.md">Docs</a> ·
  <a href="ROADMAP.md#2-positioning">Compare vs Hex/Cube</a> ·
  <a href="#-quickstart">Quickstart</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

---

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/hero-light.png">
    <img src="docs/assets/hero-light.png" alt="Nubi dashboard screenshot" width="100%">
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
- **LLM-authorable dashboards + MCP** — a dashboard is a sanitized HTML/CSS document of declarative `<nubi-kpi>`, `<nubi-table>`, and `<nubi-chart>` custom elements. LLMs and MCP agents author layout and widget attributes; they never write WebGL or fetch code. Six MCP tools expose the full authoring surface to any agent.
- **Auto-WebGL rendering** — `<nubi-chart>` switches to a regl WebGL scatter path automatically above 20,000 rows; SVG/HTML below. Up to ~1M points at interactive framerates reading Arrow columns directly.
- **SQL-first connector SDK** — any `fn(plan) -> pyarrow.Table` is a first-class connector with declared capabilities. The capability gate enforces the security floor: a connector with `predicate_rls=False` is refused (501) when policies are active. Built-in connectors: `postgres` (ADBC), `duckdb` (in-memory demo **and** read-only file-backed), `http_json`, `mysql`, `mariadb`, and `jdbc` (optional drivers). Private databases reachable via a `network_mode='bridge'` WebSocket tunnel.
- **Real free tier** — compute is the user's browser; Hex can't match it without absorbing kernel cost.

---

## 🚀 Quickstart

### Docker Compose (fastest — one command)

The repo ships a `docker-compose.yml` with two services: `db` (postgres:16-alpine) and a
combined `app` (root `Dockerfile` — builds the Vite SPA and runs FastAPI, serving the SPA and
the `/api/v1` API on a single origin at port 8000).

```bash
# 1. Clone and start the stack
git clone https://github.com/imranparuk/nubi.git
cd nubi
make up          # docker compose up -d --build

# 2. Open the app
#    App (SPA + API): http://localhost:8000
#    API docs:        http://localhost:8000/docs (dev only)

# 3. (Optional) seed a test user
cd backend && DATABASE_URL=postgresql://nubi:nubi@localhost:5432/nubi python seed.py
#    → test@nubi.dev / nubitest123

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

Seed a test user (optional, with the venv active):

```bash
cd backend && DATABASE_URL=postgresql://user:pass@host/db python seed.py
# → test@nubi.dev / nubitest123
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
| `KERNEL_LOCAL_ENABLED` | Backend | `true` to allow local subprocess kernel (dev only) |
| `LLM_PROVIDER` | Optional | `anthropic` / `openai` / `gemini` + matching API key |
</details>

---

## 🏗️ Architecture

```
                     ┌──────────────────────────────────────────────┐
                     │               Browser / Host page            │
                     │                                              │
                     │  <nubi-dashboard>  ←──  getToken()           │
                     │  <nubi-kpi> <nubi-table> <nubi-chart>        │
                     │  DuckDB-WASM  ←── Arrow IPC (streaming)      │
                     │  regl WebGL scatter (>20k rows auto-switch)  │
                     └─────────────────┬────────────────────────────┘
                                       │ HTTPS / JWT
                     ┌─────────────────▼────────────────────────────┐
                     │            FastAPI backend                   │
                     │                                              │
                     │  /auth/*     email+pw / Google OAuth / JWKS  │
                     │  /query      planner → cache → executor      │
                     │  /compute/run  kernel router                 │
                     │  /ai/*       grounding + dashboard gen       │
                     │  /lineage    SQL lineage graph               │
                     │  /jobs       cron + interval scheduler       │
                     │  REST CRUD   datastores/boards/queries/…     │
                     └────┬──────────────────────┬──────────────────┘
                          │                      │
          ┌───────────────▼──────┐  ┌────────────▼───────────────────┐
          │  Postgres / Neon     │  │  Connector registry            │
          │  (asyncpg, SSL)      │  │  postgres  (ADBC, native Arrow)│
          └──────────────────────┘  │  duckdb    (in-mem + file)     │
                                    │  http_json (post-fetch RLS)    │
                                    │  mysql · mariadb · jdbc (opt)  │
                                    │  + VPC bridge transport        │
                                    └────────────┬───────────────────┘
                                                 │ Arrow IPC
                          ┌──────────────────────▼────────────────┐
                          │  Content-addressed cache (LRU + TTL)  │
                          │  X-Nubi-Cache: HIT | MISS header      │
                          └───────────────────────────────────────┘

 Compute kernel (first-party only — embed tokens → 403):
   LocalSubprocessRunner  (dev; KERNEL_LOCAL_ENABLED=true, ENV!=production)
   E2BRunner / ModalRunner (prod; Firecracker microVM, no host network/secrets)
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
| AI / LLM | NullProvider (default, zero network); lazy Anthropic / OpenAI / Gemini via env |
| Frontend | React 19, Vite 7, TailwindCSS, react-router-dom |
| Viz | regl (WebGL scatter, ~1M pts), apache-arrow, @duckdb/duckdb-wasm, ECharts |
| Embed | Custom elements (`<nubi-dashboard>`, `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`), DOMPurify |
| SDK | `@nubi/sdk` — framework-agnostic ESM, wraps auth + query + resource CRUD + embed |
| CLI | Python typer (`nubi login / deploy / run / diff / pull`) |
| MCP | Python `mcp` SDK, stdio transport, 6 tools |
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
│   │   ├── lineage/  sqlglot AST extractor, LineageGraph
│   │   ├── jobs/     cron + interval scheduler, executor, store
│   │   ├── repos/    asyncpg (prod) + in-memory (test) repository layer
│   │   └── routes/   auth, query, compute, embed, ai, lineage, jobs, resources
│   └── tests/        ~27 test modules + conformance suite (golden Arrow + cache keys)
├── database/         Forward-only SQL migration runner + 6 migrations
├── src/              React 19 frontend (Vite + Tailwind) — pages, components, viz
├── embed/            Web components: <nubi-dashboard>, <nubi-kpi>, <nubi-table>, <nubi-chart>
├── sdk/              @nubi/sdk — createNubiClient ESM package
├── cli/              nubi CLI (typer): login / deploy / run / diff / pull
├── mcp/              MCP stdio server — 6 tools for agent authoring
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
| M7 — Lineage + AI + MCP | ✅ Done | sqlglot lineage extractor, deterministic grounding, LLMProvider, MCP server (6 tools) |
| M8 — LLM-authorable dashboards | ✅ Done | `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>` widget kit, DOMPurify renderer, `POST /ai/dashboard` |
| M9 — Connector SDK + HTTP/JSON | ✅ Done | FunctionConnector, apply_rls_postfetch, HttpJsonConnector, NoSQL deliberately out of scope |
| Connector breadth | ✅ Done | Registry ships 8 types: `postgres`, `duckdb` (in-mem + read-only file-backed), `http_json`, `mysql`, `mariadb`, `jdbc`, `snowflake`, `bigquery` (the last four via optional drivers, lazily imported) |
| VPC bridge | ✅ Done | `network_mode='bridge'` opens a WebSocket TCP tunnel via `BridgeBroker`, wired into the query path (`resolve_network_async`); other modes 501 |
| Builder layer (M13–M22) | ✅ Done | Query workspace + typed params, filter/variable/route-param interactivity, TanStack table + conditional formatting, 9 chart types, exports, scheduled reports, AI-SQL, agentic chat, git sync |
| M10 — Docker self-host smoke test | 🔄 In progress | docker-compose.yml ships locally (db + combined app on :8000); live-infra CI smoke test is the remaining capstone |
| M11 — Scheduled jobs | ✅ Done | cron + interval scheduler (deterministic `now`), `execute_job`, CRUD + run-now + run-history routes |
| M12 — Capability-gated RLS | ✅ Done | connector resolution via `datastore.config.type`, 501 gate when `predicate_rls=False` + active policies |

**Tests:** ~27 backend test modules + conformance suite (golden Arrow output + byte-identical cache keys), MCP tests, CLI tests, dashboard sanitizer (`node --test`), SDK tests.

**Experimental / not production-hardened:** `LocalSubprocessRunner` (dev-grade isolation — same OS user, host network); Docker Compose stack not yet smoke-tested against live external infra (Neon SSL, E2B, real Google OAuth).

---

## 🔌 Embedding quickstart

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

**1. Register your issuer** in `app/auth/issuers.py`:

```python
{
  "iss": "https://your-app.example.com",
  "jwks_uri": "https://your-app.example.com/.well-known/jwks.json",
  "aud": "nubi:your-project-id",
  "allowed_origins": ["https://your-app.example.com"],
}
```

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
| `nubi` CLI | [`cli/`](cli/README.md) | `login / deploy / run / diff / pull` — with `--dry-run` |
| MCP server | [`mcp/`](mcp/README.md) | stdio MCP — 6 tools for agent dashboard authoring |
| Embed bundle | [`embed/`](embed/README.md) | `<nubi-dashboard>` + widget kit custom elements |

---

## 📖 Documentation

- [`docs/cache-key-spec.md`](docs/cache-key-spec.md) — Frozen cache-key spec and test vectors (language-neutral)
- [`docs/conformance.md`](docs/conformance.md) — Conformance suite documentation
- [`docs/kernel-security.md`](docs/kernel-security.md) — Kernel security model: local vs remote
- [`ROADMAP.md`](ROADMAP.md) — Full product strategy, positioning vs Hex/Cube, milestone sequence, Rust→WASM carve-out design

---

## 🤝 Contributing

PRs are welcome. The fastest path:

1. Fork, create a feature branch.
2. Run the test suite (`cd backend && pytest`).
3. Open a PR — describe the problem and solution; reference any relevant milestone or doc.

Please keep commits small and focused. The conformance suite must stay green; any new connector or planner change needs a corresponding test vector.

---

## License

[Apache License 2.0](LICENSE) — see the `LICENSE` file.
