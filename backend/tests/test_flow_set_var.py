"""A5 set_var — a flow python cell publishes a variable (persist path).

A python cell calls set_var(name, value, persist=False). execute_task lifts the
published vars onto outcome["set_vars"]; the runtime flushes persist=True ones to
the long-term store, so later cells (which reload via load_vars_namespace) and
future runs see them.

NOTE: the ephemeral, run-scoped (persist=False) in-run overlay is a separate
follow-up — see _flush_persisted_set_vars docstring. This covers the persist path.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import pytest

from app.flows.executor import TaskContext, execute_task

CLAIMS = {"org_id": "org-test", "sub": "user-test"}


def _py(code: str) -> dict:
    return {"key": "c", "kind": "python", "config": {"code": code}}


def test_python_set_var_surfaces_on_outcome():
    out = execute_task(
        _py("set_var('cutoff', '2024-01-01', persist=True)\nresult = {'ok': 1}"),
        TaskContext(org_id="org-test"),
        CLAIMS,
    )
    assert out["state"] == "success", out.get("error")
    assert out["set_vars"] == {"cutoff": {"value": "2024-01-01", "persist": True}}
    # __set_vars__ is stripped from the user-visible result.
    assert "__set_vars__" not in out["result"]
    assert out["result"]["ok"] == 1


def test_set_var_visible_within_same_cell():
    out = execute_task(
        _py("set_var('x', 7)\nresult = {'echo': vars['x']}"),
        TaskContext(org_id="org-test"),
        CLAIMS,
    )
    assert out["result"]["echo"] == 7


def test_no_set_var_means_no_set_vars_key():
    out = execute_task(_py("result = {'plain': True}"), TaskContext(org_id="org-test"), CLAIMS)
    assert "set_vars" not in out


@pytest.mark.asyncio
async def test_flush_persists_only_persist_true():
    from app.flows.runtime import _flush_persisted_set_vars
    from app.vars.store import InMemoryVarStore, set_var_store

    store = InMemoryVarStore()
    set_var_store(store)
    try:
        outcome = {
            "set_vars": {
                "kept": {"value": "2024-01-01", "persist": True},
                "ephemeral": {"value": 99, "persist": False},
            }
        }
        await _flush_persisted_set_vars(outcome, "org-test", None)
        kept = await store.get_var("org-test", "kept")
        assert kept is not None and kept["value"] == "2024-01-01"
        # persist=False is NOT written to the long-term store.
        assert await store.get_var("org-test", "ephemeral") is None
    finally:
        set_var_store(None)
