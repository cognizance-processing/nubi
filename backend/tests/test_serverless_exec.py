"""Tests for the scale-to-zero query-execution seam (app.compute.serverless_exec).

Coverage
--------
Honesty audit — the ``fly`` backing is an unwired forward-compat skeleton.
Selecting it must never look like a working Fly Machines integration when it
isn't one:

  1. ``get_default_executor()`` default (unset/pool/auto) → HeavyPoolExecutor.
  2. ``NUBI_SERVERLESS_BACKEND=local`` → HeavyPoolExecutor with no forwarder
     (always in-process).
  3. ``NUBI_SERVERLESS_BACKEND=fly`` → FlyMachineExecutor, and selecting it
     logs a clear warning (surfaced at selection time, not buried in a stub).
  4. FlyMachineExecutor._wake / ._sleep raise NotImplementedError immediately
     if ever called directly (fail fast, never a misleading no-op/False).
  5. FlyMachineExecutor.submit() still completes successfully by falling open
     to the real pool/local backing — selecting ``fly`` never breaks or hangs,
     and never returns a fake/partial ExecResult.
  6. HeavyPoolExecutor itself (the working default) is unaffected: forwards
     when a forwarder is configured, runs locally when it is not.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import pytest

from app.compute.serverless_exec import (
    ExecPlan,
    FlyMachineExecutor,
    HeavyPoolExecutor,
    get_default_executor,
)


# ===========================================================================
# 1. Default selection (unset / pool / auto) → HeavyPoolExecutor
# ===========================================================================


def test_get_default_executor_default_is_heavy_pool():
    with patch.dict(os.environ, {"NUBI_SERVERLESS_BACKEND": ""}):
        executor = get_default_executor()
    assert isinstance(executor, HeavyPoolExecutor)


def test_get_default_executor_pool_explicit():
    with patch.dict(os.environ, {"NUBI_SERVERLESS_BACKEND": "pool"}):
        executor = get_default_executor()
    assert isinstance(executor, HeavyPoolExecutor)


# ===========================================================================
# 2. NUBI_SERVERLESS_BACKEND=local → HeavyPoolExecutor, no forwarder
# ===========================================================================


def test_get_default_executor_local_has_no_forwarder():
    async def _forward(request, payload):  # pragma: no cover - must not be used
        raise AssertionError("local backend must never forward")

    with patch.dict(os.environ, {"NUBI_SERVERLESS_BACKEND": "local"}):
        executor = get_default_executor(forward=_forward)

    assert isinstance(executor, HeavyPoolExecutor)
    assert executor._forward is None


# ===========================================================================
# 3. NUBI_SERVERLESS_BACKEND=fly → FlyMachineExecutor + clear warning logged
#    at selection time (not buried inside a stub the caller never sees).
# ===========================================================================


def test_get_default_executor_fly_selection_logs_warning(caplog):
    with patch.dict(os.environ, {"NUBI_SERVERLESS_BACKEND": "fly"}):
        with caplog.at_level(logging.WARNING, logger="nubi.compute.serverless"):
            executor = get_default_executor()

    assert isinstance(executor, FlyMachineExecutor)
    assert any(
        "NOT IMPLEMENTED" in record.message or "not implemented" in record.message.lower()
        for record in caplog.records
    ), "selecting the fly backend must log a clear, immediate warning"
    assert any("fly" in record.message.lower() for record in caplog.records)


# ===========================================================================
# 4. FlyMachineExecutor._wake / ._sleep fail fast if ever called directly.
# ===========================================================================


@pytest.mark.asyncio
async def test_fly_machine_executor_wake_raises_not_implemented():
    executor = FlyMachineExecutor()
    with pytest.raises(NotImplementedError):
        await executor._wake({})


@pytest.mark.asyncio
async def test_fly_machine_executor_sleep_raises_not_implemented():
    executor = FlyMachineExecutor()
    with pytest.raises(NotImplementedError):
        await executor._sleep()


# ===========================================================================
# 5. FlyMachineExecutor.submit() fails OPEN to a real, working backing — it
#    never returns a fake/partial ExecResult, and selecting it never hangs
#    or breaks the caller.
# ===========================================================================


@pytest.mark.asyncio
async def test_fly_machine_executor_submit_falls_open_to_pool():
    async def _local_run(plan, connector_cfg):
        return {"rows": 3}

    fallback = HeavyPoolExecutor(forward=None, local_run=_local_run)
    executor = FlyMachineExecutor(fallback=fallback)

    plan = ExecPlan(payload={"sql": "select 1"}, tier="batch", batch=True)
    result = await executor.submit(plan, connector_cfg={})

    assert result.ok is True
    assert result.payload == {"rows": 3}


# ===========================================================================
# 6. HeavyPoolExecutor (the working default) is unaffected by the honesty
#    changes to FlyMachineExecutor/ModalRunner.
# ===========================================================================


@pytest.mark.asyncio
async def test_heavy_pool_executor_runs_locally_without_forwarder():
    async def _local_run(plan, connector_cfg):
        return {"rows": 1}

    executor = HeavyPoolExecutor(forward=None, local_run=_local_run)
    plan = ExecPlan(payload={"sql": "select 1"})
    result = await executor.submit(plan, connector_cfg={})

    assert result.ok is True
    assert result.payload == {"rows": 1}
    assert result.cold_start is False


@pytest.mark.asyncio
async def test_heavy_pool_executor_forwards_heavy_batch_plan():
    class _FakeResp:
        status_code = 200
        headers = {}

    async def _forward(request, payload):
        return _FakeResp()

    executor = HeavyPoolExecutor(forward=_forward, local_run=None)
    plan = ExecPlan(payload={"sql": "select 1"}, tier="batch", batch=True)
    result = await executor.submit(plan, connector_cfg={})

    assert result.ok is True
    assert result.tier == "scale_to_zero"
    assert result.cold_start is True
