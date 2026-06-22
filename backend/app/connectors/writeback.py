"""Governed write-back engine — idempotent, dry-run, RBAC, approval gates.

This module is the B3 ("close-the-loop") write-back frontier for the Canvas /
operational-BI widget system.  Writing recommendation decisions / operational
values back to a source connector is a different risk class from ETL (e.g.
``connector_write``): it can affect production data, must be auditable, and
often requires a human-in-the-loop approval step.

State machine
-------------
Every write-back request moves through the following states::

    pending_approval  (when approval_required=True)
          │
          ▼ approve / edit
       committed  (also: rejected → terminal)
          │
          ▼ on error
       failed

    When approval_required=False the request skips straight from
    'submitted' → 'committed' (or 'failed').

The state machine is intentionally minimal so the in-memory store is a
faithful structural proxy for the future Postgres-backed store.

Store interface
---------------
``WritebackStore`` — abstract base.
``InMemoryWritebackStore`` — used in tests and local dev (no DB needed).

Route helpers (used by routes/flows.py)
---------------------------------------
``apply_writeback(request, connector_write_result, store, claims)``
    Idempotent commit: looks up (or creates) the write-back record keyed by
    ``idempotency_key``.  Performs the staging/load path from the connector_write
    handler (passed in pre-computed as ``connector_write_result``) only when the
    record doesn't already exist.

``dry_run_writeback(rows, target, claims) -> diff``
    Compute and return the diff/rows that WOULD be written without committing.
    Never touches the target connector.

``approve_writeback(wb_id, action, claims, store) -> record``
    Approve / reject / edit a pending write-back.  Only members with role
    'approver' or 'owner' may call this.  On approval, the commit actually
    runs.  On rejection, the record is marked 'rejected'.

RBAC
----
Writers (roles: 'owner', 'admin', 'member') may POST a write-back request.
Approvers (roles: 'owner', 'admin') may approve/reject/edit.
'viewer' role is always denied.

Cross-org isolation
-------------------
Every record carries ``org_id``; lookups are always org-scoped.  A record from
a different org is treated as not-found (no information leak).

Idempotency
-----------
The ``idempotency_key`` is a caller-supplied string (e.g. a UUID or a
flow_run_id + task_key combination).  If a record with the same key already
exists for the org, the existing record is returned without re-applying the
write.  This means a network retry will never double-apply.

Dry-run
-------
``POST /flows/writeback/preview`` mode returns the rows + a diff summary
without touching the target connector.  The diff shape is::

    {
        "rows": [{...}],
        "row_count": int,
        "target_object": str,
        "mode": str,
        "dry_run": True
    }
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.errors import AppError


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------

#: Roles that may SUBMIT a write-back request (trigger the flow / commit).
_WRITER_ROLES = {"owner", "admin", "member"}

#: Roles that may APPROVE / REJECT / EDIT a write-back request.
_APPROVER_ROLES = {"owner", "admin"}


def _require_writer_role(role: str | None) -> None:
    """Raise 403 if *role* is not a writer role."""
    if role not in _WRITER_ROLES:
        raise AppError(
            "forbidden",
            "Only members with writer/approver role (owner/admin/member) may "
            "submit a write-back request.",
            403,
        )


def _require_approver_role(role: str | None) -> None:
    """Raise 403 if *role* is not an approver role."""
    if role not in _APPROVER_ROLES:
        raise AppError(
            "forbidden",
            "Only members with approver role (owner/admin) may "
            "approve, reject, or edit a write-back request.",
            403,
        )


# ---------------------------------------------------------------------------
# State machine helpers
# ---------------------------------------------------------------------------

_VALID_STATES = {
    "pending_approval",
    "committed",
    "rejected",
    "failed",
}

#: Terminal states — once in these, no further transitions are allowed.
_TERMINAL_STATES = {"committed", "rejected", "failed"}


def _check_transition(current: str, target: str) -> None:
    """Raise 409 if the transition from *current* to *target* is invalid."""
    if current in _TERMINAL_STATES:
        raise AppError(
            "invalid_state_transition",
            f"Write-back is already in terminal state {current!r}; "
            f"cannot transition to {target!r}.",
            409,
        )


# ---------------------------------------------------------------------------
# WritebackStore (abstract interface + in-memory implementation)
# ---------------------------------------------------------------------------


class WritebackStore:
    """Abstract interface for the write-back persistence layer.

    Implementations must be org-scoped: every operation that accepts
    ``wb_id`` MUST also scope to ``org_id`` (return None / raise on mismatch).
    """

    async def create(
        self,
        org_id: str,
        idempotency_key: str,
        rows: list[dict[str, Any]],
        target: dict[str, Any],
        mode: str,
        created_by: str,
        approval_required: bool,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def get_by_idempotency_key(
        self, org_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    async def get(
        self, org_id: str, wb_id: str
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    async def transition(
        self,
        org_id: str,
        wb_id: str,
        new_state: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        rows_override: list[dict[str, Any]] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    async def list(
        self, org_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        raise NotImplementedError


class InMemoryWritebackStore(WritebackStore):
    """Dict-backed in-memory implementation for tests and local dev."""

    def __init__(self) -> None:
        # wb_id -> record
        self._records: dict[str, dict[str, Any]] = {}
        # (org_id, idempotency_key) -> wb_id
        self._idem_index: dict[tuple[str, str], str] = {}

    def reset(self) -> None:
        self._records.clear()
        self._idem_index.clear()

    async def create(
        self,
        org_id: str,
        idempotency_key: str,
        rows: list[dict[str, Any]],
        target: dict[str, Any],
        mode: str,
        created_by: str,
        approval_required: bool,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        wb_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        # Always start in pending_approval; auto-commit path transitions to
        # 'committed' immediately after creation (no-approval branch).
        record: dict[str, Any] = {
            "id": wb_id,
            "org_id": org_id,
            "idempotency_key": idempotency_key,
            "rows": deepcopy(rows),
            "target": deepcopy(target),
            "mode": mode,
            "created_by": created_by,
            "approval_required": approval_required,
            "state": "pending_approval",
            "result": None,
            "error": None,
            "approved_by": None,
            "meta": deepcopy(meta or {}),
            "created_at": now,
            "updated_at": now,
        }
        self._records[wb_id] = record
        self._idem_index[(org_id, idempotency_key)] = wb_id
        return deepcopy(record)

    async def get_by_idempotency_key(
        self, org_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        wb_id = self._idem_index.get((org_id, idempotency_key))
        if wb_id is None:
            return None
        record = self._records.get(wb_id)
        if record is None or str(record["org_id"]) != str(org_id):
            return None
        return deepcopy(record)

    async def get(
        self, org_id: str, wb_id: str
    ) -> dict[str, Any] | None:
        record = self._records.get(str(wb_id))
        if record is None or str(record["org_id"]) != str(org_id):
            return None
        return deepcopy(record)

    async def transition(
        self,
        org_id: str,
        wb_id: str,
        new_state: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        rows_override: list[dict[str, Any]] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any] | None:
        record = self._records.get(str(wb_id))
        if record is None or str(record["org_id"]) != str(org_id):
            return None
        _check_transition(record["state"], new_state)
        record["state"] = new_state
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        if result is not None:
            record["result"] = deepcopy(result)
        if error is not None:
            record["error"] = error
        if rows_override is not None:
            record["rows"] = deepcopy(rows_override)
        if approved_by is not None:
            record["approved_by"] = approved_by
        return deepcopy(record)

    async def list(
        self, org_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = [
            deepcopy(r)
            for r in self._records.values()
            if str(r["org_id"]) == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]


# ---------------------------------------------------------------------------
# Module-level singleton (swappable in tests)
# ---------------------------------------------------------------------------

_store: WritebackStore | None = None


def get_writeback_store() -> WritebackStore:
    """Return the module-level ``WritebackStore`` singleton (lazily initialised)."""
    global _store
    if _store is None:
        _store = InMemoryWritebackStore()
    return _store


def set_writeback_store(store: WritebackStore | None) -> None:
    """Replace the module-level store (test helper)."""
    global _store
    _store = store


# ---------------------------------------------------------------------------
# Core write-back functions
# ---------------------------------------------------------------------------


def dry_run_writeback(
    rows: list[dict[str, Any]],
    target: dict[str, Any],
    mode: str = "append",
) -> dict[str, Any]:
    """Compute a preview diff without committing.

    Returns the rows that WOULD be written, with ``dry_run=True`` to signal
    no data was touched.

    Parameters
    ----------
    rows:
        The row dicts to be written.
    target:
        The target descriptor ``{connector_id, object}``.
    mode:
        Write mode (``'append'`` | ``'overwrite'`` | ``'merge'``).

    Returns
    -------
    dict
        ``{rows, row_count, target_object, mode, dry_run}``
    """
    return {
        "rows": rows,
        "row_count": len(rows),
        "target_object": str(target.get("object") or ""),
        "mode": mode,
        "dry_run": True,
    }


async def submit_writeback(
    *,
    org_id: str,
    idempotency_key: str,
    rows: list[dict[str, Any]],
    target: dict[str, Any],
    mode: str,
    created_by: str,
    approval_required: bool,
    connector_write_fn: Any,
    store: WritebackStore,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Idempotent write-back submit.

    1. Check for an existing record with the same ``(org_id, idempotency_key)``.
       If found, return it immediately (idempotent — no re-apply).
    2. Create a new record.
    3. If ``approval_required=True`` → state is ``'pending_approval'``.
       The actual write is deferred to ``approve_writeback``.
    4. If ``approval_required=False`` → execute the write immediately
       via ``connector_write_fn(rows, target, mode)`` and set state
       to ``'committed'`` (or ``'failed'`` on error).

    Parameters
    ----------
    org_id:
        Org that owns this request.
    idempotency_key:
        Caller-supplied deduplication key.
    rows:
        Rows to write.
    target:
        Target descriptor ``{connector_id, object}``.
    mode:
        Write mode.
    created_by:
        User id of the submitter.
    approval_required:
        When True, the write is gated behind an approval step.
    connector_write_fn:
        Callable ``(rows, target, mode) -> result`` that performs the actual
        write.  Passed in so this module stays connector-agnostic.
        May be a sync or async callable — both are handled.
    store:
        The ``WritebackStore`` to persist the record in.
    meta:
        Optional free-form metadata attached to the record.

    Returns
    -------
    dict
        The write-back record (new or existing).
    """
    import asyncio  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    # ── Idempotency check ─────────────────────────────────────────────────────
    existing = await store.get_by_idempotency_key(org_id, idempotency_key)
    if existing is not None:
        return existing

    # ── Create the record (pending or auto-commit) ────────────────────────────
    record = await store.create(
        org_id=org_id,
        idempotency_key=idempotency_key,
        rows=rows,
        target=target,
        mode=mode,
        created_by=created_by,
        approval_required=approval_required,
        meta=meta,
    )

    if approval_required:
        # Gated — stays in pending_approval.  Return record as-is.
        return record

    # ── Auto-commit: execute the write now ───────────────────────────────────
    wb_id = record["id"]
    try:
        if inspect.iscoroutinefunction(connector_write_fn):
            result = await connector_write_fn(rows, target, mode)
        else:
            result = connector_write_fn(rows, target, mode)
        updated = await store.transition(
            org_id, wb_id, "committed", result=result
        )
        return updated or record
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)[:500]
        updated = await store.transition(
            org_id, wb_id, "failed", error=err_msg
        )
        return updated or record


async def approve_writeback(
    *,
    org_id: str,
    wb_id: str,
    action: str,
    approver_id: str,
    connector_write_fn: Any,
    store: WritebackStore,
    rows_override: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Approve, reject, or edit a pending write-back.

    Parameters
    ----------
    org_id:
        Org that owns the record.
    wb_id:
        Write-back record id.
    action:
        ``'approve'`` | ``'reject'`` | ``'edit'``.

        * ``'approve'`` — commit the original rows as submitted.
        * ``'reject'`` — mark the request rejected; no write is performed.
        * ``'edit'`` — replace the rows with ``rows_override`` and then
          commit.  **``rows_override`` is required** when using this action;
          omitting it raises ``AppError("rows_override_required", 400)``.
          To commit the original rows unchanged, use ``'approve'`` instead.
    approver_id:
        User id of the approver.
    connector_write_fn:
        Callable ``(rows, target, mode) -> result`` used to commit the write
        after approval.
    store:
        The ``WritebackStore`` holding the record.
    rows_override:
        Replacement rows (only for ``action='edit'``).

    Returns
    -------
    dict
        The updated write-back record.

    Raises
    ------
    AppError("not_found", 404)
        When the record does not exist or belongs to a different org.
    AppError("invalid_approval_action", 400)
        When *action* is not one of the valid values.
    AppError("rows_override_required", 400)
        When ``action='edit'`` and ``rows_override`` is ``None``.
    AppError("invalid_state_transition", 409)
        When the record is not in ``pending_approval`` state.
    """
    import asyncio  # noqa: PLC0415
    import inspect  # noqa: PLC0415

    _VALID_ACTIONS = {"approve", "reject", "edit"}
    if action not in _VALID_ACTIONS:
        raise AppError(
            "invalid_approval_action",
            f"Invalid action {action!r}. Valid actions: {sorted(_VALID_ACTIONS)}.",
            400,
        )

    # 'edit' semantics: the approver MUST supply the edited rows.
    # An edit that provides no rows is indistinguishable from a plain 'approve'
    # — which is misleading for the human-in-the-loop audit trail and risks
    # silently committing the original rows when the approver intended to
    # modify them.  To commit the original rows unchanged, use action='approve'.
    if action == "edit" and rows_override is None:
        raise AppError(
            "rows_override_required",
            "action='edit' requires rows_override to be provided. "
            "To commit the original rows unchanged, use action='approve'.",
            400,
        )

    record = await store.get(org_id, wb_id)
    if record is None:
        raise AppError("not_found", f"Write-back {wb_id!r} not found.", 404)

    if record["state"] != "pending_approval":
        raise AppError(
            "invalid_state_transition",
            f"Write-back is in state {record['state']!r}; "
            "only 'pending_approval' records can be approved/rejected/edited.",
            409,
        )

    if action == "reject":
        updated = await store.transition(
            org_id, wb_id, "rejected", approved_by=approver_id
        )
        return updated or record

    # action == 'approve' or 'edit'
    rows_to_write = rows_override if rows_override is not None else record["rows"]
    target = record["target"]
    mode = record["mode"]

    try:
        if inspect.iscoroutinefunction(connector_write_fn):
            result = await connector_write_fn(rows_to_write, target, mode)
        else:
            result = connector_write_fn(rows_to_write, target, mode)
        updated = await store.transition(
            org_id,
            wb_id,
            "committed",
            result=result,
            approved_by=approver_id,
            rows_override=rows_override,
        )
        return updated or record
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)[:500]
        updated = await store.transition(
            org_id, wb_id, "failed",
            error=err_msg,
            approved_by=approver_id,
        )
        return updated or record
