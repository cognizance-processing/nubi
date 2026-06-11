"""Metrics / semantic layer (Wave C).

A *metric* defines business logic (e.g. ``revenue = SUM(amount)``) ONCE — with an
owner, a grain, allowed dimensions, and RLS keys — and is compiled to SQL on
demand. This makes AI authoring CONSISTENT (answer from a governed definition,
not hallucinated SQL) rather than merely syntactically valid.

The layer grows on the existing substrate (no new engine): the query-registry
pattern (registry.py), the planner's RLS injection (connectors/planner.py:plan),
the pre-agg router (route_to_rollup_shape), and the query execution path. See
METRICS_LAYER.md for the full design.
"""

from app.metrics.models import (
    Dimension,
    Measure,
    MetricDefinition,
    MetricFilter,
    MetricQuery,
    TimeDimension,
)

__all__ = [
    "Dimension",
    "Measure",
    "MetricDefinition",
    "MetricFilter",
    "MetricQuery",
    "TimeDimension",
]
