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
from typing import Any

import sqlglot
import sqlglot.expressions as exp

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
        tree = sqlglot.parse_one(sql, dialect=dialect)
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
