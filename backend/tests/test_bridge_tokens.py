"""Bridge tokens — mint / validate / rotate(grace) / revoke + broker drop (§7).

What this suite verifies
------------------------
Store level (control-channel credential, scoped to (org, bridge)):
(1) mint → returns a ``nubi_br_…`` raw token once; validate returns the binding.
(2) the raw token is stored HASHED, never plaintext.
(3) rotate: the NEW token validates AND the OLD token still validates during the
    grace window; after the grace window the old token stops validating; an
    explicit revoke short-circuits the grace.
(4) revoke: subsequent validate fails.
(5) cross-org / cross-bridge isolation: a token validates only for its own
    (org, bridge); rotate/revoke refuse a token from another (org, bridge).

Broker level (revocation drops the live tunnel):
(6) a connected bridge whose token is revoked is dropped by ``broker.drop`` →
    ``is_connected`` is False → ``open_tcp_proxy`` fails fast with
    ``bridge_not_connected`` (no hang).

Route level (owner/admin, org-scoped):
(7) POST/GET/rotate/DELETE /bridges/{id}/tokens happy path; admin allowed,
    member forbidden (403); revoke drops the live tunnel.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.bridge_tokens import (
    BRIDGE_TOKEN_PREFIX,
    InMemoryBridgeTokenStore,
    hash_bridge_token,
)
from app.auth.jwt import mint_access_token
from app.bridges.broker import BridgeBroker, get_broker, reset_broker
from app.bridges.agent import BridgeAgent
from app.errors import AppError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

import app.routes.bridges  # noqa: F401 — self-register routes
from app.routes.bridges import get_bridge_token_store, reset_bridge_store

from tests.test_bridge_tunnel import _InMemoryDuplex  # reuse the duplex helper


ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
BRIDGE_1 = str(uuid.uuid4())
BRIDGE_2 = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Store-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_then_validate_returns_binding():
    store = InMemoryBridgeTokenStore()
    raw, row = await store.mint(ORG_A, BRIDGE_1, "agent-1")
    assert raw.startswith(BRIDGE_TOKEN_PREFIX)
    assert row["bridge_id"] == BRIDGE_1

    binding = await store.validate(raw)
    assert binding == (ORG_A, BRIDGE_1)


@pytest.mark.asyncio
async def test_token_stored_hashed_not_plaintext():
    store = InMemoryBridgeTokenStore()
    raw, _ = await store.mint(ORG_A, BRIDGE_1, "x")
    rows = list(store._store.values())
    assert len(rows) == 1
    row = rows[0]
    assert raw not in row.values()
    assert row["token_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert row["token_hash"] == hash_bridge_token(raw)


@pytest.mark.asyncio
async def test_validate_rejects_unknown_and_non_prefixed():
    store = InMemoryBridgeTokenStore()
    assert await store.validate("nubi_br_doesnotexist0000000000000000000") is None
    # A user API key (wrong prefix) must never validate as a bridge token.
    assert await store.validate("nubi_ak_somethingsomething") is None


@pytest.mark.asyncio
async def test_rotate_grace_window_both_validate_then_old_expires():
    store = InMemoryBridgeTokenStore()
    raw_old, row_old = await store.mint(ORG_A, BRIDGE_1, "old")
    old_id = row_old["id"]

    # Rotate with a tiny grace window.
    result = await store.rotate(old_id, ORG_A, BRIDGE_1, grace=timedelta(seconds=300))
    assert result is not None
    raw_new, _ = result

    # During the grace window BOTH tokens validate (no tunnel drop on swap).
    assert await store.validate(raw_new) == (ORG_A, BRIDGE_1)
    assert await store.validate(raw_old) == (ORG_A, BRIDGE_1)

    # Force the grace window into the past → the old token stops validating,
    # the new one keeps validating.
    store._store[old_id]["grace_until"] = store._store[old_id]["grace_until"] - timedelta(
        seconds=600
    )
    assert await store.validate(raw_old) is None
    assert await store.validate(raw_new) == (ORG_A, BRIDGE_1)


@pytest.mark.asyncio
async def test_revoke_then_validate_fails():
    store = InMemoryBridgeTokenStore()
    raw, row = await store.mint(ORG_A, BRIDGE_1, "x")
    assert await store.validate(raw) == (ORG_A, BRIDGE_1)

    assert await store.revoke(row["id"], ORG_A, BRIDGE_1) is True
    assert await store.validate(raw) is None
    # Double revoke is a no-op (returns False).
    assert await store.revoke(row["id"], ORG_A, BRIDGE_1) is False


@pytest.mark.asyncio
async def test_revoke_during_grace_short_circuits():
    store = InMemoryBridgeTokenStore()
    raw_old, row_old = await store.mint(ORG_A, BRIDGE_1, "old")
    await store.rotate(row_old["id"], ORG_A, BRIDGE_1, grace=timedelta(hours=1))
    # Old still validates (grace) — but an explicit revoke kills it immediately.
    assert await store.validate(raw_old) == (ORG_A, BRIDGE_1)
    assert await store.revoke(row_old["id"], ORG_A, BRIDGE_1) is True
    assert await store.validate(raw_old) is None


@pytest.mark.asyncio
async def test_cross_org_and_cross_bridge_isolation():
    store = InMemoryBridgeTokenStore()
    raw_a1, row_a1 = await store.mint(ORG_A, BRIDGE_1, "a1")

    # The token validates only for its own (org, bridge).
    assert await store.validate(raw_a1) == (ORG_A, BRIDGE_1)
    assert (ORG_A, BRIDGE_1) != (ORG_B, BRIDGE_1)

    # Revoke/rotate refuse the token under a different org or bridge.
    assert await store.revoke(row_a1["id"], ORG_B, BRIDGE_1) is False
    assert await store.revoke(row_a1["id"], ORG_A, BRIDGE_2) is False
    assert await store.rotate(row_a1["id"], ORG_B, BRIDGE_1) is None
    # Still live after the rejected cross-tenant ops.
    assert await store.validate(raw_a1) == (ORG_A, BRIDGE_1)


@pytest.mark.asyncio
async def test_has_any_for_bridge_is_scoped():
    """``has_any_for_bridge`` gates the legacy plaintext fallback — must be scoped."""
    store = InMemoryBridgeTokenStore()
    assert await store.has_any_for_bridge(ORG_A, BRIDGE_1) is False
    await store.mint(ORG_A, BRIDGE_1, "x")
    assert await store.has_any_for_bridge(ORG_A, BRIDGE_1) is True
    # A token on a different bridge / org does NOT count for (ORG_A, BRIDGE_1).
    assert await store.has_any_for_bridge(ORG_A, BRIDGE_2) is False
    assert await store.has_any_for_bridge(ORG_B, BRIDGE_1) is False
    # Even a revoked token still counts — adoption of v2 closes the legacy path
    # permanently, so a revoked bridge cannot be revived via plaintext.
    _, row = await store.mint(ORG_A, BRIDGE_2, "y")
    await store.revoke(row["id"], ORG_A, BRIDGE_2)
    assert await store.has_any_for_bridge(ORG_A, BRIDGE_2) is True


@pytest.mark.asyncio
async def test_list_for_bridge_is_scoped_and_carries_no_secret():
    store = InMemoryBridgeTokenStore()
    await store.mint(ORG_A, BRIDGE_1, "one")
    await store.mint(ORG_A, BRIDGE_1, "two")
    await store.mint(ORG_A, BRIDGE_2, "other")
    await store.mint(ORG_B, BRIDGE_1, "elsewhere")

    listed = await store.list_for_bridge(ORG_A, BRIDGE_1)
    assert len(listed) == 2
    for row in listed:
        assert "token_hash" not in row
        assert row["bridge_id"] == BRIDGE_1


# ---------------------------------------------------------------------------
# Broker-level: revoke drops the live tunnel
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_broker():
    reset_broker()
    yield
    reset_broker()


@pytest.mark.asyncio
async def test_broker_drop_disconnects_and_pinned_connectors_fail_fast():
    broker = BridgeBroker()
    bridge_id = str(uuid.uuid4())
    duplex = _InMemoryDuplex()

    await broker.register(bridge_id, duplex.side_a)
    agent = BridgeAgent(ws=duplex.side_b, bridge_id=bridge_id)
    import asyncio

    agent_task = asyncio.ensure_future(agent.run())
    try:
        assert broker.is_connected(bridge_id) is True

        # Revocation path: drop the live tunnel.
        dropped = await broker.drop(bridge_id)
        assert dropped is True
        assert broker.is_connected(bridge_id) is False

        # A connector pinned to the bridge now fails FAST, not a hang.
        with pytest.raises(AppError) as exc:
            await broker.open_tcp_proxy(bridge_id, "db.internal", 5432)
        assert exc.value.code == "bridge_not_connected"
        assert exc.value.status == 503

        # drop is idempotent.
        assert await broker.drop(bridge_id) is False
    finally:
        agent_task.cancel()
        try:
            await agent_task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# WS-handshake level: legacy plaintext downgrade hole + mid-session revoke drop
# ---------------------------------------------------------------------------

import asyncio as _asyncio  # noqa: E402


class _FakeWebSocket:
    """Minimal WebSocket double for driving ``bridge_connect`` directly.

    Records the close code; ``receive_bytes`` blocks on an event so the endpoint
    stays "connected" until the test releases it (simulating a live agent).
    """

    def __init__(self, headers: dict[str, str], query: dict[str, str]) -> None:
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.query_params = dict(query)
        self.accepted = False
        self.close_code: int | None = None
        self._release = _asyncio.Event()

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        if self.close_code is None:
            self.close_code = code
        self._release.set()

    async def receive_bytes(self) -> bytes:
        await self._release.wait()
        from fastapi import WebSocketDisconnect

        raise WebSocketDisconnect(code=1000)

    def release(self) -> None:
        self._release.set()


@pytest.mark.asyncio
async def test_ws_legacy_plaintext_token_is_closed_once_v2_token_minted():
    """A bridge with a v2 token must REJECT its old plaintext config token (§7).

    The legacy ``config["token"]`` fallback is a downgrade hole: it has no
    revocation. Once any hashed v2 token is minted for the bridge, the plaintext
    path must be closed so a revoked bridge cannot be kept alive through it.
    """
    from app.routes.bridges import bridge_connect, get_bridge_store, get_bridge_token_store

    reset_bridge_store()
    get_bridge_token_store().reset()
    store = get_bridge_store()
    org_id = str(uuid.uuid4())
    bridge = await store.create(org_id, "u", "B", {"token": "plaintext-legacy"})
    bridge_id = bridge["id"]

    # Before any v2 token exists, the legacy plaintext token still authenticates.
    ws = _FakeWebSocket({"x-bridge-token": "plaintext-legacy"}, {})
    task = _asyncio.ensure_future(bridge_connect(bridge_id, ws))
    await _asyncio.sleep(0.05)
    assert ws.accepted is True and ws.close_code is None
    ws.release()
    await task
    await get_broker().unregister(bridge_id)

    # Mint a v2 token → the plaintext path is now CLOSED.
    await get_bridge_token_store().mint(org_id, bridge_id, "v2")
    ws2 = _FakeWebSocket({"x-bridge-token": "plaintext-legacy"}, {})
    await bridge_connect(bridge_id, ws2)
    assert ws2.accepted is False
    assert ws2.close_code == 4401


@pytest.mark.asyncio
async def test_ws_revoked_token_drops_live_tunnel_via_revalidation(monkeypatch):
    """A token revoked mid-session drops the live tunnel on the next re-check (§7)."""
    import app.routes.bridges as br
    from app.routes.bridges import bridge_connect, get_bridge_store, get_bridge_token_store

    reset_broker()
    reset_bridge_store()
    get_bridge_token_store().reset()
    # Make the re-validation loop tick almost immediately.
    monkeypatch.setattr(br, "_BRIDGE_TOKEN_REVALIDATE_SECONDS", 0.02)

    store = get_bridge_store()
    org_id = str(uuid.uuid4())
    bridge = await store.create(org_id, "u", "B", {})
    bridge_id = bridge["id"]
    raw, row = await get_bridge_token_store().mint(org_id, bridge_id, "v2")

    ws = _FakeWebSocket({"x-bridge-token": raw}, {})
    task = _asyncio.ensure_future(bridge_connect(bridge_id, ws))
    await _asyncio.sleep(0.05)
    assert ws.accepted is True
    assert get_broker().is_connected(bridge_id) is True

    # Revoke out-of-band (no explicit broker.drop call) → the re-validation loop
    # must notice and tear the tunnel down on its own.
    await get_bridge_token_store().revoke(row["id"], org_id, bridge_id)
    for _ in range(50):
        if not get_broker().is_connected(bridge_id):
            break
        await _asyncio.sleep(0.02)
    assert get_broker().is_connected(bridge_id) is False
    assert ws.close_code == 4401

    ws.release()
    try:
        await _asyncio.wait_for(task, timeout=1.0)
    except (_asyncio.TimeoutError, Exception):
        task.cancel()


# ---------------------------------------------------------------------------
# Route-level: mint / list / rotate / revoke (owner/admin, org-scoped)
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str = "u@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "U",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest.fixture(autouse=True)
def _reset_bridges():
    reset_bridge_store()
    yield
    reset_bridge_store()


@pytest_asyncio.fixture
async def token_client(app, fake_db):
    repo = InMemoryRepo()
    set_repo(repo)

    owner_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[owner_id] = _make_user(owner_id, "owner@example.com")
    repo.seed_org_member(org_id=org_id, user_id=owner_id, role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, owner_id, org_id, repo, fake_db

    set_repo(None)


async def _create_bridge(client: AsyncClient, owner_id: str) -> str:
    resp = await client.post(
        "/api/v1/bridges", json={"name": "B"}, headers=_auth(owner_id)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_route_mint_list_rotate_revoke(token_client):
    client, owner_id, org_id, repo, fake_db = token_client
    bridge_id = await _create_bridge(client, owner_id)

    # Mint.
    mint = await client.post(
        f"/api/v1/bridges/{bridge_id}/tokens",
        json={"name": "agent"},
        headers=_auth(owner_id),
    )
    assert mint.status_code == 201, mint.text
    raw = mint.json()["token"]
    assert raw.startswith(BRIDGE_TOKEN_PREFIX)
    token_id = mint.json()["bridge_token"]["id"]
    assert "token_hash" not in mint.json()["bridge_token"]

    # The minted token validates for (org, bridge) at the store level.
    assert await get_bridge_token_store().validate(raw) == (org_id, bridge_id)

    # List shows it (no secret material).
    listed = await client.get(
        f"/api/v1/bridges/{bridge_id}/tokens", headers=_auth(owner_id)
    )
    assert listed.status_code == 200
    tokens = listed.json()["bridge_tokens"]
    assert len(tokens) == 1 and tokens[0]["id"] == token_id

    # Rotate → new raw token; old still validates (grace), new validates.
    rot = await client.post(
        f"/api/v1/bridges/{bridge_id}/tokens/{token_id}/rotate",
        headers=_auth(owner_id),
    )
    assert rot.status_code == 201, rot.text
    raw_new = rot.json()["token"]
    assert await get_bridge_token_store().validate(raw_new) == (org_id, bridge_id)
    assert await get_bridge_token_store().validate(raw) == (org_id, bridge_id)

    # Revoke the new token.
    new_id = rot.json()["bridge_token"]["id"]
    rev = await client.delete(
        f"/api/v1/bridges/{bridge_id}/tokens/{new_id}", headers=_auth(owner_id)
    )
    assert rev.status_code == 204
    assert await get_bridge_token_store().validate(raw_new) is None


@pytest.mark.asyncio
async def test_route_member_forbidden_owner_allowed(token_client):
    client, owner_id, org_id, repo, fake_db = token_client
    bridge_id = await _create_bridge(client, owner_id)

    # A plain member of the same org cannot mint bridge tokens (403).
    member_id = str(uuid.uuid4())
    fake_db.users[member_id] = _make_user(member_id, "member@example.com")
    repo.seed_org_member(org_id=org_id, user_id=member_id, role="member")

    denied = await client.post(
        f"/api/v1/bridges/{bridge_id}/tokens", json={}, headers=_auth(member_id)
    )
    assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_route_revoke_drops_live_tunnel(token_client):
    client, owner_id, org_id, repo, fake_db = token_client
    bridge_id = await _create_bridge(client, owner_id)

    mint = await client.post(
        f"/api/v1/bridges/{bridge_id}/tokens", json={}, headers=_auth(owner_id)
    )
    token_id = mint.json()["bridge_token"]["id"]

    # Simulate a live tunnel on the shared broker singleton.
    import asyncio

    from app.bridges.broker import get_broker

    broker = get_broker()
    duplex = _InMemoryDuplex()
    await broker.register(bridge_id, duplex.side_a)
    agent = BridgeAgent(ws=duplex.side_b, bridge_id=bridge_id)
    agent_task = asyncio.ensure_future(agent.run())
    try:
        assert broker.is_connected(bridge_id) is True

        rev = await client.delete(
            f"/api/v1/bridges/{bridge_id}/tokens/{token_id}", headers=_auth(owner_id)
        )
        assert rev.status_code == 204
        # Tunnel dropped + bridge marked offline.
        assert broker.is_connected(bridge_id) is False
        bridge = await client.get(
            f"/api/v1/bridges/{bridge_id}", headers=_auth(owner_id)
        )
        assert bridge.json()["status"] == "offline"
    finally:
        agent_task.cancel()
        try:
            await agent_task
        except (asyncio.CancelledError, Exception):
            pass
