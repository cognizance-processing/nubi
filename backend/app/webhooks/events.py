"""Outbound webhook event types, payload shapes, and emit functions.

Producers call the ``emit_*`` helpers (or :func:`emit_event`) at the right
points; each builds a versioned envelope and hands it to
:func:`app.webhooks.delivery.dispatch_event` for fire-and-forget, org-scoped
delivery. Emitting is always best-effort: a failure here NEVER propagates to the
triggering request/flow.

Envelope
--------
Every event is delivered as::

    {
      "type": "<event_type>",
      "id": "<uuid4>",                # unique per emission (idempotency key)
      "org_id": "<org uuid>",
      "occurred_at": "<iso8601 UTC>",
      "data": { ...event-specific... }
    }
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("app.webhooks.events")

# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

WATCH_BREACH = "watch_breach"
FRESHNESS_STALE = "freshness_stale"
QUERY_FAILED = "query_failed"
FLOW_COMPLETED = "flow_completed"
QUERY_EXECUTED = "query_executed"

ALL_EVENT_TYPES: tuple[str, ...] = (
    WATCH_BREACH,
    FRESHNESS_STALE,
    QUERY_FAILED,
    FLOW_COMPLETED,
    QUERY_EXECUTED,
)


def is_valid_event_type(event_type: str) -> bool:
    """Return whether *event_type* is a known, deliverable event type."""
    return event_type in ALL_EVENT_TYPES


# ---------------------------------------------------------------------------
# Envelope + emit core
# ---------------------------------------------------------------------------


def build_envelope(
    event_type: str, org_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Build the canonical delivery envelope for *event_type*."""
    return {
        "type": event_type,
        "id": str(uuid.uuid4()),
        "org_id": str(org_id),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def emit_event(event_type: str, org_id: str | None, data: dict[str, Any]) -> None:
    """Build the envelope and dispatch it (fire-and-forget). Never raises.

    No-op when *org_id* is falsy (an event with no owning org has no
    org-scoped endpoints to deliver to) or *event_type* is unknown.
    """
    try:
        if not org_id or not is_valid_event_type(event_type):
            return
        from app.webhooks.delivery import dispatch_event  # noqa: PLC0415

        envelope = build_envelope(event_type, str(org_id), data)
        dispatch_event(str(org_id), event_type, envelope)
    except Exception as exc:  # noqa: BLE001 — emitting must never break the caller.
        logger.warning("emit_event(%s) failed: %s", event_type, exc)


# ---------------------------------------------------------------------------
# Typed emit helpers (one per event)
# ---------------------------------------------------------------------------


def emit_watch_breach(
    org_id: str | None,
    *,
    watch_id: str | None,
    name: str | None,
    metric_id: str | None,
    value: float | None,
    explanation: str | None,
) -> None:
    """Emit ``watch_breach`` when a monitored metric breaches its threshold."""
    emit_event(
        WATCH_BREACH,
        org_id,
        {
            "watch_id": watch_id,
            "name": name,
            "metric_id": metric_id,
            "value": value,
            "explanation": explanation,
        },
    )


def emit_freshness_stale(
    org_id: str | None,
    *,
    flow_run_id: str | None = None,
    flow_id: str | None = None,
    name: str | None = None,
    age_s: float | None = None,
    sla_s: float | None = None,
) -> None:
    """Emit ``freshness_stale`` when a run/dataset exceeds its freshness SLA."""
    emit_event(
        FRESHNESS_STALE,
        org_id,
        {
            "flow_run_id": flow_run_id,
            "flow_id": flow_id,
            "name": name,
            "age_s": age_s,
            "sla_s": sla_s,
        },
    )


def emit_query_failed(
    org_id: str | None,
    *,
    error_code: str | None,
    message: str | None,
    datastore_id: str | None = None,
    query_id: str | None = None,
) -> None:
    """Emit ``query_failed`` when a query errors on an org's behalf."""
    emit_event(
        QUERY_FAILED,
        org_id,
        {
            "error_code": error_code,
            "message": message,
            "datastore_id": datastore_id,
            "query_id": query_id,
        },
    )


def emit_flow_completed(
    org_id: str | None,
    *,
    flow_run_id: str | None,
    flow_id: str | None,
    name: str | None,
    state: str | None,
    duration_s: float | None = None,
    failed_task: str | None = None,
    error: str | None = None,
) -> None:
    """Emit ``flow_completed`` when a flow run finalises (success or failure)."""
    emit_event(
        FLOW_COMPLETED,
        org_id,
        {
            "flow_run_id": flow_run_id,
            "flow_id": flow_id,
            "name": name,
            "state": state,
            "duration_s": duration_s,
            "failed_task": failed_task,
            "error": error,
        },
    )


def emit_query_executed(
    org_id: str | None,
    *,
    query_id: str | None = None,
    subject: str | None = None,
    datasource_id: str | None = None,
    row_count: int | None = None,
) -> None:
    """Emit ``query_executed`` on a SUCCESSFUL query execution (audit / POPIA log).

    POPIA-safe by construction: this payload contains ONLY metadata — no row
    data, no SQL text with literals, and no filter values that could carry PII.

    Fields
    ------
    query_id:
        The registered query id or metric slug, if any (None for ad-hoc SQL).
    subject:
        The caller's user-id or ``"embed"`` for embed-token callers.  This is
        the *subject* of the query — never the queried subject's personal data.
    datasource_id:
        The org-scoped datastore id used for the query (None for the built-in
        demo connector).
    row_count:
        Number of rows returned (the Arrow table row count).  Advisory — never
        the actual rows.
    """
    emit_event(
        QUERY_EXECUTED,
        org_id,
        {
            "query_id": query_id,
            "subject": subject,
            "datasource_id": datasource_id,
            "row_count": row_count,
        },
    )
