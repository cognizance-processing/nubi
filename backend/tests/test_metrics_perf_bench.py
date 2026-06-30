"""Performance benchmark — POST /metrics/{id}/query against a large fact table.

Proves Bet 6A (interactive-latency large aggregations) empirically: build a
bounded-but-large (~1.5M row) store x SKU x day fact table, register a
governed metric over it, and run a grouped aggregation through the SAME
``execute_metric_query`` path real traffic uses (compile → plan → rollup
routing → cache → connector.execute → Arrow IPC). We assert:

1. A cold (cache-MISS) grouped aggregation over ~1.5M rows completes within a
   generous interactive-latency bound.
2. An identical repeat query is served from cache (``X-Nubi-Cache: HIT``) and
   is dramatically faster — proving the cache path engages on repeat, exactly
   as the rollup/cache layer is designed to behave for dashboard-style reuse.

Gating
------
This test is gated behind ``RUN_BENCH=1`` (or ``-m slow``) so normal CI runs
skip it (it allocates a multi-million-row table and is not meant to run on
every commit). To run it on demand::

    RUN_BENCH=1 pytest tests/test_metrics_perf_bench.py -q -s

The ``-s`` flag is required to see the printed timing line (pytest captures
stdout by default).
"""

from __future__ import annotations

import os
import time
import uuid
from io import BytesIO

import numpy as np
import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

_RUN_BENCH = os.getenv("RUN_BENCH", "").strip().lower() in ("1", "true", "yes", "on")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.slow,
    pytest.mark.skipif(
        not _RUN_BENCH,
        reason="Perf benchmark is gated behind RUN_BENCH=1 (slow; skipped in normal CI).",
    ),
]

# ---------------------------------------------------------------------------
# Fact table shape: store x SKU x day, ~1.5M rows — bounded but large enough
# to exercise a real grouped aggregation, sized to run in a few seconds in CI.
# ---------------------------------------------------------------------------

N_ROWS = 1_500_000
N_STORES = 200
N_SKUS = 500
N_DAYS = 365

# Generous interactive-latency bound for a COLD (uncached) grouped aggregation
# over ~1.5M rows. CI hardware varies; this is intentionally loose — the point
# is to catch a regression to "minutes", not to enforce a tight SLA.
COLD_QUERY_BUDGET_MS = 15_000


def _build_fact_table() -> pa.Table:
    """Build an in-memory ~1.5M-row store x SKU x day fact table.

    Uses numpy vectorised generation (no per-row Python loop) so building the
    table itself takes a small fraction of a second and does not dominate the
    benchmark's wall-clock time.
    """
    rng = np.random.default_rng(42)
    store_id = rng.integers(1, N_STORES + 1, size=N_ROWS, dtype=np.int32)
    sku_id = rng.integers(1, N_SKUS + 1, size=N_ROWS, dtype=np.int32)
    day_offset = rng.integers(0, N_DAYS, size=N_ROWS, dtype=np.int32)
    units = rng.integers(1, 50, size=N_ROWS, dtype=np.int32)
    price = rng.uniform(1.0, 100.0, size=N_ROWS).astype(np.float64)
    revenue = units.astype(np.float64) * price
    return pa.table(
        {
            "store_id": pa.array(store_id, type=pa.int32()),
            "sku_id": pa.array(sku_id, type=pa.int32()),
            "day_offset": pa.array(day_offset, type=pa.int32()),
            "units": pa.array(units, type=pa.int32()),
            "revenue": pa.array(revenue, type=pa.float64()),
        }
    )


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _parse_arrow(content: bytes):
    return pa_ipc.open_stream(BytesIO(content)).read_all()


@pytest_asyncio.fixture
async def bench_client(app, fake_db):
    """HTTPX client with a seeded user for the perf-bench test."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "bench_tester@example.com",
        "name": "Bench Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id


async def test_metrics_query_perf_bench_large_fact(bench_client) -> None:
    """Grouped /metrics/{id}/query aggregation over ~1.5M rows stays fast.

    The fact table is registered directly into the built-in demo DuckDB
    connector (the same one ``base_table='demo'`` metrics already use) so the
    request goes through the REAL execute_metric_query path — compile, plan,
    rollup routing, cache, connector.execute, Arrow serialisation — with no
    datastore/network mocking required.
    """
    from app.routes.query import _get_demo_connector

    table = _build_fact_table()
    _get_demo_connector().register({"bench_fact": table})

    client, user_id = bench_client
    headers = _auth_headers(user_id)

    metric_def = {
        "name": f"Bench Revenue {uuid.uuid4().hex[:8]}",
        "measure": {"name": "revenue", "agg": "sum", "expr": "revenue"},
        "base_table": "bench_fact",
        "dimensions": [
            {"name": "store_id", "type": "number"},
            {"name": "sku_id", "type": "number"},
        ],
    }
    create = await client.post("/api/v1/metrics", json=metric_def, headers=headers)
    assert create.status_code == 201, create.text
    metric_id = create.json()["id"]

    body = {"dimensions": ["store_id", "sku_id"]}

    # ── Cold run: cache MISS, full scan + group-by over ~1.5M rows ───────────
    t0 = time.perf_counter()
    resp1 = await client.post(
        f"/api/v1/metrics/{metric_id}/query", json=body, headers=headers
    )
    elapsed_cold_ms = (time.perf_counter() - t0) * 1000
    assert resp1.status_code == 200, resp1.text
    assert resp1.headers.get("X-Nubi-Cache") == "MISS"

    table_out = _parse_arrow(resp1.content)
    assert table_out.num_rows > 0
    assert "revenue" in table_out.schema.names
    # store_id x sku_id grouping can produce at most N_STORES * N_SKUS rows.
    assert table_out.num_rows <= N_STORES * N_SKUS

    # ── Repeat run: identical request must be served from cache ─────────────
    t1 = time.perf_counter()
    resp2 = await client.post(
        f"/api/v1/metrics/{metric_id}/query", json=body, headers=headers
    )
    elapsed_cached_ms = (time.perf_counter() - t1) * 1000
    assert resp2.status_code == 200, resp2.text
    assert resp2.headers.get("X-Nubi-Cache") == "HIT"
    assert resp2.content == resp1.content

    print(
        f"\n[perf-bench] {N_ROWS:,} rows, group-by(store_id, sku_id) -> "
        f"{table_out.num_rows:,} groups | "
        f"cold(MISS)={elapsed_cold_ms:.1f}ms cached(HIT)={elapsed_cached_ms:.1f}ms"
    )

    # Bet 6A: a cold grouped aggregation over ~1.5M rows must stay within
    # interactive latency, not degrade to minutes.
    assert elapsed_cold_ms < COLD_QUERY_BUDGET_MS, (
        f"cold group-by over {N_ROWS:,} rows took {elapsed_cold_ms:.0f}ms "
        f"(budget {COLD_QUERY_BUDGET_MS}ms)"
    )
    # The cached repeat must be faster than the cold run — proves the
    # rollup/cache path actually engages on repeat rather than re-scanning.
    assert elapsed_cached_ms < elapsed_cold_ms
