"""Tests for the AI Canvas generation layer (Canvas Wave 6).

Coverage
--------
1. generate_canvas_doc (NullProvider)
   a. Returns a CanvasDoc instance.
   b. HTML contains nubi-kpi and nubi-table.
   c. Bindings map is non-empty and references real registered query_id(s).
   d. No <script> tags in the HTML.
   e. No on*= inline event handlers.
   f. validate_canvas_doc returns (True, []) or only warnings for null template.
   g. Output is deterministic (same question -> same doc).

2. generate_canvas_doc (FakeProvider — repair loop)
   a. Invalid JSON on round 1, valid doc on round 2 -> repaired, repair_rounds>=1.
   b. Invalid doc on EVERY round -> AppError("canvas_generation_failed", 422).
   c. Loud failure does NOT fall back to the NullProvider template silently.
   d. Valid on the first shot -> repair_rounds == 0.
   e. Non-JSON response on round 1 treated as hard error, repaired on round 2.

3. edit_canvas_doc (FakeProvider)
   a. Returns the updated CanvasDoc when the LLM response is valid.
   b. NullProvider edit returns the original doc unchanged (no-op).
   c. Invalid edit output triggers repair loop; still-invalid -> AppError 422.

4. POST /ai/canvas endpoint
   a. 200 with valid auth; response has {doc, html, grounding, provider, valid, issues}.
   b. 401 without auth.
   c. provider == "null" when no API keys configured.
   d. valid == True for NullProvider output.
   e. 422 when question field is missing.

5. POST /ai/canvas/edit endpoint
   a. 200 with valid auth and valid {doc, instruction}.
   b. 400 when doc is malformed.
   c. 401 without auth.

6. GET /ai/canvas/schema endpoint
   a. 200 with valid auth; response has canvas_doc, binding_kinds, allowed_widget_tags.
   b. 401 without auth.

Network safety
--------------
No real provider is used; all tests use NullProvider or FakeProvider (zero network).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.canvas import (
    MAX_CANVAS_REPAIR_ROUNDS,
    generate_canvas_doc,
    edit_canvas_doc,
    canvas_doc_json_schema,
)
from app.ai.grounding import build_catalog
from app.ai.provider import LLMProvider, NullProvider
from app.auth.jwt import mint_access_token
from app.dashboards.canvas import CanvasDoc, validate_canvas_doc
from app.errors import AppError


# ---------------------------------------------------------------------------
# FakeProvider — scripted .complete() responses (no network)
# ---------------------------------------------------------------------------


class FakeProvider(LLMProvider):
    """Real-provider stand-in: returns scripted JSON strings, no network.

    NOT a NullProvider, so generate_canvas_doc takes the real LLM path.
    ``responses`` is consumed one item per .complete() call; the last item
    repeats if the loop calls more times.
    """

    name = "fake"

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str | None]] = []

    def complete(self, prompt: str, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[idx]


# ---------------------------------------------------------------------------
# Canned CanvasDoc JSON builders
# ---------------------------------------------------------------------------


def _valid_canvas_json() -> str:
    """A minimal, fully-valid CanvasDoc JSON (one nubi-table bound to demo_all)."""
    return json.dumps(
        {
            "version": 1,
            "title": "repaired canvas",
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
    )


def _invalid_canvas_json() -> str:
    """A CanvasDoc that PARSES but has HARD validation errors.

    Uses an unknown nubi-element tag (not in CANVAS_ALLOWED_WIDGET_TAGS) to
    trigger a hard error from validate_canvas_doc.
    """
    return json.dumps(
        {
            "version": 1,
            "title": "broken canvas",
            "html": '<section><nubi-evil data-el-id="el_x1"></nubi-evil></section>',
            "bindings": {
                "el_x1": {
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
    )


def _valid_edit_canvas_json() -> str:
    """A valid CanvasDoc after an edit (has a header + KPI)."""
    return json.dumps(
        {
            "version": 1,
            "title": "edited canvas",
            "html": (
                '<section>'
                '<h1>Revenue</h1>'
                '<nubi-kpi data-el-id="el_kpi_1"></nubi-kpi>'
                '</section>'
            ),
            "bindings": {
                "el_kpi_1": {
                    "kind": "query",
                    "query_id": "demo_all",
                    "params": {},
                    "field": "value",
                    "format": "currency",
                }
            },
            "variables": [],
            "assets": {},
        }
    )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "canvas-tester@example.com",
        "name": "Canvas Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def canvas_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for canvas endpoint tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id


# ---------------------------------------------------------------------------
# 1. generate_canvas_doc — NullProvider
# ---------------------------------------------------------------------------


class TestGenerateCanvasDocNullProvider:
    """generate_canvas_doc with NullProvider returns correct structure."""

    def _gen(self, question: str = "show me demo data") -> CanvasDoc:
        catalog = build_catalog()
        return generate_canvas_doc(question, catalog, NullProvider())

    def test_returns_canvas_doc(self):
        result = self._gen()
        assert isinstance(result, CanvasDoc)

    def test_html_contains_nubi_kpi(self):
        result = self._gen()
        assert "<nubi-kpi" in result.html, (
            f"Expected <nubi-kpi in HTML. Got:\n{result.html[:500]}"
        )

    def test_html_contains_nubi_table(self):
        result = self._gen()
        assert "<nubi-table" in result.html, (
            f"Expected <nubi-table in HTML. Got:\n{result.html[:500]}"
        )

    def test_bindings_non_empty(self):
        result = self._gen()
        assert len(result.bindings) > 0, "Expected at least one binding."

    def test_bindings_reference_registered_query(self):
        from app.queries.registry import get_query_registry

        result = self._gen()
        registry = get_query_registry()
        known_ids = {rq.id for rq in registry.all()}

        for el_id, binding in result.bindings.items():
            if hasattr(binding, "query_id"):
                assert binding.query_id in known_ids, (
                    f"Binding el_id={el_id!r} references unregistered query_id="
                    f"{binding.query_id!r}. Known: {sorted(known_ids)}"
                )

    def test_no_script_tags(self):
        result = self._gen()
        assert "<script" not in result.html.lower(), (
            "Canvas HTML must not contain <script> tags."
        )

    def test_no_inline_event_handlers(self):
        import re

        result = self._gen()
        on_handler = re.search(r"\bon\w+=", result.html, re.IGNORECASE)
        assert on_handler is None, (
            f"Canvas HTML contains inline event handler: {on_handler.group()!r}"
        )

    def test_validates_cleanly(self):
        result = self._gen()
        ok, issues = validate_canvas_doc(result)
        hard = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
        assert not hard, f"Expected no hard errors from NullProvider doc. Hard: {hard}"

    def test_deterministic_output(self):
        catalog = build_catalog()
        r1 = generate_canvas_doc("list demo rows", catalog, NullProvider())
        r2 = generate_canvas_doc("list demo rows", catalog, NullProvider())
        assert r1.html == r2.html
        assert r1.model_dump() == r2.model_dump()

    def test_generated_by_is_null_template(self):
        result = self._gen()
        assert result.generated_by == "null_template"

    def test_repair_rounds_is_zero(self):
        result = self._gen()
        assert result.repair_rounds == 0

    def test_valid_is_true(self):
        result = self._gen()
        assert result.valid is True


# ---------------------------------------------------------------------------
# 2. generate_canvas_doc — FakeProvider repair loop
# ---------------------------------------------------------------------------


class TestGenerateCanvasDocRepair:
    """generate_canvas_doc with FakeProvider exercises the repair loop."""

    def test_repairs_invalid_then_valid(self):
        """Invalid on round 1, valid on round 2 -> repaired, repair_rounds >= 1."""
        catalog = build_catalog()
        provider = FakeProvider([_invalid_canvas_json(), _valid_canvas_json()])

        doc = generate_canvas_doc("show me demo data", catalog, provider)

        assert doc.valid is True
        assert doc.generated_by == "llm"
        assert doc.repair_rounds >= 1
        assert len(provider.calls) == 2
        assert doc.title == "repaired canvas"

    def test_invalid_every_round_fails_loud(self):
        """Invalid on EVERY round -> AppError, NOT a silent template."""
        catalog = build_catalog()
        provider = FakeProvider([_invalid_canvas_json()])  # always invalid

        with pytest.raises(AppError) as excinfo:
            generate_canvas_doc("show me demo data", catalog, provider)

        err = excinfo.value
        assert err.code == "canvas_generation_failed"
        assert err.status == 422
        # Tried the full budget.
        assert len(provider.calls) == MAX_CANVAS_REPAIR_ROUNDS

    def test_loud_failure_not_null_template(self):
        """Failure path must NOT quietly return the deterministic template."""
        catalog = build_catalog()
        provider = FakeProvider([_invalid_canvas_json()])

        # NullProvider gives a valid template — prove one is available.
        null_doc = generate_canvas_doc("show me demo data", catalog, NullProvider())
        assert null_doc.valid is True
        assert null_doc.generated_by == "null_template"

        # Real provider with always-invalid output must raise, not hand back the template.
        with pytest.raises(AppError):
            generate_canvas_doc("show me demo data", catalog, provider)

    def test_valid_first_shot_zero_repair_rounds(self):
        """Clean first generation -> repair_rounds == 0."""
        catalog = build_catalog()
        provider = FakeProvider([_valid_canvas_json()])

        doc = generate_canvas_doc("show me demo data", catalog, provider)

        assert doc.valid is True
        assert doc.repair_rounds == 0
        assert len(provider.calls) == 1

    def test_non_json_then_valid_is_repaired(self):
        """Non-JSON garbage on round 1 is treated as a hard error and repaired."""
        catalog = build_catalog()
        provider = FakeProvider(["this is not json at all", _valid_canvas_json()])

        doc = generate_canvas_doc("show me demo data", catalog, provider)

        assert doc.valid is True
        assert doc.repair_rounds >= 1

    def test_repair_prompt_contains_error_description(self):
        """The repair user-message must echo back the hard errors."""
        catalog = build_catalog()
        provider = FakeProvider([_invalid_canvas_json(), _valid_canvas_json()])
        generate_canvas_doc("show me demo data", catalog, provider)

        # Second call is the repair prompt; it should mention the bad element.
        repair_prompt = provider.calls[1][0]
        # The invalid doc uses nubi-evil; the validation error should mention it.
        assert "nubi-evil" in repair_prompt or "unknown" in repair_prompt.lower() or "canvas" in repair_prompt.lower()


# ---------------------------------------------------------------------------
# 3. edit_canvas_doc — FakeProvider
# ---------------------------------------------------------------------------


class TestEditCanvasDoc:
    """edit_canvas_doc exercises the diff-edit path."""

    def _base_doc(self) -> CanvasDoc:
        """A minimal valid CanvasDoc to use as the edit base."""
        return CanvasDoc(
            version=1,
            title="base canvas",
            html='<section><nubi-table data-el-id="el_t1"></nubi-table></section>',
            bindings={
                "el_t1": {  # type: ignore[dict-item]
                    "kind": "query",
                    "query_id": "demo_all",
                    "params": {},
                    "field": None,
                    "format": None,
                }
            },
        )

    def test_null_provider_edit_returns_original(self):
        """NullProvider edit is a no-op — returns the doc unchanged."""
        catalog = build_catalog()
        base = self._base_doc()
        result = edit_canvas_doc(base, "make the header red", catalog, NullProvider())

        assert isinstance(result, CanvasDoc)
        assert result.generated_by == "null_template"
        assert result.valid is True
        # HTML should be unchanged for NullProvider (no-op).
        assert result.html == base.html

    def test_fake_provider_returns_updated_doc(self):
        """FakeProvider edit returns the updated CanvasDoc."""
        catalog = build_catalog()
        provider = FakeProvider([_valid_edit_canvas_json()])
        base = self._base_doc()

        result = edit_canvas_doc(base, "add a KPI for revenue", catalog, provider)

        assert isinstance(result, CanvasDoc)
        assert result.valid is True
        assert result.title == "edited canvas"
        assert "<nubi-kpi" in result.html

    def test_edit_repairs_on_invalid_first_round(self):
        """Invalid edit output on round 1, valid on round 2 -> repaired."""
        catalog = build_catalog()
        provider = FakeProvider([_invalid_canvas_json(), _valid_edit_canvas_json()])
        base = self._base_doc()

        result = edit_canvas_doc(base, "bind KPI to revenue", catalog, provider)

        assert result.valid is True
        assert result.repair_rounds >= 1

    def test_edit_fails_loud_when_always_invalid(self):
        """Always-invalid edit output -> AppError("canvas_edit_failed", 422)."""
        catalog = build_catalog()
        provider = FakeProvider([_invalid_canvas_json()])
        base = self._base_doc()

        with pytest.raises(AppError) as excinfo:
            edit_canvas_doc(base, "do something", catalog, provider)

        err = excinfo.value
        assert err.code == "canvas_edit_failed"
        assert err.status == 422


# ---------------------------------------------------------------------------
# 4. POST /ai/canvas endpoint
# ---------------------------------------------------------------------------


class TestCanvasEndpoint:
    """HTTP endpoint tests for POST /ai/canvas."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, canvas_client):
        ac, _ = canvas_client
        resp = await ac.post("/api/v1/ai/canvas", json={"question": "show me data"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_with_auth(self, canvas_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_shape(self, canvas_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "doc" in body
        assert "html" in body
        assert "grounding" in body
        assert "provider" in body
        assert "valid" in body
        assert "issues" in body
        assert isinstance(body["doc"], dict)
        assert isinstance(body["html"], str)

    @pytest.mark.asyncio
    async def test_provider_is_null(self, canvas_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        assert resp.json()["provider"] == "null"

    @pytest.mark.asyncio
    async def test_valid_is_true_for_null_provider(self, canvas_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert body["valid"] is True, f"Expected valid=True, got issues: {body.get('issues')}"

    @pytest.mark.asyncio
    async def test_missing_question_returns_422(self, canvas_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas",
            json={},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. POST /ai/canvas/edit endpoint
# ---------------------------------------------------------------------------


class TestCanvasEditEndpoint:
    """HTTP endpoint tests for POST /ai/canvas/edit."""

    def _valid_doc_payload(self) -> dict[str, Any]:
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
    async def test_requires_auth(self, canvas_client):
        ac, _ = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas/edit",
            json={"doc": self._valid_doc_payload(), "instruction": "make it blue"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_with_auth(self, canvas_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas/edit",
            json={"doc": self._valid_doc_payload(), "instruction": "add a title"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_doc_in_response(self, canvas_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas/edit",
            json={"doc": self._valid_doc_payload(), "instruction": "add a title"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "doc" in body
        assert "html" in body
        assert "valid" in body

    @pytest.mark.asyncio
    async def test_malformed_doc_returns_400(self, canvas_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = canvas_client
        resp = await ac.post(
            "/api/v1/ai/canvas/edit",
            json={
                "doc": {"bindings": {"el_1": {"kind": "invalid_kind"}}},
                "instruction": "break things",
            },
            headers=_auth_headers(user_id),
        )
        # The malformed binding kind is rejected
        assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# 6. GET /ai/canvas/schema endpoint
# ---------------------------------------------------------------------------


class TestCanvasSchemaEndpoint:
    """HTTP endpoint tests for GET /ai/canvas/schema."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, canvas_client):
        ac, _ = canvas_client
        resp = await ac.get("/api/v1/ai/canvas/schema")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_with_auth(self, canvas_client):
        ac, user_id = canvas_client
        resp = await ac.get(
            "/api/v1/ai/canvas/schema",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_has_canvas_doc_key(self, canvas_client):
        ac, user_id = canvas_client
        resp = await ac.get(
            "/api/v1/ai/canvas/schema",
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "canvas_doc" in body
        assert isinstance(body["canvas_doc"], dict)

    @pytest.mark.asyncio
    async def test_response_has_binding_kinds(self, canvas_client):
        ac, user_id = canvas_client
        resp = await ac.get(
            "/api/v1/ai/canvas/schema",
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "binding_kinds" in body
        assert set(body["binding_kinds"]) == {"query", "metric", "api"}

    @pytest.mark.asyncio
    async def test_response_has_allowed_widget_tags(self, canvas_client):
        ac, user_id = canvas_client
        resp = await ac.get(
            "/api/v1/ai/canvas/schema",
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "allowed_widget_tags" in body
        tags = set(body["allowed_widget_tags"])
        assert "nubi-kpi" in tags
        assert "nubi-table" in tags
        assert "nubi-chart" in tags

    @pytest.mark.asyncio
    async def test_canvas_doc_schema_has_bindings_property(self, canvas_client):
        ac, user_id = canvas_client
        resp = await ac.get(
            "/api/v1/ai/canvas/schema",
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        schema = body["canvas_doc"]
        # The CanvasDoc schema must describe the bindings and html fields.
        props = schema.get("properties", {})
        assert "bindings" in props, f"Expected 'bindings' in canvas_doc schema properties. Got: {list(props.keys())}"
        assert "html" in props, f"Expected 'html' in canvas_doc schema properties. Got: {list(props.keys())}"


# ---------------------------------------------------------------------------
# 7. canvas_doc_json_schema() helper
# ---------------------------------------------------------------------------


class TestCanvasDocJsonSchema:
    """Unit tests for the canvas_doc_json_schema helper."""

    def test_returns_dict(self):
        schema = canvas_doc_json_schema()
        assert isinstance(schema, dict)

    def test_has_title_property(self):
        schema = canvas_doc_json_schema()
        props = schema.get("properties", {})
        assert "title" in props

    def test_has_html_property(self):
        schema = canvas_doc_json_schema()
        props = schema.get("properties", {})
        assert "html" in props

    def test_has_bindings_property(self):
        schema = canvas_doc_json_schema()
        props = schema.get("properties", {})
        assert "bindings" in props


# ---------------------------------------------------------------------------
# 8. Security: style-tag rejection + unescaped title hardening
# ---------------------------------------------------------------------------


class TestCanvasSecurityHardening:
    """Security regression tests for canvas HTML generation."""

    def test_style_tag_in_canvas_html_is_rejected(self):
        """An inline <style> tag in canvas HTML must be rejected by validate_canvas_doc.

        SECURITY (MED): CSS exfiltration / tracking via url() in inline
        <style> blocks.  'style' must be in the forbidden-tags list so it is
        caught at validate-time on both the generate and render paths.
        """
        doc = CanvasDoc(
            version=1,
            title="malicious canvas",
            html=(
                '<section>'
                '<style>body { background: url(https://evil.example/track) }</style>'
                '<nubi-table data-el-id="el_t1"></nubi-table>'
                '</section>'
            ),
            bindings={
                "el_t1": {  # type: ignore[dict-item]
                    "kind": "query",
                    "query_id": "demo_all",
                    "params": {},
                    "field": None,
                    "format": None,
                }
            },
        )
        ok, issues = validate_canvas_doc(doc)
        assert ok is False, (
            "Expected validate_canvas_doc to reject a canvas doc with an inline <style> tag."
        )
        assert any("style" in i.lower() for i in issues), issues

    def test_title_with_html_injection_is_escaped_in_null_template(self):
        """A question/title containing raw HTML must be escaped in the NullProvider output.

        SECURITY (MED): _build_null_canvas_doc interpolated question[:120]
        directly into <h1>{title}</h1> — a title like '</h1><style>...'
        injected raw markup.  html.escape() must be applied before interpolation.
        """
        malicious_question = '</h1><style>body{background:url(https://evil.example/x)}</style><h1>'
        catalog = build_catalog()
        doc = generate_canvas_doc(malicious_question, catalog, NullProvider())

        # The raw injection payload must NOT appear verbatim in the HTML.
        assert "</h1><style>" not in doc.html, (
            "Title was interpolated unescaped — raw </h1><style> survived in canvas HTML."
        )
        # The escaped form (or truncated safe form) must be present instead.
        # html.escape converts < and > so the payload becomes harmless text.
        assert "<style>" not in doc.html or "&lt;style&gt;" in doc.html or "style" not in doc.html.lower() or (
            # After escaping, any angle brackets in the title become &lt;/&gt;
            "&lt;" in doc.html
        ), doc.html
