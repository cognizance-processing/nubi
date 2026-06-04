"""M3-SEC: Embed-token query allowlist enforcement tests.

What this suite verifies
------------------------
(A) THE GAP NOW CLOSED: embed token + raw sql (no query_id) → 403
    query_not_registered.
(B) Embed token + valid query_id → 200 Arrow IPC, and the executed SQL is the
    REGISTERED sql (not the body.sql).  Proved by monkeypatching planner_plan
    to capture the sql argument: body.sql="SELECT * FROM users" with
    query_id="demo_all" → planner receives "SELECT * FROM demo", not users.
(C) Embed token + unknown query_id → 403 query_not_registered.
(D) First-party access token + raw sql → still 200 (no regression).
(E) RLS still applies to registered queries: policies from the token are
    injected by the planner even when executing via query_id.
(F) First-party access token + valid query_id → 200 (optional resolution path).

Test strategy
-------------
- Reuses the RSA keypair + _mint_embed_token helper pattern from test_embed_rls.py
  (duplicated here for isolation when run standalone).
- Reuses the app/client fixtures from conftest.py (autouse FakeDB patches).
- Monkeypatches app.routes.query.planner_plan to capture the sql arg.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
import pyarrow.ipc as pa_ipc

# ---------------------------------------------------------------------------
# Environment bootstrap (before any app import)
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
_JWKS_KEY["kid"] = "allowlist-test-key"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HOST_ISS = "https://allowlist-host.example"
_HOST_AUD = "nubi"
_EMBED_ORIGIN = "https://allowlist-host.example"
_KID = "allowlist-test-key"


# ---------------------------------------------------------------------------
# Token mint helper
# ---------------------------------------------------------------------------


def _mint_embed_token(
    *,
    iss: str = _HOST_ISS,
    aud: str = _HOST_AUD,
    sub: str = "allowlist-user-1",
    scope: list[str] | None = None,
    policies: dict | None = None,
    embed_origin: str | None = _EMBED_ORIGIN,
    exp_delta: int = 300,
) -> str:
    """Mint a test embed JWT signed with the module-level RSA private key."""
    if scope is None:
        scope = ["read:query"]
    if policies is None:
        policies = {}

    now = datetime.now(tz=timezone.utc)
    payload: dict = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "org": "test-org",
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
    """Register the test embed issuer for the allowlist suite; clean up after."""
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
    """Clear the query cache before and after each test for isolation."""
    from app.connectors.cache import get_cache
    get_cache().clear()
    yield
    get_cache().clear()


# ---------------------------------------------------------------------------
# Arrow helper
# ---------------------------------------------------------------------------


def _parse_arrow(content: bytes):
    """Parse Arrow IPC stream bytes into a pyarrow Table."""
    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all()


# ===========================================================================
# (A) THE GAP NOW CLOSED: embed token + raw sql (no query_id) → 403
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_raw_sql_blocked(client):
    """THE M3-SEC GAP IS NOW CLOSED.

    An embed token sending raw sql with NO query_id must be rejected with
    403 query_not_registered.  Previously this returned 200 (the gap).
    """
    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 403, (
        f"SECURITY FAILURE: embed token + raw sql returned {resp.status_code} "
        f"instead of 403.  The M3-SEC gap is NOT closed."
    )
    body = resp.json()
    assert body["error"]["code"] == "query_not_registered", (
        f"Expected error code 'query_not_registered', got {body['error']['code']!r}"
    )


@pytest.mark.asyncio
async def test_embed_raw_sql_with_read_star_scope_blocked(client):
    """Even a read:* scoped embed token cannot run raw sql without a query_id."""
    token = _mint_embed_token(scope=["read:*"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "query_not_registered"


# ===========================================================================
# (B) Embed token + valid query_id → 200 + uses registry SQL (not body.sql)
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_valid_query_id_returns_arrow_ipc(client):
    """Embed token + valid query_id 'demo_all' → 200 Arrow IPC."""
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
async def test_embed_body_sql_ignored_registry_sql_used(client):
    """The REGISTERED SQL is executed, not body.sql.

    Send body.sql="SELECT * FROM users" (a table that doesn't exist in DuckDB
    demo) together with query_id="demo_all".  The planner must receive
    "SELECT * FROM demo" (the registry SQL), NOT "SELECT * FROM users".
    Proved by monkeypatching planner_plan to capture the sql argument.
    """
    from app.connectors import plan as real_plan

    captured_sql: list[str] = []

    def spy_plan(sql, claims=None, params=None, **kwargs):
        """Capture sql, then forward to the real planner with the same sql."""
        captured_sql.append(sql)
        return real_plan(sql, claims={}, params=params or [], **kwargs)

    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)

    with patch("app.routes.query.planner_plan", side_effect=spy_plan):
        resp = await client.post(
            "/api/v1/query",
            json={
                # body.sql is a table that does NOT exist in the demo DuckDB —
                # if it were executed the request would fail.
                "sql": "SELECT * FROM users",
                # The registered query references "demo" (which does exist).
                "query_id": "demo_all",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
            },
        )

    assert resp.status_code == 200, (
        f"Expected 200 but got {resp.status_code}: {resp.text}\n"
        "If the planner received body.sql ('users') the DuckDB query would fail."
    )
    assert len(captured_sql) == 1, "Planner spy was not called."
    executed_sql = captured_sql[0]
    # The planner must have received the REGISTRY sql, not body.sql.
    assert "users" not in executed_sql.lower(), (
        f"SECURITY: planner received body.sql containing 'users': {executed_sql!r}. "
        f"The registry SQL should have been used instead."
    )
    assert "demo" in executed_sql.lower(), (
        f"Planner did not receive the expected registry SQL. Got: {executed_sql!r}"
    )


@pytest.mark.asyncio
async def test_embed_demo_active_query_id_returns_filtered_rows(client):
    """Embed token + query_id='demo_active' returns only active rows."""
    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_active"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    # demo table has 5 rows; 3 are active (ids 1, 3, 5).
    assert table.num_rows == 3, (
        f"Expected 3 active rows from demo_active, got {table.num_rows}"
    )


# ===========================================================================
# (C) Embed token + unknown query_id → 403 query_not_registered
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_unknown_query_id_returns_403(client):
    """Embed token + unknown query_id → 403 query_not_registered."""
    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "does_not_exist"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert body["error"]["code"] == "query_not_registered"


@pytest.mark.asyncio
async def test_embed_empty_string_query_id_counts_as_raw_sql(client):
    """An empty string query_id is treated as 'no query_id' → 403."""
    token = _mint_embed_token(scope=["read:query"], embed_origin=_EMBED_ORIGIN)
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo", "query_id": ""},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    # Empty string is falsy — treated as missing query_id.
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "query_not_registered"


# ===========================================================================
# (D) First-party access token + raw sql → still 200 (no regression)
# ===========================================================================


@pytest.mark.asyncio
async def test_first_party_raw_sql_still_works(client):
    """First-party HS256 access token can still run raw sql (no regression)."""
    from app.auth.jwt import mint_access_token

    token = mint_access_token("fp-user-allowlist-1")
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    assert table.num_rows == 5  # all 5 demo rows; no RLS on first-party


@pytest.mark.asyncio
async def test_first_party_can_also_use_query_id(client):
    """First-party token may optionally use a query_id to resolve registry SQL."""
    from app.auth.jwt import mint_access_token

    token = mint_access_token("fp-user-allowlist-2")
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_active"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    assert table.num_rows == 3  # 3 active rows


@pytest.mark.asyncio
async def test_first_party_unknown_query_id_returns_403(client):
    """First-party token + unknown query_id → 403 query_not_registered."""
    from app.auth.jwt import mint_access_token

    token = mint_access_token("fp-user-allowlist-3")
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "nonexistent_query"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "query_not_registered"


# ===========================================================================
# (E) RLS still applies to registered queries
# ===========================================================================


@pytest.mark.asyncio
async def test_rls_still_applies_via_query_id(client):
    """RLS policies from the embed token are still injected for registered queries.

    Proved by monkeypatching planner_plan to capture the claims argument: the
    token carries policies={tenant_id: 'acme'} and the planner must receive
    exactly those policies even when the SQL came from the registry.
    """
    from app.connectors import plan as real_plan

    captured_claims: dict[str, Any] = {}

    def spy_plan(sql, claims=None, params=None, **kwargs):
        captured_claims.update(claims or {})
        # Return a clean plan (no RLS) so DuckDB demo table can execute it.
        return real_plan(sql, claims={}, params=params or [], **kwargs)

    token = _mint_embed_token(
        scope=["read:query"],
        policies={"tenant_id": "acme"},
        embed_origin=_EMBED_ORIGIN,
    )

    with patch("app.routes.query.planner_plan", side_effect=spy_plan):
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_all"},
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": _EMBED_ORIGIN,
            },
        )

    assert resp.status_code == 200, resp.text

    actual_policies = captured_claims.get("policies", {})
    assert actual_policies == {"tenant_id": "acme"}, (
        f"RLS policies not passed to planner.  "
        f"Expected {{'tenant_id': 'acme'}}, got {actual_policies!r}."
    )


# ===========================================================================
# (F) Registry introspection — demo queries are seeded correctly
# ===========================================================================


def test_registry_seeds_are_present():
    """The module-level registry has the expected demo seed queries."""
    from app.queries import get_query_registry

    reg = get_query_registry()
    demo_all = reg.get("demo_all")
    assert demo_all is not None
    assert "demo" in demo_all.sql.lower()

    demo_active = reg.get("demo_active")
    assert demo_active is not None
    assert "active" in demo_active.sql.lower()

    assert reg.get("nonexistent") is None


def test_registry_all_returns_all_entries():
    """QueryRegistry.all() returns at least the two seeded demo queries."""
    from app.queries import get_query_registry

    reg = get_query_registry()
    all_queries = reg.all()
    ids = [q.id for q in all_queries]
    assert "demo_all" in ids
    assert "demo_active" in ids


def test_registry_unregister_then_get_returns_none():
    """unregister() removes a query; subsequent get() returns None."""
    from app.queries import get_query_registry

    reg = get_query_registry()
    reg.register("temp_query", "SELECT 1", "Temporary")
    assert reg.get("temp_query") is not None
    reg.unregister("temp_query")
    assert reg.get("temp_query") is None


def test_registered_query_is_immutable():
    """RegisteredQuery is a frozen dataclass — mutation raises an error."""
    from app.queries import RegisteredQuery
    import dataclasses

    rq = RegisteredQuery(id="x", sql="SELECT 1", name="X")
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        rq.sql = "DROP TABLE users"  # type: ignore[misc]
