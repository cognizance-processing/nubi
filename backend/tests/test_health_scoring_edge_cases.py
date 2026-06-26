"""Edge-case tests for app/health/scoring.py (was at 95%; target remaining branches).

Missing lines (5 uncovered):
- 116: _normalise_weights returns {} when total==0
- 122: _grade returns "B" (75 <= score < 90)
- 126: _grade returns "C" (60 <= score < 75)
- 128: _grade returns "D" (40 <= score < 60)
- 140: _dim_status returns "fail" (score < 0.6)
"""

from __future__ import annotations

import pytest

from app.health.scoring import (
    DEFAULT_WEIGHTS,
    _dim_status,
    _grade,
    _normalise_weights,
    compute_health_score,
)


# ---------------------------------------------------------------------------
# _normalise_weights
# ---------------------------------------------------------------------------

class TestNormaliseWeights:
    def test_empty_known_dims_returns_empty_dict(self):
        """All dims unknown → no known weight → returns {}."""
        result = _normalise_weights({"freshness": 0.5}, known_dims=set())
        assert result == {}

    def test_zero_weight_known_dims_returns_empty_dict(self):
        """Weights of zero for known dims → total=0 → returns {}."""
        result = _normalise_weights(
            {"freshness": 0.0, "completeness": 0.0},
            known_dims={"freshness", "completeness"},
        )
        assert result == {}

    def test_single_known_dim_normalised_to_1(self):
        result = _normalise_weights({"freshness": 0.5}, known_dims={"freshness"})
        assert abs(result["freshness"] - 1.0) < 1e-9

    def test_two_known_dims_sum_to_1(self):
        result = _normalise_weights(
            {"freshness": 0.5, "completeness": 0.3},
            known_dims={"freshness", "completeness"},
        )
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_unknown_dim_excluded_from_result(self):
        result = _normalise_weights(
            {"freshness": 0.5, "completeness": 0.3, "availability": 0.2},
            known_dims={"freshness", "completeness"},
        )
        assert "availability" not in result


# ---------------------------------------------------------------------------
# _grade
# ---------------------------------------------------------------------------

class TestGrade:
    def test_none_returns_unknown(self):
        assert _grade(None) == "unknown"

    def test_100_returns_A(self):
        assert _grade(100.0) == "A"

    def test_90_exactly_returns_A(self):
        assert _grade(90.0) == "A"

    def test_89_returns_B(self):
        assert _grade(89.9) == "B"

    def test_75_exactly_returns_B(self):
        assert _grade(75.0) == "B"

    def test_74_returns_C(self):
        assert _grade(74.9) == "C"

    def test_60_exactly_returns_C(self):
        assert _grade(60.0) == "C"

    def test_59_returns_D(self):
        assert _grade(59.9) == "D"

    def test_40_exactly_returns_D(self):
        assert _grade(40.0) == "D"

    def test_39_returns_F(self):
        assert _grade(39.9) == "F"

    def test_0_returns_F(self):
        assert _grade(0.0) == "F"


# ---------------------------------------------------------------------------
# _dim_status
# ---------------------------------------------------------------------------

class TestDimStatus:
    def test_none_returns_unknown(self):
        assert _dim_status(None) == "unknown"

    def test_1_0_returns_ok(self):
        assert _dim_status(1.0) == "ok"

    def test_0_9_exactly_returns_ok(self):
        assert _dim_status(0.9) == "ok"

    def test_0_89_returns_warn(self):
        assert _dim_status(0.89) == "warn"

    def test_0_6_exactly_returns_warn(self):
        assert _dim_status(0.6) == "warn"

    def test_0_59_returns_fail(self):
        assert _dim_status(0.59) == "fail"

    def test_0_0_returns_fail(self):
        assert _dim_status(0.0) == "fail"


# ---------------------------------------------------------------------------
# compute_health_score edge cases (correctness + grade coverage)
# ---------------------------------------------------------------------------

class TestComputeHealthScoreEdgeCases:
    """These drive the grade branches B/C/D/F that weren't covered before."""

    def test_partial_success_run_history_yields_grade_B(self):
        """~80% success → score around 80 → grade B."""
        history = [{"status": "success"}] * 8 + [{"status": "failed"}] * 2
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="fresh",
            run_history=history,
            # all weights equal so score = 100 * (1.0 + 0.8 + 1.0) / 3 = ~93.3 → A
            # Use custom weights to make score fall in B range
            weights={"freshness": 0.0, "completeness": 1.0, "availability": 0.0},
        )
        # 8/10 success rate = 0.8 → completeness is the only known dim → score = 80.0 → B
        assert result.grade == "B"
        assert result.score == 80.0

    def test_low_success_rate_yields_grade_C(self):
        """~65% success → score 65 → grade C."""
        history = [{"status": "success"}] * 13 + [{"status": "failed"}] * 7
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="unknown",  # unknown → excluded
            run_history=history,
            weights={"freshness": 0.0, "completeness": 1.0, "availability": 0.0},
        )
        # 13/20 = 0.65 → score 65.0 → C
        assert result.grade == "C"

    def test_very_low_success_rate_yields_grade_D(self):
        """~50% success → score 50 → grade D."""
        history = [{"status": "success"}] * 10 + [{"status": "failed"}] * 10
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="unknown",
            run_history=history,
            weights={"freshness": 0.0, "completeness": 1.0, "availability": 0.0},
        )
        assert result.grade == "D"
        assert result.score == 50.0

    def test_stale_and_all_failed_yields_grade_F(self):
        """Stale freshness + zero success rate → very low score → F."""
        history = [{"status": "failed"}] * 5
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="stale",
            run_history=history,
        )
        assert result.grade == "F"
        assert result.score is not None
        assert result.score < 40.0

    def test_all_dims_unknown_returns_none_score(self):
        """freshness=unknown + no run history → all dims unknown → score=None, grade=unknown."""
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="unknown",
            run_history=[],
        )
        assert result.score is None
        assert result.grade == "unknown"

    def test_custom_weights_are_normalised(self):
        """Weights that don't sum to 1.0 are normalised internally."""
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="fresh",
            run_history=[{"status": "success"}],
            weights={"freshness": 2.0, "completeness": 2.0, "availability": 2.0},
        )
        # With fresh + all success → all dims = 1.0 → score = 100
        assert result.score == 100.0

    def test_run_history_window_capped_at_20(self):
        """Only the last 20 runs are considered for completeness/availability."""
        # First 30 are failures, last 20 are successes
        old_failures = [{"status": "failed"}] * 30
        recent_success = [{"status": "success"}] * 20
        history = old_failures + recent_success
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="unknown",
            run_history=history,
            weights={"freshness": 0.0, "completeness": 1.0, "availability": 0.0},
        )
        # Window of 20 = all successes → 100.0
        assert result.score == 100.0

    def test_succeeded_status_counted_as_success(self):
        """The 'succeeded' status alias is accepted as success."""
        history = [{"status": "succeeded"}, {"status": "succeeded"}]
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="unknown",
            run_history=history,
            weights={"freshness": 0.0, "completeness": 1.0, "availability": 0.0},
        )
        assert result.score == 100.0

    def test_freshness_infers_availability_when_no_history(self):
        """Freshness 'fresh' or 'stale' infers availability=1 when no run history."""
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="stale",
            run_history=[],
        )
        avail = next(d for d in result.dimensions if d.name == "availability")
        assert avail.score == 1.0

    def test_reasons_list_has_three_entries(self):
        """reasons[] always has one entry per dimension (3 total)."""
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="fresh",
            run_history=[{"status": "success"}],
        )
        assert len(result.reasons) == 3

    def test_weights_used_keys_match_expected(self):
        result = compute_health_score(
            dataset_key="ds",
            freshness_status="fresh",
            run_history=[],
        )
        assert set(result.weights_used.keys()) == {"freshness", "completeness", "availability"}

    def test_dataset_key_present_in_result(self):
        result = compute_health_score(
            dataset_key="my_dataset",
            freshness_status="unknown",
            run_history=[],
        )
        assert result.dataset_key == "my_dataset"
