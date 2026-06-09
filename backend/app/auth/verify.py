"""Unified token verifier for Nubi (M3-A).

Accepts either a first-party Nubi HS256 access token **or** a host-signed embed
JWT verified via JWKS (RS256 / ES256).  Returns a normalised :class:`VerifiedIdentity`
dataclass regardless of the token type.

Key security properties
-----------------------
* ``alg: none`` is always rejected.
* HS256 embed path is blocked: if a token declares a non-HS256 ``iss`` but is
  signed with HS256, verification fails (alg-confusion blocked).
* RS*/ES* keys are never used to verify HS256 tokens.
* ``exp`` is mandatory; missing ``exp`` → 401.
* ``aud``/``iss`` are validated on the embed path.
* ``embed_origin`` mismatch → 403.
* Unregistered ``iss`` → 401 (generic, no oracle).

Public API
----------
VerifiedIdentity
    Normalised identity dataclass.

verify_token(token, expected_origin) -> VerifiedIdentity
    Synchronous entry point — uses only the in-process IssuerRegistry.
    Suitable for tests and non-async contexts.  The FastAPI dep uses
    ``verify_token_async`` instead.

verify_token_async(token, expected_origin) -> VerifiedIdentity
    Async entry point — falls back to the DB-backed issuers store when the
    in-process registry does not recognise the ``iss`` claim.  Used by the
    ``verified_identity`` FastAPI dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from jwt.exceptions import PyJWTError

from app.errors import AppError

# Algorithms accepted on the asymmetric (embed) path.
_EMBED_ALGS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})
# Algorithms that are always rejected regardless of path.
_BLOCKED_ALGS = frozenset({"none", "None", "NONE"})

# Default scopes granted to a verified first-party (HS256) access token.
_FIRST_PARTY_SCOPES: list[str] = ["read:*", "edit:*"]


@dataclass
class VerifiedIdentity:
    """Normalised principal extracted from a verified token.

    Attributes
    ----------
    kind:
        ``"access"`` for first-party HS256 tokens; ``"embed"`` for host-signed
        RS256/ES256 embed JWTs.
    user_id:
        Subject identifier (``sub`` claim).
    org:
        Organisation identifier from the token (may be ``None``).
    project:
        Project identifier from the token (may be ``None``).
    roles:
        List of role strings (empty list if not present in the token).
    policies:
        Dict of RLS policy claims (e.g. ``{"tenant_id": "acme"}``).
    scope:
        List of scope strings parsed from the token.
    embed_origin:
        The ``embed_origin`` claim from an embed token, or ``None``.
    raw_claims:
        The full decoded payload dict (for downstream inspection).
    """

    kind: str  # "access" | "embed"
    user_id: str
    org: str | None
    project: str | None
    roles: list[str]
    policies: dict[str, Any]
    scope: list[str]
    embed_origin: str | None
    raw_claims: dict[str, Any]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode_header_unverified(token: str) -> dict[str, Any]:
    """Decode only the JOSE header without verifying the signature.

    PyJWT's ``get_unverified_header`` is safe for this purpose — it does not
    trust the header, it merely parses it.

    Raises
    ------
    AppError("invalid_token", 401)
        If the token is not a valid three-part JWT.
    """
    try:
        return jwt.get_unverified_header(token)
    except PyJWTError:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)


def _build_jwks_from_static_key(pem: str) -> dict[str, Any]:
    """Wrap a PEM public key in a minimal single-entry JWKS dict.

    The ``kid`` is left empty so callers skip kid-matching and use the only key
    present.  This is only used for static-key issuers in tests.
    """
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "kid": "",
                "pem": pem,  # custom field; handled in _select_key_from_jwks
            }
        ]
    }


def _select_key_from_jwks(
    jwks: dict[str, Any],
    kid: str | None,
    alg: str,
) -> Any:
    """Select the correct key from a JWKS for the given *kid* and *alg*.

    Strategy:
    1. If a key has a ``"pem"`` field (our static test sentinel), return it
       directly as a string.
    2. Prefer the key whose ``kid`` matches *kid* (when *kid* is provided and
       non-empty).
    3. Fall back to the first key in the set when there is no kid or no match.

    Raises
    ------
    AppError("invalid_token", 401)
        If no usable key is found or the key cannot be loaded.
    """
    from app.errors import AppError

    keys: list[dict[str, Any]] = jwks.get("keys", [])
    if not keys:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    # 1. PEM shortcut for static test keys.
    if len(keys) == 1 and "pem" in keys[0]:
        return keys[0]["pem"]

    # 2. kid-based selection.
    selected: dict[str, Any] | None = None
    if kid:
        for k in keys:
            if k.get("kid") == kid:
                selected = k
                break

    # 3. Fallback to first key.
    if selected is None:
        selected = keys[0]

    # Convert the JWK to a public key object using PyJWT's algorithms layer.
    try:
        import jwt as _jwt  # noqa: PLC0415

        algorithm_obj = _jwt.algorithms.get_default_algorithms().get(alg)
        if algorithm_obj is None:
            raise AppError("invalid_token", "Token is invalid or has expired.", 401)
        public_key = algorithm_obj.from_jwk(selected)
        return public_key
    except (AppError, Exception):
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)


def _resolve_jwks(issuer_cfg: Any) -> dict[str, Any]:
    """Return the JWKS for *issuer_cfg*, using static data when available.

    Priority: ``static_jwks`` > ``static_public_key`` > ``jwks_uri`` (network).
    """
    if issuer_cfg.static_jwks is not None:
        return issuer_cfg.static_jwks

    if issuer_cfg.static_public_key is not None:
        return _build_jwks_from_static_key(issuer_cfg.static_public_key)

    # Live network fetch (TTL-cached).
    from app.auth.jwks_cache import get_jwks

    return get_jwks(issuer_cfg.jwks_uri)


# ---------------------------------------------------------------------------
# Shared embed-token verification core
# ---------------------------------------------------------------------------

def _peek_embed_token(
    token: str,
    alg: str,
) -> tuple[str, dict[str, Any]]:
    """Validate alg, decode unverified payload, extract iss.

    Returns
    -------
    (iss, unverified_payload)

    Raises
    ------
    AppError("invalid_token", 401)
        On HS* alg, malformed token, or missing iss.
    """
    # Reject HS* on the embed path (alg-confusion attack).
    if alg.startswith("HS"):
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    try:
        unverified_payload = jwt.decode(
            token,
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_aud": False,
            },
            algorithms=list(_EMBED_ALGS),
        )
    except PyJWTError:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    iss: str | None = unverified_payload.get("iss")
    if not iss:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    return iss, unverified_payload


def _finish_embed_verification(
    token: str,
    alg: str,
    iss: str,
    public_key: Any,
    aud: str,
    expected_origin: str | None,
) -> VerifiedIdentity:
    """Run the final PyJWT decode + origin check + build VerifiedIdentity.

    This is split out so both the sync and async paths share the same
    signature-verification + claims-building logic.

    Raises
    ------
    AppError("invalid_token", 401)
    AppError("origin_mismatch", 403)
    """
    from app.auth.scopes import parse_scopes  # noqa: PLC0415

    # Fully verify the token (signature + exp + aud + iss).
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=[alg],  # pinned to the declared alg only
            audience=aud,
            issuer=iss,
            options={
                "require": ["exp", "aud", "iss", "sub"],
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except PyJWTError:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    # Origin enforcement.
    embed_origin: str | None = claims.get("embed_origin")
    if embed_origin:
        # SECURITY: if the token carries an embed_origin claim it MUST be
        # enforced regardless of whether the request sends an Origin header.
        # A missing Origin header (e.g. server-to-server, CLI, Postman) must
        # NOT be treated as "no origin restriction" — it must fail the check
        # just like a mismatched origin, because the token was explicitly bound
        # to a specific browser origin.  Accepting a missing Origin would allow
        # any non-browser client to bypass the restriction entirely.
        if expected_origin is None or embed_origin != expected_origin:
            raise AppError(
                "origin_mismatch",
                "Request origin does not match the token's embed_origin.",
                403,
            )

    return VerifiedIdentity(
        kind="embed",
        user_id=str(claims["sub"]),
        org=claims.get("org"),
        project=claims.get("project"),
        roles=list(claims.get("roles") or []),
        policies=dict(claims.get("policies") or {}),
        scope=parse_scopes(claims),
        embed_origin=embed_origin,
        raw_claims=claims,
    )


# ---------------------------------------------------------------------------
# Embed-path verification — synchronous (in-process registry only)
# ---------------------------------------------------------------------------

def _verify_embed_token(
    token: str,
    header: dict[str, Any],
    alg: str,
    expected_origin: str | None,
) -> VerifiedIdentity:
    """Verify an RS256/ES256 embed JWT using the in-process IssuerRegistry only.

    Used by the synchronous ``verify_token`` entry point.  Does NOT consult
    the DB-backed issuers store.  For the full path (registry → DB fallback)
    use ``_verify_embed_token_async``.
    """
    from app.auth.issuers import get_issuer_registry  # noqa: PLC0415

    iss, _unverified = _peek_embed_token(token, alg)

    registry = get_issuer_registry()
    issuer_cfg = registry.get(iss)
    if issuer_cfg is None:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    jwks = _resolve_jwks(issuer_cfg)
    kid: str | None = header.get("kid") or None
    public_key = _select_key_from_jwks(jwks, kid, alg)

    return _finish_embed_verification(
        token, alg, iss, public_key, issuer_cfg.aud, expected_origin
    )


# ---------------------------------------------------------------------------
# Embed-path verification — async (registry + DB-backed fallback)
# ---------------------------------------------------------------------------

async def _verify_embed_token_async(
    token: str,
    header: dict[str, Any],
    alg: str,
    expected_origin: str | None,
) -> VerifiedIdentity:
    """Verify an RS256/ES256 embed JWT.

    Lookup priority
    ---------------
    1. In-process ``IssuerRegistry`` (populated from env/code at startup and
       kept in sync by the jwt_issuers CRUD routes after each mutation).
    2. DB-backed ``issuers_store.get_enabled_by_iss`` — consulted when the
       in-process registry misses.  The token's unverified ``org`` claim is
       used to scope the DB query.

    Unknown or disabled ``iss`` → 401.
    """
    from app.auth.issuers import get_issuer_registry  # noqa: PLC0415

    iss, unverified_payload = _peek_embed_token(token, alg)
    kid: str | None = header.get("kid") or None

    # 1. In-process registry (fast path, always tried first).
    registry = get_issuer_registry()
    issuer_cfg = registry.get(iss)

    if issuer_cfg is not None:
        jwks = _resolve_jwks(issuer_cfg)
        public_key = _select_key_from_jwks(jwks, kid, alg)
        return _finish_embed_verification(
            token, alg, iss, public_key, issuer_cfg.aud, expected_origin
        )

    # 2. DB fallback — requires the token to carry an ``org`` claim so we can
    #    scope the lookup to the correct org's configured issuers.
    org_from_token: str | None = unverified_payload.get("org")
    if not org_from_token:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    try:
        from app.security.issuers_store import get_issuers_store  # noqa: PLC0415

        db_row = await get_issuers_store().get_enabled_by_iss(org_from_token, iss)
    except Exception:  # noqa: BLE001
        db_row = None

    if db_row is None:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    from app.security.jwks import resolve_signing_key  # noqa: PLC0415

    public_key = resolve_signing_key(db_row, kid, alg)
    return _finish_embed_verification(
        token, alg, iss, public_key, db_row.get("audience", ""), expected_origin
    )


# ---------------------------------------------------------------------------
# First-party HS256 path
# ---------------------------------------------------------------------------

def _verify_first_party_token(token: str) -> VerifiedIdentity:
    """Verify a Nubi-issued HS256 access token via :func:`decode_access_token`."""
    from app.auth.jwt import decode_access_token
    from app.auth.scopes import parse_scopes

    # decode_access_token already pins HS256, validates exp, and raises AppError.
    claims = decode_access_token(token)

    # Scopes from the token, or default full-access for a logged-in user.
    token_scopes = parse_scopes(claims)
    if not token_scopes:
        token_scopes = list(_FIRST_PARTY_SCOPES)

    return VerifiedIdentity(
        kind="access",
        user_id=str(claims["sub"]),
        org=claims.get("org"),
        project=claims.get("project"),
        roles=list(claims.get("roles") or []),
        policies=dict(claims.get("policies") or {}),
        scope=token_scopes,
        embed_origin=None,
        raw_claims=claims,
    )


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def verify_token(
    token: str,
    expected_origin: str | None = None,
) -> VerifiedIdentity:
    """Verify *token* and return a normalised :class:`VerifiedIdentity`.

    **Synchronous** — uses only the in-process ``IssuerRegistry`` for embed
    tokens.  Does NOT consult the DB-backed issuers store.  Use
    :func:`verify_token_async` (via the ``verified_identity`` FastAPI dep) for
    full DB-backed lookup in production.

    Routing logic:
    - Decode the JOSE header (no signature check) to read ``alg``.
    - If ``alg == "HS256"`` → first-party path (delegates to
      :func:`~app.auth.jwt.decode_access_token` which pins HS256).
    - If ``alg`` is in the asymmetric set (RS256/ES256/…) → embed path
      (JWKS-backed verification via the issuer registry).
    - ``alg == "none"`` (any case) → always rejected.
    - Any other / unknown ``alg`` → rejected.

    Parameters
    ----------
    token:
        Raw JWT string from an ``Authorization: Bearer`` header or a web
        component attribute.
    expected_origin:
        When provided, the ``embed_origin`` claim (if present) must match.
        Pass ``None`` to skip the origin check at the call site.

    Returns
    -------
    VerifiedIdentity

    Raises
    ------
    AppError("invalid_token", 401)
        On any token-level failure (malformed, expired, bad signature,
        unknown issuer, wrong algorithm, missing required claims).
    AppError("origin_mismatch", 403)
        When ``embed_origin`` is present and does not match *expected_origin*.
    AppError("insufficient_scope", 403)
        Not raised here; callers use :func:`~app.auth.scopes.require_scope`.
    """
    header = _decode_header_unverified(token)
    alg: str = header.get("alg", "")

    # Block 'none' immediately.
    if alg in _BLOCKED_ALGS or alg == "":
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    if alg == "HS256":
        return _verify_first_party_token(token)

    if alg in _EMBED_ALGS:
        return _verify_embed_token(token, header, alg, expected_origin)

    # Unknown / unsupported algorithm.
    raise AppError("invalid_token", "Token is invalid or has expired.", 401)


async def verify_token_async(
    token: str,
    expected_origin: str | None = None,
) -> VerifiedIdentity:
    """Async variant of :func:`verify_token` with DB-backed issuer fallback.

    Identical routing logic to :func:`verify_token` except that on the embed
    path, when the in-process ``IssuerRegistry`` does not recognise the
    ``iss`` claim, the ``jwt_issuers`` DB table is consulted via
    :mod:`app.security.issuers_store`.

    This is the entry point used by the ``verified_identity`` FastAPI
    dependency so that issuers configured via the management API (without
    requiring a server restart) are correctly recognised.

    Parameters
    ----------
    token:
        Raw JWT string.
    expected_origin:
        Forwarded to origin enforcement (see :func:`verify_token`).

    Returns
    -------
    VerifiedIdentity

    Raises
    ------
    AppError("invalid_token", 401)
    AppError("origin_mismatch", 403)
    """
    header = _decode_header_unverified(token)
    alg: str = header.get("alg", "")

    if alg in _BLOCKED_ALGS or alg == "":
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    if alg == "HS256":
        return _verify_first_party_token(token)

    if alg in _EMBED_ALGS:
        return await _verify_embed_token_async(token, header, alg, expected_origin)

    raise AppError("invalid_token", "Token is invalid or has expired.", 401)
