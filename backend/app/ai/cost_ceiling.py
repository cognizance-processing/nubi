"""Per-user and per-org rolling chat cost ceiling (M-cost-ceiling).

Enforces configurable daily USD spend limits for AI chat turns, using the
existing ``usage_events`` metering store so no parallel bookkeeping is needed.

Configuration (env vars)
------------------------
NUBI_CHAT_USER_DAILY_USD  — per-(org, user) daily ceiling in USD.
                            Default: 0 (unlimited).  Set to e.g. "2.00".
NUBI_CHAT_ORG_DAILY_USD   — per-org daily ceiling in USD.
                            Default: 0 (unlimited).  Set to e.g. "50.00".

"0" or unset → unlimited (no enforcement).

Usage
-----
Two public coroutines, both called from ``app.routes.chat``:

    await check_chat_budget(org_id, user_id, cost_usd=0.0)
        Pre-turn guard: raises ``AppError("chat_budget_exceeded", 429)``
        when the rolling daily spend ALREADY exceeds the ceiling.
        Pass ``cost_usd=0.0`` for the pre-flight check (before we know the
        turn's cost); pass the actual turn cost for the post-turn check.

    await record_chat_cost(org_id, user_id, cost_usd)
        Record a turn's cost into the metering store.  Wired as a
        fire-and-forget call after every turn regardless of ceiling.

Architecture
------------
- Reuses ``app.compute.metering`` — events are written with
  ``kind="ai_chat_cost"`` and ``units=cost_usd``.
- The ceiling check sums ``units`` for ``kind="ai_chat_cost"`` over the last
  24 h for the given (org_id, user_id) pair, sourced from whichever sink is
  active:
    * InMemorySink (tests/local dev): filters ``get_events()`` in-process.
    * PgSink (production): runs a lightweight aggregation query.
- Fail-open: any exception from the metering store is caught and logged; the
  turn proceeds rather than blocking users because of a metering outage.
- NullProvider path: ``cost_usd`` is always 0.0, so NullProvider turns
  never accumulate cost and are never falsely blocked.

Event schema stored in usage_events
------------------------------------
  kind         = "ai_chat_cost"
  units        = cost_usd (float, may be 0.0 for NullProvider)
  org_id       = caller's org UUID
  user_id      = caller's user UUID
  tier         = "chat"
  elapsed_ms   = 0
  output_bytes = 0
"""

from __future__ import annotations

import logging
import os
import time
from datetime import timezone

logger = logging.getLogger("nubi.ai.cost_ceiling")

# Event kind used to tag cost records in the metering store.
_COST_KIND = "ai_chat_cost"
# Rolling window: last 24 hours (seconds).
_WINDOW_S = 86_400


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _user_daily_limit() -> float | None:
    """Return per-(org, user) daily USD ceiling, or None for unlimited."""
    raw = os.environ.get("NUBI_CHAT_USER_DAILY_USD", "0").strip()
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    return val if val > 0 else None


def _org_daily_limit() -> float | None:
    """Return per-org daily USD ceiling, or None for unlimited."""
    raw = os.environ.get("NUBI_CHAT_ORG_DAILY_USD", "0").strip()
    try:
        val = float(raw)
    except (ValueError, TypeError):
        return None
    return val if val > 0 else None


# ---------------------------------------------------------------------------
# Spend query helpers — source-agnostic
# ---------------------------------------------------------------------------


def _sum_from_memory(
    events: list[dict],
    *,
    org_id: str,
    user_id: str | None,
    since_ts: float,
) -> float:
    """Sum ``units`` from an in-memory events list for the given filters."""
    total = 0.0
    for ev in events:
        if ev.get("kind") != _COST_KIND:
            continue
        if ev.get("org_id") != org_id:
            continue
        if user_id is not None and ev.get("user_id") != user_id:
            continue
        ts = ev.get("ts", 0.0)
        if ts < since_ts:
            continue
        total += float(ev.get("units") or 0.0)
    return total


async def _query_spend_pg(
    *,
    org_id: str,
    user_id: str | None,
    since_ts: float,
) -> float:
    """Query the PgSink's ``usage_events`` table for accumulated cost."""
    from datetime import datetime  # noqa: PLC0415
    from app import db as _db  # noqa: PLC0415

    since_dt = datetime.fromtimestamp(since_ts, tz=timezone.utc)

    if user_id is not None:
        rows = await _db.fetch(
            """
            SELECT COALESCE(SUM(units), 0) AS total
            FROM usage_events
            WHERE kind     = $1
              AND org_id   = $2::uuid
              AND user_id  = $3::uuid
              AND created_at >= $4
            """,
            _COST_KIND,
            org_id,
            user_id,
            since_dt,
        )
    else:
        rows = await _db.fetch(
            """
            SELECT COALESCE(SUM(units), 0) AS total
            FROM usage_events
            WHERE kind     = $1
              AND org_id   = $2::uuid
              AND created_at >= $3
            """,
            _COST_KIND,
            org_id,
            since_dt,
        )

    return float((rows[0]["total"] if rows else 0) or 0)


async def _get_spend(
    org_id: str,
    user_id: str | None,
) -> float:
    """Return rolling 24-h spend in USD for the given (org, user?) scope.

    Fail-open: returns 0.0 on any error so a metering outage does not
    block chat.
    """
    since_ts = time.time() - _WINDOW_S
    try:
        from app.compute.metering import get_sink, InMemorySink  # noqa: PLC0415

        sink = get_sink()
        if isinstance(sink, InMemorySink):
            return _sum_from_memory(
                sink.get_events(),
                org_id=org_id,
                user_id=user_id,
                since_ts=since_ts,
            )
        # PgSink (or any other sink) — query the DB.
        return await _query_spend_pg(
            org_id=org_id,
            user_id=user_id,
            since_ts=since_ts,
        )
    except Exception:  # noqa: BLE001 — fail-open
        logger.exception(
            "cost_ceiling: failed to query spend for org=%s user=%s — failing open",
            org_id,
            user_id,
        )
        return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def check_chat_budget(
    org_id: str,
    user_id: str,
    *,
    cost_usd: float = 0.0,
) -> None:
    """Raise ``AppError("chat_budget_exceeded", 429)`` when a ceiling is hit.

    Call **before** the turn (``cost_usd=0.0``) for the pre-flight check and
    **after** recording the turn cost (``cost_usd=actual``) for the post-turn
    check.  Both calls share the same enforcement logic.

    Fail-open: when the metering store is unavailable the function returns
    normally and does NOT block the turn.

    Parameters
    ----------
    org_id:
        The caller's org UUID.
    user_id:
        The caller's user UUID.
    cost_usd:
        The cost of the CURRENT turn to add to the running total before
        comparing against the ceiling.  Pass ``0.0`` for the pre-flight
        check (before the turn runs).
    """
    user_limit = _user_daily_limit()
    org_limit = _org_daily_limit()

    # Fast exit when no limits are configured (unlimited).
    if user_limit is None and org_limit is None:
        return

    # ── Per-user ceiling ────────────────────────────────────────────────────
    if user_limit is not None:
        user_spend = await _get_spend(org_id, user_id) + cost_usd
        if user_spend > user_limit:
            from app.errors import AppError  # noqa: PLC0415

            raise AppError(
                "chat_budget_exceeded",
                (
                    f"Daily AI chat budget exceeded for this user "
                    f"(spent ${user_spend:.4f} / limit ${user_limit:.2f} USD). "
                    f"Resets in less than 24 hours."
                ),
                429,
            )

    # ── Per-org ceiling ─────────────────────────────────────────────────────
    if org_limit is not None:
        org_spend = await _get_spend(org_id, None) + cost_usd
        if org_spend > org_limit:
            from app.errors import AppError  # noqa: PLC0415

            raise AppError(
                "chat_budget_exceeded",
                (
                    f"Daily AI chat budget exceeded for this organisation "
                    f"(spent ${org_spend:.4f} / limit ${org_limit:.2f} USD). "
                    f"Resets in less than 24 hours."
                ),
                429,
            )


async def record_chat_cost(
    org_id: str,
    user_id: str,
    cost_usd: float,
) -> None:
    """Record a completed turn's cost into the metering store.

    Uses ``kind="ai_chat_cost"`` so that cost events are kept separate from
    the generic ``"ai_call"`` count events (which record units=1.0 per call).
    This avoids confusing the two dimensions in the aggregation layer.

    Fail-open: errors are logged but never re-raised.

    Parameters
    ----------
    org_id:
        The caller's org UUID.
    user_id:
        The caller's user UUID.
    cost_usd:
        Actual USD cost of the completed turn.  ``0.0`` for NullProvider
        turns (no cost incurred).
    """
    try:
        from app.compute.metering import record_usage  # noqa: PLC0415

        await record_usage(
            kind=_COST_KIND,
            user_id=user_id,
            org_id=org_id,
            units=cost_usd,
            tier="chat",
            elapsed_ms=0,
            output_bytes=0,
        )
    except Exception:  # noqa: BLE001 — fail-open
        logger.exception(
            "cost_ceiling: failed to record cost for org=%s user=%s cost_usd=%s",
            org_id,
            user_id,
            cost_usd,
        )
