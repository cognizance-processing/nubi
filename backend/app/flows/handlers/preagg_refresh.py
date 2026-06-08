"""Task handler for the ``preagg_refresh`` flow task kind.

This handler is the bridge between the Flows engine work-pool and the
auto pre-aggregation pass (``app.preagg.scheduler.run_preagg_refresh``).

When the flows executor dispatches a task of kind ``'preagg_refresh'``
it calls ``handle(config, ctx, claims)`` which:

1. Resolves ``org_id`` and ``min_hits`` from the task ``config``.
2. Calls ``run_preagg_refresh`` with the process-wide query log and rollup
   registry (using InMemory singletons; swappable in tests via the standard
   ``set_flow_store`` / reset_for_tests pattern).
3. Returns a summary dict that the executor writes as the task result.

Config keys
-----------
``org_id``  (required) — the org whose query log is mined.
``min_hits`` (optional, default 3) — minimum log frequency to surface a
             candidate for materialization.

Returns
-------
dict
    ``{org_id, candidates_found, rollups_built, rollup_ids, errors}``

Security / open-core
--------------------
No EE imports.  The handler uses only OSS-core modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext


def handle(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Invoke the suggest → materialize pass for the configured org.

    Parameters
    ----------
    config:
        Task config dict.  Required key: ``org_id``.  Optional: ``min_hits``
        (default 3), ``source_database`` (DuckDB path; ``None`` = in-memory).
    ctx:
        Task context (not used directly; available for future enrichment).
    claims:
        Caller auth claims (not used — the pass is org-scoped by config).

    Returns
    -------
    dict
        Summary from :func:`~app.preagg.scheduler.run_preagg_refresh`.
    """
    from app.errors import AppError  # noqa: PLC0415
    from app.preagg.scheduler import run_preagg_refresh  # noqa: PLC0415

    org_id: str | None = config.get("org_id")
    if not org_id:
        raise AppError(
            "invalid_task_config",
            "preagg_refresh task requires 'org_id' in config.",
            400,
        )

    min_hits: int = int(config.get("min_hits", 3))
    source_database: str | None = config.get("source_database")

    return run_preagg_refresh(
        org_id=org_id,
        min_hits=min_hits,
        source_database=source_database,
    )
