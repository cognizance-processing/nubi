# Security Review — Wave-4 Adversarial Hardening

Date: 2026-06-24  
Scope: `feat/embed-bi-substrate` (Waves 1–3 merged into `main`)

---

## 2026-06-24 — Custody-tier adversarial audit (ingest / export / CMEK / cache / creds)

Independent adversarial pass over the data-custody surface
(`backend/app/routes/ingest.py`, `routes/lake_export.py`,
`lakehouse/ingest_session.py`, `lakehouse/staging.py`, `lakehouse/dedicated.py`,
`lakehouse/cmek.py`, `lakehouse/custody.py`, `connectors/cache_encryption.py`,
`connectors/duckdb_storage.py`, `connectors/duckdb_conn.py`).

### Findings

| Area | Severity | Exploit | Fix | Test |
|------|----------|---------|-----|------|
| **Export `sql` path — arbitrary file read + cross-tenant exfiltration** | **HIGH** | `POST /lake/{id}/export` (and async `/export/jobs`) accepts a `sql` SELECT that is executed verbatim inside `COPY (<sql>) TO …` on an **un-hardened in-memory DuckDB** connection that holds the central-lake credentials and (for a local-file managed lake, via `for_memory()`) full host-FS access. A custody tenant could submit `SELECT * FROM read_csv('/etc/passwd')`, `SELECT * FROM read_parquet('file:///managed/orgs/<OTHER_ORG>/lake/**')`, or `read_parquet('s3://other-tenant-bucket/**')` to read host files or another org's lake using the shared credentials. The prior `_validate_export_sql` only blocked non-SELECT statements — `read_*`/`glob`/`*_scan` table functions inside a SELECT passed. | **FIXED** in `routes/lake_export.py`: added `_FILE_ACCESS_FUNC_RE` denylist to `_validate_export_sql`, rejecting `read_parquet`, `parquet_scan`, `read_csv(_auto)`, `read_json/ndjson`, `read_text`, `read_blob`, `read_xlsx`, `st_read`, `glob`, `parquet_metadata/schema`, `sniff_csv`, `csv_scan`, `iceberg_scan`, `delta_scan`, `postgres_scan/query`, `sqlite_scan/query`, `mysql_scan/query` in caller SQL (case-insensitive, function-call form only). Lake data is exported via `table=` whose `read_parquet` glob is built **server-side** from the org-pinned prefix — never from caller SQL — so no intended capability is lost. The guard is shared by the sync route and the async worker (both call `_validate_export_sql`). | `tests/security/test_export_security.py::test_file_access_functions_rejected` (11 payloads), `::test_file_access_blocked_end_to_end`, `::test_async_enqueue_rejects_file_access_sql`, `::test_safe_select_passes` (no false positives) |
| Ingest partition / relpath / table-name traversal | — (verified safe) | Producer-supplied `partition`, part `relpath`, and `table_name` could in theory escape the lake prefix. | Verified: `_validate_partition` / `_validate_relpath` reject `.`/`..`/absolute/disallowed-char segments; `_build_promote_callable._final_key` re-normalises and asserts the key stays under the server-pinned prefix; `StagingArea._key` strips `..` independently. No change. | `test_ingest_security.py::test_partition_traversal_rejected` (9), `::test_part_relpath_validator_rejects_traversal` (8), `::test_table_name_traversal_rejected` |
| `full_replace` sweep nuking sidecars / other tables | — (verified safe) | The full_replace sweep could delete `_nubi/` sidecars or another table's objects. | Verified: sweep scopes to `<prefix><table>/` and explicitly excludes `k.startswith(_nubi_prefix)`. No change. | `::test_full_replace_preserves_sidecars_and_other_tables` |
| Ingest CAS / cross-org IDOR | — (verified safe) | Race two commits into an invalid state; org A reads/transitions org B's session. | Verified: `transition(from_state=…)` is a CAS (single winner); every store op gates on `org_id`+`datastore_id`. No change. | `::test_cas_transition_single_winner`, `::test_cross_org_store_get_returns_none`, `::test_cross_org_session_get_is_404` |
| Export dest-overwrite / table-name injection | — (verified safe) | dest_uri inside the source lake; table-name SQL injection. | Verified: `_validate_dest_not_in_source` (prefix-safe both directions), `_validate_table_name` (strict charset). No change. | `test_export_security.py::test_dest_inside_source_rejected`, `::test_table_name_injection_rejected` |
| Export job IDOR + `dest_creds_ref` leak | — (verified safe) | Org A reads org B's job; status endpoint echoes the secret-store key. | Verified: `get_job` is org-scoped + URL-datastore double-scoped; status response strips `dest_creds_ref`. No change. | `::test_export_job_idor_and_creds_ref_redacted`, `::test_export_job_wrong_datastore_is_404`, `test_secrets_creds.py::test_dest_creds_ref_never_returned` |
| Export worker claim CAS | — (verified safe) | Two workers claim the same queued job. | Verified: `claim_job` only transitions `queued→running` (single winner). No change. | `::test_worker_claim_cas_single_winner` |
| CMEK client-mode fail-closed | — (verified safe) | Client-mode CMEK silently writes app-encrypted blobs DuckDB reads as raw Parquet (corruption). | Verified: `assert_cmek_readable("client")` raises 501 and is called at provision, ingest open/upload/commit, export, tables-list. No change. | `test_cmek_cache.py::test_client_cmek_fails_closed_on_*` (3), `::test_assert_cmek_readable_blocks_client`, `::test_client_cmek_misconfig_fails_closed` |
| Cache encryption cross-tenant replay / tamper | — (verified safe) | Copy org A's ciphertext into org B's slot; tamper bytes; wrong key → plaintext fallback. | Verified: AES-256-GCM with the scoped cache key as AAD; cross-tenant replay/tamper/wrong-key → `InvalidTag` → cache MISS (never wrong data, never 500); misconfigured key → `ValueError` at construction (no plaintext fallback). No change. | `::test_cache_cross_tenant_replay_fails`, `::test_cache_tampered_ciphertext_fails`, `::test_cache_wrong_key_fails`, `::test_cache_key_misconfig_rejected` |
| Custody gate fail-closed on every `/lake/*` route | — (verified safe) | Reach ingest/export/tables with custody OFF. | Verified: `assert_custody_enabled()` at the top of all 9 `/lake/*` handlers (and the async worker). No change. | `test_custody_gate.py` (9 routes) |
| BYO / secret-store creds leak + org-scoping | — (verified safe) | Deployer bucket creds returned by API; cross-org secret read. | Verified: `_row_with_usage` strips `aws_secret_access_key`; `SecretStore.get/delete` are org-scoped. No change. | `test_secrets_creds.py::test_row_with_usage_strips_aws_secret`, `::test_secret_store_is_org_scoped` |
| Regressions (embed raw-SQL 403, author:metric gate, webhook SSRF) | — (verified safe) | Prior-audit invariants. | Re-asserted at unit level. No change. | `test_regression.py` (embed/metric gate, `guard_url`/`resolve_and_pin` SSRF, export SQL guards) |

### Fixed vs documented

* **Fixed (1 HIGH):** the export `sql` file-access / cross-tenant exfiltration hole in `routes/lake_export.py`.
* **Documented / verified safe (everything else):** every other custody invariant was found correct and is now pinned by regression tests (118 new tests across 6 files).

### Residual risk

1. **Export `sql` defence-in-depth (LOW):** the fix is a SQL-text denylist on the caller's SELECT, not engine-level sandboxing. The `for_memory()` connector used for local-file lakes still has host-FS access at the engine level; a future hardening pass should additionally sandbox that connection (DuckDB `allowed_directories`/`enable_external_access=false` was attempted but is awkward to apply to an already-started in-memory DB in DuckDB 1.5.3 — it must be set at DB-start, which the current `for_memory()` factory does not support). The denylist is the correct, low-risk fix today; the table-export path (B/C) is already fully server-pinned and unaffected.
2. **`/lakehouse/provision` is not custody-gated (BY DESIGN, informational):** `POST /lakehouse/provision` provisions the OSS prefix-isolated managed lake, which is intentionally available without the custody tier (the *dedicated-bucket* provider only activates when `NUBI_CUSTODY_ENABLED=true`, via `lakehouse_provider_kind()`). The custody-gated routes are the `/lake/*` data-plane (ingest/export/tables). Not changed — gating provision would break OSS managed-lake usage.
3. Webhook DNS-rebinding on delivery (carried over from the 2026-06-24 Wave-4 section, LOW) — unchanged.

---

## Findings Summary

| # | Area | Severity | Exploit | Fix | Test |
|---|------|----------|---------|-----|------|
| B1 | Outbound Webhooks — SSRF at delivery time | **HIGH** | Register any webhook URL; on the next event delivery `deliver_one()` calls `httpx.AsyncClient().post(url)` with no host filter. Register `http://169.254.169.254/latest/meta-data/` to exfiltrate cloud instance credentials on every event. | Added `guard_url(url)` call at the top of `deliver_one()`. Any blocked URL logs + returns `False` immediately (never raises, preserving fire-and-forget contract). | `tests/security/test_sec_webhook_ssrf.py` — 7 tests |
| B2 | Outbound Webhooks — SSRF at registration time | **HIGH** | Stored malicious URLs are attempted on every future event. Even if B1 is fixed, old stored URLs survive. | Added `_validate_webhook_url()` helper in `router.py` calling `guard_url()`; called in `create_webhook` and `update_webhook` before any store write. | `tests/security/test_sec_webhook_ssrf.py` — 4 tests |
| B3 | Host-mode org claim — non-string type bypass | **MEDIUM** | If a host issues a token whose `org` claim is a JSON array (e.g. `["tenant_a"]`) or boolean (`True`), the old check `if not org_val or not str(org_val).strip()` passed because `str(["tenant_a"])` = `"['tenant_a']"` which is truthy. This would pin the org to a garbage string like `"['tenant_a']"` instead of a real UUID, effectively preventing DB org resolution. More serious if the downstream lookup accidentally matches a stored org with that stringified name (unlikely but theoretically exploitable in contrived scenarios). | Added `isinstance(org_val, str)` check before the strip check, in both the in-process registry path and the DB fallback path in `_maybe_pin_host_mode_org`. | `tests/security/test_sec_host_mode_org_claim.py` — 13 tests |

---

## Items Reviewed and Accepted (No Code Change)

### Area 1: Claim-Native Tenancy

**Non-host issuer cannot get host treatment.**  
`_maybe_pin_host_mode_org` only pins when `issuer_cfg.host_mode` is `True`. The in-process registry path checks this flag explicitly before reading the org claim. The DB fallback path similarly checks `db_row.get("host_mode", False)`. A non-host-mode issuer token never reaches the pin logic.

**Cross-issuer tenant hop.**  
The DB fallback in `_verify_embed_token_async` uses the token's unverified `org` claim to scope the DB lookup to `get_enabled_by_iss(org_from_token, iss)`. The token is then fully verified against the JWKS found for that `(org, iss)` pair. An attacker cannot use org A's claim to resolve org B's issuer and get org B's key because the issuer lookup is scoped to the org in the token — and the signature must match that org's issuer key. Design correct.

**Missing/empty/array/object `org` claim.**  
Missing and empty: correctly rejected (403). Array/object: fixed by B3 above.

**Host-mode write/admin escalation.**  
The `host_mode_org_pin` contextvar is set from the JWT claim. The JWT must be signed by the registered host-mode issuer. The `scope` on those tokens is whatever the host includes — there is no additional scope stripping. **Residual risk (LOW, documented):** if a host-mode issuer mints a token with `author:sql` scope, it would pass the authoring scope gate for first-party raw SQL access on `/query`. However, the M3-SEC allowlist gate fires first for `kind="embed"` tokens (embed tokens must supply a `query_id`; raw SQL is unconditionally blocked before `has_scope(SCOPE_AUTHOR_SQL)` is ever checked). Embed host-mode tokens with `author:sql` scope therefore cannot execute raw SQL.

**ContextVar cross-request leak.**  
`host_mode_org_pin` and `api_key_org_pin` are `ContextVar` instances. FastAPI creates a new context copy per request via `asyncio.Task`. The contextvar is not explicitly reset between requests, but the copy-on-entry behavior of `ContextVar` (each task gets an inherited copy, mutations are local to that task) means values cannot leak between concurrent requests. ASGI frameworks also handle this correctly via `copy_context()`. No leak possible.

### Area 2: Authoring Scope Gating

**Is there ANY path to raw SQL without `author:sql`?**  
For `kind="access"` tokens: `/query` and `/query/estimate` both call `_resolve_request_plan` which checks `if not has_scope(_scopes, SCOPE_AUTHOR_SQL)` before using `body.sql`. The gate raises 403. There is no bypass.

For `kind="embed"` tokens: the allowlist gate at line 673–679 of `query.py` fires FIRST — before the `author:sql` check — and requires a `query_id`. Raw SQL is categorically refused for embed tokens, independent of scope.

**`author:*` wildcard.**  
`has_scope(["author:*"], "author:sql")` returns `True` because `"author:sql".startswith("author:")`. This is intentional: `author:*` is a super-scope granted only to full-access sessions (analysts/admins). Embed tokens never receive `author:*`. The scope check is sound.

**Estimate path.**  
`/query/estimate` calls `_resolve_request_plan` which enforces the same scope + allowlist gates. Confirmed it cannot bypass `author:sql` gating.

**Other raw-SQL entrypoints.**  
Searched across `app/routes/`. The only raw-SQL execution routes are `/query` and `/query/estimate`, both gated as described.

### Area 3: Templated Datastores

**Allowlist bypass.**  
Phase 1 of `TemplateResolver.resolve_template` validates ALL placeholder names against `CLAIM_ALLOWLIST` before any substitution. The regex `_PLACEHOLDER_RE` only matches `claims.<identifier>` syntax and does not match `{{ nested.attr }}` or `{{ claims.__class__ }}` (Python dunder names are valid identifiers but must appear in the allowlist). `__class__` is not in the allowlist. Rejected.

**Unicode / encoded characters in claim values.**  
The resolved value must match `^[a-zA-Z0-9_-]{1,128}$`. Unicode, spaces, special characters, null bytes, slashes, quotes — all rejected. Any claim value that fails this pattern raises `TemplateSecurityError` hard-stopped before the value reaches a connector.

**Cross-org claim resolution.**  
`EncryptedStoreResolver.resolve()` reads `org_id` either from the explicit constructor arg (set by the route handler from the verified org, not from the token) or from `claims.get("org")`. For the embed/host-mode path, `claims["org"]` is the token's verified org claim. The secret lookup is `store.get(datastore_id, org_id)` which is always org-scoped. Org A's resolver cannot return org B's secret.

**Resolver failure mode.**  
`ExternalSecretResolver` propagates exceptions without wrapping. `EncryptedStoreResolver` raises `ValueError` when no org_id is available. Both fail closed. The `except ImportError` in `query.py` is a graceful degradation for missing optional module, not a security bypass.

### Area 4: Outbound Webhooks (beyond SSRF)

**HMAC signing.**  
`sign()` computes `HMAC-SHA256` over `"{timestamp}.{body}"` (Stripe-style). `verify()` uses `hmac.compare_digest` for constant-time comparison. Correct.

**Secret at rest.**  
Secrets are encrypted via Fernet (`app.secrets.crypto.encrypt`) before storage. The `_public()` helper strips `secret_encrypted` from every read. `list_for_org`, `get_by_id` — both call `_public()`. `list_active_for_event` decrypts for delivery only. Secrets are never returned by the API.

**Replay mitigation.**  
The timestamp is included in the signed payload so hosts can implement a replay window (e.g. reject deliveries where `|now - ts| > 300s`). Nubi's delivery side does not enforce this — it is the host's responsibility to implement. Documented via the `X-Nubi-Timestamp` header contract.

**Slow/hostile webhook DoS.**  
`deliver_one()` uses `timeout_s=10.0`. The `max_attempts=4` is bounded. `deliver_to_org()` uses `asyncio.gather()` over endpoints — all fan-out is bounded. The BACKGROUND_TASKS set holds strong refs to prevent premature GC. Max theoretical blocking: 10s × 4 attempts × N active endpoints. In practice N is small for any org.

**Cross-org data leak.**  
`deliver_to_org()` takes `org_id` and calls `get_webhook_store().list_active_for_event(org_id, event_type)` — always org-scoped. Events from org A cannot reach org B's endpoints.

### Area 5: /explain

**RLS from token only.**  
The `/explain` route in `metrics.py` uses `verified_identity` dependency. Looking at the explain route: RLS policies come exclusively from `identity.policies` (not from the request body). The explain math in `explain.py` is pure Python with no DB queries — it operates on pre-fetched aggregates. No raw SQL exposure.

**Other-org metric → 404.**  
Metric resolution in `metrics.py` is org-scoped via the repo. A metric ID from another org returns None → 404.

**Caps.**  
`top_n` in `build_explain_result` defaults to 10 and is passed from the route. The audit-54 fix (commit `1c40ce1`) already added time-bound guards. `/explain` is gated by `verified_identity` and the route's own concurrency guard. No unbounded fan-out identified.

---

## Residual Risk

1. **DNS rebinding on delivery** (LOW): `guard_url` resolves at delivery time and fails open for NXDOMAIN. Between `guard_url` passing and `httpx.AsyncClient().post()` connecting, a zero-TTL DNS record could rebind to a private IP. Fully closing this requires `resolve_and_pin` (already in `app.connectors.ssrf`) on the delivery path. The current fix is defense-in-depth for the common case; a future hardening pass should swap `guard_url` for `resolve_and_pin` in `deliver_one`. Noted here rather than fixed to avoid changing the `AsyncClient` instantiation pattern (which would require connecting to a pinned IP literal, complicating TLS SNI).

2. **Host-mode write escalation via issuer scope policy** (LOW): A misconfigured host-mode issuer that mints tokens with write scopes could call write routes. Mitigation: the route layer's `require_writer_default` checks the user's DB role (org_members role), which a host-mode token bypasses (no org_members row). Future: add explicit scope stripping for host-mode tokens to `read:*` only. Not done here because the change is non-trivial and the threat requires the host operator to deliberately misconfigure their issuer.

3. **SSRF via webhook URL field in `schemas.py`** (informational): The `WebhookCreate.url` field is typed as `str` with no format validation beyond Pydantic's basic string handling. A `HttpUrl` Pydantic type would give free RFC-3986 normalization. Not changed because `guard_url` performs scheme validation, and adding `HttpUrl` would change the serialization behavior.

---

## 2026-06-26 — New Surface Adversarial Audit (MCP / Governance Grants / Transpile / Health / Lineage)

Independent adversarial pass over the new capability surface introduced in `feat/embed-bi-substrate`:
`app/ai/mcp.py`, `app/mcp/store.py`, `app/routes/mcp.py`, `app/connectors/rls_hierarchy.py`,
`app/routes/transpile.py`, `app/routes/health.py`, `app/routes/lineage.py`.

Threat model: **malicious tenant / compromised read-only token**.

### Findings

| # | Area | Severity | Exploit | Fix | Test |
|---|------|----------|---------|-----|------|
| C1 | **MCP `tools/call` — scope escalation via hardcoded `write:*`** | **HIGH** | `routes/mcp.py` built the claims dict for `execute_tool` with `"scope": ["read:*", "write:*"]` hardcoded, ignoring `identity.scope`. A read-only embed token or a restricted first-party token calling `POST /mcp` with `method=tools/call` would receive full write scope in the claims passed to every Nubi tool. Any write-guarded tool that checked `claims["scope"]` instead of route-level deps would be bypassed. | **FIXED** in `app/routes/mcp.py`: replaced hardcoded scope list with `list(identity.scope or [])` and set `"kind": identity.kind` so embed tokens retain their kind and cannot acquire first-party privileges. | `test_new_surface_audit.py::TestMCPNubiServerAuth::test_mcp_tools_call_uses_identity_scope_not_hardcoded` |
| C2 | **MCP `tools/call` — raw SQL via `_tool_run_query` bypassed `author:sql` gate** | **HIGH** | `_tool_run_query` in `app/ai/tools.py` accepted a raw `sql=` argument without checking `author:sql` scope in `claims`. Unlike `query.py` (which checks `has_scope(_scopes, SCOPE_AUTHOR_SQL)` before executing ad-hoc SQL), the tool path had no such gate. Any authenticated caller — including a read-only token — could invoke `POST /mcp` with `method=tools/call, name=run_query, arguments={"sql":"SELECT * FROM sensitive_table"}` and get query results. C1 (hardcoded write scope) was necessary for this path to work but not sufficient — the gate was absent regardless of scope. | **FIXED** in `app/ai/tools.py`: added an explicit `author:sql` + `kind=="access"` gate before executing ad-hoc SQL in `_tool_run_query`. Embed tokens (`kind!="access"`) and first-party tokens lacking `author:sql` scope now raise `AppError("insufficient_scope", 403)`. Registered `query_id` paths are unaffected (no raw SQL gate needed — the SQL is pre-approved). | `test_new_surface_audit.py::TestMCPRawSQLGateInTools::test_raw_sql_blocked_for_*` (4 tests), `::test_mcp_tools_call_raw_sql_requires_author_scope` |
| C3 | MCP outbound SSRF — at both registration and call time | — (verified safe) | `guard_url` is called in `routes/mcp.py` at `create_mcp_server` and `update_mcp_server` (registration path). `mcp.py` (the client) calls `guard_url(server.url)` at the top of both `list_tools_sync` and `call_tool_sync` (call-time path). Covers: localhost, RFC1918, link-local, 169.254.169.254, file://, ftp://, gopher://. | No change needed. | `test_new_surface_audit.py::TestMCPOutboundSSRF` (22 parametrized tests) |
| C4 | MCP server auth secrets — never returned by list/get API | — (verified safe) | `_PUBLIC_COLS` tuple structurally excludes all secret column names. `get_mcp_store().list_for_org` and `get_by_id` query only `_PUBLIC_SELECT`. `get_enabled_for_org` (internal, decrypted) is only called by the agent loop, never by a public route. `_strip_secrets` is a defence-in-depth helper applied by every public CRUD handler. | No change needed. | `::TestMCPSecretsNeverReturned` (2 tests) |
| C5 | MCP cross-org IDOR — list/get/update/delete/call | — (verified safe) | Every SQL in `McpServerStore` binds `org_id` as a positional parameter (`$1::uuid AND org_id = $2::uuid`). The route handlers resolve `org_id` from `_get_user_org(user["id"], repo)` (never from the request body). A mismatched org_id returns no row → `None` → 404. | No change needed. | `::TestMCPCrossOrgIsolationStore` (2 tests) |
| C6 | Governance RLS — policies from token only | — (verified safe) | The planner's `plan()` function accepts `claims["policies"]` which the route handler sets from `identity.policies` (the verified token payload). No route reads policies from the request body. Claim dict is built server-side. | No change needed. | `::TestGovernanceRLSPolicies::test_policy_comes_only_from_token_*` (2 tests) |
| C7 | Governance RLS — hierarchy expansion is org-scoped | — (verified safe) | `DbHierarchyResolver.resolve` queries `access_hierarchy WHERE org_id = $1 AND dimension = $2 AND parent_value = $3` — all parameterised. `InMemoryHierarchyResolver` keys on `(org_id, dimension, parent_value)` tuples. Cross-org resolution is structurally impossible: org A's hierarchy never resolves via org B's key. | No change needed. | `::test_hierarchy_expansion_is_org_scoped_no_cross_org_leak`, `::test_user_granted_region_x_sees_only_x_children` |
| C8 | Governance RLS — SQL injection in policy values | — (verified safe) | Planner builds AST-level predicates (not string concatenation) via sqlglot. Policy values that contain SQL metacharacters (quotes, OR, semicolons) are treated as literal string/integer values in parameterised predicates. The test asserts `' OR '1'='1` in a policy value returns 0 rows. | No change needed. | `::test_policy_value_with_sql_injection_payload_is_not_executed` |
| C9 | Transpile — auth required, no execution, input bounded | — (verified safe) | `POST /transpile` requires `current_user` (401 without token). Uses `sqlglot.transpile` — a pure AST transform with no DB connection. Unknown dialect → 400. Empty SQL → 400. Huge input does not crash (sqlglot has internal limits; any exception is caught and surfaced as 400). Injection payload in body is just translated text, never executed. | No change needed. | `::TestTranspileEndpoint` (7 tests) |
| C10 | Health endpoints — org-scoped IDOR | — (verified safe) | `routes/health.py` uses `verified_identity` dep and resolves `org_id` via `_org_id(identity)` → `get_user_org(identity.user_id, repo)`. `store.get(org_id, dataset_key)` and `store.list_for_org(org_id)` are primary-key scoped. Org B cannot read org A's dataset → 404. `GET /health/estate` embeds `org_id` from identity in the response — not from request input. | No change needed. | `::TestHealthIDOR` (4 tests) |
| C11 | Lineage endpoints — auth required, 404 for unknown nodes | — (verified safe) | All lineage routes declare `current_user` dep (401 without token). `/lineage/dag/{node_id}` raises `AppError("node_not_found", 404)` for unknown nodes. `/lineage/query/{id}` raises `AppError("query_not_found", 404)`. Lineage graph is built from in-process registry (seed queries only) — no per-org data is exposed. For DAG, see residual risk C12. | No change needed. | `::TestLineageIDOR` (4 tests) |

### Fixed vs documented

* **Fixed (2 HIGH):**
  - C1: MCP `tools/call` scope escalation — `routes/mcp.py` now passes `identity.scope` and `identity.kind` instead of hardcoded `["read:*", "write:*"]`.
  - C2: MCP `tools/call` raw-SQL bypass — `_tool_run_query` in `ai/tools.py` now enforces `author:sql` + `kind=="access"` gate before executing ad-hoc SQL.
* **Documented / verified safe (everything else):** all other new-surface invariants were found correct. Now pinned by 56 new regression tests in `tests/security/test_new_surface_audit.py`.

### Residual risk

1. **C12 — Lineage DAG returns global seed-query graph (LOW / BY DESIGN):** `GET /lineage/dag` and `GET /lineage` build the DAG from the in-process query registry, which is populated from seed queries at startup (not org-filtered). All registered queries and metrics are visible to any authenticated user. In the current deployment model the query registry holds platform-level query definitions that are not org-specific (they don't contain org data, just SQL templates and table references). If org-specific queries are added to the registry in a future milestone, the DAG route must be extended to filter by the caller's org. Documented but not changed — the design intent (per `lineage.py` docstring) acknowledges this.
2. **C13 — Lineage `POST /lineage/plan` and `POST /lineage/cell` accept user-supplied SQL for analysis (LOW):** These endpoints accept raw SQL/FlowSpec dicts for pure AST analysis (no execution). The SQL is parsed by sqlglot but never executed against a live DB. Injection in the SQL input is inert (pure text transform). The risk is DoS via huge/nested SQL — sqlglot has no explicit size cap. Future hardening: add an input-size limit (e.g. `len(body.sql) > 500_000 → 400`).
3. **C14 — MCP outbound DNS rebinding window (LOW):** `guard_url` resolves the hostname at registration and call time, but there is a small window between DNS resolution passing and `streamablehttp_client` connecting where a zero-TTL record could rebind to an internal IP. Mitigated by the SSRF guard firing at call time (not just at registration) — the window is per-call, not persistent. Full mitigation requires connect-to-pinned-IP, which is incompatible with the MCP SDK's transport abstraction in the current version.
