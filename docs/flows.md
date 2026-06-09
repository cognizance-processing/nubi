# Flows — orchestration

Flows is Nubi's built-in workflow orchestrator — a place to build, run, and schedule multi-step data pipelines. A flow is a set of **cells** (SQL, Python, or notes) wired into a directed graph: each cell can depend on earlier cells, and Nubi runs them in dependency order, retrying failures and keeping durable run history.

You build a flow two ways — as a **notebook** (a top-to-bottom list of cells) or as a **canvas** (a visual DAG). They are the same flow underneath; flip between them at any time. This page walks through the whole thing: the two views, the cell model, advanced cell behaviour, the task inspector, environments, scheduling, running, and reading run results.

> Flow compute is metered. Cell previews and durable runs consume **compute units** drawn from your org's usage wallet (see [Billing and usage](/docs/billing-and-usage)). Designing in the notebook with small previews is cheap; large durable runs cost more.

---

## Opening the Flows workspace

Go to **Flows** (`/flows`) in the app. The workspace has three regions:

- A **flow list** — your org's saved flows plus any unsaved drafts. On desktop it lives in the collapsible right-hand sidebar; on mobile, tap the list icon in the top bar to open it as a sheet.
- A **top bar** with the flow name, the **Builder / Runs** switcher, the **Canvas / Notebook** view toggle, the **environment selector**, and the **Validate · Save · Run · Code** actions.
- The **main pane**, which shows either the builder (Builder tab) or run history and the live run view (Runs tab).

To start a new flow, click **New flow**. This seeds an empty draft and drops you into the Builder tab. To open an existing one, click it in the list.

The right-hand sidebar is shared and has three modes, switched by the toggle buttons at the right of the top bar:

- **Flows** (list icon) — the flow list.
- **Add task** (plus icon) — the cell palette.
- **Inspector** (sliders icon) — the task inspector for the selected cell.

Click the active toggle again to collapse the sidebar entirely; re-open it with any of the three buttons.

---

## Two views of one flow

The **Canvas / Notebook** toggle (in the Builder tab, top bar) switches how you see and edit the flow. Both edit the same underlying flow, so nothing is lost when you switch.

### Notebook view

The notebook renders your cells as an ordered, top-to-bottom list — like a Jupyter or Fabric notebook. This is the fastest way to author and iterate, because each cell has a **Run** button that runs an interactive preview and shows the result inline.

What you can do in the notebook:

- **Add cells** with the **+ SQL**, **+ Python**, and **+ Note** buttons in the toolbar, or with the dashed "add cell" bar that appears between cells (so you can insert anywhere).
- **Reorder** with the move-up / move-down arrows on each cell.
- **Delete** a cell with its delete button.
- **Run a cell** to preview its output. The result grid, row count, and elapsed time appear under the cell. Previews are row-capped (about 500 rows) and run quickly in-process.
- **Run all** to trigger a full durable run of the whole flow (see [Running a flow](#running-a-flow)).
- **Save** the notebook, and toggle the **Lineage** panel to see how cells feed each other.

Cells share data. A later cell can reference an earlier cell's result by its **key**: in SQL, `SELECT * FROM cell_key`; in Python, `inputs["cell_key"]["rows"]`. When you preview a cell, Nubi runs the upstream cells it needs first, so cross-cell references resolve.

### Canvas view

The canvas renders the same flow as a visual graph. Each cell is a node; arrows are dependencies (a "needs" edge). It's the clearest way to see and shape the structure of a branching or fan-out pipeline.

- **Add a cell** from the **Add task** sidebar (plus icon) or, on mobile, the **Add task** bottom sheet.
- **Connect cells** by dragging from one node's handle to another. The arrow becomes a dependency — the target cell now needs the source.
- **Move nodes** by dragging; positions are remembered.
- **Select a cell** by clicking it — this opens the **task inspector** in the right sidebar.
- Use the **minimap** and **zoom controls** in the corner to navigate large graphs. Pinch / scroll to zoom; drag the background to pan.

During a live run, the canvas turns read-only and colours each node by its current state (see [Watching a run](#watching-a-run)).

---

## The cell model

The palette offers exactly three cell types. Everything advanced is a *setting* on a SQL or Python cell, not a separate block.

| Cell | What it's for |
|------|---------------|
| **SQL query** | A `SELECT` — the everyday data block. Runs against a connector or against upstream cell outputs. |
| **Python** | Transform rows, call an API, or run an agent. The variables `inputs` and `params` are available; assign your output to `result`. |
| **Note** | Markdown prose for documentation. Never executes — it has no Run button. |

### SQL cells

A SQL cell holds one query. Write it in the Monaco editor, then click **Run** to preview rows inline. By default a SQL cell runs against in-memory DuckDB, where the outputs of upstream cells are available as tables named by their keys. You can also point it at a registered connector (see [Run against](#run-against-choosing-a-connector)).

```sql
-- references the upstream cell `orders` by key
SELECT region, sum(revenue) AS revenue
FROM orders
GROUP BY region
ORDER BY revenue DESC
```

### Python cells

A Python cell runs server-side. Two variables are injected for you:

- `inputs` — a dict of upstream cell results, keyed by cell key (e.g. `inputs["orders"]["rows"]`).
- `params` — the flow-level parameter values.

Assign your output to a variable named `result`:

```python
total = sum(row["revenue"] for row in inputs["orders"]["rows"])
result = {"total_revenue": total, "region": params.get("region", "all")}
```

In the inspector, the Python editor has an **Insert example…** picker with ready-made snippets to get you started.

### Note cells

A Note cell is plain Markdown — headings, lists, links. Click the body to edit, then blur or click **Done** to render it. Notes never run and never consume compute; use them to explain a pipeline to teammates.

---

## Advanced behaviour as cell config

The advanced patterns that used to be separate node types — materialize a table, fan out over a list, gate a step on a condition — are now **settings on a SQL or Python cell**. You configure them in the [task inspector](#the-task-inspector), under the **Cell behaviour** section. Each is summarised as a small badge on the cell in the notebook so the setting stays visible.

### Materialized (SQL cells)

By default a SQL cell is a **view** — it computes on demand and persists nothing. Set a materialization strategy to write its output to a real table so dashboards and downstream cells can read a cheap, single-source dataset instead of recomputing.

Choose a **Strategy**:

- **View** — no persistence (default).
- **Full** — overwrite the target table on every run.
- **Incremental** — process only rows newer than a stored watermark, then append or merge.

| Field | Applies to | Notes |
|-------|-----------|-------|
| **Target (logical path)** | full, incremental | Logical path like `orders/daily`. Written under `<env>/<target>` so dev and prod never clobber each other. |
| **Time column** | incremental | Only rows newer than the stored watermark on this column are processed (e.g. `updated_at`). |
| **Unique key** | incremental | Present ⇒ upsert/merge on these columns; blank ⇒ append. |
| **Lookback** | incremental | Reprocess a window before the watermark to catch late-arriving rows (e.g. `3 days`). |

### For each (fan-out)

Run a cell once per item in a list. Set **Items** to an expression or upstream reference that resolves to a list; the cell body runs once for each item.

| Field | Notes |
|-------|-------|
| **Items (list expression)** | A template expression or upstream ref, e.g. `{{ inputs.get_regions.rows }}`. Leave blank to disable fan-out. |
| **Item variable** | The name bound to each item (default `item`). Reference fields as `{{ item.<field> }}`. |
| **Max concurrency** | Maximum simultaneous item executions. `0` = unlimited. |

### Run when (gate)

Skip a cell unless a condition is true. Set **Condition** to a safe boolean expression over `inputs`, `params`, and `secrets`:

```
inputs.classify.label == 'high_value'
```

A blank condition always runs. A false condition at run time marks the cell **skipped** (and its downstream cells too).

---

## The task inspector

Click a cell on the canvas (or open the **Inspector** sidebar) to edit everything about it. The inspector is grouped into sections.

### Identity

- **Key** — the cell's unique slug (lowercase letters, digits, underscores; must start with a letter). This is the name other cells reference.
- **Kind** — the execution kind. The palette creates SQL (`query`), Python (`python`), and Note (`noop`) cells; the dropdown also exposes legacy/advanced kinds for specs that use them.
- **Needs** — the upstream cells this one depends on. This is **read-only** — change dependencies by connecting or disconnecting edges on the canvas.

### Config

The config fields depend on the kind:

- **SQL query** — toggle between **Query ID** (a registered query) and **Raw SQL**, plus the **Run against** connector picker.
- **Python** — the code editor and the **Insert example…** snippet picker.

### Run against (choosing a connector)

A SQL cell's **Run against (connector)** picker controls where it executes:

- **DuckDB · in-memory** (default) — query the outputs of upstream cells in memory. Use this for cross-cell joins and transforms.
- A **named connector** — run the SQL directly against one of your registered warehouses/connectors. Leave registered queries on DuckDB unless you need to override the target.

### Cell behaviour

For SQL and Python cells, this section hosts the advanced settings described above — **Materialization** (SQL only), **For each**, and **Run when**.

### Execution

Tune reliability and cost per cell:

| Field | Meaning |
|-------|---------|
| **Retries** | Extra attempts after the first failure. |
| **Backoff (s)** | Seconds to wait between retry attempts. |
| **Timeout (s)** | Per-attempt time limit. |
| **Cache TTL (s)** | When greater than `0`, Nubi caches the result by content and reuses it until the TTL expires. `0` = always re-run. |

---

## Referencing data between cells

Wherever cells reference each other or flow parameters, `{{ }}` template expressions resolve at run time:

| Expression | Resolves to |
|------------|-------------|
| `{{ params.region }}` | A flow parameter value. |
| `{{ inputs.orders.row_count }}` | A field from the `orders` cell's result. |
| `{{ secrets.NAME }}` | An org secret named `NAME`, resolved server-side and never sent to the browser. See [Secrets](/docs/secrets). |

In SQL, you can also reference an upstream cell as a table by its key (`FROM orders`); in Python, read it from `inputs["orders"]`.

---

## Environments

Every run targets an **environment**. The environment selector sits in the top bar (Builder tab) and shows the active env with a coloured dot.

- **prod** is the default and always available (emerald dot).
- **dev** is provided out of the box (sky dot).
- **Add your own** — open the selector and click **Add environment**, type a name (e.g. `staging`), and it's saved for next time. Custom envs can be removed from the same menu.

Why it matters: materialized targets are namespaced under the active env (`<env>/<target>`), so a run in `dev` never overwrites a `prod` table. Pick the environment **before** you click Run; it's stamped onto the run and shown on the run banner.

---

## Saving, validating, and the code panel

The Builder top bar has the core actions:

1. **Validate** — checks the flow for problems (cycles, missing dependencies, missing required config) without running it. Results appear in a banner: a green "valid" message or a list of issues.
2. **Save** — creates or updates the flow. You must save before you can run a durable flow or attach a schedule. Give the flow a name in the name field first.
3. **Code** — opens an editable Python view of the flow. Edit the generated scaffold and apply it to sync the builder, or just read it to understand the structure. The canvas, notebook, and code panel are all views of the same flow.

---

## Running a flow

There are two ways to execute, with different cost and durability.

### Preview a single cell (notebook)

In the notebook, click a cell's **Run** button. This runs a fast, row-capped, in-process preview and shows the result inline. Previews are the cheap, iterative path while you build — upstream cells run first so references resolve.

### Run all (durable)

To run the whole flow durably:

1. **Save** the flow first (durable runs require a saved flow).
2. Pick the target **environment** in the top bar.
3. Click **Run** (top bar) or **Run all** (notebook toolbar).
4. In the notebook, a **plan gate** dialog previews what will run and highlights the cell you changed most recently. Confirm to launch.

A durable run executes through the work pool with full retries, caching, timeouts, and persisted state. When it starts, the workspace switches to the **Runs** tab and shows the live run.

Durable runs consume more **compute units** than previews — they run every cell to completion, not a capped sample. Keep heavy materializations on a [schedule](#scheduling) rather than re-running them by hand.

---

## Watching a run

The Runs tab shows the live run view. It polls about every 1.5 seconds and stops automatically once the run finishes.

- A **banner** at the top shows the run id, overall state, the **environment** the run targeted, and a count summary (how many cells succeeded, failed, are running, etc.).
- The **canvas** is read-only and colours each node by its current state:

| State | Colour | Meaning |
|-------|--------|---------|
| Pending | Slate | Waiting on upstream cells. |
| Ready | Blue | Upstream done; waiting to start. |
| Running | Amber (spinning) | Executing now. |
| Retrying | Orange | Scheduled for another attempt after backoff. |
| Success | Green | Completed. |
| Failed | Red | Errored after exhausting retries. |
| Timed out | Red | Exceeded its configured timeout. |
| Upstream failed | Orange | An upstream cell failed, so this one didn't run. |
| Cancelled | Gray | The run was cancelled. |

### The run inspector

Click any node in the run view to open the **task result** panel (a side panel on desktop, a bottom sheet on mobile). It shows, for that cell:

- The state badge and timing — **started**, **finished**, **duration**, and the **attempt number** if it retried.
- The **error** message, if it failed.
- A collapsible **Logs** drawer with the cell's captured log lines.
- The **result** payload (rows, computed values) as formatted JSON.

### Run history and lineage

- The **Runs** tab (with no live run open) lists past runs of the flow, newest first, each with its state, trigger (manual or schedule), and timestamp. Click one to reopen its run view.
- The **Lineage** toggle in the notebook toolbar shows how cells feed one another across the whole flow — useful for understanding impact before a change.

---

## Scheduling

A saved flow can run on a schedule instead of by hand. Schedules use either cron or a fixed interval:

| Format | Syntax | Example |
|--------|--------|---------|
| Cron | 5-field cron | `0 7 * * 1-5` — weekdays at 07:00 UTC |
| Interval | `interval:Ns` | `interval:3600s` — every hour |

When a schedule is attached and the flow worker is enabled, Nubi materialises a new run automatically at each due tick (trigger shows as **schedule** in run history). Use scheduling for the work that should happen on a cadence — refreshing a materialized table, rebuilding a daily rollup — rather than re-running it manually. Remember that scheduled runs draw compute units like any other durable run.

---

## Tips

- **Build in the notebook, ship on the canvas.** Iterate with cheap cell previews, then switch to the canvas to see and refine the dependency structure.
- **Keep keys meaningful.** Cell keys are how other cells reference results (`FROM orders`, `inputs["orders"]`), and they show up throughout the run inspector.
- **Materialize once, read many.** Turn an expensive multi-source SQL cell into a materialized table on a schedule, then point dashboards at the cheap result instead of recomputing on every view.
- **Use Run when to skip work.** Gate expensive cells behind a condition so they only run when the data warrants it.
- **Mind the wallet.** Previews are cheap; full durable runs and large materializations cost more compute units. Schedule the heavy work and avoid re-running it by hand.

See also: [Secrets](/docs/secrets) for `{{ secrets.NAME }}`, [Connectors](/docs/connectors) for the "Run against" targets, [Dashboards](/docs/dashboards) for consuming materialized outputs, and [Billing and usage](/docs/billing-and-usage) for how compute units are metered.
