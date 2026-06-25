"""Flow-event listener that populates the freshness registry on run completion.

How it works
------------
On application startup (main.py lifespan) this module registers
``_on_flow_event`` with ``app.flows.events.register_flow_listener``.

Whenever the flow executor emits a ``flow_success`` event the listener:
  1. Resolves the flow's ``org_id`` and ``dataset_key`` (taken from
     ``event.extra["dataset_key"]`` if set, otherwise falls back to the
     flow name / flow_run_id).
  2. Calls ``store.upsert(org_id, dataset_key, last_success_at=now)``.

This keeps the ``dataset_freshness`` table up to date without any polling.

The listener is synchronous (as required by the events module) and performs no
blocking I/O — it schedules an async upsert via ``asyncio.create_task`` so
the executor is never blocked.

Registration
------------
Call ``register_health_listener()`` from main.py's lifespan once at startup.
``unregister_health_listener()`` is provided for test teardown.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.flows.events import (
    FlowEvent,
    register_flow_listener,
    unregister_flow_listener,
)

logger = logging.getLogger("nubi.health.listener")


def _on_flow_event(event: FlowEvent) -> None:
    """Handle a flow event; upsert freshness on success."""
    if event.type != "flow_success":
        return

    extra = event.extra or {}
    org_id: str | None = extra.get("org_id")
    dataset_key: str | None = extra.get("dataset_key") or extra.get("flow_name")
    expected_interval_s: int | None = extra.get("expected_interval_s")

    if not org_id or not dataset_key:
        # Not enough context to update registry — skip silently.
        return

    now = datetime.now(timezone.utc)

    async def _do_upsert() -> None:
        try:
            from app.health.store import get_freshness_store
            store = get_freshness_store()
            result = store.upsert(
                org_id=org_id,
                dataset_key=dataset_key,
                last_success_at=now,
                expected_interval_s=expected_interval_s,
            )
            # PgFreshnessStore.upsert is a coroutine; InMemory is sync.
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001
            logger.warning(
                "Health listener: failed to upsert freshness for %s/%s",
                org_id,
                dataset_key,
                exc_info=True,
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_do_upsert())
    except RuntimeError:
        # No running event loop (e.g. sync test context) — best-effort skip.
        pass


def register_health_listener() -> None:
    """Register the freshness listener with the flow event system.

    Call once from the application lifespan (main.py).
    Idempotent — registering twice is a no-op (events module guarantees this).
    """
    register_flow_listener(_on_flow_event)
    logger.debug("Health freshness listener registered.")


def unregister_health_listener() -> None:
    """Remove the freshness listener. Intended for test teardown."""
    unregister_flow_listener(_on_flow_event)
