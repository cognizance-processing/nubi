"""JWT HS256 access-token helpers.

Public API
----------
mint_access_token(user_id: str, extra_claims: dict | None) -> str
    Create and sign a short-lived access JWT.

decode_access_token(token: str) -> dict
    Verify and decode an access JWT; raise AppError on any failure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from jwt.exceptions import PyJWTError

from app.config import get_settings
from app.errors import AppError

# Algorithm is pinned here and in decode to prevent algorithm-confusion attacks.
_ALGORITHM = "HS256"
_TOKEN_TYPE = "access"


def mint_access_token(
    user_id: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed HS256 JWT access token.

    Parameters
    ----------
    user_id:
        The user's UUID (stored as the ``sub`` claim).
    extra_claims:
        Optional additional claims merged into the payload before signing.
        These must not override reserved claims (``sub``, ``iat``, ``exp``,
        ``typ``).

    Returns
    -------
    str
        The signed JWT string.
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    exp = now + timedelta(minutes=settings.JWT_ACCESS_TTL_MIN)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": now,
        "exp": exp,
        "typ": _TOKEN_TYPE,
    }
    if extra_claims:
        # extra_claims must not override core claims
        filtered = {
            k: v for k, v in extra_claims.items()
            if k not in ("sub", "iat", "exp", "typ")
        }
        payload.update(filtered)

    return jwt.encode(payload, settings.JWT_SECRET, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify and decode a JWT access token.

    Parameters
    ----------
    token:
        The raw JWT string from the ``Authorization: Bearer <token>`` header.

    Returns
    -------
    dict
        Decoded claims payload.

    Raises
    ------
    AppError("invalid_token", 401)
        On any verification failure: expired, wrong signature, bad algorithm,
        wrong token type, or malformed token.
    """
    settings = get_settings()
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[_ALGORITHM],  # pinned — rejects "none" and RS256 etc.
            options={"require": ["sub", "iat", "exp", "typ"]},
        )
    except PyJWTError:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    # Extra guard: reject tokens that are not access tokens.
    if claims.get("typ") != _TOKEN_TYPE:
        raise AppError("invalid_token", "Token is invalid or has expired.", 401)

    return claims
