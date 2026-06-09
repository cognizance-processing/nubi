# Building dashboards

A Nubi dashboard is a grid of **widgets** — KPIs, charts, tables, filters, and more — that read from your registered queries and re-query when filters change. You build dashboards in the visual **Dashboard Editor**: drag widgets onto a grid, bind each to a query, map columns to encodings, wire up cross-filtering, and save. The same dashboard can also be authored by the AI assistant or edited as raw YAML/JSON in the **Code panel**.

Under the hood every dashboard is a single JSON document — a `DashboardSpec`. The editor, the AI chat panel, the Code panel, and the embed pipeline all share it as the source of truth, so anything you build by clicking can be exported as code, and anything the AI writes can be edited by hand.

![Drag widgets onto a live grid canvas, configure encodings, and preview instantly](illustration:DashboardCanvas)

---

## The dashboards page

Open **Dashboards** from the sidebar to see all boards in the active project.

- **New dashboard** (top-right) opens a blank editor.
- **Search** filters boards by name; **Sort** toggles between *Recent* and *Name*.
- Each card shows **Open** (view the live dashboard at `/d/:id`) and **Edit** (open it in the editor). The three-dot menu adds **Duplicate** and **Delete**.
- The empty state offers **New dashboard** and **Ask AI to build one**, which opens the chat panel.

If you have read-only access to the organisation, create/edit/delete actions are hidden and a **Read-only** badge is shown — viewing still works.

---

## The editor at a glance

![The dashboard editor — board canvas on the left, Add widget panel on the right, toolbar in the top bar](/docs/screenshots/dashboard-editor.png)

The editor is a single full-height workspace. Its toolbar lives in the app's top bar:

- **Title field** — the dashboard name (also the saved board name).
- **Undo / Redo** — full edit history (`⌘Z` / `Ctrl+Z`, `⇧⌘Z` / `Ctrl+Y`).
- **Device switcher** — Desktop / Tablet / Mobile (see [Responsive layout](#responsive-layout)).
- **Zoom controls** — zoom out / *Fit* / zoom in, plus **Reset view**. Pinch or `Ctrl`/`⌘`+scroll on the canvas, and one-finger drag to pan.
- **Panel toggles** — open one of four right-hand panels: **Add** (widget palette), **Configure** (selected widget), **Layout** (dashboard-level settings), **Chat** (AI assistant).
- **Preview / Edit** — flip between the editable canvas and a live render in the current device frame.
- **Code** — open the spec as YAML or JSON (see [Code panel](#code-panel)).
- **Export / Share** — PNG, PDF, CSV, and an embed link.
- **Save / Create** — persists the board. An **Unsaved** badge appears while you have pending changes.

On phones and small tablets the toolbar cluster collapses behind a hamburger that opens a slide-out menu, and panels open as a bottom sheet.

---

## Adding widgets

1. Open the **Add** panel (the `+` toggle in the toolbar).
2. Click a widget type. Nubi drops it onto the first free spot on the grid and jumps you to its **Configure** panel.

The palette offers eight widget types:

| Widget | What it shows |
|--------|---------------|
| **KPI** | A single big formatted number from the first row of your query. |
| **Metric** | A stat tile: value + delta vs a comparison column + optional sparkline. |
| **Table** | A paginated data grid with column selection, formatting, and conditional rules. |
| **Pivot** | A rows × columns × measure matrix with a chosen aggregation. |
| **Chart** | One of 9 chart types (see [Chart types](#chart-types)). |
| **Filter** | A select / multiselect / date-range / text control that drives a variable. |
| **Text** | A Markdown content block. |
| **Section** | A section header and optional divider for grouping widgets. |

When the canvas is empty, quick **+ KPI / + TABLE / + CHART / + TEXT** buttons appear as shortcuts.

**Selecting, duplicating, deleting.** Click a widget to select it (the **Configure** panel follows). Hovering or selecting reveals a small toolbar in the widget's top-right corner with **duplicate** (`⌘D`) and **delete** (`Delete` / `Backspace`). Drag the top grip handle to move. `Esc` deselects.

---

## Binding a query

Data widgets (KPI, Metric, Table, Pivot, Chart) read from a **registered query** identified by its `query_id`.

1. Select the widget and open **Configure**.
2. Under **Query**, pick a `query_id` from the dropdown, or choose **Custom…** to type any id.
3. Once set, Nubi introspects the query's columns so the encoding dropdowns populate automatically.

The demo project ships with `demo_all`, `demo_active`, `demo_points_10k`, `demo_points_100k`, and `demo_points_500k` so you can prototype before wiring real queries. See [Queries & Parameters](/docs/queries-and-params) for registering your own.

---

## Widget encodings and props

Each widget type exposes only the column mappings it needs.

### KPI

Pick a **Value column**. Choose a **Format**: `number`, `integer`, `percent`, or `currency`. Add an optional **Label**.

### Metric

Like KPI, plus an optional **Comparison column** (renders a delta formatted as percent or absolute) and a **Sparkline column** for a mini trend line.

### Chart

Pick the **Chart type** from the icon grid (see [Chart types](#chart-types) below), then map columns:

| Chart family | Required encoding | Optional |
|---|---|---|
| Cartesian (bar/line/hbar/scatter/area) | **X column**, one or more **Series (Y)** | **Group / color column** |
| Pie / donut | **Category (x) column**, **Value (y) column** | — |
| Heatmap | **X column**, **Y column** | **Value column** (heat intensity) |
| Gauge | **Value column** | `props.min`, `props.max` |

For cartesian charts you can add multiple series (combo/dual-axis): each series picks a column, an optional render type (`bar`/`line`/`area`/`scatter`), and a left/right axis assignment. Toggle **Stack series** to stack them.

### Table

Set a **Row limit** (default 50) and toggle **Visible columns** (none selected → all shown). Expand **Column formats** to apply number/currency/percent/date formatting per column. Use **Conditional formatting** to color cells or rows by a rule (operator, value, cell-vs-row scope, background/text color, bold).

### Pivot

Pick **Rows (dimension)**, **Columns (dimension)**, an optional **Value (measure)**, and an **Aggregation** (`sum`, `avg`, `count`, `min`, `max`). With no value column, cells show the row count.

### Filter

See [Cross-filtering](#cross-filtering).

### Text

Write **Markdown content** directly in the Configure panel. Supports standard Markdown: headings, bold, italic, lists, links, inline code.

### Section

Set a **Title**, optional **Subtitle**, alignment (`left`/`center`/`right`), and a divider toggle. Use sections to visually group related widgets on the canvas.

---

## Chart types

All nine chart types are rendered by the browser via Apache ECharts. Each reads from Arrow columns streamed from the query engine.

| Type | Key encoding | Best for |
|------|-------------|----------|
| **bar** | x (category), y (value), optional color (group) | Comparing discrete categories |
| **hbar** | x (category → y-axis), y (value → x-axis), optional color | Long category labels; ranking lists |
| **line** | x (category or time), y (value), optional color | Trends over time; continuous data |
| **area** | Same as line | Cumulative totals; stacked proportions |
| **scatter** | x (numeric), y (numeric), optional color (category or numeric) | Correlation; outlier detection |
| **pie** | x (category/name), y (value) | Part-to-whole with few slices (≤ 8) |
| **donut** | Same as pie | Part-to-whole; center space for a KPI label |
| **heatmap** | x (category), y (category), value (heat intensity) | Two-dimensional density; calendar views |
| **gauge** | value (single number from first row) | Progress toward a target; single KPI with range |

**Scatter with color.** A categorical color column splits data into per-series groups. A numeric color column renders a continuous `visualMap` gradient (blue → red). Large datasets (> 5 000 rows) switch to `large` mode with `lttb` sampling automatically.

**Combo charts.** Combine bar, line, area, and scatter series on a single cartesian chart by using the multi-series encoding:

```yaml
encoding:
  x: month
  y:
    - { col: revenue, type: bar }
    - { col: profit,  type: line, axis: right }
```

Or equivalently via `props.series`. Mark a series `axis: right` to add a second y-axis.

**Stacking.** Set `props.stack: true` (or a string group id) to stack bar, line, or area series.

---

## Cross-filtering

Cross-filtering lets one widget change what the others show, via **dashboard variables**: filter widgets (and chart drilldowns) *write* variables; data widgets *read* them through parameter bindings and re-query.

### 1. Declare a variable

Open the **Layout** panel → **Variables** → **+ Add**. Give the variable a **name**, a **type** (`text`, `number`, `date`, `daterange`, `select`, `multiselect`), and an optional **default**. Toggle **Bind to URL** to make the value sync to the `/d/:id` query string (shareable views).

### 2. Add a filter widget

Add a **Filter**, then in **Configure** set:

- **Label** — what the user sees.
- **Subtype** — `select`, `multiselect`, `daterange`, or `text`.
- **Target variable** — the variable this filter writes to (e.g. `region`).
- **Options query ID** (select/multiselect only) — a query supplying the option values.

### 3. Bind data widgets to the variable

On any data widget, open **Parameters** → **+ Add**. Name the param to match the `{{named}}` parameter in your query's SQL, then set its source to a **Variable** (re-queries on change) or a fixed **Literal** value.

```json
"params": {
  "region":     { "ref": "region" },
  "date_range": { "ref": "date_range" }
}
```

A binding to an undeclared variable is flagged as an error.

### Chart drilldown (click-to-filter)

Select a chart, open **Drilldown / cross-filter**, and enable **Click-to-filter**. Set a **Target variable**: clicking a data point writes its category (or a chosen **Value field**) to that variable, driving every widget bound to it.

---

## Layout

Widgets sit on a CSS grid. Each widget has a position and size in grid cells (`x`, `y`, `w`, `h`).

- **Move** — drag a widget's top grip handle.
- **Resize** — drag any of the eight edge/corner handles.
- **Nudge** — with a widget selected, arrow keys move it one cell; `Shift`+arrow resizes it one cell.
- **Precise values** — the **Layout & size** section of **Configure** exposes numeric **X / Y / W / H** fields plus `min`/`max` width/height constraints and a **Static (pin in place)** toggle.

### Dashboard-level grid settings

Open the **Layout** panel for board-wide settings:

- **Background** — none, transparent, solid color, gradient, image URL, or raw CSS.
- **Grid** — per-device **column counts** (Desktop / Tablet / Mobile), **row height**, and **gap** (px).
- **Advanced** — **Compaction mode** (*Free place* / *Vertical* / *Horizontal* / *None*), **Dense packing**, **Container padding** (X/Y), **Breakpoint width thresholds**, and a **Max content width** cap.

### Responsive layout

The device switcher lets you tailor the layout per breakpoint:

- **Desktop** is the canonical layout. Tablet and Mobile **inherit** it until you change something.
- Switch to **Tablet** or **Mobile** and move/resize a widget to create a **custom layout** for that size. A badge shows **Inherits desktop** vs **Custom layout**, with a **Reset to desktop** button to discard overrides. Edits at a non-desktop breakpoint affect only that breakpoint.
- **Mobile** edits as a touch-friendly drag-to-reorder stack with a height stepper (▲/▼) instead of tiny resize handles.
- Per-widget **Visibility** toggles (in **Layout & size**) can hide a widget on specific breakpoints.
- Use the width preset chips (390 / 412 / 768 / 834 / 1024 px) or the numeric field to preview at a specific width.

### Widget appearance

Each widget's **Appearance** section sets a **Card background**, **Border**, **Radius**, and **Padding**. The **Custom HTML** section replaces the widget body with your own sanitized HTML and live-data tokens:

```
{{value}} · {{col:NAME}} · {{row.0.NAME}} · {{prop:NAME}}
```

All custom HTML is sanitized (DOMPurify) and every interpolated value is HTML-escaped before render.

---

## Shareable views — route params

URL-bound variables sync to and from the `/d/:id` query string. When a filter changes a variable, the new value is written back to the URL (shallow replace, no extra history entry), so the exact filtered view is shareable and refresh-safe:

```
/d/abc123?region=US-West&year=2024
```

Precedence, highest to lowest: **embed-token-locked params** → **URL params** → **`spec.variables` defaults**.

---

## Saving and publishing

- **Save / Create** persists the board (creates on first save, updates thereafter). The button is disabled while saving; an **Unsaved** badge plus a leave-the-page guard protect pending changes.
- **Open** — a saved board is immediately live at `/d/:id`. Share that URL, or use **Export / Share** for an embed snippet.
- **Export / Share** — capture the rendered dashboard as **PNG** or **PDF**, export per-widget data as **CSV**, or generate an embed link. See [Embedding](/docs/embedding) for token minting and per-viewer RLS.

---

## Code panel

Click **Code** in the toolbar to open a Monaco-powered slide-over showing the full `DashboardSpec` as YAML or JSON. The slide-over opens alongside the canvas so you can see both at once.

**View mode** (default) — read-only display of the current spec. Use **Download** to save a `.yaml` or `.json` file, or **Copy** to paste into another tool.

**Edit mode** — live editing with:

- **Syntax highlighting** (YAML or JSON — toggle with the format switcher).
- **Parse error markers** — red squiggles and line numbers for malformed YAML/JSON.
- **Spec validation** — structural issues (missing `chart_type`, undeclared variable refs, etc.) appear in a problems bar below the editor. Invalid specs cannot be applied.

The footer buttons in edit mode:

| Button | What it does |
|--------|-------------|
| **File…** | Load a `.yaml` or `.json` spec file into the edit buffer. |
| **Use current** | Reset the draft to the current canvas state. |
| **Apply to editor** | Validate and push the spec to the canvas (does not persist — use the main Save button). |
| **Save to server** / **Create on server** | Validate client-side, then upsert directly to the backend (server re-validates). |

Press `Escape` to close the panel.

---

## The DashboardSpec

The spec is a single JSON/YAML document with these top-level fields:

| Field | Description |
|-------|-------------|
| `version` | Schema version. Currently `1`. |
| `title` | Dashboard title (also the board name). |
| `layout` | Grid config: `cols` / `cols_md` / `cols_sm`, `row_height`, `gap`, `compaction`, `dense`, padding, `breakpoints`, `max_width`. |
| `variables` | Dashboard variables (name, type, default, optional `url_bind`). |
| `widgets` | Ordered list of widgets. |
| `responsive` | Per-breakpoint (`md` / `sm`) position overrides keyed by widget id. |
| `background` | Dashboard background descriptor. |

Each widget carries: `id`, `type`, `query_id`, `encoding`, `props`, `pos`, and — depending on type — `chart_type`, `subtype`, `target_var`, `options_query_id`, `content`, `params`, plus optional `columnFormats`, `formattingRules`, `drilldown`, `style`, `html`, and `hidden`.

### Minimal spec example

```yaml
version: 1
title: Revenue Overview
layout:
  cols: 12
  row_height: 60
  gap: 12
variables:
  - name: region
    type: select
    default: EMEA
    url_bind: true
widgets:
  - id: w-total
    type: kpi
    query_id: revenue_total
    encoding:
      value: revenue
    props:
      label: Total Revenue
      format: currency
    params:
      region: { ref: region }
    pos: { x: 1, y: 1, w: 4, h: 2 }

  - id: w-trend
    type: chart
    chart_type: line
    query_id: revenue_by_month
    encoding:
      x: month
      y: revenue
      color: segment
    props:
      label: Revenue by Month
    params:
      region: { ref: region }
    pos: { x: 5, y: 1, w: 8, h: 4 }

  - id: w-region
    type: filter
    subtype: select
    target_var: region
    options_query_id: regions_list
    props:
      label: Region
    pos: { x: 1, y: 3, w: 4, h: 2 }
```

The same spec applies whether you paste it into the Code panel, use the SDK, or let the AI generate it.

### Creating a dashboard via the SDK

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
          id: 'w1',
          type: 'kpi',
          query_id: 'revenue_total',
          encoding: { value: 'revenue' },
          props: { label: 'Total Revenue', format: 'currency' },
          params: { region: { ref: 'region' } },
          pos: { x: 1, y: 1, w: 4, h: 2 },
        },
      ],
    },
  },
})
```

---

## The `<nubi-*>` custom elements

When a dashboard is embedded, the spec compiles to a CSS-grid HTML fragment built from five declarative custom elements. You can also hand-write these on a host page:

```html
<nubi-kpi   query-id="revenue_total" value-col="revenue" label="Total Revenue" format="currency"></nubi-kpi>
<nubi-table query-id="events_summary" limit="50" columns="id,name,value"></nubi-table>
<nubi-chart query-id="scatter_demo" type="scatter" x="revenue" y="churn_rate" color="segment"></nubi-chart>
<nubi-filter subtype="select" target-var="region" options-query-id="regions_list" label="Region"></nubi-filter>
<nubi-text>## Section header\nExplanatory text.</nubi-text>
```

| Element | Key attributes |
|---------|----------------|
| `<nubi-kpi>` | `query-id` (req), `value-col`, `label`, `format` (`number` \| `integer` \| `percent` \| `currency`) |
| `<nubi-table>` | `query-id` (req), `limit`, `columns` (comma-separated) |
| `<nubi-chart>` | `query-id` (req), `type` (`scatter` \| `line` \| `bar`), `x`, `y`, `color` |
| `<nubi-filter>` | `subtype` (req), `target-var` (req), `options-query-id`, `label` |
| `<nubi-text>` | Markdown as the element's text content |

When the spec compiles, richer in-editor chart types degrade to the embed subset: `area` → `line`; `pie`/`donut`/`heatmap`/`gauge`/`hbar` → `bar`. To register the elements on a plain page, load the widget bundle and call `registerNubiWidgets()`; the full board is also available as `<nubi-dashboard>`. All compiled and custom HTML passes through DOMPurify. See [Embedding](/docs/embedding) for token minting and per-viewer RLS.

The elements emit two events on the host page (both `bubble`, `composed: true`):

| Event | `detail` | Fired |
|-------|----------|-------|
| `nubi:widget-ready` | `{ rows, renderer }` | After a successful render |
| `nubi:widget-error` | `{ message }` | On a non-recoverable error |

---

## Ask AI to build one

You can build or refine a dashboard by describing it in plain language. Open the **Chat** panel in the editor (or **Ask AI to build one** from the dashboards page).

1. **Pick a model** (remembered per session) and type a request — for example: *"Show revenue by region for Q1 with a KPI for total and a bar chart."*
2. The assistant streams its reply. It can inspect your data and call tools (each call shows as an expandable block). When it proposes a `DashboardSpec`, the editor **applies it automatically** and shows an **Applied to dashboard** confirmation.
3. The applied spec lands in the normal editor — refine by dragging, configuring, or chatting again. **Stop** halts an in-flight response; **History** reopens past conversations; **+ New** starts fresh.

Because the AI produces the same `DashboardSpec` you edit visually, there's no separate format to learn — generated dashboards are fully editable, and hand-built ones can be handed back to the assistant for changes.

> **Tip:** Always **Save** after applying an AI-generated dashboard. Applying a spec replaces the canvas but does not persist until you save.
