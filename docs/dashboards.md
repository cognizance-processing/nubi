# Building dashboards

A Nubi dashboard is a grid of **widgets** — KPIs, charts, tables, filters, and more — that read from your registered queries and re-query when filters change. You build dashboards in the visual **Dashboard Editor**: add widgets to a grid, bind each to a query, map columns to encodings, wire up cross-filtering, and save. The same dashboard can also be authored by the AI assistant or edited as raw YAML/JSON in the **Code panel**.

Under the hood every dashboard is a single JSON document — a `DashboardSpec`. The editor, the AI chat panel, the Code panel, and the embed pipeline all share it as the source of truth, so anything you build by clicking can be exported as code, and anything the AI writes can be edited by hand.

> **New to Nubi?** [Getting Started](/docs/getting-started) walks you from zero to your first live dashboard step by step. [UI Tour](/docs/ui-tour) covers every page in the app shell. If you have not registered any queries yet, read [Queries & Parameters](/docs/queries-and-params) first — every data widget reads from a saved query.

This guide is a step-by-step walkthrough for analysts:

1. [Build your first dashboard](#build-your-first-dashboard) — from an empty board to a published one.
2. [Every widget type](#widget-types) — a subsection per widget, what it's for, when to use it, and its key options.
3. [Chart types in depth](#chart-types-in-depth) — all 17 chart variants.
4. [Dashboard variables & filters](#dashboard-variables--filters) — interactivity, cross-filtering, and concrete use cases.
5. [Layout, theming & responsive](#layout-theming--responsive) — the practical knobs.

---

## The dashboards page

![Dashboards page — a responsive card grid of saved boards](/docs/screenshots/dashboards.webp)

Open **Dashboards** from the sidebar to see all boards in the active project.

- Boards render as a responsive **card grid** — one column on phones, two on small screens, three on desktop. Each card shows the board name plus a meta line: the widget count, or **HTML board** for legacy HTML boards.
- **New dashboard** (top-right) opens a blank editor.
- **Search** filters boards by name; **Sort** toggles between *Recent* and *Name*.
- Each card shows **Open** (view the live dashboard at `/d/:id`) and **Edit** (open it in the editor). The three-dot menu adds **Checkpoint** (snapshot the saved config as a new version), **History** (view, restore, or promote past versions), **Promote** (pin a version to another environment — promoting a board also moves its referenced queries), and **Delete** (with a confirm dialog).
- When the active environment is **protected**, a board with no version pinned there gets a **not in &lt;env&gt;** chip — promote a version to make it visible in that environment.
- The empty state offers **New dashboard** and **Ask AI to build one**, which opens the chat panel.

If you have read-only access to the organisation, create/edit/delete actions are hidden and a **Read-only** badge is shown — viewing still works.

---

## Build your first dashboard

This walkthrough takes an empty board to a published one in six steps. It assumes you have at least one saved query, or you can prototype with the built-in demo queries (`demo_all`, `demo_active`, `demo_points_10k`, `demo_points_100k`).

### 1. Open a blank board

From **Dashboards**, click **New dashboard**. The editor opens on an empty grid. Give the board a name in the **Title** field in the top toolbar — this is also the saved board name.

![Dashboard editor — the blank canvas with the toolbar](/docs/screenshots/dashboard-editor.webp)

### 2. Add your first widget

Open the **Add** panel (the **+** toggle in the toolbar) and click a widget type. Nubi drops it onto the first free spot on the grid and jumps you to its **Configure** panel.

![The Add panel — the widget palette with all eight widget types](/docs/screenshots/NEW-widget-palette.webp)

For a first board, add a **KPI** for a headline number and a **Chart** for a trend. When the canvas is empty, quick **+ KPI / + TABLE / + CHART / + TEXT** shortcut buttons also appear.

### 3. Bind it to a query

Data widgets (KPI, Metric, Table, Pivot, Chart) read from a **registered query** identified by its `query_id`.

1. Select the widget and open **Configure**.
2. Under **Query**, pick a `query_id` from the dropdown, or choose **Custom…** to type any id.
3. Nubi introspects the query's columns so the encoding dropdowns populate automatically.

New widgets default to a demo query so you see something immediately. See [Queries & Parameters](/docs/queries-and-params) for registering your own.

### 4. Map columns

Each widget type exposes only the column mappings it needs (its **encoding**). For a KPI it is one **Value column**; for a chart it is an **X** and a **Y**; for a table it is the **visible columns**. Pick the columns from the dropdowns — the widget preview updates live as you go. Full details per widget are in [Widget types](#widget-types).

### 5. Arrange the grid

- **Move** — drag a widget's top grip handle.
- **Resize** — drag any of the eight edge/corner handles.
- **Nudge** — with a widget selected, arrow keys move it one cell; `Shift`+arrow resizes it one cell.
- **Duplicate / delete** — hover or select a widget to reveal its top-right toolbar: **duplicate** (`⌘D`) and **delete** (`Delete` / `Backspace`). `Esc` deselects.

Use the **zoom** controls (or `Ctrl`/`⌘`+scroll) to fit the whole board on screen.

### 6. Save and view

1. Click **Save** (it reads **Create** the first time, then **Save**). An **Unsaved** badge appears whenever you have pending changes.
2. The board is now listed under **Dashboards**. Click **Open** on its card — or the **Preview** toggle in the editor — to see it live at `/d/:id`.

![A published dashboard rendered in the standalone live view](/docs/screenshots/dashboard-view.webp)

That `/d/:id` URL is what you share with viewers. To add interactivity — filters that re-query the whole board — continue to [Dashboard variables & filters](#dashboard-variables--filters).

---

## The editor at a glance

The editor is a single full-height workspace. Its toolbar lives in the app's top bar:

- **Title field** — the dashboard name (also the saved board name).
- **Undo / Redo** — full edit history (`⌘Z` / `Ctrl+Z`, `⇧⌘Z` / `Ctrl+Y`).
- **Device switcher** — Desktop / Tablet / Mobile (see [Responsive layout](#responsive-layout)).
- **Zoom controls** — zoom out / *Fit* / zoom in, plus **Reset view**. Pinch or `Ctrl`/`⌘`+scroll on the canvas, and one-finger drag to pan.
- **Panel toggles** — open one of five right-hand panels: **Add** (widget palette), **Configure** (selected widget), **Layout** (dashboard-level settings, grid & variables), **Tabs** (tab bar & per-tab style), **Chat** (AI assistant).
- **View switcher** — flip between the **Canvas / grid view** and a full-pane **Code / Files view** that edits the spec as a `dashboard.json` file (see [Code / Files view](#code--files-view)).
- **Preview / Edit** — flip between the editable canvas and a live render in the current device frame.
- **Code** — open the spec as YAML or JSON in a slide-over (see [Code panel](#code-panel)).
- **Export / Share** — PNG, PDF, CSV, and an embed link.
- **Save / Create** — persists the board.

On phones and small tablets the toolbar cluster collapses behind a hamburger that opens a slide-out menu, and panels open as a bottom sheet.

---

## Widget types

Open the **Add** panel to pick a widget type. Nubi offers eight:

| Widget | What it shows | Reads a query? |
|--------|---------------|----------------|
| **[KPI](#kpi)** | A single big formatted number from the first row of your query. | Yes |
| **[Metric](#metric)** | A stat tile: value + delta vs a comparison column + optional sparkline. | Yes |
| **[Table](#table)** | A paginated data grid with column selection, formatting, and conditional rules. | Yes |
| **[Pivot](#pivot)** | A rows × columns × measure matrix with a chosen aggregation. | Yes |
| **[Chart](#chart)** | One of 17 chart types (see [Chart types in depth](#chart-types-in-depth)). | Yes |
| **[Filter](#filter)** | A select / multiselect / date-range / text control that drives a variable. | Options only |
| **[Text](#text)** | A Markdown content block. | No |
| **[Section](#section)** | A section header and optional divider for grouping widgets. | No |

Every widget also has an **Appearance** section (card background, border, radius, padding), an optional **Custom HTML** override, and a **Layout & size** section — see [Widget appearance & custom HTML](#widget-appearance--custom-html).

### KPI

**What it's for:** a single headline number — total revenue, active users, tickets open.
**When to use it:** the one figure a stakeholder checks first. Pair several KPIs in a row across the top of a board.

**Config:** pick a **Value column** (Nubi reads it from the first row of the query). Choose a **Format** — `number`, `integer`, `percent`, or `currency` — and add an optional **Label**.

> *Use case:* a "Total revenue this month" tile formatted as currency, bound to a one-row aggregate query.

### Metric

**What it's for:** a KPI that also shows movement — value, a delta versus a comparison figure, and a mini trend line.
**When to use it:** when "up or down since last period" matters as much as the number itself.

**Config:** everything KPI has, plus a **Comparison column** (renders a delta, formatted as **percent** or **absolute**) and a **Sparkline column** for a mini trend line.

> *Use case:* "Revenue $128k, ▲ 12% vs last month" with a 12-point sparkline.

### Table

**What it's for:** the raw rows, formatted and scannable.
**When to use it:** detail views, top-N lists, anything a viewer might want to read row by row or export.

**Config:**

- **Row limit** — default 50 (1–10,000).
- **Visible columns** — toggle columns on/off. With none selected, all columns show.
- **Column formats** — per-column `number` / `currency` / `percent` / `date` formatting, with decimals, a currency code, or a date style (`short` / `medium` / `long` / `full`).
- **Conditional formatting** — color cells or whole rows by a rule. Each rule is an operator (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `between`, `contains`), a value (a second value for `between`), a **scope** (cell vs row), a background and text color, and an optional **bold** toggle.

> *Use case:* a top-50 orders table with negative margins highlighted red at the row level.

### Pivot

**What it's for:** a cross-tab — one dimension down the rows, another across the columns, a measure in the cells.
**When to use it:** "sales by region × month", "counts by status × owner" — quick two-dimensional summaries without writing a `PIVOT` query.

**Config:** pick **Rows (dimension)**, **Columns (dimension)**, an optional **Value (measure)**, and an **Aggregation** (`sum`, `avg`, `count`, `min`, `max`). With no value column, cells show the row count.

> *Use case:* region (rows) × month (columns) with `sum(revenue)` in the cells.

### Chart

**What it's for:** everything visual — trends, comparisons, distributions, flows, forecasts.
**When to use it:** see [Chart types in depth](#chart-types-in-depth) for a per-type "best for" guide.

**Config:** pick the **Chart type** from the icon grid, map the columns it needs (the encoding fields change per type), then open **Display** and **Axes** to tune the look. Full detail below.

![The chart-type picker grid in the Configure panel](/docs/screenshots/NEW-chart-type-picker.webp)

### Filter

**What it's for:** a control the viewer changes to re-scope the whole board.
**When to use it:** any time viewers need to slice — by region, date range, status, search term.

A filter widget writes to a **dashboard variable**; data widgets bound to that variable re-query. Full walkthrough in [Dashboard variables & filters](#dashboard-variables--filters).

**Config:**

- **Label** — what the viewer sees.
- **Subtype** — `select`, `multiselect`, `daterange`, or `text`.
- **Target variable** — the variable this filter writes to (e.g. `region`).
- **Options query ID** (select / multiselect only) — a query supplying the option values.
- **Placement** — **In grid**, **Above grid (bar)**, or **In drawer** (see [Where filters live](#where-filters-live)).

### Text

**What it's for:** headings, explanations, context, links.
**When to use it:** to annotate a board — a title block, a "what this measures" note, a link to the source doc.

**Config:** write **Markdown content** directly in the Configure panel. Supports standard Markdown: headings, bold, italic, lists, links, inline code. (For interpolating live data into free-form HTML, use a widget's [Custom HTML](#widget-appearance--custom-html) instead.)

### Section

**What it's for:** a visual grouping header on the canvas.
**When to use it:** to break a long board into labelled bands — "Revenue", "Engagement", "Ops".

**Config:** a **Title**, optional **Subtitle**, **Alignment** (`left` / `center` / `right`), and a **divider line** toggle.

---

## Chart types in depth

The chart widget renders 17 types via Apache ECharts, all reading from Arrow columns streamed from the query engine. Pick the type from the icon grid in **Configure → Chart type**; the encoding fields below it change to match.

| Type | Required columns | Optional | Best for |
|------|------------------|----------|----------|
| **Bar** | X (category), Y (value) | Group/color | Comparing discrete categories |
| **Line** | X, Y | Y2 (secondary axis), color | Trends over time; continuous data |
| **Area** | X, Y | color | Cumulative totals; stacked proportions |
| **Scatter** | X, Y | color | Correlation; outlier detection |
| **Bubble** | X, Y | Size, color | Three variables at once (size = magnitude) |
| **Pie** | Category, Value | — | Part-to-whole with few slices (≤ 8) |
| **Donut** | Category, Value | — | Part-to-whole; center space for a label |
| **Sankey** | Source, Target, Value | — | Flows between stages or nodes |
| **Funnel** | Stage/label, Value | — | Conversion funnels; drop-off by step |
| **Waterfall** | Category, Delta (change) | — | Running total of increments/decrements (a bridge) |
| **Treemap** | Name, Size (value) | Group | Hierarchical part-to-whole with many items |
| **Heatmap** | X, Y, Value | — | Two-dimensional density; calendar views |
| **Radar** | Indicator, Value | Series/group | Comparing several metrics across dimensions |
| **Box Plot** | Category, Values (raw — auto-boxed) | — | Distribution and spread; outliers by group |
| **Gauge** | Value | — | Progress toward a target; single KPI with a range |
| **Candlestick** | Date, Open, Close, Low, High | — | OHLC financial / price data |
| **Fan** | X (time), Forecast (midline) | Lower bound, Upper bound | Forecasts with a confidence band |

**Horizontal bars** are not a separate type — pick **Bar** and set **Orientation → Horizontal** in the Display section (funnels are orientable too).

### Display options (all chart types)

Open the **Display** section in Configure:

- **Stacking** (bar / line / area) — *None*, *Stacked*, or *100% stacked*.
- **Orientation** (bar / funnel) — vertical or horizontal.
- **Legend** — hidden, or positioned top / bottom / left / right.
- **Data labels** — show the value on each element.
- **Smooth curves** (line / area / fan).
- **Custom palette** — a comma-separated list of hex colors overrides the theme series colors.
- **Title** and **Height (px)**.
- **Max (gauge range)** — the top of a gauge's scale.

### Axes and reference lines (cartesian types)

Cartesian charts — bar, line, area, scatter, bubble, waterfall, boxplot, candlestick, fan — also expose an **Axes** section:

- **X axis** — label and value format (`number`, `currency`, `percent`, `SI` like 1k/1M, `date`).
- **Y axis** — label, **min**, **max**, **log scale**, and value format.
- **Secondary Y axis** (line / area) — label and format for the `Y2` series, giving you a dual-axis combo (e.g. revenue bars on the left, margin % on the right).
- **Reference lines** — add horizontal or vertical markers for targets or thresholds: a value, the axis (X/Y), an optional label, a dashed/solid style, and a color.

### Color and large datasets

A categorical **color** column splits data into per-series groups. Scatter/bubble automatically switch to a high-performance sampling mode on very large result sets so a 100k-point board still renders fast.

> **Editing tip:** the visual **Chart type** picker writes the extended types (sankey, funnel, waterfall, treemap, radar, boxplot, candlestick, fan, bubble) directly to the spec and they render everywhere. If you hand-edit a spec in the **Code panel**, note that the inline validator currently recognises only the nine *canonical* `chart_type` values (`line`, `bar`, `hbar`, `scatter`, `area`, `pie`, `donut`, `heatmap`, `gauge`) — configure the extended charts through the visual picker to avoid a spurious validation warning. See the [DashboardSpec reference](/docs/dashboard-spec-reference) for the exact enum.

---

## Dashboard variables & filters

**Dashboard variables** are the shared state that makes a board interactive. Filter widgets (and chart drilldowns) *write* variables; data widgets *read* them through parameter bindings and re-query when they change. Variables are **global to the board** — shared across every tab.

![The Layout panel's Variables editor with a region and a date-range variable](/docs/screenshots/NEW-dashboard-variables.webp)

### 1. Declare a variable

Open the **Layout** panel → **Variables** → **+ Add**. Give the variable a **name**, a **type**, and an optional **default**:

| Variable type | Drives |
|---------------|--------|
| `text` | free-text search / single string |
| `number` | a numeric threshold |
| `date` | a single date |
| `daterange` | a start + end pair (feeds two `date` query params) |
| `select` | one value from a list |
| `multiselect` | several values (expand with the `inclause` filter in your SQL) |

Toggle **Bind to URL** to sync the value to the `/d/:id` query string, so filtered views are shareable and refresh-safe (see [Shareable views](#shareable-views--route-params)).

### 2. Add a filter control

Add a **Filter** widget, then in **Configure** set its **Label**, **Subtype** (`select`, `multiselect`, `daterange`, `text`), and **Target variable** (the variable it writes to). For `select` / `multiselect`, set an **Options query ID** — a query whose rows supply the dropdown options.

### 3. Bind data widgets to the variable

On any data widget, open **Parameters → + Add**. Name the param to match the `{{named}}` parameter in your query's SQL, then set its source to a **Variable** (re-queries on change) or a fixed **Literal** value:

```json
"params": {
  "region":     { "ref": "region" },
  "date_range": { "ref": "date_range" }
}
```

A binding to an undeclared variable is flagged as an error. See [Queries & Parameters](/docs/queries-and-params) for declaring `{{named}}` params in SQL.

### Chart drilldown (click-to-filter)

Select a chart, open **Drilldown / cross-filter**, and enable **Click-to-filter**. Set a **Target variable**: clicking a data point writes its category (or a chosen **Value field**) to that variable, driving every widget bound to it. This turns any chart into a filter for the rest of the board.

### Where filters live

Every filter (and text) widget has a **Placement** control, so filters do not have to sit in the grid:

- **In grid** — a normal grid cell you drag and resize.
- **Above grid (bar)** — a compact control in a horizontal **filter bar** above the grid (below the tab bar), ordered left to right. This is the classic "filter strip across the top" layout.
- **In drawer** — lives in a slide-over **Filters** drawer, keeping the board clean until a viewer opens it. Drawer filters are global across tabs.

![A dashboard with a filter bar of controls above the grid](/docs/screenshots/NEW-filter-bar.webp)

### Use cases

**A region filter.** Declare `region` (type `select`). Add a `select` filter with **Target variable** `region` and an **Options query** returning distinct regions. Bind each data widget's `region` param to `{ref: region}`. Picking a region re-queries the whole board.

**A date range.** Declare `date_range` (type `daterange`). Add a `daterange` filter targeting it. In your SQL, use two `date` params (`{{start_date}}`, `{{end_date}}`); the range feeds both. Now every widget respects the selected window.

**A click-to-drill drilldown.** On a "revenue by region" bar chart, enable **Click-to-filter** with target `region`. Below it, place a "revenue by month" line chart whose `region` param reads `{ref: region}`. Clicking a bar drills the line chart into that region — no separate filter widget needed.

**Cascading filters.** Chain two filters so the second depends on the first: filter A writes `region`; filter B's **Options query** is parameterised by `region` (bind B's `region` param to `{ref: region}`) so its options narrow to the chosen region; a chart reads both. See example 4.8 in the [spec reference](/docs/dashboard-spec-reference).

---

## Layout, theming & responsive

### Moving and resizing

Widgets sit on a CSS grid; each has a position and size in grid cells (`x`, `y`, `w`, `h`).

- **Move** — drag a widget's top grip handle.
- **Resize** — drag any of the eight edge/corner handles.
- **Nudge** — arrow keys move a selected widget one cell; `Shift`+arrow resizes it.
- **Precise values** — the **Layout & size** section of **Configure** exposes numeric **X / Y / W / H** fields plus `min`/`max` width/height constraints and a **Static (pin in place)** toggle.

### Board grid settings

Open the **Layout** panel for board-wide settings:

- **Grid** — per-device **column counts** (Desktop / Tablet / Mobile), **row height**, and **gap** (px).
- **Advanced** — **Compaction mode** (*Free place* / *Vertical* / *Horizontal* / *None*), **Dense packing** (back-fill gaps), **Container padding** (X/Y), **Breakpoint width thresholds**, and a **Max content width** cap for the rendered board.

### Background and theming

The **Layout** panel's **Background** control sets the board backdrop: **none**, **transparent**, a **solid** color, a **gradient**, an **image URL**, or raw **CSS**. Light/dark theming follows the app automatically — charts, tables, and KPIs all re-theme with no per-widget work. All user-supplied colors and CSS flow through a sanitised style path.

### Responsive layout

The device switcher lets you tailor the layout per breakpoint:

- **Desktop** is the canonical layout. Tablet and Mobile **inherit** it until you change something.
- Switch to **Tablet** or **Mobile** and move/resize a widget to create a **custom layout** for that size. A badge shows **Inherits desktop** vs **Custom layout**, with a **Reset to desktop** button to discard overrides. Edits at a non-desktop breakpoint affect only that breakpoint.
- **Mobile** edits as a touch-friendly drag-to-reorder stack with a height stepper (▲/▼) instead of tiny resize handles.
- Per-widget **Visibility** toggles (in **Layout & size**) can hide a widget on specific breakpoints.
- Use the width preset chips (390 / 412 / 768 / 834 / 1024 px) or the numeric field to preview at a specific width.

### Widget appearance & custom HTML

Each widget's **Appearance** section sets a **Card background**, **Border**, **Radius**, and **Padding**. The **Custom HTML** section replaces the widget body with your own HTML and live-data tokens:

```
{{value}} · {{col:NAME}} · {{row.0.NAME}} · {{prop:NAME}}
```

All custom HTML is sanitised (DOMPurify) and every interpolated value is HTML-escaped before render.

---

## Tabs

A dashboard can be split into **tabs** — sections inside one board. Tabs are a *render partition, not a scope*: it stays **one board, one spec, one version history**. Add tabs to `spec.tabs`; an empty or absent `tabs` list means the dashboard behaves exactly as before (no tab bar).

- **Variables stay global.** Dashboard variables (and cross-filtering) are shared across every tab — switching tabs never resets a filter. Tabs partition *where widgets render*, not *what scope they read*.
- **Widgets bind to a tab via `widget.tab_id`.** A widget's `tab_id` must reference a tab declared in `spec.tabs` (a `tab_id` pointing at no declared tab, or a duplicate `tab.id`, is a hard validation error).
- **`tab_id: null` ⇒ first tab.** When `spec.tabs` is non-empty, a widget whose `tab_id` is `None` implicitly belongs to the **first** tab.
- **Drawer widgets stay global.** A widget with `drawer=True` (the filters drawer or a drilldown drawer) ignores `tab_id` — drawers render across all tabs.

Each tab has a stable, unique `id`, a human-readable `label`, and optional per-tab `style` overrides for the tab-bar tokens (all user-supplied colors/CSS flow through the sanitised style path — never interpolated raw).

---

## Scan vs slice params

A dashboard variable carries an optional `mode` of `'scan'` or `'slice'`. It classifies what changing that param does:

- **`scan`** (the default — `None` is treated as `scan`) — changing the param **re-queries server-side data**. A filter change re-reads from the source.
- **`slice`** — marks a param that **subsets rows already fetched** into the base result client-side (DuckDB-WASM); it is never sent to the server, so refining it never triggers a server round-trip.

`mode` is pure metadata for now: an absent or `None` value behaves exactly as `scan`.

---

## The live view — `/d/:id`

Every saved board renders as a full page at `/d/:id` — no editor chrome, just the dashboard. This is the URL you share with viewers.

![A dashboard rendered at its shareable /d/:id live view](/docs/screenshots/dashboard.webp)

- Members who can write see an **Edit in editor →** link above the board; viewers get a clean read-only render.
- Spec boards render through the same engine as the editor's Preview; older HTML boards still render via the legacy HTML path.
- `/d/sample` renders a built-in sample dashboard without a backend request — and if a board can't be loaded (404, no backend), the page shows a notice and falls back to that same sample.
- On a tabbed dashboard, the active tab syncs to a `_tab` URL parameter (shallow replace), so a link can land on a specific tab.

In short, there are three ways to look at a dashboard: the **editor** (add, configure, edit the spec), the editor's **Preview** toggle (a live render inside the editor, in the current device frame), and the **`/d/:id` live view** (the standalone page viewers use).

---

## Shareable views — route params

URL-bound variables sync to and from the `/d/:id` query string. When a filter changes a variable, the new value is written back to the URL (shallow replace, no extra history entry), so the exact filtered view is shareable and refresh-safe:

```
/d/abc123?region=US-West&year=2024
```

Precedence, highest to lowest: **embed-token-locked params** → **URL params** → **`spec.variables` defaults**. On tabbed dashboards the reserved `_tab` parameter (underscore-prefixed to avoid colliding with variable names) carries the active tab.

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

### Code / Files view

The toolbar's view switcher also offers a full-pane **Code / Files view** (the file icon next to the canvas icon) — a VS Code-style pane that replaces the canvas entirely with a `dashboard.json` file: the same `config.spec` the CLI writes to `dashboards/<slug>.json` on `nubi pull`, so the in-app view matches the on-disk files-as-code format.

Edits are parsed on every keystroke: **valid JSON applies to the spec immediately** (the same path the canvas and Code panel use), while invalid JSON surfaces an inline parse-error banner and leaves the spec untouched — a half-typed document never corrupts your dashboard. Use the main **Save** button to persist, as usual.

---

## The DashboardSpec

The spec is a single JSON/YAML document with these top-level fields:

| Field | Description |
|-------|-------------|
| `version` | Schema version. Currently `1`. |
| `title` | Dashboard title (also the board name). |
| `layout` | Grid config: `cols` / `cols_md` / `cols_sm`, `row_height`, `gap`, `compaction`, `dense`, padding, `breakpoints`, `max_width`. |
| `variables` | Dashboard variables (name, type, default, optional `url_bind`, optional `mode`). |
| `tabs` | Optional list of tabs (each `id` + `label` + optional `style`). Empty/absent ⇒ no tabs. See [Tabs](#tabs). |
| `widgets` | Ordered list of widgets. |

Each widget carries: `id`, `type`, `query_id`, `encoding`, `props`, `pos`, `tab_id`, and — depending on type — `chart_type`, `config`, `subtype`, `target_var`, `options_query_id`, `content`, `params`, `placement`, plus optional `columnFormats`, `formattingRules`, `drilldown`, `style`, `html`, and `hidden`. Chart display and axis settings live in `widget.config`. See the [DashboardSpec reference](/docs/dashboard-spec-reference) for the exact field types and enums.

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
    config:
      legend: { position: bottom }
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
      variables: [{ name: 'region', type: 'select', default: 'EMEA' }],
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

When a dashboard is embedded, the spec compiles to a CSS-grid HTML fragment built from declarative custom elements. You can also hand-write these on a host page:

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
| `<nubi-chart>` | `query-id` (req), `type`, `x`, `y`, `color` |
| `<nubi-filter>` | `subtype` (req), `target-var` (req), `options-query-id`, `label` |
| `<nubi-text>` | Markdown as the element's text content |

To register the elements on a plain page, load the widget bundle and call `registerNubiWidgets()`; the full board is also available as `<nubi-dashboard>`. All compiled and custom HTML passes through DOMPurify. See [Embedding](/docs/embedding) for token minting and per-viewer RLS.

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
