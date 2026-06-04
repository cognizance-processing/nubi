"""Tests for the canonical Dashboard SPEC layer (Wave EDITOR-2A).

Coverage
--------
1. validate_spec
   a. Accepts a fully valid spec.
   b. Rejects chart widget missing chart_type.
   c. Rejects duplicate widget ids.
   d. Warns (not hard-fail) on unknown query_id.
   e. Returns None + issues for a Pydantic parse failure.

2. spec_to_html
   a. Emits nubi-kpi / nubi-table / nubi-chart widgets.
   b. References the query_ids from the spec.
   c. Does NOT contain <script> tags.
   d. Does NOT contain on*= inline event handlers.
   e. Survives a basic sanitize check (no forbidden tags / attrs).
   f. Grid positions appear in inline style.

3. spec_json_schema
   a. Returns a dict with a 'properties' key.
   b. Contains 'widgets' and 'title' properties.

4. generate_dashboard_spec (NullProvider)
   a. Returns a valid DashboardSpec.
   b. Widgets reference a REAL registered query_id.
   c. Chart widget has chart_type and x/y encoding.
   d. Spec passes validate_spec with no hard errors.
   e. spec_to_html output passes validate_dashboard_html.

5. POST /ai/dashboard endpoint (EDITOR-2A)
   a. Returns spec + html in response body (200 with auth).
   b. 401 without auth.
   c. spec is a dict with 'widgets', 'title', 'version'.
   d. html contains nubi-* widgets.

6. GET /ai/dashboard/schema
   a. Returns JSON Schema dict (200 with auth).
   b. 401 without auth.
   c. Schema has 'properties' key.
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.dashboard import generate_dashboard_spec
from app.ai.grounding import build_catalog
from app.ai.provider import NullProvider
from app.auth.jwt import mint_access_token
from app.dashboards.spec import (
    DashboardSpec,
    Widget,
    WidgetPos,
    spec_json_schema,
    spec_to_html,
    validate_spec,
)
from app.queries.registry import get_query_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "spec-tester@example.com",
        "name": "Spec Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _good_spec_dict() -> dict[str, Any]:
    """Return a valid DashboardSpec dict using real registered query_ids."""
    return {
        "version": 1,
        "title": "Test Dashboard",
        "layout": {"cols": 12, "row_height": 60},
        "widgets": [
            {
                "id": "w1",
                "type": "kpi",
                "query_id": "demo_all",
                "encoding": {"value": "id"},
                "props": {"label": "Count"},
                "pos": {"x": 1, "y": 1, "w": 4, "h": 2},
            },
            {
                "id": "w2",
                "type": "table",
                "query_id": "demo_all",
                "encoding": {},
                "props": {"limit": 50},
                "pos": {"x": 5, "y": 1, "w": 8, "h": 2},
            },
            {
                "id": "w3",
                "type": "chart",
                "query_id": "demo_points_10k",
                "chart_type": "scatter",
                "encoding": {"x": "x", "y": "y", "color": "category"},
                "props": {},
                "pos": {"x": 1, "y": 3, "w": 12, "h": 3},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def spec_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for spec endpoint tests."""
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
# 1. validate_spec
# ---------------------------------------------------------------------------


class TestValidateSpec:
    """validate_spec correctly parses and validates DashboardSpec dicts."""

    def test_accepts_good_spec(self):
        spec, issues = validate_spec(_good_spec_dict())
        assert spec is not None, f"Expected valid spec, got issues: {issues}"
        assert isinstance(spec, DashboardSpec)
        # Registry warnings are soft — filter out only hard parse errors.
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"

    def test_good_spec_has_correct_structure(self):
        spec, _ = validate_spec(_good_spec_dict())
        assert spec is not None
        assert spec.title == "Test Dashboard"
        assert spec.version == 1
        assert len(spec.widgets) == 3

    def test_rejects_chart_missing_chart_type(self):
        data = _good_spec_dict()
        # Remove chart_type from the chart widget.
        data["widgets"][2]["chart_type"] = None
        spec, issues = validate_spec(data)
        # Should still parse but have an issue about chart_type.
        assert any("chart_type" in i for i in issues), (
            f"Expected chart_type issue, got: {issues}"
        )

    def test_rejects_chart_missing_x_encoding(self):
        data = _good_spec_dict()
        data["widgets"][2]["encoding"] = {"y": "y"}  # missing x
        spec, issues = validate_spec(data)
        assert any("'x'" in i or "encoding" in i for i in issues), (
            f"Expected encoding x issue, got: {issues}"
        )

    def test_rejects_chart_missing_y_encoding(self):
        data = _good_spec_dict()
        data["widgets"][2]["encoding"] = {"x": "x"}  # missing y
        spec, issues = validate_spec(data)
        assert any("'y'" in i or "encoding" in i for i in issues), (
            f"Expected encoding y issue, got: {issues}"
        )

    def test_rejects_duplicate_ids(self):
        data = _good_spec_dict()
        data["widgets"][1]["id"] = "w1"  # duplicate of w1
        spec, issues = validate_spec(data)
        assert any("duplicate" in i.lower() or "w1" in i for i in issues), (
            f"Expected duplicate id issue, got: {issues}"
        )

    def test_warns_unknown_query_id(self):
        data = _good_spec_dict()
        data["widgets"][0]["query_id"] = "does_not_exist_xyz"
        spec, issues = validate_spec(data)
        # Should be a soft warning, not a hard parse failure.
        assert spec is not None, "Should still parse with unknown query_id (soft warn)"
        assert any("does_not_exist_xyz" in i for i in issues), (
            f"Expected unknown query_id warning, got: {issues}"
        )

    def test_returns_none_for_pydantic_failure(self):
        bad_data = {"title": "x", "widgets": [{"id": "", "type": "bad", "query_id": "q", "pos": {}}]}
        spec, issues = validate_spec(bad_data)
        assert spec is None
        assert len(issues) > 0

    def test_returns_none_for_missing_required_field(self):
        # Missing 'title' — required field.
        data = _good_spec_dict()
        del data["title"]
        spec, issues = validate_spec(data)
        assert spec is None
        assert len(issues) > 0


# ---------------------------------------------------------------------------
# 2. spec_to_html
# ---------------------------------------------------------------------------


class TestSpecToHtml:
    """spec_to_html compiles a DashboardSpec to safe nubi-* HTML."""

    def _compile(self) -> str:
        spec, _ = validate_spec(_good_spec_dict())
        assert spec is not None
        return spec_to_html(spec)

    def test_contains_nubi_kpi(self):
        html = self._compile()
        assert "<nubi-kpi" in html, f"Expected <nubi-kpi in output:\n{html[:400]}"

    def test_contains_nubi_table(self):
        html = self._compile()
        assert "<nubi-table" in html, f"Expected <nubi-table in output:\n{html[:400]}"

    def test_contains_nubi_chart(self):
        html = self._compile()
        assert "<nubi-chart" in html, f"Expected <nubi-chart in output:\n{html[:400]}"

    def test_references_query_ids(self):
        html = self._compile()
        assert 'query-id="demo_all"' in html
        assert 'query-id="demo_points_10k"' in html

    def test_no_script_tag(self):
        html = self._compile()
        assert "<script" not in html.lower(), "Output must not contain <script>"

    def test_no_inline_event_handlers(self):
        html = self._compile()
        match = re.search(r"\bon\w+=", html, re.IGNORECASE)
        assert match is None, f"Output contains inline handler: {match.group()!r}"

    def test_no_javascript_uri(self):
        html = self._compile()
        assert "javascript:" not in html.lower()

    def test_grid_column_in_style(self):
        """Grid position should appear in inline style attributes."""
        html = self._compile()
        assert "grid-column:" in html, "Expected grid-column in inline style"
        assert "grid-row:" in html, "Expected grid-row in inline style"

    def test_chart_type_attribute(self):
        """Chart type should appear as the type attribute on nubi-chart."""
        html = self._compile()
        assert 'type="scatter"' in html, "Expected type=scatter on nubi-chart"

    def test_chart_x_y_attributes(self):
        """Chart x and y columns should appear as attributes."""
        html = self._compile()
        assert 'x="x"' in html, "Expected x=x on nubi-chart"
        assert 'y="y"' in html, "Expected y=y on nubi-chart"

    def test_survives_validate_dashboard_html(self):
        """spec_to_html output must pass the server-side HTML validator."""
        from app.ai.dashboard import validate_dashboard_html

        html = self._compile()
        ok, issues = validate_dashboard_html(html)
        assert ok is True, f"spec_to_html output failed validation: {issues}"

    def test_output_starts_with_nubi_dashboard_div(self):
        html = self._compile()
        assert html.strip().startswith('<div class="nubi-dashboard"'), (
            f"Output should start with nubi-dashboard div. Got: {html[:100]}"
        )

    def test_html_escaping_in_title(self):
        """HTML special characters in title must be escaped."""
        spec = DashboardSpec(
            title='<script>alert("xss")</script>',
            widgets=[],
        )
        html = spec_to_html(spec)
        assert "<script>" not in html, "Title must be HTML-escaped"
        assert "&lt;script&gt;" in html or "script" not in html.lower()

    def test_area_chart_type_maps_to_line(self):
        """area chart_type should map to 'line' for nubi-chart compatibility."""
        data = _good_spec_dict()
        data["widgets"][2]["chart_type"] = "area"
        spec, _ = validate_spec(data)
        assert spec is not None
        html = spec_to_html(spec)
        # Should not emit type="area" — should emit type="line"
        assert 'type="area"' not in html
        assert 'type="line"' in html

    def test_color_encoding_in_chart(self):
        """color encoding should appear as color attribute on nubi-chart."""
        html = self._compile()
        assert 'color="category"' in html, "Expected color=category on nubi-chart"


# ---------------------------------------------------------------------------
# 3. spec_json_schema
# ---------------------------------------------------------------------------


class TestSpecJsonSchema:
    """spec_json_schema returns a usable JSON Schema dict."""

    def test_returns_dict(self):
        schema = spec_json_schema()
        assert isinstance(schema, dict)

    def test_has_properties_key(self):
        schema = spec_json_schema()
        assert "properties" in schema, f"Expected 'properties' in schema: {schema}"

    def test_has_title_property(self):
        schema = spec_json_schema()
        props = schema.get("properties", {})
        assert "title" in props, f"Expected 'title' in schema properties: {props}"

    def test_has_widgets_property(self):
        schema = spec_json_schema()
        props = schema.get("properties", {})
        assert "widgets" in props, f"Expected 'widgets' in schema properties: {props}"

    def test_has_version_property(self):
        schema = spec_json_schema()
        props = schema.get("properties", {})
        assert "version" in props, f"Expected 'version' in schema properties: {props}"


# ---------------------------------------------------------------------------
# 4. generate_dashboard_spec (NullProvider)
# ---------------------------------------------------------------------------


class TestGenerateDashboardSpecNullProvider:
    """generate_dashboard_spec with NullProvider returns a real spec."""

    def _gen(self, question: str = "show me demo data") -> DashboardSpec:
        catalog = build_catalog()
        return generate_dashboard_spec(question, catalog, NullProvider())

    def test_returns_dashboard_spec(self):
        spec = self._gen()
        assert isinstance(spec, DashboardSpec)

    def test_has_title(self):
        spec = self._gen("show me demo data")
        assert isinstance(spec.title, str)
        assert len(spec.title) > 0

    def test_has_widgets(self):
        spec = self._gen()
        assert len(spec.widgets) > 0

    def test_widgets_reference_real_query_ids(self):
        """All widget query_ids must be actually registered."""
        spec = self._gen()
        registry = get_query_registry()
        known_ids = {rq.id for rq in registry.all()}

        for widget in spec.widgets:
            assert widget.query_id in known_ids, (
                f"Widget {widget.id!r} references unregistered query_id "
                f"{widget.query_id!r}. Known: {sorted(known_ids)}"
            )

    def test_chart_widget_has_chart_type(self):
        """Chart widgets must have a chart_type."""
        spec = self._gen()
        chart_widgets = [w for w in spec.widgets if w.type == "chart"]
        assert len(chart_widgets) > 0, "Expected at least one chart widget"
        for w in chart_widgets:
            assert w.chart_type is not None, (
                f"Chart widget {w.id!r} has no chart_type"
            )

    def test_chart_widget_has_x_y_encoding(self):
        """Chart widgets must have x and y in encoding."""
        spec = self._gen()
        chart_widgets = [w for w in spec.widgets if w.type == "chart"]
        for w in chart_widgets:
            assert "x" in w.encoding, f"Chart widget {w.id!r} missing x encoding"
            assert "y" in w.encoding, f"Chart widget {w.id!r} missing y encoding"

    def test_spec_passes_validate_spec(self):
        """The generated spec must pass validate_spec with no hard errors."""
        spec = self._gen()
        _, issues = validate_spec(spec.model_dump())
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert hard_issues == [], f"Generated spec has hard issues: {hard_issues}"

    def test_html_passes_validate_dashboard_html(self):
        """HTML compiled from the spec must pass the security validator."""
        from app.ai.dashboard import validate_dashboard_html

        spec = self._gen()
        html = spec_to_html(spec)
        ok, issues = validate_dashboard_html(html)
        assert ok is True, f"Compiled HTML failed validation: {issues}"

    def test_deterministic_output(self):
        """Calling twice with the same question yields identical specs."""
        catalog = build_catalog()
        s1 = generate_dashboard_spec("list demo rows", catalog, NullProvider())
        s2 = generate_dashboard_spec("list demo rows", catalog, NullProvider())
        assert s1.model_dump() == s2.model_dump()


# ---------------------------------------------------------------------------
# 5. POST /ai/dashboard endpoint (EDITOR-2A)
# ---------------------------------------------------------------------------


class TestDashboardEndpointSpec:
    """HTTP endpoint POST /ai/dashboard now returns spec + html."""

    def _clear_llm_env(self, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_requires_auth(self, spec_client):
        ac, _ = spec_client
        resp = await ac.post("/api/v1/ai/dashboard", json={"question": "show me data"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_with_auth(self, spec_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        ac, user_id = spec_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_has_spec_key(self, spec_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        ac, user_id = spec_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "spec" in body, f"Expected 'spec' key in response: {list(body.keys())}"
        assert isinstance(body["spec"], dict)

    @pytest.mark.asyncio
    async def test_spec_has_required_fields(self, spec_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        ac, user_id = spec_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        spec = resp.json()["spec"]
        assert "title" in spec, f"spec missing 'title': {spec}"
        assert "version" in spec, f"spec missing 'version': {spec}"
        assert "widgets" in spec, f"spec missing 'widgets': {spec}"
        assert isinstance(spec["widgets"], list)

    @pytest.mark.asyncio
    async def test_response_has_html_key(self, spec_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        ac, user_id = spec_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "html" in body
        assert isinstance(body["html"], str)
        assert len(body["html"]) > 0

    @pytest.mark.asyncio
    async def test_html_contains_nubi_widget(self, spec_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        ac, user_id = spec_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        html = resp.json()["html"]
        assert "<nubi-table" in html or "<nubi-chart" in html or "<nubi-kpi" in html, (
            f"Expected at least one nubi-* widget in html:\n{html[:400]}"
        )

    @pytest.mark.asyncio
    async def test_valid_is_true(self, spec_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        ac, user_id = spec_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert body["valid"] is True, f"Expected valid=True, got issues: {body.get('issues')}"


# ---------------------------------------------------------------------------
# 6. GET /ai/dashboard/schema
# ---------------------------------------------------------------------------


class TestDashboardSchemaEndpoint:
    """GET /ai/dashboard/schema returns the JSON Schema for DashboardSpec."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, spec_client):
        ac, _ = spec_client
        resp = await ac.get("/api/v1/ai/dashboard/schema")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_with_auth(self, spec_client):
        ac, user_id = spec_client
        resp = await ac.get(
            "/api/v1/ai/dashboard/schema",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_schema_has_properties(self, spec_client):
        ac, user_id = spec_client
        resp = await ac.get(
            "/api/v1/ai/dashboard/schema",
            headers=_auth_headers(user_id),
        )
        schema = resp.json()
        assert isinstance(schema, dict)
        assert "properties" in schema, f"Schema missing 'properties': {list(schema.keys())}"

    @pytest.mark.asyncio
    async def test_schema_has_widgets_and_title(self, spec_client):
        ac, user_id = spec_client
        resp = await ac.get(
            "/api/v1/ai/dashboard/schema",
            headers=_auth_headers(user_id),
        )
        props = resp.json().get("properties", {})
        assert "widgets" in props
        assert "title" in props
