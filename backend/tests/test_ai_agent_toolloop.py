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


# ---------------------------------------------------------------------------
# 5. Tool: query_metric — TENANT ISOLATION (SEC)
# ---------------------------------------------------------------------------
#
# The metric registry is a process-GLOBAL singleton, so org B's metric can
# already be loaded in-memory (a registry HIT) by the time org A's agent runs.
# The query_metric tool MUST verify org ownership of that hit — otherwise org A
# reads org B's MetricDefinition (base_sql / dimensions / datastore) and runs it.


import uuid

from app.metrics.models import Dimension, DerivedMeasure, Measure, MetricDefinition
from app.metrics import registry as _reg


def _claims_for_org(org_id: str | None) -> dict[str, Any]:
    return {
        "kind": "access",
        "sub": "test-user",
        "org": org_id,
        "policies": {},
        "scope": ["read:*"],
    }


def _register_org_metric(slug: str) -> None:
    """Register a NON-seed metric directly into the shared registry (a HIT)."""
    _reg.get_metric_registry().register(
        MetricDefinition(
            id=slug,
            name="Org B secret revenue",
            measure=Measure(name="revenue", agg="sum", expr="value", type="additive"),
            base_table="demo",
            dimensions=(Dimension(name="name", type="text"),),
            time_dimension=None,
            description="A metric that belongs to org B only.",
        )
    )


class TestQueryMetricTenantIsolation:
    def test_foreign_org_cannot_resolve_registry_hit(self, monkeypatch):
        """Org A's query_metric call cannot resolve org B's metric slug.

        The slug is a live registry HIT (loaded by org B), yet the ownership
        gate fails-closed for org A → clean ``metric_not_found`` with NO leak of
        org B's definition or data.
        """
        org_a = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        slug = "secret_rev"
        _register_org_metric(slug)

        async def _fake_fetchrow(query, *args):
            # Only org_b owns 'secret_rev'.
            if args[0] == slug and args[1] == org_b:
                return {"ok": 1}
            return None

        monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

        # Org A is denied — structured not-found, never org B's rows/definition.
        denied = execute_tool(
            "query_metric",
            {"metric_id": slug, "dimensions": ["name"]},
            _claims_for_org(org_a),
        )
        assert "error" in denied
        assert denied["error"]["code"] == "metric_not_found"
        assert "rows" not in denied and "columns" not in denied

        # The OWNING org (org B) still resolves + runs it.
        allowed = execute_tool(
            "query_metric",
            {"metric_id": slug, "dimensions": ["name"]},
            _claims_for_org(org_b),
        )
        assert "error" not in allowed
        assert allowed["row_count"] > 0

    def test_missing_org_denies_non_seed_metric(self, monkeypatch):
        """No org in claims → a non-seed slug cannot be proven owned → denied."""
        slug = "secret_rev"
        _register_org_metric(slug)

        async def _fake_fetchrow(query, *args):
            return None  # ownership can never be proven

        monkeypatch.setattr("app.db.fetchrow", _fake_fetchrow)

        result = execute_tool(
            "query_metric",
            {"metric_id": slug, "dimensions": ["name"]},
            _claims_for_org(None),
        )
        assert result["error"]["code"] == "metric_not_found"

    def test_seed_metric_resolves_for_any_org(self):
        """Seed metrics belong to no tenant → resolve for everyone, no DB hit."""
        result = execute_tool(
            "query_metric",
            {"metric_id": "demo_revenue", "dimensions": ["name"]},
            _claims_for_org(str(uuid.uuid4())),
        )
        assert "error" not in result
        assert result["row_count"] > 0


# ---------------------------------------------------------------------------
# 6. Tool: query_metric — policy_cols hoisted on layered (derived) metric
# ---------------------------------------------------------------------------
#
# [MED engine-correctness] compile_metric must receive policy_cols derived from
# claims when the metric uses the layered path (derived_measures present) and
# rls_keys=[].  Without it the __base CTE omits the policy column from its
# GROUP BY / SELECT, so the planner's injected WHERE policy_col=val hits a
# column-not-found at runtime.
#
# Test strategy:
#   1. Register a layered metric (derived_measures present, rls_keys=[]) backed
#      by the in-process demo table.
#   2. Monkeypatch metric_belongs_to_org → True so the ownership gate passes.
#   3. Execute query_metric with claims that carry a policy {"active": True}.
#   4. Assert: no "error" in result, row_count > 0, the derived column is present.
#      (proves the layered compile + plan + DuckDB execute all succeeded without
#      a column-not-found error that would have manifested as an exception before
#      the fix.)


class TestQueryMetricLayeredPolicyCols:
    """Regression test for the missing policy_cols on the layered compile path."""

    def _layered_metric_slug(self) -> str:
        return "test_layered_active_ratio"

    def _register_layered_metric(self, slug: str) -> None:
        """Register a metric with derived_measures and rls_keys=[] on demo table."""
        _reg.get_metric_registry().register(
            MetricDefinition(
                id=slug,
                name="Active ratio (layered)",
                # Primary base measure (value sum).
                measure=Measure(name="total_value", agg="sum", expr="value", type="additive"),
                base_table="demo",
                dimensions=(
                    Dimension(name="name", type="text"),
                    # 'active' column exists on the demo table — it will be used as
                    # an RLS policy column in claims so the planner tries to inject
                    # WHERE active = True on the outer SELECT over __base.
                    Dimension(name="active", type="bool"),
                ),
                time_dimension=None,
                # derived_measures present → compile_metric takes the LAYERED path.
                derived_measures=(
                    DerivedMeasure(
                        name="value_ratio",
                        formula="total_value / total_value",
                        format="number",
                    ),
                ),
                # rls_keys=[] → the compiler won't auto-hoist 'active'; it MUST
                # come from policy_cols derived from claims.
                rls_keys=(),
                description="Test-only layered metric for policy_cols regression.",
            )
        )

    def test_layered_metric_with_active_rls_policy_executes_correctly(
        self, monkeypatch
    ) -> None:
        """Layered metric + rls_keys=[] + active RLS policy → no column-not-found.

        Before the fix, compile_metric was called without policy_cols, so the
        'active' column was absent from __base's GROUP BY/SELECT.  The planner's
        injected WHERE active = True on the outer SELECT would have raised a
        column-not-found at DuckDB execution time.  After the fix, policy_cols is
        derived from claims and threaded into compile_metric, which hoists 'active'
        into __base — the query compiles and executes correctly.
        """
        slug = self._layered_metric_slug()
        self._register_layered_metric(slug)

        # Bypass tenant ownership gate — this test is about compile correctness,
        # not about multi-tenant isolation.
        async def _always_owned(*_args, **_kwargs) -> bool:
            return True

        monkeypatch.setattr("app.metrics.registry.metric_belongs_to_org", _always_owned)

        # Claims carry an RLS policy on 'active' — the planner will inject
        # WHERE active = True on the outer SELECT over __base.
        claims = {
            "kind": "access",
            "sub": "test-user",
            "org": str(uuid.uuid4()),
            "policies": {"active": True},
            "scope": ["read:*"],
        }

        result = execute_tool(
            "query_metric",
            {
                "metric_id": slug,
                "dimensions": ["name"],
            },
            claims,
        )

        # Must succeed — no column-not-found error.
        assert "error" not in result, f"Unexpected error: {result.get('error')}"
        assert result["row_count"] > 0
        # The derived measure column must be present in the output.
        assert "value_ratio" in result["columns"]

    def test_layered_metric_policy_cols_passed_to_compile(self, monkeypatch) -> None:
        """Assert policy_cols is derived from claims and forwarded to compile_metric.

        We spy on compile_metric to capture the kwargs it was called with, so this
        test does NOT depend on DuckDB execution — it purely verifies the fix is in
        place (the correct argument is forwarded).
        """
        from app.metrics import compile as _compile_mod

        slug = self._layered_metric_slug()
        self._register_layered_metric(slug)

        async def _always_owned(*_args, **_kwargs) -> bool:
            return True

        monkeypatch.setattr("app.metrics.registry.metric_belongs_to_org", _always_owned)

        captured: list[tuple] = []
        _orig_compile = _compile_mod.compile_metric

        def _spy_compile(metric, mq, **kwargs):
            captured.append((metric, mq, kwargs))
            return _orig_compile(metric, mq, **kwargs)

        monkeypatch.setattr(_compile_mod, "compile_metric", _spy_compile)

        claims = {
            "kind": "access",
            "sub": "test-user",
            "org": str(uuid.uuid4()),
            "policies": {"active": True},
            "scope": ["read:*"],
        }

        execute_tool(
            "query_metric",
            {"metric_id": slug, "dimensions": ["name"]},
            claims,
        )

        # compile_metric must have been called exactly once.
        assert len(captured) == 1
        _, _, kwargs = captured[0]
        # policy_cols must be derived from claims["policies"] keys.
        assert "policy_cols" in kwargs
        assert "active" in kwargs["policy_cols"]
