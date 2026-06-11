"""Usage metering (open-core) — per-org usage aggregation + soft quota view.

This is the OSS-core *usage* surface: read-only visibility into what an org has
consumed.  It deliberately contains **no billing** — charging, wallets, and
Paystack stay in the EE tree (``app.ee.billing``).  Usage (the counters) is a
core concern; billing (turning counters into money) is the commercial one.

Where the numbers come from
---------------------------
Everything is aggregated from the ``usage_events`` table (core migration
``0006_platform.sql``), which is already populated **off the hot path** by the
fire-and-forget metering sink (``app.compute.metering``):

- ``compute``     — query/kernel compute units (``units`` = CU; ``elapsed_ms``).
- ``query_scan``  — bytes scanned per cache-miss query (``units`` = bytes).
- ``kernel``      — legacy compute kind (folded into ``compute``).
- ``agent_run``   — remote-kernel / flow runs.
- ``ai_call``     — AI generate/chat completions.
- ``embedded_session`` — embedded view sessions.
- ``storage``     — periodic storage snapshots (``units`` = GB; period MAX).

Because we only ever READ ``usage_events`` (the metering writes already happen
asynchronously elsewhere), the usage surface adds **zero hot-path cost** — it is
a pure aggregation over data that is already recorded.  No new counters table,
no new migration.

Soft quota
----------
:func:`usage_summary` pairs each metric's used value with a configured *limit*.
Limits come from the EE tier when EE is loaded (via the optional
``app.features`` usage-limits hook), and otherwise default to *unlimited*
(``None``) — core never enforces a hard billing block, this is visibility only.
"""

from __future__ import annotations

from app.usage.aggregate import (
    METRICS,
    UsageMetric,
    period_bounds,
    usage_series,
    usage_summary,
)

__all__ = [
    "METRICS",
    "UsageMetric",
    "period_bounds",
    "usage_series",
    "usage_summary",
]
