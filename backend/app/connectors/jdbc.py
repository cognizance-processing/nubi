"""JDBC connector — optional, JVM-backed bridge via JayDeBeApi (JPype).

Python is not JDBC-native.  JDBC is a Java API, so reaching a JDBC driver from
Python requires a JVM in-process.  ``JDBCConnector`` uses **JayDeBeApi**, which
embeds a JVM via **JPype** and loads a vendor JDBC driver ``.jar`` at runtime.

This connector is intentionally OPTIONAL and best-effort:

* JayDeBeApi, JPype, a working JVM (``JAVA_HOME``/``java`` on PATH), and the
  vendor driver ``.jar`` must all be present.
* All of those are imported / loaded **lazily** inside ``execute``; the module
  imports cleanly without any of them.  If anything is missing, a clear
  ``AppError("driver_unavailable", 500)`` explains exactly what to install.

When to prefer JDBC vs a native driver
--------------------------------------
Use the native connectors (``mysql``/``mariadb``/``postgres``) whenever one
exists — they are faster (connectorx is zero-copy Arrow), have no JVM startup
cost, and no jar-management burden.  Reach for JDBC only for sources with **no
maintained Python driver** (e.g. some enterprise warehouses, legacy/JDBC-only
gateways, Denodo, certain Hive/Impala/Spark gateways) where the vendor ships a
JDBC jar.  See the connector report for the full recommendation.

Configuration
-------------
``config`` (dict) keys:

``jdbc_url`` (required)
    Full JDBC URL, e.g. ``jdbc:mysql://host:3306/db`` or
    ``jdbc:postgresql://host:5432/db``.
``driver_class`` (required)
    Fully-qualified driver class, e.g. ``com.mysql.cj.jdbc.Driver``.
``jar_path`` (required)
    Filesystem path (or ``:``-separated list) to the driver ``.jar``(s).
``user`` / ``password`` (optional)
    Credentials passed to ``DriverManager.getConnection``.  Often already
    encoded in ``jdbc_url``; provided separately when the driver requires it.

Placeholder handling
--------------------
The planner emits postgres ``$N`` placeholders.  JDBC ``PreparedStatement`` uses
``?``.  ``JDBCConnector`` reuses the MySQL connector's translator but targets the
JDBC ``?`` form, re-ordering ``plan.params`` to match.  Parameters are always
bound through the prepared statement — never string-concatenated.

Capabilities
------------
``predicate_pushdown`` / ``projection_pushdown`` / ``predicate_rls`` are ``True``
(the planner bakes them into the SQL).  ``native_arrow`` is ``False``: JDBC
returns JVM ``ResultSet`` rows that are converted to Arrow row-by-row in Python,
so there is no zero-copy Arrow path.  ``partition_pushdown`` / ``column_masking``
/ ``streaming_cdc`` are ``False``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

from app.connectors.base import Connector
from app.connectors.mysql import _rows_to_arrow
from app.connectors.plan import PhysicalPlan
from app.errors import AppError

SOURCE_TYPE = "jdbc"

_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def _import_jaydebeapi() -> Any:
    """Import JayDeBeApi lazily; raise a guidance-rich ``AppError`` if missing.

    A missing JVM surfaces as a JayDeBeApi/JPype import-or-startup error; we
    fold all of those into a single ``driver_unavailable`` with install hints.
    """
    try:
        import jaydebeapi  # noqa: PLC0415

        return jaydebeapi
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            (
                "JDBC support is not available. The JDBC path requires a JVM plus "
                "JayDeBeApi (JPype). Install with: pip install JayDeBeApi JPype1 — "
                "and ensure a JRE/JDK is installed (JAVA_HOME set or `java` on PATH) "
                "and the vendor driver .jar is provided via config['jar_path']."
            ),
            status=500,
        ) from exc


def _translate_placeholders_jdbc(sql: str, params: list[Any]) -> tuple[str, list[Any]]:
    """Rewrite postgres ``$N`` placeholders to JDBC ``?`` and order the params.

    Repeated ``$N`` placeholders bind the same value at each occurrence, in
    statement order, matching JDBC ``PreparedStatement`` semantics.
    """
    if not params:
        return sql, []

    ordered: list[Any] = []

    def _sub(match: "re.Match[str]") -> str:
        idx = int(match.group(1)) - 1
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


class JDBCConnector(Connector):
    """Optional JVM-backed connector reaching any JDBC driver via JayDeBeApi.

    Parameters
    ----------
    config:
        A dict with ``jdbc_url``, ``driver_class``, ``jar_path`` (required) and
        optional ``user`` / ``password``.  See the module docstring.

    Notes
    -----
    The JVM and JayDeBeApi are imported / started lazily on the first
    ``execute`` call so this connector can be constructed (and the module
    imported) in environments without a JVM.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._jdbc_url: str = config["jdbc_url"]
        self._driver_class: str = config["driver_class"]
        self._jar_path: Any = config["jar_path"]
        self._user: str | None = config.get("user")
        self._password: str | None = config.get("password")
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return JDBC connector capability flags.

        ``native_arrow`` is ``False`` (JDBC ResultSet → Arrow is row-by-row).
        Push-down + RLS are ``True`` because the planner encodes them in the SQL.
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
    # Internal: open a JDBC connection
    # ------------------------------------------------------------------

    def _connect(self) -> Any:
        """Open a JDBC connection via JayDeBeApi (starts the JVM on first use)."""
        jaydebeapi = _import_jaydebeapi()
        try:
            if self._user is not None:
                return jaydebeapi.connect(
                    self._driver_class,
                    self._jdbc_url,
                    [self._user, self._password or ""],
                    self._jar_path,
                )
            return jaydebeapi.connect(
                self._driver_class,
                self._jdbc_url,
                jars=self._jar_path,
            )
        except AppError:
            raise
        except Exception as exc:
            # JVM startup / jar-not-found / driver-class-not-found all land here.
            raise AppError(
                "driver_unavailable",
                (
                    f"Failed to establish a JDBC connection via JayDeBeApi: {exc}. "
                    "Verify the JVM is installed, driver_class is correct, and "
                    "jar_path points to the vendor driver .jar."
                ),
                status=500,
            ) from exc

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* over JDBC and return the result as a PyArrow Table.

        Params are bound through a JDBC ``PreparedStatement`` (``?`` markers);
        the result set is converted to Arrow row-by-row.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if the JVM/JayDeBeApi/jar is
            missing.  ``code="query_error"`` (500) if the query fails.
        """
        sql, params = _translate_placeholders_jdbc(plan.sql, plan.params)
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            try:
                cur.execute(sql, params if params else None)
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
                f"JDBC query failed: {exc}",
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

        Materialises via ``execute()`` then yields the table's batches (no
        server-side cursor streaming over JDBC in this revision).
        """
        table = self.execute(plan)
        yield from table.to_batches()
