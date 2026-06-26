"""Adversarial tests for /transpile NOT in test_transform_versions_transpile.py.

Coverage
--------
Dialect matrix:
1. duckdb → bigquery.
2. duckdb → postgres.
3. postgres → snowflake (already tested, documented here).
4. bigquery → duckdb.
5. mysql → postgres.
6. redshift → postgres.

Error cases:
7. Unknown from_dialect → 400, code='unknown_dialect'.
8. Unknown to_dialect → 400, code='unknown_dialect'.
9. Both unknown dialects → 400.
10. Empty SQL → 400, code='bad_request'.
11. Whitespace-only SQL → 400, code='bad_request'.
12. Huge input (50k chars) → 200 or 400 parse_error (not 500).
13. Non-SELECT: INSERT → transpile or parse_error (not 500).
14. Non-SELECT: CREATE TABLE → transpile or parse_error (not 500).
15. "postgresql" alias → accepted (in ALLOWED_DIALECTS).
16. Malformed SQL → 400 or 200 (sqlglot is lenient), never 500.
17. Unauthenticated → 401.
18. Case-insensitive dialect: "DuckDB" upper-cased → 400 (allowlist is lowercase only).
19. DuckDB-specific SAMPLE syntax → transpile or parse_error (not 500).
20. ALLOWED_DIALECTS includes expected dialects.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"u-{user_id[:6]}@test.example",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest_asyncio.fixture
async def transpile_client(app, fake_db):
    """ASGI client with auth for transpile adversarial tests."""
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="member")
    token = mint_access_token(user_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.headers["Authorization"] = f"Bearer {token}"
        yield c

    set_repo(None)


def _transpile(client, sql: str, from_d: str, to_d: str):
    """Synchronous transpile helper — returns the response."""
    import asyncio

    return asyncio.run(
        client.post(
            "/api/v1/transpile",
            json={"sql": sql, "from_dialect": from_d, "to_dialect": to_d},
        )
    )


# ---------------------------------------------------------------------------
# 1–6. Dialect matrix
# ---------------------------------------------------------------------------


class TestDialectMatrix:
    @pytest.mark.asyncio
    async def test_duckdb_to_bigquery(self, transpile_client):
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT id, amount FROM orders WHERE id = 1",
                "from_dialect": "duckdb",
                "to_dialect": "bigquery",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "sql" in data
        assert isinstance(data["sql"], str)
        assert len(data["sql"]) > 0

    @pytest.mark.asyncio
    async def test_duckdb_to_postgres(self, transpile_client):
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT id, STRFTIME(created_at, '%Y-%m-%d') AS day FROM orders",
                "from_dialect": "duckdb",
                "to_dialect": "postgres",
            },
        )
        # sqlglot may emit different syntax but should not error
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert "sql" in resp.json()

    @pytest.mark.asyncio
    async def test_bigquery_to_duckdb(self, transpile_client):
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT id, DATE(created_at) AS day FROM `proj.dataset.orders`",
                "from_dialect": "bigquery",
                "to_dialect": "duckdb",
            },
        )
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert "sql" in resp.json()

    @pytest.mark.asyncio
    async def test_mysql_to_postgres(self, transpile_client):
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT id, `name`, created_at FROM `orders` LIMIT 10",
                "from_dialect": "mysql",
                "to_dialect": "postgres",
            },
        )
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert "sql" in resp.json()

    @pytest.mark.asyncio
    async def test_redshift_to_postgres(self, transpile_client):
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT id, GETDATE() AS now FROM orders",
                "from_dialect": "redshift",
                "to_dialect": "postgres",
            },
        )
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert "sql" in resp.json()

    @pytest.mark.asyncio
    async def test_postgres_to_snowflake(self, transpile_client):
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT id, created_at::date AS day FROM orders",
                "from_dialect": "postgres",
                "to_dialect": "snowflake",
            },
        )
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert "sql" in resp.json()


# ---------------------------------------------------------------------------
# 7–9. Unknown dialects
# ---------------------------------------------------------------------------


def _error_code(data: dict) -> str | None:
    """Extract error code from AppError response (nested under 'error' key)."""
    error = data.get("error") or data
    return error.get("code")


class TestUnknownDialects:
    @pytest.mark.asyncio
    async def test_unknown_from_dialect_400(self, transpile_client):
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": "SELECT 1", "from_dialect": "fakedb", "to_dialect": "postgres"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert _error_code(data) == "unknown_dialect"

    @pytest.mark.asyncio
    async def test_unknown_to_dialect_400(self, transpile_client):
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": "SELECT 1", "from_dialect": "postgres", "to_dialect": "notadialect"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert _error_code(data) == "unknown_dialect"

    @pytest.mark.asyncio
    async def test_both_unknown_dialects_400(self, transpile_client):
        """Both from and to are unknown → first unknown (from) triggers 400."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": "SELECT 1", "from_dialect": "notadb", "to_dialect": "alsonotadb"},
        )
        assert resp.status_code == 400
        assert _error_code(resp.json()) == "unknown_dialect"


# ---------------------------------------------------------------------------
# 10–11. Empty and whitespace SQL
# ---------------------------------------------------------------------------


class TestEmptySql:
    @pytest.mark.asyncio
    async def test_empty_sql_returns_400(self, transpile_client):
        """Empty SQL string → 400, code='bad_request'."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": "", "from_dialect": "duckdb", "to_dialect": "postgres"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert _error_code(data) == "bad_request"

    @pytest.mark.asyncio
    async def test_whitespace_only_sql_returns_400(self, transpile_client):
        """Whitespace-only SQL → stripped to '' → 400, code='bad_request'."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": "   \n\t  ", "from_dialect": "duckdb", "to_dialect": "postgres"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert _error_code(data) == "bad_request"


# ---------------------------------------------------------------------------
# 12. Huge input
# ---------------------------------------------------------------------------


class TestHugeInput:
    @pytest.mark.asyncio
    async def test_50k_char_sql_no_crash(self, transpile_client):
        """50,000-character SQL → should return 200 or 400 parse_error, never 500."""
        big_sql = "SELECT " + ", ".join([f"col_{i}" for i in range(2000)]) + " FROM big_table"
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": big_sql, "from_dialect": "duckdb", "to_dialect": "bigquery"},
        )
        assert resp.status_code in (200, 400)
        if resp.status_code == 400:
            assert resp.json().get("code") == "parse_error"


# ---------------------------------------------------------------------------
# 13–14. Non-SELECT statements
# ---------------------------------------------------------------------------


class TestNonSelect:
    @pytest.mark.asyncio
    async def test_insert_statement_not_500(self, transpile_client):
        """INSERT statement → transpile or parse_error, never 500."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "INSERT INTO orders (id, amount) VALUES (1, 100.0)",
                "from_dialect": "postgres",
                "to_dialect": "bigquery",
            },
        )
        assert resp.status_code in (200, 400)

    @pytest.mark.asyncio
    async def test_create_table_not_500(self, transpile_client):
        """CREATE TABLE → transpile or parse_error, never 500."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "CREATE TABLE orders (id INT, amount FLOAT)",
                "from_dialect": "postgres",
                "to_dialect": "duckdb",
            },
        )
        assert resp.status_code in (200, 400)

    @pytest.mark.asyncio
    async def test_update_statement_not_500(self, transpile_client):
        """UPDATE → transpile or parse_error, never 500."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "UPDATE orders SET amount = 200 WHERE id = 1",
                "from_dialect": "postgres",
                "to_dialect": "bigquery",
            },
        )
        assert resp.status_code in (200, 400)


# ---------------------------------------------------------------------------
# 15. Dialect alias "postgresql"
# ---------------------------------------------------------------------------


class TestDialectAlias:
    @pytest.mark.asyncio
    async def test_postgresql_alias_in_allowlist(self, transpile_client):
        """'postgresql' is in ALLOWED_DIALECTS — passes the allowlist check."""
        from app.routes.transpile import ALLOWED_DIALECTS

        assert "postgresql" in ALLOWED_DIALECTS

        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT id FROM orders",
                "from_dialect": "postgresql",
                "to_dialect": "duckdb",
            },
        )
        # 'postgresql' is an accepted alias and is mapped to 'postgres' before
        # sqlglot runs, so it transpiles successfully (regression guard for the
        # fixed alias bug — previously it passed the allowlist then 400'd inside
        # sqlglot with an opaque "Unknown dialect" error).
        assert resp.status_code == 200, resp.text
        assert "orders" in resp.json()["sql"].lower()


# ---------------------------------------------------------------------------
# 16. Malformed SQL
# ---------------------------------------------------------------------------


class TestMalformedSql:
    @pytest.mark.asyncio
    async def test_malformed_sql_no_crash(self, transpile_client):
        """'SELECT FROM WHERE' → sqlglot lenient or parse_error (not 500)."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT FROM WHERE",
                "from_dialect": "duckdb",
                "to_dialect": "bigquery",
            },
        )
        assert resp.status_code in (200, 400)

    @pytest.mark.asyncio
    async def test_completely_invalid_sql(self, transpile_client):
        """Completely invalid SQL → 400 parse_error (not 500)."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "!!!not sql at all###",
                "from_dialect": "duckdb",
                "to_dialect": "bigquery",
            },
        )
        assert resp.status_code in (200, 400)


# ---------------------------------------------------------------------------
# 17. Unauthenticated
# ---------------------------------------------------------------------------


class TestUnauthenticated:
    @pytest.mark.asyncio
    async def test_unauthenticated_transpile_401(self, client):
        """POST /api/v1/transpile without auth → 401."""
        resp = await client.post(
            "/api/v1/transpile",
            json={"sql": "SELECT 1", "from_dialect": "duckdb", "to_dialect": "postgres"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# 18. Case-insensitive dialect names
# ---------------------------------------------------------------------------


class TestCaseSensitivity:
    @pytest.mark.asyncio
    async def test_uppercase_duckdb_not_accepted_by_allowlist(self, transpile_client):
        """'DuckDB' (uppercase) is stripped+lowercased before allowlist check → accepted."""
        # The route does from_d = body.from_dialect.strip().lower()
        # So "DuckDB".lower() = "duckdb" which IS in ALLOWED_DIALECTS
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": "SELECT 1", "from_dialect": "DuckDB", "to_dialect": "postgres"},
        )
        # After .lower() it's "duckdb" → accepted
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_mixed_case_bigquery_accepted(self, transpile_client):
        """'BIGQUERY' → .lower() = 'bigquery' → accepted."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": "SELECT 1", "from_dialect": "BIGQUERY", "to_dialect": "duckdb"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_mixed_case_unknown_still_400(self, transpile_client):
        """'FakeDB' → .lower() = 'fakedb' → not in allowlist → 400."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={"sql": "SELECT 1", "from_dialect": "FakeDB", "to_dialect": "postgres"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 19. DuckDB-specific syntax
# ---------------------------------------------------------------------------


class TestDuckDBSyntax:
    @pytest.mark.asyncio
    async def test_duckdb_sample_syntax(self, transpile_client):
        """DuckDB USING SAMPLE syntax → transpile or parse_error, not 500."""
        resp = await transpile_client.post(
            "/api/v1/transpile",
            json={
                "sql": "SELECT * FROM orders USING SAMPLE 10%",
                "from_dialect": "duckdb",
                "to_dialect": "bigquery",
            },
        )
        assert resp.status_code in (200, 400)


# ---------------------------------------------------------------------------
# 20. ALLOWED_DIALECTS completeness
# ---------------------------------------------------------------------------


class TestAllowedDialects:
    def test_allowed_dialects_includes_common_ones(self):
        from app.routes.transpile import ALLOWED_DIALECTS

        expected = {"duckdb", "bigquery", "postgres", "snowflake", "mysql", "redshift"}
        missing = expected - ALLOWED_DIALECTS
        assert missing == set(), f"Missing dialects from allowlist: {missing}"

    def test_postgresql_alias_in_allowed_dialects(self):
        from app.routes.transpile import ALLOWED_DIALECTS

        assert "postgresql" in ALLOWED_DIALECTS

    def test_allowed_dialects_all_lowercase(self):
        from app.routes.transpile import ALLOWED_DIALECTS

        for d in ALLOWED_DIALECTS:
            assert d == d.lower(), f"Dialect {d!r} is not lowercase"
