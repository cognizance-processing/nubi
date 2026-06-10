"""Tests for org-secret resolution + masking in notebook cell execution.

Coverage
--------
1. POST /flows/preview — `{{ secrets.NAME }}` in a SQL cell resolves via the
   org secret store (same helper as the durable path).
2. POST /flows/run-cell — same template resolves on the durable path.
3. Python cells receive a `secrets` dict local (preview + run-cell); the
   value round-trips into the cell result while NEVER appearing in the
   captured task logs (masked as '•••').
4. Error-path masking — a failing cell whose error message embeds a resolved
   secret value surfaces '•••', not the plaintext, to the client.
5. Org scoping — another org's secrets do not resolve.
6. redact_secret_values unit behaviour (mask, short-value skip, None).

The secret store is faked with ``InMemorySecretStore`` injected via
``set_secret_store`` (mirrors test_secrets.py / runtime contract).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Environment: a valid NUBI_SECRETS_KEY must exist before crypto lazy-imports
# fire inside the store (see test_secrets.py for the same pattern).
# ---------------------------------------------------------------------------

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("NUBI_SECRETS_KEY", Fernet.generate_key().decode())

from app.auth.jwt import mint_access_token  # noqa: E402
from app.flows.store import InMemoryFlowStore, set_flow_store  # noqa: E402
from app.repos.memory import InMemoryRepo  # noqa: E402
from app.repos.provider import set_repo  # noqa: E402
from app.secrets.store import InMemorySecretStore, set_secret_store  # noqa: E402


SECRET_NAME = "API_TOKEN"
SECRET_VALUE = "tok-supersecret-9f8e7d6c5b4a"
MASK = "•••"


# ---------------------------------------------------------------------------
# Helpers / fixtures (mirrors test_flows_notebook.py style)
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _sql_spec(sql: str) -> dict[str, Any]:
    return {
        "version": 1,
        "name": "secret_notebook",
        "params": [],
        "tasks": [
            {
                "key": "cell_secret",
                "kind": "query",
                "needs": [],
                "config": {"sql": sql},
                "cell_type": "sql",
            }
        ],
    }


def _python_spec(code: str) -> dict[str, Any]:
    return {
        "version": 1,
        "name": "secret_py_notebook",
        "params": [],
        "tasks": [
            {
                "key": "cell_py",
                "kind": "python",
                "needs": [],
                "config": {"code": code},
                "cell_type": "python",
            }
        ],
    }


@pytest_asyncio.fixture
async def secret_client(app, fake_db):
    """HTTPX client with a seeded user/org + an InMemorySecretStore holding
    SECRET_NAME=SECRET_VALUE for the user's org (and a decoy in another org)."""
    store = InMemoryFlowStore()
    set_flow_store(store)

    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    other_org_id = str(uuid.uuid4())

    fake_db.users[alice_id] = _make_user(alice_id)
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    secret_store = InMemorySecretStore()
    await secret_store.set_secret(alice_org_id, SECRET_NAME, SECRET_VALUE, alice_id)
    await secret_store.set_secret(other_org_id, "OTHER_ORG_ONLY", "other-org-value-123", alice_id)
    set_secret_store(secret_store)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, alice_id, alice_org_id, store

    set_secret_store(None)
    set_flow_store(None)
    set_repo(None)


# ---------------------------------------------------------------------------
# 1. Preview resolves {{ secrets.NAME }} in SQL
# ---------------------------------------------------------------------------


class TestPreviewSecretResolution:
    @pytest.mark.asyncio
    async def test_preview_sql_resolves_secret_template(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _sql_spec("SELECT '{{ secrets.API_TOKEN }}' AS tok")},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rows"], body
        assert body["rows"][0]["tok"] == SECRET_VALUE

    @pytest.mark.asyncio
    async def test_preview_unknown_secret_resolves_empty(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _sql_spec("SELECT '{{ secrets.NO_SUCH_SECRET }}' AS tok")},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"][0]["tok"] == ""

    @pytest.mark.asyncio
    async def test_preview_does_not_resolve_other_org_secret(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _sql_spec("SELECT '{{ secrets.OTHER_ORG_ONLY }}' AS tok")},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"][0]["tok"] == ""
        assert "other-org-value-123" not in resp.text


# ---------------------------------------------------------------------------
# 2. run-cell (durable path) resolves the same template
# ---------------------------------------------------------------------------


class TestRunCellSecretResolution:
    @pytest.mark.asyncio
    async def test_run_cell_sql_resolves_secret_template(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/run-cell",
            json={"spec": _sql_spec("SELECT '{{ secrets.API_TOKEN }}' AS tok")},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rows"][0]["tok"] == SECRET_VALUE


# ---------------------------------------------------------------------------
# 3. Python cells receive a `secrets` dict; logs are masked
# ---------------------------------------------------------------------------


_PY_ROUNDTRIP_CODE = (
    'print("token=" + secrets["API_TOKEN"])\n'
    'result = {"rows": [{"v": secrets["API_TOKEN"]}], "columns": ["v"], "row_count": 1}\n'
)


class TestPythonCellSecrets:
    @pytest.mark.asyncio
    async def test_run_cell_python_secrets_dict_roundtrips_and_logs_masked(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/run-cell",
            json={"spec": _python_spec(_PY_ROUNDTRIP_CODE)},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # The value round-trips into the result (by design — flow authors can
        # always exfiltrate a secret they can reference).
        assert body["rows"][0]["v"] == SECRET_VALUE

        # …but the printed value is masked in captured task logs.
        task_runs = body.get("task_runs") or []
        assert task_runs, body
        all_logs = [line for tr in task_runs for line in (tr.get("logs") or [])]
        assert any(f"token={MASK}" in line for line in all_logs), all_logs
        assert all(SECRET_VALUE not in line for line in all_logs), all_logs

    @pytest.mark.asyncio
    async def test_preview_python_secrets_dict_available(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={
                "spec": _python_spec(
                    'result = {"rows": [{"v": secrets["API_TOKEN"]}], '
                    '"columns": ["v"], "row_count": 1}\n'
                )
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"][0]["v"] == SECRET_VALUE


# ---------------------------------------------------------------------------
# 4. Error-path masking
# ---------------------------------------------------------------------------


class TestErrorMasking:
    @pytest.mark.asyncio
    async def test_preview_python_error_masks_secret_value(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _python_spec('raise RuntimeError("boom " + secrets["API_TOKEN"])')},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text
        assert SECRET_VALUE not in resp.text
        assert MASK in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_preview_failing_sql_with_secret_does_not_leak(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/preview",
            json={
                "spec": _sql_spec(
                    "SELECT '{{ secrets.API_TOKEN }}' AS tok FROM no_such_table_xyz"
                )
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text
        assert SECRET_VALUE not in resp.text

    @pytest.mark.asyncio
    async def test_run_cell_python_error_masks_secret_value(self, secret_client):
        client, alice_id, *_ = secret_client

        resp = await client.post(
            "/api/v1/flows/run-cell",
            json={"spec": _python_spec('raise RuntimeError("boom " + secrets["API_TOKEN"])')},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text
        assert SECRET_VALUE not in resp.text


# ---------------------------------------------------------------------------
# 5. redact_secret_values unit behaviour
# ---------------------------------------------------------------------------


class TestRedactSecretValues:
    def test_masks_value(self):
        from app.flows.executor import redact_secret_values

        out = redact_secret_values(
            f"connecting with {SECRET_VALUE} now", {SECRET_NAME: SECRET_VALUE}
        )
        assert out == f"connecting with {MASK} now"

    def test_masks_every_occurrence(self):
        from app.flows.executor import redact_secret_values

        out = redact_secret_values(
            f"{SECRET_VALUE} and {SECRET_VALUE}", {SECRET_NAME: SECRET_VALUE}
        )
        assert SECRET_VALUE not in out
        assert out.count(MASK) == 2

    def test_skips_short_values(self):
        from app.flows.executor import redact_secret_values

        # Values shorter than 4 chars are too noisy to redact.
        out = redact_secret_values("a is ok", {"SHORT": "ok"})
        assert out == "a is ok"

    def test_none_and_empty_passthrough(self):
        from app.flows.executor import redact_secret_values

        assert redact_secret_values(None, {SECRET_NAME: SECRET_VALUE}) is None
        assert redact_secret_values("", {SECRET_NAME: SECRET_VALUE}) == ""
        assert redact_secret_values("text", {}) == "text"
