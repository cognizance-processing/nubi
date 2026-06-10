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
import re
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
    *,
    env: str = "prod",
    flow: dict[str, Any] | None = None,
    watermark: str | None = None,
) -> dict[str, Any]:
    """Merge upstream source results in DuckDB and write the materialized table.

    Two persistence shapes are supported:

    - **View / blend (existing path)** — when ``config['materialized']`` is
      absent or ``kind == 'view'``, the combined result is written to the local
      DuckDB file (``config['database']``, table ``config['table']``) and a
      runtime query is registered.  This is unchanged from the original blend.
    - **Full / incremental (object-storage path)** — when
      ``config['materialized'].kind`` is ``'full'`` or ``'incremental'``, the
      combined result is persisted to an env-scoped Parquet target in object
      storage (or a local fallback dir) via
      :func:`app.flows.incremental.apply_incremental`.  Watermarks are passed in
      / returned for the caller (runtime) to persist in Postgres.

    Parameters
    ----------
    config:
        The materialize task config: ``combine_sql``, ``sources`` (list of
        source keys), ``rls_keys``, ``table``, ``database``, ``datastore_id``,
        ``query_id``, and the optional nested ``materialized`` block.
    inputs:
        Upstream task results keyed by task_key (the source ``key``).  Each is a
        ``{rows, row_count, columns}`` dict produced by the ``query`` handler.
    env:
        Active environment ("dev"/"prod"/custom).  Namespaces full/incremental
        targets so dev and prod never clobber each other.
    flow:
        The flow dict (used to resolve ``runtime_config.materialize_base_uri``).
        Postgres-free — this handler never touches the DB.
    watermark:
        Stored watermark (ISO string) for incremental kinds, or ``None``.

    Returns
    -------
    dict
        The materialization manifest (also the task result).  For view/blend:
        ``{datastore_id, query_id, database, table, row_count, columns,
        rls_keys, materialized_kind}``.  For full/incremental additionally:
        ``physical_target``, ``env``, ``rows_written``, ``new_watermark``.

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
    datastore_id: str | None = config.get("datastore_id")
    query_id: str | None = config.get("query_id")

    materialized: dict[str, Any] = dict(config.get("materialized") or {})
    mat_kind: str = str(materialized.get("kind") or "view").lower()
    is_persisted = mat_kind in ("full", "incremental")

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

        # ── 3a. Persisted (full/incremental) → env-scoped object-storage target
        if is_persisted:
            from app.flows.incremental import (  # noqa: PLC0415
                apply_incremental,
                resolve_target_uri,
            )

            settings = _get_settings()
            physical_target = resolve_target_uri(env, materialized, flow, settings)
            mat_for_apply = dict(materialized)
            mat_for_apply["__physical_target__"] = physical_target

            storage = _open_storage_connector(physical_target)
            try:
                from datetime import datetime, timezone  # noqa: PLC0415

                rows_written, new_watermark = apply_incremental(
                    storage,
                    combined,
                    mat_for_apply,
                    watermark,
                    datetime.now(timezone.utc),
                )
            finally:
                _close_storage_connector(storage)

            return {
                "datastore_id": datastore_id,
                "query_id": query_id,
                "table": table,
                "row_count": row_count,
                "columns": columns,
                "rls_keys": rls_keys,
                "materialized_kind": mat_kind,
                "physical_target": physical_target,
                "env": env,
                "rows_written": rows_written,
                "new_watermark": new_watermark,
            }

        # ── 3b. View / blend (existing local DuckDB path) ─────────────────────
        database: str = config.get("database") or ""
        if not database:
            raise AppError(
                "invalid_task_config",
                "materialize task requires 'database' (DuckDB file path) in config.",
                400,
            )
        # Ensure the target directory exists.
        os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)

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
        "materialized_kind": "view",
    }


# ---------------------------------------------------------------------------
# Object-storage connector helpers (full / incremental targets)
# ---------------------------------------------------------------------------


def _get_settings() -> Any:
    """Return the app settings object, or ``None`` if unavailable."""
    try:
        from app.config import get_settings  # noqa: PLC0415

        return get_settings()
    except Exception:  # noqa: BLE001
        return None


def _open_storage_connector(physical_target: str) -> Any:
    """Open a DuckDBStorageConnector suitable for writing *physical_target*.

    For ``s3://`` targets the connector installs httpfs + the S3 secret; for
    local paths it uses an in-memory DuckDB connection that can COPY TO / read
    Parquet on the local filesystem.
    """
    from app.connectors.duckdb_storage import DuckDBStorageConnector  # noqa: PLC0415

    effective = (
        physical_target[len("file://"):]
        if physical_target.startswith("file://")
        else physical_target
    )
    is_remote = bool(re.match(r"^(s3|s3a|gs|gcs|az|abfss?)://", effective, re.IGNORECASE))
    if is_remote:
        settings = _get_settings()
        cfg: dict[str, Any] = {"database": effective}
        # Pull S3 credentials from settings when present (best-effort).
        for src, dst in (
            ("S3_ENDPOINT", "endpoint"),
            ("S3_ACCESS_KEY_ID", "access_key_id"),
            ("S3_SECRET_ACCESS_KEY", "secret_access_key"),
            ("S3_REGION", "region"),
            ("AWS_ACCESS_KEY_ID", "access_key_id"),
            ("AWS_SECRET_ACCESS_KEY", "secret_access_key"),
            ("AWS_REGION", "region"),
        ):
            val = getattr(settings, src, None) if settings is not None else None
            if val and dst not in cfg:
                cfg[dst] = val
        return DuckDBStorageConnector.from_config(cfg)
    # Local target: an in-memory connection can COPY TO / read local Parquet.
    return DuckDBStorageConnector.for_memory()


def _close_storage_connector(storage: Any) -> None:
    """Best-effort close of a storage connector's raw connection."""
    try:
        inner = getattr(storage, "_inner", None)
        conn = getattr(inner, "_conn", None) if inner is not None else None
        if conn is not None:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


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


# ---------------------------------------------------------------------------
# Universal rollup refresh via ANY connector (Managed Lakehouse §2)
# ---------------------------------------------------------------------------
#
# The pre-agg system has three parts and only the *refresh* is per-connector
# (MANAGED_LAKEHOUSE.md §1):
#
#   1. Rewrite / routing  — connector-agnostic (planner.route_to_rollup_shape).
#   2. Materialization    — ALWAYS lands in the lakehouse (Parquet/DuckDB),
#                           regardless of where the base data lives.
#   3. Refresh            — run the aggregate via ``connector.execute(plan)``.
#
# ``refresh_rollup`` below generalises the DuckDB-only ``preagg.build_rollup``
# write path so the aggregate can be executed against ANY connector and the
# small grouped result landed as a lakehouse rollup.  RLS-key columns stay IN
# the rollup grain (the rls_keys check is preserved verbatim) and incremental
# refresh via a watermark is supported when the candidate declares a
# ``time_column``.


def _build_aggregate_sql(
    *,
    source_table: str,
    dimensions: list[str],
    measures: list[str],
    rls_keys: list[str],
    time_column: str | None = None,
    watermark: str | None = None,
) -> str:
    """Build the rollup aggregate SQL for refresh against a source connector.

    Mirrors :func:`app.connectors.preagg.build_rollup_sql` (RLS keys are added
    to BOTH the SELECT and the GROUP BY so the rollup keeps a row per
    ``(rls_key, dims)`` combination — pre-aggregating across the RLS key would be
    unsound).  When *time_column* and *watermark* are supplied an incremental
    ``WHERE "<time_column>" > <watermark>`` predicate is added so only new base
    rows are re-aggregated.

    Security note: the watermark is an org-scoped, engine-produced value (a prior
    ``max(time_column)``), never end-user input.  It is rendered as a typed
    literal here for the refresh-time aggregate only — the SERVED rollup is read
    through the standard RLS-injecting planner path, never this SQL.
    """
    from app.connectors.preagg import build_rollup_sql  # noqa: PLC0415

    sql = build_rollup_sql(source_table, dimensions, measures, rls_keys)

    if time_column and watermark:
        # Inject the watermark predicate BEFORE the GROUP BY via the AST so we
        # never string-splice into the middle of the statement.  sqlglot binds
        # the literal as a proper node (no concatenation into an executable
        # fragment beyond a typed literal the engine re-quotes).
        import sqlglot  # noqa: PLC0415
        import sqlglot.expressions as exp  # noqa: PLC0415

        try:
            tree = sqlglot.parse_one(sql, dialect="postgres")
            if isinstance(tree, exp.Select):
                pred = exp.GT(
                    this=exp.Column(this=exp.Identifier(this=time_column, quoted=True)),
                    expression=exp.Literal.string(str(watermark)),
                )
                tree = tree.where(pred)
                sql = tree.sql(dialect="postgres")
        except Exception:  # noqa: BLE001
            # If the predicate cannot be injected safely, fall back to a full
            # refresh (correct, just less efficient) rather than risk an unsafe
            # rewrite.
            pass
    return sql


def _base_max_time(
    connector: Any,
    source_table: str,
    time_column: str,
    watermark: str | None,
) -> str | None:
    """Return ``max(time_column)`` over the base table via *connector*, or None.

    Used to advance the incremental watermark from the BASE (the rollup output
    is aggregated and no longer carries the raw time column).  Best-effort: any
    engine error returns ``None`` so a refresh is never blocked by watermark
    advancement.  The query is a baked aggregate over the source table with the
    same watermark predicate as the refresh — no end-user input, no RLS surface
    (it reads only a single timestamp, never rows).
    """
    if not time_column:
        return None
    from app.connectors.plan import PhysicalPlan  # noqa: PLC0415

    sql = f'SELECT max("{time_column}") AS __wm FROM "{source_table}"'
    if watermark:
        # Same predicate the refresh used — only consider the new tail.
        import sqlglot  # noqa: PLC0415
        import sqlglot.expressions as exp  # noqa: PLC0415

        try:
            tree = sqlglot.parse_one(sql, dialect="postgres")
            if isinstance(tree, exp.Select):
                pred = exp.GT(
                    this=exp.Column(this=exp.Identifier(this=time_column, quoted=True)),
                    expression=exp.Literal.string(str(watermark)),
                )
                sql = tree.where(pred).sql(dialect="postgres")
        except Exception:  # noqa: BLE001
            pass
    try:
        plan = PhysicalPlan(sql=sql, params=[], cache_key=f"wm:{source_table}")
        tbl = connector.execute(plan)
        if hasattr(tbl, "read_all"):
            tbl = tbl.read_all()
        if tbl.num_rows == 0:
            return None
        val = tbl.column(0)[0].as_py()
    except Exception:  # noqa: BLE001
        return None
    if val is None:
        return None
    from datetime import datetime  # noqa: PLC0415

    return val.isoformat() if isinstance(val, datetime) else str(val)


def refresh_rollup(
    connector: Any,
    candidate: dict[str, Any],
    *,
    rls_keys: list[str] | None = None,
    env: str = "prod",
    flow: dict[str, Any] | None = None,
    watermark: str | None = None,
    cache_key: str = "",
) -> dict[str, Any]:
    """Refresh a rollup by executing its aggregate against ANY connector.

    This is the universal (connector-agnostic) refresh path: instead of reading
    a DuckDB-resident base table, it asks *connector* to ``execute`` the rollup
    aggregate and lands the small grouped result as a lakehouse rollup
    (Parquet/DuckDB).  A BigQuery- or Snowflake-backed dashboard therefore pays
    the warehouse cost ONCE per refresh, not per viewer (MANAGED_LAKEHOUSE.md §2).

    Parameters
    ----------
    connector:
        Any :class:`app.connectors.base.Connector` for the rollup's source.  Its
        ``execute(plan)`` runs the aggregate and returns a ``pyarrow.Table``.
    candidate:
        The rollup spec: ``{table | source_table, dimensions, measures,
        time_column?, materialized?}``.  ``materialized`` (optional) is the
        lakehouse persistence block (``kind`` full/incremental, ``target``); when
        absent the rollup lands in a local DuckDB file (back-compat with the
        DuckDB-only path).
    rls_keys:
        Columns that MUST survive into the rollup grain so the planner can still
        inject ``WHERE <key> = <claim>`` at READ time.  Verified post-refresh; a
        dropped key raises ``rls_key_dropped`` — RLS is never weakened.
    env / flow:
        Env namespacing + base-uri resolution for the lakehouse target (same
        contract as :func:`materialize_blend`).
    watermark:
        Stored watermark (ISO string) for incremental refresh, or ``None`` for a
        full refresh.  Used only when the candidate declares a ``time_column``.

    Returns
    -------
    dict
        ``{source_table, table, dimensions, measures, rls_keys, columns,
        row_count, materialized_kind, env, ...}``.  For lakehouse-persisted
        rollups additionally ``physical_target``, ``rows_written``,
        ``new_watermark``; for the local DuckDB fallback ``database``.

    Raises
    ------
    AppError
        ``rls_key_dropped`` (400) if a declared rls_key is not present in the
        aggregate output columns.
    """
    from app.connectors.plan import PhysicalPlan  # noqa: PLC0415

    rls_keys = list(rls_keys or [])
    source_table = str(candidate.get("source_table") or candidate.get("table") or "")
    dimensions = list(candidate.get("dimensions") or [])
    measures = list(candidate.get("measures") or [])
    time_column = candidate.get("time_column") or (
        (candidate.get("materialized") or {}).get("time_column")
    )

    materialized: dict[str, Any] = dict(candidate.get("materialized") or {})
    mat_kind: str = str(materialized.get("kind") or "view").lower()

    # Incremental is SOUND only when the time grain is itself a rollup dimension
    # (or a derived bucket of it): then every grain row belongs to exactly one
    # time bucket and never straddles the watermark boundary, so re-computing the
    # new tail and upserting BY GRAIN is correct even for additive measures
    # (SUM/COUNT).  If the time_column is NOT a dimension, a partial recompute
    # of the tail would REPLACE a grain that also accumulated older rows — that
    # would under-count.  In that case we degrade to a FULL refresh (correct,
    # just re-scans everything) rather than silently produce a wrong aggregate.
    dim_set = {str(d).lower() for d in dimensions}
    time_is_dimension = bool(time_column) and str(time_column).lower() in dim_set
    is_incremental = (
        mat_kind == "incremental" and bool(time_column) and time_is_dimension
    )
    if mat_kind == "incremental" and not is_incremental:
        # Sound degradation: keep persistence in the lakehouse but overwrite.
        materialized["kind"] = "full"
        mat_kind = "full"
    is_persisted = mat_kind in ("full", "incremental")

    agg_sql = _build_aggregate_sql(
        source_table=source_table,
        dimensions=dimensions,
        measures=measures,
        rls_keys=rls_keys,
        time_column=str(time_column) if (is_incremental and watermark) else None,
        watermark=watermark if is_incremental else None,
    )

    # ── 1. Execute the aggregate against the source connector. ────────────────
    # The plan is a baked SELECT (the aggregate) — the connector runs it
    # verbatim; it never rewrites SQL or RLS (that is the planner's job).
    plan = PhysicalPlan(sql=agg_sql, params=[], cache_key=cache_key or f"rollup:{source_table}")
    result = connector.execute(plan)
    if hasattr(result, "read_all"):
        result = result.read_all()

    # For incremental refresh, advance the watermark from the BASE (the rollup
    # output is a GROUP BY and no longer carries the raw time_column).  We ask
    # the SAME connector for max(time_column) over the rows just refreshed.
    new_base_watermark: str | None = watermark
    if is_incremental:
        base_wm = _base_max_time(connector, source_table, str(time_column), watermark)
        if base_wm is not None and (watermark is None or base_wm > watermark):
            new_base_watermark = base_wm

    columns: list[str] = list(result.schema.names)

    # ── 2. RLS-key preservation check (CRITICAL — same invariant as everywhere)
    missing = [k for k in rls_keys if k not in columns]
    if missing:
        raise AppError(
            "rls_key_dropped",
            f"Rollup refresh for {source_table!r} dropped declared rls_keys "
            f"{missing!r}; the rollup must keep them so the planner can inject "
            f"WHERE <key> = <claim> at read time. Rollup columns: {columns!r}.",
            400,
        )

    row_count = result.num_rows
    rollup_table = str(candidate.get("rollup_table") or f"rollup_{source_table}")

    # ── 3a. Lakehouse-persisted (full/incremental) → Parquet in object storage.
    if is_persisted:
        from app.flows.incremental import (  # noqa: PLC0415
            apply_incremental,
            resolve_target_uri,
        )

        settings = _get_settings()
        physical_target = resolve_target_uri(env, materialized, flow, settings)
        mat_for_apply = dict(materialized)
        mat_for_apply["__physical_target__"] = physical_target

        # The watermark is applied at the BASE scan in _build_aggregate_sql (so
        # the warehouse only re-aggregates new rows).  The rollup OUTPUT is a
        # GROUP BY and does not carry the raw time_column, so apply_incremental
        # must NOT re-filter the aggregated rows by time_column — instead we
        # upsert on the rollup GRAIN (rls_keys + dimensions) so the freshly
        # recomputed grain rows replace their stale counterparts.  We point its
        # ``time_column`` at the first grain column (which DOES exist in the
        # output, satisfying its validation) and pass watermark=None so its
        # output-side filter is a no-op; the real watermark is advanced from the
        # base above (``new_base_watermark``).
        grain: list[str] = []
        for c in list(rls_keys) + list(dimensions):
            if c not in grain:
                grain.append(c)
        if mat_for_apply.get("kind") == "incremental":
            if not mat_for_apply.get("unique_key"):
                mat_for_apply["unique_key"] = list(grain)
            if grain:
                mat_for_apply["time_column"] = grain[0]
            apply_watermark: str | None = None
        else:
            apply_watermark = watermark

        storage = _open_storage_connector(physical_target)
        try:
            from datetime import datetime, timezone  # noqa: PLC0415

            rows_written, _ = apply_incremental(
                storage,
                result,
                mat_for_apply,
                apply_watermark,
                datetime.now(timezone.utc),
            )
        finally:
            _close_storage_connector(storage)
        # Advance the watermark from the base (incremental) or leave as-is.
        new_watermark = new_base_watermark if is_incremental else watermark

        return {
            "source_table": source_table,
            "table": rollup_table,
            "dimensions": sorted(dimensions),
            "measures": sorted(measures),
            "rls_keys": rls_keys,
            "columns": columns,
            "row_count": row_count,
            "materialized_kind": mat_kind,
            "env": env,
            "physical_target": physical_target,
            "rows_written": rows_written,
            "new_watermark": new_watermark,
        }

    # ── 3b. Local DuckDB fallback (back-compat with the DuckDB-only path). ─────
    import duckdb  # noqa: PLC0415

    database: str = str(candidate.get("database") or "")
    if not database:
        rollup_id = str(candidate.get("rollup_id") or f"{source_table}")
        database = os.path.join(_seed_data_dir(), "rollups", f"{rollup_id}.duckdb")
    os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)

    out = duckdb.connect(database=database)
    try:
        out.register("_rollup_src", result)
        out.execute(f'DROP TABLE IF EXISTS "{rollup_table}"')
        out.execute(f'CREATE TABLE "{rollup_table}" AS SELECT * FROM _rollup_src')
        out.unregister("_rollup_src")
    finally:
        out.close()

    return {
        "source_table": source_table,
        "table": rollup_table,
        "database": database,
        "dimensions": sorted(dimensions),
        "measures": sorted(measures),
        "rls_keys": rls_keys,
        "columns": columns,
        "row_count": row_count,
        "materialized_kind": "view",
        "env": env,
        "new_watermark": watermark,
    }
