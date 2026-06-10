"""Managed lakehouse — the self-managing DuckDB optimizer (Wave 4, §1/§4).

The managed lakehouse makes Nubi "feel like BigQuery": you query *logical*
tables and a single optimizer owns the mapping to the *physical* structures it
maintains (layout, materializations, rewrite).  It is automatic by default
(posture C+A) and customizable per table via ``nubi.toml``.

This package is intentionally thin scaffolding around machinery that already
exists elsewhere in core:

* mining the query log → :mod:`app.connectors.preagg` (``mine`` /
  ``RollupCandidate``),
* sound rewrite/routing → :func:`app.connectors.planner.route_to_rollup_shape`,
* pre-run cost estimates → ``Connector.estimate`` (``QueryEstimate``),
* per-table overrides → :class:`app.config.nubi_toml.ProjectConfig`.

:class:`~app.lakehouse.optimizer.Optimizer` is the orchestrator that ties them
together: ``observe → decide → (build) → maintain``, plus partition/cluster
auto-detection.
"""

from __future__ import annotations

from app.lakehouse.optimizer import (
    LayoutHint,
    Optimizer,
    OptimizerPlan,
    PlannedRollup,
)

__all__ = [
    "Optimizer",
    "OptimizerPlan",
    "PlannedRollup",
    "LayoutHint",
]
