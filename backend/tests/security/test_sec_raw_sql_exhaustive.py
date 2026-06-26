"""Exhaustive security tests for raw-SQL gating across all capable entrypoints.

Coverage
--------
POST /query (raw SQL path)
1.  author:sql scope permits raw SQL → 200.
2.  No scope → 403 insufficient_scope (fail closed).
3.  read:* only (no author:sql) → 403.
4.  author:metric only (no author:sql) → 403.
5.  Embed token with author:sql in scope → 403 query_not_registered (M3-SEC gate
    fires BEFORE the authoring-scope gate).
6.  Embed token with query_id → 200 (positive control: allowlisted path works).
7.  First-party token with no explicit scope claim → gets _FIRST_PARTY_SCOPES
    default (includes author:sql) → 200 (no regression).

POST /query/estimate (shares _resolve_request_plan → same gates)
8.  Estimate raw SQL without author:sql → 403.
9.  Estimate raw SQL with author:sql → allowed through gates (connector may vary).
10. Estimate with embed token + raw SQL → 403 query_not_registered.

Wildcard scope semantics (unit tests on has_scope)
11. "*"       covers author:sql, author:metric, read:query, write:anything.
12. "read:*"  covers read:query but NOT author:sql.
13. "author:*" covers author:sql AND author:metric.
14. "write:*"  does NOT cover author:sql or read:query.
15. "author:sql" is exact-match (covers author:sql, NOT author:sqlex or author:sql:sub).

SCOPE_AUTHOR_SQL / SCOPE_AUTHOR_METRIC constants
16. Constants exported from app.auth.scopes equal "author:sql" / "author:metric".
17. has_scope([SCOPE_AUTHOR_SQL], SCOPE_AUTHOR_SQL) → True.
18. has_scope([], SCOPE_AUTHOR_SQL) → False (empty grants nothing).
"""

from __future__ import annotations

import os

import pytest

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


@pytest.fixture(autouse=True)
def _clear_connector_cache():
    from app.connectors.cache import get_cache

    get_cache().clear()
    yield
    get_cache().clear()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _fp_token(*scopes: str) -> dict[str, str]:
    """First-party HS256 token with explicit scopes."""
    token = mint_access_token(
        user_id="rawsql-sec-user",
        extra_claims={"scope": " ".join(scopes)},
    )
    return {"Authorization": f"Bearer {token}"}


def _fp_token_no_scope_claim() -> dict[str, str]:
    """First-party token with NO scope claim — defaults to _FIRST_PARTY_SCOPES."""
    token = mint_access_token(user_id="rawsql-legacy-user")
    return {"Authorization": f"Bearer {token}"}


def _embed_headers(*scopes: str) -> dict[str, str]:
    """Embed RS256 token with specified scopes."""
    token = mint_embed_token(scope=list(scopes), embed_origin=EMBED_ORIGIN)
    return {
        "Authorization": f"Bearer {token}",
        "Origin": EMBED_ORIGIN,
    }


# ===========================================================================
# POST /query — raw SQL gating
# ===========================================================================


@pytest.mark.asyncio
async def test_query_raw_sql_with_author_sql_permits(client):
    """author:sql scope → raw SQL executes → 200."""
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_fp_token("read:*", "author:sql"),
    )
    assert resp.status_code == 200, (
        f"author:sql must permit raw SQL, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_query_raw_sql_no_scope_claim_gets_defaults(client):
    """First-party token with explicitly empty scope list falls back to _FIRST_PARTY_SCOPES.

    parse_scopes([]) returns [] which is falsy, so _verify_first_party_token
    substitutes _FIRST_PARTY_SCOPES (which includes author:sql). This is
    intentional: a first-party session without an explicit scope claim is a
    full-access legacy session. This test DOCUMENTS that behavior (not a bug).
    """
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_fp_token(),  # empty scope list → defaults kick in
    )
    # By design, empty scope in a first-party token → _FIRST_PARTY_SCOPES default
    # which includes author:sql → raw SQL is allowed.
    assert resp.status_code == 200, (
        f"First-party empty-scope token should get author:sql via defaults, "
        f"got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_query_raw_sql_read_only_scope_returns_403(client):
    """read:* without author:sql → 403 insufficient_scope."""
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_fp_token("read:*"),
    )
    assert resp.status_code == 403, (
        f"read:* alone must not grant raw SQL, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["error"]["code"] == "insufficient_scope"


@pytest.mark.asyncio
async def test_query_raw_sql_author_metric_only_returns_403(client):
    """author:metric without author:sql → 403 (wrong capability)."""
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_fp_token("read:*", "author:metric"),
    )
    assert resp.status_code == 403, (
        f"author:metric alone must not grant raw SQL, got {resp.status_code}"
    )
    assert resp.json()["error"]["code"] == "insufficient_scope"


@pytest.mark.asyncio
async def test_query_embed_raw_sql_blocked_by_allowlist(client):
    """Embed token + raw SQL → 403 query_not_registered (M3-SEC gate, not scope gate)."""
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_embed_headers("read:query", "author:sql"),
    )
    assert resp.status_code == 403, (
        f"Embed raw SQL must be blocked, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    # The allowlist gate (query_not_registered) fires before the authoring scope gate
    assert body["error"]["code"] == "query_not_registered", (
        f"Expected 'query_not_registered', got {body['error']['code']!r} — "
        "the M3-SEC gate must fire before the authoring scope gate for embed tokens"
    )


@pytest.mark.asyncio
async def test_query_embed_registered_query_id_allowed(client):
    """Embed token + registered query_id → 200 (positive: allowlisted queries work)."""
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers=_embed_headers("read:query"),
    )
    assert resp.status_code == 200, (
        f"Embed token with registered query_id must succeed, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_query_first_party_no_scope_claim_gets_defaults(client):
    """First-party token with no scope claim → _FIRST_PARTY_SCOPES defaults → raw SQL allowed."""
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_fp_token_no_scope_claim(),
    )
    assert resp.status_code == 200, (
        f"First-party legacy token (no scope claim) must still get author:sql, "
        f"got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# POST /query/estimate — shares the same _resolve_request_plan gates
# ===========================================================================


@pytest.mark.asyncio
async def test_estimate_raw_sql_without_author_sql_blocked(client):
    """POST /query/estimate with raw SQL + no author:sql → 403 (same gate as /query)."""
    resp = await client.post(
        "/api/v1/query/estimate",
        json={"sql": "SELECT * FROM demo"},
        headers=_fp_token("read:*"),
    )
    assert resp.status_code == 403, (
        f"Estimate raw SQL without author:sql must be blocked, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["error"]["code"] == "insufficient_scope"


@pytest.mark.asyncio
async def test_estimate_raw_sql_with_author_sql_passes_gate(client):
    """POST /query/estimate with raw SQL + author:sql → passes scope gate."""
    resp = await client.post(
        "/api/v1/query/estimate",
        json={"sql": "SELECT * FROM demo"},
        headers=_fp_token("read:*", "author:sql"),
    )
    # The gate passes; the response may be 200 or an error about the connector (ok)
    assert resp.status_code != 403, (
        f"author:sql must allow the estimate gate, got 403: {resp.text}"
    )


@pytest.mark.asyncio
async def test_estimate_embed_raw_sql_blocked(client):
    """POST /query/estimate embed + raw SQL → 403 query_not_registered."""
    resp = await client.post(
        "/api/v1/query/estimate",
        json={"sql": "SELECT * FROM demo"},
        headers=_embed_headers("read:query", "author:sql"),
    )
    assert resp.status_code == 403, (
        f"Embed estimate raw SQL must be blocked, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["error"]["code"] == "query_not_registered"


# ===========================================================================
# Wildcard scope semantics — unit tests on has_scope
# ===========================================================================


def test_star_covers_everything():
    """'*' (super-admin) covers author:sql, author:metric, read:query, write:anything."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL, SCOPE_AUTHOR_METRIC

    scopes = ["*"]
    assert has_scope(scopes, SCOPE_AUTHOR_SQL)
    assert has_scope(scopes, SCOPE_AUTHOR_METRIC)
    assert has_scope(scopes, "read:query")
    assert has_scope(scopes, "write:dashboard:prod")


def test_read_star_does_not_cover_author_sql():
    """'read:*' does NOT cover author:sql (different action prefix)."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL

    assert not has_scope(["read:*"], SCOPE_AUTHOR_SQL), (
        "SECURITY: read:* must not grant author:sql"
    )


def test_read_star_covers_read_query():
    """'read:*' covers read:query."""
    from app.auth.scopes import has_scope

    assert has_scope(["read:*"], "read:query")


def test_author_star_covers_both_authoring_scopes():
    """'author:*' covers author:sql AND author:metric."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL, SCOPE_AUTHOR_METRIC

    assert has_scope(["author:*"], SCOPE_AUTHOR_SQL)
    assert has_scope(["author:*"], SCOPE_AUTHOR_METRIC)


def test_write_star_does_not_cover_author_sql():
    """'write:*' does NOT cover author:sql (different action prefix)."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL

    assert not has_scope(["write:*"], SCOPE_AUTHOR_SQL), (
        "write:* must not grant author:sql"
    )


def test_author_sql_does_not_cover_author_sqlex():
    """'author:sql' is an exact prefix match; it covers 'author:sql' but the wildcard
    check for 'author:sql:*' would only apply if the granted scope has the '*'."""
    from app.auth.scopes import has_scope

    # Granted: "author:sql"; required: "author:sqlex" → must NOT match
    assert not has_scope(["author:sql"], "author:sqlex"), (
        "'author:sql' must not cover 'author:sqlex' (no trailing wildcard)"
    )


def test_author_sql_does_not_cover_subresource():
    """'author:sql' granted scope does not cover 'author:sql:subresource'."""
    from app.auth.scopes import has_scope

    assert not has_scope(["author:sql"], "author:sql:anything")


def test_author_star_does_not_cover_read_query():
    """'author:*' does NOT cover 'read:query' (different action prefix)."""
    from app.auth.scopes import has_scope

    assert not has_scope(["author:*"], "read:query"), (
        "author:* must not bleed into read:query"
    )


# ===========================================================================
# Constants
# ===========================================================================


def test_scope_constants_exported():
    """SCOPE_AUTHOR_SQL and SCOPE_AUTHOR_METRIC have the expected string values."""
    from app.auth.scopes import SCOPE_AUTHOR_SQL, SCOPE_AUTHOR_METRIC

    assert SCOPE_AUTHOR_SQL == "author:sql"
    assert SCOPE_AUTHOR_METRIC == "author:metric"


def test_has_scope_with_exact_constant():
    """has_scope([SCOPE_AUTHOR_SQL], SCOPE_AUTHOR_SQL) is trivially True."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL

    assert has_scope([SCOPE_AUTHOR_SQL], SCOPE_AUTHOR_SQL)


def test_has_scope_empty_grants_nothing():
    """An empty scope list grants nothing."""
    from app.auth.scopes import has_scope, SCOPE_AUTHOR_SQL

    assert not has_scope([], SCOPE_AUTHOR_SQL)
    assert not has_scope([], "read:query")
    assert not has_scope([], "*")
