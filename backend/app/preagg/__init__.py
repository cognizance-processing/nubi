"""Auto pre-aggregation sub-package.

Public API
----------
ensure_preagg_flow(org_id, created_by, *, schedule, min_hits, flow_store)
    Register (or idempotently find) the scheduled ``preagg_refresh`` flow for
    *org_id* using ``get_flow_store()``.

run_preagg_refresh(org_id, min_hits, registry, query_log)
    Execute the suggest → materialize pass synchronously.  Called by the
    ``preagg_refresh`` flow task handler AND usable as a standalone function
    in tests.

The scheduled flow created here uses the ``preagg_refresh`` task kind
(registered in ``app.flows.registry`` alongside the other built-in kinds).
"""

from __future__ import annotations

from app.preagg.scheduler import ensure_preagg_flow, run_preagg_refresh

__all__ = ["ensure_preagg_flow", "run_preagg_refresh"]
