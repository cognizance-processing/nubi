"""Tests for MCP dashboard tools (_create_dashboard, _author_dashboard).

Strategy
--------
- Use InMemoryRepo (injected via set_repo) so no live database is needed.
- _create_dashboard: validates HTML and stores a board — assert repo has the board.
- _author_dashboard: generates + stores a board — assert id is returned and the
  board references a real registered query_id.
- All tests use NullProvider (no API keys configured) for deterministic output.

Path bootstrap
--------------
The backend/ directory is added to sys.path so that ``app.*`` modules are
importable (same strategy as the main server.py and test_tools.py).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap — backend must be importable.
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent        # mcp/tests/
_MCP_DIR = _TESTS_DIR.parent                        # mcp/
_NUBI_ROOT = _MCP_DIR.parent                        # nubi/
_BACKEND_DIR = _NUBI_ROOT / "backend"               # nubi/backend/

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ---------------------------------------------------------------------------
# Environment must be set before importing any app modules.
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-google-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-google-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")
os.environ.pop("CORS_ORIGINS", None)

import pytest

# Import InMemoryRepo + set_repo BEFORE the tool functions so that the first
# call to get_repo() gets the in-memory implementation.
from app.repos import InMemoryRepo, set_repo
from app.queries.registry import get_query_registry

# Import the plain tool-logic functions.
from nubi_mcp.server import _create_dashboard, _author_dashboard


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def inject_memory_repo():
    """Inject a fresh InMemoryRepo before every test and reset after."""
    repo = InMemoryRepo()
    set_repo(repo)
    yield repo
    set_repo(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_boards(repo: InMemoryRepo, org_id: str = "mcp") -> list[dict[str, Any]]:
    """Synchronously list boards from the in-memory repo."""
    return asyncio.run(repo.list("boards", org_id))


_MINIMAL_VALID_HTML = (
    '<div class="nubi-dashboard" style="display:grid;">'
    '<nubi-table query-id="demo_all" limit="50"></nubi-table>'
    '<nubi-chart query-id="demo_points_10k" type="scatter" x="x" y="y"></nubi-chart>'
    "</div>"
)


# ---------------------------------------------------------------------------
# Tests for _create_dashboard
# ---------------------------------------------------------------------------


class TestCreateDashboard:
    """Tests for _create_dashboard()."""

    def test_stores_board_in_repo(self, inject_memory_repo):
        repo: InMemoryRepo = inject_memory_repo
        result = _create_dashboard(name="Test Dashboard", html=_MINIMAL_VALID_HTML)

        boards = _list_boards(repo)
        assert len(boards) == 1, f"Expected 1 board stored, got {len(boards)}: {boards}"

    def test_returns_id_and_name(self, inject_memory_repo):
        result = _create_dashboard(name="My Board", html=_MINIMAL_VALID_HTML)
        assert "id" in result
        assert "name" in result
        assert isinstance(result["id"], str)
        assert len(result["id"]) > 0

    def test_returned_name_matches_input(self, inject_memory_repo):
        result = _create_dashboard(name="Special Name", html=_MINIMAL_VALID_HTML)
        assert result["name"] == "Special Name"

    def test_board_config_contains_html(self, inject_memory_repo):
        repo: InMemoryRepo = inject_memory_repo
        result = _create_dashboard(name="HTML Board", html=_MINIMAL_VALID_HTML)
        boards = _list_boards(repo)
        assert len(boards) == 1
        assert boards[0]["config"]["html"] == _MINIMAL_VALID_HTML

    def test_board_scoped_to_mcp_org(self, inject_memory_repo):
        repo: InMemoryRepo = inject_memory_repo
        result = _create_dashboard(name="Org Test", html=_MINIMAL_VALID_HTML)
        boards = _list_boards(repo, org_id="mcp")
        assert any(b["id"] == result["id"] for b in boards), (
            "Board was not found under 'mcp' org."
        )

    def test_custom_org_id(self, inject_memory_repo):
        repo: InMemoryRepo = inject_memory_repo
        result = _create_dashboard(name="Custom Org Board", html=_MINIMAL_VALID_HTML, org_id="acme")
        boards_acme = _list_boards(repo, org_id="acme")
        boards_mcp = _list_boards(repo, org_id="mcp")
        assert any(b["id"] == result["id"] for b in boards_acme)
        assert not any(b["id"] == result["id"] for b in boards_mcp)

    def test_rejects_script_tag(self, inject_memory_repo):
        """HTML with <script> should raise ValueError."""
        bad_html = (
            '<div><nubi-table query-id="demo_all"></nubi-table>'
            "<script>alert(1)</script></div>"
        )
        with pytest.raises(ValueError, match="validation"):
            _create_dashboard(name="Bad Board", html=bad_html)

    def test_rejects_inline_handler(self, inject_memory_repo):
        bad_html = '<div onclick="bad()"><nubi-table query-id="demo_all"></nubi-table></div>'
        with pytest.raises(ValueError, match="validation"):
            _create_dashboard(name="Bad Handler", html=bad_html)

    def test_multiple_stores_are_distinct(self, inject_memory_repo):
        repo: InMemoryRepo = inject_memory_repo
        _create_dashboard(name="Board A", html=_MINIMAL_VALID_HTML)
        _create_dashboard(name="Board B", html=_MINIMAL_VALID_HTML)
        boards = _list_boards(repo)
        assert len(boards) == 2
        ids = {b["id"] for b in boards}
        assert len(ids) == 2, "Two boards should have distinct ids."


# ---------------------------------------------------------------------------
# Tests for _author_dashboard
# ---------------------------------------------------------------------------


class TestAuthorDashboard:
    """Tests for _author_dashboard()."""

    def test_returns_id_and_html_preview(self, inject_memory_repo):
        result = _author_dashboard("show me demo data")
        assert "id" in result
        assert "html_preview" in result
        assert isinstance(result["id"], str)
        assert isinstance(result["html_preview"], str)

    def test_id_is_non_empty(self, inject_memory_repo):
        result = _author_dashboard("list demo rows")
        assert len(result["id"]) > 0

    def test_html_preview_is_non_empty(self, inject_memory_repo):
        result = _author_dashboard("show demo points")
        assert len(result["html_preview"]) > 0

    def test_html_preview_length(self, inject_memory_repo):
        """html_preview should be at most 200 characters."""
        result = _author_dashboard("show me demo data")
        assert len(result["html_preview"]) <= 200

    def test_stores_board_in_repo(self, inject_memory_repo):
        repo: InMemoryRepo = inject_memory_repo
        result = _author_dashboard("show me demo data")
        boards = _list_boards(repo)
        assert len(boards) >= 1
        stored_ids = {b["id"] for b in boards}
        assert result["id"] in stored_ids, (
            f"Board id {result['id']!r} not found in stored boards: {stored_ids}"
        )

    def test_board_html_references_registered_query_id(self, inject_memory_repo):
        """The stored board's HTML must reference an actually-registered query_id."""
        repo: InMemoryRepo = inject_memory_repo
        registry = get_query_registry()
        known_ids = {rq.id for rq in registry.all()}

        result = _author_dashboard("show me demo data")
        boards = _list_boards(repo)
        board = next((b for b in boards if b["id"] == result["id"]), None)
        assert board is not None

        html = board["config"]["html"]
        referenced_ids = re.findall(r'query-id=["\']([^"\']+)["\']', html)
        assert len(referenced_ids) > 0, "No query-id attributes in stored HTML."
        for qid in referenced_ids:
            assert qid in known_ids, (
                f"query-id {qid!r} is not in the registered query registry. "
                f"Known: {sorted(known_ids)}"
            )

    def test_board_html_has_no_script(self, inject_memory_repo):
        """The stored board's HTML must not contain <script> tags."""
        repo: InMemoryRepo = inject_memory_repo
        _author_dashboard("show demo data")
        boards = _list_boards(repo)
        assert len(boards) >= 1
        for board in boards:
            html = board["config"]["html"]
            assert "<script" not in html.lower(), (
                "Stored board HTML contains <script> tag."
            )

    def test_name_truncated_to_40_chars(self, inject_memory_repo):
        """Board name should be at most 40 characters (first 40 chars of question)."""
        repo: InMemoryRepo = inject_memory_repo
        long_question = "A" * 100
        result = _author_dashboard(long_question)
        boards = _list_boards(repo)
        board = next((b for b in boards if b["id"] == result["id"]), None)
        assert board is not None
        assert len(board["name"]) <= 40, (
            f"Board name is longer than 40 chars: {board['name']!r}"
        )

    def test_deterministic_with_null_provider(self, inject_memory_repo):
        """Two calls with the same question produce valid (though distinct) boards."""
        repo: InMemoryRepo = inject_memory_repo
        r1 = _author_dashboard("show demo rows")
        r2 = _author_dashboard("show demo rows")
        boards = _list_boards(repo)
        # Both boards stored separately.
        assert len(boards) == 2
        # html_preview should be identical (deterministic NullProvider).
        assert r1["html_preview"] == r2["html_preview"], (
            "NullProvider should produce identical HTML for the same question."
        )
        # But ids are distinct.
        assert r1["id"] != r2["id"]
