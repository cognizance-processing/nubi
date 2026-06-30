# Lineage

Nubi tracks two kinds of lineage:

- **Query/table lineage** (`/lineage`) — which tables and columns each
  registered query or metric reads and writes.
- **Flow/cell lineage** (`/lineage/flow/{id}`, `/lineage/plan`,
  `/lineage/cell`) — column-level cross-cell lineage within a notebook flow.
- **Inter-model DAG** (`/lineage/dag`) — a directed graph that wires
  producer→consumer edges across all queries, metrics, and bare tables.

All lineage endpoints require a valid first-party Bearer token (`current_user`
dependency).

---

## Query / table lineage

### `GET /api/v1/lineage`

Return the full lineage graph over all registered queries for the caller's org.
Rebuilt on every request (in-memory, fast, ~ms).

**Response:**
```json
{
  "queries": {
    "revenue": {
      "sql": "SELECT region, SUM(amount) FROM orders ...",
      "name": "Revenue",
      "tables": ["orders", "products"],
      "columns": [
        { "table": "orders", "column": "amount" },
        { "table": "orders", "column": "region" }
      ],
      "outputs": ["revenue"]
    }
  },
  "tables": {
    "orders": ["revenue", "active_orders"]
  },
  "columns": {
    "orders.amount": ["revenue"]
  }
}
```

### `GET /api/v1/lineage/query/{query_id}`

Return lineage detail for a single registered query.

**Path params:** `query_id` — the registered query identifier (e.g. `"revenue"`).

**404** when `query_id` is not in the registry.

**Response:**
```json
{
  "id": "revenue",
  "sql": "SELECT ...",
  "name": "Revenue",
  "tables": ["orders"],
  "columns": [{ "table": "orders", "column": "amount" }],
  "outputs": ["revenue"]
}
```

---

## Inter-model dependency DAG

### `GET /api/v1/lineage/dag`

Return the full inter-model dependency DAG covering all registered queries,
metrics, and bare physical tables. Edges flow from producer to consumer
(`A→B` means B depends on A).

**Response:**
```json
{
  "nodes": [
    {
      "id": "orders",
      "type": "table",
      "name": "orders",
      "tables": [],
      "outputs": []
    },
    {
      "id": "revenue",
      "type": "query",
      "name": "Revenue",
      "tables": ["orders"],
      "outputs": ["revenue"],
      "columns": [{ "table": "orders", "column": "amount" }]
    },
    {
      "id": "revenue_metric",
      "type": "metric",
      "name": "Revenue Metric",
      "tables": ["orders"],
      "outputs": ["revenue", "region"]
    }
  ],
  "edges": [
    { "from": "orders", "to": "revenue", "via": "orders" },
    { "from": "orders", "to": "revenue_metric", "via": "orders" }
  ]
}
```

Node types: `"query"`, `"metric"`, `"table"` (bare physical leaf).

Each edge: `{ "from": id, "to": id, "via": table_or_output_name }`.

### `GET /api/v1/lineage/dag/{node_id}?hops=N`

Return the upstream and downstream neighbourhood of a single DAG node.

**Path params:** `node_id` — id of a query, metric, or table node.
**Query params:** `hops` — traversal depth (default `3`, max `20`).

**404** when `node_id` is not in the DAG.

**Response:**
```json
{
  "node_id": "revenue_metric",
  "node": {
    "id": "revenue_metric",
    "type": "metric",
    "name": "Revenue Metric",
    "tables": ["orders"],
    "outputs": ["revenue"]
  },
  "hops": 3,
  "upstream": ["orders"],
  "downstream": []
}
```

---

## Metric lineage

### `GET /api/v1/metrics/{id}/lineage`

Return the full lineage for a single governed metric: input columns, upstream
source tables, measure definition, and any derived measure formulas.

**Response:**
```json
{
  "metric_id": "revenue",
  "name": "Revenue",
  "measure": { "name": "revenue", "agg": "sum", "expr": "amount" },
  "formula": [
    { "name": "margin", "formula": "revenue - cost" }
  ],
  "input_columns": [
    { "table": "orders", "column": "amount" }
  ],
  "upstream": ["orders"]
}
```

When the metric's SQL cannot be parsed an `"error"` field is included alongside
the other fields.

---

## Flow / notebook cell lineage

### `GET /api/v1/lineage/flow/{flow_id}`

Return column-level lineage for a persisted notebook flow. Loads the stored
FlowSpec, builds cross-cell column lineage, and returns nodes + edges + a
column-flow map.

**404** when `flow_id` is not found.

**Response:**
```json
{
  "flow_id": "uuid",
  "issues": [],
  "lineage": {
    "nodes": [...],
    "edges": [...],
    "column_flow": { ... }
  }
}
```

When the spec has hard validation errors `lineage` is `null` and `issues`
lists the problems.

### `POST /api/v1/lineage/plan`

Ephemeral plan-before-apply gate. Accepts a FlowSpec and a `changed_cell_key`,
validates the spec, and returns which downstream cells would be affected.
**Nothing is persisted.**

**Request body:**
```json
{
  "spec": { "version": 1, "name": "my_flow", "tasks": [...] },
  "changed_cell_key": "transform"
}
```

**Response:**
```json
{
  "valid": true,
  "issues": [],
  "lineage": { "nodes": [...], "edges": [...] },
  "downstream_impact": ["export", "load"]
}
```

### `POST /api/v1/lineage/cell`

Ephemeral column lineage for a single ad-hoc notebook cell. Accepts raw SQL
plus optional upstream cell SQL and returns column-level edges. **Nothing is
stored.**

**Request body:**
```json
{
  "sql": "SELECT o.amount, p.category FROM orders o JOIN products p ON ...",
  "dialect": "duckdb",
  "cell_key": "transform",
  "upstream_cells": {
    "load": "SELECT * FROM raw.orders"
  }
}
```

**Response:**
```json
{
  "cell_key": "transform",
  "edges": [
    {
      "output_col": "amount",
      "from_table": "orders",
      "from_col": "amount",
      "source_name": "o"
    }
  ]
}
```

---

## Cross-model column lineage

`resolve_column_lineage` walks the inter-model DAG *upstream* starting from a given node and column, tracing column provenance across model layers — resolving aliases and renames at each hop.

This is used internally by the lineage panel and by the auto-rebuild hook to understand which specific columns flow through each model boundary.

### Algorithm

At each model node, the resolver:

1. Parses the node's SQL to build a column-alias map.
2. Maps the current column name to its source column in the upstream model (handling `col AS alias` renames).
3. If the model uses `SELECT *`, falls back to the table-level edge (column name assumed unchanged) and marks the hop `select_star: true`.
4. Stops at a physical source table (`type: "table"`) or when `max_hops` is reached (ceiling: 20).

### Result shape

Each hop is:

```json
{
  "node":        "transform_cell",   // DAG node id
  "column":      "revenue",          // column name at this node
  "alias":       true,               // true when the name differs from the previous hop
  "select_star": false               // true when this hop was a SELECT * pass-through
}
```

The list is ordered from the starting node back to the physical source.

---

## Lineage-driven auto-rebuild

When a materialized model flow completes successfully, Nubi can automatically enqueue its downstream dependent flows — so you never have to manually chain schedules.

**Opt-in only.** Set `runtime_config.auto_rebuild_downstream = true` on the upstream flow. Existing flows are unaffected by default.

```jsonc
// flow.py
{
  "runtime_config": {
    "auto_rebuild_downstream": true
  }
}
```

### How it works

1. On flow-run success, Nubi looks up the flow's `runtime_config.auto_rebuild_downstream` flag.
2. If set, it walks the lineage DAG downstream (up to 20 hops) to find dependent flows in the same org.
3. For each downstream flow that also has `auto_rebuild_downstream: true`, a new flow run is enqueued.

### Guards

| Guard | Detail |
|-------|--------|
| **Opt-in** | Only active when `auto_rebuild_downstream = true` is set on the upstream flow |
| **Org-scoped** | Only enqueues flows in the same org |
| **Cycle-safe** | A `_visited` set of flow ids prevents circular trigger chains |
| **Storm-safe** | A module-level debounce set prevents the same downstream from being enqueued twice for the same trigger event |
| **Fan-out cap** | At most `_FLOWS_TRIGGER_MAX_FANOUT` downstream flows per trigger |
| **Success-only** | Only fires when the upstream run state is `success` |
| **Best-effort** | Any error is caught and logged; never raises or fails the upstream run |
```
