"""Attack class 3: SQL injection via named params / template substitution.

Covers
------
3a. Malicious named-param value "' OR 1=1 --" bound positionally → literal,
    does NOT return extra rows
3b. Drop-table attempt in a named param "x'); DROP TABLE users;--" → inert
3c. {{name}} substitution replaces placeholder with $N (never string-concat)
3d. Multi-statement in a param value is inert (treated as a string literal)
3e. Embedded SQL keywords in a param value don't escape the literal context
3f. Policy value containing SQL metacharacters is properly escaped
3g. The planner REJECTS non-SELECT statements (DDL / DML) → 400
3h. UNION-based injection via a named param is inert
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

from tests.security.conftest_helpers import (  # noqa: E402
    mint_access_token,
)


# ---------------------------------------------------------------------------
# Shared DuckDB setup helper
# ---------------------------------------------------------------------------

def _make_conn_with_demo() -> Any:
    """Return a fresh DuckDB connector seeded with a demo table."""
    from app.connectors.duckdb_conn import DuckDBConnector

    conn = DuckDBConnector()
    demo = pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int32()),
            "name": pa.array(["alice", "bob", "charlie"]),
            "value": pa.array([10.0, 20.0, 30.0], type=pa.float64()),
        }
    )
    conn.register({"demo": demo})
    return conn


# ===========================================================================
# 3a. Malicious named param "' OR 1=1 --" → treated as a literal string
# ===========================================================================

def test_sql_injection_or_1_equals_1_is_literal():
    """Named param "' OR 1=1 --" is bound positionally → cannot escape literal context.

    We set up a query that filters WHERE name = {{name}}.  With the injection
    string, only 0 rows should be returned (no name matches the raw injection
    string), not extra rows.
    """
    from app.connectors.planner import plan, resolve_named_params
    from app.queries.registry import QueryParam

    injection = "' OR 1=1 --"
    sql_template = "SELECT * FROM demo WHERE name = {{name}}"
    rewritten_sql, positional_params = resolve_named_params(
        sql_template, {"name": injection}
    )

    # The rewritten SQL should use $1, not the raw string.
    assert "{{name}}" not in rewritten_sql, "Placeholder not replaced"
    assert injection not in rewritten_sql, (
        f"SECURITY FAILURE: injection string embedded directly in SQL: {rewritten_sql}"
    )
    assert "$1" in rewritten_sql, f"Positional binding missing: {rewritten_sql}"
    assert positional_params == [injection], "Value not in positional params list"

    # Execute and confirm 0 matching rows (the demo table has 'alice','bob','charlie').
    conn = _make_conn_with_demo()
    physical_plan = plan(rewritten_sql, claims={}, params=positional_params)
    result = conn.execute(physical_plan)
    assert result.num_rows == 0, (
        f"SECURITY FAILURE: SQL injection via named param returned {result.num_rows} rows "
        f"(expected 0 — the injection string should not match any name)"
    )


# ===========================================================================
# 3b. Drop-table attempt in a named param → inert
# ===========================================================================

def test_sql_injection_drop_table_is_literal():
    """Named param "x'); DROP TABLE demo;--" is a string literal, not SQL."""
    from app.connectors.planner import plan, resolve_named_params

    injection = "x'); DROP TABLE demo;--"
    sql_template = "SELECT * FROM demo WHERE name = {{name}}"
    rewritten_sql, positional_params = resolve_named_params(
        sql_template, {"name": injection}
    )

    # The raw injection must NOT appear in the rewritten SQL.
    assert injection not in rewritten_sql, (
        f"SECURITY FAILURE: injection string in SQL: {rewritten_sql}"
    )
    # The rewritten SQL must remain a single-statement SELECT.
    assert "DROP TABLE" not in rewritten_sql.upper(), (
        f"SECURITY FAILURE: DROP TABLE in rewritten SQL: {rewritten_sql}"
    )
    # Execute: should return 0 rows (no name matches).
    conn = _make_conn_with_demo()
    physical_plan = plan(rewritten_sql, claims={}, params=positional_params)
    result = conn.execute(physical_plan)
    assert result.num_rows == 0, (
        f"SECURITY FAILURE: DROP-injection returned {result.num_rows} rows"
    )


# ===========================================================================
# 3c. {{name}} substitution uses $N positional binding
# ===========================================================================

def test_named_placeholder_resolved_to_positional():
    """resolve_named_params converts {{name}} to $N and collects values."""
    from app.connectors.planner import resolve_named_params

    sql = "SELECT * FROM demo WHERE name = {{name}} AND value > {{threshold}}"
    resolved_sql, params = resolve_named_params(sql, {"name": "alice", "threshold": 5})

    assert "{{name}}" not in resolved_sql
    assert "{{threshold}}" not in resolved_sql
    assert "$1" in resolved_sql
    assert "$2" in resolved_sql
    # Values appear in params list, not embedded in SQL.
    assert "alice" not in resolved_sql
    assert "alice" in params


def test_named_placeholder_same_name_appears_once_in_params():
    """The same {{name}} used twice in SQL maps to one $N slot and one param value."""
    from app.connectors.planner import resolve_named_params

    sql = "SELECT * FROM demo WHERE name = {{name}} OR alias = {{name}}"
    resolved_sql, params = resolve_named_params(sql, {"name": "alice"})

    # Only one slot $1; same $1 used twice.
    assert params == ["alice"], f"Expected single param, got {params}"
    assert resolved_sql.count("$1") == 2, (
        f"Expected $1 to appear twice, got SQL: {resolved_sql}"
    )


# ===========================================================================
# 3d. Multi-statement in a param value is inert
# ===========================================================================

def test_multi_statement_in_param_inert():
    """A param value that contains a semicolon-separated statement is just a string."""
    from app.connectors.planner import plan, resolve_named_params

    injection = "alice; SELECT * FROM demo; --"
    sql_template = "SELECT * FROM demo WHERE name = {{name}}"
    rewritten_sql, positional_params = resolve_named_params(
        sql_template, {"name": injection}
    )

    # Must not contain the injected SQL structure.
    assert "; SELECT" not in rewritten_sql, (
        f"SECURITY FAILURE: multi-statement injection in SQL: {rewritten_sql}"
    )

    conn = _make_conn_with_demo()
    physical_plan = plan(rewritten_sql, claims={}, params=positional_params)
    result = conn.execute(physical_plan)
    # No name matches the injection string → 0 rows.
    assert result.num_rows == 0


# ===========================================================================
# 3e. Embedded SQL keywords in param don't escape literal context
# ===========================================================================

def test_sql_keywords_in_param_are_literal():
    """Keywords like SELECT, WHERE, OR etc. in a param value are treated as strings."""
    from app.connectors.planner import plan, resolve_named_params

    injection = "SELECT * FROM demo WHERE 1=1"
    sql_template = "SELECT * FROM demo WHERE name = {{name}}"
    rewritten_sql, positional_params = resolve_named_params(
        sql_template, {"name": injection}
    )

    conn = _make_conn_with_demo()
    physical_plan = plan(rewritten_sql, claims={}, params=positional_params)
    result = conn.execute(physical_plan)
    # The "injection" is just a non-matching name string → 0 rows.
    assert result.num_rows == 0, (
        f"SECURITY FAILURE: SQL keyword in param returned rows: {result.num_rows}"
    )


# ===========================================================================
# 3f. Policy value containing SQL metacharacters is escaped
# ===========================================================================

def test_rls_policy_metacharacters_escaped():
    """Policy values with SQL metacharacters are properly SQL-escaped (not injected).

    sqlglot escapes single-quotes by doubling them (SQL standard), so the
    text 'DROP TABLE' appears inside a string literal — not as a top-level
    SQL statement.  We verify the generated SQL is still parseable as a
    single SELECT statement, and that executing it returns 0 rows (safe).
    """
    import sqlglot
    import sqlglot.expressions as exp
    from app.connectors.planner import plan
    from app.connectors.duckdb_conn import DuckDBConnector
    import pyarrow as pa

    malicious_tenant = "acme'; DROP TABLE demo; --"
    physical_plan = plan(
        "SELECT * FROM demo",
        claims={"policies": {"tenant_id": malicious_tenant}},
    )

    # 1. SQL must still parse as a single SELECT.
    try:
        reparsed = sqlglot.parse_one(physical_plan.sql)
    except Exception as exc:
        pytest.fail(
            f"SECURITY FAILURE: RLS SQL with metachar value is not valid SQL. "
            f"SQL: {physical_plan.sql!r}. Error: {exc}"
        )
    assert isinstance(reparsed, exp.Select), (
        f"SECURITY FAILURE: top-level node is not SELECT. "
        f"Got {type(reparsed).__name__}. SQL: {physical_plan.sql}"
    )

    # 2. Execute to confirm 0 rows (value treated as literal).
    conn = DuckDBConnector()
    t = pa.table({"tenant_id": pa.array(["acme"]), "id": pa.array([1])})
    conn.register({"demo": t})
    result = conn.execute(physical_plan)
    assert result.num_rows == 0, (
        f"SECURITY FAILURE: malicious tenant value matched rows: {result.num_rows}"
    )


# ===========================================================================
# 3g. Planner rejects non-SELECT statements → 400
# ===========================================================================

def test_planner_rejects_drop_statement():
    """DROP TABLE SQL → AppError UNSUPPORTED_QUERY 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("DROP TABLE demo")
    assert exc_info.value.status == 400
    assert "UNSUPPORTED_QUERY" in exc_info.value.code or exc_info.value.status == 400


def test_planner_rejects_delete_statement():
    """DELETE SQL → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("DELETE FROM demo WHERE id = 1")
    assert exc_info.value.status == 400


def test_planner_rejects_update_statement():
    """UPDATE SQL → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("UPDATE demo SET name = 'hacked' WHERE id = 1")
    assert exc_info.value.status == 400


def test_planner_rejects_insert_statement():
    """INSERT SQL → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("INSERT INTO demo (id, name) VALUES (99, 'injected')")
    assert exc_info.value.status == 400


def test_planner_rejects_create_table():
    """CREATE TABLE SQL → AppError 400."""
    from app.connectors.planner import plan
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        plan("CREATE TABLE evil (id INT)")
    assert exc_info.value.status == 400


# ===========================================================================
# 3h. UNION-based injection via a named param is inert
# ===========================================================================

def test_union_injection_in_named_param_is_literal():
    """A UNION-based injection in a named param is bound as a string literal."""
    from app.connectors.planner import plan, resolve_named_params

    # Classic UNION injection attempt.
    injection = "alice' UNION SELECT 1,2,3 --"
    sql_template = "SELECT * FROM demo WHERE name = {{name}}"
    rewritten_sql, positional_params = resolve_named_params(
        sql_template, {"name": injection}
    )

    # UNION must NOT appear as SQL structure in the rewritten statement.
    assert "UNION" not in rewritten_sql.upper(), (
        f"SECURITY FAILURE: UNION found in rewritten SQL: {rewritten_sql}"
    )

    conn = _make_conn_with_demo()
    physical_plan = plan(rewritten_sql, claims={}, params=positional_params)
    result = conn.execute(physical_plan)
    assert result.num_rows == 0, (
        f"SECURITY FAILURE: UNION injection returned {result.num_rows} rows"
    )


# ===========================================================================
# 3i. First-party raw SQL path: non-SELECT via POST /query → 400
# ===========================================================================

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
        transport=transport, base_url="http://testserver", follow_redirects=False,
    ) as ac:
        yield ac


@pytest.fixture(autouse=True)
def _clear_cache():
    from app.connectors.cache import get_cache
    get_cache().clear()
    yield
    get_cache().clear()


@pytest.mark.asyncio
async def test_first_party_drop_table_via_api_rejected(client):
    """First-party token sending DROP TABLE via raw SQL → 400."""
    token = mint_access_token()
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "DROP TABLE demo"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, (
        f"SECURITY FAILURE: DROP TABLE via API returned {resp.status_code} "
        f"(expected 400)"
    )


@pytest.mark.asyncio
async def test_first_party_delete_via_api_rejected(client):
    """First-party token sending DELETE via raw SQL → 400."""
    token = mint_access_token()
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "DELETE FROM demo WHERE 1=1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, (
        f"SECURITY FAILURE: DELETE via API returned {resp.status_code} "
        f"(expected 400)"
    )


# ===========================================================================
# 3j–3n: Jinja2 template engine security (attack class 3 — template surface)
# ===========================================================================

# ---------------------------------------------------------------------------
# 3j: Sandbox blocks code-exec attempts via template expressions
# ---------------------------------------------------------------------------

def test_jinja2_sandbox_blocks_class_attribute():
    """{{ ''.__class__ }} is blocked by the sandboxed environment."""
    from app.connectors.template import render_sql_template

    with pytest.raises(Exception):
        render_sql_template("SELECT {{ x.__class__ }} FROM t", {"x": ""})


def test_jinja2_sandbox_blocks_mro_chain():
    """Attribute chain reaching Python internals is blocked."""
    from app.connectors.template import render_sql_template

    with pytest.raises(Exception):
        render_sql_template("{{ ().__class__.__bases__[0].__subclasses__() }}", {})


def test_jinja2_sandbox_blocks_globals():
    """{{ x.__globals__ }} is blocked by the sandboxed environment."""
    from app.connectors.template import render_sql_template

    # Use a function object that has __globals__; sandbox should block it.
    def victim():
        pass

    with pytest.raises(Exception):
        render_sql_template("SELECT {{ fn.__globals__ }} FROM t", {"fn": victim})


# ---------------------------------------------------------------------------
# 3k: Control flow ({% if %}/{% for %}) cannot inject SQL
# ---------------------------------------------------------------------------

def test_for_loop_over_injection_strings_all_bound():
    """A {% for %} loop over user-supplied injection strings still binds each value."""
    from app.connectors.template import render_sql_template

    injections = [
        "' OR 1=1 --",
        "x'); DROP TABLE demo;--",
        "alice' UNION SELECT 1,2,3 --",
    ]
    tmpl = (
        "SELECT * FROM demo WHERE name IN "
        "({% for v in items %}{{ v }}{% if not loop.last %},{% endif %}{% endfor %})"
    )
    sql, params = render_sql_template(tmpl, {"items": injections})

    for inj in injections:
        assert inj not in sql, (
            f"SECURITY FAILURE: injection string found in SQL: {sql!r}"
        )
    assert params == injections


def test_if_branch_with_injection_value_binds_safely():
    """{% if %} condition on a user value — the value is still bound when output."""
    from app.connectors.template import render_sql_template

    injection = "'; DELETE FROM users; --"
    tmpl = (
        "SELECT * FROM demo WHERE 1=1 "
        "{% if name %} AND name = {{ name }} {% endif %}"
    )
    sql, params = render_sql_template(tmpl, {"name": injection})

    assert injection not in sql, (
        f"SECURITY FAILURE: injection in rendered SQL: {sql!r}"
    )
    assert "DELETE" not in sql.upper()
    assert params == [injection]


# ---------------------------------------------------------------------------
# 3l: inclause filter binds each element separately
# ---------------------------------------------------------------------------

def test_inclause_injection_elements_bound_not_interpolated():
    """inclause with injection strings — each bound as $N, none in SQL text."""
    from app.connectors.template import render_sql_template

    attacks = [
        "' OR 1=1 --",
        "x'); DROP TABLE demo;--",
    ]
    sql, params = render_sql_template(
        "SELECT * FROM demo WHERE name IN {{ names | inclause }}",
        {"names": attacks},
    )

    for attack in attacks:
        assert attack not in sql, (
            f"SECURITY FAILURE: attack string in SQL: {sql!r}"
        )
    assert params == attacks
    assert "OR" not in sql
    assert "DROP" not in sql.upper()


def test_inclause_numeric_values_bound():
    """inclause with a list of integers — each bound, not literal in SQL.

    We check that the SQL contains ONLY placeholders ($N) and that no
    standalone integer literal appears (checking for the literal to not appear
    outside a placeholder context).  The params list contains the raw values.
    """
    from app.connectors.template import render_sql_template

    ids = [100, 200, 300, 400, 500]  # values that can't appear in '$N' placeholders
    sql, params = render_sql_template(
        "SELECT * FROM demo WHERE id IN {{ ids | inclause }}",
        {"ids": ids},
    )
    assert params == ids
    # The raw integer values must NOT appear as literals in the SQL.
    for n in ids:
        assert str(n) not in sql, (
            f"SECURITY: literal integer {n} found in SQL: {sql!r}"
        )


# ---------------------------------------------------------------------------
# 3m: sqlsafe is an explicit escape hatch, not the default path
# ---------------------------------------------------------------------------

def test_sqlsafe_requires_explicit_filter():
    """Without the sqlsafe filter, values are always bound — never raw."""
    from app.connectors.template import render_sql_template

    # Even if someone tries a 'safe' value, it gets bound.
    sql, params = render_sql_template(
        "SELECT * FROM demo WHERE col = {{ col }}",
        {"col": "revenue"},
    )
    assert "revenue" not in sql  # bound, not raw
    assert params == ["revenue"]


def test_sqlsafe_is_documented_escape_hatch_not_default():
    """The default path (no filter) always binds; sqlsafe must be explicitly used."""
    from app.connectors.template import render_sql_template

    # Default: bound (safe)
    sql_safe, params_safe = render_sql_template(
        "SELECT {{ col }} FROM t",
        {"col": "id"},
    )
    assert "id" not in sql_safe
    assert params_safe == ["id"]

    # Explicit sqlsafe: raw (dangerous — only for trusted server-side values)
    sql_raw, params_raw = render_sql_template(
        "SELECT {{ col | sqlsafe }} FROM t",
        {"col": "id"},  # trusted server-controlled value
    )
    assert "id" in sql_raw
    assert params_raw == []


# ---------------------------------------------------------------------------
# 3n: resolve_named_params (planner) end-to-end injection resistance
# ---------------------------------------------------------------------------

def test_resolve_named_params_or_injection_is_literal():
    """resolve_named_params: OR injection bound positionally, never in SQL."""
    from app.connectors.planner import resolve_named_params, plan

    injection = "' OR 1=1 --"
    sql_tmpl = "SELECT * FROM demo WHERE name = {{name}}"
    rewritten, params = resolve_named_params(sql_tmpl, {"name": injection})

    assert injection not in rewritten, (
        f"SECURITY FAILURE: injection in rewritten SQL: {rewritten!r}"
    )
    assert params == [injection]

    # Execute against DuckDB — must return 0 rows.
    conn = _make_conn_with_demo()
    physical_plan = plan(rewritten, claims={}, params=params)
    result = conn.execute(physical_plan)
    assert result.num_rows == 0, (
        f"SECURITY FAILURE: OR injection returned {result.num_rows} rows"
    )


def test_resolve_named_params_conditional_injection_resistance():
    """Conditional template: injection value still bound even when branch is taken."""
    from app.connectors.planner import resolve_named_params

    injection = "x'); DROP TABLE demo;--"
    tmpl = (
        "SELECT * FROM demo WHERE 1=1 "
        "{% if name %} AND name = {{ name }} {% endif %}"
    )
    rewritten, params = resolve_named_params(tmpl, {"name": injection})

    assert injection not in rewritten
    assert "DROP TABLE" not in rewritten.upper()
    assert params == [injection]


def test_resolve_named_params_inclause_injection_resistance():
    """inclause filter via resolve_named_params: injections bound, not raw."""
    from app.connectors.planner import resolve_named_params

    attacks = ["' OR 1=1 --", "'; DROP TABLE demo;--"]
    tmpl = "SELECT * FROM demo WHERE name IN {{ names | inclause }}"
    rewritten, params = resolve_named_params(tmpl, {"names": attacks})

    for attack in attacks:
        assert attack not in rewritten
    assert params == attacks
    assert "OR" not in rewritten
    assert "DROP" not in rewritten.upper()
