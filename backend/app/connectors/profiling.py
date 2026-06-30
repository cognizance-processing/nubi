"""Column profiling for Nubi datasets and Parquet files.

Computes per-column statistics in a SINGLE DuckDB pass:
  * ``null_rate``     — fraction of NULL values (0.0–1.0)
  * ``distinct_count``— approximate distinct count (HLL-based, bounded)
  * ``min``           — minimum value (NULL for non-orderable types)
  * ``max``           — maximum value (NULL for non-orderable types)
  * ``type``          — DuckDB column type string (e.g. ``"VARCHAR"``, ``"DOUBLE"``)

Bounding
--------
Large tables are sampled rather than fully scanned.  The default sample cap is
100 000 rows — overridable via ``NUBI_PROFILE_SAMPLE_ROWS`` env var (set to
``0`` or ``"none"`` to scan the entire table; not recommended for production).
The distinct-count uses DuckDB's ``approx_count_distinct`` (HyperLogLog) so it
is always O(1) memory regardless of cardinality.

Usage
-----
::

    from app.connectors.profiling import profile_parquet, profile_table

    # Profile a local or remote Parquet file:
    result = profile_parquet("/tmp/data.parquet", connector)

    # Profile a DuckDB-registered view / table:
    result = profile_table("my_table", connector)

    # Shape of result:
    # {
    #   "row_count": 42000,
    #   "sampled":   True,
    #   "sample_rows": 100000,
    #   "columns": [
    #     {
    #       "name":           "order_id",
    #       "type":           "INTEGER",
    #       "null_rate":      0.0,
    #       "distinct_count": 42000,
    #       "min":            "1",
    #       "max":            "42000",
    #     },
    #     ...
    #   ]
    # }
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.connectors.duckdb_storage import DuckDBStorageConnector


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _sample_cap() -> int:
    """Return the row sample cap from env (0 = no cap)."""
    raw = os.getenv("NUBI_PROFILE_SAMPLE_ROWS", "100000").strip().lower()
    if raw in ("0", "none", "off", ""):
        return 0
    try:
        v = int(raw)
        return max(0, v)
    except ValueError:
        return 100_000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _profile_sql(source_expr: str, col_names: list[str], col_types: list[str]) -> str:
    """Build a single-pass SQL query that computes all column stats at once.

    Uses DuckDB aggregate functions — one ``SELECT`` over *source_expr* computes
    null_rate, approx_count_distinct, min, max for every column simultaneously.
    Min/max are cast to VARCHAR for uniform output (avoid type-dependent
    serialisation issues with dates, timestamps, etc.).

    Parameters
    ----------
    source_expr:
        A SQL expression usable in FROM, e.g. ``"read_parquet('/path')"`` or
        a table/view name like ``"my_table"``.
    col_names:
        List of column names (from the schema).
    col_types:
        Corresponding DuckDB type strings (same length as *col_names*).
    """
    if not col_names:
        return f"SELECT COUNT(*) AS __row_count FROM {source_expr}"

    # Per-column aggregates — flatten into a single SELECT.
    parts: list[str] = ["COUNT(*) AS __row_count"]
    for name, ctype in zip(col_names, col_types):
        q = f'"{name.replace(chr(34), chr(34)+chr(34))}"'  # escape embedded quotes
        # null_rate = fraction of NULLs.
        parts.append(f"COUNT(*) FILTER ({q} IS NOT NULL) AS __nn_{name}")
        # approx_count_distinct — HLL-based, bounded memory.
        parts.append(f"approx_count_distinct({q}) AS __dc_{name}")
        # min / max: cast to VARCHAR for uniform serialisation.
        # Non-orderable types (BLOB, LIST, STRUCT, etc.) will produce NULL here.
        _is_orderable = _type_is_orderable(ctype)
        if _is_orderable:
            parts.append(f"TRY_CAST(MIN({q}) AS VARCHAR) AS __min_{name}")
            parts.append(f"TRY_CAST(MAX({q}) AS VARCHAR) AS __max_{name}")
        else:
            parts.append(f"NULL AS __min_{name}")
            parts.append(f"NULL AS __max_{name}")

    return f"SELECT {', '.join(parts)} FROM {source_expr}"


def _type_is_orderable(ctype: str) -> bool:
    """Return True for column types that support MIN/MAX.

    Heuristic: exclude compound types (STRUCT, MAP, LIST, ARRAY, UNION) and
    BLOBs; everything else (numeric, string, date/time, bool, UUID) is orderable.
    """
    ct = ctype.upper().strip()
    for prefix in ("STRUCT", "MAP", "LIST", "ARRAY", "UNION", "BLOB", "BIT"):
        if ct.startswith(prefix):
            return False
    return True


def _describe_source(conn: "_DuckDBStorageConnector", source_expr: str) -> list[tuple[str, str]]:
    """Return [(col_name, col_type), ...] for *source_expr* via DESCRIBE."""
    try:
        rel = conn._inner._conn.execute(f"DESCRIBE SELECT * FROM {source_expr} LIMIT 0")
        rows = rel.fetchall()
        # DESCRIBE columns: column_name, column_type, null, key, default, extra
        return [(row[0], row[1]) for row in rows]
    except Exception as exc:
        from app.errors import AppError  # noqa: PLC0415
        raise AppError(
            "profile_error",
            f"Could not inspect schema of {source_expr!r}: {exc}",
            status=400,
        ) from exc


def _build_result(
    row: Any,
    col_names: list[str],
    col_types: list[str],
    total_rows: int,
    sampled: bool,
    cap: int,
) -> dict[str, Any]:
    """Assemble the profile result dict from the aggregation *row*."""
    columns: list[dict[str, Any]] = []
    for name, ctype in zip(col_names, col_types):
        nn = getattr(row, f"__nn_{name}", None)
        dc = getattr(row, f"__dc_{name}", None)
        mn = getattr(row, f"__min_{name}", None)
        mx = getattr(row, f"__max_{name}", None)
        null_rate = (1.0 - (nn / total_rows)) if total_rows > 0 and nn is not None else 0.0
        columns.append({
            "name": name,
            "type": ctype,
            "null_rate": round(null_rate, 6),
            "distinct_count": int(dc) if dc is not None else 0,
            "min": str(mn) if mn is not None else None,
            "max": str(mx) if mx is not None else None,
        })
    return {
        "row_count": total_rows,
        "sampled": sampled,
        "sample_rows": cap if sampled else total_rows,
        "columns": columns,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def profile_table(
    table_expr: str,
    connector: "_DuckDBStorageConnector",
    *,
    sample_rows: int | None = None,
) -> dict[str, Any]:
    """Profile the columns of a DuckDB table/view *table_expr*.

    Parameters
    ----------
    table_expr:
        A SQL identifier or expression queryable in FROM, e.g. ``"orders"`` or
        ``"read_parquet('gs://bucket/data.parquet')"``
    connector:
        A :class:`~app.connectors.duckdb_storage.DuckDBStorageConnector`
        (or any connector whose ``._inner._conn`` is a DuckDB connection).
    sample_rows:
        Override the env-configured sample cap for this call.  ``0`` or ``None``
        means use the env cap (which may itself be 0 = no cap).

    Returns
    -------
    dict
        ``{row_count, sampled, sample_rows, columns: [{name, type, null_rate,
        distinct_count, min, max}]}``
    """
    cap = sample_rows if sample_rows is not None else _sample_cap()
    schema = _describe_source(connector, table_expr)
    col_names = [r[0] for r in schema]
    col_types = [r[1] for r in schema]

    if cap > 0:
        # Count actual rows first so we can set sampled=True only when needed.
        # Use a simple LIMIT-based sample (deterministic, no random seed overhead).
        count_sql = f"SELECT COUNT(*) AS n FROM (SELECT 1 FROM {table_expr} LIMIT {cap + 1})"
        count_row = connector._inner._conn.execute(count_sql).fetchone()
        visible = int(count_row[0]) if count_row else 0
        sampled = visible > cap
        source_expr = f"(SELECT * FROM {table_expr} USING SAMPLE {cap} ROWS)" if sampled else table_expr
    else:
        sampled = False
        source_expr = table_expr
        cap = 0

    sql = _profile_sql(source_expr, col_names, col_types)
    try:
        rel = connector._inner._conn.execute(sql)
        row = rel.fetchone()
    except Exception as exc:
        from app.errors import AppError  # noqa: PLC0415
        raise AppError(
            "profile_error",
            f"Profiling query failed for {table_expr!r}: {exc}",
            status=500,
        ) from exc

    if row is None:
        return {
            "row_count": 0,
            "sampled": False,
            "sample_rows": 0,
            "columns": [{"name": n, "type": t, "null_rate": 0.0,
                         "distinct_count": 0, "min": None, "max": None}
                        for n, t in zip(col_names, col_types)],
        }

    total_rows = int(row[0])  # __row_count is always first
    # Build a named-tuple-style accessor by index.
    col_map = {desc[0]: i for i, desc in enumerate(rel.description)} if rel.description else {}

    class _Row:
        def __init__(self, data, mapping):
            self._d = data
            self._m = mapping

        def __getattr__(self, name):
            idx = self._m.get(name)
            if idx is None:
                return None
            return self._d[idx]

    row_obj = _Row(row, col_map)
    return _build_result(row_obj, col_names, col_types, total_rows, sampled, cap)


def profile_parquet(
    uri: str,
    connector: "_DuckDBStorageConnector",
    *,
    sample_rows: int | None = None,
) -> dict[str, Any]:
    """Profile the columns of a Parquet file at *uri*.

    Convenience wrapper around :func:`profile_table` that wraps *uri* in
    DuckDB's ``read_parquet()`` function.  The connector must already be
    configured for the URI's scheme (httpfs + secret loaded for ``gs://``/``s3://``).

    Parameters
    ----------
    uri:
        Local path or cloud URI (``/abs/path/data.parquet``, ``gs://…``, ``s3://…``).
    connector:
        An appropriately-configured DuckDB connector.
    sample_rows:
        Override the row sample cap.
    """
    # Strip file:// for local paths.
    effective = uri
    if effective.startswith("file://"):
        effective = effective[len("file://"):]

    source_expr = f"read_parquet('{effective}')"
    return profile_table(source_expr, connector, sample_rows=sample_rows)
