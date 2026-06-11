"""Tests for the ask→PIN endpoint (POST /ai/pin).

Coverage
--------
1. Pin a query-backed answer → creates a NEW board with one valid widget.
   - board is persisted in the repo
   - the widget is bound to the source query_id
   - response carries {board_id, widget_id, spec, valid: true}
2. Pin appending to an existing board_id → that board now has +1 widget,
   and the new widget is the pinned one.
3. An invalid viz (chart missing required encoding) → structured 400,
   NOTHING is persisted (no new board, existing board untouched).
4. Source binding errors → 400:
   - neither query_id nor metric_id  → invalid_pin_source
   - both query_id and metric_id      → invalid_pin_source (mutual exclusivity)
   - metric_id (registered)           → metric-bound widget (widget→metric binding)
   - metric_id (unregistered)         → still pins, with a soft warning
5. Auth gate: no token → 401.

Strategy mirrors test_resources.py (InMemoryRepo via set_repo + seeded org) and
test_ai.py / test_dashboard_validate.py (JWT auth + a query registered with a
KNOWN output_schema so spec validation/enrichment behaves like production).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.queries.registry import OutputColumn, get_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_SALES_QUERY_ID = "test_sales_by_region"
_SALES_COLUMNS = ["region", "revenue", "month"]


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "pin-tester@example.com",
        "name": "Pin Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _register_sales_query() -> None:
    """Register a query with a KNOWN output_schema (bound by pinned widgets)."""
    get_query_registry().register(
        id=_SALES_QUERY_ID,
        sql="SELECT region, revenue, month FROM sales",
        name="Sales by region",
        output_schema=[OutputColumn(name=c, type="text") for c in _SALES_COLUMNS],
    )


@pytest_asyncio.fixture
async def pin_client(app, fake_db):
    """HTTPX client with InMemoryRepo + seeded user/org + the sales query."""
    _register_sales_query()

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


def _chart_pin(board_id: str | None = None) -> dict[str, Any]:
    """A valid chart pin bound to the sales query."""
    body: dict[str, Any] = {
        "title": "Revenue by region",
        "source": {"query_id": _SALES_QUERY_ID},
        "viz": {
            "type": "chart",
            "chart_type": "bar",
            "encoding": {"x": "region", "y": "revenue"},
        },
    }
    if board_id is not None:
        body["board_id"] = board_id
    return body


def _kpi_pin() -> dict[str, Any]:
    """A valid KPI pin (no encoding requirements)."""
    return {
        "title": "Total revenue",
        "source": {"query_id": _SALES_QUERY_ID},
        "viz": {"type": "kpi"},
    }


# ---------------------------------------------------------------------------
# 1. Pin a query-backed answer → new board
# ---------------------------------------------------------------------------


class TestPinCreatesBoard:
    @pytest.mark.asyncio
    async def test_requires_auth(self, pin_client):
        ac, _user, _org, _repo = pin_client
        resp = await ac.post("/api/v1/ai/pin", json=_chart_pin())
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_pin_creates_new_board(self, pin_client):
        ac, user_id, org_id, repo = pin_client
        resp = await ac.post(
            "/api/v1/ai/pin", json=_chart_pin(), headers=_auth_headers(user_id)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["board_id"]
        assert body["widget_id"]

        # The board is persisted in the repo.
        row = await repo.get("boards", org_id, body["board_id"])
        assert row is not None

        # The spec lives under config['spec'] with exactly one widget.
        spec = row["config"]["spec"]
        assert spec["title"] == "Revenue by region"
        assert len(spec["widgets"]) == 1
        widget = spec["widgets"][0]
        # The widget is bound to the source query_id.
        assert widget["query_id"] == _SALES_QUERY_ID
        assert widget["id"] == body["widget_id"]
        assert widget["type"] == "chart"

    @pytest.mark.asyncio
    async def test_response_spec_matches_persisted(self, pin_client):
        ac, user_id, org_id, repo = pin_client
        resp = await ac.post(
            "/api/v1/ai/pin", json=_kpi_pin(), headers=_auth_headers(user_id)
        )
        assert resp.status_code == 200
        body = resp.json()
        row = await repo.get("boards", org_id, body["board_id"])
        assert row["config"]["spec"] == body["spec"]


# ---------------------------------------------------------------------------
# 2. Append to an existing board
# ---------------------------------------------------------------------------


class TestPinAppendsToBoard:
    @pytest.mark.asyncio
    async def test_append_adds_one_widget(self, pin_client):
        ac, user_id, org_id, repo = pin_client
        headers = _auth_headers(user_id)

        # First pin → creates the board (1 widget).
        first = await ac.post("/api/v1/ai/pin", json=_chart_pin(), headers=headers)
        assert first.status_code == 200
        board_id = first.json()["board_id"]

        # Second pin → append to that board.
        second = await ac.post(
            "/api/v1/ai/pin", json=_kpi_pin() | {"board_id": board_id}, headers=headers
        )
        assert second.status_code == 200
        body = second.json()
        assert body["board_id"] == board_id

        row = await repo.get("boards", org_id, board_id)
        widgets = row["config"]["spec"]["widgets"]
        assert len(widgets) == 2
        # The appended widget is the new pinned one and binds to the query.
        new_widget = next(w for w in widgets if w["id"] == body["widget_id"])
        assert new_widget["query_id"] == _SALES_QUERY_ID
        assert new_widget["type"] == "kpi"

    @pytest.mark.asyncio
    async def test_append_to_unknown_board_404(self, pin_client):
        ac, user_id, _org, _repo = pin_client
        resp = await ac.post(
            "/api/v1/ai/pin",
            json=_chart_pin(board_id=str(uuid.uuid4())),
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Invalid viz → structured 400, nothing persisted
# ---------------------------------------------------------------------------


class TestPinInvalidViz:
    @pytest.mark.asyncio
    async def test_bad_chart_encoding_returns_structured_400(self, pin_client):
        ac, user_id, org_id, repo = pin_client
        bad = {
            "title": "Broken chart",
            "source": {"query_id": _SALES_QUERY_ID},
            "viz": {
                "type": "chart",
                "chart_type": "bar",
                # encoding.x is missing → missing_encoding_x (hard error).
                "encoding": {"y": "revenue"},
            },
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=bad, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == "invalid_pin_spec"
        assert err["valid"] is False
        # Structured, repair-grade error (same shape as /dashboards/validate).
        enc = next(e for e in err["errors"] if e["code"] == "missing_encoding_x")
        assert enc["path"] == "widgets[0].encoding.x"
        assert enc["valid_options"] is not None
        for col in _SALES_COLUMNS:
            assert col in enc["valid_options"]

        # Nothing was persisted — no board for this org.
        rows = await repo.list("boards", org_id)
        assert rows == []

    @pytest.mark.asyncio
    async def test_invalid_append_leaves_existing_board_untouched(self, pin_client):
        ac, user_id, org_id, repo = pin_client
        headers = _auth_headers(user_id)

        # Create a board with one valid widget.
        first = await ac.post("/api/v1/ai/pin", json=_chart_pin(), headers=headers)
        board_id = first.json()["board_id"]

        # Try to append an invalid chart → 400, board unchanged.
        bad = {
            "title": "ignored",
            "source": {"query_id": _SALES_QUERY_ID},
            "viz": {"type": "chart", "chart_type": "bar", "encoding": {}},
            "board_id": board_id,
        }
        resp = await ac.post("/api/v1/ai/pin", json=bad, headers=headers)
        assert resp.status_code == 400

        row = await repo.get("boards", org_id, board_id)
        assert len(row["config"]["spec"]["widgets"]) == 1


# ---------------------------------------------------------------------------
# 4. Source binding errors
# ---------------------------------------------------------------------------


class TestPinSourceBinding:
    @pytest.mark.asyncio
    async def test_no_source_id_400(self, pin_client):
        ac, user_id, _org, _repo = pin_client
        body = {
            "title": "No source",
            "source": {},
            "viz": {"type": "kpi"},
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_pin_source"

    @pytest.mark.asyncio
    async def test_both_source_ids_400(self, pin_client):
        ac, user_id, _org, _repo = pin_client
        body = {
            "title": "Both",
            "source": {"query_id": _SALES_QUERY_ID, "metric_id": "demo_revenue"},
            "viz": {"type": "kpi"},
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_pin_source"

    @pytest.mark.asyncio
    async def test_metric_pin_creates_metric_widget(self, pin_client):
        """A registered metric pins to a metric-bound widget (widget→metric binding)."""
        ac, user_id, _org, _repo = pin_client
        body = {
            "title": "Metric only",
            "source": {"metric_id": "demo_revenue"},
            "viz": {"type": "kpi"},
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 200, resp.text
        widget = resp.json()["spec"]["widgets"][0]
        assert widget["metric"]["metric_id"] == "demo_revenue"
        assert not widget.get("query_id")  # metric-bound, no backing query

    @pytest.mark.asyncio
    async def test_unknown_metric_pins_with_warning(self, pin_client):
        """An unregistered metric_id is a soft warning (not a hard error), so the
        pin still succeeds with the binding — mirrors the query_id soft rule."""
        ac, user_id, _org, _repo = pin_client
        body = {
            "title": "Phantom metric",
            "source": {"metric_id": "no_such_metric"},
            "viz": {"type": "kpi"},
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["spec"]["widgets"][0]["metric"]["metric_id"] == "no_such_metric"
