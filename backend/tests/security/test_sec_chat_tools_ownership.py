"""Editor-chat tools act as the calling user, inside the calling org."""

from __future__ import annotations

import uuid

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())


@pytest.fixture
def registry():
    from app.queries.registry import get_query_registry

    reg = get_query_registry()
    created: list[str] = []
    original = reg.register

    def _tracking(*a, **k):
        rq = original(*a, **k)
        created.append(rq.id)
        return rq

    reg.register = _tracking  # type: ignore[method-assign]
    yield reg
    reg.register = original  # type: ignore[method-assign]
    for qid in created:
        reg.unregister(qid)


def _ctx(org, role="member"):
    from app.chat.tools import ToolContext

    return ToolContext(user_id=str(uuid.uuid4()), org_id=org, role=role)


def _register(ctx, **args):
    from app.chat.tools import execute_tool

    out, _extra = execute_tool("register_query", {"name": "Revenue", "sql": "SELECT 1", **args}, context=ctx)
    return out


def test_tool_cannot_overwrite_another_orgs_query(registry):
    qid = f"q_{uuid.uuid4().hex[:8]}"
    registry.register(id=qid, sql="SELECT 'a'", name="A", owner_org_id=ORG_A)

    out = _register(_ctx(ORG_B), id=qid, sql="SELECT 'hijacked'")
    assert "error" in out
    assert registry.get(qid).sql == "SELECT 'a'"
    assert registry.get(qid).owner_org_id == ORG_A


def test_tool_stamps_the_callers_org(registry):
    qid = f"q_{uuid.uuid4().hex[:8]}"
    out = _register(_ctx(ORG_A), id=qid)
    assert out.get("id") == qid, out
    assert registry.get(qid).owner_org_id == ORG_A
    # and the owner can update its own query
    assert "error" not in _register(_ctx(ORG_A), id=qid, sql="SELECT 2")
    assert registry.get(qid).sql == "SELECT 2"


@pytest.mark.parametrize("ctx_factory", [lambda: None, lambda: _ctx(ORG_A, "viewer"), lambda: _ctx(ORG_A, None), lambda: _ctx(None)])
def test_registering_needs_a_writer_in_an_org(registry, ctx_factory):
    qid = f"q_{uuid.uuid4().hex[:8]}"
    out = _register(ctx_factory(), id=qid)
    assert "error" in out
    assert registry.get(qid) is None


def test_tool_cannot_replace_a_built_in_query(registry):
    builtin = next(rq for rq in registry.all() if rq.system)
    out = _register(_ctx(ORG_B), id=builtin.id, sql="SELECT 'hijacked'")
    assert "error" in out
    assert registry.get(builtin.id).sql == builtin.sql


def test_listing_shows_only_own_and_system_queries(registry):
    from app.chat.tools import execute_tool

    a, b, unowned = (f"q_{uuid.uuid4().hex[:8]}" for _ in range(3))
    registry.register(id=a, sql="SELECT 1", name="A", owner_org_id=ORG_A)
    registry.register(id=b, sql="SELECT 1", name="B", owner_org_id=ORG_B)
    registry.register(id=unowned, sql="SELECT 1", name="U")

    out, _ = execute_tool("list_registered_queries", {}, context=_ctx(ORG_B, "viewer"))
    ids = {q["id"] for q in out["queries"]}
    assert b in ids
    assert a not in ids and unowned not in ids
    assert any(rq.system and rq.id in ids for rq in registry.all())

    out, _ = execute_tool("list_registered_queries", {}, context=None)
    ids = {q["id"] for q in out["queries"]}
    assert not ({a, b, unowned} & ids)


def test_registry_refuses_unowned_write_over_an_owned_id(registry):
    qid = f"q_{uuid.uuid4().hex[:8]}"
    registry.register(id=qid, sql="SELECT 'a'", name="A", owner_org_id=ORG_A)

    with pytest.raises(ValueError):
        registry.register(id=qid, sql="SELECT 'x'", name="X")  # owner_org_id=None
    with pytest.raises(ValueError):
        registry.register(id=qid, sql="SELECT 'x'", name="X", owner_org_id=ORG_B)
    assert registry.get(qid).sql == "SELECT 'a'"

    # The trusted server-side loader may refresh it and keeps the owner.
    registry.register(id=qid, sql="SELECT 'fresh'", name="A", internal=True)
    assert registry.get(qid).sql == "SELECT 'fresh'"
    assert registry.get(qid).owner_org_id == ORG_A


def test_chat_route_threads_the_caller_into_tools(monkeypatch):
    """The streaming route hands the tool layer the caller's org and role."""
    import inspect

    from app.chat import llm
    from app.routes import chat as chat_route

    assert "tool_context" in inspect.signature(llm.stream_chat).parameters
    src = inspect.getsource(chat_route.chat_stream)
    assert "tool_context=" in src
