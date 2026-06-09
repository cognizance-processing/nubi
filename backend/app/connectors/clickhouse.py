"""ClickHouse connector — native Arrow output via clickhouse-connect.

``ClickHouseConnector`` executes the planner's ``PhysicalPlan`` against a
ClickHouse server and returns the result as a ``pyarrow.Table`` via the client's
``query_arrow()`` (clickhouse-connect can stream the result set as Arrow IPC
natively, so there is no row-by-row Python conversion).

Placeholder translation
-----------------------
The Nubi planner always emits SQL in the ``postgres`` dialect, so ``plan.sql``
contains ``$1``, ``$2`` … positional placeholders and ``plan.params`` is the
matching positional value list (see ``app/connectors/planner.py``).  ClickHouse
does not understand ``$N`` — its server-side binding uses named parameters of
the form ``{name:Type}`` supplied via the ``parameters`` mapping.  This connector
therefore rewrites each ``$N`` into a named ``{pK:String}`` placeholder and
builds a ``{f"p{k}": value}`` mapping, so a repeated ``$1`` reuses the same named
parameter.  **Binding is always parameterised — values are NEVER
string-concatenated into the SQL.**

The ``:String`` type annotation is deliberate: ClickHouse coerces the bound
string into the column type during comparison, which keeps the mapping simple and
robust across heterogeneous param types.  This is a documented simplification —
non-string semantics (e.g. exact integer binding) rely on ClickHouse's implicit
cast.  If a future plan needs strict typing, the type tag is the single place to
extend.

Configuration
-------------
``config`` (dict) keys:

``host`` (required)
    ClickHouse host.
``port``
    HTTP(S) interface port (default ``8443``, the TLS port).
``database``
    Default database (default ``"default"``).
``user``
    Login name (default ``"default"``).
``password``
    Password.  Arrives merged in from the encrypted secret store by ``query.py``.
``secure``
    Whether to use TLS (default ``True``).

Lazy import
-----------
``clickhouse-connect`` is optional, so it is imported **inside** the methods that
need it.  The module imports cleanly without the driver installed; calling
``execute`` / ``execute_stream`` without it raises
``AppError("driver_unavailable", 500)`` with an install hint.

Capabilities
------------
``native_arrow`` is ``True`` (``client.query_arrow()`` yields Arrow natively).
``predicate_pushdown`` / ``projection_pushdown`` / ``predicate_rls`` are ``True``
because the planner bakes those directly into the SQL sent to ClickHouse.
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

SOURCE_TYPE = "clickhouse"

# Matches $1, $2, ... positional placeholders emitted by the planner's postgres
# dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_clickhouse_connect() -> Any:
    """Import clickhouse-connect lazily; return the module or raise ``AppError``."""
    try:
        import clickhouse_connect  # noqa: PLC0415

        return clickhouse_connect
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "clickhouse-connect is not installed (needed for the ClickHouse "
                "connector). Install it with: pip install clickhouse-connect"
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, dict[str, Any]]:
    """Rewrite postgres ``$N`` placeholders to ClickHouse named ``{pK:String}``.

    ClickHouse's server-side binding uses named parameters supplied through a
    ``parameters`` mapping.  This maps each distinct source index to a stable
    ``pK`` name so a repeated ``$1`` reuses the same named parameter, then builds
    the ``{name: value}`` dict ``query_arrow`` expects.  Each value is bound as a
    ``String`` and coerced by ClickHouse during comparison (see module docstring).

    Returns
    -------
    tuple[str, dict]
        ``(rewritten_sql, parameters)`` ready for ``client.query_arrow``.
    """
    if not params:
        return sql, {}

    index_to_name: dict[int, str] = {}
    parameters: dict[str, Any] = {}

    def _sub(match: "re.Match[str]") -> str:
        idx = int(match.group(1)) - 1  # $N is 1-based
        if idx < 0 or idx >= len(params):
            raise AppError(
                "query_error",
                f"Placeholder ${idx + 1} has no corresponding value in plan.params "
                f"(len={len(params)}).",
                status=500,
            )
        if idx not in index_to_name:
            name = f"p{idx}"
            index_to_name[idx] = name
            # Bind as a string; ClickHouse coerces to the column type on compare.
            value = params[idx]
            parameters[name] = value if value is None else str(value)
        return f"{{{index_to_name[idx]}:String}}"

    rewritten = _PG_PLACEHOLDER_RE.sub(_sub, sql)
    return rewritten, parameters


class ClickHouseConnector(Connector):
    """Connector backed by ClickHouse via clickhouse-connect.

    Parameters
    ----------
    config:
        A dict of connection parameters.  See the module docstring for the
        recognised keys (``host``, ``port``, ``database``, ``user``,
        ``password``, ``secure``).

    Notes
    -----
    The driver is imported lazily so the module loads without clickhouse-connect
    installed.  A fresh client is created per ``execute`` call and closed
    afterwards; pooling is deferred (mirrors the other connectors).
    """

    def __init__(self, config: dict) -> None:
        self._config = dict(config or {})
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return ClickHouse connector capability flags.

        ``native_arrow`` is ``True`` (``client.query_arrow()``).  Push-down + RLS
        are ``True`` because the planner bakes projection / predicate / RLS
        directly into the SQL sent to ClickHouse.  Partition routing, column
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
    # Internal: build a ClickHouse client
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        """Open a clickhouse-connect client assembled from the config keys."""
        clickhouse_connect = _import_clickhouse_connect()

        host = self._config.get("host")
        if not host:
            raise AppError(
                "config_error",
                "ClickHouse connector requires 'host' in config.",
                status=500,
            )
        try:
            return clickhouse_connect.get_client(
                host=host,
                port=self._config.get("port", 8443),
                database=self._config.get("database", "default"),
                username=self._config.get("user", "default"),
                password=self._config.get("password", ""),
                secure=self._config.get("secure", True),
            )
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "driver_unavailable",
                f"Failed to connect to ClickHouse: {exc}",
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        ``$N`` placeholders are translated to ClickHouse named ``{pK:String}``
        parameters and bound via the ``parameters`` mapping (never string-concat).
        The result is fetched as Arrow via ``client.query_arrow()``.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if clickhouse-connect is absent.
            ``code="query_error"`` (500) if the query fails.
        """
        sql, parameters = _translate_placeholders(plan.sql, plan.params)
        client = None
        try:
            client = self._connect()
            table = client.query_arrow(sql, parameters=parameters or None)
            if table is None:
                import pyarrow as pa  # noqa: PLC0415

                return pa.table({})
            return table
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"ClickHouse query failed: {exc}",
                status=500,
            ) from exc
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:  # pragma: no cover - best-effort close
                    pass

    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield the result as a stream of RecordBatches.

        Materialises via ``execute()`` then yields the table's batches.  True
        Arrow-stream cursor streaming is deferred (mirrors the other connectors).

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) / ``code="query_error"`` (500).
        """
        table = self.execute(plan)
        yield from table.to_batches()
