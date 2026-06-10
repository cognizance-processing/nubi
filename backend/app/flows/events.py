"""Flow event system — lightweight listener registry for status transitions.

Public API
----------
FlowEvent
    Dataclass carrying event metadata (type, flow_run_id, task_key, state,
    error, attempt, timestamp).

EventType
    String literals for the event types emitted by the executor:
    ``'task_started'``, ``'task_success'``, ``'task_failed'``,
    ``'task_retrying'``, ``'task_timed_out'``, ``'task_upstream_failed'``,
    ``'flow_started'``, ``'flow_success'``, ``'flow_failed'``.

register_flow_listener(fn)
    Register a callable that will be invoked on every ``FlowEvent``.
    The callable receives a single ``FlowEvent`` argument.
    Multiple listeners may be registered; they are called in registration order.
    Registering the same callable twice is a no-op (idempotent).

unregister_flow_listener(fn)
    Remove a previously registered listener (if present).

emit_flow_event(event)
    Dispatch *event* to all registered listeners.
    Exceptions raised inside listeners are caught and logged so one bad
    listener never breaks the engine.  Default (no listeners) = no-op.

clear_flow_listeners()
    Remove all registered listeners.  Intended for test teardown only.

Notifications module integration
---------------------------------
The notifications module (not yet written) plugs in by calling::

    from app.flows.events import register_flow_listener, FlowEvent

    def _on_flow_event(event: FlowEvent) -> None:
        if event.type in ("flow_failed", "task_failed"):
            send_alert(event)   # or queue a background task

    register_flow_listener(_on_flow_event)

Because listeners are called synchronously inside the executor, do not
perform blocking I/O directly — push work to a queue or schedule a
background task instead.

Security / isolation notes
--------------------------
- This module has NO imports from the rest of the app at module level.
  Import it freely without circular-import risk.
- Event data contains only IDs and primitives — no secrets or PII.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

EventType = Literal[
    "task_started",
    "task_success",
    "task_failed",
    "task_retrying",
    "task_timed_out",
    "task_upstream_failed",
    "task_skipped",
    "flow_started",
    "flow_success",
    "flow_failed",
]


# ---------------------------------------------------------------------------
# FlowEvent dataclass
# ---------------------------------------------------------------------------


@dataclass
class FlowEvent:
    """Carries metadata for a single flow/task status transition.

    Attributes
    ----------
    type:
        The event type — see :data:`EventType`.
    flow_run_id:
        UUID string of the flow run this event belongs to.
    task_key:
        The task key (slug), or ``None`` for flow-level events
        (``flow_started``, ``flow_success``, ``flow_failed``).
    state:
        The new state of the task or flow run after the transition.
    error:
        Error message / traceback excerpt, or ``None`` when there is no error.
    attempt:
        The attempt number (0-based) of the task run, or ``None`` for
        flow-level events.
    timestamp:
        UTC datetime of the transition.  Defaults to the current UTC time.
    extra:
        Free-form dict for additional context (e.g. duration, retries_left).
        Callers are responsible for keeping this JSON-serialisable.
    """

    type: EventType
    flow_run_id: str
    task_key: str | None = None
    state: str | None = None
    error: str | None = None
    attempt: int | None = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Listener registry
# ---------------------------------------------------------------------------

#: Module-level list of registered listener callables.
_listeners: list[Callable[[FlowEvent], None]] = []


def register_flow_listener(fn: Callable[[FlowEvent], None]) -> None:
    """Register *fn* as a flow event listener.

    Idempotent: registering the same callable twice adds it only once.

    Parameters
    ----------
    fn:
        Callable that accepts a single :class:`FlowEvent` argument.
        Must not raise — exceptions are caught and logged by the emitter,
        but a misbehaving listener will still cause log noise.
    """
    if fn not in _listeners:
        _listeners.append(fn)


def unregister_flow_listener(fn: Callable[[FlowEvent], None]) -> None:
    """Remove *fn* from the listener list (no-op if not registered).

    Parameters
    ----------
    fn:
        The callable previously passed to :func:`register_flow_listener`.
    """
    try:
        _listeners.remove(fn)
    except ValueError:
        pass


def clear_flow_listeners() -> None:
    """Remove ALL registered listeners.

    Intended for test teardown.  Production code should never call this.
    """
    _listeners.clear()


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------


def emit_flow_event(event: FlowEvent) -> None:
    """Dispatch *event* to all registered listeners.

    Exceptions raised by individual listeners are caught and logged at
    WARNING level so one bad listener cannot break the execution engine.
    If no listeners are registered this function is a pure no-op.

    Parameters
    ----------
    event:
        The :class:`FlowEvent` to dispatch.
    """
    if not _listeners:
        return

    for fn in list(_listeners):  # snapshot to avoid mutation during iteration
        try:
            fn(event)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Flow event listener %r raised an exception for event %r; ignoring.",
                fn,
                event.type,
                exc_info=True,
            )
