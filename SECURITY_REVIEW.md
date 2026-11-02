# Nubi Security Review

**Date:** 2026-11-02
**Scope:** `dev` branch at `3707fdd` — the full application: FastAPI backend (~131K LOC across ~30 subsystems), React/TypeScript frontend, database, deploy pipeline, and container/config.
**Method:** Manual source review across six subsystems (auth/access, compute sandbox/flows, connectors/SQL, AI/agent/MCP, secrets/webhooks/embed/storage, and deps/CI/Docker/frontend), run in parallel and consolidated here. `npm audit` on the installed lockfile, a git-history secret scan, and review of the existing `backend/tests/security/` suite (40+ regression tests). The headline findings were re-verified by hand against the real code. No dynamic testing against a running instance.
**Audience:** Nubi engineering team.

> **Supersedes** the earlier draft of this file. That draft reviewed a stale local snapshot whose module layout no longer exists on `dev`; its findings do not apply and have been discarded.

---

## 1. Executive summary

This is a mature, security-conscious codebase. Most classic web vulnerabilities are not just mitigated but covered by a dedicated security regression suite: SSRF with DNS-rebinding pins, SQL injection, RLS/tenant isolation, embed-token forgery, pickle-RCE signing, path traversal, and OAuth 2.1/PKCE. The review confirmed strong controls far more often than gaps (see section 4).

The real exposure is concentrated in a few places where a hardening pattern that is applied correctly in one code path is missing in a sibling path. The two most important:

1. **User Python escapes the sandbox in production on paths other than `/compute/run`.** The `/compute/run` endpoint correctly forces execution into the E2B microVM. Flow Python cells, scheduled Python jobs, and `/flows/compile` run the same kind of user code in a local host subprocess in all environments, bypassing that guard. The flow path additionally hands the org's plaintext secrets to that host process.
2. **The frontend ships a vulnerable DOMPurify** behind an otherwise well-designed single sanitizer choke-point, and LLM/user-authored dashboard HTML renders on the app origin.

Beyond those, a Data Browser DuckDB connection is not hardened the way the main query path is, an embed-issuer fast-path skips the org-scoping its DB fallback enforces, CI (which runs the security suite) is disabled, and a default superadmin password ships in config. None of these is a routine patch except DOMPurify and the headers; the execution and tenant-isolation items need code changes to route through the existing safe paths.

### Finding count by severity

| Severity | Count |
|---|---|
| Critical | 2 |
| High | 4 |
| Medium | 9 |
| Low | 13 |

Items marked **(needs verification)** could not be fully confirmed statically and should be checked against runtime/product intent before rating.

---

## 2. Critical findings

### C-01. User Python runs on the host instead of the sandbox (flows, jobs, compile)
**OWASP:** A04 Insecure Design · A01 Broken Access Control
**Verified.** `backend/app/jobs/executor.py:309` (`_run_python_job` → `LocalSubprocessRunner().run`), `backend/app/flows/registry.py:1002` (`_handle_python` → `run_sandboxed`), `backend/app/routes/flows.py` (`POST /flows/compile`), vs the guarded path in `backend/app/routes/compute.py` and `backend/app/compute/runner.py`.

`/compute/run` enforces, in production, that execution happens in the remote E2B microVM and returns 503 if no remote runner is configured. Three other paths that execute equally untrusted user Python — scheduled `python` jobs, flow `python` cells, and the `/flows/compile` tracer — never consult that guard and run in a local subprocess on the API/worker host in every environment, including production. `run_sandboxed` is self-described as dev-grade isolation only (same OS user, host network namespace, host filesystem). The `use_remote_kernel` flag that is supposed to route flow cells to E2B appears only in docstrings and is never read in code, so it is a no-op.

**Impact:** An authenticated org writer can execute code on the production host, which can reach the cloud metadata endpoint, internal network services, and the host filesystem (other tenants' on-disk data, application secrets). This is the shared-host blast radius the `/compute/run` design explicitly avoids. Rated Critical for a multi-tenant deployment; if every org writer is fully trusted in your threat model it is High.

**Fix:** Route flow cells, scheduled Python jobs, and `/flows/compile` through the same runner-selection and production guard as `/compute/run` (single shared `_choose_runner`). Implement `use_remote_kernel` or remove it and hard-fail Python cells in production until a remote runner exists. Deploy the egress firewall the runner docstrings already prescribe.

### C-02. Vulnerable DOMPurify is the sole sanitizer for same-origin user/LLM HTML
**OWASP:** A03 Injection (XSS) · A06 Vulnerable Components
**Verified (version).** `package-lock.json` resolves `dompurify@3.2.7`; sanitizer choke-point at `src/dashboards/sanitize.ts`, rendered via `renderDashboardDoc.ts` (`container.innerHTML`) and the widget HTML components, on the app origin.

The sanitizer design is good — one choke-point that forbids `script/iframe/object/style/link`, strips `on*` handlers, and rejects `javascript:`/`data:` URLs — but the installed DOMPurify version is subject to multiple published sanitizer-bypass advisories. All LLM-authored and user-authored dashboard/widget HTML passes through this one dependency and renders same-origin in the SPA.

**Impact:** A sanitizer bypass yields stored XSS on the app origin. The session design limits the damage (access token in memory, refresh token HttpOnly), but same-origin script can still call the refresh endpoint (cookie auto-sent, SameSite=Lax) to obtain an access token and drive the authenticated API.

**Fix:** Upgrade DOMPurify to the current patched 3.4.x release. Add a Content-Security-Policy (see M-05) as defense in depth. Consider rendering untrusted dashboard HTML in a sandboxed, separate-origin iframe so a future bypass cannot reach the app origin.

---

## 3. High findings

### H-01. Data Browser DuckDB connections are not hardened
**OWASP:** A01 Broken Access Control · A10 SSRF
**Verified.** `backend/app/routes/data_browser.py:248` returns `DuckDBConnector(_conn)` with no `harden_connection`; `DuckDBConnector.__init__` (`connectors/duckdb_conn.py:338`) does not harden a passed-in connection; the main query path hardens the equivalent connection at `routes/query.py:445,1332,1354`. `view_sql` is not in `_RESERVED_CONFIG_KEYS` (`routes/connectors.py`).

The main query path calls `harden_connection(..., disable_external_access=True)` precisely because tenant SQL runs verbatim and DuckDB can otherwise read local files, fetch URLs via httpfs, and install extensions. The Data Browser builds its DuckDB connection without that call, and a writer can store an arbitrary `view_sql` on a `duckdb` connector that is then executed when rows are browsed.

**Impact:** A tenant-scoped writer can read arbitrary server-side files, reach the cloud metadata endpoint via httpfs, and write files — from the Data Browser surface only. Crosses the tenant→host boundary the query path defends.

**Fix:** Call `harden_connection` on every connection `_build_duckdb_connector` returns (after httpfs/S3 setup and after executing `view_sql`), matching `routes/query.py`. Make `DuckDBConnector` harden-by-default, or route all DuckDB builds through one shared hardened factory so a new call site cannot reintroduce the gap.

### H-02. Host-mode embed-issuer fast-path skips org-scoping
**OWASP:** A01 Broken Access Control
**Verified.** `backend/app/auth/verify.py:461` and `backend/app/auth/deps.py:242` (in-process registry branch) vs the DB branch at `verify.py:473` / `deps.py:269`.

The DB fallback resolves an embed issuer with `get_enabled_by_iss(resolved_org, iss)` — requiring the issuer to be configured under the org the token claims. The in-process `IssuerRegistry` fast-path, tried first, looks issuers up by `iss` alone and then pins the org straight from the token's `org` claim, with no check that the issuer may speak for that org. A customer who controls a `host_mode` issuer's signing key could mint a token naming another tenant's org and, on a worker whose registry holds that `iss`, read that tenant's data (read-only, since scopes are stripped). The window is worker-local but effectively always-on for single-worker or sticky-routed deployments. Existing host-mode tests cover org-claim typing, not this registry-vs-DB divergence.

**Fix:** Make the registry path enforce the same org binding as the DB path — store org on the registry entry and verify it matches `resolved_org`, or drop the fast-path for host-mode issuers and always use the org-scoped `get_enabled_by_iss`. The registry must never be more permissive than the DB store.

### H-03. CI/CD is disabled, so the security suite and dependency scanning never run
**OWASP:** A05 Security Misconfiguration (process)
**Verified.** `.github/workflows/ci.yml.disabled`, `release.yml.disabled` (GitHub only runs `*.yml`/`*.yaml`).

The pipeline that would run lint, the `backend/tests/security` suite, the PG integration tests, and the frontend builds is inert, and even when enabled there is no `npm audit`/`pip-audit`/SAST/Dependabot gate in either workflow. The release workflow still builds and pushes images on tag with no security gate. Given how much of this codebase's safety rests on that regression suite, a regression (or a newly vulnerable dependency) would ship unnoticed.

**Fix:** Re-enable CI, make the security test job required, and add dependency scanning (`npm audit` / `pip-audit` / Dependabot) as a gating step before image push.

### H-04. Default superadmin credentials ship in config and are accepted in production
**OWASP:** A07 Identification and Authentication Failures (default credentials)
**Verified.** `backend/app/config.py:99` (`SUPERUSER_EMAIL="admin@nubi.dev"`, `SUPERUSER_PASSWORD="nubi-admin-2026"`); consumed by `backend/seed.py` which sets `is_superadmin=true`.

`JWT_SECRET` is correctly required and length-validated, but the bootstrap superadmin password has a repo-public default. Any deployment that runs seed/reset without overriding `SUPERUSER_*` has a known superadmin login with full `/admin/*` and cross-tenant `/admin/orgs/{id}/enter` access; login rate-limiting does not help against a known password.

**Fix:** Mirror the `JWT_SECRET` validator — refuse to start (or have `seed.py` refuse) when `ENV=production` and `SUPERUSER_PASSWORD` is unset or equals the built-in default. Treat the default as dev-only, and force a password reset on first login.

---

## 4. Medium findings

- **M-01. Daily USD cost ceiling not enforced on `/ai/chat*`.** (LLM10) `cost_ceiling.check_chat_budget`/`record_chat_cost` are wired only into the editor `/chat/stream` path (`routes/chat.py:184,261`); `ai_chat`/`ai_chat_stream` (`routes/ai.py:931,1025`) run the real-provider agent loop with only a per-request count quota, so the documented daily USD cap is silently ignored there. **Fix:** call the same budget check/record in both AI chat handlers.
- **M-02. Chat tools don't org-scope the query registry.** (LLM02 / tenant isolation) `chat/tools.py:688` `execute_tool` takes no org context; `list_registered_queries` calls `registry.all()` (process-global), and `register_query` omits `owner_org_id`, creating globally resolvable entries — unlike the REST path which filters per-org. Metadata leak (query ids/names/params across orgs) and namespace pollution; raw-data cross-tenant read is still blocked by org-scoped datastore resolution. **Fix:** thread `org_id` into the chat tools; filter listing by org and pass `owner_org_id=org_id` on register.
- **M-03. Agent allows within-org destructive tools behind a model-supplied confirm flag.** (LLM01/LLM06) The real-provider loop (`ai/agent.py`) feeds untrusted tool/MCP/board text back into the model with `update_dashboard`/`delete_dashboard`/`create_query` enabled every step; the only gate on delete is a `confirm` boolean the model itself sets (`ai/tools.py:1396`). Blast radius is the caller's own org, but it is unattended. **Fix:** require genuine out-of-band human confirmation for destructive tools, or keep mutate/delete off the autonomous path; treat tool-output text as tainted.
- **M-04. Stored XSS via SVG avatar.** (A03) `assets/service.py:31` allows `image/svg+xml` and `assets/routes.py:86` serves it with no `nosniff`/CSP/`Content-Disposition`; the sibling `dashboards/images.py` deliberately excludes SVG for exactly this reason. `PATCH /auth/me` lets any user set an arbitrary avatar URL that is re-hosted. Executes on direct navigation (not `<img>`), so click-required stored XSS on the app origin. **Fix:** drop SVG from the avatar allowlist, or serve with `Content-Disposition: attachment` + `nosniff` + restrictive CSP.
- **M-05. No HSTS / CSP / X-Frame-Options / X-Content-Type-Options.** (A05, needs verification of any fronting proxy) No security-headers middleware in `backend/main.py`; `fly.toml force_https` redirects but emits no HSTS. Absence of CSP also removes a defense-in-depth layer against C-02/M-04. **Fix:** add a security-headers middleware (HSTS, nosniff, frame-ancestors/X-Frame-Options, a CSP).
- **M-06. Python dependencies are floor-pinned with no lockfile or hashes.** (A06) Core web/auth/crypto deps are exact-pinned, but ~25 deps use `>=` floors and there is no `uv.lock`/`poetry.lock` or `--hash` pinning, so builds are non-reproducible and auto-pull future upstream releases. **Fix:** generate a hashed lockfile and install with `--require-hashes`.
- **M-07. Flow Python cells have no max timeout and can skip `RLIMIT_CPU`.** (A04 / DoS) `flows/registry.py:575` has no upper cap on `timeout_s`, and `timeout_s=0` maps to no wall-clock timeout and no CPU rlimit, unlike `/compute/run` which caps at 120s. An authenticated writer can wedge a worker. **Fix:** enforce a max cell timeout and always apply `RLIMIT_CPU`/cgroup limits.
- **M-08. Flow cells and scheduled jobs meter usage but don't enforce quota pre-run.** (A04) `/compute/run` calls `enforce_quota` before running; the flow executor and `_run_python_job` only record usage afterward, with no `enforce_quota` in `flows/` or `jobs/`. Quota bypass and an amplifier for M-07. **Fix:** enforce quota before dispatching flow cells and Python jobs.
- **M-09. `/compute/run` resolves input queries with empty claims (needs verification).** (A01) `routes/compute.py:415` fetches `registry.get(query_id)` with no org scoping and plans with `claims={}`, so RLS predicates that rely on claims may not apply. May be constrained to the seeded demo connector; a cross-org test exists. **Fix:** scope the registry lookup to the caller's org and pass the caller's real claims into the planner.

---

## 5. Low findings

- **L-01. Blind Web Push SSRF.** (A10, needs verification) `routes/push.py:57` stores a caller-supplied push `endpoint` with no validation; `notify/push.py` sends to it with no SSRF guard, while every other outbound path uses `resolve_and_pin`. Blind, POST-only, fires only if push is configured. **Fix:** run the endpoint through `guard_url` before persisting.
- **L-02. Direct-mode DB connectors apply no SSRF restriction (likely by-design, needs verification).** `connectors/network.py:283` returns user host/port verbatim for `postgres`/`mysql`/`clickhouse`; `/connectors/{id}/test` is an internal reachability oracle. Connecting to arbitrary DB hosts is the product's function, so flag for an explicit decision. **Fix:** optional allow/deny policy for non-self-hosted deployments, or document accepted risk.
- **L-03. Writable DuckDB connector opened unhardened.** (A05) `data_browser.py:985` opens read-write and unhardened; limited today because write endpoints are parameterized with allowlisted identifiers, but one careless call site from the H-01 primitive. **Fix:** harden here too.
- **L-04. `/ai/chat*` hard-codes full author scope regardless of token (needs verification).** `routes/ai.py:992,1055` build agent claims with a literal `read:*/write:*/author:*` instead of the token's real scope, unlike the MCP path which forwards `identity.scope`. Browser logins already carry these; the gap is a scoped first-party token gaining ad-hoc SQL. **Fix:** build agent scope from `identity.scope`.
- **L-05. `create_query` + `run_query(query_id)` walk around the `author:sql` gate.** `ai/tools.py` gates ad-hoc `run_query(sql=...)` on `author:sql` but `_tool_create_query` and `run_query(query_id=...)` do not re-check it. Latent (RLS still applies). **Fix:** apply the `author:sql` check on create/registered-query execution.
- **L-06. Org-invite acceptance not bound to the invited email.** (A01) **Verified** — `routes/orgs.py:826` `accept_invite` checks only `status == "pending"` and never compares the caller's email to the invite's email, so any holder of the (256-bit secret) token joins with the invited role. **Fix:** require `lower(user.email) == lower(invite.email)`, consistent with `GET /auth/me/invites`.
- **L-07. Host-mode scope strip does not remove the `*` sentinel.** (A04, defense-in-depth) **Verified** — `_strip_host_mode_write_scopes` (`auth/deps.py:185`) strips `write:`/`edit:`/`author:sql`/`admin*` but not `*`, which `has_scope` treats as all-access. No live sink today (write paths independently reject embed tokens by `kind`). **Fix:** strip `*`, or convert the function to an allow-list that fails closed.
- **L-08. Login audit IP trusts left-most `X-Forwarded-For`.** (A09) `routes/auth.py:198` uses the spoofable left-most XFF for session/login-event IP, inconsistent with the rate-limiter's trusted right-most logic. Log integrity only. **Fix:** reuse `ratelimit._client_ip`.
- **L-09. Denylist/membership checks fail open on store error.** (A04) `auth/deps.py:148,388` swallow `is_revoked` exceptions, honouring a revoked 15-min token during a denylist outage — a deliberate availability tradeoff. **Fix:** optionally fail closed in production, or alert on the exception path.
- **L-10. Cost ceiling is soft and race-prone.** (LLM10) `cost_ceiling` is pre-flight-only, fail-open on metering error, no reservation, so concurrent turns can overshoot (TOCTOU). **Fix:** reserve estimated cost up front; hard per-turn max.
- **L-11. Base images and GitHub Actions pinned by mutable tag, not digest.** (A06/A05) `node:20-alpine`, `python:3.13-slim`, `actions/*@vN`. **Fix:** pin by digest / commit SHA.
- **L-12. No `TrustedHostMiddleware`; `PUBLIC_API_URL` derived from request host when unset (needs verification).** `config.py` — since `PUBLIC_API_URL` is the OAuth issuer / MCP resource id, a spoofable Host could affect generated URLs. **Fix:** set it explicitly in prod and/or add a host allowlist.
- **L-13. Shipped frontend deps with advisories.** (A06) `react-router-dom` 7.13.0 (high), `echarts`/`mermaid` (moderate), plus a vulnerable transitive set; most of the 37 `npm audit` hits are dev/build tooling not served to users. **Fix:** `npm audit fix` and bump the shipped direct deps. Also: `argon2-cffi==21.3.0` is well behind current (L, needs verification of any CVE on the hash path).

---

## 6. Positive controls confirmed

The review confirmed a broad set of strong defenses; these should **not** be re-flagged:

- **Code execution:** `/compute/run` forces E2B in production (503 fallback), scrubs the subprocess environment of all secrets, caps output and timeout, and enforces quota pre-run. Artifact deserialization is HMAC-signed and org-bound with fail-closed production behaviour before any `pickle.loads`. The only host-side `exec`/`compile` run inside the sandbox harness, not on the host namespace.
- **SQL / templating:** The connector `SandboxedEnvironment` binds every templated value as a positional parameter and blocks `__class__`/`__globals__`; RLS predicates are built as sqlglot AST nodes from verified claims, never string-concatenated; identifiers are allowlisted and quoted; non-SELECT is rejected. Backed by `test_sec_sql_injection*.py` and `test_sec_raw_sql_exhaustive.py`.
- **SSRF:** `resolve_and_pin` (resolve-once + IP-pin, DNS-rebind-safe, redirects disabled) guards http_json, ftp, sftp, MCP, webhooks, flow `http_call`, dashboard images, and avatar fetch. Backed by the SSRF/DNS-rebind tests.
- **Secrets at rest:** Named secrets use Fernet; connector/integration/MCP secrets use AES-256-GCM with key versioning; secret columns are never returned by read or list endpoints; unset encryption key fails clean (503), never echoing the secret. Backed by `test_secrets_creds.py`, `test_mcp_secret_unconfigured.py`.
- **Embed tokens:** Host-signed RS256/ES256 via a JWKS issuer registry; `alg:none`, HS↔RS confusion, forged-key, expired, and tampered-claim tokens all rejected; embed tokens blocked from raw SQL and never metered. Backed by `test_sec_embed_*.py`.
- **Auth core:** First-party JWTs are HS256-pinned with required claims; opaque 256-bit refresh tokens hashed at rest with reuse-detection family revocation; OAuth 2.1 with mandatory PKCE, exact redirect match, single-use codes, and audience binding; Google login enforces `email_verified`; argon2id password hashing with constant-time anti-enumeration; superadmin re-read from the DB every request, never from the JWT.
- **Tenant isolation:** `X-Org-Id` honoured only after `org_members` membership check; API-key/host-mode org pinning; cross-org ids return 404 (no existence leak); exact-result cache keyed by org; rate limits keyed on the cryptographically verified org.
- **Webhooks:** Outbound URLs guarded at create/update and pinned at delivery; inbound Paystack webhook HMAC-verified with replay dedupe and cross-org isolation.
- **Frontend/session:** Single DOMPurify sanitizer choke-point (design is sound; the version is the issue — C-02); access token in memory only, refresh token HttpOnly/Secure/SameSite=Lax; no tokens in web storage; no secrets in the bundle.
- **Containers/config:** Non-root `appuser`, multi-stage builds, `.dockerignore` excludes env/secrets; CORS is not wildcard by default; `JWT_SECRET` required and length-validated; release workflow uses least-privilege permissions and no `pull_request_target`.
- **No secrets committed** to the tree or git history (every match was a placeholder or test fixture).

---

## 7. Prioritized remediation plan

**P0**
- C-01: route flow cells, scheduled Python jobs, and `/flows/compile` through the E2B-guarded runner; stop injecting org secrets into host subprocesses; implement or remove `use_remote_kernel`.
- C-02: upgrade DOMPurify to patched 3.4.x.
- H-01: harden Data Browser DuckDB connections (shared hardened factory).
- H-02: enforce org binding on the embed-issuer registry fast-path.
- H-04: forbid the default superadmin password in production.

**P1**
- H-03: re-enable CI, gate releases on the security suite, add dependency scanning.
- M-05: add security headers (HSTS/CSP/X-Frame-Options/nosniff).
- M-01, M-02, M-03: enforce the USD ceiling on `/ai/chat*`, org-scope the chat tools, require real confirmation for destructive agent tools.
- M-04: drop SVG from avatars or serve inert.
- M-07, M-08: cap flow-cell timeout + apply CPU limits; enforce quota pre-run.

**P2**
- M-06, L-11, L-13: hash-lock Python deps, digest-pin images/actions, `npm audit fix` shipped deps.
- L-01, L-02, L-12: push SSRF guard, direct-mode connector policy decision, explicit `PUBLIC_API_URL`/TrustedHost.
- L-04 to L-10: agent scope/`author:sql` consistency, invite email binding, `*` scope strip, audit-IP source, fail-closed denylist option, cost-ceiling reservation.
- M-09: verify `/compute/run` registry scoping and claims propagation.

---

*Point-in-time static review consolidated from six parallel subsystem passes; headline findings re-verified by hand. It does not replace dynamic testing or a penetration test against a running, authenticated instance, which is worth doing once the P0 items land. Several items tagged "needs verification" depend on runtime configuration or product intent.*
