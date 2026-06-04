"""Pushdown optimizer — pure sqlglot AST transforms for M2-A.

All functions operate on a *parsed* sqlglot ``Select`` AST node and return the
(possibly modified) node.  No string concatenation is ever performed; every
predicate is built from sqlglot expression objects.

Public API
----------
prune_projection(tree, columns)
    Replace the SELECT list with only the specified columns (idempotent).

push_predicates(tree, predicates)
    Add equality/comparison predicates to the WHERE clause via AST.

push_limit(tree, limit)
    Set or lower a LIMIT clause.

extract_partition_hints(tree) -> list[str]
    Return column names used in WHERE that look like partition/cluster keys
    (heuristic: *_date, *_dt, date, day, month, ts, created_at, partition*).

Thread safety
-------------
All functions are pure (they operate on the AST in place but sqlglot nodes are
not shared across call sites when ``parse_one`` is called per request).  Safe
to call concurrently.
"""

from __future__ import annotations

import re
from typing import Any

import sqlglot
import sqlglot.expressions as exp

# ---------------------------------------------------------------------------
# Heuristic patterns for partition / cluster key detection
# ---------------------------------------------------------------------------

_PARTITION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r".*_date$", re.IGNORECASE),
    re.compile(r".*_dt$", re.IGNORECASE),
    re.compile(r"^date$", re.IGNORECASE),
    re.compile(r"^day$", re.IGNORECASE),
    re.compile(r"^month$", re.IGNORECASE),
    re.compile(r"^ts$", re.IGNORECASE),
    re.compile(r"^created_at$", re.IGNORECASE),
    re.compile(r"^partition.*", re.IGNORECASE),
]


def _looks_like_partition_key(col_name: str) -> bool:
    """Return True if *col_name* matches any partition-key heuristic pattern."""
    return any(p.match(col_name) for p in _PARTITION_PATTERNS)


# ---------------------------------------------------------------------------
# prune_projection
# ---------------------------------------------------------------------------


def prune_projection(tree: exp.Select, columns: list[str]) -> exp.Select:
    """Replace the SELECT list with only *columns* (idempotent).

    If *columns* is empty the tree is returned unchanged (no-op guard).

    Parameters
    ----------
    tree:
        A parsed sqlglot ``Select`` node.  Modified **in place** and returned.
    columns:
        Column names to retain.  Order is preserved.

    Returns
    -------
    exp.Select
        The same node with the SELECT list narrowed to *columns*.

    Examples
    --------
    >>> import sqlglot
    >>> tree = sqlglot.parse_one("SELECT id, name, email FROM users")
    >>> tree = prune_projection(tree, ["id", "email"])
    >>> tree.sql(dialect="postgres")
    'SELECT id, email FROM users'
    """
    if not columns:
        return tree

    new_cols = [exp.column(col_name) for col_name in columns]
    tree.set("expressions", new_cols)
    return tree


# ---------------------------------------------------------------------------
# push_predicates
# ---------------------------------------------------------------------------


def _build_predicate_node(predicate: str | tuple[str, str, Any]) -> exp.Expression:
    """Convert a predicate spec into a sqlglot expression node.

    Accepted forms
    ~~~~~~~~~~~~~~
    ``str``
        A raw SQL predicate fragment (e.g. ``"status = 1"``).  Parsed by sqlglot.
        Only use when the string is trusted / not user-supplied.

    ``(col, op, value)`` tuple
        Column name, operator string (``"="``, ``"<"``, ``">"`` etc.), and a
        scalar value.  The value is converted to the appropriate sqlglot literal
        type (bool → ``Boolean``, int/float → ``Literal.number``,
        anything else → ``Literal.string``).

    ``{col: value}`` dict
        Single-key dict treated as equality.  Equivalent to ``(col, "=", value)``.
    """
    if isinstance(predicate, dict):
        if len(predicate) != 1:
            raise ValueError(
                "Dict predicates must have exactly one key; "
                f"got {len(predicate)} keys: {list(predicate.keys())}"
            )
        col_name, value = next(iter(predicate.items()))
        predicate = (col_name, "=", value)

    if isinstance(predicate, tuple):
        col_name, op, value = predicate
        lhs = exp.Column(this=exp.Identifier(this=col_name, quoted=False))

        if isinstance(value, bool):
            rhs: exp.Expression = exp.Boolean(this=value)
        elif isinstance(value, (int, float)):
            rhs = exp.Literal.number(value)
        else:
            rhs = exp.Literal.string(str(value))

        op_upper = op.strip().upper()
        _op_map: dict[str, type[exp.Expression]] = {
            "=": exp.EQ,
            "!=": exp.NEQ,
            "<>": exp.NEQ,
            "<": exp.LT,
            "<=": exp.LTE,
            ">": exp.GT,
            ">=": exp.GTE,
        }
        node_cls = _op_map.get(op_upper)
        if node_cls is None:
            raise ValueError(f"Unsupported predicate operator: {op!r}")
        return node_cls(this=lhs, expression=rhs)

    if isinstance(predicate, str):
        # Parse the string as a SQL expression (dialect-agnostic).
        parsed = sqlglot.parse_one(predicate, dialect="postgres")
        return parsed

    raise TypeError(
        f"predicate must be a str, tuple, or single-key dict; got {type(predicate)}"
    )


def push_predicates(
    tree: exp.Select,
    predicates: list[str | tuple[str, str, Any]],
) -> exp.Select:
    """Add *predicates* to the WHERE clause of *tree* via AST (never string-concat).

    Predicates are ANDed together and ANDed with any existing WHERE conditions.

    Parameters
    ----------
    tree:
        A parsed sqlglot ``Select`` node.
    predicates:
        A list of predicate specs.  Each element may be:

        - A ``(col, op, value)`` tuple — e.g. ``("status", "=", 1)``
        - A single-key dict — e.g. ``{"tenant_id": "acme"}``
        - A raw SQL string — e.g. ``"created_at > '2024-01-01'"``
          (string is parsed via sqlglot; do NOT pass untrusted user input as raw
          strings — use the tuple form instead).

    Returns
    -------
    exp.Select
        The modified ``Select`` node with predicates added.
    """
    for pred_spec in predicates:
        pred_node = _build_predicate_node(pred_spec)
        tree = tree.where(pred_node)
    return tree


# ---------------------------------------------------------------------------
# push_limit
# ---------------------------------------------------------------------------


def push_limit(tree: exp.Select, limit: int) -> exp.Select:
    """Set or lower the LIMIT clause of *tree*.

    If the existing LIMIT is already *smaller* than *limit*, the smaller value
    is kept (i.e. we never raise the limit, only lower it or set one that was
    absent).

    Parameters
    ----------
    tree:
        A parsed sqlglot ``Select`` node.
    limit:
        The desired maximum row count.  Must be a positive integer.

    Returns
    -------
    exp.Select
        The modified ``Select`` node with a LIMIT clause set.

    Raises
    ------
    ValueError
        If *limit* is not a positive integer.
    """
    if limit <= 0:
        raise ValueError(f"limit must be a positive integer, got {limit!r}")

    existing_limit_node = tree.args.get("limit")
    if existing_limit_node is not None:
        # Extract the integer value from the existing LIMIT expression.
        # sqlglot stores the limit value in the ``expression`` field (not ``this``).
        existing_expr = existing_limit_node.args.get("expression")
        try:
            existing_value = int(existing_expr.name)
        except (AttributeError, ValueError, TypeError):
            existing_value = None

        if existing_value is not None and existing_value <= limit:
            # Existing LIMIT is already smaller — keep it.
            return tree

    # Set / lower the LIMIT.
    tree.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
    return tree


# ---------------------------------------------------------------------------
# extract_partition_hints
# ---------------------------------------------------------------------------


def extract_partition_hints(tree: exp.Select) -> list[str]:
    """Collect columns used in WHERE that look like partition/cluster keys.

    This is an *informational* heuristic — the result is used for observability
    and routing hints, not for correctness.  False positives are harmless.

    Heuristic rules (any column name that matches):
    - Ends with ``_date`` or ``_dt``
    - Is exactly ``date``, ``day``, ``month``, ``ts``, or ``created_at``
    - Starts with ``partition``

    Parameters
    ----------
    tree:
        A parsed sqlglot ``Select`` node.

    Returns
    -------
    list[str]
        Deduplicated list of column names (lowercase) that appear in the WHERE
        clause and match the partition-key heuristic.  Empty list if no match.
    """
    where_node = tree.args.get("where")
    if where_node is None:
        return []

    hints: list[str] = []
    seen: set[str] = set()

    for col_node in where_node.find_all(exp.Column):
        col_name: str = col_node.name.lower()
        if col_name not in seen and _looks_like_partition_key(col_name):
            hints.append(col_name)
            seen.add(col_name)

    return hints
