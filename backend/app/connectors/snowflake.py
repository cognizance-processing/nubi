"""Snowflake connector — native Arrow output via snowflake-connector-python.

``SnowflakeConnector`` executes the planner's ``PhysicalPlan`` against a
Snowflake warehouse and returns the result as a ``pyarrow.Table`` via the
cursor's ``fetch_arrow_all()`` (the Snowflake Python connector fetches result
chunks as Arrow natively, so there is no row-by-row Python conversion).

Placeholder translation
-----------------------
The Nubi planner always emits SQL in the ``postgres`` dialect, so ``plan.sql``
contains ``$1``, ``$2`` … positional placeholders and ``plan.params`` is the
matching positional value list (see ``app/connectors/planner.py``).  The
Snowflake connector's default paramstyle is ``pyformat`` (``%s`` / ``%(name)s``),
so — exactly like the MySQL connector — this module rewrites each ``$N`` into a
positional ``%s`` and re-orders ``plan.params`` so a repeated ``$1`` binds the
same value at each occurrence.  Literal ``%`` characters are escaped to ``%%``.
**Binding is always parameterised — values are NEVER string-concatenated.**

Configuration
-------------
``config`` (dict) keys (mirrors snowflake.connector.connect kwargs):

``account`` (required)
    Snowflake account identifier, e.g. ``"xy12345.us-east-1"``.
``user`` (required)
    Login name.
``password``
    Password auth.  Alternatively supply key-pair auth via
    ``private_key`` / ``private_key_file`` (passed through verbatim).
``warehouse`` / ``database`` / ``schema`` / ``role``
    Optional session context — passed straight through when present.

query.py merges decrypted secrets (e.g. ``password``) into this config via its
generic ``else`` secret-fallback branch before constructing the connector.

Lazy import
-----------
``snowflake-connector-python`` is heavy and optional, so it is imported **inside**
the methods that need it.  The module imports cleanly without the driver
installed; calling ``execute`` / ``execute_stream`` without it raises
``AppError("driver_unavailable", 500)`` with an install hint.

Capabilities
------------
``native_arrow`` is ``True`` (``fetch_arrow_all()``).  ``predicate_pushdown`` /
``projection_pushdown`` / ``predicate_rls`` are ``True`` because the planner bakes
those directly into the SQL.  ``partition_pushdown`` / ``column_masking`` /
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

SOURCE_TYPE = "snowflake"

# Keys forwarded verbatim to snowflake.connector.connect when present in config.
_CONNECT_KEYS = (
    "account",
    "user",
    "password",
    "warehouse",
    "database",
    "schema",
    "role",
    "private_key",
    "private_key_file",
    "private_key_file_pwd",
    "authenticator",
    "token",
)

# Matches $1, $2, ... positional placeholders emitted by the planner's postgres
# dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_snowflake() -> Any:
    """Import snowflake-connector-python lazily; return module or raise ``AppError``."""
    try:
        import snowflake.connector  # noqa: PLC0415

        return snowflake.connector
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "snowflake-connector-python is not installed (needed for the "
                "Snowflake connector). Install it with: "
                "pip install 'snowflake-connector-python[pandas]'"
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to Snowflake ``%s`` and order params.

    The Snowflake Python connector defaults to the ``pyformat`` paramstyle, which
    uses positional ``%s`` and expects exactly one value per ``%s`` occurrence, in
    order.  This rebuilds the param list so that a repeated ``$1`` binds the same
    value at each occurrence.  Literal ``%`` characters are escaped to ``%%`` so
    they are not misread as format markers.

    Returns
    -------
    tuple[str, list]
        ``(rewritten_sql, ordered_params)`` ready for ``cursor.execute``.
    """
    if not params:
        # No binding needed; still escape stray % so paramstyle is satisfied.
        return sql.replace("%", "%%"), []

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


class SnowflakeConnector(Connector):
    """Connector backed by Snowflake via snowflake-connector-python.

    Parameters
    ----------
    config:
        A dict of connection parameters.  See the module docstring for the
        recognised keys (``account``, ``user``, ``password``, ``warehouse``,
        ``database``, ``schema``, ``role`` and key-pair / token variants).

    Notes
    -----
    The driver is imported lazily so the module loads without it.  A fresh
    connection is opened per ``execute`` call and closed afterwards; pooling is
    deferred (mirrors ``PostgresConnector`` / ``MySQLConnector``).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config or {})
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return Snowflake connector capability flags.

        ``native_arrow`` is ``True`` (``fetch_arrow_all()``).  Push-down + RLS are
        ``True`` because the planner bakes projection / predicate / RLS directly
        into the SQL sent to Snowflake.  Partition routing, column masking, and
        CDC are out of scope.
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
    # Internal: open a Snowflake connection
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        """Open a Snowflake connection from the recognised config keys."""
        connector = _import_snowflake()
        kwargs = {k: self._config[k] for k in _CONNECT_KEYS if self._config.get(k) is not None}
        if "account" not in kwargs or "user" not in kwargs:
            raise AppError(
                "config_error",
                "Snowflake connector requires at least 'account' and 'user' in config.",
                status=500,
            )
        try:
            return connector.connect(**kwargs)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "driver_unavailable",
                f"Failed to connect to Snowflake: {exc}",
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        ``$N`` placeholders are translated to Snowflake ``%s`` markers and bound
        as positional parameters (never string-concat).  The result is fetched as
        Arrow via ``cursor.fetch_arrow_all()``.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if the driver is absent.
            ``code="query_error"`` (500) if the query fails.
        """
        sql, params = _translate_placeholders(plan.sql, plan.params)
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            try:
                cur.execute(sql, params if params else None)
                table = cur.fetch_arrow_all()
            finally:
                cur.close()
            if table is None:
                # Empty result set: fetch_arrow_all() may return None.
                import pyarrow as pa  # noqa: PLC0415

                return pa.table({})
            return table
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"Snowflake query failed: {exc}",
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
