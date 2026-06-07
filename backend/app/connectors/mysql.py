"""MySQL connector — Arrow output via connectorx (fast path) or PyMySQL (bound-param path).

``MySQLConnector`` executes the planner's ``PhysicalPlan`` against a MySQL /
MariaDB server (they share the MySQL wire protocol) and returns the result as a
``pyarrow.Table``.

Placeholder translation
-----------------------
The Nubi planner always generates SQL in the ``postgres`` dialect, so
``plan.sql`` contains ``$1``, ``$2`` … positional placeholders and ``plan.params``
is the matching positional value list (see ``app/connectors/planner.py``).  MySQL
drivers do not understand ``$N`` — PyMySQL expects ``%s`` and connectorx wants the
values inlined.  This connector therefore rewrites ``$N`` placeholders into the
driver's native form and re-orders ``plan.params`` to match, so the same plan that
runs on Postgres runs unchanged here.  **Binding is always parameterised — values
are never string-concatenated into the SQL** (PyMySQL escapes them through its
own ``execute`` binding; connectorx is only used for the no-params fast path).

Execution paths
---------------
1. **No params** → ``connectorx.read_sql`` (zero-copy Arrow, ``native_arrow``).
   This is the fast path for fully-baked literal SQL (e.g. ``SELECT * FROM t``).
2. **With params** → ``PyMySQL`` with ``%s`` bound parameters, then a manual
   rows→Arrow conversion.  This keeps RLS / projection predicates parameterised
   and injection-safe.

Both driver imports are lazy so this module imports cleanly with neither
``connectorx`` nor ``PyMySQL`` installed; calling ``execute``/``execute_stream``
without a usable driver raises ``AppError("driver_unavailable", 500)`` with an
install hint.

Capabilities
------------
``native_arrow`` is ``True`` because the connectorx path yields Arrow natively
(the PyMySQL fallback materialises to Arrow as well, so the contract still holds).
``predicate_pushdown`` / ``projection_pushdown`` / ``predicate_rls`` are ``True``
because the planner encodes those directly into the SQL sent to the server.
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

# Connector type strings this module backs.  MariaDB speaks the MySQL wire
# protocol, so the same class serves both (see app/connectors/mariadb.py).
SOURCE_TYPE = "mysql"

# Matches $1, $2, ... positional placeholders emitted by the planner's
# postgres dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_connectorx() -> Any:
    """Import connectorx lazily; return the module or raise ``AppError``."""
    try:
        import connectorx  # noqa: PLC0415

        return connectorx
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "connectorx is not installed (needed for the native-Arrow MySQL "
                "path). Install it with: pip install connectorx"
            ),
            status=500,
        ) from exc


def _import_pymysql() -> Any:
    """Import PyMySQL lazily; return the module or raise ``AppError``."""
    try:
        import pymysql  # noqa: PLC0415

        return pymysql
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "PyMySQL is not installed (needed for parameterised MySQL "
                "queries). Install it with: pip install PyMySQL"
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to MySQL ``%s`` and order the params.

    The planner emits ``$1``…``$N`` (a placeholder may repeat).  PyMySQL uses
    positional ``%s`` and expects exactly one value per ``%s`` occurrence, in
    order.  This rebuilds the param list so that a repeated ``$1`` binds the
    same value at each occurrence.

    Literal ``%`` characters in the SQL are escaped to ``%%`` so PyMySQL does
    not misread them as format markers.

    Returns
    -------
    tuple[str, list]
        ``(rewritten_sql, ordered_params)`` ready for ``cursor.execute``.
    """
    if not params:
        # No binding needed; still escape stray % so PyMySQL is happy if used.
        return sql.replace("%", "%%"), []

    # Escape literal % first (no $N placeholder contains %, so this is safe),
    # then replace each $N with %s while recording the value order.
    escaped = sql.replace("%", "%%")
    ordered: list[Any] = []

    def _sub(match: "re.Match[str]") -> str:
        idx = int(match.group(1)) - 1  # $N is 1-based
        if idx < 0 or idx >= len(params):
            raise AppError(
                "query_error",
                f"Placeholder ${idx + 1} has no corresponding value in plan.params "
                f"(len={len(params)}).",
                status=500,
            )
        ordered.append(params[idx])
        return "%s"

    rewritten = _PG_PLACEHOLDER_RE.sub(_sub, escaped)
    return rewritten, ordered


def _rows_to_arrow(columns: list[str], rows: list[tuple[Any, ...]]) -> "pa.Table":
    """Convert PyMySQL rows + column names to a ``pyarrow.Table``.

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


class MySQLConnector(Connector):
    """Connector backed by MySQL (and, via subclass/alias, MariaDB).

    Parameters
    ----------
    dsn:
        A MySQL URI of the form
        ``mysql://user:password@host:port/database``.  This is the form
        connectorx expects and is also parsed for the PyMySQL fallback.

    Notes
    -----
    Drivers are imported lazily so the module loads without connectorx/PyMySQL
    installed.  A fresh connection is opened per ``execute`` call and closed
    afterwards; pooling is deferred (mirrors ``PostgresConnector``).
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return MySQL/MariaDB connector capability flags.

        Push-down + RLS are all ``True`` because the planner bakes
        projection / predicate / RLS directly into the SQL sent to the server.
        ``native_arrow`` is ``True`` (connectorx path).  Partition routing,
        column masking, and CDC are out of scope.
        """
        return {
            "native_arrow": True,
            "predicate_pushdown": True,
            "projection_pushdown": True,
            "partition_pushdown": False,
            "predicate_rls": True,
            "column_masking": False,
            "streaming_cdc": False,
        }

    # ------------------------------------------------------------------
    # Internal: PyMySQL connection from the DSN
    # ------------------------------------------------------------------

    def _connect_pymysql(self) -> Any:
        """Open a PyMySQL connection parsed from ``self._dsn``."""
        pymysql = _import_pymysql()
        from urllib.parse import unquote, urlparse  # noqa: PLC0415

        parsed = urlparse(self._dsn)
        database = parsed.path.lstrip("/") or None
        return pymysql.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=unquote(parsed.username) if parsed.username else None,
            password=unquote(parsed.password) if parsed.password else "",
            database=database,
            charset="utf8mb4",
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        No-params plans use the connectorx native-Arrow path; parameterised
        plans use PyMySQL with bound ``%s`` placeholders (never string-concat).

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if no usable driver is present.
            ``code="query_error"`` (500) if the query fails at the DB level.
        """
        if not plan.params:
            return self._execute_connectorx(plan)
        return self._execute_pymysql(plan)

    def _execute_connectorx(self, plan: PhysicalPlan) -> "pa.Table":
        """Run a no-params plan through connectorx (zero-copy Arrow)."""
        cx = _import_connectorx()
        # SQL has no $N placeholders here (plan.params is empty), but a literal
        # statement may still be run verbatim.
        try:
            return cx.read_sql(self._dsn, plan.sql, return_type="arrow")
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"MySQL query failed: {exc}",
                status=500,
            ) from exc

    def _execute_pymysql(self, plan: PhysicalPlan) -> "pa.Table":
        """Run a parameterised plan through PyMySQL and convert to Arrow."""
        sql, params = _translate_placeholders(plan.sql, plan.params)
        conn = None
        try:
            conn = self._connect_pymysql()
            with conn.cursor() as cur:
                cur.execute(sql, params)
                columns = [desc[0] for desc in cur.description] if cur.description else []
                rows = cur.fetchall()
            return _rows_to_arrow(columns, list(rows))
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"MySQL query failed: {exc}",
                status=500,
            ) from exc
        finally:
            if conn is not None:
                conn.close()

    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield the result as a stream of RecordBatches.

        Materialises via ``execute()`` then yields the table's batches.  True
        server-side cursor streaming is deferred (mirrors the DuckDB connector).

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) / ``code="query_error"`` (500).
        """
        table = self.execute(plan)
        yield from table.to_batches()
