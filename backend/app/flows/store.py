"""Flow store implementations — InMemoryFlowStore (tests) + PgFlowStore (prod).

``InMemoryFlowStore`` is a dict-backed store for :class:`Flow`,
:class:`FlowRun`, and :class:`TaskRun` records.  It is the primary store
used in tests.

``PgFlowStore`` is the asyncpg-backed production store that maps each method
to a parameterised SQL query against the ``flows``, ``flow_runs``, and
``task_runs`` tables (from 0004_flows.sql).  Rows are converted to plain
dicts; jsonb and datetime values match the shape produced by
``InMemoryFlowStore``.

Provider
--------
``get_flow_store()`` returns the configured singleton store.  By default it
returns a ``PgFlowStore`` (suitable for production); tests inject an
``InMemoryFlowStore`` via ``set_flow_store(store)``.  This mirrors the
pattern used in ``app/jobs/store.py``.

Design
------
- All mutation methods use ``uuid.uuid4()`` and ``datetime.now(timezone.utc)``
  **at call time only** — never at module/class import time.
- ``set_flow_store()`` lets tests swap the singleton for an injected store
  without touching route signatures.
- ``InMemoryFlowStore`` uses ``deepcopy`` for all returned objects so that
  callers cannot mutate internal state.
- Datetimes are always tz-aware UTC; uuids are strings.
"""

from __future__ import annotations

import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Module-level cap: maximum flows returned by list_flows.
# Override with the NUBI_MAX_FLOWS environment variable.
# ---------------------------------------------------------------------------
_MAX_FLOWS_DEFAULT = 1000
_NUBI_MAX_FLOWS: int = int(os.environ.get("NUBI_MAX_FLOWS", _MAX_FLOWS_DEFAULT))

# ---------------------------------------------------------------------------
# Per-run task_run ceiling — must match runtime.py's _MAX_TASK_RUNS_PER_RUN.
# Both read the same env var so they stay in sync without a cross-import.
# Used to bound list_task_run_results so a drain step never loads all 50k blobs.
# ---------------------------------------------------------------------------
_MAX_TASK_RUNS_PER_RUN: int = int(os.environ.get("NUBI_MAX_TASK_RUNS_PER_RUN", 50_000))


def _seed_from_run_id(run_id: str) -> int:
    """Derive a deterministic integer seed from a UUID run_id string.

    Uses the first 8 hex digits of the UUID (before any dashes) as a 32-bit
    unsigned integer, masked to fit a signed 32-bit range so it is safe to
    pass as a Python random/numpy seed.

    This is a pure function — given the same run_id it always returns the same
    seed — which is the reproducibility guarantee we need: a stochastic cell
    retried within the SAME run produces the same result; different runs (with
    different run_ids) get different seeds.
    """
    hex_digits = run_id.replace("-", "")[:8]
    try:
        return int(hex_digits, 16) & 0x7FFFFFFF
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

Flow = dict[str, Any]
FlowRun = dict[str, Any]
TaskRun = dict[str, Any]


# ---------------------------------------------------------------------------
# InMemoryFlowStore
# ---------------------------------------------------------------------------


class InMemoryFlowStore:
    """Dict-backed store for flows, flow_runs, and task_runs.

    All timestamps are ``datetime`` objects with UTC timezone.

    Flow shape
    ----------
    ``{id, org_id, created_by, name, spec(dict), version, enabled,
    schedule, next_run_at, last_run_at, created_at, updated_at}``

    FlowRun shape
    -------------
    ``{id, flow_id, org_id, state, params(dict), trigger,
    scheduled_at, started_at, finished_at, error, created_at}``

    TaskRun shape
    -------------
    ``{id, flow_run_id, org_id, task_key, state, attempt,
    depends_on(list[str]), cache_key, result(dict|None), error,
    logs(list[str]), scheduled_at, started_at, finished_at, created_at,
    parent_task_run_id(str|None), branch_taken(str|None)}``

    ``parent_task_run_id`` — for map child task_runs, points to the parent
    map task_run.  NULL for all other task_runs.

    ``branch_taken`` — for branch task_runs, stores the branch label that was
    taken (e.g. ``"condition_0"``, ``"default"``).  NULL for all other
    task_runs.
    """

    def __init__(self) -> None:
        self._flows: dict[str, Flow] = {}
        self._flow_runs: dict[str, FlowRun] = {}           # run_id → FlowRun
        self._flow_run_index: dict[str, list[str]] = {}    # flow_id → [run_id]
        self._task_runs: dict[str, TaskRun] = {}           # task_run_id → TaskRun
        self._task_run_index: dict[str, list[str]] = {}    # flow_run_id → [task_run_id]
        # Incremental materialization watermarks keyed by (flow_id, model_key,
        # env) → ISO watermark string.  Mirrors the flow_watermarks Pg table.
        self._watermarks: dict[tuple[str, str, str], str] = {}
        # B2: data-lineage output records keyed by id.
        self._run_outputs: dict[str, dict] = {}            # output_id → output record
        self._run_output_index: dict[str, list[str]] = {}  # run_id → [output_id]

    # ------------------------------------------------------------------
    # Flow operations
    # ------------------------------------------------------------------

    async def create_flow(
        self,
        org_id: str,
        created_by: str,
        name: str,
        spec: dict[str, Any],
        enabled: bool = True,
        schedule: str | None = None,
        next_run_at: datetime | None = None,
        project_id: str | None = None,
    ) -> Flow:
        """Create and store a new flow; return the stored dict."""
        flow_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        flow: Flow = {
            "id": flow_id,
            "org_id": str(org_id),
            "project_id": str(project_id) if project_id is not None else None,
            "created_by": str(created_by),
            "name": name,
            "spec": deepcopy(spec),
            "version": 1,
            "enabled": enabled,
            "schedule": schedule,
            "next_run_at": next_run_at,
            "last_run_at": None,
            "created_at": now,
            "updated_at": now,
        }
        self._flows[flow_id] = flow
        self._flow_run_index[flow_id] = []
        return deepcopy(flow)

    async def get_flow(self, flow_id: str) -> Flow | None:
        """Return a copy of the flow, or ``None`` if not found."""
        flow = self._flows.get(str(flow_id))
        return deepcopy(flow) if flow is not None else None

    async def list_flows(
        self,
        org_id: str,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[Flow]:
        """Return flows belonging to *org_id*, sorted by created_at ASC.

        Parameters
        ----------
        org_id:
            The owning organisation.
        project_id:
            When provided the result is additionally scoped to that project;
            when ``None`` all of the org's flows are returned.
        limit:
            Maximum number of rows to return.  Defaults to
            ``NUBI_MAX_FLOWS`` (env, default 1000) so the entire org's
            spec blobs are never loaded into RAM unboundedly.  Pass an
            explicit value to override (e.g. a tighter per-route cap).
        """
        effective_limit = limit if limit is not None else _NUBI_MAX_FLOWS
        rows = [
            deepcopy(f)
            for f in self._flows.values()
            if str(f["org_id"]) == str(org_id)
            and (
                project_id is None
                or str(f.get("project_id")) == str(project_id)
            )
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows[:effective_limit]

    async def update_flow(self, flow_id: str, fields: dict[str, Any]) -> Flow | None:
        """Update mutable fields on a flow in-place; return the updated copy.

        Returns ``None`` if the flow does not exist.
        """
        flow = self._flows.get(str(flow_id))
        if flow is None:
            return None
        for key, val in fields.items():
            flow[key] = val
        flow["updated_at"] = datetime.now(timezone.utc)
        return deepcopy(flow)

    async def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow and its runs; return ``True`` if deleted."""
        flow_id = str(flow_id)
        if flow_id not in self._flows:
            return False
        del self._flows[flow_id]
        for run_id in self._flow_run_index.pop(flow_id, []):
            self._flow_runs.pop(run_id, None)
            for tr_id in self._task_run_index.pop(run_id, []):
                self._task_runs.pop(tr_id, None)
        return True

    async def list_due_scheduled_flows(self, now: datetime) -> list[Flow]:
        """Return enabled, scheduled flows whose ``next_run_at`` is due (<= now)."""
        due: list[Flow] = []
        for flow in self._flows.values():
            if not flow.get("enabled", True):
                continue
            if not flow.get("schedule"):
                continue
            next_run_at = flow.get("next_run_at")
            if next_run_at is not None:
                if getattr(next_run_at, "tzinfo", None) is None:
                    next_run_at = next_run_at.replace(tzinfo=timezone.utc)
                if next_run_at > now:
                    continue
            due.append(deepcopy(flow))
        return due

    async def claim_due_scheduled_flow(
        self, flow_id: str, now: datetime, next_run_at: datetime | None
    ) -> Flow | None:
        """Atomically claim a due scheduled flow's slot; return the flow or None.

        In-memory store: single-threaded, no contention.  We re-check the due
        condition (``next_run_at <= now``) and, if still due, advance
        ``next_run_at`` / set ``last_run_at`` and return the claimed flow.  A
        second caller for the same tick will find ``next_run_at`` already
        advanced and get ``None`` — mirroring the Pg atomic-claim semantics so
        the materialize path never double-runs.
        """
        flow = self._flows.get(str(flow_id))
        if flow is None:
            return None
        cur = flow.get("next_run_at")
        if cur is not None:
            if getattr(cur, "tzinfo", None) is None:
                cur = cur.replace(tzinfo=timezone.utc)
            if cur > now:
                return None  # already advanced by another claim this tick
        flow["next_run_at"] = next_run_at
        flow["last_run_at"] = now
        flow["updated_at"] = datetime.now(timezone.utc)
        return deepcopy(flow)

    # ------------------------------------------------------------------
    # FlowRun operations
    # ------------------------------------------------------------------

    async def create_flow_run(
        self,
        flow_id: str,
        org_id: str,
        params: dict[str, Any],
        trigger: str,
        scheduled_at: datetime | None = None,
        env: str = "prod",
        seed: int | None = None,
        code_version: dict[str, Any] | None = None,
        params_snapshot: dict[str, Any] | None = None,
    ) -> FlowRun:
        """Create and store a new flow_run; return the stored dict.

        B2 additions
        ------------
        ``seed``:
            Run-level integer seed for stochastic cells.  Derived from the UUID
            run_id by convention (``int(run_id_hex[:8], 16) & 0x7FFFFFFF``) when
            not explicitly supplied.  Stored on the row so reproducibility probes
            can request a specific seed.
        ``code_version``:
            Snapshot of the flow spec version/hash at trigger time.
        ``params_snapshot``:
            Full copy of the resolved params at trigger time.
        """
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Derive seed from the run_id when not explicitly provided.
        if seed is None:
            seed = _seed_from_run_id(run_id)

        run: FlowRun = {
            "id": run_id,
            "flow_id": str(flow_id),
            "org_id": str(org_id),
            "state": "pending",
            "params": deepcopy(params),
            "trigger": trigger,
            "scheduled_at": scheduled_at,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "created_at": now,
            "env": env or "prod",
            # B2 lineage fields
            "seed": seed,
            "code_version": deepcopy(code_version) if code_version is not None else None,
            "params_snapshot": deepcopy(params_snapshot if params_snapshot is not None else params),
        }
        self._flow_runs[run_id] = run
        self._flow_run_index.setdefault(str(flow_id), []).append(run_id)
        self._task_run_index[run_id] = []
        self._run_output_index[run_id] = []
        return deepcopy(run)

    async def get_flow_run(self, run_id: str) -> FlowRun | None:
        """Return a copy of the flow_run, or ``None`` if not found."""
        run = self._flow_runs.get(str(run_id))
        return deepcopy(run) if run is not None else None

    async def list_flow_runs(
        self, flow_id: str, limit: int = 500, offset: int = 0
    ) -> list[FlowRun]:
        """Return flow_runs for *flow_id*, newest first, bounded by *limit*.

        Parameters
        ----------
        flow_id:
            The flow whose runs are listed.
        limit:
            Maximum number of rows to return (default 500).  Callers should
            pass a tighter bound (e.g. the route's ``?limit`` query param)
            so the result set is always bounded DB-side rather than
            post-hoc in Python.
        offset:
            Number of rows to skip (for pagination, default 0).
        """
        run_ids = self._flow_run_index.get(str(flow_id), [])
        rows = [deepcopy(self._flow_runs[rid]) for rid in run_ids if rid in self._flow_runs]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[offset : offset + limit]

    async def list_run_outputs_for_runs(
        self, run_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Batch-fetch run outputs for multiple run_ids in a single pass.

        Returns a dict mapping each run_id to its list of output records
        (ordered by created_at).  Run IDs with no outputs are omitted from
        the result dict (callers should use ``.get(run_id, [])``).

        This avoids an N+1 query pattern in the run-history endpoint where
        one ``list_run_outputs`` call per run would be issued.
        """
        result: dict[str, list[dict[str, Any]]] = {}
        for run_id in run_ids:
            rid = str(run_id)
            ids = self._run_output_index.get(rid, [])
            rows = [
                deepcopy(self._run_outputs[oid])
                for oid in ids
                if oid in self._run_outputs
            ]
            if rows:
                rows.sort(key=lambda r: r["created_at"])
                result[rid] = rows
        return result

    async def update_flow_run(self, run_id: str, fields: dict[str, Any]) -> FlowRun | None:
        """Update mutable fields on a flow_run; return the updated copy.

        Returns ``None`` if the flow_run does not exist.
        """
        run = self._flow_runs.get(str(run_id))
        if run is None:
            return None
        for key, val in fields.items():
            run[key] = val
        return deepcopy(run)

    # ------------------------------------------------------------------
    # TaskRun operations
    # ------------------------------------------------------------------

    async def add_task_runs(
        self, flow_run_id: str, task_runs: list[dict[str, Any]]
    ) -> list[TaskRun]:
        """Bulk-insert task_runs for a flow_run; return the stored list.

        Each dict in *task_runs* must include at least ``task_key``,
        ``org_id``, ``state``, and ``depends_on``.  ``id`` is assigned if
        not provided.
        """
        flow_run_id = str(flow_run_id)
        stored: list[TaskRun] = []
        now = datetime.now(timezone.utc)
        for tr in task_runs:
            tr_id = str(tr.get("id") or uuid.uuid4())
            record: TaskRun = {
                "id": tr_id,
                "flow_run_id": flow_run_id,
                "org_id": str(tr.get("org_id", "")),
                "task_key": tr["task_key"],
                "state": tr.get("state", "pending"),
                "attempt": tr.get("attempt", 0),
                "depends_on": list(tr.get("depends_on", [])),
                "cache_key": tr.get("cache_key", None),
                "result": tr.get("result", None),
                "error": tr.get("error", None),
                "logs": list(tr.get("logs") or []),
                "scheduled_at": tr.get("scheduled_at", None),
                "started_at": tr.get("started_at", None),
                "finished_at": tr.get("finished_at", None),
                "created_at": tr.get("created_at", now),
                # Work-pool lease fields.
                "lease_expires_at": tr.get("lease_expires_at", None),
                "worker_id": tr.get("worker_id", None),
                # Map / branch fields.
                # parent_task_run_id: set on map child task_runs to the parent
                #   map task_run id; NULL for all other task_runs.
                "parent_task_run_id": tr.get("parent_task_run_id", None),
                # branch_taken: set on branch task_runs to the label of the
                #   condition that matched (e.g. "condition_0", "default");
                #   NULL for all other task_runs.
                "branch_taken": tr.get("branch_taken", None),
                # B1: per-cell resource request fields.
                "cpu_cores": float(tr.get("cpu_cores", 0.0) or 0.0),
                "mem_mb": int(tr.get("mem_mb", 0) or 0),
                # B2: stochastic flag (bypasses cache, receives seed preamble).
                "stochastic": bool(tr.get("stochastic", False)),
            }
            self._task_runs[tr_id] = record
            self._task_run_index.setdefault(flow_run_id, []).append(tr_id)
            stored.append(deepcopy(record))
        return stored

    async def list_task_runs(
        self, flow_run_id: str, limit: int | None = None
    ) -> list[TaskRun]:
        """Return task_runs for *flow_run_id*, ordered by created_at then task_key.

        Parameters
        ----------
        flow_run_id:
            The flow run whose task_runs are listed.
        limit:
            Maximum number of rows to return.  When ``None`` (default) ALL
            rows are returned — callers should supply a bound when using this
            in a response path to avoid unbounded serialization.  SQL-level
            LIMIT equivalent for the in-memory store.
        """
        tr_ids = self._task_run_index.get(str(flow_run_id), [])
        rows = [
            deepcopy(self._task_runs[tid])
            for tid in tr_ids
            if tid in self._task_runs
        ]
        rows.sort(key=lambda r: (r["created_at"], r["task_key"]))
        if limit is not None:
            return rows[:limit]
        return rows

    async def count_task_runs(self, flow_run_id: str) -> int:
        """Return the number of task_runs belonging to *flow_run_id*.

        O(1)-ish (index length) count used at map fan-out time to enforce the
        per-run task_run ceiling WITHOUT loading + deepcopying every row.
        """
        return len(self._task_run_index.get(str(flow_run_id), []))

    async def list_task_run_results(
        self, flow_run_id: str
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Return ``(task_key, result)`` pairs for SUCCESS task_runs only.

        Results-only projection used by the executor's upstream-inputs path: it
        only needs the success tasks' keys + result dicts, not the full row set.
        Avoids the full ``list_task_runs`` deepcopy of every field for every row.

        The ``result`` value is deep-copied so callers cannot mutate internal
        state.  Ordering matches ``list_task_runs`` (created_at, then task_key).
        """
        tr_ids = self._task_run_index.get(str(flow_run_id), [])
        rows = [
            self._task_runs[tid]
            for tid in tr_ids
            if tid in self._task_runs
        ]
        rows.sort(key=lambda r: (r["created_at"], r["task_key"]))
        return [
            (r["task_key"], deepcopy(r["result"]))
            for r in rows
            if r["state"] == "success" and r.get("result") is not None
        ][:_MAX_TASK_RUNS_PER_RUN]

    async def get_task_run(self, task_run_id: str) -> TaskRun | None:
        """Return a copy of the task_run, or ``None`` if not found."""
        tr = self._task_runs.get(str(task_run_id))
        return deepcopy(tr) if tr is not None else None

    async def get_task_run_by_key(
        self, flow_run_id: str, task_key: str
    ) -> TaskRun | None:
        """Return the task_run for *flow_run_id* / *task_key*, or ``None``.

        O(k) where k is the number of task_runs in this flow_run (uses the
        flow_run_id index to avoid a full-store scan).
        """
        tr_ids = self._task_run_index.get(str(flow_run_id), [])
        for tid in tr_ids:
            tr = self._task_runs.get(tid)
            if tr is not None and tr["task_key"] == task_key:
                return deepcopy(tr)
        return None

    async def update_task_run(self, task_run_id: str, fields: dict[str, Any]) -> TaskRun | None:
        """Update mutable fields on a task_run; return the updated copy.

        Returns ``None`` if the task_run does not exist.

        ``logs`` is accumulated (appended) rather than replaced, so successive
        updates on the same task_run accumulate all captured log lines.
        """
        tr = self._task_runs.get(str(task_run_id))
        if tr is None:
            return None
        for key, val in fields.items():
            if key == "logs" and isinstance(val, list):
                # Accumulate logs across retries rather than overwriting.
                existing = tr.get("logs") or []
                tr["logs"] = existing + val
            else:
                tr[key] = val
        return deepcopy(tr)

    async def claim_ready_task_run(
        self,
        now: datetime,
        worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> TaskRun | None:
        """Claim and mark 'running' the oldest eligible task_run.

        Eligibility: ``state in ('ready', 'retrying')`` AND (``scheduled_at``
        is None OR ``scheduled_at <= now``).  Among all eligible task_runs the
        *oldest* one (by ``scheduled_at`` — None sorts first, then by
        ``created_at``) is claimed atomically (in-memory: no contention).

        States that are explicitly NOT claimable:
        - ``pending``          — not yet unblocked by ``advance_readiness``.
        - ``running``          — already claimed by another worker.
        - ``waiting_children`` — map fan-out in progress; the map task_run
                                 transitions to ``success``/``failed`` once all
                                 child task_runs are terminal.  It must NEVER
                                 be re-claimed.
        - ``success``, ``failed``, ``timed_out``, ``upstream_failed``,
          ``skipped``, ``cancelled`` — already terminal.

        Parameters
        ----------
        now:
            Injected clock datetime.
        worker_id:
            Opaque identifier for the claiming worker (e.g. hostname + pid).
            Stored on the row so reaping can be audited.
        lease_seconds:
            Duration of the worker lease.  ``lease_expires_at`` is set to
            ``now + lease_seconds``.  Pass 0 to skip setting the lease.

        Returns
        -------
        TaskRun | None
            The updated task_run dict (state='running'), or ``None`` if no
            eligible task_run exists.
        """
        from datetime import timedelta  # noqa: PLC0415

        # Only 'ready' and 'retrying' are claimable.  'waiting_children' (map
        # fan-out in progress) must never be claimed — it is not in this set.
        candidates: list[TaskRun] = [
            tr
            for tr in self._task_runs.values()
            if tr["state"] in ("ready", "retrying")
            and (tr["scheduled_at"] is None or tr["scheduled_at"] <= now)
        ]
        if not candidates:
            return None

        # Sort: None scheduled_at first (immediate), then by scheduled_at, then created_at.
        def _sort_key(tr: TaskRun):
            sa = tr["scheduled_at"]
            sa_key = (0, datetime.min.replace(tzinfo=timezone.utc)) if sa is None else (1, sa)
            return (sa_key, tr["created_at"])

        candidates.sort(key=_sort_key)
        oldest = candidates[0]

        # Mark as running, set lease fields.
        oldest["state"] = "running"
        oldest["started_at"] = now
        oldest["worker_id"] = worker_id
        oldest["lease_expires_at"] = (now + timedelta(seconds=lease_seconds)) if lease_seconds else None
        return deepcopy(oldest)

    async def extend_task_lease(
        self,
        task_run_id: str,
        worker_id: str | None,
        new_expiry: datetime,
    ) -> bool:
        """Extend the worker lease on a claimed (running) task_run.

        Used by the worker heartbeat to keep a long-running task's lease
        fresh so ``reap_expired_leases`` does not re-queue it mid-execution.

        The extension is conditional: it only applies when the task_run is
        still ``'running'`` AND its stored ``worker_id`` matches *worker_id*
        (``None`` matches ``None``).  If the lease was stolen (reaped and
        re-claimed by another worker), the worker_id no longer matches and
        this is a no-op returning ``False`` — the original worker learns it
        has lost the lease.

        Parameters
        ----------
        task_run_id:
            The claimed task_run's id.
        worker_id:
            The claiming worker's id, as passed to ``claim_ready_task_run``.
        new_expiry:
            The new ``lease_expires_at`` value (injected clock + lease).

        Returns
        -------
        bool
            ``True`` if the lease was extended; ``False`` if the task_run is
            missing, not running, or owned by a different worker.
        """
        tr = self._task_runs.get(str(task_run_id))
        if tr is None:
            return False
        if tr["state"] != "running":
            return False
        if tr.get("worker_id") != worker_id:
            return False
        tr["lease_expires_at"] = new_expiry
        return True

    async def reap_expired_leases(self, now: datetime) -> int:
        """Re-queue task_runs whose worker lease has expired.

        A task_run is eligible for reaping when:
        - ``state = 'running'``
        - ``lease_expires_at`` is set AND ``lease_expires_at < now``

        Re-queued runs are transitioned back to ``'ready'`` (or ``'retrying'``
        when ``attempt > 0``) so another worker can claim them.  The
        ``lease_expires_at`` and ``worker_id`` are cleared.

        Parameters
        ----------
        now:
            Injected clock datetime.

        Returns
        -------
        int
            Number of task_runs reaped.
        """
        count = 0
        for tr in self._task_runs.values():
            if tr["state"] != "running":
                continue
            lease_exp = tr.get("lease_expires_at")
            if lease_exp is None:
                continue
            # Ensure timezone-aware comparison.
            if getattr(lease_exp, "tzinfo", None) is None:
                lease_exp = lease_exp.replace(tzinfo=timezone.utc)
            if lease_exp >= now:
                continue

            # Lease has expired — re-queue this task_run.
            attempt = int(tr.get("attempt", 0))
            new_state = "retrying" if attempt > 0 else "ready"
            tr["state"] = new_state
            tr["lease_expires_at"] = None
            tr["worker_id"] = None
            # Do NOT reset started_at or attempt — preserve run history.
            count += 1
        return count

    # ------------------------------------------------------------------
    # Incremental watermark operations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # B2: Data-lineage output records
    # ------------------------------------------------------------------

    async def add_run_output(
        self,
        flow_run_id: str,
        org_id: str,
        task_key: str,
        output_key: str,
        output_type: str = "table",
        output_uri: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record that *flow_run_id* / *task_key* produced an output.

        Parameters
        ----------
        flow_run_id:
            The flow run that produced the output.
        org_id:
            The owning org (for multi-tenant queries).
        task_key:
            Which task inside the run produced the output.
        output_key:
            Logical name for the output (e.g. materialise target or dataset name).
        output_type:
            One of ``'table'``, ``'file'``, ``'dataset'``, ``'metric'``, ``'artifact'``.
        output_uri:
            Physical URI / table path (optional).
        meta:
            Optional free-form metadata (row counts, schema hash, etc.).

        Returns
        -------
        dict
            The stored output record.
        """
        output_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        record: dict[str, Any] = {
            "id": output_id,
            "flow_run_id": str(flow_run_id),
            "org_id": str(org_id),
            "task_key": task_key,
            "output_key": output_key,
            "output_uri": output_uri,
            "output_type": output_type,
            "meta": deepcopy(meta) if meta is not None else None,
            "created_at": now,
        }
        self._run_outputs[output_id] = record
        self._run_output_index.setdefault(str(flow_run_id), []).append(output_id)
        return deepcopy(record)

    async def list_run_outputs(
        self,
        flow_run_id: str,
    ) -> list[dict[str, Any]]:
        """Return all output records for *flow_run_id*, ordered by created_at."""
        ids = self._run_output_index.get(str(flow_run_id), [])
        rows = [
            deepcopy(self._run_outputs[oid])
            for oid in ids
            if oid in self._run_outputs
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def get_run_outputs_by_key(
        self,
        org_id: str,
        output_key: str,
    ) -> list[dict[str, Any]]:
        """Return all output records matching *output_key* for an org, newest first.

        Allows answering "which flow_run produced this table?" by key.
        """
        rows = [
            deepcopy(rec)
            for rec in self._run_outputs.values()
            if str(rec["org_id"]) == str(org_id) and rec["output_key"] == output_key
        ]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows

    # ------------------------------------------------------------------
    # Incremental watermark operations
    # ------------------------------------------------------------------

    async def get_watermark(
        self, flow_id: str, model_key: str, env: str = "prod"
    ) -> str | None:
        """Return the stored incremental watermark, or ``None`` if unset."""
        return self._watermarks.get(
            (str(flow_id), str(model_key), str(env or "prod"))
        )

    async def set_watermark(
        self, flow_id: str, model_key: str, env: str, watermark: str | None
    ) -> None:
        """Upsert the incremental watermark for ``(flow_id, model_key, env)``.

        A ``None`` watermark is ignored (we never clobber a real watermark with
        an empty advance).
        """
        if watermark is None:
            return
        self._watermarks[
            (str(flow_id), str(model_key), str(env or "prod"))
        ] = str(watermark)

    async def copy_watermark(
        self, flow_id: str, model_key: str, src_env: str, dst_env: str
    ) -> str | None:
        """Copy the watermark from *src_env* to *dst_env* (promote helper).

        Returns the copied watermark, or ``None`` if the source had none.
        """
        wm = await self.get_watermark(flow_id, model_key, src_env)
        if wm is not None:
            await self.set_watermark(flow_id, model_key, dst_env, wm)
        return wm


# ---------------------------------------------------------------------------
# PgFlowStore — asyncpg-backed production implementation
# ---------------------------------------------------------------------------


def _row_to_flow(row: Any) -> Flow:
    """Convert an asyncpg Record (or dict) to a Flow dict.

    Ensures:
    - All UUIDs are strings.
    - ``datetime`` values retain their timezone info (asyncpg returns
      timezone-aware datetimes for ``timestamptz`` columns).
    - ``None`` values for nullable columns are preserved.
    - ``spec`` jsonb is returned as a Python dict.
    """
    d = dict(row)
    for key in ("id", "org_id", "project_id", "created_by"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("next_run_at", "last_run_at", "created_at", "updated_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    # asyncpg returns jsonb as dict already; ensure spec is mutable.
    if "spec" in d and not isinstance(d["spec"], dict):
        import json  # noqa: PLC0415
        d["spec"] = json.loads(d["spec"])
    return d


def _row_to_flow_run(row: Any) -> FlowRun:
    """Convert an asyncpg Record (or dict) to a FlowRun dict."""
    d = dict(row)
    for key in ("id", "flow_id", "org_id"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("scheduled_at", "started_at", "finished_at", "created_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    # asyncpg returns jsonb as dict; normalise params.
    if "params" in d and not isinstance(d["params"], dict):
        import json  # noqa: PLC0415
        d["params"] = json.loads(d["params"])
    # env column; old rows / pre-migration read as "prod".
    if not d.get("env"):
        d["env"] = "prod"
    # B2 lineage columns; default for pre-migration rows.
    d.setdefault("seed", None)
    if "code_version" in d and d["code_version"] is not None and not isinstance(d["code_version"], dict):
        import json as _j  # noqa: PLC0415
        try:
            d["code_version"] = _j.loads(d["code_version"])
        except Exception:  # noqa: BLE001
            d["code_version"] = None
    else:
        d.setdefault("code_version", None)
    if "params_snapshot" in d and d["params_snapshot"] is not None and not isinstance(d["params_snapshot"], dict):
        import json as _j  # noqa: PLC0415
        try:
            d["params_snapshot"] = _j.loads(d["params_snapshot"])
        except Exception:  # noqa: BLE001
            d["params_snapshot"] = None
    else:
        d.setdefault("params_snapshot", None)
    return d


def _row_to_task_run(row: Any) -> TaskRun:
    """Convert an asyncpg Record (or dict) to a TaskRun dict."""
    d = dict(row)
    for key in ("id", "flow_run_id", "org_id"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("scheduled_at", "started_at", "finished_at", "created_at", "lease_expires_at"):
        val = d.get(key)
        if isinstance(val, datetime) and val.tzinfo is None:
            d[key] = val.replace(tzinfo=timezone.utc)
    # depends_on: asyncpg returns text[] as list[str]
    if "depends_on" in d and d["depends_on"] is None:
        d["depends_on"] = []
    # result: jsonb → dict or None
    if "result" in d and d["result"] is not None and not isinstance(d["result"], dict):
        import json  # noqa: PLC0415
        d["result"] = json.loads(d["result"])
    # logs: jsonb → list[str] or []
    if "logs" in d:
        if d["logs"] is None:
            d["logs"] = []
        elif not isinstance(d["logs"], list):
            import json as _json  # noqa: PLC0415
            try:
                d["logs"] = _json.loads(d["logs"])
            except Exception:  # noqa: BLE001
                d["logs"] = []
    else:
        d["logs"] = []
    # Ensure lease fields are present (older rows pre-migration may lack them).
    d.setdefault("lease_expires_at", None)
    d.setdefault("worker_id", None)
    # Map / branch fields; default to None for older rows.
    if "parent_task_run_id" in d and d["parent_task_run_id"] is not None:
        d["parent_task_run_id"] = str(d["parent_task_run_id"])
    else:
        d.setdefault("parent_task_run_id", None)
    d.setdefault("branch_taken", None)
    return d


def _row_to_run_output(row: Any) -> dict[str, Any]:
    """Convert an asyncpg Record to a flow_run_outputs dict."""
    d = dict(row)
    for key in ("id", "flow_run_id", "org_id"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    val = d.get("created_at")
    if isinstance(val, datetime) and val.tzinfo is None:
        d["created_at"] = val.replace(tzinfo=timezone.utc)
    if "meta" in d and d["meta"] is not None and not isinstance(d["meta"], dict):
        import json as _j  # noqa: PLC0415
        try:
            d["meta"] = _j.loads(d["meta"])
        except Exception:  # noqa: BLE001
            d["meta"] = None
    else:
        d.setdefault("meta", None)
    return d


class PgFlowStore:
    """asyncpg-backed flow store for production use.

    Uses the ``fetch`` / ``fetchrow`` / ``execute`` helpers from ``app.db``
    (which acquire a connection from the pool automatically).

    All SQL is parameterised with ``$N`` placeholders.  Column names match
    the ``flows``, ``flow_runs``, and ``task_runs`` tables from
    0004_flows.sql.

    Rows returned by asyncpg are converted to plain dicts that match the
    shape produced by ``InMemoryFlowStore``.
    """

    # ------------------------------------------------------------------
    # Flow operations
    # ------------------------------------------------------------------

    async def create_flow(
        self,
        org_id: str,
        created_by: str,
        name: str,
        spec: dict[str, Any],
        enabled: bool = True,
        schedule: str | None = None,
        next_run_at: datetime | None = None,
        project_id: str | None = None,
    ) -> Flow:
        """Insert a new flow row and return the stored dict.

        ``flows.project_id`` is NOT NULL: when the caller passes ``None`` the
        org's default project is resolved as a fallback so the insert always
        carries a project.
        """
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415
        from app.repos.pg import resolve_required_project_id  # noqa: PLC0415

        project_id = await resolve_required_project_id(org_id, project_id)

        row = await db_fetchrow(
            """
            INSERT INTO flows (org_id, created_by, name, spec, enabled,
                               schedule, next_run_at, project_id)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, $6, $7, $8::uuid)
            RETURNING *
            """,
            org_id,
            created_by,
            name,
            json.dumps(spec),
            enabled,
            schedule,
            next_run_at,
            project_id,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO flows returned no row.")
        return _row_to_flow(row)

    async def get_flow(self, flow_id: str) -> Flow | None:
        """Return the flow dict, or ``None`` if not found."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM flows WHERE id = $1::uuid",
            flow_id,
        )
        return _row_to_flow(row) if row is not None else None

    async def list_flows(
        self,
        org_id: str,
        project_id: str | None = None,
        limit: int | None = None,
    ) -> list[Flow]:
        """Return flows belonging to *org_id*, sorted by created_at ASC.

        Parameters
        ----------
        org_id:
            The owning organisation.
        project_id:
            When provided the result is additionally scoped to that project;
            when ``None`` all of the org's flows are returned.
        limit:
            Maximum rows returned.  Applied as a SQL LIMIT so the DB never
            sends an unbounded result set.  Defaults to ``NUBI_MAX_FLOWS``
            (env, default 1000).
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        effective_limit = limit if limit is not None else _NUBI_MAX_FLOWS

        if project_id is not None:
            rows = await db_fetch(
                "SELECT * FROM flows WHERE org_id = $1::uuid "
                "AND project_id = $2::uuid ORDER BY created_at ASC LIMIT $3",
                org_id,
                project_id,
                effective_limit,
            )
        else:
            rows = await db_fetch(
                "SELECT * FROM flows WHERE org_id = $1::uuid "
                "ORDER BY created_at ASC LIMIT $2",
                org_id,
                effective_limit,
            )
        return [_row_to_flow(r) for r in rows]

    async def update_flow(self, flow_id: str, fields: dict[str, Any]) -> Flow | None:
        """Update allowed fields on a flow; return the updated dict or ``None``."""
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        allowed = {"name", "spec", "enabled", "schedule", "next_run_at", "last_run_at"}
        updates: list[str] = []
        values: list[Any] = []
        param_idx = 1

        for field in ("name", "spec", "enabled", "schedule", "next_run_at", "last_run_at"):
            if field not in fields or field not in allowed:
                continue
            val = fields[field]
            if field == "spec":
                updates.append(f"{field} = ${param_idx}::jsonb")
                values.append(json.dumps(val))
            else:
                updates.append(f"{field} = ${param_idx}")
                values.append(val)
            param_idx += 1

        if not updates:
            return await self.get_flow(flow_id)

        updates.append("updated_at = now()")
        set_clause = ", ".join(updates)
        values.append(flow_id)
        id_param = param_idx

        row = await db_fetchrow(
            f"UPDATE flows SET {set_clause} WHERE id = ${id_param}::uuid RETURNING *",
            *values,
        )
        return _row_to_flow(row) if row is not None else None

    async def delete_flow(self, flow_id: str) -> bool:
        """Delete a flow (cascade to runs); return ``True`` if deleted."""
        from app.db import execute as db_execute  # noqa: PLC0415

        status = await db_execute(
            "DELETE FROM flows WHERE id = $1::uuid",
            flow_id,
        )
        try:
            count = int(status.split()[-1])
        except (ValueError, IndexError):
            count = 0
        return count > 0

    async def list_due_scheduled_flows(self, now: datetime) -> list[Flow]:
        """Return enabled, scheduled flows whose ``next_run_at`` is due (<= now)."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT * FROM flows
            WHERE enabled = TRUE
              AND schedule IS NOT NULL
              AND (next_run_at IS NULL OR next_run_at <= $1)
            ORDER BY next_run_at ASC NULLS FIRST
            """,
            now,
        )
        return [_row_to_flow(r) for r in rows]

    async def claim_due_scheduled_flow(
        self, flow_id: str, now: datetime, next_run_at: datetime | None
    ) -> Flow | None:
        """Atomically claim a due scheduled flow's slot (multi-instance safe).

        Uses a single ``UPDATE … WHERE id = $1 AND (next_run_at IS NULL OR
        next_run_at <= $2) RETURNING *``.  Only ONE concurrent app
        instance wins the row (the others see ``next_run_at`` already advanced
        and get no row back), so a due flow is materialized exactly once per
        schedule slot even when N instances tick simultaneously.  Task draining
        is already race-safe via ``claim_ready_task_run`` (FOR UPDATE SKIP
        LOCKED).

        Returns the claimed flow dict (with ``next_run_at`` advanced and
        ``last_run_at`` set), or ``None`` if another instance already claimed it.
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            UPDATE flows
            SET next_run_at = $3, last_run_at = $2, updated_at = now()
            WHERE id = $1::uuid
              AND enabled = TRUE
              AND schedule IS NOT NULL
              AND (next_run_at IS NULL OR next_run_at <= $2)
            RETURNING *
            """,
            flow_id,
            now,
            next_run_at,
        )
        return _row_to_flow(row) if row is not None else None

    # ------------------------------------------------------------------
    # FlowRun operations
    # ------------------------------------------------------------------

    async def create_flow_run(
        self,
        flow_id: str,
        org_id: str,
        params: dict[str, Any],
        trigger: str,
        scheduled_at: datetime | None = None,
        env: str = "prod",
        seed: int | None = None,
        code_version: dict[str, Any] | None = None,
        params_snapshot: dict[str, Any] | None = None,
    ) -> FlowRun:
        """Insert a new flow_run row and return the stored dict.

        ``env`` is the resolved execution environment for this
        run.  It namespaces materialized/incremental targets.
        B2: ``seed``, ``code_version``, ``params_snapshot`` are persisted for
        lineage / reproducibility.
        """
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        # Derive seed from a temporary run_id approximation when not provided;
        # the actual run_id is assigned by the DB.  We use uuid4 as the source.
        temp_id = str(uuid.uuid4())
        if seed is None:
            seed = _seed_from_run_id(temp_id)

        snapshot = params_snapshot if params_snapshot is not None else params

        row = await db_fetchrow(
            """
            INSERT INTO flow_runs (flow_id, org_id, params, trigger, scheduled_at, env,
                                   seed, code_version, params_snapshot)
            VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, $5, $6,
                    $7, $8::jsonb, $9::jsonb)
            RETURNING *
            """,
            flow_id,
            org_id,
            json.dumps(params),
            trigger,
            scheduled_at,
            env or "prod",
            seed,
            json.dumps(code_version) if code_version is not None else None,
            json.dumps(snapshot),
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO flow_runs returned no row.")
        return _row_to_flow_run(row)

    async def get_flow_run(self, run_id: str) -> FlowRun | None:
        """Return the flow_run dict, or ``None`` if not found."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM flow_runs WHERE id = $1::uuid",
            run_id,
        )
        return _row_to_flow_run(row) if row is not None else None

    async def list_flow_runs(
        self, flow_id: str, limit: int = 500, offset: int = 0
    ) -> list[FlowRun]:
        """Return flow_runs for *flow_id*, newest first, bounded by *limit*.

        Parameters
        ----------
        flow_id:
            The flow whose runs are listed.
        limit:
            Maximum rows returned.  Applied in SQL so the DB never sends
            an unbounded result set.
        offset:
            Number of rows to skip (for pagination, default 0).
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            "SELECT * FROM flow_runs WHERE flow_id = $1::uuid "
            "ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            flow_id,
            limit,
            offset,
        )
        return [_row_to_flow_run(r) for r in rows]

    async def list_run_outputs_for_runs(
        self, run_ids: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Batch-fetch run outputs for multiple run_ids in a single IN query.

        Returns a dict mapping each run_id to its list of output records
        (ordered by created_at).  Run IDs with no outputs are omitted from
        the result dict (callers should use ``.get(run_id, [])``).

        This eliminates the N+1 pattern in the run-history route.
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        if not run_ids:
            return {}

        # asyncpg supports passing a list as an ANY array parameter.
        rows = await db_fetch(
            """
            SELECT * FROM flow_run_outputs
            WHERE flow_run_id = ANY($1::uuid[])
            ORDER BY flow_run_id, created_at ASC
            """,
            [str(rid) for rid in run_ids],
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            rec = _row_to_run_output(row)
            rid = str(rec["flow_run_id"])
            result.setdefault(rid, []).append(rec)
        return result

    async def update_flow_run(self, run_id: str, fields: dict[str, Any]) -> FlowRun | None:
        """Update mutable fields on a flow_run; return the updated dict or ``None``."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        allowed = {"state", "started_at", "finished_at", "error"}
        updates: list[str] = []
        values: list[Any] = []
        param_idx = 1

        for field in ("state", "started_at", "finished_at", "error"):
            if field not in fields or field not in allowed:
                continue
            updates.append(f"{field} = ${param_idx}")
            values.append(fields[field])
            param_idx += 1

        if not updates:
            return await self.get_flow_run(run_id)

        set_clause = ", ".join(updates)
        values.append(run_id)
        id_param = param_idx

        row = await db_fetchrow(
            f"UPDATE flow_runs SET {set_clause} WHERE id = ${id_param}::uuid RETURNING *",
            *values,
        )
        return _row_to_flow_run(row) if row is not None else None

    # ------------------------------------------------------------------
    # TaskRun operations
    # ------------------------------------------------------------------

    async def add_task_runs(
        self, flow_run_id: str, task_runs: list[dict[str, Any]]
    ) -> list[TaskRun]:
        """Bulk-insert task_runs for a flow_run in a SINGLE round-trip.

        Performance
        -----------
        The previous implementation issued one ``INSERT ... RETURNING`` per
        row, so a 500-item map fan-out chunk meant 500 sequential DB
        round-trips.  This collapses the whole batch into ONE statement by
        ``unnest``-ing per-column parameter arrays into a multi-row
        ``INSERT ... SELECT ... RETURNING``.

        Correctness
        -----------
        Each stored row is byte-for-byte equivalent to the old per-row path:
        the same columns are written (``flow_run_id, org_id, task_key, state,
        attempt, depends_on, cache_key, result, scheduled_at,
        parent_task_run_id, branch_taken``) with ``id`` / ``created_at`` left
        to their DB defaults, and ``_row_to_task_run`` normalises the result
        identically.

        The per-row ``depends_on text[]`` column is the tricky bit: a flat
        ``text[]`` parameter cannot carry one array *per row*.  We pass the
        depends_on lists as a ``jsonb[]`` (one JSON array per row) and rebuild
        each row's ``text[]`` in SQL via ``jsonb_array_elements_text`` — this
        preserves per-row arrays (including empty ``{}``) exactly.

        ``WITH ORDINALITY`` carries the input index through so the RETURNING
        rows are re-ordered back into the caller's input order (Postgres does
        not otherwise guarantee RETURNING order), matching the old path.
        """
        import json  # noqa: PLC0415
        from app.db import fetch as db_fetch  # noqa: PLC0415

        if not task_runs:
            return []

        ids: list[str] = []
        org_ids: list[str] = []
        task_keys: list[str] = []
        states: list[str] = []
        attempts: list[int] = []
        depends_on_json: list[str] = []
        cache_keys: list[str | None] = []
        results_json: list[str | None] = []
        scheduled_ats: list[datetime | None] = []
        parent_ids: list[str | None] = []
        branch_takens: list[str | None] = []

        for tr in task_runs:
            # Assign an id client-side when absent (matches InMemory semantics)
            # so we can deterministically restore input order from RETURNING —
            # Postgres does not guarantee RETURNING preserves the SELECT order.
            ids.append(str(tr.get("id") or uuid.uuid4()))
            org_ids.append(str(tr.get("org_id", "")))
            task_keys.append(tr["task_key"])
            states.append(tr.get("state", "pending"))
            attempts.append(int(tr.get("attempt", 0)))
            # One JSON array per row → rebuilt into text[] in SQL.
            depends_on_json.append(json.dumps(list(tr.get("depends_on", []))))
            cache_keys.append(tr.get("cache_key"))
            results_json.append(
                json.dumps(tr["result"]) if tr.get("result") is not None else None
            )
            scheduled_ats.append(tr.get("scheduled_at"))
            pid = tr.get("parent_task_run_id")
            parent_ids.append(str(pid) if pid is not None else None)
            branch_takens.append(tr.get("branch_taken"))

        rows = await db_fetch(
            """
            INSERT INTO task_runs (id, flow_run_id, org_id, task_key, state, attempt,
                                   depends_on, cache_key, result, scheduled_at,
                                   parent_task_run_id, branch_taken)
            SELECT
                u.id::uuid,
                $1::uuid,
                u.org_id::uuid,
                u.task_key,
                u.state,
                u.attempt,
                ARRAY(SELECT jsonb_array_elements_text(u.depends_on))::text[],
                u.cache_key,
                u.result,
                u.scheduled_at,
                u.parent_task_run_id::uuid,
                u.branch_taken
            FROM unnest(
                $2::text[], $3::text[], $4::text[], $5::text[], $6::int[],
                $7::jsonb[], $8::text[], $9::jsonb[], $10::timestamptz[],
                $11::text[], $12::text[]
            ) AS u(
                id, org_id, task_key, state, attempt,
                depends_on, cache_key, result, scheduled_at,
                parent_task_run_id, branch_taken
            )
            RETURNING *
            """,
            flow_run_id,
            ids,
            org_ids,
            task_keys,
            states,
            attempts,
            depends_on_json,
            cache_keys,
            results_json,
            scheduled_ats,
            parent_ids,
            branch_takens,
        )
        if len(rows) != len(task_runs):  # pragma: no cover
            raise RuntimeError(
                f"bulk INSERT INTO task_runs returned {len(rows)} rows "
                f"for {len(task_runs)} inputs."
            )
        # Restore caller input order (RETURNING order is not guaranteed).
        by_id = {str(dict(r)["id"]): r for r in rows}
        return [_row_to_task_run(by_id[tid]) for tid in ids]

    async def list_task_runs(
        self, flow_run_id: str, limit: int | None = None
    ) -> list[TaskRun]:
        """Return task_runs for *flow_run_id*, ordered by created_at then task_key.

        Parameters
        ----------
        flow_run_id:
            The flow run whose task_runs are listed.
        limit:
            Maximum number of rows to return.  When ``None`` (default) ALL
            rows are returned — callers should supply a bound when using this
            in a response path to avoid unbounded serialization.  Applied as
            a SQL LIMIT so the DB never sends an unbounded result set.
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        if limit is not None:
            rows = await db_fetch(
                """
                SELECT * FROM task_runs
                WHERE flow_run_id = $1::uuid
                ORDER BY created_at ASC, task_key ASC
                LIMIT $2
                """,
                flow_run_id,
                limit,
            )
        else:
            rows = await db_fetch(
                """
                SELECT * FROM task_runs
                WHERE flow_run_id = $1::uuid
                ORDER BY created_at ASC, task_key ASC
                """,
                flow_run_id,
            )
        return [_row_to_task_run(r) for r in rows]

    async def count_task_runs(self, flow_run_id: str) -> int:
        """Return the number of task_runs belonging to *flow_run_id*.

        A cheap ``COUNT(*)`` (index-only) used at map fan-out time to enforce
        the per-run task_run ceiling without SELECT-ing + deserialising every
        row's full payload.
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT count(*) AS n FROM task_runs WHERE flow_run_id = $1::uuid",
            flow_run_id,
        )
        return int(dict(row)["n"]) if row is not None else 0

    async def list_task_run_results(
        self, flow_run_id: str
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Return ``(task_key, result)`` pairs for SUCCESS task_runs only.

        Results-only projection: selects just ``task_key`` and the ``result``
        jsonb for ``state = 'success'`` rows, so the upstream-inputs path does
        not pull every column for every row.  Ordering matches
        ``list_task_runs`` (created_at, then task_key).
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT task_key, result FROM task_runs
            WHERE flow_run_id = $1::uuid AND state = 'success' AND result IS NOT NULL
            ORDER BY created_at ASC, task_key ASC
            LIMIT $2
            """,
            flow_run_id,
            _MAX_TASK_RUNS_PER_RUN,
        )
        out: list[tuple[str, dict[str, Any] | None]] = []
        for r in rows:
            d = dict(r)
            result = d.get("result")
            if result is not None and not isinstance(result, dict):
                import json  # noqa: PLC0415
                result = json.loads(result)
            out.append((d["task_key"], result))
        return out

    async def get_task_run(self, task_run_id: str) -> TaskRun | None:
        """Return the task_run dict, or ``None`` if not found."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM task_runs WHERE id = $1::uuid",
            task_run_id,
        )
        return _row_to_task_run(row) if row is not None else None

    async def get_task_run_by_key(
        self, flow_run_id: str, task_key: str
    ) -> TaskRun | None:
        """Return the task_run for *flow_run_id* / *task_key*, or ``None``.

        Targeted single-row fetch using the (flow_run_id, task_key) index —
        avoids the full list_task_runs round-trip used by the O(N) fallback.
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            SELECT * FROM task_runs
            WHERE flow_run_id = $1::uuid AND task_key = $2
            LIMIT 1
            """,
            flow_run_id,
            task_key,
        )
        return _row_to_task_run(row) if row is not None else None

    async def update_task_run(
        self, task_run_id: str, fields: dict[str, Any]
    ) -> TaskRun | None:
        """Update mutable fields on a task_run; return the updated dict or ``None``.

        ``logs`` is accumulated (appended) in the database via jsonb concatenation
        rather than replaced, so successive updates accumulate all captured lines.
        """
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        allowed = {
            "state", "attempt", "result", "error", "logs",
            "scheduled_at", "started_at", "finished_at", "cache_key",
            # Map / branch fields.
            "branch_taken",
        }
        updates: list[str] = []
        values: list[Any] = []
        param_idx = 1

        for field in (
            "state", "attempt", "error",
            "scheduled_at", "started_at", "finished_at", "cache_key",
            "branch_taken",
        ):
            if field not in fields or field not in allowed:
                continue
            updates.append(f"{field} = ${param_idx}")
            values.append(fields[field])
            param_idx += 1

        if "result" in fields and "result" in allowed:
            val = fields["result"]
            updates.append(f"result = ${param_idx}::jsonb")
            values.append(json.dumps(val) if val is not None else None)
            param_idx += 1

        if "logs" in fields and "logs" in allowed:
            new_logs = fields["logs"] or []
            # Accumulate: coalesce existing + append new lines.
            updates.append(
                f"logs = COALESCE(logs, '[]'::jsonb) || ${param_idx}::jsonb"
            )
            values.append(json.dumps(new_logs))
            param_idx += 1

        if not updates:
            return await self.get_task_run(task_run_id)

        set_clause = ", ".join(updates)
        values.append(task_run_id)
        id_param = param_idx

        row = await db_fetchrow(
            f"UPDATE task_runs SET {set_clause} WHERE id = ${id_param}::uuid RETURNING *",
            *values,
        )
        return _row_to_task_run(row) if row is not None else None

    async def claim_ready_task_run(
        self,
        now: datetime,
        worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> TaskRun | None:
        """Claim the oldest eligible task_run with FOR UPDATE SKIP LOCKED.

        Eligibility: ``state IN ('ready', 'retrying')`` AND (``scheduled_at``
        IS NULL OR ``scheduled_at <= now``).  Uses ``FOR UPDATE SKIP LOCKED``
        so that multiple workers can safely claim without contention.

        Parameters
        ----------
        now:
            Injected clock datetime.
        worker_id:
            Opaque worker identifier stored on the row for lease tracking.
        lease_seconds:
            Lease duration.  ``lease_expires_at`` is set to
            ``now + interval``.  Pass 0 to leave it NULL.
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        if lease_seconds:
            row = await db_fetchrow(
                """
                UPDATE task_runs
                SET state = 'running',
                    started_at = $1,
                    worker_id = $2,
                    lease_expires_at = $1 + ($3 * interval '1 second')
                WHERE id = (
                    SELECT id FROM task_runs
                    WHERE state IN ('ready', 'retrying')
                      AND (scheduled_at IS NULL OR scheduled_at <= $1)
                    ORDER BY scheduled_at ASC NULLS FIRST, created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                now,
                worker_id,
                lease_seconds,
            )
        else:
            row = await db_fetchrow(
                """
                UPDATE task_runs
                SET state = 'running', started_at = $1, worker_id = $2
                WHERE id = (
                    SELECT id FROM task_runs
                    WHERE state IN ('ready', 'retrying')
                      AND (scheduled_at IS NULL OR scheduled_at <= $1)
                    ORDER BY scheduled_at ASC NULLS FIRST, created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                now,
                worker_id,
            )
        return _row_to_task_run(row) if row is not None else None

    async def extend_task_lease(
        self,
        task_run_id: str,
        worker_id: str | None,
        new_expiry: datetime,
    ) -> bool:
        """Extend the worker lease on a claimed (running) task_run.

        Conditional UPDATE: only applies when the row is still ``'running'``
        AND its ``worker_id`` matches (``IS NOT DISTINCT FROM`` so ``None``
        matches ``NULL``).  Returns ``True`` when a row was updated — i.e.
        the calling worker still owns the lease.  Semantics are identical to
        ``InMemoryFlowStore.extend_task_lease``.
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            UPDATE task_runs
            SET lease_expires_at = $3
            WHERE id = $1::uuid
              AND state = 'running'
              AND worker_id IS NOT DISTINCT FROM $2
            RETURNING id
            """,
            task_run_id,
            worker_id,
            new_expiry,
        )
        return row is not None

    async def reap_expired_leases(self, now: datetime) -> int:
        """Re-queue task_runs whose worker lease has expired.

        Transitions eligible rows (state='running', lease_expires_at < now)
        back to 'ready' (or 'retrying' when attempt > 0) and clears the
        lease fields.

        Returns the number of rows reaped.
        """
        from app.db import execute as db_execute  # noqa: PLC0415

        status = await db_execute(
            """
            UPDATE task_runs
            SET state = CASE WHEN attempt > 0 THEN 'retrying' ELSE 'ready' END,
                lease_expires_at = NULL,
                worker_id = NULL
            WHERE state = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < $1
            """,
            now,
        )
        try:
            return int(status.split()[-1])
        except (ValueError, IndexError):
            return 0

    # ------------------------------------------------------------------
    # Incremental watermark operations (flow_watermarks table)
    # ------------------------------------------------------------------

    async def get_watermark(
        self, flow_id: str, model_key: str, env: str = "prod"
    ) -> str | None:
        """Return the stored incremental watermark, or ``None`` if unset."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            SELECT watermark FROM flow_watermarks
            WHERE flow_id = $1::uuid AND model_key = $2 AND env = $3
            """,
            flow_id,
            model_key,
            env or "prod",
        )
        if row is None:
            return None
        wm = dict(row).get("watermark")
        return str(wm) if wm is not None else None

    async def set_watermark(
        self, flow_id: str, model_key: str, env: str, watermark: str | None
    ) -> None:
        """Upsert the incremental watermark for ``(flow_id, model_key, env)``.

        A ``None`` watermark is ignored so we never clobber a real watermark
        with an empty advance.
        """
        if watermark is None:
            return
        from app.db import execute as db_execute  # noqa: PLC0415

        await db_execute(
            """
            INSERT INTO flow_watermarks (flow_id, model_key, env, watermark, updated_at)
            VALUES ($1::uuid, $2, $3, $4, now())
            ON CONFLICT (flow_id, model_key, env)
            DO UPDATE SET watermark = EXCLUDED.watermark, updated_at = now()
            """,
            flow_id,
            model_key,
            env or "prod",
            str(watermark),
        )

    async def copy_watermark(
        self, flow_id: str, model_key: str, src_env: str, dst_env: str
    ) -> str | None:
        """Copy the watermark from *src_env* to *dst_env* (promote helper)."""
        wm = await self.get_watermark(flow_id, model_key, src_env)
        if wm is not None:
            await self.set_watermark(flow_id, model_key, dst_env, wm)
        return wm

    # ------------------------------------------------------------------
    # B2: Data-lineage output records (flow_run_outputs table)
    # ------------------------------------------------------------------

    async def add_run_output(
        self,
        flow_run_id: str,
        org_id: str,
        task_key: str,
        output_key: str,
        output_type: str = "table",
        output_uri: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a new flow_run_outputs row and return it."""
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            INSERT INTO flow_run_outputs
                (flow_run_id, org_id, task_key, output_key, output_type,
                 output_uri, meta)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb)
            RETURNING *
            """,
            flow_run_id,
            org_id,
            task_key,
            output_key,
            output_type,
            output_uri,
            json.dumps(meta) if meta is not None else None,
        )
        if row is None:  # pragma: no cover
            raise RuntimeError("INSERT INTO flow_run_outputs returned no row.")
        return _row_to_run_output(row)

    async def list_run_outputs(self, flow_run_id: str) -> list[dict[str, Any]]:
        """Return all output records for *flow_run_id*, ordered by created_at."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT * FROM flow_run_outputs
            WHERE flow_run_id = $1::uuid
            ORDER BY created_at ASC
            """,
            flow_run_id,
        )
        return [_row_to_run_output(r) for r in rows]

    async def get_run_outputs_by_key(
        self,
        org_id: str,
        output_key: str,
    ) -> list[dict[str, Any]]:
        """Return output records matching *output_key* for an org, newest first."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT * FROM flow_run_outputs
            WHERE org_id = $1::uuid AND output_key = $2
            ORDER BY created_at DESC
            """,
            org_id,
            output_key,
        )
        return [_row_to_run_output(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

#: Active singleton — None means "lazily create PgFlowStore on first call".
_flow_store: InMemoryFlowStore | PgFlowStore | None = None


def get_flow_store() -> InMemoryFlowStore | PgFlowStore:
    """Return (or lazily create) the module-level flow store.

    In production (no override via ``set_flow_store``), returns a
    ``PgFlowStore`` instance.  Tests inject an ``InMemoryFlowStore`` via
    ``set_flow_store`` before making requests.

    Route handlers depend on this function; they keep working without changes
    since both stores expose the same interface.
    """
    global _flow_store
    if _flow_store is None:
        _flow_store = PgFlowStore()
    return _flow_store


def set_flow_store(store: InMemoryFlowStore | PgFlowStore | None) -> None:
    """Override the module-level store singleton.

    Pass an ``InMemoryFlowStore`` instance to inject a test double.
    Pass ``None`` to reset so the next ``get_flow_store()`` call creates a
    fresh ``PgFlowStore`` (the production default).
    """
    global _flow_store
    _flow_store = store


# ---------------------------------------------------------------------------
# Notebook helpers (no new table — notebooks are stored as flows)
# ---------------------------------------------------------------------------


def notebook_spec_from_flow(flow: Flow) -> "Any":
    """Deserialise a stored ``Flow`` dict into a ``NotebookSpec``.

    Notebooks are persisted as ordinary ``Flow`` rows; the ``spec`` JSONB
    column holds the ``FlowSpec`` dict produced by ``notebook_to_flowspec()``.
    This helper reconstructs the ``NotebookSpec`` envelope from the stored
    ``FlowSpec`` dict and the ``flows.id`` field.

    Returns a ``NotebookSpec`` instance.  Raises ``ValueError`` if the spec
    dict is missing or cannot be parsed.

    Parameters
    ----------
    flow:
        A ``Flow`` dict as returned by ``get_flow`` / ``create_flow``.
    """
    from app.flows.notebook import flowspec_to_notebook  # noqa: PLC0415
    from app.flows.spec import FlowSpec  # noqa: PLC0415

    spec_dict = flow.get("spec")
    if not isinstance(spec_dict, dict):
        raise ValueError(
            f"Flow {flow.get('id')!r} has no valid 'spec' dict; "
            "cannot reconstruct NotebookSpec."
        )
    flow_spec = FlowSpec.model_validate(spec_dict)
    notebook_id = str(flow.get("id") or "")
    return flowspec_to_notebook(flow_spec, notebook_id=notebook_id)
