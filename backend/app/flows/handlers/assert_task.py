"""Task handler for the ``assert`` flow task kind.

Data-quality audit that runs expectations against a target table/query and
FAILS the run on any violation — the SQLMesh-audit equivalent for Nubi flows.

Handler signature
-----------------
``handle(config, ctx, claims) -> dict``

Config shape
------------
::

    {
        # Required: at least one expectation.
        "target":     "schema.table",       # table or CTE alias (see note)
        "datastore_id": "...",              # optional BYO connector UUID

        "expectations": [
            # ── Row count ──────────────────────────────────────────────────
            {
                "kind": "row_count",
                "min": 1,         # optional: table must have >= min rows
                "max": 1000000,   # optional: table must have <= max rows
                "exact": 42,      # optional: table must have exactly N rows
                # at least one of min / max / exact is required
            },

            # ── Not-null ────────────────────────────────────────────────────
            {
                "kind": "not_null",
                "columns": ["id", "created_at"],  # required: non-empty list
            },

            # ── Unique ──────────────────────────────────────────────────────
            {
                "kind": "unique",
                # Single column:  "column": "id"
                # Column tuple:   "columns": ["order_id", "line_num"]
                "columns": ["order_id", "line_num"],
            },

            # ── Custom SQL ──────────────────────────────────────────────────
            {
                "kind":   "custom_sql",
                "sql":    "SELECT id FROM {{ target }} WHERE amount < 0",
                # Passes when the SELECT returns 0 rows.
                # OR: "sql": "SELECT COUNT(*) = 0 AS ok FROM {{ target }}"
                # Passes when the single-cell scalar is truthy.
                "name":   "no_negative_amounts",  # optional human label
            },
        ]
    }

``target`` note
~~~~~~~~~~~~~~~
For BYO-warehouse tasks the ``target`` is a fully-qualified table identifier
(``schema.table``).  When running against the demo DuckDB connector you can
pass either a table name registered via upstream ``query`` tasks (Python→SQL
bridge) or a literal SQL sub-query string (e.g. ``"SELECT … FROM demo"``).
The handler does NOT create tables; it queries whatever the connector exposes.

Execution path
~~~~~~~~~~~~~~
The handler resolves the connector exactly the same way as
``_handle_query``/``_execute_query_with_bridge`` — via ``_resolve_flow_connector``
for BYO connectors or the demo DuckDB fallback — and honours the same RLS /
governance path.  It does NOT bypass the planner.

Returns
-------
dict
    ``{passed, failed, results: [{expectation, passed, detail}]}``.
    Always raises on any failure so the flow run is marked failed.

Raises
------
RuntimeError
    When one or more expectations fail.  The error message names every
    failing expectation and its actual value so alerting surfaces the details.
AppError("invalid_task_config", 400)
    When the config is missing required fields.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext

logger = logging.getLogger("nubi.flows.assert_task")

# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Run data-quality expectations and fail the run on any violation.

    Parameters
    ----------
    config:
        Task config dict (already template-resolved by the executor).
    ctx:
        Task execution context.
    claims:
        Caller auth claims — forwarded to the planner for RLS injection.

    Returns
    -------
    dict
        ``{passed, failed, total, results}`` where *results* is a list of
        per-expectation dicts ``{expectation, passed, detail}``.

    Raises
    ------
    RuntimeError
        When any expectation fails; message lists all violations.
    AppError("invalid_task_config", 400)
        When no expectations are provided.
    """
    from app.errors import AppError  # noqa: PLC0415

    expectations: Any = config.get("expectations")
    if not expectations or not isinstance(expectations, list):
        raise AppError(
            "invalid_task_config",
            "assert task requires 'expectations' (non-empty list) in config.",
            400,
        )

    target: str = str(config.get("target") or "").strip()
    datastore_id: str | None = config.get("datastore_id") or None

    # ── Resolve connector (same logic as _handle_query / _execute_query) ──────
    connector, dialect = _resolve_connector(datastore_id, ctx, claims)

    # ── Seed demo table if using the fallback connector ────────────────────────
    if datastore_id is None:
        _seed_demo(connector, ctx)

    # ── Run each expectation ──────────────────────────────────────────────────
    results: list[dict[str, Any]] = []
    for exp in expectations:
        if not isinstance(exp, dict):
            raise AppError(
                "invalid_task_config",
                f"assert: each expectation must be a dict; got {type(exp).__name__!r}.",
                400,
            )
        kind: str = str(exp.get("kind") or "").lower().strip()
        result = _run_expectation(kind, exp, target, connector, dialect, ctx, claims)
        results.append(result)

    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]

    if failed:
        failure_msgs = "; ".join(
            f"{r['expectation']}: {r['detail']}" for r in failed
        )
        raise RuntimeError(
            f"assert task: {len(failed)} of {len(results)} expectation(s) failed — "
            f"{failure_msgs}"
        )

    logger.info("assert: all %d expectation(s) passed", len(results))
    return {
        "passed": len(passed),
        "failed": 0,
        "total": len(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Connector resolution
# ---------------------------------------------------------------------------


def _resolve_connector(
    datastore_id: str | None,
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> tuple[Any, str]:
    """Resolve the connector to run expectations against.

    Mirrors the connector-resolution pattern in
    ``executor._execute_query_with_bridge`` / ``registry._handle_query``.

    Returns ``(connector, dialect)``.
    """
    if datastore_id:
        from app.flows.registry import _resolve_flow_connector  # noqa: PLC0415
        org_id: str = (getattr(ctx, "org_id", None) or "") or (claims or {}).get("org_id", "") or ""
        connector, dialect = _resolve_flow_connector(datastore_id, org_id)
        return connector, dialect
    else:
        from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415
        return DuckDBConnector(), "duckdb"


def _seed_demo(connector: Any, ctx: "TaskContext") -> None:
    """Seed the demo table and any Python-bridge tables into *connector*."""
    try:
        import pyarrow as pa  # noqa: PLC0415
        demo = pa.table(
            {
                "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
                "name": pa.array(["alpha", "beta", "gamma", "delta", "epsilon"]),
                "active": pa.array([True, True, False, True, False]),
                "value": pa.array([10.0, 20.0, 30.0, 40.0, 50.0], type=pa.float64()),
            }
        )
        tables_to_register: dict[str, Any] = {"demo": demo}
        # Register Python→SQL bridge tables (upstream python cell outputs).
        for cell_key, result in (ctx.inputs or {}).items():
            if isinstance(result, dict) and result.get("rows"):
                try:
                    bridge_table = pa.Table.from_pylist(result["rows"])
                    tables_to_register[cell_key] = bridge_table
                except Exception:  # noqa: BLE001
                    pass
        connector.register(tables_to_register)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Per-expectation runners
# ---------------------------------------------------------------------------


def _run_expectation(
    kind: str,
    exp: dict[str, Any],
    target: str,
    connector: Any,
    dialect: str,
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch to the correct expectation runner; return a result dict."""
    from app.errors import AppError  # noqa: PLC0415

    if kind == "row_count":
        return _expect_row_count(exp, target, connector, dialect, claims)
    elif kind == "not_null":
        return _expect_not_null(exp, target, connector, dialect, claims)
    elif kind == "unique":
        return _expect_unique(exp, target, connector, dialect, claims)
    elif kind == "custom_sql":
        return _expect_custom_sql(exp, target, connector, dialect, claims, ctx)
    else:
        raise AppError(
            "invalid_task_config",
            f"assert: unsupported expectation kind {kind!r}. "
            "Supported: row_count, not_null, unique, custom_sql.",
            400,
        )


def _execute_scalar_sql(
    sql: str,
    connector: Any,
    dialect: str,
    claims: dict[str, Any],
) -> Any:
    """Execute *sql* via the planner (RLS honoured) and return the first cell value."""
    from app.connectors.planner import plan  # noqa: PLC0415

    physical_plan = plan(sql, claims=claims, params=[], dialect=dialect)
    arrow_table = connector.execute(physical_plan)
    if arrow_table.num_rows == 0 or len(arrow_table.schema.names) == 0:
        return None
    col = arrow_table.column(0)
    return col[0].as_py() if len(col) > 0 else None


def _execute_count_sql(
    sql: str,
    connector: Any,
    dialect: str,
    claims: dict[str, Any],
) -> int:
    """Execute *sql* via the planner and return the first-cell integer value."""
    val = _execute_scalar_sql(sql, connector, dialect, claims)
    if val is None:
        return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _execute_row_count_sql(
    sql: str,
    connector: Any,
    dialect: str,
    claims: dict[str, Any],
) -> int:
    """Execute *sql* and return the number of result rows (not a scalar aggregate)."""
    from app.connectors.planner import plan  # noqa: PLC0415

    physical_plan = plan(sql, claims=claims, params=[], dialect=dialect)
    arrow_table = connector.execute(physical_plan)
    return int(arrow_table.num_rows)


def _safe_col_name(name: str) -> str:
    """Quote a column name with double-quotes for safe SQL embedding."""
    return '"' + name.replace('"', '""') + '"'


def _safe_table_ref(target: str) -> str:
    """Return *target* safe for use in a FROM clause.

    If *target* looks like a parenthesised sub-query or contains spaces it is
    returned as-is; otherwise it is treated as a dot-qualified identifier and
    each part is double-quoted.

    This is intentionally conservative — the assertion SQL is authored by a
    flow author (not an untrusted end user), but we still reject obvious
    injection patterns.
    """
    t = target.strip()
    if not t:
        return "demo"  # default to the seeded demo table
    if t.startswith("(") and t.endswith(")"):
        return t  # sub-query passthrough
    # Validate and quote each dot-separated part.
    parts = t.split(".")
    quoted = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        # Allow alphanumeric, underscore, hyphen, dollar (common in warehouse IDs).
        import re  # noqa: PLC0415
        if not re.match(r'^[A-Za-z0-9_\-\$]+$', p):
            raise ValueError(
                f"assert: target part {p!r} contains characters that are not "
                "allowed in a table identifier. "
                "Use a sub-query (SELECT ...) for complex references."
            )
        quoted.append(f'"{p}"')
    return ".".join(quoted) if quoted else "demo"


# ── row_count ──────────────────────────────────────────────────────────────

def _expect_row_count(
    exp: dict[str, Any],
    target: str,
    connector: Any,
    dialect: str,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Expectation: ``row_count`` — validates the row count of *target*."""
    from app.errors import AppError  # noqa: PLC0415

    min_rows: int | None = exp.get("min")
    max_rows: int | None = exp.get("max")
    exact: int | None = exp.get("exact")

    if min_rows is None and max_rows is None and exact is None:
        raise AppError(
            "invalid_task_config",
            "assert row_count: at least one of 'min', 'max', or 'exact' is required.",
            400,
        )

    table_ref = _safe_table_ref(target)
    sql = f"SELECT COUNT(*) FROM {table_ref}"
    actual: int = _execute_count_sql(sql, connector, dialect, claims)

    issues: list[str] = []
    if exact is not None and actual != int(exact):
        issues.append(f"expected exactly {exact} rows, got {actual}")
    if min_rows is not None and actual < int(min_rows):
        issues.append(f"expected >= {min_rows} rows, got {actual}")
    if max_rows is not None and actual > int(max_rows):
        issues.append(f"expected <= {max_rows} rows, got {actual}")

    label = f"row_count({table_ref})"
    if issues:
        detail = "; ".join(issues)
        return {"expectation": label, "passed": False, "detail": detail}
    return {"expectation": label, "passed": True, "detail": f"row_count={actual}"}


# ── not_null ──────────────────────────────────────────────────────────────

def _expect_not_null(
    exp: dict[str, Any],
    target: str,
    connector: Any,
    dialect: str,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Expectation: ``not_null`` — checks that named columns have no NULLs."""
    from app.errors import AppError  # noqa: PLC0415

    columns: Any = exp.get("columns")
    if not columns or not isinstance(columns, list):
        raise AppError(
            "invalid_task_config",
            "assert not_null: 'columns' (non-empty list) is required.",
            400,
        )

    table_ref = _safe_table_ref(target)
    issues: list[str] = []

    for col in columns:
        col_str = str(col).strip()
        col_quoted = _safe_col_name(col_str)
        sql = f"SELECT COUNT(*) FROM {table_ref} WHERE {col_quoted} IS NULL"
        null_count: int = _execute_count_sql(sql, connector, dialect, claims)
        if null_count > 0:
            issues.append(f"column {col_str!r} has {null_count} NULL value(s)")

    label = f"not_null({table_ref}, [{', '.join(str(c) for c in columns)}])"
    if issues:
        return {"expectation": label, "passed": False, "detail": "; ".join(issues)}
    return {"expectation": label, "passed": True, "detail": "no NULLs found"}


# ── unique ────────────────────────────────────────────────────────────────

def _expect_unique(
    exp: dict[str, Any],
    target: str,
    connector: Any,
    dialect: str,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Expectation: ``unique`` — checks that a column or column-tuple is unique."""
    from app.errors import AppError  # noqa: PLC0415

    # Accept either "column" (single str) or "columns" (list).
    single_col: Any = exp.get("column")
    multi_cols: Any = exp.get("columns")

    if single_col and not multi_cols:
        cols_list = [str(single_col).strip()]
    elif multi_cols and isinstance(multi_cols, list):
        cols_list = [str(c).strip() for c in multi_cols]
    else:
        raise AppError(
            "invalid_task_config",
            "assert unique: 'column' (str) or 'columns' (list) is required.",
            400,
        )

    table_ref = _safe_table_ref(target)
    col_exprs = ", ".join(_safe_col_name(c) for c in cols_list)
    sql = (
        f"SELECT COUNT(*) FROM ("
        f"SELECT {col_exprs} FROM {table_ref} "
        f"GROUP BY {col_exprs} HAVING COUNT(*) > 1"
        f") AS __dup__"
    )
    dup_count: int = _execute_count_sql(sql, connector, dialect, claims)

    label = f"unique({table_ref}, [{', '.join(cols_list)}])"
    if dup_count > 0:
        return {
            "expectation": label,
            "passed": False,
            "detail": f"{dup_count} duplicate group(s) found",
        }
    return {"expectation": label, "passed": True, "detail": "all values unique"}


# ── custom_sql ────────────────────────────────────────────────────────────

def _expect_custom_sql(
    exp: dict[str, Any],
    target: str,
    connector: Any,
    dialect: str,
    claims: dict[str, Any],
    ctx: "TaskContext",
) -> dict[str, Any]:
    """Expectation: ``custom_sql`` — author-supplied SQL assertion.

    Two supported modes:

    1. **Zero-row mode** (default): the SQL is a SELECT that must return 0 rows.
       Any rows returned are violations.
    2. **Scalar-boolean mode**: the SQL returns a single row with a single cell
       whose value is truthy (1 / True / "true") → pass; falsy → fail.
       The handler auto-detects this by checking if the result has exactly 1 row
       and 1 column.

    ``{{ target }}`` in the SQL is replaced with the safe, quoted table ref.
    """
    from app.errors import AppError  # noqa: PLC0415

    raw_sql: Any = exp.get("sql")
    if not raw_sql or not isinstance(raw_sql, str) or not raw_sql.strip():
        raise AppError(
            "invalid_task_config",
            "assert custom_sql: 'sql' (non-empty string) is required.",
            400,
        )

    name: str = str(exp.get("name") or "custom_sql").strip()

    # Replace {{ target }} placeholder with the safe table ref.
    table_ref = _safe_table_ref(target) if target else ""
    sql = raw_sql.replace("{{ target }}", table_ref).replace("{{target}}", table_ref)

    from app.connectors.planner import plan  # noqa: PLC0415

    physical_plan = plan(sql, claims=claims, params=[], dialect=dialect)
    arrow_table = connector.execute(physical_plan)

    n_rows = int(arrow_table.num_rows)
    n_cols = len(arrow_table.schema.names)

    # ── Scalar-boolean mode ───────────────────────────────────────────────────
    if n_rows == 1 and n_cols == 1:
        val = arrow_table.column(0)[0].as_py()
        # Truthy: 1, True, "true", "1", "yes", "t"
        if isinstance(val, bool):
            scalar_pass = val
        elif isinstance(val, (int, float)):
            scalar_pass = bool(val)
        elif isinstance(val, str):
            scalar_pass = val.strip().lower() in ("1", "true", "yes", "t")
        else:
            scalar_pass = bool(val)

        label = f"custom_sql:{name}"
        if scalar_pass:
            return {"expectation": label, "passed": True, "detail": f"scalar result={val!r}"}
        return {
            "expectation": label,
            "passed": False,
            "detail": f"scalar result={val!r} is falsy",
        }

    # ── Zero-row mode ─────────────────────────────────────────────────────────
    label = f"custom_sql:{name}"
    if n_rows == 0:
        return {"expectation": label, "passed": True, "detail": "returned 0 rows (pass)"}
    return {
        "expectation": label,
        "passed": False,
        "detail": f"returned {n_rows} violation row(s) (expected 0)",
    }
