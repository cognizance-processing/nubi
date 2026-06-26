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
exists for the org:

* ``pending_approval`` or ``committed`` state → the existing record is
  returned without re-applying the write (network retries never double-apply).
* ``failed`` or ``rejected`` state → the prior outcome was non-committed
  (rollback/rejection); the record is superseded and a fresh submission
  proceeds.  A ``committed`` record is never superseded.

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

import json
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
    "committing",
    "committed",
    "rejected",
    "failed",
    "superseded",
}

#: Terminal states — once in these, no further transitions are allowed.
#: 'superseded' is terminal: the record is retained purely for audit after a
#: rollback-then-retry replaced it; it must never transition again.
_TERMINAL_STATES = {"committed", "rejected", "failed", "superseded"}

#: Transient lock state used during the approval CAS sequence.
#: A record in 'committing' is owned by exactly one concurrent approver that
#: won the CAS race; all other callers see state != 'pending_approval' and
#: get a 409.  The owning approver transitions straight to 'committed' or
#: 'failed' after the connector write returns.
_COMMITTING_STATE = "committing"

#: Terminal states that represent a rolled-back or non-committed outcome.
#: A NEW logical attempt with the same idempotency_key is allowed to
#: supersede a record in one of these states (rollback-then-retry path).
#: "committed" is intentionally excluded — a successfully committed record
#: must NEVER be superseded (double-commit protection).
_SUPERSEDABLE_STATES = {"failed", "rejected"}


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
        from_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        rows_override: list[dict[str, Any]] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any] | None:
        """Transition the record to *new_state*.

        When *from_state* is supplied the UPDATE is conditional: it only
        succeeds when the current ``state`` column equals *from_state*.  This
        makes the transition an atomic compare-and-swap (CAS) at the store
        layer.  Returns ``None`` when the record does not exist **or** when
        the CAS predicate failed (i.e. another writer already transitioned the
        record out of *from_state*).
        """
        raise NotImplementedError

    async def supersede(
        self,
        org_id: str,
        old_wb_id: str,
        idempotency_key: str,
        rows: list[dict[str, Any]],
        target: dict[str, Any],
        mode: str,
        created_by: str,
        approval_required: bool,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Replace a supersedable terminal record with a fresh one.

        Called by :func:`submit_writeback` when the existing idempotency-keyed
        record is in a rolled-back/failed terminal state (``_SUPERSEDABLE_STATES``)
        and a new logical attempt should proceed.  Implementations MUST:

        * Remove the old record's claim on the ``(org_id, idempotency_key)`` slot.
        * Create a fresh ``pending_approval`` record with the same key.

        The old record itself may be soft-deleted or kept for audit purposes
        (implementation-specific); the important invariant is that a subsequent
        ``get_by_idempotency_key`` returns the NEW record.
        """
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
            "superseded_by": None,
            "superseded_at": None,
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
        from_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        rows_override: list[dict[str, Any]] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any] | None:
        record = self._records.get(str(wb_id))
        if record is None or str(record["org_id"]) != str(org_id):
            return None
        # CAS predicate: if from_state is specified, only proceed when the
        # current state matches.  This is the atomic compare-and-swap that
        # prevents the TOCTOU race between concurrent approvers.
        if from_state is not None and record["state"] != from_state:
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

    async def supersede(
        self,
        org_id: str,
        old_wb_id: str,
        idempotency_key: str,
        rows: list[dict[str, Any]],
        target: dict[str, Any],
        mode: str,
        created_by: str,
        approval_required: bool,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Remove the old record's claim on the idem index so the new create
        # can reclaim the same (org_id, idempotency_key) slot.
        idem_key = (org_id, idempotency_key)
        self._idem_index.pop(idem_key, None)
        # Create the fresh retry record FIRST so we can record the audit link
        # (superseded_by) on the old record.
        new_record = await self.create(
            org_id=org_id,
            idempotency_key=idempotency_key,
            rows=rows,
            target=target,
            mode=mode,
            created_by=created_by,
            approval_required=approval_required,
            meta=meta,
        )
        # Audit trail: mark the OLD record 'superseded' (retained, NOT deleted)
        # so the rejected/failed attempt is preserved while its idem slot is
        # freed for the new record.
        old = self._records.get(str(old_wb_id))
        if old is not None and str(old["org_id"]) == str(org_id):
            now = datetime.now(timezone.utc).isoformat()
            old["state"] = "superseded"
            old["superseded_by"] = new_record["id"]
            old["superseded_at"] = now
            old["updated_at"] = now
        return new_record

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
# PgWritebackStore — asyncpg-backed production implementation
# ---------------------------------------------------------------------------


def _row_to_record(row: Any) -> dict[str, Any]:
    """Convert an asyncpg Record (or dict) to a writeback record dict.

    Ensures uuids are strings, datetimes are ISO-8601 strings (matching
    the shape produced by InMemoryWritebackStore), and jsonb columns are
    already-parsed Python objects (asyncpg does this automatically).
    """
    d = dict(row)
    for key in ("id", "org_id", "created_by", "approved_by"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    for key in ("created_at", "updated_at"):
        val = d.get(key)
        if isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=timezone.utc)
            d[key] = val.isoformat()
    # asyncpg returns jsonb columns as parsed Python objects; handle the rare
    # case where they arrive as strings (e.g. when mocked).
    for key in ("rows", "target", "result", "meta"):
        val = d.get(key)
        if isinstance(val, (str, bytes, bytearray)):
            try:
                d[key] = json.loads(val)
            except Exception:  # noqa: BLE001
                pass
    return d


class PgWritebackStore(WritebackStore):
    """asyncpg-backed write-back store for production use.

    Uses ``fetch`` / ``fetchrow`` / ``execute`` from ``app.db``.
    All SQL is parameterised with ``$N`` placeholders.  Column names and
    constraints match the ``writeback_requests`` table in 0004_flows.sql.

    Org-scoping: every query filters on ``org_id`` so cross-org data leakage
    is impossible at the store layer (defence-in-depth on top of RLS).

    Idempotency: backed by the UNIQUE (org_id, idempotency_key) constraint in
    the table; ``create`` uses INSERT … ON CONFLICT DO NOTHING to prevent
    duplicate rows from concurrent retries, then fetches the existing row on
    conflict.
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
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            """
            INSERT INTO writeback_requests
                (org_id, idempotency_key, rows, target, mode,
                 created_by, approval_required, meta)
            VALUES
                ($1::uuid, $2, $3::jsonb, $4::jsonb, $5,
                 $6::uuid, $7, $8::jsonb)
            ON CONFLICT (org_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            org_id,
            idempotency_key,
            json.dumps(rows),
            json.dumps(target),
            mode,
            created_by,
            approval_required,
            json.dumps(meta or {}),
        )
        if row is None:
            # Race: another caller inserted with the same key; fetch it.
            row = await db_fetchrow(
                "SELECT * FROM writeback_requests "
                "WHERE org_id = $1::uuid AND idempotency_key = $2",
                org_id,
                idempotency_key,
            )
        if row is None:  # pragma: no cover — should never happen
            raise RuntimeError("writeback_requests INSERT returned no row.")
        return _row_to_record(row)

    async def get_by_idempotency_key(
        self, org_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM writeback_requests "
            "WHERE org_id = $1::uuid AND idempotency_key = $2",
            org_id,
            idempotency_key,
        )
        return _row_to_record(row) if row is not None else None

    async def get(
        self, org_id: str, wb_id: str
    ) -> dict[str, Any] | None:
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        row = await db_fetchrow(
            "SELECT * FROM writeback_requests "
            "WHERE id = $1::uuid AND org_id = $2::uuid",
            wb_id,
            org_id,
        )
        return _row_to_record(row) if row is not None else None

    async def transition(
        self,
        org_id: str,
        wb_id: str,
        new_state: str,
        *,
        from_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        rows_override: list[dict[str, Any]] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any] | None:
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        # Fetch current record to validate the transition (state machine guard).
        current = await self.get(org_id, wb_id)
        if current is None:
            return None
        _check_transition(current["state"], new_state)

        # Build the WHERE clause.  When from_state is provided, add an atomic
        # state predicate so the UPDATE is a compare-and-swap: if another
        # concurrent writer already transitioned the row out of from_state the
        # UPDATE matches zero rows and we return None (CAS loss).
        if from_state is not None:
            row = await db_fetchrow(
                """
                UPDATE writeback_requests
                   SET state       = $3,
                       result      = COALESCE($4::jsonb, result),
                       error       = COALESCE($5,        error),
                       rows        = COALESCE($6::jsonb, rows),
                       approved_by = COALESCE($7::uuid,  approved_by),
                       updated_at  = now()
                 WHERE id = $1::uuid AND org_id = $2::uuid AND state = $8
                RETURNING *
                """,
                wb_id,
                org_id,
                new_state,
                json.dumps(result) if result is not None else None,
                error,
                json.dumps(rows_override) if rows_override is not None else None,
                approved_by,
                from_state,
            )
        else:
            row = await db_fetchrow(
                """
                UPDATE writeback_requests
                   SET state       = $3,
                       result      = COALESCE($4::jsonb, result),
                       error       = COALESCE($5,        error),
                       rows        = COALESCE($6::jsonb, rows),
                       approved_by = COALESCE($7::uuid,  approved_by),
                       updated_at  = now()
                 WHERE id = $1::uuid AND org_id = $2::uuid
                RETURNING *
                """,
                wb_id,
                org_id,
                new_state,
                json.dumps(result) if result is not None else None,
                error,
                json.dumps(rows_override) if rows_override is not None else None,
                approved_by,
            )
        return _row_to_record(row) if row is not None else None

    async def supersede(
        self,
        org_id: str,
        old_wb_id: str,
        idempotency_key: str,
        rows: list[dict[str, Any]],
        target: dict[str, Any],
        mode: str,
        created_by: str,
        approval_required: bool,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from app.db import execute as db_execute  # noqa: PLC0415

        # Mark the old row 'superseded' (audit trail RETAINED — NOT deleted) to
        # release its claim on the partial-unique (org_id, idempotency_key) slot
        # (the UNIQUE index only covers state <> 'superseded'), then insert a
        # fresh record.  The rejected/failed attempt is preserved for audit and
        # linked to its successor via superseded_by.
        await db_execute(
            """
            UPDATE writeback_requests
               SET state         = 'superseded',
                   superseded_at = now(),
                   updated_at    = now()
             WHERE id = $1::uuid AND org_id = $2::uuid
            """,
            old_wb_id,
            org_id,
        )
        # Now create the fresh record via the normal INSERT path.
        new_record = await self.create(
            org_id=org_id,
            idempotency_key=idempotency_key,
            rows=rows,
            target=target,
            mode=mode,
            created_by=created_by,
            approval_required=approval_required,
            meta=meta,
        )
        # Backfill the audit link now that the successor id is known.
        await db_execute(
            """
            UPDATE writeback_requests
               SET superseded_by = $1::uuid
             WHERE id = $2::uuid AND org_id = $3::uuid
            """,
            new_record["id"],
            old_wb_id,
            org_id,
        )
        return new_record

    async def list(
        self, org_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            "SELECT * FROM writeback_requests "
            "WHERE org_id = $1::uuid "
            "ORDER BY created_at DESC "
            "LIMIT $2",
            org_id,
            limit,
        )
        return [_row_to_record(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level singleton (swappable in tests)
# ---------------------------------------------------------------------------

_store: WritebackStore | None = None


def get_writeback_store() -> WritebackStore:
    """Return the module-level ``WritebackStore`` singleton (lazily initialised).

    In production (no explicit override via ``set_writeback_store``), returns a
    ``PgWritebackStore`` instance backed by the asyncpg pool.  Tests inject an
    ``InMemoryWritebackStore`` via ``set_writeback_store`` to avoid any DB
    dependency.  This mirrors the pattern used in ``app/vars/store.py`` and
    ``app/jobs/store.py``.
    """
    global _store
    if _store is None:
        _store = PgWritebackStore()
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
    """Idempotent write-back submit with rollback-safe retry support.

    1. Check for an existing record with the same ``(org_id, idempotency_key)``.

       a. If the existing record is ``pending_approval`` or ``committed``
          → return it immediately (idempotent — no re-apply, no double-commit).
       b. If the existing record is in a supersedable terminal state
          (``failed`` or ``rejected``) → the previous attempt was rolled back or
          rejected; supersede it and proceed with a fresh submission.

    2. Create (or supersede) a record.
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
    import inspect  # noqa: PLC0415

    # ── Idempotency check ─────────────────────────────────────────────────────
    existing = await store.get_by_idempotency_key(org_id, idempotency_key)
    if existing is not None:
        existing_state = existing["state"]
        if existing_state not in _SUPERSEDABLE_STATES:
            # In-flight (pending_approval) or successfully committed: return
            # the existing record — no re-apply, no double-commit.
            return existing
        # Existing record is in a rolled-back/failed terminal state.
        # A new logical attempt is allowed to supersede it so the retry
        # can proceed safely.  "committed" is NOT supersedable — that path
        # is handled above and guards against double-commit.
        #
        # STRICTNESS RATCHET (authz): approval strictness can only INCREASE on
        # retry, never decrease.  If the superseded attempt required approval,
        # the retry MUST also require approval — otherwise a member whose
        # approval_required=True writeback was REJECTED could re-submit the same
        # idempotency_key with approval_required=False and auto-commit with no
        # approver (when the server default does not mandate approval).  Mirrors
        # _enforce_approval_policy: caller may raise the bar, never lower it.
        if existing.get("approval_required"):
            approval_required = True
        record = await store.supersede(
            org_id=org_id,
            old_wb_id=existing["id"],
            idempotency_key=idempotency_key,
            rows=rows,
            target=target,
            mode=mode,
            created_by=created_by,
            approval_required=approval_required,
            meta=meta,
        )
    else:
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
        # Rejection does not call the connector, so a simple CAS from
        # pending_approval → rejected is sufficient to prevent double-rejection.
        updated = await store.transition(
            org_id, wb_id, "rejected",
            from_state="pending_approval",
            approved_by=approver_id,
        )
        if updated is None:
            # CAS lost — another concurrent approver already transitioned this
            # record.  Treat as idempotent no-op; re-fetch to return current state.
            current = await store.get(org_id, wb_id)
            raise AppError(
                "conflict",
                f"Write-back {wb_id!r} was already actioned by a concurrent approver "
                f"(current state: {repr(current['state']) if current else repr('unknown')}).",
                409,
            )
        return updated

    # action == 'approve' or 'edit'
    #
    # TOCTOU fix: atomically claim the record by transitioning it from
    # 'pending_approval' → 'committing' BEFORE invoking connector_write_fn.
    # The CAS (from_state='pending_approval') ensures that exactly one
    # concurrent approver wins this race; all losers get None back and raise
    # 409 without ever calling connector_write_fn.
    committing_record = await store.transition(
        org_id, wb_id, _COMMITTING_STATE,
        from_state="pending_approval",
        approved_by=approver_id,
        rows_override=rows_override,
    )
    if committing_record is None:
        # CAS lost — another approver already claimed this record.
        current = await store.get(org_id, wb_id)
        _current_state = repr(current["state"]) if current else repr("unknown")
        raise AppError(
            "conflict",
            f"Write-back {wb_id!r} was already actioned by a concurrent approver "
            f"(current state: {_current_state}).",
            409,
        )

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
        return updated or committing_record
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc)[:500]
        updated = await store.transition(
            org_id, wb_id, "failed",
            error=err_msg,
            approved_by=approver_id,
        )
        return updated or committing_record
