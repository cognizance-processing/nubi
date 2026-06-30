# Nubi Capability Matrix

The authoritative, host-trackable list of what Nubi **ships today** vs. what is
**partial**, **planned**, or **deliberately out of scope**. Embedding hosts and
integrators should track this file (plus [CHANGELOG.md](./CHANGELOG.md)) instead
of carrying redundant local shims.

- **Status legend:** ✅ Shipped · 🟡 Partial (works with a caveat) · 🗓️ Roadmap (not built) · ⛔ Out of scope (won't build)
- **Contract** = the public route / web component / artifact that delivers it.
- Every row links to docs where they exist. Routes are under `/api/v1`.
- **Maintenance:** this file is updated in the same PR as any capability change,
  and a matching entry is added to [CHANGELOG.md](./CHANGELOG.md). See
  [Keeping these current](#keeping-these-current).

_Last reviewed: 2026-06-26 · branch `wave/trim-stubs`._

---

## A. Data access & semantic layer

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Registered queries + typed `{{params}}` | ✅ | `POST /query`, `/query/registry` | [queries-and-params](docs/queries-and-params.md) |
| Governed semantic metrics — serving | ✅ | `POST /metrics/{id}/query` | [metrics-reference](docs/metrics-reference.md) |
| Metric explainability — dimension-contribution decomposition (why a number moved) | ✅ | `POST /metrics/{id}/explain` | [metrics-reference](docs/metrics-reference.md#contribution-analysis-post-metricsidexplain) |
| Metric authoring (derived/ratio, time-intel, top-N) | ✅ | `/metrics` CRUD (`author:metric`) | [semantic-and-data-apps](docs/semantic-and-data-apps.md) |
| Pre-aggregations / rollups (transparent routing) | ✅ | auto, query-log mined | [pre-aggregations](docs/pre-aggregations.md) |
| Connectors (Postgres, DuckDB, MySQL, JDBC, HttpJson, BYO) | ✅ | `/connectors` | [connectors](docs/connectors.md) |
| Result cache (keyed on SQL+params+RLS) | ✅ | internal | [cache-key-spec](docs/cache-key-spec.md) |

**Rate/SLA for host calls:** query-class routes (incl. `/metrics/{id}/query`) are
rate-limited — default **120 req/min** per org (`NUBI_RATELIMIT_QUERY_RPM`), burst
1.5×, Redis-backed across workers. No per-query timeout beyond the data source's
own. Embed/viewer tokens are exempt from usage metering.

**Chat / AI cost-DoS limits ✅** — chat and AI endpoints are protected by three
independent guards: (1) a dedicated rate-limit class (`NUBI_RATELIMIT_CHAT_RPM`,
default 20 rpm, burst 1.5×, same Redis-backed bucket as other classes); (2) an
aggregate per-turn token budget (`NUBI_CHAT_TURN_TOKEN_BUDGET`, default 16 000
tokens across all agent steps — loop stops cleanly when hit, no crash); and (3) a
per-turn timeout (`NUBI_CHAT_TURN_TIMEOUT_S`, default 90 s — streaming endpoints
emit a clean `error` SSE event; non-streaming `/ai/chat` returns HTTP 504). See
[ai-and-mcp](docs/ai-and-mcp.md) for env-var reference.

## B. Row-level security & tenancy

| Capability | Status | Contract | Docs |
|---|---|---|---|
| RLS from verified token only (never request body) | ✅ | planner AST injection | [governance](docs/governance.md) |
| Policy shapes: scalar (`=`), list (`IN`), range (`gte/gt/lte/lt`) | ✅ | `policies` claim → planner | [governance](docs/governance.md) |
| RLS via **embed-token** claims (scalar/list/range) | ✅ | embed JWT `policies` claim | [embedding](docs/embedding.md) |
| Hierarchical parent→child policy expansion (auto, in query path) | ✅ | `expand_rls_policies` wired into `/query` + `/metrics/{id}/query` | [governance](docs/governance.md) |
| Claim-native host-mode tenancy (org from JWT claim) | ✅ | issuer `host_mode`; tokens stripped to read-only | [embedding](docs/embedding.md) |
| Scope-resolution endpoint (authorize host writes) | ✅ `GET /auth/scope` | resolves token policies → effective (hierarchy + grants), org-scoped, fail-closed | [governance](docs/governance.md) |
| User→scope assignment store | ✅ `/access-grants` (optional — host may mint claims instead) | org-scoped CRUD, admin-gated, merged into `/auth/scope` | [governance](docs/governance.md) |
| Policy cardinality cap | ✅ | `NUBI_RLS_MAX_POLICY_VALUES` (default 5000); over-cap IN-list/expansion fails closed (400) | [governance](docs/governance.md) |

> Hosts can pass scalar/list/range **and** hierarchical (parent-value) RLS via
> embed-token `policies` today — Nubi expands parents to children server-side,
> org-scoped and fail-closed. You can retire a local resolver/scope shim.

## C. Data health & observability

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Freshness registry (read) | ✅ | `GET /health/freshness`, `/health/freshness/{key}` | [data-health](docs/data-health.md) |
| Weighted health score (freshness/completeness/availability) | ✅ | `GET /health/score` | [data-health](docs/data-health.md) |
| Estate graph | ✅ | `GET /health/estate` | [data-health](docs/data-health.md) |
| Freshness **write** path (populated on flow success) | ✅ | listener wired at startup | [data-health](docs/data-health.md) |
| Health reads via embed tokens | ✅ | `verified_identity` (embed + first-party) | [data-health](docs/data-health.md) |
| **Schema-drift detection / event API** (column add/remove/type-change) | ✅ | `GET /health/drift`, `GET /health/drift/{key}`, `SCHEMA_DRIFT` webhook event | [data-health](docs/data-health.md) |

## D. Lineage

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Dependency DAG | ✅ | `GET /lineage/dag`, `/lineage/dag/{node}` | [lineage](docs/lineage.md) |
| Metric lineage (input columns + upstream) | ✅ | `GET /metrics/{id}/lineage` | [lineage](docs/lineage.md) |
| Flow/cell column lineage | ✅ | `GET /lineage/flow/{id}`, `/lineage/query/{id}` | [lineage](docs/lineage.md) |

## E. Transformation, flows & environments

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Cell-based flows (SQL/Python/Note), DAG + notebook views | ✅ | `/flows` | [flows](docs/flows.md) |
| Flow spec **version history + revert** | ✅ | `GET /flows/{id}/versions`, `POST /flows/{id}/revert/{v}` | [transformation](docs/transformation.md) |
| Environment list + watermarks (read) | ✅ | `GET /flows/{id}/environments` | [transformation](docs/transformation.md) |
| Environment pin/create (write) | 🟡 | via `/environments` routes (not under `/flows`) | [transformation](docs/transformation.md) |
| Backfill (write) | ✅ | `POST /flows/{id}/backfill` (`author`/writer) | [transformation](docs/transformation.md) |
| Run **params snapshot** for audit/reproducibility | ✅ | returned by `GET /flows/runs/{id}` & `/flows/{id}/runs` (`params_snapshot`, `code_version`, `seed`) | [transformation](docs/transformation.md) |
| SQL transpilation (19 dialects) | ✅ | `POST /transpile` | [transformation](docs/transformation.md) |

## F. Version control (git)

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Bind project to GitHub/GitLab repo | ✅ | `POST /git/connect` | [git-sync](docs/git-sync.md) |
| **Live** remote push (real `git push`, optional PR/MR) | ✅ | `POST /git/push` (project-scoped) | [git-sync](docs/git-sync.md) |
| **Live** remote pull (real `git fetch` → import) | ✅ | `POST /git/pull` (project-scoped) | [git-sync](docs/git-sync.md) |
| Files-as-code round-trip (CLI) | ✅ | `nubi pull/push/deploy/diff` | [files-as-code](docs/files-as-code.md) |
| **Watches as code via `nubi apply`** | ✅ | `nubi apply`→`POST /api/v1/apply` (`kind: watch`, idempotent, org-scoped) | [files-as-code § Watches as code](docs/files-as-code.md#d2-watches-as-code) |
| **`watch_breach` labels passthrough** | ✅ | `emit_watch_breach` → webhook `data.labels` map | [observability § labels](docs/observability.md#watch_breach--labels-passthrough) |

> The legacy **org-level** git flow was a no-network stub; it is superseded by
> the **project-scoped** endpoints above, which make real authenticated network
> calls (token delivered via `GIT_ASKPASS`, never in argv).

## G. AI, chat & MCP

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Grounded ask / agentic chat (built-in tools) | ✅ | `/chat`, `/ai/*` | [ai-and-mcp](docs/ai-and-mcp.md) |
| **Nubi as an MCP server** (JSON-RPC; ~14 built-in tools) | ✅ | `POST /mcp` (`initialize`/`tools/list`/`tools/call`) | [mcp](docs/mcp.md) |
| **Host registers its own MCP server** (custom domain tools) | ✅ | `POST /mcp/servers` (CRUD) | [mcp](docs/mcp.md) |
| Host's custom tools discovered + callable in the agent loop | ✅ | `serverName.toolName` dispatch | [mcp](docs/mcp.md) |
| MCP outbound SSRF + DNS-rebind pinning; encrypted creds | ✅ | registry + client | [mcp](docs/mcp.md) |

> **Yes — an embedding host can register its own action tools** (e.g.
> `create-task`, `launch-campaign`) by registering its MCP server via
> `POST /mcp/servers`. Nubi's agent/chat loop then discovers them
> (`tools/list` against the host server) and calls them namespaced as
> `serverName.toolName`. You are not limited to Nubi's built-in tools.

## H. Embedding (web-component kit)

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Framework-agnostic web components (10) | ✅ | `<nubi-dashboard/kpi/table/chart/explain/query-editor/metric-explorer/lineage/health>` | [embed-api](docs/embed-api.md) |
| Per-viewer JWT (RS256/ES256), token-locked params | ✅ | `get-token` bridge | [embedding](docs/embedding.md) |
| 25-token theme contract, cross-filter bus, scope gating | ✅ | `NubiContext` | [embed-api](docs/embed-api.md) |
| Data-custody tier (BYO storage, CMEK, region pin) | ✅ | opt-in | [custody-tier](docs/custody-tier.md) |

## I. Audit & governance reads

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Scoped audit: query-executed (POPIA), export audit | ✅ | webhook event / board config | [observability](docs/observability.md) |
| **Unified action audit-log read API** (all mutations) | ✅ | `GET /audit`, `GET /audit/{type}/{id}` (owner/admin) | [api-reference](docs/api-reference.md#audit-log) |
| Spec version/revert for **boards** | ✅ | `/environments` `/versions/board/...` | [transformation](docs/transformation.md) |
| Spec version/revert for **metrics** | ✅ | `GET /metrics/{id}/versions`, `GET /metrics/{id}/versions/{v}`, `POST /metrics/{id}/revert/{v}` (`author:metric`) | [metrics-reference](docs/metrics-reference.md#spec-version-history--revert) |

## J. Explicitly out of scope (stays with the host)

| Area | Status | Why |
|---|---|---|
| MDM / probabilistic entity matching (Splink, embeddings) | ⛔ | Not BI; host's data-mastering concern |
| ML demand forecasting / price elasticity | ⛔ | Domain modelling, not the semantic/serving layer |
| **Model** explainability — SHAP / per-prediction feature attribution | ⛔ | Attribution over the **host's own** models (demand forecast, price elasticity, MDM match-scoring) — "why did the model predict X for this SKU/store." Nubi does no ML modelling. Not to be confused with **metric** explainability (why a number moved), which **is** in scope and owned by Nubi — see §A `POST /metrics/{id}/explain`. |
| Retail cockpit / domain-specific engines | ⛔ | Host application logic |
| Host app infra (CI/CD, Docker, deploy) | ⛔ | Host's platform |
| Billing / Paystack / metered wallet | ⛔ (CE) | EE-only; not in the open-core/embeddable substrate |

> **Metric vs. model explainability — the boundary.** Nubi owns **metric**
> explainability: `POST /metrics/{id}/explain` decomposes *why a metric number
> moved* across dimensions (per-member delta / share / explanatory_power /
> coverage). It is pure math over the semantic layer — no model is involved.
> **Model** explainability (per-prediction feature attribution / SHAP on the
> host's own predictive models) stays host-side; Nubi does no ML modelling.
>
> The one nuance: the generic **compute kernel** (sandboxed Python) is available
> to hosts as a domain-agnostic **bring-your-own-model attribution runner** —
> submit Python + numeric Arrow arrays + a serialized model blob, get attribution
> values back. See [compute-kernel-attribution-runner](docs/compute-kernel-attribution-runner.md).
> Offering this runner does **not** cross the "no ML modelling" boundary: it
> carries no domain semantics and never stores or interprets the model.

## K. Capability accuracy notice (advertised vs. implemented)

The following EE tier features, network modes, and kernel providers appear in
internal code as **forward-compat stubs** but are **not yet shipped**. They have
been removed from the advertised/public surface (tier API response, config docs,
schema enum) as of 2026-06-26 to avoid misleading hosts.

| Feature | Previous advertised state | Current state | Roadmap |
|---|---|---|---|
| `has_sso_saml` (SAML IdP) | Tier flag exposed in `/ee/billing/tier` | Removed from API response; internal `TierLimits` skeleton retained | 🗓️ Roadmap |
| `has_scim` (SCIM provisioning) | Tier flag exposed in `/ee/billing/tier` | Removed from API response; internal skeleton retained | 🗓️ Roadmap |
| `has_white_label` (white-label rendering) | Tier flag exposed in `/ee/billing/tier` | Removed from API response; internal skeleton retained | 🗓️ Roadmap |
| Network mode `ssh_tunnel` | Listed as valid mode in schema docstring, returned 501 | Internal defensive stub; removed from advertised schema enum | 🗓️ Roadmap |
| Network mode `psc` (Private Service Connect) | Listed as valid mode in schema docstring, returned 501 | Internal defensive stub; removed from advertised schema enum | 🗓️ Roadmap |
| Network mode `cloudsql_proxy` | Listed as valid mode in schema docstring, returned 501 | Internal defensive stub; removed from advertised schema enum | 🗓️ Roadmap |
| Remote kernel provider `modal` | Listed as selectable in `KERNEL_REMOTE_PROVIDER` config | Stub only — `ModalRunner.run()` always 503s; removed from config docs | 🗓️ Roadmap |

**Network modes available today**: `direct` and `bridge` (async proxy via Nubi bridge agent).
**Remote kernel provider available today**: `e2b` (E2B Firecracker microVMs).
**EE feature flags available in tier API today**: `has_rls`, `has_sso_google`,
  `has_multi_tenant_workspaces`, `has_byoc`, `has_custom_domain`, `has_warehouse`,
  `has_priority_support`, SLA fields.

---

## Keeping these current

Long-term, this matrix and the changelog are part of the definition-of-done:

1. **Every PR that adds/changes a host-visible capability** updates the relevant
   row here (status + contract + docs link) **and** adds an entry to
   [CHANGELOG.md](./CHANGELOG.md) under `[Unreleased]`.
2. Status transitions (🗓️→🟡→✅) are the signal hosts watch — a capability is
   only ✅ when its public route/component exists, is documented, and is covered
   by tests.
3. On release, the `[Unreleased]` changelog section is stamped with a version +
   date; this file's "Last reviewed" line is bumped.
4. The strategic build sequence lives in [ROADMAP.md](./ROADMAP.md). This
   file is the **capability contract** for hosts/integrators.
