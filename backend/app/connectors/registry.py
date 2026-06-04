"""Connector registry — maps source-type strings to connector factories.

The registry is the single place where connector types are declared.  Route
handlers and the planner call ``get_connector_registry()`` to obtain the
module-level singleton and then call ``get(type)`` to retrieve a factory.

Design notes
------------
- Factories are **callables**, not pre-instantiated connectors.  This keeps the
  registry lazy: no connection is opened until ``factory(config/dsn)`` is called.
- ``register`` / ``get`` / ``all`` are the only public operations; the registry
  does not orchestrate connection pools or lifecycle.
- Pre-registered types:
    - ``'postgres'`` → ``PostgresConnector`` (takes ``dsn: str``)
    - ``'duckdb'`` → ``DuckDBConnector`` (takes ``connection=None`` keyword arg)
    - ``'http_json'`` → ``HttpJsonConnector`` (takes ``config: dict``)
"""

from __future__ import annotations

from typing import Any, Callable

from app.errors import AppError


class ConnectorRegistry:
    """Registry mapping connector type strings to factory callables.

    Usage
    -----
    ::

        registry = get_connector_registry()

        # Register a custom connector factory
        registry.register("my_source", MyConnector)

        # Retrieve the factory and instantiate
        factory = registry.get("my_source")
        connector = factory(config)

        # Inspect all registered types
        all_types = registry.all()  # {"postgres": ..., "duckdb": ..., "my_source": ...}
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Any]] = {}

    def register(self, type_: str, factory: Callable[..., Any]) -> None:
        """Register a connector factory under *type_*.

        Parameters
        ----------
        type_:
            A stable, lowercase identifier for the connector type
            (e.g. ``"postgres"``, ``"duckdb"``, ``"http_json"``).
        factory:
            Any callable that returns a ``Connector`` instance when invoked
            with connector-specific configuration arguments.  This is
            typically the connector class itself (used as a constructor) or a
            wrapper function.

        Notes
        -----
        Registering the same *type_* twice overwrites the previous factory.
        This is intentional so that test suites can override the production
        factory with a mock.
        """
        self._factories[type_] = factory

    def get(self, type_: str) -> Callable[..., Any]:
        """Return the factory for *type_*.

        Parameters
        ----------
        type_:
            The connector type string previously passed to ``register``.

        Returns
        -------
        callable
            The factory callable registered under *type_*.

        Raises
        ------
        app.errors.AppError
            ``code="unknown_connector"`` (404) if *type_* has not been
            registered.
        """
        try:
            return self._factories[type_]
        except KeyError:
            raise AppError(
                "unknown_connector",
                f"No connector registered for type '{type_}'. "
                f"Registered types: {sorted(self._factories)}",
                status=404,
            )

    def all(self) -> dict[str, Callable[..., Any]]:
        """Return a shallow copy of the full registry mapping.

        Returns
        -------
        dict[str, callable]
            A ``{type_string: factory}`` dict.  Mutations to the returned
            dict do not affect the registry.
        """
        return dict(self._factories)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: ConnectorRegistry | None = None


def get_connector_registry() -> ConnectorRegistry:
    """Return the module-level ``ConnectorRegistry`` singleton.

    The registry is created on first call and pre-populated with the built-in
    connector factories:

    ``'postgres'``
        ``PostgresConnector`` — takes a ``dsn: str`` positional argument.
    ``'duckdb'``
        ``DuckDBConnector`` — takes an optional ``connection`` keyword argument.

    Returns
    -------
    ConnectorRegistry
        The singleton instance.
    """
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
        _bootstrap(_registry)
    return _registry


def reset_for_tests() -> None:
    """Restore the built-in bootstrap connectors to their canonical factories.

    Re-runs ``_bootstrap`` on the existing singleton without clearing extra
    entries, so module-level test registrations (e.g. a synthetic
    ``_UnsecurableConnector``) remain available.  If a test overrode a
    built-in factory (postgres/duckdb/http_json) the bootstrap call restores
    it to the canonical implementation.  This is intentionally a test-only
    helper — production code should never call it.
    """
    global _registry
    if _registry is None:
        _registry = ConnectorRegistry()
    _bootstrap(_registry)


def _bootstrap(registry: ConnectorRegistry) -> None:
    """Pre-register the built-in connector factories.

    Factories are registered lazily (as class references, not instances) so
    that no connections are opened at import time.  The ADBC wheels for
    ``PostgresConnector`` are imported inside the connector itself, so the
    registry can be imported even in pure-DuckDB environments.

    Registered types
    ----------------
    ``'postgres'``
        ``PostgresConnector`` — ADBC-backed, native Arrow, full push-down + RLS.
    ``'duckdb'``
        ``DuckDBConnector`` — local DuckDB, used for fixtures and conformance.
    ``'http_json'``
        ``HttpJsonConnector`` — REST/JSON API source; post-fetch RLS via
        ``apply_rls_postfetch`` (fail-closed).  No predicate push-down.
    """
    from app.connectors.duckdb_conn import DuckDBConnector
    from app.connectors.http_json import HttpJsonConnector
    from app.connectors.postgres import PostgresConnector

    registry.register("postgres", PostgresConnector)
    registry.register("duckdb", DuckDBConnector)
    registry.register("http_json", lambda config: HttpJsonConnector(config))
