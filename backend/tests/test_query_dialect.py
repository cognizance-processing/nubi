"""BYO-connector dialect selection in POST /api/v1/query.

Goal
----
``POST /api/v1/query`` must generate SQL in the TARGET connector's sqlglot
dialect (like the flow query path already does) so that safe-cast forms
(``TRY_CAST`` / ``SAFE_CAST``) survive to the engine instead of being
downgraded to a plain ``CAST`` by a hardcoded ``postgres`` dialect.

What this suite verifies
------------------------
(1) Shared dialect map: ``dialect_for`` returns native dialects for BYO
    connector types and defaults to ``"postgres"`` for unknown/None.
(2) ``_resolve_target_dialect(None, ...)`` → ``"postgres"`` (no-datastore /
    demo / lake fallback keeps the historical default).
(3) Integration — BYO path: a datastore whose connector-type maps to the
    ``bigquery`` dialect plans ``TRY_CAST(...)`` as ``SAFE_CAST(...)`` (BigQuery
    safe-cast form) — proving the route wires the native dialect end-to-end.
(4) Integration — no-datastore path: the SAME query with NO datastore_id plans
    with the ``postgres`` dialect, so ``TRY_CAST`` is downgraded to ``CAST``
    (existing behaviour preserved).

Strategy
--------
- A ``_SpyConnector`` records the ``PhysicalPlan.sql`` / ``.dialect`` it is
  handed and returns a fixed Arrow table (no real engine needed).
- Registered under a fresh type ``spy_wh``; the dialect map is patched for that
  type via ``monkeypatch.setitem`` so it resolves to the ``bigquery`` dialect.
- For the no-datastore path, ``_get_demo_connector`` is patched to return the
  spy so we can inspect the SQL the demo path would execute.
"""

from __future__ import annotations

import uuid

import pyarrow as pa
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.connectors.base import Connector
from app.connectors.dialects import DEFAULT_DIALECT, dialect_for
from app.connectors.plan import PhysicalPlan
from app.connectors.registry import get_connector_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

SAFE_CAST_SQL = "SELECT TRY_CAST('7' AS INT) AS n"


# ---------------------------------------------------------------------------
# Spy connector: records the executed plan; returns a fixed Arrow table.
# ---------------------------------------------------------------------------

class _SpyConnector(Connector):
    """Records the last ``PhysicalPlan`` it executes for SQL/dialect assertions."""

    last_sql: str | None = None
    last_dialect: str | None = None

    def __init__(self, config: dict) -> None:  # noqa: ARG002
        pass

    def capabilities(self) -> dict[str, bool]:
        return {
            "native_arrow": True,
            "predicate_pushdown": False,
            "projection_pushdown": False,
            "partition_pushdown": False,
            "predicate_rls": True,
            "column_masking": False,
            "streaming_cdc": False,
        }

    def execute(self, plan: PhysicalPlan) -> "pa.Table":
        _SpyConnector.last_sql = plan.sql
        _SpyConnector.last_dialect = plan.dialect
        return pa.table({"n": pa.array([7], pa.int64())})

    def execute_stream(self, plan: PhysicalPlan):  # noqa: ARG002 pragma: no cover
        _SpyConnector.last_sql = plan.sql
        _SpyConnector.last_dialect = plan.dialect
        yield from pa.table({"n": pa.array([7], pa.int64())}).to_batches()


get_connector_registry().register("spy_wh", lambda config: _SpyConnector(config))


# ---------------------------------------------------------------------------
# Fixtures (mirror test_query_connectors.py)
# ---------------------------------------------------------------------------

def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def conn_app(app):
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, repo
    set_repo(None)


@pytest_asyncio.fixture
async def conn_client(conn_app, fake_db):
    app, repo = conn_app
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "tester@example.com",
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, org_id, repo


@pytest.fixture(autouse=True)
def _clear_cache():
    from app.connectors.cache import get_cache
    get_cache().clear()
    _SpyConnector.last_sql = None
    _SpyConnector.last_dialect = None
    yield
    get_cache().clear()


# ---------------------------------------------------------------------------
# (1) Shared dialect map
# ---------------------------------------------------------------------------

def test_dialect_for_maps_native_and_defaults_postgres():
    assert dialect_for("bigquery") == "bigquery"
    assert dialect_for("duckdb") == "duckdb"
    assert dialect_for("snowflake") == "snowflake"
    # Unknown / None fall back to the historical default.
    assert dialect_for("nonesuch") == DEFAULT_DIALECT == "postgres"
    assert dialect_for(None) == "postgres"


# ---------------------------------------------------------------------------
# (2) _resolve_target_dialect: no datastore → postgres (default preserved)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_target_dialect_no_datastore_is_postgres():
    from types import SimpleNamespace

    from app.routes.query import _resolve_target_dialect

    identity = SimpleNamespace(kind="access", org=None, user_id="u", datastore=None)
    assert await _resolve_target_dialect(None, identity) == "postgres"


# ---------------------------------------------------------------------------
# (3) BYO path: bigquery-dialect datastore preserves the safe cast (SAFE_CAST)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_byo_connector_uses_native_dialect_preserving_safe_cast(
    conn_client, monkeypatch
):
    client, user_id, org_id, repo = conn_client

    # Map the spy connector type to the BigQuery dialect for this test.
    import app.connectors.dialects as dialects_mod

    monkeypatch.setitem(dialects_mod.CONNECTOR_DIALECT, "spy_wh", "bigquery")

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Warehouse (bigquery dialect)",
        config={"type": "spy_wh"},
    )
    ds_id = ds["id"]

    resp = await client.post(
        "/api/v1/query",
        json={"sql": SAFE_CAST_SQL, "datastore_id": ds_id},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text

    # The route generated SQL in the connector's native (bigquery) dialect,
    # so the safe cast survived as SAFE_CAST rather than being downgraded.
    assert _SpyConnector.last_dialect == "bigquery"
    assert _SpyConnector.last_sql is not None
    assert "SAFE_CAST" in _SpyConnector.last_sql
    assert "TRY_CAST" not in _SpyConnector.last_sql


# ---------------------------------------------------------------------------
# (4) No-datastore path stays postgres: TRY_CAST is downgraded to CAST
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_datastore_path_stays_postgres_downgrading_try_cast(
    conn_client, monkeypatch
):
    client, user_id, org_id, repo = conn_client

    # Spy on the demo connector so we can inspect the SQL the fallback executes.
    import app.routes.query as query_mod

    monkeypatch.setattr(query_mod, "_get_demo_connector", lambda: _SpyConnector({}))

    resp = await client.post(
        "/api/v1/query",
        json={"sql": SAFE_CAST_SQL},  # no datastore_id → demo/postgres default
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text

    # Historical behaviour preserved: postgres dialect, safe cast downgraded.
    assert _SpyConnector.last_dialect == "postgres"
    assert _SpyConnector.last_sql is not None
    assert "CAST" in _SpyConnector.last_sql
    assert "SAFE_CAST" not in _SpyConnector.last_sql
    assert "TRY_CAST" not in _SpyConnector.last_sql
