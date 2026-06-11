"""Query log — in-memory ring buffer for recording executed queries.

Records ``{sql, cache_key, groupby_sig, ts, byte_size}`` entries as queries
execute.  The ``groupby_sig`` is a normalised string representation of the
GROUP BY structure (base tables, sorted dimension columns, sorted aggregate
measures) used by the pre-aggregation suggester to spot repeated patterns.

Public API
----------
record(sql, cache_key, ts=None, byte_size=0)
    Parse *sql*, extract a groupby_sig, and append an entry to the ring buffer.
    Silently skips entries whose SQL cannot be parsed or has no GROUP BY.

entries() -> list[dict]
    Return a snapshot of all current log entries (oldest-first).

get_query_log() -> QueryLog
    Return the module-level singleton.

compute_groupby_sig(sql) -> str | None
    Shared helper — also imported by ``preagg.py`` and ``planner.py`` so that
    the sig computation is a single source of truth.

Design
------
- ``collections.deque(maxlen=5000)`` — O(1) append, automatic eviction of the
  oldest entries when full.
- Thread-safe: GIL protects deque append/iterate in CPython.  For async use,
  the ring buffer is append-only so iteration snapshots are safe.
- ``ts`` is accepted as a parameter and defaults to ``time.time()`` inside
  ``record()`` (never evaluated at import time).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import sqlglot
import sqlglot.expressions as exp

from app.connectors.sql_parse import parse_sql_cached

# ---------------------------------------------------------------------------
# Shared normalisation helper
# ---------------------------------------------------------------------------

_AGG_FUNC_NAMES = {
    "SUM", "COUNT", "AVG", "MIN", "MAX",
    "STDDEV", "STDDEV_POP", "STDDEV_SAMP",
    "VAR_POP", "VAR_SAMP", "VARIANCE",
    "APPROX_COUNT_DISTINCT", "COUNT_IF",
    "MEDIAN", "PERCENTILE", "PERCENTILE_CONT", "PERCENTILE_DISC",
    "FIRST", "LAST", "ANY_VALUE",
    "BIT_AND", "BIT_OR", "BIT_XOR",
    "BOOL_AND", "BOOL_OR",
}

# Typed aggregate expression classes present in this version of sqlglot.
# Use getattr+hasattr to avoid AttributeError if a class is unavailable.
_AGG_TYPED_CLASSES = tuple(
    getattr(exp, name)
    for name in (
        "Sum", "Count", "Avg", "Min", "Max",
        "Stddev", "StddevPop", "StddevSamp",
        "Variance", "VariancePop",
        "ApproxDistinct", "CountIf",
        "Median", "AnyValue",
        "BitwiseAndAgg", "BitwiseOrAgg", "BitmapConstructAgg",
        "BoolxorAgg",
    )
    if hasattr(exp, name)
)


def _expr_to_str(node: exp.Expression) -> str:
    """Collapse an AST node to a lower-case SQL string for normalisation."""
    return node.sql(dialect="postgres").lower().strip()


def _is_agg(node: exp.Expression) -> bool:
    """Return True if *node* is a recognised aggregate function call."""
    if isinstance(node, exp.Anonymous):
        return node.name.upper() in _AGG_FUNC_NAMES
    # sqlglot models common aggs as typed classes (exp.Sum, exp.Count, …)
    return isinstance(node, _AGG_TYPED_CLASSES)


def compute_groupby_sig(sql: str, dialect: str = "postgres") -> str | None:
    """Compute a normalised GROUP BY signature for *sql*.

    Returns ``None`` when the SQL has no GROUP BY clause, when parsing fails,
    or when it is not a SELECT statement.

    The signature format is::

        "<base_tables>|dims=<dim1>,<dim2>|aggs=<agg1>,<agg2>"

    where:
    - ``<base_tables>`` — comma-joined, sorted table names from the FROM clause
      (aliases resolved to their base name).
    - ``<dim_i>`` — sorted, lower-case GROUP BY column expressions.
    - ``<agg_i>`` — sorted, lower-case aggregate function expressions from the
      SELECT list (columns not in GROUP BY whose expression is an aggregate).

    Sorting ensures the sig is order-independent: ``GROUP BY b, a`` and
    ``GROUP BY a, b`` produce the same sig.

    Parameters
    ----------
    sql:
        A SQL string (ideally a SELECT).
    dialect:
        sqlglot dialect for parsing/generation.  Default ``"postgres"``.

    Returns
    -------
    str | None
        The normalised sig, or ``None`` if the query has no GROUP BY.
    """
    try:
        tree = parse_sql_cached(sql, dialect=dialect)
    except Exception:
        return None

    if not isinstance(tree, exp.Select):
        return None

    group_node = tree.args.get("group")
    if group_node is None:
        return None

    # ── Base tables ──────────────────────────────────────────────────────────
    tables: list[str] = []
    for t in tree.find_all(exp.Table):
        tables.append(t.name.lower())
    tables_str = ",".join(sorted(set(tables))) if tables else "unknown"

    # ── GROUP BY dimensions ──────────────────────────────────────────────────
    dim_exprs: list[str] = []
    for item in group_node.expressions:
        dim_exprs.append(_expr_to_str(item))
    dims_str = ",".join(sorted(dim_exprs))

    # ── Aggregate measures from the SELECT list ──────────────────────────────
    agg_exprs: list[str] = []
    group_col_strs = set(dim_exprs)
    for sel_item in tree.expressions:
        # Unwrap aliases
        inner = sel_item.this if isinstance(sel_item, exp.Alias) else sel_item
        if _is_agg(inner):
            agg_exprs.append(_expr_to_str(inner))
        elif not _is_agg(inner) and _expr_to_str(inner) not in group_col_strs:
            # Could be a non-aggregate, non-group-by col — skip for the sig
            pass

    aggs_str = ",".join(sorted(agg_exprs))

    return f"{tables_str}|dims={dims_str}|aggs={aggs_str}"


# ---------------------------------------------------------------------------
# Structured shape extraction (for the pre-agg miner + router)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueryShape:
    """A structured, normalised description of an aggregating SELECT.

    This is the richer counterpart to :func:`compute_groupby_sig`.  Where the
    sig is a single opaque string keyed for exact-match routing, ``QueryShape``
    keeps the parsed components so the miner can cluster compatible shapes and
    the router can reason about superset-rewrites (group-by ⊆ rollup dims,
    measures derivable, filters on rollup columns).

    Attributes
    ----------
    base_table:
        The single source table name (lower-cased).  ``None`` if the query has
        zero or more than one base table (joins are out of scope for routing —
        we only mine/route single-fact aggregations conservatively).
    dimensions:
        Sorted list of bare GROUP BY column names (lower-cased).  Only simple
        column references are kept; expression group-bys (e.g. ``date_trunc(..)``)
        are recorded in ``dimension_exprs`` instead and make the shape
        non-routable (we refuse to reason about derived grains).
    dimension_exprs:
        Sorted list of non-trivial GROUP BY expressions (anything that is not a
        plain column).  A non-empty list marks the shape as non-routable.
    measures:
        Sorted list of ``(func, column)`` tuples for each aggregate in the
        SELECT list, e.g. ``("sum", "amount")``.  ``column`` is ``"*"`` for
        ``COUNT(*)``.  An aggregate whose argument is an expression is recorded
        with ``column=None`` and makes the shape non-routable.
    filter_columns:
        Sorted list of bare column names referenced in the WHERE clause.  Used
        by the router to ensure a candidate rollup carries every filtered
        column (so the predicate can still be applied post-rollup).
    routable:
        ``True`` only when the shape is a simple single-table aggregation with
        plain-column dimensions and plain-column (or ``*``) measures.  The
        router MUST refuse anything with ``routable=False``.
    """

    base_table: str | None
    dimensions: tuple[str, ...] = ()
    dimension_exprs: tuple[str, ...] = ()
    measures: tuple[tuple[str, str | None], ...] = ()
    filter_columns: tuple[str, ...] = ()
    routable: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable view (measures rendered as ``func(col)`` strings)."""
        return {
            "base_table": self.base_table,
            "dimensions": list(self.dimensions),
            "dimension_exprs": list(self.dimension_exprs),
            "measures": [_measure_str(f, c) for (f, c) in self.measures],
            "filter_columns": list(self.filter_columns),
            "routable": self.routable,
        }


def _measure_str(func: str, col: str | None) -> str:
    """Render a ``(func, col)`` measure tuple back to a ``func(col)`` string."""
    return f"{func}({col if col is not None else '?'})"


def _agg_func_name(node: exp.Expression) -> str | None:
    """Return the lower-case aggregate function name for *node*, or None."""
    if isinstance(node, exp.Anonymous):
        name = node.name.upper()
        return name.lower() if name in _AGG_FUNC_NAMES else None
    if isinstance(node, _AGG_TYPED_CLASSES):
        # Map the typed class to a canonical SQL function name.
        return type(node).__name__.lower()
    return None


def _agg_arg_column(node: exp.Expression) -> str | None:
    """Return the bare column argument of an aggregate, ``"*"`` for COUNT(*),
    or ``None`` when the argument is a non-column expression."""
    # COUNT(*) → exp.Count with a Star arg (or no expression).
    if isinstance(node, exp.Count):
        inner = node.this
        if inner is None or isinstance(inner, exp.Star):
            return "*"
    arg = node.this if not isinstance(node, exp.Anonymous) else (
        node.expressions[0] if node.expressions else None
    )
    if arg is None:
        return "*"
    if isinstance(arg, exp.Star):
        return "*"
    if isinstance(arg, exp.Column):
        return arg.name.lower()
    if isinstance(arg, exp.Distinct):
        exprs = arg.expressions
        if len(exprs) == 1 and isinstance(exprs[0], exp.Column):
            return exprs[0].name.lower()
    return None  # expression argument → not derivable


def extract_shape(sql: str, dialect: str = "postgres") -> QueryShape | None:
    """Parse *sql* into a :class:`QueryShape`, or return ``None``.

    Returns ``None`` when the SQL is not a single-statement SELECT with a GROUP
    BY clause (those are the only queries the pre-agg system cares about).  A
    parseable aggregating query that is too complex to route soundly is still
    returned, but with ``routable=False`` so the router rejects it while the
    miner can still surface it as a (non-routable) candidate.
    """
    try:
        tree = parse_sql_cached(sql, dialect=dialect)
    except Exception:
        return None
    if not isinstance(tree, exp.Select):
        return None
    group_node = tree.args.get("group")
    if group_node is None:
        return None

    # ── Base table(s): only a single table is routable. ──────────────────────
    table_names = sorted({t.name.lower() for t in tree.find_all(exp.Table)})
    base_table = table_names[0] if len(table_names) == 1 else None

    # ── Dimensions ───────────────────────────────────────────────────────────
    dims: list[str] = []
    dim_exprs: list[str] = []
    for item in group_node.expressions:
        target = item.this if isinstance(item, exp.Alias) else item
        if isinstance(target, exp.Column):
            dims.append(target.name.lower())
        else:
            dim_exprs.append(_expr_to_str(target))

    # ── Measures (aggregates in the SELECT list) ─────────────────────────────
    measures: list[tuple[str, str | None]] = []
    has_bad_measure = False
    has_non_agg_non_dim = False
    dim_set = set(dims)
    for sel_item in tree.expressions:
        inner = sel_item.this if isinstance(sel_item, exp.Alias) else sel_item
        func = _agg_func_name(inner)
        if func is not None:
            col = _agg_arg_column(inner)
            if col is None:
                has_bad_measure = True
            measures.append((func, col))
        elif isinstance(inner, exp.Column):
            if inner.name.lower() not in dim_set:
                has_non_agg_non_dim = True
        else:
            # A bare expression (e.g. CASE / arithmetic) in the SELECT list.
            has_non_agg_non_dim = True

    # ── Filter columns (WHERE clause bare columns) ───────────────────────────
    filter_cols: set[str] = set()
    where_node = tree.args.get("where")
    if where_node is not None:
        for col in where_node.find_all(exp.Column):
            filter_cols.add(col.name.lower())

    # A shape is routable only when it is a clean single-table aggregation.
    routable = (
        base_table is not None
        and not dim_exprs
        and not has_bad_measure
        and not has_non_agg_non_dim
        and tree.args.get("having") is None
        and tree.args.get("distinct") is None
    )

    return QueryShape(
        base_table=base_table,
        dimensions=tuple(sorted(dims)),
        dimension_exprs=tuple(sorted(dim_exprs)),
        measures=tuple(sorted(measures)),
        filter_columns=tuple(sorted(filter_cols)),
        routable=routable,
    )


# ---------------------------------------------------------------------------
# QueryLog
# ---------------------------------------------------------------------------

_MAXLEN = 5000


class QueryLog:
    """In-memory ring buffer recording executed query metadata.

    Parameters
    ----------
    maxlen:
        Maximum number of entries retained.  Oldest entries are evicted
        automatically when the buffer is full (``collections.deque`` semantics).
    """

    def __init__(self, maxlen: int = _MAXLEN) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=maxlen)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        sql: str,
        cache_key: str,
        ts: float | None = None,
        byte_size: int = 0,
    ) -> None:
        """Record a query execution in the ring buffer.

        Parameters
        ----------
        sql:
            The rewritten SQL string that was executed (from ``PhysicalPlan.sql``).
        cache_key:
            The content-addressed cache key for this plan.
        ts:
            Unix timestamp (seconds).  Defaults to ``time.time()`` at call time
            (NOT at import time — safe for use in tests with controlled clocks).
        byte_size:
            Approximate byte size of the Arrow result.  Used by the suggester
            to estimate ``est_bytes_saved``.
        """
        if ts is None:
            ts = time.time()

        sig = compute_groupby_sig(sql)
        if sig is None:
            # No GROUP BY — not interesting for pre-agg suggestions; still log.
            sig = ""

        self._buf.append(
            {
                "sql": sql,
                "cache_key": cache_key,
                "groupby_sig": sig,
                "ts": ts,
                "byte_size": byte_size,
            }
        )

    def entries(self) -> list[dict[str, Any]]:
        """Return a snapshot list of all current log entries (oldest-first).

        Returns
        -------
        list[dict]
            Each entry has keys: ``sql``, ``cache_key``, ``groupby_sig``,
            ``ts``, ``byte_size``.
        """
        return list(self._buf)

    def clear(self) -> None:
        """Remove all entries from the ring buffer.  Useful in tests."""
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_query_log: QueryLog | None = None


def get_query_log() -> QueryLog:
    """Return the process-wide ``QueryLog`` singleton.

    The singleton is created lazily on first call so that importing this module
    has no side-effects (no ``datetime.now()`` at import time, no global state
    mutations).
    """
    global _query_log
    if _query_log is None:
        _query_log = QueryLog()
    return _query_log


def reset_for_tests() -> None:
    """Clear the query log ring buffer for test isolation.

    Empties the deque without replacing the singleton so existing references
    remain valid.  This is intentionally a test-only helper — production code
    should never call it.
    """
    global _query_log
    if _query_log is not None:
        _query_log.clear()
    # If not yet created, the empty default on first get_query_log() is fine.
