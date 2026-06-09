# Building dashboards

A Nubi dashboard is a grid of **widgets** — KPIs, charts, tables, filters, and more — that read from your registered queries and re-query when filters change. You build dashboards in the visual **Dashboard Editor**: drag widgets onto a grid, bind each to a query, map columns to encodings, wire up cross-filtering, and save. The same dashboard can also be authored by the AI assistant or exported and edited as code.

Under the hood every dashboard is a single JSON document — a `DashboardSpec`. The editor, the AI chat panel, the Code panel, and the embed pipeline all share it as the source of truth, so anything you build by clicking can be exported as code, and anything the AI writes can be edited by hand.

---

## The dashboards page

Open **Dashboards** from the sidebar to see all boards in the active project.

- **New dashboard** (top-right) opens a blank editor.
- **Search** filters boards by name; **Sort** toggles between *Recent* and *Name*.
- Each card has **Open** (view the live dashboard at `/d/:id`) and **Edit** (open it in the editor). The three-dot menu adds **Edit** and **Delete**.
- The empty state offers **New dashboard** and **Ask AI to build one**, which opens the chat panel.

If you have read-only access to the organisation, the page hides the create/edit/delete actions and shows a **Read-only** badge — viewing still works.

---

## The editor at a glance

The editor is a single full-height workspace. Its toolbar lives in the app's top bar:

- **Title field** — the dashboard name (also the saved board name).
- **Undo / Redo** — full edit history (`⌘Z` / `Ctrl+Z`, `⇧⌘Z` / `Ctrl+Y`).
- **Device switcher** — Desktop / Tablet / Mobile (see [Responsive layout](#responsive-layout)).
- **Zoom controls** — zoom out / *Fit* / zoom in, plus **Reset view**. You can also pinch or `Ctrl`/`⌘`+scroll on the canvas, and one-finger drag to pan.
- **Panel toggles** — open one of four right-hand panels: **Add** (widget palette), **Configure** (selected widget), **Layout** (dashboard-level settings), **Chat** (AI assistant).
- **Preview / Edit** — flip between the editable canvas and a live render in the current device frame.
- **Code** — open/import the dashboard as JSON or YAML.
- **Export / Share** — PNG, PDF, CSV, and an embed link.
- **Save / Create** — persists the board. An **Unsaved** badge appears while you have pending changes.

On phones and small tablets the toolbar cluster collapses behind a hamburger that opens a slide-out menu, and the panels open as a bottom sheet instead of a sidebar.

---

## Adding widgets

1. Open the **Add** panel (the `+` toggle in the toolbar).
2. Click a widget type. Nubi drops it onto the first free spot on the grid and jumps you to its **Configure** panel.

The palette offers eight widget types:

| Widget | What it shows |
|--------|---------------|
| **KPI** | A single big formatted number from the first row. |
| **Metric** | A stat tile: value + delta vs a comparison column + optional sparkline. |
| **Table** | A data grid with row limit, column selection, and formatting. |
| **Pivot** | A rows × columns × measure matrix with an aggregation. |
| **Chart** | Bar, line, hbar, scatter, area, pie, donut, heatmap, or gauge. |
| **Filter** | A select / multiselect / date-range / text control that drives a variable. |
| **Text** | A Markdown content block. |
| **Section** | A section header / divider for grouping. |

When the canvas is empty it also offers quick **+ KPI / + TABLE / + CHART / + TEXT** buttons.

**Selecting, duplicating, deleting.** Click a widget to select it (the **Configure** panel follows your selection). Hovering or selecting a widget reveals a toolbar in its top-right corner with **duplicate** (`⌘D`) and **delete** (`Delete` / `Backspace`) actions. Drag the top strip (the grip handle) to move a widget. `Esc` deselects.

---

## Binding a query

Data widgets (KPI, Metric, Table, Pivot, Chart) read from a **registered query** — a saved SQL query identified by its `query_id`.

1. Select the widget and open **Configure**.
2. Under **Query**, pick a `query_id` from the dropdown, or choose **Custom…** to type any id.
3. Once a query is set, Nubi **introspects its columns** by running it, so the column dropdowns in the rest of the panel populate automatically.

The demo project ships with `demo_all`, `demo_active`, `demo_points_10k`, `demo_points_100k`, and `demo_points_500k` so you can prototype before wiring real queries. See **Queries & Parameters** for registering your own.

### Mapping columns (encoding)

Each widget exposes only the column mappings it needs:

- **KPI / Metric** — pick a **Value column**. Metric also takes an optional **Comparison column** (renders a delta, formatted as percent or absolute) and a **Sparkline column**. Choose a value **Format**: `number`, `integer`, `percent`, or `currency`.
- **Chart** — pick the **Chart type** from the icon grid, then map columns:
  - *Cartesian charts* (bar/line/hbar/scatter/area): an **X column** and one or more **Series (Y)**. Add multiple series for combo/dual-axis charts — each series picks a column, a render type (bar/line/area/scatter), and a **left/right axis** (L/R). An optional **Group / color column** splits by category.
  - *Pie / donut*: a **Category column** and a single **Value column**.
  - *Heatmap*: **X**, **Y**, and **Value (heat)** columns.
  - *Gauge*: a single **Value column** with an optional **Max** range.
  - **Display** options include **Stack series**, gauge **Max**, and chart **Height**.
- **Table** — set a **Row limit** and toggle **Visible columns** (none selected → all shown). Expand **Column formats** to format individual columns (number/currency/percent/date), and **Conditional formatting** to color cells or rows by a rule (operator, value, cell-vs-row scope, background/text color, bold).
- **Pivot** — pick **Rows (dimension)**, **Columns (dimension)**, an optional **Value (measure)**, and an **Aggregation** (`sum`, `avg`, `count`, `min`, `max`). With no value column, cells show the row count.
- **Filter** — see [Cross-filtering](#cross-filtering).
- **Text** — write **Markdown content**.
- **Section** — set a **Title**, optional **Subtitle**, alignment, and a divider toggle.

The widget on the canvas re-renders live as you change the query or encoding, so you always see real data while configuring.

---

## Cross-filtering

Cross-filtering lets one widget change what the others show. It works through **dashboard variables**: filters (and chart drilldowns) *write* variables; data widgets *read* them through parameter bindings and re-query.

### 1. Declare a variable

Open the **Layout** panel → **Variables** → **+ Add**. Give the variable a **name**, a **type** (`text`, `number`, `date`, `daterange`, `select`, `multiselect`), and an optional **default**. Toggle **Bind to URL** to make it shareable (see [Route params](#shareable-views--route-params)).

### 2. Add a filter widget

Add a **Filter**, then in **Configure** set:

- **Label** — what the user sees.
- **Subtype** — `select`, `multiselect`, `daterange`, or `text`.
- **Target variable** — the variable name this filter writes to (e.g. `region`).
- **Options query ID** (select/multiselect only) — a query that supplies the option values.

### 3. Bind data widgets to the variable

On any data widget, open the **Parameters** section → **+ Add**. Name the param to match the `{{named}}` parameter in your query's SQL, then bind it either to a **Variable** (re-queries when the variable changes) or to a fixed **Literal** value. A binding to a variable that isn't declared is flagged as not found.

```json
"params": {
  "region":     { "ref": "region" },
  "date_range": { "ref": "date_range" }
}
```

Now, when the user changes the filter, every widget whose params reference that variable re-runs its query with the new value.

### Chart drilldown (click-to-filter)

A chart can itself be a filter source. Select a chart, open **Drilldown / cross-filter**, and enable **Click-to-filter**. Set a **Target variable**; clicking a data point writes the clicked point's category (or a chosen **Value field**) into that variable, driving every widget bound to it.

---

## Layout

Widgets sit on a CSS grid. Each widget has a position and size in grid cells (`x`, `y`, `w`, `h`).

- **Move** — drag a widget's top grip handle.
- **Resize** — drag any of the eight edge/corner handles (desktop and tablet).
- **Nudge** — with a widget selected, arrow keys move it one cell; `Shift`+arrow resizes it one cell.
- **Precise values** — the **Layout & size** section of the **Configure** panel exposes numeric **X / Y / W / H** fields plus **min/max width/height** constraints and a **Static (pin in place)** toggle.

### Dashboard-level grid settings

Open the **Layout** panel for board-wide settings:

- **Background** — none, transparent, solid color, gradient, image URL, or raw CSS.
- **Grid** — per-device **column counts** (Desktop / Tablet / Mobile), **row height**, and **gap** (px).
- **Advanced** —
  - **Compaction mode**: *Free place* (keep exactly where placed, the default), *Vertical* (pack upward), *Horizontal* (pack leftward), or *None*; plus **Dense packing** to back-fill gaps.
  - **Container padding** (X/Y), **Breakpoint width thresholds** (the pixel widths at which the viewer switches between desktop/tablet/mobile layouts), and a **Max content width** cap.

### Responsive layout

The device switcher lets you tailor the layout per breakpoint:

- **Desktop** is the canonical layout. Tablet and Mobile **inherit** it until you change something.
- Switch to **Tablet** or **Mobile** and move/resize a widget to start a **custom layout** for that size. A badge shows **Inherits desktop** vs **Custom layout**, with a **Reset to desktop** button to discard the overrides. Edits at a non-desktop breakpoint affect *only* that breakpoint.
- **Mobile** edits as a touch-friendly drag-to-reorder stack with a height stepper (▲/▼) instead of tiny resize handles.
- Per-widget **Visibility** toggles (in **Layout & size**) can hide a widget on specific breakpoints.
- Use the width preset chips (e.g. 390 / 412 / 768 / 834 / 1024 px) or the numeric field to preview tablet/mobile at a specific width.

### Widget appearance

Each widget's **Appearance** section sets a **Card background** (same options as the dashboard background), **Border**, **Radius**, and **Padding**. The **Custom HTML** section replaces the widget body entirely with your own sanitized HTML, using live-data tokens:

```
{{value}} · {{col:NAME}} · {{row.0.NAME}} · {{prop:NAME}}
```

All custom HTML is sanitized (DOMPurify) and every interpolated value is HTML-escaped before render, so a cell value can never inject markup.

---

## Saving and publishing

- **Save / Create** persists the board (a new board on first save, an update thereafter). The button is disabled while saving, and an **Unsaved** badge plus a leave-the-page guard protect pending changes.
- **Open / Publish** — a saved board is immediately live at `/d/:id`. Share that URL, or open **Export / Share** for an embed link and snippet.
- **Export / Share** — capture the rendered dashboard as **PNG** or **PDF**, export per-widget data as **CSV**, or generate an embed link. Embedding uses a short-lived host-signed JWT whose claims carry row-level-security policies; row filtering happens server-side, so the browser never sees unfiltered data. See **Embedding**.

### Shareable views — route params

URL-bound variables sync to and from the `/d/:id` query string. When a filter changes a variable, the new value is written back to the URL (a shallow replace, no extra history entry), so the exact filtered view is shareable and refresh-safe:

```
# Share a board pre-filtered to US-West for 2024:
/d/abc123?region=US-West&year=2024
```

Precedence, highest to lowest: **embed-token-locked params** (a token can pin values so filters can't override them) → **URL params** → **`spec.variables` defaults**.

---

## Editing as code

Open the **Code** panel (slide-over) to view or edit the current dashboard as a `DashboardSpec` in JSON or YAML, with Monaco syntax highlighting and live validation (parse errors appear as red squiggles; structural spec issues appear in a problems count below). Switch to **Edit** mode to modify the spec, then:

- **Apply to editor** — pushes the validated spec into the canvas without persisting; use the normal **Save** button to persist.
- **Save to server** — upserts the spec directly to the backend (re-validates server-side).
- **Upload file** — loads a `.json` or `.yaml` spec file into the edit buffer.
- **Download** — exports the current spec to a file.

### The DashboardSpec

```json
{
  "version":   1,
  "title":     "Revenue Overview",
  "layout":    { "cols": 12, "row_height": 60, "gap": 12 },
  "variables": [
    { "name": "region", "type": "select", "default": "EMEA", "url_bind": true }
  ],
  "widgets": [
    {
      "id":       "w1",
      "type":     "kpi",
      "query_id": "revenue_total",
      "encoding": { "value": "revenue" },
      "props":    { "label": "Total Revenue", "format": "currency" },
      "params":   { "region": { "ref": "region" } },
      "pos":      { "x": 1, "y": 1, "w": 4, "h": 2 }
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `version` | Schema version. Currently `1`. |
| `title` | Dashboard title (also the board name). |
| `layout` | Grid config: `cols` / `cols_md` / `cols_sm` (per-device columns), `row_height`, `gap`, `compaction`, `dense`, padding, `breakpoints`, `max_width`. |
| `variables` | Dashboard variables (name, type, default, optional `url_bind`). |
| `widgets` | Ordered list of widgets. |
| `responsive` | Per-breakpoint (`md` / `sm`) position overrides keyed by widget id. |
| `background` | Dashboard background descriptor. |

Each widget carries `id`, `type`, `query_id`, `encoding`, `props`, `pos`, and — depending on type — `chart_type`, `subtype`, `target_var`, `options_query_id`, `content`, `params`, plus optional `columnFormats`, `formattingRules`, `drilldown`, `style`, `html`, and `hidden`.

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
          id: 'w1', type: 'kpi', query_id: 'revenue_total',
          encoding: { value: 'revenue' },
          props: { label: 'Total Revenue', format: 'currency' },
          pos: { x: 1, y: 1, w: 4, h: 2 },
        },
      ],
    },
  },
})
```

---

## The declarative `<nubi-*>` elements

When a dashboard is embedded, the spec compiles to a CSS-grid HTML fragment built from a small set of declarative custom elements. You can also hand-write these on a host page. The compiled embed surface uses five elements:

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
| `<nubi-filter>` | `subtype` (req), `target-var` (req), `query-id`, `options-query-id`, `label` |
| `<nubi-text>` | Markdown as the element's text content |

When the spec compiles, richer in-editor chart types are normalised to the embed subset: `area` → `line` and `pie` → `bar`. To register the elements on a plain page, load the widget bundle and call `registerNubiWidgets()`; the whole board is also available as a single `<nubi-dashboard>` element. All compiled and custom HTML passes through DOMPurify — `<script>`, `<style>`, `on*` handlers, and `javascript:` URIs are stripped. See **Embedding** for token minting and per-viewer RLS.

The elements emit two events on the host page (both bubble, `composed: true`):

| Event | `detail` | Fired |
|-------|----------|-------|
| `nubi:widget-ready` | `{ rows, renderer }` | after a successful render |
| `nubi:widget-error` | `{ message }` | on a non-recoverable error |

---

## LLM-authored dashboards

You can build or edit a dashboard by describing it in plain language. Open the **Chat** panel in the editor (or **Ask AI to build one** from the dashboards page).

1. **Pick a model** (remembered per session) and type a request, e.g. *"Show revenue by region for Q1 2024 with a KPI for total and a bar chart."* Press **Enter** to send.
2. The assistant streams its reply. It can inspect your data and call tools (each tool call shows as an expandable block with its input and result). When it proposes a `DashboardSpec`, the editor **applies it automatically** and shows an **Applied to dashboard** confirmation.
3. The applied spec lands in the normal editor — refine it by dragging, configuring, or chatting again. **Stop** halts an in-flight response; **History** reopens past conversations; **+ New** starts fresh.

Because the AI produces the same `DashboardSpec` you edit visually, there's no separate format to learn — generated dashboards are fully editable, and hand-built ones can be handed back to the assistant for changes.

> **Tip:** Always **Save** after applying an AI-generated dashboard. Applying a spec replaces the canvas contents but does not persist until you save.
