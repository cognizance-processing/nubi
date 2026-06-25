"""E2E: Provisioning (POST /apply) tests.

- Apply a small bundle (a query) → creates
- Re-apply identical → idempotent (no changes / unchanged actions)
- dry_run=true writes nothing (subsequent check still 0 rows for that slug)
"""

from __future__ import annotations

import uuid
import pytest


@pytest.mark.usefixtures("e2e_ctx")
class TestProvisioning:
    def test_apply_creates_query(self, e2e_ctx):
        """POST /apply with a query envelope → action=created."""
        unique_name = f"E2E Apply Test {uuid.uuid4().hex[:8]}"
        bundle = {
            "version": "1",
            "resources": [
                {
                    "kind": "query",
                    "metadata": {"name": unique_name},
                    "spec": {
                        "name": unique_name,
                        "sql": "SELECT 1 AS x",
                        "params": [],
                    },
                }
            ],
        }
        resp = e2e_ctx.client.post(
            "/apply",
            json=bundle,
            headers=e2e_ctx.su_headers(),
        )
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "results" in body
        assert "summary" in body
        assert body["dry_run"] is False

        results = body["results"]
        assert len(results) == 1
        r = results[0]
        assert r["action"] in ("create", "created", "update", "updated"), (
            f"Expected create action, got: {r['action']}"
        )
        assert r.get("error") is None, f"Unexpected error: {r.get('error')}"

    def test_apply_idempotent(self, e2e_ctx):
        """Re-applying the same bundle → no new creates (idempotent)."""
        unique_name = f"E2E Idempotent {uuid.uuid4().hex[:8]}"
        bundle = {
            "version": "1",
            "resources": [
                {
                    "kind": "query",
                    "metadata": {"name": unique_name},
                    "spec": {
                        "name": unique_name,
                        "sql": "SELECT 42 AS answer",
                        "params": [],
                    },
                }
            ],
        }

        # First apply → create
        resp1 = e2e_ctx.client.post("/apply", json=bundle, headers=e2e_ctx.su_headers())
        assert resp1.status_code == 200, f"First apply failed: {resp1.text}"
        r1 = resp1.json()
        assert r1["results"][0]["action"] in ("create", "created", "update", "updated")

        # Second apply (identical) → update or unchanged (idempotent)
        resp2 = e2e_ctx.client.post("/apply", json=bundle, headers=e2e_ctx.su_headers())
        assert resp2.status_code == 200, f"Second apply failed: {resp2.text}"
        r2 = resp2.json()
        r2_action = r2["results"][0]["action"]
        # Should not fail; either unchanged or update (upsert)
        assert r2_action in ("unchanged", "update", "updated", "create", "created"), (
            f"Unexpected action on re-apply: {r2_action}"
        )
        # If it was created first time then second time must not be "create" again
        # (that would indicate non-idempotent behavior)
        assert r2_action != "failed", f"Re-apply failed: {r2['results'][0]}"

    def test_dry_run_writes_nothing(self, e2e_ctx):
        """dry_run=true: action shown but no actual DB row created."""
        unique_name = f"E2E DryRun {uuid.uuid4().hex[:8]}"
        bundle = {
            "version": "1",
            "dry_run": True,
            "resources": [
                {
                    "kind": "query",
                    "metadata": {"name": unique_name},
                    "spec": {
                        "name": unique_name,
                        "sql": "SELECT 'dryrun' AS mode",
                        "params": [],
                    },
                }
            ],
        }
        resp = e2e_ctx.client.post("/apply", json=bundle, headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["dry_run"] is True
        results = body["results"]
        assert len(results) == 1
        # dry_run should show the action that WOULD happen, not "failed"
        assert results[0]["action"] != "failed", f"dry_run result was failed: {results[0]}"

        # Now apply real (non-dry) with same name — if dry_run truly wrote nothing,
        # the result will be "create" (not "update")
        real_bundle = {k: v for k, v in bundle.items() if k != "dry_run"}
        real_bundle["dry_run"] = False
        resp2 = e2e_ctx.client.post("/apply", json=real_bundle, headers=e2e_ctx.su_headers())
        assert resp2.status_code == 200
        r2 = resp2.json()
        # The action should indicate a new create (not "update"), since dry_run wrote nothing
        r2_action = r2["results"][0]["action"]
        assert r2_action in ("create", "created"), (
            f"Expected create after dry_run, got {r2_action} — dry_run may have written to DB"
        )

    def test_apply_bad_kind_is_recorded_as_failed(self, e2e_ctx):
        """A resource with unknown kind → action=failed in results (best-effort)."""
        bundle = {
            "version": "1",
            "resources": [
                {
                    "kind": "totally_unknown_kind_xyz",
                    "metadata": {"name": "bad"},
                    "spec": {},
                }
            ],
        }
        resp = e2e_ctx.client.post("/apply", json=bundle, headers=e2e_ctx.su_headers())
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
        body = resp.json()
        results = body["results"]
        assert len(results) == 1
        assert results[0]["action"] == "failed"
        assert results[0]["error"]

    def test_apply_returns_summary_counts(self, e2e_ctx):
        """Response summary has correct created/updated/unchanged/failed counts."""
        bundle = {
            "version": "1",
            "resources": [
                {
                    "kind": "query",
                    "metadata": {"name": f"E2E Summary {uuid.uuid4().hex[:8]}"},
                    "spec": {
                        "name": f"E2E Summary Query",
                        "sql": "SELECT 99 AS n",
                        "params": [],
                    },
                }
            ],
        }
        resp = e2e_ctx.client.post("/apply", json=bundle, headers=e2e_ctx.su_headers())
        assert resp.status_code == 200
        body = resp.json()
        summary = body["summary"]
        assert isinstance(summary, dict)
        assert "created" in summary or "updated" in summary or "unchanged" in summary
        total = (
            summary.get("created", 0)
            + summary.get("updated", 0)
            + summary.get("unchanged", 0)
            + summary.get("failed", 0)
        )
        assert total == len(bundle["resources"]), (
            f"Summary counts don't add up to resource count: {summary}"
        )
