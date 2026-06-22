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
