# Flows — orchestration

![Build, schedule, and monitor multi-step data pipelines in Nubi](illustration:FlowOrchestration)

Flows is Nubi's built-in workflow orchestrator. A flow is a set of **cells** — SQL queries, Python scripts, or Markdown notes — wired into a directed acyclic graph. Nubi runs them in dependency order, retrying failures, caching results, and keeping durable run history.

You work on a flow in **three views**: as a **notebook** (a top-to-bottom list of cells), as a **canvas** (a visual DAG), or as **code** (a file tree — `flow.py` plus one file per cell). They are three windows onto the same flow — flip between them at any time without losing anything.

> Flow compute is metered. Cell previews and durable runs consume **compute units** drawn from your org's usage wallet. Designing in the notebook with small previews is cheap; large durable runs and materializations cost more. See [Billing and usage](/docs/billing-and-usage).

---

## Opening the Flows workspace

Go to **Flows** (`/flows`) in the app. The workspace has three regions:

- A **flow list** — your org's saved flows and any unsaved drafts. On desktop it lives in the collapsible right-hand sidebar; on mobile, tap the list icon in the top bar to open it as a bottom sheet.
- A **top bar** with the **Builder / Runs** switcher, the three-way **Canvas / Notebook / Code** view switcher, the flow name, and the **Validate · Save · Checkpoint · History · Schedule · Run** actions (plus **Lineage** in notebook view).
- The **main pane**, which shows either the builder (Builder tab) or run history and the live run view (Runs tab).

The run **environment** is *not* selected here — it is global app state, chosen with the environment selector in the left sidebar (beneath the project selector). See [Environments](#environments).

To start a new flow, click **New flow**. This seeds an empty draft and drops you into the Builder tab. To open an existing one, click it in the list.

The right-hand sidebar (desktop) has three modes, switched by the toggle buttons at the far right of the top bar:

| Panel | Icon | Use |
|-------|------|-----|
| **Flows** | List | The flow list. |
| **Add task** | Plus | Cell palette — canvas view only. |
| **Inspector** | Sliders | Config for the selected cell — canvas view only. |

Click the active toggle again to collapse the sidebar entirely; re-open it with any of the three buttons. In the notebook and code views only the Flows panel toggle is shown — notebook cells are added with the **+ SQL / + Python / + Note** buttons in the top bar, and the code view has its own file explorer.

---

## Three views of one flow

A flow is canonically a single `FlowSpec` — its cells, edges, params, and settings. The **Canvas / Notebook / Code** switcher (Builder tab, top bar) projects that one spec onto three surfaces, each round-tripping losslessly back to the spec:

- **Notebook** — the spec's cells rendered as an ordered, top-to-bottom list. The fast authoring surface (the analyst iterating cell by cell).
- **Canvas** — the same cells and edges rendered as a visual DAG, with node positions persisted. The structure surface (the engineer shaping a branching pipeline).
- **Code** — the spec projected as an editable file tree: `flow.py` plus one file per cell. The files-as-code surface (the power user authoring the whole flow as files).

There is no "primary" view and no separate copy: editing in any view edits the same spec, and switching views reconstructs it exactly. (The same spec also serializes to a reviewable folder for git sync — see [Flows on disk](/docs/git-sync#flows-on-disk).)

### Notebook view

![Notebook view — Note, SQL, and Python cells in order, each with its own Run button](/docs/screenshots/flows-notebook.png)

The notebook renders cells as an ordered, top-to-bottom list. This is the fastest way to author and iterate: each cell has a **Run** button that runs a fast interactive preview and shows the result inline. The top bar grows three extra buttons — **+ SQL**, **+ Python**, and **+ Note** — and a **Lineage** toggle.

What you can do in notebook view:

- **Add cells** with the **+ SQL**, **+ Python**, or **+ Note** buttons in the top bar, or with the dashed "add cell" bar that appears between existing cells so you can insert anywhere.
- **Reorder** cells with the move-up / move-down arrows on each cell.
- **Delete** a cell with its delete button.
- **Run a cell** to preview its output. The result grid, row count, and elapsed time appear below the cell. Previews are capped at 500 rows and run without persisting a flow run. The backend re-executes the upstream cells in the dependency chain first, so cross-cell references always resolve.
- **Run all** to launch a full durable run of the whole flow (behind the [plan gate](#full-durable-run)).
- **Lineage** toggle to see how cells feed each other across the flow.

Cells share data. A downstream cell can reference an upstream cell's result by its **key**: in SQL write `SELECT * FROM cell_key`; in Python read `inputs["cell_key"]`.

### Canvas view

![Canvas view — each cell is a node, arrows are dependencies, with minimap and zoom controls](/docs/screenshots/flows-canvas.png)

The canvas renders the flow as a visual graph. Each cell is a node; arrows show dependencies. It is the clearest way to see and shape the structure of a branching or fan-out pipeline.

- **Add a cell** from the **Add task** sidebar (plus icon in the top bar).
- **Connect cells** by dragging from one node's handle to another — the arrow becomes a dependency.
- **Move nodes** by dragging; positions are remembered.
- **Select a cell** by clicking it — this opens the **Inspector** in the right sidebar for full configuration.
- Use the **minimap** and **zoom controls** in the corner to navigate large graphs.

During a live run, the canvas turns read-only and colours each node by its current state (see [Watching a run](#watching-a-run)).

### Inferred SQL dependencies

You usually do not need to draw edges into SQL cells at all. When a SQL cell's query mentions a sibling cell's key in a `FROM` or `JOIN` clause, the canvas draws a **dashed grey edge** from that cell automatically — the dependency is *inferred from the SQL itself*, SQLMesh-style.

Inferred edges are render-time only: they are re-derived from the SQL on every view switch and are never written into the cell's explicit `needs` list, so deleting the SQL reference removes the edge again. The backend runs its own canonical SQL parse (sqlglot) to order execution, so the engine honours the same dependencies the canvas shows.

### Code / Files view

![Code view — file explorer with flow.py and one file per cell, edited in Monaco](/docs/screenshots/flows-code.png)

The code view is a VS Code-style projection of the flow as an editable file tree:

```
flow.py                  # generated nubi.flows Python SDK source
cells/
  01_pull_orders.sql     # SQL cell        → its query
  02_readme.md           # Note cell       → its markdown
  03_enrich.py           # Python cell     → its code
  04_route.json          # advanced kinds  → read-only config dump
```

Each cell becomes a file named `NN_key.ext` — the zero-padded position plus the cell key, with the extension matching the cell type (`.sql`, `.py`, `.md`). Cells with no single source string (advanced kinds like `branch`, `map`, `materialize`, `agent`) appear as read-only `.json` config dumps.

Two editing modes, matching the two kinds of file:

- **Cell files** (`cells/*.sql|.py|.md`) write straight back to the spec as you type — exactly like editing the cell in the notebook. Switch to the canvas or notebook and the change is already there.
- **`flow.py`** is the whole flow as generated Python (the `nubi.flows` SDK). It is editable, but Python must be compiled before it becomes a spec — edit it, then click **Apply** to compile it on the backend and sync the result back to the canvas and notebook. **Reset** discards your edits and regenerates; **Copy** copies the active file.

While the flow is half-built (say, an empty SQL cell), `flow.py` shows a gentle "isn't ready yet" hint instead of generated code — finish the cells and it appears. A small amber dot on `flow.py` marks unapplied edits.

---

## The three cell types

The palette has exactly three cell types. Everything advanced — materialization, fan-out, gates — is a **setting on a SQL or Python cell**, not a separate block.

| Cell | Kind | What it does |
|------|------|--------------|
| **SQL** | `query` | A `SELECT` run against a connector or against upstream cell outputs in DuckDB. |
| **Python** | `python` | Transform rows, call an API, or run custom logic. Runs on the server kernel. |
| **Note** | `noop` | Markdown prose — headings, lists, links. Never executes; no compute cost. |

### SQL cells

A SQL cell holds one query. Write it in the Monaco editor and click **Run** to preview rows inline. By default the cell runs against in-memory DuckDB, where the outputs of upstream cells are available as tables named by their keys.

```sql
-- Upstream cell `orders` is available as a table named `orders`
SELECT
  region,
  SUM(revenue) AS revenue,
  COUNT(*) AS order_count
FROM orders
GROUP BY region
ORDER BY revenue DESC
```

You can also point a SQL cell at a registered connector using the **Run against** picker in the Inspector (see [Connectors](/docs/connectors)). Use this when you need to push the query down to your warehouse rather than joining in-browser.

### Python cells

Python cells run on the **server kernel** (not in the browser). Three variables are injected automatically:

- `inputs` — a dict of upstream cell results keyed by cell key. Each entry has a `rows` list (list of dicts) and a `row_count`.
- `params` — the flow-level parameter values.
- `secrets` — a dict of your org's [Secrets](/docs/secrets) (`{name: value}`), resolved server-side. Read a credential with `secrets["MY_API_KEY"]`. Secret values printed to stdout are masked as `•••` in captured task logs.

Assign your output to a variable named `result`:

```python
# Summarise revenue from the upstream `orders` cell
rows = inputs["orders"]["rows"]
total = sum(row.get("revenue", 0) for row in rows)
high_value = [r for r in rows if r.get("revenue", 0) > 10_000]

result = {
    "total_revenue": total,
    "high_value_count": len(high_value),
    "region": params.get("region", "all"),
}
```

The Inspector's Python editor includes an **Insert example...** picker with ready-made snippets — API calls, DataFrame transforms, and more. Both SQL and Python cells also have a **Secrets** dropdown in the notebook that lists your org's secret names and inserts a reference at the cursor (`{{ secrets.NAME }}` in SQL, `secrets["NAME"]` in Python) — values never leave the server.

Downstream SQL cells can reference the Python cell's result as a table. For example, if the cell key is `enrich`, a subsequent SQL cell can write `SELECT * FROM enrich`.

### Note cells

A Note cell is plain Markdown. Click the body to edit, blur or click **Done** to render. Notes never run and never consume compute — use them to explain a pipeline to teammates or to document assumptions.

---

## Advanced cell settings

The patterns that used to be separate node types — materialize a table, fan out over a list, gate a step on a condition — are now **settings on a SQL or Python cell**, configured in the Inspector under the **Cell behaviour** section. Each active setting appears as a small badge on the cell in the notebook and canvas views.

### Materialization (SQL cells)

By default a SQL cell is a **view** — it computes on demand and persists nothing. Set a materialization strategy to write its output to a real table so dashboards and downstream cells read cheap, pre-computed data instead of recomputing every time.

Choose a **Strategy**:

| Strategy | Behaviour |
|----------|-----------|
| **View** | No persistence (default). |
| **Full** | Overwrite the target table on every run. |
| **Incremental** | Process only rows newer than a stored watermark, then append or merge. |

Additional fields for `full` and `incremental`:

| Field | Applies to | Notes |
|-------|-----------|-------|
| **Target (logical path)** | full, incremental | E.g. `orders/daily`. Written as `<env>/<target>` so `dev` and `prod` never clobber each other. |
| **Time column** | incremental | Rows where `time_column > watermark` are processed (e.g. `updated_at`). |
| **Unique key** | incremental | Present → upsert/merge on these columns; absent → append. |
| **Lookback** | incremental | Re-process a window before the watermark to catch late-arriving rows (e.g. `3 days`). |

### For each (fan-out)

Run a cell once per item in a list. Set **Items** to a template expression or upstream reference that resolves to a list at run time; the cell body executes once per item.

| Field | Notes |
|-------|-------|
| **Items expression** | Template expression resolving to a list, e.g. `{{ inputs.get_regions.rows }}`. |
| **Item variable** | Name bound to each item (default `item`). Reference fields as `{{ item.<field> }}`. |
| **Max concurrency** | Maximum simultaneous item executions. `0` = unlimited. |

The results of all iterations are collected and available to downstream cells as a list.

### Run when (gate)

Skip a cell unless a condition is true. Set **Condition** to a boolean expression over `inputs`, `params`, and `secrets`:

```
inputs.classify.label == 'high_value'
```

A blank condition always runs. A false condition marks the cell **skipped** (and its downstream cells too) — the flow run still succeeds unless another cell fails.

---

## Task kinds under the hood

The palette surfaces three cell types, but the `FlowSpec` itself supports a wider set of task **kinds**. Existing specs that use them keep running, you'll meet them as read-only `.json` files in the code view, and you can author them directly in `flow.py`:

| Kind | Required config | What it does |
|------|-----------------|--------------|
| `query` | `sql` or `query_id` | Run a SQL query — the SQL cell. |
| `python` | `code` | Run a Python snippet — the Python cell. |
| `agent` | `prompt` | Run an LLM-agent step. |
| `materialize` | `combine_sql` | Merge upstream task results in DuckDB and write them to a materialized dataset. |
| `bucket_load` | `uri`, `source` | Upload an upstream task's result to a storage bucket. Optional `format` (`csv`/`json`/`ndjson`/`parquet`), `mode` (`overwrite`/`append`), `secret`. |
| `noop` | — | No-operation — a Note cell, or a join/synchronisation point. |
| `map` | `item_expr`, `body` | Fan out over an iterable; `body` is a nested sub-DAG run once per item. Optional `item_var`, `max_concurrency`, `collect_key`. |
| `branch` | `conditions` | Conditional routing — an ordered list of `{when, next}` pairs; the first matching condition activates its downstream tasks. Optional `default`. |
| `map_collect` | — | Collector for `map` fan-in; returns `{items: [...], item_count: N}`. |

For new flows, prefer the cell-level settings above — **Materialization** instead of a `materialize` task, **For each** instead of `map`, **Run when** instead of `branch` — and keep the spec-level kinds for pipelines that genuinely need nested sub-DAGs or routing.

---

## The task Inspector

Click a cell on the canvas (or open the **Inspector** sidebar) to configure it in full. The Inspector is grouped into sections.

### Identity

- **Key** — the cell's unique slug (lowercase letters, digits, underscores; must start with a letter). This is the name other cells use to reference its result.
- **Kind** — the execution kind. The palette creates `query` (SQL), `python`, and `noop` (Note) cells.
- **Needs** — upstream cells this cell depends on. This is read-only in the Inspector; change it by connecting or disconnecting edges on the canvas (or just reference the upstream cell in SQL and let the dependency be inferred).

### Config

- **SQL cell** — toggle between **Query ID** (a registered query) and **Raw SQL**, plus the **Run against** connector picker.
- **Python cell** — the code editor and the **Insert example...** snippet picker.

### Cell behaviour

Hosts the advanced settings described above: **Materialization** (SQL only), **For each**, and **Run when**.

### Execution

Per-cell reliability and cost controls:

| Field | Meaning |
|-------|---------|
| **Retries** | Extra attempts after the first failure. |
| **Backoff (s)** | Seconds to wait between retry attempts. |
| **Timeout (s)** | Per-attempt time limit. `0` = no limit. |
| **Cache TTL (s)** | When `> 0`, the engine reuses a cached result until this many seconds have elapsed. `0` = always re-run. |

The cache key is a SHA-256 hash of the canonical `{sql, params, rls_claims}` tuple — the same query with the same parameters and the same user context always hits the same cache entry.

---

## Referencing data between cells

`{{ }}` template expressions resolve at run time:

| Expression | Resolves to |
|------------|-------------|
| `{{ params.region }}` | A flow-level parameter value. |
| `{{ inputs.orders.row_count }}` | A field from the `orders` cell's result. |
| `{{ secrets.API_KEY }}` | An org secret, resolved server-side and never sent to the browser. See [Secrets](/docs/secrets). |

In SQL you can also reference an upstream cell directly as a table by its key (`FROM orders`). In Python, read it from `inputs["orders"]["rows"]`.

---

## Environments

Every run targets an **environment**. Environments belong to the **project**: the backend seeds `dev` and `prod` for each project, and you can add more. Pick the active environment with the **environment selector in the left sidebar** (beneath the project selector) — the selection is global app state, persists per project in your browser, and is shared by Flows, Queries, and Dashboards.

| Env | Dot | Notes |
|-----|-----|-------|
| `prod` | Emerald | The project default. |
| `dev` | Sky | Seeded out of the box. |
| Custom | Violet | Click **Add environment** in the sidebar selector to create one (e.g. `staging`), optionally seeded from a git branch. |

How the environment affects flows:

- **Runs are stamped with it.** Triggering a run passes the active env (resolution order: explicit override → the flow's project default), and the run banner shows which env a run targeted.
- **Materialized targets are namespaced** under it (`<env>/<target>`), so a run in `dev` never overwrites a `prod` table.
- **Protected environments gate visibility.** An environment can be marked *protected* (`prod` typically is). When the active env is protected, flows with no version pinned to it show a `not in <env>` badge in the flow list — promote a version there to make it available (see below).

Pick the environment **before** clicking Run.

---

## Versions — checkpoint, history, promote

Saved flows are versioned. Three top-bar actions drive it:

- **Checkpoint** — snapshot the current saved draft as a new immutable version, with an optional message. Unsaved edits are flushed first; if nothing changed since the last version, the existing version is reused instead of creating a duplicate.
- **History** — open the version timeline (newest first). Rows pinned to an environment carry an env chip. Per version you can:
  - **View** — load that version read-only. The builder shows it under a banner with **Restore** and **Back to draft** actions; your draft stays untouched until you restore.
  - **Restore** — write that version's spec back into the current draft (overwriting unsaved draft changes, after confirmation).
  - **Promote** — move a pinned version from one environment to another.
- **Promote** (from the History dialog) is how a flow reaches a protected environment: pin a version in `dev`, test it, then promote it to `prod`. For flows, promoting also best-effort copies incremental materialization watermarks so the promoted version doesn't reprocess history from scratch.

The pinned-version model means `prod` can keep serving a known-good version while you iterate on the draft and run it in `dev`.

---

## Saving and validating

The top bar (Builder tab) has the core actions:

1. **Validate** — checks the flow for problems (cycles, missing dependencies, missing required config) without running it. Results appear in a banner: green "Flow spec is valid." or a list of issues.
2. **Save** — creates or updates the flow. You must save before you can run a durable run, create a checkpoint, or attach a schedule.

Existing saved flows **autosave** about 2 seconds after your last edit — a subtle "saved" badge confirms it. Draft flows (never saved) require a manual Save. Editing the flow as code lives in the dedicated **Code** view (the third view-switcher option) — see [Code / Files view](#code-files-view).

---

## Scheduling a flow

![The Automations page — every flow with its schedule, next/last run, and a Run now button](/docs/screenshots/automations.png)

A saved flow can run automatically on a schedule. The **Schedule** button appears in the top bar once the flow is saved (it is hidden for new drafts).

Click **Schedule** to open the schedule popover:

1. Tick **Enabled** to activate the schedule.
2. Choose a preset or type a custom expression in the text field:

| Preset | Expression | Meaning |
|--------|-----------|---------|
| Every hour | `interval:1h` | Run once per hour. |
| Every 6 hours | `interval:6h` | Run every 6 hours. |
| Daily · 9am | `0 9 * * *` | Every day at 09:00 UTC. |
| Weekly · Mon 9am | `0 9 * * 1` | Monday at 09:00 UTC. |

You can also type any interval (`interval:30m`, `interval:12h`) or any valid 5-field cron expression (`0 7 * * 1-5` — weekdays at 07:00 UTC).

3. Click **Save** in the popover. The toolbar button turns green and shows the schedule summary when a schedule is active.

When a schedule is active and the flow worker is running, Nubi materialises a new run automatically at each due tick — the trigger shows as **schedule** in run history. Use scheduling for recurring work (refreshing a materialized table, rebuilding a daily rollup) rather than triggering manually each time. Scheduled runs consume compute units like any other durable run.

To pause a schedule without deleting it, open the popover and uncheck **Enabled**.

---

## Running a flow

There are two execution modes with different cost and durability.

### Preview a single cell (notebook)

In the notebook, click a cell's **Run** button. This runs a fast, row-capped (500 rows) preview and shows the result inline. Upstream cells run first so cross-cell references resolve. No flow run is persisted — previews are the cheap, iterative path while you build.

### Full durable run

To run the whole flow durably:

1. **Save** the flow (durable runs require a saved flow).
2. Pick the target **environment** in the sidebar selector.
3. Click **Run** in the top bar, or click **Run all** from the notebook.
4. In notebook view, a **Run Plan** gate dialog previews which cells will run and highlights the downstream impact of recently changed ones. Confirm to launch.

A durable run executes through the work pool with full retries, caching, timeouts, and persisted state. Once started, the workspace switches to the **Runs** tab and shows the live run. Durable runs run every cell to completion, not a capped sample — keep heavy materializations on a schedule rather than re-running them by hand.

---

## Watching a run

The **Runs tab** shows the live run view, polling about every 1.5 seconds and stopping automatically once the run is complete.

- A **banner** shows the run id, overall state, the environment it targeted, and a count of how many cells are in each state.
- The **canvas** is read-only and colours each node by state:

| State | Colour | Meaning |
|-------|--------|---------|
| Pending | Slate | Waiting on upstream cells. |
| Ready | Blue | Upstream done; waiting to start. |
| Running | Amber (pulsing) | Executing now. |
| Retrying | Orange | Scheduled for another attempt after backoff. |
| Success | Green | Completed successfully. |
| Failed | Red | Errored after exhausting retries. |
| Timed out | Red | Exceeded configured timeout. |
| Upstream failed | Orange | An upstream cell failed, so this one did not run. |
| Cancelled | Gray | The run was cancelled. |

### Task result panel

Click any node in the run view to open the **task result** panel. It shows:

- State badge, **started**, **finished**, **duration**, and **attempt number** (if it retried).
- The **error** message, if it failed.
- A collapsible **Logs** drawer with the cell's captured output.
- The **result** payload (rows, computed values) as formatted JSON.

### Run history and lineage

- The **Runs tab** (no live run open) lists past runs of the flow, newest first, each with its state, trigger (`manual` or `schedule`), and timestamp. Click any run to reopen its live view.
- The **Lineage** toggle (notebook view, top bar) shows how cells feed one another across the flow — useful for understanding impact before a change.

---

## Tips

- **Build in the notebook, review on the canvas, diff in code.** Iterate with cheap cell previews, switch to the canvas to refine the dependency structure, and use the code view when you want to read or edit the whole flow as files.
- **Keep keys meaningful.** Cell keys are how other cells reference results (`FROM orders`, `inputs["orders"]`), they name the files in the code view, and they appear throughout the run inspector and lineage view.
- **Let SQL declare its own dependencies.** Reference an upstream cell in `FROM`/`JOIN` and the edge is inferred — no manual wiring needed.
- **Checkpoint before big changes, promote when ready.** Versions are cheap snapshots; pinning and promoting them between `dev` and `prod` is how a flow ships safely.
- **Materialize once, read many.** Turn an expensive multi-source SQL cell into a materialized table on a schedule, then point dashboards at the cheap result instead of recomputing on every view.
- **Use Run when to skip work.** Gate expensive cells on a condition so they only run when the data warrants it.
- **Use the schedule, not manual runs.** Once a pipeline is working, attach a schedule and let it run automatically rather than triggering it by hand each time.
- **Mind the wallet.** Previews are cheap; full durable runs and large materializations cost more compute units.

See also: [Secrets](/docs/secrets) for `{{ secrets.NAME }}`, [Connectors](/docs/connectors) for Run against targets, [Dashboards](/docs/dashboards) for consuming materialized outputs, [Git sync](/docs/git-sync) for flows on disk, and [Billing and usage](/docs/billing-and-usage) for how compute units are metered.
