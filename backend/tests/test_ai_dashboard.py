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

    def test_inline_handler_with_space_before_equals_detected(self):
        """on*= handler with whitespace before '=' must be caught (XSS bypass fix).

        SECURITY (LOW XSS): the old regex r'\\bon\\w+=' was bypassed by inserting
        a space (or tab) before the '=' sign, e.g. 'onclick ="bad()"'.
        The fix adds \\s* so 'onclick =' / 'onclick\\t=' are also matched.
        """
        # Space before '='
        html_space = '<div onclick ="bad()"><nubi-table query-id="demo_all"></nubi-table></div>'
        ok, issues = validate_dashboard_html(html_space)
        assert ok is False, (
            "Expected validate_dashboard_html to reject 'onclick =' (space before =)"
        )
        assert any("on*=" in issue or "handler" in issue.lower() for issue in issues), issues

        # Tab before '='
        html_tab = '<div onclick\t="bad()"><nubi-table query-id="demo_all"></nubi-table></div>'
        ok2, issues2 = validate_dashboard_html(html_tab)
        assert ok2 is False, (
            "Expected validate_dashboard_html to reject 'onclick\\t=' (tab before =)"
        )
        assert any("on*=" in issue or "handler" in issue.lower() for issue in issues2), issues2

        # Multiple spaces before '='
        html_multi = '<div onmouseover   ="bad()"><nubi-table query-id="demo_all"></nubi-table></div>'
        ok3, issues3 = validate_dashboard_html(html_multi)
        assert ok3 is False, (
            "Expected validate_dashboard_html to reject 'onmouseover   =' (multiple spaces)"
        )
        assert any("on*=" in issue or "handler" in issue.lower() for issue in issues3), issues3

        # No space — original case must still work.
        html_nospace = '<div onclick="bad()"><nubi-table query-id="demo_all"></nubi-table></div>'
        ok4, issues4 = validate_dashboard_html(html_nospace)
        assert ok4 is False, "Expected validate_dashboard_html to reject 'onclick=' (no space)"

    # ── data: URI hardening (LOW XSS) ─────────────────────────────────────────

    def test_data_image_svg_xml_is_rejected(self):
        """data:image/svg+xml URIs must be rejected — SVG can embed <script>.

        SECURITY (LOW XSS): SVG data URIs can contain inline <script> tags that
        execute in the browser when the URI is set as an src/href attribute.
        Only safe raster image types (png/jpeg/gif/webp) are permitted.
        """
        html = (
            '<img src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53'
            'My5vcmcvMjAwMC9zdmcnPjxzY3JpcHQ+YWxlcnQoMSk8L3NjcmlwdD48L3N2Zz4=">'
        )
        ok, issues = validate_dashboard_html(html)
        assert ok is False, (
            "Expected validate_dashboard_html to reject data:image/svg+xml URI"
        )
        assert any("svg" in i.lower() or "unsafe data" in i.lower() or "data:" in i.lower() for i in issues), issues

    def test_data_image_png_is_permitted(self):
        """data:image/png URIs must be allowed — safe raster images are OK."""
        # A minimal valid 1x1 transparent PNG base64-encoded.
        html = (
            '<img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ'
            'AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==">'
        )
        ok, issues = validate_dashboard_html(html)
        # data:image/png is on the safe list — must not produce a data-URI issue.
        data_uri_issues = [i for i in issues if "unsafe data" in i.lower() or "svg" in i.lower()]
        assert data_uri_issues == [], (
            f"data:image/png should be permitted but got: {data_uri_issues}"
        )

    def test_data_image_jpeg_is_permitted(self):
        """data:image/jpeg URIs must be allowed."""
        html = '<img src="data:image/jpeg;base64,/9j/4AAQSkZJRg==">'
        ok, issues = validate_dashboard_html(html)
        data_uri_issues = [i for i in issues if "unsafe data" in i.lower() or "svg" in i.lower()]
        assert data_uri_issues == [], (
            f"data:image/jpeg should be permitted but got: {data_uri_issues}"
        )

    def test_data_image_gif_is_permitted(self):
        """data:image/gif URIs must be allowed."""
        html = '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7">'
        ok, issues = validate_dashboard_html(html)
        data_uri_issues = [i for i in issues if "unsafe data" in i.lower() or "svg" in i.lower()]
        assert data_uri_issues == [], (
            f"data:image/gif should be permitted but got: {data_uri_issues}"
        )

    def test_data_image_webp_is_permitted(self):
        """data:image/webp URIs must be allowed."""
        html = '<img src="data:image/webp;base64,UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAUAmJZACdAEO/gHOAAA=">'
        ok, issues = validate_dashboard_html(html)
        data_uri_issues = [i for i in issues if "unsafe data" in i.lower() or "svg" in i.lower()]
        assert data_uri_issues == [], (
            f"data:image/webp should be permitted but got: {data_uri_issues}"
        )

    # ── Obfuscated scheme detection (LOW XSS) ─────────────────────────────────

    def test_javascript_with_embedded_newline_is_rejected(self):
        r"""'java\nscript:' must be caught — whitespace inside scheme is stripped.

        SECURITY (LOW XSS): the original regex r'javascript\s*:' only strips
        whitespace between the scheme name and the colon.  Embedding a newline
        or tab *inside* the scheme name (e.g. 'java\nscript:') bypassed it.
        The fix normalises the full value before matching.
        """
        html = '<a href="java\nscript:alert(1)">click</a>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False, (
            r"Expected validate_dashboard_html to reject 'java\nscript:' URI"
        )
        assert any("javascript" in i.lower() or "dangerous" in i.lower() for i in issues), issues

    def test_javascript_with_embedded_tab_is_rejected(self):
        r"""'javas\tcript:' must be caught."""
        html = '<a href="javas\tcript:alert(1)">click</a>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False, (
            r"Expected validate_dashboard_html to reject 'javas\tcript:' URI"
        )
        assert any("javascript" in i.lower() or "dangerous" in i.lower() for i in issues), issues

    def test_javascript_with_embedded_carriage_return_is_rejected(self):
        r"""'java\rscript:' must be caught."""
        html = '<a href="java\rscript:alert(1)">click</a>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False, (
            r"Expected validate_dashboard_html to reject 'java\rscript:' URI"
        )
        assert any("javascript" in i.lower() or "dangerous" in i.lower() for i in issues), issues

    def test_plain_javascript_uri_still_rejected(self):
        """Plain 'javascript:' (no obfuscation) must still be rejected."""
        html = '<a href="javascript:alert(1)">click</a>'
        ok, issues = validate_dashboard_html(html)
        assert ok is False
        assert any("javascript" in i.lower() for i in issues), issues

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

    def test_style_tag_rejected(self):
        """Inline <style> tags must be rejected — CSS exfiltration via url().

        SECURITY (MED): a <style> block can load remote resources via url()
        and track users or exfiltrate page content.  'style' must be in
        _FORBIDDEN_TAGS_RE so it is blocked at validate-time on both the
        generate and render paths.
        """
        html = (
            '<div class="nubi-dashboard">'
            '<style>body { background: url(https://evil.example/track) }</style>'
            '<nubi-table query-id="demo_all"></nubi-table>'
            "</div>"
        )
        ok, issues = validate_dashboard_html(html)
        assert ok is False, (
            "Expected validate_dashboard_html to reject an inline <style> tag."
        )
        assert any("style" in i.lower() for i in issues), issues


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
