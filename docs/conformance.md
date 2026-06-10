# M1-C Conformance Suite

> **Status: frozen (Wave M1-C).** Every executor — Python today, Rust/WASM
> tomorrow — must pass this suite unchanged before it is considered
> production-ready.

The conformance suite is the golden-fixture safety net for Nubi's query
pipeline. It freezes `(sql, claims, seed-data) → Arrow schema + rows +
cache_key` triples and asserts byte-identical results on every CI run.

---

## What the suite guarantees

Each conformance case exercises four independent properties:

| Property | What is checked |
|---|---|
| **Cache-key stability** | `plan(sql, claims).cache_key` must equal the frozen hex literal in `cases.py`. Planner or algorithm drift fails CI immediately. |
| **Arrow schema** | Column names and Arrow type strings must match `expected_schema` exactly. |
| **Row correctness** | Rows (order-normalised, float-tolerant `rel=1e-9`) must match `expected_rows` exactly. |
| **IPC round-trip** | `table_to_ipc_bytes` → `pyarrow.ipc.open_stream` → identical schema and rows. Validates the `Content-Type: application/vnd.apache.arrow.stream` wire format used by the query endpoint. |

A fifth test class (`TestCacheKeySpecVectors`) re-runs the four vectors from
[`/docs/cache-key-spec`](/docs/cache-key-spec) against the live
`compute_cache_key` function. Algorithm and spec must stay in sync; either
drifting independently breaks CI.

---

## RLS security regression guard

The `rls_tenant_filter` case and `TestRLSSecurityGuard` are the multi-tenant
security regression guard.

The guard proves three things simultaneously:

1. The planner **injects** `tenant_id = 'acme'` as an AST-level predicate
   (never string concatenation) into the rewritten SQL.
2. The executor returns **only** the 3 `acme` rows.
3. The 3 `globex` rows (ids 4, 5, 6) are **absent** from the result.

If the planner ever stops injecting the predicate — for example, after a
refactor — `globex` rows appear and this test fails before the change can ship.

> The connector is the only trust boundary: anything a user may not see must be
> filtered before the Arrow buffer leaves the connector.

---

## Running the suite

From the `backend/` directory:

```bash
# All conformance tests (fast, no network):
python -m pytest tests/conformance -q

# Verbose — shows individual case names:
python -m pytest tests/conformance -v

# Single case:
python -m pytest tests/conformance -k rls_tenant_filter -v
```

No network access is required. All data is seeded from an in-memory DuckDB
connector (the `seeded_connector` fixture in `conftest.py`).

---

## Suite layout

```
backend/tests/conformance/
├── __init__.py
├── conftest.py          # seeded_connector fixture (in-memory DuckDB, deterministic data)
├── cases.py             # CONFORMANCE_CASES — frozen golden literals
└── test_conformance.py  # TestConformance, TestRLSSecurityGuard, TestCacheKeySpecVectors
```

### Test classes

| Class | Scope | Purpose |
|---|---|---|
| `TestConformance` | parametrised over `CONFORMANCE_CASES` | cache key, schema, rows, IPC round-trip |
| `TestRLSSecurityGuard` | single test | explicit absence assertion for globex rows |
| `TestCacheKeySpecVectors` | 4 tests | spec doc ↔ live `compute_cache_key` parity |

---

## Seed data

Both tables are registered via `DuckDBConnector.register()` in `conftest.py`
as frozen PyArrow literals — no files, no network.

**`users(id int32, tenant_id string, name string, age int32)`** — 6 rows:

| id | tenant_id | name  | age |
|----|-----------|-------|-----|
| 1  | acme      | Alice | 30  |
| 2  | acme      | Bob   | 25  |
| 3  | acme      | Carol | 35  |
| 4  | globex    | Dave  | 28  |
| 5  | globex    | Eve   | 42  |
| 6  | globex    | Frank | 31  |

**`orders(id int32, tenant_id string, amount float64)`** — 5 rows:

| id | tenant_id | amount |
|----|-----------|--------|
| 1  | acme      | 99.99  |
| 2  | acme      | 149.50 |
| 3  | globex    | 200.00 |
| 4  | globex    | 75.25  |
| 5  | acme      | 50.00  |

---

## Conformance cases

Full 64-character cache keys live in `tests/conformance/cases.py`. The
abbreviated values here are for quick reference only.

| id | sql | claims | cache_key prefix |
|---|---|---|---|
| `plain_select_all` | `SELECT * FROM users` | `{}` | `7db28a41…` |
| `projection_id_name` | `SELECT id, name FROM users` | `{}` | `2da34f05…` |
| `rls_tenant_filter` | `SELECT * FROM users` | `{"policies": {"tenant_id": "acme"}}` | `44b22d64…` |
| `aggregate_group_by_tenant` | `SELECT tenant_id, COUNT(*) AS cnt, AVG(age) AS avg_age FROM users GROUP BY tenant_id` | `{}` | `5c7377c9…` |

The `claims` field uses the `policies` key (matching the JWT token claim name).
The cache-key algorithm maps the `policies` sub-object to the `rls_claims`
field in the canonical JSON — see [`/docs/cache-key-spec`](/docs/cache-key-spec)
for the full algorithm.

---

## Adding a case

1. Write the `(sql, claims)` pair you want to freeze.
2. Run it against the seeded connector to capture the actual output:

```python
from app.connectors.planner import plan
from app.connectors.duckdb_conn import DuckDBConnector
import pyarrow as pa

conn = DuckDBConnector()
conn.register({"users": _USERS, "orders": _ORDERS})  # from conftest.py
p = plan(sql, claims)
r = conn.execute(p)
print("cache_key:", p.cache_key)
print("schema:", {f.name: str(f.type) for f in r.schema})
print("rows:", r.to_pydict())
```

3. Paste the output as a new entry in `CONFORMANCE_CASES` in `cases.py`.
4. Confirm the new case passes:

```bash
python -m pytest tests/conformance -q
```

Never hand-write hash values or row data — always run the code and paste
the output.

---

## Future executor requirement

A future Rust or WASM executor must pass this suite **unchanged** — same inputs,
same frozen expected outputs — before it can replace the Python executor:

- Cache keys must be byte-identical (see [`/docs/cache-key-spec`](/docs/cache-key-spec)
  for the algorithm and Rust pseudocode).
- Arrow schemas and rows must match every case in `CONFORMANCE_CASES`.
- The IPC round-trip test validates the wire format independently of executor
  language.

Run the new executor in shadow mode, diff its output against Python case by
case, and cut traffic only once every case is green.
