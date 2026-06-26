"""E2E: Live coverage for health / lineage / transpile / MCP endpoints.

Tests run against a REAL uvicorn + demo-seeded Postgres + DuckDB.
Gated: RUN_E2E=1 (see conftest.py).

Areas covered
-------------
Health
    GET /health/freshness          — all seeded datasets present, incl. stale
    GET /health/freshness/{key}    — path-encoded lookup, status returned
    GET /health/score              — numeric scores, grades, dimensions
    GET /health/estate             — nodes + edges graph

Lineage
    GET /lineage/dag               — nodes + edges for org's queries/metrics
    GET /metrics/retail_nsv/lineage — input_columns + upstream

Transpile
    POST /transpile  (valid dialects) — 200 + transpiled sql
    POST /transpile  (unknown dialect) — 400

MCP registry
    POST /mcp/servers (valid https url) — 201, secret not returned
    GET  /mcp/servers                   — lists it, no secret
    POST /mcp/servers (localhost url)   — SSRF-blocked (400)
    POST /mcp/servers unauthenticated   — 401/403

Nubi-as-MCP-server  (POST /mcp)
    tools/list — returns tool catalog
    tools/call query_metric (retail_nsv) — real rows (author token)
    tools/call run_query with raw SQL, read-only token — blocked (no author:sql)
    unauthenticated /mcp — 401/403
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING


if TYPE_CHECKING:
    from tests.e2e.conftest import E2EContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rpc(client, headers, method: str, params: dict | None = None, rpc_id: int = 1) -> Any:
    """Send a JSON-RPC 2.0 request to POST /mcp and return the parsed response body."""
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        payload["params"] = params
    resp = client.post("/mcp", json=payload, headers=headers)
    return resp


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealthFreshness:
    """GET /health/freshness and /health/freshness/{dataset_key}."""

    def test_list_freshness_returns_seeded_datasets(self, e2e_ctx: E2EContext) -> None:
        """All four seeded datasets should appear in the freshness list."""
        resp = e2e_ctx.client.get("/health/freshness", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "datasets" in data, f"No 'datasets' key in response: {data}"
        keys = {d["dataset_key"] for d in data["datasets"]}
        expected = {"model/revenue", "raw/orders", "raw/products", "raw/sessions"}
        missing = expected - keys
        assert not missing, f"Missing seeded datasets in freshness list: {missing}"

    def test_list_freshness_includes_stale_sessions(self, e2e_ctx: E2EContext) -> None:
        """raw/sessions is seeded as stale — must appear with status='stale'."""
        resp = e2e_ctx.client.get("/health/freshness", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        stale = [d for d in data["datasets"] if d["dataset_key"] == "raw/sessions"]
        assert stale, "raw/sessions not found in freshness list"
        assert stale[0]["status"] == "stale", (
            f"Expected raw/sessions to be stale, got {stale[0]['status']!r}"
        )

    def test_get_freshness_path_encoded(self, e2e_ctx: E2EContext) -> None:
        """GET /health/freshness/raw%2Fsessions (path-encoded) returns status stale."""
        resp = e2e_ctx.client.get(
            "/health/freshness/raw%2Fsessions",
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("dataset_key") == "raw/sessions", (
            f"Unexpected dataset_key: {data.get('dataset_key')!r}"
        )
        assert data.get("status") == "stale", (
            f"Expected status='stale', got {data.get('status')!r}"
        )

    def test_get_freshness_fresh_dataset(self, e2e_ctx: E2EContext) -> None:
        """GET /health/freshness/raw%2Forders returns status 'fresh'."""
        resp = e2e_ctx.client.get(
            "/health/freshness/raw%2Forders",
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("status") == "fresh", (
            f"Expected raw/orders to be fresh, got {data.get('status')!r}"
        )

    def test_get_freshness_not_found(self, e2e_ctx: E2EContext) -> None:
        """Unknown dataset key returns 404."""
        resp = e2e_ctx.client.get(
            "/health/freshness/nonexistent%2Fdataset",
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 404, (
            f"Expected 404 for nonexistent dataset, got {resp.status_code}"
        )

    def test_list_freshness_requires_auth(self, e2e_ctx: E2EContext) -> None:
        """Unauthenticated call is rejected."""
        resp = e2e_ctx.client.get("/health/freshness")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )


class TestHealthScore:
    """GET /health/score — weighted scoring."""

    def test_score_returns_datasets_with_numeric_scores(self, e2e_ctx: E2EContext) -> None:
        """Response has datasets list; each entry has a numeric score and grade."""
        resp = e2e_ctx.client.get("/health/score", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "datasets" in data, f"No 'datasets' key: {data}"
        datasets = data["datasets"]
        assert len(datasets) > 0, "No datasets in /health/score response"
        for ds in datasets:
            assert isinstance(ds.get("score"), (int, float)), (
                f"score not numeric for {ds.get('dataset_key')!r}: {ds.get('score')!r}"
            )
            assert 0 <= ds["score"] <= 100, (
                f"score out of range [0,100] for {ds.get('dataset_key')!r}: {ds['score']}"
            )
            assert isinstance(ds.get("grade"), str), (
                f"grade not a string for {ds.get('dataset_key')!r}: {ds.get('grade')!r}"
            )
            assert "dimensions" in ds and isinstance(ds["dimensions"], list), (
                f"dimensions missing/wrong type for {ds.get('dataset_key')!r}"
            )

    def test_score_dimensions_have_names_and_scores(self, e2e_ctx: E2EContext) -> None:
        """Each dimension entry has a name; scores are numeric or None (unknown).

        Some dimensions (e.g. completeness) return score=None when there is no
        run history available, which is a valid server response — we assert the
        shape is correct (name present, score is numeric-or-None) rather than
        requiring all scores to be numeric.
        """
        resp = e2e_ctx.client.get("/health/score", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        for ds in data.get("datasets", []):
            for dim in ds.get("dimensions", []):
                assert "name" in dim, f"dimension missing 'name': {dim}"
                score = dim.get("score")
                assert score is None or isinstance(score, (int, float)), (
                    f"dimension score must be numeric or None in {ds.get('dataset_key')!r}: {dim}"
                )

    def test_score_stale_dataset_has_lower_score(self, e2e_ctx: E2EContext) -> None:
        """raw/sessions (stale) should have a lower health score than a fresh dataset."""
        resp = e2e_ctx.client.get("/health/score", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        by_key = {d["dataset_key"]: d for d in data.get("datasets", [])}
        if "raw/sessions" in by_key and "raw/orders" in by_key:
            sessions_score = by_key["raw/sessions"]["score"]
            orders_score = by_key["raw/orders"]["score"]
            assert sessions_score < orders_score, (
                f"Expected stale sessions ({sessions_score}) < fresh orders ({orders_score})"
            )

    def test_score_requires_auth(self, e2e_ctx: E2EContext) -> None:
        """Unauthenticated call is rejected."""
        resp = e2e_ctx.client.get("/health/score")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )


class TestHealthEstate:
    """GET /health/estate — graph of nodes + edges."""

    def test_estate_returns_graph_shape(self, e2e_ctx: E2EContext) -> None:
        """Response is a dict with 'nodes' and 'edges' lists."""
        resp = e2e_ctx.client.get("/health/estate", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "nodes" in data, f"No 'nodes' key in estate response: {data}"
        assert "edges" in data, f"No 'edges' key in estate response: {data}"
        assert isinstance(data["nodes"], list), "nodes is not a list"
        assert isinstance(data["edges"], list), "edges is not a list"

    def test_estate_nodes_have_seeded_datasets(self, e2e_ctx: E2EContext) -> None:
        """Freshness-seeded datasets appear as nodes in the estate."""
        resp = e2e_ctx.client.get("/health/estate", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        node_keys = {n["key"] for n in data["nodes"] if "key" in n}
        expected = {"model/revenue", "raw/orders", "raw/products", "raw/sessions"}
        missing = expected - node_keys
        assert not missing, f"Missing seeded dataset nodes in estate: {missing}"

    def test_estate_nodes_have_type_and_status(self, e2e_ctx: E2EContext) -> None:
        """Each node must carry 'type' and 'status'."""
        resp = e2e_ctx.client.get("/health/estate", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, resp.text
        data = resp.json()
        for node in data.get("nodes", []):
            assert "type" in node, f"Node missing 'type': {node}"
            assert "status" in node, f"Node missing 'status': {node}"

    def test_estate_requires_auth(self, e2e_ctx: E2EContext) -> None:
        """Unauthenticated call is rejected."""
        resp = e2e_ctx.client.get("/health/estate")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


class TestLineageDag:
    """GET /lineage/dag — full inter-model DAG."""

    def test_dag_returns_nodes_and_edges(self, e2e_ctx: E2EContext) -> None:
        """Response has 'nodes' and 'edges' keys (even if both are empty)."""
        resp = e2e_ctx.client.get("/lineage/dag", headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "nodes" in data, f"No 'nodes' key in /lineage/dag response: {data}"
        assert "edges" in data, f"No 'edges' key in /lineage/dag response: {data}"
        assert isinstance(data["nodes"], (list, dict)), "nodes is unexpected type"
        assert isinstance(data["edges"], (list, dict)), "edges is unexpected type"

    def test_dag_requires_auth(self, e2e_ctx: E2EContext) -> None:
        """Unauthenticated call is rejected."""
        resp = e2e_ctx.client.get("/lineage/dag")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )

    def test_dag_read_token_allowed(self, e2e_ctx: E2EContext) -> None:
        """read:* token (no author:sql) can still read the DAG."""
        resp = e2e_ctx.client.get("/lineage/dag", headers=e2e_ctx.read_headers())
        # lineage/dag uses current_user (not verified_identity with scope check)
        # so it should succeed for any valid token
        assert resp.status_code == 200, (
            f"Expected 200 for read-only token, got {resp.status_code}: {resp.text}"
        )


class TestMetricLineage:
    """GET /metrics/{id}/lineage — column-level metric lineage."""

    def test_retail_nsv_lineage_shape(self, e2e_ctx: E2EContext) -> None:
        """retail_nsv lineage returns metric_id, input_columns, and upstream."""
        resp = e2e_ctx.client.get(
            "/metrics/retail_nsv/lineage",
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "metric_id" in data, f"No 'metric_id' in lineage response: {data}"
        assert "input_columns" in data, f"No 'input_columns' in lineage response: {data}"
        assert "upstream" in data, f"No 'upstream' in lineage response: {data}"
        assert isinstance(data["input_columns"], list), "input_columns is not a list"
        assert isinstance(data["upstream"], list), "upstream is not a list"
        assert data["metric_id"] == "retail_nsv", (
            f"Expected metric_id='retail_nsv', got {data['metric_id']!r}"
        )

    def test_metric_lineage_not_found(self, e2e_ctx: E2EContext) -> None:
        """Unknown metric id returns 404."""
        resp = e2e_ctx.client.get(
            "/metrics/nonexistent_metric_xyz/lineage",
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown metric, got {resp.status_code}"
        )

    def test_metric_lineage_requires_auth(self, e2e_ctx: E2EContext) -> None:
        """Unauthenticated call is rejected."""
        resp = e2e_ctx.client.get("/metrics/retail_nsv/lineage")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Transpile
# ---------------------------------------------------------------------------


class TestTranspile:
    """POST /transpile — pure SQL dialect conversion."""

    def test_select_1_duckdb_to_bigquery(self, e2e_ctx: E2EContext) -> None:
        """SELECT 1 transpiles from duckdb -> bigquery; 200 with non-empty sql."""
        resp = e2e_ctx.client.post(
            "/transpile",
            json={"sql": "SELECT 1", "from_dialect": "duckdb", "to_dialect": "bigquery"},
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "sql" in data, f"No 'sql' key in transpile response: {data}"
        assert data["sql"].strip(), "Transpiled SQL is empty"

    def test_simple_aggregate_duckdb_to_snowflake(self, e2e_ctx: E2EContext) -> None:
        """A real-world aggregation transpiles and retains structure."""
        sql_in = "SELECT region, SUM(revenue) AS total FROM sales GROUP BY 1"
        resp = e2e_ctx.client.post(
            "/transpile",
            json={"sql": sql_in, "from_dialect": "duckdb", "to_dialect": "snowflake"},
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "sql" in data
        assert data["sql"].strip()

    def test_unknown_from_dialect_is_400(self, e2e_ctx: E2EContext) -> None:
        """Unrecognised from_dialect returns 400."""
        resp = e2e_ctx.client.post(
            "/transpile",
            json={
                "sql": "SELECT 1",
                "from_dialect": "notarealenginexyz",
                "to_dialect": "bigquery",
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 400, (
            f"Expected 400 for unknown from_dialect, got {resp.status_code}: {resp.text}"
        )

    def test_unknown_to_dialect_is_400(self, e2e_ctx: E2EContext) -> None:
        """Unrecognised to_dialect returns 400."""
        resp = e2e_ctx.client.post(
            "/transpile",
            json={
                "sql": "SELECT 1",
                "from_dialect": "duckdb",
                "to_dialect": "fakesystem99",
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 400, (
            f"Expected 400 for unknown to_dialect, got {resp.status_code}: {resp.text}"
        )

    def test_transpile_requires_auth(self, e2e_ctx: E2EContext) -> None:
        """Unauthenticated call is rejected."""
        resp = e2e_ctx.client.post(
            "/transpile",
            json={"sql": "SELECT 1", "from_dialect": "duckdb", "to_dialect": "bigquery"},
        )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403, got {resp.status_code}"
        )

    def test_read_only_token_can_transpile(self, e2e_ctx: E2EContext) -> None:
        """read:* token (no author:sql) can use transpile (pure transform, no data access)."""
        resp = e2e_ctx.client.post(
            "/transpile",
            json={"sql": "SELECT 1", "from_dialect": "duckdb", "to_dialect": "postgres"},
            headers=e2e_ctx.read_headers(),
        )
        assert resp.status_code == 200, (
            f"Expected 200 for read-only token on transpile, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# MCP server registry
# ---------------------------------------------------------------------------


class TestMcpRegistry:
    """POST/GET /mcp/servers — CRUD for external MCP server registrations."""

    def test_register_valid_https_url(self, e2e_ctx: E2EContext) -> None:
        """POST /mcp/servers with a valid https URL returns 201 and no secret."""
        payload = {
            "name": "e2e-test-mcp-server",
            "url": "https://mcp.example.com/api",
            "transport": "http",
            "enabled": True,
        }
        resp = e2e_ctx.client.post(
            "/mcp/servers",
            json=payload,
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()
        # Secret fields must NOT be present in the response.
        secret_fields = {"auth_token", "secret", "token", "auth_secret_ciphertext"}
        leaked = secret_fields & set(data.keys())
        assert not leaked, f"Secret fields leaked in POST /mcp/servers response: {leaked}"
        # Required public fields should be present.
        assert "id" in data, f"No 'id' in create response: {data}"
        assert data.get("url") == payload["url"], f"URL mismatch: {data.get('url')!r}"
        assert data.get("name") == payload["name"]

    def test_list_mcp_servers_includes_registered(self, e2e_ctx: E2EContext) -> None:
        """GET /mcp/servers lists the registered server without secrets."""
        # Register one first.
        create_resp = e2e_ctx.client.post(
            "/mcp/servers",
            json={"name": "e2e-list-check", "url": "https://list.example.com/mcp"},
            headers=e2e_ctx.su_headers(),
        )
        assert create_resp.status_code == 201, create_resp.text
        server_id = create_resp.json()["id"]

        # List.
        list_resp = e2e_ctx.client.get("/mcp/servers", headers=e2e_ctx.su_headers())
        assert list_resp.status_code == 200, (
            f"Expected 200, got {list_resp.status_code}: {list_resp.text}"
        )
        servers = list_resp.json()
        assert isinstance(servers, list), f"Expected list, got {type(servers)}"
        ids = [s.get("id") for s in servers]
        assert server_id in ids, f"Newly created server {server_id} not in list: {ids}"

        # Verify no secrets in listed items.
        secret_fields = {"auth_token", "secret", "token", "auth_secret_ciphertext"}
        for srv in servers:
            leaked = secret_fields & set(srv.keys())
            assert not leaked, f"Secret fields leaked in GET /mcp/servers: {leaked}"

    def test_response_never_contains_secret_fields(self, e2e_ctx: E2EContext) -> None:
        """The create response must not contain any of the known secret field names.

        We create a server WITHOUT auth_token to avoid the crypto stack (which
        requires ENCRYPTION_KEY to be set — not guaranteed in local dev E2E).
        The secret-field exclusion is enforced by _strip_secrets() regardless of
        whether an auth_token was provided.
        """
        resp = e2e_ctx.client.post(
            "/mcp/servers",
            json={
                "name": "e2e-no-secret-fields",
                "url": "https://nosecret.example.com/mcp",
                # No auth_token here — avoids the encrypt_json code path
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        data = resp.json()
        # These are the fields _strip_secrets() must exclude.
        secret_fields = {"auth_token", "secret", "token", "auth_secret_ciphertext",
                         "auth_secret_nonce", "auth_secret_key_version"}
        leaked = secret_fields & set(data.keys())
        assert not leaked, (
            f"Secret fields leaked in POST /mcp/servers response: {leaked}\n"
            f"Full response: {data}"
        )

    def test_ssrf_blocked_localhost_url(self, e2e_ctx: E2EContext) -> None:
        """Registering a localhost URL is SSRF-blocked (400)."""
        resp = e2e_ctx.client.post(
            "/mcp/servers",
            json={"name": "ssrf-test", "url": "http://localhost:8080/mcp"},
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 400, (
            f"Expected SSRF-blocked 400 for localhost URL, got {resp.status_code}: {resp.text}"
        )

    def test_ssrf_blocked_private_ip(self, e2e_ctx: E2EContext) -> None:
        """Registering a private IP address is SSRF-blocked (400)."""
        resp = e2e_ctx.client.post(
            "/mcp/servers",
            json={"name": "ssrf-private", "url": "http://192.168.1.10/mcp"},
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 400, (
            f"Expected SSRF-blocked 400 for private IP URL, got {resp.status_code}: {resp.text}"
        )

    def test_register_unauthenticated_rejected(self, e2e_ctx: E2EContext) -> None:
        """POST /mcp/servers without a token is rejected (401/403)."""
        resp = e2e_ctx.client.post(
            "/mcp/servers",
            json={"name": "anon", "url": "https://example.com/mcp"},
        )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for unauthenticated register, got {resp.status_code}"
        )

    def test_list_mcp_servers_unauthenticated_rejected(self, e2e_ctx: E2EContext) -> None:
        """GET /mcp/servers without a token is rejected (401/403)."""
        resp = e2e_ctx.client.get("/mcp/servers")
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for unauthenticated list, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Nubi-as-MCP-server  (POST /mcp — JSON-RPC)
# ---------------------------------------------------------------------------


class TestNubiMcpServer:
    """POST /mcp — Nubi as an MCP server (JSON-RPC 2.0)."""

    def test_tools_list_returns_catalog(self, e2e_ctx: E2EContext) -> None:
        """tools/list returns a non-empty list of tool descriptors."""
        resp = _rpc(e2e_ctx.client, e2e_ctx.su_headers(), "tools/list")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "result" in body, f"No 'result' in JSON-RPC response: {body}"
        tools = body["result"].get("tools", [])
        assert isinstance(tools, list), f"tools is not a list: {type(tools)}"
        assert len(tools) > 0, "tools/list returned an empty tool catalog"
        # Every tool descriptor must have a name and inputSchema.
        for tool in tools:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "inputSchema" in tool, f"Tool missing 'inputSchema': {tool}"

    def test_tools_list_includes_expected_tools(self, e2e_ctx: E2EContext) -> None:
        """Known Nubi tools (query_metric, run_query, list_metrics) appear in the catalog."""
        resp = _rpc(e2e_ctx.client, e2e_ctx.su_headers(), "tools/list")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        names = {t["name"] for t in body["result"].get("tools", [])}
        for expected in ("query_metric", "run_query", "list_metrics"):
            assert expected in names, (
                f"Expected tool {expected!r} not in catalog. Found: {names}"
            )

    def test_tools_call_query_metric_retail_nsv(self, e2e_ctx: E2EContext) -> None:
        """tools/call query_metric (retail_nsv) returns real rows via the author token."""
        from tests.e2e.conftest import _mint  # noqa: PLC0415

        # Mint an author token with full scopes (author:metric + read:*).
        author_token = _mint(
            e2e_ctx.superuser_id,
            extra={"scope": "read:* author:metric author:sql"},
        )
        headers = e2e_ctx.auth_headers(author_token)
        resp = _rpc(
            e2e_ctx.client,
            headers,
            "tools/call",
            params={
                "name": "query_metric",
                "arguments": {
                    "metric_id": "retail_nsv",
                    "dimensions": ["region"],
                    "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
                    "limit": 5,
                },
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "result" in body, f"No 'result': {body}"
        result = body["result"]
        # isError must be False for a successful call.
        assert not result.get("isError"), (
            f"tools/call returned isError=True: {result.get('content')}"
        )
        # Content must be present and contain rows.
        content = result.get("content", [])
        assert content, "tools/call result.content is empty"

    def test_tools_call_run_query_raw_sql_blocked_for_read_only_token(
        self, e2e_ctx: E2EContext
    ) -> None:
        """read:* token (no author:sql) calling run_query with raw SQL is blocked.

        The MCP handler executes the tool under the caller's actual scope — a
        read-only token must NOT be able to run ad-hoc SQL via this path.
        The tool call should either return isError=True (tool-level rejection)
        or the HTTP call returns a 4xx error.
        """
        resp = _rpc(
            e2e_ctx.client,
            e2e_ctx.read_headers(),
            "tools/call",
            params={
                "name": "run_query",
                "arguments": {"sql": "SELECT 42 AS answer"},
            },
        )
        assert resp.status_code == 200, resp.text  # JSON-RPC errors are HTTP 200
        body = resp.json()
        result = body.get("result", {})
        # The tool must report an error (isError=True with an error message).
        assert result.get("isError"), (
            "Expected raw-SQL run_query to be blocked for read-only token "
            f"but isError was False. result={result}"
        )

    def test_mcp_unauthenticated_is_rejected(self, e2e_ctx: E2EContext) -> None:
        """POST /mcp without Authorization header is rejected (401/403)."""
        resp = e2e_ctx.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for unauthenticated /mcp, got {resp.status_code}"
        )

    def test_initialize_handshake(self, e2e_ctx: E2EContext) -> None:
        """tools/initialize returns server info and protocol version."""
        resp = _rpc(e2e_ctx.client, e2e_ctx.su_headers(), "initialize")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        result = body.get("result", {})
        assert "protocolVersion" in result, f"No protocolVersion in initialize result: {result}"
        assert "serverInfo" in result, f"No serverInfo in initialize result: {result}"
        assert result["serverInfo"].get("name") == "nubi", (
            f"Expected serverInfo.name='nubi', got {result['serverInfo']!r}"
        )

    def test_unknown_method_returns_method_not_found(self, e2e_ctx: E2EContext) -> None:
        """An unknown JSON-RPC method returns the method-not-found error code."""
        resp = _rpc(e2e_ctx.client, e2e_ctx.su_headers(), "no_such_method_xyz")
        assert resp.status_code == 200, resp.text  # JSON-RPC errors are HTTP 200
        body = resp.json()
        assert "error" in body, f"Expected JSON-RPC error, got: {body}"
        assert body["error"]["code"] == -32601, (
            f"Expected method-not-found code -32601, got {body['error']['code']}"
        )
