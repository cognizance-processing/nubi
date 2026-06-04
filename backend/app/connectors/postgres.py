"""PostgreSQL / Neon connector via ADBC for native Arrow output.

``PostgresConnector`` uses the ``adbc-driver-postgresql`` + ``adbc-driver-manager``
wheels to execute SQL and receive results as Arrow data without a row-by-row
Python conversion step.

Lazy ADBC import
----------------
The ``adbc_driver_postgresql`` and ``adbc_driver_manager`` wheels are large
and optional for local development (the DuckDB connector is the fixture engine).
Both are imported **inside** the methods that need them so that the module loads
cleanly even when the wheels are absent.  If a method is called while the wheels
are missing an ``AppError("driver_unavailable", 500)`` is raised with a clear
installation hint.

Design notes
------------
- Each ``execute()`` / ``execute_stream()`` call opens a fresh ADBC connection
  and closes it after use.  Connection pooling is deferred to M2/M9.
- ``plan.params`` are passed as a list; ADBC maps them to ``$1``/``$2``
  positional placeholders as used by the Nubi planner's Postgres dialect.
- ``execute_stream()`` reads one batch at a time via ``fetch_record_batch()``;
  the batch size is controlled by the ADBC driver (default 1 024 rows).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.errors import AppError


def _import_adbc() -> tuple[Any, Any]:
    """Import ADBC modules lazily; raise ``AppError`` if unavailable.

    Returns
    -------
    tuple
        ``(adbc_driver_manager, adbc_driver_postgresql)`` module references.

    Raises
    ------
    AppError
        ``code="driver_unavailable"`` (500) if either wheel is not installed.
    """
    try:
        import adbc_driver_manager
        import adbc_driver_postgresql

        return adbc_driver_manager, adbc_driver_postgresql
    except ImportError as exc:
        raise AppError(
            "driver_unavailable",
            (
                "ADBC PostgreSQL driver is not installed. "
                "Install it with: pip install adbc-driver-postgresql adbc-driver-manager"
            ),
            status=500,
        ) from exc


class PostgresConnector(Connector):
    """Connector backed by PostgreSQL / Neon via ADBC.

    Parameters
    ----------
    dsn:
        A libpq-style connection string, e.g.
        ``"postgresql://user:pass@host/db?sslmode=require"``.
        Typically sourced from ``settings.DATABASE_URL``.

    Notes
    -----
    The ADBC wheels are imported lazily so that this module can be imported
    without them present (e.g. in a pure DuckDB local-dev environment).
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return PostgreSQL connector capability flags.

        All push-down modes are enabled because the planner encodes
        projection/predicate/RLS directly into the SQL that is sent to
        the server.  Column masking and CDC are M9+ features.
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
    # Internal: open a fresh ADBC connection
    # ------------------------------------------------------------------

    def _open_connection(self) -> Any:
        """Open and return a new ADBC connection to the configured DSN.

        The caller is responsible for closing the connection (use as a
        context manager or call ``.close()``).
        """
        adbc_mgr, adbc_pg = _import_adbc()

        db = adbc_pg.connect(self._dsn)
        return adbc_mgr.AdbcConnection(db)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        Opens a fresh ADBC connection, runs ``plan.sql`` with ``plan.params``
        as positional arguments, retrieves the full Arrow table, and closes the
        connection.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan``.  SQL is run verbatim.

        Returns
        -------
        pyarrow.Table
            The full query result as Arrow.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if ADBC wheels are absent.
            ``code="query_error"`` (500) if the query fails at the DB level.
        """
        _import_adbc()  # surface driver-unavailable early

        try:
            import adbc_driver_postgresql

            with adbc_driver_postgresql.dbapi.connect(self._dsn) as db_conn:
                with db_conn.cursor() as cur:
                    cur.execute(plan.sql, plan.params if plan.params else None)
                    return cur.fetch_arrow_table()
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"PostgreSQL query failed: {exc}",
                status=500,
            ) from exc

    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield result data as a stream of RecordBatches.

        Opens a fresh ADBC connection, runs the SQL, and yields one
        ``RecordBatch`` at a time via ``fetch_record_batch()``.  The
        connection is closed after all batches have been consumed.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan``.

        Yields
        ------
        pyarrow.RecordBatch
            One batch at a time from the cursor.

        Raises
        ------
        AppError
            ``code="driver_unavailable"`` (500) if ADBC wheels are absent.
            ``code="query_error"`` (500) if the query fails.
        """
        _import_adbc()  # surface driver-unavailable early

        try:
            import adbc_driver_postgresql

            with adbc_driver_postgresql.dbapi.connect(self._dsn) as db_conn:
                with db_conn.cursor() as cur:
                    cur.execute(plan.sql, plan.params if plan.params else None)
                    while True:
                        batch = cur.fetch_record_batch()
                        if batch is None or batch.num_rows == 0:
                            break
                        yield batch
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "query_error",
                f"PostgreSQL streaming query failed: {exc}",
                status=500,
            ) from exc
