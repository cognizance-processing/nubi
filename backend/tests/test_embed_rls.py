"""M3-B: Embed auth wired into the query path — integration + security tests.

What this suite verifies
------------------------
(a) NO token → POST /query → 401
(b) Embed token WITHOUT a read scope → POST /query → 403
(c) Embed token with Origin header NOT matching embed_origin → 403
(d) Embed token WITH read scope + matching origin → 200 Arrow IPC
(e) body.claims.policies is IGNORED: planner receives policies from the token,
    not from the request body.  Verified by monkeypatching planner.plan and
    capturing the ``claims`` argument that was actually passed.
(f) First-party HS256 access token (scope read:*) → 200 (backwards-compat).
(g) GET /embed/config/{id} with embed token → 200 stub descriptor.
(h) GET /embed/config/{id} without token → 401.

Test strategy
-------------
- RSA keypair generated once at module level (same pattern as test_verify.py).
- Issuer registered in an autouse fixture; cleaned up after each test.
- DuckDB demo connector used (no DB needed).
- The app fixture from conftest.py is reused (patches app.db.* with FakeDB).
- For test (e) we monkeypatch ``app.routes.query.planner_plan`` to spy on the
  ``claims`` kwarg and forward to the real function; then we assert the
  captured claims came from the token, not the body.

Assertions achieved
-------------------
All of (a)–(h) above are implemented and pass.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
import pytest_asyncio
import pyarrow.ipc as pa_ipc

# ---------------------------------------------------------------------------
# Environment bootstrap (must happen BEFORE any app import; conftest.py already
# sets these, but we guard with setdefault for isolation when run standalone).
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

# ---------------------------------------------------------------------------
# RSA keypair (module-level, generated once)
# ---------------------------------------------------------------------------

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from jwt.algorithms import RSAAlgorithm
import jwt as pyjwt

_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_PUBLIC_KEY_PEM: str = _PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

_JWKS_KEY: dict = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "embed-test-key"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HOST_ISS = "https://embed-host.example"
_HOST_AUD = "nubi"
_EMBED_ORIGIN = "https://embed-host.example"
_KID = "embed-test-key"


# ---------------------------------------------------------------------------
# Token mint helper
# ---------------------------------------------------------------------------


def _mint_embed_token(
    *,
    iss: str = _HOST_ISS,
    aud: str = _HOST_AUD,
    sub: str = "embed-user-1",
    scope: list[str] | None = None,
    policies: dict | None = None,
    embed_origin: str | None = _EMBED_ORIGIN,
    exp_delta: int = 300,
) -> str:
    """Mint a test embed JWT signed with the test RSA private key."""
    if scope is None:
        scope = ["read:query"]
    # Default to empty policies so the demo table (no tenant_id column) works.
    # Pass explicit policies={...} for RLS-specific tests.
    if policies is None:
        policies = {}

    now = datetime.now(tz=timezone.utc)
    payload: dict = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "org": "acme-org",
        "roles": ["viewer"],
        "policies": policies,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    if embed_origin is not None:
        payload["embed_origin"] = embed_origin

    return pyjwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_embed_issuer():
    """Register the test embed issuer and clean up after each test."""
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache
    from app.config import get_settings

    get_settings.cache_clear()
    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri=f"{_HOST_ISS}/.well-known/jwks.json",
        aud=_HOST_AUD,
        allowed_origins=[_EMBED_ORIGIN],
        static_jwks=_STATIC_JWKS,
    )
    yield
    registry.unregister(_HOST_ISS)
    clear_cache()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_query_cache():
    """Clear the query cache before each test for isolation."""
    from app.connectors.cache import get_cache
    get_cache().clear()
    yield
    get_cache().clear()


@pytest.fixture(autouse=True)
def _setup_embed_repo():
    """Inject an InMemoryRepo with a seeded board so /embed/config can look it up."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    # The embed token carries org="acme-org"; seed a board under that org_id.
    # We use a fixed id so the tests can reference it by path.
    import asyncio

    # Seed the board synchronously by inserting directly into the store.
    board_id = "dash-42"
    repo._store["boards"][board_id] = {
        "id": board_id,
        "org_id": "acme-org",
        "created_by": "embed-user-1",
        "name": "Test Dashboard",
        "config": {
            "widgets": [
                {"id": "w1", "type": "table", "query_id": "demo_all"}
            ],
            "theme": {},
        },
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    set_repo(repo)
    yield
    set_repo(None)


# The `app` and `client` fixtures come from conftest.py (shared).


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _parse_arrow(content: bytes):
    """Parse Arrow IPC stream bytes into a pyarrow Table."""
    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all()


# ===========================================================================
# (a) No token → 401
# ===========================================================================


@pytest.mark.asyncio
async def test_query_no_token_returns_401(client):
    """POST /query without any Authorization header → 401 unauthorized."""
    resp = await client.post("/api/v1/query", json={"sql": "SELECT * FROM demo"})
    assert resp.status_code == 401


# ===========================================================================
# (b) Embed token WITHOUT a read scope → 403
# ===========================================================================


@pytest.mark.asyncio
async def test_query_embed_token_no_read_scope_returns_403(client):
    """Embed token that carries only write scopes → 403 insufficient_scope."""
    token = _mint_embed_token(scope=["edit:dashboard:abc"])
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "insufficient_scope"


# ===========================================================================
# (c) Embed token with wrong Origin → 403
# ===========================================================================


@pytest.mark.asyncio
async def test_query_embed_token_wrong_origin_returns_403(client):
    """Embed token has embed_origin=host.example but request Origin is evil.example → 403."""
    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": "https://evil.example",  # wrong origin
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "origin_mismatch"


# ===========================================================================
# (d) Valid embed token → 200 Arrow IPC
# ===========================================================================


@pytest.mark.asyncio
async def test_query_valid_embed_token_returns_arrow_ipc(client):
    """Embed token with read scope + matching origin + query_id → 200 Arrow IPC stream."""
    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 200, resp.text
    assert "application/vnd.apache.arrow.stream" in resp.headers.get("content-type", "")
    table = _parse_arrow(resp.content)
    assert table.num_rows > 0


@pytest.mark.asyncio
async def test_query_read_star_scope_also_passes(client):
    """Embed token with read:* scope satisfies read:query via wildcard."""
    token = _mint_embed_token(scope=["read:*"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_query_read_dashboard_star_scope_also_passes(client):
    """Embed token with read:dashboard:* scope satisfies read:query via wildcard."""
    token = _mint_embed_token(scope=["read:dashboard:*"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 200, resp.text


# ===========================================================================
# (e) body.claims.policies IGNORED — policies come from the token
# ===========================================================================


@pytest.mark.asyncio
async def test_body_claims_policies_are_ignored(client):
    """
    SECURITY: Send body.claims.policies = {tenant_id: 'attacker'} with an embed
    token whose policies are {tenant_id: 'acme'}.  Assert the planner was called
    with claims = {policies: {tenant_id: 'acme'}} — not the attacker-injected value.

    Strategy: monkeypatch planner_plan to spy on the ``claims`` arg, then return
    a real plan (with no RLS, so DuckDB can execute it against the demo table).
    The ``policies`` captured from the spy must be {'tenant_id': 'acme'} even
    though the request body carried {'tenant_id': 'attacker'}.
    """
    import pyarrow as pa
    from app.connectors import plan as real_plan
    from app.connectors.plan import PhysicalPlan
    from app.connectors.cache_key import compute_cache_key

    captured_claims: dict[str, Any] = {}

    def spy_plan(sql, claims=None, params=None, **kwargs):
        """Capture claims, then return a plan WITHOUT the RLS predicate for execution."""
        captured_claims.update(claims or {})
        # The real planner would inject tenant_id predicate which DuckDB demo
        # table can't satisfy.  We return a clean plan (no RLS) so the query
        # executes successfully, while still proving the route handler called us
        # with the TOKEN's policies, not the body's.
        clean_plan = real_plan(sql, claims={}, params=params or [], **kwargs)
        return clean_plan

    token = _mint_embed_token(
        scope=["read:query"],
        policies={"tenant_id": "acme"},  # token carries acme
        embed_origin=_EMBED_ORIGIN,
    )

    with patch("app.routes.query.planner_plan", side_effect=spy_plan):
        resp = await client.post(
            "/api/v1/query",
            json={
                "query_id": "demo_all",  # embed tokens must use query_id
                # Attacker tries to override policies via the request body.
                "claims": {"policies": {"tenant_id": "attacker"}},
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
            },
        )

    assert resp.status_code == 200, resp.text

    # The planner MUST have been called with the token's policies, NOT the body's.
    actual_policies = captured_claims.get("policies", {})
    assert actual_policies == {"tenant_id": "acme"}, (
        f"SECURITY FAILURE: planner received policies={actual_policies!r} "
        f"but expected {{'tenant_id': 'acme'}} from the token. "
        f"The body-injected attacker policy must be ignored."
    )
    assert actual_policies.get("tenant_id") != "attacker", (
        "SECURITY FAILURE: attacker-injected tenant_id='attacker' was NOT ignored."
    )


# ===========================================================================
# (f) First-party HS256 access token (backwards-compat)
# ===========================================================================


@pytest.mark.asyncio
async def test_first_party_token_still_works(client):
    """First-party HS256 access token (scope read:*) still returns 200 Arrow IPC."""
    from app.auth.jwt import mint_access_token

    token = mint_access_token("user-fp-1")
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    assert table.num_rows > 0


@pytest.mark.asyncio
async def test_first_party_token_no_rls_returns_all_rows(client):
    """First-party access token has empty policies → no RLS filter → all demo rows."""
    from app.auth.jwt import mint_access_token

    token = mint_access_token("user-fp-2")
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    # The demo table has 5 rows; first-party gets all of them (no RLS).
    assert table.num_rows == 5


# ===========================================================================
# (g) GET /embed/config/{id} — happy path + auth
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_config_with_valid_token_returns_descriptor(client):
    """GET /embed/config/{id} with a valid embed token returns the stub descriptor."""
    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)
    resp = await client.get(
        "/api/v1/embed/config/dash-42",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dashboard_id"] == "dash-42"
    assert isinstance(body["widgets"], list)
    assert len(body["widgets"]) >= 1
    assert body["widgets"][0]["type"] == "table"
    assert "theme" in body


# ===========================================================================
# (h) GET /embed/config/{id} without token → 401
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_config_no_token_returns_401(client):
    """GET /embed/config/{id} without a token → 401 unauthorized."""
    resp = await client.get("/api/v1/embed/config/dash-99")
    assert resp.status_code == 401


# ===========================================================================
# Planner-level RLS unit test — verify token → identity.policies → claims
# ===========================================================================


def test_planner_rls_from_verified_token_policies():
    """
    Unit-level proof that the route's policy derivation chain is correct:
    verify_token → identity.policies → claims passed to planner.plan.

    Steps:
    1. Mint an embed token with policies={tenant_id: acme}.
    2. verify_token() → VerifiedIdentity with policies={tenant_id: acme}.
    3. Build claims = {"policies": identity.policies}.
    4. Call planner.plan("SELECT * FROM demo2", claims) → check the SQL
       contains the RLS predicate 'acme'.
    5. Confirm the cache_key differs from one using {tenant_id: globex} proving
       per-tenant cache isolation.
    """
    from app.auth.verify import verify_token
    from app.connectors.planner import plan

    token = _mint_embed_token(
        scope=["read:query"],
        policies={"tenant_id": "acme"},
        embed_origin=None,  # skip origin check in this unit test
    )
    identity = verify_token(token, expected_origin=None)

    assert identity.kind == "embed"
    assert identity.policies == {"tenant_id": "acme"}

    # Build claims the same way the route handler does.
    claims = {"policies": identity.policies}

    physical_plan = plan("SELECT id FROM demo", claims=claims)

    # The SQL must contain the RLS predicate.
    assert "acme" in physical_plan.sql, (
        f"Planner did not inject 'acme' predicate. SQL: {physical_plan.sql}"
    )
    assert "tenant_id" in physical_plan.sql.lower()

    # Now do the same for a different tenant to prove cache isolation.
    token_globex = _mint_embed_token(
        scope=["read:query"],
        policies={"tenant_id": "globex"},
        embed_origin=None,
    )
    identity_globex = verify_token(token_globex, expected_origin=None)
    claims_globex = {"policies": identity_globex.policies}
    plan_globex = plan("SELECT id FROM demo", claims=claims_globex)

    assert "globex" in plan_globex.sql
    # Cache keys MUST differ (per-tenant cache isolation safety property).
    assert physical_plan.cache_key != plan_globex.cache_key, (
        "CACHE ISOLATION FAILURE: acme and globex produced the same cache key!"
    )


# ===========================================================================
# Cache HIT/MISS still works with embed tokens
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_token_cache_hit_miss_headers(client):
    """Two identical embed queries (via query_id): first MISS, second HIT."""
    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)
    headers = {"Authorization": f"Bearer {token}", "Origin": _EMBED_ORIGIN}

    resp1 = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers=headers,
    )
    assert resp1.status_code == 200
    assert resp1.headers.get("x-nubi-cache") == "MISS"

    resp2 = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers=headers,
    )
    assert resp2.status_code == 200
    assert resp2.headers.get("x-nubi-cache") == "HIT"
