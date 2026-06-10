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

from typing import Any

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

    claims = decode_access_token(credentials.credentials)
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
    """
    if credentials is None:
        raise AppError("unauthorized", "Authentication required.", 401)

    # Pass the request Origin header so verify_token can enforce embed_origin
    # pinning without any additional logic here.
    expected_origin: str | None = request.headers.get("origin")

    identity = await verify_token_async(credentials.credentials, expected_origin=expected_origin)

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
