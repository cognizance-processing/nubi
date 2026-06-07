# Getting Started

Nubi is a batteries-included BI and embedded-analytics platform. The kernel runs **in the user's browser** (DuckDB-WASM) by default, so the marginal cost of a dashboard view is approximately zero. A server kernel (E2B / Modal Firecracker microVM) is the escape hatch for native wheels and large jobs.

---

## Quickstart — Docker Compose

The fastest path is Docker Compose, which ships three services: `db` (Postgres 16), `backend` (FastAPI + auto-migrate), and `frontend` (nginx on port 8080).

```bash
git clone https://github.com/imranparuk/nubi.git
cd nubi
make up          # docker compose up -d --build
# Frontend:  http://localhost:8080
# API docs:  http://localhost:8000/docs
```

Seed a test user (optional):

```bash
cd backend
DATABASE_URL=postgresql://nubi:nubi@localhost:5432/nubi python seed.py
# → test@nubi.dev / nubitest123
```

Smoke test the stack:

```bash
make smoke       # scripts/smoke.sh — health + auth + query assertions
```

---

## Dev Path — Backend and Frontend Separately

**Prerequisites:** Python 3.11+, Node 20+

```bash
# Backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example backend/.env   # set DATABASE_URL and JWT_SECRET at minimum
python database/migrate.py
cd backend && uvicorn main:app --reload
# API: http://localhost:8000

# Frontend (new terminal, repo root)
npm install
cp .env.example .env           # set VITE_BACKEND_URL=http://localhost:8000
npm run dev
# Frontend: http://localhost:5173
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes | `postgresql://...?sslmode=require` (Neon) or local Postgres |
| `JWT_SECRET` | Yes | HS256 signing secret — `openssl rand -hex 32` |
| `CONNECTOR_SECRET_KEY` | Connectors | Base64-encoded 32-byte AES-256 key for connector credential encryption |
| `VITE_BACKEND_URL` | Frontend | Base URL of the FastAPI backend |
| `GOOGLE_CLIENT_ID` | OAuth | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | OAuth | Callback URL registered in Google Console |
| `FRONTEND_URL` | Backend | Where the backend redirects after Google OAuth |
| `CORS_ORIGINS` | Backend | Comma-separated allowed origins |
| `ENV` | Backend | `development` / `production` (disables `/docs` in prod) |
| `KERNEL_LOCAL_ENABLED` | Backend | `true` to allow local subprocess kernel (dev only) |
| `LLM_PROVIDER` | Optional | `anthropic` / `openai` / `gemini` + matching API key |
| `ANTHROPIC_API_KEY` | Optional | Required when `LLM_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | Optional | Required when `LLM_PROVIDER=openai` |
| `GEMINI_API_KEY` | Optional | Required when `LLM_PROVIDER=gemini` |
| `NUBI_GIT_WORKSPACE` | Optional | Root directory for git-sync repos (default: system temp dir) |
| `GIT_REMOTE_PROVIDER` | Optional | `github_app` / `gitlab` / `none` (default) |
| `GITHUB_APP_ID` | Git sync | GitHub App numeric ID |
| `GITHUB_APP_PRIVATE_KEY` | Git sync | PEM-encoded RSA private key for the GitHub App |
| `GITHUB_APP_INSTALLATION_ID` | Git sync | GitHub App installation ID |
| `GITLAB_TOKEN` | Git sync | GitLab personal or project access token |
| `GITLAB_HOST` | Git sync | GitLab host (default: `gitlab.com`) |
| `SLACK_SIGNING_SECRET` | Optional | Enables Slack Events API webhook signature verification |
| `WHATSAPP_APP_SECRET` | Optional | Enables WhatsApp Cloud API webhook signature verification |

---

## Architecture at a Glance

```
Warehouse  →  Edge (content-hashed cache)  →  Browser (DuckDB-WASM)
                                          ↘  Server Kernel (E2B / Modal)
```

- **Arrow IPC at every boundary** — no JSON round-trips, no serialization tax.
- **Content-hashed edge cache** — N viewers of the same dashboard collapse to one warehouse hit.
- **Server-side RLS** — JWT claims injected as AST predicates before the query reaches the warehouse.
- **LLM-authorable dashboards** — `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, `<nubi-text>` custom elements; DOMPurify strips scripts.

---

## Running Tests

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

---

## Monorepo Layout

```
nubi/
├── backend/      FastAPI app — connectors, planner, auth, AI, jobs, git, chat
│   ├── app/
│   │   ├── auth/       argon2id, JWT HS256, Google PKCE, JWKS, sessions
│   │   ├── connectors/ sqlglot planner, Arrow executor, cache, connectors SDK
│   │   ├── compute/    KernelRunner ABC, LocalSubprocessRunner, E2BRunner, ModalRunner
│   │   ├── ai/         LLMProvider, grounding, SQL + dashboard generation, tools, agent
│   │   ├── lineage/    sqlglot AST extractor, LineageGraph
│   │   ├── jobs/       cron scheduler, executor, store, run history
│   │   ├── repos/      asyncpg (prod) + in-memory (test) repository layer
│   │   ├── security/   AES-256-GCM crypto (app.security.crypto)
│   │   ├── bridges/    bridge broker, WebSocket protocol
│   │   ├── dashboards/ DashboardSpec, spec_to_html, validate_spec
│   │   ├── queries/    QueryRegistry, QueryParam, named-param resolver
│   │   └── routes/     auth, query, ai, embed, git, chat, connectors, bridges, jobs, resources
│   └── tests/          ~27 test modules + conformance suite (golden Arrow + cache keys)
├── database/     Forward-only SQL migrations
├── src/          React 19 frontend (Vite + Tailwind)
├── embed/        <nubi-dashboard> and widget kit custom elements
├── sdk/          @nubi/sdk — createNubiClient ESM package
├── cli/          nubi CLI (typer): login / deploy / run / diff / pull
├── mcp/          MCP stdio server — 6 tools for agent authoring
└── docs/         Documentation source files
```

---

## First Steps After Setup

1. **Open `/d/sample`** — a built-in sample dashboard requiring no backend or auth.
2. **Browse `/queries`** — the query library lists all registered queries; run any of them with named params.
3. **Open the editor** (`/editor`) — drag-and-drop dashboard builder backed by `DashboardSpec`.
4. **Try AI** — hit `POST /api/v1/ai/sql` with `{"question": "show me all active demo records"}` to generate grounded SQL. No API key required in dev (uses `NullProvider`).
5. **Embed** — follow the [Embedding](/docs/embedding) guide to mount `<nubi-dashboard>` in your own app with per-viewer RLS.
