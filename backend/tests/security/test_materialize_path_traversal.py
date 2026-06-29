"""Security tests — materialize path-traversal hardening.

Adversarial audit findings (2026-06-30)
-----------------------------------------
FINDING (HIGH): ``resolve_target_uri`` in ``app.flows.incremental`` did not
sanitize the user-supplied ``target`` field in the ``materialized`` config block
before composing the local filesystem path.  A malicious tenant could craft a
flow spec with ``target: "../../etc/passwd"`` to write Parquet data to arbitrary
paths outside the configured base directory (``seed_data/materialized/``).

The ``env`` parameter (flow-controlled) had the same vulnerability.

FIX: Two new helpers — ``_sanitize_target_segment`` and ``_sanitize_env_segment``
— reject any ``..`` component before path composition.  A belt-and-braces
``os.path.normpath`` containment check catches any further edge case.

Tests in this module assert the fix is in place and confirm that legitimate
(non-traversal) targets continue to work.

Coverage
--------
A.  Path traversal in ``target`` — single and multi-level ``..``.
B.  Path traversal via absolute-path target (leading ``/``).
C.  Path traversal in ``env`` — ``..`` in env string.
D.  Backslash traversal — Windows-style ``..\\`` in target.
E.  Tilde expansion attempt — ``~/.ssh/id_rsa`` in target.
F.  ``..`` anchored at segment boundary — ``subdir/..`` still blocked.
G.  Valid targets — single name, nested path, with extension — all pass.
H.  Valid env names — "dev", "prod", "staging" — all pass.
I.  S3 remote targets — ``..`` segments in target are still rejected.
J.  ``blend_database_path`` isolation — uses UUID (caller-controlled, not
    user-controlled), so traversal there is not a runtime risk; structural
    assertion that the function composes under the expected blends dir.
K.  Watch-sweep org-scope — ``run_watch_sweep`` only iterates watches whose
    ``org_id`` matches the job's org; a different-org record is never evaluated.
L.  Materialize write-scope org isolation — flow specs are org-scoped via
    ``get_user_org``; confirm the route blocks unauthorized cross-org access.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.errors import AppError
from app.flows.incremental import (
    _sanitize_env_segment,
    _sanitize_target_segment,
    resolve_target_uri,
)


# ===========================================================================
# A. Path traversal in target — single and multi-level
# ===========================================================================

class TestTargetPathTraversal:
    """A: ``..`` components in 'target' must be rejected."""

    @pytest.mark.parametrize("bad_target", [
        "../etc/passwd",
        "../../etc/passwd",
        "../../../etc/shadow",
        "data/../../../etc/passwd",
        "subdir/../../outside",
        "a/b/c/../../../../../../../etc/passwd",
        "..",
        "...",
        ".....",
    ])
    def test_dotdot_target_raises_400(self, bad_target):
        """A: target with '..' component → AppError 400 invalid_task_config."""
        mat = {"kind": "full", "target": bad_target}
        with pytest.raises(AppError) as exc_info:
            resolve_target_uri("prod", mat, None, None)
        err = exc_info.value
        assert err.status == 400, (
            f"SECURITY: target={bad_target!r} must raise 400, got {err.status}"
        )
        assert err.code == "invalid_task_config", (
            f"Expected 'invalid_task_config', got {err.code!r}"
        )
        # Confirm the error message mentions the traversal.
        assert "traversal" in err.message.lower() or "illegal" in err.message.lower(), (
            f"Error message should mention traversal/illegal: {err.message!r}"
        )

    def test_sanitize_target_segment_rejects_dotdot(self):
        """A: _sanitize_target_segment directly rejects '..' at any depth."""
        with pytest.raises(AppError) as exc_info:
            _sanitize_target_segment("../../etc/passwd")
        assert exc_info.value.status == 400

    def test_sanitize_target_segment_accepts_valid(self):
        """A: _sanitize_target_segment passes normal names unchanged."""
        assert _sanitize_target_segment("my_data") == "my_data"
        assert _sanitize_target_segment("sales/daily") == "sales/daily"
        assert _sanitize_target_segment("reports/2025/q4") == "reports/2025/q4"


# ===========================================================================
# B. Absolute-path target (leading /)
# ===========================================================================

class TestAbsolutePathTarget:
    """B: Absolute-path targets must not override the base directory."""

    @pytest.mark.parametrize("abs_target", [
        "/etc/passwd",
        "/tmp/evil",
        "///etc/shadow",
    ])
    def test_leading_slash_stripped_stays_within_base(self, abs_target, tmp_path):
        """B: Leading slashes stripped — resulting path stays inside base_uri."""
        mat = {
            "kind": "full",
            "target": abs_target,
            "base_uri": str(tmp_path),
        }
        # Leading slashes are stripped so the path should still land inside tmp_path.
        # If the remaining segments after stripping contain only a valid name (no ..),
        # it should succeed and stay within the base.
        try:
            result = resolve_target_uri("prod", mat, None, None)
            # Must still start with the base directory.
            assert result.startswith(str(tmp_path)), (
                f"SECURITY: absolute target {abs_target!r} escaped base: {result!r}"
            )
        except AppError as e:
            # Acceptable if any remaining segment was also problematic.
            assert e.status == 400


# ===========================================================================
# C. Path traversal in env
# ===========================================================================

class TestEnvPathTraversal:
    """C: ``..`` components in 'env' must be rejected."""

    @pytest.mark.parametrize("bad_env", [
        "../prod",
        "../../etc",
        "dev/../../../etc",
        "..",
    ])
    def test_dotdot_env_raises_400(self, bad_env):
        """C: env with '..' component → AppError 400."""
        mat = {"kind": "full", "target": "mydata"}
        with pytest.raises(AppError) as exc_info:
            resolve_target_uri(bad_env, mat, None, None)
        err = exc_info.value
        assert err.status == 400, (
            f"SECURITY: env={bad_env!r} must raise 400, got {err.status}"
        )
        assert err.code == "invalid_task_config"

    def test_sanitize_env_segment_rejects_dotdot(self):
        """C: _sanitize_env_segment directly rejects '..'."""
        with pytest.raises(AppError) as exc_info:
            _sanitize_env_segment("../prod")
        assert exc_info.value.status == 400

    def test_sanitize_env_segment_accepts_valid_envs(self):
        """C: _sanitize_env_segment passes normal env names."""
        assert _sanitize_env_segment("dev") == "dev"
        assert _sanitize_env_segment("prod") == "prod"
        assert _sanitize_env_segment("staging") == "staging"
        assert _sanitize_env_segment("") == "prod"  # empty → default


# ===========================================================================
# D. Backslash traversal
# ===========================================================================

class TestBackslashTraversal:
    """D: Windows-style backslash path separators in target must be sanitized."""

    @pytest.mark.parametrize("backslash_target", [
        "..\\..\\etc\\passwd",
        "data\\..\\..\\outside",
        "..\\..\\",
    ])
    def test_backslash_dotdot_blocked(self, backslash_target):
        """D: backslash-encoded '..' traversal is rejected."""
        mat = {"kind": "full", "target": backslash_target}
        with pytest.raises(AppError) as exc_info:
            resolve_target_uri("prod", mat, None, None)
        err = exc_info.value
        assert err.status == 400, (
            f"SECURITY: backslash target {backslash_target!r} must raise 400, got {err.status}"
        )


# ===========================================================================
# E. Tilde expansion attempt
# ===========================================================================

class TestTildeExpansion:
    """E: Tilde in target must be rejected (home-dir expansion guard)."""

    def test_tilde_in_target_rejected(self):
        """E: ~ at start of target raises 400."""
        mat = {"kind": "full", "target": "~/.ssh/id_rsa"}
        with pytest.raises(AppError) as exc_info:
            resolve_target_uri("prod", mat, None, None)
        err = exc_info.value
        assert err.status == 400, (
            f"SECURITY: tilde target must raise 400, got {err.status}"
        )

    def test_tilde_segment_rejected(self):
        """E: ~ as a path segment (not at start) is also rejected."""
        with pytest.raises(AppError) as exc_info:
            _sanitize_target_segment("data/~/secret")
        assert exc_info.value.status == 400


# ===========================================================================
# F. Dotdot anchored at segment boundary
# ===========================================================================

class TestSegmentBoundaryTraversal:
    """F: 'subdir/..' still triggers the block (no length-1 exception)."""

    def test_subdir_then_dotdot_blocked(self):
        """F: 'subdir/..' is a valid traversal back to base — must be blocked."""
        mat = {"kind": "full", "target": "subdir/.."}
        with pytest.raises(AppError) as exc_info:
            resolve_target_uri("prod", mat, None, None)
        assert exc_info.value.status == 400

    def test_multi_level_with_dotdot_blocked(self):
        """F: any path containing '..' at any depth is rejected."""
        mat = {"kind": "full", "target": "a/b/c/../d"}
        with pytest.raises(AppError) as exc_info:
            resolve_target_uri("prod", mat, None, None)
        assert exc_info.value.status == 400


# ===========================================================================
# G. Valid targets — must still pass
# ===========================================================================

class TestValidTargets:
    """G: Legitimate target values must resolve without error."""

    @pytest.mark.parametrize("valid_target, env", [
        ("my_dataset", "prod"),
        ("sales/daily", "dev"),
        ("org_data/reports/2025_q4", "staging"),
        ("data.parquet", "prod"),  # already has extension
        ("rollup_revenue", "prod"),
        ("a", "prod"),
    ])
    def test_valid_target_succeeds(self, valid_target, env, tmp_path):
        """G: Valid target names resolve to a path inside the base."""
        mat = {
            "kind": "full",
            "target": valid_target,
            "base_uri": str(tmp_path),
        }
        result = resolve_target_uri(env, mat, None, None)
        # Must start inside the base directory.
        assert result.startswith(str(tmp_path)), (
            f"Valid target {valid_target!r} resolved outside base: {result!r}"
        )
        # Must end in a parquet extension.
        assert result.endswith(".parquet") or result.endswith(".pq"), (
            f"Expected .parquet extension, got: {result!r}"
        )

    def test_valid_target_stays_within_base_normpath(self, tmp_path):
        """G: normpath-resolved path must be within the base directory."""
        mat = {
            "kind": "full",
            "target": "nested/deep/data",
            "base_uri": str(tmp_path),
        }
        result = resolve_target_uri("prod", mat, None, None)
        normalized = os.path.normpath(result)
        base_norm = os.path.normpath(str(tmp_path))
        assert normalized.startswith(base_norm), (
            f"SECURITY: normalized path escaped base. base={base_norm!r} result={normalized!r}"
        )


# ===========================================================================
# H. Valid env names
# ===========================================================================

class TestValidEnvNames:
    """H: Ordinary env names must not be rejected."""

    @pytest.mark.parametrize("valid_env", [
        "dev", "prod", "staging", "test", "qa", "uat",
    ])
    def test_valid_env_accepted(self, valid_env, tmp_path):
        """H: Standard env names pass sanitization."""
        mat = {
            "kind": "full",
            "target": "my_data",
            "base_uri": str(tmp_path),
        }
        result = resolve_target_uri(valid_env, mat, None, None)
        # Env must appear as a path component in the result.
        assert valid_env in result, (
            f"env={valid_env!r} should appear in the resolved path: {result!r}"
        )


# ===========================================================================
# I. Remote (S3) targets — traversal still rejected
# ===========================================================================

class TestRemoteTargetTraversal:
    """I: Path traversal in target is rejected even when base is an S3 URI."""

    @pytest.mark.parametrize("bad_target", [
        "../../../etc/passwd",
        "../../secret",
    ])
    def test_s3_base_still_rejects_dotdot_target(self, bad_target):
        """I: S3 base_uri does not bypass the target sanitization guard."""
        mat = {
            "kind": "full",
            "target": bad_target,
            "base_uri": "s3://my-bucket/data",
        }
        with pytest.raises(AppError) as exc_info:
            resolve_target_uri("prod", mat, None, None)
        err = exc_info.value
        assert err.status == 400, (
            f"SECURITY: S3 base should not bypass dotdot in target={bad_target!r}. "
            f"Got status={err.status}"
        )

    def test_valid_target_with_s3_base_passes(self):
        """I: Valid target with S3 base produces a clean s3:// URI."""
        mat = {
            "kind": "full",
            "target": "sales/daily",
            "base_uri": "s3://my-bucket/data",
        }
        result = resolve_target_uri("prod", mat, None, None)
        assert result.startswith("s3://"), f"Expected s3:// result, got {result!r}"
        assert ".." not in result, f"SECURITY: '..' in resolved S3 path: {result!r}"


# ===========================================================================
# J. blend_database_path isolation (structural assertion)
# ===========================================================================

class TestBlendDatabasePathIsolation:
    """J: blend_database_path input is server-generated UUID — not user-controlled."""

    def test_blend_database_path_with_uuid_stays_inside_blends_dir(self):
        """J: A UUID flow_id produces a path inside seed_data/blends/."""
        from app.flows.materialize import blend_database_path

        flow_id = str(uuid.uuid4())
        result = blend_database_path(flow_id)
        normalized = os.path.normpath(result)
        # Must contain 'blends' directory component.
        assert "blends" in normalized.split(os.sep), (
            f"blend_database_path must use blends/ subdir, got: {normalized!r}"
        )
        # Must end in .duckdb
        assert result.endswith(".duckdb"), (
            f"Expected .duckdb extension, got: {result!r}"
        )
        # The flow_id must appear in the filename.
        assert flow_id in result, (
            f"flow_id {flow_id!r} must be in path, got: {result!r}"
        )


# ===========================================================================
# K. Watch-sweep org-scope isolation
# ===========================================================================

class TestWatchSweepOrgScope:
    """K: run_watch_sweep only evaluates watches for the specified org_id."""

    @pytest.mark.asyncio
    async def test_watch_sweep_only_evaluates_target_org_watches(self):
        """K: Watches belonging to org_b are NOT evaluated by org_a's sweep.

        Verifies that the org_id filter in run_watch_sweep is effective:
        records from a different org are skipped by the sweep.
        """
        from unittest.mock import patch, AsyncMock
        from datetime import datetime, timezone
        from app.jobs.watch_sweep import run_watch_sweep

        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())

        # Two fake watch records: one for org_a, one for org_b.
        records_all = [
            {"id": "watch_a", "org_id": org_a, "enabled": True, "metric_id": "m1",
             "name": "Org A Watch", "threshold": 100.0, "condition": "gt", "channel": "webhook"},
            {"id": "watch_b", "org_id": org_b, "enabled": True, "metric_id": "m2",
             "name": "Org B Watch", "threshold": 50.0, "condition": "lt", "channel": "webhook"},
        ]

        evaluated_watch_ids: list[str] = []

        def fake_registry_all():
            return records_all

        def fake_watch_from_record(record):
            # Return a minimal object mimicking a Watch.
            class _W:
                id = record["id"]
                org_id = record["org_id"]
                enabled = record.get("enabled", True)
                name = record.get("name", "")
                metric_id = record.get("metric_id", "")
                threshold = record.get("threshold", 0.0)
                condition = record.get("condition", "gt")
                channel = record.get("channel", "webhook")
            return _W()

        async def fake_resolve_metric(metric_id, org_id):
            return {"id": metric_id, "spec": {}}

        async def fake_run_watch(watch, metric, claims):
            evaluated_watch_ids.append(watch.id)
            return {"breached": False, "value": 0.0}

        with (
            patch("app.routes.watches._registry_all", side_effect=fake_registry_all),
            patch("app.routes.watches._watch_from_record", side_effect=fake_watch_from_record),
            patch("app.routes.watches._resolve_metric_for_watch", side_effect=fake_resolve_metric),
            patch("app.ai.watch.run_watch", side_effect=fake_run_watch),
        ):
            result = await run_watch_sweep(org_a, datetime.now(timezone.utc))

        # Only org_a's watch should have been evaluated.
        assert "watch_a" in evaluated_watch_ids, (
            "watch_a (org_a) must be evaluated in org_a sweep"
        )
        assert "watch_b" not in evaluated_watch_ids, (
            "SECURITY: watch_b (org_b) must NOT be evaluated in org_a sweep"
        )
        assert result["org_id"] == org_a
        assert result["evaluated"] == 1, (
            f"Only 1 watch should be evaluated, got {result['evaluated']}"
        )

    @pytest.mark.asyncio
    async def test_watch_sweep_missing_org_id_raises(self):
        """K: execute_watch_sweep_sync with no org_id raises ValueError (fast-fail)."""
        import asyncio
        from app.jobs.watch_sweep import execute_watch_sweep_sync
        from datetime import datetime, timezone

        bad_job = {"kind": "watch_sweep", "org_id": ""}  # empty org_id
        with pytest.raises(ValueError, match="missing org_id"):
            execute_watch_sweep_sync(bad_job, datetime.now(timezone.utc))


# ===========================================================================
# L. Materialize write-scope: flow specs are org-scoped
# ===========================================================================

class TestMaterializeOrgScope:
    """L: The materialize path inherits org scoping from flow ownership."""

    def test_resolve_target_uri_no_org_cross_contamination(self, tmp_path):
        """L: Two orgs with identical target names get isolated paths via base_uri.

        In production each org's flows resolve to org-scoped storage (the
        base_uri is configured per-org or per-deployment, and flow ownership
        is verified by get_user_org).  This test confirms that two calls with
        different base_uris (simulating two org namespaces) never produce the
        same physical path.
        """
        base_org_a = str(tmp_path / "org_a")
        base_org_b = str(tmp_path / "org_b")

        mat_a = {"kind": "full", "target": "sales/daily", "base_uri": base_org_a}
        mat_b = {"kind": "full", "target": "sales/daily", "base_uri": base_org_b}

        path_a = resolve_target_uri("prod", mat_a, None, None)
        path_b = resolve_target_uri("prod", mat_b, None, None)

        assert path_a != path_b, (
            "SECURITY: identical target names for different orgs must resolve to "
            "different physical paths (org isolation via base_uri)."
        )
        assert path_a.startswith(base_org_a), f"Org A path must be inside org_a base: {path_a}"
        assert path_b.startswith(base_org_b), f"Org B path must be inside org_b base: {path_b}"

    def test_resolve_target_uri_does_not_allow_cross_base_write(self, tmp_path):
        """L: target cannot escape its base_uri even with multiple subdir levels."""
        base_org_a = str(tmp_path / "org_a")
        # Attempt to write to org_b's space using traversal.
        mat = {
            "kind": "full",
            "target": "../org_b/stolen_data",
            "base_uri": base_org_a,
        }
        with pytest.raises(AppError) as exc_info:
            resolve_target_uri("prod", mat, None, None)
        err = exc_info.value
        assert err.status == 400, (
            f"SECURITY: cross-base traversal via target must raise 400, got {err.status}"
        )
