"""Notification feed store + routes, Web Push store, and notify_event dispatch.

Coverage
--------
Store (InMemoryNotificationStore):
- targeted vs broadcast visibility (targeted leaks to nobody else; broadcast to all);
- per-user read-state for broadcasts (one user reading doesn't mark it read for another);
- unread_count + mark_all_read + targeted mark_read auth (non-addressee can't).

Routes (GET/POST /notifications/*):
- feed lists targeted + broadcast with read folding;
- unread_count badge; mark one read; read_all clears.

Push (InMemoryPushStore + send_push):
- subscribe upsert by endpoint (re-subscribe replaces, doesn't duplicate);
- list_for_users / org scoping; unsubscribe deletes;
- send_push degrades to PUSH_SKIPPED with no VAPID keys (never raises).

Dispatch (notify_event):
- writes an in-app row (broadcast + targeted);
- fans out to a monkeypatched channels_for_org's fake channel (channel.send called);
- a channel that raises is swallowed — notify_event never raises.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.routes.notifications  # noqa: F401 — register routes
import app.routes.push  # noqa: F401 — register routes
from app.auth.jwt import mint_access_token
from app.notify.notifications import (
    InMemoryNotificationStore,
    set_notification_store_for_tests,
)
from app.notify.push import (
    PUSH_SKIPPED,
    InMemoryPushStore,
    send_push,
    set_push_store_for_tests,
)
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

ORG = str(uuid.uuid4())
ALICE = str(uuid.uuid4())
BOB = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Store-level (no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_targeted_vs_broadcast_and_read_state():
    store = InMemoryNotificationStore()

    await store.create(ORG, type="system", title="Broadcast 1")
    await store.create(ORG, type="system", title="For Alice", user_id=ALICE)
    await store.create(ORG, type="system", title="For Bob", user_id=BOB)

    alice_feed = await store.list_for_user(ORG, ALICE)
    titles = {n["title"] for n in alice_feed}
    # Alice sees the broadcast + her targeted row, NOT Bob's.
    assert titles == {"Broadcast 1", "For Alice"}

    bob_feed = await store.list_for_user(ORG, BOB)
    assert {n["title"] for n in bob_feed} == {"Broadcast 1", "For Bob"}

    # Both start with 2 unread.
    assert await store.unread_count(ORG, ALICE) == 2
    assert await store.unread_count(ORG, BOB) == 2

    # Alice reads the broadcast — it is per-user, so Bob's count is unchanged.
    broadcast_id = next(n["id"] for n in alice_feed if n["broadcast"])
    assert await store.mark_read(broadcast_id, ALICE) is True
    assert await store.unread_count(ORG, ALICE) == 1
    assert await store.unread_count(ORG, BOB) == 2

    # unread_only filter for Alice now hides the read broadcast.
    unread = await store.list_for_user(ORG, ALICE, unread_only=True)
    assert {n["title"] for n in unread} == {"For Alice"}


@pytest.mark.asyncio
async def test_targeted_mark_read_requires_addressee_and_mark_all():
    store = InMemoryNotificationStore()
    row = await store.create(ORG, type="system", title="For Alice", user_id=ALICE)

    # Bob can't mark Alice's targeted notification read.
    assert await store.mark_read(row["id"], BOB) is False
    assert await store.unread_count(ORG, ALICE) == 1

    # Alice can.
    assert await store.mark_read(row["id"], ALICE) is True
    assert await store.unread_count(ORG, ALICE) == 0

    # mark_all_read clears a fresh broadcast for Bob.
    await store.create(ORG, type="system", title="Broadcast")
    assert await store.unread_count(ORG, BOB) == 1
    remaining = await store.mark_all_read(ORG, BOB)
    assert remaining == 0
    assert await store.unread_count(ORG, BOB) == 0


@pytest.mark.asyncio
async def test_org_isolation():
    store = InMemoryNotificationStore()
    other_org = str(uuid.uuid4())
    await store.create(other_org, type="system", title="Foreign broadcast")
    await store.create(ORG, type="system", title="Mine")

    feed = await store.list_for_user(ORG, ALICE)
    assert {n["title"] for n in feed} == {"Mine"}


# ---------------------------------------------------------------------------
# Push store + send_push degrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_upsert_list_delete():
    store = InMemoryPushStore()
    await store.upsert(ALICE, ORG, "https://push/ep1", "p1", "a1", "UA")
    # Re-subscribe with the same endpoint REPLACES (no duplicate).
    await store.upsert(ALICE, ORG, "https://push/ep1", "p1b", "a1b", "UA2")
    await store.upsert(BOB, ORG, "https://push/ep2", "p2", "a2", None)

    alice_subs = await store.list_for_users(ORG, [ALICE])
    assert len(alice_subs) == 1
    assert alice_subs[0]["p256dh"] == "p1b"  # upsert applied

    both = await store.list_for_users(ORG, [ALICE, BOB])
    assert len(both) == 2

    # Org scoping: a different org sees none of these.
    other = await store.list_for_users(str(uuid.uuid4()), [ALICE, BOB])
    assert other == []

    # IDOR: Bob cannot delete Alice's subscription (delete is user-scoped).
    assert await store.delete("https://push/ep1", BOB) is False
    assert len(await store.list_for_users(ORG, [ALICE])) == 1

    # Owner can delete; second delete is a no-op (already gone).
    assert await store.delete("https://push/ep1", ALICE) is True
    assert await store.delete("https://push/ep1", ALICE) is False
    assert len(await store.list_for_users(ORG, [ALICE])) == 0


@pytest.mark.asyncio
async def test_push_upsert_cannot_hijack_foreign_endpoint():
    """A user cannot rebind an endpoint already owned by another user."""
    store = InMemoryPushStore()
    await store.upsert(ALICE, ORG, "https://push/shared", "pa", "aa", "UA")

    # Bob tries to take over Alice's endpoint → refused (empty row), Alice keeps it.
    hijack = await store.upsert(BOB, ORG, "https://push/shared", "pb", "ab", "UB")
    assert hijack == {}
    alice_subs = await store.list_for_users(ORG, [ALICE])
    assert len(alice_subs) == 1 and alice_subs[0]["p256dh"] == "pa"
    assert await store.list_for_users(ORG, [BOB]) == []


@pytest.mark.asyncio
async def test_send_push_degrades_without_vapid(monkeypatch):
    # No VAPID keys configured → no-op, never raises.
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    result = send_push(
        {"endpoint": "https://push/ep", "p256dh": "p", "auth": "a"},
        {"title": "hi"},
    )
    assert result == PUSH_SKIPPED


# ---------------------------------------------------------------------------
# notify_event dispatch — end to end with a fake channel
# ---------------------------------------------------------------------------


class _FakeChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str, image_png: Any = None) -> None:
        self.sent.append(text)


class _BoomChannel:
    async def send(self, text: str, image_png: Any = None) -> None:
        raise RuntimeError("channel down")


@pytest.fixture
def stores():
    nstore = InMemoryNotificationStore()
    pstore = InMemoryPushStore()
    set_notification_store_for_tests(nstore)
    set_push_store_for_tests(pstore)
    try:
        yield nstore, pstore
    finally:
        set_notification_store_for_tests(None)
        set_push_store_for_tests(None)


@pytest.mark.asyncio
async def test_notify_event_writes_feed_and_fans_out(stores, monkeypatch):
    from app.notify import dispatch

    nstore, _ = stores
    fake = _FakeChannel()
    monkeypatch.setattr(
        dispatch, "_channels_for_org_lazy", lambda org_id: (lambda _o: [fake])
    )

    rows = await dispatch.notify_event(
        ORG,
        {"type": "watch_breach", "title": "Breached", "body": "details"},
    )
    # In-app broadcast row written.
    assert len(rows) == 1
    feed = await nstore.list_for_user(ORG, ALICE)
    assert any(n["title"] == "Breached" for n in feed)
    # Channel was sent the formatted text.
    assert fake.sent and "Breached" in fake.sent[0]


@pytest.mark.asyncio
async def test_notify_event_targeted_and_channel_failure_swallowed(stores, monkeypatch):
    from app.notify import dispatch

    nstore, _ = stores
    monkeypatch.setattr(
        dispatch, "_channels_for_org_lazy", lambda org_id: (lambda _o: [_BoomChannel()])
    )

    # Targeted: one row per user; a raising channel must NOT propagate.
    rows = await dispatch.notify_event(
        ORG, {"type": "system", "title": "Hi"}, user_ids=[ALICE, BOB]
    )
    assert len(rows) == 2
    alice_feed = await nstore.list_for_user(ORG, ALICE)
    bob_feed = await nstore.list_for_user(ORG, BOB)
    assert {n["title"] for n in alice_feed} == {"Hi"}
    assert {n["title"] for n in bob_feed} == {"Hi"}
    # Bob's targeted row is NOT visible to a third user.
    carol = str(uuid.uuid4())
    assert await nstore.list_for_user(ORG, carol) == []


@pytest.mark.asyncio
async def test_notify_event_no_channels_module(stores, monkeypatch):
    from app.notify import dispatch

    # channels_for_org unavailable → treated as no channels, still writes feed.
    monkeypatch.setattr(dispatch, "_channels_for_org_lazy", lambda org_id: None)
    rows = await dispatch.notify_event(ORG, {"type": "system", "title": "Solo"})
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def feed_client(app, fake_db):
    repo = InMemoryRepo()
    set_repo(repo)
    nstore = InMemoryNotificationStore()
    pstore = InMemoryPushStore()
    set_notification_store_for_tests(nstore)
    set_push_store_for_tests(pstore)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "feed@example.com",
        "name": "Feed User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, org_id, nstore, pstore

    set_repo(None)
    set_notification_store_for_tests(None)
    set_push_store_for_tests(None)


@pytest.mark.asyncio
async def test_feed_routes_list_count_read(feed_client):
    client, user_id, org_id, nstore, _ = feed_client
    headers = _auth(user_id)

    await nstore.create(org_id, type="system", title="Broadcast")
    targeted = await nstore.create(
        org_id, type="system", title="Yours", user_id=user_id
    )

    # List shows both.
    resp = await client.get("/api/v1/notifications", headers=headers)
    assert resp.status_code == 200, resp.text
    titles = {n["title"] for n in resp.json()["notifications"]}
    assert titles == {"Broadcast", "Yours"}

    # Unread badge = 2.
    resp = await client.get("/api/v1/notifications/unread_count", headers=headers)
    assert resp.json()["unread"] == 2

    # Mark the targeted one read.
    resp = await client.post(
        f"/api/v1/notifications/{targeted['id']}/read", headers=headers
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True
    resp = await client.get("/api/v1/notifications/unread_count", headers=headers)
    assert resp.json()["unread"] == 1

    # unread filter.
    resp = await client.get("/api/v1/notifications?unread=1", headers=headers)
    assert {n["title"] for n in resp.json()["notifications"]} == {"Broadcast"}

    # read_all clears.
    resp = await client.post("/api/v1/notifications/read_all", headers=headers)
    assert resp.status_code == 200 and resp.json()["unread"] == 0


@pytest.mark.asyncio
async def test_push_routes_subscribe_unsubscribe(feed_client):
    client, user_id, org_id, _, pstore = feed_client
    headers = _auth(user_id)

    # vapid_key returns None when unconfigured (still 200).
    resp = await client.get("/api/v1/push/vapid_key", headers=headers)
    assert resp.status_code == 200

    sub = {"endpoint": "https://push/ep-route", "keys": {"p256dh": "pk", "auth": "ak"}}
    resp = await client.post("/api/v1/push/subscribe", json=sub, headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(await pstore.list_for_users(org_id, [user_id])) == 1

    # Missing keys → 400.
    bad = await client.post(
        "/api/v1/push/subscribe", json={"endpoint": "https://x"}, headers=headers
    )
    assert bad.status_code == 400

    # Unsubscribe.
    resp = await client.post(
        "/api/v1/push/unsubscribe",
        json={"endpoint": "https://push/ep-route"},
        headers=headers,
    )
    assert resp.status_code == 200 and resp.json()["removed"] is True
    assert len(await pstore.list_for_users(org_id, [user_id])) == 0


@pytest.mark.asyncio
async def test_push_unsubscribe_idor_blocked(feed_client, fake_db):
    """A second user cannot unsubscribe (or hijack) the first user's endpoint."""
    client, alice_id, org_id, _, pstore = feed_client

    # Seed a second authenticated user (Bob) in the same org.
    bob_id = str(uuid.uuid4())
    fake_db.users[bob_id] = {
        "id": bob_id,
        "email": "bob@example.com",
        "name": "Bob",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    from app.repos.provider import get_repo

    get_repo().seed_org_member(org_id=org_id, user_id=bob_id)

    endpoint = "https://push/ep-idor"
    sub = {"endpoint": endpoint, "keys": {"p256dh": "pk", "auth": "ak"}}
    resp = await client.post(
        "/api/v1/push/subscribe", json=sub, headers=_auth(alice_id)
    )
    assert resp.status_code == 200, resp.text

    # Bob tries to unsubscribe Alice's endpoint → reports removed=False, Alice keeps it.
    resp = await client.post(
        "/api/v1/push/unsubscribe", json={"endpoint": endpoint}, headers=_auth(bob_id)
    )
    assert resp.status_code == 200 and resp.json()["removed"] is False
    assert len(await pstore.list_for_users(org_id, [alice_id])) == 1

    # Bob tries to hijack (re-subscribe) Alice's endpoint → 409 conflict.
    resp = await client.post(
        "/api/v1/push/subscribe", json=sub, headers=_auth(bob_id)
    )
    assert resp.status_code == 409, resp.text
    # Alice's subscription is untouched; Bob got nothing.
    assert len(await pstore.list_for_users(org_id, [alice_id])) == 1
    assert len(await pstore.list_for_users(org_id, [bob_id])) == 0
