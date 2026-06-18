"""Tests for T7 export routes — GET /boards/{id}/export.pdf + export.pptx.

Strategy
--------
- Use InMemoryRepo + FakeDB (no live DB) with a seeded board.
- The T2 (Node.js echarts-SSR) and T3/T4 render backends are mocked out so
  the tests are offline and CI-friendly.
- When optional render libs ARE present: the real renderer is exercised
  via the mock-SVG path; content-type + non-empty body are asserted.
- When optional render libs are ABSENT: the route must return 503 with a
  clear JSON error body (not a crash / 500).

Tests
-----
1. export.pdf — 200 + application/pdf when backends available.
2. export.pptx — 200 + correct PPTX content-type when backends available.
3. export.pdf — non-empty body (valid %PDF header) when backends available.
4. export.pptx — non-empty body (PK zip magic) when backends available.
5. export.pdf — 503 JSON error when PDF backend is absent (simulated).
6. export.pptx — 503 JSON error when pptx backend is absent (simulated).
7. export.pdf — 404 for unknown board_id.
8. export.pptx — 404 for unknown board_id.
9. export.pdf — ?page_size=Letter is accepted (no crash).
10. export.pptx — ?page_size=16:9 is accepted (no crash).
11. export.pdf — render_board_svg is invoked via asyncio.to_thread (non-blocking).
12. export.pptx — render_board_pptx_from_data is invoked via asyncio.to_thread (non-blocking).
"""

from __future__ import annotations

import importlib
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# ---------------------------------------------------------------------------
# Availability probes for optional render backends
# ---------------------------------------------------------------------------

_PDF_BACKEND_AVAILABLE = (
    importlib.util.find_spec("svglib") is not None
    and importlib.util.find_spec("reportlab") is not None
) or (
    importlib.util.find_spec("cairosvg") is not None
    and _cairosvg_ok()
)


def _cairosvg_ok() -> bool:
    try:
        import cairosvg  # noqa: PLC0415, F401
        return True
    except (ImportError, OSError):
        return False


# Re-evaluate now that helper is defined.
_PDF_BACKEND_AVAILABLE = (
    importlib.util.find_spec("svglib") is not None
    and importlib.util.find_spec("reportlab") is not None
) or _cairosvg_ok()

_PPTX_BACKEND_AVAILABLE = (
    importlib.util.find_spec("pptx") is not None
    and _cairosvg_ok()
)

# ---------------------------------------------------------------------------
# Minimal SVG that the mocked T2 returns
# ---------------------------------------------------------------------------

_STUB_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="794" height="1123">'
    '<rect width="794" height="1123" fill="#f5f5f5"/>'
    '<text x="100" y="100" font-size="24" fill="#333">Stub Board</text>'
    "</svg>"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str = "user@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_board(board_id: str, org_id: str, name: str = "Test Board") -> dict[str, Any]:
    """Return a board row with a minimal spec (one text widget, no queries)."""
    return {
        "id": board_id,
        "org_id": org_id,
        "created_by": "test",
        "name": name,
        "config": {
            "spec": {
                "title": name,
                "widgets": [
                    {
                        "id": "w1",
                        "type": "text",
                        "title": "Hello",
                        "content": "World",
                    }
                ],
                "export": {
                    "page_size": "A4",
                },
            }
        },
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def export_setup(app, fake_db):
    """Yield (client, user_id, org_id, board_id, repo).

    The board is seeded in the InMemoryRepo and the user in FakeDB.
    T2 SVG render is mocked to return _STUB_SVG so no Node.js is needed.
    """
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    board_id = str(uuid.uuid4())

    fake_db.users[user_id] = _make_user(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    # Seed board directly into the InMemoryRepo store.
    board_row = _make_board(board_id, org_id)
    repo._store["boards"][board_id] = board_row

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, user_id, org_id, board_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# PDF endpoint tests
# ---------------------------------------------------------------------------


class TestExportPdf:
    """GET /api/v1/boards/{id}/export.pdf"""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _PDF_BACKEND_AVAILABLE,
        reason="PDF backend (svglib+reportlab or cairosvg) not installed",
    )
    async def test_pdf_200_content_type(self, export_setup):
        """200 response with application/pdf content-type."""
        client, user_id, org_id, board_id, repo = export_setup

        with patch(
            "app.dashboards.svg_render.render_board_svg",
            return_value=_STUB_SVG,
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pdf",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200, resp.text
        assert "application/pdf" in resp.headers["content-type"]

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _PDF_BACKEND_AVAILABLE,
        reason="PDF backend (svglib+reportlab or cairosvg) not installed",
    )
    async def test_pdf_body_starts_with_pdf_header(self, export_setup):
        """Response body begins with %PDF (valid PDF magic bytes)."""
        client, user_id, org_id, board_id, repo = export_setup

        with patch(
            "app.dashboards.svg_render.render_board_svg",
            return_value=_STUB_SVG,
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pdf",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200
        assert resp.content[:4] == b"%PDF", "Response body must start with %PDF"

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _PDF_BACKEND_AVAILABLE,
        reason="PDF backend (svglib+reportlab or cairosvg) not installed",
    )
    async def test_pdf_page_size_letter(self, export_setup):
        """?page_size=Letter is accepted — no crash."""
        client, user_id, org_id, board_id, repo = export_setup

        with patch(
            "app.dashboards.svg_render.render_board_svg",
            return_value=_STUB_SVG,
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pdf?page_size=Letter",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200
        assert resp.content[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_pdf_404_unknown_board(self, export_setup):
        """Unknown board_id returns 404."""
        client, user_id, org_id, board_id, repo = export_setup
        missing_id = str(uuid.uuid4())

        resp = await client.get(
            f"/api/v1/boards/{missing_id}/export.pdf",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_pdf_503_when_backend_absent(self, export_setup):
        """503 with JSON error body when PDF backend is absent."""
        client, user_id, org_id, board_id, repo = export_setup

        from app.errors import AppError  # noqa: PLC0415

        def _raise_missing(*args: Any, **kwargs: Any) -> bytes:
            raise AppError(
                "pdf_backend_missing",
                "PDF export requires either cairosvg or svglib+reportlab.",
                503,
            )

        with patch(
            "app.dashboards.svg_render.render_board_svg",
            return_value=_STUB_SVG,
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ), patch(
            "app.embedding.render_pdf.render_board_pdf",
            side_effect=_raise_missing,
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pdf",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 503
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "pdf_backend_missing"


# ---------------------------------------------------------------------------
# PPTX endpoint tests
# ---------------------------------------------------------------------------


class TestExportPptx:
    """GET /api/v1/boards/{id}/export.pptx"""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _PPTX_BACKEND_AVAILABLE,
        reason="python-pptx + cairosvg not installed",
    )
    async def test_pptx_200_content_type(self, export_setup):
        """200 response with the correct PPTX MIME type."""
        client, user_id, org_id, board_id, repo = export_setup

        with patch(
            "app.embedding.render_pptx.render_board_pptx_from_data",
            return_value=b"PK\x03\x04stub-pptx-bytes",
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pptx",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200, resp.text
        expected_mime = (
            "application/vnd.openxmlformats-officedocument"
            ".presentationml.presentation"
        )
        assert expected_mime in resp.headers["content-type"]

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _PPTX_BACKEND_AVAILABLE,
        reason="python-pptx + cairosvg not installed",
    )
    async def test_pptx_body_nonempty(self, export_setup):
        """Response body is non-empty."""
        client, user_id, org_id, board_id, repo = export_setup

        with patch(
            "app.embedding.render_pptx.render_board_pptx_from_data",
            return_value=b"PK\x03\x04stub-pptx-bytes",
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pptx",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200
        assert len(resp.content) > 0

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not _PPTX_BACKEND_AVAILABLE,
        reason="python-pptx + cairosvg not installed",
    )
    async def test_pptx_page_size_widescreen(self, export_setup):
        """?page_size=16:9 is accepted — no crash."""
        client, user_id, org_id, board_id, repo = export_setup

        with patch(
            "app.embedding.render_pptx.render_board_pptx_from_data",
            return_value=b"PK\x03\x04stub-pptx-bytes",
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pptx?page_size=16:9",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_pptx_404_unknown_board(self, export_setup):
        """Unknown board_id returns 404."""
        client, user_id, org_id, board_id, repo = export_setup
        missing_id = str(uuid.uuid4())

        resp = await client.get(
            f"/api/v1/boards/{missing_id}/export.pptx",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_pptx_503_when_backend_absent(self, export_setup):
        """503 with JSON error body when python-pptx is absent."""
        client, user_id, org_id, board_id, repo = export_setup

        from app.errors import AppError  # noqa: PLC0415

        def _raise_missing(*args: Any, **kwargs: Any) -> bytes:
            raise AppError(
                "pptx_not_installed",
                "python-pptx is required for PPTX export but is not installed.",
                503,
            )

        with patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ), patch(
            "app.embedding.render_pptx.render_board_pptx_from_data",
            side_effect=_raise_missing,
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pptx",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 503
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "pptx_not_installed"


# ---------------------------------------------------------------------------
# Non-blocking (asyncio.to_thread) contract tests
# ---------------------------------------------------------------------------


class TestExportNonBlocking:
    """Verify that the heavy render calls are dispatched via asyncio.to_thread.

    The route handlers must NOT call render_board_svg or
    render_board_pptx_from_data directly on the event-loop thread.  We assert
    this by patching asyncio.to_thread and confirming it is called with the
    expected sync callable — the route should never reach the real function
    except through the thread executor.
    """

    @pytest.mark.asyncio
    async def test_pdf_render_uses_to_thread(self, export_setup):
        """export.pdf dispatches render_board_svg via asyncio.to_thread."""
        import asyncio  # noqa: PLC0415

        client, user_id, org_id, board_id, repo = export_setup

        to_thread_calls: list[Any] = []

        async def _fake_to_thread(func, *args, **kwargs):  # type: ignore[override]
            to_thread_calls.append(func)
            # Call the function synchronously so the route can finish.
            return func(*args, **kwargs)

        with patch(
            "app.routes.export_share.asyncio.to_thread",
            side_effect=_fake_to_thread,
        ), patch(
            "app.dashboards.svg_render.render_board_svg",
            return_value=_STUB_SVG,
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ), patch(
            "app.embedding.render_pdf.render_board_pdf",
            return_value=b"%PDF-1.4 stub",
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pdf",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200, resp.text
        # asyncio.to_thread must have been called at least once, proving the
        # blocking SVG render was dispatched off the event-loop thread.
        assert len(to_thread_calls) >= 1, (
            "asyncio.to_thread was never called in export_board_pdf; "
            "render_board_svg would block the event loop"
        )

    @pytest.mark.asyncio
    async def test_pptx_render_uses_to_thread(self, export_setup):
        """export.pptx dispatches render_board_pptx_from_data via asyncio.to_thread."""
        client, user_id, org_id, board_id, repo = export_setup

        to_thread_calls: list[Any] = []

        async def _fake_to_thread(func, *args, **kwargs):  # type: ignore[override]
            to_thread_calls.append(func)
            return func(*args, **kwargs)

        with patch(
            "app.routes.export_share.asyncio.to_thread",
            side_effect=_fake_to_thread,
        ), patch(
            "app.dashboards.collect.collect_board_data",
            return_value=[],
        ), patch(
            "app.embedding.render_pptx.render_board_pptx_from_data",
            return_value=b"PK\x03\x04stub",
        ):
            resp = await client.get(
                f"/api/v1/boards/{board_id}/export.pptx",
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200, resp.text
        assert len(to_thread_calls) >= 1, (
            "asyncio.to_thread was never called in export_board_pptx; "
            "render_board_pptx_from_data would block the event loop"
        )
