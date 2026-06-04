# Nubi Connector Authoring Guide

> ROADMAP §4.1 — "SQL first, flexible beyond"

This document explains how to write a Nubi connector, the capability contract
every connector must honour, and the security rules that govern Row-Level Security
enforcement for non-SQL sources.

---

## 1. Connector tiers

Nubi organises data sources into three tiers based on how much of the query
pipeline they can handle natively:

| Tier | Examples | Push-down | RLS enforcement |
|------|----------|-----------|-----------------|
| **SQL-first** | Postgres, DuckDB | Full (predicates, projection, limit) | Predicate injection into SQL AST |
| **API / function** | REST/JSON, Python functions | None | Post-fetch Python filtering (`apply_rls_postfetch`) |
| **NoSQL** | — | — | **Out of scope.** No NoSQL connector ships with Nubi. See §6. |

The planner inspects the capability flags to decide which operations it can push
down.  Operations that cannot be pushed down are applied post-fetch in Python.

---

## 2. The 7-flag capability contract

Every connector must implement `capabilities() -> dict[str, bool]` returning
exactly these seven keys:

| Flag | Meaning |
|------|---------|
| `native_arrow` | Returns data as Arrow IPC natively (e.g. ADBC), avoiding row-by-row conversion |
| `predicate_pushdown` | Can push WHERE predicates to the source |
| `projection_pushdown` | Can push column selection to the source |
| `partition_pushdown` | Can route queries to specific shards/partitions |
| `predicate_rls` | Supports Row-Level Security (see §4 below) |
| `column_masking` | Can mask/redact column values before they leave the connector |
| `streaming_cdc` | Can stream Change-Data-Capture events |

All seven keys must be present and must be `bool`.  `Connector.validate_capabilities()`
is called at construction time to enforce this; misconfigured connectors fail fast.

---

## 3. Writing a connector

### Option A: subclass `Connector`

```python
from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.errors import AppError
import pyarrow as pa

class MySourceConnector(Connector):
    def __init__(self, config: dict) -> None:
        self._url = config["url"]
        self.validate_capabilities()   # call this in every __init__

    def capabilities(self) -> dict[str, bool]:
        return {
            "native_arrow":        False,
            "predicate_pushdown":  False,
            "projection_pushdown": False,
            "partition_pushdown":  False,
            "predicate_rls":       True,   # we will call apply_rls_postfetch
            "column_masking":      False,
            "streaming_cdc":       False,
        }

    def execute(self, plan: PhysicalPlan) -> pa.Table:
        raw = self._fetch_all_rows()            # your source logic
        # REQUIRED: apply post-fetch RLS before returning
        from app.connectors.sdk import (
            apply_rls_postfetch,
            apply_projection_postfetch,
            apply_limit_postfetch,
        )
        table = apply_rls_postfetch(table, plan.rls_claims.get("policies", {}))
        table = apply_projection_postfetch(table, plan.projection)
        return table

    def execute_stream(self, plan: PhysicalPlan):
        yield from self.execute(plan).to_batches()
```

### Option B: use `FunctionConnector` (simplest)

For one-off connectors or mock fixtures, wrap any callable:

```python
from app.connectors.sdk import FunctionConnector
import pyarrow as pa

def my_fn(plan):
    return pa.table({"tenant_id": ["acme", "globex"], "value": [1, 2]})

conn = FunctionConnector(
    fn=my_fn,
    capabilities={
        "native_arrow": True, "predicate_pushdown": False,
        "projection_pushdown": False, "partition_pushdown": False,
        "predicate_rls": True, "column_masking": False, "streaming_cdc": False,
    },
)
# FunctionConnector.execute() automatically calls apply_rls_postfetch,
# apply_projection_postfetch, and apply_limit_postfetch based on the caps.
```

`FunctionConnector` is the "return an Arrow table" flexibility valve.  The fn
must return the **raw** table (all rows, all columns); post-fetch guards are
applied automatically by `FunctionConnector.execute()`.

### Option C: use `HttpJsonConnector` for REST/JSON APIs

```python
from app.connectors.http_json import HttpJsonConnector

conn = HttpJsonConnector({
    "url": "https://api.example.com/records",
    "record_path": "data.items",   # optional: dot-path to the records list
    "headers": {"Authorization": "Bearer <token>"},  # optional
})
```

`HttpJsonConnector` fetches via HTTP GET, navigates `record_path` to the records
list, normalises it to Arrow (union of all keys; missing keys → null), and then
applies the full post-fetch guard sequence automatically.

---

## 4. Post-fetch RLS: the REQUIREMENT for non-pushdown sources

> **Security rule:** A connector whose `capabilities()` returns
> `predicate_pushdown=False` and `predicate_rls=True` **MUST** call
> `apply_rls_postfetch(table, plan.rls_claims.get('policies', {}))` before
> returning any data.  The browser MUST never be trusted to filter rows.

### Why post-fetch?

SQL connectors (Postgres, DuckDB) inject RLS predicates directly into the SQL
AST using sqlglot, so the source itself enforces the policy.  Non-SQL sources
(REST APIs, Python functions) cannot receive SQL predicates.  Instead, they
fetch all rows and Nubi filters them in Python — server-side, before the data
ever leaves the connector boundary.

### `apply_rls_postfetch` — fail-closed design

`apply_rls_postfetch(table, policies)` filters an Arrow table by equality
policies using `pyarrow.compute`:

```python
from app.connectors.sdk import apply_rls_postfetch
filtered = apply_rls_postfetch(table, {"tenant_id": "acme"})
```

**Fail-closed:** if a policy references a column absent from the table, the
function raises `AppError("rls_column_missing", 403)` rather than returning
unfiltered data.  This is a deliberate security choice:

- A source that cannot honour a policy MUST NOT return data.
- Silently ignoring a missing column would allow tenant data cross-contamination,
  a critical security failure.
- 403 Forbidden is the correct status: the caller is authenticated but the source
  cannot satisfy the authorisation constraint.

### Post-fetch helper sequence for non-pushdown connectors

Apply these helpers in order:

```python
from app.connectors.sdk import (
    apply_rls_postfetch,
    apply_projection_postfetch,
    apply_limit_postfetch,
)

# 1. RLS first — never return more rows than authorised.
table = apply_rls_postfetch(table, plan.rls_claims.get("policies", {}))

# 2. Projection — narrow columns after RLS (so RLS columns are still present during filtering).
table = apply_projection_postfetch(table, plan.projection)

# 3. Limit — best-effort row cap.
# (FunctionConnector / HttpJsonConnector extract this from plan.params automatically.)
```

`apply_projection_postfetch` uses intersection semantics: columns requested in
`plan.projection` that are absent from the table are silently ignored.

---

## 5. Registering a connector

Add the connector factory to `_bootstrap()` in `app/connectors/registry.py`:

```python
from app.connectors.my_source import MySourceConnector
registry.register("my_source", lambda config: MySourceConnector(config))
```

Pre-registered built-in connectors:

| Type | Class | Notes |
|------|-------|-------|
| `postgres` | `PostgresConnector` | ADBC-backed, native Arrow, full push-down + RLS |
| `duckdb` | `DuckDBConnector` | Local DuckDB, used for fixtures and conformance |
| `http_json` | `HttpJsonConnector` | REST/JSON API; post-fetch RLS (fail-closed) |

These are the only connectors that ship with Nubi.  All three can enforce RLS
(Postgres and DuckDB via SQL AST predicate injection; `http_json` via
`apply_rls_postfetch`).  NoSQL connectors are not registered and are out of
scope — see §6.

---

## 6. NoSQL is out of scope — capability-gated RLS enforcement

> **NoSQL connectors are not shipped with Nubi.**  The MongoDB stub has been
> removed.  This section explains the policy and what would be required to add
> a NoSQL connector in the future.

### Why NoSQL is excluded

Nubi ships only data sources that can enforce Row-Level Security:

| Source | RLS mechanism |
|--------|--------------|
| `postgres` | SQL AST predicate injection via sqlglot |
| `duckdb` | SQL AST predicate injection via sqlglot |
| `http_json` | Post-fetch `apply_rls_postfetch` (fail-closed Python filter) |

NoSQL engines (MongoDB, DynamoDB, etc.) cannot receive SQL predicates and do
not have a standardised, audited Nubi RLS translator.  Allowing them would
require shipping an unaudited policy translator that, if wrong, could leak
cross-tenant data.

### Capability-gated RLS: `predicate_rls=False` is refused at the route

A connector that declares `predicate_rls=False` cannot satisfy RLS-bearing
queries.  The route layer enforces this: if the planner is given a query with
active `rls_claims.policies` and the selected connector returns
`predicate_rls=False`, the request is refused with **501 Not Implemented**
before any data is fetched.

As a defence-in-depth measure, any connector with `predicate_rls=False` MUST
also refuse secured queries in its own `execute()`:

```python
def execute(self, plan: PhysicalPlan) -> pa.Table:
    if plan.rls_claims.get("policies"):
        raise AppError(
            "source_unsupported_rls",
            "This source cannot enforce RLS.  "
            "Secure queries require predicate_rls=True.",
            status=501,
        )
    # ... unsecured fetch ...
```

A `predicate_rls=False` connector is only acceptable for fully **unsecured**
queries (no active policies) — for example, internal analytics over public data
with no tenant isolation requirement.

### Adding a NoSQL connector in the future

A NoSQL connector could be added later **only if** it implements its own RLS.
The minimum bar:

1. A translator from Nubi's `{column: value}` policy format to the native query
   language (e.g. MongoDB `$match`, DynamoDB `FilterExpression`).
2. Verification that the filter runs *before* any projection or limit stage in
   the native engine.
3. An audit confirming the translated filter is semantically equivalent to the
   SQL equality policy (no injection, no bypass).
4. Security team sign-off before `predicate_rls=True` is declared.

Until all four steps are complete, do not register a NoSQL connector in
`registry.py`.

---

## 7. Testing a new connector

A new connector should have tests covering:

1. **RLS filtering**: a two-tenant dataset with a single-tenant plan → only the
   authorised tenant's rows are returned; the other tenant is absent.
2. **Fail-closed**: a policy on a column absent from the source → 403
   `rls_column_missing`, not unfiltered data.
3. **Projection**: `plan.projection` narrows columns correctly.
4. **Error path**: source unavailable → appropriate `AppError` code and status.
5. **Registry**: `get_connector_registry().get('<type>')` returns a working factory.
6. **Conformance**: run `pytest tests/conformance` to ensure existing behaviour
   is not broken.

See `tests/test_http_connector.py` for an example of testing a post-fetch RLS connector.
