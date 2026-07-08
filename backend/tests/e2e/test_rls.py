"""E2E: RLS (Row-Level Security) tests.

RLS is expressed as policies in JWT claims: {"policies": {"region": "Gauteng"}}.
The planner injects a WHERE region = 'Gauteng' at AST level.

We verify:
- An unrestricted token → all 5 regions in result
- A token with policies={"region": "Gauteng"} → only Gauteng rows
"""

from __future__ import annotations

import pytest


def _read_arrow(resp) -> list[dict]:
    from tests.e2e.conftest import read_arrow_bytes
    return read_arrow_bytes(resp.content)


@pytest.mark.usefixtures("e2e_ctx")
class TestRLS:
    def _mint_rls_token(self, e2e_ctx, policies: dict) -> str:
        """Mint a token with the given RLS policies."""
        from tests.e2e.conftest import _mint
        return _mint(
            e2e_ctx.superuser_id,
            extra={"policies": policies},
        )

    def test_unrestricted_token_all_regions(self, e2e_ctx):
        """Unrestricted token → all 5 regions in retail_nsv."""
        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/query",
            json={
                "dimensions": ["region"],
                "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            },
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        rows = _read_arrow(resp)
        regions = {r["region"] for r in rows}
        assert len(regions) == 5, f"Expected 5 regions, got {len(regions)}: {regions}"

    def test_rls_region_filter_restricts_rows(self, e2e_ctx):
        """Token with policies={region: Gauteng} → only Gauteng in result."""
        rls_token = self._mint_rls_token(e2e_ctx, {"region": "Gauteng"})
        resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/query",
            json={
                "dimensions": ["region"],
                "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            },
            headers={
                "Authorization": f"Bearer {rls_token}",
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        rows = _read_arrow(resp)
        # RLS restricts to Gauteng only
        assert len(rows) == 1, f"Expected 1 row (Gauteng only), got {len(rows)}: {rows}"
        assert rows[0]["region"] == "Gauteng"

    def test_rls_total_matches_restricted_subset(self, e2e_ctx):
        """The RLS-filtered total NSV matches the Gauteng-only aggregate."""
        # Ground truth: Gauteng NSV for 2025-01 via unrestricted raw SQL
        raw_resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT ROUND(SUM(nsv),2) AS nsv FROM sales WHERE month='2025-01' AND region='Gauteng'"},
            headers={
                **e2e_ctx.su_headers(),
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert raw_resp.status_code == 200
        ground_truth = float(_read_arrow(raw_resp)[0]["nsv"])

        # RLS-restricted metric query
        rls_token = self._mint_rls_token(e2e_ctx, {"region": "Gauteng"})
        metric_resp = e2e_ctx.client.post(
            "/metrics/retail_nsv/query",
            json={
                "dimensions": [],
                "filters": [{"field": "month", "op": "=", "value": "2025-01"}],
            },
            headers={
                "Authorization": f"Bearer {rls_token}",
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert metric_resp.status_code == 200
        rls_total = float(_read_arrow(metric_resp)[0]["nsv"])

        assert abs(rls_total - ground_truth) < 2.0, (
            f"RLS total {rls_total} doesn't match Gauteng ground truth {ground_truth}"
        )

    def test_rls_raw_query_is_also_restricted(self, e2e_ctx):
        """RLS token on a raw SQL query → WHERE clause injected by planner."""
        rls_token = self._mint_rls_token(e2e_ctx, {"region": "Gauteng"})
        resp = e2e_ctx.client.post(
            "/query",
            json={"sql": "SELECT region, ROUND(SUM(nsv),2) AS nsv FROM sales WHERE month='2025-01' GROUP BY region"},
            headers={
                "Authorization": f"Bearer {rls_token}",
                "Accept": "application/vnd.apache.arrow.stream",
            },
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        rows = _read_arrow(resp)
        # The planner should have injected region = 'Gauteng'
        assert all(r["region"] == "Gauteng" for r in rows), (
            f"Non-Gauteng rows present with RLS token: {[r['region'] for r in rows]}"
        )
