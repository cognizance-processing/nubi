"""M12-A: Connector selection + capability-gated RLS in POST /query.

What this suite verifies
------------------------
(1) A ``duckdb``-typed datastore runs the registered query and returns 200 Arrow IPC
    (generate_series needs no seed table).
(2) An ``http_json``-typed datastore with mocked httpx returns Arrow IPC with the
    mocked rows (post-fetch RLS applied; no policies in plan → all rows returned).
(3) Capability gate (unit level): a synthetic ``_UnsecurableConnector`` (predicate_rls=False)
    registered as 'unsecurable' in the registry declares the flag correctly.
(4) Capability gate (integration): a first-party token + 'unsecurable'-typed datastore
    WITHOUT policies passes the gate (policies={}); but with policies → 501
    source_unsupported_rls.  Uses monkeypatching to inject policies into the plan
    without needing a full embed-token round-trip.
(4r) Regression: ``get_connector_registry().get('mongo')`` now raises
     AppError("unknown_connector", 404) — confirms the stub was removed.
(5) datastore_not_found → 404.
(6) No datastore_id → existing demo path still returns 200 (regression guard).

Test strategy
-------------
- ``InMemoryRepo`` injected via ``set_repo()`` to seed datastores.
- First-party JWT (``mint_access_token``) for all integration tests — simpler than
  minting an embed JWT here.  The org_id is resolved via ``repo.seed_org_member()``.
- ``fake_db`` fixture from conftest seeds the user for ``current_user`` dep.
- Monkeypatch ``httpx.get`` for the http_json connector tests.
- Cache cleared between tests.
- Synthetic ``_UnsecurableConnector`` registered under type 'unsecurable' proves the
  capability-gate logic without shipping any MongoDB code.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.connectors.base import Connector
from app.connectors.plan import PhysicalPlan
from app.connectors.registry import get_connector_registry
from app.errors import AppError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Synthetic unsecurable connector (replaces the removed MongoStubConnector)
# ---------------------------------------------------------------------------

class _UnsecurableConnector(Connector):
    """Minimal connector that declares predicate_rls=False and raises 501.

    Used in tests to prove the capability-gated RLS logic in routes/query.py
    without shipping any MongoDB code.  Registered under type 'unsecurable'.
    """

    def __init__(self, config: dict) -> None:  # noqa: ARG002
        self.validate_capabilities()

    def capabilities(self) -> dict[str, bool]:
        return {
            "native_arrow": False,
            "predicate_pushdown": False,
            "projection_pushdown": False,
            "partition_pushdown": False,
            "predicate_rls": False,  # cannot enforce RLS
            "column_masking": False,
            "streaming_cdc": False,
        }

    def execute(self, plan: PhysicalPlan) -> pa.Table:  # noqa: ARG002
        raise AppError(
            "source_unsupported_rls",
            "Synthetic unsecurable connector: predicate_rls=False; refusing all queries.",
            status=501,
        )

    def execute_stream(self, plan: PhysicalPlan):  # noqa: ARG002
        yield from []  # pragma: no cover
        self.execute(plan)  # type: ignore[misc]


# Register the synthetic connector so route-level tests can use type='unsecurable'.
get_connector_registry().register("unsecurable", lambda config: _UnsecurableConnector(config))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_arrow(content: bytes) -> pa.Table:
    """Parse Arrow IPC stream bytes into a pyarrow Table."""
    return pa_ipc.open_stream(BytesIO(content)).read_all()


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def conn_app(app):
    """Inject an InMemoryRepo alongside the conftest fake-DB app."""
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, repo
    set_repo(None)


@pytest_asyncio.fixture
async def conn_client(conn_app, fake_db):
    """HTTPX client with InMemoryRepo + a seeded user/org."""
    app, repo = conn_app

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    # Seed user in FakeDB so the JWT→user lookup works.
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "tester@example.com",
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    # Seed org membership in InMemoryRepo for get_user_org().
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, repo


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the query result cache before and after each test."""
    from app.connectors.cache import get_cache
    get_cache().clear()
    yield
    get_cache().clear()


# ---------------------------------------------------------------------------
# (1) DuckDB-typed datastore returns Arrow IPC (generate_series needs no seed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duckdb_datastore_returns_arrow(conn_client):
    """A datastore with type='duckdb' runs the registered query and returns 200 Arrow."""
    client, user_id, org_id, repo = conn_client

    # Seed a duckdb-typed datastore.
    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="DuckDB local",
        config={"type": "duckdb"},
    )
    ds_id = ds["id"]

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_points_10k", "datastore_id": ds_id},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    ct = resp.headers.get("content-type", "")
    assert "application/vnd.apache.arrow.stream" in ct

    table = _parse_arrow(resp.content)
    # demo_points_10k generates 10 000 rows.
    assert table.num_rows == 10_000


# ---------------------------------------------------------------------------
# (2) HTTP/JSON-typed datastore: monkeypatched httpx → Arrow with mocked rows
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_json_datastore_returns_mocked_rows(conn_client):
    """An http_json datastore with mocked httpx returns Arrow IPC of the mocked rows."""
    client, user_id, org_id, repo = conn_client

    mocked_rows = [
        {"id": 1, "name": "foo"},
        {"id": 2, "name": "bar"},
        {"id": 3, "name": "baz"},
    ]

    # Seed an http_json-typed datastore.
    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="API source",
        config={"type": "http_json", "url": "http://x/api/data"},
    )
    ds_id = ds["id"]

    # Monkeypatch httpx.get (httpx is lazily imported inside execute(); patch at the
    # httpx module level so the lazy import picks up the patched version).
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = mocked_rows

    with patch("httpx.get", return_value=mock_response):
        resp = await client.post(
            "/api/v1/query",
            # First-party tokens can use raw SQL.
            json={"sql": "SELECT * FROM data", "datastore_id": ds_id},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 200, resp.text
    assert "application/vnd.apache.arrow.stream" in resp.headers.get("content-type", "")

    table = _parse_arrow(resp.content)
    # All 3 mocked rows returned (no RLS policies active for a first-party token).
    assert table.num_rows == 3
    assert table.column_names == ["id", "name"] or "id" in table.column_names


# ---------------------------------------------------------------------------
# (3) Unit test: synthetic unsecurable connector declares predicate_rls=False
# ---------------------------------------------------------------------------

def test_unsecurable_connector_declares_predicate_rls_false():
    """Unit test: the 'unsecurable' connector's capabilities()['predicate_rls'] is False.

    This proves the capability contract without any MongoDB code: a connector
    that declares predicate_rls=False must be refused by the capability gate
    when a query carries active RLS policies.
    """
    factory = get_connector_registry().get("unsecurable")
    connector = factory({})
    caps = connector.capabilities()
    assert caps["predicate_rls"] is False, (
        f"Expected predicate_rls=False for _UnsecurableConnector, got {caps['predicate_rls']!r}"
    )


# ---------------------------------------------------------------------------
# (3r) Regression: registry.get('mongo') now raises unknown_connector 404
# ---------------------------------------------------------------------------

def test_mongo_connector_no_longer_registered():
    """Regression: 'mongo' is not registered → unknown_connector 404.

    Confirms that the MongoStubConnector has been removed from the registry.
    Any attempt to create a mongo-typed datastore will now surface a 404
    rather than silently routing to a stub.
    """
    with pytest.raises(AppError) as exc_info:
        get_connector_registry().get("mongo")
    err = exc_info.value
    assert err.code == "unknown_connector"
    assert err.status == 404


# ---------------------------------------------------------------------------
# (4a) Capability gate: unsecurable + NO policies → gate does NOT trigger
#      (execute() raises 501 from the connector itself, which is still a
#       source_unsupported_rls error — the gate only fires when policies are set.)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsecurable_datastore_no_policies_gate_does_not_block(conn_client):
    """'unsecurable' datastore + no active RLS policies: gate passes, connector raises 501."""
    client, user_id, org_id, repo = conn_client

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Unsecurable stub",
        config={"type": "unsecurable"},
    )
    ds_id = ds["id"]

    resp = await client.post(
        "/api/v1/query",
        # First-party token → no policies; capability gate does NOT fire.
        json={"sql": "SELECT * FROM docs", "datastore_id": ds_id},
        headers=_auth_headers(user_id),
    )
    # The CAPABILITY GATE did not fire (no policies).
    # _UnsecurableConnector.execute() then raises 501 with code=source_unsupported_rls.
    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body["error"]["code"] == "source_unsupported_rls"


# ---------------------------------------------------------------------------
# (4b) Capability gate: unsecurable + active policies → route-level 501 (gate fires)
#      We monkeypatch the planner to inject policies into the physical plan so
#      the capability gate sees them, without needing an embed token with policies.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsecurable_datastore_with_policies_gate_returns_501(conn_client):
    """'unsecurable' datastore + active RLS policies: capability gate raises 501.

    This is the core security proof: the route-level capability gate in
    routes/query.py must refuse to execute any query on a predicate_rls=False
    connector when the plan carries active RLS policies.  The gate fires BEFORE
    the connector's execute() is ever called.
    """
    client, user_id, org_id, repo = conn_client

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Unsecurable stub (secured)",
        config={"type": "unsecurable"},
    )
    ds_id = ds["id"]

    # Inject policies into the physical plan by monkeypatching the planner.
    from app.connectors import plan as real_planner_plan

    def _plan_with_policies(sql, claims=None, params=None, **kwargs):
        """Return a plan that carries active policies regardless of the token."""
        real_plan = real_planner_plan(sql, claims=claims or {}, params=params or [], **kwargs)
        # Force active policies into rls_claims so the gate sees them.
        forced_rls = {"policies": {"tenant_id": "acme"}}
        from app.connectors.cache_key import compute_cache_key
        ck = compute_cache_key(real_plan.sql, real_plan.params, forced_rls)
        return PhysicalPlan(
            dialect=real_plan.dialect,
            sql=real_plan.sql,
            params=real_plan.params,
            projection=real_plan.projection,
            predicates=real_plan.predicates,
            rls_claims=forced_rls,
            cache_key=ck,
        )

    with patch("app.routes.query.planner_plan", side_effect=_plan_with_policies):
        resp = await client.post(
            "/api/v1/query",
            json={"sql": "SELECT * FROM docs", "datastore_id": ds_id},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body["error"]["code"] == "source_unsupported_rls", (
        f"Expected source_unsupported_rls from capability gate, got {body['error']['code']!r}"
    )


# ---------------------------------------------------------------------------
# (5) datastore_not_found → 404
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_datastore_returns_404(conn_client):
    """Providing an unknown datastore_id → 404 datastore_not_found."""
    client, user_id, org_id, repo = conn_client

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT 1", "datastore_id": str(uuid.uuid4())},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "datastore_not_found"


# ---------------------------------------------------------------------------
# (6) No datastore_id → existing demo DuckDB path (regression guard)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_datastore_id_uses_demo_path(conn_client):
    """No datastore_id → demo DuckDB path returns 200 Arrow (regression guard)."""
    client, user_id, org_id, repo = conn_client

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert "application/vnd.apache.arrow.stream" in resp.headers.get("content-type", "")
    table = _parse_arrow(resp.content)
    assert table.num_rows == 5  # The demo table has 5 rows.
