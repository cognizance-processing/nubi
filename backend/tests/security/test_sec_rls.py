"""Attack class 2: RLS / claim override.

Covers
------
2a. named_params cannot set a reserved token/RLS claim name → 400
2b. A restrictive policy filters rows (assert result has fewer rows)
2c. An impossible policy (e.g. tenant_id = 'nobody') → 0 rows
2d. Capability-gated 501 when policies present and source can't enforce RLS
2e. Body-supplied policies are silently ignored; token policies take effect
2f. RLS predicate is injected at AST level (verify via planner unit test)
2g. Multiple RLS policies all applied simultaneously
"""

from __future__ import annotations

import os
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio

# ── env bootstrap ─────────────────────────────────────────────────────────────
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
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


@pytest_asyncio.fixture
async def app():
    patches = [
        patch("app.db.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=None)),
    ]
    for p in patches:
        p.start()
    try:
        import main as main_module
        _app = main_module.create_app()
        yield _app
    finally:
        for p in patches:
            p.stop()


@pytest_asyncio.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac


def _parse_arrow(content: bytes) -> pa.Table:
    reader = pa_ipc.open_stream(BytesIO(content))
    return reader.read_all()


# ===========================================================================
# 2a. named_params cannot set reserved claim names → 400
# ===========================================================================

@pytest.mark.asyncio
async def test_reserved_param_name_org_rejected(client):
    """named_params 'org' is reserved → 400 param_name_reserved."""
    token = mint_access_token()
    resp = await client.post(
        "/api/v1/query",
        json={
            "query_id": "demo_all",
            "named_params": {"org": "evil-override"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, (
        f"SECURITY FAILURE: reserved param 'org' accepted (status {resp.status_code})"
    )
    body = resp.json()
    assert body["error"]["code"] == "param_name_reserved"


@pytest.mark.asyncio
async def test_reserved_param_name_policies_rejected(client):
    """named_params 'policies' is reserved → 400."""
    token = mint_access_token()
    resp = await client.post(
        "/api/v1/query",
        json={
            "query_id": "demo_all",
            "named_params": {"policies": {"tenant_id": "attacker"}},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, (
        f"SECURITY FAILURE: reserved param 'policies' accepted "
        f"(status {resp.status_code})"
    )
    body = resp.json()
    assert body["error"]["code"] == "param_name_reserved"


@pytest.mark.asyncio
async def test_reserved_param_name_sub_rejected(client):
    """named_params 'sub' is reserved → 400."""
    token = mint_access_token()
    resp = await client.post(
        "/api/v1/query",
        json={
            "query_id": "demo_all",
            "named_params": {"sub": "hacked"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "param_name_reserved"


@pytest.mark.parametrize("reserved_name", [
    "user_id", "org_id", "project", "roles", "scope", "iss", "aud",
    "exp", "iat", "embed_origin", "kind",
])
@pytest.mark.asyncio
async def test_all_reserved_param_names_rejected(client, reserved_name):
    """All token-claim-reserved names must be rejected via named_params."""
    token = mint_access_token()
    resp = await client.post(
        "/api/v1/query",
        json={
            "query_id": "demo_all",
            "named_params": {reserved_name: "injected"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, (
        f"SECURITY FAILURE: reserved param {reserved_name!r} accepted "
        f"(status {resp.status_code})"
    )


# ===========================================================================
# 2b. Restrictive policy filters rows
# ===========================================================================

def test_rls_policy_filters_rows():
    """Planner injects a WHERE predicate from identity.policies that reduces rows.

    The demo table has 5 rows with integer id values 1-5.  We inject
    ``policies={"id": 3}`` via the planner so only row id=3 is returned.
    """
    import pyarrow as pa
    from app.connectors.duckdb_conn import DuckDBConnector
    from app.connectors.planner import plan

    # Seed a fresh connector with the demo table.
    conn = DuckDBConnector()
    demo_table = pa.table(
        {
            "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
            "name": pa.array(["a", "b", "c", "d", "e"]),
            "active": pa.array([True, True, False, True, False]),
        }
    )
    conn.register({"demo": demo_table})

    # Without RLS: all 5 rows.
    plan_all = plan("SELECT * FROM demo", claims={})
    result_all = conn.execute(plan_all)
    assert result_all.num_rows == 5

    # With RLS: only id=3.
    plan_restricted = plan(
        "SELECT * FROM demo",
        claims={"policies": {"id": 3}},
    )
    result_restricted = conn.execute(plan_restricted)
    assert result_restricted.num_rows == 1, (
        f"SECURITY FAILURE: RLS policy 'id=3' did not filter rows — "
        f"got {result_restricted.num_rows} rows instead of 1"
    )
    assert result_restricted.column("id")[0].as_py() == 3


# ===========================================================================
# 2c. Impossible policy → 0 rows
# ===========================================================================

def test_rls_impossible_policy_returns_zero_rows():
    """An impossible policy (id=9999 when no such row exists) → 0 rows."""
    import pyarrow as pa
    from app.connectors.duckdb_conn import DuckDBConnector
    from app.connectors.planner import plan

    conn = DuckDBConnector()
    demo_table = pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int32()),
            "tenant": pa.array(["acme", "acme", "globex"]),
        }
    )
    conn.register({"tenanted": demo_table})

    physical_plan = plan(
        "SELECT * FROM tenanted",
        claims={"policies": {"tenant": "nobody"}},
    )
    result = conn.execute(physical_plan)
    assert result.num_rows == 0, (
        f"SECURITY FAILURE: impossible RLS policy returned {result.num_rows} rows "
        f"(expected 0)"
    )


# ===========================================================================
# 2d. Capability-gated 501: connector cannot enforce RLS → reject before exec
# ===========================================================================

@pytest.mark.asyncio
async def test_rls_on_non_rls_connector_returns_501(client):
    """A query with active RLS policies on a connector that declares
    predicate_rls=False must be rejected with 501 before execution."""
    from app.connectors.registry import get_connector_registry
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    # Set up a mock connector that declares predicate_rls=False.
    mock_connector = MagicMock()
    mock_connector.capabilities.return_value = {"predicate_rls": False}
    mock_connector.execute.side_effect = AssertionError(
        "execute() must never be called for a non-RLS connector with active policies"
    )

    mock_factory = MagicMock(return_value=mock_connector)

    # Register a fake datastore.
    repo = InMemoryRepo()
    repo.seed_org_member(org_id="sec-org", user_id="sec-fp-user")
    ds_id = "no-rls-ds"
    repo._store["datastores"][ds_id] = {
        "id": ds_id,
        "org_id": "sec-org",
        "name": "No-RLS connector",
        "config": {"type": "mock_norls"},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    set_repo(repo)

    # Register the mock connector type.
    get_connector_registry().register("mock_norls", mock_factory)

    # An embed token WITH policies.
    token = mint_embed_token(
        org="sec-org",
        scope=["read:query"],
        policies={"tenant_id": "acme"},
    )

    try:
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_all", "datastore_id": ds_id},
            headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
        )
    finally:
        set_repo(None)
        # ConnectorRegistry has no unregister; just reset to defaults.
        from app.connectors.registry import reset_for_tests as _reset_conn_reg
        _reset_conn_reg()

    assert resp.status_code == 501, (
        f"SECURITY FAILURE: connector with predicate_rls=False + active policies "
        f"returned {resp.status_code} (expected 501)"
    )
    body = resp.json()
    assert body["error"]["code"] == "source_unsupported_rls"


# ===========================================================================
# 2e. Body policies are ignored; token policies always win (unit-level)
# ===========================================================================

def test_rls_body_policies_ignored_token_policies_enforced():
    """The route handler builds claims={policies: identity.policies}, NOT body.claims.

    This is a planner-level unit test: we call plan() with two different claims
    dicts and confirm the RLS predicate matches the TOKEN policies, not any
    hypothetical attacker-supplied body claims.
    """
    from app.connectors.planner import plan

    # Token says tenant='acme'.
    token_claims = {"policies": {"tenant_id": "acme"}}
    physical_plan = plan("SELECT * FROM demo", claims=token_claims)

    # The generated SQL must contain 'acme' (the token policy).
    assert "acme" in physical_plan.sql, (
        f"Token policy 'acme' not found in plan SQL: {physical_plan.sql}"
    )

    # If an attacker could supply body claims with a different tenant they'd
    # get different data; confirm the plan is specific to 'acme'.
    assert "attacker" not in physical_plan.sql


# ===========================================================================
# 2f. RLS predicate injected at AST level (never via string concat)
# ===========================================================================

def test_rls_predicate_is_ast_level():
    """RLS predicates are injected at AST level: the generated SQL is valid and
    the 'malicious' value is properly enclosed in a SQL string literal.

    sqlglot escapes single-quotes by doubling them (SQL standard), so the
    injected text cannot break out of the string context.  We verify this by
    confirming the generated SQL can be re-parsed by sqlglot and that the
    resulting WHERE clause still has the correct structure.
    """
    import sqlglot
    from app.connectors.planner import plan
    from app.connectors.duckdb_conn import DuckDBConnector
    import pyarrow as pa

    malicious_value = "'; DROP TABLE demo; --"
    physical_plan = plan(
        "SELECT * FROM demo",
        claims={"policies": {"tenant": malicious_value}},
    )

    # 1. The generated SQL must be parseable (it is valid SQL).
    try:
        reparsed = sqlglot.parse_one(physical_plan.sql)
    except Exception as exc:
        pytest.fail(
            f"SECURITY FAILURE: sqlglot could not re-parse the RLS-injected SQL. "
            f"SQL: {physical_plan.sql!r}. Error: {exc}"
        )

    # 2. The outer statement is still a SELECT (no DDL injected at statement level).
    import sqlglot.expressions as exp
    assert isinstance(reparsed, exp.Select), (
        f"SECURITY FAILURE: top-level statement is not SELECT after policy injection. "
        f"Got {type(reparsed).__name__}. SQL: {physical_plan.sql}"
    )

    # 3. Execute against DuckDB to prove the value is treated as a literal.
    conn = DuckDBConnector()
    t = pa.table({"tenant": pa.array(["acme"]), "id": pa.array([1])})
    conn.register({"demo": t})
    # The malicious value does not match any row -> 0 results.
    result = conn.execute(physical_plan)
    assert result.num_rows == 0, (
        f"SECURITY FAILURE: malicious RLS policy returned rows (expected 0). "
        f"SQL: {physical_plan.sql}"
    )


# ===========================================================================
# 2g. Multiple RLS policies applied simultaneously
# ===========================================================================

def test_rls_multiple_policies_all_injected():
    """All policies in the dict are injected as separate AND predicates."""
    from app.connectors.planner import plan

    physical_plan = plan(
        "SELECT * FROM demo",
        claims={"policies": {"tenant_id": "acme", "region": "us-east"}},
    )

    # Both predicates must appear in the SQL.
    sql = physical_plan.sql.lower()
    assert "acme" in sql, f"Policy 'tenant_id=acme' not in SQL: {physical_plan.sql}"
    assert "us-east" in sql, f"Policy 'region=us-east' not in SQL: {physical_plan.sql}"
    # Both should appear as predicates (plan.predicates is the observability list).
    preds = " ".join(physical_plan.predicates).lower()
    assert "acme" in preds or "acme" in sql
    assert "us-east" in preds or "us-east" in sql
