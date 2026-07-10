# Developer Guide

Build on Nubi. This is the hub for developers **self-hosting**, **integrating
with**, **extending**, or **contributing to** Nubi. Nubi is fully open source
under **Apache-2.0** — the whole product is in the repo, including the `ee/`
tree that powers Nubi Cloud's billing.

This page orients you and links out to the detailed references. If you just
want to *use* the product UI, start with the [Quickstart](/docs/quickstart)
instead.

---

## Architecture at a glance

Nubi is an LLM-native BI platform. The moving parts:

| Layer | What it is | Where it lives |
|---|---|---|
| **Frontend** | React 19 + Vite SPA — dashboard editor, query workspace, flow editor, connectors, settings | `src/` |
| **Backend** | FastAPI (Python 3.11+) — REST API under `/api/v1`, auth, RLS, query planner, content-hash cache | `backend/app/` |
| **Flows worker** | Durable DAG engine — scheduler + task pool for scheduled/triggered runs | `backend/app/flows/`, `backend/worker.py` |
| **Connectors** | 20+ data sources (Postgres, DuckDB, MySQL, Snowflake, BigQuery, Redshift, ClickHouse, Databricks, Athena, Trino, …) | `backend/app/connectors/` |
| **Lakehouse** | DuckDB compute over bucket-prefix-isolated object storage | `backend/app/lakehouse/`, `backend/app/storage/` |
| **Semantic layer** | Governed metrics — one definition, time intelligence, RLS keys | `backend/app/routes/metrics.py` |
| **AI / MCP** | Grounded text-to-SQL, agentic chat loop, Nubi-as-MCP-server | `backend/app/ai/`, `backend/app/chat/`, `backend/app/routes/mcp.py` |
| **SDK** | `@nubi/sdk` — framework-agnostic JavaScript client + embed mount | `sdk/` |
| **CLI** | `nubi` — Python CLI for files-as-code, flows, secrets | `cli/` |

For the full boundary between core and the Cloud-only `ee/` tree, see
[Open Source + Cloud Architecture](/docs/architecture-open-core).

---

## Local development

Get the dev stack running (Docker Compose or fast-refresh dev servers),
seed a demo workspace, and run the test suites: see
[Developing Nubi](/docs/development).

Quick version:

```bash
npm install
python3 -m venv .venv-backend && .venv-backend/bin/python -m pip install -r requirements.txt
# start Postgres, migrate, seed the demo workspace, then:
npm run dev:full        # API on :8000, Vite on :5173
```

Dev login: `admin@nubi.dev` / `nubi-admin-2026`. The interactive Swagger UI
is at `http://localhost:8000/docs` in development.

To deploy the free, full product for real use, see
[Self-hosting](/docs/self-host).

---

## The REST API

Every backend feature is reachable over HTTP under `/api/v1`. Endpoints are
org-scoped, return JSON (query/metric endpoints stream Apache Arrow IPC), and
require a Bearer token.

- **Full reference:** [API Reference](/docs/api-reference) — auth, conventions,
  and every endpoint grouped by resource (projects, connectors, queries,
  boards, flows, metrics, embedding, billing, …).
- **Auth model:** three token kinds — a first-party JWT from `POST /auth/login`
  or Google OAuth, a long-lived `nubi_ak_…` API key (for CLI/CI), and a
  host-signed RS256/ES256 **embed JWT**. See
  [Authentication](/docs/api-reference#authentication).

```bash
# Get a token, then call the API
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@nubi.dev","password":"nubi-admin-2026"}' | jq -r .access_token
```

---

## The SDK and CLI

Two first-party tools for building on Nubi programmatically — full reference in
[SDK & CLI](/docs/sdk-and-cli):

- **`@nubi/sdk`** (JavaScript) — query data (returns Apache Arrow `Table`s),
  CRUD on datastores/boards/widgets/queries, and `embed.mount()` for dropping a
  `<nubi-dashboard>` into any page.
- **`nubi`** (Python CLI) — the everything-as-code workflow: `nubi login`,
  `nubi pull`, edit files, `nubi push` / `nubi deploy`. Mint CI tokens with
  `nubi auth create-key`.

For agent integrations, Nubi also speaks **MCP** — both as a client (register
external servers) and as a server (`POST /mcp`, JSON-RPC 2.0). See
[AI & MCP](/docs/ai-and-mcp) and [MCP](/docs/mcp).

---

## Files-as-code and git sync

Your whole Nubi project — dashboards, queries, flows, connectors — round-trips
to a plain git repo. Edit as files, keep secrets out of git, and deploy from
CI.

- **On-disk format & round-trip:** [Files-as-Code](/docs/files-as-code)
- **Bind a project to GitHub/GitLab and reconcile:** [Git Sync](/docs/git-sync)
- **Secret model** (two gitignored `.env` stores, never committed):
  [Secrets](/docs/secrets)

The in-app Code views (flow `flow.py` + cells, query `.sql` + `.meta.json`) are
the same format the CLI reads and writes — see
[SDK & CLI → CLI](/docs/sdk-and-cli).

---

## OSS ↔ Cloud split (open core)

Nubi is open core, done honestly: **the entire product is free and functional
when self-hosted**, with billing off. The only Cloud-specific code is billing,
and it lives in one directory that ships in every clone:

- Backend billing → `backend/app/ee/` (Paystack, tiers, FX, wallet, quota).
- Frontend billing UI → `src/ee/`.
- The **core invariant:** core code never imports from `app.ee` / `src/ee`.

The `ee/` tree stays inert unless Nubi's own Cloud deployment sets its internal
`NUBI_LICENSE_KEY` switch. There is no separate paid edition to buy and nothing
to strip out for self-host. The feature-gate seam (`feature_enabled()`,
`register_feature()`, `enforce_quota()`) is documented in full in
[Open Source + Cloud Architecture](/docs/architecture-open-core); a shorter
overview is in [Open core](/docs/open-core).

---

## Extending Nubi

### Add a connector

Connectors live in `backend/app/connectors/`. Each is registered in the
connector factory. Two rules from the [conventions](/docs/development#conventions):

- **Lazy imports** — heavy drivers (BigQuery, Snowflake, …) are imported inside
  the connector factory, not at module top, so the core app starts without
  optional dependencies installed.
- **Secrets never round-trip** — credentials are AES-256-GCM encrypted at rest
  and come back blank from the API. See [Connectors](/docs/connectors) and
  [Connector security](/docs/connector-security).

For RLS-aware sources, implement predicate injection; connectors that can't
return `501 source_unsupported_rls` on governed paths.

### Add a flow task kind

Flows are a durable DAG of typed tasks (`query`, `python`, and more). The
executor, node kinds, secrets resolution, and storage backends are core
(`backend/app/flows/`). New Cloud-only task kinds (e.g. the `fx_refresh` kind
billing registers) are added through the same registry from `ee/` — see the
[startup sequence](/docs/architecture-open-core#startup-sequence). The DAG
model, task fields, and run semantics are in [Flows](/docs/flows) and the
[Flows API](/docs/api-reference#flows).

### Add a Cloud-only feature

Follow the `declare_commercial()` + `register_feature()` + `getSlot()` pattern
in [Adding a new Cloud-only feature](/docs/architecture-open-core#adding-a-new-cloud-only-feature).
Keep new billing/cloud code under `ee/`.

---

## Contributing

- [Developing Nubi](/docs/development) — repo layout, dev stack, test suites,
  migration conventions.
- [Docs & screenshots](/docs/docs-and-screenshots) — docs render in-app at
  `/docs`; update the relevant `docs/*.md` in the same PR as any UI change.

Docs are part of the product. The whole repo is Apache-2.0.

---

## Related references

| Doc | What it covers |
|-----|---------|
| [API Reference](/docs/api-reference) | Every REST endpoint, auth, conventions |
| [SDK & CLI](/docs/sdk-and-cli) | `@nubi/sdk` client, `nubi` CLI |
| [Files-as-Code](/docs/files-as-code) | On-disk project format, round-trip |
| [Git Sync](/docs/git-sync) | Bind to GitHub/GitLab, reconcile, deploy |
| [Open Source + Cloud Architecture](/docs/architecture-open-core) | Core vs `ee/`, feature gates |
| [Developing Nubi](/docs/development) | Dev stack, tests, conventions |
| [Self-hosting](/docs/self-host) | Deploy the free, full product |
| [Embedding](/docs/embedding) | Embed JWT trust boundary, RLS |
</content>
</invoke>
