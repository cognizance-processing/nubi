# Data Health

Nubi tracks data freshness and scores dataset health across three weighted
dimensions. All endpoints are org-scoped via `verified_identity`; a dataset
that belongs to a different org returns 404, never 403.

Routes: `GET /api/v1/health/...`

---

## Freshness registry

Pre-computed freshness records are maintained asynchronously (by
`app.health.listener`) and read from the `dataset_freshness` table.
Reads are single indexed-row lookups — **no live computation** on the request
path. **Read SLO target: < 5 ms p99** for single-dataset lookups.

### `GET /api/v1/health/freshness`

Return the pre-computed freshness row for **every** dataset in the caller's org.
O(index scan on `org_id`).

**Response:**
```json
{
  "org_id": "uuid",
  "datasets": [
    {
      "dataset_key": "raw/orders",
      "status": "fresh",
      "last_success_at": "2026-06-24T08:00:00+00:00",
      "expected_interval_s": 3600
    },
    {
      "dataset_key": "raw/sessions",
      "status": "stale",
      "last_success_at": "2026-06-22T10:00:00+00:00",
      "expected_interval_s": 3600
    }
  ]
}
```

### `GET /api/v1/health/freshness/{dataset_key}`

Return the pre-computed freshness row for **one** dataset.
Single primary-key lookup: O(1). Suitable as a low-latency UX gate (e.g.
"is this dataset stale before we run a report?").

**Path params:** `dataset_key` — e.g. `raw/orders`, `model/revenue`.
URL-encode slashes when calling from the shell.

**404** when no freshness record exists for the key in the caller's org.

**Response:** same shape as a single element from the `datasets` array above.

```json
{
  "dataset_key": "raw/orders",
  "status": "fresh",
  "last_success_at": "2026-06-24T08:00:00+00:00",
  "expected_interval_s": 3600
}
```

---

## Health scoring

### `GET /api/v1/health/score?dataset_key=`

Compute weighted health scores (0–100) for all datasets in the org, or for a
single dataset when `?dataset_key=` is provided.

**Query params:** `dataset_key` (optional) — filter to a single dataset.

### Scoring model

```
score = 100 × (
    w_freshness     × freshness_score     +
    w_completeness  × completeness_score  +
    w_availability  × availability_score
)
```

Default weights (configurable per-org in `health_score_config`):

| Dimension | Default weight | How computed |
|-----------|---------------|--------------|
| `freshness` | 0.50 | `"fresh"` → 1.0, `"stale"` → 0.0, `"unknown"` → excluded |
| `completeness` | 0.30 | Recent run success rate (last 20 runs): `successes / total`. `"unknown"` when no history. |
| `availability` | 0.20 | 1.0 if any successful run ever; 0.0 if none; `"unknown"` when no history. |

Unknown dimensions are excluded and their weight is redistributed proportionally
to known dimensions. When ALL dimensions are unknown the score is `null` and
`status` is `"unknown"`.

Grade mapping: A (≥ 90), B (≥ 75), C (≥ 60), D (≥ 40), F (< 40).

**Response (single dataset):**
```json
{
  "dataset_key": "raw/orders",
  "score": 87,
  "grade": "B",
  "dimensions": [
    {
      "name": "freshness",
      "score": 100,
      "status": "fresh",
      "reason": "Dataset is fresh.",
      "weight": 0.5
    },
    {
      "name": "completeness",
      "score": 90,
      "status": "ok",
      "reason": "18 / 20 recent runs succeeded.",
      "weight": 0.3
    },
    {
      "name": "availability",
      "score": 100,
      "status": "ok",
      "reason": "At least one successful run exists.",
      "weight": 0.2
    }
  ],
  "reasons": [
    "freshness: fresh",
    "completeness: 18/20 runs OK",
    "availability: ever-succeeded"
  ],
  "weights_used": { "freshness": 0.5, "completeness": 0.3, "availability": 0.2 }
}
```

**Response (all datasets):**
```json
{
  "org_id": "uuid",
  "datasets": [ /* array of per-dataset score objects */ ],
  "default_weights": { "freshness": 0.5, "completeness": 0.3, "availability": 0.2 }
}
```

---

## Health estate graph

### `GET /api/v1/health/estate`

Return a `source → raw → model → feature` flow map annotated with each node's
health and freshness status. Nodes are inferred from the freshness registry plus
the flow store.

Node types are inferred from naming conventions:

| Key prefix | Node type |
|------------|-----------|
| `source/`, `raw/`, `ingest/` | `"raw"` |
| `model/`, `transform/` | `"model"` |
| `metric/`, `feature/`, `agg/` | `"feature"` |
| anything else | `"source"` |

Edges are inferred from flow task order heuristics. When the full lineage DAG
is available (i.e. `GET /lineage/dag` is reachable), edges include
`lineage_confirmed: true` when a corresponding lineage edge exists in the DAG.

**Response:**
```json
{
  "org_id": "uuid",
  "nodes": [
    {
      "key": "raw/orders",
      "type": "raw",
      "status": "fresh",
      "last_success_at": "2026-06-24T08:00:00+00:00",
      "expected_interval_s": 3600
    },
    {
      "key": "model/revenue",
      "type": "model",
      "status": "stale",
      "last_success_at": "2026-06-22T10:00:00+00:00",
      "expected_interval_s": 86400
    }
  ],
  "edges": [
    {
      "source_key": "raw/orders",
      "target_key": "model/revenue",
      "flow_id": "uuid",
      "lineage_confirmed": true
    }
  ],
  "lineage_module_present": true
}
```
