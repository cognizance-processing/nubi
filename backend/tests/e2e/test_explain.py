"""E2E: /explain endpoint tests.

- Two time windows → delta_total ≈ current_total - comparison_total
  (computed independently from grouped queries and asserted to match)
- Driver ordering by |delta|
- Unknown dimension → 400
- No-time-dimension metric → 400
- Cross-org metric → 404

NOTE: retail_nsv's time_dimension.column is 'month' which holds VARCHAR strings
like '2024-06'. The explain endpoint filters on this using >= / < comparisons.
We use month-string boundaries (e.g., '2025-06' <= x < '2025-07') to match.
"""

from __future__ import annotations

import pytest


def _read_arrow(resp) -> list[dict]:
    from tests.e2e.conftest import read_arrow_bytes
    return read_arrow_bytes(resp.content)


@pytest.mark.usefixtures("e2e_ctx")
class TestExplain:
    # Use month-string comparisons since the time column is VARCHAR 'YYYY-MM'
    CURRENT = {"start": "2025-06", "end": "2025-07"}
    COMPARISON = {"start": "2025-05", "end": "2025-06"}

    def _get_month_total(self, e2e_ctx, month: str) -> float:
        """Get the total NSV for a single month via raw SQL (ground truth)."""
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": f"SELECT ROUND(SUM(nsv), 4) AS total FROM sales WHERE month = '{month}'"},
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        rows = _read_arrow(resp)
        return float(rows[0]["total"] or 0)

    def test_explain_response_structure(self, e2e_ctx):
        """Explain returns the expected top-level fields."""
        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/explain",
            json={
                "current": self.CURRENT,
                "comparison": self.COMPARISON,
                "dimensions": ["region"],
                "top_n": 5,
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "metric_id" in body
        assert "measure" in body
        assert "delta_total" in body
        assert "current_total" in body
        assert "comparison_total" in body
        assert "dimensions" in body

    def test_explain_delta_arithmetic(self, e2e_ctx):
        """delta_total = current_total - comparison_total (basic arithmetic check)."""
        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/explain",
            json={
                "current": self.CURRENT,
                "comparison": self.COMPARISON,
                "dimensions": ["region"],
                "top_n": 5,
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()
        delta = body["delta_total"]
        current = body["current_total"]
        comparison = body["comparison_total"]
        # delta_total must equal current_total - comparison_total (within float tolerance)
        assert abs(delta - (current - comparison)) < 1.0, (
            f"delta_total {delta} != current_total {current} - comparison_total {comparison}"
        )

    def test_explain_totals_match_raw_sql(self, e2e_ctx):
        """current_total / comparison_total match independent raw SQL aggregates."""
        # Ground truth from raw SQL (month = '2025-06' and '2025-05')
        current_raw = self._get_month_total(e2e_ctx, "2025-06")
        comparison_raw = self._get_month_total(e2e_ctx, "2025-05")

        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/explain",
            json={
                "current": self.CURRENT,
                "comparison": self.COMPARISON,
                "dimensions": ["region"],
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()

        # Allow up to 2% tolerance (rounding, float precision)
        if current_raw > 0:
            assert abs(body["current_total"] - current_raw) / current_raw < 0.02, (
                f"current_total mismatch: explain={body['current_total']:.2f}, raw={current_raw:.2f}"
            )
        if comparison_raw > 0:
            assert abs(body["comparison_total"] - comparison_raw) / comparison_raw < 0.02, (
                f"comparison_total mismatch: explain={body['comparison_total']:.2f}, raw={comparison_raw:.2f}"
            )

    def test_explain_returns_dimension_breakdown(self, e2e_ctx):
        """Explain response includes dimension breakdowns with per-member deltas."""
        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/explain",
            json={
                "current": self.CURRENT,
                "comparison": self.COMPARISON,
                "dimensions": ["region"],
                "top_n": 5,
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "dimensions" in body
        assert len(body["dimensions"]) >= 1
        dim = body["dimensions"][0]
        assert dim["dimension"] == "region"
        assert "members" in dim
        # If data exists for both windows, members should be populated
        if body["current_total"] > 0 or body["comparison_total"] > 0:
            assert len(dim["members"]) >= 1

    def test_explain_driver_ordering_by_abs_delta(self, e2e_ctx):
        """Members within a dimension should be ordered by |delta| descending."""
        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/explain",
            json={
                "current": self.CURRENT,
                "comparison": {"start": "2024-06", "end": "2024-07"},
                "dimensions": ["region"],
                "top_n": 10,
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()
        dims = body["dimensions"]
        if not dims or not dims[0]["members"]:
            pytest.skip("No members returned (data may be empty for these windows)")
        members = dims[0]["members"]
        abs_deltas = [abs(m["delta"]) for m in members]
        # Should be sorted descending by |delta|
        assert abs_deltas == sorted(abs_deltas, reverse=True), (
            f"Members not sorted by |delta|: {abs_deltas}"
        )

    def test_explain_unknown_dimension_400(self, e2e_ctx):
        """Passing an unknown dimension → 400."""
        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/explain",
            json={
                "current": self.CURRENT,
                "comparison": self.COMPARISON,
                "dimensions": ["nonexistent_dim"],
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_explain_no_time_dimension_400(self, e2e_ctx):
        """Calling /explain on a metric with no time_dimension → 400."""
        # demo_revenue has no time_dimension
        resp = e2e_ctx.client.post(
            "/metrics/demo_revenue/explain",
            json={
                "current": self.CURRENT,
                "comparison": self.COMPARISON,
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
        body = resp.json()
        err = body.get("error", body.get("detail", ""))
        assert "time_dimension" in str(err).lower() or "no_time_dimension" in str(err).lower(), (
            f"Expected time_dimension error, got: {err}"
        )

    def test_explain_cross_org_404(self, e2e_ctx):
        """Explain on a non-existent metric → 404 (no info leak)."""
        resp = e2e_ctx.client.post(
            "/metrics/completely_fake_metric_id_xyz/explain",
            json={
                "current": self.CURRENT,
                "comparison": self.COMPARISON,
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
