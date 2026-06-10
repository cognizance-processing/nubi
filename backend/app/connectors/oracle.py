"""Oracle connector — Arrow output via python-oracledb (row-based conversion).

``OracleConnector`` executes the planner's ``PhysicalPlan`` against an Oracle
Database using the modern ``python-oracledb`` driver in thin mode (no Oracle
Instant Client required).  oracledb has no native Arrow path here, so rows are
fetched via the DB-API cursor and converted column-major to Arrow (mirrors the
PyMySQL fallback path in ``mysql.py``).

Placeholder translation
-----------------------
The Nubi planner always emits SQL in the ``postgres`` dialect, so ``plan.sql``
contains ``$1``, ``$2`` … positional placeholders and ``plan.params`` is the
matching positional value list (see ``app/connectors/planner.py``).  Oracle uses
numbered bind variables of the form ``:1``, ``:2`` … which line up one-to-one
with postgres ``$N`` placeholders, so this connector rewrites each ``$N`` into a
``:N`` and passes the params positionally (a Python sequence binds to ``:1``,
``:2`` … in order).  **Binding is always parameterised — values are NEVER
string-concatenated into the SQL.**

Configuration
-------------
``config`` (dict) keys:

``host`` (required)
    Database host.
``port``
    Listener port (default ``1521``).
``service_name`` / ``sid``
    Service name (preferred) or legacy SID identifying the database instance.
``user`` / ``password``
    Login credentials.  ``password`` arrives merged in from the encrypted
    secret store by ``query.py``.

Lazy import
-----------
``oracledb`` is optional, so it is imported **inside** the methods that need it.
The module imports cleanly without the driver installed; calling ``execute`` /
``execute_stream`` without it raises ``AppError("driver_unavailable", 500)`` with
an install hint.

Capabilities
------------
``native_arrow`` is ``False`` (rows are converted to Arrow in Python).
``predicate_pushdown`` / ``projection_pushdown`` / ``predicate_rls`` are ``True``
because the planner bakes those directly into the SQL sent to the server.
``partition_pushdown`` / ``column_masking`` / ``streaming_cdc`` are ``False``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.errors import AppError

SOURCE_TYPE = "oracle"

# Matches $1, $2, ... positional placeholders emitted by the planner's postgres
# dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_oracledb() -> Any:
    """Import python-oracledb lazily; return the module or raise ``AppError``."""
    try:
        import oracledb  # noqa: PLC0415

        return oracledb
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "oracledb is not installed (needed for the Oracle connector). "
                "Install it with: pip install oracledb"
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to Oracle ``:N`` bind variables.

    Oracle's numbered bind variables (``:1``, ``:2`` …) align one-to-one with
    postgres ``$N`` placeholders.  Because Oracle resolves repeated ``:N`` to the
    same positional value, the param list does not need re-ordering — the values
    bind positionally (1-based).  This still re-emits a normalised, contiguous
    param sequence so binding is robust against unused / repeated indices.

    Returns
    -------
    tuple[str, list]
        ``(rewritten_sql, ordered_params)`` — ``ordered_params`` aligns
        positionally (1-based) with the ``:N`` markers in ``rewritten_sql``.
    """
    if not params:
        return sql, []

    # Map each distinct source index to a fresh, contiguous 1-based bind number
    # so the value sequence we pass to oracledb is dense and ordered.
    next_bind = 1
    index_to_bind: dict[int, int] = {}
    ordered: list[Any] = []

    def _sub(match: "re.Match[str]") -> str:
        nonlocal next_bind
        idx = int(match.group(1)) - 1  # $N is 1-based
        if idx < 0 or idx >= len(params):
            raise AppError(
                "query_error",
                f"Placeholder ${idx + 1} has no corresponding value in plan.params "
                f"(len={len(params)}).",
                status=500,
            )
        if idx not in index_to_bind:
            index_to_bind[idx] = next_bind
            ordered.append(params[idx])
            next_bind += 1
        return f":{index_to_bind[idx]}"

    rewritten = _PG_PLACEHOLDER_RE.sub(_sub, sql)
    return rewritten, ordered


def _rows_to_arrow(columns: list[str], rows: list[tuple[Any, ...]]) -> "pa.Table":
    """Convert oracledb rows + column names to a ``pyarrow.Table``.

    Types are inferred by PyArrow per column.  An empty result still produces a
    table with the correct column names (all-null typed columns).
    """
    import pyarrow as pa  # noqa: PLC0415

    if not columns:
        return pa.table({})

    # Column-major assembly so PyArrow can infer one type per column.
    col_data: dict[str, list[Any]] = {name: [] for name in columns}
    for row in rows:
        for name, value in zip(columns, row):
            col_data[name].append(value)
    return pa.table(col_data)


class OracleConnector(Connector):
    """Connector backed by Oracle Database via python-oracledb (thin mode).

    Parameters
    ----------
    config:
        A dict of connection parameters.  See the module docstring for the
        recognised keys (``host``, ``port``, ``service_name`` / ``sid``,
        ``user``, ``password``).

    Notes
    -----
    The driver is imported lazily so the module loads without oracledb installed.
    A fresh connection is opened per ``execute`` call and closed afterwards;
    pooling is deferred (mirrors ``MySQLConnector`` / ``PostgresConnector``).
    """

    def __init__(self, config: dict) -> None:
        self._config = dict(config or {})
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return Oracle connector capability flags.

        ``native_arrow`` is ``False`` (oracledb rows are converted to Arrow in
        Python).  Push-down + RLS are ``True`` because the planner bakes
        projection / predicate / RLS directly into the SQL sent to the server.
        Partition routing, column masking, and CDC are out of scope.
        """
        return {
            "native_arrow": False,
            "predicate_pushdown": True,
            "projection_pushdown": True,
            "partition_pushdown": False,
            "predicate_rls": True,
            "column_masking": False,
            "streaming_cdc": False,
        }

    # ------------------------------------------------------------------
    # Internal: build an oracledb connection
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        """Open an oracledb connection assembled from the config keys."""
        oracledb = _import_oracledb()

        host = self._config.get("host")
        if not host:
            raise AppError(
                "config_error",
                "Oracle connector requires 'host' in config.",
                status=500,
            )
        port = self._config.get("port", 1521)
        service_name = self._config.get("service_name")
        sid = self._config.get("sid")
        user = self._config.get("user")
        password = self._config.get("password")

        if not service_name and not sid:
            raise AppError(
                "config_error",
                "Oracle connector requires 'service_name' or 'sid' in config.",
                status=500,
            )

        # Build an Easy Connect / SID DSN via the driver's helper so both
        # service_name and legacy SID styles are supported.
        if service_name:
            dsn = oracledb.makedsn(host, port, service_name=service_name)
        else:
            dsn = oracledb.makedsn(host, port, sid=sid)

        try:
            return oracledb.connect(user=user, password=password, dsn=dsn)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "driver_unavailable",
                f"Failed to connect to Oracle: {exc}",
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        ``$N`` placeholders are translated to Oracle ``:N`` bind variables and
        bound as positional parameters (never string-concat).  Rows are fetched
        via the DB-API cursor and converted column-major to Arrow.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if oracledb is absent.
            ``code="query_error"`` (500) if the query fails.
        """
        sql, params = _translate_placeholders(plan.sql, plan.params)
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                columns = [desc[0] for desc in cur.description] if cur.description else []
                rows = cur.fetchall()
            finally:
                cur.close()
            return _rows_to_arrow(columns, [tuple(r) for r in rows])
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"Oracle query failed: {exc}",
                status=500,
            ) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # pragma: no cover - best-effort close
                    pass

    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield the result as a stream of RecordBatches.

        Materialises via ``execute()`` then yields the table's batches.  True
        server-side cursor streaming is deferred (mirrors the other connectors).

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) / ``code="query_error"`` (500).
        """
        table = self.execute(plan)
        yield from table.to_batches()
