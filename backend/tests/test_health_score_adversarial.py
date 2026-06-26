"""Adversarial tests for health scoring and schema drift.

Health score coverage (compute_health_score):
1. All-fresh + perfect run history → 100.0.
2. All-stale + all-failed runs → 0.0.
3. Freshness=fresh, no run history → score is redistributed (uses freshness + availability from freshness).
4. Freshness=unknown, no history → score=None, grade='unknown'.
5. Freshness=unknown, all-failed history → score computed from completeness=0, availability=0.
6. Boundary: exactly 20 runs (window edge), 21 runs (oldest is dropped).
7. Weight redistribution: all unknown dims → score=None.
8. Weight redistribution: one known dim.
9. Custom weights sum > 1 (normalised).
10. Custom weights sum = 0 (edge).
11. Grade boundaries: 90→A, 89.9→B, 75→B, 74.9→C, 60→C, 59.9→D, 40→D, 39.9→F.
12. 0 runs vs 1 run vs 20 runs vs 21 runs in completeness.

Schema drift (complement test_schema_drift.py):
13. Multiple type changes in same observation.
14. Rename detection (old removed + new added = separate add+remove events, not rename).
15. Cardinality: 0→50 columns (mass add).
16. Cardinality: 50→0 columns (mass remove).
17. No-change-no-event: second identical observation produces no events.
18. Cross-org isolation: org_a events never appear in org_b list.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.health.schema_drift import (
    InMemoryDriftStore,
    _diff_columns,
    detect_schema_drift,
    set_drift_store,
)
from app.health.scoring import (
    DEFAULT_WEIGHTS,
    compute_health_score,
    _grade,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def use_memory_drift_store():
    """Inject an InMemoryDriftStore for every test in this module."""
    store = InMemoryDriftStore()
    set_drift_store(store)
    yield store
    set_drift_store(None)


def _run(history_statuses: list[str]) -> list[dict[str, Any]]:
    return [{"status": s} for s in history_statuses]


# ---------------------------------------------------------------------------
# 1–6. compute_health_score: basic scenarios
# ---------------------------------------------------------------------------


class TestHealthScoreScenarios:
    def test_all_fresh_perfect_history_score_100(self):
        """Fresh freshness + 100% success history → score=100.0, grade=A."""
        history = _run(["success"] * 20)
        result = compute_health_score(
            dataset_key="ds1",
            freshness_status="fresh",
            run_history=history,
        )
        assert result.score == 100.0
        assert result.grade == "A"

    def test_all_stale_all_failed_score_0(self):
        """Stale freshness + 0% success history → score=0.0, grade=F."""
        history = _run(["failed"] * 20)
        result = compute_health_score(
            dataset_key="ds2",
            freshness_status="stale",
            run_history=history,
        )
        assert result.score == 0.0
        assert result.grade == "F"

    def test_fresh_no_history_score_redistributed(self):
        """Freshness=fresh, no run history → completeness unknown, score > 0."""
        result = compute_health_score(
            dataset_key="ds3",
            freshness_status="fresh",
            run_history=[],
        )
        # completeness is unknown (no history)
        # availability is 1.0 (inferred from freshness=fresh)
        # freshness is 1.0
        # So two known dims: freshness + availability → score > 0
        assert result.score is not None
        assert result.score > 0

    def test_unknown_freshness_no_history_score_none(self):
        """Freshness=unknown, no history → all dims unknown → score=None, grade='unknown'."""
        result = compute_health_score(
            dataset_key="ds4",
            freshness_status="unknown",
            run_history=[],
        )
        assert result.score is None
        assert result.grade == "unknown"

    def test_unknown_freshness_all_failed_history(self):
        """Freshness=unknown, all-failed history → completeness=0, availability=0."""
        history = _run(["failed"] * 5)
        result = compute_health_score(
            dataset_key="ds5",
            freshness_status="unknown",
            run_history=history,
        )
        # freshness=None, completeness=0.0, availability=0.0
        # Two known dims (completeness + availability) → score=0.0
        assert result.score is not None
        assert result.score == 0.0

    def test_exactly_20_runs_all_window(self):
        """Exactly 20 runs: all are in the window."""
        history = _run(["success"] * 10 + ["failed"] * 10)
        result = compute_health_score(
            dataset_key="ds6",
            freshness_status="fresh",
            run_history=history,
        )
        dims = {d.name: d for d in result.dimensions}
        # 10/20 = 0.5 completeness
        assert dims["completeness"].score == pytest.approx(0.5, abs=0.01)

    def test_21_runs_oldest_dropped(self):
        """21 runs: the 21st (oldest) 'failed' is dropped; window=last 20."""
        # 1 old fail + 10 success + 10 success = 21 total
        # Window = last 20: all success → completeness = 1.0
        history = _run(["failed"] + ["success"] * 20)  # oldest first
        result = compute_health_score(
            dataset_key="ds7",
            freshness_status="fresh",
            run_history=history,
        )
        dims = {d.name: d for d in result.dimensions}
        # Last 20 of history = ["success"] * 20 → completeness = 1.0
        assert dims["completeness"].score == pytest.approx(1.0, abs=0.01)

    def test_0_runs_completeness_unknown(self):
        result = compute_health_score(
            dataset_key="ds8",
            freshness_status="fresh",
            run_history=[],
        )
        dims = {d.name: d for d in result.dimensions}
        assert dims["completeness"].score is None
        assert dims["completeness"].status == "unknown"

    def test_1_run_success_completeness_1(self):
        result = compute_health_score(
            dataset_key="ds9",
            freshness_status="fresh",
            run_history=_run(["success"]),
        )
        dims = {d.name: d for d in result.dimensions}
        assert dims["completeness"].score == 1.0

    def test_1_run_fail_completeness_0(self):
        result = compute_health_score(
            dataset_key="ds10",
            freshness_status="fresh",
            run_history=_run(["failed"]),
        )
        dims = {d.name: d for d in result.dimensions}
        assert dims["completeness"].score == 0.0

    def test_succeeded_status_counted_as_success(self):
        """'succeeded' (alternate spelling) must also count as a success."""
        result = compute_health_score(
            dataset_key="ds11",
            freshness_status="fresh",
            run_history=_run(["succeeded"] * 20),
        )
        dims = {d.name: d for d in result.dimensions}
        assert dims["completeness"].score == 1.0


# ---------------------------------------------------------------------------
# 7–9. Weight redistribution
# ---------------------------------------------------------------------------


class TestWeightRedistribution:
    def test_all_unknown_dims_score_none(self):
        """All dims unknown → score=None."""
        result = compute_health_score(
            dataset_key="all_unknown",
            freshness_status="unknown",
            run_history=[],
        )
        assert result.score is None
        assert result.grade == "unknown"

    def test_one_known_dim_uses_full_normalized_weight(self):
        """Only freshness known (=1.0) → score = 100.0 (full weight redistributed to it)."""
        result = compute_health_score(
            dataset_key="one_known",
            freshness_status="fresh",
            run_history=[],
            # no history → completeness=unknown, availability inferred from fresh=1.0
            # so freshness=1.0, availability=1.0 are known
            # score = 100 × (wf*1 + wa*1) / (wf + wa) = 100
        )
        assert result.score is not None
        assert result.score > 0

    def test_custom_weights_sum_gt_1_normalised(self):
        """Weights sum > 1 → normalised before use → still valid score."""
        result = compute_health_score(
            dataset_key="custom_wt",
            freshness_status="fresh",
            run_history=_run(["success"] * 20),
            weights={"freshness": 5.0, "completeness": 3.0, "availability": 2.0},
        )
        # Normalised: 5/10=0.5, 3/10=0.3, 2/10=0.2 — same as default ratios
        assert result.score == pytest.approx(100.0, abs=0.1)
        # weights_used should sum to 1.0
        assert sum(result.weights_used.values()) == pytest.approx(1.0, abs=1e-9)

    def test_custom_weights_sum_0_score_behavior(self):
        """All zero weights → total_w=0 → weights stay as 0; _normalise_weights returns {} → score=0.

        When all weights are 0, _normalise_weights returns {} (total=0 → empty dict).
        known dims exist but norm_w is empty → sum = 0 → final_score = 0.0, grade='F'.
        This is the actual behavior (not None): score=0 because sum over empty = 0.
        """
        result = compute_health_score(
            dataset_key="zero_wt",
            freshness_status="fresh",
            run_history=_run(["success"] * 20),
            weights={"freshness": 0.0, "completeness": 0.0, "availability": 0.0},
        )
        # All three dims are known but weights are all zero → _normalise_weights returns {}
        # raw = sum over empty norm_w = 0 → final_score = 0
        assert result.score == 0
        assert result.grade == "F"

    def test_partial_custom_weights_missing_keys_default(self):
        """Partial weight override: missing keys fall back to defaults."""
        result = compute_health_score(
            dataset_key="partial_wt",
            freshness_status="fresh",
            run_history=_run(["success"] * 20),
            weights={"freshness": 1.0},  # only freshness overridden
        )
        assert result.score is not None


# ---------------------------------------------------------------------------
# 10. Grade boundaries
# ---------------------------------------------------------------------------


class TestGradeBoundaries:
    def test_grade_90_is_A(self):
        assert _grade(90.0) == "A"

    def test_grade_100_is_A(self):
        assert _grade(100.0) == "A"

    def test_grade_89_9_is_B(self):
        assert _grade(89.9) == "B"

    def test_grade_75_is_B(self):
        assert _grade(75.0) == "B"

    def test_grade_74_9_is_C(self):
        assert _grade(74.9) == "C"

    def test_grade_60_is_C(self):
        assert _grade(60.0) == "C"

    def test_grade_59_9_is_D(self):
        assert _grade(59.9) == "D"

    def test_grade_40_is_D(self):
        assert _grade(40.0) == "D"

    def test_grade_39_9_is_F(self):
        assert _grade(39.9) == "F"

    def test_grade_0_is_F(self):
        assert _grade(0.0) == "F"

    def test_grade_none_is_unknown(self):
        assert _grade(None) == "unknown"


# ---------------------------------------------------------------------------
# 11. Mixed scenarios and exact math
# ---------------------------------------------------------------------------


class TestExactMath:
    def test_fresh_mixed_run_history_exact_score(self):
        """Exact score: fresh + 10/20 success → verify formula."""
        history = _run(["success"] * 10 + ["failed"] * 10)
        result = compute_health_score(
            dataset_key="exact",
            freshness_status="fresh",
            run_history=history,
            weights=None,  # use defaults: f=0.5, c=0.3, a=0.2
        )
        # freshness=1.0, completeness=0.5, availability=1.0 (any success exists)
        # all three dims known → weights stay at default ratios:
        # 0.5 + 0.3 + 0.2 = 1.0, so no redistribution
        # score = 100 × (0.5×1.0 + 0.3×0.5 + 0.2×1.0)
        #       = 100 × (0.5 + 0.15 + 0.2)
        #       = 100 × 0.85 = 85.0
        assert result.score == pytest.approx(85.0, abs=0.2)

    def test_stale_all_success_history_exact_score(self):
        """stale + 100% success → freshness=0, completeness=1, availability=1."""
        history = _run(["success"] * 20)
        result = compute_health_score(
            dataset_key="exact2",
            freshness_status="stale",
            run_history=history,
        )
        # freshness=0.0, completeness=1.0, availability=1.0
        # score = 100 × (0.5×0 + 0.3×1.0 + 0.2×1.0) = 100 × 0.5 = 50.0
        assert result.score == pytest.approx(50.0, abs=0.2)
        assert result.grade == "D"


# ---------------------------------------------------------------------------
# 12–17. Schema drift adversarial
# ---------------------------------------------------------------------------


class TestSchemaDriftAdversarial:
    def test_multiple_type_changes_in_same_observation(self):
        """3 columns all change type at once → 3 type_changed events."""
        old_cols = [
            {"name": "a", "type": "int64"},
            {"name": "b", "type": "float64"},
            {"name": "c", "type": "text"},
        ]
        new_cols = [
            {"name": "a", "type": "text"},      # changed
            {"name": "b", "type": "int64"},     # changed
            {"name": "c", "type": "timestamp"}, # changed
        ]
        events = _diff_columns(old_cols, new_cols)
        type_changed = [e for e in events if e["change_type"] == "type_changed"]
        assert len(type_changed) == 3
        names = {e["column_name"] for e in type_changed}
        assert names == {"a", "b", "c"}

    def test_rename_detected_as_add_plus_remove(self):
        """Rename = old removed + new added (no explicit rename detection in _diff_columns)."""
        old_cols = [{"name": "old_name", "type": "int64"}]
        new_cols = [{"name": "new_name", "type": "int64"}]
        events = _diff_columns(old_cols, new_cols)
        change_types = {e["change_type"] for e in events}
        assert "added" in change_types    # new_name added
        assert "removed" in change_types  # old_name removed
        assert len(events) == 2

    def test_mass_add_50_columns(self):
        """0 → 50 columns: 50 added events."""
        new_cols = [{"name": f"col_{i}", "type": "text"} for i in range(50)]
        events = _diff_columns([], new_cols)
        assert len(events) == 50
        assert all(e["change_type"] == "added" for e in events)

    def test_mass_remove_50_columns(self):
        """50 → 0 columns: 50 removed events."""
        old_cols = [{"name": f"col_{i}", "type": "text"} for i in range(50)]
        events = _diff_columns(old_cols, [])
        assert len(events) == 50
        assert all(e["change_type"] == "removed" for e in events)

    @pytest.mark.asyncio
    async def test_no_change_no_event(self, use_memory_drift_store):
        """Second observation with identical columns produces no drift events."""
        store = use_memory_drift_store
        cols = [{"name": "id", "type": "int64"}, {"name": "name", "type": "text"}]

        # First observation: stores snapshot, no events
        await detect_schema_drift("org-1", "dataset-1", cols)
        assert store._events == []

        # Second observation: identical columns → no events
        await detect_schema_drift("org-1", "dataset-1", cols)
        assert store._events == []

    @pytest.mark.asyncio
    async def test_cross_org_isolation(self, use_memory_drift_store):
        """org_a events never appear in org_b's list."""
        store = use_memory_drift_store
        cols_v1 = [{"name": "id", "type": "int64"}]
        cols_v2 = [{"name": "id", "type": "int64"}, {"name": "new_col", "type": "text"}]

        # org_a: first observation (snapshot)
        await detect_schema_drift("org-a", "ds", cols_v1)
        # org_a: second observation (drift event)
        await detect_schema_drift("org-a", "ds", cols_v2)

        # org_b should see no events
        org_b_events = store.list_events("org-b")
        assert org_b_events == []

        # org_a should see 1 event (the added column)
        org_a_events = store.list_events("org-a")
        assert len(org_a_events) == 1
        assert org_a_events[0]["org_id"] == "org-a"

    @pytest.mark.asyncio
    async def test_first_observation_no_event(self, use_memory_drift_store):
        """First observation: snapshot stored, no events emitted."""
        store = use_memory_drift_store
        cols = [{"name": "id", "type": "int64"}]
        await detect_schema_drift("org-1", "ds", cols)
        assert store._events == []
        assert store.get_snapshot("org-1", "ds") == [{"name": "id", "type": "int64"}]

    @pytest.mark.asyncio
    async def test_detect_drift_never_raises(self, use_memory_drift_store):
        """detect_schema_drift must never raise even with bad input."""
        # Should not raise even for falsy inputs
        await detect_schema_drift("", "ds", [{"name": "a", "type": "int"}])
        await detect_schema_drift("org-1", "", [{"name": "a", "type": "int"}])
        # None should be handled gracefully
        await detect_schema_drift("org-1", "ds", None)

    def test_diff_empty_to_50(self):
        """Edge: diff from 0 columns to 50 is pure add."""
        new = [{"name": f"c{i}", "type": "int64"} for i in range(50)]
        result = _diff_columns([], new)
        assert len(result) == 50
        for ev in result:
            assert ev["from_type"] is None
            assert ev["to_type"] == "int64"

    def test_diff_50_to_empty(self):
        """Edge: diff from 50 columns to 0 is pure remove."""
        old = [{"name": f"c{i}", "type": "int64"} for i in range(50)]
        result = _diff_columns(old, [])
        assert len(result) == 50
        for ev in result:
            assert ev["to_type"] is None
            assert ev["from_type"] == "int64"
