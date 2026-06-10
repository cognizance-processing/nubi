"""A5 slice 2 — {{ vars.* }} templating in the /query path.

Covers the core mechanism without the full route harness:
  * the `vars` namespace renders to BOUND positional params (never concatenated),
  * `vars` is reserved so a caller can't shadow it via named_params,
  * _load_query_vars overlays project-scoped vars on org-global ones.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import pytest

from app.connectors.planner import resolve_named_params


def test_vars_namespace_binds_positionally():
    sql = "SELECT * FROM orders WHERE region = {{ vars.region }}"
    rewritten, params = resolve_named_params(sql, {"vars": {"region": "us-east"}})
    # The value is bound as $1, not interpolated into the SQL text.
    assert "us-east" not in rewritten
    assert "$1" in rewritten
    assert params == ["us-east"]


def test_vars_injection_payload_is_bound_not_executed():
    # A SQL-injection-shaped variable value stays a single bound parameter.
    payload = "x' UNION SELECT secret FROM other --"
    sql = "SELECT * FROM t WHERE c = {{ vars.c }}"
    rewritten, params = resolve_named_params(sql, {"vars": {"c": payload}})
    assert payload not in rewritten
    assert params == [payload]


def test_vars_is_a_reserved_named_param():
    from app.routes.query import _TOKEN_CLAIM_RESERVED_NAMES

    assert "vars" in _TOKEN_CLAIM_RESERVED_NAMES


@pytest.mark.asyncio
async def test_load_query_vars_project_overlays_global():
    from app.vars.store import InMemoryVarStore, set_var_store
    from app.routes.query import _load_query_vars

    store = InMemoryVarStore()
    set_var_store(store)
    try:
        org = "11111111-1111-1111-1111-111111111111"
        proj = "22222222-2222-2222-2222-222222222222"
        await store.set_var(org, "region", "global-default", project_id=None)
        await store.set_var(org, "tier", "free", project_id=None)
        await store.set_var(org, "region", "project-override", project_id=proj)

        org_only = await _load_query_vars(org, None)
        assert org_only == {"region": "global-default", "tier": "free"}

        with_proj = await _load_query_vars(org, proj)
        # Project var shadows the org-global of the same key; other globals remain.
        assert with_proj["region"] == "project-override"
        assert with_proj["tier"] == "free"
    finally:
        set_var_store(None)
