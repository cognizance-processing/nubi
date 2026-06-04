"""Tests for HttpJsonConnector (M9-B).

Coverage
--------
- Post-fetch RLS: plan with tenant_id='acme' -> only acme rows returned; globex DROPPED.
- Projection: plan.projection=['id','tenant_id'] -> only those columns.
- Fetch error path: httpx raises -> AppError source_fetch_error (502).
- record_path navigation: body={'data':{'items':[...]}} with record_path='data.items'.
- Fail-closed: policy on a column absent from the JSON response -> AppError rls_column_missing 403.
- Record normalisation: missing keys in some records become nulls (union-of-keys semantics).
- Empty records list -> empty Arrow table.
- Registry: get('http_json') returns a working factory.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from app.connectors.http_json import HttpJsonConnector, _records_to_arrow, _navigate_record_path
from app.connectors.plan import PhysicalPlan
from app.connectors.registry import get_connector_registry
from app.errors import AppError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(
    *,
    rls_claims: dict | None = None,
    projection: list[str] | None = None,
    sql: str = "SELECT 1",
    params: list | None = None,
) -> PhysicalPlan:
    """Construct a minimal PhysicalPlan for testing."""
    return PhysicalPlan(
        dialect="duckdb",
        sql=sql,
        params=params or [],
        projection=projection,
        predicates=[],
        rls_claims=rls_claims or {},
        cache_key="cafebabe" * 8,  # 64-char fake SHA-256
    )


def _fake_httpx_get(body: Any, status_code: int = 200):
    """Return a mock that patches httpx.get to return *body* as JSON."""
    mock_response = MagicMock()
    mock_response.json.return_value = body
    mock_response.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code),
        )
    return mock_response


# Two-tenant sample records used in most tests
_TWO_TENANT_RECORDS = [
    {"id": 1, "tenant_id": "acme",   "value": 10},
    {"id": 2, "tenant_id": "acme",   "value": 20},
    {"id": 3, "tenant_id": "globex", "value": 30},
]


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestHttpJsonConnectorCapabilities:
    def test_capabilities_shape(self) -> None:
        conn = HttpJsonConnector({"url": "http://example.com/api"})
        caps = conn.capabilities()
        assert caps["predicate_rls"] is True
        assert caps["predicate_pushdown"] is False
        assert caps["projection_pushdown"] is False
        assert caps["native_arrow"] is False
        assert caps["partition_pushdown"] is False
        assert caps["column_masking"] is False
        assert caps["streaming_cdc"] is False
        # All 7 keys present
        assert len(caps) == 7


# ---------------------------------------------------------------------------
# Post-fetch RLS: the core security test
# ---------------------------------------------------------------------------


class TestHttpJsonConnectorRls:
    """Prove that tenant rows are filtered server-side post-fetch on a JSON API source."""

    def test_rls_drops_globex_returns_only_acme(self) -> None:
        """THE CORE SECURITY TEST: acme plan -> only acme rows; globex absent.

        This proves that when an HTTP/JSON source cannot push down predicates,
        Nubi's server-side post-fetch RLS still enforces tenant isolation.
        The globex row is fetched from the API but DROPPED before the table
        is returned to the caller.
        """
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.num_rows == 2, "Only acme rows should survive post-fetch RLS"
        tenant_ids = result.column("tenant_id").to_pylist()
        assert set(tenant_ids) == {"acme"}, "Globex must be absent — security guard"
        assert "globex" not in tenant_ids

    def test_rls_globex_plan_returns_only_globex(self) -> None:
        """Symmetry: a globex policy returns only the globex row."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "globex"}})

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.num_rows == 1
        assert result.column("tenant_id").to_pylist() == ["globex"]

    def test_no_rls_returns_all_rows(self) -> None:
        """Empty policies dict -> no filtering -> all rows returned."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(rls_claims={})

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.num_rows == 3

    def test_rls_fail_closed_policy_column_absent_raises_403(self) -> None:
        """A policy on a column NOT in the JSON response -> 403 rls_column_missing.

        This is the fail-closed property: if the API doesn't return the column
        used in the RLS policy, we MUST NOT return unfiltered data.
        """
        records_without_tenant = [
            {"id": 1, "value": 10},
            {"id": 2, "value": 20},
        ]
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        with patch("httpx.get", return_value=_fake_httpx_get(records_without_tenant)):
            with pytest.raises(AppError) as exc_info:
                conn.execute(plan)

        err = exc_info.value
        assert err.code == "rls_column_missing"
        assert err.status == 403


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


class TestHttpJsonConnectorProjection:
    def test_projection_narrows_columns(self) -> None:
        """plan.projection=['id','tenant_id'] -> only those two columns returned."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(
            rls_claims={"policies": {"tenant_id": "acme"}},
            projection=["id", "tenant_id"],
        )

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.schema.names == ["id", "tenant_id"]
        assert result.num_rows == 2  # RLS already filtered to acme

    def test_projection_none_returns_all_columns(self) -> None:
        """No projection -> all columns from the JSON response are kept."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(projection=None)

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert set(result.schema.names) == {"id", "tenant_id", "value"}

    def test_projection_missing_col_silently_ignored(self) -> None:
        """A projection column absent from the JSON response is silently dropped."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(projection=["id", "nonexistent_col"])

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.schema.names == ["id"]


# ---------------------------------------------------------------------------
# Fetch error path
# ---------------------------------------------------------------------------


class TestHttpJsonConnectorFetchError:
    def test_network_error_raises_source_fetch_error_502(self) -> None:
        """A network-level error (RequestError) -> AppError source_fetch_error 502."""
        import httpx

        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan()

        with patch("httpx.get", side_effect=httpx.RequestError("Connection refused")):
            with pytest.raises(AppError) as exc_info:
                conn.execute(plan)

        err = exc_info.value
        assert err.code == "source_fetch_error"
        assert err.status == 502

    def test_http_error_status_raises_source_fetch_error_502(self) -> None:
        """An HTTP 4xx/5xx response -> AppError source_fetch_error 502."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan()

        with patch("httpx.get", return_value=_fake_httpx_get({}, status_code=500)):
            with pytest.raises(AppError) as exc_info:
                conn.execute(plan)

        err = exc_info.value
        assert err.code == "source_fetch_error"
        assert err.status == 502


# ---------------------------------------------------------------------------
# record_path navigation
# ---------------------------------------------------------------------------


class TestHttpJsonConnectorRecordPath:
    def test_record_path_navigates_nested_body(self) -> None:
        """record_path='data.items' navigates body['data']['items'] to the records."""
        nested_body = {
            "data": {
                "items": _TWO_TENANT_RECORDS,
                "total": 3,
            },
            "meta": {"page": 1},
        }
        conn = HttpJsonConnector({
            "url": "http://api.example.com/records",
            "record_path": "data.items",
        })
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        with patch("httpx.get", return_value=_fake_httpx_get(nested_body)):
            result = conn.execute(plan)

        # Should have navigated to data.items and applied RLS
        assert result.num_rows == 2
        assert set(result.column("tenant_id").to_pylist()) == {"acme"}

    def test_record_path_single_segment(self) -> None:
        """record_path='records' navigates a single level."""
        body = {"records": _TWO_TENANT_RECORDS}
        conn = HttpJsonConnector({
            "url": "http://api.example.com/records",
            "record_path": "records",
        })
        plan = _make_plan()

        with patch("httpx.get", return_value=_fake_httpx_get(body)):
            result = conn.execute(plan)

        assert result.num_rows == 3

    def test_record_path_missing_key_raises_source_fetch_error(self) -> None:
        """A record_path that can't be navigated -> AppError source_fetch_error 502."""
        body = {"data": {"wrong_key": []}}
        conn = HttpJsonConnector({
            "url": "http://api.example.com/records",
            "record_path": "data.items",  # 'items' is missing
        })
        plan = _make_plan()

        with patch("httpx.get", return_value=_fake_httpx_get(body)):
            with pytest.raises(AppError) as exc_info:
                conn.execute(plan)

        err = exc_info.value
        assert err.code == "source_fetch_error"
        assert err.status == 502

    def test_record_path_none_uses_top_level_list(self) -> None:
        """No record_path -> the top-level body must be the list of records."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan()

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.num_rows == 3


# ---------------------------------------------------------------------------
# Record normalisation
# ---------------------------------------------------------------------------


class TestRecordsToArrow:
    def test_union_of_keys_missing_become_null(self) -> None:
        """Records with different key sets: missing keys -> null in Arrow."""
        records = [
            {"id": 1, "name": "alice", "score": 99},
            {"id": 2, "name": "bob"},           # no 'score'
            {"id": 3,                "score": 77},  # no 'name'
        ]
        table = _records_to_arrow(records)

        assert set(table.schema.names) == {"id", "name", "score"}
        assert table.num_rows == 3
        # Row 1 has score=None
        scores = table.column("score").to_pylist()
        assert scores[1] is None
        # Row 2 has name=None
        names = table.column("name").to_pylist()
        assert names[2] is None

    def test_empty_records_returns_empty_table(self) -> None:
        """An empty list of records returns an empty table with no columns."""
        table = _records_to_arrow([])
        assert table.num_rows == 0
        assert table.num_columns == 0

    def test_column_order_follows_first_seen(self) -> None:
        """Columns appear in the order first encountered across all records."""
        records = [
            {"a": 1, "b": 2},
            {"c": 3, "a": 4},
        ]
        table = _records_to_arrow(records)
        # 'a' and 'b' appear first, then 'c'
        assert table.schema.names == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# execute_stream
# ---------------------------------------------------------------------------


class TestHttpJsonConnectorStream:
    def test_execute_stream_yields_batches_matching_execute(self) -> None:
        """execute_stream yields batches that reconstruct the execute() result."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            batches = list(conn.execute_stream(plan))

        assert len(batches) > 0
        combined = pa.Table.from_batches(batches)
        assert combined.num_rows == 2
        assert set(combined.column("tenant_id").to_pylist()) == {"acme"}


# ---------------------------------------------------------------------------
# Headers are forwarded
# ---------------------------------------------------------------------------


class TestHttpJsonConnectorHeaders:
    def test_custom_headers_passed_to_httpx(self) -> None:
        """Custom headers from config are forwarded to httpx.get."""
        conn = HttpJsonConnector({
            "url": "http://api.example.com/records",
            "headers": {"Authorization": "Bearer test-token", "X-Custom": "value"},
        })
        plan = _make_plan()

        with patch("httpx.get", return_value=_fake_httpx_get([])) as mock_get:
            conn.execute(plan)

        call_kwargs = mock_get.call_args
        passed_headers = call_kwargs.kwargs.get("headers") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("headers", {})
        # Just verify httpx.get was called with the right URL and headers kwarg
        assert mock_get.called
        _, kwargs = mock_get.call_args
        assert kwargs.get("headers", {}).get("Authorization") == "Bearer test-token"


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestHttpJsonRegistryIntegration:
    def test_registry_get_http_json_returns_factory(self) -> None:
        """get('http_json') from the singleton registry returns a working factory."""
        registry = get_connector_registry()
        factory = registry.get("http_json")
        conn = factory({"url": "http://api.example.com"})
        assert isinstance(conn, HttpJsonConnector)

    def test_registry_http_json_connector_is_functional(self) -> None:
        """Factory-created connector can execute a plan (smoke test)."""
        registry = get_connector_registry()
        factory = registry.get("http_json")
        conn = factory({"url": "http://api.example.com/data"})

        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        with patch("httpx.get", return_value=_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.num_rows == 2
