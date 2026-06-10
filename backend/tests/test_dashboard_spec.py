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


# ---------------------------------------------------------------------------
# 7. M14-A — variables + filter/text widget types + widget params
# ---------------------------------------------------------------------------

# Helper spec dicts for the new widget types.


def _make_variable_spec() -> dict[str, Any]:
    """Spec with variables + a filter widget + a text widget."""
    return {
        "version": 1,
        "title": "Interactive Dashboard",
        "layout": {"cols": 12, "row_height": 60},
        "variables": [
            {"name": "region", "type": "select", "default": "all"},
            {"name": "start_date", "type": "date", "default": None},
        ],
        "widgets": [
            {
                "id": "f1",
                "type": "filter",
                "subtype": "select",
                "target_var": "region",
                "options_query_id": "demo_all",
                "query_id": "",
                "pos": {"x": 1, "y": 1, "w": 3, "h": 1},
                "props": {"label": "Region"},
            },
            {
                "id": "t1",
                "type": "text",
                "content": "## Hello\n\nThis is **markdown** content.",
                "query_id": "",
                "pos": {"x": 4, "y": 1, "w": 9, "h": 1},
            },
            {
                "id": "w1",
                "type": "kpi",
                "query_id": "demo_all",
                "encoding": {"value": "id"},
                "props": {"label": "Count"},
                "params": {"region": {"ref": "region"}},
                "pos": {"x": 1, "y": 2, "w": 4, "h": 2},
            },
        ],
    }


class TestM14Variables:
    """M14-A: spec variables field validation."""

    def test_spec_with_variables_is_valid(self):
        spec, issues = validate_spec(_make_variable_spec())
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None, f"Expected valid spec; issues: {issues}"
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"

    def test_variables_are_parsed_as_variable_objects(self):
        from app.dashboards.spec import Variable
        spec, _ = validate_spec(_make_variable_spec())
        assert spec is not None
        assert len(spec.variables) == 2
        for v in spec.variables:
            assert isinstance(v, Variable)

    def test_variable_names_and_types(self):
        spec, _ = validate_spec(_make_variable_spec())
        assert spec is not None
        names = {v.name: v for v in spec.variables}
        assert "region" in names
        assert names["region"].type == "select"
        assert names["region"].default == "all"
        assert "start_date" in names
        assert names["start_date"].type == "date"

    def test_spec_without_variables_still_valid(self):
        """Backward compat: existing specs without variables key must still parse."""
        spec, issues = validate_spec(_good_spec_dict())
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None, f"Expected valid spec; issues: {issues}"
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"
        assert spec.variables == []

    def test_variable_default_none_is_allowed(self):
        data = _make_variable_spec()
        data["variables"][0]["default"] = None
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None
        assert hard_issues == []

    def test_invalid_variable_type_rejected(self):
        data = _make_variable_spec()
        data["variables"][0]["type"] = "not_a_type"
        spec, issues = validate_spec(data)
        assert spec is None, "Expected parse failure for invalid variable type"
        assert len(issues) > 0


class TestM14FilterWidget:
    """M14-A: filter widget type validation."""

    def test_filter_widget_validates(self):
        spec, issues = validate_spec(_make_variable_spec())
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"
        filter_widgets = [w for w in spec.widgets if w.type == "filter"]
        assert len(filter_widgets) == 1

    def test_filter_widget_has_subtype(self):
        spec, _ = validate_spec(_make_variable_spec())
        assert spec is not None
        fw = next(w for w in spec.widgets if w.type == "filter")
        assert fw.subtype == "select"

    def test_filter_widget_has_target_var(self):
        spec, _ = validate_spec(_make_variable_spec())
        assert spec is not None
        fw = next(w for w in spec.widgets if w.type == "filter")
        assert fw.target_var == "region"

    def test_filter_widget_missing_subtype_produces_issue(self):
        data = _make_variable_spec()
        data["widgets"][0]["subtype"] = None
        spec, issues = validate_spec(data)
        assert any("subtype" in i for i in issues), (
            f"Expected subtype issue, got: {issues}"
        )

    def test_filter_widget_missing_target_var_produces_issue(self):
        data = _make_variable_spec()
        data["widgets"][0]["target_var"] = None
        spec, issues = validate_spec(data)
        assert any("target_var" in i for i in issues), (
            f"Expected target_var issue, got: {issues}"
        )

    def test_filter_subtypes_accepted(self):
        for subtype in ("select", "multiselect", "daterange", "text"):
            data = _make_variable_spec()
            data["widgets"][0]["subtype"] = subtype
            spec, issues = validate_spec(data)
            hard_issues = [
                i for i in issues
                if "not in the registered" not in i
                and "forward reference" not in i
                and "subtype" not in i
                and "target_var" not in i
            ]
            assert spec is not None, (
                f"subtype={subtype!r} should be accepted; issues: {issues}"
            )

    def test_invalid_filter_subtype_rejected(self):
        data = _make_variable_spec()
        data["widgets"][0]["subtype"] = "slider"
        spec, issues = validate_spec(data)
        assert spec is None, "Expected parse failure for invalid filter subtype"

    def test_options_query_id_stored(self):
        spec, _ = validate_spec(_make_variable_spec())
        assert spec is not None
        fw = next(w for w in spec.widgets if w.type == "filter")
        assert fw.options_query_id == "demo_all"


class TestM14TextWidget:
    """M14-A: text widget type validation."""

    def test_text_widget_validates(self):
        spec, issues = validate_spec(_make_variable_spec())
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"
        text_widgets = [w for w in spec.widgets if w.type == "text"]
        assert len(text_widgets) == 1

    def test_text_widget_has_content(self):
        spec, _ = validate_spec(_make_variable_spec())
        assert spec is not None
        tw = next(w for w in spec.widgets if w.type == "text")
        assert tw.content is not None
        assert "Hello" in tw.content

    def test_text_widget_missing_content_produces_issue(self):
        data = _make_variable_spec()
        # Remove content from the text widget.
        data["widgets"][1]["content"] = None
        spec, issues = validate_spec(data)
        assert any("content" in i for i in issues), (
            f"Expected content issue, got: {issues}"
        )

    def test_text_widget_empty_content_produces_issue(self):
        data = _make_variable_spec()
        data["widgets"][1]["content"] = ""
        spec, issues = validate_spec(data)
        assert any("content" in i for i in issues), (
            f"Expected content issue for empty string, got: {issues}"
        )


class TestM14WidgetParams:
    """M14-A: widget params with ref validation."""

    def test_params_ref_to_declared_var_passes(self):
        spec, issues = validate_spec(_make_variable_spec())
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"
        kpi = next(w for w in spec.widgets if w.id == "w1")
        assert kpi.params == {"region": {"ref": "region"}}

    def test_params_ref_to_undeclared_var_fails(self):
        data = _make_variable_spec()
        # Reference a variable that is NOT declared.
        data["widgets"][2]["params"] = {"country": {"ref": "country"}}
        spec, issues = validate_spec(data)
        assert any("country" in i and "not declared" in i for i in issues), (
            f"Expected undeclared var issue, got: {issues}"
        )

    def test_params_literal_value_passes(self):
        data = _make_variable_spec()
        data["widgets"][2]["params"] = {"limit": 42}
        spec, issues = validate_spec(data)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"

    def test_params_mixed_ref_and_literal_passes(self):
        data = _make_variable_spec()
        data["widgets"][2]["params"] = {
            "region": {"ref": "region"},
            "limit": 100,
        }
        spec, issues = validate_spec(data)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"

    def test_params_ref_to_one_of_multiple_declared_vars(self):
        data = _make_variable_spec()
        # Reference the second declared variable.
        data["widgets"][2]["params"] = {"date": {"ref": "start_date"}}
        spec, issues = validate_spec(data)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == [], f"Unexpected hard issues for ref to start_date: {hard_issues}"

    def test_multiple_undeclared_refs_all_reported(self):
        data = _make_variable_spec()
        data["widgets"][2]["params"] = {
            "a": {"ref": "missing_a"},
            "b": {"ref": "missing_b"},
        }
        spec, issues = validate_spec(data)
        ref_issues = [i for i in issues if "not declared" in i]
        assert len(ref_issues) >= 2, (
            f"Expected two undeclared-ref issues, got: {ref_issues}"
        )

    def test_empty_params_is_valid(self):
        data = _make_variable_spec()
        data["widgets"][2]["params"] = {}
        spec, issues = validate_spec(data)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == []

    def test_params_ref_no_variables_declared_fails(self):
        """If spec has no variables, any ref is undeclared."""
        data = _make_variable_spec()
        data["variables"] = []
        data["widgets"][2]["params"] = {"region": {"ref": "region"}}
        spec, issues = validate_spec(data)
        assert any("not declared" in i for i in issues), (
            f"Expected undeclared ref issue when no variables declared: {issues}"
        )


class TestM14SpecToHtmlNewWidgets:
    """M14-A: spec_to_html emits <nubi-filter> and <nubi-text> elements."""

    def _compile_variable_spec(self) -> str:
        spec, issues = validate_spec(_make_variable_spec())
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None, f"Spec parse failed: {issues}"
        assert hard_issues == [], f"Hard issues: {hard_issues}"
        return spec_to_html(spec)

    def test_contains_nubi_filter(self):
        output = self._compile_variable_spec()
        assert "<nubi-filter" in output, f"Expected <nubi-filter in output:\n{output[:600]}"

    def test_contains_nubi_text(self):
        output = self._compile_variable_spec()
        assert "<nubi-text>" in output or "<nubi-text " in output, (
            f"Expected <nubi-text in output:\n{output[:600]}"
        )

    def test_filter_has_subtype_attribute(self):
        output = self._compile_variable_spec()
        assert 'subtype="select"' in output, (
            f"Expected subtype=select on nubi-filter:\n{output[:600]}"
        )

    def test_filter_has_target_var_attribute(self):
        output = self._compile_variable_spec()
        assert 'target-var="region"' in output, (
            f"Expected target-var=region on nubi-filter:\n{output[:600]}"
        )

    def test_filter_has_options_query_id_attribute(self):
        output = self._compile_variable_spec()
        assert 'options-query-id="demo_all"' in output, (
            f"Expected options-query-id=demo_all on nubi-filter:\n{output[:600]}"
        )

    def test_text_has_content(self):
        output = self._compile_variable_spec()
        # The markdown should be HTML-escaped inside the element.
        assert "Hello" in output, (
            f"Expected text content in nubi-text:\n{output[:600]}"
        )

    def test_text_content_is_escaped(self):
        data = _make_variable_spec()
        data["widgets"][1]["content"] = '<script>alert("xss")</script>'
        spec, _ = validate_spec(data)
        assert spec is not None
        output = spec_to_html(spec)
        assert "<script>" not in output, "Script tag must be escaped in text content"
        assert "&lt;script&gt;" in output

    def test_no_script_tag(self):
        output = self._compile_variable_spec()
        assert "<script" not in output.lower()

    def test_no_inline_event_handler(self):
        output = self._compile_variable_spec()
        match = re.search(r"\bon\w+=", output, re.IGNORECASE)
        assert match is None, f"Output contains inline handler: {match.group()!r}"

    def test_grid_column_present(self):
        output = self._compile_variable_spec()
        assert "grid-column:" in output

    def test_nubi_widget_wrapper_classes(self):
        output = self._compile_variable_spec()
        assert 'nubi-widget--filter' in output, (
            f"Expected nubi-widget--filter class:\n{output[:600]}"
        )
        assert 'nubi-widget--text' in output, (
            f"Expected nubi-widget--text class:\n{output[:600]}"
        )

    def test_filter_label_attribute(self):
        output = self._compile_variable_spec()
        assert 'label="Region"' in output, (
            f"Expected label=Region on nubi-filter:\n{output[:600]}"
        )


class TestM14Regression:
    """M14-A regression: existing kpi/table/chart specs are unaffected."""

    def test_existing_kpi_spec_validates(self):
        data = _good_spec_dict()
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None, f"Regression: kpi spec should validate; issues: {issues}"
        assert hard_issues == [], f"Regression: unexpected hard issues: {hard_issues}"

    def test_existing_table_spec_validates(self):
        data = {
            "version": 1,
            "title": "Table Only",
            "widgets": [
                {
                    "id": "t1",
                    "type": "table",
                    "query_id": "demo_all",
                    "pos": {"x": 1, "y": 1, "w": 12, "h": 4},
                }
            ],
        }
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None, f"Regression: table spec should validate; issues: {issues}"
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"

    def test_existing_chart_spec_validates(self):
        data = {
            "version": 1,
            "title": "Chart Only",
            "widgets": [
                {
                    "id": "c1",
                    "type": "chart",
                    "query_id": "demo_points_10k",
                    "chart_type": "scatter",
                    "encoding": {"x": "x", "y": "y"},
                    "pos": {"x": 1, "y": 1, "w": 12, "h": 4},
                }
            ],
        }
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None, f"Regression: chart spec should validate; issues: {issues}"
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"

    def test_spec_to_html_still_emits_kpi_table_chart(self):
        spec, _ = validate_spec(_good_spec_dict())
        assert spec is not None
        output = spec_to_html(spec)
        assert "<nubi-kpi" in output
        assert "<nubi-table" in output
        assert "<nubi-chart" in output

    def test_spec_without_variables_field_still_parses(self):
        """Specs created before M14 (no 'variables' key) must parse without error."""
        data = _good_spec_dict()
        assert "variables" not in data
        spec, issues = validate_spec(data)
        assert spec is not None
        assert spec.variables == []

    def test_spec_json_schema_includes_variables(self):
        schema = spec_json_schema()
        props = schema.get("properties", {})
        assert "variables" in props, (
            f"Schema should expose 'variables'; props: {list(props.keys())}"
        )


# ---------------------------------------------------------------------------
# 8. Track T — dashboard tabs (DASHBOARD_TABS_AND_FILTERS_IMPLEMENTATION.md T1)
# ---------------------------------------------------------------------------


def _make_tabbed_spec() -> dict[str, Any]:
    """A spec with two tabs and widgets assigned to them."""
    return {
        "version": 1,
        "title": "Tabbed Dashboard",
        "layout": {"cols": 12, "row_height": 60},
        "tabs": [
            {"id": "t1", "label": "Overview"},
            {"id": "t2", "label": "Details", "style": {"accent": "#0af"}},
        ],
        "widgets": [
            {
                "id": "w1",
                "type": "kpi",
                "query_id": "demo_all",
                "tab_id": "t1",
                "encoding": {"value": "id"},
                "pos": {"x": 1, "y": 1, "w": 4, "h": 2},
            },
            {
                "id": "w2",
                "type": "table",
                "query_id": "demo_all",
                "tab_id": "t2",
                "pos": {"x": 1, "y": 1, "w": 12, "h": 4},
            },
        ],
    }


class TestTrackTTabs:
    """Track T: tabs field + tab_id validation."""

    def test_tabbed_spec_validates(self):
        spec, issues = validate_spec(_make_tabbed_spec())
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None, f"Expected valid tabbed spec; issues: {issues}"
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"
        assert len(spec.tabs) == 2
        assert spec.tabs[0].id == "t1"
        assert spec.tabs[1].style == {"accent": "#0af"}

    def test_widget_with_valid_tab_id_passes(self):
        spec, issues = validate_spec(_make_tabbed_spec())
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"
        w1 = next(w for w in spec.widgets if w.id == "w1")
        assert w1.tab_id == "t1"

    def test_duplicate_tab_id_hard_error(self):
        data = _make_tabbed_spec()
        data["tabs"][1]["id"] = "t1"  # duplicate
        spec, issues = validate_spec(data)
        assert spec is not None, "Duplicate tab id is a soft-parse-OK hard issue, not a parse failure"
        assert any("duplicate tab id" in i.lower() or "t1" in i for i in issues), (
            f"Expected duplicate tab id issue, got: {issues}"
        )

    def test_undeclared_tab_id_hard_error(self):
        data = _make_tabbed_spec()
        data["widgets"][0]["tab_id"] = "t_missing"
        spec, issues = validate_spec(data)
        assert spec is not None
        assert any(
            "t_missing" in i and "not declared" in i for i in issues
        ), f"Expected undeclared tab_id hard error, got: {issues}"

    def test_tabless_spec_still_validates(self):
        """Backward compat: a spec without tabs validates and tabs default empty."""
        data = _good_spec_dict()
        assert "tabs" not in data
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None, f"Expected valid tab-less spec; issues: {issues}"
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"
        assert spec.tabs == []

    def test_widget_tab_id_none_with_tabs_no_error(self):
        """When tabs exist, a widget with tab_id None implicitly belongs to the first tab."""
        data = _make_tabbed_spec()
        data["widgets"][0]["tab_id"] = None
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None
        assert hard_issues == [], f"tab_id None should not error: {hard_issues}"

    def test_drawer_widget_ignores_tab_id(self):
        """Drawer widgets ignore tab_id — an undeclared tab_id on a drawer is no error."""
        data = _make_tabbed_spec()
        data["widgets"][0]["drawer"] = True
        data["widgets"][0]["drawer_group"] = "filters"
        data["widgets"][0]["tab_id"] = "t_missing"
        spec, issues = validate_spec(data)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "t_missing" not in i
        ]
        assert spec is not None
        assert not any("t_missing" in i for i in issues), (
            f"Drawer widget tab_id should be ignored, got: {issues}"
        )
        assert hard_issues == []

    def test_tab_id_undeclared_when_no_tabs_declared(self):
        """A widget.tab_id set on a spec with no tabs is a hard error."""
        data = _good_spec_dict()
        data["widgets"][0]["tab_id"] = "t1"
        spec, issues = validate_spec(data)
        assert spec is not None
        assert any("t1" in i and "not declared" in i for i in issues), (
            f"Expected undeclared tab_id error when no tabs declared: {issues}"
        )

    def test_spec_json_schema_includes_tabs(self):
        schema = spec_json_schema()
        props = schema.get("properties", {})
        assert "tabs" in props, (
            f"Schema should expose 'tabs'; props: {list(props.keys())}"
        )


# ---------------------------------------------------------------------------
# 9. Client-compute param class — Variable.mode (CLIENT_COMPUTE_PLAN.md §2.1)
# ---------------------------------------------------------------------------


class TestVariableMode:
    """Variable.mode: optional 'scan'|'slice', absent/None behaves as today."""

    def test_mode_slice_validates(self):
        data = _make_variable_spec()
        data["variables"][0]["mode"] = "slice"
        spec, issues = validate_spec(data)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None, f"Expected valid spec with mode='slice'; issues: {issues}"
        assert hard_issues == [], f"Unexpected hard issues: {hard_issues}"
        assert spec.variables[0].mode == "slice"

    def test_mode_scan_validates(self):
        data = _make_variable_spec()
        data["variables"][0]["mode"] = "scan"
        spec, issues = validate_spec(data)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == []
        assert spec.variables[0].mode == "scan"

    def test_mode_absent_still_validates(self):
        """Backward compat: variables without 'mode' validate; mode defaults to None."""
        data = _make_variable_spec()
        assert "mode" not in data["variables"][0]
        spec, issues = validate_spec(data)
        hard_issues = [
            i for i in issues
            if "not in the registered" not in i and "forward reference" not in i
        ]
        assert spec is not None
        assert hard_issues == []
        assert spec.variables[0].mode is None

    def test_invalid_mode_rejected(self):
        data = _make_variable_spec()
        data["variables"][0]["mode"] = "local"
        spec, issues = validate_spec(data)
        assert spec is None, "Expected parse failure for invalid Variable.mode"
        assert len(issues) > 0
