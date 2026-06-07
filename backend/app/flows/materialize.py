"""Materialized multi-source blends for the Flows engine.

A *blend* is a SCHEDULED flow that fans out to N single-source ``query`` tasks,
merges their results in DuckDB via an author-supplied ``combine_sql``, and
writes the combined result to a cheap, single-source MATERIALIZED dataset that
dashboards read.  The expensive multi-source join runs on a SCHEDULE, never per
dashboard view — this is the cost wedge: materialize-then-serve, NOT live
federation.

Cost wedge / RLS contract (CRITICAL)
------------------------------------
The blend declares ``rls_keys`` (e.g. ``["tenant_id"]``).  The materialized
table MUST keep those columns so the planner can still inject
``WHERE tenant_id = <claim>`` at READ time on the materialized source
(predicate injection on the blend output).  ``materialize_blend`` therefore
*verifies* that every declared rls_key appears in the output columns and raises
if one was flattened away.  Do not write a blend that drops its RLS columns —
that would defeat multi-tenant safety on the served dataset.

Public API
----------
build_blend_spec(name, sources, combine_sql, *, rls_keys, table, database,
                 datastore_id, query_id) -> dict
    Build a FlowSpec dict: one ``query`` task per source (so per-source
    predicate pushdown + RLS stay intact) plus a single ``materialize`` task
    that depends on all of them and carries the merge + materialization config.

materialize_blend(config, inputs) -> dict
    The work performed by the ``materialize`` task handler.  Registers each
    upstream source result as a DuckDB table named by its source ``key``, runs
    ``combine_sql``, writes the result to ``database`` (table ``table``),
    verifies rls_keys survived, and registers a runtime query
    (``SELECT * FROM <table>``) bound to the blend datastore so a widget can
    read via one ``query_id``.

blend_database_path(flow_id) -> str
    The default on-disk DuckDB target for a blend: ``seed_data/blends/<id>.duckdb``.

register_blend_query(query_id, database, table, datastore_id) -> None
    Register the materialized dataset into the runtime query registry so reads
    resolve without a server restart.

Security notes
--------------
- ``combine_sql`` is author-provided (first-party, org-scoped) DuckDB SQL run
  against the registered source tables; it is NOT end-user input.
- The written DuckDB file is opened READ-ONLY at query time by the existing
  read path (``routes/query.py``) with ``enable_external_access=false``.
"""

from __future__ import annotations

import os
from typing import Any

from app.errors import AppError

# Default table name inside the materialized DuckDB file.
DEFAULT_BLEND_TABLE = "blend"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _seed_data_dir() -> str:
    """Return the absolute path to backend/seed_data."""
    # materialize.py lives at backend/app/flows/materialize.py
    here = os.path.dirname(os.path.abspath(__file__))
    backend = os.path.dirname(os.path.dirname(here))
    return os.path.join(backend, "seed_data")


def blend_database_path(flow_id: str) -> str:
    """Return the default DuckDB file path for a blend keyed by *flow_id*.

    Shape: ``<backend>/seed_data/blends/<flow_id>.duckdb`` (absolute path).
    The parent directory is created lazily by :func:`materialize_blend`.
    """
    return os.path.join(_seed_data_dir(), "blends", f"{flow_id}.duckdb")


# ---------------------------------------------------------------------------
# Spec builder
# ---------------------------------------------------------------------------


def build_blend_spec(
    name: str,
    sources: list[dict[str, Any]],
    combine_sql: str,
    *,
    rls_keys: list[str] | None = None,
    table: str = DEFAULT_BLEND_TABLE,
    database: str,
    datastore_id: str,
    query_id: str,
) -> dict[str, Any]:
    """Build a blend FlowSpec dict (source query tasks + one materialize task).

    Parameters
    ----------
    name:
        Flow name.
    sources:
        List of ``{key, query_id?, sql?, datastore_id?, named_params?}`` dicts.
        Each becomes a single-source ``query`` task keyed by ``key``.
    combine_sql:
        DuckDB SQL that merges the source tables (each registered under its
        source ``key``) into the materialized result.
    rls_keys:
        Columns that MUST appear in the combined result so RLS predicate
        injection works at read time on the materialized source.
    table:
        Target table name inside the DuckDB file (default ``blend``).
    database:
        Absolute path to the DuckDB file to write.
    datastore_id / query_id:
        The pre-created ``datastores`` / ``queries`` row ids the result is
        exposed through (the widget binds to ``query_id``).

    Returns
    -------
    dict
        A FlowSpec dict (``{version, name, tasks}``) ready for the validator.
    """
    rls_keys = list(rls_keys or [])
    source_keys: list[str] = []
    tasks: list[dict[str, Any]] = []

    for src in sources:
        key = str(src["key"])
        source_keys.append(key)
        cfg: dict[str, Any] = {}
        if src.get("query_id"):
            cfg["query_id"] = str(src["query_id"])
        if src.get("sql"):
            cfg["sql"] = str(src["sql"])
        if src.get("datastore_id"):
            cfg["datastore_id"] = str(src["datastore_id"])
        if src.get("named_params"):
            cfg["named_params"] = dict(src["named_params"])
        tasks.append(
            {
                "key": key,
                "kind": "query",
                "needs": [],
                "config": cfg,
            }
        )

    tasks.append(
        {
            "key": "blend",
            "kind": "materialize",
            "needs": list(source_keys),
            "config": {
                "combine_sql": combine_sql,
                "sources": source_keys,
                "rls_keys": rls_keys,
                "table": table,
                "database": database,
                "datastore_id": datastore_id,
                "query_id": query_id,
            },
        }
    )

    return {"version": 1, "name": name, "tasks": tasks}


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _rows_to_arrow(result: Any) -> Any:
    """Convert a source-task result (``{rows, columns}``) to a pyarrow.Table.

    The ``query`` task handler returns ``{rows, row_count, columns}`` where
    ``rows`` is a list of dicts.  We rebuild an Arrow table preserving the
    declared column order (so a SELECT * keeps source ordering even when a row
    happens to be empty / sparse).
    """
    import pyarrow as pa  # noqa: PLC0415

    if not isinstance(result, dict):
        raise AppError(
            "invalid_blend_source",
            "Each blend source result must be a dict with 'rows'.",
            400,
        )
    rows = result.get("rows") or []
    columns = result.get("columns")

    if columns:
        data: dict[str, list[Any]] = {c: [] for c in columns}
        for row in rows:
            for c in columns:
                data[c].append(row.get(c) if isinstance(row, dict) else None)
        return pa.table(data)
    # No declared columns: fall back to pyarrow inference from list-of-dicts.
    return pa.Table.from_pylist(rows)


def materialize_blend(
    config: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Merge upstream source results in DuckDB and write the materialized table.

    Parameters
    ----------
    config:
        The materialize task config: ``combine_sql``, ``sources`` (list of
        source keys), ``rls_keys``, ``table``, ``database``, ``datastore_id``,
        ``query_id``.
    inputs:
        Upstream task results keyed by task_key (the source ``key``).  Each is a
        ``{rows, row_count, columns}`` dict produced by the ``query`` handler.

    Returns
    -------
    dict
        ``{datastore_id, query_id, database, table, row_count, columns,
        rls_keys}`` — the materialization manifest (also the task result).

    Raises
    ------
    AppError
        ``missing_blend_source`` (400) if a declared source has no upstream
        result; ``rls_key_dropped`` (400) if a declared rls_key is not present
        in the combined output columns.
    """
    import duckdb  # noqa: PLC0415

    combine_sql: str = config.get("combine_sql", "")
    if not combine_sql:
        raise AppError(
            "invalid_task_config",
            "materialize task requires 'combine_sql' in config.",
            400,
        )

    source_keys: list[str] = list(config.get("sources") or list(inputs.keys()))
    rls_keys: list[str] = list(config.get("rls_keys") or [])
    table: str = config.get("table") or DEFAULT_BLEND_TABLE
    database: str = config.get("database") or ""
    datastore_id: str | None = config.get("datastore_id")
    query_id: str | None = config.get("query_id")

    if not database:
        raise AppError(
            "invalid_task_config",
            "materialize task requires 'database' (DuckDB file path) in config.",
            400,
        )

    # Ensure the target directory exists.
    os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)

    # ── 1. Merge the sources in a fresh in-memory DuckDB ──────────────────────
    conn = duckdb.connect(database=":memory:")
    try:
        for key in source_keys:
            if key not in inputs:
                raise AppError(
                    "missing_blend_source",
                    f"Blend source {key!r} produced no upstream result.",
                    400,
                )
            arrow_tbl = _rows_to_arrow(inputs[key])
            # register() exposes the Arrow table to combine_sql under its key.
            conn.register(key, arrow_tbl)

        result_rel = conn.execute(combine_sql)
        combined = result_rel.arrow()
        if hasattr(combined, "read_all"):
            combined = combined.read_all()

        columns: list[str] = list(combined.schema.names)

        # ── 2. RLS-key preservation check (CRITICAL for the wedge) ────────────
        missing = [k for k in rls_keys if k not in columns]
        if missing:
            raise AppError(
                "rls_key_dropped",
                f"Blend combine_sql dropped declared rls_keys {missing!r}; "
                "the materialized table must keep them so the planner can "
                "inject WHERE <key> = <claim> at read time. Combined columns: "
                f"{columns!r}.",
                400,
            )

        row_count = combined.num_rows

        # ── 3. Write the materialized table to the on-disk DuckDB file ────────
        # Open the target file, replace the blend table from the Arrow result.
        # Writing into a fresh connection keeps the read path (read-only open)
        # consistent with how routes/query.py opens duckdb datastores.
        out = duckdb.connect(database=database)
        try:
            out.register("_blend_src", combined)
            out.execute(f'DROP TABLE IF EXISTS "{table}"')
            out.execute(f'CREATE TABLE "{table}" AS SELECT * FROM _blend_src')
            out.unregister("_blend_src")
        finally:
            out.close()
    finally:
        conn.close()

    # ── 4. Register the runtime query so reads resolve without a restart ──────
    if query_id and datastore_id:
        register_blend_query(
            query_id=query_id,
            database=database,
            table=table,
            datastore_id=datastore_id,
        )

    return {
        "datastore_id": datastore_id,
        "query_id": query_id,
        "database": database,
        "table": table,
        "row_count": row_count,
        "columns": columns,
        "rls_keys": rls_keys,
    }


def register_blend_query(
    query_id: str,
    database: str,
    table: str,
    datastore_id: str,
) -> None:
    """Register a ``SELECT * FROM <table>`` query bound to the blend datastore.

    The materialized dataset is served like any other single-source datastore:
    the read path resolves ``datastore_id`` (type=duckdb, config.database) and
    the planner injects RLS predicates on the output columns.
    """
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    registry = get_query_registry()
    registry.register(
        id=str(query_id),
        sql=f'SELECT * FROM "{table}"',
        name=f"Blend — {table}",
        datastore_id=str(datastore_id),
    )
