# Queries & parameters

The **Queries** workspace is Nubi's SQL IDE. Write SQL against any connector, turn a query into a reusable, parameterized **saved query**, and explore data in a notebook of scratch cells. Saved queries are the building blocks that dashboards, flows, and scheduled reports read from.

Open it from the sidebar (route `/queries`).

![Write SQL and see results instantly in the browser](illustration:QueryWorkspace)

---

## The layout

![The query workspace running a registered query — Monaco SQL editor, results grid, and the registry list on the right](/docs/screenshots/queries-editor.png)

On a desktop screen the page has two parts:

- **Query workspace** (main area) — a notebook-style editor. The top **primary query** cell is your query of record; below it you can add session-only scratch cells.
- **Queries panel** (right sidebar, toggled from the toolbar) — search, a **New query** button, a **Blend sources** link, and the list of queries (drafts on top, the saved **Registry** below).

Two view switchers live in the toolbar:

- An icon pair on the workspace itself flips between the **Notebook / editor view** and the **Code / Files view** — a VS Code-style projection of the query as `.sql` + `.meta.json` files (see [Code / Files view](#code-files-view)).
- A page-level **Editor / Rollups** toggle swaps the whole main area for the auto-rollups panel (pre-aggregation candidates mined from the query log — see [Rollups](#rollups-suggested-pre-aggregations)).

On smaller screens the Queries panel collapses into a **dropdown** at the top, and the editor and results stack vertically.

> If your role is read-only, you will see a "Read-only access" notice and the **Save**, **New query**, and **Schedule** actions are hidden — you can still browse and run queries.

---

## How the browser kernel works

When you click **Run**, your SQL travels like this:

1. The primary cell sends the query to the server (or, for cross-cell joins, executes entirely in DuckDB-WASM in your browser).
2. The server planner parses the SQL with sqlglot, injects any RLS predicates at the AST level, and checks the content-hashed edge cache.
3. Results stream back as **Apache Arrow IPC** over HTTP — no WebSocket, no polling. Arrow's columnar binary format means large result sets arrive and render fast.
4. The result is registered in the browser's **DuckDB-WASM** engine as `cell_1`, so later scratch cells can query it locally with zero server round-trips.

**Python cells** are different: they run on a metered, scale-to-zero server kernel (not in the browser). Use them for steps SQL cannot express.

---

## Writing SQL

The primary query cell is bordered and badged **Primary query**. Type your SQL into the Monaco editor inside it.

The editor gives you:

- **Syntax highlighting** and a dark/light theme that follows the app.
- **Autocomplete** for SQL keywords plus the tables and columns it knows about for the selected connector. Press `.` or keep typing to trigger suggestions.
- **Live validation** — a moment after you stop typing, parse errors appear as red squiggles. Validation is dialect-aware (see [Choosing a dialect](#choosing-a-dialect)).

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

### Generate SQL with AI

Use the **AI assist** input to describe what you want in plain English — for example, "Show total sales by region for last 30 days" — and Nubi generates SQL into the primary cell, grounded on the real table and column catalog for your selected connector. Click **Open chat** to continue in the full AI chat.

---

## Choosing a connector

Each query runs against a **connector** (a data source you have set up). The **Connector** picker sits on the right of the primary cell's header.

1. Open the **Connector** dropdown.
2. Pick one of your connectors, or leave it on **Demo data (built-in)** to run against Nubi's bundled DuckDB demo dataset.

If you have not added any connectors yet, the picker shows only **Demo data** with a "No connectors yet" hint. See [Connectors](/docs/connectors) to add real data sources.

### Choosing a dialect

When you pick a connector, Nubi auto-detects the SQL **dialect** for supported connector families (DuckDB, Postgres/Redshift, MySQL/MariaDB, and BigQuery) and shows it in the editor toolbar with a "from \<connector\>" hint. Other connector types (Snowflake, ClickHouse, SQL Server, etc.) default to DuckDB dialect — you can override it from the **Dialect** dropdown. Validation and autocomplete follow that dialect, and Nubi transpiles where needed.

---

## Parameters

Parameters let one saved query serve many cases: a dashboard filter, a per-recipient report value, or a scheduled run input. Declare a parameter by writing its name in double curly braces anywhere in your SQL:

```sql
SELECT *
FROM sales
WHERE region = {{region}}
  AND year   = {{year}}
ORDER BY date DESC
```

As soon as the editor sees `{{region}}` and `{{year}}`, a **Parameters** panel appears above the SQL with one row per parameter.

### Parameter types

Each parameter has a **type** that controls the input field and how the value is coerced before the query runs. The type dropdown in the Parameters panel offers:

| Type | Input rendered | Notes |
|------|---------------|-------|
| `text` | Text field | Default type. Value passed as a string. |
| `number` | Number field | Coerced to a numeric value at run time. |
| `boolean` | Text field | `"true"` / `"false"` → bound as a boolean. |
| `date` | Date picker | ISO date string. |
| `select` | Text field | One value from a fixed list — dashboard filter widgets can drive it from an options query. |
| `multiselect` | Text field | Multiple values; use the `inclause` filter in Jinja templates. |

> The `boolean` type is supported by the planner but the UI currently renders it as a text field — type `true` or `false`. Date *ranges* are handled at the dashboard level: a `daterange` dashboard variable drives a pair of `date` parameters (see the example below).

Set the type by clicking the type label in the Parameters panel row. You can also mark a parameter **required** (red `*`) — submitting the query without that parameter returns an HTTP 400 error instead of silently passing a null.

### Defaults and resolution order

Each parameter can have a **default value**. The placeholder in the input field shows the default when no value has been typed.

Resolution order at run time (first non-null value wins):

1. The value the **caller** supplies (dashboard filter, flow cell override, embed token locked value, or the value you typed in the workspace).
2. The **default** declared on the parameter.
3. `null` — allowed only for non-required parameters.

### Values are never string-concatenated

This is the most important safety property of Nubi's parameter system. When the planner resolves `{{region}}` it rewrites the SQL to a positional binding (`$1`, `$2`, …) and passes the value through the connector's parameterized query interface — asyncpg for Postgres, DuckDB's own binding for DuckDB. The raw value is never interpolated into the SQL string at any point. SQL injection via a parameter value is structurally impossible.

The same applies to RLS predicates: they are injected as AST-level `col = value` equality nodes by sqlglot, never by string concatenation.

### Parameter examples

A date-range filter:

```sql
SELECT order_id, total, created_at
FROM orders
WHERE created_at >= {{start_date}}
  AND created_at <  {{end_date}}
ORDER BY created_at DESC
```

Declare `start_date` and `end_date` as type `date`. A dashboard's date-range filter widget can then drive both values.

A select with a nullable "show all" default:

```sql
SELECT *
FROM demo
WHERE (name = {{region}} OR {{region}} IS NULL)
```

Declare `region` as type `text`, required `false`, default `null`. When `region` is null the `OR {{region}} IS NULL` arm returns all rows — no separate "all" value needed.

A multi-value `IN` filter (Jinja template syntax):

```sql
SELECT *
FROM products
WHERE category IN ({{categories | inclause}})
```

The `inclause` filter expands a multiselect value into `($1, $2, $3, …)` bound parameters. The values are still bound positionally — never concatenated.

---

## Running a query

- Click **Run** in the top toolbar, or press **Cmd/Ctrl + Enter** in the editor, to run the primary cell.
- The button shows **Running…** while the result streams, then the **Results** grid fills in below.

Above the grid you will see the **row count**, the **elapsed time** in milliseconds, and a **cache badge** when the result came from cache:

| Badge | Meaning |
|-------|---------|
| (none) | Fresh result — a cache miss. |
| **HIT** | Served from the content-addressed edge cache. Identical query, params, and RLS context as a prior run. |
| **LOCAL** | Computed in your browser's DuckDB-WASM engine (cross-cell join or local transform). |
| **SAMPLE** | A sampled/preview result, not the full dataset. |

### How a cache hit works

Every physical plan has a **cache key**: the SHA-256 digest of the canonical JSON of `{sql, params, rls_claims}`. Two runs hit the same cache entry when:

- the final rewritten SQL is identical (same source SQL, same connector dialect, same RLS predicates injected),
- the positional parameter values are identical, and
- the RLS `policies` claims in the token are identical.

Token expiry (`exp`), subject (`sub`), and other JWT metadata are excluded from the key, so a token refresh does not bust the cache.

### Viewing results

Results render in an interactive **data table** with paging (50 rows per page) and a toolbar for sorting and column controls. Query errors show inline in the results area with the message from the server.

---

## Scratch cells — exploring like a notebook

Below the primary query you can add **scratch cells** to explore without touching your saved query. Use the **+ SQL** / **+ Python** buttons in the toolbar, the footer buttons, or hover the divider between cells and click the inline chips.

Each cell has its own Run button (and **Cmd/Ctrl + Enter**), its own results grid, and controls to **collapse**, **move up/down**, and **remove** it. Drag the handle under the editor to resize it.

### Cross-cell data flow

Every cell's result is registered in the browser's DuckDB-WASM engine under a name. The primary query is **`cell_1`**, the first scratch cell is **`cell_2`**, and so on. Click the `#cell_N` badge in a cell header to copy the name. You can then read one cell's output from a later SQL cell:

```sql
-- cell_3: aggregate the result of cell_2 locally in the browser
SELECT category, COUNT(*) AS n
FROM cell_2
GROUP BY category
ORDER BY n DESC
```

This cross-cell join runs entirely in DuckDB-WASM in your browser — no server round-trip, no cold start. Chain a warehouse query into local transforms, pivots, and aggregations freely.

**Python cells** run on an on-demand server kernel for steps SQL cannot express (custom data-wrangling, ML scoring, etc.).

Click **Run all** in the toolbar to run the primary query then every SQL scratch cell, top to bottom.

> Scratch cells are **session-only** — they are not saved. Only the primary query is persisted when you Save.

---

## Saving a query

Saving turns your primary cell into a reusable, named entry in the query **registry** for the current project.

1. Click **Save** in the top toolbar.
2. On a new query, a dialog asks for a **name**. Enter one and confirm.
3. The query moves from **Drafts** into the **Registry** list in the Queries panel, and the toolbar badge changes from **unsaved** to the saved name.

For an already-saved query the button reads **Update** and saves in place (no name prompt). What gets saved: the SQL, the parameter declarations (name, type, default, required flag), and the selected connector.

Drafts (unsaved, ad-hoc queries) live only in your current session — create one any time with **New query**.

### Query id

Every saved query gets a stable **id** (shown in small monospace type under the query name in the Queries panel). This id is how dashboards and flows reference your query — rename it freely without breaking anything that binds to it by id.

### View as code / import

The **SpecIO** control in the toolbar lets you view the query as a portable spec (SQL + params + connector slug) and apply an imported spec back into the editor — handy for copying a query between environments or version control. See [Git Sync](/docs/git-sync).

### Code / Files view

The icon switcher at the left of the toolbar flips the workspace from the **Notebook / editor view** into a **Code / Files view** — a VS Code-style pane that projects the query as files matching the on-disk files-as-code shape:

- **`<slug>.sql`** — the raw SQL, fully editable. Edits write straight back to the query, exactly as if you had typed in the notebook editor.
- **`<slug>.meta.json`** — a read-only sidecar with the query's `id`, `name`, `datastore_id`, and `params` (plus `output_schema` when known). Params are derived from `{{placeholders}}` in the SQL, and the name/connector are edited via the toolbar, so the sidecar is always a faithful projection.

### Checkpoint & history

Once a query is saved, two toolbar buttons version it:

- **Checkpoint** — snapshot the current draft as a new version (with an optional message). If nothing changed since the last version, the existing one is reused.
- **History** — open the version list to view, restore, or promote past versions. Viewing a version shows its SQL and params read-only with a *Viewing vN (read-only)* banner — **Restore** copies it back into the draft, **Back to draft** leaves it untouched.

### Expose as metric

Below the primary cell, the collapsible **Expose as metric** panel can declare the saved query as a governed metric: a stable **slug**, the owning aggregation, the **dimensions** it can group by, and time **grains**. Enable it to make the query consumable as a metric by dashboards, watches, and AI; leave it off and nothing extra is saved with the query.

---

## Scheduling a query

Once a query is saved, click **Schedule** in the toolbar to run it automatically on a schedule (this creates a one-cell flow under the hood).

1. Give the scheduled run a **name**.
2. Choose **Interval** (e.g. every 6 hours) or **Cron** (a standard 5-field expression like `0 9 * * *` for every day at 09:00). The dialog shows the schedule in plain language as you type.
3. Current parameter values are captured for each run.
4. Confirm, then click **Open Automations** to manage it.

Scheduled queries are managed on the **Automations** page. For exports and emailed reports, see [Exports & Scheduled Reports](/docs/exports-and-jobs).

---

## How saved queries feed dashboards and flows

A saved query is referenced everywhere by its **id**:

- **Dashboards** — chart, table, KPI, and pivot widgets bind to a saved query by id. Any parameters you declared become dashboard variables and filter inputs viewers can change. See [Dashboards](/docs/dashboards).
- **Flows** — a SQL cell in a flow can reference a saved query by id and override its named parameters per run, chaining the result into downstream SQL or Python cells. See [Flows](/docs/flows).
- **Scheduled reports & exports** — recipients receive the query's output as CSV or PDF, with per-recipient locked parameter values. See [Exports & Scheduled Reports](/docs/exports-and-jobs).

Because the id is stable, editing and re-saving a query updates every dashboard and flow that uses it automatically.

---

## Blending multiple sources

Need to join two to four different connectors into one dataset? Click **Blend sources** in the Queries panel (route `/queries/blend`) to open the **Blend Builder**. Pick each source — a saved query or inline SQL against a connector — write the combine SQL that merges them, declare any row-level-security key columns that must survive the merge, and optionally set a refresh schedule (interval or cron). Nubi materializes the combined result into a single, cheap-to-read dataset and gives you a query id to bind widgets to.

Blends materialize on a schedule rather than joining live on every view — the same "pay once, read cheap" pattern as [materialized SQL cells in Flows](/docs/flows).

---

## Rollups (suggested pre-aggregations)

Switch the toolbar toggle from **Editor** to **Rollups** to see auto rollups — pre-aggregation candidates Nubi mined from your query log, ranked by how often they would help. Build one in a click to accelerate frequently-run aggregate queries. See [Pre-Aggregations](/docs/pre-aggregations) for how mining, building, and transparent routing work.

---

## Tips

- **Cmd/Ctrl + Enter** runs the focused cell — faster than reaching for Run.
- Declare a parameter the instant you type `{{name}}`; set its type and default before saving so dashboards and schedules get sensible inputs.
- Use scratch cells to prototype joins and transforms, then fold the final SQL into the primary cell and Save.
- Watch the **HIT** badge — it means viewers downstream get the same near-instant, near-zero-cost result without re-running the query.
- Use the search box in the Queries panel to find a query by name or id once your registry grows.
- A `select` or `multiselect` parameter can carry an `options_query_id` pointing at another saved query — dashboard filter widgets use it to populate their option lists.
