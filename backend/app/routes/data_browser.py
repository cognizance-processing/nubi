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

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pyarrow as pa
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.encoders import jsonable_encoder

from app.auth.deps import current_user
from app.auth.roles import require_writer_default
from app.connectors.arrow_io import ipc_stream_from_bytes, table_to_ipc_bytes
from app.connectors.duckdb_conn import DuckDBConnector, open_duckdb_readonly, setup_s3_httpfs
from app.connectors.introspect import (
    SYSTEM_SCHEMAS as _SYSTEM_SCHEMAS,
    introspect_tables as _introspect_tables_shared,
    pick_col as _pick_col,
    resolve_table_ref as _resolve_table_ref,
)
from app.connectors.plan import PhysicalPlan
from app.errors import AppError
from app.repos.provider import get_repo, Repo
from app.routes import api_router
from app.routes._org import resolve_org_id as _resolve_org_id

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

router = APIRouter(tags=["data-browser"])

_ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"

# ---------------------------------------------------------------------------
# Demo DuckDB connector (module-level singleton, same seed as query.py)
# ---------------------------------------------------------------------------

_demo_connector: DuckDBConnector | None = None


def _get_demo_connector() -> DuckDBConnector:
    """Return (or create) the module-level demo DuckDB connector.

    D1 consolidation: delegates to ``routes.query._build_demo_connector`` which
    now reads the 17 demo tables from the static local-parquet lakehouse
    (``seed_data/parquet/``) rather than building them in-memory.
    Same connector as the query route: all 17 tables plus the legacy ``demo``
    table are available for the Data browser.
    """
    global _demo_connector
    if _demo_connector is None:
        from app.routes.query import _build_demo_connector  # noqa: PLC0415

        _demo_connector = _build_demo_connector()
    return _demo_connector


# ---------------------------------------------------------------------------
# Org resolution helper (mirrors connectors.py)
# ---------------------------------------------------------------------------


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
    _SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    stmts = []
    for name, uri in s3_views.items():
        if not _SAFE_IDENT.match(name):
            raise ValueError(f"Invalid s3_views table name: {name!r}")
        if not isinstance(uri, str) or not (uri.startswith("s3://") or uri.startswith("/")):
            raise ValueError(f"Invalid s3_views URI: {uri!r}")
        safe_uri = uri.replace("'", "''")
        stmts.append(
            f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{safe_uri}')"
        )
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
        return DuckDBConnector(open_duckdb_readonly(db_path))

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
    """Return [{schema, name}] for every user table reachable through *connector*.

    Thin alias for :func:`app.connectors.introspect.introspect_tables` — the
    single shared implementation. Kept as a module-local name because the write
    paths and tests already reference it.
    """
    return _introspect_tables_shared(connector)


def _introspect_columns_duckdb(
    connector: DuckDBConnector, table_name: str, schema: str = "main"
) -> list[dict[str, Any]]:
    """Return [{name, type, portable_type, nullable, pk, editable}] for columns.

    ``type`` is the DuckDB-native type string (from information_schema);
    ``portable_type`` is the same value normalised to the portable vocabulary
    (text|number|bool|date|timestamp|json) via the shared Arrow-type mapper, so
    the shape matches the non-DuckDB probe path.  ``pk`` flags columns that form
    the table's primary key (or, absent a PK, a single-column UNIQUE constraint
    usable as a row identity).  ``editable`` is True for every real column — the
    write endpoints decide per-request which columns a given operation may touch
    (PK columns are not updatable via the ``set`` clause, but they are settable
    on INSERT).
    """
    pk_cols = _detect_row_identity_duckdb(connector, table_name)
    # Normalised portable type per column, from a zero-row Arrow probe (reuses
    # the shared Arrow→portable mapper).  Best-effort: falls back to the native
    # type string when the probe is unavailable.
    portable = _portable_types_via_probe(connector, table_name)
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
        names_col = _pick_col(d, "column_name")
        types_col = _pick_col(d, "data_type")
        nullable_col = _pick_col(d, "is_nullable")
        return [
            {
                "name": n,
                "type": t,
                "portable_type": portable.get(n) or _portable_from_native_type(t),
                "nullable": str(null).upper() in ("YES", "TRUE", "1"),
                "pk": n in pk_cols,
                "editable": True,
            }
            for n, t, null in zip(names_col, types_col, nullable_col)
        ]
    except Exception:
        # Fallback: DESCRIBE
        plan2 = _make_plan(f"DESCRIBE {table_name}")
        try:
            tbl2 = connector.execute(plan2)
            d2 = tbl2.to_pydict()
            col_names = _pick_col(d2, "column_name", "Field")
            col_types = _pick_col(d2, "column_type", "Type")
            nullables = _pick_col(d2, "null", "Null") or ["YES"] * len(col_names)
            return [
                {
                    "name": n,
                    "type": t,
                    "portable_type": portable.get(n) or _portable_from_native_type(t),
                    "nullable": str(null).upper() in ("YES", "TRUE", "1"),
                    "pk": n in pk_cols,
                    "editable": True,
                }
                for n, t, null in zip(col_names, col_types, nullables)
            ]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Portable (normalised) type derivation — shared by every connector
# ---------------------------------------------------------------------------
#
# The portable vocabulary (text|number|bool|date|timestamp|json) and its
# Arrow-type mapper are defined ONCE in ``routes.query._portable_arrow_type``
# and reused here so the data browser never invents a parallel vocabulary.


def _portable_from_native_type(native: str) -> str:
    """Best-effort native-type-string → portable vocabulary fallback.

    Only used when the Arrow probe is unavailable (e.g. an information_schema
    row whose type could not be probed).  Emits the SAME vocabulary as
    ``_portable_arrow_type`` — it does not introduce new type names.
    """
    n = (native or "").strip().lower()
    if n.startswith("bool"):
        return "bool"
    if "timestamp" in n or "datetime" in n:
        return "timestamp"
    if n.startswith("date"):
        return "date"
    if any(k in n for k in ("json", "struct", "list", "array", "map")):
        return "json"
    if any(
        k in n
        for k in ("int", "float", "double", "real", "decimal", "numeric", "number")
    ):
        return "number"
    if any(k in n for k in ("char", "text", "string", "uuid")):
        return "text"
    return "text"


def _portable_types_via_probe(connector: Any, table_name: str) -> dict[str, str]:
    """Return ``{column_name: portable_type}`` from a zero-row Arrow probe.

    Executes ``SELECT * FROM {table} LIMIT 0`` through *connector* and maps each
    Arrow field via the shared ``_portable_arrow_type`` mapper.  Best-effort:
    returns ``{}`` if the probe fails so callers can fall back.  Callers MUST
    have validated *table_name* via :func:`_safe_identifier`.
    """
    try:
        tbl = connector.execute(_make_plan(f"SELECT * FROM {table_name} LIMIT 0"))
    except Exception:
        return {}
    from app.routes.query import _portable_arrow_type  # noqa: PLC0415

    return {f.name: _portable_arrow_type(f.type) for f in tbl.schema}


def _introspect_columns_via_probe(
    connector: Any, table_name: str
) -> list[dict[str, Any]]:
    """Introspect columns for a non-DuckDB connector via a zero-row probe.

    Executes ``SELECT * FROM {table} LIMIT 0`` through the resolved connector and
    derives one entry per Arrow field: the native ``type`` (Arrow type string),
    the normalised ``portable_type``, ``nullable`` from the field, and
    ``pk``/``editable`` conservatively ``False`` (remote SQL sources are browsed
    read-only).  Callers MUST have validated *table_name* via
    :func:`_safe_identifier`.
    """
    from app.routes.query import _portable_arrow_type  # noqa: PLC0415

    tbl = connector.execute(_make_plan(f"SELECT * FROM {table_name} LIMIT 0"))
    return [
        {
            "name": f.name,
            "type": str(f.type),
            "portable_type": _portable_arrow_type(f.type),
            "nullable": bool(f.nullable),
            "pk": False,
            "editable": False,
        }
        for f in tbl.schema
    ]


# ---------------------------------------------------------------------------
# Write-support introspection: row identity (PK / UNIQUE) + writability
# ---------------------------------------------------------------------------


def _table_type_duckdb(connector: DuckDBConnector, table_name: str) -> str | None:
    """Return the information_schema ``table_type`` for *table_name*.

    ``'BASE TABLE'`` for a native table (DML-capable), ``'VIEW'`` for a
    read_parquet / object-store-backed view, ``None`` if not found.
    """
    plan = _make_plan(
        f"SELECT table_type FROM information_schema.tables "
        f"WHERE table_name = '{table_name}' LIMIT 1"
    )
    try:
        d = connector.execute(plan).to_pydict()
        types = _pick_col(d, "table_type")
        return str(types[0]).upper() if types else None
    except Exception:
        return None


def _detect_row_identity_duckdb(
    connector: DuckDBConnector, table_name: str
) -> list[str]:
    """Return the column list that uniquely identifies a row in *table_name*.

    Preference order:
    1. PRIMARY KEY columns (any arity).
    2. A single-column UNIQUE constraint (usable as a stable row identity).

    Returns ``[]`` when no usable identity exists — the table is then
    non-writable (editing without a row identity is unsafe).  Only real
    catalog constraints are consulted (``duckdb_constraints()``); a plain
    table with no PK/UNIQUE returns ``[]`` even if every row happens to differ.
    """
    plan = _make_plan(
        f"SELECT constraint_type, constraint_column_names "
        f"FROM duckdb_constraints() WHERE table_name = '{table_name}'"
    )
    try:
        d = connector.execute(plan).to_pydict()
    except Exception:
        return []
    ctypes = _pick_col(d, "constraint_type")
    ccols = _pick_col(d, "constraint_column_names")
    pk: list[str] = []
    unique_single: list[str] = []
    for ctype, cols in zip(ctypes, ccols):
        col_list = list(cols) if cols is not None else []
        if str(ctype).upper() == "PRIMARY KEY" and col_list:
            pk = col_list
        elif str(ctype).upper() == "UNIQUE" and len(col_list) == 1 and not unique_single:
            unique_single = col_list
    if pk:
        return pk
    return unique_single


def _writable_meta_duckdb(
    connector: DuckDBConnector, table_name: str
) -> tuple[bool, list[str]]:
    """Return ``(writable, primary_key)`` for *table_name* on a DuckDB connector.

    A table is writable ONLY when it is a native ``BASE TABLE`` (not a VIEW —
    read_parquet / object-store views report VIEW) AND a row identity (PK or
    single-column UNIQUE) can be determined.  ``primary_key`` is the identity
    column list (empty when not writable).
    """
    if _table_type_duckdb(connector, table_name) != "BASE TABLE":
        return False, []
    identity = _detect_row_identity_duckdb(connector, table_name)
    return (bool(identity), identity)


# ---------------------------------------------------------------------------
# Shared helpers that pick connector + verify table existence
# ---------------------------------------------------------------------------


async def _resolve_connector_and_tables(
    datastore_id: str | None,
    user: dict[str, Any],
    repo: Repo,
    request: Request,
) -> tuple[DuckDBConnector, list[dict[str, Any]], Callable[[], None]]:
    """Return ``(connector, tables, net_cleanup)``.

    Works for demo (``None`` / ``__demo__``) and real connectors.  ``net_cleanup``
    tears down any ephemeral bridge tunnel and MUST be called by the caller in a
    ``finally`` — use :func:`_browse_connector`, which does it for you.
    """
    # The virtual "Demo data" connector (id "__demo__") resolves to the same
    # in-process demo connector as the no-datastore path; the dataset is shared
    # across orgs and never copied per org.
    from app.routes.connectors import DEMO_CONNECTOR_ID as _DEMO_CONNECTOR_ID
    _noop: Callable[[], None] = lambda: None  # noqa: E731
    if datastore_id is None or datastore_id == _DEMO_CONNECTOR_ID:
        connector = _get_demo_connector()
        tables = await asyncio.to_thread(_introspect_tables_duckdb, connector)
        return connector, tables, _noop

    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    ds = await repo.get("datastores", org_id, datastore_id)
    if ds is None:
        raise AppError("not_found", f"Datastore {datastore_id!r} not found.", 404)

    cfg: dict = dict(ds.get("config") or {})
    ctype = cfg.get("connector_type") or cfg.get("type") or "duckdb"

    if ctype == "duckdb":
        connector = _build_duckdb_connector(cfg)
        tables = await asyncio.to_thread(_introspect_tables_duckdb, connector)
        return connector, tables, _noop

    # Non-DuckDB connectors (postgres/bigquery/snowflake/…): build the connector
    # generically through the shared connector-build path (registry + secret /
    # network resolution) and introspect its tables through the SAME
    # information_schema query used for DuckDB.  The connector only needs to
    # expose ``.execute(plan)`` — every SQL connector does.
    connector, cleanup = await _build_remote_connector(datastore_id, org_id, repo)
    try:
        tables = await asyncio.to_thread(_introspect_tables_duckdb, connector)
    except BaseException:
        # Introspection failed — release the tunnel now; no caller will get the
        # cleanup handle back through the raised exception.
        _safe_cleanup(cleanup)
        raise
    return connector, tables, cleanup


async def _build_remote_connector(
    datastore_id: str,
    org_id: str | None,
    repo: Repo,
) -> tuple[Any, Callable[[], None]]:
    """Build a non-DuckDB connector for read-only browsing.

    Reuses ``routes.query._build_connector_for_plan`` — the single place that
    resolves a datastore config into a live connector (registry factory + secret
    injection + network-mode resolution).  A policy-free plan is passed so no
    RLS predicate is required (browse never carries policies here).

    Returns ``(connector, net_cleanup)``.  ``net_cleanup`` tears down any
    ephemeral bridge tunnel and MUST be called by the caller in a ``finally``.
    It is a no-op in the default ``direct`` network mode, but in
    ``network_mode: "bridge"`` it releases the per-request tunnel — dropping it
    leaks a tunnel (and its broker slot) on every browse request.
    """
    from app.routes.query import _build_connector_for_plan  # noqa: PLC0415

    plan = _make_plan("")  # empty rls_claims → no policy-bearing browse
    connector, _kind, cleanup = await _build_connector_for_plan(
        plan, datastore_id, org_id, None, repo
    )
    return connector, cleanup


def _safe_cleanup(cleanup: Callable[[], None]) -> None:
    """Run *cleanup*, swallowing errors so it can never mask the real result."""
    try:
        cleanup()
    except Exception:  # noqa: BLE001 — cleanup must never mask the response/error.
        pass


@asynccontextmanager
async def _browse_connector(
    datastore_id: str | None,
    user: dict[str, Any],
    repo: Repo,
    request: Request,
) -> AsyncIterator[tuple[DuckDBConnector, list[dict[str, Any]]]]:
    """Yield ``(connector, tables)`` and always release the bridge tunnel.

    Every read path in this module goes through here so the ``net_cleanup``
    contract of :func:`_build_remote_connector` is honoured exactly once per
    request, on both the success and the error path.
    """
    connector, tables, cleanup = await _resolve_connector_and_tables(
        datastore_id, user, repo, request
    )
    try:
        yield connector, tables
    finally:
        _safe_cleanup(cleanup)


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
    tables = await asyncio.to_thread(_introspect_tables_duckdb, connector)
    return {"tables": tables, "datastore_id": None}


@router.get("/data/{datastore_id}/tables")
async def list_tables(
    datastore_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List tables (and schemas) for the given connector via introspection."""
    async with _browse_connector(datastore_id, user, repo, request) as (_conn, tables):
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
    tables = await asyncio.to_thread(_introspect_tables_duckdb, connector)
    known = {t["name"] for t in tables}
    if table not in known:
        raise AppError("not_found", f"Table {table!r} not found.", 404)
    columns = await asyncio.to_thread(_introspect_columns_duckdb, connector, table)
    # Demo connector is an in-memory parquet/Arrow view set — never writable.
    return {
        "table": table,
        "columns": columns,
        "datastore_id": None,
        "writable": False,
        "primary_key": [],
    }


@router.get("/data/{datastore_id}/tables/{table}/columns")
async def list_columns(
    datastore_id: str,
    table: str,
    request: Request,
    schema: str | None = Query(default=None),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return column names + types for a table in the given connector."""
    async with _browse_connector(datastore_id, user, repo, request) as (connector, tables):
        known = {t["name"] for t in tables}
        if table not in known:
            raise AppError("not_found", f"Table {table!r} not found in datastore {datastore_id!r}.", 404)
        if not _safe_identifier(table):
            raise AppError("invalid_identifier", f"Table name {table!r} is not a valid identifier.", 400)
        from app.routes.connectors import DEMO_CONNECTOR_ID as _DEMO_CONNECTOR_ID
        cfg = (
            await _datastore_cfg(datastore_id, user, repo, request)
            if datastore_id != _DEMO_CONNECTOR_ID
            else None
        )
        ctype = (
            (cfg.get("connector_type") or cfg.get("type") or "duckdb") if cfg else "duckdb"
        )

        # Non-DuckDB connectors (postgres/bigquery/snowflake/…): derive columns from a
        # zero-row Arrow probe through the resolved connector.  Remote SQL sources are
        # browsed read-only, so writability is always False here.
        if ctype != "duckdb":
            ref = _resolve_table_ref(table, tables, schema)
            columns = await asyncio.to_thread(
                _introspect_columns_via_probe, connector, ref
            )
            return {
                "table": table,
                "columns": columns,
                "datastore_id": datastore_id,
                "writable": False,
                "primary_key": [],
            }

        columns = await asyncio.to_thread(_introspect_columns_duckdb, connector, table)
        # Writability: native BASE TABLEs (on-disk file connectors) only.  The
        # virtual demo connector and read_parquet views stay read-only — Nubi does
        # not host a writable managed lakehouse.
        writable = False
        primary_key: list[str] = []
        # A user-set read-only connector is never editable in the data grid — report
        # writable=False up front so no edit affordances show (the write path also
        # gates on this, but the UI should reflect it before a write is attempted).
        read_only = bool(cfg and cfg.get("read_only"))
        if datastore_id != _DEMO_CONNECTOR_ID and not read_only:
            writable, primary_key = await asyncio.to_thread(
                _writable_meta_duckdb, connector, table
            )
        return {
            "table": table,
            "columns": columns,
            "datastore_id": datastore_id,
            "writable": writable,
            "primary_key": primary_key,
        }


# ---------------------------------------------------------------------------
# GET /data/tables/{table}/rows  (demo)
# GET /data/{datastore_id}/tables/{table}/rows?limit=N
# ---------------------------------------------------------------------------


def _rows_response(arrow_table: "pa.Table", fmt: str):
    """Render rows as Arrow IPC (default, for the DuckDB-WASM explorer) OR as
    plain JSON (``format=json``, for the editable data grid which edits row
    objects).  JSON uses ``jsonable_encoder`` so dates/decimals/uuids serialize.
    """
    if (fmt or "").lower() == "json":
        return JSONResponse(content=jsonable_encoder({
            "rows": arrow_table.to_pylist(),
            "columns": [{"name": f.name, "type": str(f.type)} for f in arrow_table.schema],
            "row_count": arrow_table.num_rows,
        }))
    return StreamingResponse(
        ipc_stream_from_bytes(table_to_ipc_bytes(arrow_table)),
        media_type=_ARROW_STREAM_MEDIA_TYPE,
    )


@router.get("/data/tables/{table}/rows")
async def get_demo_rows(
    table: str,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    format: str = Query(default="arrow"),
    user: dict[str, Any] = Depends(current_user),
):
    """Fetch rows from a demo connector table (Arrow IPC, or JSON via format=json)."""
    connector = _get_demo_connector()
    tables = await asyncio.to_thread(_introspect_tables_duckdb, connector)
    known = {t["name"] for t in tables}
    if table not in known:
        raise AppError("not_found", f"Table {table!r} not found.", 404)
    if not _safe_identifier(table):
        raise AppError("invalid_identifier", f"Table name {table!r} is not a valid identifier.", 400)
    plan = _make_plan(f"SELECT * FROM {table} LIMIT {int(limit)} OFFSET {int(offset)}")
    arrow_table = await asyncio.to_thread(connector.execute, plan)
    return await asyncio.to_thread(_rows_response, arrow_table, format)


@router.get("/data/{datastore_id}/tables/{table}/rows")
async def get_rows(
    datastore_id: str,
    table: str,
    request: Request,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    format: str = Query(default="arrow"),
    schema: str | None = Query(default=None),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
):
    """Fetch rows from a connector table (Arrow IPC, or JSON via format=json)."""
    async with _browse_connector(datastore_id, user, repo, request) as (connector, tables):
        known = {t["name"] for t in tables}
        if table not in known:
            raise AppError("not_found", f"Table {table!r} not found in datastore {datastore_id!r}.", 404)
        if not _safe_identifier(table):
            raise AppError("invalid_identifier", f"Table name {table!r} is not a valid identifier.", 400)
        ref = _resolve_table_ref(table, tables, schema)
        plan = _make_plan(f"SELECT * FROM {ref} LIMIT {int(limit)} OFFSET {int(offset)}")
        arrow_table = await asyncio.to_thread(connector.execute, plan)
        # Serialise inside the tunnel's lifetime (``table_to_ipc_bytes`` is eager,
        # so the StreamingResponse never touches the network after cleanup) and
        # off the loop, since encoding a full result set is real CPU work.
        return await asyncio.to_thread(_rows_response, arrow_table, format)


# ---------------------------------------------------------------------------
# WRITE support — PATCH / POST / DELETE rows (org-scoped, writable-gated)
# ---------------------------------------------------------------------------
#
# Request / response contract (the frontend grid implements this EXACTLY):
#
#   PATCH  /data/{datastore_id}/tables/{table}/rows
#       body:  {"pk": {col: val, ...}, "set": {col: val, ...}}
#       200 →  {"row": {col: val, ...}, "updated": 1}
#
#   POST   /data/{datastore_id}/tables/{table}/rows
#       body:  {"values": {col: val, ...}}
#       201 →  {"row": {col: val, ...}, "inserted": 1}
#
#   DELETE /data/{datastore_id}/tables/{table}/rows
#       body:  {"pk": {col: val, ...}}
#       200 →  {"deleted": 1}
#
# Security gates (all three):
#   - tenant: datastore must belong to caller's org (404 otherwise, no leak)
#   - role:   require_writer_default (viewers → 403)
#   - writable gate: non-writable table/connector → 409
#   - identifiers (table + every column key): _safe_identifier allowlist AND
#     membership in the table's introspected columns; PK keys must equal the
#     table's actual row-identity columns → no raw identifier interpolation
#   - values: bound parameters only ($N) — never string-interpolated
#   - affected-row sanity: UPDATE/DELETE require the FULL PK; RETURNING / count
#     must affect exactly 1 row (0 → 404, >1 → 409)


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier for DuckDB after allowlist validation.

    Callers MUST have already validated *name* via :func:`_safe_identifier`
    AND confirmed it is a real introspected column/table; this only adds the
    double-quoting that lets reserved words / mixed case round-trip safely.
    """
    return '"' + name.replace('"', '""') + '"'


async def _datastore_cfg(
    datastore_id: str,
    user: dict[str, Any],
    repo: Repo,
    request: Request,
) -> dict[str, Any] | None:
    """Return the org-scoped config dict for *datastore_id*, or ``None``.

    ``None`` for the virtual demo connector, a cross-org / missing datastore, or
    a row with no config.  Tenant isolation is enforced (the row is fetched by
    the caller's org), so this never leaks another org's config.
    """
    from app.routes.connectors import DEMO_CONNECTOR_ID as _DEMO_CONNECTOR_ID

    if datastore_id is None or datastore_id == _DEMO_CONNECTOR_ID:
        return None
    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    ds = await repo.get("datastores", org_id, datastore_id)
    if ds is None:
        return None
    cfg = ds.get("config")
    return dict(cfg) if isinstance(cfg, dict) else None


class _WriteTarget:
    """Resolved write target for a row mutation.

    ``connector`` set → NATIVE DML: a read-write on-disk DuckDB BASE TABLE; the
    endpoint runs UPDATE/INSERT/DELETE directly on ``connector``.  Nubi does
    not host a writable managed lakehouse, so this is the only strategy.
    """

    __slots__ = ("connector", "primary_key")

    def __init__(
        self,
        connector: DuckDBConnector,
        primary_key: list[str],
    ) -> None:
        self.connector = connector
        self.primary_key = primary_key


async def _resolve_writable_connector(
    datastore_id: str,
    table: str,
    user: dict[str, Any],
    repo: Repo,
    request: Request,
) -> _WriteTarget:
    """Resolve an org-scoped, write-capable target for *table*.

    Returns a :class:`_WriteTarget` (native-DML connector). Raises:
    - 404 if the datastore is missing / cross-org, or the table is unknown.
    - 400 for a non-duckdb connector or an unsafe table identifier.
    - 409 if the table is not writable (view / no primary key or unique row identity).

    On-disk DuckDB files are opened READ-WRITE for native DML; the demo
    connector is rejected outright.
    """
    from app.routes.connectors import DEMO_CONNECTOR_ID as _DEMO_CONNECTOR_ID

    if datastore_id == _DEMO_CONNECTOR_ID:
        raise AppError(
            "not_writable",
            "The demo dataset is read-only and cannot be edited.",
            409,
        )

    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    ds = await repo.get("datastores", org_id, datastore_id)
    if ds is None:
        raise AppError("not_found", f"Datastore {datastore_id!r} not found.", 404)

    cfg: dict = dict(ds.get("config") or {})
    ctype = cfg.get("connector_type") or cfg.get("type") or "duckdb"
    if ctype != "duckdb":
        raise AppError(
            "not_supported",
            f"Writes currently support duckdb connectors; got {ctype!r}.",
            400,
        )
    if cfg.get("read_only"):
        raise AppError(
            "not_writable",
            "This connector is configured read-only.",
            409,
        )

    connector = await asyncio.to_thread(_build_writable_duckdb_connector, cfg)
    tables = await asyncio.to_thread(_introspect_tables_duckdb, connector)
    known = {t["name"] for t in tables}
    if table not in known:
        raise AppError(
            "not_found",
            f"Table {table!r} not found in datastore {datastore_id!r}.",
            404,
        )
    if not _safe_identifier(table):
        raise AppError(
            "invalid_identifier",
            f"Table name {table!r} is not a valid identifier.",
            400,
        )

    # Native BASE TABLE (on-disk file) → direct DML.  This is the only
    # writable shape: Nubi does not host a writable managed lakehouse, so
    # read_parquet views (including the old per-project editable demo) stay
    # read-only.
    writable, primary_key = await asyncio.to_thread(
        _writable_meta_duckdb, connector, table
    )
    if writable:
        return _WriteTarget(connector, primary_key)

    raise AppError(
        "not_writable",
        f"Table {table!r} is not writable: it is a view or has no primary "
        f"key / unique row identity.",
        409,
    )


def _build_writable_duckdb_connector(cfg: dict[str, Any]) -> DuckDBConnector:
    """Build a DuckDB connector opened READ-WRITE for DML.

    Only an on-disk DuckDB file (``database`` / ``path`` that is a real local
    path, not ``:memory:`` / ``s3://``) yields a write-capable connector.  An
    in-memory / view-only config produces a connector with no native tables,
    so the writability gate downstream rejects every table on it.
    """
    db_path = cfg.get("database") or cfg.get("path")
    if db_path and db_path != ":memory:" and not str(db_path).startswith("s3://"):
        import duckdb as _duckdb

        conn = _duckdb.connect(database=db_path, read_only=False)
        return DuckDBConnector(conn)
    # No writable local store: fall back to the standard (view) builder so the
    # table list resolves; the writability gate will then reject the table.
    return _build_duckdb_connector(cfg)


def _validate_columns(
    connector: DuckDBConnector,
    table: str,
    cols: list[str],
) -> set[str]:
    """Validate *cols* against the table's introspected columns.

    Every name must pass :func:`_safe_identifier` AND be a real column.  Returns
    the set of all real column names (so callers can do further checks).  Raises
    400 on an unsafe or unknown identifier — no raw identifier ever reaches SQL
    without surviving this allowlist.
    """
    real = {c["name"] for c in _introspect_columns_duckdb(connector, table)}
    for c in cols:
        if not isinstance(c, str) or not _safe_identifier(c):
            raise AppError(
                "invalid_identifier",
                f"Column name {c!r} is not a valid identifier.",
                400,
            )
        if c not in real:
            raise AppError(
                "unknown_column",
                f"Column {c!r} is not a column of table {table!r}.",
                400,
            )
    return real


def _require_full_pk(provided: dict[str, Any], primary_key: list[str], table: str) -> None:
    """Ensure *provided* PK dict supplies exactly the table's row-identity cols."""
    if set(provided.keys()) != set(primary_key):
        raise AppError(
            "incomplete_pk",
            f"The 'pk' must specify exactly the primary key columns "
            f"{primary_key} for table {table!r}.",
            400,
        )


@router.patch(
    "/data/{datastore_id}/tables/{table}/rows",
    dependencies=[Depends(require_writer_default)],
)
async def update_row(
    datastore_id: str,
    table: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update exactly one row identified by its full primary key.

    Body ``{"pk": {col: val, ...}, "set": {col: val, ...}}``.
    """
    target = await _resolve_writable_connector(datastore_id, table, user, repo, request)
    connector, primary_key = target.connector, target.primary_key
    pk = body.get("pk")
    set_vals = body.get("set")
    if not isinstance(pk, dict) or not pk:
        raise AppError("bad_request", "Body must include a non-empty 'pk' object.", 400)
    if not isinstance(set_vals, dict) or not set_vals:
        raise AppError("bad_request", "Body must include a non-empty 'set' object.", 400)

    await asyncio.to_thread(
        _validate_columns, connector, table, list(pk.keys()) + list(set_vals.keys())
    )
    _require_full_pk(pk, primary_key, table)
    # Disallow mutating a PK column via SET (row identity must stay stable).
    pk_overlap = set(set_vals.keys()) & set(primary_key)
    if pk_overlap:
        raise AppError(
            "pk_immutable",
            f"Primary key columns {sorted(pk_overlap)} cannot be changed via 'set'.",
            400,
        )

    set_cols = list(set_vals.keys())
    pk_cols = list(pk.keys())
    params: list[Any] = [set_vals[c] for c in set_cols] + [pk[c] for c in pk_cols]
    set_clause = ", ".join(
        f"{_quote_ident(c)} = ${i + 1}" for i, c in enumerate(set_cols)
    )

    where_clause = " AND ".join(
        f"{_quote_ident(c)} = ${len(set_cols) + i + 1}" for i, c in enumerate(pk_cols)
    )
    sql = (
        f"UPDATE {_quote_ident(table)} SET {set_clause} "
        f"WHERE {where_clause} RETURNING *"
    )
    result = await asyncio.to_thread(
        connector.execute,
        PhysicalPlan(sql=sql, params=params, cache_key="", rls_claims={}),
    )
    n = result.num_rows
    if n == 0:
        raise AppError("not_found", "No row matched the supplied primary key.", 404)
    if n > 1:
        raise AppError("ambiguous_pk", "More than one row matched the primary key.", 409)
    rows = result.to_pylist()
    return {"row": rows[0], "updated": 1}


@router.post(
    "/data/{datastore_id}/tables/{table}/rows",
    status_code=201,
    dependencies=[Depends(require_writer_default)],
)
async def insert_row(
    datastore_id: str,
    table: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Insert one row.  Body ``{"values": {col: val, ...}}``; returns the new row."""
    target = await _resolve_writable_connector(datastore_id, table, user, repo, request)
    connector = target.connector
    values = body.get("values")
    if not isinstance(values, dict) or not values:
        raise AppError("bad_request", "Body must include a non-empty 'values' object.", 400)

    await asyncio.to_thread(_validate_columns, connector, table, list(values.keys()))
    cols = list(values.keys())
    params: list[Any] = [values[c] for c in cols]
    col_clause = ", ".join(_quote_ident(c) for c in cols)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))

    sql = (
        f"INSERT INTO {_quote_ident(table)} ({col_clause}) "
        f"VALUES ({placeholders}) RETURNING *"
    )
    result = await asyncio.to_thread(
        connector.execute,
        PhysicalPlan(sql=sql, params=params, cache_key="", rls_claims={}),
    )
    rows = result.to_pylist()
    return {"row": rows[0] if rows else None, "inserted": result.num_rows}


@router.delete(
    "/data/{datastore_id}/tables/{table}/rows",
    dependencies=[Depends(require_writer_default)],
)
async def delete_row(
    datastore_id: str,
    table: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Delete exactly one row identified by its full primary key.

    Body ``{"pk": {col: val, ...}}``; returns ``{"deleted": 1}``.
    """
    target = await _resolve_writable_connector(datastore_id, table, user, repo, request)
    connector, primary_key = target.connector, target.primary_key
    pk = body.get("pk")
    if not isinstance(pk, dict) or not pk:
        raise AppError("bad_request", "Body must include a non-empty 'pk' object.", 400)

    await asyncio.to_thread(_validate_columns, connector, table, list(pk.keys()))
    _require_full_pk(pk, primary_key, table)

    pk_cols = list(pk.keys())
    params: list[Any] = [pk[c] for c in pk_cols]
    where_clause = " AND ".join(
        f"{_quote_ident(c)} = ${i + 1}" for i, c in enumerate(pk_cols)
    )

    sql = (
        f"DELETE FROM {_quote_ident(table)} WHERE {where_clause} RETURNING *"
    )
    result = await asyncio.to_thread(
        connector.execute,
        PhysicalPlan(sql=sql, params=params, cache_key="", rls_claims={}),
    )
    n = result.num_rows
    if n == 0:
        raise AppError("not_found", "No row matched the supplied primary key.", 404)
    if n > 1:
        raise AppError("ambiguous_pk", "More than one row matched the primary key.", 409)
    return {"deleted": 1}


# ---------------------------------------------------------------------------
# Self-register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
