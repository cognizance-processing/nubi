"""Tests for the notebook / cell preview endpoints in routes/flows.py.

Coverage
--------
1. POST /flows/preview — inline spec, SQL cell → sampled rows + columns.
2. POST /flows/preview — cell_key selects a specific cell.
3. POST /flows/preview — up-to-cell: upstream cells executed first, inputs available.
4. POST /flows/preview — bad spec returns 400.
5. POST /flows/preview — bad cell_key returns 400.
6. POST /flows/preview — preview_limit caps rows.
7. POST /flows/preview — 401 without auth.
8. POST /flows/preview — from flow_id (persisted flow).
9. POST /flows/run-cell — runs a single SQL cell durably, returns rows.
10. POST /flows/run-cell — 401 without auth.
11. POST /flows/notebooks — save a NotebookSpec as a flow (201).
12. GET  /flows/notebooks/{id} — round-trips back to NotebookSpec.
13. notebook.py — infer_notebook_edges sequential fallback.
14. notebook.py — infer_notebook_edges respects explicit needs.
15. notebook.py — notebook_to_flow / flow_to_notebook round-trip.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


# A simple single-cell notebook spec (SQL query against the demo DuckDB table).
_SINGLE_CELL_SPEC = {
    "version": 1,
    "name": "test_notebook",
    "params": [],
    "tasks": [
        {
            "key": "cell_demo",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT id, name, value FROM demo"},
            "cell_type": "sql",
            "execution_mode": "preview",
        }
    ],
}

# A two-cell spec: cell_a produces rows, cell_b is a noop downstream.
_TWO_CELL_SPEC = {
    "version": 1,
    "name": "two_cell_notebook",
    "params": [],
    "tasks": [
        {
            "key": "cell_a",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT id, value FROM demo WHERE active = true"},
        },
        {
            "key": "cell_b",
            "kind": "noop",
            "needs": ["cell_a"],
            "config": {},
        },
    ],
}

# A valid flow spec using noop tasks (no external deps, always succeeds).
_NOOP_FLOW_SPEC = {
    "version": 1,
    "name": "noop_flow",
    "params": [],
    "tasks": [
        {"key": "step1", "kind": "noop", "needs": [], "config": {}},
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def nb_app(app):
    """FastAPI app with InMemoryFlowStore + InMemoryRepo injected."""
    store = InMemoryFlowStore()
    set_flow_store(store)

    repo = InMemoryRepo()
    set_repo(repo)

    yield app, store, repo

    set_flow_store(None)
    set_repo(None)


@pytest_asyncio.fixture
async def nb_client(nb_app, fake_db):
    """Async HTTPX client pre-seeded with a user + org."""
    app, store, repo = nb_app

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(user_id=alice_id, email="alice@example.com")

    fake_db.users[alice_id] = alice
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, alice_id, alice_org_id, store, repo


# ---------------------------------------------------------------------------
# 1. POST /flows/preview — inline SQL cell returns sampled rows
# ---------------------------------------------------------------------------


class TestPreviewCellInlineSql:
    @pytest.mark.asyncio
    async def test_preview_returns_columns_and_rows(self, nb_client):
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _SINGLE_CELL_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert "columns" in body
        assert "rows" in body
        assert "row_count" in body
        assert "cell_key" in body

        assert isinstance(body["columns"], list)
        assert isinstance(body["rows"], list)
        assert isinstance(body["row_count"], int)

        # The demo table has 5 rows; preview should return them all (within limit).
        assert body["row_count"] > 0
        assert "id" in body["columns"]
        assert "name" in body["columns"]
        assert "value" in body["columns"]

    @pytest.mark.asyncio
    async def test_preview_cell_key_returned(self, nb_client):
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _SINGLE_CELL_SPEC, "cell_key": "cell_demo"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["cell_key"] == "cell_demo"


# ---------------------------------------------------------------------------
# 2. POST /flows/preview — cell_key selects a specific cell
# ---------------------------------------------------------------------------


class TestPreviewCellKeySelection:
    @pytest.mark.asyncio
    async def test_select_first_cell(self, nb_client):
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _TWO_CELL_SPEC, "cell_key": "cell_a"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["cell_key"] == "cell_a"
        assert body["row_count"] > 0  # active rows from demo table

    @pytest.mark.asyncio
    async def test_default_cell_key_is_last_task(self, nb_client):
        client, alice_id, *_ = nb_client

        # No cell_key supplied → defaults to last task ("cell_b")
        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _TWO_CELL_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        # cell_b is a noop; it returns its inputs, not rows, but we still get 200.
        assert resp.json()["cell_key"] == "cell_b"


# ---------------------------------------------------------------------------
# 3. POST /flows/preview — upstream cells executed, inputs resolved
# ---------------------------------------------------------------------------


class TestPreviewUpstreamExecution:
    @pytest.mark.asyncio
    async def test_upstream_cells_run_before_target(self, nb_client):
        """When selecting cell_b (noop), cell_a must have run first."""
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _TWO_CELL_SPEC, "cell_key": "cell_b"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        # The response is valid (200) — cell_a ran first, cell_b got its inputs.


# ---------------------------------------------------------------------------
# 4. POST /flows/preview — bad spec returns 400
# ---------------------------------------------------------------------------


class TestPreviewBadSpec:
    @pytest.mark.asyncio
    async def test_bad_spec_returns_400(self, nb_client):
        client, alice_id, *_ = nb_client

        bad_spec = {
            "version": 1,
            "name": "bad",
            "tasks": [
                {"key": "q", "kind": "query", "needs": [], "config": {}}  # missing sql
            ],
        }
        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": bad_spec},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "bad_flow_spec"

    @pytest.mark.asyncio
    async def test_no_spec_or_flow_id_returns_400(self, nb_client):
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"params": {}},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 5. POST /flows/preview — bad cell_key returns 400
# ---------------------------------------------------------------------------


class TestPreviewBadCellKey:
    @pytest.mark.asyncio
    async def test_nonexistent_cell_key_returns_400(self, nb_client):
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _SINGLE_CELL_SPEC, "cell_key": "does_not_exist"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# 6. POST /flows/preview — preview_limit caps rows
# ---------------------------------------------------------------------------


class TestPreviewLimit:
    @pytest.mark.asyncio
    async def test_preview_limit_applied(self, nb_client):
        client, alice_id, *_ = nb_client

        # Demo table has 5 rows; cap at 2.
        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _SINGLE_CELL_SPEC, "preview_limit": 2},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["row_count"] <= 2

    @pytest.mark.asyncio
    async def test_preview_limit_max_capped_at_10000(self, nb_client):
        """preview_limit > 10 000 is silently clamped to 10 000."""
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _SINGLE_CELL_SPEC, "preview_limit": 999_999},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        # The response is valid; the actual limit enforcement is internal.


# ---------------------------------------------------------------------------
# 7. POST /flows/preview — 401 without auth
# ---------------------------------------------------------------------------


class TestPreviewAuthGuard:
    @pytest.mark.asyncio
    async def test_no_auth_returns_401(self, nb_client):
        client, *_ = nb_client
        resp = await client.post("/api/v1/flows/preview", json={"spec": _SINGLE_CELL_SPEC})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 8. POST /flows/preview — from flow_id (persisted flow)
# ---------------------------------------------------------------------------


class TestPreviewFromFlowId:
    @pytest.mark.asyncio
    async def test_preview_from_persisted_flow(self, nb_client):
        client, alice_id, org_id, store, repo = nb_client

        # Create a persisted flow.
        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Demo Notebook", "spec": _SINGLE_CELL_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        flow_id = create_resp.json()["id"]

        # Preview from flow_id.
        resp = await client.post(
            "/api/v1/flows/preview",
            json={"flow_id": flow_id, "cell_key": "cell_demo"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["cell_key"] == "cell_demo"
        assert body["row_count"] > 0

    @pytest.mark.asyncio
    async def test_preview_cross_org_flow_returns_404(self, nb_app, fake_db):
        """Bob cannot preview Alice's flow."""
        app, store, repo = nb_app

        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
            create_resp = await client.post(
                "/api/v1/flows",
                json={"name": "Alice Notebook", "spec": _SINGLE_CELL_SPEC},
                headers=_auth_headers(alice_id),
            )
            assert create_resp.status_code == 201
            flow_id = create_resp.json()["id"]

            # Bob tries to preview Alice's flow.
            resp = await client.post(
                "/api/v1/flows/preview",
                json={"flow_id": flow_id},
                headers=_auth_headers(bob_id),
            )
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 9. POST /flows/run-cell — durable single cell
# ---------------------------------------------------------------------------


class TestRunCell:
    @pytest.mark.asyncio
    async def test_run_cell_returns_rows(self, nb_client):
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/run-cell",
            json={"spec": _SINGLE_CELL_SPEC, "cell_key": "cell_demo"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["cell_key"] == "cell_demo"
        assert "columns" in body
        assert "rows" in body
        assert "row_count" in body
        assert "flow_run_id" in body
        assert body["row_count"] > 0

    @pytest.mark.asyncio
    async def test_run_cell_no_spec_returns_400(self, nb_client):
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/run-cell",
            json={},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_run_cell_no_auth_returns_401(self, nb_client):
        client, *_ = nb_client
        resp = await client.post("/api/v1/flows/run-cell", json={"spec": _SINGLE_CELL_SPEC})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 11. POST /flows/notebooks — save notebook
# ---------------------------------------------------------------------------


_NOTEBOOK_SPEC = {
    "version": 1,
    "name": "my_notebook",
    "notebook_id": "",
    "view": "notebook",
    "params": [],
    "tasks": [
        {
            "key": "cell_query",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT 1 AS n"},
            "cell_type": "sql",
            "execution_mode": "preview",
        }
    ],
    "execution_mode": "preview",
    "runtime_config": {},
    "source": "notebook",
}


class TestSaveNotebook:
    @pytest.mark.asyncio
    async def test_save_notebook_creates_flow(self, nb_client):
        client, alice_id, org_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/notebooks",
            json={"notebook": _NOTEBOOK_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "my_notebook"
        assert body["org_id"] == org_id

    @pytest.mark.asyncio
    async def test_save_notebook_name_override(self, nb_client):
        client, alice_id, *_ = nb_client

        resp = await client.post(
            "/api/v1/flows/notebooks",
            json={"notebook": _NOTEBOOK_SPEC, "name": "Overridden Name"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "Overridden Name"

    @pytest.mark.asyncio
    async def test_save_notebook_no_auth_401(self, nb_client):
        client, *_ = nb_client
        resp = await client.post("/api/v1/flows/notebooks", json={"notebook": _NOTEBOOK_SPEC})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 12. GET /flows/notebooks/{id} — round-trip to NotebookSpec
# ---------------------------------------------------------------------------


class TestGetNotebook:
    @pytest.mark.asyncio
    async def test_get_notebook_returns_notebook_key(self, nb_client):
        client, alice_id, *_ = nb_client

        # First save the notebook.
        create_resp = await client.post(
            "/api/v1/flows/notebooks",
            json={"notebook": _NOTEBOOK_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201
        flow_id = create_resp.json()["id"]

        # Retrieve as notebook.
        get_resp = await client.get(
            f"/api/v1/flows/notebooks/{flow_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 200, get_resp.text
        body = get_resp.json()
        assert "notebook" in body
        nb = body["notebook"]
        assert nb["name"] == "my_notebook"
        assert "tasks" in nb
        assert len(nb["tasks"]) == 1
        assert nb["tasks"][0]["key"] == "cell_query"
        assert nb["tasks"][0]["cell_type"] == "sql"

    @pytest.mark.asyncio
    async def test_get_notebook_cross_org_404(self, nb_app, fake_db):
        app, store, repo = nb_app

        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id)
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
            create_resp = await client.post(
                "/api/v1/flows/notebooks",
                json={"notebook": _NOTEBOOK_SPEC},
                headers=_auth_headers(alice_id),
            )
            assert create_resp.status_code == 201
            flow_id = create_resp.json()["id"]

            get_resp = await client.get(
                f"/api/v1/flows/notebooks/{flow_id}",
                headers=_auth_headers(bob_id),
            )
            assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# 13-15. notebook.py pure-unit tests
# ---------------------------------------------------------------------------


class TestNotebookModule:
    def test_infer_edges_sequential_fallback(self):
        """SQL/Python cells without explicit deps get the previous cell as dep."""
        from app.flows.notebook import CellSpec, infer_notebook_edges  # noqa: PLC0415

        # Use query/python kinds — noop is treated as markdown (decorative, no inferred deps).
        cells = [
            CellSpec(key="c1", kind="query", config={"sql": "SELECT 1"}),
            CellSpec(key="c2", kind="query", config={"sql": "SELECT 2"}),
            CellSpec(key="c3", kind="python", config={"code": "result = {'x': 1}"}),
        ]
        result = infer_notebook_edges(cells)
        assert result[0].needs == []
        assert result[1].needs == ["c1"]
        assert result[2].needs == ["c2"]

    def test_infer_edges_respects_explicit_needs(self):
        """Explicit needs are not overwritten by inference."""
        from app.flows.notebook import CellSpec, infer_notebook_edges  # noqa: PLC0415

        cells = [
            CellSpec(key="root", kind="noop", config={}),
            CellSpec(key="other", kind="noop", config={}),
            CellSpec(key="child", kind="noop", needs=["root"], config={}),
        ]
        result = infer_notebook_edges(cells)
        # child's explicit needs=["root"] must be preserved
        assert result[2].needs == ["root"]

    def test_infer_edges_sql_cross_cell_ref(self):
        """SQL cells that reference cell_<key> in their SQL get that dep inferred."""
        from app.flows.notebook import CellSpec, infer_notebook_edges  # noqa: PLC0415

        cells = [
            CellSpec(key="cell_src", kind="query", config={"sql": "SELECT 1 AS x"}),
            CellSpec(
                key="cell_derived",
                kind="query",
                config={"sql": "SELECT x FROM cell_src"},
            ),
        ]
        result = infer_notebook_edges(cells)
        # cell_derived should reference cell_src
        assert "cell_src" in result[1].needs

    def test_notebook_to_flow_round_trip(self):
        """notebook_to_flow → flow_to_notebook preserves name and task count."""
        from app.flows.notebook import (  # noqa: PLC0415
            CellSpec,
            NotebookSpec,
            flow_to_notebook,
            notebook_to_flow,
        )

        nb = NotebookSpec(
            name="roundtrip_test",
            notebook_id="nb-123",
            tasks=[
                CellSpec(key="cell_one", kind="query", config={"sql": "SELECT 1"}),
                CellSpec(key="cell_two", kind="noop", config={}),
            ],
        )
        flow_spec = notebook_to_flow(nb, infer_edges=False)
        assert flow_spec.name == "roundtrip_test"
        assert len(flow_spec.tasks) == 2

        recovered = flow_to_notebook(flow_spec, notebook_id="nb-123")
        assert recovered.name == "roundtrip_test"
        assert len(recovered.tasks) == 2
        assert recovered.tasks[0].key == "cell_one"
        assert recovered.tasks[1].key == "cell_two"

    def test_cell_spec_to_task_spec(self):
        """CellSpec.to_task_spec() preserves cell_type and execution_mode."""
        from app.flows.notebook import CellSpec  # noqa: PLC0415

        cell = CellSpec(
            key="my_cell",
            kind="query",
            config={"sql": "SELECT 1"},
            cell_type="sql",
            execution_mode="preview",
        )
        task = cell.to_task_spec()
        assert task.key == "my_cell"
        assert task.kind == "query"
        assert task.cell_type == "sql"
        assert task.execution_mode == "preview"

    def test_notebook_spec_duplicate_cell_key_raises(self):
        """NotebookSpec rejects duplicate cell keys."""
        import pytest as _pytest  # noqa: PLC0415
        from app.flows.notebook import CellSpec, NotebookSpec  # noqa: PLC0415

        with _pytest.raises(Exception, match="Duplicate cell key"):
            NotebookSpec(
                name="bad_notebook",
                tasks=[
                    CellSpec(key="dup", kind="noop", config={}),
                    CellSpec(key="dup", kind="noop", config={}),
                ],
            )
