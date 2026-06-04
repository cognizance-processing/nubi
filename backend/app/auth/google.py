"""Google OAuth 2.0 Authorization Code + PKCE flow helpers.

Public API
----------
build_authorize_url(state: str, code_challenge: str) -> str
    Build the Google authorization URL to redirect the user to.

exchange_code(code: str, code_verifier: str) -> dict
    Exchange the authorization code for tokens, fetch user info, and return
    a normalized profile dict.

generate_pkce_pair() -> (code_verifier: str, code_challenge: str)
    Utility: generate a random PKCE code_verifier and its S256 challenge.

generate_state() -> str
    Utility: generate a cryptographically random state string.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.errors import AppError

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

_SCOPES = "openid email profile"


def generate_state() -> str:
    """Return a 32-byte URL-safe random state string."""
    return secrets.token_urlsafe(32)


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE (code_verifier, code_challenge) pair.

    The challenge method is S256: challenge = BASE64URL(SHA-256(verifier)).

    Returns
    -------
    (code_verifier, code_challenge)
        Both are URL-safe strings.  Store *code_verifier* in a short-lived
        HttpOnly cookie; send *code_challenge* to Google.
    """
    code_verifier = secrets.token_urlsafe(64)  # 64 bytes → 86-char base64url
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_authorize_url(state: str, code_challenge: str) -> str:
    """Construct the Google OAuth authorization redirect URL.

    Parameters
    ----------
    state:
        Anti-CSRF state value (must be verified in the callback).
    code_challenge:
        PKCE S256 challenge derived from the code_verifier.

    Returns
    -------
    str
        The full URL to redirect the user's browser to.
    """
    settings = get_settings()
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str, code_verifier: str) -> dict[str, Any]:
    """Exchange an authorization code for user profile information.

    Performs two requests:
    1. POST to Google token endpoint with code + code_verifier (PKCE).
    2. GET to Google userinfo endpoint with the returned access_token.

    Parameters
    ----------
    code:
        The ``code`` query parameter from the OAuth callback.
    code_verifier:
        The original PKCE verifier that was stored in the state cookie.

    Returns
    -------
    dict
        Normalized user profile::

            {
                "provider_account_id": str,  # Google "sub"
                "email": str,
                "email_verified": bool,
                "name": str | None,
                "picture": str | None,
            }

    Raises
    ------
    AppError("oauth_failed", 502)
        If the token exchange or userinfo fetch fails.
    """
    settings = get_settings()

    token_payload = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        # ── 1. Exchange code for tokens ───────────────────────────────────────
        try:
            token_resp = await client.post(_GOOGLE_TOKEN_URL, data=token_payload)
            token_resp.raise_for_status()
            token_data = token_resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError, Exception) as exc:
            raise AppError(
                "oauth_failed",
                "Google token exchange failed.",
                502,
            ) from exc

        access_token: str | None = token_data.get("access_token")
        if not access_token:
            raise AppError("oauth_failed", "Google token exchange failed.", 502)

        # ── 2. Fetch user profile ─────────────────────────────────────────────
        try:
            userinfo_resp = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError, Exception) as exc:
            raise AppError(
                "oauth_failed",
                "Failed to fetch Google user info.",
                502,
            ) from exc

    # ── Normalize profile ─────────────────────────────────────────────────────
    sub: str | None = userinfo.get("sub")
    email: str | None = userinfo.get("email")

    if not sub or not email:
        raise AppError("oauth_failed", "Google did not return required fields.", 502)

    return {
        "provider_account_id": sub,
        "email": email,
        "email_verified": bool(userinfo.get("email_verified", False)),
        "name": userinfo.get("name"),
        "picture": userinfo.get("picture"),
    }
