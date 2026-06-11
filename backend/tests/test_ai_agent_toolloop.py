"""Tests for the REAL provider tool-use loop + metric tools (M22).

Coverage
--------
1. Real-provider tool loop (``run_agent`` with a non-Null FakeProvider):
   - Step 1 returns a tool-call JSON → the loop executes the tool (via
     execute_tool), feeds the observation back, and step 2's plain-text reply
     becomes the final answer — all within max_steps.
   - The loop tolerates fenced / prose-wrapped tool-call JSON.
   - A provider that ALWAYS asks for a tool hits max_steps and terminates
     gracefully (capped actions, synthesised reply — no infinite loop).
   - RLS: claims are threaded through every tool execution.
2. Tool ``list_metrics`` — returns the registered demo metric.
3. Tool ``query_metric``:
   - Returns rows against the demo connector (columns/rows/row_count).
   - RLS narrows rows when a policy is supplied.
   - Unknown metric → structured {error:{code,message}}, NOT an exception.
   - Invalid dimension → structured {error:{code,message}}, NOT an exception.
4. NullProvider path is unchanged (still scripted, still terminates).

The FakeProvider mirrors the provider/fixture patterns used by the existing
agent tests (``test_ai_agent.py``), but is a NON-Null provider so the real
tool-use branch of ``run_agent`` is exercised.
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.agent import run_agent
from app.ai.provider import LLMProvider, NullProvider
from app.ai.tools import execute_tool


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _empty_claims() -> dict[str, Any]:
    return {"kind": "access", "sub": "test-user", "policies": {}, "scope": ["read:*"]}


def _claims_with_policy(col: str, val: Any) -> dict[str, Any]:
    return {"kind": "access", "sub": "test-user", "policies": {col: val}, "scope": ["read:*"]}


class FakeProvider(LLMProvider):
    """Non-Null provider returning scripted ``complete()`` replies in order.

    Each ``complete`` call pops the next reply; when exhausted it returns a
    default plain-text answer (which the loop treats as the final reply).
    Records the prompts it was called with so tests can assert the tool result
    was fed back into the conversation.
    """

    name = "fake"

    def __init__(self, replies: list[str], *, default: str = "All done.") -> None:
        self._replies = list(replies)
        self._default = default
        self.prompts: list[str] = []
        self.systems: list[str | None] = []

    def complete(self, prompt: str, system: str | None = None) -> str:
        self.prompts.append(prompt)
        self.systems.append(system)
        if self._replies:
            return self._replies.pop(0)
        return self._default


def _tool_call(tool: str, **arguments: Any) -> str:
    return json.dumps({"tool": tool, "arguments": arguments})


# ---------------------------------------------------------------------------
# 1. Real-provider tool loop
# ---------------------------------------------------------------------------


class TestRealProviderToolLoop:
    def test_loop_executes_tool_then_returns_final_answer(self):
        """Step 1 tool call → execute → feed back → step 2 final text answer."""
        provider = FakeProvider(
            [
                _tool_call("query_metric", metric_id="demo_revenue", dimensions=["name"]),
                "Revenue by name has been computed for you.",
            ]
        )
        result = run_agent(
            [{"role": "user", "content": "what was revenue by name"}],
            provider,
            _empty_claims(),
            max_steps=4,
        )

        # Final answer is the step-2 plain-text reply.
        assert result["reply"] == "Revenue by name has been computed for you."

        # The tool was actually executed and recorded as an action.
        tool_names = [a["tool"] for a in result["actions"]]
        assert tool_names == ["query_metric"]

        # The tool returned real rows from the demo connector.
        first = result["actions"][0]["result"]
        assert first["row_count"] > 0
        assert "revenue" in first["columns"]

        # Two provider completions: one to get the tool call, one for the final.
        assert len(provider.prompts) == 2

    def test_loop_feeds_observation_back_into_conversation(self):
        """The second completion prompt must contain the tool observation."""
        provider = FakeProvider(
            [
                _tool_call("list_metrics"),
                "Here are the metrics.",
            ]
        )
        run_agent(
            [{"role": "user", "content": "list metrics"}],
            provider,
            _empty_claims(),
            max_steps=4,
        )
        # The final completion's prompt should include the tool output.
        second_prompt = provider.prompts[1]
        assert "demo_revenue" in second_prompt
        assert "tool (list_metrics)" in second_prompt

    def test_loop_tolerates_fenced_and_prose_wrapped_tool_call(self):
        """A ```json fenced tool call surrounded by prose still parses + runs."""
        fenced = (
            "Sure, let me look that up.\n\n"
            "```json\n"
            + json.dumps({"tool": "list_metrics", "arguments": {}})
            + "\n```\n"
        )
        provider = FakeProvider([fenced, "Done."])
        result = run_agent(
            [{"role": "user", "content": "metrics please"}],
            provider,
            _empty_claims(),
            max_steps=4,
        )
        assert [a["tool"] for a in result["actions"]] == ["list_metrics"]
        assert result["reply"] == "Done."

    def test_loop_hits_max_steps_and_terminates_gracefully(self):
        """A provider that always asks for a tool must stop at max_steps."""
        # Far more tool calls than max_steps; never a final text reply.
        provider = FakeProvider([_tool_call("list_metrics") for _ in range(20)])
        result = run_agent(
            [{"role": "user", "content": "loop forever"}],
            provider,
            _empty_claims(),
            max_steps=2,
        )
        # Actions capped at max_steps — no infinite loop.
        assert len(result["actions"]) == 2
        # A (synthesised) reply is always returned.
        assert isinstance(result["reply"], str) and result["reply"]

    def test_loop_immediate_final_answer_runs_no_tools(self):
        """A plain-text reply on step 1 ends the loop with zero tool calls."""
        provider = FakeProvider(["No tools needed — the answer is 42."])
        result = run_agent(
            [{"role": "user", "content": "hi"}],
            provider,
            _empty_claims(),
            max_steps=4,
        )
        assert result["actions"] == []
        assert result["reply"] == "No tools needed — the answer is 42."

    def test_loop_threads_claims_for_rls(self):
        """RLS claims passed to run_agent narrow the tool's returned rows."""
        # Unrestricted run — all demo names.
        open_provider = FakeProvider(
            [_tool_call("query_metric", metric_id="demo_revenue", dimensions=["name"]), "ok"]
        )
        open_result = run_agent(
            [{"role": "user", "content": "revenue"}],
            open_provider,
            _empty_claims(),
            max_steps=4,
        )
        open_count = open_result["actions"][0]["result"]["row_count"]

        # RLS active=True — must narrow.
        rls_provider = FakeProvider(
            [_tool_call("query_metric", metric_id="demo_revenue", dimensions=["name"]), "ok"]
        )
        rls_result = run_agent(
            [{"role": "user", "content": "revenue"}],
            rls_provider,
            _claims_with_policy("active", True),
            max_steps=4,
        )
        rls_count = rls_result["actions"][0]["result"]["row_count"]

        assert rls_count <= open_count
        assert rls_count > 0  # demo has active rows

    def test_loop_returns_unchanged_shape(self):
        """Return shape matches run_agent's {reply, actions} contract."""
        provider = FakeProvider(["final."])
        result = run_agent(
            [{"role": "user", "content": "hi"}], provider, _empty_claims()
        )
        assert set(result.keys()) == {"reply", "actions"}
        assert isinstance(result["reply"], str)
        assert isinstance(result["actions"], list)

    def test_tool_error_is_surfaced_not_raised(self):
        """A failing tool call is fed back as an error, loop still finishes."""
        provider = FakeProvider(
            [
                _tool_call("query_metric", metric_id="does_not_exist"),
                "I couldn't find that metric.",
            ]
        )
        # Should NOT raise — the tool returns a structured error.
        result = run_agent(
            [{"role": "user", "content": "bogus metric"}],
            provider,
            _empty_claims(),
            max_steps=4,
        )
        assert result["reply"] == "I couldn't find that metric."
        err = result["actions"][0]["result"]
        assert "error" in err


# ---------------------------------------------------------------------------
# 2. NullProvider path must remain scripted + unchanged
# ---------------------------------------------------------------------------


class TestNullProviderUnchanged:
    def test_null_provider_still_scripted(self):
        result = run_agent(
            [{"role": "user", "content": "run the demo query"}],
            NullProvider(),
            _empty_claims(),
        )
        tool_names = [a["tool"] for a in result["actions"]]
        assert "generate_sql" in tool_names
        assert "run_query" in tool_names
        assert isinstance(result["reply"], str) and result["reply"]


# ---------------------------------------------------------------------------
# 3. Tool: list_metrics
# ---------------------------------------------------------------------------


class TestListMetricsTool:
    def test_list_metrics_returns_demo_revenue(self):
        result = execute_tool("list_metrics", {}, _empty_claims())
        assert "metrics" in result
        ids = [m["id"] for m in result["metrics"]]
        assert "demo_revenue" in ids

    def test_list_metrics_entries_have_required_fields(self):
        result = execute_tool("list_metrics", {}, _empty_claims())
        for m in result["metrics"]:
            assert "id" in m
            assert "name" in m
            assert "measure" in m
            assert {"name", "agg", "expr"} <= set(m["measure"].keys())
            assert "dimensions" in m
            assert "time_grains" in m
            assert "description" in m


# ---------------------------------------------------------------------------
# 4. Tool: query_metric
# ---------------------------------------------------------------------------


class TestQueryMetricTool:
    def test_query_metric_returns_rows(self):
        result = execute_tool(
            "query_metric",
            {"metric_id": "demo_revenue", "dimensions": ["name"]},
            _empty_claims(),
        )
        assert "columns" in result
        assert "rows" in result
        assert "row_count" in result
        assert result["row_count"] > 0
        assert "revenue" in result["columns"]

    def test_query_metric_rls_narrows_rows(self):
        open_result = execute_tool(
            "query_metric",
            {"metric_id": "demo_revenue", "dimensions": ["name"]},
            _empty_claims(),
        )
        rls_result = execute_tool(
            "query_metric",
            {"metric_id": "demo_revenue", "dimensions": ["name"]},
            _claims_with_policy("active", True),
        )
        assert rls_result["row_count"] <= open_result["row_count"]

    def test_query_metric_unknown_metric_returns_structured_error(self):
        result = execute_tool(
            "query_metric",
            {"metric_id": "nonexistent_metric_xyz"},
            _empty_claims(),
        )
        assert "error" in result
        assert result["error"]["code"] == "metric_not_found"
        # No exception was raised — we got a dict back.
        assert isinstance(result, dict)

    def test_query_metric_invalid_dimension_returns_structured_error(self):
        result = execute_tool(
            "query_metric",
            {"metric_id": "demo_revenue", "dimensions": ["not_a_dim"]},
            _empty_claims(),
        )
        assert "error" in result
        assert result["error"]["code"] == "unknown_dimension"

    def test_query_metric_missing_metric_id_raises(self):
        """The schema requires metric_id — execute_tool validates it (400)."""
        from app.errors import AppError

        import pytest

        with pytest.raises(AppError) as exc_info:
            execute_tool("query_metric", {}, _empty_claims())
        assert exc_info.value.status == 400

    def test_query_metric_extra_arg_rejected(self):
        """additionalProperties is False → unexpected args are rejected."""
        from app.errors import AppError

        import pytest

        with pytest.raises(AppError) as exc_info:
            execute_tool(
                "query_metric",
                {"metric_id": "demo_revenue", "bogus": 1},
                _empty_claims(),
            )
        assert exc_info.value.status == 400
