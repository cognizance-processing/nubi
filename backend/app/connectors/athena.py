"""Amazon Athena connector — native Arrow output via pyathena's ArrowCursor.

``AthenaConnector`` executes the planner's ``PhysicalPlan`` against Amazon Athena
(Presto/Trino-engine, querying data in S3 catalogued by AWS Glue) and returns the
result as a ``pyarrow.Table`` via pyathena's ``ArrowCursor`` (``cursor.as_arrow()``
fetches the result as Arrow natively).

Placeholder translation
-----------------------
The Nubi planner always emits SQL in the ``postgres`` dialect, so ``plan.sql``
contains ``$1``, ``$2`` … positional placeholders and ``plan.params`` is the
matching positional value list (see ``app/connectors/planner.py``).  pyathena's
DB-API cursor uses the ``pyformat`` paramstyle — positional ``%s`` markers — so
this connector rewrites each ``$N`` into a ``%s`` and re-orders ``plan.params``
so a repeated ``$1`` binds the same value at each occurrence.  Literal ``%``
characters are escaped to ``%%``.  **Binding is always parameterised — values are
NEVER string-concatenated into the SQL.**

Configuration
-------------
``config`` (dict) keys:

``region`` (required)
    AWS region (maps to pyathena's ``region_name``).
``s3_staging_dir`` (required)
    S3 URI where Athena writes query results.
``workgroup``
    Optional Athena workgroup.
``catalog_name``
    Data catalog (default ``"AwsDataCatalog"``).
``schema_name`` / ``database``
    Default schema/database (default ``"default"``).
``aws_access_key_id`` / ``aws_secret_access_key``
    Optional explicit credentials.  ``aws_secret_access_key`` arrives merged in
    from the encrypted secret store by ``query.py``.  When absent, pyathena falls
    back to the default boto3 credential chain (env / profile / instance role).

Lazy import
-----------
``pyathena`` is optional, so it is imported **inside** the methods that need it.
The module imports cleanly without the driver installed; calling ``execute`` /
``execute_stream`` without it raises ``AppError("driver_unavailable", 500)`` with
an install hint.

Capabilities
------------
``native_arrow`` is ``True`` (``ArrowCursor.as_arrow()``).  ``predicate_pushdown``
/ ``projection_pushdown`` / ``predicate_rls`` are ``True`` because the planner
bakes those directly into the SQL.  ``partition_pushdown`` is ``True`` (Athena
prunes Glue partitions from partition-key predicates).  ``column_masking`` /
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

SOURCE_TYPE = "athena"

# Matches $1, $2, ... positional placeholders emitted by the planner's postgres
# dialect.  Captures the 1-based index.
_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_pyathena() -> Any:
    """Import pyathena lazily; return the module or raise ``AppError``."""
    try:
        import pyathena  # noqa: PLC0415

        return pyathena
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "pyathena is not installed (needed for the Athena connector). "
                "Install it with: pip install 'pyathena[arrow]'"
            ),
            status=500,
        ) from exc


def _import_arrow_cursor() -> Any:
    """Import pyathena's ArrowCursor lazily; return the class or raise ``AppError``."""
    try:
        from pyathena.arrow.cursor import ArrowCursor  # noqa: PLC0415

        return ArrowCursor
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "pyathena's Arrow support is not installed (needed for the native "
                "Arrow Athena path). Install it with: pip install 'pyathena[arrow]'"
            ),
            status=500,
        ) from exc


def _translate_placeholders(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to Athena ``%s`` and order params.

    pyathena's DB-API cursor uses the ``pyformat`` paramstyle — positional ``%s``
    markers — and expects exactly one value per ``%s`` occurrence, in order.  This
    rebuilds the param list so that a repeated ``$1`` binds the same value at each
    occurrence.  Literal ``%`` characters are escaped to ``%%``.

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


class AthenaConnector(Connector):
    """Connector backed by Amazon Athena via pyathena's ArrowCursor.

    Parameters
    ----------
    config:
        A dict of connection parameters.  See the module docstring for the
        recognised keys (``region``, ``s3_staging_dir``, ``workgroup``,
        ``catalog_name``, ``schema_name`` / ``database``, ``aws_access_key_id``,
        ``aws_secret_access_key``).

    Notes
    -----
    The driver is imported lazily so the module loads without pyathena installed.
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
        """Return Athena connector capability flags.

        ``native_arrow`` is ``True`` (``ArrowCursor.as_arrow()``).  Push-down +
        RLS are ``True`` because the planner bakes projection / predicate / RLS
        directly into the SQL.  ``partition_pushdown`` is ``True`` because Athena
        prunes Glue partitions from partition-key predicates.  Column masking and
        CDC are out of scope.
        """
        return {
            "native_arrow": True,
            "predicate_pushdown": True,
            "projection_pushdown": True,
            "partition_pushdown": True,
            "predicate_rls": True,
            "column_masking": False,
            "streaming_cdc": False,
        }

    # ------------------------------------------------------------------
    # Internal: build an Athena connection
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        """Open a pyathena connection (ArrowCursor) from the config keys."""
        pyathena = _import_pyathena()
        arrow_cursor = _import_arrow_cursor()

        region = self._config.get("region")
        s3_staging_dir = self._config.get("s3_staging_dir")
        if not region:
            raise AppError(
                "config_error",
                "Athena connector requires 'region' in config.",
                status=500,
            )
        if not s3_staging_dir:
            raise AppError(
                "config_error",
                "Athena connector requires 's3_staging_dir' in config.",
                status=500,
            )

        kwargs: dict[str, Any] = {
            "region_name": region,
            "s3_staging_dir": s3_staging_dir,
            "catalog_name": self._config.get("catalog_name", "AwsDataCatalog"),
            "schema_name": self._config.get("schema_name")
            or self._config.get("database", "default"),
            "cursor_class": arrow_cursor,
        }
        workgroup = self._config.get("workgroup")
        if workgroup:
            kwargs["work_group"] = workgroup
        # Explicit credentials are optional; when absent pyathena uses the default
        # boto3 credential chain (env / profile / instance role).
        access_key = self._config.get("aws_access_key_id")
        secret_key = self._config.get("aws_secret_access_key")
        if access_key:
            kwargs["aws_access_key_id"] = access_key
        if secret_key:
            kwargs["aws_secret_access_key"] = secret_key

        try:
            return pyathena.connect(**kwargs)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "driver_unavailable",
                f"Failed to connect to Athena: {exc}",
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        ``$N`` placeholders are translated to Athena ``%s`` markers and bound as
        positional parameters (never string-concat).  The result is fetched as
        Arrow via the ArrowCursor's ``as_arrow()``.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if pyathena is absent.
            ``code="query_error"`` (500) if the query fails.
        """
        sql, params = _translate_placeholders(plan.sql, plan.params)
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            try:
                cur.execute(sql, params if params else None)
                table = cur.as_arrow()
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
                f"Athena query failed: {exc}",
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
