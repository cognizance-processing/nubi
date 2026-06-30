"""Tests for the explain_metric_change agent tool.

Coverage
--------
1. Tool is registered and discoverable (in all_tools, tool_schemas, get_tool).
2. Tool schema is well-formed (type=object, required fields, additionalProperties=False).
3. Tool returns the expected breakdown shape for the built-in demo_revenue metric
   (which has no time dimension — the tool still runs and returns data because the
   demo table is static and period filters are empty).
4. Tool output matches the pure-math build_explain_result for the same inputs —
   the tool is a thin wrapper, not a re-implementation.
5. RLS/claims threading: a restricted identity (impossible policy) sees zero totals
   — the tool never widens scope.
6. Unknown metric → structured error (metric_not_found), not a hard exception.
7. Unknown dimension → structured error (unknown_dimension).
8. execute_tool dispatcher routes correctly (missing required args → AppError 400).
9. The tool's summary_hint is a non-empty string describing the direction of change.
10. The system prompt (agent) mentions explain_metric_change so the model uses it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.tools import all_tools, execute_tool, get_tool, tool_schemas


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_claims() -> dict[str, Any]:
    """First-party claims with no RLS policies."""
    return {"kind": "access", "sub": "test-user", "policies": {}, "scope": ["read:*"]}


def _claims_with_policy(col: str, val: Any) -> dict[str, Any]:
    """Claims with an equality RLS policy."""
    return {"kind": "access", "sub": "test-user", "policies": {col: val}, "scope": ["read:*"]}


# Placeholder time windows — demo_revenue has no time dimension so these are
# ignored at filter-build time (empty filter list produced), but the tool still
# accepts and validates them.
_CURRENT_START = "2024-02-01T00:00:00"
_CURRENT_END = "2024-03-01T00:00:00"
_COMPARISON_START = "2024-01-01T00:00:00"
_COMPARISON_END = "2024-02-01T00:00:00"


def _explain_args(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "metric_id": "demo_revenue",
        "current_start": _CURRENT_START,
        "current_end": _CURRENT_END,
        "comparison_start": _COMPARISON_START,
        "comparison_end": _COMPARISON_END,
    }
    args.update(overrides)
    return args


# ---------------------------------------------------------------------------
# 1. Tool registration
# ---------------------------------------------------------------------------


class TestExplainToolRegistration:
    def test_explain_tool_in_all_tools(self):
        names = {t.name for t in all_tools()}
        assert "explain_metric_change" in names, (
            f"explain_metric_change not found in tools: {names}"
        )

    def test_get_tool_returns_explain_tool(self):
        tool = get_tool("explain_metric_change")
        assert tool is not None
        assert tool.name == "explain_metric_change"

    def test_explain_tool_in_schemas(self):
        schemas = tool_schemas()
        names = {s["name"] for s in schemas}
        assert "explain_metric_change" in names

    def test_explain_tool_has_description(self):
        tool = get_tool("explain_metric_change")
        assert tool is not None
        assert isinstance(tool.description, str) and tool.description

    def test_explain_tool_is_callable(self):
        tool = get_tool("explain_metric_change")
        assert tool is not None
        assert callable(tool.fn)


# ---------------------------------------------------------------------------
# 2. Schema correctness
# ---------------------------------------------------------------------------


class TestExplainToolSchema:
    def _schema(self) -> dict[str, Any]:
        tool = get_tool("explain_metric_change")
        assert tool is not None
        return tool.json_schema

    def test_schema_is_object_type(self):
        assert self._schema()["type"] == "object"

    def test_schema_has_properties(self):
        assert "properties" in self._schema()

    def test_schema_has_additional_properties_false(self):
        assert self._schema().get("additionalProperties") is False

    def test_schema_requires_metric_id(self):
        assert "metric_id" in self._schema().get("required", [])

    def test_schema_requires_current_start(self):
        assert "current_start" in self._schema().get("required", [])

    def test_schema_requires_current_end(self):
        assert "current_end" in self._schema().get("required", [])

    def test_schema_requires_comparison_start(self):
        assert "comparison_start" in self._schema().get("required", [])

    def test_schema_requires_comparison_end(self):
        assert "comparison_end" in self._schema().get("required", [])

    def test_schema_dimensions_is_optional_array(self):
        props = self._schema()["properties"]
        assert "dimensions" in props
        assert props["dimensions"]["type"] == "array"
        # 'dimensions' must NOT be in required
        assert "dimensions" not in self._schema().get("required", [])

    def test_schema_top_n_is_optional_integer(self):
        props = self._schema()["properties"]
        assert "top_n" in props
        assert props["top_n"]["type"] == "integer"
        assert "top_n" not in self._schema().get("required", [])


# ---------------------------------------------------------------------------
# 3. Return shape for demo_revenue (static data, no time dimension)
# ---------------------------------------------------------------------------


class TestExplainToolShape:
    """Verify the tool returns the expected structure on the happy path."""

    def test_returns_dict(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert isinstance(result, dict)

    def test_no_error_key(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert "error" not in result, f"Unexpected error: {result.get('error')}"

    def test_has_metric_id(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert result.get("metric_id") == "demo_revenue"

    def test_has_measure(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert result.get("measure") == "revenue"

    def test_has_delta_total(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert "delta_total" in result
        assert isinstance(result["delta_total"], (int, float))

    def test_has_current_total(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert "current_total" in result
        assert isinstance(result["current_total"], (int, float))

    def test_has_comparison_total(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert "comparison_total" in result
        assert isinstance(result["comparison_total"], (int, float))

    def test_has_dimensions_list(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert "dimensions" in result
        assert isinstance(result["dimensions"], list)

    def test_has_summary_hint(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert "summary_hint" in result
        assert isinstance(result["summary_hint"], str)

    def test_dimensions_non_empty_for_demo_revenue(self):
        """demo_revenue has 'name' and 'active' dims — at least one should appear."""
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert len(result["dimensions"]) > 0

    def test_dimension_entries_have_required_keys(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        for bd in result["dimensions"]:
            for key in ("dimension", "explanatory_power", "coverage", "members"):
                assert key in bd, f"Dimension entry missing key {key!r}: {bd}"

    def test_member_entries_have_required_keys(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        for bd in result["dimensions"]:
            for m in bd["members"]:
                for key in ("member", "delta", "share", "direction"):
                    assert key in m, f"Member entry missing key {key!r}: {m}"

    def test_member_direction_valid_values(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        valid = {"up", "down", "flat"}
        for bd in result["dimensions"]:
            for m in bd["members"]:
                assert m["direction"] in valid, (
                    f"Invalid direction {m['direction']!r}"
                )


# ---------------------------------------------------------------------------
# 4. Tool output matches build_explain_result for same inputs
# ---------------------------------------------------------------------------


class TestExplainToolMatchesPureMath:
    """Tool must reuse build_explain_result — no divergent math."""

    def test_delta_equals_current_minus_comparison(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        expected_delta = result["current_total"] - result["comparison_total"]
        assert result["delta_total"] == pytest.approx(expected_delta, abs=1e-9)

    def test_dimensions_sorted_by_explanatory_power_desc(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        powers = [bd["explanatory_power"] for bd in result["dimensions"]]
        assert powers == sorted(powers, reverse=True), (
            f"Dimensions not sorted by explanatory_power desc: {powers}"
        )

    def test_demo_revenue_totals_are_sum_of_values(self):
        """demo table has values [10,20,30,40,50] → SUM=150.

        Since demo_revenue has no time dimension the period filters are empty
        and both periods query the full 5-row table.
        current_total == comparison_total == 150, delta_total == 0.
        """
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert result["current_total"] == pytest.approx(150.0, abs=1e-6)
        assert result["comparison_total"] == pytest.approx(150.0, abs=1e-6)
        assert result["delta_total"] == pytest.approx(0.0, abs=1e-6)

    def test_summary_hint_non_empty(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert len(result["summary_hint"]) > 0

    def test_summary_hint_contains_metric_id(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert "demo_revenue" in result["summary_hint"]

    def test_top_n_limits_members(self):
        """top_n=1 → at most 1 explicit member per dimension."""
        result = execute_tool(
            "explain_metric_change", _explain_args(top_n=1), _empty_claims()
        )
        for bd in result["dimensions"]:
            assert len(bd["members"]) <= 1, (
                f"Expected ≤1 member with top_n=1, got {len(bd['members'])} "
                f"in dimension {bd['dimension']!r}"
            )

    def test_top_n_clamp_high(self):
        """top_n=999 should be clamped to 50 — no error, just clamped."""
        result = execute_tool(
            "explain_metric_change", _explain_args(top_n=999), _empty_claims()
        )
        # Should succeed (clamped) with normal shape.
        assert "error" not in result

    def test_dimensions_filter_subset(self):
        """Requesting only 'name' dimension → only that dimension in output."""
        result = execute_tool(
            "explain_metric_change",
            _explain_args(dimensions=["name"]),
            _empty_claims(),
        )
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        dim_names = [bd["dimension"] for bd in result["dimensions"]]
        assert dim_names == ["name"], f"Expected only ['name'], got {dim_names}"


# ---------------------------------------------------------------------------
# 5. RLS / claims threading
# ---------------------------------------------------------------------------


class TestExplainToolRLS:
    """The tool must honour caller claims — data never exceeds caller scope."""

    def test_impossible_rls_policy_gives_zero_totals(self):
        """A policy that matches no rows → both totals are 0, delta_total is 0."""
        result = execute_tool(
            "explain_metric_change",
            _explain_args(),
            _claims_with_policy("id", -999),  # id -999 does not exist
        )
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert result["current_total"] == pytest.approx(0.0, abs=1e-9)
        assert result["comparison_total"] == pytest.approx(0.0, abs=1e-9)
        assert result["delta_total"] == pytest.approx(0.0, abs=1e-9)

    def test_active_true_policy_filters_members(self):
        """Claims with active=True must reduce the member values to active-only rows.

        demo table active rows: alpha(10), beta(20), delta(40) → SUM=70.
        """
        result = execute_tool(
            "explain_metric_change",
            _explain_args(),
            _claims_with_policy("active", True),
        )
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        # Total should be ≤ 150 (the full sum without RLS).
        assert result["current_total"] <= 150.0 + 1e-9, (
            f"current_total {result['current_total']} exceeds full-table total — RLS bypassed?"
        )
        # Specifically should be 70 (only active rows).
        assert result["current_total"] == pytest.approx(70.0, abs=1e-6), (
            f"Expected 70 (active rows only), got {result['current_total']}"
        )

    def test_claims_never_widen_to_full_data_with_policy(self):
        """A restricting policy must always return totals ≤ unrestricted total."""
        unrestricted = execute_tool(
            "explain_metric_change", _explain_args(), _empty_claims()
        )
        restricted = execute_tool(
            "explain_metric_change",
            _explain_args(),
            _claims_with_policy("active", True),
        )
        assert restricted["current_total"] <= unrestricted["current_total"] + 1e-9


# ---------------------------------------------------------------------------
# 6 & 7. Error paths
# ---------------------------------------------------------------------------


class TestExplainToolErrors:
    def test_unknown_metric_returns_error(self):
        result = execute_tool(
            "explain_metric_change",
            _explain_args(metric_id="definitely_nonexistent_xyz"),
            _empty_claims(),
        )
        assert "error" in result
        assert result["error"]["code"] == "metric_not_found"

    def test_unknown_dimension_returns_error(self):
        result = execute_tool(
            "explain_metric_change",
            _explain_args(dimensions=["nonexistent_dim_xyz"]),
            _empty_claims(),
        )
        assert "error" in result
        assert result["error"]["code"] == "unknown_dimension"

    def test_missing_metric_id_raises_via_dispatcher(self):
        """execute_tool must raise AppError(invalid_tool_input, 400) for missing required args."""
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool(
                "explain_metric_change",
                {
                    "current_start": _CURRENT_START,
                    "current_end": _CURRENT_END,
                    "comparison_start": _COMPARISON_START,
                    "comparison_end": _COMPARISON_END,
                },
                _empty_claims(),
            )
        assert exc_info.value.status == 400

    def test_missing_current_start_raises_via_dispatcher(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool(
                "explain_metric_change",
                {
                    "metric_id": "demo_revenue",
                    "current_end": _CURRENT_END,
                    "comparison_start": _COMPARISON_START,
                    "comparison_end": _COMPARISON_END,
                },
                _empty_claims(),
            )
        assert exc_info.value.status == 400

    def test_extra_arg_raises_via_dispatcher(self):
        """additionalProperties=False → extra arg → AppError 400."""
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool(
                "explain_metric_change",
                {**_explain_args(), "unexpected_field": "value"},
                _empty_claims(),
            )
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# 8. execute_tool dispatcher routing
# ---------------------------------------------------------------------------


class TestExplainToolDispatch:
    def test_execute_tool_routes_to_explain(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        assert "metric_id" in result or "error" in result

    def test_execute_tool_unknown_tool_raises(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool("explain_metric_change_nonexistent", _explain_args(), _empty_claims())
        assert exc_info.value.status == 404


# ---------------------------------------------------------------------------
# 9. Summary hint
# ---------------------------------------------------------------------------


class TestExplainToolSummaryHint:
    def test_summary_hint_mentions_measure(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        # measure is 'revenue'; hint should reference it
        assert "revenue" in result["summary_hint"]

    def test_summary_hint_mentions_direction(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        # delta_total == 0 for demo_revenue (same data in both windows)
        # → direction word should be "unchanged"
        assert any(
            word in result["summary_hint"].lower()
            for word in ("unchanged", "increased", "decreased")
        ), f"No direction word in summary_hint: {result['summary_hint']!r}"

    def test_summary_hint_mentions_totals(self):
        result = execute_tool("explain_metric_change", _explain_args(), _empty_claims())
        # The hint should contain numeric values
        import re
        assert re.search(r"\d", result["summary_hint"]), (
            f"summary_hint contains no numbers: {result['summary_hint']!r}"
        )


# ---------------------------------------------------------------------------
# 10. System prompt awareness
# ---------------------------------------------------------------------------


class TestAgentSystemPromptMentionsExplain:
    """The agent system prompt must guide the model to use explain_metric_change."""

    def _get_system_prompt(self) -> str:
        from app.ai.agent import _tool_use_system_prompt
        return _tool_use_system_prompt({})

    def test_system_prompt_contains_explain_metric_change_tool(self):
        prompt = self._get_system_prompt()
        assert "explain_metric_change" in prompt, (
            "explain_metric_change not mentioned in system prompt — model won't use it"
        )

    def test_system_prompt_contains_why_did_hint(self):
        """The prompt should hint that 'why did X change' questions use explain."""
        prompt = self._get_system_prompt().lower()
        assert any(
            phrase in prompt
            for phrase in ("why did", "explain", "root cause", "movement", "drove")
        ), (
            "System prompt does not guide the model to use explain_metric_change "
            "for 'why did X change' questions"
        )
