"""Task executor for the Flows engine.

Public API
----------
TaskContext
    Dataclass carrying flow-level context needed by each task handler.
    Fields: ``flow_params`` (flow param values), ``inputs`` (upstream task
    results keyed by task_key), ``now`` (the injected clock datetime).

execute_task(task, ctx, claims) -> dict
    Resolve ``{{ ... }}`` template expressions in the task's config,
    dispatch to the registered kind handler, enforce ``timeout_s``, and
    return a result dict.

Result dict shape
-----------------
``{"state": "success"|"failed"|"timed_out", "result": dict|None,
    "error": str|None, "logs": list[str]}``

The ``logs`` field is a list of captured stdout/log lines from the task
execution.  For non-python tasks it may be empty; for python tasks it
contains every line printed by the subprocess (excluding the
``__FLOW_RESULT__:`` sentinel line).

Templating
----------
Strings inside ``config`` values may contain ``{{ params.x }}`` or
``{{ inputs.task_key.field }}`` expressions.  Resolution is shallow and
non-recursive.  Unknown references resolve to the empty string so that
optional template params don't cause hard failures.

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
    """

    flow_params: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    now: datetime = field(default_factory=lambda: __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ))


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

    # Resolve templates in config.
    resolved_config = _resolve_config(raw_config, ctx)

    # Add timeout hint to config so python handler can pick it up.
    if timeout_s > 0:
        resolved_config.setdefault("timeout_s", timeout_s)

    # Log collector — handlers may populate this via resolved_config["_log_collector"]
    # if they support it (the python handler does via stdout capture).
    log_lines: list[str] = []

    try:
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
                    return {
                        "state": "timed_out",
                        "result": None,
                        "error": f"Task timed out after {timeout_s}s.",
                        "logs": log_lines,
                    }
        else:
            result, captured_logs = _run_handler_with_logs(handler, resolved_config, ctx, claims, log_lines)
            log_lines.extend(captured_logs)

        # Ensure result is a dict.
        if not isinstance(result, dict):
            result = {"value": result}

        return {"state": "success", "result": result, "error": None, "logs": log_lines}

    except Exception as exc:  # noqa: BLE001 — broad catch mirrors execute_job
        import traceback  # noqa: PLC0415
        tb = traceback.format_exc()
        return {
            "state": "failed",
            "result": None,
            "error": str(exc),
            "logs": log_lines + [tb] if tb != "NoneType: None\n" else log_lines,
        }


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
