# Embed API — versioned public contract (v1)

This document is the **stable public contract** for the Nubi web-component embed
kit. Hosts pin to a specific bundle version; breaking changes are gated behind a
new major version number. The current version is **v1**.

> New to embedding? Start with the step-by-step [Embedding guide](/docs/embedding)
> — how to publish a board, sign an embed JWT, and drop the tag into your page.
> This reference is the precise field/endpoint/error contract those steps rely on.

## Stability and deprecation

- Fields and events marked here are stable for the lifetime of v1.
- Deprecated attributes will remain functional for at least two minor releases
  after the deprecation notice.
- The bundle filename `nubi-embed.js` is stable. The export name `NubiEmbed`
  (UMD global, also present on the ESM) is stable.

---

## Loading the bundle

Build the bundle with:

```bash
npm run build:embed
# Output: embed/dist/nubi-embed.js
```

Add a single `<script type="module">` tag — no import map, no `node_modules`,
no bare specifiers. All dependencies (React 19, apache-arrow, ECharts) are
bundled in:

```html
<script type="module" src="nubi-embed.js"></script>
```

After the script loads every component in the table below is registered as a
custom element and ready to use.

**Monaco editor is NOT bundled.** `<nubi-query-editor>` dynamically imports
Monaco at runtime. Hosts that want the query editor must supply Monaco
separately (CDN or their own bundler). All other components work without it.

Target browsers: Chrome 89+, Firefox 90+, Safari 15+ (native Custom Elements
and Shadow DOM required).

---

## Bundle version

Every build stamps the version from `package.json` into the bundle at compile
time. After the bundle loads you can read the version string from:

| Access point | Example value |
|---|---|
| `window.__nubiVersion` | `"0.0.0"` |
| ESM named export `version` | `"0.0.0"` |
| ESM named export `NUBI_EMBED_VERSION` | `"0.0.0"` |

```js
import { version } from './nubi-embed.js'
console.log(window.__nubiVersion) // also available globally
```

### Versioned bundle path convention

The build produces two artifacts:

| File | Usage |
|------|-------|
| `embed/dist/nubi-embed.js` | Stable "latest" alias. Always points to the most recent build. |
| `embed/dist/nubi-embed-<version>.js` | Pinned version. Safe for long-cache headers. |

Pin to a specific version in production by serving the versioned file:

```html
<script type="module" src="nubi-embed-0.0.0.js"></script>
```

---

## Token resolution

Every component resolves its bearer token through three mechanisms, checked in
priority order:

| Priority | Mechanism | Meaning |
|----------|-----------|---------|
| 1 (highest) | `.getToken` instance property | An async function set directly on the element: `el.getToken = async () => fetchToken()`. Useful for programmatic integration without touching HTML attributes. |
| 2 | `token` attribute | A static JWT string baked into the HTML. Useful for server-side rendering. |
| 3 | `get-token` attribute | The name of a `window.*` function that returns `string \| Promise<string>`. Called before every request so short-lived tokens can be refreshed. |

```js
// Highest-priority: set the property directly on the element
const el = document.querySelector('nubi-dashboard')
el.getToken = async () => {
  const res = await fetch('/auth/token')
  return res.text()
}
```

```html
<!-- Static token via attribute -->
<nubi-kpi token="eyJ..." query-id="revenue"></nubi-kpi>

<!-- Dynamic token via window function name -->
<nubi-kpi get-token="getMyToken" query-id="revenue"></nubi-kpi>
<script>
  window.getMyToken = async () => fetchToken()
</script>
```

If none of the three mechanisms is configured and `query-id` / `metric-id`
is present, requests are made without an Authorization header (demo / public
boards only).

---

## Component reference

### `<nubi-dashboard>`

Read-only dashboard embed. Loads a published dashboard by ID and renders its
widgets with live cross-filtering. Source: `embed/nubi-dashboard.js`; built to
`dist-embed/nubi-dashboard.js` (UMD) and `dist-embed/nubi-dashboard.es.js` (ESM).
This element ships in its **own** bundle, separate from the `nubi-embed.js`
widget kit documented above.

| Attribute | Required | Meaning |
|-----------|----------|---------|
| `get-token` / `token` | See above | Bearer JWT |
| `backend` | No | API base URL. Default `http://localhost:8000`. |
| `dashboard-id` | One of | A saved board id. Fetches `GET /api/v1/embed/config/{id}` and renders each widget. |
| `query` | One of | A registered `query_id` (embed tokens) or a SQL string (first-party). **Takes precedence over `dashboard-id`** when both are set. |
| `theme` | No | `"dark"` (default) or `"light"`. |

Emits `nubi:ready` `{ rowCount }`, `nubi:query-run`
`{ rowCount, cacheStatus, elapsedMs, sample }`, and `nubi:error` `{ message }`.

---

### `<nubi-kpi>`

Big-number metric card. Executes a registered query and displays the first row's
value, with optional KPI-target rendering.

| Attribute | Required | Meaning |
|-----------|----------|---------|
| `get-token` / `token` | — | Bearer JWT |
| `backend` | No | API base URL. Default `http://localhost:8000`. |
| `query-id` | Yes | Registered query ID. |
| `value-col` | Yes | Column name to read the KPI value from (first row). |
| `label` | No | Display label. Defaults to `value-col`. |
| `format` | No | `"number"` (default, auto-compact), `"currency"`, `"percent"`, `"integer"`. |
| `target-col` | No | Column holding the target value (e.g. `"revenue_target"`). When set, enables KPI-target rendering (goal bar, % to goal, RAG chip). |
| `rag-col` | No | Column for RAG status string (`"green"` / `"amber"` / `"red"`). Defaults to `"<value-col>_rag"` when `target-col` is set. |
| `pct-col` | No | Column for pct-to-goal ratio. Defaults to `"<value-col>_pct_to_goal"` when `target-col` is set. |

When `target-col` is absent (or the column is not in the result) no target UI
is rendered — the widget is backward-compatible with queries that do not include
target columns.

**Events emitted:**
- `nubi:widget-ready` — `{ rows, renderer: "kpi" }` — data loaded successfully.
- `nubi:widget-error` — `{ message }` — fetch or render failed.

**Sample fallback:** any fetch failure renders a clearly-labelled sample card so
demo pages always display something meaningful.

### Inline data injection

All vanilla widgets (`<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`) accept a `data` attribute for static/SSR embeds:

| Attribute | Widgets | Meaning |
|-----------|---------|---------|
| `data` | kpi, table, chart | Inline JSON data. When present, skips all fetching and renders directly. Array of row objects for kpi/table/chart. |
| `no-sample-fallback` | kpi, table, chart | Boolean. When set, a fetch failure renders a clean error state instead of sample data. Default: show sample (back-compat). |

**Example — static KPI with target and RAG:**

```html
<nubi-kpi
  value-col="revenue"
  label="Revenue"
  format="currency"
  target-col="revenue_target"
  rag-col="revenue_rag"
  pct-col="revenue_pct_to_goal"
  data='[{"revenue":124500,"revenue_target":100000,"revenue_pct_to_goal":1.25,"revenue_rag":"green"}]'
></nubi-kpi>
```

**Example — suppress sample fallback on fetch failure:**

```html
<nubi-kpi
  no-sample-fallback
  query-id="revenue"
  value-col="revenue"
  backend="https://api.example.com"
></nubi-kpi>
```

---

### `<nubi-kpi-react>`

React-based KPI card. Same attribute surface as `<nubi-kpi>` (excluding
KPI-target attributes). Implemented with the `defineNubiElement` factory
(`embed/react-wc.js`) which wraps a React component in an open shadow root.

| Attribute | Required | Meaning |
|-----------|----------|---------|
| `get-token` / `token` | — | Bearer JWT |
| `backend` | No | API base URL. Default `http://localhost:8000`. |
| `query-id` | Yes | Registered query ID. |
| `value-col` | Yes | Column to read the KPI value from. |
| `label` | No | Card heading. |
| `format` | No | `"number"` / `"currency"` / `"percent"` / `"integer"`. |
| `theme` | No | `"dark"` (default) or `"light"`. Changing this attribute re-applies the theme CSS without a full re-mount. |

---

### `<nubi-chart>`

Auto chart using ECharts. Reads Arrow IPC from `POST /query` and picks a chart
type automatically. Switches to a WebGL scatter path above ~20,000 rows.

| Attribute | Required | Meaning |
|-----------|----------|---------|
| `get-token` / `token` | — | Bearer JWT |
| `backend` | No | API base URL. |
| `query-id` | Yes | Registered query ID. |
| `chart-type` | No | Override: `"bar"`, `"line"`, `"scatter"`, `"pie"`, `"area"`. Auto-detected when absent. |
| `x-col` | No | Column for the X axis. Auto-selected when absent. |
| `y-col` | No | Column for the Y axis / value. Auto-selected when absent. |
| `theme` | No | `"dark"` / `"light"`. |

---

### `<nubi-table>`

Data table for query results. Renders Arrow IPC rows as a paginated HTML table.

| Attribute | Required | Meaning |
|-----------|----------|---------|
| `get-token` / `token` | — | Bearer JWT |
| `backend` | No | API base URL. |
| `query-id` | Yes | Registered query ID. |
| `page-size` | No | Rows per page (default `50`). |
| `theme` | No | `"dark"` / `"light"`. |

**Events emitted:**
- `nubi:select` — `{ column, value, row }` — user clicked a cell.

---

### `<nubi-query-editor>`

Scope-gated SQL / metric query workspace. Requires the host to supply Monaco
editor separately (the bundle excludes it due to its ~7 MB size).

| Attribute | Required | Meaning |
|-----------|----------|---------|
| `get-token` / `token` | — | Bearer JWT |
| `backend` | No | API base URL. |
| `query-id` | No | Pre-load a registered query by ID. |
| `mode` | No | `"sql"`, `"metric"`, or `"auto"` (default). Auto detects from the loaded query. |
| `theme` | No | `"dark"` (default) / `"light"`. |
| `read-only` | No | Boolean attribute; forces read-only regardless of token scopes. |

**Capability gating** (cosmetic UI — the server is the real enforcement gate):

| Scope in token | Effect |
|----------------|--------|
| `author:sql` | SQL mode tab available; editor editable; Run and Save buttons enabled. |
| `author:metric` | Metric mode tab available; editor editable; Run and Save buttons enabled. |
| Neither | Editor locked; no run/save buttons (read-only indicator shown). |

**Events emitted:**
- `nubi:run` — `{ sql, queryId, params }` — user ran the query.
- `nubi:save` — `{ sql, queryId, name }` — user saved.
- `nubi:dirty` — `{ dirty: boolean }` — editor content diverged from or returned to saved state.
- `nubi:error` — `{ message, code }` — error occurred.

**Monaco shadow-DOM note**: Monaco injects styles into `document.head` and does
not work inside a shadow root. The editor mounts in a light-DOM wrapper
positioned absolutely over the shadow-root placeholder, aligned via a
ResizeObserver.

---

### `<nubi-health>`

Data-health score + freshness dashboard widget. Fetches health scores from
`GET /api/v1/health/score` and freshness from `GET /api/v1/health/freshness`
(or the `?dataset_key=` single-dataset variants) and renders:

- A circular gauge showing the overall score and grade letter (averaged across
  datasets when showing multiple).
- A reasons list from the score response.
- A freshness table with RAG status dots (green = fresh, amber = stale < 24 h,
  red = stale > 24 h).

| Attribute | Required | Meaning |
|-----------|----------|---------|
| `get-token` / `token` | — | Bearer JWT |
| `backend` | No | API base URL. Default `http://localhost:8000`. |
| `theme` | No | `"dark"` (default) / `"light"`. |
| `dataset-key` | No | When set, filters score and freshness to a single dataset. |
| `no-sample-fallback` | No | Boolean. When present, shows an error state instead of sample data. |

**Events emitted:**
- `nubi:widget-ready` — `{ score, grade, datasets, renderer: "health" }` — data
  loaded. `datasets` is an array of `{ dataset_key, score, grade, fresh, last_updated, status }`.
- `nubi:widget-error` — `{ message }` — fetch failed.

**Sample fallback:** renders sample health data (4 datasets, mixed fresh/stale
statuses) when no backend is configured or the request fails.

**Example:**

```html
<!-- All datasets for the org -->
<nubi-health get-token="getMyToken" backend="https://api.example.com"></nubi-health>

<!-- Single dataset health card -->
<nubi-health
  get-token="getMyToken"
  backend="https://api.example.com"
  dataset-key="raw/orders"
></nubi-health>
```

---

## DOM events (outbound contract)

All events bubble and are `composed: true` so they pierce shadow DOM boundaries
and reach host-document listeners. Listen on the component element or any
ancestor:

```js
document.querySelector('nubi-query-editor').addEventListener('nubi:run', e => {
  console.log(e.detail.sql)
})
```

| Event | Payload (`e.detail`) | Emitting components |
|-------|---------------------|---------------------|
| `nubi:run` | `{ sql?, queryId?, metricId?, dimensions?, timeGrain?, params? }` | query-editor |
| `nubi:save` | `{ queryId?, sql?, name? }` | query-editor |
| `nubi:dirty` | `{ dirty: boolean }` | query-editor |
| `nubi:select` | `{ column?, value?, row? }` | table |
| `nubi:widget-ready` | `{ rows?, renderer: string, score?, grade?, datasets? }` | kpi, health |
| `nubi:widget-error` | `{ message: string }` | kpi, health |
| `nubi:error` | `{ message: string, code?: string }` | query-editor |

---

## Theme contract (25 tokens)

Every component accepts a `theme` attribute (`"dark"` or `"light"`) and
exposes all 25 CSS custom properties for fine-grained host overrides. Set them
on the host element or in a parent `<style>`:

```css
nubi-kpi {
  --nubi-primary: #7c3aed;
  --nubi-bg: #0a0a0a;
}
```

| Token | Dark default | Light default | Role |
|-------|-------------|---------------|------|
| `--nubi-bg` | `#0f1117` | `#ffffff` | Primary background |
| `--nubi-bg-2` | `#1a1f2e` | `#f8f9fc` | Secondary background (toolbars) |
| `--nubi-bg-3` | `#1e2433` | `#f1f3f7` | Tertiary background |
| `--nubi-fg` | `#e2e8f0` | `#1a202c` | Primary foreground text |
| `--nubi-fg-muted` | `#718096` | `#718096` | Muted / secondary text |
| `--nubi-accent` | `#1e2433` | `#edf2f7` | Surface / accent fill |
| `--nubi-border` | `#2d3748` | `#e2e8f0` | Border / divider |
| `--nubi-primary` | `#6366f1` | `#4f46e5` | Brand / interactive primary |
| `--nubi-primary-fg` | `#ffffff` | `#ffffff` | Text on primary background |
| `--nubi-success` | `#10b981` | `#059669` | Success / green |
| `--nubi-warning` | `#f59e0b` | `#d97706` | Warning / amber |
| `--nubi-error` | `#ef4444` | `#dc2626` | Error / red |
| `--nubi-radius` | `8px` | `8px` | Border radius (large) |
| `--nubi-radius-sm` | `4px` | `4px` | Border radius (small) |
| `--nubi-font-sans` | `system-ui, …` | `system-ui, …` | Sans-serif font stack |
| `--nubi-font-mono` | `'Fira Code', …` | `'Fira Code', …` | Monospace font stack |
| `--nubi-font-size-base` | `13px` | `13px` | Base font size |
| `--nubi-font-size-sm` | `11px` | `11px` | Small font size |
| `--nubi-font-size-xs` | `10px` | `10px` | Extra-small font size |
| `--nubi-line-height` | `1.5` | `1.5` | Default line height |
| `--nubi-toolbar-h` | `36px` | `36px` | Toolbar height |
| `--nubi-z-editor` | `100` | `100` | Editor z-index layer |
| `--nubi-z-overlay` | `200` | `200` | Overlay z-index layer |
| `--nubi-z-popover` | `300` | `300` | Popover z-index layer |
| `--nubi-transition` | `0.15s ease` | `0.15s ease` | Default CSS transition |

---

## Capability gating and server enforcement

Scope-gated components (`nubi-query-editor`) decode the JWT payload
client-side to show or hide UI controls. This is **cosmetic only**.
The server is the real enforcement gate:

- `author:sql` is required by the backend to accept and execute raw SQL.
- `author:metric` is required to save metric definitions.
- `read:*` / `read:query` is required for all data queries.

Removing a scope from the client-side token is not a security measure — the
server will reject the request regardless. The client-side gating only improves
UX by hiding inaccessible controls.

Scope decoding is provided by `decodeScopes(token)` and `hasScope(scopes, required)`
in `embed/nubi-context.js`. These functions never verify the token signature —
they only parse the payload for UI purposes.

---

## Cross-filter event bus (`NubiContext`)

For multi-widget pages where components should cross-filter each other, use
`createNubiContext` from `embed/nubi-context.js` to create a shared event bus:

```js
import { createNubiContext } from './nubi-embed.js'

const ctx = createNubiContext({
  getTokenFn: window.getToken,
  backend: 'https://api.example.com',
})

// Broadcast a filter from any source
ctx.emitFilter('region', 'EMEA')

// Subscribe in any component
const unsub = ctx.onFilter(({ column, value }) => {
  console.log('filter changed:', column, value)
})
```

The bus is an in-page `EventTarget`; it does not make any network requests.

---

## Server endpoints (embed surface)

All paths are mounted under `/api/v1`. "First-party" = a Nubi HS256 access token
(a logged-in session); "embed" = a host-signed RS256/ES256 JWT.

| Method & path | Auth | Purpose |
|---------------|------|---------|
| `POST /boards/{id}/share` | First-party | Return the embed descriptor: `embed_url`, `config_endpoint`, ready-to-paste `snippet`, the `mint` block (exact claims your backend must sign; `mint.token` is always `null`), and the `rls` summary. |
| `GET /embed/config/{dashboard_id}` | First-party **or** embed | Read-only board descriptor (`dashboard_id`, `title`, `widgets`, optional `spec`/`html`/`theme`). Each embed fetch starts one metered embedded session. |
| `POST /query`, `POST /query/*` | First-party **or** embed | Execute a query and stream Arrow IPC. Embed tokens **must** pass a registered `query_id`; raw `sql` is ignored/rejected. |
| `POST /embed/embed-token` | First-party | **Dev only.** Mint an HS256 first-party token. Gated by `EMBED_DEV_TOKEN_ENABLED=true`; refused (503) otherwise and in production. Returns `{ token, expires_in }`. |
| `POST /security/jwt-issuers` | First-party | Register a JWT issuer (public key). Also `GET` (list), `GET/PUT/DELETE /security/jwt-issuers/{issuer_id}`. |
| `POST /boards/{id}/snapshot` | First-party | Create a frozen DuckDB snapshot; `?snapshot_id=<id>` refreshes it. `GET /boards/{id}/snapshot` lists them. |
| `GET /embed/frozen/{dashboard_id}` | First-party **or** embed | Frozen-view descriptor + sidecar reference (`?snapshot_id=` optional). Metered like a live embedded session. |
| `POST /boards/{id}/export/public` | First-party | **UNSAFE.** Produce a no-auth public static export. Off by default — needs `ALLOW_UNSAFE_PUBLIC_EXPORTS` **and** per-org `public_exports_enabled`. |
| `GET /boards/{id}/export.csv` `.json` `.pdf` | First-party | Server-side data / vector-PDF export of a board (editor view, no RLS). |

## Embed JWT claims (field reference)

The claims Nubi verifies for a host-signed embed token (`backend/app/auth/verify.py`).
See the [Embedding guide](/docs/embedding#the-embed-jwt-claim-contract) for signing
examples.

| Claim | Required | Type | Notes |
|-------|----------|------|-------|
| `iss` | Yes | string | Must match a registered issuer exactly. |
| `sub` | Yes | string | Viewer / session id. |
| `aud` | Yes | string | Must match the issuer's configured audience. |
| `exp` | Yes | number | Unix seconds. Keep ≤ `iat + 900` (15 min). Missing → rejected. |
| `org` | Yes (embed) | string | Org for data + RLS scoping. Non-UUID values resolve against the org `external_key`. |
| `scope` | Yes | string[] or space-delimited string | Must grant `read:*`, `read:query`, or `read:dashboard:*`. |
| `policies` | For RLS | object | Per-viewer RLS predicates, e.g. `{"tenant_id":"acme"}`. Read from the verified token only. |
| `embed_origin` | Recommended | string | Pins the token to one browser `Origin`. |
| `roles` | Optional | string[] | Carried onto the verified identity. |
| `project` | Optional | string | Carried onto the verified identity. |
| `datastore` | Optional | string | Whole-dashboard connector override (embed tokens only). |
| `iat` | Optional | number | Issued-at. |
| `locked_params` | Optional | object | Consumed by the `/d/{id}` SPA viewer to lock variable values (see the guide's [Variables & parameters](/docs/embedding#variables--parameters)). |

Accepted algorithms: `RS256`, `RS384`, `RS512`, `ES256`, `ES384`, `ES512`.
`HS256` and `alg: none` are rejected on the embed path.

## Error codes

Errors return the shape `{ "code": "<code>", "message": "..." }` with the HTTP
status below.

| Code | HTTP | When |
|------|------|------|
| `invalid_token` | 401 | Malformed/expired token, bad signature, unknown or disabled `iss`, missing required claim (`exp`/`aud`/`iss`/`sub`), or `alg: none`/HS256 on the embed path. |
| `origin_mismatch` | 403 | The token carries `embed_origin` and the request `Origin` header is missing or does not match. |
| `insufficient_scope` | 403 | The token lacks a qualifying read scope (or a query's `required_scope`). |
| `query_not_registered` | 403 | An embed request omitted `query_id`, or the id does not resolve to a registered query in the caller's org. |
| `dashboard_not_found` | 404 | No board with that id exists in the resolved org (also returned to embed identities when a protected default environment has no pinned version — no draft leak). |
| `snapshot_not_found` | 404 | No snapshot (or no snapshot with the given `snapshot_id`) exists for the board. |
| `public_exports_disabled` | 403 | UNSAFE public export refused — the deployment switch or the per-org toggle is off. |
| `board_not_found` | 404 | Export/snapshot board lookup missed in the org. |

---

## OpenAPI schema

The FastAPI app auto-generates an OpenAPI schema available at runtime at:

```
GET /openapi.json
```

(In development mode the interactive Swagger UI is at `/docs`.)

To dump a static snapshot, run with the app's Python environment active:

```bash
cd backend
python -c "
import json, sys
sys.path.insert(0, '.')
from main import app
print(json.dumps(app.openapi(), indent=2))
" > ../docs/openapi.json
```

Importing `main` requires the full backend environment (database drivers,
etc.). In CI without a live DB this import will fail; the recommended approach
is to hit `GET /openapi.json` against a running dev instance and pipe the
output to `docs/openapi.json`. A committed snapshot is not included in this
repo for that reason — rely on the live `/openapi.json` route or generate it
from a running instance.
