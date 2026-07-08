# Changelog

All notable, host-visible changes to Nubi are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/); this project tracks capability
state in [CAPABILITIES.md](./CAPABILITIES.md).

Conventions:
- Group entries under **Added / Changed / Fixed / Security / Deprecated / Removed**.
- Every PR that changes a host-visible capability adds an entry here under
  `[Unreleased]` and updates the matching row in [CAPABILITIES.md](./CAPABILITIES.md).
- On release, stamp `[Unreleased]` with a version + date.

## [Unreleased]

### Security

- **Chat cost-DoS limits.** Three independent guards now protect all chat and
  AI endpoints (`/chat/stream`, `/ai/chat`, `/ai/chat/stream`, `/ai/ask`,
  `/ai/dashboard`, `/ai/sql`, `/ai/canvas`, `/ai/canvas/edit`) from runaway
  LLM spend and resource exhaustion:
  1. **Rate limit** — a dedicated `chat` rate-limit class
     (`NUBI_RATELIMIT_CHAT_RPM`, default 20 rpm, burst 1.5×, Redis-backed)
     returns `HTTP 429 + Retry-After` when the cap is hit. Separate from the
     existing `query` and `auth` buckets.
  2. **Aggregate per-turn token budget** — the dashboard-editor chat loop
     (`app/chat/llm.py`) and the AI agent loop (`app/ai/agent.py`) now track
     cumulative token usage across all steps in a turn and stop cleanly when
     `NUBI_CHAT_TURN_TOKEN_BUDGET` (default 16 000) is reached, emitting a
     `{"type": "error", "message": "Turn token budget … reached"}` SSE event
     on the streaming path or a synthesised reply on the non-streaming path.
  3. **Per-turn timeout** — SSE generators for `/chat/stream` and
     `/ai/chat/stream` are wrapped with `asyncio.wait_for`; on expiry a clean
     `{"type": "error", "message": "Turn timeout …"}` SSE event is emitted and
     the stream closes. The non-streaming `/ai/chat` returns `HTTP 504` with
     `{"error": {"code": "turn_timeout"}}`. Default `NUBI_CHAT_TURN_TIMEOUT_S`
     = 90 s.
  The NullProvider offline path is unaffected by the token budget (no real LLM
  spend). Existing step cap (`_MAX_STEPS=6`/`max_steps=8`) and per-call token
  cap (`_MAX_TOKENS=4096`) are preserved as inner guards.

### Added
- **`drift_sweep` scheduled job + audit backstop middleware.** Two
  guaranteed-coverage additions: (1) a `kind: "drift_sweep"` job (`POST
  /api/v1/jobs`) that re-checks every observed dataset's schema on a cron —
  the guaranteed-cadence sibling to the existing fire-and-forget,
  query-triggered drift detection — emitting the same `SCHEMA_DRIFT` webhook
  events; org-scoped, best-effort per dataset. (2) `AuditMiddleware`
  (`app/middleware/audit.py`), registered unconditionally in `create_app()`,
  which records every successful (2xx) mutating `/api/v1/*` request to the
  audit log even when the route has no explicit `record_audit()` call site —
  deduplicated against explicit calls via `request.state.audit_logged`, same
  POPIA-safe metadata-only contract, fail-open.
  See [data-health § Nightly drift sweep](docs/data-health.md#nightly-drift-sweep-guaranteed-cadence)
  and [observability § Guaranteed mutation coverage](docs/observability.md#guaranteed-mutation-coverage-the-audit-backstop-middleware).

- **Bytes-scanned billing (replaces the warehouse CU multiplier).** EE billing
  now meters actual bytes scanned by DuckDB (`kind="query_scan"` usage events)
  at `R83/TiB` with the first `1 TiB/org/month` free, instead of applying a 4×
  compute-unit multiplier to warehouse (heavy-query pool) queries.
  `WAREHOUSE_CU_MULTIPLIER` is now `1` (retained as a no-op skeleton for the
  `NUBI_CU_MULTIPLIER` env var) — warehouse queries are billed identically to
  standard queries via the shared bytes-scanned meter, so "warehouse vs.
  standard" no longer appears as a separate invoice line.
  See [billing-and-usage § What we meter](docs/billing-and-usage.md#metered)
  and [billing-model § Metered Dimensions](docs/billing-model.md#metered-dimensions).

- **`http_call` flow task.** Flows can now POST (or GET/PUT/PATCH/DELETE) to any
  external HTTP endpoint from inside a flow run. Config: `url`, `method`,
  `headers`, `body` (JSON, supports `{{ params }}`), `timeout_s`, and `auth`
  (kinds: `bearer` / `header` / `basic`, all via an org secret-ref). Security:
  SSRF-guarded via DNS-rebinding-safe IP-pinning; optional org-level
  `http_call_allowed_hosts` allowlist in `runtime_config`. Fails the run on
  non-2xx. Response capped at 2 KB in the run record.
  See [flows § http_call](docs/flows.md#http_call-outbound-http-requests).

- **`assert` data-quality flow task.** A new `assert` task kind runs
  data-quality expectations against a table or query result and fails the run
  on any violation — the flows equivalent of a SQLMesh audit. Supported
  expectations: `row_count` (min/max/exact), `not_null` (per column),
  `unique` (single column or composite key), `custom_sql` (zero-row mode or
  scalar-boolean mode; `{{ target }}` placeholder). Runs through the same
  planner path as query cells, honouring RLS. Result payload names every
  failing expectation and its actual value.
  See [flows § assert](docs/flows.md#assert-data-quality-expectations).

- **Native GCS connector.** The `duckdb_storage` connector now handles
  `gs://` URIs via DuckDB's native `TYPE gcs` secret — no S3-compat
  `storage.googleapis.com` workaround required. Credential modes: HMAC key
  pair (`gcs_access_key_id` / `gcs_secret`) or Application Default Credentials
  (ADC) when both keys are empty, suitable for GCE/GKE Workload Identity. The
  connection is hardened identically to the S3 path: local FS access blocked,
  config frozen after secret registration.
  See [connectors § GCS](docs/connectors.md#native-google-cloud-storage-gcs-connector).

- **Column profiling (`GET /datasets/{id}/profile`).** New endpoint returns
  per-column statistics in a single DuckDB pass: `null_rate` (fraction of
  NULLs), `distinct_count` (HyperLogLog approximate), `min` / `max` (cast to
  string), and `type` (DuckDB type string). Default sample cap: 100 000 rows
  (override via `?sample_rows=N` or `NUBI_PROFILE_SAMPLE_ROWS`). Works across
  local, S3, and GCS-backed datasets — credentials resolved from the dataset's
  connector config.
  See [connectors § Column profiling](docs/connectors.md#column-profiling).

- **Cross-model column lineage (`resolve_column_lineage`).** The lineage DAG
  now supports column-level provenance tracing across model layers.
  `resolve_column_lineage(dag, node_id, column, max_hops)` walks upstream
  following `SELECT col AS alias` renames at each hop, falls back to
  table-level edges for `SELECT *` layers (marked `select_star: true`), and
  has a cycle guard + depth ceiling (max 20 hops). Used internally by the
  auto-rebuild hook and the lineage panel.
  See [lineage § Cross-model column lineage](docs/lineage.md#cross-model-column-lineage).

- **Lineage-driven auto-rebuild (`runtime_config.auto_rebuild_downstream`).**
  Opt-in flag on a flow spec: when set to `true`, a successful run
  automatically enqueues all downstream dependent flows in the same org
  (lineage DAG traversal, up to 20 hops). Guards: org-scoped, cycle-safe
  (`_visited` set), storm-safe debounce, fan-out cap, success-only, best-effort
  (never fails the upstream run).
  See [lineage § Lineage-driven auto-rebuild](docs/lineage.md#lineage-driven-auto-rebuild).

- **Per-org rate limiting + embed token exemption.** The rate-limit middleware
  now keys token buckets on the **verified org** from the JWT (HS256
  first-party or RS256/ES256 embed), not on IP alone. A forged `org` claim
  triggers a verification failure and falls back to IP-keyed limiting. Verified
  embed tokens (`kind: "embed"`) are fully exempt from the per-org query bucket
  on metric and registered-query read paths (`POST /metrics/{id}/query|sql`
  and `POST /query`/`/query/*`) so cockpit dashboards can fire tile queries
  concurrently without hitting the cap. First-party tokens on those paths remain
  subject to the per-org bucket.
  See [embedding § Rate limiting](docs/embedding.md#rate-limiting-and-embed-exemption).

- **Watches as code via `nubi apply`.** `watches/*.yaml` files in a bundle
  directory are now registered by `nubi apply` (`POST /api/v1/apply`) alongside
  metrics, dashboards, queries, and flows. The operation is idempotent (stable
  key: org-scoped slug of `name`) and best-effort per-resource (one failing
  watch does not abort the rest). Schema: `name` (required), `metric_id`
  (required), a `threshold`, `comparison`, or `change` rule (required),
  optional `config` block, and optional `labels` map.
  See [files-as-code § Watches as code](docs/files-as-code.md#d2-watches-as-code).
- **`watch_breach` labels passthrough.** The `watch_breach` webhook payload
  now includes a `labels` field — an arbitrary host-supplied key-value map
  declared per watch and passed through verbatim in `emit_watch_breach`. Labels
  are stored in `watches.config.labels` (JSONB) and are never interpreted by
  the server. Use them to correlate breach events with your own domain objects
  (e.g. `{"category_id": "..."}`) without a secondary API call.
  See [observability § labels](docs/observability.md#watch_breach-labels-passthrough).
- **Scope-resolution endpoint — `GET /auth/scope`.** Resolves the caller's
  effective RLS scope from the **verified token only** (first-party AND embed
  tokens): returns raw `policies`, hierarchy-expanded + grant-merged
  `effective_policies` (`{dimension: [values]}`), `scope`, `org`, and an
  `expanded` flag. Org-scoped and fail-closed (returns narrower raw policies on
  any resolution error — never widens).
- **Access-grants store — `/access-grants`.** Wires the existing `access_grants`
  table (migration 0022) to org-scoped CRUD: `GET` (list for a subject), `POST`
  (create), `DELETE /{id}`. Writes are gated to owner/admin; cross-org ids return
  404 (not 403). Non-expired grants for the caller's subject are merged into
  `GET /auth/scope`'s `effective_policies` (token policies ∪ stored grants).
  Optional companion to host-minted token claims (`app/access/grants_store.py`).
- **Metric spec version history + revert.** Every metric create or update
  snapshots the spec as an immutable version. New routes:
  `GET /metrics/{id}/versions` (list, newest first),
  `GET /metrics/{id}/versions/{v}` (full spec at that version),
  `POST /metrics/{id}/revert/{v}` (restore + record as new version; requires
  `author:metric`). Mirrors the flow versioning pattern. Migration:
  `0023_metric_spec_versions.sql`.
- **Schema-drift detection.** Column-level schema change detection for all
  observed datasets: added, removed, and type-changed columns are detected
  automatically after each query execution (best-effort, fire-and-forget — never
  blocks the query path). Read surfaces: `GET /health/drift` (org-level, with
  optional `?dataset_key=` filter) and `GET /health/drift/{dataset_key}`
  (per-dataset history + current snapshot). Emits a `SCHEMA_DRIFT` outbound
  webhook event on every detected change. First observation stores a baseline
  snapshot with no events. Migration `0024_schema_drift.sql`.
- **Unified action audit-log.** Org-scoped `audit_log` table (migration
  `0025_audit_log.sql`) records metadata-only entries for every mutation:
  create/update/delete on boards, queries, datastores, widgets, canvases,
  connectors, MCP servers, and secrets. A central `record_audit()` writer
  (`app/audit.py`) is fire-and-forget — a DB write failure never breaks the
  mutation path. Read API: `GET /audit` (paginated; filter by
  `resource_type`/`action`/`actor`/`since`/`until`) and
  `GET /audit/{resource_type}/{resource_id}` — both gated to owner/admin
  (approver role); unauthenticated 401, non-approver 403; cross-org isolation
  enforced (org_id always from the verified token). POPIA-safe: `summary` holds
  metadata only — no row data, SQL literals, PII, or credentials.
- **MCP — full integration.** Nubi as an MCP server (`POST /mcp`, JSON-RPC
  `initialize`/`tools/list`/`tools/call`, ~14 built-in tools) **and** a per-org
  external MCP server registry (`/mcp/servers` CRUD) so an embedding host can
  register its own domain action tools (e.g. `create-task`, `launch-campaign`).
  Host tools are discovered and called inside the agent/chat loop, namespaced as
  `serverName.toolName`. Outbound calls are SSRF-guarded with DNS-rebind pinning;
  auth secrets are AES-256-GCM encrypted at rest and never returned by read APIs.
- **Data health.** Freshness registry (`GET /health/freshness`,
  `/health/freshness/{key}`), weighted health score (`/health/score`), estate
  graph (`/health/estate`). Freshness is populated by a flow-success listener;
  reads work via embed and first-party tokens.
- **Lineage.** Dependency DAG (`GET /lineage/dag`, `/lineage/dag/{node}`) and
  metric lineage (`GET /metrics/{id}/lineage`).
- **Transformation.** Flow spec version history + revert
  (`/flows/{id}/versions`, `/flows/{id}/revert/{v}`), backfill
  (`POST /flows/{id}/backfill`), environment list, and SQL transpilation across
  19 dialects (`POST /transpile`).
- **Governance.** Range (`gte/gt/lte/lt`) and list (`IN`) RLS predicate shapes
  alongside scalar equality, plus hierarchical parent→child policy expansion —
  now **auto-applied in the live `/query` and `/metrics/{id}/query` paths**
  (org-scoped, fail-closed, zero-cost when no hierarchy is configured). All
  passable via embed-token `policies` claims.
- **Embed kit.** Two new web components — `<nubi-lineage>` (dependency DAG) and
  `<nubi-health>` (score gauge + freshness). Now 9 components total.
- **App.** First-party SPA gains top-level **Overview** (workspace stats + data
  health) and **Workqueue** (attention inbox) pages; `/explore` dogfoods the
  embed components live against the session token.
- **Audit/reproducibility.** Flow-run `params_snapshot` (plus `code_version`,
  `seed`) is now returned by `GET /flows/runs/{id}` and `GET /flows/{id}/runs`.
- **Docs & tracking.** `docs/mcp.md`, `docs/lineage.md`, `docs/data-health.md`,
  `docs/transformation.md`, `docs/governance.md`; new endpoints documented in
  `docs/api-reference.md`; this `CHANGELOG.md` and `CAPABILITIES.md`.

### Changed
- First-party access tokens now encode the granted `scope` claim explicitly
  (previously applied only as a verify-time default) so client-side scope gating
  in the embed components reflects real authoring scopes. Signature and effective
  server-side scope are unchanged.
- Demo seed now provisions representative `dataset_freshness` rows so the Overview
  health panel and `<nubi-health>` show real signals out of the box.

### Fixed
- `POST /mcp/servers` with an `auth_token` returned an opaque **500** when the
  secret-encryption key was unconfigured; it now returns a clear **503**
  (`secret_encryption_unconfigured`).
- `<nubi-lineage>` / `<nubi-health>` no longer hang on a "Loading…" spinner with
  no backend — they render sample data (or a clean error) with a bounded fetch.
- `<nubi-lineage>` graph no longer clips nodes; it fits/scrolls within its
  container with type-coloured nodes.

### Security
- **RLS policy cardinality cap.** New setting `NUBI_RLS_MAX_POLICY_VALUES`
  (default 5000) caps the number of values a single RLS policy may resolve to —
  both an explicit IN-list and the output of hierarchy expansion. Over-cap
  policies **fail closed** with `AppError("rls_policy_too_large", 400)` rather
  than silently truncating (which would widen access) or emitting an unbounded
  IN list. Enforced in `_make_in_predicate` / `expand_rls_policies`.
- MCP outbound HTTP is pinned to the SSRF-validated resolved IP (closes a
  DNS-rebinding window); MCP `tools/call` takes scope from the verified token
  (no privilege escalation) and gates raw SQL on `author:sql`.
- Host-mode (claim-native) tokens are stripped to read-only scope, so a
  misconfigured host issuer cannot mint write/admin/raw-SQL capability.

### Notes
- All capabilities previously flagged as roadmap are now shipped (metric versioning,
  schema-drift detection, unified audit-log). Remaining 🟡 in
  [CAPABILITIES.md](./CAPABILITIES.md): flow-scoped environment *write* aliases
  (env write is available today under the `/environments` routes).

---

_Earlier history predates this changelog; see `ROADMAP.md` (build sequence
M0–M22) and git history for milestones before this file was introduced._
