# Nubi Documentation

Nubi is a batteries-included BI and embedded-analytics platform. The kernel runs **in the user's browser** by default (DuckDB-WASM), so the marginal cost of a dashboard view is approximately zero. A server kernel (E2B / Modal Firecracker microVM) handles native wheels and large jobs when needed.

---

## What Nubi Is

**Embedded-first** — the core surface is embedding: a host app signs short-lived JWTs, mounts `<nubi-dashboard>`, and gets live cross-filtering dashboards with server-enforced row-level security at near-zero cost per view.

**BYO warehouse** — point at any Postgres-compatible warehouse (Neon, RDS, AlloyDB) or bring your own connector. Nubi does not own your data.

**AI-native** — grounded text-to-SQL (`POST /api/v1/ai/sql`), natural-language dashboard generation (`POST /api/v1/ai/dashboard`), an agentic chat loop with a 7-tool registry (`POST /api/v1/ai/chat`), and a full MCP server so Claude and other agents can author dashboards directly.

**Arrow-native data plane** — data moves as Arrow IPC at every boundary (warehouse → edge cache → browser) with no JSON round-trip tax.

---

## Start Here

| | |
|---|---|
| [**Getting Started**](/docs/getting-started) | Docker Compose or dev path, environment variables, monorepo layout |
| [**Connectors**](/docs/connectors) | Postgres, DuckDB (in-mem + file), HttpJson, MySQL/MariaDB, JDBC, BYO warehouse, 7-flag capability contract, Data Browser |
| [**Connector Security**](/docs/connector-security) | AES-256-GCM secret encryption, key rotation, network modes |
| [**Queries & Parameters**](/docs/queries-and-params) | Registered queries, `{{named}}` typed params, query library, text-to-SQL |
| [**Dashboards**](/docs/dashboards) | DashboardSpec, widget types (kpi/metric/chart/table/pivot/filter/text/section), 9 chart types, variables, `/d/:id?var=` route params |
| [**Exports & Scheduled Reports**](/docs/exports-and-jobs) | CSV/PDF exports, cron jobs, per-recipient locked params |
| [**Flows**](/docs/flows) | Multi-step DAG orchestrator: query/python/agent/materialize/bucket_load/map/branch/preagg_refresh, retries, caching, materialized blends, React Flow canvas + code-first Python SDK, LLM authoring |
| [**Pre-Aggregations**](/docs/pre-aggregations) | Auto rollups mined from the query log, ranked by frequency × scanned-bytes, RLS-preserving, transparent routing with HIT counts |
| [**Secrets**](/docs/secrets) | Org-scoped encrypted secrets, `{{ secrets.NAME }}` in flows, `nubi secrets set/list` |
| [**AI, Chat & MCP**](/docs/ai-and-mcp) | Grounded ask, agentic chat, 7 agent tools, MCP server (6 tools), Slack/WhatsApp gateway |
| [**Embedding**](/docs/embedding) | JWT minting (RS256/ES256), per-viewer RLS, token-locked params, `<nubi-dashboard>` |
| [**Git Sync**](/docs/git-sync) | GitHub App + GitLab push; commit queries and dashboards as code |
| [**Bridges**](/docs/bridges) | Agent-per-VPC reverse tunnel, WebSocket protocol, reachability modes |
| [**SDK & CLI**](/docs/sdk-and-cli) | `@nubi/sdk` JavaScript client and the `nubi` Python CLI |
| [**Billing Model**](/docs/billing-model) | Pricing tiers (Free/$79/$199/$499/$1,799), ZAR/USD FX, metered dimensions, no per-seat pricing |
| [**Open Core**](/docs/architecture-open-core) | CE/EE split, feature-gate API, Docker CE/EE images, how EE billing slots in |

---

## Architecture Overview

```
Warehouse  →  Edge (content-hashed cache)  →  Browser (DuckDB-WASM)
                                          ↘  Server Kernel (E2B / Modal)
```

The planner translates SQL through sqlglot into a `PhysicalPlan`, injects RLS predicates as AST-level predicates (never string-concatenated), checks the content-hashed cache, then streams Arrow IPC to the caller.

---

## Key Concepts

> **Arrow-native** — Data moves as Arrow IPC at every boundary. No JSON round-trips, no ORM overhead.

> **Content-hashed cache** — `cache_key = SHA-256(canonical_json({sql, params, rls_claims}))`. Identical queries with identical RLS context share one cache slot. N viewers collapse to one warehouse hit.

> **Server-side RLS** — JWT `policies` claims are injected as AST predicates by the planner. The browser never sees unfiltered data. Embed tokens cannot execute arbitrary SQL — they must reference server-registered queries.

> **LLM-authorable dashboards** — Dashboards are sanitized HTML/CSS composed of `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, and `<nubi-text>` custom elements. DOMPurify strips scripts and event handlers.

---

## Tech Stack

| Layer | Technologies |
|---|---|
| Backend | FastAPI 0.131, Python 3.11+, uvicorn, pydantic-settings v2 |
| DB | asyncpg (connection pool, raw SQL); Postgres 16 / Neon (SSL required) |
| Auth | argon2-cffi (argon2id), PyJWT HS256, cryptography RS256/ES256 JWKS |
| Data plane | sqlglot (AST planner + RLS injection), pyarrow, DuckDB, adbc-driver-postgresql |
| Cache | In-process LRU + TTL (`ContentAddressedCache`); Redis-swappable interface |
| Compute | subprocess (dev); e2b-code-interpreter / modal (prod, Firecracker microVM) |
| Frontend | React 19, Vite 7, TailwindCSS, react-router-dom |
| Viz | regl (WebGL scatter, ~1M pts), apache-arrow, @duckdb/duckdb-wasm |
| Embed | Custom elements (`<nubi-dashboard>`, `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, `<nubi-text>`), DOMPurify |
| SDK | `@nubi/sdk` — framework-agnostic ESM, wraps auth + query + resources + embed |
| CLI | Python typer (`nubi login / deploy / run / diff / pull`) |
| MCP | Python `mcp` SDK, stdio transport, 6 tools |
| Chat gateway | Slack Events API + WhatsApp Cloud API webhook adapters |
