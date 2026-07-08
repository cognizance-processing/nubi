"""Auto pre-aggregation optimizer.

The optimizer owns the mine → decide → build → maintain loop for pre-aggregations:
it observes the query log, decides which rollups are worth materialising, and
keeps them fresh — so repeated/embedded queries collapse onto compact,
edge-cached aggregates (queried in the browser or pushed down to the customer's
warehouse) instead of re-scanning. It is automatic by default and customizable
per table via ``nubi.toml``.

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
