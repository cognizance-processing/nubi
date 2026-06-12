"""Query/Metric unification — adapter, query-backed registry load, and routes.

The metric registry now SOURCES metrics from queries-with-``config.metric``
(keyed by ``config.metric.slug``). This file covers:

(1)  ``_definition_from_query_row`` maps a query row → MetricDefinition (slug=id,
     config.sql → base_sql, base_table stays None, measure/dims/time/rls/etc.).
(2)  A query with no ``metric`` block yields no metric (plain query unaffected).
(3)  ``load_metrics_from_queries`` registers a query-backed metric BY SLUG.
(4)  ``ensure_persisted_metric`` resolves a query-with-metric by slug.
(5)  ``POST /metrics`` lands in the ``queries`` table (not ``metrics``).
(6)  ``GET /metrics`` returns query-backed metrics.
(7)  Creating a query with an invalid ``config.metric`` block → 400.

The migration + slug-stability-after-migration tests live in the PG-gated
``test_pg_integration.py`` (they need real Postgres jsonb ops).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _query_row(slug: str = "revenue", *, with_metric: bool = True) -> dict:
    """A `queries` row dict, optionally carrying a config.metric block."""
    config: dict = {
        "sql": "SELECT order_date, region, amount FROM orders",
        "datastore_id": "ds-1",
    }
    if with_metric:
        config["metric"] = {
            "slug": slug,
            "measure": {"name": "revenue", "agg": "sum", "expr": "amount",
                        "type": "additive", "format": "currency"},
            "dimensions": [{"name": "region", "expr": None, "type": "text"}],
            "time_dimension": {"column": "order_date",
                               "grains": ["day", "week", "month"],
                               "default_grain": "day"},
            "default_filters": [],
            "rls_keys": ["tenant_id"],
            "owner": None,
            "description": "Total revenue",
        }
    return {
        "id": str(uuid.uuid4()),
        "org_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "name": "Revenue",
        "config": config,
    }


# ---------------------------------------------------------------------------
# (1) Adapter maps a query row → MetricDefinition
# ---------------------------------------------------------------------------


def test_definition_from_query_row_maps_fields():
    from app.metrics.registry import _definition_from_query_row

    metric = _definition_from_query_row(_query_row("revenue"))
    assert metric is not None
    assert metric.id == "revenue"  # id = config.metric.slug
    assert metric.name == "Revenue"
    assert metric.base_sql == "SELECT order_date, region, amount FROM orders"
    assert metric.base_table is None  # queries are SQL
    assert metric.datastore_id == "ds-1"
    assert metric.measure.expr == "amount"
    assert metric.measure.format == "currency"
    assert [d.name for d in metric.dimensions] == ["region"]
    assert metric.time_dimension is not None
    assert metric.time_dimension.column == "order_date"
    assert metric.rls_keys == ("tenant_id",)


def test_definition_from_query_row_parses_json_text_config():
    """A config delivered as a JSON string (asyncpg jsonb) still adapts."""
    import json

    from app.metrics.registry import _definition_from_query_row

    row = _query_row("revenue")
    row["config"] = json.dumps(row["config"])
    metric = _definition_from_query_row(row)
    assert metric is not None
    assert metric.id == "revenue"


# ---------------------------------------------------------------------------
# (2) A plain query (no metric block) yields no metric
# ---------------------------------------------------------------------------


def test_plain_query_yields_no_metric():
    from app.metrics.registry import _definition_from_query_row

    assert _definition_from_query_row(_query_row(with_metric=False)) is None


# ---------------------------------------------------------------------------
# (3) load_metrics_from_queries registers BY SLUG
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_metrics_from_queries_registers_by_slug(monkeypatch):
    from app.metrics import registry as reg

    reg.reset_for_tests()
    rows = [_query_row("revenue"), _query_row(with_metric=False)]

    async def _fake_fetch(query, *args):
        assert "config ? 'metric'" in query
        return rows

    monkeypatch.setattr("app.db.fetch", _fake_fetch)

    loaded = await reg.load_metrics_from_queries()
    assert loaded == 1  # the plain query is skipped
    metric = reg.get_metric_registry().get("revenue")
    assert metric is not None and metric.id == "revenue"
    reg.reset_for_tests()


# ---------------------------------------------------------------------------
# (4) ensure_persisted_metric resolves a query-with-metric by slug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_persisted_metric_by_slug(monkeypatch):
    from app.metrics import registry as reg

    reg.reset_for_tests()
    row = _query_row("revenue")

    async def _fake_fetchrow(query, *args):
        assert args[0] == "revenue"
        assert "config->'metric'->>'slug'" in query
        return row

    monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

    metric = await reg.ensure_persisted_metric("revenue")
    assert metric is not None
    assert metric.id == "revenue"
    assert metric.base_sql.startswith("SELECT order_date")
    reg.reset_for_tests()


# ---------------------------------------------------------------------------
# Route-level fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def m_client(app, fake_db):
    """Client with an InMemoryRepo + a seeded user/org membership.

    The query-creation route (``POST /queries``) resolves the caller's org via
    the repo, so we inject an ``InMemoryRepo`` and seed membership — the same
    pattern ``test_resources.py`` uses.
    """
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "unif_tester@example.com",
        "name": "Unif Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id
    set_repo(None)


def _revenue_def(name: str) -> dict:
    return {
        "name": name,
        "measure": {"name": "revenue", "agg": "sum", "expr": "value"},
        "base_table": "demo",
        "dimensions": [{"name": "name", "type": "text"}],
    }


# ---------------------------------------------------------------------------
# (5) POST /metrics lands in the queries table (not metrics)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_metric_persists_as_query(m_client, monkeypatch):
    client, user_id = m_client
    captured: dict[str, object] = {}

    async def _fake_execute(query, *args):
        if "INSERT INTO queries" in query:
            captured["insert"] = args
        return "INSERT 0 1"

    async def _fake_fetchrow(query, *args):
        # No existing backing query for this slug → INSERT path.
        return None

    monkeypatch.setattr("app.db.execute", _fake_execute)
    monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

    name = f"unif_revenue_{uuid.uuid4().hex[:6]}"
    resp = await client.post(
        "/api/v1/metrics", json=_revenue_def(name), headers=_auth_headers(user_id)
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Canonical id is the slug (stable), not a UUID.
    assert body["id"] == name
    # It persisted via INSERT INTO queries, carrying a config.metric block.
    assert "insert" in captured, "POST /metrics did not write to the queries table"
    config_json = captured["insert"][-1]
    assert '"metric"' in config_json
    assert name in config_json


# ---------------------------------------------------------------------------
# (6) GET /metrics returns query-backed metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_metrics_lists_query_backed_metric(m_client, monkeypatch):
    from app.metrics import registry as reg

    reg.reset_for_tests()
    row = _query_row("revenue")
    reg.get_metric_registry().register(reg._definition_from_query_row(row))

    # Org scoping reads the org's exposed slugs from the queries table — stub it
    # so 'revenue' is recognised as an org-owned query-backed metric.
    async def _fake_fetch(query, *args):
        if "config ? 'metric'" in query:
            return [{"slug": "revenue"}]
        return []

    monkeypatch.setattr("app.db.fetch", _fake_fetch)

    client, user_id = m_client
    resp = await client.get("/api/v1/metrics", headers=_auth_headers(user_id))
    assert resp.status_code == 200, resp.text
    ids = [m["id"] for m in resp.json()["metrics"]]
    # The query-backed metric (slug) and the in-code seed are both visible.
    assert "revenue" in ids
    assert "demo_revenue" in ids
    reg.reset_for_tests()


# ---------------------------------------------------------------------------
# (7) Creating a query with an invalid config.metric block → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_query_with_bad_metric_block_returns_400(m_client):
    client, user_id = m_client
    # Measure with agg=sum but expr='*' (must reference a column) → invalid.
    body = {
        "name": "Bad Metric Query",
        "config": {
            "sql": "SELECT region, amount FROM orders",
            "metric": {
                "slug": "bad_rev",
                "measure": {"name": "rev", "agg": "sum", "expr": "*"},
                "dimensions": [],
            },
        },
    }
    resp = await client.post(
        "/api/v1/queries", json=body, headers=_auth_headers(user_id)
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "invalid_measure"


@pytest.mark.asyncio
async def test_create_query_with_valid_metric_block_ok(m_client):
    client, user_id = m_client
    body = {
        "name": "Good Metric Query",
        "config": {
            "sql": "SELECT region, amount FROM orders",
            "metric": {
                "slug": "good_rev",
                "measure": {"name": "rev", "agg": "sum", "expr": "amount"},
                "dimensions": [{"name": "region", "type": "text"}],
            },
        },
    }
    resp = await client.post(
        "/api/v1/queries", json=body, headers=_auth_headers(user_id)
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_create_plain_query_unaffected(m_client):
    """A plain query (no metric block) is created normally."""
    client, user_id = m_client
    body = {"name": "Plain Query", "config": {"sql": "SELECT 1"}}
    resp = await client.post(
        "/api/v1/queries", json=body, headers=_auth_headers(user_id)
    )
    assert resp.status_code == 201, resp.text


# ===========================================================================
# TENANT ISOLATION (SEC) — a metric slug is org-scoped END TO END.
#
# The metric registry is a process-GLOBAL singleton and slugs are only UNIQUE
# per (org, slug). Before the fix, ``ensure_persisted_metric(slug)`` resolved a
# query-with-metric by slug with NO org filter, so org A could read / compile /
# execute org B's metric (base_sql + datastore + dimension disclosure, plus
# cross-tenant execution) by knowing B's slug. These tests prove the fix.
# ===========================================================================


@pytest.mark.asyncio
async def test_ensure_persisted_metric_is_org_filtered(monkeypatch):
    """The slug→query DB lookup MUST carry an org filter when org_id is given."""
    from app.metrics import registry as reg

    reg.reset_for_tests()
    captured: dict[str, object] = {}
    org_b = str(uuid.uuid4())
    row = _query_row("secret_rev")
    row["org_id"] = org_b

    async def _fake_fetchrow(query, *args):
        captured["query"] = query
        captured["args"] = args
        # Simulate the DB applying the org filter: only org_b owns the slug.
        if "org_id = $2::uuid" in query and args[1] == org_b:
            return row
        return None

    monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

    # Org B (the owner) resolves it.
    owner = await reg.ensure_persisted_metric("secret_rev", org_b)
    assert owner is not None
    assert "org_id = $2::uuid" in captured["query"], "lookup is NOT org-scoped"
    assert captured["args"] == ("secret_rev", org_b)

    # Org A (a different org) does NOT — the org filter excludes B's row.
    reg.reset_for_tests()
    org_a = str(uuid.uuid4())
    foreign = await reg.ensure_persisted_metric("secret_rev", org_a)
    assert foreign is None, "org A resolved org B's metric by slug — CROSS-TENANT LEAK"
    reg.reset_for_tests()


@pytest.mark.asyncio
async def test_metric_belongs_to_org(monkeypatch):
    """Ownership gate: seeds always pass; foreign-org slugs do not."""
    from app.metrics import registry as reg

    org_b = str(uuid.uuid4())
    org_a = str(uuid.uuid4())

    async def _fake_fetchrow(query, *args):
        # Only org_b owns 'secret_rev'.
        if args[0] == "secret_rev" and args[1] == org_b:
            return {"ok": 1}
        return None

    monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

    # In-code seeds belong to no tenant → always allowed (no DB hit needed).
    assert await reg.metric_belongs_to_org("demo_revenue", org_a) is True
    # The owning org passes; a foreign org fails (fail-closed).
    assert await reg.metric_belongs_to_org("secret_rev", org_b) is True
    assert await reg.metric_belongs_to_org("secret_rev", org_a) is False


@pytest.mark.asyncio
async def test_shared_registry_hit_rejected_for_foreign_org(monkeypatch):
    """A process-global registry HIT for org B's slug is invisible to org A.

    The registry is shared across the process: org B's metric may already be
    loaded in-memory from an earlier request. ``_resolve_metric`` MUST still
    verify org ownership of a ``registry.get()`` hit, else org A reads it for
    free without even a DB round-trip.
    """
    from app.metrics import registry as reg
    from app.routes.metrics import _resolve_metric

    reg.reset_for_tests()
    # Org B's metric is ALREADY in the shared in-memory registry.
    org_b_row = _query_row("secret_rev")
    reg.get_metric_registry().register(reg._definition_from_query_row(org_b_row))
    assert reg.get_metric_registry().get("secret_rev") is not None

    org_a = str(uuid.uuid4())

    # Org A does not own the slug: ownership check returns False AND the
    # org-filtered DB fallback finds nothing → 404 (no cross-org leak).
    async def _fake_fetchrow(query, *args):
        return None  # org A owns nothing

    monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

    import pytest as _pytest
    from app.errors import AppError

    with _pytest.raises(AppError) as ei:
        await _resolve_metric("secret_rev", org_a)
    assert ei.value.status == 404
    assert ei.value.code == "metric_not_found"

    # Sanity: the legitimate owner (org_b) still resolves the registry hit.
    async def _fake_fetchrow_owner(query, *args):
        if args[0] == "secret_rev":
            return {"ok": 1} if "SELECT 1" in query else org_b_row
        return None

    monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow_owner)
    owner_metric = await _resolve_metric("secret_rev", org_b_row["org_id"])
    assert owner_metric is not None and owner_metric.id == "secret_rev"
    reg.reset_for_tests()


@pytest.mark.asyncio
async def test_get_metric_route_blocks_cross_tenant_slug(m_client, monkeypatch):
    """End-to-end: GET /metrics/{slug} for ANOTHER org's slug → 404.

    org A's authenticated user requests a slug that only org B owns. The route
    must not leak org B's MetricDefinition (base_sql / datastore binding).
    """
    from app.metrics import registry as reg

    reg.reset_for_tests()
    # Org B's metric sits in the shared registry (loaded by an earlier request).
    org_b_row = _query_row("secret_rev")
    reg.get_metric_registry().register(reg._definition_from_query_row(org_b_row))

    client, user_id = m_client  # this user belongs to org A (seeded by fixture)

    # The org-scoped ownership / fallback DB lookups find nothing for org A.
    async def _fake_fetchrow(query, *args):
        return None

    monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

    resp = await client.get(
        "/api/v1/metrics/secret_rev", headers=_auth_headers(user_id)
    )
    assert resp.status_code == 404, resp.text
    # And the body never carries org B's base_sql.
    assert "order_date" not in resp.text
    reg.reset_for_tests()


@pytest.mark.asyncio
async def test_query_metric_route_blocks_cross_tenant_slug(m_client, monkeypatch):
    """End-to-end: POST /metrics/{slug}/query for another org's slug → 404.

    Proves org A cannot EXECUTE org B's metric (base_sql against B's binding).
    """
    from app.metrics import registry as reg

    reg.reset_for_tests()
    org_b_row = _query_row("secret_rev")
    reg.get_metric_registry().register(reg._definition_from_query_row(org_b_row))

    client, user_id = m_client  # org A user

    async def _fake_fetchrow(query, *args):
        return None

    monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

    resp = await client.post(
        "/api/v1/metrics/secret_rev/query",
        json={"dimensions": ["region"]},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404, resp.text
    reg.reset_for_tests()
