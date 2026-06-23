"""Metrics routes — CRUD + compile + execute + governance (Wave C1).

Coverage
--------
(1)  POST /metrics then GET /metrics/{id} round-trips the definition.
(2)  POST /metrics/{id}/sql returns compiled SQL + params (no execution).
(3)  POST /metrics/{id}/query executes a SEEDED metric against the demo
     connector and returns Arrow rows.
(4)  POST /metrics/{id}/query for a freshly-created metric returns rows.
(5)  A MetricQuery with an unknown dimension → 400 (governance).
(6)  POST /metrics with a definition that has no source → 400.
(7)  Embed token rejected from POST /metrics → 403.
(8)  GET /metrics lists the seeded demo metric.
(9)  Unauthenticated POST /metrics → 401.

Notes
-----
The metric registry is a process-global singleton with a built-in ``demo_revenue``
seed (SUM(value) from the 5-row demo table). conftest does not reset it between
cases, so tests use the seed for execution and unique names for created metrics.
Real-datastore execution is exercised through the built-in demo DuckDB connector
(``base_table='demo'``) — no external warehouse is needed.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _embed_headers(user_id: str) -> dict[str, str]:
    import time

    import jwt

    from app.config import get_settings

    settings = get_settings()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": user_id,
            "kind": "embed",
            "scope": ["read:query"],
            "iat": now,
            "exp": now + 900,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _parse_arrow(content: bytes):
    return pa_ipc.open_stream(BytesIO(content)).read_all()


@pytest_asyncio.fixture
async def m_client(app, fake_db):
    """HTTPX client with a seeded user for the metrics tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "metric_tester@example.com",
        "name": "Metric Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id


def _revenue_def(name: str) -> dict:
    """A valid metric definition over the built-in demo table."""
    return {
        "name": name,
        "measure": {"name": "revenue", "agg": "sum", "expr": "value"},
        "base_table": "demo",
        "dimensions": [
            {"name": "name", "type": "text"},
            {"name": "active", "type": "bool"},
        ],
    }


# ---------------------------------------------------------------------------
# (1) POST then GET round-trips the definition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_then_get_metric(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    name = f"Test Revenue {uuid.uuid4().hex[:8]}"
    resp = await client.post("/api/v1/metrics", json=_revenue_def(name), headers=headers)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == name
    assert created["measure"]["agg"] == "sum"
    assert created["base_table"] == "demo"
    metric_id = created["id"]

    get_resp = await client.get(f"/api/v1/metrics/{metric_id}", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    fetched = get_resp.json()
    assert fetched["id"] == metric_id
    assert fetched["measure"]["expr"] == "value"
    assert {d["name"] for d in fetched["dimensions"]} == {"name", "active"}


# ---------------------------------------------------------------------------
# (2) /metrics/{id}/sql returns compiled SQL + params (no execution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metric_sql_dry_compile(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/sql",
        json={"dimensions": ["name"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "sql" in body and "params" in body
    sql = body["sql"].lower()
    assert "sum" in sql
    assert "demo" in sql
    assert "group by" in sql


@pytest.mark.asyncio
async def test_metric_sql_binds_filter_params(m_client):
    """A user filter is bound as a {{param}} placeholder, not concatenated."""
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/sql",
        json={
            "dimensions": ["name"],
            "filters": [{"field": "active", "op": "=", "value": True}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The filter value rides in params, never inlined into the SQL text.
    assert any(v is True for v in body["params"].values())


# ---------------------------------------------------------------------------
# (3) /metrics/{id}/query executes the seeded metric → Arrow rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_seeded_metric_returns_rows(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/query",
        json={"dimensions": ["name"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("content-type", "").startswith(
        "application/vnd.apache.arrow.stream"
    )
    table = _parse_arrow(resp.content)
    # 5 distinct demo names → 5 grouped rows, with a `name` + `revenue` column.
    assert table.num_rows == 5
    assert "name" in table.schema.names
    assert "revenue" in table.schema.names


# ---------------------------------------------------------------------------
# (4) A freshly-created metric is immediately queryable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_created_metric_is_queryable(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    name = f"Live Metric {uuid.uuid4().hex[:8]}"
    create = await client.post("/api/v1/metrics", json=_revenue_def(name), headers=headers)
    assert create.status_code == 201, create.text
    metric_id = create.json()["id"]

    run = await client.post(
        f"/api/v1/metrics/{metric_id}/query",
        json={"dimensions": ["name"]},
        headers=headers,
    )
    assert run.status_code == 200, run.text
    table = _parse_arrow(run.content)
    assert table.num_rows == 5


# ---------------------------------------------------------------------------
# (5) Unknown dimension → 400 (governance)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_dimension_returns_400(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/query",
        json={"dimensions": ["definitely_not_a_dimension"]},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_unknown_dimension_sql_returns_400(m_client):
    """The /sql dry-compile path also governs unknown dimensions."""
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/sql",
        json={"dimensions": ["nope"]},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# (6) Bad definition (no source) → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_metric_without_source_returns_400(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    bad = {
        "name": f"No Source {uuid.uuid4().hex[:8]}",
        "measure": {"name": "revenue", "agg": "sum", "expr": "value"},
        # no base_table and no base_sql
    }
    resp = await client.post("/api/v1/metrics", json=bad, headers=headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "no_source"


@pytest.mark.asyncio
async def test_create_metric_with_empty_measure_returns_400(m_client):
    """A non-count measure that references nothing → 400."""
    client, user_id = m_client
    headers = _auth_headers(user_id)

    bad = {
        "name": f"Bad Measure {uuid.uuid4().hex[:8]}",
        "measure": {"name": "revenue", "agg": "sum", "expr": "*"},
        "base_table": "demo",
    }
    resp = await client.post("/api/v1/metrics", json=bad, headers=headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "invalid_measure"


# ---------------------------------------------------------------------------
# (7) Embed token rejected from POST /metrics → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_token_cannot_create_metric(m_client):
    client, user_id = m_client

    resp = await client.post(
        "/api/v1/metrics",
        json=_revenue_def(f"Embed Attempt {uuid.uuid4().hex[:8]}"),
        headers=_embed_headers(user_id),
    )
    assert resp.status_code in (401, 403), resp.text


# ---------------------------------------------------------------------------
# (8) GET /metrics lists the seeded demo metric
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_metrics_includes_seed(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.get("/api/v1/metrics", headers=headers)
    assert resp.status_code == 200, resp.text
    metrics = resp.json()["metrics"]
    ids = [m["id"] for m in metrics]
    assert "demo_revenue" in ids
    seed = next(m for m in metrics if m["id"] == "demo_revenue")
    assert seed["measure"]["agg"] == "sum"
    assert "name" in seed["dimensions"]


# ---------------------------------------------------------------------------
# (9) Unauthenticated POST → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_create_returns_401(m_client):
    client, _ = m_client

    resp = await client.post(
        "/api/v1/metrics", json=_revenue_def("Anon Metric")
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# (10) DELETE unregisters the metric
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_metric_unregisters(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    name = f"Doomed Metric {uuid.uuid4().hex[:8]}"
    create = await client.post("/api/v1/metrics", json=_revenue_def(name), headers=headers)
    assert create.status_code == 201, create.text
    metric_id = create.json()["id"]

    # Present before delete.
    assert (await client.get(f"/api/v1/metrics/{metric_id}", headers=headers)).status_code == 200

    delete = await client.delete(f"/api/v1/metrics/{metric_id}", headers=headers)
    assert delete.status_code == 200, delete.text
    assert delete.json()["deleted"] is True

    # Gone after delete (the persistence-free path returns 404 from the registry).
    after = await client.get(f"/api/v1/metrics/{metric_id}", headers=headers)
    assert after.status_code == 404, after.text


# ---------------------------------------------------------------------------
# (11) Embed / viewer metering INVARIANT
#
# INVARIANT: embed tokens and first-party viewer-role users are NEVER metered.
# Only first-party callers with a writer/admin/member/owner role hit enforce_quota.
#
# Tests:
#  (a) Embed identity on POST /metrics/{id}/query → enforce_quota NOT called.
#  (b) Embed identity on POST /query              → enforce_quota NOT called.
#  (c) First-party writer on POST /metrics/{id}/query → enforce_quota IS called.
#  (d) First-party viewer on POST /metrics/{id}/query → enforce_quota NOT called.
#
# Strategy: inject VerifiedIdentity via dependency_overrides to avoid needing a
# real embed issuer (asymmetric keys etc.) — the route logic only reads
# identity.kind, identity.user_id, identity.org, and identity.scope.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def metering_client(app, fake_db):
    """Client with InMemoryRepo + seeded user/org for metering-invariant tests."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "metering-inv@example.com",
        "name": "Metering Inv",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    # Seed as a writer (owner) by default.
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, org_id, repo, app

    set_repo(None)


def _embed_identity(org_id: str) -> "VerifiedIdentity":
    """Build a VerifiedIdentity with kind='embed' for dependency injection."""
    from app.auth.verify import VerifiedIdentity

    return VerifiedIdentity(
        kind="embed",
        user_id="embed-user",
        org=org_id,
        project=None,
        roles=["viewer"],
        policies={},
        scope=["read:query"],
        embed_origin=None,
        datastore=None,
        raw_claims={},
    )


def _writer_identity(user_id: str, org_id: str) -> "VerifiedIdentity":
    """Build a VerifiedIdentity with kind='access' and owner role."""
    from app.auth.verify import VerifiedIdentity

    return VerifiedIdentity(
        kind="access",
        user_id=user_id,
        org=org_id,
        project=None,
        roles=["owner"],
        policies={},
        scope=["read:query", "write:*"],
        embed_origin=None,
        datastore=None,
        raw_claims={},
    )


def _viewer_identity(user_id: str, org_id: str) -> "VerifiedIdentity":
    """Build a VerifiedIdentity with kind='access' and viewer role."""
    from app.auth.verify import VerifiedIdentity

    return VerifiedIdentity(
        kind="access",
        user_id=user_id,
        org=org_id,
        project=None,
        roles=["viewer"],
        policies={},
        scope=["read:query"],
        embed_origin=None,
        datastore=None,
        raw_claims={},
    )


@pytest.mark.asyncio
async def test_embed_token_metrics_query_skips_enforce_quota(metering_client):
    """(a) Embed identity on POST /metrics/{id}/query MUST NOT call enforce_quota."""
    from app.auth.deps import verified_identity

    client, _user_id, org_id, _repo, app_ = metering_client
    embed_id = _embed_identity(org_id)
    app_.dependency_overrides[verified_identity] = lambda: embed_id
    try:
        with patch("app.features.enforce_quota", new_callable=AsyncMock) as mock_eq:
            resp = await client.post(
                "/api/v1/metrics/demo_revenue/query",
                json={"dimensions": ["name"]},
                headers={"Authorization": "Bearer ignored"},
            )
        assert resp.status_code == 200, resp.text
        compute_calls = [
            c for c in mock_eq.call_args_list if "compute_units" in str(c)
        ]
        assert compute_calls == [], (
            f"enforce_quota was called for embed identity: {mock_eq.call_args_list}"
        )
    finally:
        app_.dependency_overrides.pop(verified_identity, None)


@pytest.mark.asyncio
async def test_embed_token_query_skips_enforce_quota(metering_client):
    """(b) Embed identity on POST /query MUST NOT call enforce_quota.

    Embed tokens must use a registered query_id (raw SQL is rejected for embed).
    We use the built-in 'demo_all' registered query.
    """
    from app.auth.deps import verified_identity

    client, _user_id, org_id, _repo, app_ = metering_client
    embed_id = _embed_identity(org_id)
    app_.dependency_overrides[verified_identity] = lambda: embed_id
    try:
        with patch("app.features.enforce_quota", new_callable=AsyncMock) as mock_eq:
            resp = await client.post(
                "/api/v1/query",
                json={"query_id": "demo_all"},
                headers={"Authorization": "Bearer ignored"},
            )
        assert resp.status_code == 200, resp.text
        compute_calls = [
            c for c in mock_eq.call_args_list if "compute_units" in str(c)
        ]
        assert compute_calls == [], (
            f"enforce_quota was called for embed identity: {mock_eq.call_args_list}"
        )
    finally:
        app_.dependency_overrides.pop(verified_identity, None)


@pytest.mark.asyncio
async def test_writer_metrics_query_calls_enforce_quota(metering_client):
    """(c) First-party writer on POST /metrics/{id}/query MUST call enforce_quota."""
    from app.auth.deps import verified_identity

    client, user_id, org_id, _repo, app_ = metering_client
    writer_id = _writer_identity(user_id, org_id)
    app_.dependency_overrides[verified_identity] = lambda: writer_id
    try:
        with patch("app.features.enforce_quota", new_callable=AsyncMock) as mock_eq:
            resp = await client.post(
                "/api/v1/metrics/demo_revenue/query",
                json={"dimensions": ["name"]},
                headers={"Authorization": "Bearer ignored"},
            )
        assert resp.status_code == 200, resp.text
        compute_calls = [
            c for c in mock_eq.call_args_list if "compute_units" in str(c)
        ]
        assert len(compute_calls) >= 1, (
            f"enforce_quota was NOT called for writer: {mock_eq.call_args_list}"
        )
    finally:
        app_.dependency_overrides.pop(verified_identity, None)


@pytest.mark.asyncio
async def test_viewer_metrics_query_skips_enforce_quota(metering_client, fake_db):
    """(d) First-party viewer on POST /metrics/{id}/query MUST NOT call enforce_quota."""
    from app.auth.deps import verified_identity

    client, _user_id, org_id, repo, app_ = metering_client

    # Create a separate viewer user in the same org.
    viewer_id = str(uuid.uuid4())
    fake_db.users[viewer_id] = {
        "id": viewer_id,
        "email": "viewer-inv@example.com",
        "name": "Viewer Inv",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=viewer_id, role="viewer")

    viewer_id_obj = _viewer_identity(viewer_id, org_id)
    app_.dependency_overrides[verified_identity] = lambda: viewer_id_obj
    try:
        with patch("app.features.enforce_quota", new_callable=AsyncMock) as mock_eq:
            resp = await client.post(
                "/api/v1/metrics/demo_revenue/query",
                json={"dimensions": ["name"]},
                headers={"Authorization": "Bearer ignored"},
            )
        assert resp.status_code == 200, resp.text
        compute_calls = [
            c for c in mock_eq.call_args_list if "compute_units" in str(c)
        ]
        assert compute_calls == [], (
            f"enforce_quota was called for viewer: {mock_eq.call_args_list}"
        )
    finally:
        app_.dependency_overrides.pop(verified_identity, None)


# ---------------------------------------------------------------------------
# (e) [audit-14] /metrics/{id}/query threads the caller's org into rollup routing
# ---------------------------------------------------------------------------
# REGRESSION: the route used to call route_to_rollup_shape WITHOUT org_id, so
# org-scoped rollups (build_rollup_for_metric(..., org_id)) were NEVER served and
# an unscoped rollup could leak across tenants.  These tests assert the route now
# passes the caller's org through, so an org-scoped rollup is served to its own
# org and never to another org.


@pytest.mark.asyncio
async def test_metrics_query_passes_caller_org_to_rollup_router(metering_client):
    """The route must call route_to_rollup_shape with org_id == the caller's org.

    We spy on the (lazily-imported) router and capture the org_id kwarg.  The
    seeded demo_revenue metric runs against the built-in demo connector, so the
    request succeeds end-to-end while we assert the org was threaded through.
    """
    from app.auth.deps import verified_identity
    from app.connectors import planner as planner_mod

    client, user_id, org_id, _repo, app_ = metering_client
    writer = _writer_identity(user_id, org_id)
    app_.dependency_overrides[verified_identity] = lambda: writer

    captured: dict[str, object] = {}
    real_router = planner_mod.route_to_rollup_shape

    def _spy(plan, registry, *, org_id=None):
        captured["org_id"] = org_id
        return real_router(plan, registry, org_id=org_id)

    try:
        with patch.object(planner_mod, "route_to_rollup_shape", _spy):
            resp = await client.post(
                "/api/v1/metrics/demo_revenue/query",
                json={"dimensions": ["name"]},
                headers={"Authorization": "Bearer ignored"},
            )
        assert resp.status_code == 200, resp.text
        assert "org_id" in captured, "router was never invoked by the route"
        assert captured["org_id"] == org_id, (
            f"route must pass the caller's org ({org_id}) to the rollup router, "
            f"got {captured['org_id']!r}"
        )
    finally:
        app_.dependency_overrides.pop(verified_identity, None)


@pytest.mark.asyncio
async def test_metrics_query_serves_org_scoped_rollup_only_to_its_org(
    metering_client,
):
    """An org-scoped rollup is served to its own org via /metrics and NEVER cross-org.

    Build a rollup tagged org_id=<caller org> for the demo connector's base table
    and confirm the router (invoked by the route with the caller's org) returns it
    as a candidate; then assert a DIFFERENT org gets ZERO candidates for the same
    shape — proving the route's org-scoping prevents cross-tenant rollup reuse.
    """
    from app.auth.deps import verified_identity
    from app.connectors import planner as planner_mod
    from app.connectors.preagg import BuiltRollup, get_registry

    client, user_id, org_id, _repo, app_ = metering_client
    writer = _writer_identity(user_id, org_id)
    app_.dependency_overrides[verified_identity] = lambda: writer

    # Register an org-scoped rollup for the demo base table in the GLOBAL registry
    # the route consults.  (We assert candidate selection rather than execution so
    # the test does not depend on a materialised rollup file matching the demo
    # connector's data — the org-scoping decision is what the fix changes.)
    reg = get_registry()
    rollup = BuiltRollup(
        rollup_id="audit14-demo-rollup",
        table="rollup_demo",
        source_table="demo",
        dimensions=["name"],
        rls_keys=[],
        org_id=org_id,
    )
    reg.add_rollup(rollup)

    captured: dict[str, object] = {}
    real_router = planner_mod.route_to_rollup_shape

    def _spy(plan, registry, *, org_id=None):
        # Record what THIS org's call sees as candidates for the demo table.
        captured["org_id"] = org_id
        captured["candidates"] = [
            r.rollup_id
            for r in registry.candidates_for_table("demo", org_id=org_id)
        ]
        return real_router(plan, registry, org_id=org_id)

    try:
        with patch.object(planner_mod, "route_to_rollup_shape", _spy):
            resp = await client.post(
                "/api/v1/metrics/demo_revenue/query",
                json={"dimensions": ["name"]},
                headers={"Authorization": "Bearer ignored"},
            )
        assert resp.status_code == 200, resp.text
        # The caller's own org sees its rollup as a candidate.
        assert captured.get("org_id") == org_id
        assert "audit14-demo-rollup" in captured.get("candidates", []), (
            f"own-org rollup must be a candidate, got {captured.get('candidates')!r}"
        )
        # A DIFFERENT org must see NO candidates for the same table/shape.
        other_org = str(uuid.uuid4())
        assert reg.candidates_for_table("demo", org_id=other_org) == [], (
            "org-scoped rollup must NEVER be a candidate for another org"
        )
    finally:
        app_.dependency_overrides.pop(verified_identity, None)
        # Clean up the rollup we injected into the process-global registry so we
        # do not perturb other tests sharing the singleton.
        try:
            reg._rollups.pop("audit14-demo-rollup", None)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# (f) [event-loop] compile_metric + planner_plan + connector.execute are
#     offloaded via asyncio.to_thread (CPU-bound work must not block the loop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_query_offloads_cpu_work_to_thread(metering_client):
    """compile_metric, planner_plan, and connector.execute must be called via
    asyncio.to_thread so CPU-bound sqlglot work does not block the event loop.

    Strategy: patch asyncio.to_thread to record calls, then assert each of the
    three expensive operations was scheduled through it.  The real to_thread is
    still invoked so the response succeeds end-to-end.
    """
    import asyncio as _asyncio

    from app.auth.deps import verified_identity

    client, user_id, org_id, _repo, app_ = metering_client
    writer = _writer_identity(user_id, org_id)
    app_.dependency_overrides[verified_identity] = lambda: writer

    recorded_fns: list[str] = []
    real_to_thread = _asyncio.to_thread

    async def _spy_to_thread(fn, *args, **kwargs):
        recorded_fns.append(getattr(fn, "__name__", repr(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    try:
        with patch("app.routes.metrics.asyncio.to_thread", side_effect=_spy_to_thread):
            resp = await client.post(
                "/api/v1/metrics/demo_revenue/query",
                json={"dimensions": ["name"]},
                headers={"Authorization": "Bearer ignored"},
            )
        assert resp.status_code == 200, resp.text
        # All three CPU-bound callables must appear in recorded_fns.
        assert "compile_metric" in recorded_fns, (
            f"compile_metric not offloaded; offloaded: {recorded_fns}"
        )
        assert "plan" in recorded_fns, (
            f"planner_plan not offloaded; offloaded: {recorded_fns}"
        )
        assert "execute" in recorded_fns, (
            f"connector.execute not offloaded; offloaded: {recorded_fns}"
        )
    finally:
        app_.dependency_overrides.pop(verified_identity, None)
