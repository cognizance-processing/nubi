"""Data Browser endpoints — org-scoped, auth via current_user.

Endpoints
---------
GET  /data/tables                         → demo connector: list tables
GET  /data/{datastore_id}/tables          → list tables for a connector
GET  /data/tables/{table}/columns         → demo connector: column names + types
GET  /data/{datastore_id}/tables/{table}/columns → column names + types
GET  /data/tables/{table}/rows            → demo connector: Arrow IPC row sample
GET  /data/{datastore_id}/tables/{table}/rows?limit=N → Arrow IPC row sample

Security
--------
- All endpoints require a valid first-party Bearer token (``current_user``).
- The datastore row is fetched org-scoped; a row belonging to a different org
  is treated as not-found (no information leak).
- Table names used in SQL are validated against the introspected table list
  before being interpolated (SQL injection prevention).

The router self-registers on the shared ``api_router`` at import time so that
``main.py``'s ``include_router(api_router, prefix="/api/v1")`` picks it up
automatically — do NOT edit main.py.
"""

from __future__ import annotations

import re
from typing import Any

import pyarrow as pa
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.auth.deps import current_user
from app.connectors.arrow_io import ipc_stream_from_bytes, table_to_ipc_bytes
from app.connectors.duckdb_conn import DuckDBConnector
from app.connectors.plan import PhysicalPlan
from app.errors import AppError
from app.repos.provider import get_repo, Repo
from app.routes import api_router

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["data-browser"])

_ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"

# ---------------------------------------------------------------------------
# Demo DuckDB connector (module-level singleton, same seed as query.py)
# ---------------------------------------------------------------------------

_demo_connector: DuckDBConnector | None = None


def _get_demo_connector() -> DuckDBConnector:
    """Return (or create) the module-level demo DuckDB connector."""
    global _demo_connector
    if _demo_connector is None:
        conn = DuckDBConnector()
        demo_table = pa.table(
            {
                "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
                "name": pa.array(
                    ["alpha", "beta", "gamma", "delta", "epsilon"],
                    type=pa.string(),
                ),
                "value": pa.array([1.1, 2.2, 3.3, 4.4, 5.5], type=pa.float64()),
                "active": pa.array([True, False, True, False, True], type=pa.bool_()),
            }
        )
        conn.register({"demo": demo_table})
        _demo_connector = conn
    return _demo_connector


# ---------------------------------------------------------------------------
# Org resolution helper (mirrors connectors.py)
# ---------------------------------------------------------------------------


async def _get_user_org(user_id: str, repo: Repo) -> str:
    from app.db import fetchrow

    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)
        if org_id:
            return org_id
        raise AppError("org_not_found", "User has no org membership.", 404)

    row = await fetchrow(
        """
        SELECT org_id FROM org_members
        WHERE user_id = $1::uuid
        ORDER BY org_id
        LIMIT 1
        """,
        user_id,
    )
    if row is None:
        raise AppError("org_not_found", "User has no org membership.", 404)
    return str(row["org_id"])


# ---------------------------------------------------------------------------
# Connector resolution
# ---------------------------------------------------------------------------


def _build_duckdb_connector(cfg: dict[str, Any]) -> DuckDBConnector:
    """Build a DuckDB connector from datastore config (read-only file or memory)."""
    db_path = cfg.get("database") or cfg.get("path")
    if db_path and db_path != ":memory:":
        import duckdb as _duckdb

        _conn = _duckdb.connect(database=db_path, read_only=True)
        try:
            _conn.execute("SET enable_external_access=false")
        except Exception:
            pass
        return DuckDBConnector(_conn)
    return DuckDBConnector()


def _make_plan(sql: str) -> PhysicalPlan:
    """Wrap a bare SQL string into a minimal PhysicalPlan."""
    return PhysicalPlan(sql=sql, params=[], cache_key="", rls_claims={})


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _safe_identifier(name: str) -> bool:
    """Return True if *name* is a safe SQL identifier (no injection risk)."""
    return bool(_SAFE_IDENTIFIER_RE.match(name)) and len(name) <= 256


def _introspect_tables_duckdb(connector: DuckDBConnector) -> list[dict[str, Any]]:
    """Return [{schema, name}] for all user tables in a DuckDB connector."""
    plan = _make_plan(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
        "ORDER BY table_schema, table_name"
    )
    try:
        tbl = connector.execute(plan)
        rows = tbl.to_pydict()
        schemas = rows.get("table_schema", [])
        names = rows.get("table_name", [])
        return [{"schema": s, "name": n} for s, n in zip(schemas, names)]
    except Exception:
        # Fallback: SHOW TABLES (DuckDB in-memory with registered Arrow views)
        plan2 = _make_plan("SHOW TABLES")
        try:
            tbl2 = connector.execute(plan2)
            d = tbl2.to_pydict()
            # Column may be 'name' or 'Name' depending on DuckDB version
            col = next((k for k in d if k.lower() == "name"), None)
            names_list = d.get(col, []) if col else []
            return [{"schema": "main", "name": n} for n in names_list]
        except Exception:
            return []


def _introspect_columns_duckdb(
    connector: DuckDBConnector, table_name: str, schema: str = "main"
) -> list[dict[str, Any]]:
    """Return [{name, type, nullable, pk}] for columns in *table_name*."""
    # Quote schema and table safely (already validated via allowlist before call)
    plan = _make_plan(
        f"SELECT column_name, data_type, is_nullable "
        f"FROM information_schema.columns "
        f"WHERE table_name = '{table_name}' "
        f"ORDER BY ordinal_position"
    )
    try:
        tbl = connector.execute(plan)
        d = tbl.to_pydict()
        names_col = d.get("column_name", [])
        types_col = d.get("data_type", [])
        nullable_col = d.get("is_nullable", [])
        return [
            {
                "name": n,
                "type": t,
                "nullable": str(null).upper() in ("YES", "TRUE", "1"),
                "pk": False,
            }
            for n, t, null in zip(names_col, types_col, nullable_col)
        ]
    except Exception:
        # Fallback: DESCRIBE
        plan2 = _make_plan(f"DESCRIBE {table_name}")
        try:
            tbl2 = connector.execute(plan2)
            d2 = tbl2.to_pydict()
            col_names = d2.get("column_name", d2.get("Field", []))
            col_types = d2.get("column_type", d2.get("Type", []))
            nullables = d2.get("null", d2.get("Null", ["YES"] * len(col_names)))
            return [
                {
                    "name": n,
                    "type": t,
                    "nullable": str(null).upper() in ("YES", "TRUE", "1"),
                    "pk": False,
                }
                for n, t, null in zip(col_names, col_types, nullables)
            ]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Shared helpers that pick connector + verify table existence
# ---------------------------------------------------------------------------


async def _resolve_connector_and_tables(
    datastore_id: str | None,
    user: dict[str, Any],
    repo: Repo,
) -> tuple[DuckDBConnector, list[dict[str, Any]]]:
    """Return (connector, tables) — works for demo (None) and real connectors."""
    if datastore_id is None:
        connector = _get_demo_connector()
        tables = _introspect_tables_duckdb(connector)
        return connector, tables

    org_id = await _get_user_org(str(user["id"]), repo)
    ds = await repo.get("datastores", org_id, datastore_id)
    if ds is None:
        raise AppError("not_found", f"Datastore {datastore_id!r} not found.", 404)

    cfg: dict = dict(ds.get("config") or {})
    ctype = cfg.get("connector_type") or cfg.get("type") or "duckdb"

    if ctype != "duckdb":
        raise AppError(
            "not_supported",
            f"Data browser currently supports duckdb connectors; got {ctype!r}.",
            400,
        )

    connector = _build_duckdb_connector(cfg)
    tables = _introspect_tables_duckdb(connector)
    return connector, tables


# ---------------------------------------------------------------------------
# GET /data/tables  (demo)
# GET /data/{datastore_id}/tables
# ---------------------------------------------------------------------------

_TABLE_LIST_SENTINEL = "tables"  # distinguishes literal "tables" from a UUID


@router.get("/data/tables")
async def list_demo_tables(
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """List tables in the built-in demo connector."""
    connector = _get_demo_connector()
    tables = _introspect_tables_duckdb(connector)
    return {"tables": tables, "datastore_id": None}


@router.get("/data/{datastore_id}/tables")
async def list_tables(
    datastore_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List tables (and schemas) for the given connector via introspection."""
    connector, tables = await _resolve_connector_and_tables(datastore_id, user, repo)
    return {"tables": tables, "datastore_id": datastore_id}


# ---------------------------------------------------------------------------
# GET /data/tables/{table}/columns  (demo)
# GET /data/{datastore_id}/tables/{table}/columns
# ---------------------------------------------------------------------------


@router.get("/data/tables/{table}/columns")
async def list_demo_columns(
    table: str,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return column names + types for a table in the demo connector."""
    connector = _get_demo_connector()
    tables = _introspect_tables_duckdb(connector)
    known = {t["name"] for t in tables}
    if table not in known:
        raise AppError("not_found", f"Table {table!r} not found.", 404)
    columns = _introspect_columns_duckdb(connector, table)
    return {"table": table, "columns": columns, "datastore_id": None}


@router.get("/data/{datastore_id}/tables/{table}/columns")
async def list_columns(
    datastore_id: str,
    table: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return column names + types for a table in the given connector."""
    connector, tables = await _resolve_connector_and_tables(datastore_id, user, repo)
    known = {t["name"] for t in tables}
    if table not in known:
        raise AppError("not_found", f"Table {table!r} not found in datastore {datastore_id!r}.", 404)
    if not _safe_identifier(table):
        raise AppError("invalid_identifier", f"Table name {table!r} is not a valid identifier.", 400)
    columns = _introspect_columns_duckdb(connector, table)
    return {"table": table, "columns": columns, "datastore_id": datastore_id}


# ---------------------------------------------------------------------------
# GET /data/tables/{table}/rows  (demo)
# GET /data/{datastore_id}/tables/{table}/rows?limit=N
# ---------------------------------------------------------------------------


@router.get("/data/tables/{table}/rows")
async def get_demo_rows(
    table: str,
    limit: int = Query(default=500, ge=1, le=5000),
    user: dict[str, Any] = Depends(current_user),
) -> StreamingResponse:
    """Fetch up to *limit* rows from a demo connector table as Arrow IPC."""
    connector = _get_demo_connector()
    tables = _introspect_tables_duckdb(connector)
    known = {t["name"] for t in tables}
    if table not in known:
        raise AppError("not_found", f"Table {table!r} not found.", 404)
    if not _safe_identifier(table):
        raise AppError("invalid_identifier", f"Table name {table!r} is not a valid identifier.", 400)
    plan = _make_plan(f"SELECT * FROM {table} LIMIT {int(limit)}")
    arrow_table = connector.execute(plan)
    full_bytes = table_to_ipc_bytes(arrow_table)
    return StreamingResponse(
        ipc_stream_from_bytes(full_bytes),
        media_type=_ARROW_STREAM_MEDIA_TYPE,
    )


@router.get("/data/{datastore_id}/tables/{table}/rows")
async def get_rows(
    datastore_id: str,
    table: str,
    limit: int = Query(default=500, ge=1, le=5000),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> StreamingResponse:
    """Fetch up to *limit* rows from a connector table as Arrow IPC."""
    connector, tables = await _resolve_connector_and_tables(datastore_id, user, repo)
    known = {t["name"] for t in tables}
    if table not in known:
        raise AppError("not_found", f"Table {table!r} not found in datastore {datastore_id!r}.", 404)
    if not _safe_identifier(table):
        raise AppError("invalid_identifier", f"Table name {table!r} is not a valid identifier.", 400)
    plan = _make_plan(f"SELECT * FROM {table} LIMIT {int(limit)}")
    arrow_table = connector.execute(plan)
    full_bytes = table_to_ipc_bytes(arrow_table)
    return StreamingResponse(
        ipc_stream_from_bytes(full_bytes),
        media_type=_ARROW_STREAM_MEDIA_TYPE,
    )


# ---------------------------------------------------------------------------
# Self-register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
