"""Flow task execution emits compute metering.

Flow runs consume compute on our nodes — the same COGS line as interactive
query/kernel compute. ``execute_task`` must record a metering event so flow
compute counts toward the org's compute-unit quota / overage. Preview runs must
NOT meter (no real execution, no bill).
"""

from __future__ import annotations

import time

import pytest

from app.compute.metering import InMemorySink, get_usage, set_sink
from app.flows.executor import TaskContext, execute_task


@pytest.fixture(autouse=True)
def _fresh_sink():
    set_sink(InMemorySink())
    yield
    set_sink(None)


def _run(task, **ctx_kwargs):
    return execute_task(task, TaskContext(org_id="org-123", **ctx_kwargs), {"sub": "user-1"})


def test_python_task_records_compute_metering():
    res = _run({"kind": "python", "config": {"code": "result = {'rows': [{'a': 1}]}"}})
    assert res["state"] == "success"
    time.sleep(0.05)  # _meter fires fire-and-forget on the loop / inline
    events = get_usage()
    assert len(events) == 1
    ev = events[0]
    assert ev["org_id"] == "org-123"
    assert ev["user_id"] == "user-1"
    assert ev["kind"] == "kernel"          # → compute_units in reconciliation
    assert ev["tier"] == "flow_kernel"     # attribution: flow compute
    assert ev["units"] > 0                 # compute-seconds consumed


def test_preview_run_does_not_meter():
    res = _run({"kind": "python", "config": {"code": "result = {}"}}, preview_mode=True)
    assert res["state"] == "success"
    time.sleep(0.05)
    assert get_usage() == []


def test_failed_task_still_meters_consumed_compute():
    res = _run({"kind": "python", "config": {"code": "raise ValueError('boom')"}})
    assert res["state"] == "failed"
    time.sleep(0.05)
    # The handler ran (and consumed compute) before raising.
    assert len(get_usage()) == 1
    assert get_usage()[0]["tier"] == "flow_kernel"
