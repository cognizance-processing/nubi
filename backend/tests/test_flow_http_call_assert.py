"""Tests for the ``http_call`` and ``assert`` flow task kinds.

Coverage
--------
http_call handler
-----------------
1.  2xx response → state='success', result has {status_code, ok, response_snippet}.
2.  5xx response → handler raises RuntimeError → execute_task returns state='failed'.
3.  Timeout → raises socket.timeout → state='failed' with timeout message.
4.  SSRF blocked: localhost URL → AppError("ssrf_blocked") → state='failed'.
5.  SSRF blocked: 127.x URL → state='failed'.
6.  Secret not leaked: auth secret value absent from result/error.
7.  Bearer auth: Authorization header injected with secret value.
8.  Missing URL config → AppError("invalid_task_config").
9.  Invalid method → AppError("invalid_task_config").
10. Org host allowlist: host in list → permitted.
11. Org host allowlist: host NOT in list → AppError("http_call_not_allowed").
12. POST with JSON body: Content-Type set automatically.
13. GET method: no body sent.

assert handler
--------------
14. row_count pass: table has 5 rows, min=1 → passes.
15. row_count fail min: table has 5 rows, min=10 → fails run.
16. row_count fail max: table has 5 rows, max=3 → fails run.
17. row_count exact pass: exact=5 → passes.
18. row_count exact fail: exact=3 → fails run.
19. not_null pass: column 'id' has no NULLs.
20. not_null fail: column 'active' not null scenario (custom table).
21. unique pass: 'id' column is unique in demo.
22. unique fail: non-unique column → fails run.
23. unique column-tuple pass.
24. custom_sql zero-row pass: SELECT returns 0 rows.
25. custom_sql zero-row fail: SELECT returns rows → fails run.
26. custom_sql scalar-bool pass: SELECT returns 1 / True.
27. custom_sql scalar-bool fail: SELECT returns 0 / False → fails run.
28. Multiple expectations: one failure → run fails, message names failed expectation.
29. No expectations provided → AppError("invalid_task_config").
30. Unknown expectation kind → AppError("invalid_task_config").

spec validation
---------------
31. http_call spec without 'url' → hard error.
32. http_call spec with 'url' → valid.
33. assert spec without 'expectations' → hard error.
34. assert spec with valid expectations → valid.
35. assert spec with unknown expectation kind → hard error.
36. assert spec with row_count missing min/max/exact → hard error.

registry
--------
37. 'http_call' is registered in the singleton registry.
38. 'assert' is registered in the singleton registry.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.flows.executor import TaskContext, execute_task
from app.flows.handlers.assert_task import handle as assert_handle
from app.flows.handlers.http_call import handle as http_handle
from app.flows.registry import get_task_kind_registry, reset_for_tests
from app.flows.spec import validate_flow_spec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    inputs: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
    org_id: str = "org1",
    flow: dict[str, Any] | None = None,
) -> TaskContext:
    ctx = TaskContext(
        flow_params={},
        inputs=inputs or {},
        now=datetime(2025, 1, 1, tzinfo=timezone.utc),
        org_id=org_id,
    )
    ctx.secrets = secrets or {}
    ctx.flow = flow
    return ctx


_CLAIMS: dict[str, Any] = {"org_id": "org1"}


# ---------------------------------------------------------------------------
# http_call tests
# ---------------------------------------------------------------------------


class TestHttpCallSuccess:
    """1. 2xx response → success."""

    def test_2xx_returns_ok(self):
        config = {"url": "https://example.com/hook", "method": "POST"}
        ctx = _ctx()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"ok": true}'

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="93.184.216.34", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            result = http_handle(config, ctx, _CLAIMS)

        assert result["status_code"] == 200
        assert result["ok"] is True
        assert "response_snippet" in result
        assert result["url"] == "https://example.com/hook"
        assert result["method"] == "POST"


class TestHttpCallFailure:
    """2. 5xx response → raises RuntimeError."""

    def test_5xx_raises(self):
        config = {"url": "https://example.com/hook"}
        ctx = _ctx()

        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.read.return_value = b"Internal Server Error"

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="93.184.216.34", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            with pytest.raises(RuntimeError, match="HTTP 500"):
                http_handle(config, ctx, _CLAIMS)

    def test_5xx_via_execute_task(self):
        """execute_task returns state='failed' when handler raises."""
        task = {
            "kind": "http_call",
            "config": {"url": "https://example.com/hook"},
            "timeout_s": 0,
        }
        ctx = _ctx()

        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.read.return_value = b"Service Unavailable"

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="93.184.216.34", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            outcome = execute_task(task, ctx, _CLAIMS)

        assert outcome["state"] == "failed"
        assert "503" in (outcome.get("error") or "")


class TestHttpCallTimeout:
    """3. Timeout → state='failed'."""

    def test_timeout_fails(self):
        config = {"url": "https://example.com/hook", "timeout_s": 1}
        ctx = _ctx()

        mock_conn = MagicMock()
        mock_conn.request.side_effect = socket.timeout("timed out")

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="93.184.216.34", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            task = {"kind": "http_call", "config": config, "timeout_s": 0}
            outcome = execute_task(task, ctx, _CLAIMS)

        assert outcome["state"] == "failed"


class TestHttpCallSSRF:
    """4–5. SSRF blocked: localhost URLs."""

    def test_localhost_blocked(self):
        from app.errors import AppError
        config = {"url": "http://localhost:8080/internal"}
        ctx = _ctx()
        # Use the real SSRF guard (no mock) — localhost is always blocked
        with pytest.raises((AppError, Exception)) as exc_info:
            http_handle(config, ctx, _CLAIMS)
        err = str(exc_info.value)
        # Either ssrf_blocked AppError or a RuntimeError from the guard
        assert "ssrf" in err.lower() or "block" in err.lower() or "loopback" in err.lower() or "127" in err

    def test_localhost_blocked_via_execute_task(self):
        """execute_task state='failed' when SSRF blocks localhost."""
        task = {
            "kind": "http_call",
            "config": {"url": "http://localhost/secret"},
            "timeout_s": 0,
        }
        ctx = _ctx()
        outcome = execute_task(task, ctx, _CLAIMS)
        assert outcome["state"] == "failed"
        error = outcome.get("error") or ""
        assert any(
            kw in error.lower()
            for kw in ("ssrf", "block", "loopback", "private", "127")
        )

    def test_private_ip_blocked_via_execute_task(self):
        """10.x.x.x is RFC1918 — blocked."""
        task = {
            "kind": "http_call",
            "config": {"url": "http://10.0.0.1/admin"},
            "timeout_s": 0,
        }
        ctx = _ctx()
        outcome = execute_task(task, ctx, _CLAIMS)
        assert outcome["state"] == "failed"


class TestHttpCallSecretNotLeaked:
    """6. Secret value must not appear in result or error."""

    def test_secret_not_in_result(self):
        secret_val = "super-secret-token-abc123"
        config = {
            "url": "https://example.com/hook",
            "auth": {"kind": "bearer", "secret_name": "MY_TOK"},
        }
        ctx = _ctx(secrets={"MY_TOK": secret_val})

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="93.184.216.34", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            task = {"kind": "http_call", "config": config, "timeout_s": 0}
            outcome = execute_task(task, ctx, _CLAIMS)

        # result and error must not contain the raw secret value
        import json as _json
        result_str = _json.dumps(outcome.get("result") or {})
        error_str = outcome.get("error") or ""
        assert secret_val not in result_str
        assert secret_val not in error_str


class TestHttpCallBearerAuth:
    """7. Bearer auth: Authorization header injected."""

    def test_bearer_header_set(self):
        secret_val = "test-bearer-xyz"
        config = {
            "url": "https://example.com/hook",
            "auth": {"kind": "bearer", "secret_name": "TOK"},
        }
        ctx = _ctx(secrets={"TOK": secret_val})

        captured_headers: dict[str, str] = {}

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"

        def fake_request(method, path, body=None, headers=None):
            captured_headers.update(headers or {})

        mock_conn = MagicMock()
        mock_conn.request.side_effect = fake_request
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="1.2.3.4", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            http_handle(config, ctx, _CLAIMS)

        assert captured_headers.get("Authorization") == f"Bearer {secret_val}"


class TestHttpCallMissingURL:
    """8. Missing URL → AppError."""

    def test_missing_url_raises(self):
        from app.errors import AppError
        config = {"method": "POST"}
        ctx = _ctx()
        with pytest.raises(AppError, match="url"):
            http_handle(config, ctx, _CLAIMS)


class TestHttpCallInvalidMethod:
    """9. Invalid method → AppError."""

    def test_invalid_method(self):
        from app.errors import AppError
        config = {"url": "https://example.com/hook", "method": "BREW"}
        ctx = _ctx()
        with pytest.raises(AppError, match="method"):
            http_handle(config, ctx, _CLAIMS)


class TestHttpCallAllowlist:
    """10–11. Org host allowlist."""

    def test_allowed_host_passes(self):
        """Host in allowlist → proceed to SSRF check (mock the rest)."""
        flow = {"runtime_config": {"http_call_allowed_hosts": ["example.com"]}}
        config = {"url": "https://example.com/hook"}
        ctx = _ctx(flow=flow)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="1.2.3.4", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            result = http_handle(config, ctx, _CLAIMS)

        assert result["ok"] is True

    def test_disallowed_host_blocked(self):
        """Host NOT in allowlist → AppError("http_call_not_allowed")."""
        from app.errors import AppError
        flow = {"runtime_config": {"http_call_allowed_hosts": ["allowed.com"]}}
        config = {"url": "https://notallowed.com/hook"}
        ctx = _ctx(flow=flow)

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="notallowed.com", ip="1.2.3.4", port=443),
        ):
            with pytest.raises(AppError) as exc_info:
                http_handle(config, ctx, _CLAIMS)
        assert exc_info.value.code == "http_call_not_allowed"


class TestHttpCallBody:
    """12–13. JSON body and GET method."""

    def test_post_with_body_sets_content_type(self):
        config = {
            "url": "https://example.com/hook",
            "method": "POST",
            "body": {"run_date": "2025-01-01"},
        }
        ctx = _ctx()
        captured_headers: dict[str, str] = {}

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"ok"

        def fake_request(method, path, body=None, headers=None):
            captured_headers.update(headers or {})

        mock_conn = MagicMock()
        mock_conn.request.side_effect = fake_request
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="1.2.3.4", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            http_handle(config, ctx, _CLAIMS)

        assert "application/json" in captured_headers.get("Content-Type", "")

    def test_get_method(self):
        config = {"url": "https://example.com/status", "method": "GET"}
        ctx = _ctx()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"pong"

        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        with patch(
            "app.connectors.ssrf.resolve_and_pin",
            return_value=MagicMock(host="example.com", ip="1.2.3.4", port=443),
        ), patch("http.client.HTTPSConnection", return_value=mock_conn):
            result = http_handle(config, ctx, _CLAIMS)

        assert result["method"] == "GET"
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# assert handler tests
# ---------------------------------------------------------------------------


class TestAssertRowCountPass:
    """14. row_count pass."""

    def test_row_count_min_pass(self):
        config = {
            "target": "demo",
            "expectations": [{"kind": "row_count", "min": 1}],
        }
        ctx = _ctx()
        result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1
        assert result["failed"] == 0

    def test_row_count_max_pass(self):
        config = {
            "target": "demo",
            "expectations": [{"kind": "row_count", "max": 100}],
        }
        ctx = _ctx()
        result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1

    def test_row_count_exact_pass(self):
        config = {
            "target": "demo",
            "expectations": [{"kind": "row_count", "exact": 5}],
        }
        ctx = _ctx()
        result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1


class TestAssertRowCountFail:
    """15–18. row_count fail paths."""

    def test_row_count_min_fail(self):
        config = {
            "target": "demo",
            "expectations": [{"kind": "row_count", "min": 100}],
        }
        ctx = _ctx()
        with pytest.raises(RuntimeError, match="row_count"):
            assert_handle(config, ctx, _CLAIMS)

    def test_row_count_max_fail(self):
        config = {
            "target": "demo",
            "expectations": [{"kind": "row_count", "max": 2}],
        }
        ctx = _ctx()
        with pytest.raises(RuntimeError, match="row_count"):
            assert_handle(config, ctx, _CLAIMS)

    def test_row_count_exact_fail(self):
        config = {
            "target": "demo",
            "expectations": [{"kind": "row_count", "exact": 3}],
        }
        ctx = _ctx()
        with pytest.raises(RuntimeError, match="row_count"):
            assert_handle(config, ctx, _CLAIMS)

    def test_row_count_fail_message_names_expectation(self):
        """Error message must name the expectation and actual count."""
        config = {
            "target": "demo",
            "expectations": [{"kind": "row_count", "min": 999}],
        }
        ctx = _ctx()
        with pytest.raises(RuntimeError) as exc_info:
            assert_handle(config, ctx, _CLAIMS)
        msg = str(exc_info.value)
        assert "row_count" in msg


class TestAssertNotNull:
    """19–20. not_null."""

    def test_not_null_pass(self):
        config = {
            "target": "demo",
            "expectations": [{"kind": "not_null", "columns": ["id", "name"]}],
        }
        ctx = _ctx()
        result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1

    def test_not_null_fail(self):
        """Register a table with a NULL in 'val' column → not_null fail."""
        import pyarrow as pa
        from app.connectors.duckdb_conn import DuckDBConnector

        table = pa.table({
            "id": pa.array([1, 2, 3]),
            "val": pa.array([10, None, 30], type=pa.int64()),
        })
        connector = DuckDBConnector()
        connector.register({"my_table": table})

        config = {
            "target": "my_table",
            "expectations": [{"kind": "not_null", "columns": ["val"]}],
        }
        ctx = _ctx()
        # Patch _resolve_connector to return our pre-loaded connector
        with patch(
            "app.flows.handlers.assert_task._resolve_connector",
            return_value=(connector, "duckdb"),
        ):
            with pytest.raises(RuntimeError, match="not_null"):
                assert_handle(config, ctx, _CLAIMS)


class TestAssertUnique:
    """21–23. unique."""

    def test_unique_column_pass(self):
        config = {
            "target": "demo",
            "expectations": [{"kind": "unique", "columns": ["id"]}],
        }
        ctx = _ctx()
        result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1

    def test_unique_fail(self):
        """Table with duplicate id → unique fails."""
        import pyarrow as pa
        from app.connectors.duckdb_conn import DuckDBConnector

        table = pa.table({
            "id": pa.array([1, 1, 2]),
            "val": pa.array(["a", "b", "c"]),
        })
        connector = DuckDBConnector()
        connector.register({"dup_table": table})

        config = {
            "target": "dup_table",
            "expectations": [{"kind": "unique", "columns": ["id"]}],
        }
        ctx = _ctx()
        with patch(
            "app.flows.handlers.assert_task._resolve_connector",
            return_value=(connector, "duckdb"),
        ):
            with pytest.raises(RuntimeError, match="unique"):
                assert_handle(config, ctx, _CLAIMS)

    def test_unique_column_tuple_pass(self):
        """Unique on (order_id, line_num) tuple — no duplicates."""
        import pyarrow as pa
        from app.connectors.duckdb_conn import DuckDBConnector

        table = pa.table({
            "order_id": pa.array([1, 1, 2]),
            "line_num": pa.array([1, 2, 1]),
        })
        connector = DuckDBConnector()
        connector.register({"order_lines": table})

        config = {
            "target": "order_lines",
            "expectations": [{"kind": "unique", "columns": ["order_id", "line_num"]}],
        }
        ctx = _ctx()
        with patch(
            "app.flows.handlers.assert_task._resolve_connector",
            return_value=(connector, "duckdb"),
        ):
            result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1


class TestAssertCustomSQL:
    """24–27. custom_sql."""

    def test_custom_sql_zero_row_pass(self):
        """SELECT with WHERE that matches nothing → 0 rows → pass."""
        config = {
            "target": "demo",
            "expectations": [
                {
                    "kind": "custom_sql",
                    "sql": "SELECT id FROM demo WHERE value < 0",
                    "name": "no_negative_values",
                }
            ],
        }
        ctx = _ctx()
        result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1

    def test_custom_sql_zero_row_fail(self):
        """SELECT that returns rows → fail."""
        config = {
            "target": "demo",
            "expectations": [
                {
                    "kind": "custom_sql",
                    "sql": "SELECT id FROM demo WHERE value > 0",
                    "name": "no_positive_values",
                }
            ],
        }
        ctx = _ctx()
        with pytest.raises(RuntimeError, match="custom_sql"):
            assert_handle(config, ctx, _CLAIMS)

    def test_custom_sql_scalar_bool_pass(self):
        """SELECT COUNT(*) = 0 AS ok → True → pass."""
        config = {
            "target": "demo",
            "expectations": [
                {
                    "kind": "custom_sql",
                    "sql": "SELECT COUNT(*) = 0 AS ok FROM demo WHERE value < 0",
                    "name": "no_negative_scalar",
                }
            ],
        }
        ctx = _ctx()
        result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1

    def test_custom_sql_scalar_bool_fail(self):
        """SELECT COUNT(*) > 0 AS violation → True scalar → …wait, semantics:
        scalar truthy = PASS.  So to fail: return False scalar."""
        config = {
            "target": "demo",
            "expectations": [
                {
                    "kind": "custom_sql",
                    "sql": "SELECT 0 AS check_result",
                    "name": "always_false",
                }
            ],
        }
        ctx = _ctx()
        with pytest.raises(RuntimeError, match="custom_sql"):
            assert_handle(config, ctx, _CLAIMS)

    def test_custom_sql_target_placeholder(self):
        """{{ target }} placeholder is replaced with the safe table ref."""
        config = {
            "target": "demo",
            "expectations": [
                {
                    "kind": "custom_sql",
                    "sql": "SELECT id FROM {{ target }} WHERE value < 0",
                    "name": "placeholder_test",
                }
            ],
        }
        ctx = _ctx()
        result = assert_handle(config, ctx, _CLAIMS)
        assert result["passed"] == 1


class TestAssertMultipleExpectations:
    """28. Multiple expectations: one failure names expectation."""

    def test_one_fail_message_names_it(self):
        config = {
            "target": "demo",
            "expectations": [
                {"kind": "row_count", "min": 1},       # passes
                {"kind": "row_count", "min": 9999},    # fails
                {"kind": "unique", "columns": ["id"]}, # passes
            ],
        }
        ctx = _ctx()
        with pytest.raises(RuntimeError) as exc_info:
            assert_handle(config, ctx, _CLAIMS)
        msg = str(exc_info.value)
        assert "row_count" in msg
        # Should mention 1 of 3 failed
        assert "1 of 3" in msg


class TestAssertNoExpectations:
    """29. No expectations → AppError."""

    def test_empty_expectations(self):
        from app.errors import AppError
        config = {"target": "demo", "expectations": []}
        ctx = _ctx()
        with pytest.raises(AppError, match="expectations"):
            assert_handle(config, ctx, _CLAIMS)

    def test_missing_expectations_key(self):
        from app.errors import AppError
        config = {"target": "demo"}
        ctx = _ctx()
        with pytest.raises(AppError, match="expectations"):
            assert_handle(config, ctx, _CLAIMS)


class TestAssertUnknownKind:
    """30. Unknown expectation kind → AppError."""

    def test_unknown_expectation_kind(self):
        from app.errors import AppError
        config = {
            "target": "demo",
            "expectations": [{"kind": "magic_check"}],
        }
        ctx = _ctx()
        with pytest.raises(AppError, match="magic_check"):
            assert_handle(config, ctx, _CLAIMS)


# ---------------------------------------------------------------------------
# Spec validation tests
# ---------------------------------------------------------------------------


class TestHttpCallSpec:
    """31–32. Spec validation for http_call."""

    def test_http_call_without_url_is_hard_error(self):
        spec_data = {
            "version": 1,
            "name": "test",
            "tasks": [{"key": "call", "kind": "http_call", "config": {}}],
        }
        _, issues = validate_flow_spec(spec_data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("url" in i.lower() for i in hard)

    def test_http_call_with_url_is_valid(self):
        spec_data = {
            "version": 1,
            "name": "test",
            "tasks": [
                {
                    "key": "call",
                    "kind": "http_call",
                    "config": {"url": "https://example.com/hook"},
                }
            ],
        }
        _, issues = validate_flow_spec(spec_data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert not hard


class TestAssertSpec:
    """33–36. Spec validation for assert."""

    def test_assert_without_expectations_is_hard_error(self):
        spec_data = {
            "version": 1,
            "name": "test",
            "tasks": [{"key": "chk", "kind": "assert", "config": {}}],
        }
        _, issues = validate_flow_spec(spec_data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("expectations" in i.lower() for i in hard)

    def test_assert_with_valid_expectations_is_valid(self):
        spec_data = {
            "version": 1,
            "name": "test",
            "tasks": [
                {
                    "key": "chk",
                    "kind": "assert",
                    "config": {
                        "target": "demo",
                        "expectations": [{"kind": "row_count", "min": 1}],
                    },
                }
            ],
        }
        _, issues = validate_flow_spec(spec_data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert not hard

    def test_assert_unknown_expectation_kind_is_hard_error(self):
        spec_data = {
            "version": 1,
            "name": "test",
            "tasks": [
                {
                    "key": "chk",
                    "kind": "assert",
                    "config": {
                        "expectations": [{"kind": "magic"}],
                    },
                }
            ],
        }
        _, issues = validate_flow_spec(spec_data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("magic" in i.lower() or "unsupported" in i.lower() for i in hard)

    def test_assert_row_count_missing_constraints_is_hard_error(self):
        spec_data = {
            "version": 1,
            "name": "test",
            "tasks": [
                {
                    "key": "chk",
                    "kind": "assert",
                    "config": {
                        "expectations": [{"kind": "row_count"}],
                    },
                }
            ],
        }
        _, issues = validate_flow_spec(spec_data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("row_count" in i.lower() or "min" in i.lower() for i in hard)

    def test_assert_not_null_missing_columns_is_hard_error(self):
        spec_data = {
            "version": 1,
            "name": "test",
            "tasks": [
                {
                    "key": "chk",
                    "kind": "assert",
                    "config": {
                        "expectations": [{"kind": "not_null"}],
                    },
                }
            ],
        }
        _, issues = validate_flow_spec(spec_data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("not_null" in i.lower() or "columns" in i.lower() for i in hard)

    def test_assert_custom_sql_missing_sql_is_hard_error(self):
        spec_data = {
            "version": 1,
            "name": "test",
            "tasks": [
                {
                    "key": "chk",
                    "kind": "assert",
                    "config": {
                        "expectations": [{"kind": "custom_sql"}],
                    },
                }
            ],
        }
        _, issues = validate_flow_spec(spec_data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("custom_sql" in i.lower() or "sql" in i.lower() for i in hard)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    """37–38. Both new kinds are registered."""

    def setup_method(self):
        reset_for_tests()

    def test_http_call_registered(self):
        registry = get_task_kind_registry()
        # Should not raise
        handler = registry.get("http_call")
        assert callable(handler)

    def test_assert_registered(self):
        registry = get_task_kind_registry()
        handler = registry.get("assert")
        assert callable(handler)

    def test_all_includes_new_kinds(self):
        registry = get_task_kind_registry()
        kinds = set(registry.all().keys())
        assert "http_call" in kinds
        assert "assert" in kinds
