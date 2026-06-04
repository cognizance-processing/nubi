# Nubi — Tasks (M0: Foundation)

Execution backlog for the **M0 Foundation** milestone (see ROADMAP.md §10). Built by waves
of Sonnet agents. This file is the **shared contract** — every agent reads §1–§3 first so
parallel work does not diverge on schema, API shape, or layout.

> Scope of M0: clean **React** frontend + **FastAPI/Python** backend rebuilt from scratch on
> **Neon Postgres**. Auth = email/password (access + rotating refresh, reuse detection) +
> Google OAuth. Migrations from scratch. Domain tables (orgs, datastores, boards, queries,
> widgets, chats) carried forward as stubs so later milestones have a home.

---

## 1. Stack contract (do not deviate without updating this file)

**Backend**
- Python 3.11+, **FastAPI**, **uvicorn**, pydantic v2.
- DB: **Neon Postgres** via **asyncpg** (connection pool). SSL required (`sslmode=require`).
- Migrations: plain SQL files + a Python runner (`database/migrate.py`), forward-only,
  numbered `NNNN_name.sql`. No ORM; raw SQL via asyncpg.
- Passwords: **argon2id** (`argon2-cffi`). Never bcrypt unless argon2 unavailable.
- Tokens: access = **JWT HS256** (env `JWT_SECRET`), TTL **15 min**. Refresh = **opaque
  random 256-bit**, stored **hashed (SHA-256)** in `sessions`, rotated on every use, with
  **reuse detection** (presenting a rotated/used token revokes the whole family).
- Google OAuth: **Authorization Code + PKCE**, backend exchanges code, links by verified
  email, stores in `oauth_accounts`.
- Config via env (pydantic-settings). Lint: ruff. Tests: pytest + httpx AsyncClient.

**Frontend**
- React 19 + Vite + TailwindCSS + react-router-dom (already chosen).
- Auth state in a context; **access token in memory only**; refresh via **httpOnly secure
  cookie** set by backend.
- API client wrapper with a 401 interceptor that calls `/auth/refresh` once then retries.
- Google sign-in via redirect flow (backend `/auth/google/start` → `/auth/google/callback`).
- Pages: Landing, Login, Register, Dashboard (placeholder), 404. Protected-route wrapper.

**Token delivery decision (locked):** access token returned in JSON body (held in memory);
refresh token set as `HttpOnly; Secure; SameSite=Lax` cookie scoped to `/auth`. Logout
clears the cookie and revokes the session family.

---

## 2. Database schema contract (M0 migrations)

`0001_extensions.sql` — `citext`, `pgcrypto` (for `gen_random_uuid()`).

`0002_users.sql`
- `users(id uuid pk default gen_random_uuid(), email citext unique not null,
  password_hash text null, email_verified bool not null default false, name text,
  avatar_url text, created_at timestamptz default now(), updated_at timestamptz default now())`
- `password_hash` nullable → OAuth-only users have no password.

`0003_oauth_accounts.sql`
- `oauth_accounts(id uuid pk, user_id uuid fk users on delete cascade, provider text not
  null, provider_account_id text not null, created_at timestamptz default now(),
  unique(provider, provider_account_id))`

`0004_sessions.sql` (refresh-token families)
- `sessions(id uuid pk, user_id uuid fk users on delete cascade, token_hash text not null
  unique, family_id uuid not null, parent_id uuid null, expires_at timestamptz not null,
  revoked_at timestamptz null, user_agent text, ip inet, created_at timestamptz default now())`
- Index on `(user_id)`, `(family_id)`, `(token_hash)`.

`0005_orgs.sql` (multi-tenancy spine, carried forward)
- `orgs(id uuid pk, name text not null, slug citext unique not null, created_at ...)`
- `org_members(org_id fk, user_id fk, role text not null default 'member', primary key
  (org_id, user_id))`
- On register, create a personal org + owner membership.

`0006_domain_stubs.sql` (homes for later milestones; minimal columns now)
- `datastores`, `boards`, `queries`, `widgets`, `chats` — each `id uuid pk, org_id fk,
  created_by fk users, name text, created_at, updated_at` plus a `config jsonb default '{}'`.
  Just enough to build CRUD on later; no business logic in M0.

---

## 3. API contract (M0 endpoints)

Base: `/api/v1`. All JSON. Errors as `{ "error": { "code", "message" } }`.

**Auth**
- `POST /auth/register` `{email, password, name}` → `201 {user, access_token}` + refresh cookie.
- `POST /auth/login` `{email, password}` → `200 {user, access_token}` + refresh cookie.
- `POST /auth/refresh` (refresh cookie) → `200 {access_token}` + rotated refresh cookie.
  On reuse of a consumed token → `401` + revoke family.
- `POST /auth/logout` (refresh cookie) → `204`, revoke family, clear cookie.
- `GET  /auth/me` (Bearer access) → `200 {user}`.
- `GET  /auth/google/start` → `302` to Google (PKCE, state cookie).
- `GET  /auth/google/callback?code&state` → exchanges, links/creates user, sets refresh
  cookie, `302` to frontend with a short-lived handoff or sets access via the refresh flow.

**Health**
- `GET /health` → `200 {status:"ok", db:"ok"}`.

`user` shape: `{id, email, name, avatar_url, email_verified, created_at}`.

---

## 4. Task backlog (waves)

Each task: scope → key files → done-criteria. Agents must keep to §1–§3 contracts.

### Wave A — scaffolding & schema (parallel; disjoint files)
- **A1. Backend scaffold.** FastAPI app, settings, asyncpg pool lifespan, CORS, error
  handler, `/health`. Files: `backend/main.py`, `backend/app/config.py`, `backend/app/db.py`,
  `backend/app/errors.py`, `backend/requirements.txt`, `backend/Dockerfile`.
  Done: `GET /health` returns db:ok against Neon.
- **A2. Migrations + runner.** All `0001–0006` SQL + `database/migrate.py` (apply, status).
  Files: `database/migrations/*.sql`, `database/migrate.py`.
  Done: `python database/migrate.py` applies cleanly; `--status` lists applied.
- **A3. Frontend scaffold.** Vite+React+Tailwind app shell, router, layout, API client
  wrapper with refresh interceptor, env wiring. Files: `src/main.jsx`, `src/App.jsx`,
  `src/lib/api.js`, `src/layouts/*`, `tailwind`/`vite` configs.
  Done: app builds, renders Landing, 404 works.

### Wave B — auth core (depends on A1, A2)
- **B1. Password auth.** argon2 hashing, register/login/me, JWT access mint/verify, Bearer
  dependency. Files: `backend/app/auth/passwords.py`, `backend/app/auth/jwt.py`,
  `backend/app/auth/deps.py`, `backend/app/routes/auth.py`.
- **B2. Refresh sessions.** Opaque refresh issue/rotate/revoke, family reuse-detection,
  cookie helpers, logout. Files: `backend/app/auth/sessions.py`, extend `routes/auth.py`.
- **B3. Google OAuth.** PKCE start/callback, code exchange (httpx), link-by-email,
  `oauth_accounts`. Files: `backend/app/auth/google.py`, extend `routes/auth.py`.
  Done (B1–B3): full auth flow exercised by tests in Wave D.

### Wave C — frontend auth (depends on A3 + B contract)
- **C1. Auth context + flows.** Context, login/register pages, Google button, protected
  route, token-in-memory + refresh-on-401, logout. Files: `src/contexts/AuthContext.jsx`,
  `src/pages/Login.jsx`, `src/pages/Register.jsx`, `src/components/ProtectedRoute.jsx`,
  `src/pages/Dashboard.jsx` (placeholder behind auth).
  Done: register→login→see Dashboard→refresh persists→logout, against the live backend.

### Wave D — integration, tests, hardening (depends on B, C)
- **D1. Backend tests.** pytest for register/login/refresh-rotation/reuse-revoke/logout/me
  + Google callback (mocked token endpoint). Files: `backend/tests/test_auth.py`,
  `backend/tests/conftest.py`.
- **D2. Security review.** Adversarial pass on the auth code: argon2 params, JWT alg
  pinning, refresh reuse-detection correctness, cookie flags, CORS, SQL via parameterized
  asyncpg only, no secrets logged. Output: findings + fixes.
- **D3. Docs + env.** Update `README.md` (run steps), `.env.example` (Neon URL, JWT_SECRET,
  Google creds, frontend/backend URLs).

---

## 5. Conventions
- Forward-only migrations; never edit an applied migration.
- All SQL parameterized (asyncpg `$1`), never f-string interpolation.
- No secret in logs or error responses.
- Small modules, typed signatures, docstrings on public functions.
- Each agent runs lint before declaring done (ruff for Python, eslint for JS).

---

## 6. Later milestones (not M0 — see ROADMAP.md)
M1 WASM runtime + first connector (plan/execute split, conformance suite) · M2 Arrow
streaming + edge cache + pushdown · M3 auth-as-code + JWT/JWKS + read-only embed · M4
on-demand kernel · M5 WebGL/WebGPU viz · M6 API/SDK/CLI · M7 lineage + AI + MCP · M8
embedded editor · M9 connectors + Python connector SDK · M10 self-hosted · M11 scheduled
jobs · M12 API & NoSQL sources.

---

# M1 — WASM runtime + first connector (plan/execute split + conformance)

Prove kernel-in-browser end to end against one real SQL source, with the carve-out seam
(ROADMAP §3.1) honoured from day one: planner (sqlglot) → serialized physical plan →
executor → Arrow IPC, plus a frozen cache-key spec and a conformance suite.

## M1 contracts (shared — read before building)

**Physical plan** (`backend/app/connectors/plan.py`) — a serializable dataclass/pydantic
model: `{ dialect: str, sql: str, params: list, projection: list[str]|None,
predicates: list[str], rls_claims: dict, cache_key: str }`. Produced by the planner,
consumed by the executor. JSON-serializable; this is the language-neutral boundary.

**Cache-key spec** (`backend/app/connectors/cache_key.py` + `docs/cache-key-spec.md`) —
`cache_key = sha256(canonical_json({ "sql": sql, "params": params,
"rls": sorted RLS-affecting claims }))`, hex. Canonical JSON = sorted keys, no whitespace,
UTF-8. MUST ship **test vectors** (input → expected hex) so a future Rust executor matches
byte-for-byte. Cache key derives from the SAME rls claim subset the predicate injector uses.

**Capability descriptor** — each connector exposes `capabilities()` →
`{native_arrow, predicate_pushdown, projection_pushdown, partition_pushdown, predicate_rls,
column_masking, streaming_cdc}` (bools).

**Executor interface** (`backend/app/connectors/base.py`) —
`Connector.execute(plan) -> pyarrow.Table` (and `execute_stream(plan) -> Iterator[RecordBatch]`).
Postgres/Neon connector uses **ADBC** (`adbc-driver-postgresql`) for native Arrow; a
**DuckDB** connector (`duckdb` python) is the deterministic fixture/local engine for the
conformance suite (no network).

**Query API** — `POST /api/v1/query` body `{datastore_id?, sql, params?, claims?}` →
responds `200` with **Arrow IPC stream** bytes, `Content-Type:
application/vnd.apache.arrow.stream`. Pipeline: plan(sql, claims) → cache lookup by
cache_key → on miss execute + cache → stream Arrow. Content-addressed cache is an in-memory
LRU for M1 (interface allows Redis later).

**Predicate injection (RLS)** — planner injects `claims.policies` as AST predicates via
sqlglot (parse → add WHERE → regenerate), NEVER string-concat. M1 supports simple
`{column: value}` equality policies.

## M1 task waves

### Wave M1-A — connector core (backend; planner + contracts)
- `connectors/plan.py` (PhysicalPlan), `connectors/cache_key.py` (+ test vectors),
  `connectors/base.py` (Connector ABC + capabilities), `connectors/planner.py` (sqlglot:
  parse SQL, apply projection/predicate + RLS injection, emit PhysicalPlan + cache_key),
  `docs/cache-key-spec.md`. Add `sqlglot`, `pyarrow` to requirements.
- Done: planner unit tests green; cache-key test vectors stable; `py_compile` clean.

### Wave M1-B — executors + query endpoint (backend; depends on A)
- `connectors/postgres.py` (ADBC → Arrow), `connectors/duckdb_conn.py` (DuckDB → Arrow,
  fixture engine), `cache.py` (LRU content-addressed), `routes/query.py`
  (`POST /api/v1/query` → Arrow IPC stream). Add `adbc-driver-postgresql`,
  `adbc-driver-manager`, `duckdb` to requirements.
- Done: endpoint returns valid Arrow IPC for a DuckDB-backed query; `py_compile` clean.

### Wave M1-C — conformance suite (backend; depends on A,B)
- `backend/tests/conformance/` — golden fixtures `(sql, claims, seed-data) → expected Arrow
  (feather) + expected cache_key`. Runs via the DuckDB connector for determinism. A
  `conftest` seeds an in-memory DuckDB. Assert Arrow schema+rows and cache_key match goldens.
- Done: `pytest backend/tests/conformance` green; documented as the suite a Rust executor
  must pass.

### Wave M1-D — frontend WASM runtime (frontend; parallel, builds vs the query contract)
- Add `@duckdb/duckdb-wasm` and `apache-arrow`. `src/lib/wasmRuntime.js`: init DuckDB-WASM
  (lazy), `runArrowQuery(sql)` → fetch `POST /api/v1/query` → read Arrow IPC into an Arrow
  Table → register in DuckDB-WASM. `src/components/QueryCell.jsx`: a textarea SQL input +
  "Run" → renders results as a table (first 100 rows) from the Arrow Table. Wire a
  `/playground` route (behind ProtectedRoute) hosting a QueryCell.
- Done: `npm run build` green; QueryCell renders a static/sample Arrow table if backend
  unavailable (graceful).

---

# M2 — Arrow streaming + edge cache + pushdown rewriter + auto-pre-agg seed

Make the latency/cost story real: stream Arrow batch-by-batch, a content-hashed cache that
collapses identical-query viewers, an aggressive pushdown optimizer, and the seed of
automatic pre-aggregations (query-log mining → rollup suggestions → rollup routing).

## M2 contracts
- **Streaming:** `POST /api/v1/query` returns a `StreamingResponse` of Arrow IPC stream
  chunks (record-batch granularity), `Content-Type: application/vnd.apache.arrow.stream`.
  Response header `X-Nubi-Cache: HIT|MISS`. On cache HIT, stream cached IPC bytes; on MISS,
  execute, tee to cache, stream.
- **Cache:** extend `ContentAddressedCache` with per-entry TTL + hit/miss counters; expose
  `GET /api/v1/_cache/stats` (auth) → `{entries, hits, misses, hit_rate}`. Key stays
  `plan.cache_key`.
- **Pushdown optimizer** (`connectors/optimize.py`, called from planner): projection
  pruning (select only needed cols), predicate pushdown into WHERE, LIMIT pushdown, and
  `partition_hints` extraction. Planner signature gains `limit` and `predicates`. Must not
  break M1 conformance — RLS predicates still injected, cache_key recomputed after optimize.
- **Auto pre-agg seed:** `connectors/query_log.py` records `{cache_key, sql, groupby_sig,
  ts, bytes}` (in-memory ring buffer); `connectors/preagg.py` `suggest(log) ->
  [RollupSuggestion{base_table, dimensions, measures, hits, est_bytes_saved}]` for GROUP BY
  patterns seen ≥ N times; planner `route_to_rollup(plan, registry)` rewrites to a
  registered rollup table when one covers the query. `GET /api/v1/_preagg/suggestions`
  (auth) returns current suggestions.

## M2 task waves
- **M2-A** (backend): pushdown optimizer + planner integration + tests. Files:
  `connectors/optimize.py`, extend `connectors/planner.py`, `tests/test_optimize.py`.
  Done: optimizer tests green; M1 conformance still green.
- **M2-B** (backend): streaming endpoint + cache TTL/stats + `_cache/stats`. Files: extend
  `connectors/cache.py`, `routes/query.py`, `arrow_io.py`. Done: endpoint streams; HIT/MISS
  header correct; stats endpoint works; existing tests green.
- **M2-C** (backend; after A): query-log + pre-agg suggester + rollup routing +
  `_preagg/suggestions`. Files: `connectors/query_log.py`, `connectors/preagg.py`, extend
  planner with `route_to_rollup`, `routes/insights.py`, `tests/test_preagg.py`.
- **M2-D** (frontend; after B): consume the Arrow IPC **stream** incrementally in
  `wasmRuntime.js` (RecordBatchReader over the response stream); QueryCell shows a cache
  badge (HIT/MISS) + elapsed ms + streamed-rows counter; a small "Pre-agg suggestions"
  panel on Playground calling `_preagg/suggestions`. Done: `npm run build` green.

---

# M3 — auth-as-code + JWT/JWKS + read-only embed

Same JWT + predicate-injection primitive powers core auth AND embedding. Host signs
short-lived JWTs (verified via JWKS), claims carry RLS policies + scopes + origin; the
connector enforces them server-side. Ship `<nubi-dashboard>` + the `getToken()` contract.

## M3 contracts
- **Unified token verification** (`backend/app/auth/verify.py`): accept EITHER a first-party
  Nubi HS256 access token OR a host-signed embed JWT verified via **JWKS** (RS256/ES256).
  Embed claims: `{iss, sub(user_id), org, project, roles[], policies{col:val}, scope[],
  embed_origin, exp(≤15m), aud}`. JWKS fetched from a registered issuer's `jwks_uri`, cached
  with TTL, `alg` pinned to the key type, `none` rejected, `exp`/`aud`/`iss` validated.
- **Issuer registry** (`backend/app/auth/issuers.py`): in-memory map `iss -> {jwks_uri, aud,
  allowed_origins[]}` (later DB-backed). For tests, allow registering a static JWKS/public key.
- **Server-side RLS enforcement:** `/query` derives `policies` from the VERIFIED token, NOT
  from the request body (body.claims is ignored when a token carries policies). Policies →
  planner predicate injection (existing). Scope gate: embed read requires
  `scope` containing `read:*` / `read:dashboard:*`. Origin pinning: if `embed_origin` present,
  the request `Origin` header must match (else 403).
- **`getToken()` contract:** SDK exposes `getToken: () => Promise<string>`; on `exp`
  approaching, re-call host to mint a fresh JWT (silent refresh). Document + reference impl.
- **`<nubi-dashboard>` web component:** framework-agnostic custom element
  `<nubi-dashboard query="..." token="..." backend="...">` (or `dashboard-id`), read-only:
  fetches Arrow via `/query` with the token, renders a table (chart later). Shadow DOM,
  CSS-var theming. Built as a standalone bundle (vite lib entry) so a host drops in one script.

## M3 task waves
- **M3-A** (backend): `auth/verify.py` (unified HS256 + JWKS verifier), `auth/issuers.py`,
  `auth/scopes.py` (scope check helpers). Tests with a locally-generated RSA keypair acting
  as the "host" issuer + a static JWKS. Done: verify accepts both token types, rejects
  bad alg/exp/iss/aud; tests green.
- **M3-B** (backend; after A): wire into `/query` — verified-token policies override body,
  scope gate, origin pinning; add `routes/embed.py` `GET /embed/config/{id}` stub returning a
  read-only widget descriptor. Tests: embed token with `policies{tenant_id:acme}` returns
  only acme rows (server-enforced RLS); wrong origin → 403; missing read scope → 403.
- **M3-C** (frontend; parallel, builds vs contract): `<nubi-dashboard>` web component in
  `embed/nubi-dashboard.js` + a vite lib build config `vite.embed.config.js` producing
  `dist-embed/nubi-dashboard.js`; `embed/demo.html` host page using a mock `getToken()`;
  reference `getToken()` in `embed/getToken.reference.js`. Done: embed bundle builds; demo
  renders sample data without a live backend.
- **M3-SEC** (after A,B): adversarial review of embed auth — JWKS `alg` confusion, `none`,
  key-injection, exp/aud/iss bypass, origin spoofing, body-policy override bypass, scope
  escalation. Apply high-confidence fixes; flag the rest.

---

# M4 — On-demand kernel (escape hatch) + compute-placement router

The safety valve for native wheels / big jobs / >browser-cap (ROADMAP §6). Build the
verifiable core now; the real remote sandbox (Modal/E2B) is a stubbed interface (external
infra). The router decides which tier runs a cell.

## M4 contracts
- **`KernelRunner` ABC** (`backend/app/compute/runner.py`): `run(code:str,
  inputs:dict[str, pyarrow.Table], timeout_s:int) -> KernelResult{table, stdout, tier,
  elapsed_ms}`.
- **`LocalSubprocessRunner`**: executes the cell in a SUBPROCESS — inputs written as Arrow
  IPC temp files, the snippet binds them (e.g. `inputs['df']` as a pyarrow Table / pandas),
  must assign `result` (a table/df) which is serialized back as Arrow IPC. Hard timeout
  (kill on expiry), restricted env (do NOT inherit app secrets — pass a scrubbed env),
  output size cap. **SECURITY: dev-grade isolation only — first-party callers ONLY; prod
  MUST use a real sandbox (Modal/E2B/gVisor). Document this loudly.**
- **`RemoteRunner` stub**: Modal/E2B-shaped interface; `run(...)` raises
  `AppError("kernel_unavailable",503)` when not configured.
- **`ComputePlacementRouter`** (`backend/app/compute/router.py`): `place(cell) -> tier` where
  cell = `{kind:'sql'|'python', est_rows:int, libs:list[str], needs_native_wheel:bool}`.
  Rules per ROADMAP §6: sql→'warehouse'; python small + Pyodide-portable→'browser'; native
  wheel or est_rows>browser_cap or libs∉pyodide→'local_kernel' (or 'remote_kernel' when
  remote configured). Pure logic, table-driven, fully unit-tested.
- **`POST /compute/run`** (`backend/app/routes/compute.py`): `verified_identity` dep, **reject
  embed kind → 403** (only first-party may run code). Body `{code, input_query_id?, timeout_s?}`
  — if input_query_id given, resolve+execute that registered query to an Arrow table and bind
  as `inputs['input']`. Run via LocalSubprocessRunner. Return Arrow IPC of `result`. Meter:
  record kernel-seconds (stub a metering hook for billing).

## M4 task waves
- **M4-A** (backend): runner.py (ABC + Local + Remote stub), router.py, routes/compute.py,
  metering hook stub `compute/metering.py`. Tests test_kernel.py + test_router.py: local
  runner round-trips a trivial transform to Arrow; timeout kills a sleep; embed token →403;
  router picks correct tier for each cell shape; remote stub →503. Add no heavy deps (use
  stdlib subprocess + pyarrow already present; pandas optional — snippet may use pyarrow).
- **M4-B** (frontend; vs contract): Playground gets a "Python cell" — code textarea + Run →
  POST /compute/run, render the Arrow result table + a tier badge (local_kernel) + elapsed.
  Graceful sample fallback. `npm run build` green.
- **M4-SEC** (after A): adversarial review of code execution — env scrubbing (no secret
  leakage to the subprocess), timeout/kill correctness, output-size DoS, path traversal via
  temp files, embed-forbidden enforcement, resource limits. Apply fixes; flag residual
  (the fundamental "subprocess ≠ real sandbox" risk → documented, remote runner is the prod
  answer).

---

# M5 — WebGL viz layer (the demo moment)

Render Arrow buffers on the GPU: 1M+ point scatter at interactive framerates, reading the
columnar buffers directly. This is the "feel it" demo.

## M5 contracts
- **GPU scatter** (`src/viz/`): a regl-based (lightweight WebGL) scatter renderer that takes
  Arrow columns (x, y, optional color/category) and draws points to a canvas. Must handle
  100k–1M points smoothly; graceful 2D-canvas fallback when WebGL is unavailable.
- **Chart component** `src/components/Chart.jsx`: props `{table, xCol, yCol, colorCol?}`;
  reads the Arrow Table's vectors directly (no row materialization), normalizes to clip
  space, renders via the regl renderer; shows point count + fps.
- **Integration:** Playground gains column pickers + a "Render" action on a query/python
  result → GPU scatter. A "generate N points" demo path (client-side synthetic Arrow) proves
  framerate without a backend.
- **Backend point-cloud source** (small): registered queries using DuckDB `generate_series`
  /`random()` to emit a large synthetic point cloud (`demo_points` with x, y, category) so
  the GPU demo runs end-to-end through the real Arrow streaming pipeline.

## M5 task waves
- **M5-A** (frontend): `src/viz/scatterRenderer.js` (regl WebGL points + fallback),
  `src/components/Chart.jsx`, wire into Playground (column pickers + Render + a "generate
  500k points" demo button). Add `regl` dep. Done: `npm run build` green; renders synthetic
  data with no backend; fps + count shown.
- **M5-B** (backend): register `demo_points` (and a couple sizes) via DuckDB generate_series
  in `app/queries/registry.py` (or a small seed module) so `/query` (and embed allowlist)
  can stream a large point cloud; a test asserting the query returns N rows as Arrow. Keep
  conformance unchanged.

---

# M6 — REST API + JS SDK + CLI (dashboards-as-code)

Make resources reachable from machines. "The API is the resource tree." Resource CRUD over
REST, a JS SDK wrapping auth+query+embed+CRUD, and a CLI for deploy/run/diff.

## M6 contracts
- **Testability without a live DB:** introduce a repository layer
  (`backend/app/repos/`): a `Repo` protocol with an **asyncpg** implementation (prod) and an
  **in-memory** implementation (tests). Routes depend on `get_repo()`; tests inject the
  in-memory repo. This is how CRUD gets tested with no Neon.
- **REST CRUD** (`/api/v1`), org-scoped, `current_user` (first-party) auth, ownership via
  `created_by`, org via the user's personal org membership. Resources: `datastores`,
  `boards`, `widgets`, `queries`. Each shape `{id, org_id, created_by, name, config(jsonb),
  created_at, updated_at}`. Endpoints per resource:
  `GET /{r}` (list, org-scoped) · `POST /{r}` `{name, config}` → 201 · `GET /{r}/{id}` ·
  `PUT /{r}/{id}` `{name?, config?}` · `DELETE /{r}/{id}` → 204. 404 on cross-org access
  (no leak). Errors as the standard `{error:{code,message}}`.
- **JS SDK** (`sdk/`): `createNubiClient({baseUrl, getToken})` exposing `.auth`,
  `.query(sqlOrId) -> Arrow Table`, `.resources.{datastores,boards,widgets,queries}.{list,
  get,create,update,remove}`, and `.embed.mount(el, {query, token})`. Framework-agnostic ESM;
  builds to `sdk/dist`.
- **CLI** (`cli/`): a Python (typer) `nubi` CLI talking to the REST API: `nubi login`,
  `nubi deploy <dir>` (push dashboard JSON/JSX resources), `nubi run <query_id>`, `nubi diff
  <dir>` (compare local dashboards-as-code vs server). Dashboards-as-code = JSON files on
  disk mapping to resources. Include `--dry-run`.

## M6 task waves
- **M6-A** (backend): `repos/` (protocol + asyncpg + in-memory), `routes/resources.py`
  (generic CRUD for the 4 resources, org-scoped, auth, ownership, no cross-org leak),
  register on api_router + import in main. Tests `test_resources.py` via the in-memory repo:
  create/list/get/update/delete happy paths + cross-org 404 + unauth 401. Keep all prior
  tests + conformance green.
- **M6-B** (JS SDK; vs contract): `sdk/` package (package.json, src/index.js + modules),
  `createNubiClient`, builds to `sdk/dist` (vite/rollup lib). A node smoke test or a build
  check. Reuses Arrow parsing for `.query`.
- **M6-C** (CLI; vs contract): `cli/` typer app `nubi` with login/deploy/run/diff +
  `--dry-run`; `cli/requirements.txt`; tests for arg parsing + a dry-run deploy against a
  mocked API (httpx mock / responses). `python -m py_compile` + tests green.

---

# M7 — Lineage index + AI grounding + MCP server

The compounding moat: a lineage graph over SQL, retrieval that grounds an LLM in REAL
columns/tables (not hallucinated), and an MCP server so agents can author dashboards.

## M7 contracts
- **Lineage** (`backend/app/lineage/`): `extract_lineage(sql) -> {tables:[...],
  columns:[{table,column}], outputs:[...]}` via sqlglot (use sqlglot's lineage/qualify where
  helpful). `build_graph(registered_queries) -> LineageGraph` mapping query_id → tables →
  columns; expose `GET /lineage` (auth) returning the graph and `GET /lineage/query/{id}`.
  Deterministic; no LLM.
- **AI grounding** (`backend/app/ai/`): `LLMProvider` abstraction with `NullProvider` (no
  network — returns a templated/echo response for tests) and lazy real providers
  (Anthropic/OpenAI/Gemini via env keys, picked by config; only the configured one loads).
  `ground(question, catalog) -> GroundingContext{relevant_tables, relevant_columns,
  snippets}` — deterministic keyword/overlap ranking over the lineage catalog + registered
  queries (NO LLM needed for grounding itself). `POST /ai/ask {question}` → returns grounding
  context + (if a provider is configured) a generated SQL suggestion; with NullProvider it
  returns grounding + a templated suggestion. Tests use NullProvider.
- **MCP server** (`mcp/`): a standalone MCP server (python `mcp` sdk, stdio) exposing tools:
  `list_dashboards` (repo), `run_query` (query pipeline / registry), `list_lineage`,
  `propose_materialized_view` (preagg.suggest). Tools call into the existing app modules.
  Test: tools registered; a tool invocation returns expected shape against in-memory
  repo/mocks (don't require a live server).

## M7 task waves
- **M7-A** (backend): `lineage/extract.py`, `lineage/graph.py`, `routes/lineage.py`, tests
  `test_lineage.py` (extract tables/columns from sample SQL incl joins + aliases; graph over
  registered queries; endpoint auth). conformance unchanged.
- **M7-B** (backend; after A): `ai/provider.py` (LLMProvider + NullProvider + lazy real
  stubs), `ai/grounding.py` (deterministic retrieval over lineage+registry), `routes/ai.py`
  `POST /ai/ask`, tests `test_ai.py` with NullProvider (grounding picks the right tables for a
  question; endpoint works; no network).
- **M7-C** (MCP; parallel): `mcp/server.py` + `mcp/requirements.txt` exposing the 4 tools
  wired to repo/query/lineage/preagg; `mcp/tests/` verifying tool registration + one
  invocation each via mocks. `python -m py_compile`.

---

# M9 — Python connector SDK + API/HTTP source ("SQL first, flexible beyond")

Realize the capability contract (ROADMAP §4.1): SQL sources are first-class; anything else
is "return an Arrow table + declare capabilities." Proves the flexibility valve (python/API
now, NoSQL later) AND that RLS is enforced server-side even when a source can't push down.

## M9 contracts
- **Connector registry** (`backend/app/connectors/registry.py`): `register(type, factory)`,
  `get(type)`, `all()`; pre-register `postgres`, `duckdb`, plus the new ones below.
- **Connector SDK** (`backend/app/connectors/sdk.py`):
  - `FunctionConnector(Connector)`: wraps `fn(params) -> pyarrow.Table` as a first-class
    connector with a declared `capabilities()` dict. The "return an Arrow table" valve.
  - `apply_rls_postfetch(table, policies) -> table`: filter an Arrow table by equality
    policies using pyarrow.compute — **server-side RLS for sources that can't push down**
    (the security property must hold for non-SQL sources too; never the browser).
  - `apply_projection_postfetch` / `apply_limit_postfetch` helpers for non-pushdown sources.
  - Convention: a connector whose caps have `predicate_pushdown=False` but
    `predicate_rls=True` MUST apply `apply_rls_postfetch` before returning. Document loudly.
- **HTTP/API source** (`backend/app/connectors/http_json.py`): `HttpJsonConnector(config{url,
  record_path, headers?})` — fetch JSON (httpx, lazy), normalize a list-of-records to Arrow,
  then post-fetch RLS + projection + limit. caps: native_arrow=False, predicate_pushdown=False,
  projection_pushdown=False, predicate_rls=True, column_masking=False, streaming_cdc=False.
- **NoSQL stub** (`backend/app/connectors/mongo_stub.py`): a Mongo-shaped connector that
  declares `predicate_rls=False` and whose `execute` raises `AppError("source_unsupported_rls",
  501)` — demonstrating the planner/capability contract REFUSING a source it can't secure
  (the "needs a separate RLS story before we ship it" rule). Documents the NoSQL path.

## M9 task waves
- **M9-A** (backend): `connectors/registry.py`, `connectors/sdk.py` (FunctionConnector +
  post-fetch RLS/projection/limit helpers), tests `test_connector_sdk.py`: FunctionConnector
  returns Arrow; `apply_rls_postfetch` filters rows by policy (acme rows only; globex absent —
  the security guard); projection/limit helpers work; registry register/get. conformance
  unchanged.
- **M9-B** (backend; after A): `connectors/http_json.py` (+ mocked-httpx tests proving
  post-fetch RLS actually drops non-matching tenant rows from a JSON API response),
  `connectors/mongo_stub.py` (+ test that it raises source_unsupported_rls so the contract
  refuses an unsecurable source), register both in the registry, `docs/connectors.md`
  authoring guide. Whole suite + conformance green.

---

# M10 — Runnable self-hosted stack (Docker Compose) + live smoke test

Turn the 9 milestones into something that launches with one command, and actually run it
end to end (local Postgres = same code path as Neon). Closes the "never run against live
infra" gap.

## M10 contracts
- **`docker-compose.yml`**: services `db` (postgres:16 + healthcheck + volume), `backend`
  (build backend/Dockerfile, depends_on db healthy, entrypoint runs `python database/
  migrate.py` then uvicorn, env from compose), `frontend` (build the Vite app → nginx serving
  dist, or vite preview; proxy/api base to backend). One network.
- **`frontend/Dockerfile`** (or root): multi-stage node build → nginx static serve.
- **Migration-on-boot**: a backend entrypoint script that waits for db, runs migrations, then
  starts uvicorn. Idempotent (the migrate runner already is).
- **`.env.compose`** (example, not secrets): DATABASE_URL=postgres://...@db:5432/nubi?…,
  JWT_SECRET (dev value), ENV=development, COOKIE_SECURE=false, CORS_ORIGINS, FRONTEND_URL,
  Google creds placeholders, KERNEL_LOCAL_ENABLED=true.
- **`Makefile`**: `make up`, `make down`, `make migrate`, `make logs`, `make smoke`.
- **Live smoke test** (`scripts/smoke.sh`): against the running stack — GET /health (db ok),
  register a user, login, GET /auth/me with the token, POST /query {query_id:'demo_all'} →
  Arrow bytes, POST /query {query_id:'demo_points_10k'} → ~10k rows. Asserts each step.

## M10 task wave (single)
- **M10-A**: build all the above. THEN actually run it: `docker compose build`, `docker
  compose up -d`, wait for healthy, run `scripts/smoke.sh`, capture real output (health,
  register, login, query row counts), then `docker compose down`. Note SSL: local postgres
  may not want sslmode=require — handle by using a DATABASE_URL without sslmode for the
  compose db (the asyncpg pool/migrate must connect to plain local PG). Report the REAL smoke
  output. This is the first true end-to-end run of the system.

---

# M8 (reframed) — LLM-authorable HTML/CSS dashboards + auto-WebGL widgets

A dashboard is a sanitized HTML/CSS document (LLM-authorable) composed of declarative Nubi
widget custom elements. Widgets fetch via the allowlist `/query`; charts auto-upgrade to
WebGL for big data. Ties AI grounding + MCP to authored output. (Replaces the deferred
human WYSIWYG editor.)

## M8 contracts
- **Widget custom elements** (`embed/widgets/`, framework-agnostic, reuse M5 regl renderer +
  embed token pattern):
  - `<nubi-kpi query-id value-col label format?>` → HTML/CSS metric card (first row / agg).
  - `<nubi-table query-id limit? columns?>` → HTML table.
  - `<nubi-chart query-id type=line|bar|scatter x y color? backend token>` → reads Arrow;
    **if rows > WEBGL_THRESHOLD (~20000) render via regl/WebGL canvas, else SVG/HTML**. One
    element, automatic tier. Shadow DOM, CSS-var theming. Emits `nubi:widget-ready/error`.
  - `registerNubiWidgets()` registers all custom elements; data pulled via `/query`
    {query_id} with a token (same allowlist path — embed-safe).
- **Dashboard doc renderer** (`src/dashboards/`): `renderDashboardDoc(html, {backend, token})`
  — **sanitize** the HTML with DOMPurify (config: forbid `<script>`, `on*` handlers,
  `javascript:`/`data:` script URLs; ALLOW `nubi-kpi|nubi-table|nubi-chart` tags + their
  attributes + safe layout tags + class/style) then set innerHTML so custom elements upgrade.
  A React `<DashboardView html=.. />` page + a standalone path. NEVER render unsanitized LLM
  HTML.
- **Storage**: a dashboard is a `boards` resource whose `config = {html, theme?}`. CRUD via
  the existing /api/v1/boards. A `GET /boards/{id}` returns the html; the viewer renders it.
- **AI authoring** (`backend/app/ai/`): `POST /ai/dashboard {question}` → ground the question
  (M7 grounding) → produce a dashboard HTML doc that references REAL registered query_ids +
  real columns (NullProvider returns a deterministic templated dashboard HTML using the
  grounded tables; real providers generate it). Returns {html, grounding}. The generated HTML
  uses only nubi-* widgets + safe layout.
- **MCP tool**: add `create_dashboard(name, html)` (store as a boards resource via repo) and
  `author_dashboard(question)` (call the AI dashboard generator then store) to the MCP server.

## M8 task waves
- **M8-A** (frontend): widget kit custom elements (kpi/table/chart with auto WebGL threshold,
  reuse src/viz/scatterRenderer.js for the GL path) + `registerNubiWidgets()` in
  `embed/widgets/`; a vite build entry (extend vite.embed.config or add one) producing a
  widgets bundle. Demo HTML page rendering all three with sample data (graceful no-backend
  fallback). `npm run build` + the embed build green.
- **M8-B** (frontend; after A): `src/dashboards/DashboardView.jsx` + `renderDashboardDoc`
  with DOMPurify sanitization (add `dompurify` dep); a `/d/:id` route that loads a board and
  renders its html; a sample dashboard. Sanitization tests (script/on*-stripped; nubi-*
  kept). `npm run build` green.
- **M8-C** (backend; parallel with A): `POST /ai/dashboard` (grounded, NullProvider templated
  HTML using real query_ids), and MCP `create_dashboard`/`author_dashboard` tools wired to
  the repo + AI generator. Tests: generated HTML contains only nubi-* widgets + references a
  real registered query_id; endpoint auth; MCP tool stores a board. Whole suite + conformance
  green.

---

# M11 — Scheduled jobs / persistent Python (separate surface)

Promote a query/cell to a scheduled job. The recurring trigger is infra (cron/worker) — we
build the deterministic, testable core: model + store + due-calculation + execution + runs
history. Prod wires `run_due_jobs(now)` to a scheduler.

## M11 contracts
- **Model**: `job {id, org_id, created_by, name, kind:'query'|'python', target (query_id or
  code), schedule (cron string OR 'interval:Ns'), enabled, next_run_at, last_run_at}`;
  `job_run {id, job_id, status:'success'|'error', started_at, finished_at, row_count,
  message}`. Migration `0007_jobs.sql` (jobs + job_runs). In-memory `JobStore` for tests.
- **Scheduler** (`backend/app/jobs/schedule.py`): `next_run(schedule, after) -> datetime`
  (croniter LAZY import for cron; native interval parse for 'interval:Ns'). `run_due_jobs(
  store, now, executor) -> [job_run]` runs jobs with `next_run_at <= now`, records runs,
  advances `next_run_at`. **`now` is a PARAM (deterministic tests; no hidden clock in core).**
- **Executor** (`backend/app/jobs/executor.py`): `execute_job(job) -> job_run`: kind=='query'
  → resolve registered query_id, planner.plan + DuckDBConnector → row_count; kind=='python'
  → run via M4 LocalSubprocessRunner (first-party/system context). Record metering.
- **Routes** (`backend/app/routes/jobs.py`): POST /jobs, GET /jobs, GET /jobs/{id}, DELETE
  /jobs/{id}, POST /jobs/{id}/run (run now), GET /jobs/{id}/runs. `current_user`, org-scoped.

## M11 task wave
- **M11-A** (backend): `jobs/` (store, schedule, executor), `routes/jobs.py`, `0007_jobs.sql`,
  tests `test_jobs.py`: next_run for cron + interval; run_due_jobs with injected `now` runs
  only due jobs and advances next_run; execute_job(query) records success+row_count;
  execute_job(bad target) records error; CRUD + run-now + runs endpoints (auth, org-scoped).
  Whole suite + conformance green. main.py import.

---

# M12 — Connector selection (SDK reachable end-to-end) + capability-gated RLS

Make the M9 connector SDK actually reachable: a datastore declares its `type`, `/query`
resolves the connector via the registry, and the route REFUSES sources that can't honor RLS.

## M12 contracts
- **Datastore typing**: a `datastores` resource's `config = {type:'postgres'|'duckdb'|
  'http_json'|'mongo', ...connector config}`.
- **`/query` connector resolution** (`routes/query.py`): when `datastore_id` is given, load the
  datastore via the repo (org-scoped), read `config.type`, build the connector via
  `get_connector_registry().get(type)(config)`, execute the plan on it. No datastore_id →
  existing DuckDB demo path (unchanged).
- **Capability-gated RLS (security)**: before executing, if the plan carries RLS policies
  (`plan.rls_claims.policies` non-empty) and `connector.capabilities()['predicate_rls']` is
  False → `AppError("source_unsupported_rls",501)`. Never run a secured query on a source that
  can't enforce it. (The mongo stub thus refuses at the route, not just in execute.)
- Non-pushdown connectors (http_json/FunctionConnector) already apply post-fetch RLS — the
  route just passes the policy-bearing plan through.

## M12 task wave
- **M12-A** (backend): wire connector resolution + capability gate into `routes/query.py`
  (minimal, preserve the demo path + cache + streaming + allowlist + query_log). Tests
  `test_query_connectors.py`: a `duckdb`-typed datastore runs a query; an `http_json`-typed
  datastore (mocked httpx) returns post-fetch-RLS-filtered rows; a `mongo`-typed datastore
  WITH policies → 501 (capability gate); without policies the gate doesn't trigger. Seed
  datastores via InMemoryRepo. conformance UNCHANGED (demo path identical). Whole suite green.

---

# M4-REMOTE — Real remote kernel sandbox (E2B/Modal), prod code-execution path

Replace the `RemoteRunner` 503 stub with a real sandbox so prod code execution is safe
(firecracker/microVM isolation — closes the local-subprocess "shares host network/IMDS"
gap). Provider-pluggable; E2B primary. Local runner stays dev-only/prod-gated-off; remote
becomes the prod path.

## M4-REMOTE contracts
- **`E2BRunner(KernelRunner)`** (`backend/app/compute/remote_e2b.py`): lazy-import the E2B
  Code Interpreter SDK. `run(code, inputs, timeout_s) -> KernelResult(tier='remote_kernel')`:
  create a sandbox (with timeout), write each input table as Arrow IPC into the sandbox
  filesystem, run the SAME harness contract as LocalSubprocessRunner (binds `inputs` dict,
  user assigns `result`, serialize result to Arrow IPC), read the result file back, parse to
  pyarrow.Table, capture stdout, then close the sandbox. Output-size cap. On SDK/sandbox error
  → AppError("kernel_error",400 or 502); on timeout → AppError("kernel_timeout",504); if the
  SDK isn't installed or no API key → AppError("kernel_unavailable",503). Verify the exact
  E2B SDK method names from current docs (WebFetch e2b docs if unsure) and document them.
- **`ModalRunner`** (`backend/app/compute/remote_modal.py`): a parallel adapter shaped to
  Modal's API (lazy import); may be a thinner impl but must follow the same KernelResult
  contract and 503-when-unconfigured rule. (E2B is the primary tested path.)
- **Config** (`config.py`): `KERNEL_REMOTE_PROVIDER` ('e2b'|'modal'|'' ), `E2B_API_KEY`,
  `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`. 
- **Runner selection** (`routes/compute.py` `_choose_runner`): if a remote provider is
  configured (key present) → use the remote runner (works in ANY env incl production); elif
  `ENV != production` and `KERNEL_LOCAL_ENABLED` → LocalSubprocessRunner; else
  AppError("kernel_disabled",503). So: **production with E2B configured now WORKS** (instead
  of always 503). Response header `X-Nubi-Tier` reflects the runner used.
- **Compute placement router** (`compute/router.py`): when remote is configured, the
  'remote_kernel' tier is selected for native-wheel/oversized cells (already designed —
  confirm `remote_configured=True` path routes there).
- **Docs** (`docs/kernel-security.md`): update — remote sandbox is now the prod answer;
  describe E2B isolation (no host network/IMDS), how to enable (env), and that local stays
  dev-only.

## M4-REMOTE task wave
- **M4R-A** (backend): `remote_e2b.py`, `remote_modal.py`, config additions, `_choose_runner`
  update, router confirm, docs update. Tests `test_remote_kernel.py`: with a MOCKED E2B SDK
  (monkeypatch the sandbox class) assert E2BRunner writes inputs, runs the harness, parses the
  returned Arrow, returns tier='remote_kernel'; timeout path → 504; SDK-missing/no-key → 503;
  `_choose_runner` picks remote when E2B_API_KEY set (even ENV=production), local when
  dev+enabled, 503 in prod with nothing configured; endpoint `/compute/run` with remote
  configured (mocked) returns 200 + `X-Nubi-Tier: remote_kernel` and still rejects embed
  tokens (403). Whole suite + conformance green; no hard dependency on the e2b package being
  installed (lazy + mocked).

---

# PROD — Productionization + CI (Wave 1)

- **CI**: `.github/workflows/ci.yml` running ALL suites (backend pytest, mcp pytest, cli pytest, sdk `node --test`, frontend `test:dash`) + ALL builds (app, embed, widgets, sdk) on push/PR. Cache deps. Matrix optional.
- **PgJobStore + scheduler runtime**: asyncpg-backed `JobStore` (uses migration 0007) behind a provider (Pg in prod, InMemory in tests, like repos). A background scheduler tick (`run_due_jobs`) started opt-in from the app lifespan via env `JOBS_SCHEDULER_ENABLED` (asyncio loop; off in tests). Tests via InMemory.
- **Metering persistence + embed config**: persist kernel/usage metering to a table (`0008_usage.sql`) behind a provider (in-memory for tests); `/embed/config/{id}` reads the real `boards` resource (spec/html) instead of the stub.
- **Test-isolation cleanup**: autouse fixtures in conftest that reset module singletons (cache, connector registry, query_registry, query_log, metering, job store) between tests so the suite is order-independent. Fix the latent flakiness.
- **SDK license**: align `sdk/package.json` to Apache-2.0 (repo license).

# EDITOR — Drag-and-drop dashboard editor, LLM-native (Wave 2)

- **Chart lib: Apache ECharts** (+ echarts-gl reserved for big-data). Mobile-first, flexible.
- **Canonical dashboard SPEC (JSON)** = single source of truth for BOTH the DnD editor AND the LLM:
  `{ version, title, layout:{cols, rowHeight}, widgets:[{id, type:'kpi'|'table'|'chart',
  chartType?, query_id, encoding:{x,y,color,...}, props, pos:{x,y,w,h}}] }`.
  - Backend: spec schema + validator (`app/dashboards/spec.py`); `spec_to_html(spec)` compiles
    to sanitized `nubi-*` widgets (embed path); AI generator (`/ai/dashboard`) emits SPEC
    (not raw HTML); grounding exposes the spec schema so the LLM is *aware of the format*; MCP
    `author_dashboard`/`create_dashboard` accept/emit spec; stored in `boards.config.spec`.
  - Frontend: ECharts chart component (mobile-responsive); the DnD editor (react-grid-layout:
    add/move/resize widgets, palette, per-widget config — query picker, chart type, column
    encoding; live preview); load/save spec to `boards`; backwards-compat with existing
    HTML dashboards; an in-editor "Ask AI" panel that calls `/ai/dashboard` → spec → applies
    to the canvas (round-trip proof). LLM context: ship the spec schema + a system prompt so
    generated dashboards are always editor-compatible.
