"""Unit tests for the map, branch, and map_collect handlers.

Coverage
--------
1. handle_map
   a. Resolves item_expr from ctx.inputs and returns __map_items__.
   b. Resolves item_expr from ctx.flow_params.
   c. Raises ValueError when item_expr resolves to non-list.
   d. Raises ValueError when item count exceeds max_map_size.
   e. Raises ValueError when item_expr is empty.

2. handle_branch
   a. First matching condition is taken; returns branch_taken/branch_index/__branch_next__.
   b. Second condition taken when first is false.
   c. Default branch taken when no condition matches and default is set.
   d. Raises ValueError when no condition matches and default is empty (Q1).
   e. Literal True/False conditions (via ast.literal_eval path).
   f. Skips condition with empty 'when' expression.

3. handle_map_collect
   a. Returns {"items": [...], "item_count": N} from upstream map result.
   b. Raises ValueError when 'source' is not in config.
   c. Raises ValueError when source key is not in ctx.inputs.
   d. Handles map result without 'items' key (wraps as single-item list).

4. registry integration
   a. get_task_kind_registry() includes 'map', 'branch', 'map_collect'.
   b. reset_for_tests() re-bootstraps with the new kinds.
"""

from __future__ import annotations

import pytest

from app.flows.executor import TaskContext
from app.flows.handlers.branch import handle_branch
from app.flows.handlers.map import handle_map
from app.flows.handlers.map_collect import handle_map_collect
from app.flows.registry import get_task_kind_registry, reset_for_tests

# Stub claims — handlers do not inspect these.
_CLAIMS: dict = {"sub": "user-1", "org_id": "org-1"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(
    inputs: dict | None = None,
    flow_params: dict | None = None,
) -> TaskContext:
    """Build a minimal TaskContext for testing."""
    return TaskContext(
        flow_params=flow_params or {},
        inputs=inputs or {},
    )


# ---------------------------------------------------------------------------
# 1. handle_map
# ---------------------------------------------------------------------------


class TestHandleMap:
    def test_resolves_from_inputs(self) -> None:
        ctx = _ctx(inputs={"source": {"rows": [{"id": 1}, {"id": 2}]}})
        result = handle_map(
            config={"item_expr": "{{ inputs.source.rows }}", "body": []},
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["__map_items__"] == [{"id": 1}, {"id": 2}]
        assert result["item_count"] == 2

    def test_resolves_from_flow_params(self) -> None:
        ctx = _ctx(flow_params={"regions": ["north", "south", "east"]})
        result = handle_map(
            config={"item_expr": "{{ params.regions }}", "body": []},
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["__map_items__"] == ["north", "south", "east"]
        assert result["item_count"] == 3

    def test_raises_when_not_list(self) -> None:
        ctx = _ctx(inputs={"source": {"count": 42}})
        with pytest.raises(ValueError, match="must resolve to a list"):
            handle_map(
                config={"item_expr": "{{ inputs.source.count }}", "body": []},
                ctx=ctx,
                claims=_CLAIMS,
            )

    def test_raises_when_exceeds_max_map_size(self) -> None:
        ctx = _ctx(inputs={"src": {"items": list(range(10))}})
        with pytest.raises(ValueError, match="exceeds max_map_size"):
            handle_map(
                config={
                    "item_expr": "{{ inputs.src.items }}",
                    "max_map_size": 5,
                    "body": [],
                },
                ctx=ctx,
                claims=_CLAIMS,
            )

    def test_raises_when_item_expr_empty(self) -> None:
        ctx = _ctx()
        with pytest.raises(ValueError, match="'item_expr' must be set"):
            handle_map(
                config={"item_expr": "", "body": []},
                ctx=ctx,
                claims=_CLAIMS,
            )

    def test_empty_list_allowed(self) -> None:
        ctx = _ctx(inputs={"source": {"rows": []}})
        result = handle_map(
            config={"item_expr": "{{ inputs.source.rows }}", "body": []},
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["__map_items__"] == []
        assert result["item_count"] == 0

    def test_respects_max_map_size_default_1000(self) -> None:
        ctx = _ctx(inputs={"src": {"data": list(range(1000))}})
        result = handle_map(
            config={"item_expr": "{{ inputs.src.data }}", "body": []},
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["item_count"] == 1000

    def test_1001_items_raises(self) -> None:
        ctx = _ctx(inputs={"src": {"data": list(range(1001))}})
        with pytest.raises(ValueError, match="exceeds max_map_size"):
            handle_map(
                config={"item_expr": "{{ inputs.src.data }}", "body": []},
                ctx=ctx,
                claims=_CLAIMS,
            )


# ---------------------------------------------------------------------------
# 2. handle_branch
# ---------------------------------------------------------------------------


class TestHandleBranch:
    def test_first_condition_taken(self) -> None:
        ctx = _ctx(inputs={"classify": {"label": "high_value"}})
        result = handle_branch(
            config={
                "conditions": [
                    {
                        "when": "{{ inputs.classify.label }} == 'high_value'",
                        "next": ["enrich"],
                    },
                    {
                        "when": "{{ inputs.classify.label }} == 'low_value'",
                        "next": ["archive"],
                    },
                ],
                "default": [],
            },
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["branch_taken"] == "condition_0"
        assert result["branch_index"] == 0
        assert result["__branch_next__"] == ["enrich"]

    def test_second_condition_taken(self) -> None:
        ctx = _ctx(inputs={"classify": {"label": "low_value"}})
        result = handle_branch(
            config={
                "conditions": [
                    {
                        "when": "{{ inputs.classify.label }} == 'high_value'",
                        "next": ["enrich"],
                    },
                    {
                        "when": "{{ inputs.classify.label }} == 'low_value'",
                        "next": ["archive"],
                    },
                ],
                "default": [],
            },
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["branch_taken"] == "condition_1"
        assert result["branch_index"] == 1
        assert result["__branch_next__"] == ["archive"]

    def test_default_taken_when_no_match(self) -> None:
        ctx = _ctx(inputs={"classify": {"label": "unknown"}})
        result = handle_branch(
            config={
                "conditions": [
                    {"when": "{{ inputs.classify.label }} == 'high_value'", "next": ["enrich"]},
                ],
                "default": ["log_task"],
            },
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["branch_taken"] == "default"
        assert result["branch_index"] == -1
        assert result["__branch_next__"] == ["log_task"]

    def test_raises_when_no_match_and_no_default(self) -> None:
        """Q1: else_ is optional — absent default → ValueError (engine marks failed)."""
        ctx = _ctx(inputs={"classify": {"label": "unknown"}})
        with pytest.raises(ValueError, match="no condition matched"):
            handle_branch(
                config={
                    "conditions": [
                        {"when": "{{ inputs.classify.label }} == 'high_value'", "next": ["enrich"]},
                    ],
                    # No 'default' key at all
                },
                ctx=ctx,
                claims=_CLAIMS,
            )

    def test_literal_true_condition(self) -> None:
        """ast.literal_eval path: template resolves to literal 'True'."""
        ctx = _ctx(flow_params={"flag": "True"})
        result = handle_branch(
            config={
                "conditions": [
                    {"when": "{{ params.flag }}", "next": ["task_a"]},
                    {"when": "False", "next": ["task_b"]},
                ],
                "default": [],
            },
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["branch_taken"] == "condition_0"
        assert result["__branch_next__"] == ["task_a"]

    def test_literal_false_falls_through_to_next(self) -> None:
        ctx = _ctx(flow_params={"flag": "False"})
        result = handle_branch(
            config={
                "conditions": [
                    {"when": "{{ params.flag }}", "next": ["task_a"]},
                    {"when": "True", "next": ["task_b"]},
                ],
                "default": [],
            },
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["branch_taken"] == "condition_1"
        assert result["__branch_next__"] == ["task_b"]

    def test_skips_condition_with_empty_when(self) -> None:
        """Conditions with empty 'when' are skipped; default is used."""
        ctx = _ctx()
        result = handle_branch(
            config={
                "conditions": [
                    {"when": "", "next": ["task_a"]},
                ],
                "default": ["fallback"],
            },
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["branch_taken"] == "default"
        assert result["__branch_next__"] == ["fallback"]

    def test_raises_when_no_conditions_and_no_default(self) -> None:
        ctx = _ctx()
        with pytest.raises(ValueError, match="no condition matched"):
            handle_branch(
                config={"conditions": [], "default": []},
                ctx=ctx,
                claims=_CLAIMS,
            )

    def test_comparison_with_int_value(self) -> None:
        ctx = _ctx(inputs={"score": {"value": 90}})
        result = handle_branch(
            config={
                "conditions": [
                    {"when": "int('{{ inputs.score.value }}') > 80", "next": ["high"]},
                    {"when": "True", "next": ["low"]},
                ],
                "default": [],
            },
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["branch_taken"] == "condition_0"
        assert result["__branch_next__"] == ["high"]


# ---------------------------------------------------------------------------
# 3. handle_map_collect
# ---------------------------------------------------------------------------


class TestHandleMapCollect:
    def test_returns_items_from_map_result(self) -> None:
        ctx = _ctx(
            inputs={
                "my_map": {
                    "items": [{"index": 0, "result": {"v": 1}}, {"index": 1, "result": {"v": 2}}],
                    "item_count": 2,
                }
            }
        )
        result = handle_map_collect(
            config={"source": "my_map"},
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["item_count"] == 2
        assert len(result["items"]) == 2
        assert result["items"][0] == {"index": 0, "result": {"v": 1}}

    def test_raises_when_source_not_in_config(self) -> None:
        ctx = _ctx()
        with pytest.raises(ValueError, match="'source' must be set"):
            handle_map_collect(config={}, ctx=ctx, claims=_CLAIMS)

    def test_raises_when_source_not_in_inputs(self) -> None:
        ctx = _ctx(inputs={})
        with pytest.raises(ValueError, match="has no result"):
            handle_map_collect(
                config={"source": "missing_map"},
                ctx=ctx,
                claims=_CLAIMS,
            )

    def test_wraps_non_items_result_as_single_item(self) -> None:
        """If map result has no 'items' key, wrap it as a single-item list."""
        ctx = _ctx(inputs={"my_map": {"row_count": 5, "rows": [{"id": 1}]}})
        result = handle_map_collect(
            config={"source": "my_map"},
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["item_count"] == 1
        assert result["items"] == [{"row_count": 5, "rows": [{"id": 1}]}]

    def test_empty_items_list(self) -> None:
        ctx = _ctx(inputs={"my_map": {"items": [], "item_count": 0}})
        result = handle_map_collect(
            config={"source": "my_map"},
            ctx=ctx,
            claims=_CLAIMS,
        )
        assert result["item_count"] == 0
        assert result["items"] == []


# ---------------------------------------------------------------------------
# 4. registry integration
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_new_kinds_present_in_registry(self) -> None:
        registry = get_task_kind_registry()
        kinds = set(registry.all().keys())
        assert "map" in kinds
        assert "branch" in kinds
        assert "map_collect" in kinds

    def test_reset_for_tests_re_bootstraps_new_kinds(self) -> None:
        reset_for_tests()
        registry = get_task_kind_registry()
        kinds = set(registry.all().keys())
        assert "map" in kinds
        assert "branch" in kinds
        assert "map_collect" in kinds

    def test_map_handler_is_callable(self) -> None:
        registry = get_task_kind_registry()
        handler = registry.get("map")
        assert callable(handler)

    def test_branch_handler_is_callable(self) -> None:
        registry = get_task_kind_registry()
        handler = registry.get("branch")
        assert callable(handler)

    def test_map_collect_handler_is_callable(self) -> None:
        registry = get_task_kind_registry()
        handler = registry.get("map_collect")
        assert callable(handler)

    def test_map_via_registry_dispatch(self) -> None:
        """End-to-end: dispatch through registry to handle_map."""
        registry = get_task_kind_registry()
        handler = registry.get("map")
        ctx = _ctx(inputs={"src": {"items": [1, 2, 3]}})
        result = handler(
            {"item_expr": "{{ inputs.src.items }}", "body": []},
            ctx,
            _CLAIMS,
        )
        assert result["item_count"] == 3
        assert result["__map_items__"] == [1, 2, 3]

    def test_branch_via_registry_dispatch(self) -> None:
        """End-to-end: dispatch through registry to handle_branch."""
        registry = get_task_kind_registry()
        handler = registry.get("branch")
        ctx = _ctx(flow_params={})
        result = handler(
            {
                "conditions": [{"when": "True", "next": ["go"]}],
                "default": [],
            },
            ctx,
            _CLAIMS,
        )
        assert result["branch_taken"] == "condition_0"
        assert result["__branch_next__"] == ["go"]

    def test_map_collect_via_registry_dispatch(self) -> None:
        """End-to-end: dispatch through registry to handle_map_collect."""
        registry = get_task_kind_registry()
        handler = registry.get("map_collect")
        ctx = _ctx(inputs={"fan_out": {"items": ["a", "b"], "item_count": 2}})
        result = handler({"source": "fan_out"}, ctx, _CLAIMS)
        assert result["item_count"] == 2
        assert result["items"] == ["a", "b"]
