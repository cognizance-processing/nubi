"""Usage endpoints (``/usage``) — open-core usage metering view.

Routes (all under ``/api/v1``)
------------------------------
- ``GET /usage``        — current-period usage summary per org, each metric paired
                          with its configured soft limit → ``{used, limit, pct}``.
- ``GET /usage/series`` — a per-bucket time series for one metric (for charts).

Both are org-scoped via the standard ``current_user`` + ``resolve_org_id``
pattern (honours ``X-Org-Id`` for org switching, verifying membership), so an
org only ever sees its own usage — tenant isolation is the same gate used by
``/connectors`` and friends.

Open-core boundary
------------------
This surface is intentionally BILLING-FREE.  It reads the core ``usage_events``
table (populated off the hot path by the metering sink) via ``app.usage`` and
surfaces *soft* quotas: limits come from the EE tier when EE is loaded (through
the ``app.features`` usage-limits hook) and otherwise default to unlimited.
Core never enforces a hard billing block here — this is visibility only.

The ``/usage`` prefix does not collide with the semantic ``/metrics`` layer or
the ``/ops`` observability surface; it self-registers on the shared
``api_router`` at import time (mirrors ``watches`` / ``connectors``).
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from app.auth.deps import current_user
from app.errors import AppError
from app.repos.provider import get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id
from app.usage import METRICS, usage_series, usage_summary

_VALID_PERIODS = ("day", "week", "month")
_METRIC_IDS = {m.id for m in METRICS}


async def _caller_org(user: dict[str, Any], request: Request) -> str:
    """Resolve the caller's effective org id (honours ``X-Org-Id``)."""
    return await resolve_org_id(str(user["id"]), get_repo(), request)


def _normalise_period(period: str | None) -> str:
    """Clamp an arbitrary ``?period=`` value to a supported window."""
    p = (period or "month").strip().lower()
    return p if p in _VALID_PERIODS else "month"


@api_router.get("/usage")
async def get_usage(
    request: Request,
    period: str = "month",
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the caller's org usage summary for the current period.

    Query params
    ------------
    period:
        ``"day"`` | ``"week"`` | ``"month"`` (default; calendar-month-to-date).
        Unknown values fall back to ``"month"``.
    """
    org_id = await _caller_org(user, request)
    return await usage_summary(org_id, _normalise_period(period))


@api_router.get("/usage/series")
async def get_usage_series(
    request: Request,
    metric: str,
    period: str = "month",
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return a per-bucket time series for one usage *metric*.

    Query params
    ------------
    metric:
        A usage-metric id (see ``app.usage.aggregate.METRICS``), e.g.
        ``"queries"``, ``"compute_units"``, ``"bytes_scanned"``. Unknown ids 404.
    period:
        ``"day"`` (hourly buckets) | ``"week"`` | ``"month"`` (daily buckets).
    """
    metric_id = (metric or "").strip().lower()
    if metric_id not in _METRIC_IDS:
        raise AppError(
            "metric_not_found",
            f"Unknown usage metric {metric!r}. Valid: {sorted(_METRIC_IDS)}.",
            404,
        )
    org_id = await _caller_org(user, request)
    return await usage_series(org_id, metric_id, _normalise_period(period))
