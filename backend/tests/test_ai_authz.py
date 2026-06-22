"""Authz regression tests for POST /ai/sql (save_as) and POST /ai/pin.

Invariant: viewer role is read-only. Viewers must receive 403 on any route
that mutates state. Writer/admin/owner must still receive 200.

Findings fixed:
  [HIGH] POST /ai/sql with save_as — viewer was not blocked (writer_default guard added).
  [MED]  POST /ai/pin            — viewer was not blocked (writer guard added).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.queries.registry import OutputColumn, get_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SALES_QUERY_ID = "authz_test_sales"
_SALES_COLUMNS = ["region", "revenue", "month"]


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str, email: str = "tester@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _register_sales_query() -> None:
    get_query_registry().register(
        id=_SALES_QUERY_ID,
        sql="SELECT region, revenue, month FROM sales",
        name="Sales by region (authz test)",
        output_schema=[OutputColumn(name=c, type="text") for c in _SALES_COLUMNS],
    )


def _chart_pin() -> dict[str, Any]:
    return {
        "title": "Revenue by region",
        "source": {"query_id": _SALES_QUERY_ID},
        "viz": {
            "type": "chart",
            "chart_type": "bar",
            "encoding": {"x": "region", "y": "revenue"},
        },
    }


# ---------------------------------------------------------------------------
# Fixture: two users in the same org (viewer + writer)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def authz_client(app, fake_db):
    """Client with an InMemoryRepo seeded with a viewer and an owner in the same org."""
    _register_sales_query()

    repo = InMemoryRepo()
    set_repo(repo)

    viewer_id = str(uuid.uuid4())
    writer_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[viewer_id] = _make_user(viewer_id, "viewer@example.com")
    fake_db.users[writer_id] = _make_user(writer_id, "writer@example.com")

    repo.seed_org_member(org_id=org_id, user_id=viewer_id, role="viewer")
    repo.seed_org_member(org_id=org_id, user_id=writer_id, role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, viewer_id, writer_id, org_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# [HIGH] POST /ai/sql — viewer blocked, writer succeeds
# ---------------------------------------------------------------------------


class TestSqlAuthz:
    """Viewer must get 403 on POST /ai/sql with save_as; writer/owner must get 200."""

    @pytest.mark.asyncio
    async def test_viewer_blocked_on_sql_with_save_as(self, authz_client):
        """Viewer role → 403 on POST /ai/sql with save_as."""
        ac, viewer_id, _writer_id, _org_id, _repo = authz_client
        save_id = f"authz_viewer_{uuid.uuid4().hex[:8]}"

        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me orders", "save_as": save_id},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, (
            f"Expected 403 for viewer on /ai/sql with save_as, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_viewer_blocked_on_sql_without_save_as(self, authz_client):
        """Viewer role → 403 on POST /ai/sql even without save_as (route is write-guarded)."""
        ac, viewer_id, _writer_id, _org_id, _repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me orders"},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, (
            f"Expected 403 for viewer on /ai/sql, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_sql(self, authz_client, monkeypatch):
        """Owner/writer role → 200 on POST /ai/sql."""
        from app.config import get_settings
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        get_settings.cache_clear()

        ac, _viewer_id, writer_id, _org_id, _repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me orders"},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, (
            f"Expected 200 for owner on /ai/sql, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_writer_save_as_registers_query(self, authz_client, monkeypatch):
        """Owner/writer role → save_as persists the query; viewer is never involved."""
        from app.config import get_settings
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        get_settings.cache_clear()

        ac, _viewer_id, writer_id, _org_id, _repo = authz_client
        save_id = f"authz_writer_{uuid.uuid4().hex[:8]}"

        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me orders", "save_as": save_id},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, (
            f"Expected 200 for owner on /ai/sql with save_as, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["registered_id"] == save_id

        # The SQL is actually registered (not just a string match on the response).
        registry = get_query_registry()
        rq = registry.get(save_id)
        assert rq is not None, f"Query '{save_id}' not found in registry after owner save_as"
        # Verify it's parseable SQL — real SQL check via sqlglot, not just substring.
        import sqlglot
        parsed = sqlglot.parse_one(rq.sql)
        assert parsed is not None, f"Registered SQL is not parseable: {rq.sql!r}"

    @pytest.mark.asyncio
    async def test_viewer_save_as_does_not_register_query(self, authz_client):
        """Viewer 403 means the QueryRegistry must never be touched."""
        ac, viewer_id, _writer_id, _org_id, _repo = authz_client
        save_id = f"authz_viewer_noop_{uuid.uuid4().hex[:8]}"

        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me orders", "save_as": save_id},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403

        # The registry must NOT have the id — the handler never ran.
        registry = get_query_registry()
        assert registry.get(save_id) is None, (
            f"Query '{save_id}' was registered despite viewer being blocked (403)"
        )


# ---------------------------------------------------------------------------
# [MED] POST /ai/pin — viewer blocked, writer succeeds
# ---------------------------------------------------------------------------


class TestPinAuthz:
    """Viewer must get 403 on POST /ai/pin; writer/owner must get 200."""

    @pytest.mark.asyncio
    async def test_viewer_blocked_on_pin(self, authz_client):
        """Viewer role → 403 on POST /ai/pin."""
        ac, viewer_id, _writer_id, _org_id, _repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/pin",
            json=_chart_pin(),
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, (
            f"Expected 403 for viewer on /ai/pin, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_pin(self, authz_client):
        """Owner/writer role → 200 on POST /ai/pin."""
        ac, _viewer_id, writer_id, _org_id, _repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/pin",
            json=_chart_pin(),
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, (
            f"Expected 200 for owner on /ai/pin, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["valid"] is True
        assert body["board_id"]
        assert body["widget_id"]

    @pytest.mark.asyncio
    async def test_viewer_pin_does_not_persist_board(self, authz_client):
        """Viewer 403 on /ai/pin means no board is created in the repo."""
        ac, viewer_id, _writer_id, org_id, repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/pin",
            json=_chart_pin(),
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403

        # No board must have been persisted.
        rows = await repo.list("boards", org_id)
        assert rows == [], (
            f"Board was created despite viewer being blocked (403): {rows}"
        )

    @pytest.mark.asyncio
    async def test_writer_pin_persists_board(self, authz_client):
        """Owner/writer's pin creates a board in the repo (persisted state confirmed)."""
        ac, _viewer_id, writer_id, org_id, repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/pin",
            json=_chart_pin(),
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        board_id = body["board_id"]

        # Board must be persisted.
        row = await repo.get("boards", org_id, board_id)
        assert row is not None, "Board not persisted after owner pin"
        widgets = row["config"]["spec"]["widgets"]
        assert len(widgets) == 1
        assert widgets[0]["query_id"] == _SALES_QUERY_ID
