"""Tests for the connector create/update API (connectors.py).

Strategy
--------
- Use ``InMemoryRepo`` injected via ``set_repo()`` — no live DB required.
- Use ``InMemorySecretStore`` injected via ``_inject_secret_store()`` —
  no encryption key needed (the in-memory store stores plaintext for tests).
- Seed org memberships on the repo directly (``repo.seed_org_member()``).
- Generate real JWTs via ``mint_access_token``; the conftest patches
  ``app.auth.deps.fetchrow`` so the user lookup resolves against ``FakeDB``.
- Import ``app.routes.connectors`` explicitly so it self-registers on the
  shared ``api_router`` before the app is constructed.

Coverage
--------
1.  create_connector → 201, config has NO password (secret not in config)
2.  secret is retrievable from the store after create
3.  GET /connectors/{id} returns the connector, no secret material
4.  GET /connectors (list) returns only connectors, no secret material
5.  update_connector — rotate secret; new secret retrievable, old gone
6.  update_connector — update non-secret config
7.  delete_connector → 204; GET → 404; secret removed from store
8.  org-scoping: org B cannot GET org A's connector (404)
9.  auth required: no token → 401
10. POST /connectors/{id}/test — ok path returns {ok: True}
11. POST /connectors/{id}/test — config missing → ok: False
12. POST /connectors/{id}/test — secret missing → ok: False, correct layer reported
13. create_connector raises 422 if secret keys appear in config
14. list excludes datastores that are not connectors
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Ensure the connectors router is registered on api_router at import time.
import app.routes.connectors  # noqa: F401


# ---------------------------------------------------------------------------
# In-memory secret store (no crypto — plaintext for testing)
# ---------------------------------------------------------------------------


class InMemorySecretStore:
    """Plaintext in-memory substitute for the real AES-256-GCM SecretStore.

    The real ``get_secret_store()`` encrypts; this keeps secrets in a dict
    keyed by ``(datastore_id, org_id)`` for fast test inspection.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], dict[str, Any]] = {}

    async def put(self, datastore_id: str, org_id: str, secret: dict[str, Any]) -> None:
        self._store[(str(datastore_id), str(org_id))] = dict(secret)

    async def get(self, datastore_id: str, org_id: str) -> dict[str, Any]:
        key = (str(datastore_id), str(org_id))
        if key not in self._store:
            raise KeyError(f"Secret not found for {datastore_id!r}")
        return dict(self._store[key])

    async def delete(self, datastore_id: str, org_id: str) -> None:
        key = (str(datastore_id), str(org_id))
        self._store.pop(key, None)

    def has(self, datastore_id: str, org_id: str) -> bool:
        return (str(datastore_id), str(org_id)) in self._store

    def get_raw(self, datastore_id: str, org_id: str) -> dict[str, Any] | None:
        return self._store.get((str(datastore_id), str(org_id)))

    def reset(self) -> None:
        self._store.clear()


# Module-level singleton reused across tests
_secret_store = InMemorySecretStore()


def _make_get_secret_store():
    """Return a factory that always yields the module-level InMemorySecretStore."""
    def _get():
        return _secret_store
    return _get


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


# Secret-key names that must NEVER appear in API responses
_SECRET_KEYS = {"password", "service_account_json", "token", "api_key"}


def _assert_no_secret_in_response(body: dict[str, Any]) -> None:
    """Assert the response dict is free of secret material."""
    # Check top-level keys
    top_secrets = _SECRET_KEYS & set(body.keys())
    assert not top_secrets, f"Secret key(s) {top_secrets!r} found at top level of response"

    # Check inside config
    config = body.get("config")
    if isinstance(config, dict):
        config_secrets = _SECRET_KEYS & set(config.keys())
        assert not config_secrets, f"Secret key(s) {config_secrets!r} found in config of response"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def connectors_app(app):
    """FastAPI app with InMemoryRepo + InMemorySecretStore injected."""
    _secret_store.reset()

    repo = InMemoryRepo()
    set_repo(repo)

    # Patch the _secret_store function in the connectors route module
    with patch(
        "app.routes.connectors._secret_store",
        side_effect=_make_get_secret_store(),
    ):
        yield app, repo

    set_repo(None)
    _secret_store.reset()


@pytest_asyncio.fixture
async def connectors_client(connectors_app, fake_db):
    """Async HTTPX client with InMemoryRepo + InMemorySecretStore, pre-seeded user + org."""
    app, repo = connectors_app

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(user_id=alice_id, email="alice@example.com")

    fake_db.users[alice_id] = alice
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, alice_id, alice_org_id, repo


# ---------------------------------------------------------------------------
# Tests: create
# ---------------------------------------------------------------------------


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_create_postgres_returns_201(self, connectors_client):
        """POST /connectors with a postgres connector returns 201."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "prod-postgres",
                "type": "postgres",
                "config": {
                    "host": "db.example.com",
                    "port": 5432,
                    "database": "analytics",
                    "user": "readonly",
                    "sslmode": "require",
                },
                "secret": {"password": "s3cr3t!"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "prod-postgres"
        assert "id" in body
        assert body["org_id"] == org_id

    @pytest.mark.asyncio
    async def test_password_never_stored_in_config(self, connectors_client):
        """CRITICAL: datastore.config must NOT contain the password after create."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "no-secret-config",
                "type": "postgres",
                "config": {"host": "localhost", "port": 5432, "database": "test", "user": "u"},
                "secret": {"password": "topsecret"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        row_id = resp.json()["id"]

        # Inspect the repo directly — the stored config must not contain the password
        stored_row = await repo.get("datastores", org_id, row_id)
        assert stored_row is not None
        config = stored_row.get("config", {})
        assert "password" in _SECRET_KEYS  # sanity
        for key in _SECRET_KEYS:
            assert key not in config, (
                f"SECRET LEAK: key {key!r} found in datastores.config — "
                "secrets must only live in the encrypted secret store"
            )

    @pytest.mark.asyncio
    async def test_secret_is_retrievable_from_store(self, connectors_client):
        """After create, the secret is readable from the InMemorySecretStore."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "retrieve-test",
                "type": "postgres",
                "config": {"host": "db.local", "port": 5432, "database": "mydb", "user": "me"},
                "secret": {"password": "my-password"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        row_id = resp.json()["id"]

        # Secret must be retrievable (decrypted) from the store
        secret = await _secret_store.get(row_id, org_id)
        assert secret.get("password") == "my-password"

    @pytest.mark.asyncio
    async def test_create_response_has_no_secret(self, connectors_client):
        """The create response must never include secret material."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "secret-free-response",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "hunter2"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        _assert_no_secret_in_response(resp.json())

    @pytest.mark.asyncio
    async def test_create_bigquery_with_service_account_json(self, connectors_client):
        """BigQuery connector with service_account_json in secret (not config)."""
        client, alice_id, org_id, repo = connectors_client

        sa_json = '{"type": "service_account", "project_id": "my-proj"}'
        resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "bq-prod",
                "type": "bigquery",
                "config": {"database": "my-proj.dataset"},
                "secret": {"service_account_json": sa_json},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        row_id = resp.json()["id"]
        _assert_no_secret_in_response(resp.json())

        # Secret is in the store
        secret = await _secret_store.get(row_id, org_id)
        assert secret.get("service_account_json") == sa_json

    @pytest.mark.asyncio
    async def test_create_without_secret_is_allowed(self, connectors_client):
        """Connectors that do not need a secret (e.g. public HTTP) are accepted."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "public-api",
                "type": "http_json",
                "config": {"host": "https://api.example.com"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        row_id = resp.json()["id"]

        # Empty secret blob is stored (not an error)
        secret = await _secret_store.get(row_id, org_id)
        assert isinstance(secret, dict)


# ---------------------------------------------------------------------------
# Tests: validation — secret keys in config must be rejected
# ---------------------------------------------------------------------------


class TestSecretInConfigRejected:

    @pytest.mark.asyncio
    async def test_password_in_config_raises_422(self, connectors_client):
        """Passing 'password' inside config must be rejected with 422."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "bad-request",
                "type": "postgres",
                "config": {
                    "host": "db.local",
                    "port": 5432,
                    "database": "d",
                    "user": "u",
                    "password": "oops",  # SECRET MUST NOT BE HERE
                },
                "secret": {},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 422, (
            f"Expected 422 when secret key appears in config, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Tests: GET (single + list)
# ---------------------------------------------------------------------------


class TestGetConnector:

    @pytest.mark.asyncio
    async def test_get_single_connector(self, connectors_client):
        """GET /connectors/{id} returns the connector row."""
        client, alice_id, org_id, repo = connectors_client

        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "get-me",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "pw"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        get_resp = await client.get(f"/api/v1/connectors/{row_id}", headers=_auth_headers(alice_id))
        assert get_resp.status_code == 200, get_resp.text
        body = get_resp.json()
        assert body["id"] == row_id
        assert body["name"] == "get-me"
        _assert_no_secret_in_response(body)

    @pytest.mark.asyncio
    async def test_get_unknown_id_returns_404(self, connectors_client):
        """GET /connectors/{unknown} → 404."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.get(
            f"/api/v1/connectors/{uuid.uuid4()}",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_list_connectors(self, connectors_client):
        """GET /connectors returns a list of connectors (no secrets)."""
        client, alice_id, org_id, repo = connectors_client

        # Create two connectors
        for name in ["conn-a", "conn-b"]:
            await client.post(
                "/api/v1/connectors",
                json={
                    "name": name,
                    "type": "postgres",
                    "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                    "secret": {"password": "pw"},
                },
                headers=_auth_headers(alice_id),
            )

        list_resp = await client.get("/api/v1/connectors", headers=_auth_headers(alice_id))
        assert list_resp.status_code == 200, list_resp.text
        items = list_resp.json()
        assert isinstance(items, list)
        names = [c["name"] for c in items]
        assert "conn-a" in names
        assert "conn-b" in names
        # No secrets in any item
        for item in items:
            _assert_no_secret_in_response(item)

    @pytest.mark.asyncio
    async def test_list_excludes_non_connector_datastores(self, connectors_client):
        """GET /connectors must not include plain datastores (no connector_type)."""
        client, alice_id, org_id, repo = connectors_client

        # Create a plain datastore (no connector_type in config)
        await repo.create(
            resource="datastores",
            org_id=org_id,
            created_by=alice_id,
            name="plain-datastore",
            config={"some_key": "some_value"},  # No connector_type
        )

        # Create a connector
        await client.post(
            "/api/v1/connectors",
            json={"name": "real-connector", "type": "duckdb", "config": {}, "secret": {}},
            headers=_auth_headers(alice_id),
        )

        list_resp = await client.get("/api/v1/connectors", headers=_auth_headers(alice_id))
        items = list_resp.json()
        names = [c["name"] for c in items]
        assert "real-connector" in names
        assert "plain-datastore" not in names


# ---------------------------------------------------------------------------
# Tests: update (rotate secret + update config)
# ---------------------------------------------------------------------------


class TestUpdateConnector:

    @pytest.mark.asyncio
    async def test_rotate_secret(self, connectors_client):
        """PUT with new secret rotates the stored secret."""
        client, alice_id, org_id, repo = connectors_client

        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "rotatable",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "old-password"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        # Rotate the secret
        update_resp = await client.put(
            f"/api/v1/connectors/{row_id}",
            json={"secret": {"password": "new-password"}},
            headers=_auth_headers(alice_id),
        )
        assert update_resp.status_code == 200, update_resp.text
        _assert_no_secret_in_response(update_resp.json())

        # Old secret must be replaced
        secret = await _secret_store.get(row_id, org_id)
        assert secret.get("password") == "new-password", (
            "Secret rotation failed: store still has old password"
        )

    @pytest.mark.asyncio
    async def test_update_non_secret_config(self, connectors_client):
        """PUT with new config updates non-secret fields."""
        client, alice_id, org_id, repo = connectors_client

        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "updatable",
                "type": "postgres",
                "config": {"host": "old-host.local", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "pw"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        update_resp = await client.put(
            f"/api/v1/connectors/{row_id}",
            json={"name": "updated-name", "config": {"host": "new-host.local"}},
            headers=_auth_headers(alice_id),
        )
        assert update_resp.status_code == 200, update_resp.text
        body = update_resp.json()
        assert body["name"] == "updated-name"
        assert body["config"]["host"] == "new-host.local"
        _assert_no_secret_in_response(body)

    @pytest.mark.asyncio
    async def test_update_response_has_no_secret(self, connectors_client):
        """PUT response must never include secret material."""
        client, alice_id, org_id, repo = connectors_client

        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "safe-update",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "secret123"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        update_resp = await client.put(
            f"/api/v1/connectors/{row_id}",
            json={"secret": {"password": "new-secret123"}},
            headers=_auth_headers(alice_id),
        )
        _assert_no_secret_in_response(update_resp.json())

    @pytest.mark.asyncio
    async def test_update_unknown_id_returns_404(self, connectors_client):
        """PUT /connectors/{unknown} → 404."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.put(
            f"/api/v1/connectors/{uuid.uuid4()}",
            json={"name": "ghost"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: delete
# ---------------------------------------------------------------------------


class TestDeleteConnector:

    @pytest.mark.asyncio
    async def test_delete_returns_204_then_get_404(self, connectors_client):
        """DELETE removes the connector; subsequent GET returns 404."""
        client, alice_id, org_id, repo = connectors_client

        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "delete-me",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "pw"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/v1/connectors/{row_id}",
            headers=_auth_headers(alice_id),
        )
        assert del_resp.status_code == 204, del_resp.text

        get_resp = await client.get(
            f"/api/v1/connectors/{row_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_removes_secret_from_store(self, connectors_client):
        """DELETE also removes the secret from the SecretStore."""
        client, alice_id, org_id, repo = connectors_client

        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "delete-secret",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "pw"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        # Verify secret exists before deletion
        assert _secret_store.has(row_id, org_id), "Secret should exist before delete"

        await client.delete(
            f"/api/v1/connectors/{row_id}",
            headers=_auth_headers(alice_id),
        )

        # Secret must be removed
        assert not _secret_store.has(row_id, org_id), (
            "Secret must be removed from store on connector delete"
        )

    @pytest.mark.asyncio
    async def test_delete_unknown_id_returns_404(self, connectors_client):
        """DELETE /connectors/{unknown} → 404."""
        client, alice_id, org_id, repo = connectors_client

        resp = await client.delete(
            f"/api/v1/connectors/{uuid.uuid4()}",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: org-scoping
# ---------------------------------------------------------------------------


class TestOrgScoping:

    @pytest.mark.asyncio
    async def test_org_b_cannot_get_org_a_connector(self, connectors_app, fake_db):
        """Org B cannot GET a connector owned by org A — returns 404, not data."""
        app, repo = connectors_app

        # Alice in org A
        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        # Bob in org B
        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            # Alice creates a connector
            create_resp = await client.post(
                "/api/v1/connectors",
                json={
                    "name": "alice-secret-conn",
                    "type": "postgres",
                    "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                    "secret": {"password": "alice-pw"},
                },
                headers=_auth_headers(alice_id),
            )
            assert create_resp.status_code == 201, create_resp.text
            conn_id = create_resp.json()["id"]

            # Bob tries to GET Alice's connector — must get 404, not data
            get_resp = await client.get(
                f"/api/v1/connectors/{conn_id}",
                headers=_auth_headers(bob_id),
            )
            assert get_resp.status_code == 404, (
                "Cross-org GET must return 404 (no information leak)"
            )

    @pytest.mark.asyncio
    async def test_org_b_cannot_delete_org_a_connector(self, connectors_app, fake_db):
        """Org B cannot DELETE a connector owned by org A."""
        app, repo = connectors_app

        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            create_resp = await client.post(
                "/api/v1/connectors",
                json={
                    "name": "alice-conn",
                    "type": "postgres",
                    "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                    "secret": {"password": "pw"},
                },
                headers=_auth_headers(alice_id),
            )
            conn_id = create_resp.json()["id"]

            del_resp = await client.delete(
                f"/api/v1/connectors/{conn_id}",
                headers=_auth_headers(bob_id),
            )
            assert del_resp.status_code == 404, (
                "Cross-org DELETE must return 404 (no information leak)"
            )

            # Alice's connector must still exist
            get_resp = await client.get(
                f"/api/v1/connectors/{conn_id}",
                headers=_auth_headers(alice_id),
            )
            assert get_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_does_not_include_other_org_connectors(self, connectors_app, fake_db):
        """GET /connectors only returns connectors from the caller's org."""
        app, repo = connectors_app

        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            await client.post(
                "/api/v1/connectors",
                json={
                    "name": "alice-only",
                    "type": "postgres",
                    "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                    "secret": {"password": "pw"},
                },
                headers=_auth_headers(alice_id),
            )

            list_resp = await client.get("/api/v1/connectors", headers=_auth_headers(bob_id))
            assert list_resp.status_code == 200
            items = list_resp.json()
            names = [c["name"] for c in items]
            assert "alice-only" not in names, (
                "Bob must not see Alice's connector in his list"
            )


# ---------------------------------------------------------------------------
# Tests: auth guard
# ---------------------------------------------------------------------------


class TestAuthGuard:

    @pytest.mark.asyncio
    async def test_no_token_create_returns_401(self, connectors_client):
        """POST /connectors without Authorization → 401."""
        client, *_ = connectors_client
        resp = await client.post(
            "/api/v1/connectors",
            json={"name": "x", "type": "postgres", "config": {}, "secret": {}},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_token_list_returns_401(self, connectors_client):
        """GET /connectors without Authorization → 401."""
        client, *_ = connectors_client
        resp = await client.get("/api/v1/connectors")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_token_get_returns_401(self, connectors_client):
        """GET /connectors/{id} without Authorization → 401."""
        client, *_ = connectors_client
        resp = await client.get(f"/api/v1/connectors/{uuid.uuid4()}")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_token_delete_returns_401(self, connectors_client):
        """DELETE /connectors/{id} without Authorization → 401."""
        client, *_ = connectors_client
        resp = await client.delete(f"/api/v1/connectors/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: /test endpoint
# ---------------------------------------------------------------------------


class TestConnectorTest:

    @pytest.mark.asyncio
    async def test_ok_when_config_and_secret_resolve(self, connectors_client):
        """POST /connectors/{id}/test → ok:True when both layers are resolvable."""
        client, alice_id, org_id, repo = connectors_client

        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "testable",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "pw"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        test_resp = await client.post(
            f"/api/v1/connectors/{row_id}/test",
            headers=_auth_headers(alice_id),
        )
        assert test_resp.status_code == 200, test_resp.text
        body = test_resp.json()
        assert body["ok"] is True
        assert body["layers"]["config"] is True
        assert body["layers"]["secret"] is True
        assert body["connector_id"] == row_id
        # No network was opened — this is a structural check only
        _assert_no_secret_in_response(body)

    @pytest.mark.asyncio
    async def test_config_missing_returns_not_ok(self, connectors_client):
        """POST /connectors/{unknown}/test → ok:False, config layer missing."""
        client, alice_id, org_id, repo = connectors_client

        test_resp = await client.post(
            f"/api/v1/connectors/{uuid.uuid4()}/test",
            headers=_auth_headers(alice_id),
        )
        assert test_resp.status_code == 200, test_resp.text
        body = test_resp.json()
        assert body["ok"] is False
        assert body["layers"]["config"] is False
        assert body["layers"]["secret"] is False

    @pytest.mark.asyncio
    async def test_secret_missing_returns_not_ok(self, connectors_client):
        """POST /connectors/{id}/test → ok:False when secret is missing from store."""
        client, alice_id, org_id, repo = connectors_client

        # Create the connector normally
        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "no-secret",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "pw"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        # Manually remove the secret from the store to simulate decryption failure
        _secret_store.reset()

        test_resp = await client.post(
            f"/api/v1/connectors/{row_id}/test",
            headers=_auth_headers(alice_id),
        )
        assert test_resp.status_code == 200, test_resp.text
        body = test_resp.json()
        assert body["ok"] is False
        assert body["layers"]["config"] is True
        assert body["layers"]["secret"] is False

    @pytest.mark.asyncio
    async def test_test_response_has_no_secret(self, connectors_client):
        """POST /connectors/{id}/test response must never include secret material."""
        client, alice_id, org_id, repo = connectors_client

        create_resp = await client.post(
            "/api/v1/connectors",
            json={
                "name": "safe-test",
                "type": "postgres",
                "config": {"host": "h", "port": 5432, "database": "d", "user": "u"},
                "secret": {"password": "super-secret"},
            },
            headers=_auth_headers(alice_id),
        )
        row_id = create_resp.json()["id"]

        test_resp = await client.post(
            f"/api/v1/connectors/{row_id}/test",
            headers=_auth_headers(alice_id),
        )
        _assert_no_secret_in_response(test_resp.json())
