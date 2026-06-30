"""Drift-sweep job engine — nightly (or on-demand) org-scoped schema-drift detection.

When the scheduler fires a ``drift_sweep`` job, this module:

1. Iterates ALL dataset snapshot keys that exist for the job's owning org in
   the drift store.
2. For each dataset, fetches the live column list from the registered connector
   (best-effort via the DuckDB connector or the dataset catalog fallback) and
   calls ``detect_schema_drift`` against the stored snapshot.
3. Emits a ``SCHEMA_DRIFT`` outbound webhook for any changed datasets (via
   the existing ``detect_schema_drift`` path which already does this).
4. Returns a summary ``(changed_count, message)`` tuple so the scheduler can
   record it in the job_runs table.

Design notes
------------
- **Org-scoped** — the sweep only touches snapshots whose ``org_id`` matches
  the job's ``org_id``.  No cross-org leakage.
- **Best-effort per-dataset** — a single dataset failing (connector error,
  schema unavailable, etc.) is caught and logged; the sweep continues for
  all remaining datasets.
- **Deterministic ``now``** — the caller passes ``now`` (from the scheduler
  tick or the run-now path); no hidden ``datetime.now()`` inside this module.
- **No new tables** — reuses ``dataset_schema_snapshots`` +
  ``schema_drift_events`` (already in the schema via earlier migrations).
- **Guaranteed cadence** — unlike the opportunistic piggyback on query
  execution, this sweep runs on whatever schedule the operator sets (e.g.
  ``"0 2 * * *"`` for 2 AM nightly) regardless of query activity.

Public API
----------
``run_drift_sweep(org_id, now) -> dict``
    Async: detect schema drift for all org datasets and return a summary dict.

``execute_drift_sweep_sync(job, now) -> tuple[int, str]``
    Sync wrapper (runs the async coroutine via a thread pool) so
    ``execute_job`` (which is intentionally sync) can drive the sweep.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("nubi.jobs.drift_sweep")


# ---------------------------------------------------------------------------
# Async core
# ---------------------------------------------------------------------------


async def run_drift_sweep(
    org_id: str,
    now: datetime,
) -> dict[str, Any]:
    """Detect schema drift for all datasets belonging to *org_id*.

    Parameters
    ----------
    org_id:
        The org whose dataset snapshots are checked.  Only snapshots whose
        ``org_id`` stamp matches this value are evaluated (tenant isolation).
    now:
        Reference UTC datetime.  Passed for deterministic logging.

    Returns
    -------
    dict
        ``{"org_id": str, "swept_at": iso8601,
           "evaluated": int, "changed": int, "errors": int,
           "datasets": [per-dataset summary dicts]}``
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    from app.health.schema_drift import get_drift_store, detect_schema_drift  # noqa: PLC0415

    store = get_drift_store()

    # Retrieve all snapshot keys for this org.  The InMemoryDriftStore exposes
    # its internal dict; PgDriftStore requires an explicit listing query.
    dataset_keys: list[str] = await _list_org_dataset_keys(store, org_id)

    results: list[dict[str, Any]] = []
    changed_count = 0
    error_count = 0

    for dataset_key in dataset_keys:
        try:
            # Fetch the live columns for this dataset.
            live_columns = await _fetch_live_columns(org_id, dataset_key)

            if live_columns is None:
                # Connector unavailable / dataset no longer exists — skip but
                # don't count as error (connector may be temporarily offline).
                logger.debug(
                    "drift_sweep: no live columns for org=%s dataset=%s — skipping",
                    org_id,
                    dataset_key,
                )
                results.append({
                    "dataset_key": dataset_key,
                    "state": "skipped",
                    "changed": False,
                    "reason": "live_columns_unavailable",
                })
                continue

            # Capture old snapshot BEFORE calling detect_schema_drift so we
            # can determine whether a change was recorded.
            _snap = store.get_snapshot(org_id, dataset_key)
            if hasattr(_snap, "__await__"):
                old_cols = await _snap
            else:
                old_cols = _snap

            # Run detection (upserts snapshot + emits webhook on change).
            await detect_schema_drift(org_id, dataset_key, live_columns)

            # Check if a change occurred by comparing old snapshot to new live columns.
            # If old_cols is None → first observation (no change emitted).
            did_change = False
            if old_cols is not None:
                norm_live = [{"name": str(c["name"]), "type": str(c["type"])} for c in live_columns]
                norm_old = [{"name": str(c["name"]), "type": str(c["type"])} for c in old_cols]
                did_change = norm_live != norm_old

            if did_change:
                changed_count += 1

            results.append({
                "dataset_key": dataset_key,
                "state": "ok",
                "changed": did_change,
                "columns": len(live_columns),
            })

        except Exception as exc:  # noqa: BLE001 — never abort the sweep
            logger.warning(
                "drift_sweep: org=%s dataset=%s failed: %s",
                org_id,
                dataset_key,
                exc,
            )
            results.append({
                "dataset_key": dataset_key,
                "state": "error",
                "changed": False,
                "error": str(exc),
            })
            error_count += 1

    return {
        "org_id": str(org_id),
        "swept_at": now.isoformat(),
        "evaluated": len(results),
        "changed": changed_count,
        "errors": error_count,
        "datasets": results,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _list_org_dataset_keys(store: Any, org_id: str) -> list[str]:
    """Return all dataset keys with snapshots for *org_id*.

    For InMemoryDriftStore: inspect the internal _snapshots dict.
    For PgDriftStore: run a SELECT DISTINCT dataset_key query.
    """
    # In-memory store (tests): read internal dict directly.
    if hasattr(store, "_snapshots"):
        return [
            dataset_key
            for (o, dataset_key) in store._snapshots
            if str(o) == str(org_id)
        ]

    # Postgres store (prod): query the snapshot table.
    try:
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            """
            SELECT DISTINCT dataset_key
            FROM   dataset_schema_snapshots
            WHERE  org_id = $1::uuid
            ORDER  BY dataset_key
            """,
            org_id,
        )
        return [r["dataset_key"] for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "drift_sweep: could not list dataset keys for org=%s: %s", org_id, exc
        )
        return []


async def _fetch_live_columns(
    org_id: str, dataset_key: str
) -> list[dict[str, str]] | None:
    """Attempt to retrieve live column metadata for *dataset_key*.

    Returns a list of ``{"name": ..., "type": ...}`` dicts, or ``None`` when
    the dataset cannot be introspected (connector offline, key not resolvable).

    Strategy
    --------
    1. Try the datasets catalog (lakehouse/managed datasets).
    2. Fall back to a lightweight DuckDB DESCRIBE on the dataset path.

    This is best-effort; returning ``None`` tells the caller to skip the
    dataset without counting it as an error.
    """
    # Try the datasets catalog first.
    try:
        from app.datasets import get_catalog  # noqa: PLC0415

        catalog = get_catalog()
        if catalog is not None:
            _cols = catalog.get_columns(org_id, dataset_key)
            if hasattr(_cols, "__await__"):
                _cols = await _cols
            if _cols is not None:
                return [{"name": str(c["name"]), "type": str(c["type"])} for c in _cols]
    except Exception:  # noqa: BLE001
        pass  # Catalog unavailable — try connector path.

    # DuckDB connector introspection (best-effort for file-backed datasets).
    try:
        from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415

        connector = DuckDBConnector()
        # Use DESCRIBE to get column names + types without reading all rows.
        # dataset_key is a path (e.g. "raw/orders") — map to a parquet glob or
        # the registered view name if available.
        result = connector.execute(f"DESCRIBE SELECT * FROM '{dataset_key}' LIMIT 0")
        if result is not None and result.num_rows > 0:
            col_name_idx = result.schema.get_field_index("column_name")
            col_type_idx = result.schema.get_field_index("column_type")
            if col_name_idx >= 0 and col_type_idx >= 0:
                col_names = result.column(col_name_idx).to_pylist()
                col_types = result.column(col_type_idx).to_pylist()
                return [
                    {"name": str(n), "type": str(t)}
                    for n, t in zip(col_names, col_types)
                ]
    except Exception:  # noqa: BLE001
        pass  # Connector introspection failed — return None.

    return None


# ---------------------------------------------------------------------------
# Sync wrapper for the executor (execute_job is intentionally sync)
# ---------------------------------------------------------------------------


def execute_drift_sweep_sync(
    job: dict[str, Any],
    now: datetime,
) -> tuple[int, str]:
    """Synchronous entry point called by ``execute_job`` for ``kind='drift_sweep'``.

    Runs ``run_drift_sweep`` in a dedicated thread so it is safe to call from
    the sync executor regardless of whether the caller is inside an asyncio
    event loop or not.

    Returns
    -------
    tuple[int, str]
        ``(changed_count, message)`` on success.

    Raises
    ------
    Any exception from ``run_drift_sweep`` is propagated; the executor's
    try/except converts it to an error run.
    """
    import concurrent.futures  # noqa: PLC0415

    org_id: str = str(job.get("org_id") or "")
    if not org_id:
        raise ValueError("drift_sweep job missing org_id")

    async def _run() -> dict[str, Any]:
        return await run_drift_sweep(org_id, now)

    # Always run in a dedicated thread so asyncio.run() starts a fresh event
    # loop — safe both when there IS an outer event loop (FastAPI request
    # handler calling run_now → execute_job → here) and when there is not
    # (the background scheduler tick, which IS async, never calls execute_job
    # directly — it calls run_due_jobs which calls execute_job in the loop).
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run())
        summary = future.result()

    changed = summary.get("changed", 0)
    evaluated = summary.get("evaluated", 0)
    errors = summary.get("errors", 0)
    message = (
        f"Drift sweep complete: org={org_id!r}, "
        f"evaluated={evaluated}, changed={changed}, errors={errors}."
    )
    return changed, message
