"""
explain.py — pure, IO-free contribution math for root-cause analysis.

Given current-period and comparison-period aggregates keyed by dimension member,
compute per-member delta contributions, rank dimensions by explanatory power,
and bucket tail members into an "Other" group.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MemberContribution:
    member: Any                   # dimension member value (str/int/None)
    current: Optional[float]      # None if absent in current period
    comparison: Optional[float]   # None if absent in comparison period
    delta: float                  # current - comparison (0 if one side absent)
    share: float                  # delta / abs(delta_total)  (0 when delta_total≈0)
    direction: str                # "up" | "down" | "flat"


@dataclass
class DimensionBreakdown:
    dimension: str
    members: List[MemberContribution]    # top_n + optional "Other"
    other: Optional[MemberContribution]  # the aggregated tail, or None
    coverage: float                      # sum(|top_n delta|) / sum(|all delta|)
    explanatory_power: float             # weighted R²-like score for ranking dims


@dataclass
class ExplainResult:
    metric_id: str
    measure: str
    delta_total: float
    current_total: float
    comparison_total: float
    dimensions: List[DimensionBreakdown]   # sorted by explanatory_power desc
    summary_hint: str                      # plain-text, filled by NL layer


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_delta(current: Optional[float], comparison: Optional[float]) -> float:
    """Return (current or 0.0) - (comparison or 0.0)."""
    return (current or 0.0) - (comparison or 0.0)


def _member_contributions(
    current_agg: Dict[Any, float],
    comparison_agg: Dict[Any, float],
    delta_total: float,
) -> List[MemberContribution]:
    """Build a MemberContribution list for all members in either period.

    - Unions all keys from both dicts.
    - delta = current.get(m, 0) - comparison.get(m, 0)
    - share = delta / abs(delta_total) if abs(delta_total) > 1e-9 else 0.0
    - direction: "up" if delta > 1e-9, "down" if delta < -1e-9, else "flat"
    - Sorted by abs(delta) descending.
    """
    all_members = set(current_agg.keys()) | set(comparison_agg.keys())
    abs_total = abs(delta_total)

    contributions = []
    for m in all_members:
        in_current = m in current_agg
        in_comparison = m in comparison_agg
        current_val: Optional[float] = current_agg[m] if in_current else None
        comparison_val: Optional[float] = comparison_agg[m] if in_comparison else None
        delta = (current_val or 0.0) - (comparison_val or 0.0)
        share = delta / abs_total if abs_total > 1e-9 else 0.0
        if delta > 1e-9:
            direction = "up"
        elif delta < -1e-9:
            direction = "down"
        else:
            direction = "flat"
        contributions.append(MemberContribution(
            member=m,
            current=current_val,
            comparison=comparison_val,
            delta=delta,
            share=share,
            direction=direction,
        ))

    contributions.sort(key=lambda c: abs(c.delta), reverse=True)
    return contributions


def _explanatory_power(members: List[MemberContribution], delta_total: float) -> float:
    """Score = sum(|delta|) / |delta_total|, capped at 1.0.

    Returns 0.0 when delta_total ≈ 0.
    """
    if abs(delta_total) < 1e-9:
        return 0.0
    score = sum(abs(m.delta) for m in members) / abs(delta_total)
    return min(score, 1.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_dimension_breakdown(
    dimension: str,
    current_agg: Dict[Any, float],
    comparison_agg: Dict[Any, float],
    delta_total: float,
    top_n: int = 10,
) -> DimensionBreakdown:
    """Build a DimensionBreakdown for a single dimension.

    1. Computes all member contributions.
    2. Splits into top_n (by abs delta) and tail.
    3. Tail members -> a single "Other" MemberContribution (if any tail).
    4. coverage = sum(|top delta|) / sum(|all delta|) (1.0 if no deltas).
    5. explanatory_power from _explanatory_power over top_members.
    """
    all_members = _member_contributions(current_agg, comparison_agg, delta_total)
    top_members = all_members[:top_n]
    tail_members = all_members[top_n:]

    other: Optional[MemberContribution] = None
    if tail_members:
        tail_has_current = any(m.current is not None for m in tail_members)
        tail_has_comparison = any(m.comparison is not None for m in tail_members)

        other_current: Optional[float] = (
            sum(m.current or 0.0 for m in tail_members) if tail_has_current else None
        )
        other_comparison: Optional[float] = (
            sum(m.comparison or 0.0 for m in tail_members) if tail_has_comparison else None
        )
        other_delta = sum(m.delta for m in tail_members)
        abs_total = abs(delta_total)
        other_share = other_delta / abs_total if abs_total > 1e-9 else 0.0
        if other_delta > 1e-9:
            other_direction = "up"
        elif other_delta < -1e-9:
            other_direction = "down"
        else:
            other_direction = "flat"
        other = MemberContribution(
            member="Other",
            current=other_current,
            comparison=other_comparison,
            delta=other_delta,
            share=other_share,
            direction=other_direction,
        )

    # Coverage: top absolute deltas / all absolute deltas
    sum_all_abs = sum(abs(m.delta) for m in all_members)
    if sum_all_abs < 1e-9:
        coverage = 1.0
    else:
        sum_top_abs = sum(abs(m.delta) for m in top_members)
        coverage = sum_top_abs / sum_all_abs

    explanatory_power = _explanatory_power(top_members, delta_total)

    return DimensionBreakdown(
        dimension=dimension,
        members=top_members,
        other=other,
        coverage=coverage,
        explanatory_power=explanatory_power,
    )


def build_explain_result(
    metric_id: str,
    measure: str,
    current_total: float,
    comparison_total: float,
    dimension_breakdowns: Dict[str, Tuple[Dict[Any, float], Dict[Any, float]]],
    top_n: int = 10,
) -> ExplainResult:
    """Build a full ExplainResult from per-dimension aggregate dicts.

    Parameters
    ----------
    metric_id:            The metric's id/slug.
    measure:              The measure name (e.g. "revenue").
    current_total:        Overall measure total for the current period.
    comparison_total:     Overall measure total for the comparison period.
    dimension_breakdowns: dict mapping dimension name ->
                          (current_agg, comparison_agg) where each agg is a
                          dict of member -> float.
    top_n:                Maximum members to show before collapsing to "Other".

    Returns an ExplainResult with dimensions sorted by explanatory_power desc
    and summary_hint="".
    """
    delta_total = current_total - comparison_total

    dims: List[DimensionBreakdown] = []
    for dim_name, (current_agg, comparison_agg) in dimension_breakdowns.items():
        bd = build_dimension_breakdown(
            dim_name, current_agg, comparison_agg, delta_total, top_n=top_n
        )
        dims.append(bd)

    dims.sort(key=lambda d: d.explanatory_power, reverse=True)

    return ExplainResult(
        metric_id=metric_id,
        measure=measure,
        delta_total=delta_total,
        current_total=current_total,
        comparison_total=comparison_total,
        dimensions=dims,
        summary_hint="",
    )
