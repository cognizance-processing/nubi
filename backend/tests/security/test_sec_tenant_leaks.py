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
