"""Tests for M2-B: Arrow IPC streaming endpoint + cache TTL/stats.

Coverage
--------
1. Unit tests for ``ContentAddressedCache`` TTL and ``stats()``.
2. Unit tests for ``ipc_stream_from_bytes`` round-trip.
3. HTTP integration tests via the in-memory fake-DB app (conftest fixtures):
   - Two successive ``POST /api/v1/query`` calls: first returns MISS, second HIT.
   - Both responses parse as valid Arrow IPC with identical rows > 0.
   - ``GET /api/v1/_cache/stats`` returns ``hits >= 1`` after the HIT.
"""

from __future__ import annotations

import time
from io import BytesIO
from unittest.mock import patch

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio

# conftest.py sets environment variables before any app import.
# We deliberately import app modules INSIDE test functions / fixtures to let
# conftest run first.


# ===========================================================================
# 1. Unit tests — ContentAddressedCache TTL + stats
# ===========================================================================


class TestCacheTTL:
    """Per-entry TTL: expired entries are misses and are lazily evicted."""

    def _fresh_cache(self, ttl: float = 300.0):
        """Return a fresh (non-singleton) cache instance for isolation."""
        from app.connectors.cache import ContentAddressedCache
        return ContentAddressedCache(max_entries=8, ttl=ttl)

    def test_get_returns_value_before_expiry(self):
        c = self._fresh_cache(ttl=60.0)
        c.put("k1", b"hello")
        assert c.get("k1") == b"hello"

    def test_get_returns_none_after_expiry(self):
        c = self._fresh_cache(ttl=0.05)  # 50 ms TTL
        c.put("k1", b"hello")
        time.sleep(0.1)
        assert c.get("k1") is None

    def test_expired_entry_evicted_from_store(self):
        c = self._fresh_cache(ttl=0.05)
        c.put("k1", b"hello")
        assert c.size() == 1
        time.sleep(0.1)
        c.get("k1")          # triggers lazy eviction
        assert c.size() == 0

    def test_put_resets_ttl(self):
        c = self._fresh_cache(ttl=0.1)
        c.put("k1", b"v1")
        time.sleep(0.07)
        c.put("k1", b"v2")   # TTL reset
        time.sleep(0.07)     # total 0.14s but TTL reset at 0.07
        assert c.get("k1") == b"v2"

    def test_invalid_ttl_raises(self):
        from app.connectors.cache import ContentAddressedCache
        with pytest.raises(ValueError, match="ttl must be > 0"):
            ContentAddressedCache(ttl=0)

    def test_invalid_max_entries_raises(self):
        from app.connectors.cache import ContentAddressedCache
        with pytest.raises(ValueError, match="max_entries must be >= 1"):
            ContentAddressedCache(max_entries=0)


class TestCacheStats:
    """stats() returns correct hit/miss counters and hit_rate."""

    def _fresh_cache(self, ttl: float = 300.0):
        from app.connectors.cache import ContentAddressedCache
        return ContentAddressedCache(max_entries=8, ttl=ttl)

    def test_stats_initial(self):
        c = self._fresh_cache()
        s = c.stats()
        assert s == {"entries": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}

    def test_stats_after_miss(self):
        c = self._fresh_cache()
        c.get("nonexistent")
        s = c.stats()
        assert s["misses"] == 1
        assert s["hits"] == 0
        assert s["hit_rate"] == 0.0

    def test_stats_after_hit(self):
        c = self._fresh_cache()
        c.put("k1", b"data")
        c.get("k1")
        s = c.stats()
        assert s["hits"] == 1
        assert s["misses"] == 0
        assert s["hit_rate"] == 1.0

    def test_stats_hit_rate_mixed(self):
        c = self._fresh_cache()
        c.put("k1", b"data")
        c.get("k1")   # hit
        c.get("k2")   # miss
        c.get("k1")   # hit
        s = c.stats()
        assert s["hits"] == 2
        assert s["misses"] == 1
        assert abs(s["hit_rate"] - 2 / 3) < 1e-9

    def test_clear_resets_stats(self):
        c = self._fresh_cache()
        c.put("k1", b"data")
        c.get("k1")
        c.get("k99")
        c.clear()
        s = c.stats()
        assert s == {"entries": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}

    def test_expired_entry_counts_as_miss(self):
        c = self._fresh_cache(ttl=0.05)
        c.put("k1", b"data")
        time.sleep(0.1)
        c.get("k1")   # expired → miss
        s = c.stats()
        assert s["misses"] == 1
        assert s["hits"] == 0

    def test_entries_reflects_live_count(self):
        c = self._fresh_cache()
        c.put("k1", b"a")
        c.put("k2", b"b")
        assert c.stats()["entries"] == 2


# ===========================================================================
# 2. Unit tests — ipc_stream_from_bytes round-trip
# ===========================================================================


class TestIpcStreamFromBytes:
    """ipc_stream_from_bytes yields valid Arrow IPC data that round-trips."""

    def _make_ipc_bytes(self, table: pa.Table) -> bytes:
        from app.connectors.arrow_io import table_to_ipc_bytes
        return table_to_ipc_bytes(table)

    def test_round_trip_single_chunk(self):
        from app.connectors.arrow_io import ipc_stream_from_bytes
        t = pa.table({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        raw = self._make_ipc_bytes(t)
        chunks = list(ipc_stream_from_bytes(raw, chunk_size=len(raw) + 100))
        reassembled = b"".join(chunks)
        assert reassembled == raw

    def test_round_trip_multi_chunk(self):
        from app.connectors.arrow_io import ipc_stream_from_bytes
        t = pa.table({"x": list(range(100))})
        raw = self._make_ipc_bytes(t)
        chunks = list(ipc_stream_from_bytes(raw, chunk_size=64))
        assert len(chunks) > 1
        reassembled = b"".join(chunks)
        assert reassembled == raw

    def test_reassembled_parses_as_arrow(self):
        from app.connectors.arrow_io import ipc_stream_from_bytes
        t = pa.table({"id": pa.array([10, 20], type=pa.int32()), "val": [1.5, 2.5]})
        raw = self._make_ipc_bytes(t)
        reassembled = b"".join(ipc_stream_from_bytes(raw, chunk_size=32))
        reader = pa_ipc.open_stream(BytesIO(reassembled))
        result = reader.read_all()
        assert result.equals(t)

    def test_empty_bytes_yields_nothing(self):
        from app.connectors.arrow_io import ipc_stream_from_bytes
        assert list(ipc_stream_from_bytes(b"")) == []

    def test_all_chunks_non_empty(self):
        from app.connectors.arrow_io import ipc_stream_from_bytes
        t = pa.table({"n": list(range(50))})
        raw = self._make_ipc_bytes(t)
        for chunk in ipc_stream_from_bytes(raw, chunk_size=16):
            assert len(chunk) > 0


# ===========================================================================
# 3. HTTP integration tests — streaming endpoint + HIT/MISS headers + stats
# ===========================================================================


def _parse_arrow_ipc(content: bytes) -> pa.Table:
    """Parse Arrow IPC stream bytes into a pyarrow Table."""
    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all()


async def _get_auth_token(client) -> str:
    """Register a user and return an access token."""
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "stream_tester@example.com", "password": "password123", "name": "Streamer"},
    )
    assert resp.status_code == 201, f"Registration failed: {resp.text}"
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_query_first_call_is_miss(client):
    """First POST /query for a given SQL → X-Nubi-Cache: MISS."""
    # Reset cache singleton state for this test.
    from app.connectors.cache import get_cache
    get_cache().clear()

    token = await _get_auth_token(client)
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("x-nubi-cache") == "MISS"


@pytest.mark.asyncio
async def test_query_second_call_is_hit(client):
    """Second POST /query for same SQL → X-Nubi-Cache: HIT."""
    from app.connectors.cache import get_cache
    get_cache().clear()

    token = await _get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # First call — populates cache.
    resp1 = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=headers,
    )
    assert resp1.status_code == 200
    assert resp1.headers.get("x-nubi-cache") == "MISS"

    # Second call — should hit cache.
    resp2 = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=headers,
    )
    assert resp2.status_code == 200
    assert resp2.headers.get("x-nubi-cache") == "HIT"


@pytest.mark.asyncio
async def test_query_hit_and_miss_produce_identical_valid_arrow(client):
    """Both HIT and MISS responses parse as valid Arrow IPC with identical rows."""
    from app.connectors.cache import get_cache
    get_cache().clear()

    token = await _get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    resp_miss = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=headers,
    )
    assert resp_miss.status_code == 200
    table_miss = _parse_arrow_ipc(resp_miss.content)
    assert table_miss.num_rows > 0, "MISS response has no rows"

    resp_hit = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=headers,
    )
    assert resp_hit.status_code == 200
    table_hit = _parse_arrow_ipc(resp_hit.content)
    assert table_hit.num_rows > 0, "HIT response has no rows"

    assert table_miss.equals(table_hit), "MISS and HIT tables differ"


@pytest.mark.asyncio
async def test_cache_stats_hits_after_hit(client):
    """GET /_cache/stats → hits >= 1 after a cache HIT."""
    from app.connectors.cache import get_cache
    get_cache().clear()

    token = await _get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}

    # Two calls to produce at least one HIT.
    for _ in range(2):
        await client.post(
            "/api/v1/query",
            json={"sql": "SELECT * FROM demo"},
            headers=headers,
        )

    resp = await client.get("/api/v1/_cache/stats", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "hits" in body
    assert "misses" in body
    assert "hit_rate" in body
    assert "entries" in body
    assert body["hits"] >= 1
    assert body["hit_rate"] > 0.0


@pytest.mark.asyncio
async def test_cache_stats_requires_auth(client):
    """GET /_cache/stats without a token → 401."""
    resp = await client.get("/api/v1/_cache/stats")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_query_requires_auth(client):
    """POST /query without a token → 401."""
    resp = await client.post("/api/v1/query", json={"sql": "SELECT 1"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_content_type_is_arrow_stream(client):
    """Response Content-Type must be application/vnd.apache.arrow.stream."""
    from app.connectors.cache import get_cache
    get_cache().clear()

    token = await _get_auth_token(client)
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "application/vnd.apache.arrow.stream" in resp.headers.get("content-type", "")
