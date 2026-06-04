"""Query registry — server-side allowlist of registered queries (M3-SEC).

Only queries present in this registry may be executed by embed-kind identities.
First-party (kind='access') identities may still run arbitrary SELECT SQL.

Design
------
- ``RegisteredQuery`` is an immutable dataclass: id, sql, name, and an optional
  ``required_scope``.  When ``required_scope`` is set the caller must carry that
  scope (or a wildcard that covers it) in addition to the base read scope gate.
- ``QueryRegistry`` is a plain dict wrapper with register / unregister / get /
  all operations.  It is NOT thread-safe for concurrent writes (registration
  happens at module import time, which is single-threaded in CPython).
- ``get_query_registry()`` returns the module-level singleton.  Seed queries are
  registered at import time so they are available from the first request.

Demo seed queries
-----------------
id="demo_all"
    ``SELECT * FROM demo``  — returns all demo rows; no extra scope required.
id="demo_active"
    ``SELECT * FROM demo WHERE active = true``  — only active rows; no extra
    scope required beyond the read gate.

Point-cloud seed queries (M5-B)
--------------------------------
Synthetic point-cloud data generated via DuckDB ``generate_series`` + ``random()``.
No seed table is required — ``generate_series`` is a DuckDB built-in.

Columns: id (int), x (double), y (double), category (int 0–4).

id="demo_points_10k"   — 10 000 points (fast preview / unit test target)
id="demo_points_100k"  — 100 000 points (standard GPU scatter demo)
id="demo_points_500k"  — 500 000 points (stress test / large-data demo)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisteredQuery:
    """A server-registered, immutable query.

    Attributes
    ----------
    id:
        Stable, URL-safe identifier (e.g. ``"demo_all"``).  Clients reference
        queries by this id; the server resolves it to the canonical SQL.
    sql:
        The canonical SELECT SQL that the server will execute.  This is the
        ONLY SQL that will run for a given id — any ``sql`` field in the request
        body is completely ignored for embed-kind callers.
    name:
        Human-readable label (for introspection / admin UIs).
    required_scope:
        Optional additional scope that the caller must carry beyond the base
        read gate.  ``None`` means no extra scope is required.  When set the
        route handler calls ``has_scope(identity.scope, required_scope)`` before
        executing.  Example: ``"read:query:demo_active"`` could restrict this
        query to tokens explicitly granted that scope.
    """

    id: str
    sql: str
    name: str
    required_scope: str | None = None


class QueryRegistry:
    """Registry of server-registered queries (the embed-token allowlist).

    Usage
    -----
    ::

        registry = get_query_registry()
        registry.register("my_query", "SELECT id FROM users", "User IDs")
        rq = registry.get("my_query")  # -> RegisteredQuery | None
        all_queries = registry.all()   # -> list[RegisteredQuery]
    """

    def __init__(self) -> None:
        self._store: dict[str, RegisteredQuery] = {}

    def register(
        self,
        id: str,
        sql: str,
        name: str,
        required_scope: str | None = None,
    ) -> RegisteredQuery:
        """Register a query and return the ``RegisteredQuery`` object.

        Overwrites any existing registration with the same *id* — this is
        intentional so that seed queries can be refreshed at startup.

        Parameters
        ----------
        id:
            Stable, URL-safe identifier.
        sql:
            The canonical SELECT SQL string.
        name:
            Human-readable label.
        required_scope:
            Optional additional scope required beyond the base read gate.

        Returns
        -------
        RegisteredQuery
            The newly registered query object.
        """
        rq = RegisteredQuery(id=id, sql=sql, name=name, required_scope=required_scope)
        self._store[id] = rq
        return rq

    def get(self, id: str) -> RegisteredQuery | None:
        """Return the ``RegisteredQuery`` for *id*, or ``None`` if not found."""
        return self._store.get(id)

    def all(self) -> list[RegisteredQuery]:
        """Return all registered queries as a list (insertion order)."""
        return list(self._store.values())

    def unregister(self, id: str) -> None:
        """Remove a query from the registry (mainly useful in tests)."""
        self._store.pop(id, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: QueryRegistry | None = None


def reset_for_tests() -> None:
    """Reset the query registry singleton to its default seeded state.

    Clears any test-registered queries and re-seeds the built-in demo queries
    so every test starts from a known baseline.  This is intentionally a
    test-only helper — production code should never call it.
    """
    global _registry
    _registry = None
    get_query_registry()


def get_query_registry() -> QueryRegistry:
    """Return (or create) the module-level ``QueryRegistry`` singleton.

    The registry is seeded with demo queries on first call so that the embed
    flow works out of the box with no external configuration.

    Seed queries
    ------------
    ``demo_all``
        ``SELECT * FROM demo`` — all demo rows.
    ``demo_active``
        ``SELECT * FROM demo WHERE active = true`` — active rows only.

    Point-cloud queries (M5-B)
    --------------------------
    ``demo_points_10k``
        10 000 synthetic points (id, x, y, category).
    ``demo_points_100k``
        100 000 synthetic points — standard GPU scatter demo size.
    ``demo_points_500k``
        500 000 synthetic points — large-data / stress demo.
    """
    global _registry
    if _registry is None:
        _registry = QueryRegistry()
        # ── Seed demo queries ─────────────────────────────────────────────
        _registry.register(
            id="demo_all",
            sql="SELECT * FROM demo",
            name="Demo — all rows",
            required_scope=None,
        )
        _registry.register(
            id="demo_active",
            sql="SELECT * FROM demo WHERE active = true",
            name="Demo — active rows only",
            required_scope=None,
        )
        # ── Seed point-cloud queries (M5-B) ──────────────────────────────
        # DuckDB generate_series is a built-in; no seed table is required.
        # These queries run on the demo DuckDB connector path in routes/query.py.
        _registry.register(
            id="demo_points_10k",
            sql=(
                "SELECT i AS id, random() AS x, random() AS y, (i % 5) AS category"
                " FROM generate_series(1, 10000) AS t(i)"
            ),
            name="Point cloud — 10 000 points",
            required_scope=None,
        )
        _registry.register(
            id="demo_points_100k",
            sql=(
                "SELECT i AS id, random() AS x, random() AS y, (i % 5) AS category"
                " FROM generate_series(1, 100000) AS t(i)"
            ),
            name="Point cloud — 100 000 points",
            required_scope=None,
        )
        _registry.register(
            id="demo_points_500k",
            sql=(
                "SELECT i AS id, random() AS x, random() AS y, (i % 5) AS category"
                " FROM generate_series(1, 500000) AS t(i)"
            ),
            name="Point cloud — 500 000 points",
            required_scope=None,
        )
    return _registry
