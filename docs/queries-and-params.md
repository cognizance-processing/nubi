# Queries & parameters

The **Queries** workspace is Nubi's SQL IDE. It's where you write and run SQL against any connector, turn a query into a reusable, parameterized **saved query**, and explore data in a notebook of scratch cells. Saved queries become the building blocks that dashboards, scheduled reports, and flows read from.

Open it from the sidebar (the **Queries** item, route `/queries`).

---

## The layout

On a desktop screen the page has two parts:

- **Query workspace** (main area) — a notebook-style editor for the query you've selected. The top **primary query** cell is your saved query of record; below it you can add session-only scratch cells.
- **Queries panel** (right sidebar, toggled from the toolbar) — search, a **New query** button, a **Blend sources** link, and the list of queries (drafts on top, the saved **Registry** below). A view toggle in the toolbar switches the main area between **Editor** and **Rollups** (auto pre-aggregations).

On smaller screens the Queries panel collapses into a query **dropdown** at the top of the workspace, and the editor and results stack vertically.

> If your role is read-only, you'll see a "Read-only access" notice and the **Save**, **New query**, and **Schedule** actions are hidden — you can still browse and run queries.

---

## Writing SQL

The primary query cell is bordered and badged **Primary query**. Type your SQL into the Monaco editor inside it.

The editor gives you:

- **Syntax highlighting** and a **dark/light theme** that follows the app.
- **Autocomplete** for SQL keywords plus the tables and columns it knows about for the selected connector. Press `.` or space, or just keep typing, to trigger suggestions.
- **Live validation** — a moment after you stop typing, parse errors appear as red squiggles with a message at the offending position. Validation is dialect-aware (see [Choosing a dialect](#choosing-a-dialect)).

### Starter templates

Click **Templates** in the editor toolbar to drop a ready-made starter query at your cursor. Options range from a basic `SELECT` to a `GROUP BY` aggregate, date-range filters, multi-select `IN (…)`, a date + filter combo, and a window-function running total. Templates that use parameters show how many `{{param}}` placeholders they introduce.

```sql
SELECT
  category,
  COUNT(*)   AS n,
  SUM(value) AS total
FROM demo
GROUP BY category
ORDER BY n DESC
```

Click **What's this?** in the Templates menu for a short explainer of templates and parameters.

### Generate SQL with AI

Don't want to write it by hand? Use the AI assist input to describe what you want in plain English (for example, "Show total sales by region for last 30 days") and Nubi generates SQL into the primary cell. You can also click **Open chat** to continue in the full AI chat.

---

## Choosing a connector

Each query runs against a **connector** (a data source you've set up). The **Connector** picker sits on the right of the primary cell's header.

1. Open the **Connector** dropdown.
2. Pick one of your connectors, or leave it on **Demo data (built-in)** to run against Nubi's bundled DuckDB demo dataset.

If you haven't added any connectors yet, the picker shows only **Demo data** with a "No connectors yet" hint. To add real data sources, see [Connectors](/docs/connectors).

### Choosing a dialect

When you pick a connector, Nubi auto-detects the matching SQL **dialect** (DuckDB, Postgres, MySQL, or BigQuery) and shows it in the editor toolbar with a "from <connector>" hint. Validation and autocomplete follow this dialect. You can override it from the **Dialect** dropdown if you're writing SQL in one dialect to run against another — Nubi transpiles where needed.

---

## Parameters

Parameters let one saved query serve many cases: a dashboard filter, a per-recipient report value, or a scheduled run input. You declare a parameter just by referencing it in your SQL with double curly braces:

```sql
SELECT *
FROM sales
WHERE region = {{region}}
  AND year   = {{year}}
ORDER BY date DESC
```

As soon as the editor sees `{{region}}` and `{{year}}`, a **Parameters** panel appears above the SQL with one row per parameter. Values are always **bound safely** by the connector — never string-concatenated into your SQL — so they're injection-safe.

For each parameter you can set:

- **Type** — `text`, `number`, `boolean`, `date`, `daterange`, `select`, or `multiselect`. The input field adapts (a date picker for `date`, a number field for `number`, and so on).
- **req** (required) — tick this if a value must be supplied. Required params are marked with a red `*`.
- **A value** — type the value to use when you run the query now. The placeholder shows the default if one is set.

Remove a parameter with the trash icon on its row. (If the `{{name}}` is still in your SQL it will reappear — delete the placeholder too.)

When you run the query, the values you typed are sent with the run; types are coerced (numbers become numbers, booleans become booleans). When the query is later used on a dashboard or in a flow, each parameter becomes a bindable input there.

---

## Running a query

- Click **Run** in the top toolbar (or press **⌘/Ctrl + Enter** in the editor) to run the primary cell.
- The button shows **Running…** while it streams, then the **Results** grid fills in below.

Above the grid you'll see the **row count**, the **elapsed time** in milliseconds, and a **cache badge** when the result came from a cache:

| Badge | Meaning |
|-------|---------|
| (none) | Fresh result computed for this run (a cache miss). |
| **HIT** | Served from the content-addressed cache — identical query, params, and access context as a prior run. |
| **LOCAL** | Computed in your browser's local DuckDB engine. |
| **SAMPLE** | A sampled/preview result rather than the full dataset. |

### Viewing results

Results render in an interactive **data table** with paging (50 rows per page) and a toolbar for sorting and column controls. Errors (a bad query, a missing required param) show inline in the results area with the message.

---

## Scratch cells — exploring like a notebook

Below the primary query you can add **scratch cells** to explore without touching your saved query. Use the **+ SQL** / **+ Python** buttons in the toolbar, the **SQL cell** / **Python cell** buttons in the footer, or hover the divider between cells and click the inline **+ SQL** / **+ Python** chips.

Each cell has its own Run button (and **⌘/Ctrl + Enter**), its own results grid, and controls to **collapse**, **move up/down**, and **remove** it. Drag the handle under the editor to resize it.

### Referencing other cells

Every cell's result is registered under a name — the primary query is **`cell_1`**, the first scratch cell is **`cell_2`**, and so on. Click the `#cell_N` badge in a cell header to copy its name. You can then read one cell's output from a later SQL cell:

```sql
-- in cell_3, using the result of cell_2
SELECT category, COUNT(*) AS n
FROM cell_2
GROUP BY category
```

This cross-cell data flow runs in your browser, so you can chain a warehouse query into local transforms and aggregations. **Python cells** run on an on-demand kernel for steps SQL can't express.

Click **Run all** in the toolbar to run the primary query and then every SQL scratch cell, top to bottom.

> Scratch cells are **session-only** — they are not saved. Only the primary query is persisted when you Save.

---

## Saving a query

Saving turns your primary cell into a reusable, named entry in the query **registry** for the current project.

1. Click **Save** in the top toolbar.
2. On a new query, a dialog asks for a **name**. Enter one and confirm.
3. The query moves out of **Drafts** and into the **Registry** list in the Queries panel, and the toolbar badge changes from **unsaved** to **registered**.

For an already-saved query the button reads **Update** and saves in place (no name prompt). What gets saved: the SQL, the parameter declarations, and the selected connector.

Drafts (unsaved, ad-hoc queries) are marked **draft** in the list and live only in your current session — create one any time with **New query**.

### View as code / import

The **SpecIO** control in the toolbar lets you view the query as a portable spec (SQL + params + connector) and apply an imported spec back into the editor — handy for copying a query between environments or version control. See [Git Sync](/docs/git-sync) for committing queries as code.

---

## Scheduling a query

Once a query is saved, click **Schedule** in the toolbar to run it automatically on a schedule (this creates a small one-task flow under the hood).

1. Give the scheduled run a **name**.
2. Choose **Interval** (e.g. every 6 hours) or **Cron** (a standard 5-field expression like `0 9 * * *` for every day at 09:00).
3. The dialog previews the schedule in plain language and captures the current parameter values for each run.
4. Confirm, then click **Open Automations** to manage it.

Scheduled queries and reports are managed on the **Automations** page. For exports and emailed reports, see [Exports & Scheduled Reports](/docs/exports-and-jobs).

---

## How saved queries feed dashboards and flows

A saved query is identified by an **id**, shown under its name in the Queries panel. That id is how the rest of Nubi reuses your work:

- **Dashboards** — chart, table, KPI, and pivot widgets bind to a saved query by its id. Any parameters you declared become dashboard variables and filter inputs that viewers can change. See [Dashboards](/docs/dashboards).
- **Flows** — a SQL cell can reference a saved query by its id and override its named parameters per run, chaining the result into downstream SQL or Python cells (and on into materialization or export). See [Flows](/docs/flows).
- **Scheduled reports & exports** — recipients can receive a query's output as CSV/PDF, with per-recipient locked parameter values. See [Exports & Scheduled Reports](/docs/exports-and-jobs).

Because they're reused by id, editing and re-saving a query updates everywhere it's used.

---

## Blending multiple sources

Need to combine two to four different sources into one dataset a dashboard can read? Click **Blend sources** in the Queries panel (route `/queries/blend`) to open the **Blend Builder**. You pick the source queries, write the SQL that merges them, declare any row-level-security key columns that must survive the merge, and optionally set a refresh schedule. Nubi materializes the combined result into a single, cheap-to-read dataset and gives you a query id to bind a widget to.

Blends materialize on a schedule instead of joining sources live on every view — the same "pay once, read cheap" pattern as [materialized SQL cells in Flows](/docs/flows#materialized-sql-cells).

---

## Rollups (auto pre-aggregations)

Switch the toolbar toggle from **Editor** to **Rollups** to see pre-aggregation candidates Nubi mined from your query log, ranked by how often they'd help. Build one in a click to speed up frequently-run aggregate queries. See [Pre-Aggregations](/docs/pre-aggregations) for how mining, building, and transparent routing work.

---

## Tips

- **⌘/Ctrl + Enter** runs the focused cell — faster than reaching for the Run button.
- Declare a parameter the instant you type `{{name}}`; set its type and default before saving so dashboards and schedules get sensible inputs.
- Use scratch cells to prototype joins and transforms, then fold the final SQL into the primary cell and Save.
- Watch the **cache badge** — a **HIT** means viewers downstream get the same near-instant, near-zero-cost result.
- Use the search box in the Queries panel to find a query by name or id once your registry grows.
