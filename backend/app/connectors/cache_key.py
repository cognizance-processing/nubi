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

SECURITY — Exact-result cache MUST be org+datastore scoped at the call site
----------------------------------------------------------------------------
``compute_cache_key`` is intentionally a pure content-hash over
``(sql, params, rls_policies)``.  It does NOT incorporate ``org_id`` or
``datastore_id`` because those are routing concerns, not plan-content concerns.

HOWEVER, this means two tenants whose admins have set ``policies={}`` (no
row-level predicate) running the same SQL would produce an identical
``compute_cache_key`` digest — and one org could be served the other's cached
result bytes.  This is a cross-tenant data leak.

The fix is applied AT THE CALL SITE (``routes/query.py`` and
``routes/metrics.py``) using :func:`scope_cache_key`, which appends
``org_id`` and ``effective_datastore_id`` to the plan's content-hash before
hashing again.  All ``cache.get`` and ``cache.put`` invocations in those
routes MUST use the scoped key returned by this helper, never the raw
``physical_plan.cache_key`` directly.

Base-scan cache key (BET 2b) — DISABLED
-----------------------------------------
:func:`compute_base_scan_key` derives a COARSER key from
``(base_tables, WHERE_predicates_normalised, params, rls_policies)``
— stripping SELECT/GROUP BY/HAVING/ORDER BY.

**BASE-SCAN FUSION IS CURRENTLY DISABLED.**

The helper is defined (and its key derivation is sound for RLS isolation) but
the put/get integration in the query/metrics routes has been removed because the
original implementation was UNSOUND: it stored the **fully-aggregated** result of
one query (e.g. ``SELECT SUM(amount) GROUP BY category``) under the coarser key
and then returned those bytes verbatim to a DIFFERENT query that shared only the
base table and WHERE clause but had a different GROUP BY.  The second query
received the wrong schema and wrong values — data corruption across requests.

A correct implementation MUST cache the **pre-GROUP-BY raw scan** (the unaggregated
row stream), not the final aggregated result.  That requires splitting the execution
into a raw-scan phase and a per-query aggregation phase — a non-trivial engine
change that is out of scope here.

Until that raw-unaggregated-scan implementation ships, ``compute_base_scan_key``
returns a key that callers MAY store/retrieve independently, but the routes
(``routes/query.py`` and ``routes/metrics.py``) do NOT call ``put_base_scan`` or
act on a ``get_base_scan`` HIT.  The helpers remain available for tests and for
the future correct implementation.

SECURITY: the rls_hash component incorporates the full ``policies`` dict so
different tenants NEVER share a base-scan entry even for identical SQL shapes.
Base-scan keys MUST also be org+datastore scoped at the call site using
:func:`scope_cache_key` if base-scan fusion is ever re-enabled.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Bump this constant when the cache-key algorithm changes so callers can detect
# version skew.  The version is NOT hashed — the algorithm structure is the
# version.  Annotated here for operators and the conformance suite.
CACHE_KEY_VERSION: str = "1"

#: Sentinel used when no real datastore id is available (demo/no-datastore path).
_DEMO_DATASTORE_SENTINEL: str = "__demo__"


def scope_cache_key(
    plan_cache_key: str,
    org_id: str | None,
    effective_datastore_id: str | None,
) -> str:
    """Return an org+datastore-scoped cache key for exact-result cache access.

    SECURITY: ``compute_cache_key`` is a pure content-hash over
    ``(sql, params, rls_policies)``.  Two tenants whose admins have configured
    ``policies={}`` (no row-level predicate) running the same SQL will therefore
    produce the same ``plan_cache_key``.  Without additional scoping the exact-
    result cache would serve org A's Arrow bytes to org B — a cross-tenant data
    leak.

    This function appends ``org_id`` and ``effective_datastore_id`` to the plan
    key before re-hashing so that the result cache namespace is always both
    org- and datastore-scoped, regardless of whether RLS policies differ.

    Parameters
    ----------
    plan_cache_key:
        The SHA-256 hex digest returned by :func:`compute_cache_key` (or stored
        on ``PhysicalPlan.cache_key``).
    org_id:
        The verified org identifier from the identity token.  ``None`` is
        accepted (maps to the empty string) so the demo path keeps working.
    effective_datastore_id:
        The resolved datastore id for this request.  ``None`` maps to the
        ``__demo__`` sentinel so the demo path produces a stable, distinct key.

    Returns
    -------
    str
        64-character lowercase hex string (SHA-256 digest of the scoped input).
    """
    ds = effective_datastore_id if effective_datastore_id is not None else _DEMO_DATASTORE_SENTINEL
    org = org_id if org_id is not None else ""
    raw = f"{plan_cache_key}:{org}:{ds}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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

    SECURITY — PARTIAL ISOLATION ONLY via RLS policies:
    ``rls_policies`` is fully incorporated so tenants whose admins have
    configured *different* row-level policies will produce different keys.
    However, two tenants whose admins have configured ``policies={}`` (no
    predicates) running the same SQL WILL produce the SAME raw base-scan key.
    This mirrors the same risk as ``compute_cache_key`` (see module docstring).

    REQUIRED: callers MUST wrap the returned key with :func:`scope_cache_key`
    (passing ``org_id`` and ``effective_datastore_id``) before using it as a
    cache store/fetch key.  The live usage in ``board_data._provider_cache_key``
    satisfies this by embedding ``org_id`` directly in the key string.  The
    route helpers (``routes/query.py``, ``routes/metrics.py``) compute this key
    for future use but do NOT call ``get_base_scan``/``put_base_scan`` yet
    (base-scan fusion is disabled — see module-level docstring).

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
