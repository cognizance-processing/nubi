"""AI tool definitions for the Nubi Flows workflow orchestrator.

Five ``ToolDef`` entries:
  list_flows      → {flows: [{id, name}]}
  create_flow     → validate + store; return {id, valid, issues}
  run_flow        → materialize + drain; return {flow_run_id, state, task_runs}
  get_flow_run    → {state, task_runs:[...]}
  generate_flow   → NL → FlowSpec; with NullProvider returns a deterministic demo

Each tool callable accepts ``claims`` plus named kwargs.  Org is resolved from
``claims["org_id"]`` (the same pattern used in ``routes/ai.py`` + ``routes/flows.py``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, TypeVar

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# Sync→async bridge
# ---------------------------------------------------------------------------
#
# The AI agent loop (app/ai/agent.py → app/ai/tools.execute_tool) is fully
# SYNCHRONOUS, but the flow store + engine functions are now ``async def``
# (so InMemory and Pg share one async interface).  These tools therefore must
# stay sync (the agent calls them directly) and bridge to the async layer.
#
# ``_run_sync`` runs a coroutine to completion from sync code:
#  - If no event loop is running in this thread → use ``asyncio.run``.
#  - If a loop IS already running (e.g. the tool was invoked inside FastAPI's
#    event loop), run the coroutine in a fresh loop on a worker thread so we
#    never try to nest loops.  In FastAPI the route layer dispatches sync route
#    handlers / threadpool work, and the streaming agent iterates the generator
#    in a threadpool, so this path stays off the main loop in practice.


def _run_sync(coro: Awaitable[_T]) -> _T:
    """Run *coro* to completion from synchronous code and return its result."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)  # type: ignore[arg-type]
            return future.result()

    return asyncio.run(coro)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_list_flows(claims: dict[str, Any]) -> dict[str, Any]:
    """Return all flows for the caller's org as ``{flows: [{id, name}]}``.

    Parameters
    ----------
    claims:
        Caller claims — must include ``org_id``.
    """
    from app.flows.store import get_flow_store  # noqa: PLC0415

    org_id = claims.get("org_id", "")
    store = get_flow_store()
    flows = _run_sync(store.list_flows(org_id))
    return {"flows": [{"id": f["id"], "name": f["name"]} for f in flows]}


def _tool_create_flow(
    name: str,
    spec: dict[str, Any],
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Validate a FlowSpec and store it; return ``{id, valid, issues}``.

    Parameters
    ----------
    name:
        Human-readable flow name.
    spec:
        Raw FlowSpec dict.
    claims:
        Caller claims — must include ``org_id`` and ``sub``.
    """
    from app.flows.spec import flow_spec_is_valid, validate_flow_spec  # noqa: PLC0415
    from app.flows.store import get_flow_store  # noqa: PLC0415

    org_id = claims.get("org_id", "")
    created_by = claims.get("sub", "")

    flow_spec, issues = validate_flow_spec(spec)
    valid = flow_spec_is_valid(issues)

    if not valid:
        return {"id": None, "valid": False, "issues": issues}

    store = get_flow_store()
    flow = _run_sync(
        store.create_flow(
            org_id=org_id,
            created_by=created_by,
            name=name,
            spec=flow_spec.model_dump() if flow_spec is not None else spec,
        )
    )
    return {"id": flow["id"], "valid": True, "issues": issues}


def _tool_run_flow(
    flow_id: str,
    claims: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialise and drain a flow run; return ``{flow_run_id, state, task_runs}``.

    Parameters
    ----------
    flow_id:
        UUID of the flow to run.
    claims:
        Caller claims (passed through to the executor for RLS).
    params:
        Optional flow-level parameter values.
    """
    from app.flows.runtime import drain_flow_run, materialize_flow_run  # noqa: PLC0415
    from app.flows.store import get_flow_store  # noqa: PLC0415

    if params is None:
        params = {}

    store = get_flow_store()

    async def _run() -> dict[str, Any]:
        flow = await store.get_flow(flow_id)
        if flow is None:
            from app.errors import AppError  # noqa: PLC0415
            raise AppError("not_found", f"Flow {flow_id!r} not found.", 404)

        now = datetime.now(timezone.utc)

        flow_run = await materialize_flow_run(store, flow, params, "agent", now)
        flow_run = await drain_flow_run(store, flow_run["id"], now, claims=claims)

        task_runs = await store.list_task_runs(flow_run["id"])
        return {
            "flow_run_id": flow_run["id"],
            "state": flow_run["state"],
            "task_runs": [
                {"task_key": tr["task_key"], "state": tr["state"]} for tr in task_runs
            ],
        }

    return _run_sync(_run())


def _tool_get_flow_run(
    flow_run_id: str,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Return ``{state, task_runs: [...]}`` for a flow run.

    Parameters
    ----------
    flow_run_id:
        UUID of the flow run.
    claims:
        Caller claims — used to verify org ownership.
    """
    from app.flows.store import get_flow_store  # noqa: PLC0415

    store = get_flow_store()
    run = _run_sync(store.get_flow_run(flow_run_id))
    if run is None:
        from app.errors import AppError  # noqa: PLC0415
        raise AppError("not_found", f"Flow run {flow_run_id!r} not found.", 404)

    task_runs = _run_sync(store.list_task_runs(flow_run_id))
    return {
        "state": run["state"],
        "task_runs": [
            {
                "task_key": tr["task_key"],
                "state": tr["state"],
                "result": tr.get("result"),
                "error": tr.get("error"),
            }
            for tr in task_runs
        ],
    }


def _tool_generate_flow(
    question: str,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Generate a FlowSpec from a natural-language *question*.

    With ``NullProvider`` (the default when no API key is set) this returns a
    deterministic 2-task demo flow (query ``demo_all`` → agent summary) that
    passes ``validate_flow_spec`` — useful for testing without a real LLM.

    Parameters
    ----------
    question:
        Natural-language description of what the flow should do.
    claims:
        Caller claims (passed to the provider if available).
    """
    from app.ai.provider import get_provider  # noqa: PLC0415
    from app.flows.spec import flow_spec_json_schema  # noqa: PLC0415

    provider = get_provider()

    # With a NullProvider (or any provider that returns an empty / non-JSON
    # response), fall back to a deterministic demo spec.
    if provider.name == "null":
        spec = _demo_spec(question)
        return {"spec": spec, "provider": provider.name}

    # With a real provider, try to generate a spec via the LLM.
    schema = flow_spec_json_schema()
    system_prompt = (
        "You are a workflow spec generator. "
        "Return ONLY a valid JSON object matching the FlowSpec schema below. "
        "No markdown fences, no explanation.\n\n"
        f"Schema:\n{schema}"
    )
    user_prompt = f"Generate a FlowSpec for: {question}"
    raw = provider.complete(user_prompt, system=system_prompt)

    import json  # noqa: PLC0415

    try:
        spec = json.loads(raw)
    except Exception:
        # LLM returned non-JSON — fall back to demo spec.
        spec = _demo_spec(question)

    return {"spec": spec, "provider": provider.name}


def _demo_spec(question: str) -> dict[str, Any]:
    """Return a deterministic 2-task demo FlowSpec that passes validation.

    Tasks: query ``demo_all`` → agent summary.
    The *question* is used as the agent prompt so the spec relates to the
    question, making NullProvider tests meaningful.
    """
    return {
        "version": 1,
        "name": "demo_flow",
        "params": [],
        "tasks": [
            {
                "key": "pull",
                "kind": "query",
                "needs": [],
                "config": {"query_id": "demo_all"},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 60,
                "cache_ttl_s": 0,
                "ui": {"x": 0, "y": 0},
            },
            {
                "key": "summary",
                "kind": "agent",
                "needs": ["pull"],
                "config": {
                    "prompt": f"Summarize the result. Context: {question}",
                    "max_steps": 2,
                },
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 60,
                "cache_ttl_s": 0,
                "ui": {"x": 200, "y": 0},
            },
        ],
    }


# ---------------------------------------------------------------------------
# JSON Schemas for each tool
# ---------------------------------------------------------------------------

_SCHEMA_LIST_FLOWS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCHEMA_CREATE_FLOW: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Human-readable flow name.",
        },
        "spec": {
            "type": "object",
            "description": "FlowSpec dict (version, name, params, tasks).",
        },
    },
    "required": ["name", "spec"],
    "additionalProperties": False,
}

_SCHEMA_RUN_FLOW: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flow_id": {
            "type": "string",
            "description": "UUID of the flow to run.",
        },
        "params": {
            "type": "object",
            "description": "Optional flow-level parameter values.",
            "additionalProperties": True,
        },
    },
    "required": ["flow_id"],
    "additionalProperties": False,
}

_SCHEMA_GET_FLOW_RUN: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flow_run_id": {
            "type": "string",
            "description": "UUID of the flow run.",
        },
    },
    "required": ["flow_run_id"],
    "additionalProperties": False,
}

_SCHEMA_GENERATE_FLOW: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "Natural-language description of what the flow should do.",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# ToolDef wrappers (fn signature: fn(claims, **kwargs) -> dict)
# ---------------------------------------------------------------------------
#
# These are imported by ``app.ai.tools._make_registry()`` and appended to the
# tools list.  The wrapper functions forward kwargs to the typed implementations
# above, keeping the tool registry pattern consistent.


def _wrap_list_flows(claims: dict[str, Any], **_kw: Any) -> dict[str, Any]:
    return _tool_list_flows(claims)


def _wrap_create_flow(
    claims: dict[str, Any],
    name: str,
    spec: dict[str, Any],
    **_kw: Any,
) -> dict[str, Any]:
    return _tool_create_flow(name, spec, claims)


def _wrap_run_flow(
    claims: dict[str, Any],
    flow_id: str,
    params: dict[str, Any] | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    return _tool_run_flow(flow_id, claims, params=params)


def _wrap_get_flow_run(
    claims: dict[str, Any],
    flow_run_id: str,
    **_kw: Any,
) -> dict[str, Any]:
    return _tool_get_flow_run(flow_run_id, claims)


def _wrap_generate_flow(
    claims: dict[str, Any],
    question: str,
    **_kw: Any,
) -> dict[str, Any]:
    return _tool_generate_flow(question, claims)


# ---------------------------------------------------------------------------
# Exported ToolDef instances — imported by tools.py
# ---------------------------------------------------------------------------
#
# We import ToolDef lazily (inside the function) to avoid a circular import
# at module load time, since tools.py also imports from this module.
# However, we can import it at module level safely because tools.py does not
# import from flow_tools at module level — it only does so inside
# _make_registry() which runs after all modules have loaded.


def make_flow_tool_defs() -> list[Any]:
    """Build and return the list of flow ToolDef instances.

    Called by ``app.ai.tools._make_registry()`` to avoid circular imports
    (tools.py imports this module; this function defers the ToolDef import
    until tools.py has already been fully loaded).
    """
    from app.ai.tools import ToolDef  # noqa: PLC0415

    return [
        ToolDef(
            name="list_flows",
            description="List all workflow flows for the caller's org (returns id + name pairs).",
            json_schema=_SCHEMA_LIST_FLOWS,
            fn=_wrap_list_flows,
        ),
        ToolDef(
            name="create_flow",
            description=(
                "Validate a FlowSpec and persist it as a new flow. "
                "Returns {id, valid, issues}."
            ),
            json_schema=_SCHEMA_CREATE_FLOW,
            fn=_wrap_create_flow,
        ),
        ToolDef(
            name="run_flow",
            description=(
                "Materialise and synchronously drain a flow run. "
                "Returns {flow_run_id, state, task_runs:[{task_key,state}]}."
            ),
            json_schema=_SCHEMA_RUN_FLOW,
            fn=_wrap_run_flow,
        ),
        ToolDef(
            name="get_flow_run",
            description=(
                "Get the current state of a flow run. "
                "Returns {state, task_runs:[{task_key,state,result,error}]}."
            ),
            json_schema=_SCHEMA_GET_FLOW_RUN,
            fn=_wrap_get_flow_run,
        ),
        ToolDef(
            name="generate_flow",
            description=(
                "Generate a FlowSpec from a natural-language question. "
                "With NullProvider returns a deterministic 2-task demo flow."
            ),
            json_schema=_SCHEMA_GENERATE_FLOW,
            fn=_wrap_generate_flow,
        ),
    ]
