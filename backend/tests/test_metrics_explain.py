"""Unit tests for backend/app/metrics/explain.py — pure math, no IO."""
import pytest
from app.metrics.explain import (
    build_dimension_breakdown,
    build_explain_result,
    MemberContribution,
    _member_contributions,
    _explanatory_power,
    _safe_delta,
)


# ---------------------------------------------------------------------------
# _safe_delta
# ---------------------------------------------------------------------------

def test_safe_delta_both_present():
    assert _safe_delta(10.0, 7.0) == pytest.approx(3.0)


def test_safe_delta_current_none():
    assert _safe_delta(None, 5.0) == pytest.approx(-5.0)


def test_safe_delta_comparison_none():
    assert _safe_delta(5.0, None) == pytest.approx(5.0)


def test_safe_delta_both_none():
    assert _safe_delta(None, None) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _member_contributions
# ---------------------------------------------------------------------------

def test_member_contributions_direction_up():
    current = {"A": 100.0}
    comparison = {"A": 80.0}
    members = _member_contributions(current, comparison, 20.0)
    assert len(members) == 1
    assert members[0].direction == "up"
    assert members[0].delta == pytest.approx(20.0)


def test_member_contributions_direction_down():
    current = {"A": 80.0}
    comparison = {"A": 100.0}
    members = _member_contributions(current, comparison, -20.0)
    assert members[0].direction == "down"
    assert members[0].delta == pytest.approx(-20.0)


def test_member_contributions_direction_flat():
    current = {"A": 50.0}
    comparison = {"A": 50.0}
    members = _member_contributions(current, comparison, 0.0)
    assert members[0].direction == "flat"
    assert members[0].share == 0.0


def test_member_contributions_sorted_by_abs_delta():
    current = {"A": 110.0, "B": 60.0, "C": 30.0}
    comparison = {"A": 100.0, "B": 80.0, "C": 20.0}
    delta_total = (110 + 60 + 30) - (100 + 80 + 20)  # 0
    members = _member_contributions(current, comparison, delta_total)
    # Sort by abs delta desc: B=-20, A=+10, C=+10  (B has largest abs)
    assert abs(members[0].delta) >= abs(members[1].delta)
    assert abs(members[1].delta) >= abs(members[2].delta)


def test_member_contributions_share_sums_correct():
    current = {"A": 100.0, "B": 50.0}
    comparison = {"A": 80.0, "B": 40.0}
    delta_total = (100 + 50) - (80 + 40)  # 30
    members = _member_contributions(current, comparison, delta_total)
    total_share = sum(m.share for m in members)
    assert total_share == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _explanatory_power
# ---------------------------------------------------------------------------

def test_explanatory_power_zero_delta_total():
    from app.metrics.explain import MemberContribution
    members = [MemberContribution(member="A", current=5.0, comparison=5.0,
                                   delta=0.0, share=0.0, direction="flat")]
    assert _explanatory_power(members, 0.0) == 0.0


def test_explanatory_power_capped_at_one():
    # Offsetting members: one +30, one -20 → sum(abs) = 50, delta_total = 10
    # 50/10 = 5.0 → should be capped to 1.0
    from app.metrics.explain import MemberContribution
    members = [
        MemberContribution("A", 30.0, 0.0, 30.0, 3.0, "up"),
        MemberContribution("B", 0.0, 20.0, -20.0, -2.0, "down"),
    ]
    assert _explanatory_power(members, 10.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# build_dimension_breakdown
# ---------------------------------------------------------------------------

def test_contributions_sum_to_one():
    """Shares across all members should sum to ~1.0 when delta_total != 0."""
    current = {"A": 100, "B": 50, "C": 30}
    comparison = {"A": 80, "B": 60, "C": 20}
    delta_total = sum(current.values()) - sum(comparison.values())  # 20
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=10)
    all_members = bd.members + ([bd.other] if bd.other else [])
    total_share = sum(m.share for m in all_members)
    assert abs(total_share - 1.0) < 1e-6


def test_delta_zero_path():
    """When delta_total ~= 0, shares should all be 0, no division error."""
    current = {"A": 50, "B": 50}
    comparison = {"A": 50, "B": 50}
    bd = build_dimension_breakdown("dim", current, comparison, 0.0)
    for m in bd.members:
        assert m.share == 0.0


def test_member_in_one_period():
    """Members present in only one period get current/comparison as None."""
    current = {"A": 100}
    comparison = {"B": 80}
    delta_total = 100 - 80  # 20
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=10)
    members_by_name = {m.member: m for m in bd.members}
    assert members_by_name["A"].comparison is None
    assert members_by_name["B"].current is None


def test_top_n_other_bucket():
    """Members beyond top_n should be aggregated into Other."""
    current = {str(i): float(i * 10) for i in range(1, 16)}  # 15 members
    comparison = {str(i): float(i * 8) for i in range(1, 16)}
    delta_total = sum(current.values()) - sum(comparison.values())
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=10)
    assert len(bd.members) == 10
    assert bd.other is not None
    assert bd.other.member == "Other"


def test_coverage_no_tail():
    """When all members fit in top_n, coverage == 1.0 and other is None."""
    current = {"A": 100.0, "B": 50.0}
    comparison = {"A": 80.0, "B": 40.0}
    bd = build_dimension_breakdown("dim", current, comparison, 30.0, top_n=10)
    assert bd.coverage == pytest.approx(1.0)
    assert bd.other is None


def test_coverage_with_tail():
    """Coverage with tail < 1.0 (tail members have non-zero abs delta)."""
    current = {str(i): float(i * 10) for i in range(1, 16)}
    comparison = {str(i): float(i * 8) for i in range(1, 16)}
    delta_total = sum(current.values()) - sum(comparison.values())
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=10)
    assert bd.coverage < 1.0
    assert bd.coverage > 0.0


def test_explanatory_power_present():
    """Explanatory power should be > 0 when top members have non-zero deltas."""
    current = {"A": 120.0, "B": 80.0}
    comparison = {"A": 100.0, "B": 50.0}
    delta_total = (120 + 80) - (100 + 50)  # 50
    bd = build_dimension_breakdown("dim", current, comparison, delta_total, top_n=10)
    assert bd.explanatory_power > 0.0
    assert bd.explanatory_power <= 1.0


# ---------------------------------------------------------------------------
# build_explain_result
# ---------------------------------------------------------------------------

def test_dimension_ranking():
    """Dimension with more explanatory power should rank first."""
    result = build_explain_result(
        metric_id="m1",
        measure="revenue",
        current_total=200.0,
        comparison_total=150.0,
        dimension_breakdowns={
            "region": (
                {"North": 120, "South": 80},
                {"North": 90, "South": 60},
            ),
            "category": (
                {"A": 190, "B": 10},
                {"A": 145, "B": 5},
            ),
        },
        top_n=10,
    )
    assert len(result.dimensions) == 2
    # Should be sorted by explanatory_power desc
    assert result.dimensions[0].explanatory_power >= result.dimensions[1].explanatory_power


def test_explain_result_totals():
    """delta_total should equal current_total - comparison_total."""
    result = build_explain_result(
        metric_id="m2",
        measure="count",
        current_total=300.0,
        comparison_total=250.0,
        dimension_breakdowns={
            "type": ({"X": 180, "Y": 120}, {"X": 150, "Y": 100}),
        },
    )
    assert result.delta_total == pytest.approx(50.0)
    assert result.current_total == pytest.approx(300.0)
    assert result.comparison_total == pytest.approx(250.0)


def test_explain_result_summary_hint_empty():
    """summary_hint is empty string (filled by NL layer)."""
    result = build_explain_result(
        metric_id="m3",
        measure="revenue",
        current_total=100.0,
        comparison_total=80.0,
        dimension_breakdowns={},
    )
    assert result.summary_hint == ""


def test_explain_result_no_dimensions():
    """Works with empty dimension_breakdowns."""
    result = build_explain_result(
        metric_id="m4",
        measure="revenue",
        current_total=100.0,
        comparison_total=80.0,
        dimension_breakdowns={},
    )
    assert result.dimensions == []
    assert result.delta_total == pytest.approx(20.0)


def test_explain_result_negative_delta():
    """Handles negative delta_total correctly."""
    result = build_explain_result(
        metric_id="m5",
        measure="revenue",
        current_total=80.0,
        comparison_total=100.0,
        dimension_breakdowns={
            "region": (
                {"North": 50, "South": 30},
                {"North": 70, "South": 30},
            ),
        },
    )
    assert result.delta_total == pytest.approx(-20.0)
    # North declined by 20 → explanatory_power > 0
    assert result.dimensions[0].explanatory_power > 0.0
