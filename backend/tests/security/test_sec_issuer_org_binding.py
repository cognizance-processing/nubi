"""An issuer registered by one organisation only ever authenticates that organisation."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

import jwt as pyjwt  # noqa: E402
from cryptography.hazmat.backends import default_backend  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from jwt.algorithms import RSAAlgorithm  # noqa: E402


def _keypair(kid: str):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    jwk = json.loads(RSAAlgorithm.to_jwk(priv.public_key()))
    jwk["kid"] = kid
    jwk["use"] = "sig"
    return priv, {"keys": [jwk]}


_KEY_A, _JWKS_A = _keypair("key-a")
_KEY_B, _JWKS_B = _keypair("key-b")
ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
ISS = "https://idp.shared.example"
AUD = "nubi-embed"


def _token(key, kid, *, iss=ISS, **claims) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": iss,
        "aud": AUD,
        "sub": "embed-user",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "scope": "read:*",
        **claims,
    }
    return pyjwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture(autouse=True)
def _stores():
    from app.auth.issuers import get_issuer_registry
    from app.routes._org import host_mode_org_pin
    from app.security.issuers_store import InMemoryIssuersStore, set_issuers_store

    store = InMemoryIssuersStore()
    set_issuers_store(store)
    get_issuer_registry().clear()
    host_mode_org_pin.set(None)
    yield store
    get_issuer_registry().clear()
    set_issuers_store(None)
    host_mode_org_pin.set(None)


async def _register(store, org, jwks, *, iss=ISS, sync=True, **kw):
    """Create an issuer the way the management routes do (store + registry sync)."""
    from app.routes.jwt_issuers import _sync_registry

    await store.create(
        org_id=org, name="idp", issuer=iss, audience=AUD, created_by="u",
        static_jwks_json=jwks, **kw,
    )
    if sync:
        _sync_registry(org, await store.list_for_org(org))


async def _verify_and_pin(token):
    from app.auth.deps import _maybe_pin_host_mode_org
    from app.auth.verify import verify_token_async
    from app.routes._org import host_mode_org_pin

    identity = await verify_token_async(token)
    host_mode_org_pin.set(None)
    await _maybe_pin_host_mode_org(identity)
    return identity, host_mode_org_pin.get()


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [True, False], ids=["registry", "db-fallback"])
async def test_token_naming_another_org_is_rejected(_stores, sync):
    from app.errors import AppError

    await _register(_stores, ORG_A, _JWKS_A, sync=sync)
    with pytest.raises(AppError) as exc:
        await _verify_and_pin(_token(_KEY_A, "key-a", org=ORG_B))
    assert exc.value.status == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [True, False], ids=["registry", "db-fallback"])
async def test_org_claim_cannot_select_another_org(_stores, sync):
    from app.errors import AppError
    from app.routes._org import host_mode_org_pin

    await _register(_stores, ORG_A, _JWKS_A, sync=sync, host_mode=True, org_claim="tenant")
    with pytest.raises(AppError) as exc:
        await _verify_and_pin(_token(_KEY_A, "key-a", org=ORG_A, tenant=ORG_B))
    assert exc.value.status in (401, 403)
    assert host_mode_org_pin.get() != ORG_B


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [True, False], ids=["registry", "db-fallback"])
async def test_same_issuer_in_two_orgs_stays_isolated(_stores, sync):
    from app.errors import AppError

    await _register(_stores, ORG_A, _JWKS_A, sync=sync)
    await _register(_stores, ORG_B, _JWKS_B, sync=sync)

    ident_a, _ = await _verify_and_pin(_token(_KEY_A, "key-a", org=ORG_A))
    assert ident_a.org == ORG_A
    ident_b, _ = await _verify_and_pin(_token(_KEY_B, "key-b", org=ORG_B))
    assert ident_b.org == ORG_B

    # A's key presented for B (with either kid) never verifies.
    for kid in ("key-a", "key-b"):
        with pytest.raises(AppError) as exc:
            await _verify_and_pin(_token(_KEY_A, kid, org=ORG_B))
        assert exc.value.status == 401

    # No org claim + the issuer string exists in two orgs → ambiguous.
    with pytest.raises(AppError) as exc:
        await _verify_and_pin(_token(_KEY_A, "key-a"))
    assert exc.value.status == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [True, False], ids=["registry", "db-fallback"])
async def test_host_mode_embed_still_works(_stores, sync):
    await _register(_stores, ORG_A, _JWKS_A, sync=sync, host_mode=True, org_claim="org")
    identity, pinned = await _verify_and_pin(_token(_KEY_A, "key-a", org=ORG_A))
    assert identity.org == ORG_A
    assert pinned == ORG_A


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [True, False], ids=["registry", "db-fallback"])
async def test_token_without_org_claim_gets_the_issuer_org(_stores, sync):
    await _register(_stores, ORG_A, _JWKS_A, sync=sync)
    identity, _ = await _verify_and_pin(_token(_KEY_A, "key-a"))
    assert identity.org == ORG_A


def test_sync_verify_rejects_another_org(_stores):
    import asyncio

    from app.auth.verify import verify_token
    from app.errors import AppError

    asyncio.run(_register(_stores, ORG_A, _JWKS_A))
    assert verify_token(_token(_KEY_A, "key-a", org=ORG_A)).org == ORG_A
    with pytest.raises(AppError):
        verify_token(_token(_KEY_A, "key-a", org=ORG_B))


def _app(role: str):
    from fastapi import FastAPI

    from app.auth.deps import current_user
    from app.errors import register_handlers
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo
    from app.routes import api_router

    user_id = str(uuid.uuid4())
    repo = InMemoryRepo()
    repo.seed_org_member(ORG_A, user_id, role)
    set_repo(repo)
    app = FastAPI()
    register_handlers(app)
    app.dependency_overrides[current_user] = lambda: {"id": user_id, "email": "t@example.com"}
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.mark.parametrize("role,expected", [("member", 403), ("viewer", 403), ("admin", 201), ("owner", 201)])
def test_only_owner_or_admin_manage_issuers(role, expected):
    from fastapi.testclient import TestClient

    from app.repos.provider import set_repo

    try:
        with TestClient(_app(role)) as client:
            resp = client.post(
                "/api/v1/security/jwt-issuers",
                json={"name": "x", "issuer": "https://x.example", "audience": AUD, "static_jwks_json": _JWKS_A},
            )
            assert resp.status_code == expected, resp.text
            if expected == 201:
                issuer_id = resp.json()["id"]
                assert client.delete(f"/api/v1/security/jwt-issuers/{issuer_id}").status_code == 204
    finally:
        set_repo(None)


# ---------------------------------------------------------------------------
# Rate-limit buckets
# ---------------------------------------------------------------------------


def _request(path: str, token: str):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
            "client": ("1.2.3.4", 1234),
        }
    )


def test_embed_token_never_shares_the_first_party_org_bucket(_stores):
    import asyncio

    from app.middleware.ratelimit import _extract_verified_identity

    asyncio.run(_register(_stores, ORG_A, _JWKS_A))
    key, is_embed = _extract_verified_identity(_request("/api/v1/dashboards", _token(_KEY_A, "key-a", org=ORG_A)))
    assert is_embed is True
    assert key != f"org:{ORG_A}"


def test_auth_routes_are_keyed_by_client_address():
    from app.auth.jwt import mint_access_token
    from app.middleware.ratelimit import _bucket_identity, _extract_verified_identity

    req = _request("/api/v1/auth/login", mint_access_token(str(uuid.uuid4()), extra_claims={"org": "some-org"}))
    token_key, _ = _extract_verified_identity(req)
    assert token_key == "org:some-org"
    assert _bucket_identity(req, "auth", token_key) == "ip:1.2.3.4"
    assert _bucket_identity(req, "default", token_key) == "org:some-org"


# ---------------------------------------------------------------------------
# Query registry listing for embed identities
# ---------------------------------------------------------------------------


def _embed_identity(org):
    from app.auth.verify import VerifiedIdentity

    return VerifiedIdentity(
        kind="embed", user_id="embed-user", org=org, project=None, roles=[], policies={},
        scope=["read:query"], embed_origin=None, datastore=None, raw_claims={},
    )


@pytest.mark.asyncio
async def test_embed_identity_without_org_sees_no_persisted_queries():
    from app.routes.query import _visible_registry_row_ids

    assert await _visible_registry_row_ids(None, _embed_identity(None)) == set()


def test_registry_entries_omit_sql_for_embed_identities():
    from app.auth.verify import VerifiedIdentity
    from app.queries.registry import RegisteredQuery
    from app.routes.query import _registry_entry

    rq = RegisteredQuery(id="q1", sql="SELECT 1", name="Q")
    assert "sql" not in _registry_entry(rq, _embed_identity(ORG_A))
    first_party = VerifiedIdentity(
        kind="access", user_id="u", org=None, project=None, roles=[], policies={},
        scope=["read:query"], embed_origin=None, datastore=None, raw_claims={},
    )
    assert _registry_entry(rq, first_party)["sql"] == "SELECT 1"
