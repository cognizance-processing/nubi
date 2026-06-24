"""Security tests for author:sql / author:metric capability-gating.

Covers
------
1. Token WITH ``author:sql`` runs raw SQL successfully → 200.
2. Token WITHOUT ``author:sql`` (only ``read:*``) → 403 on raw SQL (fail closed).
3. Token with only ``author:metric`` (no ``author:sql``) → 403 on raw SQL.
4. Token with no scopes at all (first-party default) → gets author:sql via
   ``_FIRST_PARTY_SCOPES`` fallback → 200 (no regression for existing sessions).
5. Embed token (kind=embed) is still blocked for raw SQL by the M3-SEC allowlist
   gate, independent of authoring scopes (``query_not_registered`` error, not
   ``insufficient_scope``).
6. First-party token with ``query_id`` (registered path) works without ``author:sql``
   — governed queries do not require the authoring scope.
7. ``SCOPE_AUTHOR_SQL`` / ``SCOPE_AUTHOR_METRIC`` constants are exported from
   ``app.auth.scopes``.

Test strategy
-------------
- Reuses the RSA keypair + embed token helpers from conftest_helpers.
- First-party (HS256) tokens are minted via ``mint_access_token`` with explicit
  ``scope`` extra_claims to test specific scope combinations.
- The app / client fixtures are pulled from the shared conftest.py (autouse
  FakeDB patches).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, patch

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
os.environ.setdefault(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback"
)
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

from tests.security.conftest_helpers import (  # noqa: E402
    mint_embed_token,
    mint_access_token,
    STATIC_JWKS,
    HOST_ISS,
    HOST_AUD,
    EMBED_ORIGIN,
    KID,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_issuer():
    """Register the test embed issuer; unregister + clear caches after each test."""
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


@pytest.fixture(autouse=True)
def _clear_cache():
    from app.connectors.cache import get_cache

    get_cache().clear()
    yield
    get_cache().clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_arrow(content: bytes):
    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all()


def _mint_fp_token_with_scopes(*scopes: str) -> str:
    """Mint a first-party HS256 token carrying an explicit scope list.

    Passing an explicit ``scope`` claim bypasses the ``_FIRST_PARTY_SCOPES``
    default so we can test restricted-scope first-party tokens.
    """
    return mint_access_token(
        user_id="authoring-scope-test-user",
        extra_claims={"scope": " ".join(scopes)},
    )


# ---------------------------------------------------------------------------
# 1. Token WITH author:sql → raw SQL succeeds (200)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_sql_scope_permits_raw_sql(client):
    """A first-party token carrying author:sql can run raw SQL → 200."""
    token = _mint_fp_token_with_scopes("read:*", "author:sql")
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, (
        f"Expected 200 with author:sql scope, got {resp.status_code}: {resp.text}"
    )
    table = _parse_arrow(resp.content)
    assert table.num_rows == 5


# ---------------------------------------------------------------------------
# 2. Token WITHOUT author:sql (read:* only) → 403 on raw SQL (fail closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_author_sql_blocks_raw_sql(client):
    """A token with only read:* (no author:sql) is rejected with 403 on raw SQL."""
    token = _mint_fp_token_with_scopes("read:*")
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, (
        f"SECURITY FAILURE: raw SQL permitted without author:sql scope "
        f"(got {resp.status_code}).  The authoring gate is NOT enforced."
    )
    body = resp.json()
    assert body["error"]["code"] == "insufficient_scope", (
        f"Expected error code 'insufficient_scope', got {body['error']['code']!r}"
    )


# ---------------------------------------------------------------------------
# 3. Token with author:metric only (no author:sql) → 403 on raw SQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_author_metric_only_blocks_raw_sql(client):
    """author:metric without author:sql is insufficient for raw SQL → 403."""
    token = _mint_fp_token_with_scopes("read:*", "author:metric")
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 when author:sql absent (got {resp.status_code})"
    )
    body = resp.json()
    assert body["error"]["code"] == "insufficient_scope"


# ---------------------------------------------------------------------------
# 4. First-party token with NO explicit scope → default includes author:sql
#    (no regression for existing admin/analyst sessions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_party_default_scopes_include_author_sql(client):
    """Existing first-party sessions (no scope claim) keep raw-SQL access via
    _FIRST_PARTY_SCOPES default — no regression for admins / analysts."""
    # mint_access_token with no extra_claims → no scope claim → defaults kick in
    token = mint_access_token(user_id="legacy-analyst-user")
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, (
        f"REGRESSION: existing first-party session lost raw-SQL access "
        f"(got {resp.status_code}): {resp.text}"
    )
    table = _parse_arrow(resp.content)
    assert table.num_rows == 5


# ---------------------------------------------------------------------------
# 5. Embed token → still blocked for raw SQL (M3-SEC allowlist, not scope gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_token_raw_sql_blocked_by_allowlist(client):
    """Embed token raw SQL is blocked by the M3-SEC allowlist gate (not scope).

    The error code must be 'query_not_registered' (allowlist gate), NOT
    'insufficient_scope' (scope gate).  The embed path is gated before the
    authoring scope check is even reached.
    """
    token = mint_embed_token(
        scope=["read:query", "author:sql"],  # even with author:sql in scope
        embed_origin=EMBED_ORIGIN,
    )
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 403, (
        f"SECURITY FAILURE: embed token raw SQL not blocked (got {resp.status_code})"
    )
    body = resp.json()
    assert body["error"]["code"] == "query_not_registered", (
        f"Expected 'query_not_registered' (M3-SEC gate), "
        f"got {body['error']['code']!r}"
    )


# ---------------------------------------------------------------------------
# 6. First-party token with read:* only → can use a registered query_id (200)
#    Governed queries do not require author:sql
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_query_does_not_require_author_sql(client):
    """A read-only token can execute via query_id without author:sql → 200."""
    token = _mint_fp_token_with_scopes("read:*")
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, (
        f"Governed query path should not require author:sql "
        f"(got {resp.status_code}): {resp.text}"
    )
    table = _parse_arrow(resp.content)
    assert table.num_rows > 0


# ---------------------------------------------------------------------------
# 7. SCOPE_AUTHOR_SQL / SCOPE_AUTHOR_METRIC constants exported from scopes
# ---------------------------------------------------------------------------


def test_authoring_scope_constants_exported():
    """SCOPE_AUTHOR_SQL and SCOPE_AUTHOR_METRIC are importable from app.auth.scopes."""
    from app.auth.scopes import SCOPE_AUTHOR_SQL, SCOPE_AUTHOR_METRIC

    assert SCOPE_AUTHOR_SQL == "author:sql"
    assert SCOPE_AUTHOR_METRIC == "author:metric"


# ---------------------------------------------------------------------------
# 8. has_scope wildcard: author:* covers both authoring scopes
# ---------------------------------------------------------------------------


def test_author_wildcard_covers_authoring_scopes():
    """A token with 'author:*' satisfies both author:sql and author:metric."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL, SCOPE_AUTHOR_METRIC

    scopes = ["read:*", "author:*"]
    assert has_scope(scopes, SCOPE_AUTHOR_SQL), "author:* should cover author:sql"
    assert has_scope(scopes, SCOPE_AUTHOR_METRIC), "author:* should cover author:metric"


def test_super_admin_wildcard_covers_authoring_scopes():
    """Super-admin '*' scope covers all authoring scopes."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL, SCOPE_AUTHOR_METRIC

    assert has_scope(["*"], SCOPE_AUTHOR_SQL)
    assert has_scope(["*"], SCOPE_AUTHOR_METRIC)


def test_read_star_does_not_cover_author_sql():
    """read:* does NOT cover author:sql — different action prefix."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL

    assert not has_scope(["read:*"], SCOPE_AUTHOR_SQL), (
        "read:* must not grant author:sql (different action prefix)"
    )
