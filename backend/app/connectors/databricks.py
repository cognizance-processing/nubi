"""Databricks connector — native Arrow output via databricks-sql-connector.

``DatabricksConnector`` executes the planner's ``PhysicalPlan`` against a
Databricks SQL warehouse and returns the result as a ``pyarrow.Table`` via the
cursor's ``fetchall_arrow()`` (the Databricks SQL connector fetches result chunks
as Arrow natively, so there is no row-by-row Python conversion).

Placeholder translation
-----------------------
The Nubi planner always emits SQL in the ``postgres`` dialect, so ``plan.sql``
contains ``$1``, ``$2`` … positional placeholders and ``plan.params`` is the
matching positional value list (see ``app/connectors/planner.py``).  The
Databricks SQL connector uses positional ``?`` markers, so this connector
rewrites each ``$N`` into a ``?`` and re-orders ``plan.params`` so a repeated
``$1`` binds the same value at each occurrence, then passes the values as a
positional sequence.  **Binding is always parameterised — values are NEVER
string-concatenated into the SQL.**

Configuration
-------------
``config`` (dict) keys:

``server_hostname`` (required)
    Workspace hostname, e.g. ``"dbc-xxxx.cloud.databricks.com"``.
``http_path`` (required)
    SQL warehouse / cluster HTTP path, e.g. ``"/sql/1.0/warehouses/abc123"``.
``access_token`` (required)
    Personal-access / OAuth token.  Arrives merged in from the encrypted secret
    store by ``query.py``.
``catalog`` / ``schema``
    Optional Unity Catalog catalog / schema session context.

Lazy import
-----------
``databricks-sql-connector`` is optional, so it is imported **inside** the methods
that need it.  The module imports cleanly without the driver installed; calling
``execute`` / ``execute_stream`` without it raises
``AppError("driver_unavailable", 500)`` with an install hint.

Capabilities
------------
``native_arrow`` is ``True`` (``cursor.fetchall_arrow()``).  ``predicate_pushdown``
/ ``projection_pushdown`` / ``predicate_rls`` are ``True`` because the planner
bakes those directly into the SQL.  ``partition_pushdown`` / ``column_masking`` /
``streaming_cdc`` are ``False``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.errors import AppError

SOURCE_TYPE = "databricks"

# Matches $1, $2, ... positional placeholders emitted by the planner's postgres
# dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_databricks_sql() -> Any:
    """Import databricks-sql-connector lazily; return the module or raise ``AppError``."""
    try:
        from databricks import sql  # noqa: PLC0415

        return sql
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "databricks-sql-connector is not installed (needed for the "
                "Databricks connector). Install it with: "
                "pip install databricks-sql-connector"
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to Databricks ``?`` and order params.

    The Databricks SQL connector uses positional ``?`` markers and expects
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


class DatabricksConnector(Connector):
    """Connector backed by a Databricks SQL warehouse via databricks-sql-connector.

    Parameters
    ----------
    config:
        A dict of connection parameters.  See the module docstring for the
        recognised keys (``server_hostname``, ``http_path``, ``access_token``,
        ``catalog``, ``schema``).

    Notes
    -----
    The driver is imported lazily so the module loads without the connector
    installed.  A fresh connection is opened per ``execute`` call and closed
    afterwards; pooling is deferred (mirrors the other connectors).
    """

    def __init__(self, config: dict) -> None:
        self._config = dict(config or {})
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return Databricks connector capability flags.

        ``native_arrow`` is ``True`` (``cursor.fetchall_arrow()``).  Push-down +
        RLS are ``True`` because the planner bakes projection / predicate / RLS
        directly into the SQL sent to the warehouse.  Partition routing, column
        masking, and CDC are out of scope.
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
    # Internal: open a Databricks connection
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        """Open a Databricks SQL connection assembled from the config keys."""
        sql = _import_databricks_sql()

        server_hostname = self._config.get("server_hostname")
        http_path = self._config.get("http_path")
        access_token = self._config.get("access_token")
        if not server_hostname or not http_path or not access_token:
            raise AppError(
                "config_error",
                "Databricks connector requires 'server_hostname', 'http_path' and "
                "'access_token' in config.",
                status=500,
            )

        kwargs: dict[str, Any] = {
            "server_hostname": server_hostname,
            "http_path": http_path,
            "access_token": access_token,
        }
        catalog = self._config.get("catalog")
        schema = self._config.get("schema")
        if catalog:
            kwargs["catalog"] = catalog
        if schema:
            kwargs["schema"] = schema

        try:
            return sql.connect(**kwargs)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "driver_unavailable",
                f"Failed to connect to Databricks: {exc}",
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        ``$N`` placeholders are translated to Databricks ``?`` markers and bound
        as positional parameters (never string-concat).  The result is fetched as
        Arrow via ``cursor.fetchall_arrow()``.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if the connector is absent.
            ``code="query_error"`` (500) if the query fails.
        """
        sql_text, params = _translate_placeholders(plan.sql, plan.params)
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            try:
                cur.execute(sql_text, params if params else None)
                table = cur.fetchall_arrow()
            finally:
                cur.close()
            if table is None:
                import pyarrow as pa  # noqa: PLC0415

                return pa.table({})
            return table
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"Databricks query failed: {exc}",
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
        cursor streaming is deferred (mirrors the other connectors).

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) / ``code="query_error"`` (500).
        """
        table = self.execute(plan)
        yield from table.to_batches()
