"""Watch evaluation, AI explanation, and breach dispatch — the WATCH end-state.

A *watch* monitors a single governed metric (``app.metrics``) at a chosen
grain/dimension and fires when a threshold (or a change-over-time rule) is
breached. This module is the pure-ish engine behind ``app.routes.watches``:

- :class:`Watch` — an immutable parse of the persisted ``watches.config`` dict.
- :func:`evaluate_watch` — compile + execute the watched metric (REUSING the
  exact ``/metrics/{id}/query`` execution path, RLS via ``claims``), reduce the
  result to a scalar, and evaluate the threshold/comparison → :class:`WatchResult`.
- :func:`explain_breach` — compose a concise human explanation via the AI
  provider; under :class:`~app.ai.provider.NullProvider` the explanation is a
  DETERMINISTIC template (value, threshold, delta, top dimension) — no network.
- :func:`fire_watch` — build an alert event and dispatch it through the
  ``app.chat.notify`` channels exactly like ``runtime._fire_flow_alert``;
  best-effort, never raises, returns the count sent.
- :func:`run_watch` — evaluate → (if breached) explain + fire → summary dict.

Reuse, not reinvention
----------------------
``evaluate_watch`` does NOT fork the metrics machinery. It calls
:func:`app.metrics.compile.compile_metric` then runs the compiled SQL through the
SAME planner+connector chain ``POST /metrics/{id}/query`` uses
(``resolve_named_params`` → ``planner.plan`` with ``claims['policies']`` →
``_build_connector_for_plan`` → ``connector.execute``), so RLS is threaded
identically. The shared step is factored into :func:`_run_metric`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.metrics.compile import compile_metric
from app.metrics.models import MetricDefinition, MetricQuery

logger = logging.getLogger("nubi.watch")

__all__ = [
    "Watch",
    "WatchResult",
    "evaluate_watch",
    "explain_breach",
    "fire_watch",
    "run_watch",
]

# Threshold operators → comparison callables. Mirrors metrics.FilterOp scalar
# ops; ``==`` is included for exact-match thresholds.
_OPS: dict[str, Any] = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}


# ---------------------------------------------------------------------------
# Watch definition (parsed from the persisted config dict)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Watch:
    """A parsed watch definition.

    ``threshold`` is ``{op, value}`` (a level rule on the scalar measure), OR
    ``comparison`` is ``{kind:'change_pct', vs:'previous_period', op, value}`` (a
    change-over-time rule). Exactly one drives the breach decision — when both
    are present the ``comparison`` rule wins (it is the more specific intent).
    """

    id: str
    name: str
    metric_id: str
    dimensions: tuple[str, ...] = ()
    time_grain: str | None = None
    threshold: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    channel_config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_config(
        cls, *, id: str, name: str, metric_id: str, config: dict[str, Any] | None
    ) -> Watch:
        """Build a :class:`Watch` from a ``watches`` row id/name/metric + config."""
        cfg = dict(config or {})
        comparison = cfg.get("comparison")
        # Tolerate the change rule living under either key.
        if comparison is None and isinstance(cfg.get("change"), dict):
            comparison = cfg.get("change")
        return cls(
            id=str(id),
            name=str(name or id),
            metric_id=str(metric_id),
            dimensions=tuple(str(d) for d in (cfg.get("dimensions") or ())),
            time_grain=cfg.get("time_grain"),
            threshold=cfg.get("threshold") if isinstance(cfg.get("threshold"), dict) else None,
            comparison=comparison if isinstance(comparison, dict) else None,
            channel_config=dict(cfg.get("channel_config") or cfg.get("channel") or {}),
            enabled=bool(cfg.get("enabled", True)),
        )

    def measure_name(self, metric: MetricDefinition) -> str:
        """The output column the scalar is reduced from (the metric's measure)."""
        return metric.measure.name


# ---------------------------------------------------------------------------
# Evaluation result
# ---------------------------------------------------------------------------


@dataclass
class WatchResult:
    """The outcome of evaluating a watch."""

    breached: bool
    value: float | None
    threshold: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    previous_value: float | None = None
    delta_pct: float | None = None
    top_dimension: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    measure_name: str = "value"
    error: str | None = None

    @property
    def state(self) -> str:
        """The persisted ``last_state``: ``error`` / ``breached`` / ``ok``."""
        if self.error is not None:
            return "error"
        return "breached" if self.breached else "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "breached": self.breached,
            "value": self.value,
            "threshold": self.threshold,
            "comparison": self.comparison,
            "previous_value": self.previous_value,
            "delta_pct": self.delta_pct,
            "top_dimension": self.top_dimension,
            "measure": self.measure_name,
            "rows": self.rows,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Metric execution (the SAME chain POST /metrics/{id}/query uses)
# ---------------------------------------------------------------------------


async def _run_metric(
    metric: MetricDefinition, mq: MetricQuery, claims: dict[str, Any]
) -> list[dict[str, Any]]:
    """Async: compile + plan + build connector + execute → list of row dicts.

    This is the watch-side mirror of ``POST /metrics/{id}/query`` minus caching,
    metering, and quota (a watch evaluation is an internal monitor, not a billed
    user query). RLS is threaded EXACTLY like the route: claims come from the
    verified token's policies.
    """
    from app.connectors import plan as planner_plan
    from app.connectors.planner import resolve_named_params
    from app.repos.provider import get_repo
    from app.routes.query import _build_connector_for_plan

    sql, named_params = compile_metric(metric, mq)
    effective_sql, effective_params = resolve_named_params(sql, named_params)

    physical_plan = planner_plan(
        sql=effective_sql,
        claims={"policies": (claims or {}).get("policies", [])},
        params=effective_params,
    )

    repo = get_repo()
    # Org attribution is only needed to scope a bound datastore; the demo metric
    # (datastore_id=None) tolerates a no-org caller. We resolve best-effort.
    org_id: str | None = None
    org_lookup_error: Exception | None = None
    try:
        org_id = (claims or {}).get("org_id")
    except Exception:  # noqa: BLE001
        org_id = None

    connector, _conn_kind, net_cleanup = await _build_connector_for_plan(
        physical_plan,
        metric.datastore_id or None,
        org_id,
        org_lookup_error,
        repo,
    )
    try:
        arrow_table = connector.execute(physical_plan)
    finally:
        try:
            net_cleanup()
        except Exception:  # noqa: BLE001 — cleanup never masks the result/error.
            pass

    return list(arrow_table.to_pylist())


# ---------------------------------------------------------------------------
# Scalar reduction + threshold evaluation
# ---------------------------------------------------------------------------


def _scalar_from_rows(rows: list[dict[str, Any]], measure: str) -> float | None:
    """Reduce result *rows* to a single scalar for the *measure* column.

    With no dimensions the query returns a single overall row → that measure
    value. With dimensions it returns one row per group → we SUM the measure
    across groups (the additive overall total), so a threshold on an
    overall-grouped metric and a dimensioned one agree on the total.
    """
    total = 0.0
    seen = False
    for row in rows:
        v = row.get(measure)
        if v is None:
            continue
        try:
            total += float(v)
            seen = True
        except (TypeError, ValueError):
            continue
    return total if seen else None


def _top_dimension(
    rows: list[dict[str, Any]], measure: str, dimensions: tuple[str, ...]
) -> dict[str, Any] | None:
    """The single dimension group with the LARGEST measure value (for context).

    Returns ``{"dimension": name, "label": value, "value": v}`` or ``None`` when
    the watch is not dimensioned / no rows.
    """
    if not dimensions or not rows:
        return None
    dim = dimensions[0]
    best: dict[str, Any] | None = None
    best_v = float("-inf")
    for row in rows:
        v = row.get(measure)
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > best_v:
            best_v = fv
            best = {"dimension": dim, "label": row.get(dim), "value": fv}
    return best


def _eval_threshold(value: float | None, threshold: dict[str, Any]) -> bool:
    """``op(value, threshold.value)`` — False when value is None or op unknown."""
    if value is None:
        return False
    op = _OPS.get(str(threshold.get("op")))
    if op is None:
        return False
    try:
        return bool(op(value, float(threshold["value"])))
    except (TypeError, ValueError, KeyError):
        return False


# ---------------------------------------------------------------------------
# evaluate_watch
# ---------------------------------------------------------------------------


def _build_query(
    watch: Watch, metric: MetricDefinition, *, time_grain: str | None
) -> MetricQuery:
    """A MetricQuery for the watch: its dimensions + (optional) time grain.

    Ordered by the measure descending so the first row is the top contributor
    (used for the top-dimension context in the explanation).
    """
    order_by: tuple[tuple[str, str], ...] = ()
    if watch.dimensions:
        order_by = ((metric.measure.name, "desc"),)
    return MetricQuery(
        metric_id=metric.id,
        dimensions=watch.dimensions,
        time_grain=time_grain,
        filters=(),
        order_by=order_by,
    )


async def evaluate_watch(
    watch: Watch, metric: MetricDefinition, claims: dict[str, Any]
) -> WatchResult:
    """Evaluate *watch* against *metric*: execute, reduce, decide breach.

    For a threshold rule: run the metric, reduce to the scalar measure total,
    and apply ``op(value, threshold.value)``.

    For a ``change_pct`` comparison rule: run the metric bucketed by
    ``time_grain``, take the most recent bucket as *current* and the prior bucket
    as *previous*, compute ``delta_pct = (current - previous) / |previous| * 100``
    and apply the op to that delta.

    RLS is threaded via *claims* (``claims['policies']``) — identical to the
    ``POST /metrics/{id}/query`` route. Never raises: a failure is captured on
    ``WatchResult.error`` so the route/tick can report it as state ``error``.
    """
    measure = metric.measure.name

    # ── change_pct comparison rule (takes precedence over a bare threshold) ──
    if watch.comparison and str(watch.comparison.get("kind")) == "change_pct":
        return await _evaluate_change_pct(watch, metric, claims)

    # ── level threshold rule ────────────────────────────────────────────────
    try:
        mq = _build_query(watch, metric, time_grain=watch.time_grain)
        rows = await _run_metric(metric, mq, claims)
    except Exception as exc:  # noqa: BLE001 — surface as a WatchResult error.
        logger.warning("evaluate_watch(%s): metric execution failed: %s", watch.id, exc)
        return WatchResult(
            breached=False, value=None, threshold=watch.threshold,
            measure_name=measure, error=str(exc),
        )

    value = _scalar_from_rows(rows, measure)
    top = _top_dimension(rows, measure, watch.dimensions)
    breached = bool(watch.threshold) and _eval_threshold(value, watch.threshold)
    return WatchResult(
        breached=breached,
        value=value,
        threshold=watch.threshold,
        top_dimension=top,
        rows=rows,
        measure_name=measure,
    )


async def _evaluate_change_pct(
    watch: Watch, metric: MetricDefinition, claims: dict[str, Any]
) -> WatchResult:
    """Evaluate a ``change_pct`` rule: latest bucket vs the prior bucket."""
    measure = metric.measure.name
    grain = watch.time_grain or (
        metric.time_dimension.default_grain if metric.time_dimension else None
    )
    try:
        mq = _build_query(watch, metric, time_grain=grain)
        rows = await _run_metric(metric, mq, claims)
    except Exception as exc:  # noqa: BLE001
        logger.warning("evaluate_watch(%s): change_pct execution failed: %s", watch.id, exc)
        return WatchResult(
            breached=False, value=None, comparison=watch.comparison,
            measure_name=measure, error=str(exc),
        )

    # Bucket the measure by the time alias and order chronologically.
    time_alias = None
    if grain and metric.time_dimension is not None:
        time_alias = f"{metric.time_dimension.column}_{grain}"

    series: list[tuple[Any, float]] = []
    if time_alias:
        for row in rows:
            v = row.get(measure)
            try:
                series.append((row.get(time_alias), float(v)))
            except (TypeError, ValueError):
                continue
        series.sort(key=lambda t: (t[0] is None, t[0]))

    if len(series) >= 2:
        previous = series[-2][1]
        current = series[-1][1]
    else:
        # No usable previous period → current == previous → 0% delta (no breach).
        current = _scalar_from_rows(rows, measure)
        previous = current

    delta_pct: float | None = None
    if current is not None and previous is not None:
        if previous == 0:
            delta_pct = 0.0 if current == 0 else 100.0
        else:
            delta_pct = (current - previous) / abs(previous) * 100.0

    breached = False
    if watch.comparison and delta_pct is not None:
        breached = _eval_threshold(delta_pct, watch.comparison)

    top = _top_dimension(rows, measure, watch.dimensions)
    return WatchResult(
        breached=breached,
        value=current,
        previous_value=previous,
        delta_pct=delta_pct,
        comparison=watch.comparison,
        top_dimension=top,
        rows=rows,
        measure_name=measure,
    )


# ---------------------------------------------------------------------------
# explain_breach
# ---------------------------------------------------------------------------


def _fmt_num(value: float | None) -> str:
    """Compact numeric format (trim trailing zeros)."""
    if value is None:
        return "n/a"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _deterministic_explanation(watch: Watch, result: WatchResult) -> str:
    """Template explanation used under NullProvider — no network, fully stable.

    Includes the measure value, the threshold/rule, the delta (for change rules)
    and the top contributing dimension when the watch is dimensioned.
    """
    parts: list[str] = [f"{watch.name}:"]
    val = _fmt_num(result.value)

    if result.comparison and result.delta_pct is not None:
        direction = "down" if result.delta_pct < 0 else "up"
        parts.append(
            f"{result.measure_name} is {direction} "
            f"{_fmt_num(abs(result.delta_pct))}% to {val} "
            f"(from {_fmt_num(result.previous_value)})"
        )
        op = result.comparison.get("op")
        bound = result.comparison.get("value")
        parts.append(f"— change {op} {_fmt_num(bound)}% threshold breached.")
    elif result.threshold:
        op = result.threshold.get("op")
        bound = result.threshold.get("value")
        parts.append(
            f"{result.measure_name} is {val}, {op} the {_fmt_num(bound)} threshold."
        )
    else:
        parts.append(f"{result.measure_name} is {val}.")

    if result.top_dimension:
        td = result.top_dimension
        parts.append(
            f"Largest contributor: {td.get('dimension')}="
            f"{td.get('label')} ({_fmt_num(td.get('value'))})."
        )
    return " ".join(parts)


async def explain_breach(
    watch: Watch, result: WatchResult, *, provider: Any = None
) -> str:
    """Compose a concise human explanation of a breach.

    Uses the AI *provider* (defaults to ``app.ai.provider.get_provider()``). The
    deterministic template is built first and handed to the provider as grounding
    context; under :class:`~app.ai.provider.NullProvider` (or any provider
    failure) the deterministic template IS the explanation — zero network calls.
    """
    base = _deterministic_explanation(watch, result)

    if provider is None:
        from app.ai.provider import get_provider

        provider = get_provider()

    from app.ai.provider import NullProvider

    if isinstance(provider, NullProvider):
        # Deterministic path — no network, stable output for tests.
        return base

    system = (
        "You explain why a monitored business metric breached its threshold. "
        "Write ONE concise sentence for a Slack alert. Be specific and factual; "
        "do not invent numbers beyond those provided."
    )
    prompt = (
        f"Metric: {result.measure_name}\n"
        f"Facts: {base}\n"
        f"Rows: {result.rows[:10]}\n"
        "Write the alert sentence:"
    )
    try:
        text = provider.complete(prompt, system=system)
        return (text or "").strip() or base
    except Exception as exc:  # noqa: BLE001 — degrade to the deterministic template.
        logger.warning("explain_breach(%s): provider failed, using template: %s", watch.id, exc)
        return base


# ---------------------------------------------------------------------------
# fire_watch — dispatch via app.chat.notify (mirror runtime._fire_flow_alert)
# ---------------------------------------------------------------------------


async def fire_watch(watch: Watch, result: WatchResult, explanation: str) -> int:
    """Dispatch a breach alert via the notify channels. Best-effort; never raises.

    Builds an alert event and sends it through ``app.chat.notify.channels_for``
    + ``notify_flow_run`` — the SAME dispatch ``runtime._fire_flow_alert`` uses.
    The watch's ``channel_config`` (slack_webhook / slack_channel / whatsapp_to)
    selects the channels; with nothing configured the result is 0 (a no-op).

    Returns the number of channels the message was delivered to.
    """
    try:
        from app.chat import notify

        event = {
            "kind": "watch",
            "name": watch.name,
            "state": "breached",
            "metric_id": watch.metric_id,
            "error": explanation,  # surfaces the explanation in the alert body
        }
        channels = notify.channels_for(watch.channel_config or {})
        return notify.notify_flow_run(event, channels=channels)
    except Exception as exc:  # noqa: BLE001 — alerts are strictly best-effort.
        logger.warning("fire_watch(%s): dispatch failed: %s", watch.id, exc)
        return 0


# ---------------------------------------------------------------------------
# run_watch — the full evaluate -> explain -> fire pass
# ---------------------------------------------------------------------------


async def run_watch(
    watch: Watch,
    metric: MetricDefinition,
    claims: dict[str, Any],
    *,
    provider: Any = None,
) -> dict[str, Any]:
    """Evaluate *watch*; on breach, explain + fire. Return a summary dict.

    Summary shape::

        {
          "breached": bool,
          "value": float | None,
          "state": "ok" | "breached" | "error",
          "explanation": str | None,   # present only on breach
          "sent": int,                 # channels the alert reached (0 if none)
          "result": { ...WatchResult.to_dict() }
        }
    """
    result = await evaluate_watch(watch, metric, claims)

    summary: dict[str, Any] = {
        "breached": result.breached,
        "value": result.value,
        "state": result.state,
        "sent": 0,
        "result": result.to_dict(),
    }
    if result.error is not None:
        summary["error"] = result.error

    if result.breached:
        explanation = await explain_breach(watch, result, provider=provider)
        summary["explanation"] = explanation
        summary["sent"] = await fire_watch(watch, result, explanation)

    return summary
