"""E2E: Metric query endpoint tests.

- retail_nsv with dimensions/filters/time_grain → correct row shape
- Ordering by dimension value
- top_n → only top N rows
- Filter by region
- read-only token can still query governed metrics (no author:sql needed)
"""

from __future__ import annotations

import pytest


@pytest.mark.usefixtures("e2e_ctx")
class TestMetricQuery:
    def _query_metric(self, e2e_ctx, metric_id: str, body: dict, token: str | None = None) -> list[dict]:
        headers = e2e_ctx.auth_headers(token or e2e_ctx.su_token)
        headers["Accept"] = "application/vnd.apache.arrow.stream"
        resp = e2e_ctx.client.post(
            f"/metrics/{metric_id}/query",
            json=body,
            headers=headers,
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        from tests.e2e.conftest import read_arrow_bytes
        return read_arrow_bytes(resp.content)

    def test_nsv_by_region(self, e2e_ctx):
        """retail_nsv grouped by region returns 5 regions with positive nsv."""
        metric_id = "retail_nsv"
        rows = self._query_metric(e2e_ctx, metric_id, {
            "dimensions": ["region"],
            "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
        })
        assert len(rows) == 5, f"Expected 5 regions, got {len(rows)}"
        assert all("region" in r for r in rows)
        assert all("nsv" in r for r in rows)
        assert all(r["nsv"] > 0 for r in rows)

    def test_nsv_filter_region(self, e2e_ctx):
        """Filter by a specific region narrows results to that region only."""
        rows = self._query_metric(e2e_ctx, "retail_nsv", {
            "dimensions": ["region"],
            "filters": [
                {"field": "region", "op": "=", "value": "Gauteng"},
                {"field": "month", "op": "=", "value": "2025-01"},
            ],
        })
        assert len(rows) == 1
        assert rows[0]["region"] == "Gauteng"
        # Known value from DuckDB: ~430k
        assert rows[0]["nsv"] > 100_000

    def test_nsv_ordering(self, e2e_ctx):
        """order_by nsv desc → first row is highest NSV region."""
        rows = self._query_metric(e2e_ctx, "retail_nsv", {
            "dimensions": ["region"],
            "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            "order_by": [["nsv", "desc"]],
        })
        assert len(rows) >= 2
        # Rows should be in descending nsv order
        values = [r["nsv"] for r in rows]
        assert values == sorted(values, reverse=True), f"Not sorted: {values}"

    def test_nsv_top_n(self, e2e_ctx):
        """top_n=2 returns exactly 2 rows (the top 2 regions by nsv)."""
        rows = self._query_metric(e2e_ctx, "retail_nsv", {
            "dimensions": ["region"],
            "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            "top_n": {"dimension": "region", "n": 2, "order": "desc"},
        })
        assert len(rows) == 2, f"Expected 2 rows with top_n=2, got {len(rows)}"

    def test_nsv_with_product_group(self, e2e_ctx):
        """Grouping by product_group returns rows with that dimension column."""
        rows = self._query_metric(e2e_ctx, "retail_nsv", {
            "dimensions": ["product_group"],
            "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
        })
        assert len(rows) >= 1
        assert all("product_group" in r for r in rows)
        assert all("nsv" in r for r in rows)

    def test_nsv_total_no_dims(self, e2e_ctx):
        """No dimensions → single-row total aggregate."""
        rows = self._query_metric(e2e_ctx, "retail_nsv", {
            "dimensions": [],
            "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
        })
        assert len(rows) == 1
        # Total for 2025-01 from local parquet is > 0 (exact value may vary from DuckDB file)
        assert rows[0]["nsv"] > 100_000

    def test_read_only_token_can_query_metric(self, e2e_ctx):
        """read:* token (no author:sql) can still query a governed metric."""
        rows = self._query_metric(e2e_ctx, "retail_nsv", {
            "dimensions": ["region"],
            "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
        }, token=e2e_ctx.read_token)
        assert len(rows) == 5

    def test_unknown_dimension_returns_400(self, e2e_ctx):
        """Querying an undeclared dimension → 400 governance error."""
        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/query",
            json={"dimensions": ["not_a_real_dimension"]},
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
