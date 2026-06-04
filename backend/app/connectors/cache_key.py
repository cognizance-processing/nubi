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
