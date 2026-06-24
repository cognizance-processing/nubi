# Security Review — Wave-4 Adversarial Hardening

Date: 2026-06-24  
Scope: `feat/embed-bi-substrate` (Waves 1–3 merged into `main`)

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
