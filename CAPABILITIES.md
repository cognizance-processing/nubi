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

_Last reviewed: 2026-07-01 · branch `docs/full-coverage`._

---

## A. Data access & semantic layer

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Registered queries + typed `{{params}}` | ✅ | `POST /query`, `/query/registry` | [queries-and-params](docs/queries-and-params.md) |
| Governed semantic metrics — serving | ✅ | `POST /metrics/{id}/query` | [metrics-reference](docs/metrics-reference.md) |
| Metric authoring (derived/ratio, time-intel, top-N) | ✅ | `/metrics` CRUD (`author:metric`) | [semantic-and-data-apps](docs/semantic-and-data-apps.md) |
| Pre-aggregations / rollups (transparent routing) | ✅ | auto, query-log mined | [pre-aggregations](docs/pre-aggregations.md) |
| Connectors (Postgres, DuckDB, MySQL, JDBC, HttpJson, BYO) | ✅ | `/connectors` | [connectors](docs/connectors.md) |
| **Native GCS connector (`gs://` via DuckDB `TYPE gcs`)** | ✅ | `duckdb_storage` connector; HMAC key pair or ADC | [connectors](docs/connectors.md#native-google-cloud-storage-gcs-connector) |
| **Column profiling (`null_rate` / `distinct_count` / `min` / `max` per column)** | ✅ | `GET /datasets/{id}/profile` | [connectors](docs/connectors.md#column-profiling) |
| **File ingestion + auto-DDL** — SFTP/FTP/bucket sources (incl. zip) normalized to Parquet and loaded into any target connector, auto-registering/evolving the target's schema contract | ✅ | `file_ingest` flow task kind; `connector_write` is the write-side sibling | [flows](docs/flows.md#task-kinds-under-the-hood) |
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
| **Nightly schema-drift sweep** (guaranteed cadence, not just on-query) | ✅ | `kind: drift_sweep` scheduled job via `POST /jobs` | [data-health § Nightly drift sweep](docs/data-health.md#nightly-drift-sweep-guaranteed-cadence) |

## D. Transformation, flows & environments

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Cell-based flows (SQL/Python/Note), DAG + notebook views | ✅ | `/flows` | [flows](docs/flows.md) |
| **`http_call` flow task** — SSRF-guarded outbound HTTP from a flow, auth via org secret-ref, org allowlist | ✅ | `kind: http_call` in FlowSpec; fails run on non-2xx | [flows](docs/flows.md#http_call-outbound-http-requests) |
| **`assert` flow task** — `row_count` / `not_null` / `unique` / `custom_sql` expectations; fails run on violation | ✅ | `kind: assert` in FlowSpec | [flows](docs/flows.md#assert-data-quality-expectations) |
| Flow spec **version history + revert** | ✅ | `GET /flows/{id}/versions`, `POST /flows/{id}/revert/{v}` | [transformation](docs/transformation.md) |
| Environment list + watermarks (read) | ✅ | `GET /flows/{id}/environments` | [transformation](docs/transformation.md) |
| Environment pin/create (write) | 🟡 | via `/environments` routes (not under `/flows`) | [transformation](docs/transformation.md) |
| Backfill (write) | ✅ | `POST /flows/{id}/backfill` (`author`/writer) | [transformation](docs/transformation.md) |
| Run **params snapshot** for audit/reproducibility | ✅ | returned by `GET /flows/runs/{id}` & `/flows/{id}/runs` (`params_snapshot`, `code_version`, `seed`) | [transformation](docs/transformation.md) |
| SQL transpilation (19 dialects) | ✅ | `POST /transpile` | [transformation](docs/transformation.md) |

## E. Version control (git)

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Bind project to GitHub/GitLab repo | ✅ | `POST /git/connect` | [git-sync](docs/git-sync.md) |
| **Live** remote push (real `git push`, optional PR/MR) | ✅ | `POST /git/push` (project-scoped) | [git-sync](docs/git-sync.md) |
| **Live** remote pull (real `git fetch` → import) | ✅ | `POST /git/pull` (project-scoped) | [git-sync](docs/git-sync.md) |
| Files-as-code round-trip (CLI) | ✅ | `nubi pull/push/deploy/diff` | [files-as-code](docs/files-as-code.md) |
| **Watches as code via `nubi apply`** | ✅ | `nubi apply`→`POST /api/v1/apply` (`kind: watch`, idempotent, org-scoped) | [files-as-code § Watches as code](docs/files-as-code.md#d2-watches-as-code) |
| **`watch_breach` labels passthrough** | ✅ | `emit_watch_breach` → webhook `data.labels` map | [observability § labels](docs/observability.md#watch_breach-labels-passthrough) |

> The legacy **org-level** git flow was a no-network stub; it is superseded by
> the **project-scoped** endpoints above, which make real authenticated network
> calls (token delivered via `GIT_ASKPASS`, never in argv).

## F. AI, chat & MCP

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

## G. Embedding (web-component kit)

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Framework-agnostic web components (7) | ✅ | `<nubi-dashboard/kpi/table/chart/query-editor/health>` | [embed-api](docs/embed-api.md) |
| Per-viewer JWT (RS256/ES256), token-locked params | ✅ | `get-token` bridge | [embedding](docs/embedding.md) |
| 25-token theme contract, cross-filter bus, scope gating | ✅ | `NubiContext` | [embed-api](docs/embed-api.md) |
| **Per-org rate limiting + embed exemption** — token-bucket keyed by verified org; verified embed tokens exempt on metric/query read paths | ✅ | `middleware/ratelimit.py`; `NUBI_RATELIMIT_QUERY_RPM` (default 120) | [embedding](docs/embedding.md#rate-limiting-and-embed-exemption) |

## H. Audit & governance reads

| Capability | Status | Contract | Docs |
|---|---|---|---|
| Scoped audit: query-executed (POPIA), export audit | ✅ | webhook event / board config | [observability](docs/observability.md) |
| **Unified action audit-log read API** (all mutations) | ✅ | `GET /audit`, `GET /audit/{type}/{id}` (owner/admin) | [api-reference](docs/api-reference.md#audit-log) |
| **Audit backstop middleware** — guaranteed 2xx-mutation coverage even for routes with no explicit `record_audit` call site; dedupes against explicit calls via `request.state.audit_logged` | ✅ | `app/middleware/audit.py` (`AuditMiddleware`), registered unconditionally in `create_app()` | [observability § Guaranteed mutation coverage](docs/observability.md#guaranteed-mutation-coverage-the-audit-backstop-middleware) |
| Spec version/revert for **boards** | ✅ | `/environments` `/versions/board/...` | [transformation](docs/transformation.md) |
| Spec version/revert for **metrics** | ✅ | `GET /metrics/{id}/versions`, `GET /metrics/{id}/versions/{v}`, `POST /metrics/{id}/revert/{v}` (`author:metric`) | [metrics-reference](docs/metrics-reference.md#spec-version-history-revert) |

## I. Explicitly out of scope (stays with the host)

| Area | Status | Why |
|---|---|---|
| MDM / probabilistic entity matching (Splink, embeddings) | ⛔ | Not BI; host's data-mastering concern |
| ML demand forecasting / price elasticity | ⛔ | Domain modelling, not the semantic/serving layer |
| **Model / predictive explainability** — SHAP / per-prediction feature attribution, metric root-cause contribution decomposition | ⛔ | Data-science tooling, not embedded dashboard BI. Attribution over the **host's own** models (demand forecast, price elasticity, MDM match-scoring) — "why did the model predict X for this SKU/store" — stays host-side; Nubi does no ML modelling and does not compute contribution/decomposition breakdowns itself. |
| Retail cockpit / domain-specific engines | ⛔ | Host application logic |
| Host app infra (CI/CD, Docker, deploy) | ⛔ | Host's platform |
| Billing / Paystack / metered wallet | ⛔ (core) | Nubi Cloud-only; not in the open-source/embeddable substrate |

> **Explainability — fully out of scope.** Nubi does not compute *why a metric
> number moved* (root-cause/contribution decomposition) or *why a model predicted
> X* (SHAP / per-prediction feature attribution). Both are data-science surfaces,
> not embedded dashboard BI, and stay host-side.
>
> The one nuance: the generic **compute kernel** (sandboxed Python) is available
> to hosts as a domain-agnostic **bring-your-own-model attribution runner** —
> submit Python + numeric Arrow arrays + a serialized model blob, get attribution
> values back. See [compute-kernel-attribution-runner](docs/compute-kernel-attribution-runner.md).
> Offering this runner does **not** cross the "no ML modelling" boundary: it
> carries no domain semantics and never stores or interprets the model.

## J. Capability accuracy notice (advertised vs. implemented)

The following Nubi Cloud tier features, network modes, and kernel providers appear in
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
**Feature flags available in tier API today**: `has_rls`, `has_sso_google`,
  `has_multi_tenant_workspaces`, `has_byoc`, `has_custom_domain`, `has_warehouse`,
  `has_priority_support`, SLA fields.

---

## K. Nubi Cloud billing (Cloud-only)

These capabilities live in the `ee/` tree — which ships open in every clone
of this repo — but only **activate** on Nubi Cloud, where Nubi's own
infrastructure sets the internal `NUBI_LICENSE_KEY` operations switch (see
§I — billing itself is **out of scope for the open-source/embeddable
substrate** other self-hosters embed against; there is no self-hosted paid
tier or purchasable license). Listed here so the same "shipped vs. roadmap"
contract applies to the Cloud product, not just the embeddable core.

| Capability | Status | Contract | Docs |
|---|---|---|---|
| 5-tier plan catalogue (Free/Starter/Team/Pro/Enterprise), unlimited seats at every tier | ✅ | `GET /pricing` (public), `GET /ee/billing/tier` | [billing-and-usage](docs/billing-and-usage.md), [billing-model](docs/billing-model.md) |
| Paystack checkout + webhook (subscription + wallet) | ✅ | `POST /ee/billing/checkout`, `POST /ee/billing/webhook` (HMAC-SHA512 verified) | [billing-and-usage](docs/billing-and-usage.md#upgrading-or-changing-your-plan) |
| Usage wallet — manual top-up, ledger, balance | ✅ | `GET /ee/billing/wallet`, `POST /ee/billing/wallet/topup` | [billing-and-usage § usage wallet](docs/billing-and-usage.md#the-usage-wallet) |
| **Wallet auto-topup** (threshold / amount / monthly caps, saved-card charge, in-flight guard, 3DS pause handling) | ✅ | `PUT /ee/billing/wallet/autotopup`; `app/ee/billing/wallet.py` | [billing-and-usage § Auto top-up](docs/billing-and-usage.md#auto-top-up), [billing-model § Usage Wallet](docs/billing-model.md#usage-wallet) |
| **USD-anchored, ZAR-billed FX** (daily refresh, 2% buffer, ceil-to-nearest-R10, staleness fallback, disclosed variance) | ✅ | `app/ee/billing/fx.py`; rate surfaced in `GET /pricing` and the wallet card | [billing-and-usage § Prices in USD, billed in ZAR](docs/billing-and-usage.md#prices-in-usd-billed-in-zar), [billing-model § Currency and FX](docs/billing-model.md#currency-and-fx) |
| Bytes-scanned metering + free allowance (replaces the old warehouse CU multiplier) | ✅ | `kind="query_scan"` usage events; `SCAN_ZAR_PER_TIB` / `SCAN_FREE_ALLOWANCE_TIB` in `tiers.py` | [billing-and-usage § What we meter](docs/billing-and-usage.md#metered), [architecture-and-economics](docs/architecture-and-economics.md) |
| Monthly invoices — PDF render, email delivery, VAT (TAX INVOICE when issuer is VAT-registered) | ✅ | `GET /ee/billing/invoices`, `GET /ee/billing/invoices/{id}/pdf` | [billing-and-usage § Monthly invoices, PDFs & VAT](docs/billing-and-usage.md#monthly-invoices-pdfs-vat) |
| Billing-cycle reconciliation (idempotent close, wallet-first draw-down, deterministic charge reference) | ✅ | `run_billing_cycle()`; `GET /ee/billing/invoices/current-cycle` (dry-run projection) | [billing-model](docs/billing-model.md) |
| Quota enforcement (hard stop where no overage rate; wallet-billable overage otherwise) | ✅ | `app.ee.billing.quota.billing_quota_checker` registered into the core `enforce_quota` hook | [billing-model § Resource Limits by Tier](docs/billing-model.md#resource-limits-by-tier) |
| **LLM token-passthrough billing** — real-time per-call wallet charge (cost + `NUBI_TOKEN_MARKUP_PCT`) for AI tokens beyond the tier's free monthly allowance; replaces the flat per-call `ai_calls` overage rate | ✅ | `app/ee/billing/token_billing.py`; `app.features.meter_ai_usage` hook; `TierLimits.max_ai_tokens_per_month` | [ai-and-mcp § Token-passthrough billing](docs/ai-and-mcp.md#token-passthrough-billing-ee) |
| **BYO AI provider keys** (paid tiers only) — AES-256-GCM encrypted, org calls route through the org's own vendor key and are never wallet-charged | ✅ | `POST`/`DELETE /ai/keys`; `GET /ai/providers`; `app/ee/billing/org_ai_keys.py` | [ai-and-mcp § Token-passthrough billing](docs/ai-and-mcp.md#token-passthrough-billing-ee) |
| Internal tier resolution for Cloud environments (`NUBI_LICENSE_KEY` → Free/Pro/Enterprise; set only by Nubi's own infra, not a customer-facing license) | ✅ | `app.ee.licensing.license.get_license()` | [open-core § NUBI_LICENSE_KEY resolution](docs/open-core.md#nubi_license_key-resolution) |

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
