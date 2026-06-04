"""HttpOnly refresh-token cookie helpers.

Public API
----------
set_refresh_cookie(response, raw_token, expires_at) -> None
    Write the refresh token into an HttpOnly secure cookie on *response*.

clear_refresh_cookie(response) -> None
    Expire the refresh cookie immediately.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Response

from app.config import get_settings

REFRESH_COOKIE_NAME = "nubi_refresh"

# Cookie is scoped to the auth sub-path so the browser only sends it on
# refresh/logout requests — it is never readable from JavaScript.
_COOKIE_PATH = "/api/v1/auth"


def set_refresh_cookie(
    response: Response,
    raw_token: str,
    expires_at: datetime,
) -> None:
    """Attach the refresh token as an HttpOnly cookie to *response*.

    Parameters
    ----------
    response:
        The FastAPI ``Response`` object (injected into the route function).
    raw_token:
        The plaintext refresh token returned by ``issue_refresh`` or
        ``rotate_refresh``.
    expires_at:
        The token's expiry datetime (timezone-aware UTC).  Used to compute
        ``max_age`` in seconds.
    """
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    max_age = max(0, int((expires_at - now).total_seconds()))

    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_token,
        max_age=max_age,
        path=_COOKIE_PATH,
        secure=settings.COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response) -> None:
    """Remove the refresh token cookie from *response*.

    Sets the cookie to an empty value with ``max_age=0`` so the browser
    discards it immediately.

    Parameters
    ----------
    response:
        The FastAPI ``Response`` object.
    """
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value="",
        max_age=0,
        path=_COOKIE_PATH,
        secure=settings.COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
