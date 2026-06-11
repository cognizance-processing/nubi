# DashboardSpec reference (for AI agents)

Single-file, paste-into-context reference for authoring a Nubi **DashboardSpec** —
the canonical JSON format shared by the drag-and-drop editor and the LLM
authoring pipeline. Field names, types, and enums below are exact
(`backend/app/dashboards/spec.py`). The authoritative machine-readable schema is
`GET /api/v1/ai/dashboard/schema`; cross-check against it if in doubt.

A spec is a JSON document. Validate it with `POST /api/v1/dashboards/validate`
before publishing.

---

## 1. Structure

### DashboardSpec (top level)

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `version` | int (≥1) | no | `1` | Schema version. Currently `1`. |
| `title` | string (min len 1) | **yes** | — | Human-readable dashboard title. |
| `layout` | object | no | `{"cols": 12, "row_height": 60}` | CSS-grid config. `cols` = grid columns, `row_height` = px per row. |
| `variables` | `Variable[]` | no | `[]` | Dashboard-level variables (shared across tabs). |
| `tabs` | `Tab[]` | no | `[]` | Optional tabs. Empty ⇒ no tabs (behaves as a single section). |
| `widgets` | `Widget[]` | no | `[]` | Ordered list of widgets to render. |

### Variable

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `name` | string (min len 1) | **yes** | — | Unique variable name within the spec (e.g. `"region"`). |
| `type` | enum | **yes** | — | One of `text`, `number`, `date`, `daterange`, `select`, `multiselect`. |
| `default` | any | no | `null` | Default value. |
| `mode` | enum or null | no | `null` | `scan` (default; re-reads server data on change) or `slice` (subsets already-fetched rows client-side, never sent to server). Metadata only. |

### Tab

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `id` | string (min len 1) | **yes** | — | Stable, unique tab id within the spec (e.g. `"t1"`). |
| `label` | string (min len 1) | **yes** | — | Tab label shown in the tab bar. |
| `style` | object | no | `{}` | Optional per-tab style-token overrides (sanitized on the frontend). |

### Widget

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `id` | string (min len 1) | **yes** | — | Stable, unique widget id (e.g. `"w1"`). |
| `type` | enum | **yes** | — | `kpi`, `table`, `chart`, `filter`, `text` (canonical). Extended: `metric`, `pivot`, `section`, `html` (frontend-only renderers). |
| `tab_id` | string or null | no | `null` | Tab this widget belongs to. `null` ⇒ first tab when `tabs` is non-empty. Must reference a declared tab id (hard error otherwise). Ignored for drawer widgets. |
| `query_id` | string | for `kpi`/`table`/`chart` | `""` | Registered query id backing this widget. Empty for `text`; optional for `filter` (which may use `options_query_id`). |
| `chart_type` | enum or null | **yes for `chart`** | `null` | `line`, `bar`, `hbar`, `scatter`, `area`, `pie`, `donut`, `heatmap`, `gauge`. |
| `encoding` | object (str→str) | charts need `x`,`y` | `{}` | Column encoding. Charts: `x`, `y` (required), optional `color`. KPI: `value` (value column alias). |
| `props` | object | no | `{}` | Extra props, e.g. `label`, `format`, `limit`, `columns`, `value_col`. |
| `pos` | `WidgetPos` | **yes** | — | Grid position/size. |
| `drawer` | bool | no | `false` | Render inside a slide-out drawer instead of the grid. |
| `drawer_group` | string or null | no | `null` | `"filters"` (shared filters drawer) or `"dg_<id>"` (a drilldown drawer). |
| `order` | int | no | `0` | Sort order within a drawer. |
| `subtype` | enum or null | **yes for `filter`** | `null` | `select`, `multiselect`, `daterange`, `text`. |
| `options_query_id` | string or null | no | `null` | Query providing dropdown options for `select`/`multiselect` filters. |
| `target_var` | string or null | **yes for `filter`** | `null` | Variable name this filter writes to. |
| `content` | string or null | **yes for `text`** | `null` | Markdown content for `text` widgets. |
| `params` | object | no | `{}` | Named param bindings: `{paramName: {"ref": "<varName>"} | <literal>}`. Refs must resolve to a declared variable. |

### WidgetPos

All values are CSS-grid units. `x`/`y` are 1-based starts; `w`/`h` are spans.

| Field | Type | Constraint |
|-------|------|------------|
| `x` | int | ≥1 (column start) |
| `y` | int | ≥1 (row start) |
| `w` | int | ≥1 (column span) |
| `h` | int | ≥1 (row span) |

---

## 2. Authoring conventions

These mirror the `conventions` block returned by `GET /api/v1/ai/context` and the
validators in `validate_spec`.

- **Query binding.** Bind a data widget to a query by its `id` via `query_id`.
  Every `query_id` (and `options_query_id`) must exist in the live query
  registry — discover real ids with `GET /ai/context`. (Unknown ids are a
  *warning*, not a hard error, for forward-compat — but you should treat them as
  errors when authoring.)
- **Column binding.** A widget's `encoding`/`columns` must reference names from
  that query's `output_schema`. Never invent column names.
- **Variables & `{{vars.*}}`.** Declare dashboard-level variables in
  `variables`. They are referenced in templated text/config with
  `{{vars.<name>}}` and routed to a query's matching **named param** by name at
  execution time. A widget binds a param to a variable with
  `params: {<paramName>: {"ref": "<varName>"}}`. A `{"ref": ...}` to an
  undeclared variable is a **hard error**.
- **Params.** A param value is either a literal scalar or a `{"ref": "<varName>"}`.
  Respect each query param's `name`, `type`, and `required`.
  `select`/`multiselect` params draw options from another query via
  `options_query_id`.
- **Filters → variables (cascading).** A `filter` widget writes the user's
  selection to its `target_var`. Any data widget whose `params` reference that
  variable re-queries when it changes. Cascade by chaining: filter A writes
  `var_a`; filter B's `options_query_id` is a query parameterised by `var_a`
  (bind via B's `params`); a chart reads both. Filters require `subtype` and
  `target_var` (hard errors if missing).
- **Tabs.** Declare tabs in `tabs`; bind a widget with `tab_id`. A widget with
  `tab_id: null` belongs to the **first** tab. A `tab_id` not declared in `tabs`
  is a **hard error**. Variables are global across tabs. Drawer widgets
  (`drawer: true`) stay global and ignore `tab_id`.
- **Chart requirement.** `chart` widgets MUST set `chart_type` and an `encoding`
  with at least `x` and `y` (hard errors if missing).
- **Text requirement.** `text` widgets MUST set `content`.
- **Uniqueness.** Widget ids and tab ids must be unique within the spec.

### What `POST /dashboards/validate` returns

`{valid, errors, warnings}`. Each issue carries a JSON `path`, a machine `code`,
and (where it can) `valid_options` — e.g. the bound query's real output columns
for a bad chart encoding, or the known query ids for an unknown `query_id`. The
endpoint never persists the spec; it is a pure oracle. `valid` is true iff there
are zero error-severity issues. Loop: validate → read `errors` → patch the
exact `path` using `valid_options` → re-validate until `valid: true`.

---

## 3. How to build a dashboard as an agent

1. **Discover.** `GET /api/v1/ai/context` (optionally `?q=<intent>` to rank,
   `?compact=true` to shrink). Record each query's `id`, `params`
   (name/type/required), and `output_schema` columns. These are the ONLY ids
   and columns you may reference.
2. **Generate.** Either author the `DashboardSpec` JSON directly (against
   `GET /ai/dashboard/schema`), or call `POST /api/v1/ai/dashboard` with
   `{question}` to get a grounded spec.
3. **Validate.** `POST /api/v1/dashboards/validate` with `{spec}`.
4. **Repair.** For each error, use `path` + `valid_options` to fix the field;
   re-validate until `valid: true`. Common fixes: replace an invented column
   with a real `output_schema` name; add `chart_type`/`encoding.x`/`encoding.y`
   to charts; add `subtype`/`target_var` to filters; declare a referenced
   variable.
5. **Preview.** `POST /api/v1/query/estimate` to dry-run scan/cost for the
   backing queries before running them (same auth/scope/RLS gates as `POST
   /query`; no execution, no metering).
6. **Publish.** Persist the spec onto a board, then embed read-only via
   `GET /api/v1/embed/config/{dashboard_id}`. Promotion to higher environments
   is human-gated (`POST /api/v1/environments/promote`, writer scope).

---

## 4. Example specs (complete & valid)

All examples bind to real seeded demo queries — `demo_all`, `demo_active`,
`demo_points_10k` (columns `id, x, y, category`), and `demo_by_region` (named
param `region: text`). Replace these with the ids/columns from your own
`GET /ai/context` response. JSON shown is the full spec document.

### 4.1 KPI

```json
{
  "version": 1,
  "title": "Active rows KPI",
  "layout": {"cols": 12, "row_height": 60},
  "widgets": [
    {
      "id": "k1",
      "type": "kpi",
      "query_id": "demo_active",
      "encoding": {"value": "id"},
      "props": {"label": "Active rows", "format": "0,0"},
      "pos": {"x": 1, "y": 1, "w": 3, "h": 2}
    }
  ]
}
```

### 4.2 Table

```json
{
  "version": 1,
  "title": "All rows table",
  "layout": {"cols": 12, "row_height": 60},
  "widgets": [
    {
      "id": "t1",
      "type": "table",
      "query_id": "demo_all",
      "props": {"limit": 50, "columns": ["id", "name", "active"]},
      "pos": {"x": 1, "y": 1, "w": 8, "h": 6}
    }
  ]
}
```

### 4.3 Chart — line

```json
{
  "version": 1,
  "title": "Points — line",
  "layout": {"cols": 12, "row_height": 60},
  "widgets": [
    {
      "id": "c1",
      "type": "chart",
      "chart_type": "line",
      "query_id": "demo_points_10k",
      "encoding": {"x": "id", "y": "y"},
      "pos": {"x": 1, "y": 1, "w": 6, "h": 4}
    }
  ]
}
```

### 4.4 Chart — bar with color series

```json
{
  "version": 1,
  "title": "Points by category — bar",
  "layout": {"cols": 12, "row_height": 60},
  "widgets": [
    {
      "id": "c2",
      "type": "chart",
      "chart_type": "bar",
      "query_id": "demo_points_10k",
      "encoding": {"x": "category", "y": "id", "color": "category"},
      "pos": {"x": 1, "y": 1, "w": 6, "h": 4}
    }
  ]
}
```

### 4.5 Chart — scatter

```json
{
  "version": 1,
  "title": "Points — scatter",
  "layout": {"cols": 12, "row_height": 60},
  "widgets": [
    {
      "id": "c3",
      "type": "chart",
      "chart_type": "scatter",
      "query_id": "demo_points_10k",
      "encoding": {"x": "x", "y": "y", "color": "category"},
      "pos": {"x": 1, "y": 1, "w": 6, "h": 5}
    }
  ]
}
```

### 4.6 Filter (select) writing to a variable + a chart that reads it

```json
{
  "version": 1,
  "title": "Region-filtered demo",
  "layout": {"cols": 12, "row_height": 60},
  "variables": [
    {"name": "region", "type": "select", "default": null}
  ],
  "widgets": [
    {
      "id": "f1",
      "type": "filter",
      "subtype": "select",
      "target_var": "region",
      "options_query_id": "demo_all",
      "props": {"label": "Region"},
      "pos": {"x": 1, "y": 1, "w": 3, "h": 1}
    },
    {
      "id": "t2",
      "type": "table",
      "query_id": "demo_by_region",
      "params": {"region": {"ref": "region"}},
      "props": {"limit": 50},
      "pos": {"x": 1, "y": 2, "w": 8, "h": 6}
    }
  ]
}
```

### 4.7 Text (markdown)

```json
{
  "version": 1,
  "title": "Annotated board",
  "layout": {"cols": 12, "row_height": 60},
  "widgets": [
    {
      "id": "x1",
      "type": "text",
      "content": "## Overview\nThis board summarises the demo dataset. Use the filter to scope by region.",
      "pos": {"x": 1, "y": 1, "w": 12, "h": 2}
    }
  ]
}
```

### 4.8 Cascading filters (filter A scopes filter B's options + a chart)

```json
{
  "version": 1,
  "title": "Cascading filters",
  "layout": {"cols": 12, "row_height": 60},
  "variables": [
    {"name": "region", "type": "select", "default": null},
    {"name": "active", "type": "select", "default": null}
  ],
  "widgets": [
    {
      "id": "f1",
      "type": "filter",
      "subtype": "select",
      "target_var": "region",
      "options_query_id": "demo_all",
      "props": {"label": "Region"},
      "pos": {"x": 1, "y": 1, "w": 3, "h": 1}
    },
    {
      "id": "f2",
      "type": "filter",
      "subtype": "select",
      "target_var": "active",
      "options_query_id": "demo_by_region",
      "params": {"region": {"ref": "region"}},
      "props": {"label": "Status (scoped by region)"},
      "pos": {"x": 4, "y": 1, "w": 3, "h": 1}
    },
    {
      "id": "c1",
      "type": "chart",
      "chart_type": "bar",
      "query_id": "demo_by_region",
      "params": {"region": {"ref": "region"}},
      "encoding": {"x": "name", "y": "id"},
      "pos": {"x": 1, "y": 2, "w": 8, "h": 5}
    }
  ]
}
```

### 4.9 Tabs (two tabs, one widget each + a filter on the first tab)

```json
{
  "version": 1,
  "title": "Tabbed overview",
  "layout": {"cols": 12, "row_height": 60},
  "variables": [
    {"name": "region", "type": "select", "default": null}
  ],
  "tabs": [
    {"id": "t_overview", "label": "Overview"},
    {"id": "t_detail", "label": "Detail"}
  ],
  "widgets": [
    {
      "id": "f1",
      "type": "filter",
      "tab_id": "t_overview",
      "subtype": "select",
      "target_var": "region",
      "options_query_id": "demo_all",
      "props": {"label": "Region"},
      "pos": {"x": 1, "y": 1, "w": 3, "h": 1}
    },
    {
      "id": "k1",
      "type": "kpi",
      "tab_id": "t_overview",
      "query_id": "demo_by_region",
      "params": {"region": {"ref": "region"}},
      "encoding": {"value": "id"},
      "props": {"label": "Rows in region"},
      "pos": {"x": 1, "y": 2, "w": 3, "h": 2}
    },
    {
      "id": "tb1",
      "type": "table",
      "tab_id": "t_detail",
      "query_id": "demo_all",
      "props": {"limit": 100},
      "pos": {"x": 1, "y": 1, "w": 10, "h": 6}
    }
  ]
}
```

### 4.10 Drawer (shared filters drawer + a chart on the grid)

```json
{
  "version": 1,
  "title": "Board with filters drawer",
  "layout": {"cols": 12, "row_height": 60},
  "variables": [
    {"name": "region", "type": "select", "default": null}
  ],
  "widgets": [
    {
      "id": "f1",
      "type": "filter",
      "drawer": true,
      "drawer_group": "filters",
      "order": 0,
      "subtype": "select",
      "target_var": "region",
      "options_query_id": "demo_all",
      "props": {"label": "Region"},
      "pos": {"x": 1, "y": 1, "w": 3, "h": 1}
    },
    {
      "id": "c1",
      "type": "chart",
      "chart_type": "bar",
      "query_id": "demo_by_region",
      "params": {"region": {"ref": "region"}},
      "encoding": {"x": "name", "y": "id"},
      "pos": {"x": 1, "y": 1, "w": 8, "h": 5}
    }
  ]
}
```
