"""Issuer registry for embed JWT verification.

Maintains an in-memory map of ``iss`` (issuer string) → issuer configuration.
Configurations declare the JWKS endpoint, expected audience, allowed embed origins,
and optional static key material for tests (bypasses network fetches).

Public API
----------
IssuerConfig
    Dataclass holding the configuration for a single issuer.

IssuerRegistry
    In-memory registry.  ``register(iss, ...)`` to add an issuer; ``get(iss)``
    to retrieve it (returns ``None`` when the issuer is unknown).

get_issuer_registry() -> IssuerRegistry
    Return the process-level singleton registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IssuerConfig:
    """Configuration for a single JWT issuer.

    Attributes
    ----------
    iss:
        The issuer string that must appear in the ``iss`` claim of the token.
    jwks_uri:
        The URL of the JSON Web Key Set endpoint.  Used for live JWKS fetches
        when neither ``static_jwks`` nor ``static_public_key`` is provided.
    aud:
        The expected audience value.  Tokens whose ``aud`` does not match are
        rejected.
    allowed_origins:
        List of origins permitted to embed dashboards from this issuer.  An
        empty list means no origin restriction at the issuer level (the per-
        request ``embed_origin`` claim still applies).
    static_jwks:
        A pre-parsed JWKS dict (``{"keys": [...]}``).  When set, JWKS fetches
        are skipped entirely — useful for unit tests.
    static_public_key:
        A PEM-encoded RSA/EC public key string.  Converted to a minimal JWKS
        on first use.  Mutually exclusive with ``static_jwks``; ``static_jwks``
        takes priority when both are set.
    """

    iss: str
    jwks_uri: str
    aud: str
    allowed_origins: list[str] = field(default_factory=list)
    static_jwks: dict[str, Any] | None = None
    static_public_key: str | None = None


class IssuerRegistry:
    """Thread-safe (GIL-protected) in-memory issuer registry.

    For M3 the registry is seeded in-process (e.g. from config or DB).
    M4+ will swap this for a DB-backed implementation behind the same interface.
    """

    def __init__(self) -> None:
        self._issuers: dict[str, IssuerConfig] = {}

    def register(
        self,
        iss: str,
        *,
        jwks_uri: str,
        aud: str,
        allowed_origins: list[str] | None = None,
        static_jwks: dict[str, Any] | None = None,
        static_public_key: str | None = None,
    ) -> IssuerConfig:
        """Register (or overwrite) an issuer configuration.

        Parameters
        ----------
        iss:
            Issuer identifier string (must match the ``iss`` claim in tokens).
        jwks_uri:
            JWKS endpoint URL.
        aud:
            Expected audience value.
        allowed_origins:
            Origins allowed to embed.  Defaults to an empty list (no restriction).
        static_jwks:
            Pre-built JWKS dict for tests.  When provided, no network fetch occurs.
        static_public_key:
            PEM public key for tests.  Ignored when ``static_jwks`` is set.

        Returns
        -------
        IssuerConfig
            The newly created / updated configuration.
        """
        cfg = IssuerConfig(
            iss=iss,
            jwks_uri=jwks_uri,
            aud=aud,
            allowed_origins=allowed_origins or [],
            static_jwks=static_jwks,
            static_public_key=static_public_key,
        )
        self._issuers[iss] = cfg
        return cfg

    def get(self, iss: str) -> IssuerConfig | None:
        """Return the config for *iss*, or ``None`` if not registered."""
        return self._issuers.get(iss)

    def unregister(self, iss: str) -> None:
        """Remove an issuer from the registry (idempotent)."""
        self._issuers.pop(iss, None)

    def clear(self) -> None:
        """Remove all registered issuers (useful in test teardown)."""
        self._issuers.clear()

    def __len__(self) -> int:  # pragma: no cover
        return len(self._issuers)


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_registry: IssuerRegistry | None = None


def get_issuer_registry() -> IssuerRegistry:
    """Return the process-level singleton :class:`IssuerRegistry`.

    The singleton is created on first call and shared across all subsequent
    calls in the same process.  Tests should call :meth:`IssuerRegistry.clear`
    (or ``get_issuer_registry().clear()``) in teardown to isolate state.
    """
    global _registry
    if _registry is None:
        _registry = IssuerRegistry()
    return _registry
