"""Nubi connector SDK — post-fetch helpers and FunctionConnector.

This module provides:

1. **Post-fetch security and shaping helpers** — used by connectors that
   declare one or more capability flags as ``False`` (i.e. they cannot push
   down the operation to the underlying source):

   - ``apply_rls_postfetch`` — enforce Row-Level Security on the Python side.
   - ``apply_projection_postfetch`` — narrow the returned column set.
   - ``apply_limit_postfetch`` — slice the table to a row limit.

2. **FunctionConnector** — a ``Connector`` subclass that wraps any callable
   ``fn(plan: PhysicalPlan) -> pyarrow.Table`` as a first-class Nubi connector.
   It applies the post-fetch guards automatically based on the declared
   capability flags.

Security contract (MUST read before adding a new connector)
-----------------------------------------------------------
A connector whose ``capabilities()`` returns::

    {
        "predicate_pushdown": False,
        "predicate_rls":      True,
        ...
    }

**MUST** call ``apply_rls_postfetch`` before returning data to the caller.
This is the server-side RLS enforcement for non-SQL sources (REST APIs, NoSQL,
Python functions …).  The browser MUST never be trusted to filter rows.

``apply_rls_postfetch`` is designed to **fail closed**: if a policy references
a column that is absent from the returned table the function raises
``AppError("rls_column_missing", 403)`` rather than returning unfiltered data.
This is a deliberate security choice — a source that cannot honour a policy
MUST NOT return data, because silently returning the full dataset would violate
the tenant isolation guarantee.

``FunctionConnector.execute()`` enforces this contract automatically: when
``predicate_pushdown`` is ``False`` and ``predicate_rls`` is ``True``, post-fetch
RLS is applied unconditionally before the table is returned.  A connector author
who sets ``predicate_pushdown=True`` is asserting that their ``fn`` already
filtered the data; no post-fetch RLS is applied in that case (the planner encoded
the RLS predicates into the plan and the source handled them).

Projection post-fetch (``apply_projection_postfetch``)
-------------------------------------------------------
When ``projection_pushdown`` is ``False``, the source returns all columns.
``apply_projection_postfetch`` narrows the column set to ``plan.projection``.
Columns requested in ``projection`` that are ABSENT from the table are silently
ignored (intersection semantics).  This is safe because absent columns produce
nulls in SQL, and the caller already knows the schema from the connector's
declared schema metadata.  Document this if you need strict-miss behaviour.

Limit post-fetch (``apply_limit_postfetch``)
---------------------------------------------
A best-effort row cap.  Pass ``None`` to skip slicing.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

import pyarrow as pa
import pyarrow.compute as pc

from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.errors import AppError


# ---------------------------------------------------------------------------
# Post-fetch helpers
# ---------------------------------------------------------------------------


def apply_rls_postfetch(table: pa.Table, policies: dict[str, Any]) -> pa.Table:
    """Filter *table* to rows that satisfy all RLS *policies*.

    Each entry in *policies* is an equality constraint ``{column: value}``.
    Multiple policies are combined with AND (only rows matching every policy
    are retained).

    Fail-closed design
    ------------------
    If a policy references a column that is **absent** from *table*, the
    function raises ``AppError("rls_column_missing", 403)`` rather than
    returning unfiltered data.  This is a deliberate security choice:

    - A source that cannot honour a policy MUST NOT return data.
    - Silently ignoring a missing policy column would allow tenant data
      cross-contamination, which is a critical security failure.
    - 403 Forbidden is the correct HTTP status: the caller is authenticated
      but the source cannot satisfy the authorisation constraint.

    Parameters
    ----------
    table:
        The raw Arrow table returned by the source.
    policies:
        A ``{column_name: expected_value}`` dict.  Each key must be present
        in *table*'s schema.  An empty dict returns *table* unchanged.

    Returns
    -------
    pyarrow.Table
        A filtered table containing only rows where every policy column
        equals its expected value.

    Raises
    ------
    app.errors.AppError
        ``code="rls_column_missing"`` (403) if any policy column is absent
        from *table*.  Fail-closed — never returns unfiltered data.
    """
    if not policies:
        return table

    column_names = set(table.schema.names)

    # Validate all policy columns are present BEFORE filtering.
    # We check all columns up-front so the error message can name every
    # missing column in a single raise (rather than one-at-a-time).
    missing = [col for col in policies if col not in column_names]
    if missing:
        raise AppError(
            "rls_column_missing",
            (
                f"RLS policy references column(s) absent from the source table: "
                f"{sorted(missing)}. "
                "Returning unfiltered data would violate tenant isolation — "
                "failing closed (403)."
            ),
            status=403,
        )

    # Build a combined boolean mask: AND of all per-column equality masks.
    mask: pa.ChunkedArray | pa.Array | None = None
    for col, value in policies.items():
        col_array = table.column(col)
        col_mask = pc.equal(col_array, value)
        if mask is None:
            mask = col_mask
        else:
            mask = pc.and_(mask, col_mask)

    if mask is None:
        return table  # no policies (unreachable given early return, but defensive)

    return table.filter(mask)


def apply_projection_postfetch(
    table: pa.Table,
    projection: list[str] | None,
) -> pa.Table:
    """Select a subset of columns from *table*.

    When ``projection_pushdown`` is ``False``, the source returns all columns
    and this helper narrows the result to the requested set.

    Intersection semantics
    ----------------------
    Columns listed in *projection* that are ABSENT from *table* are silently
    ignored.  Only columns present in both *projection* and the table schema
    are selected.  This mirrors SQL ``SELECT`` behaviour where a missing column
    produces a null rather than an error, and avoids breaking connectors whose
    schema evolves (added columns become visible; removed columns are not
    fatal).

    Parameters
    ----------
    table:
        The Arrow table to narrow.
    projection:
        The list of column names to keep, or ``None`` to return *table*
        unchanged (full result set).

    Returns
    -------
    pyarrow.Table
        A table containing only the intersection of *projection* and the
        table's own columns, in *projection* order.  If *projection* is
        ``None`` the original *table* is returned unchanged.
    """
    if projection is None:
        return table

    column_names = set(table.schema.names)
    # Preserve the order specified in projection; silently drop absent columns.
    selected = [col for col in projection if col in column_names]

    if not selected:
        # Projection requested columns that are all absent; return empty table
        # with the original schema rather than an error (intersection = empty).
        return table.select([])

    return table.select(selected)


def apply_limit_postfetch(table: pa.Table, limit: int | None) -> pa.Table:
    """Slice *table* to at most *limit* rows.

    Parameters
    ----------
    table:
        The Arrow table to cap.
    limit:
        Maximum number of rows to return, or ``None`` to return *table*
        unchanged.  A *limit* of 0 returns an empty table (zero rows).

    Returns
    -------
    pyarrow.Table
        *table* sliced to the first *limit* rows, or *table* itself if
        *limit* is ``None``.
    """
    if limit is None:
        return table
    return table.slice(0, limit)


# ---------------------------------------------------------------------------
# FunctionConnector
# ---------------------------------------------------------------------------


class FunctionConnector(Connector):
    """Wrap any Python callable as a first-class Nubi connector.

    ``FunctionConnector`` is the primary extension point for non-SQL data
    sources: REST APIs, Python functions, in-memory data, mock fixtures, etc.
    The caller provides:

    - ``fn`` — a callable that accepts a ``PhysicalPlan`` and returns a
      ``pyarrow.Table`` containing the **raw** source data (all rows, all
      columns the source can provide).
    - ``capabilities`` — a dict declaring which push-downs the source
      implements.  This controls which post-fetch guards are applied
      automatically.

    Post-fetch guard contract
    -------------------------
    ``execute()`` applies the following guards **automatically** based on the
    declared capabilities:

    1. **RLS guard** — if ``predicate_pushdown=False`` AND
       ``predicate_rls=True``: call ``apply_rls_postfetch(table,
       plan.rls_claims.get('policies', {}))``.

       This is the server-side RLS for non-SQL sources.  The browser MUST
       never be trusted to filter rows.  ``apply_rls_postfetch`` fails closed
       on a missing policy column (403).

       If ``predicate_pushdown=True``, the caller's ``fn`` is assumed to have
       already filtered the data (the planner encoded the RLS predicates into
       the plan and the source handled them).  No post-fetch RLS is applied.

    2. **Projection guard** — if ``projection_pushdown=False`` and
       ``plan.projection`` is set: call ``apply_projection_postfetch``.

    3. **Limit guard** — best-effort row cap encoded in ``plan.params`` as the
       last element when the SQL ends with ``LIMIT ?`` / ``LIMIT $N``.
       ``FunctionConnector`` extracts a limit from the plan on a best-effort
       basis (see ``_extract_limit``).

    Parameters
    ----------
    fn:
        ``fn(plan: PhysicalPlan) -> pyarrow.Table``.  Called once per
        ``execute()`` invocation.  The function MUST return a ``pa.Table``;
        it MUST NOT apply RLS filtering — that is ``FunctionConnector``'s job.
    capabilities:
        The 7-flag capability dict (see ``Connector.capabilities()``).  All
        seven keys must be present; ``validate_capabilities()`` is called in
        ``__init__`` to enforce this.

    Example
    -------
    ::

        import pyarrow as pa
        from app.connectors.sdk import FunctionConnector

        def my_source(plan):
            return pa.table({
                "tenant_id": ["acme", "acme", "globex"],
                "value":     [1,      2,      3],
            })

        conn = FunctionConnector(
            fn=my_source,
            capabilities={
                "native_arrow":        True,
                "predicate_pushdown":  False,
                "projection_pushdown": False,
                "partition_pushdown":  False,
                "predicate_rls":       True,
                "column_masking":      False,
                "streaming_cdc":       False,
            },
        )

        # execute() will auto-apply RLS; only acme rows are returned.
        plan = make_plan(rls_claims={"policies": {"tenant_id": "acme"}})
        table = conn.execute(plan)
        assert table.num_rows == 2
    """

    def __init__(
        self,
        fn: Callable[[PhysicalPlan], pa.Table],
        capabilities: dict[str, bool],
    ) -> None:
        self._fn = fn
        self._caps = capabilities
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, bool]:
        """Return the capability flags supplied at construction time."""
        return self._caps

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(self, plan: PhysicalPlan) -> pa.Table:
        """Execute *plan* and return a post-fetch-guarded Arrow table.

        Steps
        -----
        1. Call ``self._fn(plan)`` to obtain raw source data.
        2. If ``predicate_pushdown=False`` and ``predicate_rls=True``:
           apply ``apply_rls_postfetch`` using ``plan.rls_claims['policies']``.
        3. If ``projection_pushdown=False`` and ``plan.projection`` is set:
           apply ``apply_projection_postfetch``.
        4. Apply ``apply_limit_postfetch`` (best-effort; see ``_extract_limit``).

        Returns
        -------
        pyarrow.Table
            Filtered, projected, and capped result.

        Raises
        ------
        app.errors.AppError
            ``code="rls_column_missing"`` (403) if step 2 encounters a policy
            column absent from the source table (fail-closed).
        """
        caps = self._caps

        # Step 1: fetch raw data from the underlying source.
        table: pa.Table = self._fn(plan)

        # Step 2: RLS post-fetch guard.
        # A non-pushdown source with predicate_rls=True MUST have RLS applied
        # server-side.  apply_rls_postfetch fails closed on missing columns.
        if not caps.get("predicate_pushdown", False) and caps.get("predicate_rls", False):
            policies: dict[str, Any] = plan.rls_claims.get("policies", {})
            table = apply_rls_postfetch(table, policies)

        # Step 3: projection post-fetch guard.
        if not caps.get("projection_pushdown", False) and plan.projection:
            table = apply_projection_postfetch(table, plan.projection)

        # Step 4: limit post-fetch guard (best-effort).
        limit = _extract_limit(plan)
        table = apply_limit_postfetch(table, limit)

        return table

    def execute_stream(self, plan: PhysicalPlan) -> Iterator[pa.RecordBatch]:
        """Execute *plan* and yield result as a stream of RecordBatches.

        Delegates to ``execute()`` so all post-fetch guards are applied, then
        yields the resulting table's batches one at a time.

        Yields
        ------
        pyarrow.RecordBatch
            One or more batches from the post-fetch-guarded result.
        """
        table = self.execute(plan)
        yield from table.to_batches()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_limit(plan: PhysicalPlan) -> int | None:
    """Best-effort extraction of a LIMIT value from *plan*.

    The Nubi planner encodes a LIMIT clause by appending ``LIMIT ?`` /
    ``LIMIT $N`` to ``plan.sql`` and placing the limit value as the last
    element of ``plan.params``.  This helper detects that pattern and returns
    the limit as an ``int``, or ``None`` if no limit is detectable.

    This is best-effort: if the SQL does not end with a LIMIT clause the
    function returns ``None`` and no row-capping is applied.  A wrong guess
    (false-positive) would merely cap more rows than needed, which is safe.
    A false-negative means no cap is applied, which is also safe (the caller
    gets more rows than requested, not fewer).

    Parameters
    ----------
    plan:
        The physical plan to inspect.

    Returns
    -------
    int | None
        The detected limit value, or ``None``.
    """
    sql_upper = plan.sql.upper().rstrip()
    # Match "LIMIT ?" or "LIMIT $N" patterns at the end of the SQL.
    if plan.params and ("LIMIT ?" in sql_upper or _has_limit_placeholder(sql_upper)):
        last_param = plan.params[-1]
        if isinstance(last_param, int) and last_param >= 0:
            return last_param
    return None


def _has_limit_placeholder(sql_upper: str) -> bool:
    """Return True if *sql_upper* ends with a LIMIT $N placeholder."""
    import re
    return bool(re.search(r"LIMIT\s+\$\d+\s*$", sql_upper))
