"""Flow store implementations — InMemoryFlowStore (tests) + PgFlowStore (prod).

``InMemoryFlowStore`` is a dict-backed store for :class:`Flow`,
:class:`FlowRun`, and :class:`TaskRun` records.  It is the primary store
used in tests.

``PgFlowStore`` is the asyncpg-backed production store that maps each method
to a parameterised SQL query against the ``flows``, ``flow_runs``, and
``task_runs`` tables (from migration 0012).  Rows are converted to plain
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

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


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
    map task_run.  NULL for all other task_runs (migration 0020).

    ``branch_taken`` — for branch task_runs, stores the branch label that was
    taken (e.g. ``"condition_0"``, ``"default"``).  NULL for all other
    task_runs (migration 0020).
    """

    def __init__(self) -> None:
        self._flows: dict[str, Flow] = {}
        self._flow_runs: dict[str, FlowRun] = {}           # run_id → FlowRun
        self._flow_run_index: dict[str, list[str]] = {}    # flow_id → [run_id]
        self._task_runs: dict[str, TaskRun] = {}           # task_run_id → TaskRun
        self._task_run_index: dict[str, list[str]] = {}    # flow_run_id → [task_run_id]

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

    async def list_flows(self, org_id: str) -> list[Flow]:
        """Return all flows belonging to *org_id*, sorted by created_at."""
        rows = [
            deepcopy(f)
            for f in self._flows.values()
            if str(f["org_id"]) == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

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
    ) -> FlowRun:
        """Create and store a new flow_run; return the stored dict."""
        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
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
        }
        self._flow_runs[run_id] = run
        self._flow_run_index.setdefault(str(flow_id), []).append(run_id)
        self._task_run_index[run_id] = []
        return deepcopy(run)

    async def get_flow_run(self, run_id: str) -> FlowRun | None:
        """Return a copy of the flow_run, or ``None`` if not found."""
        run = self._flow_runs.get(str(run_id))
        return deepcopy(run) if run is not None else None

    async def list_flow_runs(self, flow_id: str) -> list[FlowRun]:
        """Return all flow_runs for *flow_id*, newest first."""
        run_ids = self._flow_run_index.get(str(flow_id), [])
        rows = [deepcopy(self._flow_runs[rid]) for rid in run_ids if rid in self._flow_runs]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows

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
                # Work-pool lease fields (migration 0016).
                "lease_expires_at": tr.get("lease_expires_at", None),
                "worker_id": tr.get("worker_id", None),
                # Map / branch fields (migration 0020).
                # parent_task_run_id: set on map child task_runs to the parent
                #   map task_run id; NULL for all other task_runs.
                "parent_task_run_id": tr.get("parent_task_run_id", None),
                # branch_taken: set on branch task_runs to the label of the
                #   condition that matched (e.g. "condition_0", "default");
                #   NULL for all other task_runs.
                "branch_taken": tr.get("branch_taken", None),
            }
            self._task_runs[tr_id] = record
            self._task_run_index.setdefault(flow_run_id, []).append(tr_id)
            stored.append(deepcopy(record))
        return stored

    async def list_task_runs(self, flow_run_id: str) -> list[TaskRun]:
        """Return all task_runs for *flow_run_id*, ordered by created_at then task_key."""
        tr_ids = self._task_run_index.get(str(flow_run_id), [])
        rows = [
            deepcopy(self._task_runs[tid])
            for tid in tr_ids
            if tid in self._task_runs
        ]
        rows.sort(key=lambda r: (r["created_at"], r["task_key"]))
        return rows

    async def get_task_run(self, task_run_id: str) -> TaskRun | None:
        """Return a copy of the task_run, or ``None`` if not found."""
        tr = self._task_runs.get(str(task_run_id))
        return deepcopy(tr) if tr is not None else None

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
    for key in ("id", "org_id", "created_by"):
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
    # Map / branch fields added in migration 0020; default to None for older rows.
    if "parent_task_run_id" in d and d["parent_task_run_id"] is not None:
        d["parent_task_run_id"] = str(d["parent_task_run_id"])
    else:
        d.setdefault("parent_task_run_id", None)
    d.setdefault("branch_taken", None)
    return d


class PgFlowStore:
    """asyncpg-backed flow store for production use.

    Uses the ``fetch`` / ``fetchrow`` / ``execute`` helpers from ``app.db``
    (which acquire a connection from the pool automatically).

    All SQL is parameterised with ``$N`` placeholders.  Column names match
    the ``flows``, ``flow_runs``, and ``task_runs`` tables from migration
    0012.

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
        """Insert a new flow row and return the stored dict."""
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

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

    async def list_flows(self, org_id: str) -> list[Flow]:
        """Return all flows belonging to *org_id*, sorted by created_at."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            "SELECT * FROM flows WHERE org_id = $1::uuid ORDER BY created_at ASC",
            org_id,
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
        next_run_at <= $2) RETURNING *``.  Only ONE concurrent Cloud Run
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
    ) -> FlowRun:
        """Insert a new flow_run row and return the stored dict."""
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            INSERT INTO flow_runs (flow_id, org_id, params, trigger, scheduled_at)
            VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, $5)
            RETURNING *
            """,
            flow_id,
            org_id,
            json.dumps(params),
            trigger,
            scheduled_at,
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

    async def list_flow_runs(self, flow_id: str) -> list[FlowRun]:
        """Return all flow_runs for *flow_id*, newest first."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            "SELECT * FROM flow_runs WHERE flow_id = $1::uuid ORDER BY created_at DESC",
            flow_id,
        )
        return [_row_to_flow_run(r) for r in rows]

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
        """Bulk-insert task_runs for a flow_run; return the stored list."""
        import json  # noqa: PLC0415
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        stored: list[TaskRun] = []
        for tr in task_runs:
            parent_id = tr.get("parent_task_run_id")
            row = await db_fetchrow(
                """
                INSERT INTO task_runs (flow_run_id, org_id, task_key, state, attempt,
                                       depends_on, cache_key, result, scheduled_at,
                                       parent_task_run_id, branch_taken)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb, $9,
                        $10::uuid, $11)
                RETURNING *
                """,
                flow_run_id,
                tr.get("org_id", ""),
                tr["task_key"],
                tr.get("state", "pending"),
                tr.get("attempt", 0),
                tr.get("depends_on", []),
                tr.get("cache_key"),
                json.dumps(tr["result"]) if tr.get("result") is not None else None,
                tr.get("scheduled_at"),
                parent_id,
                tr.get("branch_taken"),
            )
            if row is None:  # pragma: no cover
                raise RuntimeError("INSERT INTO task_runs returned no row.")
            stored.append(_row_to_task_run(row))
        return stored

    async def list_task_runs(self, flow_run_id: str) -> list[TaskRun]:
        """Return all task_runs for *flow_run_id*, ordered by created_at then task_key."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT * FROM task_runs
            WHERE flow_run_id = $1::uuid
            ORDER BY created_at ASC, task_key ASC
            """,
            flow_run_id,
        )
        return [_row_to_task_run(r) for r in rows]

    async def get_task_run(self, task_run_id: str) -> TaskRun | None:
        """Return the task_run dict, or ``None`` if not found."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM task_runs WHERE id = $1::uuid",
            task_run_id,
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
            # Map / branch fields (migration 0020).
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
