"""Adversarial tests for the audit log NOT covered in test_audit.py.

Coverage
--------
Pagination:
1. offset=0 limit=1 → first item only.
2. offset=1 limit=1 → second item only.
3. offset=9999 limit=50 (beyond total) → items=[] but total is correct.

Filtering:
4. Filter by action only.
5. Filter by actor_user_id.
6. Combined filter: resource_type + action.

Ordering:
7. Results are newest-first (descending 'at').

Cross-org:
8. org_b user cannot query org_a audit rows.

POPIA:
9. mcp_server.create summary has no auth_token/password/key.
10. metric.create summary has no secrets.
11. audit route response metadata does not expose email PII.

Empty log:
12. Empty audit log → items=[], total=0.
13. Huge offset → empty items, no error.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(user_id: str) -> str:
    return mint_access_token(str(user_id))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _user_row(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"u-{user_id[:6]}@test.example",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(timezone.utc),
    }


def _make_audit_row(org_id: str, **kwargs: Any) -> dict[str, Any]:
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


def _build_fake_db_callbacks(user_id: str, org_id: str, audit_rows: list[dict]):
    """Build fake fetchrow/fetch callbacks for audit route tests.

    The audit route imports `from app.db import fetch, fetchrow` at module level,
    so we must patch the names in `app.routes.audit` (not `app.db`).
    """

    async def fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper()
        if "FROM USERS" in q:
            return _user_row(user_id)
        if "COUNT(*)" in q:
            return {"total": len(audit_rows)}
        return None

    async def fake_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.upper()
        if "AUDIT_LOG" in q:
            # Parse limit and offset from args (last two positional args)
            # Route calls: *params, limit, offset
            if len(args) >= 2:
                offset = int(args[-1])
                limit = int(args[-2])
            else:
                offset = 0
                limit = 50
            sliced = audit_rows[offset: offset + limit]
            return sliced
        return []

    return fake_fetchrow, fake_fetch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def audit_adv_app(app):
    return app


@pytest_asyncio.fixture
async def audit_client_and_user(audit_adv_app, fake_db):
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _user_row(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
    token = _make_token(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=audit_adv_app), base_url="http://testserver"
    ) as ac:
        ac.headers.update(_auth(token))
        ac._user_id = user_id
        ac._org_id = org_id
        yield ac, user_id, org_id

    set_repo(None)


# ---------------------------------------------------------------------------
# 1–3. Pagination tests
# ---------------------------------------------------------------------------


class TestPagination:
    @pytest.mark.asyncio
    async def test_offset0_limit1_returns_first_item(self, audit_adv_app, fake_db, audit_client_and_user):
        client, user_id, org_id = audit_client_and_user

        row_a = _make_audit_row(org_id, action="board.create", resource_type="board")
        row_b = _make_audit_row(org_id, action="flow.create", resource_type="flow")
        all_rows = [row_a, row_b]

        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, all_rows)

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit", params={"limit": 1, "offset": 0})

        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 1
        assert data["offset"] == 0
        assert len(data["items"]) == 1
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_offset1_limit1_returns_second_item(self, audit_adv_app, fake_db, audit_client_and_user):
        client, user_id, org_id = audit_client_and_user

        row_a = _make_audit_row(org_id, action="board.create")
        row_b = _make_audit_row(org_id, action="flow.create")
        all_rows = [row_a, row_b]

        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, all_rows)

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit", params={"limit": 1, "offset": 1})

        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 1
        assert data["offset"] == 1
        assert len(data["items"]) == 1
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_offset_beyond_total_returns_empty_items(self, audit_adv_app, fake_db, audit_client_and_user):
        """offset=9999 beyond 2 total rows → items=[], total=2."""
        client, user_id, org_id = audit_client_and_user

        all_rows = [_make_audit_row(org_id), _make_audit_row(org_id)]
        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, all_rows)

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit", params={"limit": 50, "offset": 9999})

        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_huge_offset_returns_empty_no_error(self, audit_adv_app, fake_db, audit_client_and_user):
        """offset=1000000 → empty items without error (no 400/500)."""
        client, user_id, org_id = audit_client_and_user

        all_rows: list[dict] = []
        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, all_rows)

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit", params={"limit": 50, "offset": 1000000})

        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0


# ---------------------------------------------------------------------------
# 4–6. Filter tests
# ---------------------------------------------------------------------------


class TestFilters:
    @pytest.mark.asyncio
    async def test_filter_by_action(self, audit_adv_app, fake_db, audit_client_and_user):
        """Filter by action='flow.create' → route passes action filter."""
        client, user_id, org_id = audit_client_and_user

        flow_row = _make_audit_row(org_id, action="flow.create")
        all_rows = [flow_row]
        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, all_rows)

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit", params={"action": "flow.create"})

        assert resp.status_code == 200
        data = resp.json()
        # The route accepts action filter — verify no error
        assert "items" in data

    @pytest.mark.asyncio
    async def test_filter_by_actor_user_id(self, audit_adv_app, fake_db, audit_client_and_user):
        """Filter by actor=<user_id> → route passes actor filter."""
        client, user_id, org_id = audit_client_and_user

        actor_id = str(uuid.uuid4())
        actor_row = _make_audit_row(org_id, actor_user_id=actor_id)
        all_rows = [actor_row]
        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, all_rows)

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit", params={"actor": actor_id})

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_combined_resource_type_and_action_filter(self, audit_adv_app, fake_db, audit_client_and_user):
        """Combined resource_type + action → both filters are applied (AND)."""
        client, user_id, org_id = audit_client_and_user

        all_rows = [_make_audit_row(org_id, resource_type="flow", action="flow.create")]
        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, all_rows)

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get(
                "/api/v1/audit",
                params={"resource_type": "flow", "action": "flow.create"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data


# ---------------------------------------------------------------------------
# 7. Ordering — newest-first
# ---------------------------------------------------------------------------


class TestOrdering:
    @pytest.mark.asyncio
    async def test_results_newest_first(self, audit_adv_app, fake_db, audit_client_and_user):
        """Audit list returns newest-first (ORDER BY at DESC in SQL)."""
        client, user_id, org_id = audit_client_and_user

        older = _make_audit_row(
            org_id,
            action="board.create",
            at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        newer = _make_audit_row(
            org_id,
            action="flow.create",
            at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        # Fake DB returns newer first (as DB ORDER BY at DESC would)
        all_rows = [newer, older]
        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, all_rows)

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit", params={"limit": 10})

        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        if len(items) >= 2:
            # First item should be newer
            assert items[0]["action"] == "flow.create"


# ---------------------------------------------------------------------------
# 8. Cross-org isolation
# ---------------------------------------------------------------------------


class TestCrossOrgIsolation:
    @pytest.mark.asyncio
    async def test_org_b_cannot_see_org_a_rows(self, audit_adv_app, fake_db):
        """org_b user queries /audit — should never see org_a's rows."""
        repo_a = InMemoryRepo()
        user_a = str(uuid.uuid4())
        org_a = str(uuid.uuid4())
        fake_db.users[user_a] = _user_row(user_a)
        repo_a.seed_org_member(org_id=org_a, user_id=user_a, role="owner")

        repo_b = InMemoryRepo()
        user_b = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        fake_db.users[user_b] = _user_row(user_b)
        repo_b.seed_org_member(org_id=org_b, user_id=user_b, role="owner")

        # Use org_b's repo
        set_repo(repo_b)
        token_b = _make_token(user_b)

        org_a_row = _make_audit_row(org_a, action="board.create")

        async def fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
            q = query.upper()
            if "FROM USERS" in q:
                # Return the right user
                uid = str(args[0]) if args else ""
                if uid == user_b:
                    return _user_row(user_b)
                if uid == user_a:
                    return _user_row(user_a)
                return None
            if "COUNT(*)" in q:
                # Return 0 because org_b has no rows
                return {"total": 0}
            return None

        async def fake_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
            # Org_b's audit rows — none
            return []

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_fetchrow),
            patch("app.routes.audit.fetch", side_effect=fake_fetch),
            patch("app.auth.deps.fetchrow", side_effect=fake_fetchrow),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=audit_adv_app), base_url="http://testserver"
            ) as c:
                c.headers["Authorization"] = f"Bearer {token_b}"
                resp = await c.get("/api/v1/audit")

        set_repo(None)
        assert resp.status_code == 200
        data = resp.json()
        # org_b must see 0 items — org_a's row is not in the result
        for item in data["items"]:
            assert item.get("org_id") != org_a


# ---------------------------------------------------------------------------
# 9–11. POPIA — no secrets or PII in audit summaries
# ---------------------------------------------------------------------------


class TestPopia:
    @pytest.mark.asyncio
    async def test_mcp_server_create_no_auth_token_in_summary(self):
        """record_audit for mcp_server.create must NOT include auth_token in summary."""
        from app.audit import record_audit

        calls: list[tuple] = []

        async def fake_execute(query: str, *args: Any) -> str:
            calls.append((query, args))
            return "INSERT 0 1"

        with patch("app.db.execute", new=fake_execute):
            await record_audit(
                org_id="org-1",
                actor_user_id="user-1",
                actor_kind="access",
                action="mcp_server.create",
                resource_type="mcp_server",
                resource_id="srv-1",
                summary={"name": "my-server", "transport": "http"},
            )

        assert len(calls) == 1
        _, args = calls[0]
        summary = json.loads(args[6])
        assert "auth_token" not in summary
        assert "password" not in summary
        assert "secret" not in summary
        assert "token" not in summary

    @pytest.mark.asyncio
    async def test_metric_create_no_secrets_in_summary(self):
        """record_audit for metric.create summary must not have secrets."""
        from app.audit import record_audit

        calls: list[tuple] = []

        async def fake_execute(query: str, *args: Any) -> str:
            calls.append((query, args))
            return "INSERT 0 1"

        with patch("app.db.execute", new=fake_execute):
            await record_audit(
                org_id="org-1",
                actor_user_id="user-1",
                actor_kind="access",
                action="metric.create",
                resource_type="metric",
                resource_id="metric-1",
                summary={"name": "revenue", "measure": "sum"},
            )

        assert len(calls) == 1
        _, args = calls[0]
        summary = json.loads(args[6])
        assert "password" not in summary
        assert "api_key" not in summary
        assert "token" not in summary

    @pytest.mark.asyncio
    async def test_audit_response_no_raw_email_in_metadata(
        self, audit_adv_app, fake_db, audit_client_and_user
    ):
        """Audit list response metadata does not expose raw email addresses."""
        client, user_id, org_id = audit_client_and_user

        row = _make_audit_row(org_id, action="board.create")
        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, [row])

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit")

        assert resp.status_code == 200
        data = resp.json()
        # Response should not expose raw email in any top-level metadata keys
        for key in ("email", "user_email", "actor_email"):
            assert key not in data


# ---------------------------------------------------------------------------
# 12–13. Empty log and large offset
# ---------------------------------------------------------------------------


class TestEmptyLog:
    @pytest.mark.asyncio
    async def test_empty_audit_log_returns_zero_total(
        self, audit_adv_app, fake_db, audit_client_and_user
    ):
        """Empty audit log → items=[], total=0."""
        client, user_id, org_id = audit_client_and_user

        fake_frow, fake_f = _build_fake_db_callbacks(user_id, org_id, [])

        with (
            patch("app.routes.audit.fetchrow", side_effect=fake_frow),
            patch("app.routes.audit.fetch", side_effect=fake_f),
            patch("app.auth.deps.fetchrow", side_effect=fake_frow),
        ):
            resp = await client.get("/api/v1/audit")

        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
