"""Tests for per-org connected-integration CRUD + channels_for_org (Agent A).

Strategy (mirrors test_connectors_route.py)
-------------------------------------------
- ``InMemoryRepo`` via ``set_repo()`` for org membership / ``resolve_org_id``.
- ``InMemoryIntegrationStore`` via ``set_integration_store_for_tests()`` — real
  AES-256-GCM crypto (no DB), so secret-at-rest is genuinely exercised.
- A fresh in-process AES key is set in os.environ for the suite.
- Real JWTs via ``mint_access_token``; conftest patches the user lookup.

Coverage
--------
1.  POST /integrations → 201; secret split out of config + scrubbed from response.
2.  Secret encrypted at rest (ciphertext != plaintext) yet decrypts correctly.
3.  GET /integrations (list) → each item scrubbed + ``configured`` flag.
4.  GET /integrations/{id} → scrubbed; unknown → 404.
5.  PUT — update config + rotate secret.
6.  DELETE → 204 then GET → 404; secret gone.
7.  Cross-org GET/PUT/DELETE → 404 (no info leak).
8.  Invalid kind → 400.
9.  Auth required → 401.
10. POST /integrations/{id}/test — built-channel send (mock httpx) / incomplete.
11. channels_for_org builds the right channels + skips incomplete ones.
"""

from __future__ import annotations

import base64
import os
import secrets
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Self-register the integrations router on api_router at import time.
import app.routes.integrations  # noqa: F401

from app.notify.integrations import (
    InMemoryIntegrationStore,
    channels_for_org,
    get_integration_store,
    set_integration_store_for_tests,
    split_secret,
)


# ---------------------------------------------------------------------------
# Crypto key for the suite
# ---------------------------------------------------------------------------


def _ensure_key() -> None:
    from app.security.crypto import reset_keys_for_tests

    if not os.environ.get("CONNECTOR_SECRET_KEY"):
        os.environ["CONNECTOR_SECRET_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
        os.environ["CONNECTOR_SECRET_KEY_VERSION"] = "1"
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    reset_keys_for_tests()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET_FIELDS = {"webhook_url", "bot_token", "access_token", "url", "smtp_password"}


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _assert_no_secret(body: dict[str, Any]) -> None:
    """Assert no secret field leaks anywhere in the response (top-level or config)."""
    import json

    serialised = json.dumps(body)
    for field in _SECRET_FIELDS:
        assert f'"{field}"' not in serialised, f"SECRET LEAK: {field!r} in response"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def integrations_app(app):
    """FastAPI app with InMemoryRepo + InMemoryIntegrationStore injected."""
    _ensure_key()
    store = InMemoryIntegrationStore()
    set_integration_store_for_tests(store)

    repo = InMemoryRepo()
    set_repo(repo)

    yield app, repo, store

    set_repo(None)
    set_integration_store_for_tests(None)


@pytest_asyncio.fixture
async def client(integrations_app, fake_db):
    """Async client with a pre-seeded user + org."""
    app, repo, store = integrations_app

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as c:
        yield c, alice_id, alice_org_id, store


# ---------------------------------------------------------------------------
# CRUD + secret scrubbing
# ---------------------------------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_slack_scrubs_secret(self, client):
        c, alice_id, org_id, store = client
        resp = await c.post(
            "/api/v1/integrations",
            json={
                "kind": "slack",
                "name": "Data-ops Slack",
                "config": {"channel": "#alerts", "webhook_url": "https://hooks.slack.com/x"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["kind"] == "slack"
        assert body["name"] == "Data-ops Slack"
        assert body["configured"] is True
        # Non-secret config retained, secret stripped.
        assert body["config"].get("channel") == "#alerts"
        _assert_no_secret(body)

    @pytest.mark.asyncio
    async def test_secret_encrypted_at_rest_but_decrypts(self, client):
        c, alice_id, org_id, store = client
        resp = await c.post(
            "/api/v1/integrations",
            json={
                "kind": "google_chat",
                "name": "Eng space",
                "config": {"space": "Engineering", "webhook_url": "https://chat.googleapis.com/v1/x"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        integration_id = resp.json()["id"]

        # Ciphertext at rest must NOT be the plaintext webhook URL.
        blob = store._secrets[integration_id]
        assert b"chat.googleapis.com" not in blob["ciphertext"]
        # Yet the store decrypts it correctly.
        secret = await store.get_secret(integration_id, org_id)
        assert secret == {"webhook_url": "https://chat.googleapis.com/v1/x"}

        # And the non-secret config carries only the space label.
        row = await store.get(integration_id, org_id)
        assert row["config"] == {"space": "Engineering"}

    @pytest.mark.asyncio
    async def test_invalid_kind_400(self, client):
        c, alice_id, org_id, store = client
        resp = await c.post(
            "/api/v1/integrations",
            json={"kind": "fax", "name": "x", "config": {}},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text


class TestListGet:
    @pytest.mark.asyncio
    async def test_list_scrubbed_with_configured_flag(self, client):
        c, alice_id, org_id, store = client
        # One configured (teams), one not (slack with no secret).
        await c.post(
            "/api/v1/integrations",
            json={"kind": "teams", "name": "Ops", "config": {"webhook_url": "https://outlook.office.com/w"}},
            headers=_auth_headers(alice_id),
        )
        await c.post(
            "/api/v1/integrations",
            json={"kind": "slack", "name": "Empty", "config": {"channel": "#x"}},
            headers=_auth_headers(alice_id),
        )
        resp = await c.get("/api/v1/integrations", headers=_auth_headers(alice_id))
        assert resp.status_code == 200, resp.text
        items = resp.json()["integrations"]
        assert len(items) == 2
        by_name = {i["name"]: i for i in items}
        assert by_name["Ops"]["configured"] is True
        assert by_name["Empty"]["configured"] is False
        for item in items:
            _assert_no_secret(item)

    @pytest.mark.asyncio
    async def test_get_unknown_404(self, client):
        c, alice_id, org_id, store = client
        resp = await c.get(f"/api/v1/integrations/{uuid.uuid4()}", headers=_auth_headers(alice_id))
        assert resp.status_code == 404


class TestUpdateDelete:
    @pytest.mark.asyncio
    async def test_update_config_and_rotate_secret(self, client):
        c, alice_id, org_id, store = client
        create = await c.post(
            "/api/v1/integrations",
            json={"kind": "teams", "name": "Ops", "config": {"webhook_url": "https://old.example/w"}},
            headers=_auth_headers(alice_id),
        )
        iid = create.json()["id"]

        upd = await c.put(
            f"/api/v1/integrations/{iid}",
            json={"name": "Ops v2", "config": {"webhook_url": "https://new.example/w"}},
            headers=_auth_headers(alice_id),
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["name"] == "Ops v2"
        _assert_no_secret(upd.json())

        secret = await store.get_secret(iid, org_id)
        assert secret == {"webhook_url": "https://new.example/w"}

    @pytest.mark.asyncio
    async def test_delete_then_404(self, client):
        c, alice_id, org_id, store = client
        create = await c.post(
            "/api/v1/integrations",
            json={"kind": "teams", "name": "Ops", "config": {"webhook_url": "https://x.example/w"}},
            headers=_auth_headers(alice_id),
        )
        iid = create.json()["id"]

        d = await c.delete(f"/api/v1/integrations/{iid}", headers=_auth_headers(alice_id))
        assert d.status_code == 204
        g = await c.get(f"/api/v1/integrations/{iid}", headers=_auth_headers(alice_id))
        assert g.status_code == 404
        assert await store.get_secret(iid, org_id) is None


class TestCrossOrg:
    @pytest.mark.asyncio
    async def test_other_org_cannot_access(self, integrations_app, fake_db):
        app, repo, store = integrations_app

        alice_id, alice_org = str(uuid.uuid4()), str(uuid.uuid4())
        bob_id, bob_org = str(uuid.uuid4()), str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=False
        ) as c:
            create = await c.post(
                "/api/v1/integrations",
                json={"kind": "teams", "name": "A", "config": {"webhook_url": "https://a.example/w"}},
                headers=_auth_headers(alice_id),
            )
            iid = create.json()["id"]

            assert (await c.get(f"/api/v1/integrations/{iid}", headers=_auth_headers(bob_id))).status_code == 404
            assert (await c.put(
                f"/api/v1/integrations/{iid}", json={"name": "hijack"}, headers=_auth_headers(bob_id)
            )).status_code == 404
            assert (await c.delete(f"/api/v1/integrations/{iid}", headers=_auth_headers(bob_id))).status_code == 404
            # Alice's row survives.
            assert (await c.get(f"/api/v1/integrations/{iid}", headers=_auth_headers(alice_id))).status_code == 200


class TestAuthGuard:
    @pytest.mark.asyncio
    async def test_no_token_401(self, client):
        c, *_ = client
        assert (await c.get("/api/v1/integrations")).status_code == 401
        assert (await c.post("/api/v1/integrations", json={"kind": "teams", "name": "x"})).status_code == 401


class TestTestEndpoint:
    @pytest.mark.asyncio
    async def test_send_via_built_channel(self, client):
        c, alice_id, org_id, store = client
        create = await c.post(
            "/api/v1/integrations",
            json={"kind": "google_chat", "name": "Eng", "config": {"webhook_url": "https://chat.googleapis.com/v1/x"}},
            headers=_auth_headers(alice_id),
        )
        iid = create.json()["id"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            resp = await c.post(
                f"/api/v1/integrations/{iid}/test",
                json={"message": "ping"},
                headers=_auth_headers(alice_id),
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["sent"] is True
        mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_incomplete_reports_not_sent(self, client):
        c, alice_id, org_id, store = client
        # slack with no secret → not deliverable.
        create = await c.post(
            "/api/v1/integrations",
            json={"kind": "slack", "name": "Empty", "config": {"channel": "#x"}},
            headers=_auth_headers(alice_id),
        )
        iid = create.json()["id"]
        with patch("httpx.post") as mock_post:
            resp = await c.post(
                f"/api/v1/integrations/{iid}/test", json={}, headers=_auth_headers(alice_id)
            )
        assert resp.status_code == 200
        assert resp.json()["sent"] is False
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# channels_for_org
# ---------------------------------------------------------------------------


class TestChannelsForOrg:
    @pytest.mark.asyncio
    async def test_builds_enabled_skips_incomplete_and_disabled(self):
        _ensure_key()
        store = InMemoryIntegrationStore()
        set_integration_store_for_tests(store)
        org_id = str(uuid.uuid4())
        try:
            # 1. complete google_chat (enabled) → built.
            cfg, sec = split_secret("google_chat", {"space": "Eng", "webhook_url": "https://chat.googleapis.com/v1/x"})
            await store.create(org_id=org_id, created_by="u", kind="google_chat", name="Eng",
                               config=cfg, secret=sec, enabled=True)
            # 2. complete teams but DISABLED → skipped.
            cfg, sec = split_secret("teams", {"webhook_url": "https://outlook.office.com/w"})
            await store.create(org_id=org_id, created_by="u", kind="teams", name="Off",
                               config=cfg, secret=sec, enabled=False)
            # 3. slack with NO secret (incomplete) → skipped.
            cfg, sec = split_secret("slack", {"channel": "#x"})
            await store.create(org_id=org_id, created_by="u", kind="slack", name="Empty",
                               config=cfg, secret=sec, enabled=True)

            from app.notify.channels import GoogleChatChannel

            channels = await channels_for_org(org_id)
            assert len(channels) == 1
            assert isinstance(channels[0], GoogleChatChannel)
            assert channels[0].webhook_url == "https://chat.googleapis.com/v1/x"
        finally:
            set_integration_store_for_tests(None)

    @pytest.mark.asyncio
    async def test_whatsapp_field_mapping(self):
        _ensure_key()
        store = InMemoryIntegrationStore()
        set_integration_store_for_tests(store)
        org_id = str(uuid.uuid4())
        try:
            cfg, sec = split_secret("whatsapp", {
                "phone_number_id": "pid", "to": "+27821234567", "access_token": "tok",
            })
            assert sec == {"access_token": "tok"}
            await store.create(org_id=org_id, created_by="u", kind="whatsapp", name="WA",
                               config=cfg, secret=sec, enabled=True)

            from app.notify.channels import WhatsAppChannel

            channels = await channels_for_org(org_id)
            assert len(channels) == 1
            ch = channels[0]
            assert isinstance(ch, WhatsAppChannel)
            assert ch.token == "tok"
            assert ch.phone_number_id == "pid"
            assert ch.recipient == "+27821234567"
        finally:
            set_integration_store_for_tests(None)

    @pytest.mark.asyncio
    async def test_empty_org_returns_empty(self):
        _ensure_key()
        set_integration_store_for_tests(InMemoryIntegrationStore())
        try:
            assert await channels_for_org(str(uuid.uuid4())) == []
        finally:
            set_integration_store_for_tests(None)
