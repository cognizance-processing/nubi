"""Shared, process-wide state is only ever read back by the org (or user) it belongs to."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.repos.provider import set_repo

    app, repo, users = make_tenant_app(
        {"a": (ORG_A, "owner"), "b": (ORG_B, "owner"), "b2": (ORG_B, "member"), "bv": (ORG_B, "viewer")},
        modules=("app.routes.git", "app.routes.chat", "app.routes.metrics", "app.routes.cache", "app.routes.ops"),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        c.users = users  # type: ignore[attr-defined]
        c.repo = repo  # type: ignore[attr-defined]
        yield c
    set_repo(None)


# ---------------------------------------------------------------------------
# Git sync
# ---------------------------------------------------------------------------


def test_git_sync_commits_only_the_callers_queries(client):
    from app.queries.registry import get_query_registry

    reg = get_query_registry()
    a, b, unowned = (f"q_{uuid.uuid4().hex[:8]}" for _ in range(3))
    reg.register(id=a, sql="SELECT secret_a FROM ledger_a", name="A", owner_org_id=ORG_A)
    reg.register(id=b, sql="SELECT own_b FROM ledger_b", name="B", owner_org_id=ORG_B)
    reg.register(id=unowned, sql="SELECT secret_u FROM ledger_u", name="U")

    captured: list = []

    class _FakeGit:
        def commit_resources(self, items, message, author=None):
            captured.extend(items)
            return "deadbeef"

    try:
        with patch("app.routes.git._org_repo", return_value=_FakeGit()):
            resp = client.post("/api/v1/git/sync", json={"message": "sync"}, headers=as_user("b"))
        assert resp.status_code == 200, resp.text
        blob = json.dumps(captured, default=str)
        assert "ledger_b" in blob
        assert "ledger_a" not in blob and "ledger_u" not in blob
        assert "demo" in blob  # built-in queries are still included
    finally:
        for qid in (a, b, unowned):
            reg.unregister(qid)


# ---------------------------------------------------------------------------
# Chat transcripts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_store_lookups_are_per_user():
    from app.chat import store

    user, org, chat = str(uuid.uuid4()), ORG_A, str(uuid.uuid4())
    with (
        patch.object(store, "ensure_tables", new=AsyncMock()),
        patch.object(store, "fetchrow", new=AsyncMock(return_value=None)) as fetchrow,
        patch.object(store, "fetch", new=AsyncMock(return_value=[])) as fetch,
    ):
        assert await store.get_chat(org, chat, user) is None
        sql, *args = fetchrow.call_args.args
        assert "user_id" in sql and user in args and org in args

        assert await store.get_conversation(org, chat, user) is None
        assert fetch.call_count == 0  # messages are never read for a chat that is not yours

        for board in (None, "board-1"):
            await store.list_conversations(org, board, user)
            sql, *args = fetch.call_args.args
            assert "user_id" in sql and user in args and org in args

    with pytest.raises(TypeError):
        await store.get_chat(org, chat)  # type: ignore[call-arg]


def test_chat_routes_pass_the_calling_user(client):
    from app.chat import store

    chat_id = str(uuid.uuid4())
    with (
        patch.object(store, "list_conversations", new=AsyncMock(return_value=[])) as listing,
        patch.object(store, "get_conversation", new=AsyncMock(return_value=None)) as one,
    ):
        assert client.get("/api/v1/chat/conversations", headers=as_user("b2")).status_code == 200
        assert client.get(f"/api/v1/chat/conversations/{chat_id}", headers=as_user("b2")).status_code == 404
    assert client.users["b2"] in listing.call_args.args + tuple(listing.call_args.kwargs.values())
    assert client.users["b2"] in one.call_args.args + tuple(one.call_args.kwargs.values())


# ---------------------------------------------------------------------------
# Metric registry
# ---------------------------------------------------------------------------


def _metric(slug: str, table: str):
    from app.metrics.models import Measure, MetricDefinition

    return MetricDefinition(
        id=slug, name=slug, measure=Measure(name="m", agg="sum", expr="v", type="additive"), base_table=table
    )


def test_same_metric_slug_in_two_orgs_stays_separate():
    from app.metrics.registry import MetricRegistry

    reg = MetricRegistry()
    reg.register(_metric("revenue", "ledger_a"), org_id=ORG_A)
    reg.register(_metric("revenue", "ledger_b"), org_id=ORG_B)
    reg.register(_metric("only_a", "ledger_a"), org_id=ORG_A)
    reg.register(_metric("shared_seed", "demo"))

    assert reg.get("revenue", ORG_A).base_table == "ledger_a"
    assert reg.get("revenue", ORG_B).base_table == "ledger_b"
    assert reg.get("only_a", ORG_B) is None
    assert reg.get("only_a") is None  # no org named → org-owned metrics are not found
    assert reg.get("shared_seed", ORG_B).base_table == "demo"
    assert {m.base_table for m in reg.for_org(ORG_B)} == {"ledger_b", "demo"}

    reg.unregister("revenue", ORG_B)
    assert reg.get("revenue", ORG_B) is None
    assert reg.get("revenue", ORG_A).base_table == "ledger_a"


@pytest.mark.asyncio
async def test_lazy_metric_load_registers_under_the_rows_org():
    from app.metrics import registry as mreg

    mreg.reset_for_tests()
    row = {
        "id": str(uuid.uuid4()),
        "org_id": ORG_A,
        "project_id": None,
        "name": "Revenue",
        "config": {
            "sql": "SELECT 1",
            "metric": {"slug": "rev_zz", "measure": {"name": "m", "agg": "sum", "expr": "v"}, "base_table": "ledger_a"},
        },
    }
    try:
        with patch("app.db.fetchrow", new=AsyncMock(return_value=row)):
            loaded = await mreg.ensure_persisted_metric("rev_zz", ORG_A)
        assert loaded is not None
        reg = mreg.get_metric_registry()
        assert reg.get("rev_zz", ORG_A) is not None
        assert reg.get("rev_zz", ORG_B) is None
        assert reg.get("rev_zz") is None
    finally:
        mreg.reset_for_tests()


# ---------------------------------------------------------------------------
# Cache and operational endpoints (process-wide state)
# ---------------------------------------------------------------------------


@pytest.fixture
def ops_client(client):
    """The cache / ops routers are mounted by main.py ahead of the generic
    resource routes; mount them the same way on a small app."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.errors import register_handlers
    from app.routes.cache import router as cache_router
    from app.routes.insights import router as insights_router
    from app.routes.ops import router as ops_router

    app = FastAPI()
    register_handlers(app)
    app.dependency_overrides = client.app.dependency_overrides
    for r in (cache_router, ops_router, insights_router):
        app.include_router(r, prefix="/api/v1")
    with TestClient(app, raise_server_exceptions=False) as c:
        c.repo = client.repo  # type: ignore[attr-defined]
        yield c


def _superadmin(is_admin: bool):
    return patch("app.db.fetchrow", new=AsyncMock(return_value={"is_superadmin": is_admin}))


@pytest.mark.parametrize("path", ["/api/v1/cache/stats", "/api/v1/ops/stats", "/api/v1/_cache/stats"])
def test_process_wide_stats_are_operator_only(ops_client, path):
    client = ops_client

    with _superadmin(False):
        assert client.get(path, headers=as_user("b")).status_code == 403
    with _superadmin(True):
        assert client.get(path, headers=as_user("b")).status_code == 200


def test_cache_invalidation_is_scoped_to_the_callers_org(ops_client):
    import asyncio

    client = ops_client

    from app.connectors.cache import get_cache

    cache = get_cache()
    cache.put("k_a", b"a", tags=[f"org:{ORG_A}", "board:board-a"])
    cache.put("k_b", b"b", tags=[f"org:{ORG_B}"])
    board_b = asyncio.run(
        client.repo.create(resource="boards", org_id=ORG_B, created_by="u", name="B", config={})
    )
    cache.put("k_bb", b"bb", tags=[f"org:{ORG_B}", f"board:{board_b['id']}"])

    def post(body, user="b"):
        with _superadmin(False):
            return client.post("/api/v1/cache/invalidate", json=body, headers=as_user(user))

    assert post({"all": True}).status_code == 403
    assert post({"tag": f"org:{ORG_A}"}).status_code == 403
    assert post({"tag": "board:board-a"}).status_code == 403
    assert post({"tag": "something-else"}).status_code == 403
    assert cache.get("k_a") == b"a"
    assert post({"tag": f"org:{ORG_B}"}, user="bv").status_code == 403  # read-only member

    assert post({"tag": f"board:{board_b['id']}"}).status_code == 200
    assert cache.get("k_bb") is None and cache.get("k_b") == b"b"
    assert post({"tag": f"org:{ORG_B}"}).status_code == 200
    assert cache.get("k_b") is None and cache.get("k_a") == b"a"

    with _superadmin(True):
        assert client.post("/api/v1/cache/invalidate", json={"all": True}, headers=as_user("b")).status_code == 200
    assert cache.get("k_a") is None


# ---------------------------------------------------------------------------
# Rendered thumbnails
# ---------------------------------------------------------------------------


def test_thumbnail_cache_key_includes_the_org():
    from app.routes.export_share import _policy_fingerprint

    claims = {"policies": {}}
    assert _policy_fingerprint(claims, ORG_A) != _policy_fingerprint(claims, ORG_B)
    assert _policy_fingerprint(claims, ORG_A) == _policy_fingerprint({"policies": {}}, ORG_A)
    assert _policy_fingerprint({"policies": {"region": "x"}}, ORG_A) != _policy_fingerprint(claims, ORG_A)
    with pytest.raises(TypeError):
        _policy_fingerprint(claims)  # type: ignore[call-arg]
