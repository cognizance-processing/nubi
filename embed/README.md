# Nubi Embed

Drop-in web components for embedding Nubi dashboards and data widgets into any
host page. No framework required — pure vanilla JS custom elements with
apache-arrow and (for WebGL scatter charts) regl bundled in.

Two independently loadable bundles are produced from this directory:

| Bundle | Source entry | Build command | Output |
|--------|-------------|--------------|--------|
| `nubi-dashboard` | `embed/nubi-dashboard.js` | `npm run build:embed` | `dist-embed/nubi-dashboard.js` (UMD) + `dist-embed/nubi-dashboard.es.js` (ESM) |
| `nubi-widgets` | `embed/widgets/index.js` | `npm run build:widgets` | `dist-embed/nubi-widgets.js` (UMD) + `dist-embed/nubi-widgets.es.js` (ESM) |

Both commands are run from the **repo root** (where `package.json` lives).

---

## Building

```bash
# From repo root
npm install

# Build the <nubi-dashboard> bundle
npm run build:embed

# Build the widget kit (<nubi-kpi> / <nubi-table> / <nubi-chart>)
npm run build:widgets
```

Output lands in `dist-embed/`. Both builds use `emptyOutDir: false` so they
coexist in the same directory without clobbering each other.

---

## `<nubi-dashboard>` — read-only dashboard embed

**Source:** `embed/nubi-dashboard.js`

A single custom element that fetches Arrow IPC data from the Nubi query API
and renders it as a styled HTML table inside a Shadow DOM. When the backend
is unreachable (no `backend` attribute, CORS error, auth failure) the component
falls back to an inline sample table and shows an orange "preview (sample data)"
banner so demo pages always render something visible.

### Loading

```html
<!-- UMD (plain <script>) — registers <nubi-dashboard> automatically -->
<script src="https://cdn.example.com/dist-embed/nubi-dashboard.js"></script>

<!-- or ES module -->
<script type="module" src="https://cdn.example.com/dist-embed/nubi-dashboard.es.js"></script>
```

### Basic usage

```html
<nubi-dashboard
  query="SELECT region, SUM(revenue) AS total FROM sales GROUP BY 1"
  get-token="getEmbedToken"
  backend="https://api.example.com"
  style="display:block; height:300px;">
</nubi-dashboard>
```

### Attribute reference

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query` | Yes | SQL string or a registered query id. |
| `token` | One of these | Static JWT string. Mutually exclusive with `get-token`. |
| `get-token` | One of these | Name of a function on `window` that returns `Promise<string>` or `string`. Called before each fetch. |
| `backend` | No | Base URL of the Nubi API, e.g. `https://api.example.com`. Defaults to `http://localhost:8000`. |
| `theme` | No | Reserved for future preset names. Theming is currently done via CSS custom properties. |

### Events dispatched

All events bubble and are `composed: true` (cross Shadow DOM boundary).

| Event | `detail` shape | Fired when |
|-------|---------------|------------|
| `nubi:ready` | `{ rowCount: number }` | After a successful render (real or sample). |
| `nubi:error` | `{ message: string }` | On any non-recoverable error (before falling back to sample). |
| `nubi:query-run` | `{ rowCount, cacheStatus, elapsedMs, sample }` | After each query attempt or sample fallback. |

```js
document.addEventListener('nubi:ready', (e) => {
  console.log('rows:', e.detail.rowCount)
})
document.addEventListener('nubi:query-run', (e) => {
  console.log('cache:', e.detail.cacheStatus, 'sample:', e.detail.sample)
})
```

### CSS custom properties

Set on any ancestor element (or `:root`) to theme the component:

| Property | Default | Role |
|----------|---------|------|
| `--nubi-bg` | `#0f1117` | Background colour of the table wrapper |
| `--nubi-fg` | `#e2e8f0` | Primary foreground / text colour |
| `--nubi-accent` | `#1e2433` | Header row and toolbar background |
| `--nubi-border` | `#2d3748` | Cell / table border colour |

```css
nubi-dashboard {
  --nubi-bg:     #ffffff;
  --nubi-fg:     #1a1a2e;
  --nubi-accent: #f0f4ff;
  --nubi-border: #dde1ea;
}
```

### Sample fallback

On **any** failure (network, auth, parse error) the component renders a built-in
five-row sample table (`id`, `name`, `value`, `active`, `category`) and displays
an orange "preview (sample data)" banner. The `nubi:error` event is still fired
so the host can log or surface the underlying error.

---

## Widget Kit — `<nubi-kpi>` / `<nubi-table>` / `<nubi-chart>`

**Source:** `embed/widgets/` (`index.js`, `nubi-kpi.js`, `nubi-table.js`,
`nubi-chart.js`, `glScatter.js`, `shared.js`)

Three purpose-built custom elements for LLM-authored dashboards. All widgets:
- Fetch Arrow IPC data by registered query id (POST `/api/v1/query`).
- Fall back to visible sample data on any error so pages always render.
- Share the same token resolution and CSS theming contract as `<nubi-dashboard>`.

### Loading

```html
<!-- UMD: auto-calls registerNubiWidgets() on load -->
<script src="https://cdn.example.com/dist-embed/nubi-widgets.js"></script>

<!-- or ES module: call registerNubiWidgets() explicitly -->
<script type="module">
  import { registerNubiWidgets } from 'https://cdn.example.com/dist-embed/nubi-widgets.es.js'
  registerNubiWidgets()
</script>
```

The UMD build calls `registerNubiWidgets()` automatically. The function is
idempotent — safe to call multiple times.

---

### `<nubi-kpi>` — big-number metric card

Displays a single formatted metric value from the first row of a query result.

```html
<nubi-kpi
  query-id="revenue_total"
  value-col="revenue"
  label="Total Revenue"
  format="currency"
  get-token="getEmbedToken"
  backend="https://api.example.com"
  style="height:110px;">
</nubi-kpi>
```

**Attributes:**

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query-id` | Yes | Registered query id to execute. |
| `value-col` | Yes | Column name to read the metric value from (first row). |
| `label` | No | Display label shown below the number. Defaults to `value-col`. |
| `format` | No | `"number"` (default, auto-compact K/M) \| `"currency"` (USD) \| `"percent"` \| `"integer"`. |
| `token` | — | Static JWT. |
| `get-token` | — | Window function name returning `Promise<string>`. |
| `backend` | No | API base URL. Defaults to `http://localhost:8000`. |

**Events:** `nubi:widget-ready` `{ rows, renderer: 'kpi' }`, `nubi:widget-error` `{ message }`.

---

### `<nubi-table>` — HTML data table

Renders query results as a paginated HTML table with a sticky header.

```html
<nubi-table
  query-id="events_summary"
  limit="50"
  columns="id,name,value,category"
  get-token="getEmbedToken"
  backend="https://api.example.com"
  style="display:block; height:280px;">
</nubi-table>
```

**Attributes:**

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query-id` | Yes | Registered query id. |
| `limit` | No | Max rows to display. Default: `100`. |
| `columns` | No | Comma-separated list of column names to show (ordered). All columns shown if omitted. |
| `token` / `get-token` | — | Same as other widgets. |
| `backend` | No | API base URL. |

**Events:** `nubi:widget-ready` `{ rows, renderer: 'table' }`, `nubi:widget-error` `{ message }`.

---

### `<nubi-chart>` — auto-WebGL/SVG chart

Renders scatter, line, or bar charts. Auto-selects the renderer based on row count:

- **Scatter + rows > 20 000** → WebGL canvas via `glScatter.js` (regl-based). If WebGL context creation fails, falls back to 2D canvas and emits `nubi:widget-error`.
- **Scatter + rows ≤ 20 000** → inline SVG circles.
- **Line** → inline SVG polyline (any row count).
- **Bar** → inline SVG rects, capped at 40 bars for readability (any row count).

```html
<nubi-chart
  query-id="scatter_demo"
  type="scatter"
  x="revenue"
  y="churn_rate"
  color="segment"
  get-token="getEmbedToken"
  backend="https://api.example.com"
  style="display:block; height:280px;">
</nubi-chart>
```

**Attributes:**

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query-id` | Yes | Registered query id. |
| `type` | No | `"scatter"` (default) \| `"line"` \| `"bar"`. |
| `x` | Yes | Column name for X axis. Falls back to the first schema column if omitted. |
| `y` | Yes | Column name for Y axis. Falls back to the second schema column if omitted. |
| `color` | No | Column name for categorical per-point color (scatter only). |
| `token` / `get-token` | — | Same as other widgets. |
| `backend` | No | API base URL. |

**WebGL threshold:** `WEBGL_THRESHOLD = 20000`. Exported from the ES module:

```js
import { WEBGL_THRESHOLD } from './dist-embed/nubi-widgets.es.js'
console.log(WEBGL_THRESHOLD) // 20000
```

**Events:** `nubi:widget-ready` `{ rows, renderer: 'webgl'|'svg'|'canvas' }`,
`nubi:widget-error` `{ message }`.

---

## `getToken()` contract

Both `<nubi-dashboard>` and all widget kit elements accept a `get-token` attribute
whose value is the **name** of a function on `window`. That function must conform
to this contract:

```js
getToken: () => Promise<string>   // returns a valid JWT
```

The function is called before every query fetch. It must return a short-lived JWT
(≤ 15 minutes recommended) signed by the host's private key.

**Reference implementation:** `embed/getToken.reference.js` — copy into your
host app and replace `mintUrl` with your backend's token-mint endpoint URL:

```js
import { createGetToken } from './getToken.reference.js'

window.getEmbedToken = createGetToken({
  mintUrl: '/api/embed-token',          // your backend endpoint
  fetchOptions: { credentials: 'include' }, // send your session cookie
})
```

`createGetToken` handles in-memory caching and proactive refresh (~60 s before
expiry). It never writes the token to `localStorage` or `sessionStorage`.

### Required JWT claims

```json
{
  "iss": "https://your-app.example.com",
  "sub": "user-or-service-id",
  "aud": "nubi:<project-id>",
  "org": "your-org-slug",
  "project": "your-project-slug",
  "roles": ["viewer"],
  "scope": ["read:*"],
  "policies": { "tenant_id": "acme" },
  "embed_origin": "https://your-host-page.example.com",
  "exp": 1234567890,
  "iat": 1234566990
}
```

The Nubi backend verifies the signature (RS256 or ES256 via JWKS), `exp`, `aud`,
`iss`, `embed_origin` (must match the request's `Origin` header), and injects
`policies` as server-side RLS predicates.

---

## Demo pages

The runnable demos live under [`examples/`](../examples/) so the source tree
here stays pure component code:

| Demo | Bundle needed | Purpose |
|------|--------------|---------|
| [`examples/embed-demo/`](../examples/embed-demo/) | `dist-embed/nubi-dashboard.js` | Full `<nubi-dashboard>` with per-tenant JWT RLS (`index.html`, needs the backend) and a zero-setup mock-token variant (`demo-mock.html`). |
| [`examples/widgets-demo/`](../examples/widgets-demo/) | `dist-embed/nubi-widgets.js` | The standalone widget kit — `<nubi-kpi>` / `<nubi-table>` / `<nubi-chart>`. |

The `demo-mock.html` and `widgets-demo` pages run entirely from built-in
sample data — no backend required. Open them after building the bundles:

```bash
# build both bundles, then open the mock-data demos
npm run build:embed && npm run build:widgets
open examples/embed-demo/demo-mock.html
open examples/widgets-demo/index.html
```

---

## File layout

```
embed/
├── nubi-dashboard.js          <nubi-dashboard> custom element source
├── getToken.reference.js      Reference getToken() implementation + contract docs
└── widgets/
    ├── index.js               Entry point — registerNubiWidgets()
    ├── nubi-kpi.js            <nubi-kpi> element
    ├── nubi-table.js          <nubi-table> element
    ├── nubi-chart.js          <nubi-chart> element (auto WebGL/SVG)
    ├── glScatter.js           WebGL scatter renderer (regl)
    └── shared.js              Shared helpers: resolveToken, fetchArrow, formatters, BASE_STYLES

dist-embed/                    (generated — do not edit)
├── nubi-dashboard.js          UMD build of <nubi-dashboard>
├── nubi-dashboard.es.js       ESM build of <nubi-dashboard>
├── nubi-widgets.js            UMD build of widget kit
└── nubi-widgets.es.js         ESM build of widget kit
```
