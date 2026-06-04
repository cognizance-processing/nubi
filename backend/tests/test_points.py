"""M5-B: Synthetic point-cloud registered queries — unit + endpoint tests.

Coverage
--------
Unit (registry + DuckDB):
  1. get_query_registry().get('demo_points_10k') returns a RegisteredQuery.
  2. Execute demo_points_10k SQL via DuckDBConnector + planner.plan → Arrow
     table with exactly 10 000 rows and columns [id, x, y, category].

Endpoint (POST /api/v1/query):
  3. First-party access token + {query_id: 'demo_points_10k'} → 200 Arrow IPC;
     parsed table has exactly 10 000 rows.

All three point-cloud query ids are also spot-checked for registration.
"""

from __future__ import annotations

import os
from io import BytesIO

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE any app import.
# conftest.py already sets these; the setdefault guards make this file runnable
# standalone (e.g. python -m pytest tests/test_points.py).
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback"
)
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")


# ---------------------------------------------------------------------------
# Arrow helper
# ---------------------------------------------------------------------------


def _parse_arrow(content: bytes) -> pa.Table:
    """Parse Arrow IPC stream bytes into a pyarrow Table."""
    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all()


# ===========================================================================
# 1. Registry unit tests — all three point-cloud queries are registered
# ===========================================================================


class TestPointCloudRegistration:
    """All three demo_points_* ids must be present in the registry."""

    def _registry(self):
        # Always fetch a fresh reference (singleton); no teardown needed here.
        from app.queries.registry import get_query_registry
        return get_query_registry()

    def test_demo_points_10k_registered(self):
        """demo_points_10k is present in the registry."""
        rq = self._registry().get("demo_points_10k")
        assert rq is not None, "demo_points_10k is not registered"
        assert rq.id == "demo_points_10k"
        assert "10000" in rq.sql or "10,000" in rq.name
        assert rq.required_scope is None

    def test_demo_points_100k_registered(self):
        """demo_points_100k is present in the registry."""
        rq = self._registry().get("demo_points_100k")
        assert rq is not None, "demo_points_100k is not registered"
        assert rq.id == "demo_points_100k"
        assert "100000" in rq.sql

    def test_demo_points_500k_registered(self):
        """demo_points_500k is present in the registry."""
        rq = self._registry().get("demo_points_500k")
        assert rq is not None, "demo_points_500k is not registered"
        assert rq.id == "demo_points_500k"
        assert "500000" in rq.sql

    def test_all_point_queries_have_expected_columns_in_sql(self):
        """All point-cloud sqls mention x, y, category."""
        reg = self._registry()
        for qid in ("demo_points_10k", "demo_points_100k", "demo_points_500k"):
            rq = reg.get(qid)
            assert rq is not None
            assert " x," in rq.sql or " x " in rq.sql or " AS x" in rq.sql
            assert " y," in rq.sql or " y " in rq.sql or " AS y" in rq.sql
            assert "category" in rq.sql

    def test_existing_demo_queries_still_registered(self):
        """Existing demo_all / demo_active are not broken by M5-B."""
        reg = self._registry()
        assert reg.get("demo_all") is not None
        assert reg.get("demo_active") is not None


# ===========================================================================
# 2. DuckDB execution unit test — 10k rows, correct schema
# ===========================================================================


class TestPointCloudExecution:
    """Execute demo_points_10k via DuckDBConnector + planner and validate output."""

    def test_10k_rows_and_schema(self):
        """demo_points_10k produces 10 000 rows with columns [id, x, y, category]."""
        from app.connectors.duckdb_conn import DuckDBConnector
        from app.connectors import plan as planner_plan
        from app.queries.registry import get_query_registry

        rq = get_query_registry().get("demo_points_10k")
        assert rq is not None, "demo_points_10k not found in registry"

        physical_plan = planner_plan(sql=rq.sql, claims={"policies": {}}, params=[])
        conn = DuckDBConnector()
        table = conn.execute(physical_plan)

        assert table.num_rows == 10_000, (
            f"Expected 10 000 rows from demo_points_10k, got {table.num_rows}"
        )
        assert set(table.column_names) == {"id", "x", "y", "category"}, (
            f"Unexpected columns: {table.column_names}"
        )
        # x and y must be floating-point; category must be integral
        schema = table.schema
        assert pa.types.is_floating(schema.field("x").type), (
            f"x column type should be floating, got {schema.field('x').type}"
        )
        assert pa.types.is_floating(schema.field("y").type), (
            f"y column type should be floating, got {schema.field('y').type}"
        )
        assert pa.types.is_integer(schema.field("category").type), (
            f"category column type should be integer, got {schema.field('category').type}"
        )
        # category values must be in range 0..4
        categories = table.column("category").to_pylist()
        assert set(categories) == {0, 1, 2, 3, 4}, (
            f"Expected category values {{0,1,2,3,4}}, got {set(categories)}"
        )

    def test_100k_row_count(self):
        """demo_points_100k produces exactly 100 000 rows."""
        from app.connectors.duckdb_conn import DuckDBConnector
        from app.connectors import plan as planner_plan
        from app.queries.registry import get_query_registry

        rq = get_query_registry().get("demo_points_100k")
        physical_plan = planner_plan(sql=rq.sql, claims={"policies": {}}, params=[])
        conn = DuckDBConnector()
        table = conn.execute(physical_plan)
        assert table.num_rows == 100_000, (
            f"Expected 100 000 rows from demo_points_100k, got {table.num_rows}"
        )

    def test_500k_row_count(self):
        """demo_points_500k produces exactly 500 000 rows."""
        from app.connectors.duckdb_conn import DuckDBConnector
        from app.connectors import plan as planner_plan
        from app.queries.registry import get_query_registry

        rq = get_query_registry().get("demo_points_500k")
        physical_plan = planner_plan(sql=rq.sql, claims={"policies": {}}, params=[])
        conn = DuckDBConnector()
        table = conn.execute(physical_plan)
        assert table.num_rows == 500_000, (
            f"Expected 500 000 rows from demo_points_500k, got {table.num_rows}"
        )


# ===========================================================================
# 3. HTTP endpoint test — first-party token + query_id → 200 Arrow IPC
# ===========================================================================


@pytest.fixture(autouse=True)
def _clear_query_cache():
    """Clear the query cache before and after each endpoint test."""
    from app.connectors.cache import get_cache
    get_cache().clear()
    yield
    get_cache().clear()


@pytest.mark.asyncio
async def test_endpoint_demo_points_10k_returns_10000_rows(client):
    """POST /api/v1/query with first-party token + query_id='demo_points_10k' → 200 Arrow IPC, 10 000 rows.

    Uses the same mint_access_token pattern as test_embed_allowlist.py (first-party path).
    The DuckDB demo connector runs the registered generate_series SQL natively;
    no extra seed table is required.
    """
    from app.auth.jwt import mint_access_token

    token = mint_access_token("points-test-user-1")
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_points_10k"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert "application/vnd.apache.arrow.stream" in resp.headers.get("content-type", ""), (
        f"Expected Arrow IPC content-type, got: {resp.headers.get('content-type')}"
    )

    table = _parse_arrow(resp.content)
    assert table.num_rows == 10_000, (
        f"Expected 10 000 rows in Arrow IPC response, got {table.num_rows}"
    )
    assert set(table.column_names) == {"id", "x", "y", "category"}, (
        f"Unexpected columns in response: {table.column_names}"
    )


@pytest.mark.asyncio
async def test_endpoint_demo_points_100k_returns_arrow_ipc(client):
    """POST /api/v1/query with query_id='demo_points_100k' → 200 Arrow IPC, 100 000 rows.

    Confirms the 100k point cloud streams correctly through the full pipeline.
    """
    from app.auth.jwt import mint_access_token

    token = mint_access_token("points-test-user-2")
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_points_100k"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    table = _parse_arrow(resp.content)
    assert table.num_rows == 100_000, (
        f"Expected 100 000 rows from demo_points_100k endpoint, got {table.num_rows}"
    )


@pytest.mark.asyncio
async def test_endpoint_demo_points_500k_streams_as_arrow(client):
    """POST /api/v1/query with query_id='demo_points_500k' → 200 Arrow IPC, 500 000 rows.

    Confirms the 500k point cloud streams through the Arrow IPC pipeline.
    """
    from app.auth.jwt import mint_access_token

    token = mint_access_token("points-test-user-3")
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_points_500k"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    table = _parse_arrow(resp.content)
    assert table.num_rows == 500_000, (
        f"Expected 500 000 rows from demo_points_500k endpoint, got {table.num_rows}"
    )
