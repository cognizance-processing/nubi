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
from app.connectors.duckdb_conn import DuckDBConnector, setup_s3_httpfs
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
    """Return (or create) the module-level demo DuckDB connector.

    Same connector as the query route: the full 17-table demo dataset plus the
    legacy ``demo`` table, so the Data browser lists every demo table (not just
    the old single placeholder).
    """
    global _demo_connector
    if _demo_connector is None:
        from app.routes.query import _build_demo_connector  # noqa: PLC0415

        _demo_connector = _build_demo_connector()
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


def _cfg_references_s3(cfg: dict[str, Any]) -> bool:
    """Return True when *cfg* contains any s3:// references.

    Checks:
    - ``parquet_path``, ``database``, ``path``: ``startswith("s3://")``
    - ``view_sql``: substring ``"s3://"`` anywhere in the string (a
      multi-table view_sql starts with ``CREATE OR REPLACE VIEW`` but
      references ``s3://`` URIs inside the read_parquet calls)
    - ``s3_views`` dict values: ``startswith("s3://")``
    """
    # Strict startswith check for non-SQL fields (must be an s3:// URI)
    for key in ("parquet_path", "database", "path"):
        val = cfg.get(key) or ""
        if isinstance(val, str) and val.startswith("s3://"):
            return True
    # view_sql may contain s3:// anywhere (single-table path or multi-table)
    view_sql = cfg.get("view_sql") or ""
    if isinstance(view_sql, str) and "s3://" in view_sql:
        return True
    # s3_views: {table_name: "s3://bucket/key.parquet", ...}
    s3_views = cfg.get("s3_views")
    if isinstance(s3_views, dict):
        for v in s3_views.values():
            if isinstance(v, str) and v.startswith("s3://"):
                return True
    return False


def _build_view_sql_from_s3_views(s3_views: dict[str, str]) -> str:
    """Build a multi-statement ``CREATE VIEW`` SQL string from an *s3_views* dict.

    Each entry ``{table_name: s3_uri}`` produces:

        CREATE OR REPLACE VIEW <table_name> AS
            SELECT * FROM read_parquet('<s3_uri>');

    The result is a single string with all statements separated by ``; \\n``.
    This is the **canonical** config shape for the multi-table S3 connector
    (documented here as the seam for DemoSeedAgent).

    SEAM (DemoSeedAgent)
    --------------------
    To register a multi-table S3-backed datastore that flows through the normal
    connector → /data-browser → /query pipeline, create a datastores row with
    the following config shape::

        {
            "connector_type": "duckdb",
            "database": ":memory:",
            "s3_views": {
                "sales":         "s3://<bucket>/projects/<project_id>/demo/sales.parquet",
                "dim_customers": "s3://<bucket>/projects/<project_id>/demo/dim_customers.parquet",
                "dim_products":  "s3://<bucket>/projects/<project_id>/demo/dim_products.parquet",
                "dim_regions":   "s3://<bucket>/projects/<project_id>/demo/dim_regions.parquet",
                "budget":        "s3://<bucket>/projects/<project_id>/demo/budget.parquet",
                "targets":       "s3://<bucket>/projects/<project_id>/demo/targets.parquet",
            },
            # Optional S3 credential overrides (else env vars are used):
            "s3_key_id":    "...",
            "s3_secret":    "...",
            "s3_endpoint":  "http://localhost:9000",
            "s3_region":    "us-east-1",
        }

    Alternatively, supply a ``view_sql`` string containing one or more
    semicolon-separated ``CREATE [OR REPLACE] VIEW`` statements — each will be
    executed in order so every view is registered on the in-memory connection.
    """
    stmts = [
        f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{uri}')"
        for name, uri in s3_views.items()
    ]
    return ";\n".join(stmts)


def _build_duckdb_connector(cfg: dict[str, Any]) -> DuckDBConnector:
    """Build a DuckDB connector from datastore config (read-only file or memory).

    Supported config shapes
    -----------------------
    **Local on-disk DuckDB file** — ``database`` / ``path`` is a real filesystem
    path (not ``:memory:`` and not ``s3://``).  The file is opened read-only.

    **Single-table Parquet view** — ``view_sql`` contains exactly one
    ``CREATE VIEW … AS SELECT * FROM read_parquet('…')`` statement.

    **Multi-table S3 views** (new) — ``s3_views`` is a dict mapping table names
    to ``s3://`` URIs.  A ``CREATE OR REPLACE VIEW`` statement is built for each
    entry.  See :func:`_build_view_sql_from_s3_views` for the full seam doc.

    **Multi-statement view_sql** (new) — ``view_sql`` contains multiple
    semicolon-separated ``CREATE [OR REPLACE] VIEW`` statements.  Each statement
    is executed individually so that a failure in one does not silently swallow
    the rest.

    When *any* ``s3://`` reference is detected (via :func:`_cfg_references_s3`),
    the httpfs extension is installed/loaded and a DuckDB S3 SECRET is registered
    from ``cfg`` or the standard ``AWS_*`` / ``S3_ENDPOINT_URL`` environment
    variables before any view SQL is executed.
    Local ``/path/to/file`` paths continue to work without any changes.
    """
    db_path = cfg.get("database") or cfg.get("path")
    if db_path and db_path != ":memory:" and not db_path.startswith("s3://"):
        import duckdb as _duckdb

        _conn = _duckdb.connect(database=db_path, read_only=True)
        try:
            _conn.execute("SET enable_external_access=false")
        except Exception:
            pass
        return DuckDBConnector(_conn)

    # In-memory path (also used for s3:// Parquet datasets and multi-table views):
    # 1. Create a fresh in-memory connection.
    # 2. If any s3:// references are present, install httpfs + register SECRET.
    # 3. Build / execute all view SQL statements so every table is visible for
    #    introspection and row-sampling.
    import duckdb as _duckdb_mem

    _conn = _duckdb_mem.connect(database=":memory:")

    if _cfg_references_s3(cfg):
        try:
            setup_s3_httpfs(_conn, cfg)
        except Exception:
            pass  # best-effort; let the view_sql fail with a clear error

    # --- Collect all view SQL statements -----------------------------------
    # Priority: s3_views dict > view_sql string.
    # When both are present, s3_views takes precedence and view_sql is ignored
    # to avoid registering stale / conflicting views.
    _s3_views: dict[str, str] | None = cfg.get("s3_views")
    if isinstance(_s3_views, dict) and _s3_views:
        _view_sql_combined = _build_view_sql_from_s3_views(_s3_views)
    else:
        _view_sql_combined = cfg.get("view_sql") or ""

    # Execute each semicolon-delimited statement individually.  This means a
    # failure on one view does not silently prevent subsequent views from being
    # registered (important for the multi-table demo connector).
    if _view_sql_combined:
        for _stmt in _view_sql_combined.split(";"):
            _stmt = _stmt.strip()
            if _stmt:
                try:
                    _conn.execute(_stmt)
                except Exception:
                    pass  # best-effort; introspection will surface any gap

    return DuckDBConnector(_conn)


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
    # The virtual "Demo data" connector (id "__demo__") resolves to the same
    # in-process demo connector as the no-datastore path; the dataset is shared
    # across orgs and never copied per org.
    from app.routes.connectors import DEMO_CONNECTOR_ID as _DEMO_CONNECTOR_ID
    if datastore_id is None or datastore_id == _DEMO_CONNECTOR_ID:
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
