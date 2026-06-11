# Security Audit — Multi-Tenant SaaS Threat Model

**Scope:** Nubi backend (`backend/app/`), focused on the new notification/integration/
push/portability/api-key surface on branch `feat/bi-dashboards`.
**Date:** 2026-06-11
**Auditor:** automated security review (Claude).
**Disposition:** all CRITICAL/HIGH findings fixed in the working tree (not committed);
broad-refactor items flagged.

---

## Threat model

The system is a multi-tenant BI SaaS. The primary adversary is **an authenticated
tenant user (org A) attempting to read or mutate another tenant's (org B) data**,
plus **a tenant user trying to use the server as a confused deputy** (SSRF) or to
**escalate from read to write / from one org to another**. Trust boundaries:

| Boundary | Control |
|----------|---------|
| Unauthenticated → authed | JWT (HS256 first-party) or opaque API key (`nubi_ak_…`) via `auth/deps.py::current_user`. |
| Authed user → org data | `routes/_org.py::resolve_org_id` — `X-Org-Id` honoured only after an `org_members` membership check; API keys are **pinned** to their minting org. |
| Read → write | `auth/roles.py::require_writer` on mutating portability/import routes; watches reject embed tokens. |
| Server → outbound URL (connectors/webhooks) | `connectors/ssrf.py::guard_url` — resolves every A/AAAA record, blocks loopback/RFC1918/link-local/metadata. |
| Inbound webhook (Slack/WhatsApp) → handler | `chat/gateway.py::verify_signature` — HMAC-SHA256, timing-safe, 5-min replay window; fail-closed in production. |
| Secrets at rest | AES-256-GCM (`integration_secrets`, connector secrets); SHA-256 hash for API keys; ciphertext-only in DB; never returned in API responses. |

---

## Findings by severity

| # | Severity | Area | File:line | Status |
|---|----------|------|-----------|--------|
| 1 | **HIGH** | Push IDOR (cross-user unsubscribe + endpoint hijack) | `app/notify/push.py:218`, `app/routes/push.py:99` | **FIXED** |
| 2 | **HIGH** | SSRF via user-supplied outbound webhook URLs | `app/notify/channels.py:138,296,342` | **FIXED** |
| 3 | **HIGH** | Watches cross-org IDOR (no tenant scoping) | `app/routes/watches.py` (list/get/put/delete/evaluate) | **FIXED** |
| 4 | LOW (informational) | `/watches/tick` runs every org's watches with empty RLS under a shared secret | `app/routes/watches.py:480` | FLAGGED (intentional; documented) |
| 5 | LOW (informational) | `_resolve_org_id` for inbound chat can run agent "unscoped" when unbound | `app/chat/gateway.py:480` | OK by design (tools enforce RLS) |

Areas audited and found **sound** (no action): tenant isolation on
`integrations` / `notifications` / `projects_bundle` / `portability` (connector
kind) / `api_keys` routes; secret encryption-at-rest and scrubbing; API-key
org-pinning + `X-Org-Id` rejection; revoked-key rejection; Slack/WhatsApp webhook
signature verification (timing-safe + replay); the `/flows/compile` Python
sandbox (subprocess, scrubbed env, 15s timeout, no in-process exec); SSRF guard on
the `http_json` connector and S3 endpoints.

---

## Detailed findings + fixes

### 1. HIGH — Web Push subscription IDOR (`notify/push.py`, `routes/push.py`)

**Attack.** `PushStore.delete(endpoint)` deleted any subscription by `endpoint`
alone, with no owner check (`DELETE FROM push_subscriptions WHERE endpoint = $1`).
A push endpoint is a long but **guessable / leakable URL** (it appears in browser
devtools, proxy logs, etc.). Any authenticated user could call
`POST /push/unsubscribe` with another user's endpoint and **prune that user's
push subscription** (denial of notifications). Worse, `upsert` used
`ON CONFLICT (endpoint) DO UPDATE SET user_id = EXCLUDED.user_id` — letting a
caller **hijack** an existing endpoint, rebinding it to themselves and silently
redirecting the victim's pushes.

**Fix.**
- `delete(endpoint, user_id)` now scopes the DELETE to the owning user
  (`AND user_id = $2::uuid`); the in-memory store enforces the same. Route
  passes `str(user["id"])` (`routes/push.py:99`).
- `upsert` `ON CONFLICT` no longer overwrites `user_id` and adds
  `WHERE push_subscriptions.user_id = EXCLUDED.user_id`, so a conflicting
  endpoint owned by another user is a **no-op**; the route surfaces that as
  **409 Conflict** instead of a silent hijack (`routes/push.py:80`).
- In-memory store returns `{}` (refusal) when a foreign user tries to rebind.

**Regression tests.** `tests/test_notifications.py`:
`test_push_upsert_list_delete` (user-scoped delete), `test_push_upsert_cannot_hijack_foreign_endpoint`,
`test_push_unsubscribe_idor_blocked` (route-level: Bob cannot unsubscribe/hijack Alice's endpoint → `removed=false` / 409).

### 2. HIGH — SSRF via outbound notification webhooks (`notify/channels.py`)

**Attack.** `SlackChannel._post_webhook`, `GoogleChatChannel.send`, and
`TeamsChannel.send` (and the generic `webhook` kind, which routes through
GoogleChat) `httpx.post()` a **user-supplied `webhook_url`** with no host filter.
A tenant user creates an integration with
`webhook_url = http://169.254.169.254/latest/meta-data/iam/security-credentials/…`
(or `http://127.0.0.1:…/internal`) and fires it via `POST /integrations/{id}/test`
or any alert dispatch. The connector SSRF guard (`connectors/ssrf.py`) existed but
was **not** wired into these channels. `/integrations/{id}/test` even returns the
delivery error text (`Delivery failed: …`), which for a `ChannelError` includes a
200-char excerpt of the response body — an exfiltration channel for the metadata
service response.

**Fix.** Added `guard_url(self.webhook_url)` immediately before each user-controlled
outbound POST in `SlackChannel._post_webhook`, `GoogleChatChannel.send`, and
`TeamsChannel.send`. The fixed-vendor endpoints (`slack.com/api/chat.postMessage`,
`graph.facebook.com`) are not user-controlled and are left as-is. Blocked requests
now raise `AppError("ssrf_blocked", …, 400)` **before** any network call, so the
test endpoint reports a safe `ssrf_blocked` message rather than leaking a response.

**Regression tests.** `tests/test_notify.py::TestWebhookSSRF` — Slack/GoogleChat/
Teams channels reject metadata IP / loopback (and assert `httpx.post` is never
called), and a normal public webhook still posts.

### 3. HIGH — Watches cross-org IDOR (`routes/watches.py`)

**Attack.** The watches router kept a **process-global in-memory registry**
(`_WATCHES: dict[watch_id, record]`) with **no org dimension**, and the DB hydrate
was `SELECT … FROM watches WHERE id = $1` with no org filter. Consequences:
- `GET /watches` returned `_registry_all()` — **every org's** watches.
- `GET/PUT/DELETE /watches/{id}` and `POST /watches/{id}/evaluate` resolved a
  watch by bare id, so org A could read, **overwrite, delete, or evaluate** org
  B's watches (and `evaluate` runs the metric, leaking org B's data).

**Fix (localized to `watches.py`).**
- Added `_caller_org(identity)` resolving the caller's org once per request.
- Every record is **stamped with `org_id`** at create / update / persist / hydrate.
- `list_watches` filters to the caller's org.
- `_resolve_watch(watch_id, org_id)` returns 404 for a registry hit whose
  `org_id` mismatches, and hydrates with a **tenant-scoped** query
  (`WHERE id = $1 AND org_id = $2`).
- `get/delete/evaluate` resolve through the scoped path; `delete` also adds
  `AND org_id = $2` to the SQL DELETE.
- `update` (which doubles as create-via-PUT) explicitly 404s when the id belongs
  to **another** org, so it can never silently overwrite a foreign watch.

**Regression test.** `tests/test_watches.py::test_watch_cross_org_isolation` — Bob
(org B) cannot list / GET / DELETE / PUT Alice's (org A) watch (404, no info leak),
and Alice's watch is intact afterwards. The `w_client` fixture now seeds an
`InMemoryRepo` + org membership (watch routes are tenant-scoped).

### 4. LOW (flagged, not changed) — `/watches/tick` global sweep

`POST /watches/tick` (shared-secret gated via `WATCHES_TICK_SECRET`) iterates the
**entire** in-process registry across all orgs and evaluates each with empty RLS
policies (system context). This is intentional for a single-node best-effort
scheduler and already documented in the route. **Risk if the tick secret leaks:**
an attacker could trigger evaluation of all watches. A production multi-tenant
scheduler should resolve each watch's owning identity/policies (DB-driven due-scan)
rather than a global in-memory sweep. **Flagged** — broader refactor, out of scope
for a localized fix.

---

## Architectural note (flagged, not fixed)

The watches registry being an **in-process singleton** is a latent multi-node
hazard: records live only in the memory of the worker that created them, so a
lazy DB hydrate is required on every other worker. The IDOR is now closed (all
reads/writes are tenant-scoped at both the registry and DB layers), but moving
watches to a DB-first model (like the integrations/notifications stores) would be
the durable fix. Out of scope here to avoid a sprawling change.

---

## Fixes made (file:line)

| Fix | Files |
|-----|-------|
| Push IDOR (user-scoped delete; anti-hijack upsert; 409 on conflict) | `app/notify/push.py` (`delete`, `upsert`, both stores), `app/routes/push.py` (subscribe 409, unsubscribe user-scoped) |
| SSRF guard on outbound webhooks | `app/notify/channels.py` (`SlackChannel._post_webhook`, `GoogleChatChannel.send`, `TeamsChannel.send`) |
| Watches cross-org tenant scoping | `app/routes/watches.py` (`_caller_org`, `_hydrate_watch`, `_resolve_watch`, `list/get/create/update/delete/evaluate_watch`) |

## Tests added

- `tests/test_notifications.py`: `test_push_upsert_cannot_hijack_foreign_endpoint`,
  `test_push_unsubscribe_idor_blocked`; updated `test_push_upsert_list_delete` to the
  user-scoped `delete` signature + cross-user assertions.
- `tests/test_notify.py`: `TestWebhookSSRF` (4 cases).
- `tests/test_watches.py`: `test_watch_cross_org_isolation`; `w_client` fixture now
  seeds an org membership + resets the registry.

## Test tally

Command:
`cd backend && python -m pytest tests/ -k "integration or notif or push or api_key or portab or auth or connector or watch or chat or gateway" -q`

- **682 passed**, 9 skipped, 14 failed.
- The **14 failures are pre-existing** and unrelated to this audit: they are
  `RuntimeError: login_events table …` cross-module test-isolation failures in
  `test_auth.py` / `test_admin.py` / `test_onboarding.py`. Verified identical on the
  clean tree (`git stash`): clean = 675 passed / 14 failed; post-fix = 682 passed /
  14 failed. All 7 new security tests pass; **0 new failures introduced**. Each of
  these auth files passes in isolation.
