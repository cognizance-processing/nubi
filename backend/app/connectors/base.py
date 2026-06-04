"""Abstract base class for Nubi connectors.

Each connector wraps one data source and declares its capabilities via the
``capabilities()`` method.  The planner queries capabilities to decide which
push-downs are safe; the executor calls ``execute()`` or ``execute_stream()``
to materialise the result.

Design contract (ROADMAP §3.1, §4.1)
--------------------------------------
- ``capabilities()`` returns the 7-flag dict from §4.1.
- ``execute(plan)`` returns a ``pyarrow.Table`` (batch / materialise path).
- ``execute_stream(plan)`` returns an ``Iterator[pyarrow.RecordBatch]``
  (streaming path for large result sets).
- Both methods receive a fully-baked ``PhysicalPlan``; they MUST NOT rewrite
  SQL or touch RLS logic — that is the planner's job.
- Connectors are stateless w.r.t. individual queries; connection pools may live
  in instance state.

Adding a new connector
-----------------------
1. Subclass ``Connector``.
2. Implement ``capabilities()``, ``execute()``, and ``execute_stream()``.
3. Register it in the connector registry (Wave M1-B).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

from app.connectors.plan import PhysicalPlan


class Connector(ABC):
    """Abstract base for all Nubi data-source connectors.

    Concrete subclasses must implement the three abstract methods below.
    All other methods have sensible defaults and need not be overridden.
    """

    # ------------------------------------------------------------------
    # Capability descriptor
    # ------------------------------------------------------------------

    @abstractmethod
    def capabilities(self) -> dict[str, bool]:
        """Return the capability flags for this connector.

        Returns
        -------
        dict[str, bool]
            A dict with exactly the following keys (all booleans):

            ``native_arrow``
                The connector can return data as Arrow IPC natively (e.g. via
                ADBC) without a Python-level row-by-row conversion.
            ``predicate_pushdown``
                The connector can push WHERE predicates down to the source,
                reducing the amount of data transferred.
            ``projection_pushdown``
                The connector can push column selection down to the source so
                that only requested columns are fetched.
            ``partition_pushdown``
                The connector can route queries to specific partitions/shards
                based on partition-key predicates.
            ``predicate_rls``
                The connector supports AST-level predicate injection for
                Row-Level Security (enforced inside the connector, never
                browser-side).
            ``column_masking``
                The connector can mask/redact column values before they leave
                the connector boundary (e.g. nullify PII columns for
                unauthorised callers).
            ``streaming_cdc``
                The connector can stream Change-Data-Capture events for
                real-time / live-dashboard use cases.

        All seven keys MUST be present.  The planner will raise a ``KeyError``
        if any are missing.

        Example
        -------
        ::

            def capabilities(self) -> dict[str, bool]:
                return {
                    "native_arrow": True,
                    "predicate_pushdown": True,
                    "projection_pushdown": True,
                    "partition_pushdown": False,
                    "predicate_rls": True,
                    "column_masking": False,
                    "streaming_cdc": False,
                }
        """

    # ------------------------------------------------------------------
    # Execution interface
    # ------------------------------------------------------------------

    @abstractmethod
    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        """Execute *plan* and return the full result as a PyArrow Table.

        This is the batch/materialise path.  Use ``execute_stream()`` for
        large result sets that should not be loaded into memory at once.

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan`` produced by the planner.  The SQL
            in ``plan.sql`` is ready to run verbatim; do NOT rewrite it here.

        Returns
        -------
        pyarrow.Table
            The query result.  Column names and types are determined by the
            source; the planner's ``projection`` field is already encoded in
            ``plan.sql``.

        Raises
        ------
        app.errors.AppError
            With an appropriate ``code`` and HTTP status if the query fails.
        """

    @abstractmethod
    def execute_stream(self, plan: PhysicalPlan) -> Iterator["pa.RecordBatch"]:
        """Execute *plan* and yield result data as a stream of RecordBatches.

        Use this for large result sets.  The caller is responsible for reading
        all batches and releasing resources (e.g. closing the ADBC cursor).

        Parameters
        ----------
        plan:
            A fully-baked ``PhysicalPlan`` produced by the planner.

        Yields
        ------
        pyarrow.RecordBatch
            One or more record batches making up the full result set.
            Batch size is implementation-defined.

        Raises
        ------
        app.errors.AppError
            With an appropriate ``code`` and HTTP status if the query fails.
        """

    # ------------------------------------------------------------------
    # Optional helper
    # ------------------------------------------------------------------

    def validate_capabilities(self) -> None:
        """Assert that ``capabilities()`` returns all required keys.

        Called at connector construction time so misconfigured connectors fail
        fast rather than surfacing a ``KeyError`` during a live query.

        Raises
        ------
        ValueError
            If any required capability key is missing or the value is not bool.
        """
        _REQUIRED: frozenset[str] = frozenset(
            {
                "native_arrow",
                "predicate_pushdown",
                "projection_pushdown",
                "partition_pushdown",
                "predicate_rls",
                "column_masking",
                "streaming_cdc",
            }
        )
        caps: dict[str, Any] = self.capabilities()
        missing = _REQUIRED - caps.keys()
        if missing:
            raise ValueError(
                f"{type(self).__name__}.capabilities() is missing keys: {sorted(missing)}"
            )
        non_bool = {k: v for k, v in caps.items() if not isinstance(v, bool)}
        if non_bool:
            raise ValueError(
                f"{type(self).__name__}.capabilities() has non-bool values: {non_bool}"
            )
