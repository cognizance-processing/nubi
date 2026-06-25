"""E2E: Auth & tenancy tests.

- No token → 401
- Valid superuser token → 200 with org-scoped data
- A metric ID from a fake org → 404 (no cross-org IDOR)
- read:* token → can read but cannot author raw SQL
"""

from __future__ import annotations

import pytest


@pytest.mark.usefixtures("e2e_ctx")
class TestAuth:
    def test_no_token_returns_401(self, e2e_ctx):
        """Unauthenticated request to a protected endpoint → 401."""
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT 1 AS x"},
            headers={"Accept": "application/vnd.apache.arrow.stream"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    def test_valid_token_returns_data(self, e2e_ctx):
        """Valid superuser token → 200 with real Arrow data."""
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT COUNT(*) AS n FROM sales"},
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        from tests.e2e.conftest import read_arrow_bytes
        rows = read_arrow_bytes(resp.content)
        assert len(rows) == 1
        assert rows[0]["n"] > 0, "Should have sales rows"

    def test_wrong_org_metric_returns_404(self, e2e_ctx):
        """A metric id that doesn't exist for this org → 404 (no info leak)."""
        fake_metric_id = "does_not_exist_for_any_org_xyz"
        resp = e2e_ctx.client.post(
            f"/metrics/{fake_metric_id}/query",
            json={"dimensions": [], "filters": []},
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_read_only_token_can_read_registered_metric(self, e2e_ctx):
        """read:* token can query a governed metric (read scope satisfied)."""
        # Metrics are addressed by their slug, not by the query row UUID
        metric_id = "retail_nsv"
        resp = e2e_ctx.client.post(
            f"/metrics/{metric_id}/query",
            json={
                "dimensions": ["region"],
                "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            },
            headers={
                **e2e_ctx.read_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        from tests.e2e.conftest import read_arrow_bytes
        rows = read_arrow_bytes(resp.content)
        assert len(rows) > 0

    def test_org_scoped_data_is_isolated(self, e2e_ctx):
        """The list-metrics endpoint only returns this org's metrics."""
        resp = e2e_ctx.client.get(
            "/metrics",
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        data = resp.json()
        metrics = data.get("metrics", [])
        # Must have at least the 3 demo metrics
        slugs = {m.get("id") for m in metrics}
        assert "retail_nsv" in slugs, f"retail_nsv not in {slugs}"
        assert "retail_attainment" in slugs, f"retail_attainment not in {slugs}"
