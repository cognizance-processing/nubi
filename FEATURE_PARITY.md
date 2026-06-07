# Manual Builder — Legacy Feature Parity Analysis

_Comparing the current spec-driven manual builder (`src/editor`, `src/dashboards`, `src/viz`, `src/lib`) against the legacy BI tool (`legacy/src/cog/bitool`, `legacy/src/pages/app/bitool`)._

Date: 2026-06-05 · **Matrix re-audited against source 2026-06-07**

> **2026-06-07 update.** The original gap analysis below predates the M13–M22 builder
> waves. Most of the "biggest gaps" have since landed: query templating/params, the
> filter/variable/route-param interactivity layer, a TanStack `DataGrid` table, more chart
> types + stacking/combo/dual-axis, new widget types (metric/pivot/section + custom HTML),
> export/share, and a streamed agentic AI chat. The **capability matrix** and **TL;DR**
> have been refreshed to reflect what now exists in the code; §§3–5 below are kept as the
> original design rationale (they read as "to do" but are now largely "done" — see the
> matrix for current status).

---

## TL;DR — your three direct questions

1. **"What are we missing from legacy in the manual builder?"**
   _Originally_ a lot of depth on three axes. **As of 2026-06-07 all three are substantially
   closed:** (a) **query authoring** — the merged Queries workspace (`src/pages/app/QueryWorkspace.jsx`,
   `/playground` now redirects to `/queries`) has a Monaco SQL editor, connector picker,
   dialect auto-detected from the connector, sqlglot dialect validation, templates,
   named/typed params, and Save-as-registered-query, plus an AI text-to-SQL path; (b)
   **widget breadth** — 9 chart types + stacking/combo/dual-axis, a TanStack `DataGrid`
   table, and new metric/pivot/section widgets plus per-widget custom HTML; (c) **dashboard
   interactivity** — filter widgets, a `VariableStore`, cross-widget variables, drilldown,
   and URL-param↔variable binding. Genuinely-still-missing: multi-tab dashboards, dashboard
   folders, org-level global variables (see matrix).

2. **"Can our SQL queries also do templates?"**
   **Yes — shipped.** Registered queries declare typed/named params; the planner renders
   `{{name}}` placeholders to positional binds server-side (never string-concat), with a
   security-critical resolver precedence (token/RLS claims > body params > query default).
   See `backend/app/queries/registry.py`, `backend/app/connectors/planner.py`,
   `backend/app/routes/query.py`.

3. **"Can dashboards have params in routes like legacy?"**
   **Yes — shipped.** `DashboardViewPage` (`src/pages/DashboardViewPage.jsx`) reads
   `useSearchParams()`, seeds a `VariableStore` (`src/dashboards/VariableStore.jsx`), and
   writes filter changes back to the URL (`/d/:id?region=North&from=2026-01-01`). Precedence:
   embed-token locked param (hook present, not yet server-wired) > URL `?var=` > filter
   widget > variable default. See [§5](#5-dashboard-parameters--routing).

---

## Architecture context (why parity is not 1:1)

The two systems are **fundamentally different in where logic lives**, so "parity" should mean _equivalent user capability_, not a port of legacy internals.

| Concern | Legacy | Current |
|---|---|---|
| Query definition | Stored `Query` object: `templatedQuery` (SQL + Go template) + `queryConfig` (visual builder JSON) + argument links | Widget references a **`query_id`** string; the SQL lives **server-side**, resolved by `POST /api/v1/query {query_id}` |
| Query authoring UI | Ace SQL editor **and** visual query builder, in-app | None in builder. Freeform SQL only in `/playground`. Builder just **picks a query_id** |
| Execution | `Executor.ExecuteOne()` JSONRPC, renders Go templates server-side, multi-DB | `runArrowQueryById()` → Arrow IPC stream (`src/lib/wasmRuntime.js`), client streams record batches |
| Widget model | `Widget` with 270+ `BasicWidgetSettings` fields, Redux-driven variable graph | `{ id, type, query_id, chart_type, encoding, props, pos }` (`src/editor/DashboardEditor.jsx`) |
| Charting | ApexCharts + Chart.js | Apache ECharts (`src/viz/EChart.jsx`) |
| Layout | react-grid-layout, 12–72 col presets, freeform/snap modes, multi-tab | react-grid-layout, fixed 12-col, single tab |
| State | Redux store + selectors (`widgetChangeVariable`, dependency selectors) | Local React state, no cross-widget graph |
| Stack | TypeScript | JSX/JS |

**Implication:** because our SQL is server-resolved by `query_id`, templating has two possible homes — (a) keep queries server-side and pass a `params` map alongside `query_id` (recommended), or (b) bring SQL authoring into the client like legacy. Option (a) is far less work and keeps the clean separation.

---

## Capability matrix

✅ have · 🟡 partial · ❌ missing

| Capability | Legacy | Current | Verdict / status |
|---|---|---|---|
| **Query** | | | |
| SQL editor in-app | ✅ Ace | ✅ Monaco in the Queries workspace (`src/components/SqlEditor.jsx`, `src/pages/app/QueryWorkspace.jsx`) | Done |
| Visual query builder | ✅ | ❌ | **❌ CUT — explicitly not wanted (2026-06-05). SQL editor + AI text-to-SQL cover authoring.** |
| Query parameters / templating | ✅ `{{ .Var }}` + serializers | ✅ `{{name}}` → positional binds, server-side (`connectors/planner.py`, `routes/query.py`) | Done |
| Typed arguments (text/number/date/dropdown) | ✅ | ✅ `QueryParam` types text/number/date/daterange/select/multiselect (`queries/registry.py`) | Done |
| Dependent / query-sourced dropdowns | 🟡 manual | ✅ filter `options_query_id` sources options from a query | Done |
| Text-to-SQL (LLM) | ✅ chat-based | ✅ `POST /ai/sql` (`backend/app/ai/sql.py`) + agentic chat tool | Done |
| Multi-DB connectors | ✅ Postgres/MySQL/BigQuery | ✅ registry (8): `postgres`, `duckdb` (file-backed), `http_json`, `mysql`, `mariadb`, `jdbc`, `snowflake`, `bigquery` (`connectors/registry.py`) | Done |
| Dialect-aware validation | ✅ | ✅ sqlglot dialect validation; dialect auto-detected from the connector (`QueryWorkspace.jsx` `CONNECTOR_DIALECT`) | Done |
| **Widgets** | | | |
| KPI / single value | ✅ QuickStats + sparkline | ✅ KPI **+ delta vs `encoding.compare` + sparkline `encoding.spark`** (`KpiWidget.jsx`) | Done |
| Table | ✅ MUI DataGrid Premium | ✅ TanStack `DataGrid` — sort/filter/paginate/resize/reorder/pin/group/aggregate/virtualize/export (`src/components/DataGrid.jsx`, `widgets/TableWidget.jsx`) | Done |
| Conditional formatting (tables) | ✅ rule engine | ✅ rule engine + column number/date/currency formats (`widgets/conditionalFormat.js`) | Done |
| Chart types | ✅ ~16 (Apex + Chart.js) | ✅ 9: line/bar/hbar/scatter/area/pie/donut/heatmap/gauge **+ stacking/combo/dual-axis** (`src/viz/chartOption.js`, `CHART_TYPES` in `DashboardEditor.jsx`) | Done |
| Filter widgets (autocomplete, date range) | ✅ | ✅ `filter` widget select/multiselect/daterange/text (`widgets/FilterWidget.jsx`) | Done |
| Misc (label, image, divider, breadcrumb) | ✅ | ✅ `text` (markdown) + `section` widgets; per-widget custom sanitized HTML (`widgets/TextWidget.jsx`, `SectionWidget.jsx`, `HtmlWidget.jsx`, `widgetHtml.js`) | Done |
| Metric / pivot widgets | 🟡 | ✅ `metric` (stat tile) + `pivot` (rows×cols×measure) (`widgets/MetricWidget.jsx`, `PivotWidget.jsx`) | Done |
| Widget groups / steppers / drilldown | ✅ | 🟡 drilldown (chart-click → variable) shipped (`DrilldownSection` in editor); groups/steppers skipped | Drilldown done; rest skip |
| **Dashboard** | | | |
| Grid layout / drag-resize | ✅ | ✅ | — |
| Per-breakpoint / responsive layouts | ✅ 12–72, 3 modes | ✅ desktop/tablet/mobile overrides + device switcher (`src/dashboards/responsiveLayout.js`) | Done |
| Multi-tab dashboards | ✅ | ❌ | Maybe |
| Dashboard groups / folders | ✅ | ❌ | Maybe |
| Dashboard background | 🟡 | ✅ none/transparent/solid/gradient/image/css (`BACKGROUND_TYPES`, `widgetHtml.js` `backgroundToCss`) | Done |
| Dashboard-level variables/filters | ✅ | ✅ `spec.variables` + `VariableStore` (`src/dashboards/VariableStore.jsx`) | Done |
| Cross-widget variable graph | ✅ Redux | ✅ lightweight context (no Redux); widgets `ref` a var and re-query on change | Done |
| Org-level global variables | ✅ | ❌ | Maybe |
| **Route params binding to filters** | ✅ `useSearchParams` | ✅ seed-from-URL + write-back (`DashboardViewPage.jsx`) | Done |
| Undo/redo | ✅ (25-step) | 🟡 see M19 (TASKS.md) — large-depth history planned/landing in editor | Verify |
| Theming per dashboard | 🟡 (DashConfig; ThemeCreator abandoned) | 🟡 background + per-widget style; no full theme creator | Partial (deliberate) |
| **Sharing / ops** | | | |
| Embedding (JWT, expiry, org secret) | ✅ | ✅ `<nubi-dashboard>` + JWKS verifier + server-side RLS (M3); editor share menu surfaces embed url + RLS model (`ExportShareMenu.jsx`) | Done |
| CSV / Excel / PNG / PDF export | ✅ all four | ✅ CSV + PNG + PDF (`src/lib/exports.js`, `ExportShareMenu.jsx`, `WidgetToolbar.jsx`); Excel deferred | CSV/PNG/PDF done |
| Scheduled email reports (cron) | ✅ | ✅ `report` job kind on the M11 scheduler (`backend/app/jobs/`) | Done |
| Dashboard clone / overwrite | ✅ | 🟡 board CRUD exists; clone affordance not surfaced | Maybe (cheap) |
| Agentic AI chat (build/modify dashboards) | 🟡 feedback chat | ✅ streamed Cursor-style chat + tool use + model picker + history (`src/editor/ChatPanel.jsx`, backend `app/chat/`, `app/ai/agent.py` + `tools.py`) | Done |
| Git sync for queries/dashboards | ❌ | ✅ `POST /git/sync` + history/restore (`backend/app/git/`, `docs/git-sync.md`) | Done (beyond legacy) |
| VPC bridge (private-network connectors) | 🟡 data bridge | ✅ WebSocket tunnel wired into the query path (`network_mode='bridge'`, `docs/bridges.md`) | Done (beyond legacy) |
| Layout sync from template | ✅ | ❌ | Skip |
| Action logs / audit | ✅ | ❌ | Skip (until enterprise) |
| Table manager (CRUD on source tables) | ✅ | ❌ | Skip (scope creep) |
| Data bridge (semantic/ETL layer) | ✅ | ❌ | Skip (heavy; revisit later) |

---

## 3. Query templating (our biggest gap)

### How legacy does it
- `Query.templatedQuery` holds SQL with Go-template placeholders: `{{ .StartDate }}`, with conditionals like `{{ if (eq .Level "High Level") }}…{{ end }}`.
- Placeholders are extracted in the UI via regex `\{\{[\s\S]*?\}\}` (`legacy/.../query/utils.ts`).
- Values are supplied at run time as a `templates` map and rendered **server-side** by `Executor.ExecuteOne()`.
- **Safety:** each value carries a `serializerStringFunction` — `$StringArraySerializer` → `('a','b','c')` for `IN`, `$SqlStringSerializer` → quoted+escaped, `$DateSerializer` → SQL date, `$RawSqlSerializer` (escape hatch). DB-aware (Postgres/BQ/MySQL quoting).
- Arguments are first-class: `QueryArgument` rows define the param set; org-level `GlobalVariable`s auto-inject.

### How it should map to us
Our SQL is already server-side behind `query_id`, so the natural design is:

```
POST /api/v1/query  { query_id: "sales_by_region", params: { region: "North", from: "2026-01-01" } }
```

- Backend stores the registered query as templated SQL and renders `params` server-side with **typed, safe serialization** (do **not** string-concat on the client — keep injection safety on the server, same as legacy).
- Front-end work:
  1. Extend the widget spec: `query_id` + optional **`params: { name: value }`** (or `params: { name: { ref: "<variableName>" } }` to bind to a dashboard variable — see §5).
  2. A param can be **literal** (set in the config panel) or **bound** to a dashboard variable / filter widget output.
  3. `runArrowQueryById(queryId, params)` — thread `params` through `src/lib/wasmRuntime.js`.
- Param types to support first: **text, number, date, date-range, single-select, multi-select**. Multi-select → server `IN (...)` serializer.

**Estimated effort:** medium. The client side is small (spec field + plumbing). The real work is the server rendering+serialization contract, which can mirror legacy's serializer list.

---

## 4. Filter widgets + cross-widget variables

Templating is only useful if something supplies the values. Legacy does this with **filter widgets** (autocomplete, date-range) that write to a variable, and dependent widgets re-run when it changes (Redux `widgetChangeVariable` + dependency selectors).

Recommended for us:
- Add **filter widget types**: `filter_select` (options from a `query_id`), `filter_daterange`, `filter_text`.
- Introduce a lightweight **dashboard variable store** (React context or a small reducer — we don't need full Redux): `{ [varName]: value }`.
- Filter widgets write `varName`; data widgets whose `params` reference that var re-query on change.
- This is the same primitive that powers route params (§5) and global variables.

**Do this together with §3** — they are one feature ("parameterized, interactive dashboards"), and shipping templating without filters leaves params un-fillable.

---

## 5. Dashboard parameters & routing

### Legacy
- Routes `dash/:dashName/:dashId/:bridgeId?` and embed `embedding/:token`.
- `Dashboard.tsx` / `DashboardEmbedded.tsx` call `useSearchParams()` and seed widget variables from the URL, so a dashboard can be deep-linked with filter state.

### Current
- `/d/:id` and `/editor/:id` (`src/App.jsx`). The `:id` is the only param; **the spec has no variables to bind a query string to.**

### To reach parity
1. **Prerequisite:** dashboard variables from §4.
2. On `DashboardViewPage` mount, read `useSearchParams()` and **initialize the variable store** from the query string (`/d/abc?region=North&from=2026-01-01`).
3. When a filter widget changes a variable, **write it back to the URL** (`setSearchParams`) so the view is shareable/bookmarkable — exactly legacy's behavior.
4. Define precedence: URL param > filter widget default > variable default.

**Effort:** small **once variables exist**. Without variables it's meaningless, so it's strictly downstream of §4.

---

## Gap list, triaged

### ✅ Sensible parity — DONE (was "do these in order"; all landed by 2026-06-07)
1. **Query templating / parameters** (§3) — ✅ typed/named params, server-side safe positional binds.
2. **Filter widgets + dashboard variable store** (§4) — ✅ `FilterWidget` + `VariableStore`.
3. **Route params bind to variables** (§5) — ✅ seed-from-URL + write-back in `DashboardViewPage`.
4. **Table upgrade + conditional formatting** — ✅ TanStack `DataGrid` + `conditionalFormat.js` rule engine.
5. **More chart types** — ✅ 9 types + stacking/combo/dual-axis (`chartOption.js`).
6. **CSV/PNG/PDF export** — ✅ `src/lib/exports.js` + `ExportShareMenu` / `WidgetToolbar`.
7. **AI SQL** — ✅ `POST /ai/sql` emitting a registered query; also exposed as an agent tool.

### 🟡 Maybe — worth it but defer / product call
- Multi-tab dashboards; dashboard groups/folders; undo/redo (cheap); dashboard clone (cheap); column-count presets / snap modes; org-level global variables; in-builder SQL editor (vs. keeping it in playground); misc widgets (label/image/divider/breadcrumb).
- **Embedding** (JWT + expiry + org secret) and **scheduled email reports** — high value for a BI product but both need real backend work and a security model; schedule deliberately, not as a port.
- **Multi-DB connectors** — only if the product is moving from "single managed warehouse" to "bring your own DB." Big surface (`connectors/forms`, credential storage, test-connection). Decide by product direction first.

### ❌ Not sensible — skip (or much later)
- **Data Bridge** (bridge/entity/entityMapping semantic+ETL layer) — heavy, half-finished in legacy, only justified with a mature multi-source story.
- **Table Manager** (CRUD/DDL on source tables from the UI) — scope creep, security risk; a BI viewer shouldn't mutate source data.
- **Layout synchronizer**, **dashboard feedback chat**, **action logs/audit** — enterprise/ops niceties; not core builder capability.
- **Theme Creator** — already abandoned/commented-out in legacy; our design system supersedes it.
- **Widget groups / steppers** — do **not** port the legacy widget. In legacy this was one feature family: a container holding child widgets in a nested grid (`BasicWidgetGroup.tsx`), optionally rendered as a wizard via MUI `Stepper` with 7 cosmetic appearance variants (`BasicWidgetGroupStepper.tsx`). The appearance zoo, manual long-press timers, and Redux/MUI coupling are over-engineered polish with thin real usage ("clients enjoyed them but we had few"). The drilldown half already shipped (chart-click → variable, `DrilldownSection`; see Widgets table) and the in-place filter model covers the 80% case. The only net-new value is the **guided drill *path*** (click → advance → next view scoped to the clicked value), which is real but a minority interaction model. **If demand appears, build the modern equivalent — a container widget with `display: 'tabs' | 'stepper'` over child widget IDs — not a port.** Tabs is the broadly-wanted version (same primitive as "Multi-tab dashboards", listed Maybe in the Dashboard table); stepper is tabs + linear nav + drill-pass. Skip speculatively; the trigger is a client specifically asking for the advance-and-narrow wizard.
- **PSU / `levelFilterPSU` argument special-casing** — that's keystone-product domain logic, not generic BI.

---

## Suggested phasing — status (2026-06-07)

- **Phase 1 — Interactivity (the headline gap):** ✅ DONE. §3 templating, §4 filter widgets +
  variable store, §5 route params all shipped (M13/M14).
- **Phase 2 — Widget depth:** ✅ DONE. TanStack table + conditional formatting, 9 chart types +
  stacking/combo/dual-axis, CSV/PNG/PDF export (M15/M16).
- **Phase 3 — Authoring & AI:** ✅ DONE. Monaco SQL editor promoted into the merged Queries
  workspace + text-to-SQL emitting registered queries; agentic AI chat with tools (M18/M21).
- **Phase 4 — Sharing & ops:** ✅ mostly DONE. Embedding (JWT/JWKS), scheduled reports (M17),
  export/share. Still open: dashboard clone affordance, multi-tab/groups.
- **Beyond legacy (new):** Git sync for queries/dashboards (M20), VPC bridge for private-network
  connectors, conversational gateway groundwork (M22).
- **Never (unless product pivots):** data bridge (semantic/ETL), table manager,
  audit/sync/feedback/theme-creator, visual query builder.

---

## Key file references

**Current builder (post M13–M22)**
- `src/editor/DashboardEditor.jsx` — spec editor: `CHART_TYPES`, `BACKGROUND_TYPES`, widget palette (kpi/metric/chart/table/pivot/filter/text/section), config panels, drilldown, per-breakpoint layouts, `ExportShareMenu`.
- `src/editor/ChatPanel.jsx` — streamed agentic AI chat (tool use, model picker, history) that can build/modify the spec.
- `src/lib/wasmRuntime.js` — `runArrowQueryById(queryId, {params})` → Arrow IPC stream (params now threaded).
- `src/viz/chartOption.js` — 9 chart types + stacking/combo/dual-axis option builder.
- `src/components/DataGrid.jsx` — TanStack table; `src/dashboards/widgets/*` — KPI/metric/table/pivot/chart/filter/text/section + `conditionalFormat.js`, `HtmlWidget`.
- `src/dashboards/VariableStore.jsx`, `src/dashboards/responsiveLayout.js`, `src/dashboards/widgetHtml.js`.
- `src/pages/DashboardViewPage.jsx` — `/d/:id` loader/renderer; seeds `VariableStore` from `useSearchParams`, writes back to URL.
- `src/pages/app/QueryWorkspace.jsx` — merged Queries workspace (Monaco SQL + Python notebook, connector picker, dialect auto-detect, templates, Save-as-query). `/playground` → redirect.
- `src/lib/exports.js` — Arrow→CSV, chart→PNG, dashboard→PDF.
- `src/App.jsx` — route table.

**Current backend (builder-relevant)**
- `backend/app/queries/registry.py` — `RegisteredQuery` + typed `QueryParam`.
- `backend/app/connectors/planner.py`, `backend/app/routes/query.py` — `{{name}}` → positional binds, resolver precedence, connector resolution, bridge wiring.
- `backend/app/ai/sql.py`, `app/ai/agent.py`, `app/ai/tools.py`, `app/chat/` — text-to-SQL + agentic chat tool registry.
- `backend/app/jobs/` — scheduler + `report` job kind. `backend/app/git/` — git sync.

**Legacy reference implementations**
- Templating: `legacy/src/cog/bitool/query/Query.ts`, `…/query/utils.ts`, `…/widget/widgets/serializers.ts`.
- Arguments: `legacy/.../query/argument/Argument.ts`, `…/argumentEditor/ArgumentEditor.tsx`.
- Global vars: `legacy/src/cog/bitool/globalVariable/GlobalVariable.ts`.
- Text-to-SQL: `legacy/src/cog/bitool/query/text2SQL/Store.ts`.
- Filter widgets: `legacy/.../widgets/basicSettings/filters/…`, conditional formatting `…/tables/basicTable/BasicTableConditionalSettings.tsx`.
- Variable graph: `legacy/.../reportsAndExports/OutputUtil.tsx` (`widgetChangeVariable`).
- Routing/params: `legacy/src/contexts/routes.tsx`, `legacy/.../dashboard/Dashboard.tsx`, `DashboardEmbedded.tsx`.
- Embedding: `legacy/src/cog/bitool/dashboard/embedding/Store.tsx`, `legacy/.../management/embedding/EmbeddingSettings.tsx`.
- Export: `legacy/.../reportsAndExports/exportCsv.ts`, `useDashboardPdfExport.ts`.
