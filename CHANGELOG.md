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

### Added
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
  `<nubi-health>` (score gauge + freshness). Now 10 components total.
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
- MCP outbound HTTP is pinned to the SSRF-validated resolved IP (closes a
  DNS-rebinding window); MCP `tools/call` takes scope from the verified token
  (no privilege escalation) and gates raw SQL on `author:sql`.
- Host-mode (claim-native) tokens are stripped to read-only scope, so a
  misconfigured host issuer cannot mint write/admin/raw-SQL capability.

### Notes
- **Not yet shipped (tracked in [CAPABILITIES.md](./CAPABILITIES.md)):** active
  schema-drift detection/event API; a consolidated cross-mutation audit-log read
  API; spec version/revert for metrics (boards already have it via environments).

---

_Earlier history predates this changelog; see `ROADMAP.md` (build sequence
M0–M22) and git history for milestones before this file was introduced._
