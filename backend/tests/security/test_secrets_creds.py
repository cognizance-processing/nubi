"""SECRETS / CREDS — deployer & export credentials never leak via the API,
and are org-scoped.

Covers:
  - Deployer BYO-bucket creds (aws_secret_access_key) never returned by the
    managed-lakehouse API (_row_with_usage strips them).
  - Export async dest_creds_ref never returned by the job-status endpoint.
  - Secret store is org-scoped: org B cannot read org A's datastore secret.
"""

from __future__ import annotations

import base64
import secrets
import uuid

import pytest

from app.connectors.secret_store import InMemorySecretStore
from app.routes.lakehouse import _row_with_usage
from tests.security._custody_fixtures import auth_headers, custody_env  # noqa: F401


@pytest.fixture
def _connector_key(monkeypatch):
    """Provide a connector secret key so the secret store can encrypt at rest."""
    monkeypatch.setenv("CONNECTOR_SECRET_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("CONNECTOR_SECRET_KEY_VERSION", "1")
    monkeypatch.delenv("CONNECTOR_SECRET_KEYS", raising=False)
    # Reset any cached key registry so the new env takes effect.
    from app.security.crypto import reset_keys_for_tests
    reset_keys_for_tests()
    yield
    reset_keys_for_tests()


def test_row_with_usage_strips_aws_secret():
    row = {
        "id": "ds1",
        "name": "lake",
        "config": {
            "connector_type": "duckdb",
            "database": "file:///x",
            "aws_access_key_id": "AKIA...",
            "aws_secret_access_key": "super-secret-value",  # must be stripped
        },
    }
    out = _row_with_usage(row, 100)
    assert "aws_secret_access_key" not in out["config"]
    # The original row is not mutated in a way that re-exposes the secret.
    # (out is a copy; only the safe config is returned.)
    assert "super-secret-value" not in str(out["config"])


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


@pytest.mark.asyncio
async def test_dest_creds_ref_never_returned(custody_env):
    e = custody_env
    enq = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs",
        json={
            "dest_uri": "file:///exports/out/",
            "dest_creds_ref": "kms://deployer/secret/key",
            "table": "orders",
        },
        headers=auth_headers(e["alice_id"]),
    )
    assert enq.status_code == 202, enq.text
    # The 202 enqueue response itself must not echo the creds ref.
    assert "dest_creds_ref" not in enq.json()

    job_id = enq.json()["job_id"]
    status = await e["client"].get(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs/{job_id}",
        headers=auth_headers(e["alice_id"]),
    )
    assert status.status_code == 200
    body = status.json()
    assert "dest_creds_ref" not in body
    assert "kms://deployer/secret/key" not in str(body)
