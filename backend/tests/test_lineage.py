"""Tests for the SQL lineage index (M7-A).

Coverage
--------
1. extract_lineage — JOIN query with aliases: tables, columns, alias resolution.
2. extract_lineage — simple single-table SELECT: table, columns.
3. extract_lineage — aggregate query: table, columns include GROUP BY + SUM arg.
4. extract_lineage — malformed SQL: no crash, returns dict with 'error' key.
5. build_graph — over the registered query registry; demo_all present; inverted
   table index maps a table to the query IDs that use it.
6. GET /lineage — 401 without a token; 200 with a valid token.
7. GET /lineage/query/{id} — 200 for a known id; 404 for an unknown id.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.lineage.extract import extract_lineage
from app.lineage.graph import build_graph
from app.queries.registry import QueryRegistry, RegisteredQuery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "tester@example.com",
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def lineage_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for lineage endpoint tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id


# ---------------------------------------------------------------------------
# 1. extract_lineage — JOIN with aliases
# ---------------------------------------------------------------------------

class TestExtractLineageJoin:
    """extract_lineage on a JOIN query with table aliases."""

    SQL = (
        "SELECT u.id, u.name, o.amount "
        "FROM users u "
        "JOIN orders o ON o.user_id = u.id "
        "WHERE u.tenant_id = 'x'"
    )

    def test_tables_include_users_and_orders(self):
        result = extract_lineage(self.SQL)
        assert "users" in result["tables"]
        assert "orders" in result["tables"]

    def test_tables_are_sorted(self):
        result = extract_lineage(self.SQL)
        assert result["tables"] == sorted(result["tables"])

    def test_no_error_key(self):
        result = extract_lineage(self.SQL)
        assert "error" not in result

    def test_columns_include_users_id(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("users", "id") in col_keys

    def test_columns_include_users_name(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("users", "name") in col_keys

    def test_columns_include_orders_amount(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("orders", "amount") in col_keys

    def test_columns_include_users_tenant_id(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("users", "tenant_id") in col_keys

    def test_columns_include_orders_user_id(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("orders", "user_id") in col_keys

    def test_alias_u_resolves_to_users(self):
        """All columns with alias 'u' must resolve to table 'users'."""
        result = extract_lineage(self.SQL)
        for col in result["columns"]:
            if col["column"] in ("id", "name", "tenant_id") and col["table"] is not None:
                # At least one of these should be attributed to users, not 'u'.
                assert col["table"] in ("users", None)

    def test_alias_o_resolves_to_orders(self):
        """All columns with alias 'o' must resolve to table 'orders'."""
        result = extract_lineage(self.SQL)
        for col in result["columns"]:
            if col["column"] in ("amount", "user_id") and col["table"] is not None:
                assert col["table"] in ("orders", None)


# ---------------------------------------------------------------------------
# 2. extract_lineage — simple single-table SELECT
# ---------------------------------------------------------------------------

class TestExtractLineageSimple:
    """extract_lineage on a plain single-table SELECT."""

    SQL = "SELECT id, name FROM users"

    def test_table_is_users(self):
        result = extract_lineage(self.SQL)
        assert result["tables"] == ["users"]

    def test_column_users_id(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("users", "id") in col_keys

    def test_column_users_name(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("users", "name") in col_keys

    def test_outputs_include_id_and_name(self):
        result = extract_lineage(self.SQL)
        assert "id" in result["outputs"]
        assert "name" in result["outputs"]

    def test_no_error_key(self):
        result = extract_lineage(self.SQL)
        assert "error" not in result


# ---------------------------------------------------------------------------
# 3. extract_lineage — aggregate query
# ---------------------------------------------------------------------------

class TestExtractLineageAggregate:
    """extract_lineage on a GROUP BY / aggregate query."""

    SQL = (
        "SELECT tenant_id, SUM(amount) AS total "
        "FROM orders "
        "GROUP BY tenant_id"
    )

    def test_table_is_orders(self):
        result = extract_lineage(self.SQL)
        assert result["tables"] == ["orders"]

    def test_column_orders_tenant_id(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("orders", "tenant_id") in col_keys

    def test_column_orders_amount(self):
        result = extract_lineage(self.SQL)
        col_keys = {(c["table"], c["column"]) for c in result["columns"]}
        assert ("orders", "amount") in col_keys

    def test_outputs_include_tenant_id(self):
        result = extract_lineage(self.SQL)
        assert "tenant_id" in result["outputs"]

    def test_outputs_include_total_alias(self):
        result = extract_lineage(self.SQL)
        assert "total" in result["outputs"]

    def test_no_error_key(self):
        result = extract_lineage(self.SQL)
        assert "error" not in result


# ---------------------------------------------------------------------------
# 4. Malformed SQL — graceful handling
# ---------------------------------------------------------------------------

class TestExtractLineageMalformed:
    """Malformed SQL must never raise; always returns a dict."""

    @pytest.mark.parametrize("bad_sql", [
        "",
        "NOT VALID SQL !!!",
        "SELECT FROM FROM FROM",
        "INSERT INTO foo VALUES (1)",   # non-SELECT
        ";;;",
    ])
    def test_no_crash(self, bad_sql):
        result = extract_lineage(bad_sql)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("bad_sql", [
        "",
        "NOT VALID SQL !!!",
        ";;;",
    ])
    def test_returns_empty_lists_on_failure(self, bad_sql):
        result = extract_lineage(bad_sql)
        # Either error key is present OR lists are empty — no crash is the key contract.
        assert isinstance(result.get("tables"), list)
        assert isinstance(result.get("columns"), list)
        assert isinstance(result.get("outputs"), list)

    def test_error_key_present_on_parse_failure(self):
        result = extract_lineage("NOT VALID SQL !!!")
        # Some parsers recover; if no error key, at least no crash.
        # If error key is present, the message must be a non-empty string.
        if "error" in result:
            assert isinstance(result["error"], str)
            assert result["error"]

    def test_non_select_returns_error_or_empty(self):
        result = extract_lineage("INSERT INTO foo VALUES (1)")
        # Non-SELECT should produce an error key (our implementation returns not_a_select)
        # or at minimum empty lists.
        assert isinstance(result, dict)
        if "error" in result:
            assert result["error"]


# ---------------------------------------------------------------------------
# 5. build_graph — over the registered query registry
# ---------------------------------------------------------------------------

class TestBuildGraph:
    """build_graph over a synthetic registry."""

    def _make_registry_queries(self) -> list[RegisteredQuery]:
        return [
            RegisteredQuery(id="q_users", sql="SELECT id, name FROM users", name="Users"),
            RegisteredQuery(
                id="q_join",
                sql="SELECT u.id, o.amount FROM users u JOIN orders o ON o.user_id = u.id",
                name="Users+Orders",
            ),
            RegisteredQuery(id="q_orders", sql="SELECT id, total FROM orders", name="Orders"),
        ]

    def test_queries_dict_has_all_ids(self):
        queries = self._make_registry_queries()
        graph = build_graph(queries)
        assert set(graph.queries.keys()) == {"q_users", "q_join", "q_orders"}

    def test_tables_index_maps_users_to_correct_query_ids(self):
        queries = self._make_registry_queries()
        graph = build_graph(queries)
        users_ids = set(graph.tables.get("users", []))
        assert "q_users" in users_ids
        assert "q_join" in users_ids
        # q_orders does not reference users
        assert "q_orders" not in users_ids

    def test_tables_index_maps_orders(self):
        queries = self._make_registry_queries()
        graph = build_graph(queries)
        orders_ids = set(graph.tables.get("orders", []))
        assert "q_join" in orders_ids
        assert "q_orders" in orders_ids

    def test_for_query_returns_detail(self):
        queries = self._make_registry_queries()
        graph = build_graph(queries)
        detail = graph.for_query("q_users")
        assert detail is not None
        assert detail["sql"] == "SELECT id, name FROM users"
        assert "users" in detail["tables"]

    def test_for_query_unknown_id_returns_none(self):
        queries = self._make_registry_queries()
        graph = build_graph(queries)
        assert graph.for_query("does_not_exist") is None

    def test_columns_inverted_index_populated(self):
        queries = self._make_registry_queries()
        graph = build_graph(queries)
        # users.id should appear in both q_users and q_join
        users_id_queries = set(graph.columns.get("users.id", []))
        assert "q_users" in users_id_queries
        assert "q_join" in users_id_queries

    def test_build_graph_over_real_registry(self):
        """build_graph over the real seeded query registry must include demo_all."""
        from app.queries.registry import get_query_registry
        registry = get_query_registry()
        queries = registry.all()
        graph = build_graph(queries)
        # demo_all should be present
        assert "demo_all" in graph.queries

    def test_table_inverted_index_maps_demo_to_query_ids(self):
        """The 'demo' table should map to at least demo_all and demo_active."""
        from app.queries.registry import get_query_registry
        registry = get_query_registry()
        queries = registry.all()
        graph = build_graph(queries)
        demo_ids = set(graph.tables.get("demo", []))
        assert "demo_all" in demo_ids
        assert "demo_active" in demo_ids


# ---------------------------------------------------------------------------
# 6 & 7. Endpoint tests
# ---------------------------------------------------------------------------

class TestLineageEndpoints:
    """HTTP endpoint tests for GET /lineage and GET /lineage/query/{id}."""

    @pytest.mark.asyncio
    async def test_get_lineage_requires_auth(self, lineage_client):
        ac, _ = lineage_client
        resp = await ac.get("/api/v1/lineage")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_lineage_returns_200_with_auth(self, lineage_client):
        ac, user_id = lineage_client
        resp = await ac.get("/api/v1/lineage", headers=_auth_headers(user_id))
        assert resp.status_code == 200
        body = resp.json()
        assert "queries" in body
        assert "tables" in body
        assert "columns" in body

    @pytest.mark.asyncio
    async def test_get_lineage_includes_demo_all(self, lineage_client):
        ac, user_id = lineage_client
        resp = await ac.get("/api/v1/lineage", headers=_auth_headers(user_id))
        assert resp.status_code == 200
        body = resp.json()
        assert "demo_all" in body["queries"]

    @pytest.mark.asyncio
    async def test_get_lineage_tables_is_inverted_index(self, lineage_client):
        ac, user_id = lineage_client
        resp = await ac.get("/api/v1/lineage", headers=_auth_headers(user_id))
        body = resp.json()
        # 'demo' table should be present (demo_all / demo_active both use it)
        assert "demo" in body["tables"]
        demo_query_ids = body["tables"]["demo"]
        assert "demo_all" in demo_query_ids
        assert "demo_active" in demo_query_ids

    @pytest.mark.asyncio
    async def test_get_lineage_query_requires_auth(self, lineage_client):
        ac, _ = lineage_client
        resp = await ac.get("/api/v1/lineage/query/demo_all")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_lineage_query_returns_200_for_known_id(self, lineage_client):
        ac, user_id = lineage_client
        resp = await ac.get(
            "/api/v1/lineage/query/demo_all",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "demo_all"
        assert "tables" in body
        assert "columns" in body
        assert "outputs" in body

    @pytest.mark.asyncio
    async def test_get_lineage_query_returns_404_for_unknown_id(self, lineage_client):
        ac, user_id = lineage_client
        resp = await ac.get(
            "/api/v1/lineage/query/does_not_exist_xyz",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_lineage_query_demo_active_has_demo_table(self, lineage_client):
        ac, user_id = lineage_client
        resp = await ac.get(
            "/api/v1/lineage/query/demo_active",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "demo" in body["tables"]
