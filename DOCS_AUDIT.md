# Nubi Documentation Coverage Matrix

**Last refreshed:** 2026-06-26  
**Branch:** `docs/comprehensive` (merged from `feat/embed-bi-substrate`)

This matrix replaces the previous point-in-time audit snapshot. It is the
current per-audience coverage record. Each row is a capability or surface;
each column is an audience; the cell shows the primary document(s) and status.

Legend: **OK** = documented and accurate, **ADDED** = added in this wave,
**REF** = referenced from another doc (not a standalone page).

---

## Audience 1 — End Users / Analysts

| Capability / Surface | Doc(s) | Status |
|---|---|---|
| Getting started, sign-up, first dashboard | `docs/getting-started.md` | OK |
| UI tour — app shell, sidebar, topbar | `docs/ui-tour.md` | OK |
| Overview page (`/overview`) — stats + data health + activity | `docs/ui-tour.md` §Overview | ADDED |
| Workqueue page (`/workqueue`) — alerts + failed runs + stale data | `docs/ui-tour.md` §Workqueue | ADDED |
| Connectors (Postgres, DuckDB, HTTP/JSON, etc.) | `docs/connectors.md` | OK |
| Data browser | `docs/connectors.md` §Data Browser | OK |
| Queries & parameters | `docs/queries-and-params.md` | OK |
| Dashboards & DashboardSpec | `docs/dashboards.md` | OK |
| Canvas (HTML-native) | `docs/semantic-and-data-apps.md` §Canvas | OK |
| Flows (notebook + DAG canvas) | `docs/flows.md`, `docs/semantic-and-data-apps.md` | OK |
| Watches / alerts | `docs/notifications-and-integrations.md` | OK |
| Automations / scheduled jobs | `docs/exports-and-jobs.md` | OK |
| Pre-aggregations | `docs/pre-aggregations.md` | OK |
| Exports & scheduled reports | `docs/exports-and-jobs.md` | OK |
| Settings — profile, members, integrations, security, usage | `docs/organization-settings.md`, `docs/ui-tour.md` §Settings | OK |
| Secrets (flow secrets) | `docs/secrets.md` | OK |
| KPI targets + RAG semantics | `docs/metrics-reference.md` | OK |
| Metric /explain root-cause analysis | `docs/metrics-reference.md`, `docs/semantic-and-data-apps.md` | OK |

---

## Audience 2 — OSS Contributors

| Capability / Surface | Doc(s) | Status |
|---|---|---|
| Dev setup (env vars, venv, npm, seed) | `docs/development.md`, `CONTRIBUTING.md` | OK |
| Test suites (pytest, test:embed, test:e2e:embed, test:dash) | `docs/development.md` | OK |
| Architecture (planner→executor, conformance, engine boundary) | `docs/architecture-and-economics.md`, `docs/conformance.md`, `docs/cache-key-spec.md` | OK |
| How to add a connector / metric / web component | `CONTRIBUTING.md` | OK |
| Migration convention (in-place `CREATE TABLE IF NOT EXISTS`) | `CONTRIBUTING.md`, `docs/development.md` | OK |
| Repo layout & conventions | `CONTRIBUTING.md` | OK |
| Files-as-code / Git Sync | `docs/files-as-code.md`, `docs/git-sync.md` | OK |
| SDK & CLI | `docs/sdk-and-cli.md` | OK |
| Security review notes | `SECURITY_REVIEW.md` | OK |

---

## Audience 3 — Cloud / EE Operators & Host Integrators

| Capability / Surface | Doc(s) | Status |
|---|---|---|
| Self-host deployment (Docker Compose, SSL, Postgres) | `docs/self-host.md` | OK |
| Open-core / EE split | `docs/open-core.md`, `docs/architecture-open-core.md` | OK |
| Billing (ZAR + USD anchoring + FX) | `docs/billing-and-usage.md`, `docs/cloud.md` | OK |
| Data-custody tier (BYO storage, CMEK, region pinning) | `docs/custody-tier.md` | OK |
| Embedding — JWT minting (RS256/ES256), per-viewer RLS | `docs/embedding.md` | OK |
| Embed API v1 — all web components, events, theme contract | `docs/embed-api.md` | OK |
| Nubi as MCP server — JSON-RPC, tool catalog, auth | `docs/mcp.md` §3, `docs/api-reference.md` §MCP | OK |
| MCP server registry CRUD | `docs/mcp.md` §1, `docs/api-reference.md` §MCP | OK |
| Bridges (agent-per-VPC reverse tunnel) | `docs/bridges.md` | OK |
| Lakehouse (ingest, export, DuckDB on object storage) | `docs/lakehouse.md` | OK |
| Governance — RLS, authoring scopes | `docs/governance.md` | OK |
| Outbound webhooks (5 events) | `docs/semantic-and-data-apps.md` §Outbound webhooks | OK |
| Declarative provisioning (`nubi apply`) | `docs/semantic-and-data-apps.md` §Declarative provisioning | OK |
| Host-mode tenancy (`org_claim`) | `docs/semantic-and-data-apps.md` §Enabling host mode | OK |
| Issuer registration (`POST /security/jwt-issuers`) | `docs/embedding.md`, `README.md` | OK |
| Connector security (AES-256-GCM, key rotation) | `docs/connector-security.md` | OK |
| Kernel security (two-kernel trust boundary) | `docs/kernel-security.md` | OK |

---

## Route-prefix coverage matrix

All prefixes under `/api/v1`. **Primary doc** = the doc that covers it most
fully. REF = mentioned in another doc.

| Route prefix | Primary doc | Status |
|---|---|---|
| `/admin` | `docs/organization-settings.md` (admin console) | OK |
| `/auth` | `docs/getting-started.md`, `docs/api-reference.md` §Authentication | OK |
| `/boards` | `docs/dashboards.md`, `docs/api-reference.md` §DataProvider boards | OK |
| `/bridges` | `docs/bridges.md` | OK |
| `/cache` | `docs/cache-key-spec.md` | OK |
| `/chat` | `docs/ai-and-mcp.md` | OK |
| `/connectors` | `docs/connectors.md` | OK |
| `/datasets` | `docs/lakehouse.md` | OK |
| `/embed` | `docs/embedding.md`, `docs/embed-api.md` | OK |
| `/features` | `docs/open-core.md`, `docs/architecture-open-core.md` | OK (REF) |
| `/flows` | `docs/flows.md`, `docs/api-reference.md` §Flows | OK |
| `/flows/{id}/versions`, `/revert/{v}`, `/environments` | `docs/transformation.md`, `docs/api-reference.md` §Flow versions | ADDED (api-reference) |
| `/git` | `docs/git-sync.md` | OK |
| `/health` (freshness/score/estate) | `docs/data-health.md`, `docs/api-reference.md` §Data Health | ADDED (api-reference) |
| `/integrations` | `docs/organization-settings.md` §Integrations | OK |
| `/jobs` | `docs/exports-and-jobs.md` | OK |
| `/lake` (ingest/export) | `docs/lakehouse.md` | OK |
| `/lakehouse` | `docs/lakehouse.md` | OK |
| `/lineage` (dag, dag/{id}) | `docs/lineage.md`, `docs/api-reference.md` §Lineage DAG | ADDED (api-reference) |
| `/mcp` (servers CRUD + JSON-RPC) | `docs/mcp.md`, `docs/api-reference.md` §MCP | ADDED (api-reference) |
| `/metrics` | `docs/metrics-reference.md`, `docs/api-reference.md` §Metrics | OK |
| `/metrics/{id}/lineage` | `docs/lineage.md` §Metric lineage, `docs/api-reference.md` | ADDED (api-reference) |
| `/notifications` | `docs/organization-settings.md` (REF) | OK |
| `/ops` | `docs/self-host.md` (REF), production monitoring note | OK (REF) |
| `/orgs` | `docs/organization-settings.md` | OK |
| `/projects` | `docs/organization-settings.md` §Project settings | OK |
| `/push` | `docs/notifications-and-integrations.md` | OK |
| `/secrets` | `docs/secrets.md`, `docs/sdk-and-cli.md` | OK |
| `/security/jwt-issuers` | `docs/embedding.md` §JWT issuers | OK |
| `/transpile` | `docs/transformation.md` §SQL dialect transpilation, `docs/api-reference.md` §SQL Transpilation | ADDED (api-reference) |
| `/variables` | `docs/flows.md` (REF — flow param variables) | OK (REF) |

---

## Embed component coverage (embed-api.md)

| Component | Documented | Status |
|---|---|---|
| `<nubi-dashboard>` | `docs/embed-api.md` §nubi-dashboard | OK |
| `<nubi-kpi>` | `docs/embed-api.md` §nubi-kpi | OK |
| `<nubi-kpi-react>` | `docs/embed-api.md` §nubi-kpi-react | OK |
| `<nubi-table>` | `docs/embed-api.md` §nubi-table | OK |
| `<nubi-chart>` | `docs/embed-api.md` §nubi-chart | OK |
| `<nubi-explain>` | `docs/embed-api.md` §nubi-explain | OK |
| `<nubi-query-editor>` | `docs/embed-api.md` §nubi-query-editor | OK |
| `<nubi-metric-explorer>` | `docs/embed-api.md` §nubi-metric-explorer | OK |
| `<nubi-lineage>` | `docs/embed-api.md` §nubi-lineage | OK |
| `<nubi-health>` | `docs/embed-api.md` §nubi-health | OK |

---

## Remaining gaps

- `/ops` — the `/ops/stats` and `/ops/health` in-process observability routes
  are mentioned only as asides in self-host / production hardening notes. No
  dedicated reference entry exists. Low priority (internal-only endpoint).
- `/variables` — the variables resource has no dedicated doc page; it is
  referenced as flow param variables in `docs/flows.md`. A short reference
  entry in `docs/api-reference.md` would help integrators.
- `/features` — internal feature-flag endpoint; mentioned in architecture docs
  but not in api-reference.md. Intentionally excluded (internal surface).
