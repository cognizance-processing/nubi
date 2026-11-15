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
    host_mode:
        When ``True``, this issuer operates in "embedded host" tenancy mode.
        The caller's org is resolved from the claim named by ``org_claim``
        rather than via an ``org_members`` DB lookup.  Opt-in per issuer;
        defaults to ``False`` so existing issuers are unaffected.
    org_claim:
        The JWT claim name whose value is used as the caller's ``org_id`` when
        ``host_mode`` is ``True``.  ``None`` for non-host-mode issuers.
    """

    iss: str
    jwks_uri: str
    aud: str
    allowed_origins: list[str] = field(default_factory=list)
    static_jwks: dict[str, Any] | None = None
    static_public_key: str | None = None
    host_mode: bool = False
    org_claim: str | None = None
    # The organisation that owns this issuer. A tenant-managed issuer only
    # ever authenticates its own organisation. ``None`` marks an
    # operator-registered (process-level) issuer.
    org_id: str | None = None


class AmbiguousIssuer(Exception):
    """The same ``iss`` is configured by several orgs and the token names none."""


class IssuerRegistry:
    """Thread-safe (GIL-protected) in-memory issuer registry.

    For M3 the registry is seeded in-process (e.g. from config or DB).
    M4+ will swap this for a DB-backed implementation behind the same interface.
    """

    def __init__(self) -> None:
        # Keyed by (owning org id | None, iss) so two orgs may configure the
        # same issuer string without sharing keys or trust.
        self._issuers: dict[tuple[str | None, str], IssuerConfig] = {}

    def register(
        self,
        iss: str,
        *,
        jwks_uri: str,
        aud: str,
        allowed_origins: list[str] | None = None,
        static_jwks: dict[str, Any] | None = None,
        static_public_key: str | None = None,
        host_mode: bool = False,
        org_claim: str | None = None,
        org_id: str | None = None,
    ) -> IssuerConfig:
        """Register (or overwrite) an issuer configuration.

        ``org_id`` is the owning organisation; tenant-managed issuers always
        pass it. Operator-registered issuers leave it ``None``.

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
        host_mode:
            When ``True``, org is resolved from the JWT claim named by
            ``org_claim`` rather than via an ``org_members`` DB lookup.
        org_claim:
            The JWT claim name carrying the ``org_id`` when ``host_mode`` is
            ``True``.

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
            host_mode=host_mode,
            org_claim=org_claim,
            org_id=str(org_id) if org_id else None,
        )
        self._issuers[(cfg.org_id, iss)] = cfg
        return cfg

    def candidates(self, iss: str) -> list[IssuerConfig]:
        """Every configuration registered for *iss*, across owners."""
        return [cfg for (_org, i), cfg in self._issuers.items() if i == iss]

    def resolve(self, iss: str, org: str | None = None) -> IssuerConfig | None:
        """Pick the configuration that may authenticate a token.

        ``org`` is the (normalised) org the token names, if any. It only ever
        *selects among* configurations — the organisation a token acts for is
        the selected configuration's ``org_id``:

        * an org-owned configuration matches only its own org;
        * an operator-registered configuration matches any org;
        * with no org named, a single configuration is used, several are
          ambiguous (:class:`AmbiguousIssuer`).
        """
        cands = self.candidates(iss)
        if not cands:
            return None
        if org:
            for cfg in cands:
                if cfg.org_id == str(org):
                    return cfg
        for cfg in cands:
            if cfg.org_id is None:
                return cfg
        if org:
            return None
        if len(cands) == 1:
            return cands[0]
        raise AmbiguousIssuer(iss)

    def get(self, iss: str, org_id: str | None = None) -> IssuerConfig | None:
        """Return the config for *iss* owned by *org_id*.

        With no ``org_id``: the operator-registered entry, else the only
        org-owned entry, else ``None``. Not for token verification — use
        :meth:`resolve`.
        """
        if org_id:
            return self._issuers.get((str(org_id), iss))
        cfg = self._issuers.get((None, iss))
        if cfg is not None:
            return cfg
        cands = self.candidates(iss)
        return cands[0] if len(cands) == 1 else None

    def for_org(self, org_id: str) -> list[IssuerConfig]:
        """Configurations owned by *org_id*."""
        return [cfg for (o, _i), cfg in self._issuers.items() if o == str(org_id)]

    def unregister(self, iss: str, org_id: str | None = None) -> None:
        """Remove an issuer from the registry (idempotent).

        With ``org_id`` only that org's entry is removed; without it the
        operator-registered entry is.
        """
        self._issuers.pop((str(org_id) if org_id else None, iss), None)

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
