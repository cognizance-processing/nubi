"""Tests for per-org connected-integration CRUD + channels_for_org (Agent A).

Nubi ships EMAIL as its one connected-integration kind (Nubi is embedded BI,
not a chat-ops platform — the embedding host owns Slack/Teams/etc.
notifications), so all fixtures below exercise the ``email`` kind. Secret
material for email is optional (SMTP overrides); the CRUD/secret-at-rest
mechanics themselves are kind-agnostic and remain fully exercised.

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
8.  Invalid kind → 400 (including a removed chat kind, e.g. 'slack').
9.  Auth required → 401.
10. POST /integrations/{id}/test — built-channel send / incomplete.
11. channels_for_org builds the right channels, skips disabled + legacy
    unsupported kinds (regression guard for the chat-channel removal).
"""

from __future__ import annotations

import base64
import os
import secrets
import uuid
from typing import Any

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

_SECRET_FIELDS = {"smtp_password", "smtp_user", "smtp_host", "smtp_port"}


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
    async def test_create_email_scrubs_secret(self, client):
        c, alice_id, org_id, store = client
        resp = await c.post(
            "/api/v1/integrations",
            json={
                "kind": "email",
                "name": "Ops Alerts",
                "config": {"recipients": ["ops@acme.com"], "smtp_password": "hunter2"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["kind"] == "email"
        assert body["name"] == "Ops Alerts"
        assert body["configured"] is True
        # Non-secret config retained, secret stripped.
        assert body["config"].get("recipients") == ["ops@acme.com"]
        _assert_no_secret(body)

    @pytest.mark.asyncio
    async def test_secret_encrypted_at_rest_but_decrypts(self, client):
        c, alice_id, org_id, store = client
        resp = await c.post(
            "/api/v1/integrations",
            json={
                "kind": "email",
                "name": "Ops SMTP override",
                "config": {
                    "recipients": ["ops@acme.com"],
                    "smtp_password": "hunter2",
                    "smtp_host": "smtp.acme.com",
                },
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        integration_id = resp.json()["id"]

        # Ciphertext at rest must NOT be the plaintext password.
        blob = store._secrets[integration_id]
        assert b"hunter2" not in blob["ciphertext"]
        # Yet the store decrypts it correctly.
        secret = await store.get_secret(integration_id, org_id)
        assert secret == {"smtp_password": "hunter2", "smtp_host": "smtp.acme.com"}

        # And the non-secret config carries only the recipients.
        row = await store.get(integration_id, org_id)
        assert row["config"] == {"recipients": ["ops@acme.com"]}

    @pytest.mark.asyncio
    async def test_invalid_kind_400(self, client):
        c, alice_id, org_id, store = client
        resp = await c.post(
            "/api/v1/integrations",
            json={"kind": "fax", "name": "x", "config": {}},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text

    @pytest.mark.asyncio
    async def test_removed_chat_kind_400(self, client):
        """Slack (and friends) were removed — no longer a valid integration kind."""
        c, alice_id, org_id, store = client
        for kind in ("slack", "whatsapp", "teams", "google_chat", "webhook"):
            resp = await c.post(
                "/api/v1/integrations",
                json={"kind": kind, "name": "x", "config": {"webhook_url": "https://example.com/hook"}},
                headers=_auth_headers(alice_id),
            )
            assert resp.status_code == 400, f"kind={kind!r} should be rejected: {resp.text}"


class TestListGet:
    @pytest.mark.asyncio
    async def test_list_scrubbed_with_configured_flag(self, client):
        c, alice_id, org_id, store = client
        await c.post(
            "/api/v1/integrations",
            json={"kind": "email", "name": "Ops", "config": {"recipients": ["ops@acme.com"]}},
            headers=_auth_headers(alice_id),
        )
        await c.post(
            "/api/v1/integrations",
            json={"kind": "email", "name": "Backup", "config": {"recipients": ["backup@acme.com"]}},
            headers=_auth_headers(alice_id),
        )
        resp = await c.get("/api/v1/integrations", headers=_auth_headers(alice_id))
        assert resp.status_code == 200, resp.text
        items = resp.json()["integrations"]
        assert len(items) == 2
        by_name = {i["name"]: i for i in items}
        # Email integrations need no secret to be usable — always "configured".
        assert by_name["Ops"]["configured"] is True
        assert by_name["Backup"]["configured"] is True
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
            json={
                "kind": "email",
                "name": "Ops",
                "config": {"recipients": ["ops@acme.com"], "smtp_password": "old-pw"},
            },
            headers=_auth_headers(alice_id),
        )
        iid = create.json()["id"]

        upd = await c.put(
            f"/api/v1/integrations/{iid}",
            json={"name": "Ops v2", "config": {"smtp_password": "new-pw"}},
            headers=_auth_headers(alice_id),
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["name"] == "Ops v2"
        _assert_no_secret(upd.json())

        secret = await store.get_secret(iid, org_id)
        assert secret == {"smtp_password": "new-pw"}

    @pytest.mark.asyncio
    async def test_delete_then_404(self, client):
        c, alice_id, org_id, store = client
        create = await c.post(
            "/api/v1/integrations",
            json={"kind": "email", "name": "Ops", "config": {"recipients": ["ops@acme.com"]}},
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
                json={"kind": "email", "name": "A", "config": {"recipients": ["a@acme.com"]}},
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
        assert (await c.post("/api/v1/integrations", json={"kind": "email", "name": "x"})).status_code == 401


class TestTestEndpoint:
    @pytest.mark.asyncio
    async def test_send_via_built_channel(self, client):
        c, alice_id, org_id, store = client
        create = await c.post(
            "/api/v1/integrations",
            json={"kind": "email", "name": "Ops", "config": {"recipients": ["ops@acme.com"]}},
            headers=_auth_headers(alice_id),
        )
        iid = create.json()["id"]

        resp = await c.post(
            f"/api/v1/integrations/{iid}/test",
            json={"message": "ping"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["sent"] is True
        assert body["kind"] == "email"

    @pytest.mark.asyncio
    async def test_incomplete_reports_not_sent(self, client):
        """A NullChannel-resolving integration (incomplete/unsupported) → not sent."""
        from unittest.mock import patch

        c, alice_id, org_id, store = client
        create = await c.post(
            "/api/v1/integrations",
            json={"kind": "email", "name": "Empty", "config": {}},
            headers=_auth_headers(alice_id),
        )
        iid = create.json()["id"]

        from app.notify.channels import NullChannel

        with patch("app.notify.channels.get_channel", return_value=NullChannel()):
            resp = await c.post(
                f"/api/v1/integrations/{iid}/test", json={}, headers=_auth_headers(alice_id)
            )
        assert resp.status_code == 200
        assert resp.json()["sent"] is False


# ---------------------------------------------------------------------------
# channels_for_org
# ---------------------------------------------------------------------------


class TestChannelsForOrg:
    @pytest.mark.asyncio
    async def test_builds_enabled_skips_disabled(self):
        _ensure_key()
        store = InMemoryIntegrationStore()
        set_integration_store_for_tests(store)
        org_id = str(uuid.uuid4())
        try:
            # 1. complete email (enabled) → built.
            cfg, sec = split_secret("email", {"recipients": ["ops@acme.com"]})
            await store.create(org_id=org_id, created_by="u", kind="email", name="Ops",
                               config=cfg, secret=sec, enabled=True)
            # 2. complete email but DISABLED → skipped.
            cfg, sec = split_secret("email", {"recipients": ["off@acme.com"]})
            await store.create(org_id=org_id, created_by="u", kind="email", name="Off",
                               config=cfg, secret=sec, enabled=False)

            from app.notify.channels import EmailChannel

            channels = await channels_for_org(org_id)
            assert len(channels) == 1
            assert isinstance(channels[0], EmailChannel)
            assert channels[0].recipient == "ops@acme.com"
        finally:
            set_integration_store_for_tests(None)

    @pytest.mark.asyncio
    async def test_legacy_unsupported_kind_is_skipped(self):
        """A pre-existing row with a now-removed kind (e.g. 'slack') is inert.

        Regression guard for the chat-channel removal: channels_for_org must
        not resolve/crash on rows whose kind fell out of VALID_KINDS.
        """
        _ensure_key()
        store = InMemoryIntegrationStore()
        set_integration_store_for_tests(store)
        org_id = str(uuid.uuid4())
        try:
            # Simulate a legacy row (bypasses route-layer kind validation, as a
            # pre-migration row created before Slack support was removed would).
            await store.create(
                org_id=org_id, created_by="u", kind="slack", name="Legacy Slack",
                config={"channel": "#alerts"}, secret={"webhook_url": "https://hooks.slack.com/x"},
                enabled=True,
            )

            channels = await channels_for_org(org_id)
            assert channels == []
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
