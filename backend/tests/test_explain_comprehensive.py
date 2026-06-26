from __future__ import annotations

"""Comprehensive edge-case tests for app/metrics/explain.py.

These tests extend (not replace) the existing test_metrics_explain.py coverage.
All functions under test are pure/sync; no async fixtures needed.
"""

import pytest
from app.metrics.explain import (
    MemberContribution,
    _safe_delta,
    _member_contributions,
    _explanatory_power,
    build_dimension_breakdown,
    build_explain_result,
)


# ---------------------------------------------------------------------------
# _safe_delta — edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current, comparison, expected",
    [
        (0.0, 0.0, 0.0),
        (0.0, None, 0.0),
        (None, 0.0, 0.0),
        (-5.0, None, -5.0),
        (None, -5.0, 5.0),
        (1e-10, 1e-10, 0.0),
        (1_000_000.0, 999_999.0, 1.0),
    ],
)
def test_safe_delta_parametrized(current, comparison, expected):
    assert _safe_delta(current, comparison) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _member_contributions — all members only in current period
# ---------------------------------------------------------------------------


def test_member_contributions_all_current_only():
    """When comparison is empty, every member has comparison=None and positive delta."""
    current = {"A": 100.0, "B": 200.0, "C": 50.0}
    comparison: dict = {}
    delta_total = 350.0  # sum of current
    members = _member_contributions(current, comparison, delta_total)

    assert len(members) == 3
    for m in members:
        assert m.comparison is None
        assert m.current is not None
        assert m.delta > 0
        assert m.direction == "up"

    # Sorted by abs(delta) descending: B=200, A=100, C=50
    assert members[0].member == "B"
    assert members[1].member == "A"
    assert members[2].member == "C"


# ---------------------------------------------------------------------------
# _member_contributions — all members only in comparison period
# ---------------------------------------------------------------------------


def test_member_contributions_all_comparison_only():
    """When current is empty, every member has current=None and negative delta."""
    current: dict = {}
    comparison = {"X": 10.0, "Y": 30.0}
    delta_total = -40.0
    members = _member_contributions(current, comparison, delta_total)

    assert len(members) == 2
    for m in members:
        assert m.current is None
        assert m.comparison is not None
        assert m.delta < 0
        assert m.direction == "down"

    # Sorted by abs(delta) desc: Y=-30, X=-10
    assert members[0].member == "Y"
    assert members[1].member == "X"


# ---------------------------------------------------------------------------
# _member_contributions — negative deltas (all members decline)
# ---------------------------------------------------------------------------


def test_member_contributions_all_decline_directions():
    current = {"A": 50.0, "B": 20.0}
    comparison = {"A": 80.0, "B": 60.0}
    delta_total = (50 + 20) - (80 + 60)  # -70
    members = _member_contributions(current, comparison, delta_total)

    for m in members:
        assert m.direction == "down"
        assert m.delta < 0

    # shares should be negative (delta negative, abs_total positive)
    for m in members:
        assert m.share < 0


def test_member_contributions_negative_delta_total_share_sign():
    """When delta_total is negative, shares are also negative."""
    current = {"A": 10.0}
    comparison = {"A": 100.0}
    delta_total = -90.0
    members = _member_contributions(current, comparison, delta_total)
    assert members[0].share == pytest.approx(-90.0 / 90.0)  # -1.0


# ---------------------------------------------------------------------------
# _member_contributions — delta_total == 0 (division guard)
# ---------------------------------------------------------------------------


def test_member_contributions_zero_delta_total_no_error():
    """When delta_total == 0, no ZeroDivisionError; all shares are 0.0."""
    current = {"A": 50.0, "B": 50.0}
    comparison = {"A": 50.0, "B": 50.0}
    members = _member_contributions(current, comparison, 0.0)
    for m in members:
        assert m.share == 0.0


def test_member_contributions_zero_delta_total_direction_still_computed():
    """Direction should still reflect individual member movement when delta_total=0."""
    # Member A went up, B went down by same amount — delta_total = 0
    current = {"A": 60.0, "B": 40.0}
    comparison = {"A": 50.0, "B": 50.0}
    delta_total = 0.0
    members = _member_contributions(current, comparison, delta_total)
    by_name = {m.member: m for m in members}
    assert by_name["A"].direction == "up"
    assert by_name["B"].direction == "down"


# ---------------------------------------------------------------------------
# _member_contributions — direction "flat" edge (exact zero delta)
# ---------------------------------------------------------------------------


def test_member_contributions_direction_flat_exact_zero():
    """A member with identical current and comparison gets direction='flat' and share=0."""
    current = {"A": 100.0, "B": 75.0}
    comparison = {"A": 100.0, "B": 50.0}
    delta_total = 25.0
    members = _member_contributions(current, comparison, delta_total)
    by_name = {m.member: m for m in members}
    assert by_name["A"].direction == "flat"
    assert by_name["A"].share == pytest.approx(0.0)
    assert by_name["B"].direction == "up"


# ---------------------------------------------------------------------------
# _explanatory_power — formula and cap
# ---------------------------------------------------------------------------


def test_explanatory_power_exact_formula():
    """Verify sum(|delta|) / |delta_total| without cap."""
    members = [
        MemberContribution("A", 30.0, 20.0, 10.0, 0.5, "up"),
        MemberContribution("B", 15.0, 5.0, 10.0, 0.5, "up"),
    ]
    # sum(|delta|) = 20, |delta_total| = 20
    result = _explanatory_power(members, 20.0)
    assert result == pytest.approx(1.0)


def test_explanatory_power_partial_coverage():
    """When top members cover only part of total movement."""
    members = [
        MemberContribution("A", None, None, 5.0, 0.5, "up"),
    ]
    result = _explanatory_power(members, 20.0)
    assert result == pytest.approx(5.0 / 20.0)


def test_explanatory_power_capped_at_one_offsetting():
    """Offsetting members can drive sum(|delta|) > |delta_total|; must be capped."""
    members = [
        MemberContribution("A", None, None, 50.0, 5.0, "up"),
        MemberContribution("B", None, None, -40.0, -4.0, "down"),
    ]
    # sum(|delta|) = 90, |delta_total| = 10 -> uncapped = 9.0 -> capped = 1.0
    result = _explanatory_power(members, 10.0)
    assert result == pytest.approx(1.0)


def test_explanatory_power_empty_members_list():
    """With an empty members list, result is 0.0 (sum = 0)."""
    result = _explanatory_power([], 100.0)
    assert result == pytest.approx(0.0)


def test_explanatory_power_near_zero_delta_total():
    """Returns 0.0 when |delta_total| < 1e-9."""
    members = [MemberContribution("A", 10.0, 10.0, 0.0, 0.0, "flat")]
    assert _explanatory_power(members, 1e-10) == 0.0
    assert _explanatory_power(members, 0.0) == 0.0


def test_explanatory_power_negative_delta_total():
    """_explanatory_power uses abs(delta_total); works correctly for negative totals."""
    members = [
        MemberContribution("A", 10.0, 50.0, -40.0, -1.0, "down"),
    ]
    # sum(|delta|) = 40, |delta_total| = 40 -> score = 1.0
    result = _explanatory_power(members, -40.0)
    assert result == pytest.approx(1.0)


def test_explanatory_power_negative_delta_partial():
    """Partial explanatory power when delta is only a fraction of negative total."""
    members = [
        MemberContribution("A", 80.0, 100.0, -20.0, None, "down"),
    ]
    # sum(|delta|) = 20, |delta_total| = 80 -> score = 0.25
    result = _explanatory_power(members, -80.0)
    assert result == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# build_dimension_breakdown — top_n boundary tests
# ---------------------------------------------------------------------------


def test_top_n_one_member_shown_rest_in_other():
    """top_n=1 -> exactly 1 member in members list, rest in Other."""
    current = {"A": 100.0, "B": 80.0, "C": 60.0}
    comparison = {"A": 70.0, "B": 50.0, "C": 30.0}
    delta_total = (100 + 80 + 60) - (70 + 50 + 30)  # 90
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=1)

    assert len(bd.members) == 1
    assert bd.other is not None
    assert bd.other.member == "Other"


def test_top_n_larger_than_member_count_no_other():
    """top_n=50 with only 3 members -> all 3 in members, other=None."""
    current = {"A": 10.0, "B": 20.0, "C": 30.0}
    comparison = {"A": 5.0, "B": 10.0, "C": 15.0}
    delta_total = 30.0
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=50)

    assert len(bd.members) == 3
    assert bd.other is None


def test_top_n_equals_member_count_no_other():
    """top_n == number of members -> all in top, no Other."""
    current = {"X": 1.0, "Y": 2.0}
    comparison = {"X": 0.5, "Y": 1.0}
    bd = build_dimension_breakdown("dim", current, comparison, 1.5, top_n=2)

    assert len(bd.members) == 2
    assert bd.other is None


# ---------------------------------------------------------------------------
# build_dimension_breakdown — empty inputs
# ---------------------------------------------------------------------------


def test_empty_both_aggs_no_members():
    """Both current and comparison empty -> members=[], other=None."""
    bd = build_dimension_breakdown("dim", {}, {}, 0.0)
    assert bd.members == []
    assert bd.other is None


def test_empty_both_aggs_coverage_one():
    """When there are no deltas, coverage=1.0 (guard: sum_all_abs < 1e-9)."""
    bd = build_dimension_breakdown("dim", {}, {}, 0.0)
    assert bd.coverage == pytest.approx(1.0)


def test_empty_both_aggs_explanatory_power_zero():
    """Empty aggs -> explanatory_power=0.0."""
    bd = build_dimension_breakdown("dim", {}, {}, 0.0)
    assert bd.explanatory_power == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# build_dimension_breakdown — single member
# ---------------------------------------------------------------------------


def test_single_member_full_share():
    """Single member carries 100% of the movement; share == 1.0."""
    current = {"only": 150.0}
    comparison = {"only": 100.0}
    delta_total = 50.0
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=10)

    assert len(bd.members) == 1
    assert bd.members[0].share == pytest.approx(1.0)
    assert bd.members[0].direction == "up"
    assert bd.other is None


def test_single_member_zero_delta_share():
    """Single member with no change -> share=0.0, direction='flat'."""
    current = {"only": 100.0}
    comparison = {"only": 100.0}
    bd = build_dimension_breakdown("dim", current, comparison, 0.0, top_n=10)

    assert bd.members[0].share == pytest.approx(0.0)
    assert bd.members[0].direction == "flat"


# ---------------------------------------------------------------------------
# build_dimension_breakdown — negative delta_total
# ---------------------------------------------------------------------------


def test_negative_delta_total_all_members_down():
    """All members decline; direction='down' for every member, shares are negative."""
    current = {"A": 20.0, "B": 30.0}
    comparison = {"A": 50.0, "B": 80.0}
    delta_total = (20 + 30) - (50 + 80)  # -80
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=10)

    for m in bd.members:
        assert m.direction == "down"
        assert m.share < 0


def test_negative_delta_total_explanatory_power_positive():
    """explanatory_power is positive when delta_total is negative."""
    current = {"A": 10.0}
    comparison = {"A": 50.0}
    bd = build_dimension_breakdown("dim", current, comparison, -40.0, top_n=10)
    assert bd.explanatory_power > 0.0


# ---------------------------------------------------------------------------
# build_dimension_breakdown — "Other" bucket aggregation correctness
# ---------------------------------------------------------------------------


def test_other_bucket_delta_equals_sum_of_tail_deltas():
    """other.delta == sum of tail member deltas."""
    current = {str(i): float(i * 10) for i in range(1, 8)}
    comparison = {str(i): float(i * 6) for i in range(1, 8)}
    delta_total = sum(current.values()) - sum(comparison.values())
    all_members = _member_contributions(current, comparison, delta_total)
    tail_delta_sum = sum(m.delta for m in all_members[5:])

    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=5)

    assert bd.other is not None
    assert bd.other.delta == pytest.approx(tail_delta_sum)


def test_other_bucket_share_equals_sum_of_tail_shares():
    """other.share == sum of tail member shares."""
    current = {str(i): float(i * 10) for i in range(1, 8)}
    comparison = {str(i): float(i * 6) for i in range(1, 8)}
    delta_total = sum(current.values()) - sum(comparison.values())
    all_members = _member_contributions(current, comparison, delta_total)
    tail_share_sum = sum(m.share for m in all_members[5:])

    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=5)

    assert bd.other is not None
    assert bd.other.share == pytest.approx(tail_share_sum)


def test_other_bucket_current_equals_sum_of_tail_current():
    """other.current == sum of tail current values (when all tail members have current data)."""
    current = {str(i): float(i * 10) for i in range(1, 8)}
    comparison = {str(i): float(i * 8) for i in range(1, 8)}
    delta_total = sum(current.values()) - sum(comparison.values())
    all_members = _member_contributions(current, comparison, delta_total)
    tail_current_sum = sum(m.current or 0.0 for m in all_members[5:])

    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=5)

    assert bd.other is not None
    assert bd.other.current == pytest.approx(tail_current_sum)


def test_other_bucket_comparison_equals_sum_of_tail_comparison():
    """other.comparison == sum of tail comparison values (when all tail members have comparison data)."""
    current = {str(i): float(i * 10) for i in range(1, 8)}
    comparison = {str(i): float(i * 8) for i in range(1, 8)}
    delta_total = sum(current.values()) - sum(comparison.values())
    all_members = _member_contributions(current, comparison, delta_total)
    tail_comparison_sum = sum(m.comparison or 0.0 for m in all_members[5:])

    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=5)

    assert bd.other is not None
    assert bd.other.comparison == pytest.approx(tail_comparison_sum)


def test_other_bucket_no_current_data():
    """Tail members present only in comparison -> other.current should be None."""
    # Top member in current only (large positive delta) takes top_n=1 slot.
    # Tail members are comparison-only.
    current = {"top": 1000.0}
    comparison = {"a": 5.0, "b": 3.0, "c": 1.0}
    delta_total = 1000.0 - 9.0  # 991
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=1)

    assert bd.other is not None
    # All tail members have current=None -> tail_has_current=False -> other.current=None
    assert bd.other.current is None


def test_other_bucket_no_comparison_data():
    """Tail members present only in current period -> other.comparison should be None."""
    comparison = {"top": 1000.0}
    current = {"a": 5.0, "b": 3.0, "c": 1.0}
    # Deltas: top=-1000, a=+5, b=+3, c=+1 -> top_n=1 captures top (largest abs)
    delta_total = 9.0 - 1000.0  # -991
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=1)

    assert bd.other is not None
    # Tail members (a, b, c) all have comparison=None -> other.comparison=None
    assert bd.other.comparison is None


def test_other_bucket_mixed_current_and_comparison_presence():
    """Tail with mixed presence: some in both periods, some current-only, some comparison-only."""
    # Top member: large delta captures the top_n=1 slot.
    # Tail: one member in both, one current-only, one comparison-only.
    current = {"top": 500.0, "both": 20.0, "curr_only": 15.0}
    comparison = {"top": 100.0, "both": 10.0, "comp_only": 12.0}
    delta_total = (500 + 20 + 15) - (100 + 10 + 12)  # 413
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=1)

    assert bd.other is not None
    # Tail has current data (both=20, curr_only=15) -> other.current is not None
    assert bd.other.current is not None
    # Tail has comparison data (both=10, comp_only=12) -> other.comparison is not None
    assert bd.other.comparison is not None
    # Verify aggregated delta
    all_members = _member_contributions(current, comparison, delta_total)
    tail_delta = sum(m.delta for m in all_members[1:])
    assert bd.other.delta == pytest.approx(tail_delta)


# ---------------------------------------------------------------------------
# build_dimension_breakdown — Other bucket direction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tail_current, tail_comparison, expected_direction",
    [
        (50.0, 30.0, "up"),    # tail net positive
        (30.0, 50.0, "down"),  # tail net negative
        (50.0, 50.0, "flat"),  # tail net zero
    ],
)
def test_other_bucket_direction(tail_current, tail_comparison, expected_direction):
    """Other bucket direction reflects the sign of the aggregated tail delta."""
    # One dominant member to absorb the top_n=1 slot
    current = {"dom": 10100.0, "t": tail_current}
    comparison = {"dom": 100.0, "t": tail_comparison}
    delta_total = (10100.0 + tail_current) - (100.0 + tail_comparison)
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=1)

    assert bd.other is not None
    assert bd.other.direction == expected_direction


# ---------------------------------------------------------------------------
# build_dimension_breakdown — coverage math
# ---------------------------------------------------------------------------


def test_coverage_all_fit_top_n_equals_one():
    """All members in top_n -> coverage=1.0."""
    current = {"A": 100.0}
    comparison = {"A": 50.0}
    bd = build_dimension_breakdown("dim", current, comparison, 50.0, top_n=10)
    assert bd.coverage == pytest.approx(1.0)


def test_coverage_with_tail_ratio():
    """Coverage equals expected ratio when tail exists."""
    # 5 members with deltas: 50, 40, 30, 20, 10  (sum_all_abs = 150)
    # top_n=3 -> top abs = 50+40+30=120, coverage = 120/150
    current = {"a": 50.0, "b": 40.0, "c": 30.0, "d": 20.0, "e": 10.0}
    comparison = {k: 0.0 for k in current}
    delta_total = sum(current.values())  # 150
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=3)

    expected_coverage = (50 + 40 + 30) / (50 + 40 + 30 + 20 + 10)
    assert bd.coverage == pytest.approx(expected_coverage)


def test_coverage_all_deltas_zero_equals_one():
    """When all member deltas are 0, coverage=1.0 (sum_all_abs < 1e-9 guard)."""
    current = {"A": 50.0, "B": 30.0}
    comparison = {"A": 50.0, "B": 30.0}
    bd = build_dimension_breakdown("dim", current, comparison, 0.0, top_n=1)
    assert bd.coverage == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# build_dimension_breakdown — delta_total == 0 comprehensive
# ---------------------------------------------------------------------------


def test_zero_delta_total_explanatory_power_is_zero():
    """When delta_total == 0 exactly, explanatory_power=0.0."""
    current = {"A": 60.0, "B": 40.0}
    comparison = {"A": 40.0, "B": 60.0}
    bd = build_dimension_breakdown("dim", current, comparison, 0.0, top_n=10)
    assert bd.explanatory_power == pytest.approx(0.0)


def test_zero_delta_total_shares_all_zero():
    """shares are all 0.0 when delta_total == 0."""
    current = {"A": 60.0, "B": 40.0}
    comparison = {"A": 40.0, "B": 60.0}
    bd = build_dimension_breakdown("dim", current, comparison, 0.0, top_n=10)
    for m in bd.members:
        assert m.share == pytest.approx(0.0)


def test_zero_delta_total_coverage_one_all_members_in_top():
    """When all members fit in top_n and delta_total=0, coverage=1.0."""
    current = {"A": 60.0, "B": 40.0}
    comparison = {"A": 40.0, "B": 60.0}
    bd = build_dimension_breakdown("dim", current, comparison, 0.0, top_n=10)
    # sum_all_abs = 40 > 1e-9, top_abs = 40, coverage = 40/40 = 1.0
    assert bd.coverage == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# build_explain_result — delta_total on ExplainResult
# ---------------------------------------------------------------------------


def test_explain_result_delta_total_negative():
    """When current < comparison, delta_total on result is negative."""
    result = build_explain_result(
        metric_id="m1",
        measure="revenue",
        current_total=80.0,
        comparison_total=120.0,
        dimension_breakdowns={
            "region": ({"North": 80.0}, {"North": 120.0}),
        },
    )
    assert result.delta_total == pytest.approx(-40.0)
    assert result.delta_total < 0


def test_explain_result_all_member_directions_down_negative_delta():
    """All member directions are 'down' when overall delta is negative."""
    result = build_explain_result(
        metric_id="neg",
        measure="sales",
        current_total=50.0,
        comparison_total=150.0,
        dimension_breakdowns={
            "cat": ({"A": 30.0, "B": 20.0}, {"A": 90.0, "B": 60.0}),
        },
    )
    for m in result.dimensions[0].members:
        assert m.direction == "down"


# ---------------------------------------------------------------------------
# build_explain_result — empty dimension_breakdowns
# ---------------------------------------------------------------------------


def test_explain_result_empty_dimensions_list():
    """Empty dimension_breakdowns -> result.dimensions == []."""
    result = build_explain_result(
        metric_id="empty",
        measure="count",
        current_total=50.0,
        comparison_total=30.0,
        dimension_breakdowns={},
    )
    assert result.dimensions == []


# ---------------------------------------------------------------------------
# build_explain_result — multiple dimensions sorted desc
# ---------------------------------------------------------------------------


def test_three_dimensions_sorted_desc():
    """3 dimensions with varying explanatory power are sorted descending."""
    result = build_explain_result(
        metric_id="multi",
        measure="sales",
        current_total=1100.0,
        comparison_total=1000.0,
        dimension_breakdowns={
            "dim_low":  ({"x": 1001.0}, {"x": 1000.0}),   # delta=1,  ep~0.01
            "dim_high": ({"x": 1090.0}, {"x": 1000.0}),   # delta=90, ep=0.9
            "dim_mid":  ({"x": 1050.0}, {"x": 1000.0}),   # delta=50, ep=0.5
        },
        top_n=10,
    )
    assert len(result.dimensions) == 3
    powers = [d.explanatory_power for d in result.dimensions]
    assert powers == sorted(powers, reverse=True)
    assert result.dimensions[0].dimension == "dim_high"
    assert result.dimensions[1].dimension == "dim_mid"
    assert result.dimensions[2].dimension == "dim_low"


# ---------------------------------------------------------------------------
# build_explain_result — tied explanatory_power (both present, stable sort)
# ---------------------------------------------------------------------------


def test_tied_explanatory_power_both_dimensions_present():
    """When two dims have equal explanatory_power, both appear in output."""
    result = build_explain_result(
        metric_id="tie",
        measure="revenue",
        current_total=200.0,
        comparison_total=100.0,
        dimension_breakdowns={
            "dim_a": ({"x": 200.0}, {"x": 100.0}),  # ep=1.0
            "dim_b": ({"y": 200.0}, {"y": 100.0}),  # ep=1.0
        },
        top_n=10,
    )
    assert len(result.dimensions) == 2
    assert result.dimensions[0].explanatory_power == pytest.approx(
        result.dimensions[1].explanatory_power
    )
    dim_names = {d.dimension for d in result.dimensions}
    assert "dim_a" in dim_names
    assert "dim_b" in dim_names


def test_tied_explanatory_power_stable_sort():
    """Tied dimensions preserve insertion order under stable sort."""
    # Python's sort is stable; equal-ep dims retain their dict insertion order.
    dimension_breakdowns = {
        "first":  ({"x": 200.0}, {"x": 100.0}),
        "second": ({"y": 200.0}, {"y": 100.0}),
        "third":  ({"z": 200.0}, {"z": 100.0}),
    }
    result = build_explain_result(
        metric_id="tie2",
        measure="revenue",
        current_total=200.0,
        comparison_total=100.0,
        dimension_breakdowns=dimension_breakdowns,
        top_n=10,
    )
    dim_names = [d.dimension for d in result.dimensions]
    assert dim_names == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# build_explain_result — all members only in one period
# ---------------------------------------------------------------------------


def test_explain_result_all_members_current_only():
    """Works when all dimension members exist only in current period."""
    result = build_explain_result(
        metric_id="new",
        measure="signups",
        current_total=300.0,
        comparison_total=0.0,
        dimension_breakdowns={
            "channel": ({"email": 200.0, "social": 100.0}, {}),
        },
    )
    assert result.delta_total == pytest.approx(300.0)
    members = result.dimensions[0].members
    assert all(m.comparison is None for m in members)
    assert all(m.direction == "up" for m in members)


def test_explain_result_all_members_comparison_only():
    """Works when all dimension members exist only in comparison period."""
    result = build_explain_result(
        metric_id="churn",
        measure="users",
        current_total=0.0,
        comparison_total=200.0,
        dimension_breakdowns={
            "plan": ({}, {"free": 150.0, "pro": 50.0}),
        },
    )
    assert result.delta_total == pytest.approx(-200.0)
    members = result.dimensions[0].members
    assert all(m.current is None for m in members)
    assert all(m.direction == "down" for m in members)


# ---------------------------------------------------------------------------
# ExplainResult — field integrity
# ---------------------------------------------------------------------------


def test_explain_result_fields_correct():
    """All top-level fields on ExplainResult are set correctly."""
    result = build_explain_result(
        metric_id="mymetric",
        measure="clicks",
        current_total=500.0,
        comparison_total=400.0,
        dimension_breakdowns={
            "browser": (
                {"chrome": 300.0, "firefox": 200.0},
                {"chrome": 250.0, "firefox": 150.0},
            ),
        },
    )
    assert result.metric_id == "mymetric"
    assert result.measure == "clicks"
    assert result.current_total == pytest.approx(500.0)
    assert result.comparison_total == pytest.approx(400.0)
    assert result.delta_total == pytest.approx(100.0)
    assert result.summary_hint == ""
    assert isinstance(result.dimensions, list)


# ---------------------------------------------------------------------------
# build_dimension_breakdown — dimension name propagation
# ---------------------------------------------------------------------------


def test_dimension_name_propagated():
    """DimensionBreakdown.dimension equals the name passed in."""
    bd = build_dimension_breakdown("my_dimension", {"A": 10.0}, {"A": 5.0}, 5.0)
    assert bd.dimension == "my_dimension"


# ---------------------------------------------------------------------------
# MemberContribution.member — non-string hashable types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("member_key", [42, None, True, ("compound", "key")])
def test_member_key_non_string_types(member_key):
    """Keys of any hashable type are accepted and returned correctly."""
    current = {member_key: 100.0}
    comparison = {member_key: 50.0}
    members = _member_contributions(current, comparison, 50.0)
    assert len(members) == 1
    assert members[0].member == member_key
    assert members[0].delta == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Large member count — top_n boundary stress
# ---------------------------------------------------------------------------


def test_large_member_count_top_n_exact_boundary():
    """With exactly top_n members, no Other bucket is created."""
    n = 10
    current = {str(i): float(i) for i in range(n)}
    comparison = {str(i): 0.0 for i in range(n)}
    delta_total = sum(current.values())
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=n)

    assert len(bd.members) == n
    assert bd.other is None
    assert bd.coverage == pytest.approx(1.0)


def test_large_member_count_top_n_boundary_plus_one():
    """With top_n+1 members, exactly 1 member goes to Other."""
    n = 10
    current = {str(i): float(i + 1) for i in range(n + 1)}
    comparison = {str(i): 0.0 for i in range(n + 1)}
    delta_total = sum(current.values())
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=n)

    assert len(bd.members) == n
    assert bd.other is not None
    all_members = _member_contributions(current, comparison, delta_total)
    tail = all_members[n:]
    assert len(tail) == 1
    assert bd.other.delta == pytest.approx(tail[0].delta)


# ---------------------------------------------------------------------------
# share values sum check
# ---------------------------------------------------------------------------


def test_shares_sum_to_one_positive_delta():
    """All share values across all members sum to 1.0 for positive delta_total."""
    current = {"A": 100.0, "B": 50.0, "C": 25.0}
    comparison = {"A": 70.0, "B": 30.0, "C": 10.0}
    delta_total = sum(current.values()) - sum(comparison.values())  # 65
    members = _member_contributions(current, comparison, delta_total)
    total_share = sum(m.share for m in members)
    assert total_share == pytest.approx(1.0)
