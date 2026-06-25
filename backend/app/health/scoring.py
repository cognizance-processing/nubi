"""Weighted data-health score computation (B.1).

Scoring model
-------------
The health score is a number in [0, 100] computed as a weighted sum of three
normalised dimensions:

    score = 100 × (
        w_freshness     × freshness_score     +
        w_completeness  × completeness_score  +
        w_availability  × availability_score
    )

where the three ``w_*`` weights are configurable per-org (table
``health_score_config``) and default to:

    freshness=0.50, completeness=0.30, availability=0.20

Default weights were chosen so that the most impactful BI risk (stale data
served to decision-makers) dominates the score.

Dimension computation
---------------------
freshness (0–1)
    Derived directly from the pre-computed ``dataset_freshness.status``
    column — no live compute needed:
      - 'fresh'   → 1.0
      - 'stale'   → 0.0
      - 'unknown' → None  (dimension marked 'unknown' in reasons[])

completeness (0–1)
    Currently derived from recent run success rate (last N runs):
      - If run history is available: successes / total_runs (capped to N=20)
      - If no history:               None ('unknown')
    Future enhancement: integrate actual NULL/row-count signals from DuckDB
    output metadata when cheaply available (tracked as TODO B.1-c).

availability (0–1)
    Binary: 1.0 if any successful run exists ever, 0.0 if no run has ever
    completed.  'unknown' when we have no history at all.

Handling 'unknown' dimensions
------------------------------
An 'unknown' dimension contributes zero to the raw weighted sum and its
weight is redistributed proportionally to the remaining known dimensions,
so that the final score is always comparable within the range of known
information. If ALL dimensions are unknown the score is None and `status`
is ``'unknown'``.

Honesty: we never fake a score for a dimension that has no signal.

Public API
----------
compute_health_score(freshness_rec, run_history, weights) -> HealthScoreResult
    Pure function — no I/O, no async.  Suitable for use in sync contexts
    (tests, batch jobs) and in async route handlers alike.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Default weights
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "freshness": 0.50,
    "completeness": 0.30,
    "availability": 0.20,
}

_HISTORY_WINDOW = 20  # recent runs considered for completeness/availability


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DimensionResult:
    """One scoring dimension with its raw score and explanation."""

    name: str
    score: float | None   # 0-1, or None if 'unknown'
    status: str           # 'ok' | 'warn' | 'fail' | 'unknown'
    reason: str


@dataclass
class HealthScoreResult:
    """Output of :func:`compute_health_score`."""

    dataset_key: str
    score: float | None           # 0-100 or None (all dims unknown)
    grade: str                    # 'A'/'B'/'C'/'D'/'F'/'unknown'
    dimensions: list[DimensionResult]
    reasons: list[str]            # human-readable summary lines
    weights_used: dict[str, float]


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def _normalise_weights(
    weights: dict[str, float],
    known_dims: set[str],
) -> dict[str, float]:
    """Redistribute weight from unknown dims proportionally to known dims."""
    known_w = {k: v for k, v in weights.items() if k in known_dims}
    total = sum(known_w.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in known_w.items()}


def _grade(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def _dim_status(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.9:
        return "ok"
    if score >= 0.6:
        return "warn"
    return "fail"


def compute_health_score(
    *,
    dataset_key: str,
    freshness_status: str,                 # 'fresh' | 'stale' | 'unknown'
    run_history: list[dict[str, Any]],     # list of {status: str} dicts; may be empty
    weights: dict[str, float] | None = None,
) -> HealthScoreResult:
    """Compute a weighted health score for a single dataset.

    Parameters
    ----------
    dataset_key:
        Logical key for the dataset (used in the returned record).
    freshness_status:
        Pre-computed status from the freshness registry ('fresh'/'stale'/'unknown').
    run_history:
        Recent run records; each dict must have at minimum a ``"status"`` key
        with value ``"success"`` or other.  Pass ``[]`` when unavailable.
    weights:
        Optional weight override dict with keys 'freshness', 'completeness',
        'availability'.  Missing keys default to :data:`DEFAULT_WEIGHTS` values.
        Weights are normalised internally so they need not sum to 1.0.

    Returns
    -------
    HealthScoreResult
    """
    # Resolve weights — fill in defaults for any missing key, then normalise.
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        for k in ("freshness", "completeness", "availability"):
            if k in weights:
                w[k] = float(weights[k])
    total_w = sum(w.values())
    if total_w > 0:
        w = {k: v / total_w for k, v in w.items()}

    dims: list[DimensionResult] = []

    # ── Freshness dimension ─────────────────────────────────────────────────
    if freshness_status == "fresh":
        f_score: float | None = 1.0
        f_reason = "Data is within the expected freshness interval."
    elif freshness_status == "stale":
        f_score = 0.0
        f_reason = "Data has not been refreshed within the expected interval."
    else:
        f_score = None
        f_reason = (
            "Freshness is unknown: no successful run recorded or no SLA configured."
        )
    dims.append(
        DimensionResult(
            name="freshness",
            score=f_score,
            status=_dim_status(f_score),
            reason=f_reason,
        )
    )

    # ── Completeness dimension (success rate proxy) ─────────────────────────
    window = run_history[-_HISTORY_WINDOW:] if run_history else []
    if window:
        successes = sum(1 for r in window if str(r.get("status", "")).lower() in ("success", "succeeded"))
        c_score: float | None = successes / len(window)
        c_reason = (
            f"Success rate: {successes}/{len(window)} recent runs succeeded."
        )
    else:
        c_score = None
        c_reason = "No run history available to compute completeness."
    dims.append(
        DimensionResult(
            name="completeness",
            score=c_score,
            status=_dim_status(c_score),
            reason=c_reason,
        )
    )

    # ── Availability dimension ──────────────────────────────────────────────
    if run_history:
        any_success = any(
            str(r.get("status", "")).lower() in ("success", "succeeded")
            for r in run_history
        )
        a_score: float | None = 1.0 if any_success else 0.0
        a_reason = (
            "At least one successful run has been recorded."
            if any_success
            else "No successful run has ever been recorded."
        )
    elif freshness_status in ("fresh", "stale"):
        # last_success_at is set → at least one success occurred
        a_score = 1.0
        a_reason = "At least one successful run inferred from freshness registry."
    else:
        a_score = None
        a_reason = "No run history or freshness data to determine availability."
    dims.append(
        DimensionResult(
            name="availability",
            score=a_score,
            status=_dim_status(a_score),
            reason=a_reason,
        )
    )

    # ── Weighted sum (redistribute weight from unknown dims) ────────────────
    known = {d.name for d in dims if d.score is not None}
    if not known:
        final_score: float | None = None
        grade = "unknown"
    else:
        norm_w = _normalise_weights(w, known)
        raw = sum(norm_w[d.name] * d.score for d in dims if d.name in norm_w)  # type: ignore[operator]
        final_score = round(raw * 100, 1)
        grade = _grade(final_score)

    # ── Human-readable reasons ──────────────────────────────────────────────
    reasons: list[str] = []
    for d in dims:
        prefix = f"[{d.status.upper()}] {d.name}: "
        reasons.append(prefix + d.reason)

    return HealthScoreResult(
        dataset_key=dataset_key,
        score=final_score,
        grade=grade,
        dimensions=dims,
        reasons=reasons,
        weights_used=w,
    )
