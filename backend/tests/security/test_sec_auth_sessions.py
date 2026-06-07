"""Attack class 7: Auth session hardening.

Covers
------
7a. Refresh token rotation: a rotated (consumed) token is rejected on reuse
7b. Refresh token reuse triggers family revocation (all family tokens revoked)
7c. An unknown refresh token is rejected → AppError("refresh_reuse", 401)
7d. An expired refresh token is rejected
7e. Access token after logout is still accepted (stateless JWT — documented gap)
7f. Refresh token is hashed in storage (raw token never stored)
7g. A new raw token is issued on each successful rotation (not the same token)
7h. Family revocation: after a sibling token is reused, a valid sibling is also denied
7i. SECURITY GAP: access token cannot be invalidated after logout (stateless JWT).
    Documented as xfail because the JWT does not use a blocklist.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio

# ── env bootstrap ─────────────────────────────────────────────────────────────
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

from tests.security.conftest_helpers import mint_access_token  # noqa: E402


# ---------------------------------------------------------------------------
# Fake session store (minimal, for testing sessions.py directly)
# ---------------------------------------------------------------------------

class _SessionStore:
    """In-memory session store that mimics the DB schema."""

    def __init__(self):
        self._sessions: dict[str, dict[str, Any]] = {}

    def reset(self):
        self._sessions.clear()

    def _hash(self, raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    def get_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        for row in self._sessions.values():
            if row["token_hash"] == token_hash:
                return row
        return None

    def get_by_parent_id(self, parent_id: str) -> dict[str, Any] | None:
        for row in self._sessions.values():
            if str(row.get("parent_id") or "") == str(parent_id):
                return row
        return None

    def insert(self, row: dict[str, Any]) -> None:
        self._sessions[row["id"]] = dict(row)

    def mark_revoked(self, session_id: str) -> None:
        if session_id in self._sessions:
            self._sessions[session_id]["revoked_at"] = datetime.now(tz=timezone.utc)

    def revoke_family(self, family_id: str) -> None:
        for row in self._sessions.values():
            if str(row["family_id"]) == str(family_id) and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(tz=timezone.utc)


_store = _SessionStore()


# ---------------------------------------------------------------------------
# Fake DB wiring for sessions module
# ---------------------------------------------------------------------------

async def _fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
    q = query.upper().strip()
    if "FROM SESSIONS" in q:
        if "WHERE TOKEN_HASH" in q:
            return _store.get_by_hash(str(args[0]))
        if "WHERE PARENT_ID" in q:
            return _store.get_by_parent_id(str(args[0]))
    return None


async def _fake_execute(query: str, *args: Any) -> str:
    # Normalise whitespace to single spaces for reliable matching.
    q = " ".join(query.upper().split())
    if q.startswith("INSERT") and "SESSIONS" in q:
        row = {
            "id": str(args[0]),
            "user_id": str(args[1]),
            "token_hash": str(args[2]),
            "family_id": str(args[3]),
            "parent_id": str(args[4]) if args[4] is not None else None,
            "expires_at": args[5],
            "revoked_at": None,
            "user_agent": args[6] if len(args) > 6 else None,
            "ip": args[7] if len(args) > 7 else None,
        }
        _store.insert(row)
        return "INSERT 0 1"
    if "UPDATE SESSIONS" in q and "REVOKED_AT" in q:
        if "WHERE ID = " in q or "WHERE ID =" in q or "WHERE ID=$1" in q.replace(" ", ""):
            _store.mark_revoked(str(args[0]))
            return "UPDATE 1"
        if "WHERE FAMILY_ID" in q:
            _store.revoke_family(str(args[0]))
            return "UPDATE 1"
    return "OK"


class _FakeConn:
    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return await _fake_fetchrow(query, *args)

    async def fetch(self, *a, **kw) -> list:
        return []

    async def execute(self, query: str, *args: Any) -> str:
        return await _fake_execute(query, *args)

    def transaction(self):
        return _FakeTx()


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@asynccontextmanager
async def _fake_get_connection():
    yield _FakeConn()


# ---------------------------------------------------------------------------
# Session test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_store():
    _store.reset()
    yield
    _store.reset()


@pytest.fixture
def _session_patches():
    return [
        patch("app.auth.sessions.fetchrow", side_effect=_fake_fetchrow),
        patch("app.auth.sessions.execute", side_effect=_fake_execute),
        patch("app.auth.sessions.get_connection", new=_fake_get_connection),
    ]


# ===========================================================================
# 7f. Refresh token is hashed in storage (raw token never stored)
# ===========================================================================

@pytest.mark.asyncio
async def test_refresh_token_hash_stored_not_raw(_session_patches):
    """The raw refresh token must NOT appear in the session row."""
    for p in _session_patches:
        p.start()
    try:
        from app.auth.sessions import issue_refresh

        raw, expires = await issue_refresh(
            user_id="test-user-1",
            user_agent="pytest",
            ip="127.0.0.1",
        )

        # The raw token must appear in the returned value.
        assert raw is not None and len(raw) > 10

        # The session store must NOT contain the raw token.
        for row in _store._sessions.values():
            assert row["token_hash"] != raw, (
                "SECURITY FAILURE: raw refresh token stored instead of hash"
            )
            # The stored hash must equal SHA-256 of the raw token.
            expected_hash = hashlib.sha256(raw.encode()).hexdigest()
            assert row["token_hash"] == expected_hash, (
                "SECURITY FAILURE: stored hash does not match SHA-256 of raw token"
            )
    finally:
        for p in _session_patches:
            p.stop()


# ===========================================================================
# 7a. Rotated refresh token rejected on reuse → AppError("refresh_reuse", 401)
# ===========================================================================

@pytest.mark.asyncio
async def test_refresh_token_reuse_rejected(_session_patches):
    """A refresh token that has already been rotated is rejected on second use."""
    for p in _session_patches:
        p.start()
    try:
        from app.auth.sessions import issue_refresh, rotate_refresh
        from app.errors import AppError

        # Issue a fresh refresh token.
        raw, _ = await issue_refresh("user-reuse-1", user_agent="ua", ip="1.2.3.4")

        # First rotation succeeds.
        new_raw, user_id, _ = await rotate_refresh(raw, user_agent="ua", ip="1.2.3.4")
        assert user_id == "user-reuse-1"
        assert new_raw != raw

        # Second rotation using the ORIGINAL (now-consumed) token must fail.
        with pytest.raises(AppError) as exc_info:
            await rotate_refresh(raw, user_agent="ua", ip="1.2.3.4")

        err = exc_info.value
        assert err.code == "refresh_reuse", (
            f"SECURITY FAILURE: reused refresh token accepted with code={err.code!r}"
        )
        assert err.status == 401
    finally:
        for p in _session_patches:
            p.stop()


# ===========================================================================
# 7g. New token issued on each rotation (not the same token)
# ===========================================================================

@pytest.mark.asyncio
async def test_refresh_token_rotated_to_new_value(_session_patches):
    """Each rotation produces a new, different raw token."""
    for p in _session_patches:
        p.start()
    try:
        from app.auth.sessions import issue_refresh, rotate_refresh

        raw1, _ = await issue_refresh("user-rotate-1", user_agent="ua", ip="1.2.3.4")
        raw2, user_id, _ = await rotate_refresh(raw1)
        raw3, _, _ = await rotate_refresh(raw2)

        assert raw1 != raw2, "SECURITY FAILURE: rotation produced same token"
        assert raw2 != raw3, "SECURITY FAILURE: rotation produced same token"
        assert raw1 != raw3
    finally:
        for p in _session_patches:
            p.stop()


# ===========================================================================
# 7b. Refresh token reuse triggers family revocation
# ===========================================================================

@pytest.mark.asyncio
async def test_refresh_reuse_revokes_entire_family(_session_patches):
    """When a reused token is detected, all family tokens are revoked."""
    for p in _session_patches:
        p.start()
    try:
        from app.auth.sessions import issue_refresh, rotate_refresh
        from app.errors import AppError

        # Issue and rotate to build a family of 3 tokens:
        # raw1 → raw2 → raw3 (current).
        raw1, _ = await issue_refresh("user-family-1", user_agent="ua", ip="1.2.3.4")
        raw2, _, _ = await rotate_refresh(raw1)
        raw3, _, _ = await rotate_refresh(raw2)

        # Capture the family_id from the store.
        first_hash = hashlib.sha256(raw1.encode()).hexdigest()
        row = _store.get_by_hash(first_hash)
        family_id = row["family_id"]

        # Reuse the already-consumed raw1 — this should revoke the entire family.
        with pytest.raises(AppError) as exc_info:
            await rotate_refresh(raw1)
        assert exc_info.value.code == "refresh_reuse"

        # ALL tokens in the family should now be revoked.
        for row in _store._sessions.values():
            if str(row["family_id"]) == str(family_id):
                assert row["revoked_at"] is not None, (
                    f"SECURITY FAILURE: family member not revoked after reuse detection. "
                    f"Session id={row['id']}"
                )
    finally:
        for p in _session_patches:
            p.stop()


# ===========================================================================
# 7c. Unknown refresh token → rejected
# ===========================================================================

@pytest.mark.asyncio
async def test_unknown_refresh_token_rejected(_session_patches):
    """A completely unknown refresh token → AppError("refresh_reuse", 401)."""
    for p in _session_patches:
        p.start()
    try:
        from app.auth.sessions import rotate_refresh
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            await rotate_refresh("definitely-not-a-valid-refresh-token-xyz")
        assert exc_info.value.code == "refresh_reuse"
        assert exc_info.value.status == 401
    finally:
        for p in _session_patches:
            p.stop()


# ===========================================================================
# 7d. Expired refresh token is rejected
# ===========================================================================

@pytest.mark.asyncio
async def test_expired_refresh_token_rejected(_session_patches):
    """A refresh token whose expires_at is in the past is rejected."""
    for p in _session_patches:
        p.start()
    try:
        from app.auth.sessions import rotate_refresh
        from app.errors import AppError
        import secrets
        import hashlib

        # Manually insert an expired session into the store.
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        expired_session = {
            "id": "expired-session-001",
            "user_id": "user-expired",
            "token_hash": token_hash,
            "family_id": "family-expired-001",
            "parent_id": None,
            "expires_at": datetime.now(tz=timezone.utc) - timedelta(days=1),  # expired
            "revoked_at": None,
            "user_agent": None,
            "ip": None,
        }
        _store.insert(expired_session)

        with pytest.raises(AppError) as exc_info:
            await rotate_refresh(raw)
        err = exc_info.value
        assert err.status == 401, (
            f"SECURITY FAILURE: expired refresh token accepted (status={err.status})"
        )
    finally:
        for p in _session_patches:
            p.stop()


# ===========================================================================
# 7e & 7i. Access token after logout: denylist closes the stateless-JWT gap
# ===========================================================================

def _make_logout_patches():
    """Patches needed for the logout / denylist integration tests."""
    return [
        patch("app.db.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=None)),
    ]


@pytest.fixture(autouse=True)
def _reset_denylist():
    """Reset the denylist singleton to a fresh InMemory instance for each test."""
    from app.auth.denylist import InMemoryTokenDenylist, set_token_denylist_for_tests
    store = InMemoryTokenDenylist()
    set_token_denylist_for_tests(store)
    yield store
    set_token_denylist_for_tests(None)


@pytest.mark.asyncio
async def test_access_token_invalid_after_logout(_reset_denylist):
    """Access token is rejected immediately after logout (denylist implemented).

    The jti of the bearer token sent to POST /auth/logout is added to the
    denylist.  Any subsequent request using the same token → 401.
    """
    patches = _make_logout_patches()
    for p in patches:
        p.start()
    try:
        import main as main_module
        _app = main_module.create_app()

        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=_app),
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            token = mint_access_token("user-post-logout")

            # Logout — sends the access token in the Authorization header so
            # the logout handler can extract and denylist its jti.
            logout_resp = await ac.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert logout_resp.status_code == 204

            # The same access token must now be rejected.
            resp = await ac.post(
                "/api/v1/query",
                json={"sql": "SELECT 1"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 401, (
                f"SECURITY FAILURE: access token still accepted after logout "
                f"(status {resp.status_code}).  Denylist not working."
            )
    finally:
        for p in patches:
            p.stop()


# ===========================================================================
# 7h. After sibling reuse: valid sibling also denied
# ===========================================================================

@pytest.mark.asyncio
async def test_valid_sibling_denied_after_family_revocation(_session_patches):
    """After a reuse is detected and the family revoked, a 'valid' sibling token
    (one that was never consumed) is also denied.

    Family structure:
      raw1 (root) → raw2 (child) → raw3 (grandchild, never rotated yet)
    Reuse raw1 → family revoked.
    Now try to rotate raw3 → must fail (revoked).
    """
    for p in _session_patches:
        p.start()
    try:
        from app.auth.sessions import issue_refresh, rotate_refresh
        from app.errors import AppError

        raw1, _ = await issue_refresh("user-sibling-1")
        raw2, _, _ = await rotate_refresh(raw1)
        # raw3 is issued fresh (a second device login, same family would not be
        # realistic here; for this test we simulate by issuing a child of raw2).
        raw3, _, _ = await rotate_refresh(raw2)

        # Now reuse raw1 (already consumed).
        with pytest.raises(AppError) as exc_info:
            await rotate_refresh(raw1)
        assert exc_info.value.code == "refresh_reuse"

        # raw3 was never rotated but its family is revoked.
        # Attempting to use it must now fail.
        with pytest.raises(AppError) as exc_info2:
            await rotate_refresh(raw3)
        assert exc_info2.value.status == 401, (
            f"SECURITY FAILURE: valid sibling token accepted after family revocation "
            f"(status={exc_info2.value.status})"
        )
    finally:
        for p in _session_patches:
            p.stop()


# ===========================================================================
# Denylist unit tests (InMemoryTokenDenylist)
# ===========================================================================

@pytest.mark.asyncio
async def test_non_revoked_token_not_in_denylist(_reset_denylist):
    """A token that was never revoked is NOT reported as revoked."""
    from app.auth.denylist import InMemoryTokenDenylist
    store: InMemoryTokenDenylist = _reset_denylist

    from app.auth.jwt import decode_access_token, mint_access_token as _mint
    token = _mint("user-not-revoked")
    claims = decode_access_token(token)

    assert not await store.is_revoked(claims["jti"]), (
        "FAILURE: freshly minted token incorrectly reported as revoked"
    )


@pytest.mark.asyncio
async def test_revoke_is_jti_scoped(_reset_denylist):
    """Revoking one token does not affect another token for the same user."""
    from app.auth.denylist import InMemoryTokenDenylist
    from app.auth.jwt import decode_access_token, mint_access_token as _mint
    from datetime import datetime, timezone
    store: InMemoryTokenDenylist = _reset_denylist

    token_a = _mint("user-scope-test")
    token_b = _mint("user-scope-test")  # same user, different jti
    claims_a = decode_access_token(token_a)
    claims_b = decode_access_token(token_b)

    # Sanity: distinct jti values.
    assert claims_a["jti"] != claims_b["jti"], "Test setup error: same jti minted twice"

    raw_exp = claims_a["exp"]
    if isinstance(raw_exp, datetime):
        exp_dt = raw_exp if raw_exp.tzinfo else raw_exp.replace(tzinfo=timezone.utc)
    else:
        exp_dt = datetime.fromtimestamp(int(raw_exp), tz=timezone.utc)

    # Revoke only token A.
    await store.revoke(claims_a["jti"], exp_dt)

    assert await store.is_revoked(claims_a["jti"]), "FAILURE: token_a should be revoked"
    assert not await store.is_revoked(claims_b["jti"]), (
        "SECURITY FAILURE: revoking token_a also revoked token_b (wrong jti scoping)"
    )


@pytest.mark.asyncio
async def test_purge_expired_removes_stale_entries(_reset_denylist):
    """purge_expired() removes entries whose expires_at is in the past."""
    from app.auth.denylist import InMemoryTokenDenylist
    from datetime import datetime, timedelta, timezone
    store: InMemoryTokenDenylist = _reset_denylist

    now = datetime.now(tz=timezone.utc)
    past = now - timedelta(seconds=1)
    future = now + timedelta(minutes=15)

    await store.revoke("expired-jti-001", past)
    await store.revoke("live-jti-001", future)

    assert await store.is_revoked("expired-jti-001")
    assert await store.is_revoked("live-jti-001")

    purged = await store.purge_expired()
    assert purged == 1, f"Expected 1 purged row, got {purged}"

    assert not await store.is_revoked("expired-jti-001"), (
        "FAILURE: expired entry should have been purged"
    )
    assert await store.is_revoked("live-jti-001"), (
        "FAILURE: live entry should not have been purged"
    )


@pytest.mark.asyncio
async def test_access_token_carries_jti():
    """Every minted access token must include a non-empty jti claim."""
    from app.auth.jwt import decode_access_token, mint_access_token as _mint

    token = _mint("user-jti-check")
    claims = decode_access_token(token)

    assert "jti" in claims, "FAILURE: access token missing jti claim"
    assert isinstance(claims["jti"], str) and len(claims["jti"]) > 0, (
        "FAILURE: jti claim is empty or not a string"
    )


@pytest.mark.asyncio
async def test_two_tokens_have_distinct_jtis():
    """Each minted access token must have a unique jti."""
    from app.auth.jwt import decode_access_token, mint_access_token as _mint

    claims_1 = decode_access_token(_mint("user-dup-jti"))
    claims_2 = decode_access_token(_mint("user-dup-jti"))

    assert claims_1["jti"] != claims_2["jti"], (
        "SECURITY FAILURE: two tokens for the same user share the same jti"
    )
