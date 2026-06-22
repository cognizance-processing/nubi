"""B5 — Scenario sweep + backfill runner.

Public API
----------
run_sweep(store, flow, param_sets, trigger, now, claims) -> SweepResult
    Run a flow over N param sets (the matrix), collecting each run's outputs
    and producing a comparison/diff surface keyed by param set index.
    Each sweep cell is a full flow run with its own run_id, params_snapshot,
    and seed (via materialize_flow_run + drain_flow_run).

run_backfill(store, flow, start, end, window, trigger, now, claims) -> BackfillResult
    Re-run a flow over a date range, iterating the date windows (one run per
    window).  Reuses stored watermarks where present so each window is
    incremental-aware.  Windows are half-open: [window_start, window_end).

Design notes
------------
- Both runners REUSE materialize_flow_run + drain_flow_run — no new engine code.
- Each cell/window is a real flow_run row with run lineage (trigger='sweep' /
  trigger='backfill').  Linked back to the sweep/backfill request via
  ``params.__sweep_id__`` / ``params.__backfill_id__`` so the ops UI can group
  them.
- Best-effort isolation: a failing cell/window is recorded but does NOT abort
  the rest of the matrix/range.
- Param grid expansion: if the caller passes ``grid`` (a dict of
  name → [values]) rather than ``param_sets`` (a list of dicts), the runner
  expands it into the full Cartesian product.

Trigger strings used by the engine
-----------------------------------
- ``'sweep'``    — param-sweep run (each matrix cell).
- ``'backfill'`` — date-range backfill run (each window).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resource cap: max drain steps per sweep/backfill cell.
#
# A single runaway cell flow (e.g. an infinite retry loop) must not block the
# entire sweep/backfill request.  This cap limits how many task-run steps
# drain_flow_run will execute for each cell.  Override via env var for longer
# flows (e.g. NUBI_SWEEP_CELL_DRAIN_MAX_STEPS=500).
# ---------------------------------------------------------------------------
_SWEEP_CELL_DRAIN_MAX_STEPS: int = int(
    os.environ.get("NUBI_SWEEP_CELL_DRAIN_MAX_STEPS", "50")
)

# ---------------------------------------------------------------------------
# Diff-surface result blob cap.
#
# diff_surface() ships per-cell task results across the wire.  A sweep over N
# param sets each producing large result dicts would return an unbounded payload
# — O(N * result_size).  This cap limits the JSON-serialised size of each
# per-task result dict included in the surface.  Results that exceed the cap are
# replaced with a compact summary containing the size and a truncation flag so
# consumers can still diff structural metadata without pulling megabyte blobs.
#
# Override via NUBI_SWEEP_DIFF_RESULT_BYTES_CAP (bytes).  Default: 64 KiB per
# task result, consistent with the run-history blob budget.
# ---------------------------------------------------------------------------
_SWEEP_DIFF_RESULT_BYTES_CAP: int = int(
    os.environ.get("NUBI_SWEEP_DIFF_RESULT_BYTES_CAP", str(64 * 1024))
)


# ---------------------------------------------------------------------------
# Data-classes for structured results
# ---------------------------------------------------------------------------


@dataclass
class SweepCellResult:
    """Result for a single param-set cell in a sweep."""

    index: int
    params: dict[str, Any]
    run_id: str
    state: str  # 'success' | 'failed' | 'error'
    outputs: dict[str, Any]  # task_key → result dict (success tasks only)
    error: str | None = None


@dataclass
class SweepResult:
    """Aggregated result of a param sweep."""

    sweep_id: str
    flow_id: str
    total: int
    succeeded: int
    failed: int
    cells: list[SweepCellResult] = field(default_factory=list)

    def diff_surface(
        self,
        result_bytes_cap: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return the comparison surface — one entry per successful cell.

        Each entry is ``{index, params, run_id, outputs}`` where *outputs* is a
        dict of task_key → capped result.  Results whose JSON representation
        exceeds *result_bytes_cap* bytes are replaced with a compact summary::

            {
                "__truncated__": True,
                "size_bytes": <original size>,
                "cap_bytes": <cap>,
            }

        This keeps the payload bounded (O(N * cap) not O(N * result_size)) while
        preserving enough metadata for consumers to diff structural information
        across param sets without shipping raw megabyte blobs.

        Parameters
        ----------
        result_bytes_cap:
            Per-task-result byte cap for JSON serialisation.  Defaults to
            ``_SWEEP_DIFF_RESULT_BYTES_CAP`` (64 KiB, env-overridable via
            ``NUBI_SWEEP_DIFF_RESULT_BYTES_CAP``).  Pass ``0`` or a negative
            value to disable capping entirely (useful in tests that need raw
            results).
        """
        cap = result_bytes_cap if result_bytes_cap is not None else _SWEEP_DIFF_RESULT_BYTES_CAP

        def _cap_result(result: Any) -> Any:
            """Return result as-is or replace with a truncation summary."""
            if cap <= 0:
                # Capping disabled — return raw value.
                return result
            try:
                serialised = json.dumps(result, default=str)
            except Exception:  # noqa: BLE001
                serialised = str(result)
            size = len(serialised.encode("utf-8"))
            if size <= cap:
                return result
            return {
                "__truncated__": True,
                "size_bytes": size,
                "cap_bytes": cap,
            }

        surface = []
        for c in self.cells:
            if c.state != "success":
                continue
            capped_outputs = {
                task_key: _cap_result(result)
                for task_key, result in c.outputs.items()
            }
            surface.append(
                {
                    "index": c.index,
                    "params": c.params,
                    "run_id": c.run_id,
                    "outputs": capped_outputs,
                }
            )
        return surface


@dataclass
class BackfillWindowResult:
    """Result for a single date window in a backfill."""

    index: int
    window_start: datetime
    window_end: datetime
    run_id: str
    state: str  # 'success' | 'failed' | 'error' | 'skipped'
    error: str | None = None


@dataclass
class BackfillResult:
    """Aggregated result of a date-range backfill."""

    backfill_id: str
    flow_id: str
    total: int
    succeeded: int
    failed: int
    skipped: int
    windows: list[BackfillWindowResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Grid expansion helper
# ---------------------------------------------------------------------------


def expand_grid(
    grid: dict[str, list[Any]],
    *,
    max_cells: int | None = None,
) -> list[dict[str, Any]]:
    """Expand a param grid dict into a full Cartesian product of param sets.

    Parameters
    ----------
    grid:
        Mapping of param name → list of values to combine.
    max_cells:
        Optional cap on the number of cells.  When supplied the function raises
        ``ValueError`` as soon as the running product would exceed the cap,
        so the full Cartesian product is **never materialised** if it would be
        too large.  ``run_sweep`` always passes its effective cap here so the
        memory guard fires *before* the post-expansion length check.

    Example
    -------
    >>> expand_grid({"region": ["ZA", "NG"], "date": ["2025-01-01", "2025-02-01"]})
    [
        {"region": "ZA", "date": "2025-01-01"},
        {"region": "ZA", "date": "2025-02-01"},
        {"region": "NG", "date": "2025-01-01"},
        {"region": "NG", "date": "2025-02-01"},
    ]
    """
    if not grid:
        return [{}]

    # Enforce the cap *during* expansion so a huge grid never materialises.
    # We track the running count and raise as soon as it would exceed the cap.
    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]
    result: list[dict[str, Any]] = []
    for combo in itertools.product(*value_lists):
        if max_cells is not None and len(result) >= max_cells:
            raise ValueError(
                f"Sweep matrix too large: grid would produce more than "
                f"{max_cells} cells (max_cells={max_cells}). "
                "Reduce the grid dimensions or raise max_cells."
            )
        result.append(dict(zip(keys, combo)))
    return result


# ---------------------------------------------------------------------------
# run_sweep
# ---------------------------------------------------------------------------


async def run_sweep(
    store: Any,
    flow: dict[str, Any],
    param_sets: list[dict[str, Any]] | None,
    trigger: str,
    now: datetime,
    claims: dict[str, Any] | None = None,
    *,
    grid: dict[str, list[Any]] | None = None,
    max_cells: int = 200,
) -> SweepResult:
    """Run a flow over N param sets, collect outputs, return a diff surface.

    Parameters
    ----------
    store:
        Flow store instance.
    flow:
        The flow dict (from ``store.get_flow``).
    param_sets:
        A list of param dicts — one flow run per dict.  Mutually exclusive with
        *grid* (supply one or the other; *param_sets* takes precedence).
    trigger:
        Trigger string stored on each run (usually ``'sweep'`` or ``'manual'``).
    now:
        Injected clock datetime.
    claims:
        Caller's auth claims (RLS enforced by query/agent handlers).
    grid:
        Param grid dict ``{name: [v1, v2, ...]}``; expanded via
        ``expand_grid`` when *param_sets* is None.
    max_cells:
        Safety cap — refuse to run more than this many param sets.

    Returns
    -------
    SweepResult
        Structured result with per-cell outcomes and a diff surface.
    """
    from app.flows.runtime import drain_flow_run, materialize_flow_run  # noqa: PLC0415

    if claims is None:
        claims = {}

    # Resolve param sets (grid expansion when needed).
    resolved_sets: list[dict[str, Any]]
    if param_sets is not None:
        resolved_sets = list(param_sets)
    elif grid:
        # Pass max_cells so the cap fires DURING expansion, not after a
        # potentially enormous list has already been materialised.
        resolved_sets = expand_grid(grid, max_cells=max_cells)
    else:
        resolved_sets = [{}]  # single run with default params

    if len(resolved_sets) > max_cells:
        raise ValueError(
            f"Sweep matrix too large: {len(resolved_sets)} cells exceeds max_cells={max_cells}."
        )

    sweep_id = str(uuid.uuid4())
    flow_id = flow["id"]
    cells: list[SweepCellResult] = []
    succeeded = 0
    failed = 0

    # Phase 1: drain each cell sequentially; record outcomes but defer output
    # collection for successful cells until after the loop.  This lets us batch
    # all list_task_runs calls into a single asyncio.gather in Phase 2 instead
    # of issuing one blocking query per successful cell inside the loop (N+1).
    _pending_output_cells: list[int] = []  # indices of successful cells needing outputs

    for idx, params in enumerate(resolved_sets):
        # Tag params with the sweep_id so ops UI can group runs.
        tagged_params = {**params, "__sweep_id__": sweep_id, "__sweep_index__": idx}

        run_id = "?"
        cell_state = "error"
        error: str | None = None

        try:
            flow_run = await materialize_flow_run(
                store, flow, tagged_params, trigger, now
            )
            run_id = flow_run["id"]
            final_run = await drain_flow_run(
                store, run_id, now, claims=claims,
                max_steps=_SWEEP_CELL_DRAIN_MAX_STEPS,
            )
            cell_state = final_run.get("state", "failed")

            if cell_state == "success":
                succeeded += 1
            else:
                failed += 1
                error = final_run.get("error")

        except Exception as exc:  # noqa: BLE001
            # Best-effort: record the failure and continue.
            logger.warning(
                "Sweep %s cell %d failed with exception: %s",
                sweep_id, idx, exc,
            )
            failed += 1
            error = str(exc)
            cell_state = "error"

        cells.append(SweepCellResult(
            index=idx,
            params=dict(params),
            run_id=run_id,
            state=cell_state,
            outputs={},  # populated in Phase 2 for successful cells
            error=error,
        ))
        if cell_state == "success":
            _pending_output_cells.append(idx)

    # Phase 2: batch-collect outputs for all successful cells in one gather.
    # A single asyncio.gather issues all list_task_runs calls concurrently so
    # the per-cell output read is bounded to a single concurrent round-trip
    # rather than O(cells) sequential blocking queries.
    if _pending_output_cells:
        run_ids_to_fetch = [cells[i].run_id for i in _pending_output_cells]
        task_run_lists = await asyncio.gather(
            *(store.list_task_runs(rid) for rid in run_ids_to_fetch)
        )
        for cell_idx, task_runs in zip(_pending_output_cells, task_run_lists):
            outputs: dict[str, Any] = {}
            for tr in task_runs:
                if tr.get("state") == "success" and tr.get("result") is not None:
                    outputs[tr["task_key"]] = tr["result"]
            cells[cell_idx].outputs = outputs

    return SweepResult(
        sweep_id=sweep_id,
        flow_id=flow_id,
        total=len(resolved_sets),
        succeeded=succeeded,
        failed=failed,
        cells=cells,
    )


# ---------------------------------------------------------------------------
# Backfill window helpers
# ---------------------------------------------------------------------------


def _iter_windows(
    start: datetime,
    end: datetime,
    window: str,
    max_windows: int | None = None,
) -> list[tuple[datetime, datetime]]:
    """Iterate date windows over [start, end) with the given step.

    Parameters
    ----------
    start:
        Inclusive start of the range (tz-aware UTC).
    end:
        Exclusive end of the range (tz-aware UTC).
    window:
        Window size expressed as ``'Nd'`` (days), ``'Nh'`` (hours),
        ``'Nm'`` (minutes), or an ISO-8601 duration like ``'P1D'``,
        ``'PT1H'``, ``'PT30M'``.  Shorthand: ``'daily'`` = 1 day,
        ``'weekly'`` = 7 days, ``'hourly'`` = 1 hour.
    max_windows:
        When set, the cap is enforced INSIDE the loop: a ``ValueError`` is
        raised as soon as the count would exceed the cap, before the full
        list is materialised.  This prevents OOM on huge date ranges.

    Returns
    -------
    list of (window_start, window_end) tuples (exclusive end).
    """
    delta = _parse_window(window)
    if delta.total_seconds() <= 0:
        raise ValueError(f"window must be a positive duration; got {window!r}.")

    windows: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        if max_windows is not None and len(windows) >= max_windows:
            raise ValueError(
                f"Backfill range too large: would exceed max_windows={max_windows}."
            )
        w_end = min(cursor + delta, end)
        windows.append((cursor, w_end))
        cursor = cursor + delta

    return windows


def _parse_window(window: str) -> timedelta:
    """Parse a window string into a timedelta.

    Supported forms:
    - ``'Nd'`` / ``'N days'`` — N days
    - ``'Nh'`` / ``'N hours'`` / ``'N hour'`` — N hours
    - ``'Nm'`` / ``'N minutes'`` / ``'N minute'`` — N minutes
    - ``'daily'`` — 1 day
    - ``'weekly'`` — 7 days
    - ``'hourly'`` — 1 hour
    - ISO-8601 basic duration strings: ``'P1D'``, ``'PT1H'``, ``'PT30M'``,
      ``'PT12H'``, ``'P7D'``, etc.
    """
    import re  # noqa: PLC0415

    w = window.strip().lower()

    # Named shorthands.
    _named = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1), "hourly": timedelta(hours=1)}
    if w in _named:
        return _named[w]

    # "Nd" / "N days" etc.
    m = re.match(r"^(\d+)\s*(d|day|days)$", w)
    if m:
        return timedelta(days=int(m.group(1)))
    m = re.match(r"^(\d+)\s*(h|hour|hours)$", w)
    if m:
        return timedelta(hours=int(m.group(1)))
    m = re.match(r"^(\d+)\s*(m|min|minute|minutes)$", w)
    if m:
        return timedelta(minutes=int(m.group(1)))

    # ISO-8601 basic: P<n>D / PT<n>H / PT<n>M
    m = re.match(r"^p(\d+)d$", w)
    if m:
        return timedelta(days=int(m.group(1)))
    m = re.match(r"^pt(\d+)h$", w)
    if m:
        return timedelta(hours=int(m.group(1)))
    m = re.match(r"^pt(\d+)m$", w)
    if m:
        return timedelta(minutes=int(m.group(1)))

    raise ValueError(
        f"Cannot parse window {window!r}. "
        "Use '1d', '2h', '30m', 'daily', 'weekly', 'hourly', or ISO-8601 "
        "'P1D' / 'PT1H' / 'PT30M'."
    )


# ---------------------------------------------------------------------------
# run_backfill
# ---------------------------------------------------------------------------


async def run_backfill(
    store: Any,
    flow: dict[str, Any],
    start: datetime,
    end: datetime,
    window: str,
    trigger: str,
    now: datetime,
    claims: dict[str, Any] | None = None,
    *,
    max_windows: int = 500,
    extra_params: dict[str, Any] | None = None,
) -> BackfillResult:
    """Re-run a flow over a date range, one run per window.

    For each window ``[window_start, window_end)`` the flow is triggered with
    params ``{__window_start__, __window_end__, __backfill_id__, ...}`` so the
    flow spec's cells can reference the window bounds (e.g. an incremental SQL
    cell that filters ``WHERE updated_at >= {{ params.__window_start__ }}``).

    Watermarks are intentionally NOT advanced during backfill runs so the
    backfill does not corrupt incremental state for future live runs.  (The
    backfill trigger can be distinguished from the live trigger via
    ``params.__backfill_id__``.)

    Parameters
    ----------
    store:
        Flow store instance.
    flow:
        The flow dict (from ``store.get_flow``).
    start:
        Inclusive start of the backfill range (UTC).
    end:
        Exclusive end of the backfill range (UTC).
    window:
        Window size string (e.g. ``'1d'``, ``'daily'``, ``'PT1H'``).
    trigger:
        Trigger string stored on each run (usually ``'backfill'``).
    now:
        Injected clock datetime.
    claims:
        Caller's auth claims.
    max_windows:
        Safety cap — refuse to run more than this many windows.
    extra_params:
        Additional params merged into each window's run params.

    Returns
    -------
    BackfillResult
    """
    from app.flows.runtime import drain_flow_run, materialize_flow_run  # noqa: PLC0415

    if claims is None:
        claims = {}

    # Ensure tz-aware.
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    # The cap is enforced INSIDE _iter_windows — it raises ValueError before
    # the full list is materialised, preventing OOM on huge date ranges.
    windows = _iter_windows(start, end, window, max_windows=max_windows)

    backfill_id = str(uuid.uuid4())
    flow_id = flow["id"]
    window_results: list[BackfillWindowResult] = []
    succeeded = 0
    failed = 0
    skipped = 0

    for idx, (w_start, w_end) in enumerate(windows):
        run_id = "?"
        win_state = "error"
        error: str | None = None

        try:
            params: dict[str, Any] = {
                "__backfill_id__": backfill_id,
                "__backfill_index__": idx,
                "__window_start__": w_start.isoformat(),
                "__window_end__": w_end.isoformat(),
            }
            if extra_params:
                params.update(extra_params)

            flow_run = await materialize_flow_run(
                store, flow, params, trigger, now
            )
            run_id = flow_run["id"]
            final_run = await drain_flow_run(
                store, run_id, now, claims=claims,
                max_steps=_SWEEP_CELL_DRAIN_MAX_STEPS,
            )
            win_state = final_run.get("state", "failed")

            if win_state == "success":
                succeeded += 1
            else:
                failed += 1
                error = final_run.get("error")

        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Backfill %s window %d (%s → %s) failed: %s",
                backfill_id, idx, w_start.isoformat(), w_end.isoformat(), exc,
            )
            failed += 1
            error = str(exc)
            win_state = "error"

        window_results.append(BackfillWindowResult(
            index=idx,
            window_start=w_start,
            window_end=w_end,
            run_id=run_id,
            state=win_state,
            error=error,
        ))

    return BackfillResult(
        backfill_id=backfill_id,
        flow_id=flow_id,
        total=len(windows),
        succeeded=succeeded,
        failed=failed,
        skipped=skipped,
        windows=window_results,
    )
