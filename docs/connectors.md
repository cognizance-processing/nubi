# Connectors

Nubi connectors are the bridge between the query planner and your data warehouse. Every connector implements a 7-flag capability contract that tells the planner what operations can be pushed down — and whether the connector can enforce row-level security. Credentials are always encrypted at the application layer before reaching the database.

---

## Connector Tiers

| Tier | Examples | Push-down | RLS enforcement |
|------|----------|-----------|-----------------|
| **SQL-first** | Postgres, DuckDB | Full (predicates, projection, limit) | Predicate injection into SQL AST |
| **API / function** | REST/JSON, Python functions | None | Post-fetch Python filtering |
| **NoSQL** | — | — | Out of scope — no audited RLS translator ships with Nubi |

---

## Built-in Connectors

The registry (`_bootstrap` in `app/connectors/registry.py`) pre-registers:

| Type | Class | Driver | Notes |
|------|-------|--------|-------|
| `postgres` | `PostgresConnector` | ADBC (bundled) | Native Arrow, full push-down + RLS |
| `duckdb` | `DuckDBConnector` | `duckdb` (bundled) | Local DuckDB. Two modes: an in-memory DB for the built-in demo dataset / fixtures / conformance, **and** a real config-driven **read-only file-backed** source when the datastore config sets `database`/`path` to a `.duckdb` file (opened `read_only=True` with `enable_external_access=false`). |
| `http_json` | `HttpJsonConnector` | `httpx` (lazy) | REST/JSON API; post-fetch RLS (fail-closed). No push-down. |
| `mysql` | `MySQLConnector` | MySQL driver (optional) | Takes a `mysql://` DSN; the registry factory assembles one from the config parts (host/port/database/user/password, URL-encoded). |
| `mariadb` | `MariaDBConnector` | MySQL driver (optional) | Wire-compatible with MySQL; reuses the same DSN scheme. |
| `jdbc` | `JDBCConnector` | JVM + JDBC driver jar (optional) | Generic JDBC bridge (`jdbc_url` / `driver_class` / `jar_path`); driver import is lazy. |

**Optional drivers:** `postgres` (ADBC) and `duckdb` ship with the backend. `mysql`/`mariadb`
need their MySQL driver; `jdbc` needs a JVM + the relevant JDBC jar; `snowflake` needs
`snowflake-connector-python`; `bigquery` needs `google-cloud-bigquery`. All imports are lazy, so
the registry imports fine in a pure-Postgres/DuckDB environment and a connector only raises
`driver_unavailable` (500, with install guidance) if used without its driver.

> **`bigquery` / `snowflake`:** now registered in `_bootstrap` (`BigQueryConnector`,
> `SnowflakeConnector`), both returning native Arrow with `$N`→dialect param translation.
> `bigquery` reads a `service_account_json` secret (else ADC); `snowflake` takes
> account/user/password (+ warehouse/database/schema/role). Registered connector set is now
> **8**: postgres, duckdb, http_json, mysql, mariadb, jdbc, snowflake, bigquery.

### Demo Dataset (DuckDB fallback)

When no `datastore_id` is provided, queries run against a built-in in-memory DuckDB dataset:

```
demo(id INTEGER, name TEXT, value DOUBLE, active BOOLEAN)
```

| id | name | value | active |
|----|------|-------|--------|
| 1 | alpha | 1.1 | true |
| 2 | beta | 2.2 | false |
| 3 | gamma | 3.3 | true |
| 4 | delta | 4.4 | false |
| 5 | epsilon | 5.5 | true |

`SELECT * FROM demo` works out of the box with no configuration.

---

## The 7-Flag Capability Contract

Every connector implements `capabilities() -> dict[str, bool]` with exactly these keys:

| Flag | Meaning |
|------|---------|
| `native_arrow` | Returns Arrow IPC natively (e.g. ADBC), no row-by-row conversion |
| `predicate_pushdown` | Can push WHERE predicates to the source |
| `projection_pushdown` | Can push column selection to the source |
| `partition_pushdown` | Can route queries to specific shards/partitions |
| `predicate_rls` | Supports Row-Level Security (see below) |
| `column_masking` | Can mask/redact column values before they leave the connector |
| `streaming_cdc` | Can stream Change-Data-Capture events |

`Connector.validate_capabilities()` is called at construction time — misconfigured connectors fail fast.

### Capability-Gated RLS

A connector with `predicate_rls=False` is refused with **501 Not Implemented** when active `rls_claims.policies` are present. The route layer enforces this before any data is fetched — the connector's `execute()` is never called for unsecurable sources.

---

## Writing a Connector

### Option A — Subclass `Connector`

```python
from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
import pyarrow as pa

class MySourceConnector(Connector):
    def __init__(self, config: dict) -> None:
        self._url = config["url"]
        self.validate_capabilities()

    def capabilities(self) -> dict[str, bool]:
        return {
            "native_arrow":        False,
            "predicate_pushdown":  False,
            "projection_pushdown": False,
            "partition_pushdown":  False,
            "predicate_rls":       True,
            "column_masking":      False,
            "streaming_cdc":       False,
        }

    def execute(self, plan: PhysicalPlan) -> pa.Table:
        raw = self._fetch_all_rows()
        from app.connectors.sdk import (
            apply_rls_postfetch,
            apply_projection_postfetch,
        )
        table = apply_rls_postfetch(raw, plan.rls_claims.get("policies", {}))
        table = apply_projection_postfetch(table, plan.projection)
        return table
```

### Option B — `FunctionConnector` (simplest)

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
```

`FunctionConnector.execute()` automatically applies `apply_rls_postfetch`, `apply_projection_postfetch`, and `apply_limit_postfetch`.

### Option C — `HttpJsonConnector`

```python
from app.connectors.http_json import HttpJsonConnector

conn = HttpJsonConnector({
    "url":         "https://api.example.com/records",
    "record_path": "data.items",   # dot-path to the records list
    "headers":     {"Authorization": "Bearer <token>"},
})
```

Fetches via HTTP GET, navigates `record_path`, normalises to Arrow, and applies the full post-fetch guard sequence automatically.

---

## Post-Fetch RLS — the Security Rule

> A connector with `predicate_pushdown=False` and `predicate_rls=True` **must** call `apply_rls_postfetch` before returning any data. The browser is never trusted to filter rows.

Apply helpers in this order:

```python
from app.connectors.sdk import (
    apply_rls_postfetch,
    apply_projection_postfetch,
    apply_limit_postfetch,
)

# 1. RLS first — never return more rows than authorised
table = apply_rls_postfetch(table, plan.rls_claims.get("policies", {}))

# 2. Projection — narrow columns after RLS
table = apply_projection_postfetch(table, plan.projection)

# 3. Limit — best-effort row cap
```

**Fail-closed:** if a policy references a column absent from the table, `apply_rls_postfetch` raises `AppError("rls_column_missing", 403)`. Silently ignoring a missing column would allow cross-tenant data leakage.

---

## Registering a Connector

Add the factory to `_bootstrap()` in `app/connectors/registry.py`:

```python
from app.connectors.my_source import MySourceConnector
registry.register("my_source", lambda config: MySourceConnector(config))
```

---

## BYO Warehouse

Point at any Postgres-compatible warehouse (Neon, RDS, AlloyDB, etc.) by setting `DATABASE_URL` in your environment. The `PostgresConnector` uses ADBC for native Arrow output with no intermediate conversion.

For cloud warehouses not yet supported natively, use `HttpJsonConnector` or `FunctionConnector` to wrap any HTTP API or Python callable. Post-fetch RLS is enforced at the same security level as predicate pushdown for equality policies.

### Selecting a Datastore per Query

Pass `datastore_id` in the query request body to route a query to a specific registered datastore:

```json
POST /api/v1/query
{
  "query_id":     "revenue_by_month",
  "datastore_id": "ds-uuid"
}
```

The datastore is resolved from the `datastores` resource (org-scoped). The connectors REST
route stores the type under `config.connector_type`; the `/query` executor reads the type from
`config.type`. (A registered query may also carry its own bound `datastore_id`, in which case a
widget can send only `{query_id}` and still execute against the correct source — the request
body's `datastore_id` takes precedence when both are present.)

### Data Browser

The Data Browser lets you introspect a connector's tables and preview rows without writing SQL. The UI lives at `/connectors/:id/data` (`DataBrowser.jsx`); it lists tables, shows each table's columns and types, and streams a row sample as Arrow IPC. The backing endpoints (auth via Bearer token, org-scoped) are:

| Endpoint | Returns |
|----------|---------|
| `GET /api/v1/data/{datastore_id}/tables` | Tables (and schemas) discovered by introspection. |
| `GET /api/v1/data/{datastore_id}/tables/{table}/columns` | Column names + types for one table. |
| `GET /api/v1/data/{datastore_id}/tables/{table}/rows?limit=N` | A row sample (`limit` 1–5000, default 500) as Arrow IPC. |

The same paths without a `datastore_id` segment (`/data/tables`, `/data/tables/{table}/columns`, `/data/tables/{table}/rows`) target the built-in DuckDB demo dataset. A datastore belonging to a different org is treated as not-found, and table names are validated against the introspected list before interpolation (SQL-injection prevention).

---

## Connector REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/connectors` | Create a connector (config + encrypted secret). Returns 201. |
| `GET` | `/api/v1/connectors` | List connectors for the caller's org (no secrets returned). |
| `GET` | `/api/v1/connectors/{id}` | Fetch a single connector. |
| `PUT` | `/api/v1/connectors/{id}` | Update config and/or rotate the secret. |
| `DELETE` | `/api/v1/connectors/{id}` | Delete connector and secret blob. Returns 204. |
| `POST` | `/api/v1/connectors/{id}/test` | Structural check — verifies config + secret resolvable, no network socket opened. |

### Creating a Connector

```json
POST /api/v1/connectors
{
  "name": "prod-postgres",
  "type": "postgres",
  "config": {
    "host": "db.example.com",
    "port": 5432,
    "database": "analytics",
    "user": "readonly",
    "sslmode": "require"
  },
  "secret": {
    "password": "hunter2"
  }
}
```

The execution registry registers all eight types: `postgres`, `duckdb`, `http_json`, `mysql`,
`mariadb`, `jdbc`, `snowflake`, and `bigquery` (the last four via optional drivers — see the
built-in connectors note above).

Valid secret keys: `password`, `service_account_json`, `token`, `api_key`. Any of these in `config` is rejected with `422 Unprocessable Entity`.

### Test Probe Response

```json
{
  "ok": true,
  "checked": "config+secret resolved",
  "connector_id": "uuid",
  "type": "postgres",
  "layers": { "config": true, "secret": true }
}
```

---

## Network Mode and Bridges

Connectors that live inside a private network use two optional config fields:

| Field | Type | Meaning |
|-------|------|---------|
| `network_mode` | `string` | `"direct"` (default) — egress goes directly from the Nubi backend to the database. `"bridge"` — routes through the Nubi bridge agent identified by `bridge_id`. |
| `bridge_id` | `string (uuid)` | Reference to the bridge agent that proxies traffic. Stored in `datastores.config` (non-secret). |

When `network_mode="bridge"`, the query executor calls `resolve_network_async()` which opens a local TCP proxy through the bridge agent's WebSocket tunnel. See [Bridges](/docs/bridges) for the full bridge setup guide.

Other modes (`ssh_tunnel`, `psc`, `cloudsql_proxy`) are reserved for future use and currently return **501** when requested.
