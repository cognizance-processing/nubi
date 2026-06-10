"""SQL Server connector — Arrow output via pyodbc (row-based conversion).

``SQLServerConnector`` executes the planner's ``PhysicalPlan`` against a
Microsoft SQL Server / Azure SQL / Azure Synapse instance (all speak T-SQL over
TDS) and returns the result as a ``pyarrow.Table``.  pyodbc has no native Arrow
path, so rows are fetched via the DB-API cursor and converted column-major to
Arrow (mirrors the PyMySQL fallback path in ``mysql.py``).

Placeholder translation
-----------------------
The Nubi planner always emits SQL in the ``postgres`` dialect, so ``plan.sql``
contains ``$1``, ``$2`` … positional placeholders and ``plan.params`` is the
matching positional value list (see ``app/connectors/planner.py``).  pyodbc
uses the ``qmark`` paramstyle — positional ``?`` markers — so this connector
rewrites each ``$N`` into a ``?`` and re-orders ``plan.params`` so a repeated
``$1`` binds the same value at each occurrence.  **Binding is always
parameterised — values are NEVER string-concatenated into the SQL.**

Configuration
-------------
``config`` (dict) keys:

``host`` (required)
    Server hostname or IP.
``port``
    TDS port (default ``1433``).
``database``
    Initial catalog / database name.
``user`` / ``password``
    SQL authentication credentials.  ``password`` arrives merged in from the
    encrypted secret store by ``query.py``.
``driver``
    ODBC driver name (default ``"ODBC Driver 18 for SQL Server"``).
``encrypt``
    Whether to encrypt the connection (default ``True``); maps to the ODBC
    ``Encrypt=yes|no`` keyword.
``trust_server_certificate``
    Skip server-certificate validation (default ``False``); maps to
    ``TrustServerCertificate=yes|no``.

Lazy import
-----------
``pyodbc`` is optional, so it is imported **inside** the methods that need it.
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

SOURCE_TYPE = "sqlserver"

# Matches $1, $2, ... positional placeholders emitted by the planner's postgres
# dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_pyodbc() -> Any:
    """Import pyodbc lazily; return the module or raise ``AppError``."""
    try:
        import pyodbc  # noqa: PLC0415

        return pyodbc
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "pyodbc is not installed (needed for the SQL Server connector). "
                "Install it with: pip install pyodbc  (and the matching "
                "'ODBC Driver 18 for SQL Server' system package)."
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to SQL Server ``?`` and order params.

    pyodbc uses the ``qmark`` paramstyle — positional ``?`` markers — and expects
    exactly one value per ``?`` occurrence, in order.  This rebuilds the param
    list so that a repeated ``$1`` binds the same value at each occurrence.

    Returns
    -------
    tuple[str, list]
        ``(rewritten_sql, ordered_params)`` ready for ``cursor.execute``.
    """
    if not params:
        return sql, []

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
        return "?"

    rewritten = _PG_PLACEHOLDER_RE.sub(_sub, sql)
    return rewritten, ordered


def _rows_to_arrow(columns: list[str], rows: list[tuple[Any, ...]]) -> "pa.Table":
    """Convert pyodbc rows + column names to a ``pyarrow.Table``.

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


class SQLServerConnector(Connector):
    """Connector backed by Microsoft SQL Server / Azure SQL / Synapse via pyodbc.

    Parameters
    ----------
    config:
        A dict of connection parameters.  See the module docstring for the
        recognised keys (``host``, ``port``, ``database``, ``user``,
        ``password``, ``driver``, ``encrypt``, ``trust_server_certificate``).

    Notes
    -----
    The driver is imported lazily so the module loads without pyodbc installed.
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
        """Return SQL Server connector capability flags.

        ``native_arrow`` is ``False`` (pyodbc rows are converted to Arrow in
        Python).  Push-down + RLS are ``True`` because the planner bakes
        projection / predicate / RLS directly into the T-SQL sent to the server.
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
    # Internal: build a pyodbc connection
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        """Open a pyodbc connection assembled from the config keys."""
        pyodbc = _import_pyodbc()

        host = self._config.get("host")
        if not host:
            raise AppError(
                "config_error",
                "SQL Server connector requires 'host' in config.",
                status=500,
            )
        port = self._config.get("port", 1433)
        driver = self._config.get("driver", "ODBC Driver 18 for SQL Server")
        database = self._config.get("database")
        user = self._config.get("user")
        password = self._config.get("password")
        encrypt = self._config.get("encrypt", True)
        trust_cert = self._config.get("trust_server_certificate", False)

        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={host},{port}",
        ]
        if database:
            parts.append(f"DATABASE={database}")
        if user is not None:
            parts.append(f"UID={user}")
        if password is not None:
            parts.append(f"PWD={password}")
        parts.append(f"Encrypt={'yes' if encrypt else 'no'}")
        parts.append(f"TrustServerCertificate={'yes' if trust_cert else 'no'}")
        conn_str = ";".join(parts)

        try:
            return pyodbc.connect(conn_str)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "driver_unavailable",
                f"Failed to connect to SQL Server: {exc}",
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        ``$N`` placeholders are translated to pyodbc ``?`` markers and bound as
        positional parameters (never string-concat).  Rows are fetched via the
        DB-API cursor and converted column-major to Arrow.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if pyodbc is absent.
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
                f"SQL Server query failed: {exc}",
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
