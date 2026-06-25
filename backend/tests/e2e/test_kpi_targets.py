"""E2E: KPI targets (RAG status) tests.

Creates THREE metrics with targets calibrated against the real data
so specific queries yield deterministic green / amber / red.

RAG logic (higher_is_better, default amber_threshold=0.8):
  green:  actual >= target
  amber:  actual >= target * 0.8   AND actual < target
  red:    actual <  target * 0.8

We query the TOTAL NSV for a specific month from the parquet data and set targets:
  green_metric:  target = actual * 0.8   → actual ≥ target → GREEN
  amber_metric:  target = actual * 1.15  → actual/target ≈ 0.87 → AMBER
  red_metric:    target = actual * 2.0   → actual/target = 0.5 < 0.8 → RED
"""

from __future__ import annotations

import uuid
import pytest


def _get_nsv_total(e2e_ctx, month: str) -> float:
    """Get the actual NSV for a month via raw SQL (ground truth)."""
    resp = e2e_ctx.client.post(
        "/query",
        json={"sql": f"SELECT ROUND(SUM(nsv), 4) AS total FROM sales WHERE month = '{month}'"},
        headers={
            **e2e_ctx.su_headers(),
            "Accept": "application/vnd.apache.arrow.stream",
        },
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
    from tests.e2e.conftest import read_arrow_bytes
    rows = read_arrow_bytes(resp.content)
    return float(rows[0]["total"])


def _create_nsv_metric_with_target(
    e2e_ctx,
    metric_name: str,
    target_value: float,
) -> str:
    """Create a metric with a target; return its canonical id (slug)."""
    body = {
        "name": metric_name,
        "measure": {
            "name": "nsv",
            "agg": "sum",
            "expr": "nsv",
            "type": "additive",
        },
        "base_sql": (
            "SELECT month, region, product_group, channel, "
            "ROUND(SUM(nsv), 2) AS nsv, SUM(units) AS units "
            "FROM sales GROUP BY month, region, product_group, channel"
        ),
        "datastore_id": e2e_ctx.datastore_id,
        "dimensions": [{"name": "month", "type": "text"}],
        "time_dimension": None,  # No time_dimension to avoid date_trunc on VARCHAR month
        "default_filters": [],
        "target": {
            "value": str(round(target_value, 4)),
            "direction": "higher_is_better",
            "amber_threshold": 0.8,
        },
    }
    resp = e2e_ctx.client.post(
        "/metrics",
        json=body,
        headers=e2e_ctx.su_headers(),
    )
    assert resp.status_code in (200, 201), (
        f"Create metric failed {resp.status_code}: {resp.text}"
    )
    created = resp.json()
    return created.get("id", "")


def _query_metric_rag(e2e_ctx, metric_id: str, month: str | None = None) -> list[dict]:
    """Query a metric for a specific month; return Arrow rows (includes RAG columns)."""
    filters = []
    if month:
        filters = [{"field": "month", "op": "=", "value": month}]
    resp = e2e_ctx.client.post(
        f"/metrics/{metric_id}/query",
        json={"dimensions": [], "filters": filters},
        headers={
            **e2e_ctx.su_headers(),
            "Accept": "application/vnd.apache.arrow.stream",
        },
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
    from tests.e2e.conftest import read_arrow_bytes
    return read_arrow_bytes(resp.content)


@pytest.mark.usefixtures("e2e_ctx")
class TestKPITargets:
    TEST_MONTH = "2025-06"

    def _get_total(self, e2e_ctx) -> float:
        return _get_nsv_total(e2e_ctx, self.TEST_MONTH)

    def test_green_target(self, e2e_ctx):
        """Metric with target < actual → RAG = green."""
        total = self._get_total(e2e_ctx)
        assert total > 0, f"No data for {self.TEST_MONTH}"
        # Target is 80% of actual → actual ≥ target → GREEN
        target_val = total * 0.8

        slug = _create_nsv_metric_with_target(
            e2e_ctx,
            f"E2E RAG Green {uuid.uuid4().hex[:6]}",
            target_val,
        )
        assert slug, "No metric id returned"
        rows = _query_metric_rag(e2e_ctx, slug, self.TEST_MONTH)

        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        row = rows[0]
        assert "nsv_rag" in row, f"nsv_rag column missing; cols={list(row.keys())}"
        actual = float(row.get("nsv") or 0)
        assert row["nsv_rag"] == "green", (
            f"Expected green (actual={actual:.2f} ≥ target={target_val:.2f}), "
            f"got '{row['nsv_rag']}'"
        )
        assert "nsv_target" in row
        assert "nsv_vs_target" in row
        assert "nsv_pct_to_goal" in row
        assert float(row["nsv_pct_to_goal"]) >= 1.0, (
            f"pct_to_goal should be ≥ 1.0 for green, got {row['nsv_pct_to_goal']}"
        )

    def test_amber_target(self, e2e_ctx):
        """Metric with target in amber band (actual/target in [0.8, 1.0)) → RAG = amber."""
        total = self._get_total(e2e_ctx)
        assert total > 0, f"No data for {self.TEST_MONTH}"
        # Target is 115% of actual → actual/target ≈ 0.87 → AMBER
        target_val = total * 1.15

        slug = _create_nsv_metric_with_target(
            e2e_ctx,
            f"E2E RAG Amber {uuid.uuid4().hex[:6]}",
            target_val,
        )
        assert slug
        rows = _query_metric_rag(e2e_ctx, slug, self.TEST_MONTH)

        assert len(rows) == 1
        row = rows[0]
        assert "nsv_rag" in row, f"nsv_rag column missing; cols={list(row.keys())}"
        assert row["nsv_rag"] == "amber", (
            f"Expected amber (target={target_val:.2f} is 115% of actual), "
            f"got '{row['nsv_rag']}'"
        )
        pct = float(row.get("nsv_pct_to_goal", 0))
        assert 0.8 <= pct < 1.0, (
            f"pct_to_goal should be in [0.8, 1.0) for amber, got {pct}"
        )

    def test_red_target(self, e2e_ctx):
        """Metric with target 200% of actual → RAG = red."""
        total = self._get_total(e2e_ctx)
        assert total > 0, f"No data for {self.TEST_MONTH}"
        # Target is 200% of actual → actual/target = 0.5 < 0.8 → RED
        target_val = total * 2.0

        slug = _create_nsv_metric_with_target(
            e2e_ctx,
            f"E2E RAG Red {uuid.uuid4().hex[:6]}",
            target_val,
        )
        assert slug
        rows = _query_metric_rag(e2e_ctx, slug, self.TEST_MONTH)

        assert len(rows) == 1
        row = rows[0]
        assert "nsv_rag" in row, f"nsv_rag column missing; cols={list(row.keys())}"
        assert row["nsv_rag"] == "red", (
            f"Expected red (target={target_val:.2f} is 200% of actual), "
            f"got '{row['nsv_rag']}'"
        )
        pct = float(row.get("nsv_pct_to_goal", 1))
        assert pct < 0.8, (
            f"pct_to_goal should be < 0.8 for red, got {pct}"
        )

    def test_all_four_rag_columns_present(self, e2e_ctx):
        """All four RAG columns: _target, _vs_target, _pct_to_goal, _rag."""
        total = self._get_total(e2e_ctx)
        assert total > 0
        slug = _create_nsv_metric_with_target(
            e2e_ctx,
            f"E2E RAG Cols {uuid.uuid4().hex[:6]}",
            total * 0.9,
        )
        assert slug
        rows = _query_metric_rag(e2e_ctx, slug, self.TEST_MONTH)
        assert len(rows) >= 1
        row = rows[0]
        for col in ("nsv_target", "nsv_vs_target", "nsv_pct_to_goal", "nsv_rag"):
            assert col in row, f"Column '{col}' missing; got: {list(row.keys())}"

    def test_rag_vs_target_sign(self, e2e_ctx):
        """vs_target is positive (actual > target) for green, negative for red."""
        total = self._get_total(e2e_ctx)
        assert total > 0

        # Green: target = 80% of actual → vs_target > 0
        slug_green = _create_nsv_metric_with_target(
            e2e_ctx, f"E2E RAG Sign Green {uuid.uuid4().hex[:6]}", total * 0.8
        )
        rows_green = _query_metric_rag(e2e_ctx, slug_green, self.TEST_MONTH)
        assert float(rows_green[0]["nsv_vs_target"]) > 0, "vs_target should be positive for green"

        # Red: target = 200% of actual → vs_target < 0
        slug_red = _create_nsv_metric_with_target(
            e2e_ctx, f"E2E RAG Sign Red {uuid.uuid4().hex[:6]}", total * 2.0
        )
        rows_red = _query_metric_rag(e2e_ctx, slug_red, self.TEST_MONTH)
        assert float(rows_red[0]["nsv_vs_target"]) < 0, "vs_target should be negative for red"
