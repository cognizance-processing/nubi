# Client-Compute Plan — making the "no viewer tax" wedge real

**Status:** proposal · **Owner:** — · **Last updated:** 2026-06-10

## 1. Why this exists

The wedge we sell: **cost scales with data freshness, not audience size.**
`docs/billing-and-usage.md` says "dashboards compute in the viewer's browser
(DuckDB-WASM)". Today that is not what ships:

| Claim | Reality (file:line) |
|---|---|
| Dashboards compute in the browser | Every widget (`KpiWidget`, `ChartWidget`, `TableWidget`, `PivotWidget`, `HtmlWidget`) calls `runArrowQueryById` → `POST /api/v1/query` on first render **and on every param change** (`src/dashboards/widgets/ChartWidget.jsx:79`) |
| Viewers are near-free | Each interaction = app-machine request + Arrow egress. Edge cache (`X-Nubi-Cache: HIT`) saves recompute, not the round-trip |
| Browser engine is used | Only in `QueryWorkspace` scratch cells, behind a hidden `\bcell_\d+\b` regex (`src/pages/app/QueryWorkspace.jsx:833`) — no badge, no docs, no choice |
| Shared queries are fetched once | No coalescing — two widgets on the same `query_id` fire two POSTs |

Target architecture:

```
                 server (planner, RLS, cache)          browser (DuckDB-WASM)
first load   →   base results (scan params only)   →   registerArrowTable(...)
interaction  →   ──────────── nothing ────────────  →   queryLocal() over base results
param that
changes scan →   re-fetch base result              →   re-register
```

A dashboard becomes: **few base results computed server-side, all viewer
interaction recomputed locally.** Security model unchanged — the browser only
ever queries data the server already planned, RLS-filtered, and released.

---

## 2. Phase 1 — local slice recompute + run-location badges

### 2.1 Param classes: `scan` vs `slice`

The core contract. Each widget param is classified:

- **`scan`** — changes what the server reads (date range → partition pruning,
  anything interpolated into a large scan). Sent as `named_params`; changing it
  re-fetches the base result. **This is today's behavior and the default.**
- **`slice`** — subsets rows/groups already present in the base result
  (region dropdown, status filter, top-N, sort). Never sent to the server;
  applied locally via `queryLocal`.

A param may only be `slice` if the saved query **returns the unfiltered
superset** for it (i.e. the query does not reference that param in SQL, or has
a default that returns all rows) and that superset fits the client budget
(§2.5). This is a semantic contract authors opt into, not something we infer.

**Spec change** (`src/dashboards/validateSpec.js`): widget params are currently
`{ ref: '<varName>' }` or literals (see `resolveParams` in
`src/dashboards/VariableStore.jsx`). Extend to:

```jsonc
"params": {
  "from_date": { "ref": "from_date" },                  // default mode: "scan"
  "region":    { "ref": "region", "mode": "slice", "column": "region" }
}
```

`column` names the base-result column the slice filters on (defaults to the
param name). `validateSpec.js` rejects `mode: "slice"` without a resolvable
column. Existing specs are untouched — everything defaults to `scan`.

### 2.2 Shared widget data hook (also fixes coalescing)

Today each widget duplicates the fetch effect. Replace with one hook,
`src/dashboards/useWidgetData.js`:

```js
// useWidgetData(query_id, widgetParams) → { table, origin, cacheStatus, loading, error }
// origin: 'local' | 'edge' | 'server' | 'sample'
```

Behavior:

1. Split `resolvedParams` into `scanParams` / `sliceParams` using the spec
   modes (resolution itself stays in `VariableStore.jsx`).
2. **Base fetch** keyed by `(query_id, scanParams)` through a module-level
   in-flight map + result store (`src/lib/resultStore.js`) so N widgets on the
   same key share one POST:
   ```js
   const inflight = new Map()   // key → Promise<{table, cacheStatus}>
   const results  = new Map()   // key → { table, registeredName, bytes }
   ```
3. On arrival, `registerArrowTable(name, table)` (`src/lib/wasmRuntime.js:471`)
   under a deterministic name: `base_<query_id>_<hash(scanParams)>` (sanitize
   like `runLocalSqlForCell` does for cell names).
4. **Slice locally**: if `sliceParams` is non-empty and the base is registered,
   build and run via `queryLocal` (`src/lib/wasmRuntime.js:492`):
   ```sql
   SELECT * FROM "base_<id>_<hash>" WHERE "region" = ? AND ...
   ```
   Use DuckDB-WASM prepared statements for values — never string-interpolate
   user input, even client-side. `origin = 'local'`.
5. Slice-param changes re-run step 4 only. Scan-param changes invalidate the
   key and re-run from step 2.
6. **Fallback**: if registration or local query throws (OOM, unsupported type),
   permanently demote that widget for the session — send all params as
   `named_params` exactly like today. The failure path is the current path.

Migrate the five widgets to the hook one at a time; each migration deletes its
bespoke `useEffect` (e.g. `ChartWidget.jsx:68–101`).

### 2.3 Run-location badge

Small pill on each widget chrome and each scratch cell, fed by `origin`:

- `local` (green) — computed in browser, zero server cost
- `edge` (blue) — served from content-addressed cache (`X-Nubi-Cache: HIT`,
  read at `wasmRuntime.js:151`)
- `server` (amber) — planned + executed (`MISS`)
- `sample` (gray) — offline fallback (existing behavior)

Reuse the `CacheBadge` pattern from `src/components/QueryCell.jsx`. Also add
the badge to `ScratchSqlCell` so the existing hidden `cell_N` routing becomes
visible. Tooltip shows rows + approximate bytes.

This is the "guided to use it correctly" piece: users learn the cost ladder by
watching it, and the wedge becomes demoable.

### 2.4 What runs locally in v1

Equality / IN filters, range filters on slice columns, sort, top-N/limit.
**Not in v1:** local re-aggregation (pivot regroup), cross-base joins, local
date-bucket changes. Pivot/Chart widgets whose interactions change grouping
keep those params as `scan`. Re-aggregation is Phase 4 — it needs the base
result to be pre-aggregation-compatible (additive measures), which is a rollup
concern, not a transport concern.

### 2.5 Client budget guardrails

- Base result is client-registered only if `table.byteLength ≤ 64 MB`
  (constant `CLIENT_SLICE_MAX_BYTES` in `resultStore.js`). Oversize → demote
  to server-param mode, badge stays `server`.
- Halve the budget when `navigator.deviceMemory ≤ 4` (covers iOS Safari's
  ~300–500 MB practical tab ceiling).
- Cap total registered bytes per dashboard at 256 MB; evict LRU bases
  (`DROP TABLE` + re-register on demand).
- WASM hard limits (for reviewers): wasm32 = 4 GB address space, ~1–2 GB
  practical; **single-threaded** (jsDelivr bundle, no SharedArrayBuffer —
  intentional, COOP/COEP would break iframe embeds); **no spill** — over-budget
  queries throw, hence the demotion path, never a user-facing OOM.

### 2.6 Acceptance criteria (Phase 1)

- A dashboard with one `slice` param: changing it produces **zero** requests
  in the network tab; widget shows `local`.
- Two widgets sharing `query_id` + scan params produce exactly one POST.
- Killing the backend after first load: slice interactions keep working.
- A 100 MB base result demotes gracefully — behavior identical to today.
- Legacy specs (no `mode`) behave byte-for-byte identically to today.

---

## 3. Phase 2 — browser result cache (returning viewers)

The edge cache is content-addressed; the browser still re-downloads on every
visit. Add an IndexedDB cache in `resultStore.js`:

1. Server adds `X-Nubi-Result-Hash: <content-hash>` to `/api/v1/query`
   responses (the hash already exists as the edge cache key —
   `backend/app/routes/query.py`).
2. Client stores Arrow IPC bytes keyed by hash; on load, sends
   `If-None-Match: <hash>`; server answers `304` on match.
3. Cap the store (~200 MB, LRU). Treat as transparent transport optimization —
   no UX, no correctness impact (hash mismatch = full response).

Cuts repeat-viewer egress to near zero — the other half of viewer marginal cost.

---

## 4. Phase 3 — pinned extracts (client-mode datasets)

Per-dataset, not per-query (a per-query "run in browser" toggle is the wrong
shape: RLS forces first-touch through the server, and WASM OOM makes free
choice a footgun).

- Add `client_mode: bool` to materialized rollups (`source = "materialized"`,
  see `docs/lakehouse.md`). Enforce at materialization: result ≤ 100 MB Parquet
  **and** RLS-free for the audience (org/public visibility) — reject otherwise.
  Per-user RLS extracts are explicitly out: they kill shared caching and move
  enforcement into devtools-land.
- Dashboard load: fetch the extract once (Phase 2 cache applies), register it,
  and route *all* widget queries against it through `queryLocal` — including
  aggregation, since the extract is by construction small.
- Use cases: embeds (works through API hiccups), high-fan-out public
  dashboards, offline demos.

---

## 5. Phase 4 — local re-aggregation + rollup loop

1. Mark base queries whose measures are additive (sum/count/min/max — planner
   can derive) as `reaggregable`. For those, group-by changes and pivot
   regroups run locally over the base grain.
2. Surface the pre-agg suggester (`fetchPreaggSuggestions`,
   `wasmRuntime.js:427`; panel at `src/pages/app/PreaggregationsPanel.jsx`)
   inline at the point of pain: query > 3 s or result > client budget →
   suggest the rollup that would make it client-sliceable. This is the loop
   that converts "big slow query" into "small base + free local interaction".

---

## 6. Out of scope / explicitly rejected

- **Per-query run-location toggle** — see §4 rationale.
- **Browser → raw object storage** (httpfs in WASM with presigned URLs) —
  bypasses planner RLS; no acceptable enforcement story.
- **Multithreaded WASM** — requires COOP/COEP, conflicts with embed iframes.
- **Mixing local + server tables in one query** (`cell_1 JOIN orders`) — fails
  today in both engines; Phase 1 badge at least makes the boundary visible.

## 7. Docs to update when Phase 1 lands

- `docs/billing-and-usage.md` — until then, soften "dashboards compute in the
  viewer's browser" to describe the edge cache honestly. **Do this first; it's
  a demo-able gap a sophisticated buyer will catch.**
- `docs/getting-started.md` — document badges and the scan/slice param modes.
- Landing page "no viewer tax" — true after Phase 1 for slice interactions;
  true for repeat visits after Phase 2.

## 8. Suggested sequencing

| Step | Size | Depends on |
|---|---|---|
| Docs honesty fix (§7) | XS | — |
| `resultStore.js` + coalescing | S | — |
| `useWidgetData` + migrate 5 widgets | M | resultStore |
| Badges (widgets + scratch cells) | S | useWidgetData |
| Spec `mode: slice` + validateSpec + local slicing | M | useWidgetData |
| Guardrails (§2.5) | S | local slicing |
| IndexedDB cache + `X-Nubi-Result-Hash` | M | — (parallel) |
| Pinned extracts | L | Phase 1 |
| Local re-agg + inline suggester | L | Phase 1 |
