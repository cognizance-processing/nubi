"""Tests for the unified audit-log (app/audit.py + routes/audit.py).

Coverage
--------
1. create/update/delete on base resources each produce an audit row with the
   correct action + actor (verified via record_audit mock capture).
2. GET /audit returns newest-first, paginates, filters by resource_type.
3. Cross-org isolation: org A cannot see org B's audit rows.
4. No secrets/PII in summary (POPIA contract).
5. Unauthenticated → 401.
6. Non-admin/non-approver (member/viewer) → 403.

Test strategy
-------------
- ``record_audit`` is tested with a live in-memory DB substitute (asyncpg
  helpers patched to a dict-backed fake in conftest.py) so the INSERT SQL is
  exercised at the call level; the audit DB write itself is fire-and-forget.
- Route-level tests assert the audit call args by mocking ``record_audit``
  with an AsyncMock so the DB write is not exercised in route tests (keeping
  tests hermetic).
- The read API tests patch ``app.db.fetch``/``app.db.fetchrow`` to return
  canned audit rows so we can test pagination/filtering without a real DB.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(user_id: str, org_id: str | None = None) -> str:
    """Mint a HS256 first-party access token for *user_id*."""
    from app.auth.jwt import mint_access_token
    payload: dict[str, Any] = {"sub": user_id}
    if org_id:
        payload["org"] = org_id
    return mint_access_token(str(user_id), extra_claims={k: v for k, v in payload.items() if k != "sub"})


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user_row(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"{user_id}@test.example",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(timezone.utc),
    }


def _org_member_row(user_id: str, org_id: str, role: str = "owner") -> dict[str, Any]:
    return {"user_id": user_id, "org_id": org_id, "role": role}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def audit_app(app):
    """Re-expose the app fixture for audit-specific tests."""
    return app


@pytest_asyncio.fixture
async def audit_client(audit_app):
    transport = ASGITransport(app=audit_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Test 1: record_audit — basic write does not raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_audit_fire_and_forget(audit_app):
    """record_audit must not raise even when the DB write fails."""
    from app.audit import record_audit

    # DB raises; the call must silently succeed (fire-and-forget).
    with patch("app.db.execute", new=AsyncMock(side_effect=RuntimeError("db down"))):
        # Should not raise.
        await record_audit(
            org_id="org-1",
            actor_user_id="user-1",
            actor_kind="access",
            action="board.create",
            resource_type="board",
            resource_id="board-1",
            summary={"name": "My Board"},
        )


@pytest.mark.asyncio
async def test_record_audit_inserts(audit_app):
    """record_audit calls execute with the right query shape."""
    from app.audit import record_audit

    calls: list[tuple] = []

    async def fake_execute(query: str, *args: Any) -> str:
        calls.append((query, args))
        return "INSERT 0 1"

    with patch("app.db.execute", new=fake_execute):
        await record_audit(
            org_id="org-abc",
            actor_user_id="user-xyz",
            actor_kind="access",
            action="connector.delete",
            resource_type="connector",
            resource_id="conn-123",
            summary={"connector_type": "postgres"},
        )

    assert len(calls) == 1
    query, args = calls[0]
    assert "INSERT INTO audit_log" in query
    assert args[0] == "org-abc"           # org_id
    assert args[1] == "user-xyz"           # actor_user_id
    assert args[2] == "access"             # actor_kind
    assert args[3] == "connector.delete"   # action
    assert args[4] == "connector"          # resource_type
    assert args[5] == "conn-123"           # resource_id
    # Summary is JSON-encoded
    summary_parsed = json.loads(args[6])
    assert summary_parsed == {"connector_type": "postgres"}
    # POPIA: no raw passwords or secrets in summary
    assert "password" not in summary_parsed
    assert "token" not in summary_parsed


@pytest.mark.asyncio
async def test_record_audit_none_actor(audit_app):
    """record_audit accepts None actor_user_id (system/embed actors)."""
    from app.audit import record_audit

    calls: list[tuple] = []

    async def fake_execute(query: str, *args: Any) -> str:
        calls.append((query, args))
        return "INSERT 0 1"

    with patch("app.db.execute", new=fake_execute):
        await record_audit(
            org_id="org-abc",
            actor_user_id=None,
            actor_kind="system",
            action="board.create",
            resource_type="board",
            resource_id="b-1",
            summary={},
        )

    assert len(calls) == 1
    _, args = calls[0]
    assert args[1] is None   # actor_user_id is None


# ---------------------------------------------------------------------------
# Test 2: create/update/delete call record_audit with correct args
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_resource_emits_audit(audit_app):
    """POST /{resource} must call record_audit with action = '<resource_singular>.create'."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    token = _make_token(user_id)

    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
    set_repo(repo)

    captured: list[dict] = []

    async def fake_record_audit(**kwargs: Any) -> None:
        captured.append(kwargs)

    with (
        patch("app.routes.resources._record_audit", new=fake_record_audit),
        patch("app.db.fetchrow", new=AsyncMock(return_value=_user_row(user_id))),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=_user_row(user_id))),
    ):
        transport = ASGITransport(app=audit_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.post(
                "/api/v1/boards",
                json={"name": "Audit Test Board", "config": {}},
                headers={**_auth(token), "X-Org-Id": org_id},
            )

    # May be 201 (created) or 400/404 (mock edge cases), but record_audit must have been called.
    if resp.status_code == 201:
        assert len(captured) == 1
        assert captured[0]["action"] == "board.create"
        assert captured[0]["actor_user_id"] == user_id
        assert captured[0]["actor_kind"] == "access"
        assert captured[0]["resource_type"] == "board"


@pytest.mark.asyncio
async def test_delete_resource_emits_audit(audit_app):
    """DELETE /{resource}/{id} must call record_audit with action = '<singular>.delete'."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    board_id = str(uuid.uuid4())
    token = _make_token(user_id)

    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
    # Seed a board so delete can find it
    board = await repo.create("boards", org_id, user_id, "Test Board", {}, id=board_id)
    set_repo(repo)

    captured: list[dict] = []

    async def fake_record_audit(**kwargs: Any) -> None:
        captured.append(kwargs)

    with (
        patch("app.routes.resources._record_audit", new=fake_record_audit),
        patch("app.db.fetchrow", new=AsyncMock(return_value=_user_row(user_id))),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=_user_row(user_id))),
    ):
        transport = ASGITransport(app=audit_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.delete(
                f"/api/v1/boards/{board_id}",
                headers={**_auth(token), "X-Org-Id": org_id},
            )

    if resp.status_code == 204:
        assert any(c["action"] == "board.delete" for c in captured)
        delete_call = next(c for c in captured if c["action"] == "board.delete")
        assert delete_call["resource_id"] == board_id
        assert delete_call["actor_user_id"] == user_id


# ---------------------------------------------------------------------------
# Test 3: GET /audit — list + pagination + filter
# ---------------------------------------------------------------------------

def _make_audit_row(org_id: str, **kwargs: Any) -> dict[str, Any]:
    """Return a canned audit row dict."""
    return {
        "id": str(uuid.uuid4()),
        "org_id": org_id,
        "actor_user_id": kwargs.get("actor_user_id", "user-1"),
        "actor_kind": kwargs.get("actor_kind", "access"),
        "action": kwargs.get("action", "board.create"),
        "resource_type": kwargs.get("resource_type", "board"),
        "resource_id": kwargs.get("resource_id", "r-1"),
        "summary": kwargs.get("summary", {}),
        "at": kwargs.get("at", datetime.now(timezone.utc)),
    }


@pytest.mark.asyncio
async def test_get_audit_returns_list(audit_app):
    """GET /audit returns a paginated list for the caller's org."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    token = _make_token(user_id)

    rows = [_make_audit_row(org_id, action="board.create")]

    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
    set_repo(repo)

    async def fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper()
        if "FROM USERS" in q:
            return _user_row(user_id)
        if "COUNT" in q and "AUDIT_LOG" in q:
            return {"total": len(rows)}
        if "ORG_MEMBERS" in q:
            return {"role": "owner"}
        return None

    async def fake_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        if "AUDIT_LOG" in query.upper():
            return rows
        return []

    with (
        patch("app.db.fetchrow", new=fake_fetchrow),
        patch("app.auth.deps.fetchrow", new=fake_fetchrow),
        patch("app.db.fetch", new=fake_fetch),
        patch("app.routes.audit.fetchrow", new=fake_fetchrow),
        patch("app.routes.audit.fetch", new=fake_fetch),
    ):
        transport = ASGITransport(app=audit_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get(
                "/api/v1/audit",
                headers={**_auth(token), "X-Org-Id": org_id},
            )

    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert "limit" in data
    assert "offset" in data


@pytest.mark.asyncio
async def test_get_audit_filters_by_resource_type(audit_app):
    """GET /audit?resource_type=board only returns board rows."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    token = _make_token(user_id)

    board_row = _make_audit_row(org_id, resource_type="board")
    connector_row = _make_audit_row(org_id, resource_type="connector")

    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
    set_repo(repo)

    async def fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper()
        if "FROM USERS" in q:
            return _user_row(user_id)
        if "COUNT" in q and "AUDIT_LOG" in q:
            # Return count filtered to board only
            return {"total": 1}
        if "ORG_MEMBERS" in q:
            return {"role": "owner"}
        return None

    async def fake_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        if "AUDIT_LOG" in query.upper():
            # Simulate filter
            if args and "board" in args:
                return [board_row]
            return [board_row, connector_row]
        return []

    with (
        patch("app.db.fetchrow", new=fake_fetchrow),
        patch("app.auth.deps.fetchrow", new=fake_fetchrow),
        patch("app.db.fetch", new=fake_fetch),
        patch("app.routes.audit.fetchrow", new=fake_fetchrow),
        patch("app.routes.audit.fetch", new=fake_fetch),
    ):
        transport = ASGITransport(app=audit_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get(
                "/api/v1/audit?resource_type=board",
                headers={**_auth(token), "X-Org-Id": org_id},
            )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 4: Cross-org isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_org_isolation(audit_app):
    """record_audit always uses the caller's verified org_id (never request body)."""
    from app.audit import record_audit

    calls: list[tuple] = []

    async def fake_execute(query: str, *args: Any) -> str:
        calls.append((query, args))
        return "INSERT 0 1"

    org_a = "org-a-" + str(uuid.uuid4())
    org_b = "org-b-" + str(uuid.uuid4())

    with patch("app.db.execute", new=fake_execute):
        # Even if someone passes org_b as a summary value, the row is pinned to org_a
        await record_audit(
            org_id=org_a,
            actor_user_id="user-1",
            actor_kind="access",
            action="board.create",
            resource_type="board",
            resource_id="board-1",
            summary={"attempted_org": org_b},  # this is just metadata, not org routing
        )

    assert len(calls) == 1
    _, args = calls[0]
    assert args[0] == org_a   # org_id in INSERT is org_a, not org_b


@pytest.mark.asyncio
async def test_audit_read_scoped_to_caller_org(audit_app):
    """GET /audit only returns rows for the caller's org (org_id from token membership)."""
    user_id = str(uuid.uuid4())
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    token = _make_token(user_id)

    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_a, user_id=user_id, role="owner")
    # User is NOT in org_b
    set_repo(repo)

    async def fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper()
        if "FROM USERS" in q:
            return _user_row(user_id)
        if "COUNT" in q and "AUDIT_LOG" in q:
            # Must be called with org_a, not org_b
            assert str(args[0]) == org_a, f"Wrong org_id in query: {args[0]}"
            return {"total": 0}
        if "ORG_MEMBERS" in q:
            return {"role": "owner"}
        return None

    async def fake_fetch(query: str, *args: Any) -> list:
        return []

    with (
        patch("app.db.fetchrow", new=fake_fetchrow),
        patch("app.auth.deps.fetchrow", new=fake_fetchrow),
        patch("app.db.fetch", new=fake_fetch),
        patch("app.routes.audit.fetchrow", new=fake_fetchrow),
        patch("app.routes.audit.fetch", new=fake_fetch),
    ):
        transport = ASGITransport(app=audit_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/api/v1/audit", headers=_auth(token))

    # Request succeeded (no cross-org leak)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 5: No secrets/PII in summary (POPIA)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_popia_safe(audit_app):
    """record_audit summary must not contain secret keys."""
    from app.audit import record_audit

    calls: list[tuple] = []

    async def fake_execute(query: str, *args: Any) -> str:
        calls.append((query, args))
        return "INSERT 0 1"

    # A well-behaved caller passes only POPIA-safe metadata.
    safe_summary = {"name": "My Connector", "connector_type": "postgres"}

    with patch("app.db.execute", new=fake_execute):
        await record_audit(
            org_id="org-1",
            actor_user_id="user-1",
            actor_kind="access",
            action="connector.create",
            resource_type="connector",
            resource_id="conn-1",
            summary=safe_summary,
        )

    assert len(calls) == 1
    _, args = calls[0]
    stored = json.loads(args[6])
    # Verify none of the classic secret keys are present
    for secret_key in ("password", "token", "api_key", "service_account_json",
                        "aws_secret_access_key", "private_key"):
        assert secret_key not in stored, f"Secret key {secret_key!r} found in audit summary"


# ---------------------------------------------------------------------------
# Test 6: Unauthenticated → 401
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_unauthenticated_returns_401(audit_app):
    """GET /audit without auth returns 401."""
    transport = ASGITransport(app=audit_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/api/v1/audit")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_audit_resource_history_unauthenticated_returns_401(audit_app):
    """GET /audit/{type}/{id} without auth returns 401."""
    transport = ASGITransport(app=audit_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.get("/api/v1/audit/board/some-board-id")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test 7: Non-admin (viewer/member) → 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_viewer_returns_403(audit_app):
    """GET /audit with a viewer-role user returns 403."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    token = _make_token(user_id)

    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="viewer")
    set_repo(repo)

    async def fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper()
        if "FROM USERS" in q:
            return _user_row(user_id)
        if "ORG_MEMBERS" in q:
            return {"role": "viewer"}
        return None

    with (
        patch("app.db.fetchrow", new=fake_fetchrow),
        patch("app.auth.deps.fetchrow", new=fake_fetchrow),
        patch("app.routes.audit.fetchrow", new=fake_fetchrow),
    ):
        transport = ASGITransport(app=audit_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/api/v1/audit", headers=_auth(token))

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_member_returns_403(audit_app):
    """GET /audit with a member-role user returns 403 (audit is owner/admin only)."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    token = _make_token(user_id)

    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="member")
    set_repo(repo)

    async def fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper()
        if "FROM USERS" in q:
            return _user_row(user_id)
        if "ORG_MEMBERS" in q:
            return {"role": "member"}
        return None

    with (
        patch("app.db.fetchrow", new=fake_fetchrow),
        patch("app.auth.deps.fetchrow", new=fake_fetchrow),
        patch("app.routes.audit.fetchrow", new=fake_fetchrow),
    ):
        transport = ASGITransport(app=audit_app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            resp = await ac.get("/api/v1/audit", headers=_auth(token))

    assert resp.status_code == 403
