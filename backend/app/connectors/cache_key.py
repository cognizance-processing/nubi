"""Cache-key derivation for physical query plans.

Algorithm (frozen — see docs/cache-key-spec.md for the full spec and test vectors)
----------------------------------------------------------------------------------
1. Build a dict ``{"sql": sql, "params": params, "rls": rls_subset}`` where
   ``rls_subset`` contains only the RLS-affecting claims (from
   ``claims.get("policies", {})``) with keys sorted lexicographically.
2. Serialise to **canonical JSON**: ``json.dumps(..., sort_keys=True,
   separators=(',', ':'))`` — sorted keys, no whitespace.
3. Encode the JSON string to **UTF-8**.
4. Return the **SHA-256 hex digest** of those bytes.

A future Rust executor MUST reproduce byte-identical keys given the same inputs.
The test vectors in ``docs/cache-key-spec.md`` are the conformance contract.

CACHE_KEY_VERSION is embedded for future algorithm migrations; it is NOT part of
the hash input (the algorithm itself encodes the version implicitly via the dict
structure).  Bump it if the algorithm changes; old keys are then invalid.

Base-scan cache key (BET 2b)
----------------------------
:func:`compute_base_scan_key` derives a COARSER key from
``(base_tables, WHERE_predicates_normalised, params, rls_policies)``
— stripping SELECT/GROUP BY/HAVING/ORDER BY so that two widget queries
that scan the *same* model with the *same* filter predicate and the *same*
RLS tenant share one base-scan cache entry.

SECURITY: the rls_hash component incorporates the full ``policies`` dict so
different tenants NEVER share a base-scan entry even for identical SQL shapes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Bump this constant when the cache-key algorithm changes so callers can detect
# version skew.  The version is NOT hashed — the algorithm structure is the
# version.  Annotated here for operators and the conformance suite.
CACHE_KEY_VERSION: str = "1"


def compute_cache_key(
    sql: str,
    params: list[Any],
    rls_claims: dict[str, Any],
) -> str:
    """Return the SHA-256 hex cache key for a physical plan.

    Parameters
    ----------
    sql:
        The rewritten SQL string (after projection / predicate injection).
    params:
        Ordered list of positional query parameters.  Empty list if none.
    rls_claims:
        The RLS claims dict as passed to the planner.  Only the ``"policies"``
        sub-dict is considered (the RLS-affecting subset); all other JWT claims
        (e.g. ``"sub"``, ``"exp"``) are excluded so that key expiry differences
        do not cause cache misses for identical data access patterns.

    Returns
    -------
    str
        64-character lowercase hex string (SHA-256 digest).

    Notes
    -----
    The canonical JSON is produced with ``sort_keys=True`` and
    ``separators=(',', ':')`` (no whitespace) so that key insertion order and
    pretty-printing flags never affect the result.

    The ``rls`` sub-dict keys are sorted independently of ``sort_keys`` to make
    the contract explicit and portable to languages without a ``sort_keys``
    equivalent.
    """
    # Extract only the RLS-affecting claims (the "policies" sub-dict).
    # This is the same subset the predicate injector consumes — one source of truth.
    policies: dict[str, Any] = {}
    if rls_claims:
        raw_policies = rls_claims.get("policies", {})
        if isinstance(raw_policies, dict):
            # Sort by key so order-independent callers get the same result.
            policies = dict(sorted(raw_policies.items()))

    canonical: dict[str, Any] = {
        "sql": sql,
        "params": params,
        "rls": policies,
    }

    # Canonical JSON: sorted keys, no whitespace, UTF-8.
    canonical_json: str = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest: str = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return digest


def compute_base_scan_key(
    sql: str,
    params: list[Any],
    rls_claims: dict[str, Any],
) -> str | None:
    """Return a stable base-scan cache key for shared-scan fusion (BET 2b).

    This key is COARSER than :func:`compute_cache_key`: it captures only the
    ``(base_tables, WHERE_predicates, params, rls_policies)`` triple — the
    SELECT/GROUP BY/HAVING/ORDER BY are stripped.  Two widget queries that
    differ only in their selected columns / aggregations but share the same
    model + predicate + tenant will therefore hash to the same base-scan key,
    allowing the engine to share a single base-scan result between them.

    SECURITY: the ``rls_policies`` dict is fully incorporated so different
    tenants ALWAYS produce different keys — tenants cannot share a base-scan
    entry even for structurally identical queries.

    Returns ``None`` when *sql* cannot be parsed (SQL parse is best-effort;
    callers treat ``None`` as "no base-scan sharing possible").  The full
    ``cache_key`` (exact result) is always computed and used independently;
    this key is ADDITIVE — it never replaces the per-plan key.

    Parameters
    ----------
    sql:
        The RLS-rewritten SQL string from ``PhysicalPlan.sql``.
    params:
        Positional query parameters.
    rls_claims:
        The RLS claims dict; only ``policies`` is incorporated.

    Returns
    -------
    str | None
        64-character lowercase hex SHA-256 digest, or ``None`` on parse failure.
    """
    try:
        import sqlglot  # noqa: PLC0415
        import sqlglot.expressions as exp  # noqa: PLC0415

        tree = sqlglot.parse_one(sql, read="postgres", error_level=sqlglot.ErrorLevel.RAISE)
    except Exception:  # noqa: BLE001 — parse failure → no base-scan key
        return None

    if not isinstance(tree, exp.Select):
        return None

    # ── Collect base table names ─────────────────────────────────────────────
    tables: list[str] = sorted(
        {t.name.lower() for t in tree.find_all(exp.Table) if t.name}
    )
    if not tables:
        return None

    # ── Normalise the WHERE clause ───────────────────────────────────────────
    # We want a canonical, order-independent representation of the WHERE so
    # ``WHERE a=1 AND b=2`` and ``WHERE b=2 AND a=1`` hash the same.  We
    # extract all leaf predicate sub-expressions, sort them, and re-join.
    where_node = tree.args.get("where")
    where_parts: list[str] = []
    if where_node is not None:
        # Walk the WHERE tree; collect every leaf comparison / IS NULL / etc.
        for node in where_node.walk():
            if isinstance(node, (exp.EQ, exp.GT, exp.LT, exp.GTE, exp.LTE,
                                  exp.NEQ, exp.Is, exp.In, exp.Like)):
                where_parts.append(node.sql(dialect="postgres").lower().strip())
    where_sig = "|".join(sorted(set(where_parts)))

    # ── RLS policies (SECURITY — tenant isolation) ───────────────────────────
    policies: dict[str, Any] = {}
    if rls_claims:
        raw = rls_claims.get("policies", {})
        if isinstance(raw, dict):
            policies = dict(sorted(raw.items()))

    canonical: dict[str, Any] = {
        "tables": tables,
        "where": where_sig,
        "params": params,
        "rls": policies,
    }
    canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
