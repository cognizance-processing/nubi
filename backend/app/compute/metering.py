"""Kernel usage metering — PROD provider pattern.

Records kernel execution events through a swappable ``MeteringSink`` interface:

- ``InMemorySink``  — in-process list (default; used by tests and local dev).
- ``PgSink``        — persists each event to the ``usage_events`` table via
                      ``app.db.execute`` with parameterised asyncpg ``$N`` args
                      (never f-string interpolation).

Provider selection
------------------
``get_sink()`` returns a ``PgSink`` when the env var ``METERING_PERSIST=1``
is set; otherwise an ``InMemorySink``.

Test injection
--------------
``set_sink(sink)`` replaces the module-level singleton so tests can inject a
fresh ``InMemorySink`` without touching env vars.  ``get_usage()`` and
``clear_usage()`` both operate on the *current* singleton's in-memory store
(only meaningful when the active sink is an ``InMemorySink``).

Billing-model dimensions
------------------------
- ``org_id``      — org-level cost attribution (may be ``None`` for legacy callers).
- ``user_id``     — per-user cost attribution.
- ``kind``        — event category (e.g. ``"kernel"``).
- ``tier``        — compute tier (``"local_kernel"`` / ``"remote_kernel"``).
- ``elapsed_ms``  — wall-clock kernel time.
- ``output_bytes``— data egress (bytes processed pricing).
- ``units``       — abstract billing units (future; defaults to elapsed_ms / 1000).

Thread safety
-------------
``InMemorySink._log`` is mutated only within the ASGI event loop.  No locking
is required for the ASGI use case.  ``PgSink`` delegates to ``app.db.execute``
which uses asyncpg's own pool-level thread safety.
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("nubi.metering")


# ---------------------------------------------------------------------------
# MeteringSink interface
# ---------------------------------------------------------------------------


class MeteringSink(ABC):
    """Abstract base for metering backends."""

    @abstractmethod
    async def record(
        self,
        *,
        user_id: str,
        tier: str,
        elapsed_ms: int,
        output_bytes: int,
        org_id: str | None = None,
        kind: str = "kernel",
        units: float | None = None,
    ) -> None:
        """Persist one metering event.

        Parameters
        ----------
        user_id:
            The authenticated user's ID (UUID string).
        tier:
            The compute tier (e.g. ``"local_kernel"``).
        elapsed_ms:
            Kernel wall-clock duration in milliseconds.
        output_bytes:
            Size of the Arrow IPC result in bytes.
        org_id:
            Organisation UUID string (may be ``None``).
        kind:
            Event category — defaults to ``"kernel"``.
        units:
            Abstract billing units — defaults to ``elapsed_ms / 1000`` (kernel-seconds).
        """
        ...

    def get_events(self) -> list[dict[str, Any]]:
        """Return recorded events (only meaningful for InMemorySink).

        Subclasses that don't maintain an in-memory list return ``[]``.
        """
        return []

    def clear(self) -> None:
        """Clear recorded events (no-op for PgSink)."""


# ---------------------------------------------------------------------------
# InMemorySink — list-backed, used in tests and local dev
# ---------------------------------------------------------------------------


class InMemorySink(MeteringSink):
    """In-process list-backed metering sink (test / local dev).

    Accumulates events in ``_log``; ``get_events()`` returns a snapshot;
    ``clear()`` empties the log.
    """

    def __init__(self) -> None:
        self._log: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        user_id: str,
        tier: str,
        elapsed_ms: int,
        output_bytes: int,
        org_id: str | None = None,
        kind: str = "kernel",
        units: float | None = None,
    ) -> None:
        if units is None:
            units = elapsed_ms / 1000.0
        self._log.append(
            {
                "user_id": user_id,
                "org_id": org_id,
                "kind": kind,
                "tier": tier,
                "elapsed_ms": elapsed_ms,
                "output_bytes": output_bytes,
                "units": units,
                "ts": time.time(),
            }
        )

    def get_events(self) -> list[dict[str, Any]]:
        return list(self._log)

    def clear(self) -> None:
        self._log.clear()


# ---------------------------------------------------------------------------
# PgSink — persists to usage_events via app.db.execute (parameterised SQL)
# ---------------------------------------------------------------------------


class PgSink(MeteringSink):
    """asyncpg-backed metering sink.

    Inserts one row into ``usage_events`` per ``record()`` call using
    parameterised asyncpg ``$N`` placeholders — never f-string interpolation.

    The ``app.db`` module is imported lazily inside ``record()`` so that this
    class can be instantiated at import time without triggering the DB pool.
    """

    async def record(
        self,
        *,
        user_id: str,
        tier: str,
        elapsed_ms: int,
        output_bytes: int,
        org_id: str | None = None,
        kind: str = "kernel",
        units: float | None = None,
    ) -> None:
        if units is None:
            units = elapsed_ms / 1000.0

        # Lazy import — avoids importing the DB pool at module load time.
        from app import db as _db  # noqa: PLC0415

        await _db.execute(
            """
            INSERT INTO usage_events
                (org_id, user_id, kind, tier, elapsed_ms, output_bytes, units)
            VALUES
                ($1, $2, $3, $4, $5, $6, $7)
            """,
            org_id,
            user_id,
            kind,
            tier,
            elapsed_ms,
            output_bytes,
            units,
        )


# ---------------------------------------------------------------------------
# Module-level singleton + provider
# ---------------------------------------------------------------------------

_sink: MeteringSink | None = None


def get_sink() -> MeteringSink:
    """Return the active metering sink.

    Selection logic (first match wins):
    1. If ``set_sink()`` has been called, return that instance.
    2. Else if ``METERING_PERSIST=1`` (env), return a ``PgSink``.
    3. Else return an ``InMemorySink``.

    The singleton is initialised lazily so that env-var reads happen at
    call time (not import time), respecting test overrides.
    """
    global _sink
    if _sink is None:
        if os.getenv("METERING_PERSIST") == "1":
            _sink = PgSink()
        else:
            _sink = InMemorySink()
    return _sink


def set_sink(sink: MeteringSink | None) -> None:
    """Override the active sink (for tests).

    Pass ``None`` to reset to the auto-selected default on the next
    ``get_sink()`` call.

    Parameters
    ----------
    sink:
        A ``MeteringSink`` instance to inject, or ``None`` to reset.
    """
    global _sink
    _sink = sink


# ---------------------------------------------------------------------------
# Public API — backward-compatible with existing callers in routes/compute.py
# ---------------------------------------------------------------------------


async def record_kernel_usage(
    user_id: str,
    tier: str,
    elapsed_ms: int,
    output_bytes: int,
    org_id: str | None = None,
) -> None:
    """Record a kernel usage event via the active sink.

    This is the primary public entry point.  Existing callers in
    ``routes/compute.py`` pass ``user_id``, ``tier``, ``elapsed_ms``, and
    ``output_bytes``; the remaining parameters are optional.

    Parameters
    ----------
    user_id:
        The authenticated user's ID.
    tier:
        The compute tier (e.g. ``"local_kernel"``).
    elapsed_ms:
        Kernel wall-clock duration in milliseconds.
    output_bytes:
        Size of the Arrow IPC result in bytes.
    org_id:
        Organisation UUID string (may be ``None``).
    """
    if org_id is None:
        # Billing aggregation (app.ee.billing.reconcile.aggregate_usage_for_org)
        # filters on org_id — a NULL-org event can never be billed or counted
        # against any quota.  Catch unattributable callers early.
        logger.warning(
            "metering: kernel usage event for user=%s has no org_id — "
            "it will not count toward any org's quota or billing",
            user_id,
        )
    await get_sink().record(
        user_id=user_id,
        tier=tier,
        elapsed_ms=elapsed_ms,
        output_bytes=output_bytes,
        org_id=org_id,
        kind="kernel",
    )


async def record_usage(
    *,
    kind: str,
    user_id: str,
    org_id: str | None,
    units: float = 1.0,
    tier: str = "",
    elapsed_ms: int = 0,
    output_bytes: int = 0,
) -> None:
    """Record one usage event of an arbitrary billing *kind* via the active sink.

    The generic counterpart of :func:`record_kernel_usage` for the non-kernel
    metered dimensions consumed by ``app.ee.billing.reconcile``:

    - ``kind="ai_call"``          — one AI generate/chat completion (units=1).
    - ``kind="embedded_session"`` — one embedded view session (units=1).
    - ``kind="agent_run"``        — one remote-kernel / agent run (units=1).
    - ``kind="storage"``          — a storage snapshot; ``units`` = total GB
      (billing takes the period MAX, not the sum).

    Unlike the kernel path, ``units`` never defaults to ``elapsed_ms / 1000``
    — discrete dimensions are counted, not timed — so it is passed explicitly
    (default ``1.0``).

    Parameters
    ----------
    kind:
        Event category (see above).
    user_id:
        The authenticated user's ID.
    org_id:
        Organisation UUID string.  ``None`` is tolerated but logged: such
        events are invisible to billing aggregation.
    units:
        Billing units for this event (default ``1.0``).
    tier / elapsed_ms / output_bytes:
        Optional extra dimensions (kept for parity with the sink schema).
    """
    if org_id is None:
        logger.warning(
            "metering: %s usage event for user=%s has no org_id — "
            "it will not count toward any org's quota or billing",
            kind,
            user_id,
        )
    await get_sink().record(
        user_id=user_id,
        tier=tier,
        elapsed_ms=elapsed_ms,
        output_bytes=output_bytes,
        org_id=org_id,
        kind=kind,
        units=units,
    )


def record_usage_safe(**kwargs: Any) -> None:
    """Sync-safe, best-effort wrapper around :func:`record_usage`.

    Mirrors :func:`record_kernel_usage_safe`: schedules fire-and-forget on a
    running loop when one exists, else runs to completion.  Metering is
    best-effort — failures are swallowed.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop is not None:
            loop.create_task(record_usage(**kwargs))
        else:
            asyncio.run(record_usage(**kwargs))
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        pass


def record_kernel_usage_safe(**kwargs: Any) -> None:
    """Sync-safe wrapper for non-async callers (e.g. the jobs executor).

    ``record_kernel_usage`` is async (PgSink does async DB I/O).  Sync call
    sites schedule it fire-and-forget on the running loop when one exists,
    else run it to completion.  Metering is best-effort: failures are swallowed.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop is not None:
            loop.create_task(record_kernel_usage(**kwargs))
        else:
            asyncio.run(record_kernel_usage(**kwargs))
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        pass


def get_usage() -> list[dict[str, Any]]:
    """Return a snapshot of all recorded usage events.

    Operates on the active sink's ``get_events()`` method.  For an
    ``InMemorySink`` this returns all accumulated events; for a ``PgSink``
    it returns ``[]`` (events live in the database).

    Returns
    -------
    list[dict]
        List of event dicts.  Empty for ``PgSink``.
    """
    return get_sink().get_events()


def clear_usage() -> None:
    """Clear the in-memory usage log.

    Calls ``get_sink().clear()`` — a no-op for ``PgSink``.
    """
    get_sink().clear()
