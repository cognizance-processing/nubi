# Nubi M1-C Conformance Suite

> **Status:** Frozen (Wave M1-C).  Tests must pass before any executor
> (Python or Rust/WASM) is considered production-ready.

---

## 1. What the suite guarantees

The conformance suite is the **carve-out safety artifact** described in
ROADMAP §3.1 rule 4.  It freezes golden fixtures of the form:

```
(sql, claims, seed-data) → expected Arrow schema + expected rows + expected cache_key
```

and asserts that the running implementation produces byte-identical results
every time.  It gives four concrete guarantees:

| Guarantee | How it is tested |
|---|---|
| **Cache-key stability** | `plan(sql, claims).cache_key` must equal the frozen hex literal in `cases.py`. Any drift in the planner or cache-key algorithm breaks CI immediately. |
| **Arrow schema correctness** | Column names and Arrow type strings must match `expected_schema` exactly. |
| **Row correctness** | Rows (order-normalised, float-tolerant) must match `expected_rows` exactly. |
| **Wire format (IPC round-trip)** | `table_to_ipc_bytes` → `pyarrow.ipc.open_stream` → identical schema and rows. This validates the `Content-Type: application/vnd.apache.arrow.stream` response used by the query endpoint. |

A fifth test class (`TestCacheKeySpecVectors`) links the spec document
(`docs/cache-key-spec.md`) to the live `compute_cache_key` implementation:
if the algorithm drifts from the spec's test vectors, CI fails and a
conscious version bump + spec update is required.

---

## 2. RLS security regression guard

The `rls_tenant_filter` conformance case and the dedicated
`TestRLSSecurityGuard` class are the **multi-tenant security regression
guard** (ROADMAP §5.2 and §5.3).

The guard proves:

1. The planner **injects** the `tenant_id = 'acme'` predicate into the
   rewritten SQL (AST-level, never string-concat).
2. The executor returns **only** the 3 `acme` rows.
3. The 3 `globex` rows (ids 4, 5, 6) are **absent** from the result.

If the planner ever stops injecting the predicate — e.g. due to a refactor —
`globex` rows will appear and this test will fail before the change can ship.

> **The connector is the only trust boundary** (ROADMAP §5.3): anything a
> user may not see must be filtered before the Arrow buffer leaves the
> connector.  This test guards that invariant.

---

## 3. How to run

From the `backend/` directory:

```bash
# Run only the conformance suite (fast, no network):
python -m pytest tests/conformance -q

# Run with verbose output to see individual case names:
python -m pytest tests/conformance -v

# Run a single case:
python -m pytest tests/conformance -k rls_tenant_filter -v
```

The suite requires **no network access**.  All data is seeded from an
in-memory DuckDB connector (the `seeded_connector` fixture in
`tests/conformance/conftest.py`).

---

## 4. Suite structure

```
backend/tests/conformance/
├── __init__.py
├── conftest.py          # seeded_connector fixture (DuckDB, deterministic data)
├── cases.py             # CONFORMANCE_CASES list with frozen literals
└── test_conformance.py  # pytest test classes
```

### Seed data

Two tables registered via `DuckDBConnector.register()`:

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

### Conformance cases

| id | sql | claims | frozen cache_key |
|---|---|---|---|
| `plain_select_all` | `SELECT * FROM users` | `{}` | `7db28a41...` |
| `projection_id_name` | `SELECT id, name FROM users` | `{}` | `2da34f05...` |
| `rls_tenant_filter` | `SELECT * FROM users` | `{"policies": {"tenant_id": "acme"}}` | `44b22d64...` |
| `aggregate_group_by_tenant` | `SELECT tenant_id, COUNT(*) AS cnt, AVG(age) AS avg_age FROM users GROUP BY tenant_id` | `{}` | `5c7377c9...` |

Full 64-character cache keys are in `tests/conformance/cases.py`.

---

## 5. Adding a new conformance case

1. Write the `(sql, claims)` pair you want to freeze.
2. Run it against the seeded connector to capture the actual output:

```python
from app.connectors.planner import plan
from app.connectors.duckdb_conn import DuckDBConnector
import pyarrow as pa

conn = DuckDBConnector()
conn.register({"users": <seed_table>, "orders": <seed_table>})
p = plan(sql, claims)
r = conn.execute(p)
print("cache_key:", p.cache_key)
print("rows:", r.to_pydict())
print("schema:", {f.name: str(f.type) for f in r.schema})
```

3. Paste the output as a new entry in `CONFORMANCE_CASES` in `cases.py`.
4. Run `python -m pytest tests/conformance -q` to confirm the new case passes.

> Do **not** hand-write hashes or row values — always run the code and
> paste the output.

---

## 6. Rust/WASM executor requirement

> **A future Rust executor MUST pass this suite unchanged before it can
> replace the Python executor.**

Per ROADMAP §3.1 rules 4 and 8:

- The conformance suite is the acceptance gate for shadow-mode migration.
- The Rust executor must produce **byte-identical** cache keys (see
  `docs/cache-key-spec.md` for the algorithm and Rust pseudocode).
- The Rust executor must return **identical Arrow schemas and rows** for
  every case in `CONFORMANCE_CASES`.
- The IPC round-trip test validates the wire format independently of the
  executor language.

The suite is the **shared contract** that makes the Python → Rust cutover
zero-risk: run Rust in shadow, diff its output against Python, and only
cut traffic once every conformance case is green.

---

## 7. Cache-key spec link

The `TestCacheKeySpecVectors` class in `test_conformance.py` re-runs all
four test vectors from `docs/cache-key-spec.md` against the live
`compute_cache_key` function.  This means:

- Editing the algorithm without updating the spec breaks CI.
- Updating the spec without changing the algorithm breaks CI.
- Any version bump in `cache_key.py:CACHE_KEY_VERSION` must be accompanied
  by new spec vectors and corresponding conformance case updates.
