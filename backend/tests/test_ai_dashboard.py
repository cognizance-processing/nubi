"""Tests for the AI dashboard generation layer (M8-C).

Coverage
--------
1. generate_dashboard_html (NullProvider)
   a. Returns a string.
   b. Contains <nubi-chart and <nubi-table widgets.
   c. References a query_id that is actually registered in get_query_registry().
   d. Contains NO <script> tags.
   e. Contains NO on*= inline event handlers.

2. validate_dashboard_html
   a. Returns (True, []) for a clean NullProvider-generated dashboard.
   b. Returns (False, issues) for HTML containing <script>.
   c. Returns (False, issues) for HTML with on*= handlers.
   d. Returns (False, issues) for HTML with javascript: URIs.
   e. Returns (False, issues) for HTML with unknown custom elements.

3. POST /ai/dashboard endpoint
   a. 200 with valid auth; response has {html, grounding, provider, valid, issues}.
   b. 401 without auth.
   c. html in response contains <nubi-table or <nubi-chart.
   d. valid == True for NullProvider-generated HTML.
   e. provider == "null" when no API keys configured.
   f. 422 when question field is missing.

Network safety
--------------
NullProvider makes zero network calls; all tests use it (no API keys set).
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.dashboard import generate_dashboard_html, validate_dashboard_html
from app.ai.grounding import build_catalog
from app.ai.provider import NullProvider
from app.auth.jwt import mint_access_token
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
        "email": "dashboard-tester@example.com",
        "name": "Dashboard Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def dashboard_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for dashboard endpoint tests."""
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
# 1. generate_dashboard_html — NullProvider
# ---------------------------------------------------------------------------


class TestGenerateDashboardHtmlNullProvider:
    """generate_dashboard_html with NullProvider returns correct structure."""

    def _gen(self, question: str = "show me demo data") -> str:
        catalog = build_catalog()
        return generate_dashboard_html(question, catalog, NullProvider())

    def test_returns_string(self):
        result = self._gen()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_nubi_chart(self):
        result = self._gen()
        assert "<nubi-chart" in result, (
            f"Expected <nubi-chart in output. Got:\n{result[:500]}"
        )

    def test_contains_nubi_table(self):
        result = self._gen()
        assert "<nubi-table" in result, (
            f"Expected <nubi-table in output. Got:\n{result[:500]}"
        )

    def test_contains_nubi_kpi(self):
        result = self._gen()
        assert "<nubi-kpi" in result, (
            f"Expected <nubi-kpi in output. Got:\n{result[:500]}"
        )

    def test_references_registered_query_id(self):
        """query-id attribute must reference an actually-registered query."""
        result = self._gen()
        registry = get_query_registry()
        known_ids = {rq.id for rq in registry.all()}

        matches = re.findall(r'query-id=["\']([^"\']+)["\']', result)
        assert len(matches) > 0, "No query-id attributes found in dashboard HTML."

        for qid in matches:
            assert qid in known_ids, (
                f"query-id {qid!r} is not in the registered query registry. "
                f"Known ids: {sorted(known_ids)}"
            )

    def test_no_script_tags(self):
        result = self._gen()
        assert "<script" not in result.lower(), (
            "Dashboard HTML must not contain <script> tags."
        )

    def test_no_inline_event_handlers(self):
        """No on*= attribute handlers should appear in the output."""
        result = self._gen()
        on_handler = re.search(r"\bon\w+=", result, re.IGNORECASE)
        assert on_handler is None, (
            f"Dashboard HTML contains inline event handler: {on_handler.group()!r}"
        )

    def test_no_javascript_uri(self):
        result = self._gen()
        assert "javascript:" not in result.lower(), (
            "Dashboard HTML must not contain javascript: URIs."
        )

    def test_columns_from_catalog_appear_in_html_when_known(self):
        """When the catalog has matching columns, those should appear in widget attrs.

        We use a catalog that has known columns and check the value-col attribute
        on nubi-kpi appears in the known column set.  We don't assert x= / y= here
        because the dashboard may fall back to 'x'/'y' when only a few columns exist.
        """
        catalog = build_catalog()
        # Get all known columns from catalog.
        all_known_cols: set[str] = set()
        for cols in catalog["tables"].values():
            all_known_cols.update(cols)
        # Only run assertion if there are any known columns to check.
        if not all_known_cols:
            pytest.skip("No columns in catalog — grounding fall-back path; skip.")
        result = generate_dashboard_html("show me demo data", catalog, NullProvider())
        # Extract value-col attribute values (from nubi-kpi).
        value_cols = re.findall(r'value-col=["\']([^"\']+)["\']', result)
        for val in value_cols:
            assert val in all_known_cols, (
                f"value-col {val!r} referenced in nubi-kpi is not in the catalog. "
                f"Known columns: {sorted(all_known_cols)}"
            )

    def test_deterministic_output(self):
        """Calling twice with the same question returns identical HTML."""
        catalog = build_catalog()
        r1 = generate_dashboard_html("list demo rows", catalog, NullProvider())
        r2 = generate_dashboard_html("list demo rows", catalog, NullProvider())
        assert r1 == r2

    def test_question_sanitised_in_output(self):
        """Angle brackets in question should be escaped (not raw HTML injection)."""
        catalog = build_catalog()
        result = generate_dashboard_html("<script>bad</script> question", catalog, NullProvider())
        # The raw <script> tag from the question should NOT appear unescaped.
        assert "<script>bad</script>" not in result


# ---------------------------------------------------------------------------
# 2. validate_dashboard_html
# ---------------------------------------------------------------------------


class TestValidateDashboardHtml:
    """validate_dashboard_html catches security issues."""

    def _valid_html(self) -> str:
        """Return a valid dashboard HTML for baseline tests."""
        return (
            '<div class="nubi-dashboard" style="display:grid;">'
            '  <nubi-kpi query-id="demo_all" value-col="id" label="Count"></nubi-kpi>'
            '  <nubi-table query-id="demo_all" limit="50"></nubi-table>'
            '  <nubi-chart query-id="demo_points_10k" type="scatter" x="x" y="y"></nubi-chart>'
            "</div>"
        )

    def test_clean_null_provider_html_is_valid(self):
        """NullProvider-generated HTML should pass validation."""
        catalog = build_catalog()
        html = generate_dashboard_html("show me demo data", catalog, NullProvider())
        ok, issues = validate_dashboard_html(html)
        assert ok is True, f"Expected valid HTML, got issues: {issues}"
        assert issues == []

    def test_valid_html_returns_true(self):
        ok, issues = validate_dashboard_html(self._valid_html())
        assert ok is True
        assert issues == []

    def test_script_tag_detected(self):
        html = '<div><nubi-table query-id="demo_all"></nubi-table><script>alert(1)</script></div>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False
        assert any("script" in issue.lower() for issue in issues), issues

    def test_script_tag_case_insensitive(self):
        html = '<SCRIPT>alert(1)</SCRIPT><nubi-table query-id="demo_all"></nubi-table>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False

    def test_inline_handler_detected(self):
        html = '<div onclick="bad()"><nubi-table query-id="demo_all"></nubi-table></div>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False
        assert any("on*=" in issue or "handler" in issue.lower() for issue in issues), issues

    def test_javascript_uri_detected(self):
        html = '<a href="javascript:void(0)"><nubi-table query-id="demo_all"></nubi-table></a>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False
        assert any("javascript" in issue.lower() for issue in issues), issues

    def test_unknown_custom_element_detected(self):
        html = (
            '<div>'
            '<nubi-table query-id="demo_all"></nubi-table>'
            '<evil-element src="bad"></evil-element>'
            "</div>"
        )
        ok, issues = validate_dashboard_html(html)
        assert ok is False
        assert any("evil-element" in issue for issue in issues), issues

    def test_unknown_query_id_adds_issue(self):
        html = '<nubi-table query-id="does_not_exist_ever_xyz"></nubi-table>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False
        assert any("does_not_exist_ever_xyz" in issue for issue in issues), issues

    def test_empty_html_is_valid(self):
        ok, issues = validate_dashboard_html("")
        assert ok is True
        assert issues == []

    def test_plain_div_with_no_widgets_is_valid(self):
        ok, issues = validate_dashboard_html('<div class="wrapper">Hello</div>')
        assert ok is True

    def test_multiple_issues_returned(self):
        """Both a script tag and an unknown element should produce multiple issues."""
        html = (
            "<script>bad()</script>"
            '<bad-element query-id="demo_all"></bad-element>'
        )
        ok, issues = validate_dashboard_html(html)
        assert ok is False
        assert len(issues) >= 2


# ---------------------------------------------------------------------------
# 3. POST /ai/dashboard endpoint
# ---------------------------------------------------------------------------


class TestDashboardEndpoint:
    """HTTP endpoint tests for POST /ai/dashboard."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, dashboard_client):
        ac, _ = dashboard_client
        resp = await ac.post("/api/v1/ai/dashboard", json={"question": "show me data"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_with_auth(self, dashboard_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = dashboard_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_has_html_key(self, dashboard_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = dashboard_client
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
    async def test_response_has_grounding_key(self, dashboard_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = dashboard_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "grounding" in body
        assert "relevant_tables" in body["grounding"]

    @pytest.mark.asyncio
    async def test_response_has_provider_null(self, dashboard_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = dashboard_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert body["provider"] == "null"

    @pytest.mark.asyncio
    async def test_html_contains_nubi_widget(self, dashboard_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = dashboard_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo points"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        html = body["html"]
        assert "<nubi-table" in html or "<nubi-chart" in html, (
            f"Expected at least one nubi widget tag in HTML. Got:\n{html[:400]}"
        )

    @pytest.mark.asyncio
    async def test_valid_is_true_for_null_provider(self, dashboard_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = dashboard_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert body["valid"] is True, f"Expected valid=True, got issues: {body.get('issues')}"
        assert body["issues"] == []

    @pytest.mark.asyncio
    async def test_missing_question_returns_422(self, dashboard_client, monkeypatch):
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = dashboard_client
        resp = await ac.post(
            "/api/v1/ai/dashboard",
            json={},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 422
