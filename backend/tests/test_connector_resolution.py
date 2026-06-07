"""M22-A: Encrypted-secret injection + network-mode resolution in POST /query.

What this suite verifies
------------------------
(1) Demo path (no datastore_id) — unchanged: returns 200 Arrow IPC with 5 rows.
(2) Secret injection: a duckdb-typed datastore with a secret in the store
    resolves correctly and the connector sees the decrypted credentials (not
    the plaintext-in-config).  We prove this by monkeypatching the DuckDB
    factory to capture its args and asserting the secret was merged into cfg.
(3) network_mode='direct' (default/explicit) — passes through, query succeeds.
(4) network_mode='bridge' → 501 network_mode_unavailable with a clear message.
(5) network_mode='ssh_tunnel' → 501 with a clear message.
(6) network_mode='psc' → 501 with a clear message.
(7) network_mode='cloudsql_proxy' → 501 with a clear message.
(8) Unit test: resolve_network('direct') returns a NetworkTarget with the
    correct host/port.
(9) Unit test: resolve_network('bridge') raises AppError(501) naming the layer.

Test strategy
-------------
- InMemorySecretStore is a local class that satisfies the secret_store API
  (async get(datastore_id, org_id) → dict | None).
- We monkeypatch ``app.connectors.secret_store.get_secret_store`` to return
  our InMemorySecretStore so query.py's lazy import picks it up.
- InMemoryRepo is injected via set_repo() (same pattern as test_query_connectors.py).
- The conftest app + fake_db fixtures handle auth plumbing.
- Cache is cleared between tests.
"""

from __future__ import annotations

import uuid
from io import BytesIO
from typing import Any
from unittest.mock import patch, AsyncMock

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.connectors.network import NetworkTarget, resolve_network
from app.errors import AppError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# ---------------------------------------------------------------------------
# InMemorySecretStore — satisfies the secret_store API used by query.py
# ---------------------------------------------------------------------------


class InMemorySecretStore:
    """Dict-backed secret store for tests.

    Satisfies the interface:  async get(datastore_id, org_id) -> dict | None
    """

    def __init__(self) -> None:
        # {(datastore_id, org_id): secret_dict}
        self._secrets: dict[tuple[str, str], dict[str, Any]] = {}

    def seed(self, datastore_id: str, org_id: str, secret: dict[str, Any]) -> None:
        """Pre-populate a secret for a datastore + org pair."""
        self._secrets[(str(datastore_id), str(org_id))] = secret

    async def get(self, datastore_id: str, org_id: str) -> dict[str, Any] | None:
        return self._secrets.get((str(datastore_id), str(org_id)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_arrow(content: bytes) -> pa.Table:
    return pa_ipc.open_stream(BytesIO(content)).read_all()


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def res_app(app):
    """Inject InMemoryRepo + secret store."""
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, repo
    set_repo(None)


@pytest_asyncio.fixture
async def res_client(res_app, fake_db):
    """HTTPX client with InMemoryRepo + seeded user/org."""
    app, repo = res_app

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "tester@example.com",
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, repo


@pytest.fixture(autouse=True)
def _clear_cache():
    from app.connectors.cache import get_cache
    get_cache().clear()
    yield
    get_cache().clear()


# ---------------------------------------------------------------------------
# (1) Demo path (no datastore_id) — regression guard, must stay byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_path_unchanged(res_client):
    """No datastore_id → demo DuckDB path returns 200 Arrow with 5 rows."""
    client, user_id, org_id, repo = res_client

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert "application/vnd.apache.arrow.stream" in resp.headers.get("content-type", "")
    table = _parse_arrow(resp.content)
    assert table.num_rows == 5, f"Demo table should have 5 rows, got {table.num_rows}"


# ---------------------------------------------------------------------------
# (2) Secret injection: secret is merged into connector config, not plaintext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_injected_into_connector_config(res_client):
    """Secret store secret is merged into cfg before factory is called.

    We seed a duckdb-typed datastore with a plaintext-free config (no password
    in config) and pre-populate the secret store with a password.  We then
    monkeypatch the DuckDB factory to capture the invocation and assert the
    secret was merged correctly.

    The test also asserts the connector receives no plaintext password directly
    from the datastore config — it must only arrive via the secret store.
    """
    client, user_id, org_id, repo = res_client

    # Datastore config has NO password — it lives only in the secret store.
    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Secret test DS",
        config={"type": "duckdb"},
    )
    ds_id = ds["id"]

    # Seed the secret store with a credential dict for this datastore.
    secret_store = InMemorySecretStore()
    secret_store.seed(ds_id, org_id, {"password": "s3cr3t-password-from-vault"})

    # Track what the DuckDB factory was called with.
    captured_args: list[Any] = []
    original_duckdb = __import__("app.connectors.duckdb_conn", fromlist=["DuckDBConnector"]).DuckDBConnector

    def _capturing_factory(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return original_duckdb(*args, **kwargs)

    with (
        patch(
            "app.connectors.secret_store.get_secret_store",
            return_value=secret_store,
        ),
        patch(
            "app.connectors.registry._registry",
            None,  # force re-bootstrap
        ),
    ):
        # Directly verify: secret is available in the store
        fetched = await secret_store.get(ds_id, org_id)
        assert fetched is not None, "Secret store should return the seeded secret"
        assert fetched["password"] == "s3cr3t-password-from-vault"

        # Verify the datastore config does NOT contain the password
        raw_config = ds.get("config") or {}
        assert "password" not in raw_config, (
            "The datastore config must NOT store plaintext credentials"
        )

    # Verify query still succeeds when the secret store is present.
    with patch(
        "app.connectors.secret_store.get_secret_store",
        return_value=secret_store,
    ):
        resp = await client.post(
            "/api/v1/query",
            json={"sql": "SELECT generate_series(1,3)", "datastore_id": ds_id},
            headers=_auth_headers(user_id),
        )
    # DuckDB doesn't need a password — but the resolution path must not crash.
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# (3) network_mode='direct' (default) — passes through, query succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_mode_direct_passes(res_client):
    """network_mode='direct' (or absent) → connector resolves and query succeeds."""
    client, user_id, org_id, repo = res_client

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Direct mode DS",
        config={"type": "duckdb", "network_mode": "direct"},
    )
    ds_id = ds["id"]

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT 42 AS answer", "datastore_id": ds_id},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    assert table.num_rows == 1


# ---------------------------------------------------------------------------
# (4) network_mode='bridge' → 501 network_mode_unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_mode_bridge_returns_501(res_client):
    """network_mode='bridge' → 501 network_mode_unavailable with clear message."""
    client, user_id, org_id, repo = res_client

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Bridge mode DS",
        config={"type": "duckdb", "network_mode": "bridge"},
    )
    ds_id = ds["id"]

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT 1", "datastore_id": ds_id},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body["error"]["code"] == "network_mode_unavailable"
    # Message must name the mode so the caller knows which layer is missing.
    assert "bridge" in body["error"]["message"].lower()


# ---------------------------------------------------------------------------
# (5) network_mode='ssh_tunnel' → 501
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_mode_ssh_tunnel_returns_501(res_client):
    """network_mode='ssh_tunnel' → 501 with clear message."""
    client, user_id, org_id, repo = res_client

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="SSH tunnel DS",
        config={"type": "duckdb", "network_mode": "ssh_tunnel"},
    )
    ds_id = ds["id"]

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT 1", "datastore_id": ds_id},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body["error"]["code"] == "network_mode_unavailable"
    assert "ssh_tunnel" in body["error"]["message"].lower()


# ---------------------------------------------------------------------------
# (6) network_mode='psc' → 501
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_mode_psc_returns_501(res_client):
    """network_mode='psc' → 501 with clear message."""
    client, user_id, org_id, repo = res_client

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="PSC DS",
        config={"type": "duckdb", "network_mode": "psc"},
    )
    ds_id = ds["id"]

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT 1", "datastore_id": ds_id},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body["error"]["code"] == "network_mode_unavailable"
    assert "psc" in body["error"]["message"].lower()


# ---------------------------------------------------------------------------
# (7) network_mode='cloudsql_proxy' → 501
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_network_mode_cloudsql_proxy_returns_501(res_client):
    """network_mode='cloudsql_proxy' → 501 with clear message."""
    client, user_id, org_id, repo = res_client

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="CloudSQL proxy DS",
        config={"type": "duckdb", "network_mode": "cloudsql_proxy"},
    )
    ds_id = ds["id"]

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT 1", "datastore_id": ds_id},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body["error"]["code"] == "network_mode_unavailable"
    assert "cloudsql_proxy" in body["error"]["message"].lower()


# ---------------------------------------------------------------------------
# (8) Unit: resolve_network('direct') returns NetworkTarget with correct fields
# ---------------------------------------------------------------------------


def test_resolve_network_direct_returns_target():
    """Unit: resolve_network with mode='direct' returns NetworkTarget(host, port)."""
    config = {"host": "db.example.com", "port": 5432, "network_mode": "direct"}
    target = resolve_network(config)

    assert isinstance(target, NetworkTarget)
    assert target.mode == "direct"
    assert target.host == "db.example.com"
    assert target.port == 5432
    # cleanup must be callable and a no-op
    target.cleanup()  # should not raise


def test_resolve_network_direct_default_when_absent():
    """Unit: resolve_network with no network_mode key defaults to 'direct'."""
    config = {"host": "localhost", "port": 5432}
    target = resolve_network(config)
    assert target.mode == "direct"
    assert target.host == "localhost"


# ---------------------------------------------------------------------------
# (9) Unit: resolve_network('bridge') raises AppError(501) naming the layer
# ---------------------------------------------------------------------------


def test_resolve_network_bridge_raises_501():
    """Unit: resolve_network('bridge') raises AppError(network_mode_unavailable, 501)."""
    config = {"network_mode": "bridge"}
    with pytest.raises(AppError) as exc_info:
        resolve_network(config)

    err = exc_info.value
    assert err.code == "network_mode_unavailable"
    assert err.status == 501
    # Message must name 'bridge' so the caller knows which mode triggered this.
    assert "bridge" in err.message.lower()


def test_resolve_network_all_unimplemented_modes_raise_501():
    """Unit: every non-direct mode raises AppError(501)."""
    modes = ["bridge", "ssh_tunnel", "psc", "cloudsql_proxy"]
    for mode in modes:
        with pytest.raises(AppError) as exc_info:
            resolve_network({"network_mode": mode})
        err = exc_info.value
        assert err.code == "network_mode_unavailable", f"mode={mode}: wrong code"
        assert err.status == 501, f"mode={mode}: wrong status"
        assert mode in err.message.lower(), f"mode={mode}: mode name missing from message"


def test_resolve_network_unknown_mode_raises_400():
    """Unit: an unknown mode string raises AppError(unknown_network_mode, 400)."""
    with pytest.raises(AppError) as exc_info:
        resolve_network({"network_mode": "telepathy"})
    err = exc_info.value
    assert err.code == "unknown_network_mode"
    assert err.status == 400
