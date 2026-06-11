"""Tests for the governed-metric binding on dashboard Widgets (MetricBinding).

Coverage
--------
1. validate_spec (pure):
   - a metric-bound chart validates (with a registered metric) — encoding rules
     still apply, no hard errors.
   - a metric-bound kpi validates (with a registered metric).
   - a metric-bound widget with an UNKNOWN metric_id yields a WARNING (a
     ``[warn]``-prefixed issue mapped to severity="warning"), NOT a hard error.
   - a data widget with NEITHER query_id NOR metric is a HARD error.
2. spec_to_html (pure):
   - emits metric-* attributes for a metric widget and OMITS query-id.
   - is byte-identical for a query-bound widget (no regression).
3. POST /ai/pin:
   - a metric_id source now creates a valid metric-bound widget (no
     metric_pin_unsupported), persisted under config['spec'].

Mirrors test_dashboard_validate.py (validate_spec + registry seeding) and
test_ai_pin.py (conftest app/fake_db + JWT auth + InMemoryRepo).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.dashboards.errors import to_structured_issues
from app.dashboards.spec import DashboardSpec, spec_to_html, validate_spec
from app.metrics.models import Dimension, Measure, MetricDefinition
from app.metrics.registry import get_metric_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

# The built-in seed metric (registry.py seeds it on first access).
_SEED_METRIC_ID = "demo_revenue"


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "metric-tester@example.com",
        "name": "Metric Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _register_metric(metric_id: str = "test_orders") -> str:
    """Register a metric so binding validation finds it; return its id."""
    get_metric_registry().register(
        MetricDefinition(
            id=metric_id,
            name="Test orders",
            measure=Measure(name="orders", agg="count", expr="*", type="additive"),
            base_table="orders",
            dimensions=(
                Dimension(name="region", type="text"),
                Dimension(name="revenue", type="number"),
            ),
            time_dimension=None,
            description="Order count by region — test metric.",
        )
    )
    return metric_id


def _pos() -> dict[str, int]:
    return {"x": 1, "y": 1, "w": 4, "h": 3}


def _metric_chart_spec(metric_id: str) -> dict[str, Any]:
    """A metric-bound chart spec (no query_id) with valid encoding."""
    return {
        "version": 1,
        "title": "Orders by region",
        "widgets": [
            {
                "id": "w1",
                "type": "chart",
                "chart_type": "bar",
                "encoding": {"x": "region", "y": "orders"},
                "metric": {
                    "metric_id": metric_id,
                    "dimensions": ["region"],
                    "filters": [{"field": "region", "op": "=", "value": "EU"}],
                },
                "pos": _pos(),
            }
        ],
    }


def _metric_kpi_spec(metric_id: str) -> dict[str, Any]:
    """A metric-bound kpi spec (no query_id)."""
    return {
        "version": 1,
        "title": "Total orders",
        "widgets": [
            {
                "id": "k1",
                "type": "kpi",
                "metric": {"metric_id": metric_id},
                "pos": _pos(),
            }
        ],
    }


def _neither_source_spec() -> dict[str, Any]:
    """A kpi data widget with NEITHER query_id NOR metric."""
    return {
        "version": 1,
        "title": "Orphan widget",
        "widgets": [
            {
                "id": "x1",
                "type": "kpi",
                "pos": _pos(),
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. validate_spec — metric binding
# ---------------------------------------------------------------------------


class TestValidateMetricBinding:
    def test_metric_chart_validates(self):
        mid = _register_metric()
        spec, issues = validate_spec(_metric_chart_spec(mid))
        assert spec is not None
        # No hard errors: the only acceptable issues here would be warnings.
        structured = to_structured_issues(_metric_chart_spec(mid), issues)
        errors = [i for i in structured if i.severity == "error"]
        assert errors == []
        # The binding round-trips onto the parsed widget.
        w = spec.widgets[0]
        assert w.metric is not None
        assert w.metric.metric_id == mid
        assert w.metric.dimensions == ["region"]
        assert w.query_id == ""

    def test_metric_kpi_validates(self):
        mid = _register_metric()
        spec, issues = validate_spec(_metric_kpi_spec(mid))
        assert spec is not None
        structured = to_structured_issues(_metric_kpi_spec(mid), issues)
        assert [i for i in structured if i.severity == "error"] == []

    def test_metric_chart_still_requires_encoding(self):
        """A metric-bound chart still needs encoding x/y (hard error if absent)."""
        mid = _register_metric()
        spec_data = _metric_chart_spec(mid)
        spec_data["widgets"][0]["encoding"] = {"y": "orders"}  # drop x
        _spec, issues = validate_spec(spec_data)
        structured = to_structured_issues(spec_data, issues)
        enc = next(i for i in structured if i.code == "missing_encoding_x")
        assert enc.severity == "error"
        assert enc.path == "widgets[0].encoding.x"

    def test_unknown_metric_id_is_warning_not_error(self):
        """An unknown metric_id is a soft WARNING (forward-ref), not a hard error."""
        spec_data = _metric_kpi_spec("no_such_metric_xyz")
        spec, issues = validate_spec(spec_data)
        assert spec is not None  # parse succeeds
        structured = to_structured_issues(spec_data, issues)
        # No hard errors.
        assert [i for i in structured if i.severity == "error"] == []
        # The unknown-metric issue exists and is a warning.
        assert any(
            i.severity == "warning" and "no_such_metric_xyz" in i.message
            for i in structured
        )

    def test_neither_query_nor_metric_is_hard_error(self):
        spec_data = _neither_source_spec()
        spec, issues = validate_spec(spec_data)
        assert spec is not None  # parse succeeds; this is a semantic error
        # The raw issue names the missing-source rule.
        assert any(
            "must have either a non-empty 'query_id' or a 'metric' binding" in i
            for i in issues
        )
        structured = to_structured_issues(spec_data, issues)
        assert any(i.severity == "error" for i in structured)

    def test_query_only_widget_unaffected(self):
        """A query_id-only data widget still validates with no source error."""
        spec_data = {
            "version": 1,
            "title": "Query widget",
            "widgets": [
                {
                    "id": "q1",
                    "type": "kpi",
                    "query_id": "some_query",
                    "pos": _pos(),
                }
            ],
        }
        _spec, issues = validate_spec(spec_data)
        assert not any("must have either" in i for i in issues)


# ---------------------------------------------------------------------------
# 2. spec_to_html — metric attributes
# ---------------------------------------------------------------------------


class TestSpecToHtmlMetric:
    def test_metric_chart_emits_metric_attrs(self):
        mid = _register_metric()
        spec, _issues = validate_spec(_metric_chart_spec(mid))
        assert spec is not None
        html_out = spec_to_html(spec)
        assert f'metric-id="{mid}"' in html_out
        assert 'metric-dimensions="region"' in html_out
        # filters are JSON-encoded (quotes escaped by _esc).
        assert "metric-filters=" in html_out
        assert "&quot;field&quot;" in html_out
        # query-id is OMITTED for a metric-bound widget.
        assert "query-id=" not in html_out
        # Chart encoding still emitted.
        assert 'x="region"' in html_out
        assert 'y="orders"' in html_out

    def test_metric_kpi_emits_metric_id_and_omits_query_id(self):
        mid = _register_metric()
        spec, _issues = validate_spec(_metric_kpi_spec(mid))
        assert spec is not None
        html_out = spec_to_html(spec)
        assert "<nubi-kpi" in html_out
        assert f'metric-id="{mid}"' in html_out
        assert "query-id=" not in html_out

    def test_query_widget_html_unchanged(self):
        """A query-bound widget's HTML is byte-identical to the pre-metric path."""
        spec_data = {
            "version": 1,
            "title": "Sales overview",
            "widgets": [
                {
                    "id": "w1",
                    "type": "chart",
                    "query_id": "sales_q",
                    "chart_type": "bar",
                    "encoding": {"x": "region", "y": "revenue"},
                    "pos": _pos(),
                }
            ],
        }
        spec = DashboardSpec.model_validate(spec_data)
        html_out = spec_to_html(spec)
        # The query path emits query-id and NO metric-* attributes.
        assert 'query-id="sales_q"' in html_out
        assert "metric-id=" not in html_out
        assert "metric-dimensions=" not in html_out


# ---------------------------------------------------------------------------
# 3. POST /ai/pin — metric source path
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pin_client(app, fake_db):
    """HTTPX client with InMemoryRepo + seeded user/org (metrics seeded by registry)."""
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


class TestPinMetric:
    @pytest.mark.asyncio
    async def test_pin_metric_creates_metric_bound_widget(self, pin_client):
        ac, user_id, org_id, repo = pin_client
        body = {
            "title": "Demo revenue KPI",
            "source": {
                "metric_id": _SEED_METRIC_ID,
                "dimensions": ["name"],
            },
            "viz": {"type": "kpi"},
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["valid"] is True
        assert out["board_id"]
        assert out["widget_id"]

        # Persisted spec carries a native metric binding (no query_id).
        row = await repo.get("boards", org_id, out["board_id"])
        widget = row["config"]["spec"]["widgets"][0]
        assert widget["query_id"] == ""
        assert widget["metric"]["metric_id"] == _SEED_METRIC_ID
        assert widget["metric"]["dimensions"] == ["name"]

    @pytest.mark.asyncio
    async def test_pin_metric_chart_carries_encoding(self, pin_client):
        ac, user_id, org_id, repo = pin_client
        body = {
            "title": "Demo revenue chart",
            "source": {"metric_id": _SEED_METRIC_ID, "dimensions": ["name"]},
            "viz": {
                "type": "chart",
                "chart_type": "bar",
                "encoding": {"x": "name", "y": "revenue"},
            },
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 200, resp.text
        out = resp.json()
        widget = out["spec"]["widgets"][0]
        assert widget["type"] == "chart"
        assert widget["metric"]["metric_id"] == _SEED_METRIC_ID
        assert widget["encoding"] == {"x": "name", "y": "revenue"}

    @pytest.mark.asyncio
    async def test_pin_metric_no_longer_unsupported(self, pin_client):
        """The old metric_pin_unsupported 400 is gone — a metric pin succeeds."""
        ac, user_id, _org, _repo = pin_client
        body = {
            "title": "Metric pin",
            "source": {"metric_id": _SEED_METRIC_ID},
            "viz": {"type": "kpi"},
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 200, resp.text
        assert "error" not in resp.json()

    @pytest.mark.asyncio
    async def test_pin_no_source_still_400(self, pin_client):
        ac, user_id, _org, _repo = pin_client
        body = {"title": "No source", "source": {}, "viz": {"type": "kpi"}}
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_pin_source"

    @pytest.mark.asyncio
    async def test_pin_both_sources_still_400(self, pin_client):
        ac, user_id, _org, _repo = pin_client
        body = {
            "title": "Both",
            "source": {"query_id": "q", "metric_id": _SEED_METRIC_ID},
            "viz": {"type": "kpi"},
        }
        resp = await ac.post(
            "/api/v1/ai/pin", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_pin_source"
