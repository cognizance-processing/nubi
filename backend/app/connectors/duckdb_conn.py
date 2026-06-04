"""DuckDB connector — deterministic local engine and conformance fixture.

``DuckDBConnector`` wraps an in-memory (or on-disk) DuckDB database.  It is
the primary fixture engine for the conformance suite (Wave M1-C) and the
fallback executor used by the query endpoint when no external data-store is
configured.

Design notes
------------
- Pure Python; no network I/O; deterministic given the same seed data.
- Native Arrow output via ``duckdb.DuckDBPyConnection.arrow()`` — zero-copy
  where DuckDB supports it.
- ``register(tables)`` seeds named tables from a dict of ``{name: pa.Table}``
  so tests can inject arbitrary fixture data without touching the filesystem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    import duckdb as _duckdb_t
    import pyarrow as pa

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.errors import AppError


class DuckDBConnector(Connector):
    """Connector backed by an in-process DuckDB database.

    Parameters
    ----------
    connection:
        An existing ``duckdb.DuckDBPyConnection`` to use.  Defaults to a
        fresh in-memory database when ``None``.

    Usage
    -----
    ::

        conn = DuckDBConnector()
        conn.register({"demo": pa.table({"id": [1, 2], "value": [10.0, 20.0]})})
        plan = planner.plan("SELECT * FROM demo")
        table = conn.execute(plan)
    """

    def __init__(self, connection: "_duckdb_t.DuckDBPyConnection | None" = None) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise AppError(
                "driver_unavailable",
                "DuckDB is not installed.  Add 'duckdb>=1.0' to requirements.txt.",
                status=500,
            ) from exc

        if connection is not None:
            self._conn = connection
        else:
            self._conn = duckdb.connect(database=":memory:")

        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return DuckDB connector capability flags.

        DuckDB supports native Arrow output, full predicate and projection
        push-down (they are encoded in the SQL the planner generates), and
        predicate-level RLS (injected into the WHERE clause by the planner).
        It does not support partition routing, column masking, or CDC.
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
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan``.  ``plan.sql`` is run verbatim via
            DuckDB; ``plan.params`` are passed as positional parameters.

        Returns
        -------
        pyarrow.Table
            The full query result.

        Raises
        ------
        AppError
            ``code="query_error"`` (500) if DuckDB raises any exception.
        """
        try:
            rel = self._conn.execute(plan.sql, plan.params)
            # duckdb >=1.0 returns a RecordBatchReader from .arrow(); call
            # .read_all() to materialise it as a pyarrow.Table.
            result = rel.arrow()
            if hasattr(result, "read_all"):
                return result.read_all()
            return result  # already a pa.Table in older builds
        except Exception as exc:
            raise AppError(
                "query_error",
                f"DuckDB query failed: {exc}",
                status=500,
            ) from exc

    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield result data as a stream of RecordBatches.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan``.

        Yields
        ------
        pyarrow.RecordBatch
            One or more batches forming the full result.  For M1 the entire
            result is fetched eagerly and yielded as a single batch stream;
            true chunking will be added in M2.

        Raises
        ------
        AppError
            ``code="query_error"`` (500) if DuckDB raises any exception.
        """
        table = self.execute(plan)
        yield from table.to_batches()

    # ------------------------------------------------------------------
    # Seeding helper
    # ------------------------------------------------------------------

    def register(self, tables: dict[str, "pa.Table"]) -> None:
        """Register Arrow tables as named DuckDB views.

        This is the primary way to inject fixture data for tests and the
        demo dataset.  Each entry in *tables* becomes a named relation that
        can be queried by ``SELECT * FROM <name>``.

        Parameters
        ----------
        tables:
            A mapping of ``{table_name: pyarrow.Table}``.  Existing views
            with the same name are replaced.

        Example
        -------
        ::

            import pyarrow as pa
            conn = DuckDBConnector()
            conn.register({
                "orders": pa.table({
                    "id": pa.array([1, 2, 3], type=pa.int32()),
                    "total": pa.array([9.99, 19.99, 4.99], type=pa.float64()),
                })
            })
        """
        for name, table in tables.items():
            # DuckDB can register a PyArrow table directly as a named relation.
            self._conn.register(name, table)
