"""Authz regression tests for POST /ai/sql (save_as) and POST /ai/pin.

Invariant: viewer role is read-only. Viewers must receive 403 on any route
that mutates state. Writer/admin/owner must still receive 200.

Findings fixed:
  [HIGH] POST /ai/sql with save_as — viewer was not blocked (writer_default guard added).
  [MED]  POST /ai/pin            — viewer was not blocked (writer guard added).
  [MED]  POST /ai/sql save_as    — cross-tenant id collision: org A can overwrite org B's
                                   registered query id in the process-global registry.
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
        # registered_id is org-scoped ("{org}:{save_as}") for tenant isolation;
        # it ends with the human id the caller supplied.
        assert body["registered_id"].endswith(save_id)

        # The SQL is actually registered (not just a string match on the response).
        registry = get_query_registry()
        rq = registry.get(body["registered_id"])
        assert rq is not None, (
            f"Query '{body['registered_id']}' not found in registry after owner save_as"
        )
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


# ---------------------------------------------------------------------------
# [MED] Cross-tenant QueryRegistry id collision via POST /ai/sql save_as
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def two_org_clients(app, fake_db, monkeypatch):
    """Two fully independent orgs (org A and org B), each with an owner user.

    Clears LLM env vars so both clients use NullProvider (deterministic, no
    network).  Yields (client_a, writer_a, org_a, client_b, writer_b, org_b).
    """
    from app.config import get_settings
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()

    repo = InMemoryRepo()
    set_repo(repo)

    writer_a = str(uuid.uuid4())
    org_a = str(uuid.uuid4())
    writer_b = str(uuid.uuid4())
    org_b = str(uuid.uuid4())

    for uid, email in [(writer_a, "org_a_owner@example.com"), (writer_b, "org_b_owner@example.com")]:
        fake_db.users[uid] = {
            "id": uid,
            "email": email,
            "name": "Owner",
            "avatar_url": None,
            "email_verified": True,
            "created_at": "2024-01-01T00:00:00+00:00",
        }
    repo.seed_org_member(org_id=org_a, user_id=writer_a, role="owner")
    repo.seed_org_member(org_id=org_b, user_id=writer_b, role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as ac:
        yield ac, writer_a, org_a, writer_b, org_b

    set_repo(None)


class TestCrossTenantSaveAs:
    """POST /ai/sql save_as must org-scope the registry key so one org cannot
    squat a human id (and so the other org never gets a permanent 409), while
    still never overwriting/leaking the other org's registered SQL."""

    @pytest.mark.asyncio
    async def test_org_b_can_reuse_org_a_save_as_human_id(self, two_org_clients):
        """Org B's save_as with the SAME human id org A used → 200, distinct entry.

        Anti-squatting: the registry key is namespaced by org (``{org}:{id}``), so
        org B reusing the human id does NOT collide with org A and does NOT get a
        409. Each org gets its own distinct, org-owned registry entry.
        """
        ac, writer_a, org_a, writer_b, org_b = two_org_clients
        shared_id = f"shared_query_{uuid.uuid4().hex[:8]}"

        # Org A registers the human id first.
        resp_a = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show orders", "save_as": shared_id},
            headers=_auth_headers(writer_a),
        )
        assert resp_a.status_code == 200, (
            f"Org A save_as failed unexpectedly: {resp_a.status_code} {resp_a.text}"
        )
        assert resp_a.json()["registered_id"].endswith(shared_id)

        # Org B uses the same human id → succeeds (no squatting-induced 409).
        resp_b = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "different question", "save_as": shared_id},
            headers=_auth_headers(writer_b),
        )
        assert resp_b.status_code == 200, (
            f"Expected 200 (org-scoped keys, no squatting) but got "
            f"{resp_b.status_code}: {resp_b.text}"
        )
        # Distinct keys, both ending with the human id.
        assert resp_a.json()["registered_id"] != resp_b.json()["registered_id"]
        assert resp_b.json()["registered_id"].endswith(shared_id)

    @pytest.mark.asyncio
    async def test_registry_retains_org_a_sql_after_org_b_reuses_id(self, two_org_clients):
        """Org B reusing the human id must NOT alter org A's registered entry."""
        from app.queries.registry import get_query_registry

        ac, writer_a, org_a, writer_b, org_b = two_org_clients
        shared_id = f"retain_query_{uuid.uuid4().hex[:8]}"

        # Org A registers first.
        resp_a = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "count users", "save_as": shared_id},
            headers=_auth_headers(writer_a),
        )
        assert resp_a.status_code == 200
        original_sql = resp_a.json()["sql"]
        id_a = resp_a.json()["registered_id"]

        # Org B reuses the human id (distinct scoped key) → 200.
        resp_b = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "totally different", "save_as": shared_id},
            headers=_auth_headers(writer_b),
        )
        assert resp_b.status_code == 200

        # Org A's entry (its own scoped key) is unchanged and still org A's.
        rq = get_query_registry().get(id_a)
        assert rq is not None, "Org A's query was removed from registry"
        assert rq.sql == original_sql, (
            f"Registry SQL was silently overwritten: expected {original_sql!r}, "
            f"got {rq.sql!r}"
        )
        assert rq.owner_org_id == org_a, (
            f"Registry owner_org_id changed: expected {org_a!r}, got {rq.owner_org_id!r}"
        )

    @pytest.mark.asyncio
    async def test_each_org_can_register_its_own_unique_id(self, two_org_clients):
        """Each org can independently register a unique id without collision."""
        ac, writer_a, org_a, writer_b, org_b = two_org_clients

        id_a = f"org_a_query_{uuid.uuid4().hex[:8]}"
        id_b = f"org_b_query_{uuid.uuid4().hex[:8]}"

        resp_a = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "list orders", "save_as": id_a},
            headers=_auth_headers(writer_a),
        )
        assert resp_a.status_code == 200, resp_a.text

        resp_b = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "list users", "save_as": id_b},
            headers=_auth_headers(writer_b),
        )
        assert resp_b.status_code == 200, resp_b.text

        assert resp_a.json()["registered_id"].endswith(id_a)
        assert resp_b.json()["registered_id"].endswith(id_b)

    @pytest.mark.asyncio
    async def test_same_org_can_overwrite_its_own_save_as(self, two_org_clients):
        """The same org can update/overwrite its own registered id (upsert)."""
        ac, writer_a, org_a, writer_b, org_b = two_org_clients
        my_id = f"my_query_{uuid.uuid4().hex[:8]}"

        # Register once.
        r1 = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "list orders", "save_as": my_id},
            headers=_auth_headers(writer_a),
        )
        assert r1.status_code == 200, r1.text

        # Same org registers again (upsert) — must succeed under the SAME key.
        r2 = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "updated orders", "save_as": my_id},
            headers=_auth_headers(writer_a),
        )
        assert r2.status_code == 200, (
            f"Same-org upsert failed: {r2.status_code} {r2.text}"
        )
        assert r2.json()["registered_id"].endswith(my_id)
        assert r1.json()["registered_id"] == r2.json()["registered_id"]

    @pytest.mark.asyncio
    async def test_system_builtin_ids_not_blocked(self, two_org_clients):
        """Built-in/system query ids (demo_*) remain accessible and unaffected."""
        from app.queries.registry import get_query_registry

        _ac, _writer_a, _org_a, _writer_b, _org_b = two_org_clients
        registry = get_query_registry()
        rq = registry.get("demo_all")
        assert rq is not None, "Built-in 'demo_all' missing from registry"
        assert rq.system is True, "Built-in demo_all must have system=True"


# ---------------------------------------------------------------------------
# [MED] POST /ai/canvas and POST /ai/canvas/edit — viewer blocked, writer succeeds
# ---------------------------------------------------------------------------


class TestCanvasAuthz:
    """Viewer must get 403 on POST /ai/canvas and /ai/canvas/edit; writer gets 200."""

    def _valid_doc_payload(self) -> dict:
        return {
            "version": 1,
            "title": "base canvas",
            "html": '<section><nubi-table data-el-id="el_t1"></nubi-table></section>',
            "bindings": {
                "el_t1": {
                    "kind": "query",
                    "query_id": "demo_all",
                    "params": {},
                    "field": None,
                    "format": None,
                }
            },
            "variables": [],
            "assets": {},
        }

    @pytest.mark.asyncio
    async def test_viewer_blocked_on_canvas_generate(self, authz_client):
        """Viewer role → 403 on POST /ai/canvas."""
        ac, viewer_id, _writer_id, _org_id, _repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/canvas",
            json={"question": "show me data"},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, (
            f"Expected 403 for viewer on /ai/canvas, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_viewer_blocked_on_canvas_edit(self, authz_client):
        """Viewer role → 403 on POST /ai/canvas/edit."""
        ac, viewer_id, _writer_id, _org_id, _repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/canvas/edit",
            json={"doc": self._valid_doc_payload(), "instruction": "add a title"},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, (
            f"Expected 403 for viewer on /ai/canvas/edit, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_canvas_generate(self, authz_client, monkeypatch):
        """Owner/writer role → 200 on POST /ai/canvas."""
        from app.config import get_settings
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        get_settings.cache_clear()

        ac, _viewer_id, writer_id, _org_id, _repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/canvas",
            json={"question": "show me demo data"},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, (
            f"Expected 200 for owner on /ai/canvas, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "doc" in body
        assert "valid" in body

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_canvas_edit(self, authz_client, monkeypatch):
        """Owner/writer role → 200 on POST /ai/canvas/edit."""
        from app.config import get_settings
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        get_settings.cache_clear()

        ac, _viewer_id, writer_id, _org_id, _repo = authz_client

        resp = await ac.post(
            "/api/v1/ai/canvas/edit",
            json={"doc": self._valid_doc_payload(), "instruction": "add a title"},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, (
            f"Expected 200 for owner on /ai/canvas/edit, got {resp.status_code}: {resp.text}"
        )
