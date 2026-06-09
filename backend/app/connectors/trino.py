"""Trino connector — Arrow output via the trino DB-API (row-based conversion).

``TrinoConnector`` executes the planner's ``PhysicalPlan`` against a Trino
cluster (also used for Presto, which shares the same Python client) and returns
the result as a ``pyarrow.Table``.  The ``trino`` client has no native Arrow
path, so rows are fetched via the DB-API cursor and converted column-major to
Arrow (mirrors the PyMySQL fallback path in ``mysql.py``).

Placeholder translation
-----------------------
The Nubi planner always emits SQL in the ``postgres`` dialect, so ``plan.sql``
contains ``$1``, ``$2`` … positional placeholders and ``plan.params`` is the
matching positional value list (see ``app/connectors/planner.py``).  The trino
client uses positional ``?`` markers (qmark paramstyle), so this connector
rewrites each ``$N`` into a ``?`` and re-orders ``plan.params`` so a repeated
``$1`` binds the same value at each occurrence.  **Binding is always
parameterised — values are NEVER string-concatenated into the SQL.**

Configuration
-------------
``config`` (dict) keys:

``host`` (required)
    Trino coordinator host.
``port``
    Coordinator port (default ``443``).
``user`` (required)
    Trino user name.
``catalog`` / ``schema``
    Default catalog / schema session context.
``http_scheme``
    ``"https"`` (default) or ``"http"``.
``password``
    Optional password; when present, HTTP basic auth is used via
    ``trino.auth.BasicAuthentication``.  Arrives merged in from the encrypted
    secret store by ``query.py``.

Lazy import
-----------
``trino`` is optional, so it is imported **inside** the methods that need it.
The module imports cleanly without the driver installed; calling ``execute`` /
``execute_stream`` without it raises ``AppError("driver_unavailable", 500)`` with
an install hint.

Capabilities
------------
``native_arrow`` is ``False`` (rows are converted to Arrow in Python).
``predicate_pushdown`` / ``projection_pushdown`` / ``predicate_rls`` are ``True``
because the planner bakes those directly into the SQL sent to Trino.
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

SOURCE_TYPE = "trino"

# Matches $1, $2, ... positional placeholders emitted by the planner's postgres
# dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_trino() -> Any:
    """Import the trino client lazily; return the module or raise ``AppError``."""
    try:
        import trino  # noqa: PLC0415

        return trino
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "trino is not installed (needed for the Trino/Presto connector). "
                "Install it with: pip install trino"
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to Trino ``?`` and order params.

    The trino client uses positional ``?`` markers (qmark paramstyle) and expects
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
    """Convert trino rows + column names to a ``pyarrow.Table``.

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


class TrinoConnector(Connector):
    """Connector backed by Trino (or Presto) via the trino Python client.

    Parameters
    ----------
    config:
        A dict of connection parameters.  See the module docstring for the
        recognised keys (``host``, ``port``, ``user``, ``catalog``, ``schema``,
        ``http_scheme``, ``password``).

    Notes
    -----
    The driver is imported lazily so the module loads without trino installed.
    A fresh connection is opened per ``execute`` call and closed afterwards;
    pooling is deferred (mirrors the other connectors).
    """

    def __init__(self, config: dict) -> None:
        self._config = dict(config or {})
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return Trino connector capability flags.

        ``native_arrow`` is ``False`` (trino rows are converted to Arrow in
        Python).  Push-down + RLS are ``True`` because the planner bakes
        projection / predicate / RLS directly into the SQL sent to Trino.
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
    # Internal: open a Trino connection
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        """Open a trino DB-API connection assembled from the config keys."""
        trino = _import_trino()

        host = self._config.get("host")
        user = self._config.get("user")
        if not host:
            raise AppError(
                "config_error",
                "Trino connector requires 'host' in config.",
                status=500,
            )
        if not user:
            raise AppError(
                "config_error",
                "Trino connector requires 'user' in config.",
                status=500,
            )

        kwargs: dict[str, Any] = {
            "host": host,
            "port": self._config.get("port", 443),
            "user": user,
            "http_scheme": self._config.get("http_scheme", "https"),
        }
        catalog = self._config.get("catalog")
        schema = self._config.get("schema")
        if catalog:
            kwargs["catalog"] = catalog
        if schema:
            kwargs["schema"] = schema

        # Optional HTTP basic auth when a password is supplied.
        password = self._config.get("password")
        if password is not None:
            kwargs["auth"] = trino.auth.BasicAuthentication(user, password)

        try:
            return trino.dbapi.connect(**kwargs)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "driver_unavailable",
                f"Failed to connect to Trino: {exc}",
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        ``$N`` placeholders are translated to trino ``?`` markers and bound as
        positional parameters (never string-concat).  Rows are fetched via the
        DB-API cursor and converted column-major to Arrow.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if trino is absent.
            ``code="query_error"`` (500) if the query fails.
        """
        sql, params = _translate_placeholders(plan.sql, plan.params)
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            try:
                cur.execute(sql, params if params else None)
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description] if cur.description else []
            finally:
                cur.close()
            return _rows_to_arrow(columns, [tuple(r) for r in rows])
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"Trino query failed: {exc}",
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
