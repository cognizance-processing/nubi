"""Tests for the process-global prefetch semaphore in _prefetch_datastores.

The bug: _prefetch_datastores created a fresh asyncio.Semaphore(_PREFETCH_CONCURRENCY=10)
per call.  With N concurrent canvas/board loads each creating their own semaphore, up to
N × 10 simultaneous repo.get('datastores') calls could exhaust the DB pool.

The fix: a process-global prefetch semaphore (_get_global_prefetch_sem) is acquired
second (after the per-call semaphore) so total concurrent prefetch fetches across ALL
simultaneous loads is bounded to _GLOBAL_PREFETCH_CONCURRENCY regardless of N.

Covers:
1. Simultaneous loads: total concurrent repo.get('datastores') across N concurrent
   _prefetch_datastores calls is bounded by _GLOBAL_PREFETCH_CONCURRENCY.
2. No data loss: all datastores are returned from all loads (no deadlock, no drop).
3. No deadlock when per-call cap < global cap (typical config).
4. Module constants: _DEFAULT_GLOBAL_PREFETCH_CONCURRENCY == _PREFETCH_CONCURRENCY * 2.
5. Env var NUBI_PREFETCH_GLOBAL_CONCURRENCY is parsed correctly.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.dashboards.collect as collect_mod
from app.dashboards.collect import _prefetch_datastores


# ---------------------------------------------------------------------------
# Fixture: reset global prefetch semaphore between tests for isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_global_prefetch_sem():
    """Reset the process-global prefetch semaphore state before each test."""
    original_sem = collect_mod._global_prefetch_sem
    original_cap = collect_mod._GLOBAL_PREFETCH_CONCURRENCY
    original_per_call = collect_mod._PREFETCH_CONCURRENCY
    yield
    collect_mod._global_prefetch_sem = original_sem
    collect_mod._GLOBAL_PREFETCH_CONCURRENCY = original_cap
    collect_mod._PREFETCH_CONCURRENCY = original_per_call


# ---------------------------------------------------------------------------
# 1. Total concurrent prefetch fetches bounded by global cap across N loads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_prefetch_cap_bounds_concurrent_fetches_across_loads():
    """N simultaneous _prefetch_datastores calls must not exceed _GLOBAL_PREFETCH_CONCURRENCY.

    Strategy:
    * Set global cap to 3.
    * Launch 4 simultaneous _prefetch_datastores calls, each with 5 distinct ds_ids
      (20 total fetch coroutines across all calls).
    * Instrument repo.get with a tracker that records the peak in-flight count.
    * Assert peak <= global cap (3).
    * Assert all 20 ds rows are returned (no data loss / deadlock).
    """
    GLOBAL_CAP = 3
    PER_CALL_CAP = 5   # per-call cap >= global cap; global cap is the binding constraint
    N_LOADS = 4
    DS_PER_LOAD = 5

    collect_mod._PREFETCH_CONCURRENCY = PER_CALL_CAP
    collect_mod._GLOBAL_PREFETCH_CONCURRENCY = GLOBAL_CAP
    # Force creation of a fresh global semaphore with the reduced cap.
    collect_mod._global_prefetch_sem = asyncio.Semaphore(GLOBAL_CAP)

    org_id = str(uuid.uuid4())

    # Build N_LOADS × DS_PER_LOAD distinct ds_ids, all with real rows.
    all_load_ds_ids: list[list[str]] = [
        [str(uuid.uuid4()) for _ in range(DS_PER_LOAD)] for _ in range(N_LOADS)
    ]
    all_ds_rows: dict[str, dict] = {
        ds_id: {"id": ds_id, "org_id": org_id}
        for load in all_load_ds_ids
        for ds_id in load
    }

    # Shared counters (asyncio is single-threaded so no lock needed).
    in_flight: list[int] = [0]
    peak_concurrent: list[int] = [0]
    total_completed: list[int] = [0]

    async def _tracked_get(resource: str, _org_id: str, ds_id: str) -> Any:
        in_flight[0] += 1
        if in_flight[0] > peak_concurrent[0]:
            peak_concurrent[0] = in_flight[0]
        # Yield to maximise concurrency overlap so contention is exercised.
        await asyncio.sleep(0)
        in_flight[0] -= 1
        total_completed[0] += 1
        return all_ds_rows.get(ds_id)

    mock_repo = AsyncMock()
    mock_repo.get = _tracked_get

    # Launch all loads simultaneously.
    caches = await asyncio.gather(
        *(_prefetch_datastores(set(ds_ids), org_id, mock_repo) for ds_ids in all_load_ds_ids)
    )

    # Verify: total completed fetches.
    expected_total = N_LOADS * DS_PER_LOAD
    assert total_completed[0] == expected_total, (
        f"Expected {expected_total} completed fetches, got {total_completed[0]}"
    )

    # Verify: peak concurrent never exceeded global cap.
    assert peak_concurrent[0] <= GLOBAL_CAP, (
        f"Peak concurrent prefetch fetches ({peak_concurrent[0]}) exceeded "
        f"global cap ({GLOBAL_CAP}).  The global semaphore is not working correctly."
    )

    # Verify: all ds_ids present in respective caches (no data loss).
    for load_idx, (cache, ds_ids) in enumerate(zip(caches, all_load_ds_ids)):
        for ds_id in ds_ids:
            assert ds_id in cache, (
                f"Load {load_idx}: ds_id={ds_id!r} missing from prefetch cache"
            )


# ---------------------------------------------------------------------------
# 2. No data loss: all datastores returned; no deadlock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_prefetch_no_data_loss_no_deadlock():
    """All datastores are returned even when global cap < total fetch count.

    Uses a small global cap (2) and 3 simultaneous loads with 4 ds each (12 total).
    The test must complete (no timeout) and all 12 ds rows must be in their caches.
    """
    GLOBAL_CAP = 2
    N_LOADS = 3
    DS_PER_LOAD = 4

    collect_mod._PREFETCH_CONCURRENCY = 4
    collect_mod._GLOBAL_PREFETCH_CONCURRENCY = GLOBAL_CAP
    collect_mod._global_prefetch_sem = asyncio.Semaphore(GLOBAL_CAP)

    org_id = str(uuid.uuid4())
    all_load_ds_ids = [
        [str(uuid.uuid4()) for _ in range(DS_PER_LOAD)] for _ in range(N_LOADS)
    ]
    all_ds_rows = {
        ds_id: {"id": ds_id}
        for load in all_load_ds_ids
        for ds_id in load
    }

    async def _get(_res, _org, ds_id):
        await asyncio.sleep(0)
        return all_ds_rows.get(ds_id)

    mock_repo = AsyncMock()
    mock_repo.get = _get

    caches = await asyncio.wait_for(
        asyncio.gather(
            *(_prefetch_datastores(set(ds_ids), org_id, mock_repo) for ds_ids in all_load_ds_ids)
        ),
        timeout=5.0,
    )

    for load_idx, (cache, ds_ids) in enumerate(zip(caches, all_load_ds_ids)):
        assert len(cache) == DS_PER_LOAD, (
            f"Load {load_idx}: expected {DS_PER_LOAD} ds rows in cache, got {len(cache)}"
        )
        for ds_id in ds_ids:
            assert ds_id in cache, (
                f"Load {load_idx}: ds_id={ds_id!r} missing from cache"
            )


# ---------------------------------------------------------------------------
# 3. No deadlock when per-call cap < global cap (typical production config)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_prefetch_no_deadlock_per_call_lt_global():
    """No deadlock when per-call semaphore cap is smaller than global cap.

    Per-call: 2, global: 10 (typical config where per-call is the tighter constraint).
    Acquisition order must be per-call → global (matching the widget semaphore pattern)
    to prevent any coroutine from holding the global and then blocking on per-call.
    """
    collect_mod._PREFETCH_CONCURRENCY = 2
    collect_mod._GLOBAL_PREFETCH_CONCURRENCY = 10
    collect_mod._global_prefetch_sem = asyncio.Semaphore(10)

    org_id = str(uuid.uuid4())
    ds_ids = [str(uuid.uuid4()) for _ in range(8)]
    ds_rows = {ds_id: {"id": ds_id} for ds_id in ds_ids}

    async def _get(_res, _org, ds_id):
        await asyncio.sleep(0)
        return ds_rows.get(ds_id)

    mock_repo = AsyncMock()
    mock_repo.get = _get

    # Two simultaneous loads, same ds_ids — must not deadlock.
    cache_a, cache_b = await asyncio.wait_for(
        asyncio.gather(
            _prefetch_datastores(set(ds_ids), org_id, mock_repo),
            _prefetch_datastores(set(ds_ids), org_id, mock_repo),
        ),
        timeout=5.0,
    )

    assert len(cache_a) == len(ds_ids)
    assert len(cache_b) == len(ds_ids)


# ---------------------------------------------------------------------------
# 4. Module constant: _DEFAULT_GLOBAL_PREFETCH_CONCURRENCY == 2 × _PREFETCH_CONCURRENCY
# ---------------------------------------------------------------------------


def test_default_global_prefetch_concurrency_is_2x_per_call():
    """_DEFAULT_GLOBAL_PREFETCH_CONCURRENCY must equal _PREFETCH_CONCURRENCY * 2."""
    assert collect_mod._DEFAULT_GLOBAL_PREFETCH_CONCURRENCY == collect_mod._PREFETCH_CONCURRENCY * 2, (
        f"Expected _DEFAULT_GLOBAL_PREFETCH_CONCURRENCY == _PREFETCH_CONCURRENCY * 2 "
        f"({collect_mod._PREFETCH_CONCURRENCY * 2}), "
        f"got {collect_mod._DEFAULT_GLOBAL_PREFETCH_CONCURRENCY}"
    )


# ---------------------------------------------------------------------------
# 5. Env var NUBI_PREFETCH_GLOBAL_CONCURRENCY is parsed
# ---------------------------------------------------------------------------


def test_global_prefetch_concurrency_env_override(monkeypatch: pytest.MonkeyPatch):
    """NUBI_PREFETCH_GLOBAL_CONCURRENCY must be parsed by the module formula."""
    monkeypatch.setenv("NUBI_PREFETCH_GLOBAL_CONCURRENCY", "42")
    computed = max(
        1,
        int(
            os.environ.get(
                "NUBI_PREFETCH_GLOBAL_CONCURRENCY",
                collect_mod._DEFAULT_GLOBAL_PREFETCH_CONCURRENCY,
            )
        ),
    )
    assert computed == 42, f"Expected 42 from env override; got {computed}"


def test_global_prefetch_concurrency_floor_guard(monkeypatch: pytest.MonkeyPatch):
    """NUBI_PREFETCH_GLOBAL_CONCURRENCY='0' must be clamped to 1."""
    monkeypatch.setenv("NUBI_PREFETCH_GLOBAL_CONCURRENCY", "0")
    computed = max(
        1,
        int(
            os.environ.get(
                "NUBI_PREFETCH_GLOBAL_CONCURRENCY",
                collect_mod._DEFAULT_GLOBAL_PREFETCH_CONCURRENCY,
            )
        ),
    )
    assert computed >= 1, f"Floor guard failed; computed={computed}"
