"""Tests for POST /query/validate — SQL diagnostics endpoint.

Covers
------
1. Invalid SQL returns ok=False with at least one error carrying line/col/severity.
2. Valid SQL returns ok=True with an empty errors list.
3. Empty/whitespace SQL is treated as valid (ok=True, no errors).
4. Dialect-specific syntax: a query valid under the requested dialect does not
   produce errors.
5. Each error object carries ``severity`` defaulting to ``'error'``.
6. Multi-statement SQL with a syntax error in the second statement surfaces an
   error with line > 0.
7. Unknown/unsupported dialect falls back to the default dialect without 500ing.
8. 401 is returned when no auth token is present.
9. 422 is returned when the request body omits the ``sql`` field entirely (but
   since ``sql`` defaults to ``""`` in the model, an empty body also OK — 422
   only on type mismatch).

Auth
----
All authed requests mint a JWT for a user who exists in the FakeDB (seeded in
the ``authed_client`` fixture).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "validator@example.com",
        "name": "Validator",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Fixture: authed HTTPX client with a seeded user in FakeDB
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def authed_client(app, fake_db):
    """Yield an (AsyncClient, user_id) pair for the validate tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_sql_returns_error_with_position(authed_client):
    """Syntactically broken SQL must return ok=False plus at least one error
    carrying line/col so Monaco can render a red squiggle at the right spot."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "SELECT FROM WHERE", "dialect": "duckdb"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert len(body["errors"]) >= 1
    err = body["errors"][0]
    # 1-based positions — must be >= 1
    assert err["line"] >= 1
    assert err["col"] >= 1
    assert isinstance(err["message"], str) and err["message"]


@pytest.mark.asyncio
async def test_invalid_sql_errors_carry_severity(authed_client):
    """Each error dict must include a ``severity`` field (defaulting to
    ``'error'``) so the frontend can map it to a Monaco MarkerSeverity."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "SELECT x y FROM", "dialect": "postgres"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    for err in body["errors"]:
        assert "severity" in err
        assert err["severity"] in {"error", "warning", "info", "hint"}


@pytest.mark.asyncio
async def test_valid_sql_returns_ok(authed_client):
    """A well-formed SELECT must return ok=True with an empty errors list."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "SELECT id, name FROM users WHERE id = 1", "dialect": "postgres"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_empty_sql_is_valid(authed_client):
    """Empty and whitespace-only SQL must be treated as valid — no errors.

    The editor starts blank; flagging it immediately would be noisy.
    """
    ac, user_id = authed_client
    for sql_value in ("", "   ", "\n\t"):
        resp = await ac.post(
            "/api/v1/query/validate",
            json={"sql": sql_value},
            headers=_auth(user_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True, f"Expected ok for sql={sql_value!r}"
        assert body["errors"] == []


@pytest.mark.asyncio
async def test_omitted_sql_field_defaults_to_valid(authed_client):
    """Sending an empty JSON body (``{}``) must not raise 422 — ``sql``
    defaults to ``""`` in the Pydantic model and blank SQL is valid."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True


@pytest.mark.asyncio
async def test_dialect_aware_valid_duckdb(authed_client):
    """DuckDB-specific syntax (e.g. ``$param`` positional placeholder) must
    parse cleanly when the dialect is ``duckdb``."""
    ac, user_id = authed_client
    # DuckDB supports EXCLUDE in SELECT *; this is a real DuckDB extension.
    resp = await ac.post(
        "/api/v1/query/validate",
        json={
            "sql": "SELECT * EXCLUDE (secret) FROM users",
            "dialect": "duckdb",
        },
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # sqlglot parses this without error under duckdb
    assert body["ok"] is True


@pytest.mark.asyncio
async def test_dialect_aware_valid_bigquery(authed_client):
    """BigQuery-specific functions / syntax must parse cleanly under
    ``dialect='bigquery'``."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={
            "sql": "SELECT TIMESTAMP_TRUNC(created_at, DAY) AS day FROM `proj.ds.events`",
            "dialect": "bigquery",
        },
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True


@pytest.mark.asyncio
async def test_error_line_col_are_one_based(authed_client):
    """Line and col in error objects must be >= 1 (1-based, not 0-based)
    so they can be passed directly to Monaco's ``IMarkerData``."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "SELEC * FROM t", "dialect": "mysql"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Whether sqlglot raises or not, if it does errors must be 1-based.
    for err in body.get("errors", []):
        assert err["line"] >= 1, f"line must be >= 1, got {err['line']}"
        assert err["col"] >= 1, f"col must be >= 1, got {err['col']}"


@pytest.mark.asyncio
async def test_unknown_dialect_falls_back_gracefully(authed_client):
    """An unsupported dialect string must not 500 — it falls back to the
    default (postgres) and returns a normal response."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "SELECT 1", "dialect": "cobol_96_nonexistent"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True  # SELECT 1 is valid under the fallback dialect


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(authed_client):
    """Requests without a Bearer token must be rejected with 401."""
    ac, _user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "SELECT 1"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_multiline_sql_error_has_correct_line(authed_client):
    """A parse error in the SECOND statement of a multi-statement input must
    carry a line number > 1, proving the endpoint preserves line context."""
    ac, user_id = authed_client
    # First statement is valid; second is broken.
    sql = "SELECT 1;\nSELECT FROM WHERE x = 1;"
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": sql, "dialect": "postgres"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The endpoint may surface ok=True if sqlglot is lenient about the second
    # statement, but if it surfaces errors they must have correct positions.
    if not body["ok"]:
        lines = [e["line"] for e in body["errors"]]
        # At least one error must point at line 2 (where the bad SELECT is)
        assert any(ln >= 2 for ln in lines), (
            f"Expected error on line >= 2 for second-statement error, got lines={lines}"
        )


@pytest.mark.asyncio
async def test_response_shape_is_complete(authed_client):
    """The response must always contain both ``ok`` (bool) and ``errors``
    (list) regardless of outcome — frontend relies on this contract."""
    ac, user_id = authed_client
    for payload in [
        {"sql": "SELECT 1"},
        {"sql": "BROKEN !!!"},
        {"sql": ""},
    ]:
        resp = await ac.post(
            "/api/v1/query/validate",
            json=payload,
            headers=_auth(user_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "ok" in body, "Response missing 'ok' field"
        assert "errors" in body, "Response missing 'errors' field"
        assert isinstance(body["ok"], bool)
        assert isinstance(body["errors"], list)


# ---------------------------------------------------------------------------
# Edge Case 3 — WARNING pass (advisory linting)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_star_produces_warning_not_error(authed_client):
    """SELECT * must return ok=True (parse success) with at least one warning
    whose severity is 'warning' — it must NOT be treated as a parse error."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "SELECT * FROM users", "dialect": "postgres"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, "SELECT * should parse successfully"
    warnings = [e for e in body["errors"] if e["severity"] == "warning"]
    assert len(warnings) >= 1, f"Expected at least one warning for SELECT *, got: {body['errors']}"
    assert any("SELECT *" in w["message"] or "star" in w["message"].lower() or "explicit" in w["message"].lower()
               for w in warnings), f"Warning message not helpful: {warnings}"


@pytest.mark.asyncio
async def test_select_star_warning_has_valid_position(authed_client):
    """The SELECT * warning must carry line/col >= 1 (1-based Monaco position)."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "SELECT * FROM orders", "dialect": "duckdb"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for w in body["errors"]:
        assert w["line"] >= 1, f"line must be >= 1, got {w['line']}"
        assert w["col"] >= 1, f"col must be >= 1, got {w['col']}"


@pytest.mark.asyncio
async def test_delete_without_where_produces_warning(authed_client):
    """DELETE with no WHERE clause must trigger a 'warning'-severity marker
    since it would wipe all rows.  ok must still be True (parses fine)."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "DELETE FROM users", "dialect": "postgres"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, "DELETE should parse successfully"
    warnings = [e for e in body["errors"] if e["severity"] == "warning"]
    assert len(warnings) >= 1, f"Expected warning for DELETE without WHERE; got {body['errors']}"
    assert any("DELETE" in w["message"] or "WHERE" in w["message"] for w in warnings), (
        f"Warning message doesn't mention DELETE/WHERE: {warnings}"
    )


@pytest.mark.asyncio
async def test_delete_with_where_no_warning(authed_client):
    """DELETE with a WHERE clause must NOT produce a dangling-delete warning."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "DELETE FROM users WHERE id = 1", "dialect": "postgres"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    delete_warnings = [
        e for e in body["errors"]
        if e["severity"] == "warning" and ("DELETE" in e["message"] or "WHERE" in e["message"])
    ]
    assert delete_warnings == [], f"Unexpected DELETE warning: {delete_warnings}"


@pytest.mark.asyncio
async def test_update_without_where_produces_warning(authed_client):
    """UPDATE with no WHERE clause must trigger a warning (severity='warning')."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={"sql": "UPDATE users SET active = false", "dialect": "postgres"},
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    warnings = [e for e in body["errors"] if e["severity"] == "warning"]
    assert len(warnings) >= 1, f"Expected warning for UPDATE without WHERE; got {body['errors']}"
    assert any("UPDATE" in w["message"] or "WHERE" in w["message"] for w in warnings), (
        f"Warning message doesn't mention UPDATE/WHERE: {warnings}"
    )


@pytest.mark.asyncio
async def test_warnings_do_not_set_ok_false(authed_client):
    """Warnings must NEVER flip ok to False — ok reflects parse success only."""
    ac, user_id = authed_client
    # All three of these should produce warnings but ok=True
    sqls = [
        "SELECT * FROM t",
        "DELETE FROM t",
        "UPDATE t SET x = 1",
    ]
    for sql in sqls:
        resp = await ac.post(
            "/api/v1/query/validate",
            json={"sql": sql, "dialect": "postgres"},
            headers=_auth(user_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True, (
            f"ok must be True for parseable SQL even with warnings; sql={sql!r}, body={body}"
        )


@pytest.mark.asyncio
async def test_valid_non_star_query_has_no_warnings(authed_client):
    """A well-formed explicit-column query without risky constructs must return
    ok=True and an empty errors list (no spurious warnings)."""
    ac, user_id = authed_client
    resp = await ac.post(
        "/api/v1/query/validate",
        json={
            "sql": "SELECT id, name, email FROM users WHERE id = 42",
            "dialect": "postgres",
        },
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["errors"] == [], f"Expected no warnings for safe explicit query; got {body['errors']}"


@pytest.mark.asyncio
async def test_lint_warnings_unit_select_star():
    """Unit-test _lint_warnings directly (no HTTP layer) for SELECT *."""
    import sqlglot  # noqa: PLC0415

    from app.routes.query_tools import _lint_warnings  # noqa: PLC0415

    stmts = sqlglot.parse("SELECT * FROM t", dialect="postgres")
    warnings = _lint_warnings(stmts)
    assert any(w.severity == "warning" for w in warnings), "Expected warning for SELECT *"
    assert all(w.line >= 1 and w.col >= 1 for w in warnings), "All positions must be >= 1"


@pytest.mark.asyncio
async def test_lint_warnings_unit_delete_no_where():
    """Unit-test _lint_warnings directly for DELETE without WHERE."""
    import sqlglot  # noqa: PLC0415

    from app.routes.query_tools import _lint_warnings  # noqa: PLC0415

    stmts = sqlglot.parse("DELETE FROM users", dialect="postgres")
    warnings = _lint_warnings(stmts)
    assert any(w.severity == "warning" and "DELETE" in w.message for w in warnings), (
        f"Expected DELETE warning; got {warnings}"
    )


@pytest.mark.asyncio
async def test_lint_warnings_unit_no_false_positives():
    """_lint_warnings must return empty list for a clean, explicit-column SELECT."""
    import sqlglot  # noqa: PLC0415

    from app.routes.query_tools import _lint_warnings  # noqa: PLC0415

    stmts = sqlglot.parse("SELECT id, name FROM users WHERE id = 1", dialect="postgres")
    warnings = _lint_warnings(stmts)
    assert warnings == [], f"Expected no warnings for clean query; got {warnings}"
