"""Nubi connector package — Wave M1-A.

Public surface for Wave M1-A:

    PhysicalPlan      — serialisable query plan (plan.py)
    Connector         — abstract base class (base.py)
    plan              — planner entry-point (planner.py)
    compute_cache_key — stable cache-key hash (cache_key.py)

Executors (Postgres/ADBC, DuckDB) live in Wave M1-B and are not imported here.
"""

from app.connectors.plan import PhysicalPlan
from app.connectors.base import Connector
from app.connectors.cache_key import compute_cache_key
from app.connectors.planner import plan

__all__ = [
    "PhysicalPlan",
    "Connector",
    "compute_cache_key",
    "plan",
]
