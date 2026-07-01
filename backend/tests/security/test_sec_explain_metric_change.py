"""Adversarial coverage for the explain_metric_change agent tool (app/ai/tools.py).

RLS/claims-threading and the pure-math shape are already exercised in
``tests/test_ai_explain_tool.py::TestExplainToolRLS`` — this file does NOT
duplicate that.  It targets the specific gap this security wave calls out:
tenant-isolation (no scope bypass) for the org-check gate at
``_tool_explain_metric_change`` lines ~559-568, which mirrors
``_tool_query_metric``'s isolation gate.

Coverage
--------
1. A non-seed metric that does NOT belong to the caller's org → structured
   ``metric_not_found`` error (never leaks a distinguishable "found but
   forbidden" response — same non-disclosure contract as a 404 IDOR gate).
2. A non-seed metric with NO org claim at all → same rejection (a caller
   cannot skip the org check by simply omitting ``org`` from claims).
3. Built-in seed metrics (``SEED_METRIC_IDS``) are exempt from the org check
   BY DESIGN (they belong to no tenant) — confirms the exemption is scoped to
   seeds only, not a blanket bypass.
4. ``policy_cols`` fed into ``compile_metric`` comes ONLY from
   ``claims["policies"]`` — extra keys smuggled via the tool's own arguments
   (``dimensions``, ``top_n``) cannot widen the RLS policy set.
5. The tenant gate calls ``metric_belongs_to_org`` with the metric id AND the
   claims' org — never a hardcoded / body-supplied org.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.tools import _tool_explain_metric_change


_CURRENT_START = "2024-02-01T00:00:00"
_CURRENT_END = "2024-03-01T00:00:00"
_COMPARISON_START = "2024-01-01T00:00:00"
_COMPARISON_END = "2024-02-01T00:00:00"


def _claims(org: str | None = "org-a", policies: dict | None = None) -> dict[str, Any]:
    c: dict[str, Any] = {"kind": "access", "sub": "u1", "scope": ["read:*"]}
    if org is not None:
        c["org"] = org
    c["policies"] = policies or {}
    return c


def _call(claims: dict[str, Any], metric_id: str = "governed_metric_x", **overrides: Any):
    args = {
        "metric_id": metric_id,
        "current_start": _CURRENT_START,
        "current_end": _CURRENT_END,
        "comparison_start": _COMPARISON_START,
        "comparison_end": _COMPARISON_END,
    }
    args.update(overrides)
    return _tool_explain_metric_change(claims, **args)


def _fake_metric():
    """A minimal governed (non-seed) MetricDefinition-like object."""
    from app.metrics.models import Dimension, Measure, MetricDefinition

    return MetricDefinition(
        id="governed_metric_x",
        name="Governed metric",
        measure=Measure(name="revenue", agg="sum", expr="value", type="additive"),
        base_table="sales",
        dimensions=(Dimension(name="region", type="text"),),
        time_dimension=None,
    )


class TestExplainToolCrossOrgIsolation:
    """1-2: non-seed metric belonging to another org (or no org at all)."""

    def test_cross_org_metric_returns_structured_error_not_data(self):
        metric = _fake_metric()
        with patch("app.metrics.registry.get_metric_registry") as mock_reg, patch(
            "app.metrics.registry.metric_belongs_to_org", new=AsyncMock(return_value=False)
        ):
            mock_reg.return_value.get.return_value = metric
            result = _call(_claims(org="org-b"), metric_id="governed_metric_x")

        assert "error" in result, (
            f"SECURITY: cross-org metric access did not return a structured error: {result}"
        )
        assert result["error"]["code"] == "metric_not_found", (
            f"SECURITY: cross-org rejection should look identical to 'not found' "
            f"(no existence disclosure), got code={result['error']['code']!r}"
        )
        # No data keys must leak alongside the error.
        assert "rows" not in result and "dimensions" not in result

    def test_no_org_claim_rejected_for_non_seed_metric(self):
        metric = _fake_metric()
        with patch("app.metrics.registry.get_metric_registry") as mock_reg, patch(
            "app.metrics.registry.metric_belongs_to_org", new=AsyncMock(return_value=True)
        ) as mock_belongs:
            mock_reg.return_value.get.return_value = metric
            result = _call(_claims(org=None), metric_id="governed_metric_x")

        assert "error" in result and result["error"]["code"] == "metric_not_found", (
            f"SECURITY: a caller with NO org claim must not be able to explain a "
            f"governed metric, got: {result}"
        )
        # The DB-backed ownership check must never even be reached without an org.
        mock_belongs.assert_not_called()

    def test_same_org_metric_is_allowed_through_the_gate(self):
        """Sanity: the SAME org is allowed past the gate (no over-blocking)."""
        metric = _fake_metric()
        with patch("app.metrics.registry.get_metric_registry") as mock_reg, patch(
            "app.metrics.registry.metric_belongs_to_org", new=AsyncMock(return_value=True)
        ) as mock_belongs:
            mock_reg.return_value.get.return_value = metric
            result = _call(_claims(org="org-a"), metric_id="governed_metric_x")

        # Should proceed past the tenant gate (may still fail later on DuckDB
        # execution details in this mocked context, but must NOT be the
        # tenant-isolation error).
        if "error" in result:
            assert result["error"]["code"] != "metric_not_found"
        mock_belongs.assert_called_once()
        called_metric_id, called_org = mock_belongs.call_args[0]
        assert called_metric_id == "governed_metric_x"
        assert called_org == "org-a"


class TestExplainToolSeedExemption:
    """3: built-in seed metrics bypass the org check BY DESIGN (no tenant)."""

    def test_seed_metric_no_org_claim_still_runs(self):
        """demo_revenue (SEED_METRIC_IDS) works with no org claim at all."""
        result = _call(_claims(org=None), metric_id="demo_revenue")
        assert "error" not in result, f"Seed metric should not require an org claim: {result}"
        assert result["metric_id"] == "demo_revenue"

    def test_non_seed_id_cannot_impersonate_seed_by_name_collision(self):
        """A non-seed metric registered under an arbitrary id is NOT exempt —
        the exemption is keyed on membership in SEED_METRIC_IDS, not on any
        naming convention an attacker could imitate."""
        from app.metrics.registry import SEED_METRIC_IDS

        assert "governed_metric_x" not in SEED_METRIC_IDS
        metric = _fake_metric()
        with patch("app.metrics.registry.get_metric_registry") as mock_reg, patch(
            "app.metrics.registry.metric_belongs_to_org", new=AsyncMock(return_value=False)
        ) as mock_belongs:
            mock_reg.return_value.get.return_value = metric
            result = _call(_claims(org="org-b"), metric_id="governed_metric_x")

        mock_belongs.assert_called_once()
        assert result["error"]["code"] == "metric_not_found"


class TestExplainToolPolicyColsSourceOfTruth:
    """4: policy_cols is derived ONLY from claims['policies'], never tool args."""

    def test_policy_cols_ignores_dimensions_and_top_n_args(self):
        """Extra tool arguments cannot smuggle additional RLS policy columns."""
        captured_policy_cols: list[tuple] = []

        def fake_compile_metric(metric, mq, policy_cols=()):
            captured_policy_cols.append(policy_cols)
            raise __import__("app.metrics.models", fromlist=["MetricError"]).MetricError(
                "stub", "stubbed out for capture"
            )

        with patch("app.metrics.compile.compile_metric", side_effect=fake_compile_metric):
            _call(
                _claims(org=None, policies={"tenant_id": "alpha"}),
                metric_id="demo_revenue",
                dimensions=["name"],
                top_n=5,
            )

        assert captured_policy_cols, "compile_metric was never called"
        for cols in captured_policy_cols:
            assert set(cols) == {"tenant_id"}, (
                f"SECURITY: policy_cols must equal claims['policies'].keys() exactly, "
                f"got {cols!r} (dimensions/top_n args must never widen the RLS column set)"
            )
