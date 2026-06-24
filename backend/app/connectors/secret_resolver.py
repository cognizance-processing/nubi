"""Pluggable secret resolver for claim-templated datastores.

When a datastore uses ``template_config`` to resolve connection fields at
request time, credentials must likewise be resolved per-tenant rather than
stored once per datastore row.  This module provides the resolver abstraction.

Design
------
``SecretResolver`` is an abstract base class with one async method::

    async def resolve(datastore_id: str, claims: dict) -> dict

Concrete implementations:

``EncryptedStoreResolver`` (default)
    Wraps the existing :class:`~app.connectors.secret_store.SecretStore`
    behaviour — looks up the encrypted secret by ``datastore_id`` and
    ``org_id`` (extracted from ``claims``).  This is exactly what
    ``_build_connector_for_plan`` does today, so existing connectors are
    unaffected.

``ExternalSecretResolver``
    Takes an arbitrary async or sync callable at construction time.  Useful
    for injecting secrets from an external vault (HashiCorp Vault, AWS
    Secrets Manager, GCP Secret Manager, …) in production and for
    test doubles.

Factory
-------
``get_resolver(datastore_config: dict) -> SecretResolver``
    Reads the optional ``secret_resolver`` key from the datastore config and
    returns the matching implementation.

    ``"encrypted_store"`` (or absent) -> :class:`EncryptedStoreResolver`
    ``"external"``                     -> raises ``RuntimeError`` unless an
                                           ``_external_resolver_factory`` is
                                           registered (tests / production wiring
                                           must call ``set_external_resolver``).

Security contract
-----------------
- Resolver failure ALWAYS propagates — there is no fallback to a default
  credential set.  Wrong-tenant credential silencing is a security failure.
- ``EncryptedStoreResolver`` preserves the existing org-scope enforcement of
  ``SecretStore.get`` (cross-org access returns ``None``, not a secret).
- ``ExternalSecretResolver`` propagates any exception from the callable;
  callers must not swallow it.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class SecretResolver(ABC):
    """Abstract base class for per-tenant credential resolution.

    Implementations must override :meth:`resolve`.  They MUST NOT swallow
    exceptions — any failure must propagate so the caller can fail closed.
    """

    @abstractmethod
    async def resolve(self, datastore_id: str, claims: dict[str, Any]) -> dict[str, Any]:
        """Return the credential dict for the given datastore and claims.

        Parameters
        ----------
        datastore_id:
            UUID string of the datastore row (used as a lookup key in the
            encrypted store or forwarded to an external vault).
        claims:
            Verified JWT claims for the current request.  Implementations may
            use ``claims["org"]``, ``claims["sub"]``, etc. to scope the lookup.

        Returns
        -------
        dict
            Credential dict suitable for merging into the connector config.
            May be an empty dict for secret-less connectors; must NOT be
            ``None``.

        Raises
        ------
        Any exception
            Resolver failure must propagate — callers must not provide a
            fallback so that wrong-tenant credentials are never silently used.
        """


# ---------------------------------------------------------------------------
# EncryptedStoreResolver — wraps existing SecretStore
# ---------------------------------------------------------------------------


class EncryptedStoreResolver(SecretResolver):
    """Default resolver: looks up the encrypted secret by ``datastore_id``.

    Preserves exact parity with the legacy ``SecretStore.get`` call in
    ``_build_connector_for_plan``: secrets are scoped by ``org_id`` (read
    from ``claims["org"]`` for embed/host-mode tokens, or an explicit
    ``org_id`` kwarg).

    Parameters
    ----------
    org_id:
        Explicit org UUID string.  When supplied it overrides the ``org``
        claim for every ``resolve`` call.  Intended for non-embed callers
        (first-party access tokens) where the org is resolved by the route
        handler before constructing the resolver.
    """

    def __init__(self, org_id: str | None = None) -> None:
        self._org_id = org_id

    async def resolve(self, datastore_id: str, claims: dict[str, Any]) -> dict[str, Any]:
        """Fetch the encrypted secret from the store, scoped to org_id.

        Uses ``claims["org"]`` if no explicit *org_id* was supplied at
        construction time.  Raises ``ValueError`` if neither is available so
        the call fails closed rather than returning empty creds.
        """
        from app.connectors.secret_store import get_secret_store  # local import

        org_id = self._org_id or str(claims.get("org") or "")
        if not org_id:
            raise ValueError(
                "EncryptedStoreResolver: no org_id available (not in claims "
                "and not set at construction time)."
            )

        store = get_secret_store()
        secret = await store.get(datastore_id, org_id)
        # ``None`` means no secret stored — return empty dict (secret-less connector).
        # This mirrors the existing behaviour in _build_connector_for_plan.
        return secret or {}


# ---------------------------------------------------------------------------
# ExternalSecretResolver — callable-backed (vault, test doubles)
# ---------------------------------------------------------------------------


class ExternalSecretResolver(SecretResolver):
    """Resolver backed by an arbitrary callable (vault client, test double).

    The callable receives ``(datastore_id, claims)`` and must return a
    ``dict`` of credentials.  It may be async or sync.

    Parameters
    ----------
    callable_:
        A function ``(datastore_id: str, claims: dict) -> dict`` (sync) or
        ``async (datastore_id: str, claims: dict) -> dict`` (async) that
        produces the credential dict.  Any exception from the callable is
        propagated — the resolver never swallows exceptions.
    """

    def __init__(
        self,
        callable_: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        self._callable = callable_

    async def resolve(self, datastore_id: str, claims: dict[str, Any]) -> dict[str, Any]:
        """Call the vault callable and return its credential dict.

        Propagates any exception from the callable without wrapping —
        the caller must handle failure by aborting the request.
        """
        result = self._callable(datastore_id, claims)
        # Support both async and sync callables.
        if asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, dict):
            raise TypeError(
                f"ExternalSecretResolver: callable returned {type(result)!r}, "
                "expected dict."
            )
        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Optional module-level factory for "external" resolver wiring.
# Set by production/test code via set_external_resolver_factory().
_external_resolver_factory: Callable[..., ExternalSecretResolver] | None = None


def set_external_resolver_factory(
    factory: Callable[..., ExternalSecretResolver] | None,
) -> None:
    """Register (or clear) the factory used when ``secret_resolver = "external"``.

    Call this at application startup when wiring an external vault client.
    Tests may call it to inject a :class:`ExternalSecretResolver` double.
    Pass ``None`` to remove the factory (reset to default for tests).
    """
    global _external_resolver_factory
    _external_resolver_factory = factory


def get_resolver(
    datastore_config: dict[str, Any],
    org_id: str | None = None,
) -> SecretResolver:
    """Return the appropriate :class:`SecretResolver` for *datastore_config*.

    Parameters
    ----------
    datastore_config:
        The datastore ``config`` dict (from the ``datastores`` table).  The
        optional ``secret_resolver`` key selects the implementation:

        ``"encrypted_store"`` or absent
            Returns :class:`EncryptedStoreResolver` (default, preserves
            existing behaviour).
        ``"external"``
            Returns the resolver produced by the registered external factory.
            Raises ``RuntimeError`` if no factory has been registered.

    org_id:
        Explicit org UUID forwarded to :class:`EncryptedStoreResolver` when
        the org is resolved by the route handler (first-party access tokens).
        Ignored for ``"external"`` resolvers (they receive full claims).

    Returns
    -------
    SecretResolver
        The resolver instance.

    Raises
    ------
    RuntimeError
        If ``secret_resolver = "external"`` is requested but no external
        factory has been registered.
    ValueError
        If an unknown ``secret_resolver`` value is specified.
    """
    resolver_kind: str = str(
        datastore_config.get("secret_resolver") or "encrypted_store"
    ).lower()

    if resolver_kind == "encrypted_store":
        return EncryptedStoreResolver(org_id=org_id)

    if resolver_kind == "external":
        if _external_resolver_factory is None:
            raise RuntimeError(
                "secret_resolver='external' requested but no external resolver "
                "factory has been registered.  Call "
                "secret_resolver.set_external_resolver_factory() at startup."
            )
        return _external_resolver_factory()

    raise ValueError(
        f"Unknown secret_resolver value: {resolver_kind!r}.  "
        "Supported values: 'encrypted_store', 'external'."
    )
