"""Attack class 6: Connector / planner hardening.

Covers
------
6a. Planner rejects DROP TABLE → AppError 400
6b. Planner rejects DELETE → AppError 400
6c. Planner rejects UPDATE → AppError 400
6d. Planner rejects INSERT → AppError 400
6e. Planner rejects CREATE TABLE → AppError 400
6f. Planner rejects TRUNCATE → AppError 400
6g. Planner rejects multi-statement (SELECT; DROP) — sqlglot parse behaviour
6h. Planner allows valid SELECT → passes through
6i. DuckDB connector: direct execution path does not permit DDL
6j. Named-param resolve_named_params does NOT string-concat (positional only)
6k. SECURITY GAP: multi-statement SQL ("SELECT 1; DROP TABLE x") may be
    accepted if sqlglot only parses the first statement.  Document actual
    behavior.
6l. Raw SQL API: first-party token sending DROP TABLE → 400
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pyarrow as pa
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

from tests.security.conftest_helpers import mint_access_token  # noqa: E402


# ---------------------------------------------------------------------------
# Planner-level unit tests (no HTTP)
# ---------------------------------------------------------------------------

# ===========================================================================
# 6a. DROP TABLE
# ===========================================================================

def test_planner_rejects_drop_table():
    """DROP TABLE → AppError UNSUPPORTED_QUERY 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("DROP TABLE users")
    err = exc_info.value
    assert err.status == 400, f"Expected 400, got {err.status}"


# ===========================================================================
# 6b. DELETE
# ===========================================================================

def test_planner_rejects_delete():
    """DELETE → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("DELETE FROM users WHERE id = 1")
    assert exc_info.value.status == 400


# ===========================================================================
# 6c. UPDATE
# ===========================================================================

def test_planner_rejects_update():
    """UPDATE → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("UPDATE users SET name = 'hacked'")
    assert exc_info.value.status == 400


# ===========================================================================
# 6d. INSERT
# ===========================================================================

def test_planner_rejects_insert():
    """INSERT → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("INSERT INTO users (id, name) VALUES (999, 'injected')")
    assert exc_info.value.status == 400


# ===========================================================================
# 6e. CREATE TABLE
# ===========================================================================

def test_planner_rejects_create_table():
    """CREATE TABLE → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("CREATE TABLE evil_table (id INT, data TEXT)")
    assert exc_info.value.status == 400


# ===========================================================================
# 6f. TRUNCATE
# ===========================================================================

def test_planner_rejects_truncate():
    """TRUNCATE → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("TRUNCATE TABLE users")
    assert exc_info.value.status == 400


# ===========================================================================
# 6h. Valid SELECT passes through planner
# ===========================================================================

def test_planner_allows_valid_select():
    """A valid SELECT statement is planned successfully."""
    from app.connectors.planner import plan

    physical_plan = plan("SELECT id, name FROM demo WHERE active = true")
    assert physical_plan.sql is not None
    assert "SELECT" in physical_plan.sql.upper()


# ===========================================================================
# 6g. Multi-statement SQL: document actual behavior
# ===========================================================================

def test_planner_rejects_multi_statement_sql():
    """Multi-statement SQL ('SELECT 1; DROP TABLE users') IS rejected.

    sqlglot's parse_one() raises SqlglotError when given multiple statements
    separated by semicolons, which the planner converts to AppError 400
    (INVALID_SQL).  This is the correct and expected behavior.
    """
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("SELECT 1; DROP TABLE users")
    assert exc_info.value.status == 400, (
        f"Multi-statement SQL not rejected with 400: {exc_info.value}"
    )


# ===========================================================================
# 6i. DuckDB connector: DDL via execute() on the physical plan fails
# ===========================================================================

def test_duckdb_connector_ddl_rejected_by_planner():
    """Even if someone bypassed the planner, DuckDB would refuse DDL in a
    positional-bound query.  Primarily this confirms the planner gate works."""
    from app.connectors.planner import plan
    from app.errors import AppError

    # We can only test the planner gate here (it's the primary control).
    for ddl in [
        "DROP TABLE demo",
        "DELETE FROM demo",
        "CREATE TABLE bad (x INT)",
    ]:
        with pytest.raises(AppError) as exc_info:
            plan(ddl)
        assert exc_info.value.status == 400, (
            f"Planner did not reject DDL: {ddl!r} (status={exc_info.value.status})"
        )


# ===========================================================================
# 6j. Named params: values are positional, never string-concatenated
# ===========================================================================

def test_named_param_values_never_in_sql():
    """Named param values appear in the positional list, not in the SQL string."""
    from app.connectors.planner import resolve_named_params

    sql = "SELECT * FROM demo WHERE name = {{name}}"
    value = "alice"
    rewritten, params = resolve_named_params(sql, {"name": value})

    # The literal value must NOT appear in the rewritten SQL.
    assert value not in rewritten, (
        f"SECURITY FAILURE: value '{value}' embedded in SQL: {rewritten}"
    )
    assert params == [value], f"Expected ['{value}'] in params, got {params}"
    assert "$1" in rewritten, f"Positional binding $1 missing: {rewritten}"


# ===========================================================================
# 6l. Raw SQL API: DROP TABLE via POST /query → 400
# ===========================================================================

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


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "DROP TABLE demo",
    "DELETE FROM demo WHERE 1=1",
    "UPDATE demo SET name='hacked' WHERE 1=1",
    "INSERT INTO demo VALUES (99, 'injected', 0.0, true)",
    "CREATE TABLE evil (x INT)",
    "TRUNCATE TABLE demo",
])
async def test_ddl_via_api_rejected(client, sql):
    """First-party token sending DDL/DML via POST /query → 400."""
    token = mint_access_token()
    resp = await client.post(
        "/api/v1/query",
        json={"sql": sql},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, (
        f"SECURITY FAILURE: '{sql}' via API returned {resp.status_code} "
        f"(expected 400)"
    )
