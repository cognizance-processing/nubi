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

import itertools
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


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

    def diff_surface(self) -> list[dict[str, Any]]:
        """Return the comparison surface — one entry per successful cell.

        Each entry is ``{index, params, outputs}`` where *outputs* is a dict of
        task_key → result dict.  Consumers diff the outputs across param sets
        to see how different inputs affect each task's output.
        """
        return [
            {
                "index": c.index,
                "params": c.params,
                "run_id": c.run_id,
                "outputs": c.outputs,
            }
            for c in self.cells
            if c.state == "success"
        ]


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


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Expand a param grid dict into a full Cartesian product of param sets.

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
    keys = list(grid.keys())
    value_lists = [grid[k] for k in keys]
    result: list[dict[str, Any]] = []
    for combo in itertools.product(*value_lists):
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
        resolved_sets = expand_grid(grid)
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

    for idx, params in enumerate(resolved_sets):
        # Tag params with the sweep_id so ops UI can group runs.
        tagged_params = {**params, "__sweep_id__": sweep_id, "__sweep_index__": idx}

        run_id = "?"
        cell_state = "error"
        outputs: dict[str, Any] = {}
        error: str | None = None

        try:
            flow_run = await materialize_flow_run(
                store, flow, tagged_params, trigger, now
            )
            run_id = flow_run["id"]
            final_run = await drain_flow_run(store, run_id, now, claims=claims)
            cell_state = final_run.get("state", "failed")

            # Collect task outputs (success tasks).
            task_runs = await store.list_task_runs(run_id)
            for tr in task_runs:
                if tr.get("state") == "success" and tr.get("result") is not None:
                    outputs[tr["task_key"]] = tr["result"]

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
            outputs=outputs,
            error=error,
        ))

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

    windows = _iter_windows(start, end, window)

    if len(windows) > max_windows:
        raise ValueError(
            f"Backfill range too large: {len(windows)} windows exceeds max_windows={max_windows}."
        )

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
            final_run = await drain_flow_run(store, run_id, now, claims=claims)
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
