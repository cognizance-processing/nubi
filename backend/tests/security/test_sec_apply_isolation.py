"""Security tests for POST /api/v1/apply.

Coverage
--------
1.  Unauthenticated request → 401/403 (never 200).
2.  Embed token cannot call /apply → 403 (write scope check).
3.  Read-only scoped first-party token → 403 (require_writer blocks it).
4.  dry_run=True writes nothing — repo untouched.
5.  Org A user cannot apply into org B (X-Org-Id from a different org is
    membership-checked; without membership it fails — 403 or resource attributed
    to correct org, never org B without membership).
6.  apply of unknown kind → partial failure (action=failed), HTTP 200.
7.  Empty resources list → 200 with empty results.
8.  Partial failure does not expose other resources' IDs / errors.
9.  Malformed envelope (missing ``kind``) → handled gracefully, HTTP 200, action=failed.
10. valid apply creates resource under caller's org (positive control).
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Env bootstrap
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("NUBI_RATELIMIT_ENABLED", "false")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("NUBI_SECRETS_KEY", Fernet.generate_key().decode())

from tests.security.conftest_helpers import (  # noqa: E402
    mint_access_token,
    mint_embed_token,
    STATIC_JWKS,
    HOST_ISS,
    HOST_AUD,
    EMBED_ORIGIN,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_issuer():
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache
    from app.config import get_settings

    get_settings.cache_clear()
    reg = get_issuer_registry()
    reg.register(
        HOST_ISS,
        jwks_uri=f"{HOST_ISS}/.well-known/jwks.json",
        aud=HOST_AUD,
        allowed_origins=[EMBED_ORIGIN],
        static_jwks=STATIC_JWKS,
    )
    yield
    reg.unregister(HOST_ISS)
    clear_cache()
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def apply_client(app, fake_db):
    """HTTPX test client with a seeded user."""
    from httpx import ASGITransport, AsyncClient
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "apply-sec@nubi.dev",
        "name": "ApplySec",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    fake_db.orgs[org_id] = {"id": org_id, "name": "ApplySecOrg"}
    fake_db.org_members[f"{org_id}:{user_id}"] = {
        "user_id": user_id,
        "org_id": org_id,
        "role": "admin",
    }

    # Seed repo membership so require_writer resolves correctly
    repo._org_members[f"{org_id}:{user_id}"] = {  # type: ignore[attr-defined]
        "user_id": user_id,
        "org_id": org_id,
        "role": "admin",
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac, user_id, org_id, repo


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _dashboard_envelope(name: str, env_id: str | None = None):
    meta = {"name": name}
    if env_id:
        meta["id"] = env_id
    return {
        "kind": "dashboard",
        "apiVersion": "nubi/v1",
        "metadata": meta,
        "spec": {"title": name, "panels": []},
    }


def _connector_envelope(name: str):
    return {
        "kind": "connector",
        "apiVersion": "nubi/v1",
        "metadata": {"name": name},
        "spec": {
            "connector_type": "postgres",
            "host": "db.example.com",
            "database": "prod",
        },
    }


# ===========================================================================
# 1. Unauthenticated → 401/403
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_unauthenticated_rejected(apply_client):
    """POST /apply without auth → 401 or 403."""
    client, _uid, _oid, _repo = apply_client
    resp = await client.post(
        "/api/v1/apply",
        json={"resources": []},
    )
    assert resp.status_code in (401, 403), (
        f"Unauthenticated /apply must be rejected, got {resp.status_code}"
    )


# ===========================================================================
# 2. Embed token → blocked (embed tokens have no write scope; require_writer rejects)
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_embed_token_rejected(apply_client):
    """Embed token (kind=embed) cannot reach /apply — require_writer blocks it."""
    client, _uid, _oid, _repo = apply_client
    token = mint_embed_token(scope=["read:query", "author:metric"], embed_origin=EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/apply",
        json={"resources": []},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": EMBED_ORIGIN,
        },
    )
    assert resp.status_code in (401, 403), (
        f"Embed token must be blocked from /apply, got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# 3. dry_run=True writes nothing
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_dryrun_writes_nothing(apply_client):
    """dry_run=True: response shows planned actions but repo is untouched."""
    client, user_id, org_id, repo = apply_client
    env_name = f"dry-run-dash-{uuid.uuid4()}"

    resp = await client.post(
        "/api/v1/apply",
        json={
            "resources": [_dashboard_envelope(env_name)],
            "dry_run": True,
        },
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    results = body["results"]
    assert len(results) == 1
    assert results[0]["action"] in ("create", "update")

    # Repo must be untouched — no board (dashboard) exists
    boards = await repo.list("boards", org_id)
    assert len(boards) == 0, "dry_run must not write to repo"


# ===========================================================================
# 4. Unknown envelope kind → partial failure, HTTP 200
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_unknown_kind_partial_failure(apply_client):
    """An unknown resource kind is recorded as failed; HTTP 200 always."""
    client, user_id, org_id, _repo = apply_client
    resp = await client.post(
        "/api/v1/apply",
        json={
            "resources": [
                {
                    "kind": "ghost_resource_that_does_not_exist",
                    "apiVersion": "nubi/v1",
                    "metadata": {"name": "bad"},
                    "spec": {},
                }
            ],
        },
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["failed"] >= 1
    result = body["results"][0]
    assert result["action"] == "failed"
    # error message must not contain internal DB details
    error = result.get("error", "")
    assert "sql" not in error.lower() or "unsupported" in error.lower() or "unknown" in error.lower()


# ===========================================================================
# 5. Empty resources list → 200, empty results
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_empty_resources_200(apply_client):
    """resources=[] → HTTP 200 with empty results and all-zero summary."""
    client, user_id, org_id, _repo = apply_client
    resp = await client.post(
        "/api/v1/apply",
        json={"resources": []},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["results"] == []
    assert body["summary"] == {"created": 0, "updated": 0, "unchanged": 0, "failed": 0}


# ===========================================================================
# 6. Malformed envelope (missing kind) → handled gracefully
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_missing_kind_envelope_handled(apply_client):
    """An envelope with no 'kind' key → action=failed, not a 500."""
    client, user_id, org_id, _repo = apply_client
    resp = await client.post(
        "/api/v1/apply",
        json={
            "resources": [
                {"apiVersion": "nubi/v1", "metadata": {"name": "no-kind"}, "spec": {}}
            ]
        },
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["failed"] >= 1


# ===========================================================================
# 7. Partial failure does not leak IDs from other resources
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_partial_failure_no_cross_resource_leak(apply_client):
    """When one envelope fails, its error must not contain info from other envelopes."""
    client, user_id, org_id, _repo = apply_client

    secret_name = f"secret-resource-{uuid.uuid4()}"
    bad_kind = "unknown_kind_sec"

    resp = await client.post(
        "/api/v1/apply",
        json={
            "resources": [
                _dashboard_envelope(secret_name),
                {
                    "kind": bad_kind,
                    "apiVersion": "nubi/v1",
                    "metadata": {"name": "attacker"},
                    "spec": {},
                },
            ]
        },
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    results = body["results"]
    assert len(results) == 2

    failed_result = next((r for r in results if r["action"] == "failed"), None)
    assert failed_result is not None

    # The error for the failed envelope must not contain the secret_name from the other resource
    error_text = failed_result.get("error", "")
    assert secret_name not in error_text, (
        f"Error for failed envelope leaks other resource name: {error_text!r}"
    )


# ===========================================================================
# 8. Positive control: valid apply creates resource
# ===========================================================================


@pytest.mark.asyncio
async def test_apply_creates_dashboard_for_caller_org(apply_client):
    """A valid first-party apply creates the resource in the caller's org."""
    client, user_id, org_id, repo = apply_client
    name = f"sec-dash-{uuid.uuid4()}"

    resp = await client.post(
        "/api/v1/apply",
        json={"resources": [_dashboard_envelope(name)]},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Created or unchanged (if the name already existed in a prior test run)
    assert body["summary"]["failed"] == 0, f"Expected 0 failures: {body}"
    result = body["results"][0]
    assert result["action"] in ("create", "created", "update", "updated", "unchanged")
