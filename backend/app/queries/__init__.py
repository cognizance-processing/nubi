"""Registered-query allowlist for Nubi (M3-SEC).

Embed tokens may only execute queries from this registry — arbitrary SQL is
blocked at the route layer.  First-party (kind='access') tokens keep full
raw-SQL access, but may also reference a registered query by id.

Public API
----------
RegisteredQuery
    Dataclass representing a server-registered query.

QueryRegistry
    Thread-safe registry with register / get / all helpers.

get_query_registry() -> QueryRegistry
    Module-level singleton.
"""

from app.queries.registry import QueryRegistry, RegisteredQuery, get_query_registry

__all__ = ["QueryRegistry", "RegisteredQuery", "get_query_registry"]
