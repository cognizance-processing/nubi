"""Usage aggregation over ``usage_events`` (open-core, read-only).

All figures are derived from the ``usage_events`` table — which is populated
*off the hot path* by the fire-and-forget metering sink (``app.compute.metering``)
— so the usage surface adds no hot-path cost.  When the DB pool is unavailable
(local dev / tests) the same aggregation runs over the in-process metering sink
(``app.compute.metering.get_usage``), mirroring
``app.ee.billing.reconcile.aggregate_usage_for_org``.

Two shapes are produced:

- :func:`usage_summary` — current-period totals per metric, paired with the
  org's configured limit (from the EE tier hook, else unlimited) → ``{used,
  limit, pct}``.
- :func:`usage_series` — a per-bucket (day/hour) time series for one metric,
  for charting.

Metric catalogue
----------------
The :data:`METRICS` list is the single source of truth for which dimensions the
usage view exposes and how each maps onto ``usage_events`` rows:

- ``queries``         — count of ``compute`` events (one per query/kernel run).
- ``compute_units``   — summed ``units`` of ``compute`` events (CU).
- ``bytes_scanned``   — summed ``units`` of ``query_scan`` events (raw bytes).
- ``rows_returned``   — summed ``output_bytes`` proxy is NOT rows; we count
  ``compute`` events' ``output_bytes`` only where available (see note).  Rows
  are not separately metered, so this metric is omitted unless a future
  ``rows`` kind is recorded.
- ``flow_runs``       — count of ``agent_run`` / ``kernel`` (remote) events.
- ``ai_tokens``       — summed ``units`` of ``ai_call`` events (tokens when the
  AI path records them, else call count).
- ``storage_gb``      — period MAX of ``storage`` event ``units`` (GB).

Only metrics that map to recorded data are surfaced; everything is best-effort
and never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

logger = logging.getLogger("nubi.usage")

PeriodName = Literal["day", "week", "month"]
Bucket = Literal["hour", "day"]


@dataclass(frozen=True)
class UsageMetric:
    """One exposed usage dimension and how it maps onto ``usage_events``.

    Attributes
    ----------
    id:
        Stable metric id used in the API and by the EE limits hook.
    label:
        Human-readable name for the UI.
    unit:
        Display unit (``"count"``, ``"CU"``, ``"bytes"``, ``"GB"``, ``"tokens"``).
    kinds:
        ``usage_events.kind`` values that feed this metric (lower-cased).
    agg:
        How to combine event ``units`` within a kind:
        ``"count"`` (one per event), ``"sum"`` (sum ``units``),
        ``"max"`` (period peak — used for storage snapshots).
    """

    id: str
    label: str
    unit: str
    kinds: tuple[str, ...]
    agg: Literal["count", "sum", "max"]


# Single source of truth for the usage view's dimensions.  Order = display order.
METRICS: tuple[UsageMetric, ...] = (
    UsageMetric("queries", "Queries run", "count", ("compute", "kernel"), "count"),
    UsageMetric("compute_units", "Compute units", "CU", ("compute", "kernel"), "sum"),
    UsageMetric("bytes_scanned", "Bytes scanned", "bytes", ("query_scan", "scan"), "sum"),
    UsageMetric("flow_runs", "Flow runs", "count", ("agent_run", "agent"), "count"),
    UsageMetric("ai_tokens", "AI usage", "tokens", ("ai_call", "ai"), "sum"),
    UsageMetric("embedded_sessions", "Embedded sessions", "count", ("embedded_session", "embed"), "count"),
    UsageMetric("storage_gb", "Storage", "GB", ("storage",), "max"),
)

_METRIC_BY_ID: dict[str, UsageMetric] = {m.id: m for m in METRICS}

# Reverse map: a usage_events kind → the metric(s) it contributes to.
_KIND_TO_METRICS: dict[str, list[UsageMetric]] = {}
for _m in METRICS:
    for _k in _m.kinds:
        _KIND_TO_METRICS.setdefault(_k, []).append(_m)


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------


def period_bounds(period: str, now: datetime | None = None) -> tuple[datetime, datetime, Bucket]:
    """Return ``(start, end, bucket)`` for *period* (``"day"``/``"week"``/``"month"``).

    The bucket grain is chosen so a chart has a reasonable number of points:
    ``"day"`` → hourly buckets; ``"week"``/``"month"`` → daily buckets.  Unknown
    periods fall back to ``"month"``.  ``end`` is exclusive (``now``).
    """
    now = now or datetime.now(timezone.utc)
    if period == "day":
        start = now - timedelta(days=1)
        return start, now, "hour"
    if period == "week":
        start = now - timedelta(days=7)
        return start, now, "day"
    # default: calendar-month-to-date (matches the billing period fallback)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now, "day"


def _event_ts(ev: dict[str, Any]) -> datetime:
    """Return a tz-aware datetime for an event (``created_at`` or ``ts``)."""
    raw = ev.get("created_at")
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    ts = ev.get("ts")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Event source: DB ``usage_events`` with in-memory sink fallback
# ---------------------------------------------------------------------------


async def _events_for_org(org_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Return raw usage events for *org_id* in [start, end).

    Prefers the ``usage_events`` table; on any failure (no pool / test double)
    falls back to the in-process metering sink, filtered to the org and window.
    Never raises.
    """
    try:
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            """
            SELECT kind, tier, units, output_bytes, created_at
            FROM usage_events
            WHERE org_id = $1::uuid
              AND created_at >= $2 AND created_at < $3
            ORDER BY created_at
            """,
            str(org_id), start, end,
        )
        if rows:
            return [dict(r) for r in rows]
        # An empty DB result is authoritative ONLY when a pool is configured;
        # if the in-memory sink also has data (local dev), prefer that below.
    except Exception:  # noqa: BLE001 — DB not available → in-memory fallback
        pass

    try:
        from app.compute.metering import get_usage  # noqa: PLC0415

        out: list[dict[str, Any]] = []
        for ev in get_usage():
            if str(ev.get("org_id")) != str(org_id):
                continue
            ts = _event_ts(ev)
            if start <= ts < end:
                out.append(ev)
        return out
    except Exception:  # noqa: BLE001
        return []


def _metric_value(metric: UsageMetric, events: list[dict[str, Any]]) -> float:
    """Aggregate *events* into one numeric value for *metric*."""
    relevant = [e for e in events if (e.get("kind") or "").lower() in metric.kinds]
    if not relevant:
        return 0.0
    if metric.agg == "count":
        return float(len(relevant))
    if metric.agg == "max":
        return max((float(e.get("units") or 0.0) for e in relevant), default=0.0)
    # sum
    return float(sum(float(e.get("units") or 0.0) for e in relevant))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def usage_summary(org_id: str, period: str = "month", now: datetime | None = None) -> dict[str, Any]:
    """Return the org's current-period usage summary with soft quotas.

    Shape::

        {
          "period": "month",
          "period_start": "<iso>",
          "period_end":   "<iso>",
          "metrics": [
            {"id": "queries", "label": "Queries run", "unit": "count",
             "used": 12.0, "limit": 2000.0 | null, "pct": 0.6 | null},
            ...
          ]
        }

    ``limit`` is ``None`` (unlimited) unless EE registered a usage-limits
    provider for the org's tier.  ``pct`` is ``used / limit * 100`` rounded to
    one decimal, or ``None`` when the metric is unlimited.
    """
    from app.features import get_usage_limits  # noqa: PLC0415

    start, end, _bucket = period_bounds(period, now)
    events = await _events_for_org(org_id, start, end)
    limits = await get_usage_limits(org_id)

    metrics_out: list[dict[str, Any]] = []
    for metric in METRICS:
        used = _metric_value(metric, events)
        limit = limits.get(metric.id)
        pct: float | None = None
        if limit is not None and float(limit) > 0:
            pct = round(used / float(limit) * 100.0, 1)
        metrics_out.append(
            {
                "id": metric.id,
                "label": metric.label,
                "unit": metric.unit,
                "used": used,
                "limit": (float(limit) if limit is not None else None),
                "pct": pct,
            }
        )

    return {
        "period": period if period in ("day", "week", "month") else "month",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "metrics": metrics_out,
    }


def _bucket_key(ts: datetime, bucket: Bucket) -> datetime:
    """Floor *ts* to the start of its hour/day bucket (UTC)."""
    ts = ts.astimezone(timezone.utc)
    if bucket == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def _iter_buckets(start: datetime, end: datetime, bucket: Bucket) -> list[datetime]:
    """Return the ordered list of bucket-start datetimes covering [start, end)."""
    step = timedelta(hours=1) if bucket == "hour" else timedelta(days=1)
    cur = _bucket_key(start, bucket)
    out: list[datetime] = []
    # Cap the number of buckets defensively (day=24, month≈31, week=7).
    while cur < end and len(out) < 800:
        out.append(cur)
        cur = cur + step
    return out


async def usage_series(
    org_id: str, metric_id: str, period: str = "month", now: datetime | None = None
) -> dict[str, Any]:
    """Return a per-bucket time series for one metric (for charting).

    Shape::

        {
          "metric": "queries", "label": "Queries run", "unit": "count",
          "period": "month", "bucket": "day",
          "points": [{"t": "<iso>", "value": 3.0}, ...]
        }

    Buckets are dense (zero-filled) so the chart has a continuous x-axis.
    Raises ``KeyError`` semantics are avoided — an unknown ``metric_id`` yields
    an empty (but well-formed) series so the route can 404 explicitly instead.
    """
    metric = _METRIC_BY_ID.get(metric_id)
    start, end, bucket = period_bounds(period, now)
    if metric is None:
        return {
            "metric": metric_id, "label": metric_id, "unit": "",
            "period": period, "bucket": bucket, "points": [],
        }

    events = await _events_for_org(org_id, start, end)
    relevant = [e for e in events if (e.get("kind") or "").lower() in metric.kinds]

    # Aggregate per bucket.  For "max" metrics (storage) we take the per-bucket
    # peak; for count/sum we accumulate.
    agg: dict[datetime, float] = {}
    for ev in relevant:
        key = _bucket_key(_event_ts(ev), bucket)
        if metric.agg == "count":
            agg[key] = agg.get(key, 0.0) + 1.0
        elif metric.agg == "max":
            agg[key] = max(agg.get(key, 0.0), float(ev.get("units") or 0.0))
        else:  # sum
            agg[key] = agg.get(key, 0.0) + float(ev.get("units") or 0.0)

    points = [
        {"t": b.isoformat(), "value": round(agg.get(b, 0.0), 4)}
        for b in _iter_buckets(start, end, bucket)
    ]
    return {
        "metric": metric.id,
        "label": metric.label,
        "unit": metric.unit,
        "period": period if period in ("day", "week", "month") else "month",
        "bucket": bucket,
        "points": points,
    }
