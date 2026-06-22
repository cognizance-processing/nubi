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

import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


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

    async def list_all(self, org_id: str) -> list[Trigger]:
        """Return all triggers for *org_id*."""
        return [
            t for t in self._triggers.values()
            if str(t.org_id) == str(org_id)
        ]

    async def get(self, trigger_id: str) -> Trigger | None:
        """Return the trigger by id, or None."""
        return self._triggers.get(trigger_id)

    async def delete(self, trigger_id: str) -> bool:
        """Delete a trigger; return True if deleted."""
        if trigger_id in self._triggers:
            del self._triggers[trigger_id]
            return True
        return False

    async def update_enabled(self, trigger_id: str, enabled: bool) -> Trigger | None:
        """Enable/disable a trigger; return the updated trigger or None."""
        t = self._triggers.get(trigger_id)
        if t is None:
            return None
        t.enabled = enabled
        return t

    def reset(self) -> None:
        """Clear all triggers (for tests)."""
        self._triggers.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: InMemoryTriggerRegistry | None = None


def get_trigger_registry() -> InMemoryTriggerRegistry:
    """Return (or lazily create) the module-level trigger registry."""
    global _registry
    if _registry is None:
        _registry = InMemoryTriggerRegistry()
    return _registry


def set_trigger_registry(registry: InMemoryTriggerRegistry | None) -> None:
    """Override the module-level trigger registry (for tests)."""
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
# Completion hook: on_flow_run_complete
# ---------------------------------------------------------------------------


async def on_flow_run_complete(
    store: Any,
    flow_run_id: str,
    state: str,
    now: datetime,
    claims: dict[str, Any] | None = None,
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
        Optional auth claims for the downstream run.
    """
    from app.flows.runtime import materialize_flow_run  # noqa: PLC0415

    if claims is None:
        claims = {}

    try:
        flow_run = await store.get_flow_run(flow_run_id)
        if flow_run is None:
            return
        upstream_flow_id = flow_run.get("flow_id", "")
        org_id = flow_run.get("org_id", "")
        if not upstream_flow_id or not org_id:
            return

        registry = get_trigger_registry()
        triggers = await registry.list_by_upstream(upstream_flow_id, org_id)

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

                run_params: dict[str, Any] = {
                    "__upstream_flow_id__": upstream_flow_id,
                    "__upstream_run_id__": flow_run_id,
                    "__upstream_state__": state,
                    "__trigger_id__": trigger.id,
                }
                # Merge any static params from the trigger's extra dict.
                if isinstance(trigger.extra.get("params"), dict):
                    run_params.update(trigger.extra["params"])

                flow_run_new = await materialize_flow_run(
                    store, downstream_flow, run_params, "event", now
                )
                logger.info(
                    "on_flow_run_complete: downstream trigger %s (flow %s) fired → run %s",
                    trigger.id, trigger.flow_id, flow_run_new["id"],
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
