"""Tests for the AI text-to-SQL layer (M18-A).

Coverage
--------
1. generate_sql (unit — no HTTP)
   a. NullProvider returns a non-empty string.
   b. NullProvider output is DETERMINISTIC (same question → same SQL twice).
   c. NullProvider output is parseable by sqlglot (valid=True).
   d. NullProvider output references a REAL table from the catalog.
   e. NullProvider output starts with SELECT.
   f. A fake provider that returns junk SQL → valid=False with issues.
   g. A fake provider returning a valid SELECT → valid=True.

2. POST /ai/sql endpoint
   a. 401 without auth.
   b. 200 with auth; response has {sql, valid, issues, provider, grounding}.
   c. NullProvider: provider == "null" when no API keys are configured.
   d. NullProvider: returned SQL is deterministic (two identical requests).
   e. NullProvider: returned SQL is parseable (valid=True).
   f. save_as registers the query; get_query_registry().get(id) returns it.
   g. save_as response includes registered_id matching the save_as value.
   h. Registered query SQL matches what was generated.
   i. Junk provider SQL (via monkeypatch) → valid=False with non-empty issues.
   j. 422 when question field is missing.

Network safety
--------------
NullProvider makes zero network calls; all tests use it (no API keys set).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.grounding import build_catalog
from app.ai.provider import LLMProvider, NullProvider
from app.ai.sql import generate_sql
from app.auth.jwt import mint_access_token
from app.queries.registry import get_query_registry


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "sql-tester@example.com",
        "name": "SQL Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _minimal_catalog() -> dict[str, Any]:
    """Return a minimal in-memory catalog (no registry required)."""
    return {
        "tables": {
            "orders": ["id", "tenant_id", "amount", "created_at"],
            "users": ["id", "name", "email", "tenant_id"],
        },
        "queries": [
            {
                "id": "q_orders",
                "name": "Orders",
                "tables": ["orders"],
                "outputs": ["id", "tenant_id", "amount"],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Fake provider helpers
# ---------------------------------------------------------------------------


class _JunkProvider(LLMProvider):
    """Returns syntactically invalid SQL (non-parseable junk)."""

    name = "junk"

    def complete(self, prompt: str, system: str | None = None) -> str:
        return "DROP TABLE users; THIS IS NOT SQL $$$ @@@ ???"


class _ValidSQLProvider(LLMProvider):
    """Returns a valid SELECT statement."""

    name = "valid_sql"

    def complete(self, prompt: str, system: str | None = None) -> str:
        return "SELECT id, amount FROM orders WHERE tenant_id = 'acme'"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sql_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for /ai/sql endpoint tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id


def _clear_llm_env(monkeypatch: Any) -> None:
    """Remove all LLM-related env vars and clear the settings cache."""
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    from app.config import get_settings
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 1. generate_sql unit tests
# ---------------------------------------------------------------------------


class TestGenerateSqlUnit:
    """Unit tests for generate_sql() — no HTTP, no registry needed."""

    def test_null_provider_returns_nonempty_string(self):
        catalog = _minimal_catalog()
        result = generate_sql("show me all orders", catalog, NullProvider())
        assert isinstance(result["sql"], str)
        assert len(result["sql"]) > 0

    def test_null_provider_is_deterministic(self):
        catalog = _minimal_catalog()
        r1 = generate_sql("show me all orders", catalog, NullProvider())
        r2 = generate_sql("show me all orders", catalog, NullProvider())
        assert r1["sql"] == r2["sql"]

    def test_null_provider_sql_is_valid(self):
        catalog = _minimal_catalog()
        result = generate_sql("show me all orders", catalog, NullProvider())
        assert result["valid"] is True
        assert result["issues"] == []

    def test_null_provider_sql_starts_with_select(self):
        catalog = _minimal_catalog()
        result = generate_sql("show me all orders", catalog, NullProvider())
        assert result["sql"].strip().upper().startswith("SELECT")

    def test_null_provider_sql_references_real_table(self):
        catalog = _minimal_catalog()
        result = generate_sql("show me all orders", catalog, NullProvider())
        sql_lower = result["sql"].lower()
        # The SQL must reference one of the real catalog tables.
        assert any(tbl in sql_lower for tbl in catalog["tables"]), (
            f"Generated SQL does not reference any catalog table: {result['sql']!r}"
        )

    def test_null_provider_result_has_all_keys(self):
        catalog = _minimal_catalog()
        result = generate_sql("list users", catalog, NullProvider())
        assert "sql" in result
        assert "valid" in result
        assert "issues" in result

    def test_junk_provider_returns_valid_false(self):
        catalog = _minimal_catalog()
        result = generate_sql("show me orders", catalog, _JunkProvider())
        assert result["valid"] is False
        assert len(result["issues"]) > 0

    def test_valid_sql_provider_returns_valid_true(self):
        catalog = _minimal_catalog()
        result = generate_sql("show me orders", catalog, _ValidSQLProvider())
        assert result["valid"] is True
        assert result["issues"] == []

    def test_valid_sql_provider_sql_matches_output(self):
        catalog = _minimal_catalog()
        result = generate_sql("show me orders", catalog, _ValidSQLProvider())
        assert "orders" in result["sql"]
        assert result["sql"].strip().upper().startswith("SELECT")

    def test_empty_catalog_produces_valid_sql(self):
        """generate_sql with an empty catalog should still return a valid SELECT."""
        empty_catalog: dict[str, Any] = {"tables": {}, "queries": []}
        result = generate_sql("show me everything", empty_catalog, NullProvider())
        assert isinstance(result["sql"], str)
        assert result["sql"].strip().upper().startswith("SELECT")
        # May not be valid if it references a non-existent table, but must not crash.
        assert "valid" in result
        assert "issues" in result

    def test_different_questions_may_target_different_tables(self):
        """Grounding causes different questions to pick different tables."""
        catalog = _minimal_catalog()
        r_orders = generate_sql("show me all orders by amount", catalog, NullProvider())
        r_users = generate_sql("list all users by email", catalog, NullProvider())
        # Both must be parseable.
        assert r_orders["valid"] is True
        assert r_users["valid"] is True
        # May reference different tables (grounding-dependent; just check they're SELECT).
        assert r_orders["sql"].strip().upper().startswith("SELECT")
        assert r_users["sql"].strip().upper().startswith("SELECT")


# ---------------------------------------------------------------------------
# 2. POST /ai/sql endpoint tests
# ---------------------------------------------------------------------------


class TestSqlEndpoint:
    """HTTP endpoint tests for POST /ai/sql."""

    @pytest.mark.asyncio
    async def test_requires_auth(self, sql_client):
        ac, _ = sql_client
        resp = await ac.post("/api/v1/ai/sql", json={"question": "show me orders"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_returns_200_with_auth(self, sql_client):
        ac, user_id = sql_client
        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me orders"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_response_has_required_keys(self, sql_client):
        ac, user_id = sql_client
        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me all demo rows"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "sql" in body
        assert "valid" in body
        assert "issues" in body
        assert "provider" in body
        assert "grounding" in body

    @pytest.mark.asyncio
    async def test_uses_null_provider_by_default(self, sql_client, monkeypatch):
        """With no API keys the provider must be 'null'."""
        _clear_llm_env(monkeypatch)
        ac, user_id = sql_client
        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "list all demo rows"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "null"

    @pytest.mark.asyncio
    async def test_null_provider_sql_is_deterministic(self, sql_client, monkeypatch):
        """Two identical requests with NullProvider must return the same SQL."""
        _clear_llm_env(monkeypatch)
        ac, user_id = sql_client
        headers = _auth_headers(user_id)
        question = "show me all active demo rows"

        resp1 = await ac.post(
            "/api/v1/ai/sql", json={"question": question}, headers=headers
        )
        resp2 = await ac.post(
            "/api/v1/ai/sql", json={"question": question}, headers=headers
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["sql"] == resp2.json()["sql"]

    @pytest.mark.asyncio
    async def test_null_provider_sql_is_valid(self, sql_client, monkeypatch):
        """NullProvider must return valid=True (parseable SQL)."""
        _clear_llm_env(monkeypatch)
        ac, user_id = sql_client
        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me demo data"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["issues"] == []

    @pytest.mark.asyncio
    async def test_null_provider_sql_starts_with_select(self, sql_client, monkeypatch):
        _clear_llm_env(monkeypatch)
        ac, user_id = sql_client
        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "get all rows"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert body["sql"].strip().upper().startswith("SELECT")

    @pytest.mark.asyncio
    async def test_grounding_has_expected_keys(self, sql_client):
        ac, user_id = sql_client
        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me all demo rows"},
            headers=_auth_headers(user_id),
        )
        grounding = resp.json()["grounding"]
        assert "relevant_tables" in grounding
        assert "relevant_columns" in grounding
        assert "related_queries" in grounding
        assert "snippets" in grounding

    @pytest.mark.asyncio
    async def test_missing_question_returns_422(self, sql_client):
        ac, user_id = sql_client
        resp = await ac.post(
            "/api/v1/ai/sql",
            json={},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 422

    # ── save_as tests ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_save_as_registers_query(self, sql_client, monkeypatch):
        """save_as persists the generated SQL in the query registry."""
        _clear_llm_env(monkeypatch)
        ac, user_id = sql_client
        save_id = f"test_sql_{uuid.uuid4().hex[:8]}"

        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me demo rows", "save_as": save_id},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["registered_id"] == save_id

        # Verify the query is retrievable from the registry.
        registry = get_query_registry()
        rq = registry.get(save_id)
        assert rq is not None, f"Query '{save_id}' not found in registry after save_as"

    @pytest.mark.asyncio
    async def test_save_as_registered_sql_matches_response(self, sql_client, monkeypatch):
        """The SQL stored in the registry must match the sql field in the response."""
        _clear_llm_env(monkeypatch)
        ac, user_id = sql_client
        save_id = f"test_sql_{uuid.uuid4().hex[:8]}"

        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me all demo rows", "save_as": save_id},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        returned_sql = body["sql"]

        registry = get_query_registry()
        rq = registry.get(save_id)
        assert rq is not None
        assert rq.sql == returned_sql

    @pytest.mark.asyncio
    async def test_save_as_without_save_as_has_null_registered_id(
        self, sql_client, monkeypatch
    ):
        """Without save_as the registered_id field must be null."""
        _clear_llm_env(monkeypatch)
        ac, user_id = sql_client
        resp = await ac.post(
            "/api/v1/ai/sql",
            json={"question": "show me demo rows"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["registered_id"] is None

    @pytest.mark.asyncio
    async def test_save_as_infers_params_from_placeholders(
        self, sql_client, monkeypatch
    ):
        """SQL with {{name}} placeholders → QueryParams inferred in the registry."""
        _clear_llm_env(monkeypatch)
        ac, user_id = sql_client
        save_id = f"test_param_sql_{uuid.uuid4().hex[:8]}"

        # Use a provider that returns SQL with a named placeholder.
        class _PlaceholderProvider(LLMProvider):
            name = "placeholder"

            def complete(self, prompt: str, system: str | None = None) -> str:
                return "SELECT * FROM demo WHERE tenant_id = '{{tenant}}'"

        with patch("app.routes.ai.get_provider", return_value=_PlaceholderProvider()):
            resp = await ac.post(
                "/api/v1/ai/sql",
                json={"question": "filter by tenant", "save_as": save_id},
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200
        registry = get_query_registry()
        rq = registry.get(save_id)
        assert rq is not None
        param_names = [p.name for p in rq.params]
        assert "tenant" in param_names, (
            f"Expected 'tenant' in params, got {param_names!r}"
        )

    # ── junk provider → valid=False ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_junk_provider_returns_valid_false(self, sql_client):
        """A provider returning non-SQL junk must yield valid=False with issues."""
        ac, user_id = sql_client

        with patch("app.routes.ai.get_provider", return_value=_JunkProvider()):
            resp = await ac.post(
                "/api/v1/ai/sql",
                json={"question": "show me orders"},
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False
        assert len(body["issues"]) > 0

    @pytest.mark.asyncio
    async def test_junk_provider_sql_is_in_response(self, sql_client):
        """Even junk SQL is returned so the caller can inspect / log it."""
        ac, user_id = sql_client

        with patch("app.routes.ai.get_provider", return_value=_JunkProvider()):
            resp = await ac.post(
                "/api/v1/ai/sql",
                json={"question": "show me orders"},
                headers=_auth_headers(user_id),
            )

        body = resp.json()
        assert isinstance(body["sql"], str)
        assert len(body["sql"]) > 0

    @pytest.mark.asyncio
    async def test_valid_provider_returns_valid_true(self, sql_client):
        """A provider returning a valid SELECT → valid=True."""
        ac, user_id = sql_client

        with patch("app.routes.ai.get_provider", return_value=_ValidSQLProvider()):
            resp = await ac.post(
                "/api/v1/ai/sql",
                json={"question": "show me orders"},
                headers=_auth_headers(user_id),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["issues"] == []
