"""Backend auth test suite — Task D1.

Structure
---------
Section A  Pure-unit tests (no DB, no HTTP): passwords + JWT.
Section B  HTTP flow tests against the in-memory fake DB (via conftest fixtures).

Running
-------
    cd backend
    python -m pytest -q

All DB-dependent tests are driven by the in-memory fake defined in conftest.py.
If the fake is insufficient for a test it is marked with
``pytest.mark.skip(reason=...)``.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import jwt
import pytest
import pytest_asyncio

# ============================================================================
# Section A — Pure-unit tests (no DB, no HTTP, no fixtures needed)
# ============================================================================


class TestPasswordHashing:
    """argon2id hash/verify round-trips — no DB or app required."""

    def test_hash_returns_string(self):
        from app.auth.passwords import hash_password
        h = hash_password("correct-horse-battery")
        assert isinstance(h, str)
        assert h.startswith("$argon2id$")

    def test_verify_correct_password(self):
        from app.auth.passwords import hash_password, verify_password
        pw = "correct-horse-battery-staple"
        h = hash_password(pw)
        assert verify_password(h, pw) is True

    def test_verify_wrong_password(self):
        from app.auth.passwords import hash_password, verify_password
        h = hash_password("right-password")
        assert verify_password(h, "wrong-password") is False

    def test_verify_empty_against_real_hash(self):
        from app.auth.passwords import hash_password, verify_password
        h = hash_password("non-empty")
        assert verify_password(h, "") is False

    def test_verify_invalid_hash_string_returns_false(self):
        from app.auth.passwords import verify_password
        assert verify_password("not-an-argon2-hash", "password") is False

    def test_two_hashes_of_same_password_differ(self):
        """Each call generates a new salt so hashes should differ."""
        from app.auth.passwords import hash_password
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2

    def test_both_hashes_verify(self):
        from app.auth.passwords import hash_password, verify_password
        pw = "same-password"
        h1, h2 = hash_password(pw), hash_password(pw)
        assert verify_password(h1, pw) is True
        assert verify_password(h2, pw) is True


class TestJwtMintDecode:
    """JWT mint/decode — uses real JWT_SECRET from the test env vars."""

    def _settings(self):
        from app.config import get_settings
        get_settings.cache_clear()
        return get_settings()

    def test_mint_returns_string(self):
        from app.auth.jwt import mint_access_token
        tok = mint_access_token("user-123")
        assert isinstance(tok, str)
        # three base64url segments separated by dots
        assert tok.count(".") == 2

    def test_decode_roundtrip(self):
        from app.auth.jwt import decode_access_token, mint_access_token
        user_id = "abc-def-123"
        tok = mint_access_token(user_id)
        claims = decode_access_token(tok)
        assert claims["sub"] == user_id
        assert claims["typ"] == "access"

    def test_decode_contains_required_claims(self):
        from app.auth.jwt import decode_access_token, mint_access_token
        tok = mint_access_token("u1")
        claims = decode_access_token(tok)
        for field in ("sub", "iat", "exp", "typ"):
            assert field in claims

    def test_expired_token_rejected(self):
        """A token with exp in the past must raise AppError(401)."""
        from app.auth.jwt import decode_access_token
        from app.errors import AppError

        settings = self._settings()
        now = datetime.now(tz=timezone.utc)
        payload = {
            "sub": "u1",
            "iat": now - timedelta(minutes=20),
            "exp": now - timedelta(minutes=5),
            "typ": "access",
        }
        expired_tok = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
        with pytest.raises(AppError) as exc_info:
            decode_access_token(expired_tok)
        assert exc_info.value.status == 401

    def test_wrong_algorithm_rejected(self):
        """Token signed with 'none' algorithm must be rejected."""
        from app.auth.jwt import decode_access_token
        from app.errors import AppError

        settings = self._settings()
        now = datetime.now(tz=timezone.utc)
        payload = {
            "sub": "u1",
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "typ": "access",
        }
        # Craft token with alg=none — PyJWT allows this with algorithm="none"
        # but our decoder must reject it because algorithms=["HS256"] is pinned.
        none_tok = jwt.encode(payload, "", algorithm="none")
        with pytest.raises(AppError) as exc_info:
            decode_access_token(none_tok)
        assert exc_info.value.status == 401

    def test_tampered_signature_rejected(self):
        from app.auth.jwt import decode_access_token, mint_access_token
        from app.errors import AppError

        tok = mint_access_token("u1")
        header, payload, sig = tok.split(".")
        bad_tok = f"{header}.{payload}.invalidsignature"
        with pytest.raises(AppError) as exc_info:
            decode_access_token(bad_tok)
        assert exc_info.value.status == 401

    def test_wrong_token_type_rejected(self):
        """Token with typ != 'access' must be rejected even if otherwise valid."""
        from app.auth.jwt import decode_access_token
        from app.errors import AppError

        settings = self._settings()
        now = datetime.now(tz=timezone.utc)
        payload = {
            "sub": "u1",
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "typ": "refresh",  # wrong type
        }
        tok = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
        with pytest.raises(AppError) as exc_info:
            decode_access_token(tok)
        assert exc_info.value.status == 401

    def test_wrong_secret_rejected(self):
        from app.auth.jwt import decode_access_token
        from app.errors import AppError

        now = datetime.now(tz=timezone.utc)
        payload = {
            "sub": "u1",
            "iat": now,
            "exp": now + timedelta(minutes=15),
            "typ": "access",
        }
        bad_tok = jwt.encode(payload, "wrong-secret-key", algorithm="HS256")
        with pytest.raises(AppError) as exc_info:
            decode_access_token(bad_tok)
        assert exc_info.value.status == 401

    def test_extra_claims_included(self):
        from app.auth.jwt import decode_access_token, mint_access_token
        tok = mint_access_token("u1", extra_claims={"role": "admin"})
        claims = decode_access_token(tok)
        assert claims.get("role") == "admin"

    def test_extra_claims_cannot_override_sub(self):
        """extra_claims must not override reserved 'sub'."""
        from app.auth.jwt import decode_access_token, mint_access_token
        tok = mint_access_token("real-user-id", extra_claims={"sub": "hacker-id"})
        claims = decode_access_token(tok)
        assert claims["sub"] == "real-user-id"


class TestTokenHashing:
    """Ensure the sessions module hashes tokens with SHA-256."""

    def test_hash_is_sha256_hex(self):
        from app.auth.sessions import _hash_token
        raw = "my-raw-token"
        result = _hash_token(raw)
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert result == expected
        assert len(result) == 64  # 32 bytes → 64 hex chars

    def test_different_tokens_different_hashes(self):
        from app.auth.sessions import _hash_token
        assert _hash_token("token-a") != _hash_token("token-b")


# ============================================================================
# Section B — HTTP flow tests (require in-memory fake DB via conftest)
# ============================================================================

# Helpers for cookie extraction -------------------------------------------

def _get_refresh_cookie(response) -> str | None:
    """Extract the nubi_refresh cookie value from a response."""
    cookies = response.cookies
    return cookies.get("nubi_refresh")


def _assert_error_shape(body: dict, code: str | None = None) -> None:
    """Assert the response body is the standard error envelope."""
    assert "error" in body, f"Expected 'error' key in body: {body}"
    if code is not None:
        assert body["error"]["code"] == code, (
            f"Expected error code '{code}', got '{body['error']['code']}'"
        )


# ── Register ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_201_returns_user_and_token(client, fake_db):
    """POST /auth/register → 201, body contains user + access_token, sets cookie."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "password123", "name": "Alice"},
    )
    assert resp.status_code == 201, resp.text

    body = resp.json()
    assert "user" in body
    assert "access_token" in body

    user = body["user"]
    assert user["email"] == "alice@example.com"
    assert user["name"] == "Alice"
    assert "id" in user
    assert "created_at" in user
    assert user["email_verified"] is False

    # Refresh cookie must be set
    assert "nubi_refresh" in resp.cookies, f"Cookies: {dict(resp.cookies)}"


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(client):
    """Registering with an already-taken email must return 409."""
    payload = {"email": "dup@example.com", "password": "password123"}
    await client.post("/api/v1/auth/register", json=payload)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 409
    _assert_error_shape(resp.json(), "email_taken")


@pytest.mark.asyncio
async def test_register_short_password_422(client):
    """Passwords shorter than 8 chars must be rejected at validation layer."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "short@example.com", "password": "short"},
    )
    assert resp.status_code == 422


# ── Login ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_login_success(client):
    """POST /auth/login with correct credentials → 200, user + token + cookie."""
    # Register first
    await client.post(
        "/api/v1/auth/register",
        json={"email": "bob@example.com", "password": "mypassword1"},
    )

    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "mypassword1"},
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "user" in body
    assert "access_token" in body
    assert body["user"]["email"] == "bob@example.com"
    assert "nubi_refresh" in resp.cookies


@pytest.mark.asyncio
async def test_login_wrong_password_401(client):
    """Wrong password → 401 with invalid_credentials code."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "carol@example.com", "password": "rightpassword1"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "carol@example.com", "password": "wrongpassword1"},
    )
    assert resp.status_code == 401
    _assert_error_shape(resp.json(), "invalid_credentials")


@pytest.mark.asyncio
async def test_login_unknown_email_401(client):
    """Unknown email → 401 with the SAME error code as wrong password."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "doesntmatter"},
    )
    assert resp.status_code == 401
    _assert_error_shape(resp.json(), "invalid_credentials")


@pytest.mark.asyncio
async def test_login_wrong_pw_and_unknown_email_same_error(client):
    """The error code for wrong-password and unknown-email must be identical.

    This prevents user enumeration attacks.
    """
    await client.post(
        "/api/v1/auth/register",
        json={"email": "dave@example.com", "password": "correctpassword1"},
    )

    wrong_pw = await client.post(
        "/api/v1/auth/login",
        json={"email": "dave@example.com", "password": "wrongpassword1"},
    )
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "wrongpassword1"},
    )

    assert wrong_pw.status_code == unknown.status_code == 401
    assert (
        wrong_pw.json()["error"]["code"] == unknown.json()["error"]["code"]
    ), "Error codes differ — user enumeration possible"


# ── /me ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_me_with_valid_bearer(client):
    """GET /auth/me with a valid Bearer token → 200 + user."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "eve@example.com", "password": "password123"},
    )
    token = reg.json()["access_token"]

    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == "eve@example.com"


@pytest.mark.asyncio
async def test_me_without_token_401(client):
    """GET /auth/me with no Authorization header → 401."""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_bad_token_401(client):
    """GET /auth/me with a garbage token → 401."""
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer this-is-not-a-jwt"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_expired_token_401(client):
    """GET /auth/me with an expired JWT → 401."""
    from app.config import get_settings

    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": "some-user-id",
        "iat": now - timedelta(minutes=20),
        "exp": now - timedelta(minutes=5),
        "typ": "access",
    }
    expired_tok = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_tok}"},
    )
    assert resp.status_code == 401


# ── Refresh ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_rotates_token(client):
    """POST /auth/refresh rotates: new access token returned, new cookie set."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "frank@example.com", "password": "password123"},
    )
    original_cookie = _get_refresh_cookie(reg)
    assert original_cookie, "No refresh cookie after register"

    resp = await client.post(
        "/api/v1/auth/refresh",
        cookies={"nubi_refresh": original_cookie},
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "access_token" in body

    # New cookie must be set and differ from the old one
    new_cookie = _get_refresh_cookie(resp)
    assert new_cookie, "No new refresh cookie after rotation"
    assert new_cookie != original_cookie, "Refresh cookie was not rotated"


@pytest.mark.asyncio
async def test_refresh_without_cookie_401(client):
    """POST /auth/refresh with no cookie → 401."""
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_with_bad_token_401(client):
    """POST /auth/refresh with a bogus cookie value → 401."""
    resp = await client.post(
        "/api/v1/auth/refresh",
        cookies={"nubi_refresh": "not-a-real-token"},
    )
    assert resp.status_code == 401


# ── Refresh reuse detection ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_reuse_revokes_family(client, fake_db):
    """Reusing a consumed refresh token must revoke the whole family.

    Flow:
      1. register → get T1
      2. POST /refresh with T1 → get T2  (T1 is now consumed/revoked)
      3. POST /refresh with T1 again → 401 (reuse detected; family revoked)
      4. POST /refresh with T2 → 401 (family is now revoked)
    """
    # Step 1: register
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "grace@example.com", "password": "password123"},
    )
    t1 = _get_refresh_cookie(reg)
    assert t1

    # Step 2: first rotation — T1 → T2
    rot1 = await client.post(
        "/api/v1/auth/refresh",
        cookies={"nubi_refresh": t1},
    )
    assert rot1.status_code == 200, rot1.text
    t2 = _get_refresh_cookie(rot1)
    assert t2 and t2 != t1

    # Step 3: reuse T1 — must 401 and revoke family
    reuse = await client.post(
        "/api/v1/auth/refresh",
        cookies={"nubi_refresh": t1},
    )
    assert reuse.status_code == 401, f"Expected 401 on reuse, got {reuse.status_code}"

    # Step 4: T2 should also be revoked now (family nuked)
    t2_try = await client.post(
        "/api/v1/auth/refresh",
        cookies={"nubi_refresh": t2},
    )
    assert t2_try.status_code == 401, (
        f"T2 should be revoked after family revocation, got {t2_try.status_code}"
    )


# ── Logout ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout_returns_204(client):
    """POST /auth/logout → 204."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "henry@example.com", "password": "password123"},
    )
    cookie = _get_refresh_cookie(reg)

    resp = await client.post(
        "/api/v1/auth/logout",
        cookies={"nubi_refresh": cookie},
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_logout_then_refresh_fails(client):
    """After logout, the old refresh token must no longer work."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "iris@example.com", "password": "password123"},
    )
    cookie = _get_refresh_cookie(reg)

    # Logout
    await client.post(
        "/api/v1/auth/logout",
        cookies={"nubi_refresh": cookie},
    )

    # Attempt refresh with the old cookie
    resp = await client.post(
        "/api/v1/auth/refresh",
        cookies={"nubi_refresh": cookie},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_without_cookie_204(client):
    """POST /auth/logout with no cookie must still return 204 (idempotent)."""
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 204


# ── Google OAuth — /google/start ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_google_start_redirects_to_google(client):
    """GET /auth/google/start → 302 with Location pointing to accounts.google.com."""
    resp = await client.get("/api/v1/auth/google/start")
    assert resp.status_code == 302

    location = resp.headers.get("location", "")
    assert "accounts.google.com" in location, (
        f"Expected redirect to accounts.google.com, got: {location}"
    )


@pytest.mark.asyncio
async def test_google_start_sets_pkce_cookies(client):
    """GET /auth/google/start must set state + verifier cookies."""
    resp = await client.get("/api/v1/auth/google/start")
    assert resp.status_code == 302

    # State and verifier cookies should be set (path=/api/v1/auth/google)
    # httpx stores them in the jar; check Set-Cookie headers directly
    set_cookie_headers = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []
    if not set_cookie_headers:
        # Fallback: check via raw headers
        set_cookie_headers = [
            v for k, v in resp.headers.items() if k.lower() == "set-cookie"
        ]

    cookie_names = [h.split("=")[0].strip() for h in set_cookie_headers]
    assert "nubi_oauth_state" in cookie_names, (
        f"Expected nubi_oauth_state cookie. Set-Cookie headers: {set_cookie_headers}"
    )
    assert "nubi_oauth_verifier" in cookie_names, (
        f"Expected nubi_oauth_verifier cookie. Set-Cookie headers: {set_cookie_headers}"
    )


@pytest.mark.asyncio
async def test_google_start_location_contains_state_and_pkce(client):
    """The Google redirect URL must include state and code_challenge params."""
    resp = await client.get("/api/v1/auth/google/start")
    location = resp.headers.get("location", "")

    assert "state=" in location
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location


# ── Google OAuth — /google/callback ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_google_callback_creates_user_and_redirects(client, fake_db):
    """Google callback with mocked exchange_code creates a user and redirects to FRONTEND_URL."""
    fake_profile = {
        "provider_account_id": "google-sub-12345",
        "email": "julia@example.com",
        "email_verified": True,
        "name": "Julia",
        "picture": "https://example.com/julia.jpg",
    }

    state_val = "test-state-value-abc123"
    verifier_val = "test-verifier-value-xyz789"

    with patch("app.routes.auth.exchange_code", new=AsyncMock(return_value=fake_profile)):
        resp = await client.get(
            "/api/v1/auth/google/callback",
            params={"code": "fake-auth-code", "state": state_val},
            cookies={
                "nubi_oauth_state": state_val,
                "nubi_oauth_verifier": verifier_val,
            },
        )

    assert resp.status_code == 302, resp.text
    location = resp.headers.get("location", "")
    assert "localhost:3000" in location, f"Expected redirect to FRONTEND_URL, got: {location}"

    # User should be created in the fake DB
    user = fake_db._user_by_email("julia@example.com")
    assert user is not None, "User was not created in fake DB"
    assert user["name"] == "Julia"
    assert user["email_verified"] is True

    # OAuth account row should exist
    oauth = fake_db._oauth_by_provider("google", "google-sub-12345")
    assert oauth is not None, "oauth_accounts row not created"

    # Refresh cookie should be set on the redirect response.
    # httpx may join multiple Set-Cookie headers into one string separated by ", ".
    # Split each header value on ", " to get individual cookie directives, then
    # find the nubi_refresh cookie that has a positive Max-Age.
    all_set_cookie_raw = [v for k, v in resp.headers.items() if k.lower() == "set-cookie"]
    # Flatten: each raw value may contain multiple cookies joined by ", "
    all_cookie_parts: list[str] = []
    for raw in all_set_cookie_raw:
        all_cookie_parts.extend(raw.split(", "))
    refresh_cookies = [
        c for c in all_cookie_parts
        if c.strip().startswith("nubi_refresh=") and "Max-Age=0" not in c
    ]
    assert refresh_cookies, (
        f"Expected nubi_refresh cookie on redirect. Set-Cookie headers: {all_set_cookie_raw}"
    )


@pytest.mark.asyncio
async def test_google_callback_links_existing_user(client, fake_db):
    """If the email already exists, Google callback must link, not create a duplicate."""
    # Pre-create user via register
    await client.post(
        "/api/v1/auth/register",
        json={"email": "kyle@example.com", "password": "password123", "name": "Kyle"},
    )

    # Verify only one user exists
    users_before = [u for u in fake_db.users.values() if u["email"].lower() == "kyle@example.com"]
    assert len(users_before) == 1

    fake_profile = {
        "provider_account_id": "google-sub-kyle-99",
        "email": "kyle@example.com",
        "email_verified": True,
        "name": "Kyle",
        "picture": None,
    }

    state_val = "state-for-kyle"
    verifier_val = "verifier-for-kyle"

    with patch("app.routes.auth.exchange_code", new=AsyncMock(return_value=fake_profile)):
        resp = await client.get(
            "/api/v1/auth/google/callback",
            params={"code": "fake-code", "state": state_val},
            cookies={
                "nubi_oauth_state": state_val,
                "nubi_oauth_verifier": verifier_val,
            },
        )

    assert resp.status_code == 302

    # Still only one user
    users_after = [u for u in fake_db.users.values() if u["email"].lower() == "kyle@example.com"]
    assert len(users_after) == 1, "Duplicate user created for existing email"

    # OAuth account linked to existing user
    oauth = fake_db._oauth_by_provider("google", "google-sub-kyle-99")
    assert oauth is not None
    assert oauth["user_id"] == users_after[0]["id"]


@pytest.mark.asyncio
async def test_google_callback_state_mismatch_redirects_with_error(client):
    """Mismatched state cookie → redirect to frontend with error param."""
    state_val = "correct-state"
    wrong_state = "wrong-state"

    with patch("app.routes.auth.exchange_code", new=AsyncMock()):
        resp = await client.get(
            "/api/v1/auth/google/callback",
            params={"code": "fake-code", "state": wrong_state},
            cookies={
                "nubi_oauth_state": state_val,
                "nubi_oauth_verifier": "some-verifier",
            },
        )

    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "auth_error=oauth_state_mismatch" in location


@pytest.mark.asyncio
async def test_google_callback_missing_state_cookie_redirects_with_error(client):
    """Missing state cookie → redirect with oauth_state_missing error."""
    resp = await client.get(
        "/api/v1/auth/google/callback",
        params={"code": "fake-code", "state": "some-state"},
        # No cookies provided
    )
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "auth_error=" in location


@pytest.mark.asyncio
async def test_google_callback_google_error_redirects(client):
    """If Google returns an error param, redirect to frontend with oauth_denied."""
    resp = await client.get(
        "/api/v1/auth/google/callback",
        params={"error": "access_denied"},
    )
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "auth_error=oauth_denied" in location


# ── Full happy-path integration ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_auth_flow(client):
    """End-to-end: register → login → /me → refresh → logout → refresh fails."""
    # Register
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": "lara@example.com", "password": "password123", "name": "Lara"},
    )
    assert reg.status_code == 201
    access1 = reg.json()["access_token"]
    refresh1 = _get_refresh_cookie(reg)

    # /me works
    me1 = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access1}"})
    assert me1.status_code == 200
    assert me1.json()["user"]["email"] == "lara@example.com"

    # Refresh rotates
    ref = await client.post("/api/v1/auth/refresh", cookies={"nubi_refresh": refresh1})
    assert ref.status_code == 200
    access2 = ref.json()["access_token"]
    refresh2 = _get_refresh_cookie(ref)
    assert access2  # new access token returned
    assert refresh2 != refresh1  # refresh cookie must rotate

    # /me with new access token
    me2 = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access2}"})
    assert me2.status_code == 200

    # Logout
    lo = await client.post("/api/v1/auth/logout", cookies={"nubi_refresh": refresh2})
    assert lo.status_code == 204

    # Refresh after logout must fail
    ref_fail = await client.post("/api/v1/auth/refresh", cookies={"nubi_refresh": refresh2})
    assert ref_fail.status_code == 401
