# Dashboards

A Nubi dashboard is a `DashboardSpec` — a structured JSON document that the editor, the LLM authoring pipeline, and the MCP server all share as the single source of truth. The spec compiles to a CSS-grid HTML fragment composed of five custom elements: `<nubi-kpi>`, `<nubi-table>`, `<nubi-chart>`, `<nubi-filter>`, and `<nubi-text>`. DOMPurify strips all `<script>` tags and inline event handlers at storage time.

---

## The DashboardSpec

The canonical spec format (version 1):

```json
{
  "version":   1,
  "title":     "Revenue Overview",
  "layout":    { "cols": 12, "row_height": 60 },
  "variables": [
    { "name": "region", "type": "text", "default": "EMEA" }
  ],
  "widgets": [
    {
      "id":       "w1",
      "type":     "kpi",
      "query_id": "revenue_total",
      "encoding": { "value": "revenue" },
      "props":    { "label": "Total Revenue", "format": "currency" },
      "pos":      { "x": 1, "y": 1, "w": 4, "h": 2 }
    }
  ]
}
```

### `DashboardSpec` Fields

| Field | Type | Description |
|-------|------|-------------|
| `version` | `int` | Schema version. Currently `1`. |
| `title` | `string` | Human-readable dashboard title. Min length 1. |
| `layout.cols` | `int` | Number of CSS-grid columns (default: 12). |
| `layout.row_height` | `int` | Row height in pixels (default: 60). |
| `variables` | `array` | Dashboard-level variables (see [Variables](#variables)). |
| `widgets` | `array` | Ordered list of widgets. |

### `Widget` Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Unique stable identifier within the spec. Min length 1. |
| `type` | `string` | `kpi` \| `table` \| `chart` \| `filter` \| `text` |
| `query_id` | `string` | Registered query that backs this widget. Required for `kpi`, `table`, `chart`; optional for `filter` and `text`. |
| `chart_type` | `string \| null` | Required for chart widgets: `line` \| `bar` \| `scatter` \| `area` \| `pie` |
| `encoding` | `object` | Column mapping. For charts: `x`, `y` (required), `color` (optional). For KPI: `value`. |
| `props` | `object` | Extra props: `label`, `limit`, `format`, `columns`, etc. |
| `pos` | `object` | Grid position: `x`, `y`, `w`, `h` (1-based, in grid units). All fields `>= 1`. |
| `subtype` | `string \| null` | Filter sub-type: `select` \| `multiselect` \| `daterange` \| `text`. Required for filter widgets. |
| `options_query_id` | `string \| null` | Registered query providing select/multiselect options. |
| `target_var` | `string \| null` | Variable name this filter writes to. Required for filter widgets. |
| `content` | `string \| null` | Markdown content for text widgets. Required for text widgets. |
| `params` | `object` | Named param bindings: `{paramName: {ref: '<varName>'} \| <literal>}`. |

---

## Variables

Dashboard variables are declared at the spec level. Filter widgets write to variables; data widgets re-query when referenced variables change.

```json
"variables": [
  { "name": "region",     "type": "select",    "default": "EMEA" },
  { "name": "date_range", "type": "daterange", "default": null   }
]
```

Variable types: `text`, `number`, `date`, `daterange`, `select`, `multiselect`.

Widget `params` can reference variables via `{ref: '<varName>'}`:

```json
"params": {
  "region":     { "ref": "region"     },
  "date_range": { "ref": "date_range" }
}
```

Ref names must resolve to declared `variables` on the spec — undeclared refs are a hard validation error.

---

## Route Params — `/d/:id?var=value`

Dashboard variables are synced to and from URL search params on the `/d/:id` route.

**Precedence (highest → lowest):**
1. Embed-token-locked params (tokens can lock param values so filter widgets cannot override them)
2. URL search params (`?varName=value`)
3. `spec.variables` defaults

When a filter widget changes a variable, the new value is written back to the URL as a shallow replace (no extra browser history entry). This makes dashboards **shareable with pre-set filters** — just copy the URL.

```
# Share a dashboard pre-filtered to the US-West region and 2024:
/d/abc123?region=US-West&year=2024
```

---

## Widget Kit

### `<nubi-kpi>` — Big-Number Metric Card

Displays a single formatted value from the first row of a query result.

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

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query-id` | Yes | Registered query id |
| `value-col` | Yes | Column to read from the first row |
| `label` | No | Display label (defaults to `value-col`) |
| `format` | No | `number` (default, auto-compact K/M) \| `currency` (USD) \| `percent` \| `integer` |

The KPI value column is resolved from `encoding.value`, then `encoding.y`, then `props.value_col`, then `encoding.x`.

---

### `<nubi-table>` — HTML Data Table

Renders query results as a paginated table with a sticky header.

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

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query-id` | Yes | Registered query id |
| `limit` | No | Max rows to display (default: 50 in spec compilation; 100 in the element default) |
| `columns` | No | Comma-separated column names to show (all shown if omitted) |

---

### `<nubi-chart>` — Auto-WebGL/SVG Chart

Renders scatter, line, bar, area, or pie charts. The renderer is chosen automatically:

| Condition | Renderer |
|-----------|----------|
| Scatter + rows > 20 000 | WebGL canvas via **regl** — reads Arrow columns directly, ~1M pts interactive |
| Scatter + rows ≤ 20 000 | Inline SVG circles |
| Line | Inline SVG polyline (any row count) |
| Bar | Inline SVG rects, capped at 40 bars |
| `area` | Mapped to `line` at compile time |
| `pie` | Mapped to `bar` at compile time |

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

| Attribute | Required | Description |
|-----------|----------|-------------|
| `query-id` | Yes | Registered query id |
| `type` | No | `scatter` (default) \| `line` \| `bar` |
| `x` | Yes | Column name for X axis |
| `y` | Yes | Column name for Y axis |
| `color` | No | Column for per-point color (scatter only) |

---

### `<nubi-filter>` — Interactive Filter Widget

A filter widget that writes to a dashboard variable when the user changes its value.

```html
<nubi-filter
  subtype="select"
  target-var="region"
  options-query-id="regions_list"
  label="Region">
</nubi-filter>
```

| Attribute | Required | Description |
|-----------|----------|-------------|
| `subtype` | Yes | `select` \| `multiselect` \| `daterange` \| `text` |
| `target-var` | Yes | Variable name this filter writes to |
| `query-id` | No | Optional backing query for the widget |
| `options-query-id` | No | Query that provides select/multiselect option values |
| `label` | No | Human-readable label |

---

### `<nubi-text>` — Markdown Text Block

Renders markdown content as a static text widget. Useful for section headers, explanatory notes, or dividers.

```html
<nubi-text>## Section header\nSome explanatory text.</nubi-text>
```

The `content` field in the spec carries the raw markdown; the frontend element renders it. The content is HTML-escaped during compilation with `html.escape()`.

---

## Spec Validation

`validate_spec(data)` in `app/dashboards/spec.py` checks:

1. Pydantic field types and required fields (returns `None, [errors]` on parse failure).
2. Widget `id` uniqueness — duplicate ids produce a warning.
3. Chart widgets must have `chart_type` and `encoding.x` + `encoding.y`.
4. Filter widgets must have `subtype` and `target_var`.
5. Text widgets must have `content`.
6. Widget `params` that use `{ref: 'varName'}` must reference a declared variable (hard error).
7. `query_id` values are checked against the live registry (soft warning for unknown ids — forward-compatible).

Returns `(spec, issues)`. An empty `issues` list means the spec is fully valid.

---

## Creating Dashboards via the API

```js
// Using @nubi/sdk
const board = await client.resources.boards.create({
  name: 'Revenue Overview',
  config: {
    spec: {
      version: 1,
      title: 'Revenue Overview',
      layout: { cols: 12, row_height: 60 },
      variables: [{ name: 'region', type: 'text', default: 'EMEA' }],
      widgets: [
        {
          id: 'w1', type: 'kpi', query_id: 'revenue_total',
          encoding: { value: 'revenue' },
          props: { label: 'Total Revenue', format: 'currency' },
          pos: { x: 1, y: 1, w: 4, h: 2 }
        }
      ]
    }
  },
})
```

---

## DOMPurify Sanitizer

All dashboard HTML — whether generated by AI or compiled from a spec — is run through DOMPurify before storage. Allowed elements include the five `<nubi-*>` custom elements plus standard layout HTML (`<div>`, `<p>`, `<h1>`–`<h6>`, `<ul>`, `<ol>`, `<li>`, `<span>`). Blocked: `<script>`, `<style>`, `on*` event handler attributes, and `javascript:` links.

The spec compiler (`spec_to_html`) never emits `<script>` tags, `on*` handlers, or `javascript:` URIs. All string values written into HTML attributes are escaped with `html.escape()`.

---

## AI Dashboard Generation

`POST /api/v1/ai/dashboard` accepts a natural-language question and returns a compiled dashboard:

```json
{ "question": "Show me revenue by region for Q1 2024" }
```

Response:

```json
{
  "spec":      { "version": 1, "title": "...", "widgets": [...] },
  "html":      "<div class='nubi-dashboard'>...</div>",
  "grounding": { "relevant_tables": [...], "relevant_columns": [...] },
  "provider":  "null",
  "valid":     true,
  "issues":    []
}
```

Get the JSON Schema for the spec (useful for grounding your own LLMs):

```
GET /api/v1/ai/dashboard/schema
```

---

## Dashboard Versioning and CLI Diff

```bash
nubi diff ./dashboards     # compare local JSON files against server
nubi deploy ./dashboards   # live deploy
nubi deploy ./dashboards --dry-run  # preview only
```

---

## Events

All widget elements dispatch these events on the host page (bubble, `composed: true`):

| Event | `detail` | Fired when |
|-------|----------|------------|
| `nubi:widget-ready` | `{ rows, renderer }` | After successful render |
| `nubi:widget-error` | `{ message }` | On any non-recoverable error |

```js
document.addEventListener('nubi:widget-ready', (e) => {
  console.log('rendered', e.detail.rows, 'rows via', e.detail.renderer)
})
```
