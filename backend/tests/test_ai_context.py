"""Tests for the AI authoring-context layer (M23-A).

Coverage
--------
1. build_catalog enrichment (unit — no HTTP)
   a. Each query descriptor carries `params` and `output_schema` keys.
   b. A fixture query with declared params/output_schema surfaces real names.
   c. Existing keys (id/name/tables/outputs) are preserved (ADD-ONLY).
   d. ground() exposes `related_query_details` without breaking related_queries.

2. GET /ai/context endpoint
   a. 401 without auth.
   b. 200 with auth; response has {queries, conventions, compact, filtered_by}.
   c. A fixture query shows populated params + output_schema.
   d. `?q=` narrows / reorders the queries to the most relevant.
   e. `?compact=true` returns the trimmed per-query shape.
   f. `conventions` block is present and non-empty.

Network safety
--------------
No LLM provider is invoked by /ai/context; all tests are pure + offline.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.grounding import build_catalog, ground
from app.auth.jwt import mint_access_token
from app.queries.registry import (
    OutputColumn,
    QueryParam,
    get_query_registry,
)


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "context-tester@example.com",
        "name": "Context Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


#: A registered query id used as a fixture across these tests.  It carries BOTH
#: declared params and an output_schema so we can assert they surface.
_FIXTURE_QUERY_ID = "ctx_orders_by_region"


#: A registered metric id used as a fixture for the /ai/context metrics block.
_FIXTURE_METRIC_ID = "ctx_revenue"


def _register_fixture_metric() -> str:
    """Register a governed metric and return its id, or skip if the layer's absent.

    The metrics registry (``app/metrics/registry.py``) is built by another wave;
    if it isn't present yet we skip the test rather than fail. We construct a
    real ``MetricDefinition`` (revenue = SUM(amount), groupable by region, monthly
    grain) and register it, adapting to whatever the registry's register signature
    accepts (mirrors ``QueryRegistry.register``).
    """
    try:
        from app.metrics.models import (
            Dimension,
            Measure,
            MetricDefinition,
            TimeDimension,
        )
        from app.metrics.registry import get_metric_registry
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"metrics registry not available yet: {exc}")

    md = MetricDefinition(
        id=_FIXTURE_METRIC_ID,
        name="Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="orders",
        dimensions=(Dimension(name="region", type="text"),),
        time_dimension=TimeDimension(
            column="created_at",
            grains=("month", "quarter", "year"),
            default_grain="month",
        ),
        rls_keys=("org_id",),
        description="Total order revenue (SUM of amount).",
    )

    registry = get_metric_registry()
    # Adapt to the registry's register signature: prefer register(definition),
    # fall back to register(id=..., definition=...) if it mirrors QueryRegistry's
    # keyword style.
    try:
        registry.register(md)
    except TypeError:
        registry.register(id=md.id, definition=md)  # type: ignore[call-arg]
    return _FIXTURE_METRIC_ID


def _register_fixture_query() -> str:
    """Register a query with real params + output_schema; return its id."""
    registry = get_query_registry()
    registry.register(
        id=_FIXTURE_QUERY_ID,
        sql=(
            "SELECT id AS order_id, name AS region, active AS is_active "
            "FROM demo WHERE (name = {{region}} OR {{region}} IS NULL)"
        ),
        name="Orders by region",
        params=[
            QueryParam(
                name="region",
                type="select",
                default=None,
                required=True,
                options_query_id="demo_all",
            ),
        ],
        output_schema=[
            OutputColumn(name="order_id", type="number"),
            OutputColumn(name="region", type="text"),
            OutputColumn(name="is_active", type="bool"),
        ],
    )
    return _FIXTURE_QUERY_ID


@pytest_asyncio.fixture
async def ctx_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for /ai/context tests."""
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
# 1. build_catalog / ground enrichment (unit)
# ---------------------------------------------------------------------------


class TestCatalogEnrichment:
    """Unit tests for the build_catalog / ground enrichment."""

    def test_every_query_has_new_fields(self):
        _register_fixture_query()
        catalog = build_catalog()
        for qd in catalog["queries"]:
            assert "params" in qd
            assert "output_schema" in qd
            assert "datastore" in qd
            assert isinstance(qd["params"], list)
            assert isinstance(qd["output_schema"], list)

    def test_existing_keys_preserved(self):
        """ADD-ONLY: original keys are untouched."""
        _register_fixture_query()
        catalog = build_catalog()
        for qd in catalog["queries"]:
            assert "id" in qd
            assert "name" in qd
            assert "tables" in qd
            assert "outputs" in qd

    def test_fixture_query_surfaces_real_params_and_outputs(self):
        qid = _register_fixture_query()
        catalog = build_catalog()
        entry = next(q for q in catalog["queries"] if q["id"] == qid)

        # params
        assert len(entry["params"]) == 1
        p = entry["params"][0]
        assert p["name"] == "region"
        assert p["type"] == "select"
        assert p["required"] is True
        assert p["options_query_id"] == "demo_all"

        # output_schema
        out_names = [c["name"] for c in entry["output_schema"]]
        assert out_names == ["order_id", "region", "is_active"]
        out_by_name = {c["name"]: c["type"] for c in entry["output_schema"]}
        assert out_by_name["order_id"] == "number"
        assert out_by_name["is_active"] == "bool"

    def test_ground_exposes_related_query_details(self):
        """ground() keeps related_queries (ids) and adds related_query_details."""
        _register_fixture_query()
        catalog = build_catalog()
        grounding = ground("show me orders by region", catalog)

        # Existing key unchanged: list of id strings.
        assert isinstance(grounding["related_queries"], list)
        assert all(isinstance(x, str) for x in grounding["related_queries"])

        # New key: parallel richer descriptors.
        assert "related_query_details" in grounding
        ids = [d["id"] for d in grounding["related_query_details"]]
        assert ids == grounding["related_queries"]
        for d in grounding["related_query_details"]:
            assert "params" in d
            assert "output_schema" in d


# ---------------------------------------------------------------------------
# 2. GET /ai/context endpoint
# ---------------------------------------------------------------------------


class TestContextEndpoint:
    """HTTP endpoint tests for GET /ai/context."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, ctx_client):
        ac, _ = ctx_client
        resp = await ac.get("/api/v1/ai/context")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_with_auth(self, ctx_client):
        _register_fixture_query()
        ac, user_id = ctx_client
        resp = await ac.get("/api/v1/ai/context", headers=_auth_headers(user_id))
        assert resp.status_code == 200
        body = resp.json()
        assert "queries" in body
        assert "metrics" in body
        assert "conventions" in body
        assert "compact" in body
        assert "filtered_by" in body

    @pytest.mark.asyncio
    async def test_conventions_present_and_nonempty(self, ctx_client):
        ac, user_id = ctx_client
        resp = await ac.get("/api/v1/ai/context", headers=_auth_headers(user_id))
        body = resp.json()
        conventions = body["conventions"]
        assert isinstance(conventions, dict)
        assert len(conventions) > 0
        # Factual keys an agent needs.
        assert "query_binding" in conventions
        assert "variables" in conventions
        assert "spec_binding" in conventions

    @pytest.mark.asyncio
    async def test_fixture_query_has_params_and_output_schema(self, ctx_client):
        qid = _register_fixture_query()
        ac, user_id = ctx_client
        resp = await ac.get("/api/v1/ai/context", headers=_auth_headers(user_id))
        body = resp.json()
        entry = next(q for q in body["queries"] if q["id"] == qid)

        assert [p["name"] for p in entry["params"]] == ["region"]
        assert entry["params"][0]["type"] == "select"
        assert entry["params"][0]["required"] is True

        out_names = [c["name"] for c in entry["output_schema"]]
        assert out_names == ["order_id", "region", "is_active"]

    @pytest.mark.asyncio
    async def test_default_returns_all_queries(self, ctx_client):
        _register_fixture_query()
        ac, user_id = ctx_client
        resp = await ac.get("/api/v1/ai/context", headers=_auth_headers(user_id))
        body = resp.json()
        assert body["filtered_by"] is None
        registry = get_query_registry()
        # Every registered query is represented.
        ctx_ids = {q["id"] for q in body["queries"]}
        for rq in registry.all():
            assert rq.id in ctx_ids

    @pytest.mark.asyncio
    async def test_q_narrows_and_reorders(self, ctx_client):
        """`?q=` filters to the relevant queries and is a subset of the full set."""
        _register_fixture_query()
        ac, user_id = ctx_client
        headers = _auth_headers(user_id)

        full = await ac.get("/api/v1/ai/context", headers=headers)
        full_ids = [q["id"] for q in full.json()["queries"]]

        filtered = await ac.get(
            "/api/v1/ai/context", params={"q": "orders by region"}, headers=headers
        )
        assert filtered.status_code == 200
        fbody = filtered.json()
        filtered_ids = [q["id"] for q in fbody["queries"]]

        assert fbody["filtered_by"] == "orders by region"
        # Narrowing: strictly fewer (or equal) queries than the unfiltered set,
        # and every filtered id is a real registered id.
        assert len(filtered_ids) <= len(full_ids)
        assert set(filtered_ids).issubset(set(full_ids))
        # The region-relevant fixture query (touches the `demo` table) is included.
        assert _FIXTURE_QUERY_ID in filtered_ids
        # A query that shares no relevant tables is excluded — e.g. point clouds.
        assert "demo_points_10k" not in filtered_ids

    @pytest.mark.asyncio
    async def test_compact_trims_shape(self, ctx_client):
        qid = _register_fixture_query()
        ac, user_id = ctx_client
        resp = await ac.get(
            "/api/v1/ai/context",
            params={"compact": "true"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["compact"] is True
        entry = next(q for q in body["queries"] if q["id"] == qid)

        # Compact drops description + datastore.
        assert "description" not in entry
        assert "datastore" not in entry
        # Core binding fields remain.
        assert "id" in entry
        assert "name" in entry
        assert "params" in entry
        assert "output_schema" in entry

        # Compact params drop default + options_query_id.
        p = entry["params"][0]
        assert set(p.keys()) == {"name", "type", "required"}

    @pytest.mark.asyncio
    async def test_full_has_description_and_datastore(self, ctx_client):
        qid = _register_fixture_query()
        ac, user_id = ctx_client
        resp = await ac.get("/api/v1/ai/context", headers=_auth_headers(user_id))
        body = resp.json()
        assert body["compact"] is False
        entry = next(q for q in body["queries"] if q["id"] == qid)
        assert "description" in entry
        assert "datastore" in entry
        # Full param entries carry the verbose fields too.
        p = entry["params"][0]
        assert "default" in p
        assert "options_query_id" in p


# ---------------------------------------------------------------------------
# 3. GET /ai/context — metrics block (Wave C3)
# ---------------------------------------------------------------------------


class TestContextMetricsBlock:
    """The /ai/context `metrics` block surfaces registered governed metrics."""

    @pytest.mark.asyncio
    async def test_metrics_block_is_present(self, ctx_client):
        """The response always carries a `metrics` list (possibly empty)."""
        ac, user_id = ctx_client
        resp = await ac.get("/api/v1/ai/context", headers=_auth_headers(user_id))
        assert resp.status_code == 200
        body = resp.json()
        assert "metrics" in body
        assert isinstance(body["metrics"], list)

    @pytest.mark.asyncio
    async def test_conventions_mention_metrics(self, ctx_client):
        """The conventions block explains how to query a governed metric."""
        ac, user_id = ctx_client
        resp = await ac.get("/api/v1/ai/context", headers=_auth_headers(user_id))
        conventions = resp.json()["conventions"]
        assert "metrics" in conventions
        assert "/metrics/" in conventions["metrics"]

    @pytest.mark.asyncio
    async def test_fixture_metric_has_full_shape(self, ctx_client):
        """A registered metric surfaces with {id, name, measure, dimensions,
        time_grains, description}."""
        mid = _register_fixture_metric()  # skips if the registry isn't built yet
        ac, user_id = ctx_client
        resp = await ac.get("/api/v1/ai/context", headers=_auth_headers(user_id))
        body = resp.json()
        entry = next(m for m in body["metrics"] if m["id"] == mid)

        assert entry["name"] == "Revenue"
        assert entry["measure"] == {"name": "revenue", "agg": "sum", "expr": "amount"}
        assert entry["dimensions"] == ["region"]
        assert entry["time_grains"] == ["month", "quarter", "year"]
        assert entry["description"] == "Total order revenue (SUM of amount)."

    @pytest.mark.asyncio
    async def test_compact_trims_metric_shape(self, ctx_client):
        """`?compact=true` trims a metric to {id, name, measure, dimensions}."""
        mid = _register_fixture_metric()
        ac, user_id = ctx_client
        resp = await ac.get(
            "/api/v1/ai/context",
            params={"compact": "true"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert body["compact"] is True
        entry = next(m for m in body["metrics"] if m["id"] == mid)

        assert set(entry.keys()) == {"id", "name", "measure", "dimensions"}
        # Compact drops the verbose fields.
        assert "time_grains" not in entry
        assert "description" not in entry
        # Core fields remain accurate.
        assert entry["measure"]["agg"] == "sum"
        assert entry["dimensions"] == ["region"]
