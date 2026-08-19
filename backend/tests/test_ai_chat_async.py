"""Tests for POST /ai/chat/async — background agent turn + notify-on-completion.

Coverage
--------
1. Auth gate: no token -> 401.
2. Returns immediately with a job_id (the agent turn runs detached).
3. A "build me a dashboard" prompt: the background turn persists a NEW board
   (there's no user left to click "Replace canvas") and writes a success/
   warning notification linking to it (``/d/{board_id}``).
4. A prompt that produces no dashboard spec still notifies, with no link —
   a background turn never fails silently.

Strategy mirrors test_ai_pin.py (InMemoryRepo + seeded org) and
test_notifications.py (InMemoryNotificationStore via
set_notification_store_for_tests).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.notify.notifications import (
    InMemoryNotificationStore,
    get_notification_store,
    set_notification_store_for_tests,
)
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "chat-async-tester@example.com",
        "name": "Chat Async Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


@pytest_asyncio.fixture
async def chat_client(app, fake_db):
    """HTTPX async client with a pre-seeded owner user in an org.

    Mirrors test_ai.py's ai_client — owner role so require_writer_default
    passes — plus an InMemoryNotificationStore so the background turn's
    notification write doesn't need a real Postgres pool.
    """
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
    set_repo(repo)
    set_notification_store_for_tests(InMemoryNotificationStore())

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, repo

    set_repo(None)
    set_notification_store_for_tests(None)


async def _wait_for_notifications(
    org_id: str, user_id: str, *, timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """Poll the notification store until the background turn writes its row."""
    store = get_notification_store()
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        items = await store.list_for_user(org_id, user_id)
        if items:
            return items
        await asyncio.sleep(0.02)
    return []


@pytest.mark.asyncio
class TestChatAsync:
    async def test_requires_auth(self, chat_client):
        ac, _user_id, _org_id, _repo = chat_client
        resp = await ac.post(
            "/api/v1/ai/chat/async",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    async def test_returns_job_id_immediately(self, chat_client):
        ac, user_id, _org_id, _repo = chat_client
        resp = await ac.post(
            "/api/v1/ai/chat/async",
            json={"messages": [{"role": "user", "content": "build me a dashboard of sales"}]},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["job_id"]

    async def test_dashboard_prompt_persists_board_and_notifies(self, chat_client):
        ac, user_id, org_id, repo = chat_client
        resp = await ac.post(
            "/api/v1/ai/chat/async",
            json={"messages": [{"role": "user", "content": "build me a dashboard of sales"}]},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

        items = await _wait_for_notifications(org_id, user_id)
        assert items, "expected the background turn to write a notification"
        note = items[0]
        assert note["type"] == "ai_chat_build"
        assert note["severity"] in ("success", "warning")
        assert note["link"] and note["link"].startswith("/d/")

        board_id = note["link"].split("/d/", 1)[1]
        row = await repo.get("boards", org_id, board_id)
        assert row is not None
        assert row["config"]["spec"]["widgets"]

    async def test_non_dashboard_prompt_notifies_without_board(self, chat_client):
        ac, user_id, org_id, _repo = chat_client
        resp = await ac.post(
            "/api/v1/ai/chat/async",
            json={"messages": [{"role": "user", "content": "what were our top regions last month"}]},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

        items = await _wait_for_notifications(org_id, user_id)
        assert items, "expected the background turn to write a notification"
        note = items[0]
        assert note["type"] == "ai_chat_build"
        assert not note.get("link")
