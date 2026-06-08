"""Tests for the avatar / asset-hosting feature (AvatarAssetsAgent).

Coverage
--------
1. ``ingest_avatar_from_url`` — happy path: downloads bytes, stores via
   storage client, returns a served URL.
2. ``ingest_avatar_from_url`` — absent-safe: bad HTTP status / network error
   returns None without raising.
3. ``ingest_avatar_from_url`` — size guard: rejects oversized responses.
4. ``ingest_avatar_from_url`` — content-type guard: rejects non-image
   responses.
5. Local asset serve route: GET /assets/avatars/{key} returns stored bytes.
6. Bunny mode: ``asset_url`` builds the pull-zone URL (no local API path).
7. PATCH /auth/me: updates name + avatar; external avatar_url is ingested.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Ensure test env is set BEFORE any app imports (conftest.py already handles
# this for the shared fixtures, but we make it explicit here too).
# ---------------------------------------------------------------------------

os.environ.setdefault("ENV_FILE", "/nonexistent/nubi-tests.env")
os.environ.setdefault("ASSET_SERVE_MODE", "local")


# ============================================================================
# Section 1 — Pure-unit tests for app.assets (no HTTP, no DB)
# ============================================================================


class TestAssetUrl:
    """``asset_url`` builds the correct URL for each serving mode."""

    def _clear_env(self) -> None:
        os.environ.pop("ASSET_SERVE_MODE", None)
        os.environ.pop("BUNNY_PULL_ZONE_URL", None)
        os.environ.pop("BUNNY_STORAGE_ZONE", None)
        os.environ.pop("BUNNY_STORAGE_API_KEY", None)

    def test_local_mode_returns_api_path(self) -> None:
        os.environ["ASSET_SERVE_MODE"] = "local"
        try:
            from app.assets.config import asset_url
            url = asset_url("avatars/user/abc123.jpg")
            assert url == "/api/v1/assets/avatars/user/abc123.jpg"
        finally:
            self._clear_env()

    def test_local_mode_strips_leading_slash(self) -> None:
        os.environ["ASSET_SERVE_MODE"] = "local"
        try:
            from app.assets.config import asset_url
            url = asset_url("/avatars/user/abc123.jpg")
            assert url.startswith("/api/v1/assets/avatars/"), url
        finally:
            self._clear_env()

    def test_bunny_mode_builds_pull_zone_url(self) -> None:
        os.environ["ASSET_SERVE_MODE"] = "bunny"
        os.environ["BUNNY_PULL_ZONE_URL"] = "https://cdn.example.b-cdn.net"
        try:
            from importlib import reload
            import app.assets.config as cfg
            reload(cfg)
            url = cfg.asset_url("avatars/user/abc123.jpg")
            assert url == "https://cdn.example.b-cdn.net/avatars/user/abc123.jpg", url
        finally:
            self._clear_env()
            # Re-import with local defaults for subsequent tests.
            import app.assets.config as cfg2
            from importlib import reload as rl
            rl(cfg2)

    def test_default_mode_is_local(self) -> None:
        self._clear_env()
        try:
            from app.assets.config import asset_url
            url = asset_url("avatars/x.png")
            assert url.startswith("/api/v1/assets/"), url
        finally:
            self._clear_env()


class TestIngestAvatarFromUrl:
    """``ingest_avatar_from_url`` unit tests with mocked httpx + storage."""

    def _mock_http_response(
        self,
        status_code: int = 200,
        content: bytes = b"fake-image-data",
        content_type: str = "image/jpeg",
    ) -> MagicMock:
        """Build a minimal mock that satisfies the httpx.AsyncClient contract."""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.content = content
        mock_resp.headers = {"content-type": content_type}
        return mock_resp

    @pytest.mark.asyncio
    async def test_happy_path_returns_served_url(self, tmp_path) -> None:
        """Successful ingest stores bytes and returns a served URL."""
        os.environ["ASSET_SERVE_MODE"] = "local"
        os.environ["LOCAL_STORAGE_ROOT"] = str(tmp_path)

        mock_resp = self._mock_http_response()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        try:
            with patch("httpx.AsyncClient", return_value=mock_client):
                from app.assets import service as svc
                result = await svc.ingest_avatar_from_url(
                    "https://example.com/avatar.jpg",
                    "user",
                    "user-abc-123",
                )

            assert result is not None, f"Expected a served URL, got None"
            assert "/avatars/user/user-abc-123/" in result
        finally:
            os.environ.pop("LOCAL_STORAGE_ROOT", None)
            os.environ.pop("ASSET_SERVE_MODE", None)

    @pytest.mark.asyncio
    async def test_bad_http_status_returns_none(self) -> None:
        """HTTP 404 → None, no raise."""
        mock_resp = self._mock_http_response(status_code=404)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from importlib import reload
            import app.assets.service as svc
            reload(svc)

            result = await svc.ingest_avatar_from_url(
                "https://example.com/missing.jpg",
                "user",
                "user-xyz",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_oversized_response_returns_none(self) -> None:
        """Response > 2 MiB → None."""
        huge = b"x" * (2 * 1024 * 1024 + 1)
        mock_resp = self._mock_http_response(content=huge)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from importlib import reload
            import app.assets.service as svc
            reload(svc)

            result = await svc.ingest_avatar_from_url(
                "https://example.com/huge.jpg",
                "user",
                "user-xyz",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_disallowed_content_type_returns_none(self) -> None:
        """Non-image content type → None."""
        mock_resp = self._mock_http_response(content_type="application/octet-stream")
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from importlib import reload
            import app.assets.service as svc
            reload(svc)

            result = await svc.ingest_avatar_from_url(
                "https://example.com/file.bin",
                "user",
                "user-xyz",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self) -> None:
        """Network failure → None, no raise."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            from importlib import reload
            import app.assets.service as svc
            reload(svc)

            result = await svc.ingest_avatar_from_url(
                "https://example.com/avatar.jpg",
                "user",
                "user-xyz",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_non_http_url_returns_none(self) -> None:
        """Non-HTTP URL is rejected immediately (no network call)."""
        from importlib import reload
        import app.assets.service as svc
        reload(svc)

        result = await svc.ingest_avatar_from_url("data:image/png;base64,abc", "user", "u")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_url_returns_none(self) -> None:
        """Empty URL is rejected immediately."""
        from importlib import reload
        import app.assets.service as svc
        reload(svc)

        result = await svc.ingest_avatar_from_url("", "user", "u")
        assert result is None

    @pytest.mark.asyncio
    async def test_same_url_derives_same_key(self, tmp_path) -> None:
        """The same URL always maps to the same storage key (idempotent)."""
        from app.assets.service import _derive_key

        key1 = _derive_key("https://example.com/a.jpg", "user", "uid1", ".jpg")
        key2 = _derive_key("https://example.com/a.jpg", "user", "uid1", ".jpg")
        assert key1 == key2

    @pytest.mark.asyncio
    async def test_different_urls_derive_different_keys(self) -> None:
        """Different URLs produce different keys."""
        from app.assets.service import _derive_key

        k1 = _derive_key("https://a.com/x.jpg", "user", "uid1", ".jpg")
        k2 = _derive_key("https://b.com/x.jpg", "user", "uid1", ".jpg")
        assert k1 != k2


# ============================================================================
# Section 2 — HTTP tests against the in-memory fake DB (via conftest fixtures)
# ============================================================================

# ---------------------------------------------------------------------------
# Extended fake DB for avatar / PATCH /me tests
# ---------------------------------------------------------------------------

class _ExtendedFakeDB:
    """Minimal in-memory store for the assets-route HTTP tests.

    Wraps the conftest FakeDB and adds support for the dynamic UPDATE that
    PATCH /auth/me generates (arbitrary SET clauses for name / avatar_url).
    """

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self.users.clear()
        self.sessions.clear()

    # -- minimal helpers reused from conftest FakeDB --------------------------

    def _user_by_email(self, email: str) -> dict[str, Any] | None:
        for row in self.users.values():
            if str(row["email"]).lower() == email.lower():
                return row
        return None

    def _user_by_id(self, uid: str) -> dict[str, Any] | None:
        clean = str(uid).replace("::uuid", "").strip()
        return self.users.get(clean)

    def _session_by_token_hash(self, th: str) -> dict[str, Any] | None:
        for r in self.sessions.values():
            if r["token_hash"] == th:
                return r
        return None

    def _session_by_parent_id(self, pid: str) -> dict[str, Any] | None:
        for r in self.sessions.values():
            if str(r.get("parent_id") or "") == pid:
                return r
        return None

    # -- query dispatcher -----------------------------------------------------

    def _do_fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper().strip()
        upper = q

        if "FROM USERS" in upper or "UPDATE USERS" in upper:
            if "WHERE ID = " in q or "WHERE ID =" in q or "WHERE ID=$" in q.replace(" ", ""):
                return self._user_by_id(str(args[0]))
            if "WHERE EMAIL = " in q or "WHERE EMAIL =" in q:
                return self._user_by_email(str(args[0]))

        if "FROM SESSIONS" in upper:
            if "WHERE TOKEN_HASH" in q:
                return self._session_by_token_hash(str(args[0]))
            if "WHERE PARENT_ID" in q:
                return self._session_by_parent_id(str(args[0]))

        if "SELECT 1" in q and "FROM" not in q:
            return {"ping": 1}

        return None

    def _do_execute(self, query: str, *args: Any) -> str:  # noqa: PLR0912
        q = query.upper().strip()

        if q.startswith("INSERT") and "INTO USERS" in q:
            row: dict[str, Any] = {
                "id": str(args[0]),
                "email": str(args[1]).lower(),
                "created_at": datetime.now(tz=timezone.utc),
                "updated_at": datetime.now(tz=timezone.utc),
            }
            if len(args) == 4:
                row["password_hash"] = args[2]
                row["name"] = args[3]
                row["avatar_url"] = None
                row["email_verified"] = False
            elif len(args) == 5:
                row["password_hash"] = None
                row["name"] = args[2]
                row["avatar_url"] = args[3]
                row["email_verified"] = bool(args[4])
            else:
                row["password_hash"] = args[2] if len(args) > 2 else None
                row["name"] = args[3] if len(args) > 3 else None
                row["avatar_url"] = None
                row["email_verified"] = False
            self.users[row["id"]] = row
            return "INSERT 0 1"

        if q.startswith("INSERT") and "INTO SESSIONS" in q:
            r2 = {
                "id": str(args[0]),
                "user_id": str(args[1]),
                "token_hash": str(args[2]),
                "family_id": str(args[3]),
                "parent_id": str(args[4]) if args[4] is not None else None,
                "expires_at": args[5],
                "revoked_at": None,
                "user_agent": args[6] if len(args) > 6 else None,
                "ip": args[7] if len(args) > 7 else None,
                "created_at": datetime.now(tz=timezone.utc),
            }
            self.sessions[r2["id"]] = r2
            return "INSERT 0 1"

        if q.startswith("INSERT") and "INTO ORGS" in q:
            return "INSERT 0 1"

        if q.startswith("INSERT") and "INTO ORG_MEMBERS" in q:
            return "INSERT 0 1"

        if q.startswith("INSERT") and "INTO OAUTH_ACCOUNTS" in q:
            return "INSERT 0 1"

        if q.startswith("UPDATE SESSIONS") and "REVOKED_AT" in q:
            if "WHERE ID=" in q.replace(" ", "") or "WHERE ID =" in q:
                sid = str(args[0])
                if sid in self.sessions:
                    self.sessions[sid]["revoked_at"] = datetime.now(tz=timezone.utc)
                return "UPDATE 1"
            if "WHERE FAMILY_ID" in q:
                fid = str(args[0])
                count = 0
                for s in self.sessions.values():
                    if str(s["family_id"]) == fid and s["revoked_at"] is None:
                        s["revoked_at"] = datetime.now(tz=timezone.utc)
                        count += 1
                return f"UPDATE {count}"

        # Generic UPDATE users SET <col>=... WHERE id=...
        # Handles PATCH /me which generates dynamic SET clauses.
        if q.startswith("UPDATE USERS"):
            # last arg is the user_id (before ::uuid suffix in placeholder)
            user_id = str(args[-1]).replace("::uuid", "").strip()
            user = self.users.get(user_id)
            if user is not None:
                # Positional args: col1_val, col2_val, ..., user_id
                # Parse column names from SET clause.
                import re  # noqa: PLC0415
                set_match = re.search(r"SET\s+(.+?)\s+WHERE", query, re.IGNORECASE | re.DOTALL)
                if set_match:
                    set_clause = set_match.group(1)
                    # Extract col = $N assignments (skip updated_at = now())
                    col_assign = re.findall(r"(\w+)\s*=\s*\$(\d+)", set_clause)
                    for col, idx in col_assign:
                        col = col.lower()
                        val_idx = int(idx) - 1  # 0-based
                        if val_idx < len(args) - 1:  # last arg is user_id
                            user[col] = args[val_idx]
                    user["updated_at"] = datetime.now(tz=timezone.utc)
            return "UPDATE 1"

        return "OK"

    async def fake_fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self._do_fetchrow(query, *args)

    async def fake_execute(self, query: str, *args: Any) -> str:
        return self._do_execute(query, *args)

    async def fake_fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        return []

    @asynccontextmanager
    async def fake_get_connection(self):
        class _FakeConn:
            def __init__(self, db_: "_ExtendedFakeDB") -> None:
                self._db = db_

            def transaction(self):
                class _FakeTx:
                    async def __aenter__(self_):
                        return self_
                    async def __aexit__(self_, *a):
                        return False
                return _FakeTx()

            async def fetchrow(self_, q: str, *a: Any) -> dict[str, Any] | None:
                return self._do_fetchrow(q, *a)

            async def fetch(self_, q: str, *a: Any) -> list:
                return []

            async def execute(self_, q: str, *a: Any) -> str:
                return self._do_execute(q, *a)

        yield _FakeConn(self)


_ext_db = _ExtendedFakeDB()


@pytest_asyncio.fixture
async def assets_app():
    """FastAPI app fixture that also patches app.assets.routes DB helpers."""
    _ext_db.reset()
    patches = [
        patch("app.db.fetchrow",              side_effect=_ext_db.fake_fetchrow),
        patch("app.db.fetch",                 side_effect=_ext_db.fake_fetch),
        patch("app.db.execute",               side_effect=_ext_db.fake_execute),
        patch("app.db.get_connection",        new=_ext_db.fake_get_connection),
        patch("app.routes.auth.fetchrow",     side_effect=_ext_db.fake_fetchrow),
        patch("app.routes.auth.execute",      side_effect=_ext_db.fake_execute),
        patch("app.assets.routes.fetchrow",   side_effect=_ext_db.fake_fetchrow),
        patch("app.assets.routes.execute",    side_effect=_ext_db.fake_execute),
        patch("app.auth.sessions.fetchrow",   side_effect=_ext_db.fake_fetchrow),
        patch("app.auth.sessions.execute",    side_effect=_ext_db.fake_execute),
        patch("app.auth.sessions.get_connection", new=_ext_db.fake_get_connection),
        patch("app.auth.deps.fetchrow",       side_effect=_ext_db.fake_fetchrow),
        patch("app.db.init_db",               new=AsyncMock()),
        patch("app.db.close_db",              new=AsyncMock()),
    ]
    for p in patches:
        p.start()
    try:
        import main as main_module  # noqa: PLC0415
        _app = main_module.create_app()
        yield _app
    finally:
        for p in patches:
            p.stop()
        _ext_db.reset()


@pytest_asyncio.fixture
async def assets_client(assets_app):
    """HTTPX client backed by the extended fake DB app."""
    transport = ASGITransport(app=assets_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_local_serve_route_returns_bytes(assets_client, tmp_path) -> None:
    """GET /assets/avatars/{key} → 200 with image bytes when asset exists.

    The route is /assets/avatars/{key:path} so ``key`` captured from the path
    does NOT include the ``avatars/`` prefix.  Storage key == path key.
    """
    os.environ["ASSET_SERVE_MODE"] = "local"
    os.environ["LOCAL_STORAGE_ROOT"] = str(tmp_path)

    # Write a fake image; key relative to storage root = user/u123/deadbeef.jpg
    target = tmp_path / "user" / "u123"
    target.mkdir(parents=True, exist_ok=True)
    (target / "deadbeef.jpg").write_bytes(b"FAKE_JPEG_DATA")

    resp = await assets_client.get("/api/v1/assets/avatars/user/u123/deadbeef.jpg")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"FAKE_JPEG_DATA"

    os.environ.pop("LOCAL_STORAGE_ROOT", None)
    os.environ.pop("ASSET_SERVE_MODE", None)


@pytest.mark.asyncio
async def test_local_serve_route_404_for_missing(assets_client, tmp_path) -> None:
    """GET /assets/avatars/{key} → 404 when asset does not exist."""
    os.environ["ASSET_SERVE_MODE"] = "local"
    os.environ["LOCAL_STORAGE_ROOT"] = str(tmp_path)

    resp = await assets_client.get("/api/v1/assets/avatars/user/nobody/missing.jpg")
    assert resp.status_code == 404

    os.environ.pop("LOCAL_STORAGE_ROOT", None)
    os.environ.pop("ASSET_SERVE_MODE", None)


@pytest.mark.asyncio
async def test_patch_me_updates_name(assets_client) -> None:
    """PATCH /auth/me with {name} updates the user's display name."""
    reg = await assets_client.post(
        "/api/v1/auth/register",
        json={"email": "patchme@example.com", "password": "password123", "name": "Old Name"},
    )
    assert reg.status_code == 201, reg.text
    token = reg.json()["access_token"]

    resp = await assets_client.patch(
        "/api/v1/auth/me",
        json={"name": "New Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["name"] == "New Name"


@pytest.mark.asyncio
async def test_patch_me_requires_auth(assets_client) -> None:
    """PATCH /auth/me without a token → 401."""
    resp = await assets_client.patch("/api/v1/auth/me", json={"name": "Whatever"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_patch_me_ingests_external_avatar(assets_client) -> None:
    """PATCH /auth/me with an external avatar_url triggers ingest."""
    reg = await assets_client.post(
        "/api/v1/auth/register",
        json={"email": "avatar@example.com", "password": "password123"},
    )
    token = reg.json()["access_token"]

    served_url = "/api/v1/assets/avatars/user/uid/abc.jpg"

    with patch(
        "app.assets.routes.ingest_avatar_from_url",
        new=AsyncMock(return_value=served_url),
    ):
        resp = await assets_client.patch(
            "/api/v1/auth/me",
            json={"avatar_url": "https://example.com/photo.jpg"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    # The returned avatar_url should be the served URL (not the Google URL).
    assert resp.json()["user"]["avatar_url"] == served_url


@pytest.mark.asyncio
async def test_patch_me_local_avatar_url_stored_as_is(assets_client) -> None:
    """PATCH /auth/me with a local (non-HTTP) avatar_url stores it directly."""
    reg = await assets_client.post(
        "/api/v1/auth/register",
        json={"email": "localav@example.com", "password": "password123"},
    )
    token = reg.json()["access_token"]

    local_path = "/api/v1/assets/avatars/user/uid/local.jpg"

    resp = await assets_client.patch(
        "/api/v1/auth/me",
        json={"avatar_url": local_path},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["avatar_url"] == local_path
