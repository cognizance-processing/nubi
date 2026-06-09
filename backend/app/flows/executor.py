"""Task executor for the Flows engine.

Public API
----------
TaskContext
    Dataclass carrying flow-level context needed by each task handler.
    Fields: ``flow_params`` (flow param values), ``inputs`` (upstream task
    results keyed by task_key), ``now`` (the injected clock datetime),
    ``secrets`` (resolved org secrets keyed by name, plaintext values),
    ``org_id`` (org owning this flow run — used by connector resolution),
    ``preview_mode`` (True when running a preview/interactive cell),
    ``preview_limit`` (row cap for preview queries, default 500).

execute_task(task, ctx, claims) -> dict
    Resolve ``{{ ... }}`` template expressions in the task's config,
    dispatch to the registered kind handler, enforce ``timeout_s``, and
    return a result dict.

    Preview mode behaviour
    ----------------------
    When ``ctx.preview_mode=True``:
    - ``query`` tasks automatically receive a ``preview_limit`` cap so the
      planner injects ``LIMIT <n>`` before plan execution.
    - No flow_run is persisted (the caller is responsible for this).
    - Results are identical in shape to durable results; callers read
      ``result["rows"]`` as usual.

    Python→SQL bridge
    -----------------
    When a ``query`` task runs and upstream ``ctx.inputs`` contain entries
    with a ``rows`` key (i.e. a Python cell produced row data), those rows
    are automatically registered as in-memory DuckDB tables named by the
    upstream cell key before the SQL is executed.  This mirrors the resolved
    decision in the notebook system blueprint (OQ-3 option B).

Result dict shape
-----------------
``{"state": "success"|"failed"|"timed_out", "result": dict|None,
    "error": str|None, "logs": list[str]}``

The ``logs`` field is a list of captured stdout/log lines from the task
execution.  For non-python tasks it may be empty; for python tasks it
contains every line printed by the subprocess (excluding the
``__FLOW_RESULT__:`` sentinel line).

Secret redaction: before any outcome is returned, every occurrence of a
resolved secret VALUE (length >= 4) in ``error`` and ``logs`` is replaced
with ``'•••'`` (see ``redact_secret_values``), so plaintext secrets never
surface in task_run errors, captured logs, or preview error responses.

Templating
----------
Strings inside ``config`` values may contain ``{{ params.x }}``,
``{{ inputs.task_key.field }}``, or ``{{ secrets.NAME }}`` expressions.
Resolution is shallow and non-recursive.  Unknown references resolve to the
empty string so that optional template params don't cause hard failures.

``secrets.NAME`` resolves the plaintext value of the named org secret from
``ctx.secrets`` (a ``dict[str, str]`` populated by the runtime before
``execute_task`` is called via ``secret_store.resolve_all(org_id)``).

Timeout
-------
``timeout_s`` is honoured via ``concurrent.futures.ThreadPoolExecutor``
with ``result(timeout=timeout_s)``.  Zero means no timeout.  A timed-out
task returns ``state='timed_out'`` (distinct from ``'failed'``).

Error handling
--------------
Any exception raised by a handler is caught; the task is marked
``"failed"`` and the exception message is stored as ``"error"``.
This mirrors the ``execute_job`` broad-except pattern in
``app/jobs/executor.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ---------------------------------------------------------------------------
# TaskContext
# ---------------------------------------------------------------------------


@dataclass
class TaskContext:
    """Context passed to every task handler.

    Attributes
    ----------
    flow_params:
        Resolved flow-level parameter values keyed by parameter name.
        These are the merged values of the flow spec defaults + caller-
        supplied overrides at run time.
    inputs:
        Upstream task results keyed by task_key.  Only tasks whose
        task_run reached ``'success'`` state will be present.
    now:
        The injected clock datetime (UTC, tz-aware).  Never call
        ``datetime.now()`` inside handlers — use this instead so the
        engine stays deterministic in tests.
    secrets:
        Resolved org secret values keyed by secret name (plaintext strings).
        Populated by the runtime via ``secret_store.resolve_all(org_id)``
        before ``execute_task`` is called.  Handlers may read credentials
        via ``ctx.secrets[name]`` or resolve ``{{ secrets.NAME }}`` in
        their config strings.  Never log or expose these values.
    item:
        For map body task_runs: the current item dict being processed.
        Template expressions ``{{ item.field }}`` resolve against this dict.
        ``None`` for non-map tasks (regular task execution).
    org_id:
        The organisation that owns this flow run.  Populated by the runtime
        from ``flow_run.org_id``.  Used by handlers that resolve BYO-warehouse
        connectors via the datastore registry (P1-D blueprint seam).
    preview_mode:
        ``True`` when executing a single cell interactively (preview path).
        ``query`` tasks automatically receive ``LIMIT <preview_limit>``; no
        flow_run row is persisted.
    preview_limit:
        Row cap applied to ``query`` tasks in preview mode (default 500).
        Ignored when ``preview_mode=False``.
    """

    flow_params: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ))
    secrets: dict[str, str] = field(default_factory=dict)
    item: dict[str, Any] | None = field(default=None)
    org_id: str | None = field(default=None)
    preview_mode: bool = field(default=False)
    preview_limit: int = field(default=500)
    # ── Environment / incremental materialization (additive, all defaulted) ──
    # env:       active environment for this flow run ("dev"/"prod"/custom).
    #            Namespaces materialized/incremental targets so dev/prod never
    #            clobber.  Populated by the runtime from flow_run["env"].
    # watermark: stored incremental watermark (ISO string) for a materialize
    #            task, read by the runtime from flow_watermarks before execution.
    # flow:      the flow dict (for runtime_config.materialize_base_uri lookup).
    env: str = field(default="prod")
    watermark: str | None = field(default=None)
    flow: dict[str, Any] | None = field(default=None)


# ---------------------------------------------------------------------------
# Template resolution
# ---------------------------------------------------------------------------

#: Matches ``{{ some.dotted.path }}`` (with optional whitespace).
_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _resolve_value(expr: str, ctx: TaskContext) -> str:
    """Resolve a single template expression like ``params.x`` or ``inputs.k.f``."""
    parts = expr.split(".")
    if not parts:
        return ""

    namespace = parts[0]
    rest = parts[1:]

    if namespace == "params":
        if not rest:
            return ""
        val = ctx.flow_params.get(rest[0], "")
        # Navigate deeper if needed (rare)
        for key in rest[1:]:
            if isinstance(val, dict):
                val = val.get(key, "")
            else:
                return str(val)
        return str(val) if val is not None else ""

    if namespace == "inputs":
        if not rest:
            return ""
        task_key = rest[0]
        val = ctx.inputs.get(task_key, {})
        for key in rest[1:]:
            if isinstance(val, dict):
                val = val.get(key, "")
            else:
                return str(val)
        return str(val) if val is not None else ""

    if namespace == "secrets":
        if not rest:
            return ""
        secret_name = rest[0]
        # Only the first segment is used; deeper navigation is not supported
        # for secrets (values are always plain strings, not nested dicts).
        val = ctx.secrets.get(secret_name, "")
        return str(val) if val is not None else ""

    if namespace == "item":
        # Map body task: resolve against the current item dict in ctx.item.
        item_val = ctx.item
        if item_val is None:
            return ""
        if not rest:
            return str(item_val) if item_val is not None else ""
        val = item_val
        for key in rest:
            if isinstance(val, dict):
                val = val.get(key, "")
            else:
                return str(val) if val is not None else ""
        return str(val) if val is not None else ""

    # Unknown namespace → empty string (soft failure).
    return ""


def _resolve_string(s: str, ctx: TaskContext) -> str:
    """Replace all ``{{ ... }}`` expressions in *s* with resolved values."""
    def _sub(match: re.Match) -> str:  # type: ignore[type-arg]
        return _resolve_value(match.group(1), ctx)

    return _TEMPLATE_RE.sub(_sub, s)


def _resolve_config(config: dict[str, Any], ctx: TaskContext) -> dict[str, Any]:
    """Return a shallow copy of *config* with all string values template-resolved.

    Only top-level string values are resolved.  Nested dicts/lists are
    recursively processed.
    """
    resolved: dict[str, Any] = {}
    for k, v in config.items():
        resolved[k] = _resolve_any(v, ctx)
    return resolved


def _resolve_any(v: Any, ctx: TaskContext) -> Any:
    """Recursively resolve templates in *v*."""
    if isinstance(v, str):
        return _resolve_string(v, ctx)
    if isinstance(v, dict):
        return {kk: _resolve_any(vv, ctx) for kk, vv in v.items()}
    if isinstance(v, list):
        return [_resolve_any(item, ctx) for item in v]
    return v


def _resolve_str(expr: str, ctx: TaskContext) -> str:
    """Alias for ``_resolve_string`` used by the branch/map handlers.

    Exported under this name so handlers can import it without knowing the
    internal function name.  Kept as a thin wrapper to avoid duplication.
    """
    return _resolve_string(expr, ctx)


# ---------------------------------------------------------------------------
# Secret-value redaction
# ---------------------------------------------------------------------------

#: Mask substituted for secret values in errors / captured logs.
SECRET_MASK = "•••"

#: Secret values shorter than this are not redacted (too noisy — e.g. "1",
#: "ok" would mangle unrelated text far more often than they protect anything).
_MIN_REDACT_LEN = 4


def redact_secret_values(text: str | None, secrets: dict[str, str]) -> str | None:
    """Replace every occurrence of a resolved secret value in *text* with '•••'.

    Cheap plain-string replacement over the resolved secret VALUES (not the
    names — ``{{ secrets.NAME }}`` references and secret names stay readable).
    Values shorter than 4 characters are skipped.  ``None``/empty input is
    returned unchanged.
    """
    if not text or not secrets:
        return text
    for val in secrets.values():
        if isinstance(val, str) and len(val) >= _MIN_REDACT_LEN and val in text:
            text = text.replace(val, SECRET_MASK)
    return text


def _redact_outcome(outcome: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    """Redact secret values from the ``error`` and ``logs`` of an outcome dict.

    Applied to every ``execute_task`` return path so plaintext secret values
    can never surface in task_run errors, captured stdout/log lines, or
    cell-preview error responses — regardless of which runtime (durable,
    preview, run-cell) invoked the executor.  Results are NOT redacted: a
    flow author who can already reference ``{{ secrets.NAME }}`` can always
    SELECT the value into the result by design.
    """
    if not secrets:
        return outcome
    if outcome.get("error"):
        outcome["error"] = redact_secret_values(outcome["error"], secrets)
    logs = outcome.get("logs")
    if logs:
        outcome["logs"] = [redact_secret_values(line, secrets) or "" for line in logs]
    return outcome


# ---------------------------------------------------------------------------
# Python→SQL bridge helpers
# ---------------------------------------------------------------------------


def _collect_bridge_tables(ctx: TaskContext) -> dict[str, Any]:
    """Collect upstream inputs that carry row data for DuckDB bridge registration.

    Returns a dict mapping cell_key → pyarrow.Table for every upstream input
    that has a non-empty ``rows`` list.  Returns ``{}`` when pyarrow is not
    available or no inputs have rows.

    This implements the resolved OQ-3 decision: a durable/preview SQL cell
    that reads ``SELECT * FROM <cell_key>`` will find the upstream Python
    cell's output registered as an in-memory DuckDB table.
    """
    tables: dict[str, Any] = {}
    try:
        import pyarrow as pa  # noqa: PLC0415
    except ImportError:
        return tables

    for cell_key, result in ctx.inputs.items():
        if not isinstance(result, dict):
            continue
        rows = result.get("rows")
        if not rows:
            continue
        try:
            tables[cell_key] = pa.Table.from_pylist(rows)
        except Exception:  # noqa: BLE001
            # Unparseable rows — skip this cell silently.
            pass
    return tables


def _execute_query_with_bridge(
    config: dict[str, Any],
    ctx: TaskContext,
    claims: dict[str, Any],
    bridge_tables: dict[str, Any],
) -> dict[str, Any]:
    """Execute a ``query`` task with Python→SQL bridge table registration.

    Creates a fresh ``DuckDBConnector``, registers all *bridge_tables*
    (upstream Python cell outputs) as named in-memory tables, then runs the
    planner + SQL.  Applies ``LIMIT <preview_limit>`` via the planner when
    ``ctx.preview_mode`` is ``True``.

    This function lives in executor.py (owned) rather than registry.py so
    that the Python→SQL bridge is implemented without modifying the
    pre-existing handler.

    Parameters
    ----------
    config:
        Resolved task config dict (after template substitution).
    ctx:
        Execution context carrying ``preview_mode`` / ``preview_limit``.
    claims:
        Caller auth claims (RLS injection by the planner).
    bridge_tables:
        Dict mapping cell_key → ``pyarrow.Table`` to pre-register.

    Returns
    -------
    dict
        ``{rows, row_count, columns}``
    """
    from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415
    from app.connectors.planner import plan, resolve_named_params  # noqa: PLC0415
    from app.errors import AppError  # noqa: PLC0415
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    query_id: str | None = config.get("query_id")
    sql: str | None = config.get("sql")
    named_params: dict[str, Any] = config.get("named_params") or {}

    resolved_sql: str
    positional_params: list[Any] = []

    if query_id is not None:
        registry = get_query_registry()
        rq = registry.get(query_id)
        if rq is None:
            raise AppError(
                "query_not_found",
                f"No registered query with id {query_id!r}.",
                404,
            )
        resolved_sql = rq.sql
        if named_params and rq.params:
            resolved: dict[str, Any] = {}
            for p in rq.params:
                if p.name in named_params:
                    resolved[p.name] = named_params[p.name]
                elif p.default is not None:
                    resolved[p.name] = p.default
                elif p.required:
                    raise AppError(
                        "missing_required_param",
                        f"Required param {p.name!r} was not supplied.",
                        400,
                    )
            resolved_sql, positional_params = resolve_named_params(resolved_sql, resolved)
    elif sql is not None:
        resolved_sql = sql
    else:
        raise AppError(
            "invalid_task_config",
            "query task requires 'query_id' or 'sql' in config.",
            400,
        )

    # Apply preview row limit when running in preview mode.
    limit: int | None = ctx.preview_limit if ctx.preview_mode else None

    physical_plan = plan(
        resolved_sql,
        claims=claims,
        params=positional_params,
        limit=limit,
    )

    connector = DuckDBConnector()

    # Seed the demo table (mirrors _handle_query / _tool_run_query).
    try:
        import pyarrow as pa  # noqa: PLC0415

        demo = pa.table(
            {
                "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
                "name": pa.array(["alpha", "beta", "gamma", "delta", "epsilon"]),
                "active": pa.array([True, True, False, True, False]),
                "value": pa.array([10.0, 20.0, 30.0, 40.0, 50.0], type=pa.float64()),
            }
        )
        connector.register({"demo": demo})
    except Exception:  # noqa: BLE001
        pass

    # Register bridge tables (Python cell outputs) as in-memory DuckDB tables.
    if bridge_tables:
        connector.register(bridge_tables)

    arrow_table = connector.execute(physical_plan)
    columns = arrow_table.schema.names
    rows = arrow_table.to_pylist()
    return {"rows": rows, "row_count": len(rows), "columns": columns}


# ---------------------------------------------------------------------------
# execute_task
# ---------------------------------------------------------------------------


def execute_task(
    task: dict[str, Any],
    ctx: TaskContext,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single task and return a result descriptor.

    Parameters
    ----------
    task:
        A TaskRun-like dict (or a task spec dict with ``kind``, ``config``,
        ``timeout_s``, etc.).  The engine passes the full task_run dict
        which also contains spec fields copied in by the runtime.
    ctx:
        Execution context (flow params, upstream inputs, clock).
    claims:
        Caller's auth claims — passed through to all handlers for RLS.

    Returns
    -------
    dict
        ``{"state": "success"|"failed"|"timed_out", "result": dict|None,
           "error": str|None, "logs": list[str]}``
    """
    from app.flows.registry import get_task_kind_registry  # noqa: PLC0415

    kind: str = task.get("kind", "")
    raw_config: dict[str, Any] = task.get("config") or {}
    timeout_s: int = int(task.get("timeout_s", 0) or 0)

    # ── run_when gate (cells-not-kinds) ───────────────────────────────────────
    # A cell may carry a ``config.run_when`` boolean expression over
    # inputs/params/secrets.  Read it from the RAW config (NOT _resolve_config —
    # template str-coercion would break ``==`` comparisons) BEFORE handler
    # dispatch.  When it evaluates False the cell is SKIPPED.  A malformed gate
    # raises ValueError, which propagates to the broad-except below ⇒ 'failed'
    # (the gate fails loudly, never silently skips).
    run_when_expr = raw_config.get("run_when")
    if run_when_expr is not None and str(run_when_expr).strip():
        from app.flows.run_when import evaluate_run_when  # noqa: PLC0415

        try:
            gate_passes = evaluate_run_when(run_when_expr, ctx)
        except Exception as exc:  # noqa: BLE001 — malformed gate fails loudly
            return _redact_outcome({
                "state": "failed",
                "result": None,
                "error": f"run_when evaluation failed: {exc}",
                "logs": [],
            }, ctx.secrets)
        if not gate_passes:
            return {"state": "skipped", "result": None, "error": None, "logs": []}

    # ── Map child item injection ──────────────────────────────────────────────
    # If the task config contains ``__item__`` (set by _expand_map_children),
    # augment the TaskContext with the item value so ``{{ item.field }}``
    # template expressions resolve correctly.  We build a new ctx with item set
    # rather than mutating the caller's ctx.
    if raw_config.get("__item__") is not None and ctx.item is None:
        from dataclasses import replace as _dc_replace  # noqa: PLC0415
        ctx = _dc_replace(ctx, item=raw_config["__item__"])

    # For python tasks with a map item: prepend ``item = <value>`` to the code
    # so user snippets can access ``item.field`` as a dict key.  The item var
    # name is read from ``__item_var__`` (default: ``"item"``).
    if kind == "python" and raw_config.get("__item__") is not None:
        import json as _json  # noqa: PLC0415
        item_var_name: str = str(raw_config.get("__item_var__") or "item")
        try:
            item_json_str = _json.dumps(raw_config["__item__"])
        except (TypeError, ValueError):
            item_json_str = "{}"
        item_preamble = (
            f"import json as __item_json_mod__\n"
            f"{item_var_name} = __item_json_mod__.loads({item_json_str!r})\n"
        )
        existing_code = raw_config.get("code", "")
        raw_config = dict(raw_config)
        raw_config["code"] = item_preamble + existing_code

    # Resolve templates in config.
    # Exception: map tasks require native (non-string) resolution for item_expr
    # so that the list value is preserved.  Skip resolving item_expr here;
    # the map handler uses _resolve_native to obtain the Python list directly.
    if kind == "map":
        resolved_config = {
            k: (_resolve_any(v, ctx) if k not in ("item_expr", "body") else v)
            for k, v in raw_config.items()
        }
    else:
        resolved_config = _resolve_config(raw_config, ctx)

    # Add timeout hint to config so python handler can pick it up.
    if timeout_s > 0:
        resolved_config.setdefault("timeout_s", timeout_s)

    # Log collector — handlers may populate this via resolved_config["_log_collector"]
    # if they support it (the python handler does via stdout capture).
    log_lines: list[str] = []

    # ── Compute metering ──────────────────────────────────────────────────────
    # Flow task execution consumes compute on our nodes — the same COGS line as
    # interactive query/kernel compute (see app.ee.billing.tiers compute_units).
    # We meter wall-clock here so flow runs count toward the org's compute-unit
    # quota (and overage). Skipped in preview mode (no real run, no bill).
    import time as _time  # noqa: PLC0415
    _t0 = _time.perf_counter()

    def _meter(result_obj: Any = None) -> None:
        if ctx.preview_mode:
            return
        try:
            from app.compute.metering import record_kernel_usage_safe  # noqa: PLC0415

            elapsed_ms = int((_time.perf_counter() - _t0) * 1000)
            out_bytes = 0
            if isinstance(result_obj, dict):
                rows = result_obj.get("rows")
                if isinstance(rows, list):
                    out_bytes = len(rows) * 64  # rough egress estimate
            record_kernel_usage_safe(
                user_id=str(claims.get("sub") or claims.get("user_id") or "flow"),
                tier="flow_kernel",
                elapsed_ms=elapsed_ms,
                output_bytes=out_bytes,
                org_id=ctx.org_id,
            )
        except Exception:  # noqa: BLE001 — metering must never break a flow run
            pass

    try:
        # ── Python→SQL bridge + preview dispatch for query tasks ─────────────
        # When upstream inputs carry ``rows`` data (Python cell outputs) or
        # preview_mode is active, we bypass the standard registry handler for
        # ``query`` tasks and use our bridge-aware executor instead.
        # This keeps all bridge logic inside executor.py (our owned file) and
        # avoids modifying registry.py.
        if kind == "query":
            bridge_tables = _collect_bridge_tables(ctx)
            if bridge_tables or ctx.preview_mode:
                # Use the inline bridge handler.
                def _bridge_handler(
                    cfg: dict[str, Any],
                    _ctx: TaskContext,
                    _claims: dict[str, Any],
                ) -> dict[str, Any]:
                    return _execute_query_with_bridge(cfg, _ctx, _claims, bridge_tables)

                handler: Any = _bridge_handler
            else:
                registry = get_task_kind_registry()
                handler = registry.get(kind)
        else:
            registry = get_task_kind_registry()
            handler = registry.get(kind)

        if timeout_s > 0:
            import concurrent.futures  # noqa: PLC0415

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run_handler_with_logs, handler, resolved_config, ctx, claims, log_lines)
                try:
                    result, captured_logs = future.result(timeout=timeout_s)
                    log_lines.extend(captured_logs)
                except concurrent.futures.TimeoutError:
                    _meter()  # the task ran (and consumed compute) until the timeout
                    return _redact_outcome({
                        "state": "timed_out",
                        "result": None,
                        "error": f"Task timed out after {timeout_s}s.",
                        "logs": log_lines,
                    }, ctx.secrets)
        else:
            result, captured_logs = _run_handler_with_logs(handler, resolved_config, ctx, claims, log_lines)
            log_lines.extend(captured_logs)

        # Ensure result is a dict.
        if not isinstance(result, dict):
            result = {"value": result}

        _meter(result)
        return _redact_outcome(
            {"state": "success", "result": result, "error": None, "logs": log_lines},
            ctx.secrets,
        )

    except Exception as exc:  # noqa: BLE001 — broad catch mirrors execute_job
        import traceback  # noqa: PLC0415
        tb = traceback.format_exc()
        _meter()  # a failed handler still consumed compute before raising
        return _redact_outcome({
            "state": "failed",
            "result": None,
            "error": str(exc),
            "logs": log_lines + [tb] if tb != "NoneType: None\n" else log_lines,
        }, ctx.secrets)


def _run_handler_with_logs(
    handler: Any,
    config: dict[str, Any],
    ctx: TaskContext,
    claims: dict[str, Any],
    _existing_logs: list[str],
) -> tuple[Any, list[str]]:
    """Run a handler, capturing stdout log lines.

    Returns ``(result, captured_log_lines)``.

    For the python handler, stdout is already captured in the subprocess;
    those lines are extracted from the result by the caller.  For other
    handlers we capture Python-level logging output via a StringIO handler.
    """
    import io  # noqa: PLC0415
    import logging  # noqa: PLC0415

    captured: list[str] = []
    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    log_handler.setLevel(logging.DEBUG)
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)

    try:
        result = handler(config, ctx, claims)
    finally:
        root_logger.removeHandler(log_handler)
        log_output = log_stream.getvalue()
        if log_output.strip():
            captured.extend(log_output.splitlines())

    # For python handler: extract stdout lines from result metadata.
    # The python handler attaches "_stdout_lines" when available.
    if isinstance(result, dict) and "_stdout_lines" in result:
        captured = list(result.pop("_stdout_lines")) + captured

    return result, captured
