"""E2E: Watches tests.

- Create a watch on retail_nsv with a threshold
- GET /watches → returns the created watch
- POST /watches/{id}/evaluate → returns a real evaluation result (scalar/threshold)
- Watch with a threshold that is definitely breached → breached=True
- Watch with a very high threshold → breached=False (ok state)
- No AI explain text asserted beyond NullProvider deterministic path
"""

from __future__ import annotations

import uuid
import pytest


@pytest.mark.usefixtures("e2e_ctx")
class TestWatches:
    def _create_watch(self, e2e_ctx, name: str, threshold_op: str, threshold_value: float) -> dict:
        """Create a watch on retail_nsv with the given threshold.

        NOTE: We do NOT set time_grain because retail_nsv's time_dimension column
        is VARCHAR ('2024-06' strings), and date_trunc fails on VARCHAR.
        Without time_grain the watch evaluates the total aggregate.
        """
        body = {
            "name": name,
            "metric_id": "retail_nsv",
            "config": {
                "dimensions": [],
                # No time_grain — the month column is VARCHAR, date_trunc would fail
                "threshold": {
                    "op": threshold_op,
                    "value": threshold_value,
                },
                "enabled": True,
            },
        }
        resp = e2e_ctx.client.post(
            "/watches",
            json=body,
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 201, f"Create watch failed {resp.status_code}: {resp.text}"
        return resp.json()

    def test_create_watch_201(self, e2e_ctx):
        """POST /watches → 201 with watch record."""
        name = f"E2E Watch {uuid.uuid4().hex[:6]}"
        watch = self._create_watch(e2e_ctx, name, ">", 0.0)
        assert watch.get("id"), "Watch id missing"
        assert watch.get("name") == name
        assert watch.get("metric_id") == "retail_nsv"
        assert "config" in watch

    def test_list_watches_returns_created(self, e2e_ctx):
        """GET /watches returns the watch we just created."""
        name = f"E2E List Watch {uuid.uuid4().hex[:6]}"
        created = self._create_watch(e2e_ctx, name, ">", 0.0)
        watch_id = created["id"]

        list_resp = e2e_ctx.client.get("/watches", headers=e2e_ctx.su_headers())
        assert list_resp.status_code == 200, f"Got {list_resp.status_code}: {list_resp.text}"
        watches = list_resp.json().get("watches", [])
        ids = [w["id"] for w in watches]
        assert watch_id in ids, f"Created watch {watch_id} not in list: {ids}"

    def test_get_watch_by_id(self, e2e_ctx):
        """GET /watches/{id} returns the watch details."""
        name = f"E2E Get Watch {uuid.uuid4().hex[:6]}"
        created = self._create_watch(e2e_ctx, name, ">", 0.0)
        watch_id = created["id"]

        get_resp = e2e_ctx.client.get(f"/watches/{watch_id}", headers=e2e_ctx.su_headers())
        assert get_resp.status_code == 200, f"Got {get_resp.status_code}: {get_resp.text}"
        body = get_resp.json()
        assert body["id"] == watch_id
        assert body["metric_id"] == "retail_nsv"

    def test_evaluate_watch_breached(self, e2e_ctx):
        """Evaluate a watch with threshold NSV > 0 → breached=True (always positive)."""
        name = f"E2E Eval Breach {uuid.uuid4().hex[:6]}"
        # threshold: nsv > 0 — always breached since actual NSV is millions
        watch = self._create_watch(e2e_ctx, name, ">", 0.0)
        watch_id = watch["id"]

        eval_resp = e2e_ctx.client.post(
            f"/watches/{watch_id}/evaluate",
            headers=e2e_ctx.su_headers(),
        )
        assert eval_resp.status_code == 200, f"Got {eval_resp.status_code}: {eval_resp.text}"
        result = eval_resp.json()
        assert "breached" in result or "state" in result, f"Missing breach info: {result}"
        # Total NSV > 0, so the watch must be breached
        assert result.get("breached") is True or result.get("state") == "breached", (
            f"Expected breached=True for NSV > 0, got: {result}"
        )
        # Value should be populated
        assert result.get("value") is not None
        assert float(result["value"]) > 0

    def test_evaluate_watch_not_breached(self, e2e_ctx):
        """Watch with very high threshold (NSV > 999_999_999) → breached=False."""
        name = f"E2E Eval OK {uuid.uuid4().hex[:6]}"
        # threshold: nsv > 999_999_999 — impossible, so NOT breached
        watch = self._create_watch(e2e_ctx, name, ">", 999_999_999.0)
        watch_id = watch["id"]

        eval_resp = e2e_ctx.client.post(
            f"/watches/{watch_id}/evaluate",
            headers=e2e_ctx.su_headers(),
        )
        assert eval_resp.status_code == 200, f"Got {eval_resp.status_code}: {eval_resp.text}"
        result = eval_resp.json()
        # NSV is ~40M, not 999M, so NOT breached
        assert result.get("breached") is False or result.get("state") == "ok", (
            f"Expected not-breached for NSV > 999M threshold, got: {result}"
        )

    def test_evaluate_watch_returns_scalar_value(self, e2e_ctx):
        """Evaluation result contains a numeric scalar value."""
        name = f"E2E Scalar {uuid.uuid4().hex[:6]}"
        watch = self._create_watch(e2e_ctx, name, ">", 0.0)
        watch_id = watch["id"]

        eval_resp = e2e_ctx.client.post(
            f"/watches/{watch_id}/evaluate",
            headers=e2e_ctx.su_headers(),
        )
        assert eval_resp.status_code == 200
        result = eval_resp.json()
        value = result.get("value")
        assert value is not None, f"No value in evaluation result: {result}"
        # Total NSV across all time from local parquet is ~22.3M
        assert float(value) > 1_000_000, f"Unexpected scalar value: {value}"

    def test_delete_watch(self, e2e_ctx):
        """DELETE /watches/{id} → watch no longer in list."""
        name = f"E2E Delete Watch {uuid.uuid4().hex[:6]}"
        watch = self._create_watch(e2e_ctx, name, ">", 0.0)
        watch_id = watch["id"]

        del_resp = e2e_ctx.client.delete(f"/watches/{watch_id}", headers=e2e_ctx.su_headers())
        assert del_resp.status_code == 200, f"Delete failed {del_resp.status_code}: {del_resp.text}"
        assert del_resp.json().get("deleted") is True

        # Should no longer be in list
        list_resp = e2e_ctx.client.get("/watches", headers=e2e_ctx.su_headers())
        watches = list_resp.json().get("watches", [])
        assert watch_id not in [w["id"] for w in watches], f"Watch still in list after delete"

    def test_watch_with_missing_threshold_400(self, e2e_ctx):
        """Creating a watch without threshold or comparison → 400."""
        resp = e2e_ctx.client.post(
            "/watches",
            json={
                "name": "E2E No Threshold Watch",
                "metric_id": "retail_nsv",
                "config": {
                    "dimensions": [],
                    "enabled": True,
                    # No threshold, no comparison → validation error
                },
            },
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
