"""Auto pre-aggregation scheduler — dogfoods the Flows engine.

The core idea
-------------
The suggest → materialize pass (mine query log → build rollups) is itself
registered as a *scheduled flow* so it benefits from the same work-pool,
retry, and observability machinery as any other pipeline.

This module provides two public helpers:

``ensure_preagg_flow(org_id, created_by, *, schedule, min_hits, flow_store)``
    Idempotently ensure the ``preagg_refresh`` scheduled flow exists for the
    given org.  If a flow named ``"__preagg_refresh__"`` already exists for
    the org the existing one is returned unchanged (first-write-wins so safe
    to call on every startup).

``run_preagg_refresh(org_id, min_hits, registry, query_log)``
    The actual suggest → materialize pass.  Mines the in-memory query log,
    builds rollups for all high-frequency candidates, and returns a summary
    dict.  This is the function called both by the ``preagg_refresh`` task
    handler AND by the end-to-end test so the test exercises the real logic.

Flow spec
---------
The registered flow has a single ``preagg_refresh`` task (kind registered in
``app.flows.registry`` by ``_bootstrap``).  The task's config carries
``min_hits`` so the behaviour is tunable per-org without code changes.

Cron default: ``'0 * * * *'`` (hourly).  Callers may override via the
``schedule`` parameter.

Security / open-core
--------------------
This is pure OSS core: no billing, no EE-specific code.  Rollups are built
using the same DuckDB write path (``app.connectors.preagg.build_rollup``) and
registry as the on-demand ``POST /preagg/build`` endpoint.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.connectors.preagg import RollupRegistry
    from app.connectors.query_log import QueryLog
    from app.flows.store import InMemoryFlowStore, PgFlowStore

logger = logging.getLogger(__name__)

# Stable flow name used to detect an existing preagg flow for an org.
_PREAGG_FLOW_NAME = "__preagg_refresh__"

# Default cron schedule — once per hour, on the hour.
_DEFAULT_SCHEDULE = "0 * * * *"


# ---------------------------------------------------------------------------
# run_preagg_refresh — the actual suggest → materialize pass
# ---------------------------------------------------------------------------


def run_preagg_refresh(
    org_id: str,
    min_hits: int = 3,
    *,
    registry: "RollupRegistry | None" = None,
    query_log: "QueryLog | None" = None,
    source_database: str | None = None,
) -> dict[str, Any]:
    """Mine the query log and build rollups for high-frequency patterns.

    This function is called by:
    - The ``preagg_refresh`` task handler (scheduled execution via the flows
      engine work-pool).
    - Tests that want to exercise the full wedge synchronously.

    Parameters
    ----------
    org_id:
        The org whose query log / rollup registry is being refreshed.
    min_hits:
        Minimum sample count for a candidate to be materialized.  Default 3.
    registry:
        Rollup registry to register newly built rollups in.  Defaults to the
        process-wide singleton (``get_registry()``).
    query_log:
        Query log to mine.  Defaults to the process-wide singleton
        (``get_query_log()``).
    source_database:
        Absolute path to the DuckDB file holding the base fact tables.
        When ``None`` the DuckDB context is fully in-memory (suitable for
        tests that register Arrow tables directly into the connector).

    Returns
    -------
    dict
        ``{candidates_found: int, rollups_built: int, errors: list[str],
           rollup_ids: list[str]}``
    """
    from app.connectors.preagg import build_rollup, get_registry, mine  # noqa: PLC0415
    from app.connectors.query_log import get_query_log  # noqa: PLC0415

    reg = registry if registry is not None else get_registry()
    log = query_log if query_log is not None else get_query_log()

    candidates = mine(log, min_hits=min_hits)
    built_ids: list[str] = []
    errors: list[str] = []

    for candidate in candidates:
        try:
            # Skip candidates that already have a registered rollup (idempotent).
            existing = reg.candidates_for_table(candidate.table)
            existing_dims = {frozenset(r.dimensions) for r in existing}
            candidate_dims = frozenset(candidate.dimensions)
            if candidate_dims in existing_dims:
                logger.debug(
                    "preagg_refresh: skipping %s (rollup already built)",
                    candidate.cluster_key,
                )
                continue

            built = build_rollup(
                candidate,
                rls_keys=[],
                source_database=source_database,
                registry=reg,
                register_query=True,
            )
            built_ids.append(built.rollup_id)
            logger.info(
                "preagg_refresh: built rollup %s for %s (score=%d)",
                built.rollup_id,
                candidate.cluster_key,
                candidate.score,
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"Failed to build rollup for {candidate.cluster_key!r}: {exc}"
            logger.warning("preagg_refresh: %s", msg)
            errors.append(msg)

    return {
        "org_id": org_id,
        "candidates_found": len(candidates),
        "rollups_built": len(built_ids),
        "rollup_ids": built_ids,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# ensure_preagg_flow — idempotent flow registration
# ---------------------------------------------------------------------------


async def ensure_preagg_flow(
    org_id: str,
    created_by: str,
    *,
    schedule: str = _DEFAULT_SCHEDULE,
    min_hits: int = 3,
    flow_store: "InMemoryFlowStore | PgFlowStore | None" = None,
) -> dict[str, Any]:
    """Ensure the ``preagg_refresh`` scheduled flow exists for *org_id*.

    Idempotent: if a flow named ``"__preagg_refresh__"`` already exists for
    the org, it is returned unchanged.  A new flow is created only on the
    first call for a given org.

    The created flow has a single ``preagg_refresh`` task that invokes
    ``run_preagg_refresh`` with the configured ``min_hits``.  The task's
    config carries ``min_hits`` and ``org_id`` so the handler can call the
    right logic without importing from the scheduler.

    Parameters
    ----------
    org_id:
        Target org.
    created_by:
        User ID to stamp as the creator of the flow.
    schedule:
        Cron expression for the refresh schedule.  Default ``"0 * * * *"``
        (hourly).
    min_hits:
        Minimum query-log frequency for a candidate to be materialized.
    flow_store:
        Flow store to use.  Defaults to ``get_flow_store()``.

    Returns
    -------
    dict
        The flow dict (from the store), either newly created or pre-existing.
    """
    from app.flows.store import get_flow_store  # noqa: PLC0415

    store = flow_store if flow_store is not None else get_flow_store()

    # ── Idempotency: return the existing flow if already registered. ─────────
    existing_flows = await store.list_flows(org_id)
    for flow in existing_flows:
        if flow.get("name") == _PREAGG_FLOW_NAME:
            logger.debug(
                "ensure_preagg_flow: flow %s already exists for org %s",
                flow["id"],
                org_id,
            )
            return flow

    # ── Build the FlowSpec with a single preagg_refresh task. ───────────────
    spec: dict[str, Any] = {
        "version": 1,
        "name": _PREAGG_FLOW_NAME,
        "params": [],
        "tasks": [
            {
                "key": "refresh",
                "kind": "preagg_refresh",
                "needs": [],
                "config": {
                    "org_id": org_id,
                    "min_hits": min_hits,
                },
                "retries": 1,
                "retry_backoff_s": 60,
                "timeout_s": 300,
                "cache_ttl_s": 0,
            }
        ],
    }

    flow = await store.create_flow(
        org_id=org_id,
        created_by=created_by,
        name=_PREAGG_FLOW_NAME,
        spec=spec,
        enabled=True,
        schedule=schedule,
    )
    logger.info(
        "ensure_preagg_flow: registered preagg_refresh flow %s for org %s (schedule=%r)",
        flow["id"],
        org_id,
        schedule,
    )
    return flow
