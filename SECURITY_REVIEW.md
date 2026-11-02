# Nubi Security Review

**Date:** 2026-11-02
**Scope:** `dev` branch at commit `1347510`. Backend FastAPI app, React/Vite frontend, database migrations, GitHub Actions deploy pipeline, Cloud Run and Firebase configuration.
**Method:** Manual source review of all backend modules and all frontend auth, API, and rendering code; `npm audit` on the installed lockfile; a git-history scan for leaked credentials; review of the deploy pipeline and the Dockerfile on `main`. No dynamic testing was run against a live instance. Python dependencies were not audited because no virtualenv is present and `requirements.txt` is unpinned (see F-20).
**Audience:** Nubi engineering team.

---

## 1. Executive summary

In its current state the application should not be exposed to untrusted users. The product goal — let an LLM and users run Python and SQL against connected data — has been built without the tenant isolation, authorization, and code-execution sandboxing that goal requires. The themes, most to least urgent:

1. **Server-side code and query execution without a sandbox.** Three independent backend paths run user-controlled logic: stored Python is executed directly, user SQL is passed through Jinja2 before execution, and datastore connection strings are user-defined with arbitrary database dialects allowed. Several of these paths need no authentication token.
2. **Missing authorization (tenant isolation).** Most object endpoints fetch rows by UUID with no ownership or membership check, the AI tool layer enumerates every datastore and board in the database when no user context is attached, and `/explore` falls back to "the first datastore in the table." Datastore configs hold database passwords and cloud keys in plaintext and are returned to any caller who knows the UUID.
3. **AI chat endpoints are reachable without authentication** and expose tools that query any datastore, create and delete queries, and write files. This enables data disclosure and uncontrolled LLM spend.
4. **Stored cross-site scripting with session theft potential.** Board and widget HTML authored by one user is rendered for other users, and the session token lives in `localStorage`.
5. **A seed account with a hard-coded password** is created in every environment, including production.

Themes 1 to 3 are design-level and need an authorization layer plus sandboxing, not a patch. Themes 4 and 5 are quick fixes. A prioritized plan is in section 5.

### Finding count by severity

| Severity | Count |
|---|---|
| Critical | 6 |
| High | 9 |
| Medium | 10 |
| Low | 7 |

---

## 2. Attack surface map

### Backend HTTP endpoints

| Route | Auth | Note |
|---|---|---|
| `POST /auth/signup` | none | Open registration, no input validation |
| `POST /auth/signin` | none | No rate limiting |
| `POST /auth/google` | none | No OAuth `state`, no `verified_email` check |
| `GET /auth/me` | JWT | |
| `GET/POST /organizations`, `PATCH /organizations/{id}` | JWT | PATCH checks role — the one correct example |
| `GET /boards` | JWT | Membership-scoped (correct) |
| `POST /boards` | JWT | No membership check on `organization_id` |
| `GET/PATCH /boards/{id}`, `.../code`, `.../queries` | JWT | No ownership check |
| `GET/PATCH/DELETE /queries/{id}` | JWT | No ownership check |
| `GET /datastores` | JWT | User-scoped (correct) |
| `POST /datastores` | JWT | No membership check on `organization_id` |
| `GET/PATCH/DELETE /datastores/{id}` | JWT | No ownership check; GET returns plaintext secrets |
| `POST /upload/keyfile` | JWT | No size or content-type limit |
| `GET/POST /chats`, `PATCH /chats/{id}` | JWT | List/patch user-scoped |
| `GET/POST /chats/{id}/messages` | JWT | No ownership check |
| `GET /widgets`, `POST /widgets` | JWT | Public widgets cross tenant |
| `GET /widgets/{id}` | JWT | No ownership check |
| `PATCH/DELETE /widgets/{id}` | JWT | Owner check present (correct) |
| `GET /stats`, `GET /usage*` | JWT | Counts leak for any org id |
| `POST /explore` | none | Executes stored Python |
| `POST /test-datastore` | none | Connects to arbitrary hosts from raw config |
| `POST /query` | none | Jinja2 render + SQL on any datastore |
| `POST /get-schema` | none | Schema of any datastore |
| `GET /models` | none | Reveals configured providers |
| `POST /board-helper`, `/board-helper-stream` | optional | LLM agent loop with data tools |
| `POST /generate-chat-title` | optional | LLM spend |

### Trust boundaries that are missing

- **User to backend:** no tenant or ownership authorization layer.
- **Backend to LLM:** tool output (query results, stored board code) is fed back into the model while destructive tools are enabled — classic indirect prompt-injection exposure.
- **User HTML to other users' browsers:** boards and widgets render in same-origin iframes.
- **Backend to customer databases:** connection strings are user-controlled, arbitrary dialects are accepted, and queries are not constrained to read-only.

---

## 3. Findings

OWASP references are to the Top 10 (2021) and the Top 10 for LLM Applications (2025).

### Critical

#### F-01. Unsandboxed execution of stored Python
**OWASP:** A03 Injection · A08 Integrity Failures
**Where:** [backend/app/routes/explore.py:102](backend/app/routes/explore.py#L102), [backend/app/query_engine.py:224](backend/app/query_engine.py#L224)

The `python_code` column of `board_queries` is executed with `exec()` using a plain locals dict, so full Python builtins are available to it. A query is created through `POST /boards/{id}/queries` (no board ownership check, F-07) and executed through `POST /explore`, which requires no token (F-05). The LLM `run_query` and `create_or_update_query` tools run the same path.

**Impact:** The code runs with the privileges of the backend process. On Cloud Run that process runs as root (the Dockerfile on `main` sets no `USER`) under a service account that `setup-secrets.sh` grants read access to every application secret. This path should be treated as full backend compromise.

**Fix:** Do not execute user Python in-process. Move query execution to an isolated, resource-limited sandbox (a separate container or microVM such as gVisor/Firecracker, or a dedicated worker with `seccomp` and no secret access and no network egress to internal services). If a restricted expression language is enough for the real use cases, replace free-form Python with it. At minimum, require authentication and ownership on every path that can trigger execution, as an interim control only.

#### F-02. Server-side template injection in SQL rendering
**OWASP:** A03 Injection
**Where:** [backend/app/routes/explore.py:195](backend/app/routes/explore.py#L195), [backend/app/query_engine.py:153](backend/app/query_engine.py#L153), [backend/app/query_engine.py:206](backend/app/query_engine.py#L206)

SQL is rendered with `jinja2.Template(sql).render(**args)` where both the template and `args` come from the request body. Jinja2 used this way is not sandboxed, so template expressions can reach Python object internals, not just substitute values. `POST /query` reaches this with no authentication.

**Fix:** Stop templating SQL as a document. Use parameterized queries with bound parameters for all user values. If templating is genuinely needed, use `jinja2.sandbox.SandboxedEnvironment` with autoescaping off but a strict allowlist, and still bind values as query parameters rather than string-interpolating them into SQL.

#### F-03. User-controlled database connection strings and arbitrary dialects
**OWASP:** A03 Injection · A10 SSRF
**Where:** [backend/app/query_engine.py:82](backend/app/query_engine.py#L82) (`get_sa_engine`), [backend/app/query_engine.py:71](backend/app/query_engine.py#L71) (`get_duckdb_connection_string`)

`create_engine(connection_string)` is called on a string taken verbatim from datastore config, and the supported dialects include DuckDB. DuckDB and similar embedded engines can read and write the local filesystem and load extensions, so a crafted datastore turns "connect to my database" into local file access on the backend. Arbitrary host connections also enable SSRF against internal network services. `POST /test-datastore` reaches this unauthenticated.

**Fix:** Constrain datastore types to an explicit allowlist of remote dialects. Build connection URLs from individual validated fields (host, port, db, user, password) rather than accepting a full connection string. Forbid embedded file-backed engines, or run them in the same isolated sandbox as F-01 with no host filesystem. Apply egress network policy so the backend can only reach approved database hosts.

#### F-04. Datastore secrets returned to any authenticated caller
**OWASP:** A01 Broken Access Control · A02 Cryptographic Failures
**Where:** [backend/app/routes/crud.py:233](backend/app/routes/crud.py#L233) (`GET /datastores/{id}`), stored by [backend/app/routes/crud.py:223](backend/app/routes/crud.py#L223)

`config` is stored as plaintext JSONB and includes `connection_string`, AWS `access_key_id` / `secret_access_key`, and BigQuery details. `GET /datastores/{id}` has no ownership check and returns the full config, so any logged-in user who knows or guesses a UUID retrieves another tenant's database credentials. The frontend also binds these secrets straight back into form fields ([DatastoreDetailPage.jsx:104](src/pages/Datastores/DatastoreDetailPage.jsx#L104)).

**Fix:** Add ownership/membership checks to every datastore read (see F-07). Never return secret fields to the client — store them encrypted (envelope encryption with a KMS key, or GCP Secret Manager references) and return only non-secret metadata and a "configured" boolean. Treat the current secrets as exposed and rotate them.

#### F-05. Unauthenticated data-access and execution endpoints
**OWASP:** A01 Broken Access Control
**Where:** [backend/app/routes/explore.py:19](backend/app/routes/explore.py#L19) (`/explore`), [backend/app/routes/explore.py:132](backend/app/routes/explore.py#L132) (`/test-datastore`), [backend/app/routes/explore.py:175](backend/app/routes/explore.py#L175) (`/query`), [backend/app/routes/ai.py:1196](backend/app/routes/ai.py#L1196) (`/get-schema`)

None of these endpoints declare `Depends(get_current_user)`. They execute stored Python, run arbitrary SQL, connect to arbitrary hosts, and read schemas — all anonymously. Combined with F-01 to F-03 this is remotely reachable without credentials.

**Fix:** Require authentication on every endpoint except the public marketing/auth routes. Add authorization so the caller may only act on datastores and queries they own. Consider a default-deny router dependency so new endpoints are protected unless explicitly marked public.

#### F-06. Unauthenticated LLM agent endpoints with data tools
**OWASP:** A01 Broken Access Control · LLM01 Prompt Injection · LLM06 Excessive Agency
**Where:** [backend/app/routes/ai.py:165](backend/app/routes/ai.py#L165), [backend/app/routes/ai.py:227](backend/app/routes/ai.py#L227) (both use `get_optional_user`)

`board-helper` and `board-helper-stream` accept requests with no token (`user_id` becomes `None`). With no user context the tool layer enumerates and operates on all datastores and boards (F-08), and every call drives paid LLM requests. An anonymous caller can read data through the model and run up cost.

**Fix:** Require authentication on both endpoints. Scope every tool call to the authenticated user's organizations. Add per-user rate limiting and a spend cap. Treat tool output returned to the model as untrusted and keep write/delete tools behind explicit, authorized, non-anonymous actions (see F-09).

### High

#### F-07. Object endpoints lack ownership checks (IDOR)
**OWASP:** A01 Broken Access Control
**Where:** boards [crud.py:92](backend/app/routes/crud.py#L92), [crud.py:101](backend/app/routes/crud.py#L101), [crud.py:119](backend/app/routes/crud.py#L119), [crud.py:128](backend/app/routes/crud.py#L128), [crud.py:142](backend/app/routes/crud.py#L142); queries [crud.py:165](backend/app/routes/crud.py#L165), [crud.py:174](backend/app/routes/crud.py#L174), [crud.py:192](backend/app/routes/crud.py#L192); chat messages [crud.py:322](backend/app/routes/crud.py#L322), [crud.py:331](backend/app/routes/crud.py#L331); datastores [crud.py:244](backend/app/routes/crud.py#L244), [crud.py:266](backend/app/routes/crud.py#L266)

These handlers select or mutate by id with no check that the row belongs to the caller's user or organization. Any authenticated user can read, modify, or delete another tenant's boards, queries, chat messages, and datastores by iterating UUIDs.

**Fix:** Add an ownership/membership check to every by-id handler, ideally a shared helper (e.g. `require_board_access(board_id, user)`) or a SQL `JOIN` to `organization_members` as already done in `GET /boards`. The `PATCH /organizations/{id}` role check and the widget owner check are the correct template to follow.

#### F-08. AI tool layer defaults to all-tenants scope
**OWASP:** A01 Broken Access Control
**Where:** [backend/app/ai_tools.py:279](backend/app/ai_tools.py#L279) (`get_available_datastores`), [ai_tools.py:292](backend/app/ai_tools.py#L292) (`get_available_boards`), [ai_tools.py:502](backend/app/ai_tools.py#L502) (`create_or_update_datastore` falls back to first user), and the query/board tools in [ai.py](backend/app/routes/ai.py) that take raw ids.

When `org_id` and `user_id` are absent the datastore query is `SELECT ... FROM datastores` with no filter; `get_available_boards` always lists 20 boards with no scoping. Tools like `get_query_code`, `get_board_code`, `execute_query_direct`, and `delete_query` act on any id the model is given.

**Fix:** Make scoping mandatory. Pass the authenticated user/org into every tool, filter all tool queries by it, and reject tool calls that reference ids outside the caller's scope.

#### F-09. Destructive and write tools exposed to a prompt-injectable agent
**OWASP:** LLM01 Prompt Injection · LLM06 Excessive Agency
**Where:** tool dispatch [backend/app/routes/ai.py:74](backend/app/routes/ai.py#L74); tools include `delete_query`, `create_or_update_query`, `create_or_update_datastore`, `save_keyfile`.

The agent loop feeds tool results and stored board/query code back into the model (e.g. [ai.py:299-304](backend/app/routes/ai.py#L299)), and board HTML is attacker-controllable. Instructions embedded in that content can steer the model into calling write and delete tools. The loop also allows up to 200 tool iterations per request.

**Fix:** Apply least privilege to the agent. Keep destructive actions out of the autonomous loop or gate them behind an explicit user confirmation step. Scope all tool effects to the caller. Lower the iteration ceiling and add a per-request tool-call budget. Clearly separate trusted instructions from untrusted data in the prompt.

#### F-10. Stored XSS via board and widget HTML in same-origin iframes
**OWASP:** A03 Injection (XSS)
**Where:** board preview [BoardEditor.jsx:1032](src/components/BoardEditor.jsx#L1032) (iframe with no `sandbox`), [BoardEditor.jsx:585](src/components/BoardEditor.jsx#L585) (`srcdoc = finalCode`); widget detail [WidgetDetailPage.jsx:277](src/pages/Widgets/WidgetDetailPage.jsx#L277) (`sandbox="allow-scripts allow-same-origin"`); thumbnails in HomePage, BoardsList, WidgetsPage.

Board and widget HTML is authored by users and rendered as live markup. Public widgets and shared boards are shown to other users. The main board preview iframe has no `sandbox` attribute at all, and the widget editor iframe combines `allow-scripts` with `allow-same-origin`, which lets framed script reach the parent origin. Because the session JWT is in `localStorage` (F-11), script running on the app origin can read it.

**Fix:** Render all user HTML in a sandboxed iframe that never combines `allow-scripts` with `allow-same-origin`. Serve user content from a separate origin (e.g. a sandbox domain) so even same-origin tricks cannot reach app storage or cookies. Add a Content-Security-Policy. Reconsider whether arbitrary author HTML/JS is required at all.

#### F-11. Session token stored in `localStorage`
**OWASP:** A07 Identification and Authentication Failures
**Where:** [src/lib/api.js:5-10](src/lib/api.js#L5), [AuthContext.jsx:19](src/contexts/AuthContext.jsx#L19)

The JWT is kept in `localStorage`, readable by any JavaScript on the origin. Given F-10 this is a direct session-theft path. The token is also a 7-day bearer token with no server-side revocation (F-14).

**Fix:** Prefer an httpOnly, Secure, SameSite cookie for the session so script cannot read it. If you keep a bearer token, pair it with the XSS fixes in F-10 and a CSP, and shorten its lifetime with refresh.

#### F-12. Hard-coded seed user with known password in all environments
**OWASP:** A07 Authentication Failures · A05 Misconfiguration
**Where:** [database/seed.sql:3](database/seed.sql#L3)

`seed.sql` inserts `imran@cognizance.vision` with the bcrypt hash of `happy123`, and `migrate.py` runs seeds against any environment including `prod`. This is a known-credential backdoor into production.

**Fix:** Remove the seeded password account. Do not run seed data in production. If a bootstrap admin is needed, create it out of band with a generated password and force a reset on first login.

#### F-13. Permissive CORS allowing any origin
**OWASP:** A05 Misconfiguration
**Where:** [backend/main.py:24-29](backend/main.py#L24)

`allow_origins=["*"]` with all methods and headers. Any website can call the API from a victim's browser. Because auth is a bearer header rather than cookies this is less severe than it looks, but it still broadens the reach of the unauthenticated endpoints (F-05, F-06) and would become critical if you move to cookie auth (F-11).

**Fix:** Restrict `allow_origins` to the known frontend origins. Do not use `*` together with credentials.

#### F-14. Weak JWT handling: default secret, no revocation, long lifetime
**OWASP:** A02 Cryptographic Failures · A07 Authentication Failures
**Where:** [backend/app/auth.py:9](backend/app/auth.py#L9), [auth.py:27](backend/app/auth.py#L27)

`JWT_SECRET` defaults to `"change-me-in-production"`; if the env var is ever unset the app signs tokens with a public constant, letting anyone forge sessions. Tokens last 7 days with no server-side invalidation, so sign-out and compromise cannot be remediated.

**Fix:** Fail startup if `JWT_SECRET` is missing rather than defaulting. Shorten token lifetime and add refresh. Add a revocation mechanism (token version per user, or a denylist) so sign-out and compromise are enforceable.

#### F-15. Google OAuth login without `state` or email-verification check
**OWASP:** A07 Authentication Failures
**Where:** [backend/app/routes/auth.py:41](backend/app/routes/auth.py#L41), [backend/app/auth.py:74](backend/app/auth.py#L74), frontend [Login.jsx:52](src/components/Login.jsx#L52)

The OAuth flow sends no `state` parameter, so it is open to login CSRF / code injection. The backend also links accounts by email without checking Google's `verified_email`, and `auth_google` links to an existing local account by email, so an attacker-controlled Google identity with a matching unverified email could attach to it.

**Fix:** Generate and validate an unguessable `state` value bound to the session. Require `verified_email == true` before trusting the email. Be explicit about account-linking rules and require proof of control of the existing account before linking.

### Medium

#### F-16. Vulnerable frontend dependencies
**OWASP:** A06 Vulnerable and Outdated Components
**Where:** [package.json](package.json), lockfile

`npm audit` reports 23 advisories (16 high). Notable direct dependencies: `vite` 7.3.1 (dev-server path traversal / arbitrary file read, `server.fs.deny` bypass), `react-router-dom` 7.13.0 (the `react-router` chain includes an RCE via vendored turbo-stream deserialization and several XSS/open-redirect issues), `postcss` (XSS via unescaped stringify). Transitive: `dompurify` (many XSS bypasses, via monaco-editor), `ws`, `js-yaml`, `minimatch`, `nanoid`, `rollup` path traversal.

**Fix:** Run `npm audit fix`, then bump `vite`, `react-router-dom`, and `postcss` to patched majors/minors and re-test. Several Vite issues are dev-server only, but the build toolchain still matters for CI. Add `npm audit` (or Dependabot) to CI.

#### F-17. No rate limiting on auth or expensive endpoints
**OWASP:** A07 Authentication Failures
**Where:** `/auth/signin`, `/auth/signup`, all LLM endpoints

Nothing throttles credential-guessing against `/auth/signin` or abuse of the LLM/query endpoints, which also cost money.

**Fix:** Add rate limiting (per IP and per account) on auth endpoints and spend caps / quotas on LLM and query endpoints. Add account lockout or exponential backoff on repeated failures.

#### F-18. Keyfile upload has no size, type, or ownership-path controls
**OWASP:** A04 Insecure Design
**Where:** [backend/app/routes/crud.py:273](backend/app/routes/crud.py#L273), storage [local.py:17](backend/app/storage/local.py#L17)

`upload_keyfile` reads the whole file into memory with no size limit (memory-exhaustion DoS) and no content-type/JSON validation, storing it under `secret-files/{user_id}/`. The `save_keyfile` AI tool writes to the same area and, with no user, falls back to the first user in the table ([ai_tools.py:599](backend/app/ai_tools.py#L599)).

**Fix:** Enforce a max upload size (streamed), validate that the content parses as a service-account JSON, and derive the storage path from the authenticated user only. Encrypt stored keyfiles at rest.

#### F-19. Internal error details and stack traces returned to clients
**OWASP:** A05 Misconfiguration
**Where:** throughout — e.g. [explore.py:129](backend/app/routes/explore.py#L129) `detail=f"Query execution failed: {str(e)}"`, [auth.py:85](backend/app/auth.py#L85) returns Google's raw error body, many `traceback.print_exc()` plus raw `str(e)` in responses.

Raw exception text and upstream error bodies are returned to callers and printed to logs, leaking schema names, file paths, and connection details. `print()` statements with query ids and SQL are scattered through the request paths.

**Fix:** Return generic error messages with a correlation id; log details server-side only. Replace `print` with a structured logger and scrub secrets.

#### F-20. Unpinned Python dependencies
**OWASP:** A06 Vulnerable Components · A08 Integrity Failures
**Where:** [backend/requirements.txt](backend/requirements.txt)

Every dependency is unpinned (no version, no hashes), so builds are non-reproducible and silently pull whatever is latest at deploy time, including a future malicious or breaking release. The Docker build installs these fresh on every deploy.

**Fix:** Pin exact versions and use a lockfile with hashes (`pip-compile`/`uv`/Poetry). Add `pip-audit` to CI.

#### F-21. `POST /boards` and `POST /datastores` trust the client's `organization_id`
**OWASP:** A01 Broken Access Control
**Where:** [crud.py:77](backend/app/routes/crud.py#L77), [crud.py:223](backend/app/routes/crud.py#L223)

Both insert using the `organization_id` from the body without verifying the user is a member of that org, so a user can create objects inside another tenant's organization.

**Fix:** Verify membership of the target organization before insert, as `GET /boards` does.

#### F-22. `/stats` and `/usage` leak existence/counts for arbitrary orgs
**OWASP:** A01 Broken Access Control
**Where:** [crud.py:417](backend/app/routes/crud.py#L417)

`get_stats` takes an `organization_id` and returns board/query counts without confirming membership (board count is join-scoped, but the query/other counts are not fully guarded), leaking activity about other orgs.

**Fix:** Require membership on the requested org before returning any counts.

#### F-23. SSRF via datastore host and Google token exchange
**OWASP:** A10 SSRF
**Where:** [query_engine.py:82](backend/app/query_engine.py#L82), [query_engine.py:28](backend/app/query_engine.py#L28)

Datastore connections and schema reads target user-specified hosts with no allowlist, so the backend can be pointed at internal addresses (metadata service, internal databases). `/test-datastore` reaches this unauthenticated.

**Fix:** Allowlist destination hosts, block link-local/internal ranges, and apply egress network policy on Cloud Run.

#### F-24. Local test harness calls backend over plain HTTP to a hard-coded localhost URL
**OWASP:** A05 Misconfiguration
**Where:** [backend/app/routes/ai.py:997](backend/app/routes/ai.py#L997), [ai.py:1146](backend/app/routes/ai.py#L1146)

The exploration helper makes an in-process HTTP call to `http://localhost:8000/explore` to test generated code, and it creates temporary `board_queries` rows against the first board in the table ([ai.py:982](backend/app/routes/ai.py#L982)). This couples internal logic to a hard-coded URL/port and writes rows into an arbitrary tenant's board.

**Fix:** Call the execution function directly instead of looping back over HTTP. Scope any temporary test rows to the caller and clean them up.

#### F-25. Widget `is_public` exposes content across all tenants
**OWASP:** A01 Broken Access Control
**Where:** [crud.py:345](backend/app/routes/crud.py#L345) (`WHERE ... OR is_public = true`)

Any widget marked public is returned to every user in every organization, with no org boundary. Combined with F-10 a public widget is a stored-XSS delivery vector to all users.

**Fix:** Decide the intended sharing scope (per-org vs global) and enforce it. Keep public user HTML sandboxed and off the app origin.

### Low

#### F-26. Legacy `OLDMAIN.PY` retained in the repo
**Where:** [backend/OLDMAIN.PY](backend/OLDMAIN.PY)
A 3,692-line Supabase-era file with its own endpoints and credential handling. Dead code expands the attack surface and confuses audits. **Fix:** delete it.

#### F-27. `.env.example` documents a weak default `JWT_SECRET`
**Where:** [.env.example](.env.example)
Ships `JWT_SECRET=change-me-in-production` as the documented value; combined with F-14's code default this invites shipping the placeholder. **Fix:** leave it blank and fail startup if unset.

#### F-28. No security headers / CSP on the frontend
**Where:** [firebase.json](firebase.json), app
No CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, or HSTS are configured. **Fix:** add security headers via Firebase Hosting config; a CSP meaningfully mitigates F-10.

#### F-29. `exec`/execution debug output via `print`
**Where:** explore/query paths
`print("DEBUG: ...")` with SQL and ids ends up in Cloud Run logs. **Fix:** structured logging with levels, no secrets.

#### F-30. No password strength or email validation at signup
**Where:** [backend/app/routes/auth.py:14](backend/app/routes/auth.py#L14)
`email`/`password` are accepted as raw strings with no format or strength checks. **Fix:** validate email format and enforce a minimum password policy; consider verification email.

#### F-31. `--allow-unauthenticated` Cloud Run service
**Where:** [.github/workflows/deploy-main.yml](.github/workflows/deploy-main.yml#L61)
The backend is deployed publicly, which is expected for a public API but means every F-05/F-06 unauthenticated endpoint is internet-reachable. **Fix:** keep public only after the auth gaps are closed; consider a WAF / API gateway in front.

#### F-32. Container likely runs as root
**Where:** Dockerfile on `main`
No `USER` directive, so the process runs as root, worsening the impact of F-01/F-03. **Fix:** add a non-root `USER` and a read-only root filesystem where possible.

---

## 4. Positive observations

- SQL to the application's own Postgres uses asyncpg parameterized queries (`$1, $2`) consistently; no first-order SQL injection was found in the app database layer. The injection risk is in the *user-datastore* query path (F-02), not the app's own queries.
- Passwords are hashed with bcrypt and a per-hash salt ([auth.py:19](backend/app/auth.py#L19)).
- A few handlers model authorization correctly and should be the template for the rest: `PATCH /organizations/{id}` (role check), `GET /boards` (membership join), and the widget owner checks.
- Secrets are injected into Cloud Run from Secret Manager rather than baked into the image, and `.gitignore` / `.dockerignore` exclude `.env` and `storage/`.
- **No committed secrets were found** in the tracked tree or in the git history scan (patterns for API keys, private keys, JWTs, AWS keys, and credential-bearing DB URLs). The only in-repo credential is the seeded `happy123` (F-12).

---

## 5. Prioritized remediation plan

**P0 — before any untrusted exposure**
- F-05, F-06: require authentication on all data and AI endpoints; default-deny.
- F-07, F-08, F-21, F-22, F-25: add a tenant authorization layer; scope every by-id and tool query to the caller.
- F-01, F-02, F-03: sandbox code and query execution; parameterize SQL; constrain datastore types and build connection URLs from validated fields.
- F-04: stop returning datastore secrets; encrypt at rest; rotate exposed credentials.
- F-12: remove the seeded production account.

**P1 — soon after**
- F-09: least-privilege the agent; gate destructive tools; cap iterations.
- F-10, F-11, F-28: sandbox user HTML off-origin, add CSP, move session off `localStorage`.
- F-14, F-15: fix JWT defaults/lifetime/revocation and OAuth `state` + verified-email.
- F-13: lock down CORS.
- F-17: rate limiting and spend caps.

**P2 — hardening**
- F-16, F-20: patch frontend deps, pin and lock Python deps, add audits to CI.
- F-18, F-19, F-23, F-24, F-29, F-30, F-31, F-32: upload limits, error hygiene, SSRF egress policy, internal loopback call, non-root container, input validation.
- F-26, F-27: delete legacy code, blank the example secret.

---

*Prepared as a point-in-time static review. It does not replace dynamic testing or a penetration test against a running, authenticated instance, which is recommended once the P0 items are addressed.*
