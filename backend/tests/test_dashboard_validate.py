"""Tests for repair-grade dashboard spec validation (POST /dashboards/validate).

Coverage
--------
1. to_structured_issues (pure) — code/path recovery + valid_options enrichment.
2. POST /dashboards/validate — auth gate + structured response shape for:
   - a fully valid spec                       → valid=True, no errors
   - a spec missing a required field          → valid=False, missing_field
   - a chart with a bad/absent encoding bound to a query with a KNOWN
     output_schema → valid_options carries the real column names
   - a widget bound to an unknown query_id    → warning, unknown_query_id with
     known-query-id valid_options

The bad-encoding case is the headline: an agent must be able to read the single
error and fix it in ONE round-trip via path + valid_options.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.dashboards.errors import to_structured_issues
from app.dashboards.spec import validate_spec
from app.queries.registry import OutputColumn, get_query_registry


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

# Query id (with a declared output_schema) we bind chart widgets to in tests.
_SALES_QUERY_ID = "test_sales_by_region"
_SALES_COLUMNS = ["region", "revenue", "month"]


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "dash-tester@example.com",
        "name": "Dash Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _register_sales_query() -> None:
    """Register a query with a KNOWN output_schema so encoding enrichment works."""
    get_query_registry().register(
        id=_SALES_QUERY_ID,
        sql="SELECT region, revenue, month FROM sales",
        name="Sales by region",
        output_schema=[OutputColumn(name=c, type="text") for c in _SALES_COLUMNS],
    )


def _pos() -> dict[str, int]:
    return {"x": 1, "y": 1, "w": 4, "h": 3}


def _valid_spec() -> dict[str, Any]:
    """A fully valid single-chart dashboard spec bound to the sales query."""
    return {
        "version": 1,
        "title": "Sales overview",
        "widgets": [
            {
                "id": "w1",
                "type": "chart",
                "query_id": _SALES_QUERY_ID,
                "chart_type": "bar",
                "encoding": {"x": "region", "y": "revenue"},
                "pos": _pos(),
            }
        ],
    }


def _missing_field_spec() -> dict[str, Any]:
    """Spec missing the required top-level 'title' field."""
    return {
        "version": 1,
        "widgets": [],
    }


def _bad_encoding_spec() -> dict[str, Any]:
    """Chart bound to a query with a known output_schema but missing encoding.x."""
    return {
        "version": 1,
        "title": "Broken chart",
        "widgets": [
            {
                "id": "w3",
                "type": "chart",
                "query_id": _SALES_QUERY_ID,
                "chart_type": "bar",
                # encoding.x is missing → missing_encoding_x with valid_options.
                "encoding": {"y": "revenue"},
                "pos": _pos(),
            }
        ],
    }


def _unknown_query_spec() -> dict[str, Any]:
    """Chart bound to a query_id that is not registered."""
    return {
        "version": 1,
        "title": "Unknown query",
        "widgets": [
            {
                "id": "w4",
                "type": "chart",
                "query_id": "does_not_exist",
                "chart_type": "bar",
                "encoding": {"x": "a", "y": "b"},
                "pos": _pos(),
            }
        ],
    }


@pytest_asyncio.fixture
async def dash_client(app, fake_db):
    """HTTPX async client with a pre-seeded user + the sales test query registered."""
    _register_sales_query()
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
# 1. Pure: to_structured_issues
# ---------------------------------------------------------------------------


class TestToStructuredIssues:
    """The pure converter recovers code/path and enriches valid_options."""

    def test_valid_spec_has_no_issues(self):
        _register_sales_query()
        _spec, raw = validate_spec(_valid_spec())
        structured = to_structured_issues(_valid_spec(), raw)
        assert [i for i in structured if i.severity == "error"] == []

    def test_missing_field_is_missing_field_code(self):
        spec = _missing_field_spec()
        _s, raw = validate_spec(spec)
        structured = to_structured_issues(spec, raw)
        codes = {i.code for i in structured}
        assert "missing_field" in codes
        title_issue = next(i for i in structured if i.path == "title")
        assert title_issue.severity == "error"

    def test_bad_encoding_carries_real_columns(self):
        _register_sales_query()
        spec = _bad_encoding_spec()
        _s, raw = validate_spec(spec)
        structured = to_structured_issues(spec, raw)
        enc = next(i for i in structured if i.code == "missing_encoding_x")
        assert enc.path == "widgets[0].encoding.x"
        assert enc.severity == "error"
        # The headline guarantee: real column names for one-round-trip repair.
        assert enc.valid_options is not None
        for col in _SALES_COLUMNS:
            assert col in enc.valid_options

    def test_unknown_query_id_is_warning_with_known_ids(self):
        _register_sales_query()
        spec = _unknown_query_spec()
        _s, raw = validate_spec(spec)
        structured = to_structured_issues(spec, raw)
        uq = next(i for i in structured if i.code == "unknown_query_id")
        assert uq.severity == "warning"
        assert uq.path == "widgets[0].query_id"
        assert uq.valid_options is not None
        # The registered sales query should be offered as an option.
        assert _SALES_QUERY_ID in uq.valid_options

    def test_warn_prefix_forces_warning_severity(self):
        # A "[warn]"-prefixed raw issue is mapped to severity="warning".
        structured = to_structured_issues({}, ["[warn] something soft happened"])
        assert structured[0].severity == "warning"

    def test_unexpected_format_falls_back_to_generic(self):
        structured = to_structured_issues({}, ["totally unrecognised gibberish"])
        assert structured[0].code == "generic"
        assert structured[0].message == "totally unrecognised gibberish"


# ---------------------------------------------------------------------------
# 2. HTTP: POST /dashboards/validate
# ---------------------------------------------------------------------------


class TestValidateEndpoint:
    """HTTP endpoint tests for POST /dashboards/validate."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, dash_client):
        ac, _ = dash_client
        resp = await ac.post(
            "/api/v1/dashboards/validate", json={"spec": _valid_spec()}
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_spec_returns_valid_true(self, dash_client):
        ac, user_id = dash_client
        resp = await ac.post(
            "/api/v1/dashboards/validate",
            json={"spec": _valid_spec()},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["errors"] == []

    @pytest.mark.asyncio
    async def test_missing_field_returns_valid_false(self, dash_client):
        ac, user_id = dash_client
        resp = await ac.post(
            "/api/v1/dashboards/validate",
            json={"spec": _missing_field_spec()},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        codes = {e["code"] for e in body["errors"]}
        assert "missing_field" in codes
        # Every error must carry a path + code (the repair contract).
        for e in body["errors"]:
            assert "path" in e and "code" in e

    @pytest.mark.asyncio
    async def test_bad_encoding_returns_valid_options(self, dash_client):
        ac, user_id = dash_client
        resp = await ac.post(
            "/api/v1/dashboards/validate",
            json={"spec": _bad_encoding_spec()},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        enc = next(e for e in body["errors"] if e["code"] == "missing_encoding_x")
        assert enc["path"] == "widgets[0].encoding.x"
        assert enc["valid_options"] is not None
        for col in _SALES_COLUMNS:
            assert col in enc["valid_options"]

    @pytest.mark.asyncio
    async def test_unknown_query_id_is_warning(self, dash_client):
        ac, user_id = dash_client
        resp = await ac.post(
            "/api/v1/dashboards/validate",
            json={"spec": _unknown_query_spec()},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Unknown query_id is a soft warning → spec stays valid (no errors).
        assert body["valid"] is True
        warn = next(
            w for w in body["warnings"] if w["code"] == "unknown_query_id"
        )
        assert warn["path"] == "widgets[0].query_id"
        assert _SALES_QUERY_ID in warn["valid_options"]
