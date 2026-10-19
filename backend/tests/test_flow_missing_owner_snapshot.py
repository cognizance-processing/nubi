"""A query cell refused for a missing owner-policy snapshot fails cleanly.

_claims_with_owner_policies is fail-closed: a scheduled query cell with no
owner-policy snapshot must never run (it would apply no row filter). That
refusal must surface as a terminal, explained task failure, not as an
exception out of the worker loop, which left the task to be reaped and
retried every lease period.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.flows.registry import reset_for_tests
from app.flows.runtime import (
    MissingOwnerPolicySnapshot,
    _claims_with_owner_policies,
    drain_flow_run,
    materialize_flow_run,
    run_one_ready_task,
)
from app.flows.store import InMemoryFlowStore, set_flow_store

_ORG_ID = "00000000-0000-0000-0000-000000000001"
_USER_ID = "00000000-0000-0000-0000-000000000002"
_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _spec(runtime_config: dict | None) -> dict:
    spec = {
        "version": 1,
        "name": "snapshot_check",
        "tasks": [
            {"key": "q", "kind": "query", "needs": [], "retries": 3,
             "retry_backoff_s": 0, "config": {"sql": "SELECT 1 AS n"}},
            {"key": "after", "kind": "noop", "needs": ["q"], "config": {}},
        ],
    }
    if runtime_config is not None:
        spec["runtime_config"] = runtime_config
    return spec


async def _run(spec: dict):
    store = InMemoryFlowStore()
    set_flow_store(store)
    flow = await store.create_flow(org_id=_ORG_ID, created_by=_USER_ID, name="f", spec=spec)
    reset_for_tests()
    flow_run = await materialize_flow_run(store, flow, {}, "schedule", _NOW)
    return store, flow_run


@pytest.mark.asyncio
async def test_missing_snapshot_fails_the_task_without_raising():
    store, flow_run = await _run(_spec(runtime_config={}))

    # Scheduled/worker claims carry no policies. Must not raise.
    result = await run_one_ready_task(store, _NOW, claims={}, worker_id="w", lease_seconds=60)

    assert result is not None
    assert result["task_key"] == "q"
    assert result["state"] == "failed"
    assert "owner-policy snapshot" in (result.get("error") or "")
    # Terminal: retries are not spent on a refusal that cannot succeed.
    assert result["attempt"] == 0

    states = {r["task_key"]: r["state"] for r in await store.list_task_runs(flow_run["id"])}
    assert states["q"] == "failed"
    assert states["after"] != "success", "a downstream cell must not run after the refusal"
    assert (await store.get_flow_run(flow_run["id"]))["state"] == "failed"


@pytest.mark.asyncio
async def test_drain_path_also_fails_cleanly():
    store, flow_run = await _run(_spec(runtime_config={}))
    await drain_flow_run(store, flow_run["id"], _NOW, claims={})
    runs = {r["task_key"]: r for r in await store.list_task_runs(flow_run["id"])}
    assert runs["q"]["state"] == "failed"
    assert "owner-policy snapshot" in (runs["q"].get("error") or "")
    assert runs["after"]["state"] != "success"


def test_guard_still_refuses_and_is_still_a_runtime_error():
    """The fail-closed contract is unchanged for every other caller."""
    with pytest.raises(RuntimeError) as exc_info:
        _claims_with_owner_policies({}, {"id": "f", "spec": _spec(None)}, requires_rls=True)
    assert isinstance(exc_info.value, MissingOwnerPolicySnapshot)
