"""RLS hierarchical scope expansion — E.2.

HierarchyResolver
-----------------
Resolves a coarse parent grant (e.g. region="Western Cape") to the list of
fine-grained child values (e.g. store_ids [10, 11, 12]) that are members of
that parent scope.

The resolution reads from the ``access_hierarchy`` table, which is:
  - org-scoped (every query filters on org_id — cross-org leakage is impossible)
  - populated by trusted platform / admin tooling (NOT by user request input)

Security contract
-----------------
* ``resolve()`` always filters by org_id; passing a different org_id is the
  caller's responsibility.  A caller MUST take org_id from the verified token,
  NEVER from the request body.
* If a dimension has no children registered, the original scalar value is
  returned unchanged (non-hierarchical dimensions pass through transparently).
* Resolution output is used to build IN-list predicates at AST level (see
  planner.py ``_make_in_predicate``).

Thread safety
-------------
HierarchyResolver is stateless; ``resolve()`` is safe to call concurrently.
The in-process cache (``InMemoryHierarchyResolver``) is protected by a lock.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class HierarchyResolver(ABC):
    """Abstract resolver: (org_id, dimension, parent_value) → list[child_value].

    Concrete implementations:
    - ``DbHierarchyResolver`` — asyncpg pool against the real DB.
    - ``InMemoryHierarchyResolver`` — dict-backed; for tests.
    - ``NullHierarchyResolver`` — always returns empty (no expansion).
    """

    @abstractmethod
    async def resolve(
        self,
        org_id: str,
        dimension: str,
        parent_value: str,
    ) -> list[str]:
        """Return child values for *(org_id, dimension, parent_value)*.

        Parameters
        ----------
        org_id:
            The organisation whose hierarchy to query.  MUST come from the
            verified token — never from request input.
        dimension:
            The column name being expanded (e.g. ``"region"``).
        parent_value:
            The coarse value to expand (e.g. ``"Western Cape"``).

        Returns
        -------
        list[str]
            Child values that are members of *parent_value* within *org_id*'s
            hierarchy for *dimension*.  Empty list if no children are registered
            (caller should treat original scalar value as-is).
        """

    async def expand_policy(
        self,
        org_id: str,
        dimension: str,
        scalar_value: Any,
    ) -> Any:
        """Expand a scalar policy value if the dimension is hierarchical.

        If *(org_id, dimension, str(scalar_value))* has registered children,
        returns a list of those children (triggering an IN predicate in the
        planner).  Otherwise returns *scalar_value* unchanged (equality
        predicate — backward-compatible).

        This is the primary entry-point used by the planner's RLS injection.

        Security: the expansion source is trusted DB data (org-scoped),
        NOT derived from request input.
        """
        children = await self.resolve(org_id, dimension, str(scalar_value))
        if children:
            return children  # Caller will produce: col IN (children...)
        return scalar_value  # No expansion — keep original scalar.


# ---------------------------------------------------------------------------
# Null implementation (no expansion — used when hierarchy feature is off)
# ---------------------------------------------------------------------------


class NullHierarchyResolver(HierarchyResolver):
    """No-op resolver that never expands any value.

    Used when the hierarchy feature is disabled or org has no hierarchy rows.
    All policy values pass through unchanged (scalar equality).
    """

    async def resolve(
        self,
        org_id: str,
        dimension: str,
        parent_value: str,
    ) -> list[str]:
        return []


# ---------------------------------------------------------------------------
# In-memory implementation (for tests and development)
# ---------------------------------------------------------------------------


class InMemoryHierarchyResolver(HierarchyResolver):
    """Dict-backed resolver for tests.

    Seed with ``add(org_id, dimension, parent_value, child_values)``.
    Thread-safe (asyncio.Lock on writes; reads are also guarded).

    Security properties are identical to the DB resolver:
    - org_id scoping is enforced at the dict key level.
    - Cross-org lookups are impossible by construction (keys are
      (org_id, dimension, parent_value) triples).
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, str], list[str]] = {}
        self._lock = asyncio.Lock()

    async def add(
        self,
        org_id: str,
        dimension: str,
        parent_value: str,
        child_values: list[str],
    ) -> None:
        """Register child_values for (org_id, dimension, parent_value)."""
        async with self._lock:
            key = (org_id, dimension, parent_value)
            self._data[key] = list(child_values)

    def add_sync(
        self,
        org_id: str,
        dimension: str,
        parent_value: str,
        child_values: list[str],
    ) -> None:
        """Synchronous seed helper (for use outside an event loop, e.g. test setup)."""
        key = (org_id, dimension, parent_value)
        self._data[key] = list(child_values)

    async def resolve(
        self,
        org_id: str,
        dimension: str,
        parent_value: str,
    ) -> list[str]:
        """Return children for (org_id, dimension, parent_value), or []."""
        key = (org_id, dimension, parent_value)
        return list(self._data.get(key, []))

    def clear(self) -> None:
        """Reset all data (for test teardown)."""
        self._data.clear()


# ---------------------------------------------------------------------------
# DB implementation (asyncpg)
# ---------------------------------------------------------------------------


class DbHierarchyResolver(HierarchyResolver):
    """Asyncpg-backed resolver reading from the ``access_hierarchy`` table.

    Security
    --------
    Every query filters on org_id (parameterised — never interpolated).
    Cross-org data cannot be accessed regardless of the inputs.

    Parameters
    ----------
    pool:
        An asyncpg connection pool.  The resolver acquires a connection per
        ``resolve()`` call and releases it immediately.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def resolve(
        self,
        org_id: str,
        dimension: str,
        parent_value: str,
    ) -> list[str]:
        """Query access_hierarchy for children of (org_id, dimension, parent_value).

        Uses parameterised query — org_id, dimension, parent_value are NEVER
        string-concatenated into the SQL.
        """
        rows = await self._pool.fetch(
            """
            SELECT child_value
            FROM access_hierarchy
            WHERE org_id = $1
              AND dimension = $2
              AND parent_value = $3
            ORDER BY child_value
            """,
            org_id,
            dimension,
            parent_value,
        )
        return [row["child_value"] for row in rows]


# ---------------------------------------------------------------------------
# Module-level singleton (swappable in tests)
# ---------------------------------------------------------------------------

_resolver: HierarchyResolver = NullHierarchyResolver()


def get_hierarchy_resolver() -> HierarchyResolver:
    """Return the active HierarchyResolver (default: NullHierarchyResolver)."""
    return _resolver


def set_hierarchy_resolver(resolver: HierarchyResolver) -> None:
    """Replace the active resolver (for tests or app startup)."""
    global _resolver
    _resolver = resolver


def reset_for_tests() -> None:
    """Reset the resolver to NullHierarchyResolver (called by test conftest)."""
    global _resolver
    _resolver = NullHierarchyResolver()
