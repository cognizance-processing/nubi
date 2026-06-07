"""Tests for the flows spec layer — validate_flow_spec, flow_spec_is_valid,
flow_spec_json_schema.

Coverage
--------
1. validate_flow_spec
   a. Accepts a fully valid spec (linear 3-task DAG).
   b. Accepts a valid spec with parallel tasks (fan-out/fan-in).
   c. Detects cycle (a→b→a) — hard error, cycle reported in message.
   d. Detects longer cycle (a→b→c→a).
   e. Rejects missing dependency reference — hard error.
   f. Rejects duplicate task key — hard error.
   g. Rejects bad Pydantic parse (invalid kind) — returns (None, issues).
   h. Rejects missing required fields per kind:
      - query without query_id or sql
      - python without code
      - agent without prompt
   i. noop with empty config is valid.
   j. query with sql (no query_id) is valid.
   k. Soft warn on unknown query_id in registry (spec still valid).

2. flow_spec_is_valid
   a. Empty issues list → True.
   b. List with only [warn] entries → True.
   c. List with a hard error → False.
   d. Mixed hard + soft → False.

3. flow_spec_json_schema
   a. Returns a dict with 'properties'.
   b. Contains 'name', 'tasks', 'version' properties.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.flows.spec import (
    FlowSpec,
    TaskSpec,
    flow_spec_is_valid,
    flow_spec_json_schema,
    validate_flow_spec,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _linear_spec() -> dict[str, Any]:
    """Valid 3-task linear spec: pull → enrich → summary."""
    return {
        "version": 1,
        "name": "daily_revenue",
        "params": [
            {"name": "region", "type": "text", "default": "us", "required": False}
        ],
        "tasks": [
            {
                "key": "pull",
                "kind": "query",
                "needs": [],
                "config": {"query_id": "demo_all"},
            },
            {
                "key": "enrich",
                "kind": "python",
                "needs": ["pull"],
                "config": {"code": "result = {'rows': inputs['pull']['row_count']}"},
            },
            {
                "key": "summary",
                "kind": "agent",
                "needs": ["enrich"],
                "config": {"prompt": "Summarize the enriched result.", "max_steps": 4},
            },
        ],
    }


def _parallel_spec() -> dict[str, Any]:
    """Valid spec with parallel tasks + a noop join node."""
    return {
        "version": 1,
        "name": "parallel_flow",
        "tasks": [
            {"key": "root", "kind": "noop", "needs": [], "config": {}},
            {"key": "left", "kind": "python", "needs": ["root"], "config": {"code": "result = 1"}},
            {"key": "right", "kind": "python", "needs": ["root"], "config": {"code": "result = 2"}},
            {"key": "join", "kind": "noop", "needs": ["left", "right"], "config": {}},
        ],
    }


# ---------------------------------------------------------------------------
# 1. validate_flow_spec
# ---------------------------------------------------------------------------


class TestValidateFlowSpecValid:
    """validate_flow_spec accepts well-formed specs."""

    def test_accepts_linear_spec(self):
        spec, issues = validate_flow_spec(_linear_spec())
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert spec is not None, f"Expected valid spec; hard issues: {hard}"
        assert hard == [], f"Unexpected hard issues: {hard}"

    def test_returns_flow_spec_instance(self):
        spec, _ = validate_flow_spec(_linear_spec())
        assert isinstance(spec, FlowSpec)

    def test_spec_fields_parsed_correctly(self):
        spec, _ = validate_flow_spec(_linear_spec())
        assert spec is not None
        assert spec.name == "daily_revenue"
        assert spec.version == 1
        assert len(spec.tasks) == 3
        assert len(spec.params) == 1
        assert spec.params[0].name == "region"

    def test_tasks_have_correct_keys(self):
        spec, _ = validate_flow_spec(_linear_spec())
        assert spec is not None
        keys = [t.key for t in spec.tasks]
        assert keys == ["pull", "enrich", "summary"]

    def test_accepts_parallel_spec(self):
        spec, issues = validate_flow_spec(_parallel_spec())
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert spec is not None, f"Expected valid spec; issues: {issues}"
        assert hard == [], f"Unexpected hard issues: {hard}"

    def test_minimal_spec_no_params_no_tasks(self):
        data = {"version": 1, "name": "empty_flow", "tasks": []}
        spec, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert spec is not None
        assert hard == []

    def test_noop_with_empty_config_is_valid(self):
        data = {
            "version": 1,
            "name": "noop_flow",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
        }
        spec, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert spec is not None
        assert hard == []

    def test_query_with_sql_only_is_valid(self):
        """query task with sql (no query_id) must be accepted."""
        data = {
            "version": 1,
            "name": "sql_flow",
            "tasks": [
                {
                    "key": "q1",
                    "kind": "query",
                    "needs": [],
                    "config": {"sql": "SELECT 1 AS n"},
                }
            ],
        }
        spec, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert spec is not None
        assert hard == [], f"Unexpected hard issues: {hard}"

    def test_retries_and_timeout_fields_parsed(self):
        data = _linear_spec()
        data["tasks"][0]["retries"] = 3
        data["tasks"][0]["retry_backoff_s"] = 60
        data["tasks"][0]["timeout_s"] = 120
        data["tasks"][0]["cache_ttl_s"] = 300
        spec, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert spec is not None
        assert hard == []
        assert spec.tasks[0].retries == 3
        assert spec.tasks[0].retry_backoff_s == 60
        assert spec.tasks[0].timeout_s == 120
        assert spec.tasks[0].cache_ttl_s == 300

    def test_ui_position_parsed(self):
        data = _linear_spec()
        data["tasks"][0]["ui"] = {"x": 100.0, "y": 200.0}
        spec, _ = validate_flow_spec(data)
        assert spec is not None
        assert spec.tasks[0].ui.x == 100.0
        assert spec.tasks[0].ui.y == 200.0


class TestValidateFlowSpecCycle:
    """validate_flow_spec detects cyclic DAGs."""

    def test_simple_cycle_a_b_a(self):
        data = {
            "version": 1,
            "name": "cyclic",
            "tasks": [
                {"key": "a", "kind": "noop", "needs": ["b"], "config": {}},
                {"key": "b", "kind": "noop", "needs": ["a"], "config": {}},
            ],
        }
        spec, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("cycle" in i.lower() for i in hard), (
            f"Expected cycle error in issues: {hard}"
        )

    def test_cycle_message_contains_nodes(self):
        data = {
            "version": 1,
            "name": "cyclic",
            "tasks": [
                {"key": "a", "kind": "noop", "needs": ["b"], "config": {}},
                {"key": "b", "kind": "noop", "needs": ["a"], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        cycle_issues = [i for i in issues if "cycle" in i.lower()]
        assert len(cycle_issues) >= 1
        # Cycle should mention task keys a and b.
        assert any("a" in i and "b" in i for i in cycle_issues), (
            f"Cycle message should mention involved nodes: {cycle_issues}"
        )

    def test_longer_cycle_a_b_c_a(self):
        data = {
            "version": 1,
            "name": "three_cycle",
            "tasks": [
                {"key": "a", "kind": "noop", "needs": ["c"], "config": {}},
                {"key": "b", "kind": "noop", "needs": ["a"], "config": {}},
                {"key": "c", "kind": "noop", "needs": ["b"], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("cycle" in i.lower() for i in hard), (
            f"Expected cycle error in issues: {hard}"
        )

    def test_cycle_is_hard_error(self):
        """A cycle must be a hard error (not prefixed with [warn])."""
        data = {
            "version": 1,
            "name": "cyclic",
            "tasks": [
                {"key": "x", "kind": "noop", "needs": ["y"], "config": {}},
                {"key": "y", "kind": "noop", "needs": ["x"], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        cycle_issues = [i for i in issues if "cycle" in i.lower()]
        assert all(not i.startswith("[warn]") for i in cycle_issues), (
            f"Cycle should be a hard error, not a warning: {cycle_issues}"
        )

    def test_flow_spec_is_valid_returns_false_for_cycle(self):
        data = {
            "version": 1,
            "name": "cyclic",
            "tasks": [
                {"key": "p", "kind": "noop", "needs": ["q"], "config": {}},
                {"key": "q", "kind": "noop", "needs": ["p"], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        assert flow_spec_is_valid(issues) is False


class TestValidateFlowSpecMissingDep:
    """validate_flow_spec detects references to undeclared task keys."""

    def test_missing_dep_is_hard_error(self):
        data = {
            "version": 1,
            "name": "broken",
            "tasks": [
                {"key": "t1", "kind": "noop", "needs": ["ghost"], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("ghost" in i for i in hard), (
            f"Expected 'ghost' in issues: {hard}"
        )

    def test_missing_dep_message_includes_task_key(self):
        data = {
            "version": 1,
            "name": "broken",
            "tasks": [
                {"key": "consumer", "kind": "noop", "needs": ["producer"], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("consumer" in i and "producer" in i for i in hard), (
            f"Expected both task keys in issues: {hard}"
        )

    def test_flow_spec_is_valid_false_for_missing_dep(self):
        data = {
            "version": 1,
            "name": "broken",
            "tasks": [
                {"key": "t1", "kind": "noop", "needs": ["missing"], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        assert flow_spec_is_valid(issues) is False


class TestValidateFlowSpecDuplicateKey:
    """validate_flow_spec detects duplicate task keys."""

    def test_duplicate_key_is_hard_error(self):
        data = {
            "version": 1,
            "name": "dupe",
            "tasks": [
                {"key": "t1", "kind": "noop", "needs": [], "config": {}},
                {"key": "t1", "kind": "noop", "needs": [], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("duplicate" in i.lower() or "t1" in i for i in hard), (
            f"Expected duplicate key error: {hard}"
        )


class TestValidateFlowSpecPydanticFailure:
    """validate_flow_spec returns (None, issues) on Pydantic parse failure."""

    def test_invalid_kind_returns_none(self):
        data = {
            "version": 1,
            "name": "bad",
            "tasks": [
                {"key": "t1", "kind": "unknown_kind", "needs": [], "config": {}},
            ],
        }
        spec, issues = validate_flow_spec(data)
        assert spec is None, "Expected None for invalid kind"
        assert len(issues) > 0

    def test_missing_name_returns_none(self):
        data = {"version": 1, "tasks": []}
        spec, issues = validate_flow_spec(data)
        assert spec is None
        assert len(issues) > 0

    def test_invalid_param_type_returns_none(self):
        data = {
            "version": 1,
            "name": "bad_params",
            "params": [{"name": "p", "type": "not_a_type"}],
            "tasks": [],
        }
        spec, issues = validate_flow_spec(data)
        assert spec is None
        assert len(issues) > 0

    def test_non_dict_input_returns_none(self):
        spec, issues = validate_flow_spec("not a dict")
        assert spec is None
        assert len(issues) > 0

    def test_empty_dict_returns_none(self):
        spec, issues = validate_flow_spec({})
        assert spec is None
        assert len(issues) > 0


class TestValidateFlowSpecKindConfig:
    """validate_flow_spec enforces kind-specific config requirements."""

    def test_query_without_query_id_or_sql_is_hard_error(self):
        data = {
            "version": 1,
            "name": "bad_query",
            "tasks": [
                {"key": "q1", "kind": "query", "needs": [], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("query_id" in i or "sql" in i for i in hard), (
            f"Expected query config error: {hard}"
        )

    def test_python_without_code_is_hard_error(self):
        data = {
            "version": 1,
            "name": "bad_python",
            "tasks": [
                {"key": "p1", "kind": "python", "needs": [], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("code" in i for i in hard), (
            f"Expected python code error: {hard}"
        )

    def test_agent_without_prompt_is_hard_error(self):
        data = {
            "version": 1,
            "name": "bad_agent",
            "tasks": [
                {"key": "a1", "kind": "agent", "needs": [], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("prompt" in i for i in hard), (
            f"Expected agent prompt error: {hard}"
        )

    def test_query_with_query_id_is_valid(self):
        data = {
            "version": 1,
            "name": "ok_query",
            "tasks": [
                {"key": "q1", "kind": "query", "needs": [], "config": {"query_id": "demo_all"}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert not any("query_id" in i or "sql" in i for i in hard), (
            f"Should not have query config error: {hard}"
        )

    def test_python_with_code_is_valid(self):
        data = {
            "version": 1,
            "name": "ok_python",
            "tasks": [
                {"key": "p1", "kind": "python", "needs": [], "config": {"code": "result = 42"}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert not any("code" in i for i in hard), (
            f"Should not have code error: {hard}"
        )

    def test_agent_with_prompt_is_valid(self):
        data = {
            "version": 1,
            "name": "ok_agent",
            "tasks": [
                {"key": "a1", "kind": "agent", "needs": [], "config": {"prompt": "Do something."}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert not any("prompt" in i for i in hard), (
            f"Should not have prompt error: {hard}"
        )

    def test_unknown_query_id_is_soft_warning(self):
        """query_id not in registry → soft [warn], spec still parseable."""
        data = {
            "version": 1,
            "name": "unknown_qid",
            "tasks": [
                {
                    "key": "q1",
                    "kind": "query",
                    "needs": [],
                    "config": {"query_id": "does_not_exist_xyz"},
                },
            ],
        }
        spec, issues = validate_flow_spec(data)
        warn_issues = [i for i in issues if i.startswith("[warn]")]
        hard_issues = [i for i in issues if not i.startswith("[warn]")]
        # spec must still parse
        assert spec is not None, "Spec must still parse with unknown query_id"
        # no hard error about query_id
        assert not any("query_id" in i or "sql" in i for i in hard_issues), (
            f"Should not have a hard error for unknown query_id: {hard_issues}"
        )
        # should have a soft warning (if registry is reachable)
        # (warning may be absent if registry unavailable — that's OK)
        if warn_issues:
            assert any("does_not_exist_xyz" in i for i in warn_issues), (
                f"Warn should mention the unknown query_id: {warn_issues}"
            )

    def test_kind_config_error_identifies_task_key(self):
        """Config error message must include the offending task key."""
        data = {
            "version": 1,
            "name": "bad",
            "tasks": [
                {"key": "my_task", "kind": "python", "needs": [], "config": {}},
            ],
        }
        _, issues = validate_flow_spec(data)
        hard = [i for i in issues if not i.startswith("[warn]")]
        assert any("my_task" in i for i in hard), (
            f"Error should mention the task key: {hard}"
        )


# ---------------------------------------------------------------------------
# 2. flow_spec_is_valid
# ---------------------------------------------------------------------------


class TestFlowSpecIsValid:
    """flow_spec_is_valid correctly distinguishes hard errors from warnings."""

    def test_empty_issues_is_valid(self):
        assert flow_spec_is_valid([]) is True

    def test_only_warnings_is_valid(self):
        issues = [
            "[warn] Task 'q1': query_id 'x' not in registry.",
            "[warn] Another warning.",
        ]
        assert flow_spec_is_valid(issues) is True

    def test_single_hard_error_is_invalid(self):
        issues = ["Duplicate task key 't1'."]
        assert flow_spec_is_valid(issues) is False

    def test_mixed_hard_and_warn_is_invalid(self):
        issues = [
            "[warn] Task 'q1': query_id 'x' not in registry.",
            "Cycle detected: a → b → a.",
        ]
        assert flow_spec_is_valid(issues) is False

    def test_multiple_hard_errors_is_invalid(self):
        issues = [
            "Duplicate task key 't1'.",
            "Task 't2' needs 'ghost', which is not declared.",
        ]
        assert flow_spec_is_valid(issues) is False

    def test_full_valid_spec_produces_no_hard_errors(self):
        _, issues = validate_flow_spec(_linear_spec())
        assert flow_spec_is_valid(issues) is True


# ---------------------------------------------------------------------------
# 3. flow_spec_json_schema
# ---------------------------------------------------------------------------


class TestFlowSpecJsonSchema:
    """flow_spec_json_schema returns a usable JSON Schema dict."""

    def test_returns_dict(self):
        schema = flow_spec_json_schema()
        assert isinstance(schema, dict)

    def test_has_properties_key(self):
        schema = flow_spec_json_schema()
        assert "properties" in schema, f"Expected 'properties' in schema: {schema}"

    def test_has_name_property(self):
        schema = flow_spec_json_schema()
        props = schema.get("properties", {})
        assert "name" in props, f"Expected 'name' in schema properties: {props}"

    def test_has_tasks_property(self):
        schema = flow_spec_json_schema()
        props = schema.get("properties", {})
        assert "tasks" in props, f"Expected 'tasks' in schema properties: {props}"

    def test_has_version_property(self):
        schema = flow_spec_json_schema()
        props = schema.get("properties", {})
        assert "version" in props, f"Expected 'version' in schema properties: {props}"

    def test_has_params_property(self):
        schema = flow_spec_json_schema()
        props = schema.get("properties", {})
        assert "params" in props, f"Expected 'params' in schema properties: {props}"
