# Nubi Conformance Suite

> `tests/conformance/` — M1-C golden-fixture tests

The conformance suite is the canonical correctness contract for the Nubi query
pipeline.  It verifies that the planner, cache-key algorithm, executor, and
wire format all produce stable, known-good outputs against frozen golden fixtures.

---

## What the suite verifies

For every case in `tests/conformance/cases.py`:

| Check | What it asserts |
|-------|----------------|
| **Cache key** | `plan(sql, claims).cache_key` equals the frozen `expected_cache_key` literal. Any drift in the planner or cache-key algorithm immediately breaks this test. |
| **Arrow schema** | The result table's column names and Arrow type strings match `expected_schema` exactly. |
| **Rows** | Result rows (as plain Python dicts) match `expected_rows` after order-normalisation. Floating-point values use `pytest.approx(rel=1e-9)`. |
| **IPC round-trip** | `table_to_ipc_bytes` → parse → identical schema and rows. Validates the wire format used by the query endpoint. |
| **RLS security guard** | For `rls_tenant_filter`: asserts that `globex` rows are entirely absent when `tenant_id=acme` is the active policy. |
| **Cache-key spec vectors** | The four test vectors in `docs/cache-key-spec.md` are re-computed live and compared to the spec's expected values. |

---

## Registered conformance cases

| ID | SQL | RLS claims | Purpose |
|----|-----|-----------|---------|
| `plain_select_all` | `SELECT * FROM users` | none | Baseline: all rows returned, schema correct |
| `projection_id_name` | `SELECT id, name FROM users` | none | Column projection pushdown |
| `rls_tenant_filter` | `SELECT * FROM users` | `tenant_id=acme` | Multi-tenant security regression guard |
| `aggregate_group_by_tenant` | `SELECT tenant_id, COUNT(*), AVG(age) FROM users GROUP BY tenant_id` | none | Aggregate pushdown + float precision |

Seed data is defined in `tests/conformance/conftest.py` (the `seeded_connector`
fixture).  The seed is a 6-row `users` table with 3 `acme` rows and 3 `globex`
rows.

---

## Running the suite

```
cd backend
python -m pytest tests/conformance -q
```

No network access required.  All data is in-memory (DuckDB).

---

## The RLS security regression guard

The `rls_tenant_filter` case is the multi-tenant security regression guard
described in ROADMAP §3.1 rule 4.

If the planner ever stops injecting the RLS predicate into the SQL AST, the
`globex` rows (ids 4, 5, 6) will appear in the result and the conformance test
fails immediately.  The test explicitly checks:

1. The rewritten SQL contains `tenant_id` and the literal value `acme`.
2. The result contains exactly 3 rows.
3. Every row has `tenant_id = 'acme'`.
4. None of the `globex` row ids (4, 5, 6) appear in the result.

---

## Rust / WASM executor conformance requirement

A future Rust or WASM executor MUST pass this suite unchanged — same SQL
inputs, same RLS claims, same expected cache keys and rows — before it can be
considered a valid replacement for the Python executor.

The cache-key algorithm is specified in `docs/cache-key-spec.md` with four test
vectors that the Rust executor must reproduce byte-for-byte.

---

## Adding a new conformance case

1. Write and run the query against the seed data:
   ```python
   from app.connectors.planner import plan
   from app.connectors.duckdb_conn import DuckDBConnector
   # ... seed the connector ...
   p = plan(sql, claims)
   r = conn.execute(p)
   print(p.cache_key, r.to_pydict())
   ```
2. Copy the cache key and rows into a new entry in `CONFORMANCE_CASES` in
   `tests/conformance/cases.py`.
3. Run the suite to confirm the new case passes.

Do not edit the frozen `expected_cache_key` values manually.  They are the
contract — if the algorithm changes, all values must be recomputed from code.
