"""Query planner — compile ``(sql, claims, projection)`` into a ``PhysicalPlan``.

Pipeline (M2-A extended)
------------------------
1. Parse the SQL with sqlglot (AST, no string manipulation).
2. Reject non-SELECT statements immediately (security: no arbitrary DML/DDL).
3. Optionally narrow the column list to ``projection`` via ``optimize.prune_projection``.
4. Push any caller-supplied ``predicates`` into WHERE via ``optimize.push_predicates``.
5. Inject RLS predicates from ``claims["policies"]`` as AST-level ``col = value``
   equalities added to the WHERE clause.  NEVER string-concat.
6. Optionally push a ``limit`` via ``optimize.push_limit``.
7. Re-generate SQL from the modified AST.
8. Compute the cache key via ``cache_key.compute_cache_key`` (uses final SQL).
9. Return a frozen ``PhysicalPlan``.

Backwards-compatibility guarantee (M1 conformance)
---------------------------------------------------
When called with no ``limit`` and no ``predicates`` arguments the pipeline
produces EXACTLY the same SQL and cache_key as the M1 planner.  Steps 4 and 6
are no-ops in that case, so the transform order and output are unchanged.

RLS contract (M1 — equality-only)
-----------------------------------
``claims["policies"]`` is a ``{column: value}`` dict.  Each entry becomes an
``AND col = 'value'`` predicate added to the WHERE clause.  The cache key
derives from exactly this same dict so the two are always in sync.

Thread safety
-------------
The planner is stateless; ``plan()`` is safe to call concurrently.
"""

from __future__ import annotations

from typing import Any, Iterator

import sqlglot
import sqlglot.expressions as exp

from app.connectors.cache_key import compute_cache_key
from app.connectors.optimize import prune_projection, push_limit, push_predicates
from app.connectors.plan import PhysicalPlan
from app.connectors.query_log import compute_groupby_sig
from app.errors import AppError

# Default dialect for SQL generation.  Overridable per-call.
_DEFAULT_DIALECT: str = "postgres"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_eq_predicate(column: str, value: Any) -> exp.EQ:
    """Build an AST ``column = value`` equality node for a single RLS claim.

    Parameters
    ----------
    column:
        The column name (unquoted identifier).
    value:
        The scalar value.  Typed mapping:
        - ``bool``  → ``exp.Boolean``
        - ``int`` / ``float`` → ``exp.Literal.number``
        - anything else → ``exp.Literal.string(str(value))``

    Returns
    -------
    exp.EQ
        A sqlglot EQ expression node ready to be added to a WHERE clause.
    """
    lhs = exp.Column(this=exp.Identifier(this=column, quoted=False))

    if isinstance(value, bool):
        rhs: exp.Expression = exp.Boolean(this=value)
    elif isinstance(value, (int, float)):
        rhs = exp.Literal.number(value)
    else:
        rhs = exp.Literal.string(str(value))

    return exp.EQ(this=lhs, expression=rhs)


def _collect_predicates(node: exp.Expression | None, dialect: str) -> list[str]:
    """Walk an AND-tree and collect individual predicate strings.

    Parameters
    ----------
    node:
        The WHERE condition node (may be a nested AND tree).
    dialect:
        sqlglot dialect for SQL generation.

    Returns
    -------
    list[str]
        Each leaf predicate as a SQL string.
    """
    if node is None:
        return []

    def _walk(n: exp.Expression) -> Iterator[str]:
        if isinstance(n, exp.And):
            yield from _walk(n.left)
            yield from _walk(n.right)
        else:
            yield n.sql(dialect=dialect)

    return list(_walk(node))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def plan(
    sql: str,
    claims: dict[str, Any] | None = None,
    projection: list[str] | None = None,
    dialect: str = _DEFAULT_DIALECT,
    params: list[Any] | None = None,
    limit: int | None = None,
    predicates: list[Any] | None = None,
) -> PhysicalPlan:
    """Compile a logical SQL query and optional claims into a ``PhysicalPlan``.

    Parameters
    ----------
    sql:
        The logical SQL query string (SELECT only — anything else raises
        ``AppError("UNSUPPORTED_QUERY", 400)``).
    claims:
        JWT / auth claims dict.  Only ``claims["policies"]`` is used for RLS
        predicate injection; all other claims are ignored by the planner (they
        may be checked by the auth layer before planning).
    projection:
        If given, the SELECT list is replaced with exactly these column names.
        Useful for push-down projection to reduce bytes over the wire.
    dialect:
        sqlglot dialect for parsing and generation.  Default ``"postgres"``.
    params:
        Positional query parameters (passed through unchanged into the plan).
        Default ``[]``.
    limit:
        Optional row limit to push down into the query.  If the SQL already
        contains a LIMIT that is smaller than *limit*, the existing (smaller)
        LIMIT is kept.  ``None`` means no limit is pushed (M1-compatible path).
    predicates:
        Optional list of extra predicates to push into WHERE before RLS injection.
        Each element may be a ``(col, op, value)`` tuple, a single-key ``{col:
        value}`` dict, or a raw SQL string (trusted; not user-supplied).
        ``None`` means no extra predicates are pushed (M1-compatible path).

    Returns
    -------
    PhysicalPlan
        A frozen, JSON-serialisable plan ready for the executor.

    Raises
    ------
    app.errors.AppError
        ``code="UNSUPPORTED_QUERY"`` (status 400) if the SQL is not a SELECT
        statement.
        ``code="INVALID_SQL"`` (status 400) if sqlglot cannot parse the SQL.
    """
    if params is None:
        params = []
    if claims is None:
        claims = {}

    # ── 1. Parse ────────────────────────────────────────────────────────────
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except sqlglot.errors.SqlglotError as exc:
        raise AppError("INVALID_SQL", f"Failed to parse SQL: {exc}", status=400) from exc

    # ── 2. Reject non-SELECT ─────────────────────────────────────────────────
    if not isinstance(tree, exp.Select):
        raise AppError(
            "UNSUPPORTED_QUERY",
            "Only SELECT statements are supported by the Nubi planner.",
            status=400,
        )

    # ── 3. Apply projection (via optimizer) ─────────────────────────────────
    if projection:
        tree = prune_projection(tree, projection)

    # ── 4. Push extra caller-supplied predicates (M2-A; no-op when None) ────
    if predicates:
        tree = push_predicates(tree, predicates)

    # ── 5. Inject RLS predicates (AST-level, NEVER string-concat) ───────────
    policies: dict[str, Any] = {}
    raw_policies = claims.get("policies", {})
    if isinstance(raw_policies, dict):
        policies = raw_policies

    injected_predicates: list[str] = []
    for col_name, col_value in sorted(policies.items()):
        pred_node = _make_eq_predicate(col_name, col_value)
        tree = tree.where(pred_node)
        injected_predicates.append(pred_node.sql(dialect=dialect))

    # ── 6. Push LIMIT (M2-A; no-op when None) ───────────────────────────────
    if limit is not None:
        tree = push_limit(tree, limit)

    # ── 7. Collect all predicates (original + extra + injected) ─────────────
    where_node = tree.args.get("where")
    all_predicates = _collect_predicates(
        where_node.this if where_node else None, dialect=dialect
    )

    # ── 8. Re-generate SQL ───────────────────────────────────────────────────
    rewritten_sql: str = tree.sql(dialect=dialect)

    # ── 9. Compute cache key from the FINAL SQL + params + RLS claims ────────
    cache_key = compute_cache_key(
        sql=rewritten_sql,
        params=params,
        rls_claims=claims,
    )

    # ── 10. Build and return the plan ────────────────────────────────────────
    return PhysicalPlan(
        dialect=dialect,
        sql=rewritten_sql,
        params=params,
        projection=projection,
        predicates=all_predicates,
        rls_claims=claims,
        cache_key=cache_key,
    )


# ---------------------------------------------------------------------------
# M2-C: Rollup routing (opt-in; does NOT change plan() behaviour)
# ---------------------------------------------------------------------------


def route_to_rollup(plan: PhysicalPlan, registry: Any) -> PhysicalPlan:
    """Rewrite *plan* to query a registered rollup table when one is available.

    This function is opt-in and completely separate from ``plan()``.  Call it
    after ``plan()`` if you want rollup routing; do **not** call it if you want
    the original plan (e.g. in conformance tests).

    Algorithm
    ---------
    1. Compute the ``groupby_sig`` of ``plan.sql`` using the same normalisation
       helper as ``query_log.compute_groupby_sig`` (single source of truth).
    2. Look up the sig in *registry* (a ``RollupRegistry`` instance).
    3. If a rollup table is registered:
       a. Parse ``plan.sql`` with sqlglot.
       b. Find all ``From`` / ``Join`` table references and replace the primary
          table's name with the rollup table name via AST rewrite (no string
          manipulation).
       c. Re-generate SQL and recompute the cache key.
       d. Return a new ``PhysicalPlan`` with the rewritten SQL + new cache key
          (all other fields preserved).
    4. If no rollup is registered, return *plan* unchanged (same object,
       same ``cache_key``).

    Parameters
    ----------
    plan:
        The ``PhysicalPlan`` produced by ``plan()``.
    registry:
        A ``RollupRegistry`` instance (use ``preagg.get_registry()`` for the
        singleton or pass a fresh instance in tests).

    Returns
    -------
    PhysicalPlan
        Either the rewritten plan (rollup hit) or the original plan (no match).

    Notes
    -----
    - The original ``plan()`` is never touched; M1 conformance keys are
      unchanged because ``route_to_rollup`` is only called when a rollup is
      registered, which tests control explicitly.
    - RLS claims are preserved in the returned plan so downstream audit/logging
      is not affected.
    """
    sig = compute_groupby_sig(plan.sql, dialect=plan.dialect)
    if sig is None:
        return plan  # No GROUP BY — nothing to route.

    rollup_table = registry.lookup(sig)
    if rollup_table is None:
        return plan  # No registered rollup for this pattern.

    # ── Rewrite the FROM clause via sqlglot AST ──────────────────────────────
    try:
        tree = sqlglot.parse_one(plan.sql, dialect=plan.dialect)
    except Exception:
        return plan  # Parse failure — return original plan unchanged.

    if not isinstance(tree, exp.Select):
        return plan

    # Replace all Table nodes whose name matches the base_table in the sig.
    # The base_table is the first component of the sig (before the first "|").
    base_table = sig.split("|")[0].split(",")[0]  # primary table name (first)

    # Walk all From and Join table references and replace the primary table.
    _replaced = False
    for table_node in tree.find_all(exp.Table):
        if table_node.name.lower() == base_table.lower():
            # Replace in-place on the AST node.
            table_node.set("this", exp.Identifier(this=rollup_table, quoted=False))
            _replaced = True
            break  # Only replace the first (primary) table occurrence.

    if not _replaced:
        return plan  # Could not find the table to replace.

    rewritten_sql = tree.sql(dialect=plan.dialect)

    new_cache_key = compute_cache_key(
        sql=rewritten_sql,
        params=plan.params,
        rls_claims=plan.rls_claims,
    )

    return PhysicalPlan(
        dialect=plan.dialect,
        sql=rewritten_sql,
        params=list(plan.params),
        projection=plan.projection,
        predicates=list(plan.predicates),
        rls_claims=dict(plan.rls_claims),
        cache_key=new_cache_key,
    )
