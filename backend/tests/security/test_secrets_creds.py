"""SECRETS / CREDS — connector credentials never leak via the API, and are
org-scoped.

Covers:
  - Secret store is org-scoped: org B cannot read org A's datastore secret.
"""

from __future__ import annotations

import uuid

import pytest

from app.connectors.secret_store import InMemorySecretStore


@pytest.fixture
def _connector_key(monkeypatch):
    """Provide a connector secret key so the secret store can encrypt at rest."""
    import base64
    import secrets

    monkeypatch.setenv("CONNECTOR_SECRET_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("CONNECTOR_SECRET_KEY_VERSION", "1")
    monkeypatch.delenv("CONNECTOR_SECRET_KEYS", raising=False)
    # Reset any cached key registry so the new env takes effect.
    from app.security.crypto import reset_keys_for_tests
    reset_keys_for_tests()
    yield
    reset_keys_for_tests()


@pytest.mark.asyncio
async def test_secret_store_is_org_scoped(_connector_key):
    store = InMemorySecretStore()
    org_a, org_b, ds = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    await store.put(ds, org_a, {"aws_secret_access_key": "A-secret"})
    # Org A can read its own secret.
    got = await store.get(ds, org_a)
    assert got == {"aws_secret_access_key": "A-secret"}
    # Org B (same datastore id, different org) cannot.
    assert await store.get(ds, org_b) is None
    # Org B deleting does not affect org A's secret.
    await store.delete(ds, org_b)
    assert await store.get(ds, org_a) is not None
