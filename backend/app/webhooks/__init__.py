"""Outbound webhook / event-sink subsystem.

First-class outbound webhooks let host products receive Nubi signals (events):
``watch_breach``, ``freshness_stale``, ``query_failed``, ``flow_completed``.

Modules
-------
events
    Event-type constants, payload builders, and the ``emit_event`` entrypoint
    used by producers (watch dispatch, query error paths, flow completion,
    freshness checks).
models
    Org-scoped store for the ``webhook_endpoints`` table (InMemory + Pg +
    singleton provider), mirroring ``app.security.issuers_store``.
delivery
    Signed async HTTP delivery: HMAC-SHA256 signature, timestamp header,
    bounded retry with backoff. Fire-and-forget; failures are logged, never
    raised, so a webhook can never break the request that triggered it.
schemas
    Pydantic request/response schemas for the admin CRUD API.
router
    Org-scoped admin CRUD API (first-party auth), self-registered on
    ``app.routes.api_router``.
"""

from __future__ import annotations
