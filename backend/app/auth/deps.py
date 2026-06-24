"""FastAPI dependencies for authenticated routes.

Public API
----------
current_user
    A ``Depends``-injectable that reads the ``Authorization: Bearer <token>``
    header, decodes the JWT, loads the user row from the DB, and returns it
    as a plain dict.

    Raises ``AppError("unauthorized", 401)`` if:
    - The header is absent or malformed.
    - The JWT is invalid or expired.
    - The user no longer exists in the database.

verified_identity
    A ``Depends``-injectable that reads the ``Authorization: Bearer <token>``
    header and calls ``verify_token(token, expected_origin)`` to return a
    :class:`~app.auth.verify.VerifiedIdentity`.  Accepts BOTH first-party
    HS256 access tokens AND host-signed RS256/ES256 embed JWTs.

    Raises ``AppError("unauthorized", 401)`` if the header is absent.
    Re-raises any ``AppError`` from ``verify_token`` (e.g. 401 invalid token,
    403 origin mismatch).
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.denylist import get_token_denylist
from app.auth.jwt import decode_access_token
from app.auth.verify import VerifiedIdentity, verify_token_async
from app.db import fetchrow
from app.errors import AppError

# HTTPBearer extracts ``Authorization: Bearer <token>`` automatically.
# ``auto_error=False`` lets us emit a proper AppError instead of a generic 403.
_bearer = HTTPBearer(auto_error=False)


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    """Dependency: resolve the bearer token to a verified user dict.

    Parameters
    ----------
    credentials:
        Injected by FastAPI from the ``Authorization`` header.

    Returns
    -------
    dict
        User row with keys matching the ``user`` shape in the API contract:
        ``{id, email, name, avatar_url, email_verified, created_at}``.

    Raises
    ------
    AppError("unauthorized", 401)
        If the token is missing, invalid, expired, or the user is not found.
    """
    if credentials is None:
        raise AppError("unauthorized", "Authentication required.", 401)

    # ── API-key bearer path (CLI / CI long-lived tokens) ────────────────────
    # An opaque ``nubi_ak_…`` credential is NOT a JWT; resolve it against the
    # api_keys store instead of decoding. A resolved key authenticates exactly
    # like an access token but additionally PINS the request to the org the key
    # was minted for (carried as ``_api_key_org_id`` so resolve_org_id/
    # get_user_org honour it and reject any cross-org X-Org-Id).
    from app.auth.api_keys import get_api_key_store, looks_like_api_key

    raw_credential: str = credentials.credentials
    if looks_like_api_key(raw_credential):
        key_row = await get_api_key_store().resolve(raw_credential)
        if key_row is None:
            raise AppError("unauthorized", "Authentication required.", 401)
        user_row = await fetchrow(
            """
            SELECT id, email, name, avatar_url, email_verified, created_at
            FROM users
            WHERE id = $1::uuid
            """,
            str(key_row["user_id"]),
        )
        if user_row is None:
            raise AppError("unauthorized", "Authentication required.", 401)
        user = dict(user_row)
        user["_api_key_org_id"] = str(key_row["org_id"])
        # Pin org resolution for the rest of this request to the key's org.
        from app.routes._org import api_key_org_pin

        api_key_org_pin.set(str(key_row["org_id"]))
        return user

    claims = decode_access_token(raw_credential)
    user_id: str = claims["sub"]

    # Denylist check: reject tokens that have been explicitly revoked (e.g.
    # after logout) even if they are otherwise valid JWTs.
    jti: str = claims["jti"]
    try:
        if await get_token_denylist().is_revoked(jti):
            raise AppError("unauthorized", "Authentication required.", 401)
    except AppError:
        raise
    except Exception:
        # Fail-safe: if the denylist store raises unexpectedly, let the request
        # through rather than causing an outage.  Log in production environments.
        pass

    row = await fetchrow(
        """
        SELECT id, email, name, avatar_url, email_verified, created_at
        FROM users
        WHERE id = $1::uuid
        """,
        user_id,
    )

    if row is None:
        raise AppError("unauthorized", "Authentication required.", 401)

    return dict(row)


async def _maybe_pin_host_mode_org(identity: "VerifiedIdentity") -> None:
    """Pin the request's org from a host-mode embed token's claim (async).

    Called only for embed tokens (``kind="embed"``).  Looks up the issuer
    config for the token's ``iss`` claim; if the issuer is flagged
    ``host_mode=True``, extracts the org value from the named claim, validates
    it is non-empty, and sets the ``host_mode_org_pin`` ContextVar so that
    ``get_user_org`` / ``resolve_org_id`` never touch ``org_members``.

    Security invariants enforced here:
    - Only issuers explicitly flagged ``host_mode`` trigger this path.
    - Missing / empty org claim on a host-mode token → AppError 403.
    - The scope on the identity is limited to embed/READ only (host-mode
      tokens cannot carry write/admin scopes — enforced by the issuer's
      scope policy, verified in tests).
    - RLS policies come from the ``policies`` claim (already extracted into
      ``identity.policies``); the org claim only selects the tenant.
    """
    from app.auth.issuers import get_issuer_registry  # noqa: PLC0415
    from app.routes._org import host_mode_org_pin  # noqa: PLC0415

    iss: str | None = identity.raw_claims.get("iss")
    if not iss:
        return  # no iss claim — non-host-mode embed token, skip

    # 1. In-process registry (fast path)
    registry = get_issuer_registry()
    issuer_cfg = registry.get(iss)

    if issuer_cfg is not None:
        if not issuer_cfg.host_mode:
            return  # standard embed token — org_members membership required
        claim_name = issuer_cfg.org_claim or "org"
        org_val = identity.raw_claims.get(claim_name)
        # SECURITY: org claim must be a plain string — reject arrays, objects,
        # numbers, and booleans.  A non-string claim that stringifies to a
        # non-empty value (e.g. ["tenant_a"]) would otherwise bypass the empty
        # check and pin a garbage org_id like "['tenant_a']".
        if not isinstance(org_val, str) or not org_val.strip():
            raise AppError(
                "forbidden",
                "Host-mode token is missing the required org claim.",
                403,
            )
        host_mode_org_pin.set(org_val.strip())
        return

    # 2. DB fallback — check the issuers store
    org_hint: str | None = identity.raw_claims.get("org")
    if not org_hint:
        return  # no org hint to scope the query; skip (non-host-mode)

    try:
        from app.security.issuers_store import get_issuers_store  # noqa: PLC0415

        db_row = await get_issuers_store().get_enabled_by_iss(org_hint, iss)
    except Exception:  # noqa: BLE001
        db_row = None

    if db_row is None:
        return  # issuer not found or not enabled; skip

    if not db_row.get("host_mode", False):
        return  # issuer found but not host_mode

    claim_name = db_row.get("org_claim") or "org"
    org_val = identity.raw_claims.get(claim_name)
    # SECURITY: org claim must be a plain string (same type guard as the
    # in-process registry path above).
    if not isinstance(org_val, str) or not org_val.strip():
        raise AppError(
            "forbidden",
            "Host-mode token is missing the required org claim.",
            403,
        )
    host_mode_org_pin.set(org_val.strip())


async def verified_identity(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> VerifiedIdentity:
    """Dependency: verify the bearer token and return a :class:`VerifiedIdentity`.

    Accepts BOTH first-party Nubi HS256 access tokens AND host-signed RS256/
    ES256 embed JWTs (verified via JWKS through the issuer registry).  The
    ``Origin`` header is forwarded to ``verify_token`` so that embed tokens
    with an ``embed_origin`` claim are validated against the actual request
    origin — no extra work is needed in the route handler.

    Parameters
    ----------
    request:
        The incoming FastAPI ``Request`` (needed to read the ``Origin`` header).
    credentials:
        Injected by FastAPI from the ``Authorization: Bearer`` header.

    Returns
    -------
    VerifiedIdentity
        Normalised identity with ``kind``, ``user_id``, ``policies``,
        ``scope``, ``embed_origin``, and ``raw_claims``.

    Raises
    ------
    AppError("unauthorized", 401)
        If the ``Authorization`` header is absent.
    AppError("invalid_token", 401)
        If the token is malformed, expired, or fails signature verification.
    AppError("origin_mismatch", 403)
        If the token's ``embed_origin`` claim does not match the request
        ``Origin`` header.
    AppError("forbidden", 403)
        If the token is a host-mode embed token missing the required org claim.
    """
    if credentials is None:
        raise AppError("unauthorized", "Authentication required.", 401)

    # Pass the request Origin header so verify_token can enforce embed_origin
    # pinning without any additional logic here.
    expected_origin: str | None = request.headers.get("origin")

    identity = await verify_token_async(credentials.credentials, expected_origin=expected_origin)

    # Host-mode embed tokens: pin the org from the JWT claim so downstream
    # org-resolution helpers (get_user_org / resolve_org_id) never consult
    # org_members for this request.
    if identity.kind == "embed":
        await _maybe_pin_host_mode_org(identity)

    # Denylist check for first-party (HS256) access tokens only.
    # Embed tokens are short-lived, audience-scoped and host-signed; they
    # cannot be revoked via Nubi logout so we skip the check for them.
    if identity.kind == "access":
        jti: str | None = identity.raw_claims.get("jti")
        if jti:
            try:
                if await get_token_denylist().is_revoked(jti):
                    raise AppError("unauthorized", "Authentication required.", 401)
            except AppError:
                raise
            except Exception:
                # Fail-safe: if the denylist store raises unexpectedly, let the
                # request through rather than causing an outage.
                pass

    return identity
