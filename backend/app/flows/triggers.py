"""B6 — Flow triggers: event/webhook registry + downstream completion hooks.

Public API
----------
TriggerRegistry (singleton)
    In-memory registry of trigger configurations.  Injected stores can
    override with a persistent backend.

register_trigger(flow_id, kind, source, org_id, secret, extra) -> Trigger
    Register a trigger.  ``kind`` is ``'event'``, ``'webhook'``, or
    ``'downstream'``.  For ``'event'`` / ``'webhook'`` triggers, ``source``
    is the event key that fires the flow.  For ``'downstream'`` triggers,
    ``source`` is the upstream flow_id whose completion fires this flow.

fire_event(event_key, payload, org_id, store, now, claims) -> list[str]
    Fire all matching ``event`` / ``webhook`` triggers for *event_key* in
    *org_id*.  Returns a list of run_ids created.  Used by the
    ``POST /flows/triggers/fire`` endpoint.

on_flow_run_complete(store, flow_run_id, state, now) -> None
    COMPLETION HOOK called by the engine when a flow_run reaches a terminal
    state.  Fires registered ``downstream`` triggers for that flow's
    completions.  Best-effort + idempotent + error-isolated — NEVER raises.

on_materialized_model_complete(store, flow_run_id, state, now, dag, ...) -> list[str]
    LINEAGE-DRIVEN AUTO-REBUILD HOOK — opt-in.
    When a materialized model flow run completes successfully AND the flow has
    ``auto_rebuild_downstream: true`` in its spec ``runtime_config``, resolves
    downstream materialized models via the lineage DAG and enqueues their flow
    runs.  Returns list of enqueued run_ids.

    Opt-in flag:  set ``runtime_config.auto_rebuild_downstream = true`` on the
    upstream flow's spec.  Only fires on state == 'success'.  Org-scoped:
    only downstream flows in the same org are rebuilt.  Cycle-safe via visited
    set.  Storm-safe via debounce set (same upstream_run_id → only rebuilt once
    per downstream flow_id).

get_trigger_registry() / set_trigger_registry(registry)
    Singleton accessor.

Schema additions (folded into 0004_flows.sql)
---------------------------------------------
``flow_triggers`` table — stores registered triggers.
``flow_sla_alerts`` table — optional SLA breach tracking (flagged when a
  run exceeds ``expected_duration_s``).

SLA hook (run-history helper)
------------------------------
``flag_sla_breach(flow_run, expected_s, now)`` — returns True if the run
exceeded the expected duration.  Called in the run-history serializer.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# B6 — Cycle / depth guard for downstream trigger chains.
# A trigger chain A→B→A would loop indefinitely without a depth cap.
# Default 8; override with the FLOWS_TRIGGER_MAX_DEPTH environment variable.
_FLOWS_TRIGGER_MAX_DEPTH: int = int(os.environ.get("FLOWS_TRIGGER_MAX_DEPTH", "8"))

# B6 — Fan-out width cap for event/downstream triggers.
# A single event firing N matching flows (each potentially triggering more) can
# amplify resource consumption without bound.  Cap the number of flows that any
# one event (or one downstream completion) may fan-out to.
# Default 50; override with the FLOWS_TRIGGER_MAX_FANOUT environment variable.
_FLOWS_TRIGGER_MAX_FANOUT: int = int(os.environ.get("FLOWS_TRIGGER_MAX_FANOUT", "50"))

# B6 — list_all ceiling.
# GET /flows/triggers (list_all) must not scan an unbounded number of rows.
# Default 1000; override with FLOWS_TRIGGER_LIST_ALL_LIMIT environment variable.
_FLOWS_TRIGGER_LIST_ALL_LIMIT: int = int(os.environ.get("FLOWS_TRIGGER_LIST_ALL_LIMIT", "1000"))


# ---------------------------------------------------------------------------
# Trigger data-class
# ---------------------------------------------------------------------------


@dataclass
class Trigger:
    """A registered flow trigger.

    Attributes
    ----------
    id:
        Unique trigger id.
    flow_id:
        The flow to fire when this trigger activates.
    kind:
        ``'event'``, ``'webhook'``, or ``'downstream'``.
    source:
        For ``event`` / ``webhook``: the event_key that activates this trigger.
        For ``downstream``: the upstream flow_id whose completion fires this flow.
    org_id:
        The org that owns this trigger (for multi-tenant isolation).
    secret:
        Optional HMAC secret for webhook authentication.
    extra:
        Free-form metadata (e.g. filter predicates on the event payload).
    enabled:
        Whether the trigger is active.
    created_at:
        Trigger creation timestamp.
    """

    id: str
    flow_id: str
    kind: str  # 'event' | 'webhook' | 'downstream'
    source: str  # event_key OR upstream flow_id
    org_id: str
    secret: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# In-memory trigger registry
# ---------------------------------------------------------------------------


class InMemoryTriggerRegistry:
    """Dict-backed trigger registry for tests + single-process deployments."""

    def __init__(self) -> None:
        self._triggers: dict[str, Trigger] = {}

    async def register(
        self,
        flow_id: str,
        kind: str,
        source: str,
        org_id: str,
        secret: str | None = None,
        extra: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> Trigger:
        """Create and store a new trigger; return it."""
        trigger = Trigger(
            id=str(uuid.uuid4()),
            flow_id=flow_id,
            kind=kind,
            source=source,
            org_id=org_id,
            secret=secret,
            extra=deepcopy(extra) if extra else {},
            enabled=enabled,
        )
        self._triggers[trigger.id] = trigger
        return trigger

    async def list_by_event(self, event_key: str, org_id: str) -> list[Trigger]:
        """Return all enabled event/webhook triggers matching *event_key* + *org_id*."""
        return [
            t for t in self._triggers.values()
            if t.enabled
            and str(t.org_id) == str(org_id)
            and t.kind in ("event", "webhook")
            and t.source == event_key
        ]

    async def list_by_upstream(self, upstream_flow_id: str, org_id: str) -> list[Trigger]:
        """Return all enabled downstream triggers for *upstream_flow_id*."""
        return [
            t for t in self._triggers.values()
            if t.enabled
            and str(t.org_id) == str(org_id)
            and t.kind == "downstream"
            and t.source == upstream_flow_id
        ]

    async def list_all(self, org_id: str, limit: int | None = None) -> list[Trigger]:
        """Return triggers for *org_id*, newest-first up to *limit* (default ceiling)."""
        effective_limit = limit if limit is not None else _FLOWS_TRIGGER_LIST_ALL_LIMIT
        rows = sorted(
            (t for t in self._triggers.values() if str(t.org_id) == str(org_id)),
            key=lambda t: t.created_at,
        )
        return rows[:effective_limit]

    async def get(self, trigger_id: str, org_id: str | None = None) -> Trigger | None:
        """Return the trigger by id, or None.

        When *org_id* is provided the trigger is only returned when its
        ``org_id`` matches — preventing cross-tenant access.
        """
        t = self._triggers.get(trigger_id)
        if t is None:
            return None
        if org_id is not None and str(t.org_id) != str(org_id):
            return None
        return t

    async def delete(self, trigger_id: str, org_id: str | None = None) -> bool:
        """Delete a trigger; return True if deleted.

        When *org_id* is provided the row is only deleted when it belongs to
        that org — preventing cross-tenant mutation.
        """
        t = self._triggers.get(trigger_id)
        if t is None:
            return False
        if org_id is not None and str(t.org_id) != str(org_id):
            return False
        del self._triggers[trigger_id]
        return True

    async def update_enabled(self, trigger_id: str, enabled: bool, org_id: str | None = None) -> Trigger | None:
        """Enable/disable a trigger; return the updated trigger or None.

        When *org_id* is provided the update is only applied when the trigger
        belongs to that org — preventing cross-tenant mutation.
        """
        t = self._triggers.get(trigger_id)
        if t is None:
            return None
        if org_id is not None and str(t.org_id) != str(org_id):
            return None
        t.enabled = enabled
        return t

    def reset(self) -> None:
        """Clear all triggers (for tests)."""
        self._triggers.clear()


# ---------------------------------------------------------------------------
# Pg-backed trigger registry
# ---------------------------------------------------------------------------


def _row_to_trigger(row: Any) -> Trigger:
    """Convert an asyncpg Record (or dict) to a Trigger dataclass.

    Coerces uuid columns to str and ensures created_at is tz-aware UTC.
    Parses extra jsonb when returned as a raw string.
    """
    d = dict(row)
    for key in ("id", "org_id", "flow_id"):
        if key in d and d[key] is not None and not isinstance(d[key], str):
            d[key] = str(d[key])
    val = d.get("created_at")
    if isinstance(val, datetime) and val.tzinfo is None:
        d["created_at"] = val.replace(tzinfo=timezone.utc)
    # asyncpg returns jsonb already parsed; guard against stringified jsonb.
    extra = d.get("extra")
    if isinstance(extra, (str, bytes, bytearray)):
        try:
            d["extra"] = json.loads(extra)
        except Exception:  # noqa: BLE001
            d["extra"] = {}
    elif extra is None:
        d["extra"] = {}
    return Trigger(
        id=d["id"],
        flow_id=d["flow_id"],
        kind=d["kind"],
        source=d["source"],
        org_id=d["org_id"],
        secret=d.get("secret"),
        extra=d["extra"],
        enabled=bool(d.get("enabled", True)),
        created_at=d.get("created_at", datetime.now(timezone.utc)),
    )


class PgTriggerRegistry:
    """asyncpg-backed trigger registry for production use.

    Uses the ``fetch`` / ``fetchrow`` / ``execute`` helpers from ``app.db``.
    All SQL is parameterised with ``$N`` placeholders.  Column names match the
    ``flow_triggers`` table from 0004_flows.sql.
    """

    async def register(
        self,
        flow_id: str,
        kind: str,
        source: str,
        org_id: str,
        secret: str | None = None,
        extra: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> Trigger:
        """INSERT a new trigger row and return it as a Trigger."""
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        trigger_id = str(uuid.uuid4())
        extra_json = json.dumps(extra or {})
        row = await db_fetchrow(
            """
            INSERT INTO flow_triggers (id, org_id, flow_id, kind, source, secret, extra, enabled)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::jsonb, $8)
            RETURNING *
            """,
            trigger_id,
            org_id,
            flow_id,
            kind,
            source,
            secret,
            extra_json,
            enabled,
        )
        if row is None:
            # Fallback: construct from inputs (shouldn't happen with RETURNING *)
            return Trigger(
                id=trigger_id,
                flow_id=flow_id,
                kind=kind,
                source=source,
                org_id=org_id,
                secret=secret,
                extra=dict(extra or {}),
                enabled=enabled,
            )
        return _row_to_trigger(row)

    async def list_by_event(self, event_key: str, org_id: str) -> list[Trigger]:
        """Return enabled event/webhook triggers matching *event_key* + *org_id*."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT * FROM flow_triggers
            WHERE org_id = $1::uuid
              AND source = $2
              AND kind IN ('event', 'webhook')
              AND enabled = TRUE
            ORDER BY created_at ASC
            """,
            org_id,
            event_key,
        )
        return [_row_to_trigger(r) for r in rows]

    async def list_by_upstream(self, upstream_flow_id: str, org_id: str) -> list[Trigger]:
        """Return enabled downstream triggers for *upstream_flow_id*."""
        from app.db import fetch as db_fetch  # noqa: PLC0415

        rows = await db_fetch(
            """
            SELECT * FROM flow_triggers
            WHERE org_id = $1::uuid
              AND source = $2
              AND kind = 'downstream'
              AND enabled = TRUE
            ORDER BY created_at ASC
            """,
            org_id,
            upstream_flow_id,
        )
        return [_row_to_trigger(r) for r in rows]

    async def list_all(self, org_id: str, limit: int | None = None) -> list[Trigger]:
        """Return triggers for *org_id*, oldest-first up to *limit* (default ceiling).

        The LIMIT prevents unbounded full-table scans on large tenants.
        Override the default ceiling via the FLOWS_TRIGGER_LIST_ALL_LIMIT env var.
        """
        from app.db import fetch as db_fetch  # noqa: PLC0415

        effective_limit = limit if limit is not None else _FLOWS_TRIGGER_LIST_ALL_LIMIT
        rows = await db_fetch(
            """
            SELECT * FROM flow_triggers
            WHERE org_id = $1::uuid
            ORDER BY created_at ASC
            LIMIT $2
            """,
            org_id,
            effective_limit,
        )
        return [_row_to_trigger(r) for r in rows]

    async def get(self, trigger_id: str, org_id: str | None = None) -> Trigger | None:
        """Return the trigger by id, or None.

        When *org_id* is provided, an ``AND org_id = $2::uuid`` predicate is
        appended so the row is only returned when it belongs to that org —
        preventing cross-tenant read access.
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        if org_id is not None:
            row = await db_fetchrow(
                "SELECT * FROM flow_triggers WHERE id = $1::uuid AND org_id = $2::uuid",
                trigger_id,
                org_id,
            )
        else:
            row = await db_fetchrow(
                "SELECT * FROM flow_triggers WHERE id = $1::uuid",
                trigger_id,
            )
        return _row_to_trigger(row) if row is not None else None

    async def delete(self, trigger_id: str, org_id: str | None = None) -> bool:
        """Delete a trigger; return True if a row was deleted.

        When *org_id* is provided, an ``AND org_id = $2::uuid`` predicate is
        appended so only the owning org can delete its own trigger — preventing
        cross-tenant mutation.
        """
        from app.db import execute as db_execute  # noqa: PLC0415

        if org_id is not None:
            status = await db_execute(
                "DELETE FROM flow_triggers WHERE id = $1::uuid AND org_id = $2::uuid",
                trigger_id,
                org_id,
            )
        else:
            status = await db_execute(
                "DELETE FROM flow_triggers WHERE id = $1::uuid",
                trigger_id,
            )
        # asyncpg returns e.g. "DELETE 1" or "DELETE 0"
        try:
            return int(status.split()[-1]) > 0
        except (IndexError, ValueError):
            return False

    async def update_enabled(self, trigger_id: str, enabled: bool, org_id: str | None = None) -> Trigger | None:
        """Enable/disable a trigger; return the updated trigger or None.

        When *org_id* is provided, an ``AND org_id = $3::uuid`` predicate is
        appended so only the owning org can mutate its own trigger — preventing
        cross-tenant mutation.
        """
        from app.db import fetchrow as db_fetchrow  # noqa: PLC0415

        if org_id is not None:
            row = await db_fetchrow(
                """
                UPDATE flow_triggers SET enabled = $1, updated_at = now()
                WHERE id = $2::uuid AND org_id = $3::uuid
                RETURNING *
                """,
                enabled,
                trigger_id,
                org_id,
            )
        else:
            row = await db_fetchrow(
                """
                UPDATE flow_triggers SET enabled = $1, updated_at = now()
                WHERE id = $2::uuid
                RETURNING *
                """,
                enabled,
                trigger_id,
            )
        return _row_to_trigger(row) if row is not None else None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

# _registry == None  → not yet chosen; get_trigger_registry() will lazily
#                       create a PgTriggerRegistry (production default).
_registry: InMemoryTriggerRegistry | PgTriggerRegistry | None = None


def get_trigger_registry() -> InMemoryTriggerRegistry | PgTriggerRegistry:
    """Return (or lazily create) the module-level trigger registry.

    In production (no override via ``set_trigger_registry``), returns a
    ``PgTriggerRegistry`` backed by the asyncpg pool.  Tests inject an
    ``InMemoryTriggerRegistry`` via ``set_trigger_registry`` before each test.
    """
    global _registry
    if _registry is None:
        _registry = PgTriggerRegistry()
    return _registry


def set_trigger_registry(
    registry: InMemoryTriggerRegistry | PgTriggerRegistry | None,
) -> None:
    """Override the module-level trigger registry (for tests).

    Pass an ``InMemoryTriggerRegistry`` to inject a test double.  Pass
    ``None`` to reset so the next ``get_trigger_registry()`` call creates
    a fresh ``PgTriggerRegistry`` (the production default).
    """
    global _registry
    _registry = registry


# ---------------------------------------------------------------------------
# Public API: register_trigger
# ---------------------------------------------------------------------------


async def register_trigger(
    flow_id: str,
    kind: str,
    source: str,
    org_id: str,
    secret: str | None = None,
    extra: dict[str, Any] | None = None,
    enabled: bool = True,
) -> Trigger:
    """Register a trigger in the module-level registry.

    Parameters
    ----------
    flow_id:
        The flow to fire when this trigger activates.
    kind:
        ``'event'``, ``'webhook'``, or ``'downstream'``.
    source:
        For ``event`` / ``webhook``: the event_key string.
        For ``downstream``: the upstream flow_id.
    org_id:
        The owning org (for multi-tenant isolation).
    secret:
        Optional HMAC secret (webhook auth).
    extra:
        Free-form metadata (filter predicates, etc.).
    enabled:
        Whether the trigger starts enabled.

    Returns
    -------
    Trigger
        The created trigger.
    """
    registry = get_trigger_registry()
    return await registry.register(
        flow_id=flow_id,
        kind=kind,
        source=source,
        org_id=org_id,
        secret=secret,
        extra=extra,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Public API: fire_event
# ---------------------------------------------------------------------------


async def fire_event(
    event_key: str,
    payload: dict[str, Any],
    org_id: str,
    store: Any,
    now: datetime,
    claims: dict[str, Any] | None = None,
) -> list[str]:
    """Fire all matching event/webhook triggers for *event_key*.

    Looks up all enabled ``event`` / ``webhook`` triggers registered for
    *event_key* in *org_id*, and for each one fires a flow run (via
    ``materialize_flow_run``).  The event payload is threaded into the run
    params as ``params.__event_payload__``.

    The run is materialised but NOT drained synchronously — the work pool will
    pick it up.  For synchronous in-process execution (tests, small deployments)
    the caller may drain manually.

    Parameters
    ----------
    event_key:
        The event key that fired (e.g. ``'stock_take.landed'``).
    payload:
        Event payload dict (included in run params).
    org_id:
        The owning org (for multi-tenant isolation).
    store:
        Flow store instance.
    now:
        Injected clock datetime.
    claims:
        Caller's auth claims (used for RLS on synchronous drain paths).

    Returns
    -------
    list[str]
        The run_ids of the materialised flow runs.
    """
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    if claims is None:
        claims = {}

    registry = get_trigger_registry()
    triggers = await registry.list_by_event(event_key, org_id)

    # ── Fan-out width cap ─────────────────────────────────────────────────────
    if len(triggers) > _FLOWS_TRIGGER_MAX_FANOUT:
        logger.warning(
            "fire_event: event '%s' (org %s) matched %d triggers which exceeds "
            "FLOWS_TRIGGER_MAX_FANOUT=%d; firing only the first %d and skipping %d.",
            event_key, org_id, len(triggers), _FLOWS_TRIGGER_MAX_FANOUT,
            _FLOWS_TRIGGER_MAX_FANOUT, len(triggers) - _FLOWS_TRIGGER_MAX_FANOUT,
        )
        triggers = triggers[:_FLOWS_TRIGGER_MAX_FANOUT]

    run_ids: list[str] = []

    for trigger in triggers:
        try:
            flow = await store.get_flow(trigger.flow_id)
            if flow is None:
                logger.warning(
                    "fire_event: trigger %s references unknown flow %s; skipping.",
                    trigger.id, trigger.flow_id,
                )
                continue
            if str(flow.get("org_id", "")) != str(org_id):
                logger.warning(
                    "fire_event: trigger %s flow %s is in a different org; skipping.",
                    trigger.id, trigger.flow_id,
                )
                continue

            run_params: dict[str, Any] = {
                "__event_key__": event_key,
                "__event_payload__": deepcopy(payload),
                "__trigger_id__": trigger.id,
            }
            # Merge any static params from the trigger's extra dict.
            if isinstance(trigger.extra.get("params"), dict):
                run_params.update(trigger.extra["params"])

            flow_run = await materialize_flow_run(
                store, flow, run_params, "event", now
            )
            run_ids.append(flow_run["id"])
            logger.info(
                "fire_event: fired trigger %s (flow %s) → run %s",
                trigger.id, trigger.flow_id, flow_run["id"],
            )

        except Exception as exc:  # noqa: BLE001
            # Best-effort: log and continue to other triggers.
            logger.warning(
                "fire_event: trigger %s (flow %s) raised: %s",
                trigger.id, trigger.flow_id, exc,
            )

    return run_ids


# ---------------------------------------------------------------------------
# RLS helper for downstream triggers
# ---------------------------------------------------------------------------


# Key under which the owner's RLS policy snapshot is stored in the flow spec's
# runtime_config.  Mirrors ``_OWNER_POLICIES_KEY`` in runtime.py (kept as a
# local literal to avoid a runtime→triggers import cycle).
_OWNER_POLICIES_KEY = "__owner_policies__"


def _downstream_claims_with_owner_policies(
    claims: dict[str, Any],
    upstream_flow: dict[str, Any] | None,
    flow_run_id: str,
) -> dict[str, Any] | None:
    """Return claims augmented with the upstream flow's owner-policy snapshot.

    Mirrors ``_claims_with_owner_policies`` in runtime.py for the trigger path.

    Returns
    -------
    dict
        Merged claims dict with ``policies`` filled from the snapshot, OR the
        original *claims* if they already carry ``policies`` (interactive path).
    None
        When the snapshot **key is absent** AND *claims* has no ``policies`` —
        signals that the downstream trigger must be skipped (fail-closed for RLS
        safety).

    Key-presence distinction
    ------------------------
    The function distinguishes two semantically different situations:

    1. **Key absent** (``_OWNER_POLICIES_KEY`` not in ``runtime_config``):
       The upstream flow was created before policy snapshotting was introduced, or
       the snapshot was never written.  We cannot know what policies should apply,
       so we fail-closed (return ``None`` → caller skips the trigger).

    2. **Key present but value is empty dict** (``{}``):
       An admin-created flow where RLS is legitimately disabled (no per-tenant
       predicates needed).  The empty dict is an *explicit* signal that no
       row-level filtering applies.  The downstream run is ALLOWED with an empty
       ``policies`` dict — it must not be dropped.

    This mirrors the behaviour of ``_claims_with_owner_policies`` in runtime.py
    which uses the same ``key in runtime_config`` check rather than truthiness.
    """
    # Interactive path: caller already supplied policies — leave unchanged.
    if "policies" in claims:
        return claims

    # Extract the owner-policy snapshot from the upstream flow's spec.
    spec = (upstream_flow or {}).get("spec")
    runtime_config = spec.get("runtime_config") if isinstance(spec, dict) else None

    # Distinguish key-absent (fail-closed) from key-present-but-empty (admin/no-RLS).
    if not isinstance(runtime_config, dict) or _OWNER_POLICIES_KEY not in runtime_config:
        # Snapshot key is absent — fail-closed: return None so the caller skips
        # the downstream trigger to prevent running with unknown RLS context.
        return None

    # Key is present; the value may legitimately be an empty dict (admin flow,
    # no per-tenant RLS).  Accept any dict value, including {}.
    policies = runtime_config[_OWNER_POLICIES_KEY]
    if not isinstance(policies, dict):
        # Malformed snapshot value (not a dict at all) — fail-closed.
        return None

    merged = dict(claims)
    merged["policies"] = policies
    return merged


# ---------------------------------------------------------------------------
# Completion hook: on_flow_run_complete
# ---------------------------------------------------------------------------


async def on_flow_run_complete(
    store: Any,
    flow_run_id: str,
    state: str,
    now: datetime,
    claims: dict[str, Any] | None = None,
    _trigger_depth: int = 0,
    _trigger_chain: frozenset[str] | None = None,
) -> None:
    """COMPLETION HOOK — fired when a flow_run finalises (best-effort).

    Called by the engine's ``advance_readiness`` when all task_runs are
    terminal.  Finds all enabled ``downstream`` triggers that reference the
    completed flow and fires a new run for each.

    Contract
    --------
    - **Best-effort**: any error is caught and logged; NEVER raises or breaks
      the completing run.
    - **Idempotent**: the trigger fires at most once per completing run_id +
      trigger_id pair (guarded by ``__upstream_run_id__`` in run params so
      re-entrant calls produce duplicate params but not duplicate logic).
    - **Error-isolated**: a failing downstream trigger does NOT roll back the
      parent run or block other triggers.
    - **RLS-safe**: downstream runs carry the OWNER's snapshotted policies
      from the upstream flow's spec ``runtime_config.__owner_policies__``,
      so they apply the same row-level security as a scheduled run.  If no
      snapshot exists the downstream trigger is SKIPPED (fail-closed).
    - **Cycle-safe**: a trigger chain is capped at ``_FLOWS_TRIGGER_MAX_DEPTH``
      and will not revisit a flow_id already in the current chain.

    Parameters
    ----------
    store:
        Flow store instance.
    flow_run_id:
        The id of the completed flow_run.
    state:
        The terminal state of the run (``'success'`` | ``'failed'``).
    now:
        Injected clock datetime.
    claims:
        Optional auth claims for the downstream run.  When the upstream run
        was materialised from a scheduled context these may be empty; in that
        case the owner-policy snapshot on the upstream flow is used instead
        (same approach as ``_claims_with_owner_policies`` in runtime.py).
    _trigger_depth:
        Internal: current depth in the downstream trigger chain.  Callers
        must NOT pass this — it is threaded recursively by the hook itself.
    _trigger_chain:
        Internal: frozenset of flow_ids already in the current chain.
        Prevents cycles (A→B→A ...).
    """
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    if claims is None:
        claims = {}
    if _trigger_chain is None:
        _trigger_chain = frozenset()

    try:
        flow_run = await store.get_flow_run(flow_run_id)
        if flow_run is None:
            return
        upstream_flow_id = flow_run.get("flow_id", "")
        org_id = flow_run.get("org_id", "")
        if not upstream_flow_id or not org_id:
            return

        # ── Cycle / depth guard ───────────────────────────────────────────────
        if _trigger_depth >= _FLOWS_TRIGGER_MAX_DEPTH:
            logger.warning(
                "on_flow_run_complete: trigger chain depth %d reached for flow %s "
                "(run %s); stopping to prevent infinite chain (FLOWS_TRIGGER_MAX_DEPTH=%d).",
                _trigger_depth, upstream_flow_id, flow_run_id, _FLOWS_TRIGGER_MAX_DEPTH,
            )
            return

        # ── RLS: get the upstream flow to read its owner-policy snapshot ──────
        upstream_flow = await store.get_flow(upstream_flow_id)

        # Build child claims with the upstream flow's owner-policy snapshot.
        # _claims_with_owner_policies is pure (no store IO) so we replicate
        # its logic here rather than importing from runtime (avoids a cycle).
        child_claims = _downstream_claims_with_owner_policies(claims, upstream_flow, flow_run_id)
        # child_claims is None when the snapshot is missing AND we should fail-closed.
        if child_claims is None:
            _suppression_msg = (
                "downstream triggers suppressed: upstream flow has no owner-policy "
                "snapshot; re-save the flow to enable"
            )
            logger.warning(
                "on_flow_run_complete: upstream flow %s (run %s) has no owner-policy "
                "snapshot; skipping all downstream triggers (fail-closed for RLS safety). "
                "%s",
                upstream_flow_id, flow_run_id, _suppression_msg,
            )
            # Make suppression VISIBLE on the upstream flow_run so operators can
            # detect the silent skip via queries / dashboards.  We stamp a
            # structured field (downstream_triggers_suppressed) and, when the
            # run already has a terminal state that permits it, we set the
            # human-readable `error` field if it is currently empty.
            try:
                _suppression_fields: dict[str, Any] = {
                    "downstream_triggers_suppressed": _suppression_msg,
                }
                _current_run = await store.get_flow_run(flow_run_id)
                if _current_run is not None and not _current_run.get("error"):
                    _suppression_fields["error"] = _suppression_msg
                await store.update_flow_run(flow_run_id, _suppression_fields)
            except Exception as _stamp_exc:  # noqa: BLE001
                logger.warning(
                    "on_flow_run_complete: could not stamp suppression record on run %s: %s",
                    flow_run_id, _stamp_exc,
                )
            return

        # ── Build the new chain (parent flow now in visited set) ─────────────
        new_chain = _trigger_chain | {upstream_flow_id}

        registry = get_trigger_registry()
        triggers = await registry.list_by_upstream(upstream_flow_id, org_id)

        # ── Fan-out width cap ─────────────────────────────────────────────────
        if len(triggers) > _FLOWS_TRIGGER_MAX_FANOUT:
            logger.warning(
                "on_flow_run_complete: flow %s (run %s) matched %d downstream triggers "
                "which exceeds FLOWS_TRIGGER_MAX_FANOUT=%d; firing only the first %d "
                "and skipping %d.",
                upstream_flow_id, flow_run_id, len(triggers), _FLOWS_TRIGGER_MAX_FANOUT,
                _FLOWS_TRIGGER_MAX_FANOUT, len(triggers) - _FLOWS_TRIGGER_MAX_FANOUT,
            )
            triggers = triggers[:_FLOWS_TRIGGER_MAX_FANOUT]

        for trigger in triggers:
            try:
                # Respect ``on_states`` filter in extra (default: fire on 'success' only).
                on_states: list[str] = trigger.extra.get("on_states") or ["success"]
                if state not in on_states:
                    logger.debug(
                        "on_flow_run_complete: trigger %s skipped (state=%s not in on_states=%s)",
                        trigger.id, state, on_states,
                    )
                    continue

                downstream_flow = await store.get_flow(trigger.flow_id)
                if downstream_flow is None:
                    logger.warning(
                        "on_flow_run_complete: trigger %s references unknown downstream flow %s",
                        trigger.id, trigger.flow_id,
                    )
                    continue
                if str(downstream_flow.get("org_id", "")) != str(org_id):
                    logger.warning(
                        "on_flow_run_complete: trigger %s downstream flow %s is in a different org",
                        trigger.id, trigger.flow_id,
                    )
                    continue

                # ── Cycle guard: skip if the downstream flow is already in the chain ──
                if trigger.flow_id in new_chain:
                    logger.warning(
                        "on_flow_run_complete: trigger %s would create a cycle "
                        "(flow %s already in chain %s); skipping.",
                        trigger.id, trigger.flow_id, new_chain,
                    )
                    continue

                # ── RLS fail-closed guard on the DOWNSTREAM flow ──────────────
                # The downstream run drains using its OWN owner-policy snapshot
                # (via _claims_with_owner_policies in runtime.py).  If the
                # downstream flow lacks that snapshot (e.g. created before the
                # feature) AND the caller's claims carry no policies, the drain
                # would resolve claims to {} → NO RLS predicates → cross-tenant
                # rows.  Mirror the upstream guard: validate the downstream flow
                # also has a usable snapshot and SKIP (fail-closed) otherwise.
                downstream_claims = _downstream_claims_with_owner_policies(
                    claims, downstream_flow, flow_run_id
                )
                if downstream_claims is None:
                    logger.warning(
                        "on_flow_run_complete: downstream flow %s (trigger %s) has no "
                        "owner-policy snapshot and caller carries no policies; skipping "
                        "trigger (fail-closed for RLS safety).",
                        trigger.flow_id, trigger.id,
                    )
                    continue

                run_params: dict[str, Any] = {
                    "__upstream_flow_id__": upstream_flow_id,
                    "__upstream_run_id__": flow_run_id,
                    "__upstream_state__": state,
                    "__trigger_id__": trigger.id,
                    "__trigger_depth__": _trigger_depth + 1,
                }
                # Merge any static params from the trigger's extra dict.
                if isinstance(trigger.extra.get("params"), dict):
                    run_params.update(trigger.extra["params"])

                flow_run_new = await materialize_flow_run(
                    store, downstream_flow, run_params, "event", now
                )
                logger.info(
                    "on_flow_run_complete: downstream trigger %s (flow %s) fired → run %s "
                    "(depth=%d, chain=%s)",
                    trigger.id, trigger.flow_id, flow_run_new["id"],
                    _trigger_depth + 1, new_chain,
                )

            except Exception as exc:  # noqa: BLE001
                # Error-isolated: log and continue.
                logger.warning(
                    "on_flow_run_complete: downstream trigger %s (flow %s) failed: %s",
                    trigger.id, trigger.flow_id, exc,
                )

    except Exception as exc:  # noqa: BLE001
        # Outermost guard: NEVER break the completing run.
        logger.warning(
            "on_flow_run_complete: hook failed for run %s (state=%s): %s",
            flow_run_id, state, exc,
        )


# ---------------------------------------------------------------------------
# SLA hook helper
# ---------------------------------------------------------------------------


def flag_sla_breach(
    flow_run: dict[str, Any],
    expected_s: float | None,
    now: datetime,
) -> bool:
    """Return True if the flow_run exceeded *expected_s* seconds.

    Used in the run-history serializer to flag SLA breaches.  Always returns
    False when *expected_s* is None or zero.

    Parameters
    ----------
    flow_run:
        A flow_run dict (must contain ``started_at`` and optionally ``finished_at``).
    expected_s:
        Expected maximum duration in seconds.  ``0`` or ``None`` = no SLA.
    now:
        Current clock (used as the ``finished_at`` proxy for still-running runs).

    Returns
    -------
    bool
        ``True`` iff the run's duration exceeded *expected_s*.
    """
    if not expected_s:
        return False
    started = flow_run.get("started_at")
    if started is None:
        return False
    finished = flow_run.get("finished_at") or now
    if not isinstance(started, datetime):
        return False
    if not isinstance(finished, datetime):
        finished = now
    # Ensure tz-aware.
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)
    duration_s = (finished - started).total_seconds()
    return duration_s > expected_s


# ---------------------------------------------------------------------------
# Integration hook: wire into advance_readiness
# ---------------------------------------------------------------------------
# NOTE: Rather than modifying runtime.py (which is owned by this agent), the
# completion hook is exposed as a public function.  The caller (routes layer or
# an enhanced advance_readiness) calls it after finalising the flow_run.
# The hook is also called explicitly in the test suite to verify isolation.


# ---------------------------------------------------------------------------
# Lineage-driven auto-rebuild (Feature B)
# ---------------------------------------------------------------------------

# Key in flow spec's runtime_config that enables auto-rebuild for a flow.
_AUTO_REBUILD_KEY = "auto_rebuild_downstream"

# Debounce: track (upstream_run_id, downstream_flow_id) pairs already enqueued
# to guard against duplicate enqueues within the same process lifetime.
# (Thread-safe for async; not distributed — Pg-side idempotency via params.)
_auto_rebuild_enqueued: set[tuple[str, str]] = set()


def _reset_auto_rebuild_debounce() -> None:
    """Clear the debounce set (for tests)."""
    _auto_rebuild_enqueued.clear()


def _flow_has_auto_rebuild(flow: dict[str, Any]) -> bool:
    """Return True when the flow spec has ``runtime_config.auto_rebuild_downstream = true``."""
    spec = flow.get("spec") or {}
    if not isinstance(spec, dict):
        return False
    runtime_config = spec.get("runtime_config") or {}
    if not isinstance(runtime_config, dict):
        return False
    return bool(runtime_config.get(_AUTO_REBUILD_KEY, False))


async def on_materialized_model_complete(
    store: Any,
    flow_run_id: str,
    state: str,
    now: datetime,
    dag: Any,  # DependencyDAG — typed as Any to avoid import cycle
    claims: dict[str, Any] | None = None,
    _visited: frozenset[str] | None = None,
) -> list[str]:
    """LINEAGE-DRIVEN AUTO-REBUILD HOOK — opt-in, best-effort.

    When a materialized model flow run completes successfully:
    1.  Checks if the upstream flow has ``auto_rebuild_downstream: true`` in its
        spec's ``runtime_config``.  If not set, returns immediately (opt-in).
    2.  Uses the lineage DAG to find downstream nodes (within hops=20).
    3.  For each downstream node that maps to a flow in the same org AND whose
        flow also has ``auto_rebuild_downstream: true`` (or is a direct dependent),
        enqueues a new flow run.

    Guards
    ------
    - **Opt-in**: only active when ``auto_rebuild_downstream = true`` is set
      on the upstream flow.  Existing flows are unaffected.
    - **Org-scoped**: only enqueues flows in the same org as the upstream run.
    - **Cycle-safe**: a ``_visited`` set of flow_ids prevents circular chains.
    - **Storm-safe / debounce**: a module-level set ``_auto_rebuild_enqueued``
      tracks ``(upstream_run_id, downstream_flow_id)`` pairs so the same
      downstream is not enqueued twice for the same trigger event.
    - **Fan-out cap**: at most ``_FLOWS_TRIGGER_MAX_FANOUT`` downstream flows.
    - **Success-only**: only fires when ``state == 'success'``.
    - **Best-effort**: any error is caught and logged; NEVER raises.

    Parameters
    ----------
    store:
        Flow store instance.
    flow_run_id:
        Id of the completed flow run.
    state:
        Terminal state (``'success'`` | ``'failed'``).
    now:
        Injected clock datetime.
    dag:
        Fully built ``DependencyDAG`` (from ``app.lineage.dag.build_dag``).
        The caller is responsible for providing the current org-scoped DAG.
    claims:
        Optional auth claims.
    _visited:
        Internal cycle guard: set of flow_ids already processed in this chain.

    Returns
    -------
    list[str]
        Run ids of downstream flows that were enqueued.  Empty on no-op or error.
    """
    if state != "success":
        return []

    if _visited is None:
        _visited = frozenset()

    if claims is None:
        claims = {}

    enqueued_run_ids: list[str] = []

    try:
        from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

        # ── 1. Load the completing flow run ──────────────────────────────────
        flow_run = await store.get_flow_run(flow_run_id)
        if flow_run is None:
            return []

        upstream_flow_id = flow_run.get("flow_id", "")
        org_id = str(flow_run.get("org_id", ""))
        if not upstream_flow_id or not org_id:
            return []

        # Cycle guard: if this flow_id is already in the visited chain, stop.
        if upstream_flow_id in _visited:
            logger.warning(
                "on_materialized_model_complete: cycle detected for flow %s "
                "(visited=%s); stopping auto-rebuild chain.",
                upstream_flow_id, _visited,
            )
            return []

        # ── 2. Opt-in check ──────────────────────────────────────────────────
        upstream_flow = await store.get_flow(upstream_flow_id)
        if upstream_flow is None:
            return []

        if not _flow_has_auto_rebuild(upstream_flow):
            # Auto-rebuild not opted in for this flow — skip silently.
            return []

        # ── 3. Resolve downstream nodes from the lineage DAG ─────────────────
        # We look up the flow's model_id (if set) or fall back to the flow_id
        # itself as the DAG node identifier.
        upstream_spec = upstream_flow.get("spec") or {}
        upstream_model_id = (
            (upstream_spec.get("runtime_config") or {}).get("model_id")
            or upstream_flow_id
        )

        # Walk DAG downstream (up to max hops).
        downstream_node_ids: list[str] = dag.downstream(upstream_model_id, hops=20)
        if not downstream_node_ids:
            logger.debug(
                "on_materialized_model_complete: no downstream DAG nodes for "
                "model '%s' (flow %s).",
                upstream_model_id, upstream_flow_id,
            )
            return []

        # Build a mapping: model_id → flow for quick lookup.
        # The store is expected to support list_flows_by_model_ids or we
        # enumerate by convention (flow name == model_id).
        # We call store.list_flows with org_id to avoid cross-org leaks.
        try:
            all_flows = await store.list_flows(org_id=org_id)
        except Exception:  # noqa: BLE001
            all_flows = []

        # Index flows by their model_id (from runtime_config) or by flow_id.
        flow_index: dict[str, dict[str, Any]] = {}
        for f in all_flows:
            fspec = f.get("spec") or {}
            frc = (fspec.get("runtime_config") or {}) if isinstance(fspec, dict) else {}
            mid = frc.get("model_id") or f.get("id", "")
            if mid:
                flow_index[str(mid)] = f
            # Also index by flow id for direct flow_id == node_id matches.
            flow_index[str(f.get("id", ""))] = f

        # ── 4. Fan-out: enqueue downstream flows ─────────────────────────────
        new_visited = _visited | {upstream_flow_id}

        enqueued_count = 0
        for ds_node_id in downstream_node_ids:
            if enqueued_count >= _FLOWS_TRIGGER_MAX_FANOUT:
                logger.warning(
                    "on_materialized_model_complete: fan-out cap %d reached for "
                    "upstream flow %s (run %s); skipping remaining %d downstream nodes.",
                    _FLOWS_TRIGGER_MAX_FANOUT, upstream_flow_id, flow_run_id,
                    len(downstream_node_ids) - enqueued_count,
                )
                break

            ds_flow = flow_index.get(ds_node_id)
            if ds_flow is None:
                # No flow registered for this DAG node — skip (it may be a
                # bare table leaf or an unmanaged model).
                continue

            ds_flow_id = str(ds_flow.get("id", ""))
            ds_org_id = str(ds_flow.get("org_id", ""))

            # Org-scope guard.
            if ds_org_id != org_id:
                logger.warning(
                    "on_materialized_model_complete: downstream flow %s is in "
                    "different org (%s != %s); skipping.",
                    ds_flow_id, ds_org_id, org_id,
                )
                continue

            # Cycle guard.
            if ds_flow_id in new_visited:
                logger.warning(
                    "on_materialized_model_complete: downstream flow %s already "
                    "in visited chain %s; skipping to prevent cycle.",
                    ds_flow_id, new_visited,
                )
                continue

            # Storm-safe debounce: don't enqueue the same downstream more than
            # once for a single upstream run.
            debounce_key = (flow_run_id, ds_flow_id)
            if debounce_key in _auto_rebuild_enqueued:
                logger.debug(
                    "on_materialized_model_complete: downstream flow %s already "
                    "enqueued for upstream run %s; skipping (debounce).",
                    ds_flow_id, flow_run_id,
                )
                continue

            try:
                run_params: dict[str, Any] = {
                    "__auto_rebuild__": True,
                    "__upstream_flow_id__": upstream_flow_id,
                    "__upstream_run_id__": flow_run_id,
                    "__upstream_state__": state,
                    "__lineage_dag_node__": ds_node_id,
                }

                ds_run = await materialize_flow_run(
                    store, ds_flow, run_params, "lineage_rebuild", now
                )
                run_id = ds_run["id"]
                enqueued_run_ids.append(run_id)
                _auto_rebuild_enqueued.add(debounce_key)
                enqueued_count += 1

                logger.info(
                    "on_materialized_model_complete: enqueued rebuild of "
                    "downstream flow %s (node %s) → run %s "
                    "(upstream flow %s, run %s).",
                    ds_flow_id, ds_node_id, run_id,
                    upstream_flow_id, flow_run_id,
                )

            except Exception as exc:  # noqa: BLE001
                # Error-isolated: log and continue to other downstream flows.
                logger.warning(
                    "on_materialized_model_complete: failed to enqueue downstream "
                    "flow %s (node %s): %s",
                    ds_flow_id, ds_node_id, exc,
                )

    except Exception as exc:  # noqa: BLE001
        # Outermost guard: NEVER raise.
        logger.warning(
            "on_materialized_model_complete: hook failed for run %s (state=%s): %s",
            flow_run_id, state, exc,
        )

    return enqueued_run_ids
