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

import socket
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from app.connectors.http_json import HttpJsonConnector, _records_to_arrow
from app.connectors.plan import PhysicalPlan
from app.connectors.registry import get_connector_registry
from app.errors import AppError


# A public IP every test host "resolves" to, so the TOCTOU-safe pinning
# resolver (resolve_and_pin) sees a safe address and never blocks the mocked
# fetches below.  No real DNS or network call is made anywhere in this module.
_PUBLIC_IP = "93.184.216.34"  # example.com


def _fake_getaddrinfo(*ips: str):
    """Return a fake socket.getaddrinfo resolving any host to *ips*."""

    def _fake(host, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        infos = []
        for ip in ips:
            if ":" in ip:
                family, sockaddr = socket.AF_INET6, (ip, 0, 0, 0)
            else:
                family, sockaddr = socket.AF_INET, (ip, 0)
            infos.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return infos

    return _fake


@contextmanager
def _patched_fetch(response, *, resolves_to: tuple[str, ...] = (_PUBLIC_IP,)):
    """Patch the resolver to a safe public IP and httpx.request -> *response*.

    The connector now pins the connection to a validated IP (DNS-rebinding
    defence) and issues the request via ``httpx.request``; tests must mock both
    the resolution and the request boundary.  Yields the request mock so callers
    can assert on how the request was issued.
    """
    with patch.object(socket, "getaddrinfo", _fake_getaddrinfo(*resolves_to)):
        with patch("httpx.request", return_value=response) as mock_request:
            yield mock_request


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

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.num_rows == 2, "Only acme rows should survive post-fetch RLS"
        tenant_ids = result.column("tenant_id").to_pylist()
        assert set(tenant_ids) == {"acme"}, "Globex must be absent — security guard"
        assert "globex" not in tenant_ids

    def test_rls_globex_plan_returns_only_globex(self) -> None:
        """Symmetry: a globex policy returns only the globex row."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "globex"}})

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.num_rows == 1
        assert result.column("tenant_id").to_pylist() == ["globex"]

    def test_no_rls_returns_all_rows(self) -> None:
        """Empty policies dict -> no filtering -> all rows returned."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(rls_claims={})

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
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

        with _patched_fetch(_fake_httpx_get(records_without_tenant)):
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

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.schema.names == ["id", "tenant_id"]
        assert result.num_rows == 2  # RLS already filtered to acme

    def test_projection_none_returns_all_columns(self) -> None:
        """No projection -> all columns from the JSON response are kept."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(projection=None)

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert set(result.schema.names) == {"id", "tenant_id", "value"}

    def test_projection_missing_col_silently_ignored(self) -> None:
        """A projection column absent from the JSON response is silently dropped."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan(projection=["id", "nonexistent_col"])

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
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

        with patch.object(socket, "getaddrinfo", _fake_getaddrinfo(_PUBLIC_IP)):
            with patch("httpx.request", side_effect=httpx.RequestError("Connection refused")):
                with pytest.raises(AppError) as exc_info:
                    conn.execute(plan)

        err = exc_info.value
        assert err.code == "source_fetch_error"
        assert err.status == 502

    def test_http_error_status_raises_source_fetch_error_502(self) -> None:
        """An HTTP 4xx/5xx response -> AppError source_fetch_error 502."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan()

        with _patched_fetch(_fake_httpx_get({}, status_code=500)):
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

        with _patched_fetch(_fake_httpx_get(nested_body)):
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

        with _patched_fetch(_fake_httpx_get(body)):
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

        with _patched_fetch(_fake_httpx_get(body)):
            with pytest.raises(AppError) as exc_info:
                conn.execute(plan)

        err = exc_info.value
        assert err.code == "source_fetch_error"
        assert err.status == 502

    def test_record_path_none_uses_top_level_list(self) -> None:
        """No record_path -> the top-level body must be the list of records."""
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan()

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
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

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
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
        """Custom headers from config are forwarded to the pinned request."""
        conn = HttpJsonConnector({
            "url": "http://api.example.com/records",
            "headers": {"Authorization": "Bearer test-token", "X-Custom": "value"},
        })
        plan = _make_plan()

        with _patched_fetch(_fake_httpx_get([])) as mock_request:
            conn.execute(plan)

        assert mock_request.called
        _, kwargs = mock_request.call_args
        headers = kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test-token"
        assert headers.get("X-Custom") == "value"

    def test_pinned_request_connects_to_ip_and_preserves_host(self) -> None:
        """TOCTOU defence: the request goes to the validated IP literal, while the
        original hostname is preserved in the Host header and the TLS SNI."""
        conn = HttpJsonConnector({"url": "https://api.example.com/records"})
        plan = _make_plan()

        with _patched_fetch(_fake_httpx_get([]), resolves_to=(_PUBLIC_IP,)) as mock_request:
            conn.execute(plan)

        args, kwargs = mock_request.call_args
        method, target_url = args[0], args[1]
        assert method == "GET"
        # Connection target is the pinned IP literal, NOT the hostname.
        assert _PUBLIC_IP in target_url
        assert "api.example.com" not in target_url
        # Host header + TLS SNI/cert hostname still the original hostname.
        assert kwargs.get("headers", {}).get("Host") == "api.example.com"
        assert kwargs.get("extensions", {}).get("sni_hostname") == "api.example.com"


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestHttpJsonConnectorTimeout:
    """Verify that every request carries an explicit, bounded timeout."""

    def test_request_issued_with_explicit_timeout(self) -> None:
        """httpx.request is called with a timeout= kwarg (not None / missing)."""
        import httpx

        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan()

        with _patched_fetch(_fake_httpx_get([])) as mock_request:
            conn.execute(plan)

        _, kwargs = mock_request.call_args
        timeout = kwargs.get("timeout")
        assert timeout is not None, "timeout must be passed explicitly to httpx.request"
        assert isinstance(timeout, httpx.Timeout), (
            "timeout must be an httpx.Timeout instance so all phases are bounded"
        )

    def test_default_timeout_is_30_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without NUBI_HTTP_CONNECTOR_TIMEOUT_S the default is 30 s."""
        import httpx

        monkeypatch.delenv("NUBI_HTTP_CONNECTOR_TIMEOUT_S", raising=False)
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan()

        with _patched_fetch(_fake_httpx_get([])) as mock_request:
            conn.execute(plan)

        _, kwargs = mock_request.call_args
        timeout: httpx.Timeout = kwargs["timeout"]
        # httpx.Timeout(30) sets connect/read/write/pool all to 30.
        assert timeout.connect == 30.0
        assert timeout.read == 30.0

    def test_custom_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NUBI_HTTP_CONNECTOR_TIMEOUT_S overrides the default."""
        import httpx

        monkeypatch.setenv("NUBI_HTTP_CONNECTOR_TIMEOUT_S", "5")
        conn = HttpJsonConnector({"url": "http://api.example.com/records"})
        plan = _make_plan()

        with _patched_fetch(_fake_httpx_get([])) as mock_request:
            conn.execute(plan)

        _, kwargs = mock_request.call_args
        timeout: httpx.Timeout = kwargs["timeout"]
        assert timeout.connect == 5.0
        assert timeout.read == 5.0

    def test_slow_upstream_raises_source_fetch_error_not_hang(self) -> None:
        """A simulated slow upstream (TimeoutException) surfaces as AppError 502,
        not a hung worker thread.  This is the key reliability property."""
        import httpx

        conn = HttpJsonConnector({"url": "http://slow.example.com/records"})
        plan = _make_plan()

        with patch.object(socket, "getaddrinfo", _fake_getaddrinfo(_PUBLIC_IP)):
            with patch(
                "httpx.request",
                side_effect=httpx.ReadTimeout("timed out reading response"),
            ):
                with pytest.raises(AppError) as exc_info:
                    conn.execute(plan)

        err = exc_info.value
        assert err.code == "source_fetch_error"
        assert err.status == 502

    def test_connect_timeout_surfaces_as_source_fetch_error(self) -> None:
        """A connection-phase timeout (ConnectTimeout) is also caught and wrapped."""
        import httpx

        conn = HttpJsonConnector({"url": "http://unreachable.example.com/records"})
        plan = _make_plan()

        with patch.object(socket, "getaddrinfo", _fake_getaddrinfo(_PUBLIC_IP)):
            with patch(
                "httpx.request",
                side_effect=httpx.ConnectTimeout("connection timed out"),
            ):
                with pytest.raises(AppError) as exc_info:
                    conn.execute(plan)

        err = exc_info.value
        assert err.code == "source_fetch_error"
        assert err.status == 502


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

        with _patched_fetch(_fake_httpx_get(_TWO_TENANT_RECORDS)):
            result = conn.execute(plan)

        assert result.num_rows == 2


# ---------------------------------------------------------------------------
# DNS-rebinding / TOCTOU regression
# ---------------------------------------------------------------------------


class TestHttpJsonDnsRebinding:
    """Regression: a host that passes the SSRF check then rebinds to an internal
    address at connect time must NOT reach that address."""

    def test_rebind_to_internal_is_blocked_and_pinned(self) -> None:
        """Resolver returns a PUBLIC IP at check time, then would return an
        INTERNAL IP if re-resolved at connect time.

        Because the connector resolves once and pins the connection to the
        validated public IP, the second (malicious) resolution is never used:
        httpx.request is invoked with the pinned public IP literal, never the
        internal one — closing the TOCTOU window.
        """
        conn = HttpJsonConnector({"url": "http://rebind.example.com/records"})
        plan = _make_plan()

        # A resolver whose answer FLIPS between calls: public first (check),
        # internal second (the rebind a naive client would connect to).
        answers = iter(
            [
                _fake_getaddrinfo(_PUBLIC_IP),       # 1st call: SSRF check
                _fake_getaddrinfo("169.254.169.254"),  # 2nd call: rebind
            ]
        )

        def _flipping(host, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            return next(answers)(host, *args, **kwargs)

        with patch.object(socket, "getaddrinfo", _flipping):
            with patch("httpx.request", return_value=_fake_httpx_get([])) as mock_request:
                conn.execute(plan)

        # The connection was pinned to the checked PUBLIC IP, not the rebind.
        target_url = mock_request.call_args.args[1]
        assert _PUBLIC_IP in target_url
        assert "169.254.169.254" not in target_url

    def test_rebind_resolving_only_to_internal_is_blocked(self) -> None:
        """If the host resolves to an internal address at check time, the fetch
        is blocked outright and no request is ever issued."""
        conn = HttpJsonConnector({"url": "http://evil.example.com/records"})
        plan = _make_plan()

        with patch.object(socket, "getaddrinfo", _fake_getaddrinfo("169.254.169.254")):
            with patch("httpx.request") as mock_request:
                with pytest.raises(AppError) as exc_info:
                    conn.execute(plan)

        assert exc_info.value.code == "ssrf_blocked"
        assert mock_request.call_count == 0

    def test_mixed_public_and_internal_addresses_blocked(self) -> None:
        """A public A-record cannot mask an internal one: every resolved address
        is validated, so the presence of any forbidden address blocks the fetch."""
        conn = HttpJsonConnector({"url": "http://sneaky.example.com/records"})
        plan = _make_plan()

        with patch.object(socket, "getaddrinfo", _fake_getaddrinfo(_PUBLIC_IP, "127.0.0.1")):
            with patch("httpx.request") as mock_request:
                with pytest.raises(AppError) as exc_info:
                    conn.execute(plan)

        assert exc_info.value.code == "ssrf_blocked"
        assert mock_request.call_count == 0
