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
