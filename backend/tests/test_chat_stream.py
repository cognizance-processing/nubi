"""Tests for the streaming editor chat backend (models + offline agent loop).

Coverage
--------
1.  GET /chat/models without auth → 401.
2.  GET /chat/models with auth → 200, returns the curated [{id, label}] list
    including the exact Opus 4.8 / Sonnet 4.6 / Haiku 4.5 model ids.
3.  Model resolution: invalid/empty ids fall back to the Opus 4.8 default.
4.  Offline streaming (no ANTHROPIC_API_KEY): a dashboard request fires a real
    propose_dashboard_spec tool call and the turn accumulates the spec + text,
    emitting the documented token / tool_use / tool_result event shapes.

The streaming SSE endpoint itself is exercised end-to-end against a real
Postgres in the pg integration suite; here we cover the model surface and the
offline agent loop (which needs no DB or network).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token

# Import the chat router module so it self-registers on api_router at import time.
import app.routes.chat  # noqa: F401, E402


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def chat_client(app, fake_db):
    user_id = "11111111-1111-1111-1111-111111111111"
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "editor@example.com",
        "name": "Editor",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc),
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, user_id


# ---------------------------------------------------------------------------
# GET /chat/models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_models_requires_auth(chat_client):
    ac, _ = chat_client
    resp = await ac.get("/api/v1/chat/models")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_models_returns_curated_list(chat_client):
    ac, user_id = chat_client
    resp = await ac.get("/api/v1/chat/models", headers=_auth_headers(user_id))
    assert resp.status_code == 200
    models = resp.json()
    ids = [m["id"] for m in models]
    assert "claude-opus-4-8" in ids
    assert "claude-sonnet-4-6" in ids
    assert any(i.startswith("claude-haiku-4-5") for i in ids)
    # Every entry has id + label.
    assert all(m.get("id") and m.get("label") for m in models)


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------


def test_resolve_model_falls_back_to_default():
    from app.chat.models import DEFAULT_MODEL_ID, resolve_model

    assert DEFAULT_MODEL_ID == "claude-opus-4-8"
    assert resolve_model(None) == "claude-opus-4-8"
    assert resolve_model("") == "claude-opus-4-8"
    assert resolve_model("not-a-real-model") == "claude-opus-4-8"
    assert resolve_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Offline agent loop
# ---------------------------------------------------------------------------


def test_offline_stream_proposes_dashboard_spec(monkeypatch):
    # Force the offline path regardless of the ambient environment.
    monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: None)

    from app.chat.llm import stream_chat

    history = [{"role": "user", "content": "build a dashboard of revenue by month"}]
    events: list[dict[str, Any]] = []
    turn = None
    for ev, t in stream_chat(history, "claude-opus-4-8"):
        events.append(ev)
        turn = t

    types = [e["type"] for e in events]
    assert "tool_use" in types
    assert "tool_result" in types
    assert "token" in types

    tool_use = next(e for e in events if e["type"] == "tool_use")
    assert tool_use["name"] == "propose_dashboard_spec"
    assert "instruction" in tool_use["input"]

    assert turn is not None
    assert turn.spec is not None
    assert len(turn.spec.get("widgets", [])) >= 1
    assert turn.text  # a textual reply was produced
    assert len(turn.tool_calls) == 1


def test_offline_stream_non_dashboard_request(monkeypatch):
    monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: None)

    from app.chat.llm import stream_chat

    history = [{"role": "user", "content": "hello there"}]
    events = [ev for ev, _ in stream_chat(history, "claude-opus-4-8")]
    types = [e["type"] for e in events]
    # No tool call for a plain greeting, but tokens still stream.
    assert "tool_use" not in types
    assert "token" in types


# ---------------------------------------------------------------------------
# Real (LiteLLM) streaming loop — driven by a fake `litellm` module
# ---------------------------------------------------------------------------
#
# litellm is an optional dependency (not installed in CI), so we inject a fake
# module that mimics the slice of the SDK `_stream_real` uses: a streaming
# `completion()` yielding OpenAI-style chunks, and `stream_chunk_builder()`
# re-assembling the final message (incl. tool_calls).  This proves the
# OpenAI-normalised parse path: live token deltas, an assembled tool call,
# tool execution, and the multi-step loop terminating on a plain-text turn.


class _Delta:
    def __init__(self, content=None):
        self.content = content
        self.tool_calls = None


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _StreamChoice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, content):
        self.choices = [_StreamChoice(_Delta(content=content))]


class _RebuiltChoice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason


class _Rebuilt:
    def __init__(self, message, finish_reason):
        self.choices = [_RebuiltChoice(message, finish_reason)]


class _FakeLiteLLM:
    """Minimal stand-in for the litellm module used by `_stream_real`.

    *steps* is a list of dicts: ``{"tokens": [str, ...], "rebuilt": _Rebuilt}``.
    ``completion()`` and ``stream_chunk_builder()`` are each called once per
    agentic step, in order, so a shared cursor advances after each rebuild.
    """

    def __init__(self, steps):
        self._steps = steps
        self._i = 0
        self.drop_params = False

    def completion(self, **kwargs):  # noqa: D401 — mimics litellm.completion(stream=True)
        return iter([_Chunk(t) for t in self._steps[self._i]["tokens"]])

    def stream_chunk_builder(self, chunks, messages=None):
        rebuilt = self._steps[self._i]["rebuilt"]
        self._i += 1
        return rebuilt


def test_real_litellm_stream_tool_then_final(monkeypatch):
    import sys

    # A credential is present → real path (not offline).
    monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: "sk-test")

    steps = [
        # Step 1: stream some text, then call a no-arg tool.
        {
            "tokens": ["Looking ", "up ", "queries… "],
            "rebuilt": _Rebuilt(
                _Msg("", tool_calls=[_ToolCall("call_1", "list_registered_queries", "{}")]),
                finish_reason="tool_calls",
            ),
        },
        # Step 2: final plain-text answer, no tools → loop ends.
        {
            "tokens": ["Done."],
            "rebuilt": _Rebuilt(_Msg("Done."), finish_reason="stop"),
        },
    ]
    monkeypatch.setitem(sys.modules, "litellm", _FakeLiteLLM(steps))

    from app.chat.llm import stream_chat

    history = [{"role": "user", "content": "what queries exist?"}]
    events: list[dict[str, Any]] = []
    turn = None
    for ev, t in stream_chat(history, "claude-opus-4-8"):
        events.append(ev)
        turn = t

    types = [e["type"] for e in events]
    assert types.count("token") >= 2  # tokens from both steps streamed live
    assert "tool_use" in types and "tool_result" in types

    tool_use = next(e for e in events if e["type"] == "tool_use")
    assert tool_use["id"] == "call_1"
    assert tool_use["name"] == "list_registered_queries"
    assert tool_use["input"] == {}

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["id"] == "call_1"
    assert "queries" in tool_result["output"]  # real execute_tool ran

    assert turn is not None
    assert len(turn.tool_calls) == 1
    assert turn.text.endswith("Done.")  # final-turn text accumulated
