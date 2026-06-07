# Nubi — Roadmap

A modern BI/dashboard tool. Connectors to SQL warehouses (and beyond), simple flexible
auth, WASM rendering, Pyodide for in-browser Python, an on-demand server kernel as an
escape hatch. Positioned against Hex and Cube.

---

## 1. The wedge

The whole strategy reduces to one structural bet: **the analytics kernel runs in the
user's browser by default, so the marginal cost of a dashboard view is ≈ $0.** Hex runs a
Python kernel per session in their cloud (cold starts, $$ to keep warm). Cube runs the
data plane in their cloud and bills per the infra it stands up. Nubi pushes compute to the
browser and only falls through to a server kernel for the ~10% of workloads that need it.

That bet is too broad to *ship* as a wedge, so the entry point is narrower:

> **Embed a live, cross-filtering, million-point dashboard inside someone else's SaaS for
> near-zero marginal cost per view.**

This hits a budget-holder (embedded-analytics buyers — today served by Sigma/Cube/Embed.dev
at prices that hurt), and it's the cut where "kernel-in-the-browser" is *felt*, not
explained. Everything else in this document is a way to spend that advantage.

### Honest scope of the cost claim
The 10–50× cost advantage vs naive warehouse usage is **real only at high cache-hit /
pre-aggregation rates** — e.g. 500 viewers of the *same* dashboard collapsing to one
backend hit. For 500 analysts each slicing differently, cache hit rate craters and you are
back to warehouse scans. We price and design for the *repeated-query* (embedded) shape
first, and adopt automatic pre-aggregations (see §4) so the advantage survives diverse
workloads.

---

## 2. Positioning

| | Hex | Cube | Nubi |
|---|---|---|---|
| Shape | Notebook + apps | Headless semantic layer + API | Batteries-included BI + embed |
| Kernel | Python per session, their cloud (10–30s cold, $$) | n/a (warehouse + Cube Store) | Pyodide in browser; on-demand server kernel only when needed |
| Result transport | JSON via pandas | JSON / SQL API | Arrow IPC over WebSocket |
| Viz | Plotly/SVG, chokes past ~50k rows | bring-your-own | WebGL/WebGPU on Arrow buffers, 1M+ points |
| Caching | Per-session, weak cross-user | Pre-aggregations in Cube Store | Content-hashed edge cache **+ auto pre-aggregations** |
| Modeling tax | medium | high (define cubes first) | low (point at a warehouse and go) |
| Security model | workspace/project perms | security-context JWT → SQL filters | same JWT + predicate-injection primitive for users, groups, embed |
| Embedding | separate product, bolt-on auth | core strength, headless only | core surface; editor embeddable, not just output |
| Pricing | per-seat (kernels cost real money) | infra/seat | connector throughput / embed views / AI calls / on-demand kernel time; real free tier |

**How we're better:** cheaper than Hex (no per-session kernel), cheaper-at-scale than naive
caching (auto pre-aggregations, the Cube weapon), lower friction than Cube (no hand-written
semantic model to start), and rendering + authoring are included rather than bring-your-own.

---

## 3. The consolidation decision: one language, one engine, one wire format

Backend language is **Python**, and it is the *only* language we can consolidate on —
because the compute kernel is irreducibly Python (Pyodide in the browser, native wheels on
the server). Any other primary language is automatically ≥2 languages. So:

- **Language:** Python everywhere (FastAPI control plane + connector planner/executor).
- **Engine:** DuckDB everywhere — DuckDB-WASM in the browser, DuckDB embedded in the
  connector, DuckDB in the kernel.
- **Wire format:** Arrow IPC at every boundary.
- **Pushdown brain:** `sqlglot` (pure Python) — runs in Pyodide *and* at the edge *and* in
  the kernel. The same rewriter logic in all three tiers.

This is *more* consolidated than a Rust-edge split would be: the browser tier is already
Pyodide (Python), so a Rust edge would **fragment** the engine, while all-Python **shares**
it across browser ↔ edge ↔ kernel.

**What we defer, not sacrifice:** edge ubiquity (Cloudflare Workers / 300-PoP — Python
Workers exist but are young; use fly.io regional connectors near the warehouse until then),
per-node WebSocket throughput (connectors are stateless brokers → scale horizontally), and
memory footprint (a cost line, not a capability line). All are horizontally solvable and
retrofittable per-component because Arrow is the contract at every boundary.

### 3.1 The Rust→WASM edge-executor carve-out (planned day one, built on demand)

We design the **seam** now so the executor can be swapped to Rust→WASM later as a
contained, test-guarded component replacement — never a rewrite. Eight day-one rules:

1. **Split plan from execute.** The pushdown *planner* (sqlglot, Python) compiles
   `(logical query + RLS claims)` → a **serialized physical plan** (dialect SQL string(s) +
   projection/predicate metadata + cache key). The *executor* runs that plan, encodes
   Arrow, streams, caches. The planner stays Python forever; only the executor is ever
   ported.
2. **Freeze the executor contract** as language-neutral now: `ExecutePlan(plan, claims) →
   stream<ArrowRecordBatch>` (protobuf/flatbuffer). Control plane and clients speak only
   this contract.
3. **Frozen, language-neutral cache-key spec** with test vectors — two implementations must
   produce byte-identical keys (the embedded-analytics safety property).
4. **Conformance suite from day one**: golden `(plan, claims, fixture) → expected Arrow +
   cache key`. The future Rust executor must pass the *same* suite to be swappable.
5. **ADBC / Arrow-native warehouse drivers** so the porting gap is host glue, not data path.
6. **Stateless executor + external content-addressed cache** so a Rust executor can run
   beside the Python one.
7. **Deployment-target neutrality** — Dockerized for fly.io now, no fly-isms, so a `wasm32`
   target is a build flag later.
8. **Shadow-mode migration** — run Rust in shadow, diff its Arrow + cache key against
   Python before cutting traffic. Conformance suite + shadow = zero-risk cutover.

---

## 4. Connectors (edge query brokers)

- Deployed on fly.io (regional, near each warehouse) now; Cloudflare Workers later.
- **SQL-aware proxies, not passthroughs:** rewrite for pushdown (partition filters,
  clustering keys, column projection), enforce auth predicates, return Arrow IPC.
- **Plan/execute split** (see §3.1): planner (sqlglot) → physical plan → executor.
- **Content-hashed edge cache** keyed on `(serialized plan, RLS-affecting JWT claims)`. N
  viewers of the same dashboard collapse to 1 warehouse hit. Cache key derived from the
  *same* claim set the predicate injector consumes — from one source of truth.
- **Automatic pre-aggregations:** mine the query log → suggest/build rollup tables so
  diverse-slice workloads also hit small rollups, not raw fact tables. This is the Cube
  weapon, made automatic.
- **BigQuery:** Storage Read API (native Arrow), BI Engine awareness, auto-MV suggestions
  from query-log mining, slot-reservation routing for flat-rate customers.

### 4.1 Source capability contract — "SQL first, flexible beyond"

We do **not** promise "any DB." We define a connector contract and rank sources by how
completely they satisfy it. Each connector declares `capabilities`:
`{ native_arrow, predicate_pushdown, projection_pushdown, partition_pushdown,
predicate_rls, column_masking, streaming_cdc }`. The planner degrades gracefully on what a
source lacks.

| Source class | Fit | Notes |
|---|---|---|
| **Cloud warehouses** (BigQuery, Snowflake, Redshift, Databricks SQL) | First-class (BigQuery + Snowflake shipped) | Sweet spot. `BigQueryConnector` + `SnowflakeConnector` are registered (native Arrow, `$N`→dialect param translation, optional drivers). Redshift runs via the Postgres wire today; Databricks SQL is next. |
| **OLTP** (Postgres incl. **Neon**, MySQL, MariaDB) | Shipped | `postgres` (ADBC, native Arrow), `mysql` + `mariadb` connectors registered (optional drivers). Generic `jdbc` bridge also registered for any JDBC-reachable source. |
| **Fast OLAP** (ClickHouse, DuckDB-as-service, Trino) | Supported | `duckdb` ships as both an in-memory engine and a read-only file-backed source; ClickHouse/Trino reachable today via the `jdbc` connector. |
| **Python / "or whatever" sources** | Supported via **Python connector SDK** | A source is any function returning an Arrow table. Lets a customer wrap a proprietary store, a dataframe job, or a REST pull as a first-class connector. Pushdown is best-effort; RLS enforced post-fetch in the connector (still server-side, never browser). |
| **APIs / SaaS** (Stripe, Salesforce, Sheets) | Shipped (HTTP/JSON connector) | No pushdown; RLS enforced post-fetch server-side via `apply_rls_postfetch`. Implemented as `HttpJsonConnector`; `predicate_rls=True` so the capability gate passes. |
| **NoSQL / document** (Mongo, DynamoDB, Elastic) | **OUT OF SCOPE** | Nubi only ships sources that can enforce RLS. SQL-predicate-injection is the RLS primitive: there is no equivalent for document stores, and building a per-source NoSQL RLS story is deliberate scope we don't carry. The capability contract is the escape hatch: a connector declaring `predicate_rls=False` is **REFUSED** (501) — so a customer could add a NoSQL connector later only if they implement its own RLS. The Mongo stub has been removed. Registered connectors today (8): `postgres`, `duckdb`, `http_json`, `mysql`, `mariadb`, `jdbc`, `snowflake`, `bigquery`. |

The Python connector SDK is the flexibility valve: SQL is first-class and fast; anything
else is "return an Arrow table, declare your capabilities," and the planner does the rest.
**The capability gate enforces the security floor:** any connector with `predicate_rls=False`
that receives a policy-bearing query is refused with a 501 — so the gate, not convention,
keeps unsecurable sources out of prod.

---

## 5. Auth & security model

### 5.1 Auth as code, not a matrix
- Signed JWT; claims include row/column policies.
- Connector enforces policies via SQL rewrite (predicate injection), **not** middleware.
- Same primitive powers users, groups, and embedding. No separate embed SDK.
- Policies live as TypeScript/SQL in the repo — diffable, PR-reviewable. (We may build a UI
  matrix as a *generator on top* of the code layer for enterprises that demand one.)

### 5.2 RLS is conditional; connector hardening is not
RLS matters enormously for **embedded/multi-tenant** (leaking tenant A's data to tenant B
is company-ending) and little for **internal BI** (trusted employees). But **connector
hardening is a floor for every deployment**, because the connector is two things at once:
it holds warehouse credentials (must never leak, regardless of data sensitivity) and it is
a gateway to a resource that costs money to query (an unhardened connector is an open SQL
proxy someone can point at your whole warehouse — a cost-bomb, even on public data).

**Always on (every deployment):**
- Warehouse creds confined to the connector (or the customer's network in self-host mode).
- Connector is **not** an arbitrary SQL passthrough — AST-validated, allowlisted plan
  shapes (kills injection *and* cost-bombs).
- Resource governance: per-query byte/cost ceilings, timeouts, rate limits, concurrency caps.
- AuthN on every request + authZ that the token may touch this connector/dataset.
- TLS, signed JWTs with `alg` pinning, JWKS issuer pinning, audit log.
- **Predicate injection is AST-based, never string-concat** — it is auth-critical code.

**Conditional (scales with tenancy / sensitivity):**
- Predicate injection / RLS — only if multi-tenant or row-scoped.
- Column masking — only if column-level sensitivity exists (cannot be done in the browser).
- Tenant-partitioned cache keys — only if caching *and* multi-tenant.
- Server-side-only compute for sensitive derivations / residency — only regulated/high-sens.
- mTLS, private networking, self-hosted connector — enterprise/regulated.

### 5.3 The browser is hostile
Because compute runs client-side, **raw Arrow buffers land in untrusted JavaScript.** So
the connector is the *only* trust boundary: anything a user may not see must be
filtered/masked **before** the buffer leaves the connector. You cannot hide a row or mask a
column in the browser. This holds even with zero RLS.

### 5.4 Security is a dial that maps to a billing tier
The dial is **not** "how hardened is the connector" (always max). It is *data minimization
+ where sensitive compute runs*: browser compute on pre-scoped data (cheap, legitimately
secure for that case) ↔ server-side compute for sensitive derivations/residency
(expensive). An embedder "lowering the bar for embedding" is choosing browser-side compute;
"I need hardening" is choosing server-side. Each maps to a metered tier (see §8).

---

## 6. Compute placement planner

Sibling of the query planner. The query planner decides *what SQL to push to the
warehouse*; the compute planner decides *which tier runs each cell*. Same philosophy: push
work to the cheapest correct place. Five tiers:

| Tier | Runs where | Best for | Who pays |
|---|---|---|---|
| **Pushdown** | the warehouse | aggregations, joins, windows, percentiles, ML-in-SQL | customer's scan |
| **Edge / connector** | fly.io/CF worker | masking, light Arrow transforms, decryption, projection | Nubi (cheap, near-data) |
| **Browser** (default) | the user's tab | last-mile shaping, cross-filter, interactive viz, small inference | $0 to Nubi |
| **On-demand kernel** | Modal/E2B, scale-to-zero | native wheels (pytorch/xgboost/prophet/GDAL), jobs > minutes, > browser cap | metered per-second |
| **Persistent / GPU** | dedicated backend | scheduled pipelines, training, secrets-bearing ops | separate SKU |

**Routing inputs:** data volume vs ~4GB browser cap; library availability (Pyodide port?);
latency/interactivity; cost & who pays; security (secrets/masking → never browser);
reproducibility/lifecycle (scheduled → never a tab).

**Substrate that makes offload free:** Arrow as universal interchange (a dataframe hops
browser↔edge↔kernel with no serialization tax) + content-hash caching keyed on
`(cell code + input-data hash + resolved env)` so a result is reused regardless of where it
ran.

**Default:** auto-route, override available (`run on GPU` / `force browser` / `schedule`).
**Discipline:** default to free tiers; the kernel is exceptional, metered, scale-to-zero —
every workload silently routed to a server kernel reabsorbs Hex's cost line and loses the
pricing weapon. **Watch:** version skew (Pyodide numpy ≠ server numpy) — pin/match or
declare which tier produced a result.

---

## 7. Rendering (WASM-first) & embeddability

- Arrow IPC over WebSocket → columnar buffers in browser; viz reads buffers directly
  (Perspective / deck.gl / regl / WebGPU). 1M+ point scatter, linked cross-filter at 60fps.
- Dashboards are JSX/MDX files in git. `npm run dev` against prod via the WASM runtime.
- **LLM-authorable rendering (decision):** a dashboard is a *sanitized HTML/CSS document*
  (LLMs author HTML/CSS natively) composed of declarative Nubi widget custom elements —
  `<nubi-kpi>`, `<nubi-table>` (HTML/CSS) and `<nubi-chart>` which **auto-upgrades to WebGL
  (regl) above a row threshold** and renders SVG/HTML below it. The author (human or LLM)
  never writes WebGL or fetch code — only layout + `<nubi-chart query-id=… type=scatter
  x=… y=…>`. The renderer sanitizes (DOMPurify: no `<script>`/`on*`/`javascript:`, allow
  `nubi-*` + safe tags/styles) and mounts; widgets pull data via the allowlist `/query`
  path. This is how "flexible, HTML/CSS-first, WebGL for big data, agent-authorable" holds
  together — and it ties the AI grounding (§7-AI) + MCP server directly to authored output.
- **Embed surfaces:** read-only `<nubi-dashboard>` (iframe + web component, JWT-scoped,
  CSS-var theming) → cell-level `<nubi-widget>` → embedded `<nubi-editor>` → headless
  PNG/PDF render → bring-your-own-frontend (engine as a library).
- **API surfaces:** REST + GraphQL (resource CRUD; dashboards-as-code ⇒ the API is largely
  the file tree over HTTP), JS SDK (embed lifecycle, typed event bridge), Python SDK
  (orchestration), CLI (`deploy`/`run`/`diff`, PR previews), MCP server (agents author
  dashboards), webhooks, OAuth + service accounts.
- **Embed auth contract:** host publishes JWKS, implements `getToken()`, mounts
  `<NubiEditor basePath getToken />`. Short-lived JWT (≤15m) + silent refresh, claims-driven
  RLS, origin pinning, per-feature scopes, service-account tokens, signed webhooks.
- **Deploy modes:** hosted → self-hosted connector (creds never leave customer network) →
  fully self-hosted (VPC, regulated).
- **VPC bridge (shipped):** a connector with `network_mode='bridge'` reaches a private
  database via a lightweight agent inside the customer's network that holds an outbound
  WebSocket tunnel — no inbound firewall ports. The query path calls `resolve_network_async()`,
  the `BridgeBroker` opens an ephemeral local TCP proxy, the connector's dial target is
  rewritten to `127.0.0.1:<port>`, and the proxy is torn down after execution. Wired into
  `backend/app/routes/query.py`; other transports (`ssh_tunnel`, `psc`, `cloudsql_proxy`)
  return 501. See [`docs/bridges.md`](./docs/bridges.md).

---

## 8. Pricing & billing

Marginal cost per dashboard view ≈ $0 (compute is the user's browser). Charge for:
- Connector throughput (bytes / queries).
- Embed views (per-thousand).
- AI calls (lineage-grounded retrieval + generation) — **grounded on real model prices**.
- On-demand kernel time (per-second, scale-to-zero).
- Scheduled jobs / persistent Python — separate SKU.
- Security/compute tier (see §5.4) — server-side sensitive compute is a metered upsell.

A generous free tier is structurally available; Hex can't match it without bleeding kernel
cost.

### 8.1 Billing implementation notes
- **Currency:** charge in **ZAR** via **Paystack**. ZA fees: **2.9% + R1** local, **3.1% +
  R1** international, **+15% VAT**, no cap, no small-transaction waiver.
- **FX:** USD→ZAR from a keyless rate API (`open.er-api.com/v6/latest/USD`, `rates.ZAR`),
  cached with `time_last_update`, fallback to a pinned table.
- **LLM cost grounding:** pull per-token prices from
  `BerriAI/litellm/model_prices_and_context_window.json` (USD per token, incl.
  cache-read/cache-write). Apply margin → convert to ZAR → add Paystack fee.
- **Metering dimensions:** LLM tokens (in/out/cached), compute (kernel-seconds, edge),
  storage (GB-month), connector bytes, embed views.
- Scenario modelling lives in the **gitignored** `billing-model/` folder
  (`generate_scenarios.py`) — internal, not part of the product.

---

## 9. Honest tradeoffs

- **Connector security surface** — each new source is fresh attack surface; the rewriter +
  cache key concentrate ~all risk. Threat-model them before anything else.
- **Pyodide gaps** — native wheels won't run in browser; the on-demand kernel is a launch
  requirement, not optional.
- **Browser memory cap (~4GB)** — pushdown must be aggressive; a dumb connector breaks the
  whole story.
- **Opinionated auth** — policies-as-code wins for engineers; some enterprises demand a UI
  matrix. Offer it as a generator on the code layer, or say no.
- **Live streaming on huge tables** — needs a hot-mirror engine (Materialize/RisingWave);
  real surface, not day one. Hex doesn't have it either.
- **Persistent-Python temptation** — bake a kernel server into the dashboard runtime and you
  absorb Hex's cost line. Keep it strictly opt-in and separate.
- **Embedded editor ≫ embedded read-only** — multi-tenant theming, host event bridge,
  version skew. Don't promise it day one.
- **API-first slows early velocity** — but retrofitting is worse. API-first for resource
  CRUD; UI-only for ephemeral state.
- **JWT lifecycle is a sharp edge** — 15m + silent refresh default; ship a reference
  `getToken()` and warn loudly if `exp > 1h`.
- **NoSQL is deliberately out of scope** — not a gap, a choice. Predicate-injection RLS has
  no clean analog for document stores; shipping a NoSQL connector without a per-source RLS
  story would break the security floor. The capability gate (501 on `predicate_rls=False`)
  enforces this automatically. A future team can add a NoSQL connector if they implement its
  RLS — the contract provides the escape hatch.

---

## 10. Build sequence

**M0 — Foundation.** ✅ Clean frontend (React) + backend (FastAPI/Python) rebuild on
**Neon Postgres**. Auth: email/password with access+refresh (rotation + reuse detection)
and Google OAuth. Migrations from scratch.

1. ✅ **M1 — WASM runtime + connector + conformance** — plan/execute split (sqlglot planner
   → PhysicalPlan → executor), Postgres/Neon connector (ADBC), DuckDB connector, frozen
   cache-key spec with test vectors, conformance suite.
2. ✅ **M2 — Arrow streaming + edge cache + pushdown + auto-pre-agg seed** — streaming
   `POST /query`, content-hashed LRU cache (HIT/MISS header), pushdown optimizer (projection
   + predicate + LIMIT), query-log ring buffer, rollup suggester + routing.
3. ✅ **M3 — Embed auth + allowlist + `<nubi-dashboard>`** — unified HS256 + JWKS verifier,
   issuer registry, server-side RLS enforcement from verified token, scope gate, origin
   pinning, `<nubi-dashboard>` web component + `getToken()` reference.
4. ✅ **M4 — On-demand kernel + remote E2B/Modal sandbox (prod path)** — `KernelRunner` ABC,
   `LocalSubprocessRunner` (dev), `RemoteRunner` stub promoted to real `E2BRunner` +
   `ModalRunner` adapters; `_choose_runner` selects remote in prod when configured,
   `ComputePlacementRouter` for tier selection, `POST /compute/run`.
5. ✅ **M5 — WebGL/WebGPU viz layer** — regl-based GPU scatter renderer on Arrow buffers,
   `<nubi-chart>` auto-upgrades to WebGL above row threshold, DuckDB synthetic point-cloud
   source for the demo.
6. ✅ **M6 — REST API + JS SDK + CLI** — repo layer (asyncpg + in-memory), CRUD for
   datastores/boards/widgets/queries (org-scoped), JS SDK (`createNubiClient`), Python CLI
   (typer: login/deploy/run/diff).
7. ✅ **M7 — Lineage index + AI grounding + MCP server** — sqlglot lineage extractor,
   lineage graph + `/lineage` endpoints, deterministic grounding over catalog, `LLMProvider`
   abstraction + NullProvider, `POST /ai/ask`, MCP server (4 tools).
8. ✅ **M8 — LLM-authorable HTML/CSS dashboards + auto-WebGL widgets** — `<nubi-kpi>`,
   `<nubi-table>`, `<nubi-chart>` (auto-WebGL threshold) custom elements, DOMPurify
   sanitized dashboard renderer, `POST /ai/dashboard`, MCP `create_dashboard`/
   `author_dashboard` tools.
9. ✅ **M9 — Python connector SDK + HTTP/JSON source** — `FunctionConnector`,
   `apply_rls_postfetch` (server-side RLS for non-pushdown sources), connector registry,
   `HttpJsonConnector` (`predicate_rls=True`, post-fetch RLS). Mongo stub removed — NoSQL
   out of scope (see §4.1). **Registry since extended:** `duckdb` is now a real read-only
   file-backed source (not just the in-memory demo), plus `mysql`, `mariadb`, and a generic
   `jdbc`, `snowflake`, `bigquery` connectors (the last four via optional drivers).
10. **M10 — Runnable Docker self-host stack** — `docker-compose.yml` (db + backend +
    frontend), migration-on-boot entrypoint, `.env.compose`, Makefile, live smoke test
    (`scripts/smoke.sh`). **Not yet built — this is the remaining capstone.**
11. ✅ **M11 — Scheduled jobs / persistent Python** — `jobs` + `job_runs` schema, cron +
    interval scheduler (deterministic `now` param), `execute_job` (query and Python paths),
    CRUD + run-now + runs-history routes.
12. ✅ **M12 — Connector selection + capability-gated RLS** — `/query` resolves connectors
    from the registry via `datastore.config.type`; 501 gate before execution when
    `predicate_rls=False` and policies are present. API sources done (`http_json`); NoSQL
    out of scope (§4.1).

---

## 11. Builder feature layer (authoring UX)

§§1–10 build the *platform* — kernel, connectors, security, compute placement, rendering
engine. This section is the **product surface a user actually touches to build a
dashboard**: the manual builder. The wedge (§1) is an *embeddable, cross-filtering,
million-point* dashboard — so the builder's job is to let someone compose one without
writing WebGL, fetch, or auth code, against **their own warehouse** (§4). Legacy
gap-analysis that motivated this section: [`FEATURE_PARITY.md`](./FEATURE_PARITY.md).

### 11.1 Three clean layers (the anti–legacy-coupling decision)
Legacy welded each widget to its own query + template + Redux variable web. We **decouple**.
Each layer references the one below by id and never reaches into its internals:

```
Connector (§4)  →  Query (reusable asset, params declared)  →  Widget (<nubi-*> ref + params)  →  Dashboard (sanitized HTML/CSS, §7)
```

A **Query** is authored once in the query library and reused everywhere. A **Widget** binds
query params to literals or to **dashboard variables**. This keeps dashboards
tenant-agnostic and safe to clone/template (consistent with §5.7 isolation posture).

### 11.2 Widgets: fewer, more robust
We keep the `<nubi-*>` custom-element set (§7, M8) **small and powerful**. A new widget is
justified only by a new *interaction model*, never a visual variant — a donut is
`<nubi-chart type=pie>`, not a new element. The whole surface is ~5 archetypes vs. legacy's
~24:

| Element | Subsumes (legacy ~24) | "Robust" means |
|---|---|---|
| `<nubi-chart>` (ECharts + auto-WebGL, §7) | all 16 Apex/Chart.js types | `line/bar/area/scatter/pie/donut/heatmap/combo`; multi-series, stacking, dual axis, labels, number formatting — all attributes/config |
| `<nubi-table>` | DataGrid + conditional settings | sort, paginate, column formatting, **conditional formatting rules**, show/hide/reorder, inline export. The workhorse — over-invest |
| `<nubi-kpi>` | QuickStats + sparkline | value + delta vs. comparison + sparkline + format |
| `<nubi-filter>` *(new)* | autocomplete, date-range, text | writes a dashboard variable; options sourced from a `query-id` |
| `<nubi-text>` *(new, markdown)* | label, divider, image, breadcrumb | one element covers all four |

Rationale: every element is permanent config, test, AND LLM-authoring/grounding surface
(§7-AI, MCP). Five orthogonal primitives compose into "comprehensive"; 24 variants rot and
bloat the AI's authoring vocabulary.

### 11.3 Query templating / parameters (user-facing)
Distinct from RLS predicate injection (§5) — that is **token-locked and server-enforced**;
*this* is author-facing parameterization. Queries declare typed params
(text/number/date/date-range/single-/multi-select); widgets supply them:

```
POST /query  { query_id, params: { region: "North", from: "2026-01-01" } }
```

- Params are **rendered + serialized server-side in the connector** (AST-based, never
  string-concat — same rule as §5.1's predicate injector). The browser never builds SQL.
- **Param resolver precedence is security-critical:** `token-claims (RLS, locked) > URL
  param > filter widget > query default`. Token/claim params are **un-overridable** by URL
  or filter — this is the line between "viewer may slice" and "viewer may not escape their
  tenant." Needs golden tests before embedding ships (cf. §3.1 conformance discipline, §9
  JWT sharp edge).
- **Cache-key impact:** user params join the content-hash cache key alongside RLS claims
  (§4) — already the design, just extended to author params.

### 11.4 Interactivity: variables, filters, route params
The wedge says "cross-filtering" (§1) — this makes it real, with **lightweight state, no
Redux** (§ the "low modeling tax" ethos):
- **Dashboard variable store** — small reducer/signal map `{ var: value }`.
- **`<nubi-filter>`** widgets write variables; widgets whose params `ref` a variable re-query
  (cache usually makes this near-instant; linked cross-filter target 60fps per §7).
- **Route ↔ variable binding** — `/d/:id?region=North` seeds the store on mount; filter
  changes write back to the URL → shareable, bookmarkable, and the natural seam for
  embed-token params to inject locked values (§11.3 precedence).

### 11.5 Query library (promote the playground)
Promote `/playground` into a **first-class query library** over the existing query CRUD
(M6): Monaco SQL editor + declared params + saved/named/versioned queries + schema
introspection (§4.1) for autocomplete + **schema-grounded text-to-SQL** (reuse M7 lineage
grounding + M8 AI, emitting a saved parameterized query — not raw SQL to the client).

### 11.6 Exports & scheduled reporting
- **User-facing exports** per widget/dashboard: CSV + PNG (ECharts/regl `getDataURL`) →
  Excel (ExcelJS) → PDF (reuse the §7 headless PNG/PDF render path).
- **Scheduled email reporting** is a *report job type* on top of M11's scheduler — **do not
  rebuild scheduling.** A report job = `{ dashboard_id, bound params, cron, recipients,
  subject/body }` → render (headless) → email PDF/CSV. **Per-recipient RLS reuses the §11.3
  locked-param resolver** so a scheduled send can't leak across tenants.

### 11.7 Build sequence (builder layer)
Original status reflected a code audit on 2026-06-05; **re-audited 2026-06-07 — all of
M13–M18 have landed** (see [`TASKS.md`](./TASKS.md) M13–M18 for the per-wave breakdown).

| Milestone | Status | Scope | Where it lives |
|---|---|---|---|
| **M13 — Query library + params** | ✅ Done | Named/typed params (`{{name}}` → positional binds, resolver precedence), Queries workspace (Monaco), Save-as-query | `queries/registry.py`, `connectors/planner.py`, `routes/query.py`, `src/pages/app/QueryWorkspace.jsx`, `src/components/SqlEditor.jsx` |
| **M14 — Interactivity** | ✅ Done | `spec.variables` + `VariableStore`, filter/text widgets, widget `params` ref→re-query, route↔variable binding | `src/dashboards/VariableStore.jsx`, `widgets/FilterWidget.jsx` / `TextWidget.jsx`, `SpecRenderer.jsx`, `DashboardViewPage.jsx` |
| **M15 — Widget depth** | ✅ Done | Stacking/combo/dual-axis charts; TanStack table + conditional formatting + column formats | `src/viz/chartOption.js`, `src/components/DataGrid.jsx`, `widgets/TableWidget.jsx`, `widgets/conditionalFormat.js` |
| **M16 — Exports** | ✅ Done | Per-widget CSV/PNG + dashboard PDF (Excel deferred) | `src/lib/exports.js`, `src/dashboards/WidgetToolbar.jsx`, `src/components/ExportShareMenu.jsx` |
| **M17 — Scheduled reporting** | ✅ Done | `report` job kind on the M11 scheduler → render → email, per-recipient RLS | `backend/app/jobs/report.py`, `jobs/executor.py`, `routes/jobs.py` |
| **M18 — AI-SQL in library** | ✅ Done | `POST /ai/sql` → grounded text-to-SQL emitting a saved parameterized query | `backend/app/ai/sql.py`, `routes/ai.py` |

**Deliberately still excluded** (consistent with legacy gap-analysis): second charting
library, Redux variable graph, 270-field widget configs, Data Bridge / semantic-ETL layer
(revisit a *modern* lightweight one only on demand), Table Manager (DDL from UI — violates
the read-only/connector-hardening floor, §5.2), Theme Creator, layout synchronizer. Widget
groups/steppers are **not a port**: drilldown already shipped (chart-click → variable), and if
demand appears the modern equivalent is a container widget with `display: 'tabs' | 'stepper'`
over child widget IDs — not the legacy MUI/Redux widget. See [`FEATURE_PARITY.md`](./FEATURE_PARITY.md)
for the full triage.

---

## 12. Builder layer — extended scope (2026-06-05 decisions)

New direction from product, layered on §11. Per-milestone tasks in [`TASKS.md`](./TASKS.md)
(M19–M22). **Status (re-audited 2026-06-07): all four have landed** —
M19 undo/redo (`src/editor/history.js` + wiring in `DashboardEditor.jsx`),
M20 git sync (`backend/app/git/`, `routes/git.py`, `docs/git-sync.md`),
M21 agentic chat (`backend/app/ai/agent.py` + `ai/tools.py`, `routes/ai.py`, `src/editor/ChatPanel.jsx`),
M22 conversational gateway (`backend/app/chat/`, `routes/chat.py`).

**Decision — NO visual query builder.** Authoring is **SQL editor (Monaco) + AI text-to-SQL**
only. The legacy drag-the-joins visual builder is explicitly out of scope; it adds large
surface for a workflow our SQL+AI path already covers.

### 12.1 Deeper undo/redo (M19) — ✅ Done
A large-depth history stack over the dashboard `spec` with ⌘Z/⇧⌘Z shortcuts and redo,
snapshotting the spec on each mutation (rapid drags coalesced). Pure reducer in
`src/editor/history.js` (with `history.test.mjs`), wired into `src/editor/DashboardEditor.jsx`.

### 12.2 Git sync for queries & dashboards (M20)
Queries and dashboards are **versioned in git** — the "dashboards-as-code" thesis (§7) made
real for end users. A workspace can connect a git repo; saving a query/board writes a
deterministic file (`queries/<id>.sql` + meta, `dashboards/<id>.json`) and commits;
pull/diff/restore from history. Testable core: serialize resource → file tree → commit in a
local/temp repo (GitPython or `git` subprocess), provider-abstracted so remote auth
(GitHub App / deploy key) is a later adapter. Ties to the CLI's `deploy`/`diff` (M6) and the
REST "API is largely the file tree over HTTP" idea (§7).

### 12.3 Agentic AI chat with tools (M21)
The AI is not a text box — it's an **agent with robust tools** it can call in a loop:
`get_schema`, `generate_sql`/`create_query`, `run_query`, `create_dashboard`,
`edit_dashboard` (add/move/configure widgets on the spec), `list_queries`. Build on the
existing MCP tool surface (M7/M8) + `/ai/sql` (M18) — promote those into a shared **tool
registry** the model drives via tool-calling. `POST /ai/chat` runs the agent loop (provider
tool-calls → execute tool → feed result → repeat) with a `NullProvider` deterministic path
for tests. RLS/claims flow into every tool (a tool can never exceed the caller's scope).

### 12.4 Conversational gateway — Slack / WhatsApp + chart images (M22)
A **headless chat surface** over the M21 agent so a user can DM Slack/WhatsApp: "revenue by
region last quarter" → the agent runs the query and replies with a **chart rendered as a PNG
image** + a short summary. Components: (a) **server-side chart→image** render
(`render_chart_png(spec, data)` — start with a Python renderer, swappable for headless-browser
ECharts to match the web look); (b) **gateway webhooks** (`/chat/slack`, `/chat/whatsapp`)
that verify signatures, normalize the inbound message, dispatch to the M21 agent, and post
back text+image via the platform API (behind a `ChatTransport` provider; `NullTransport`
for tests). External tokens (Slack app, WhatsApp Cloud API) are config/adapters — the core
(parse → agent → render → reply) is testable offline.

### 12.5 Sequencing
M19 (undo/redo) ships immediately (small, editor-local). M21 (agent tools) is the spine —
M22's bots and the in-app AI chat both ride on it, so build the tool registry once. M20 (git)
is independent and parallelizable. All reuse existing infra (registry, spec, MCP, providers)
rather than new subsystems.
