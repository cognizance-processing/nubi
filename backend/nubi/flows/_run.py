"""Flow runner helpers — run_local() and arun().

These helpers compile a ``@flow``-decorated function to a FlowSpec dict and
then invoke the Nubi flows engine to execute it via the in-memory store.

**Q5 (resolved):**
- ``run_local()`` raises a clear ``RuntimeError`` when called from inside an
  already-running event loop (e.g. inside an ``async def`` or a Jupyter cell
  that already has a loop).
- ``arun()`` is the async counterpart and should be used from within async
  contexts.

Public API
----------
run_local(flow_fn, params, *, max_steps, claims) -> dict
    Compile and synchronously run a ``@flow`` function via the in-memory
    store.  Returns the final flow_run dict.  Raises ``RuntimeError`` inside
    a running event loop.

arun(flow_fn, params, *, max_steps, claims) -> dict
    Async variant of ``run_local``.  Safe to ``await`` from inside an
    ``async def``.  Returns the final flow_run dict.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


def run_local(
    flow_fn: Callable,
    params: dict[str, Any] | None = None,
    *,
    max_steps: int = 200,
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile and synchronously run a ``@flow`` function.

    Compiles *flow_fn* to a FlowSpec dict, materialises a flow_run in the
    in-memory ``InMemoryFlowStore``, drains it via ``drain_flow_run``, and
    returns the final flow_run dict.

    **Q5:** Raises ``RuntimeError`` when called from inside an already-running
    event loop.  Use ``arun()`` instead in async contexts.

    Parameters
    ----------
    flow_fn:
        A function decorated with ``@flow``.  Must have a ``.compile()``
        method (attached by the decorator).
    params:
        Flow-level parameter overrides (passed as keyword arguments to
        ``.compile()``).  Defaults to ``{}``.
    max_steps:
        Maximum number of task executions before ``drain_flow_run`` gives up.
        Prevents infinite loops in buggy specs.  Defaults to ``200``.
    claims:
        Auth claims dict passed through to all task handlers (for RLS).
        Defaults to ``{}``.

    Returns
    -------
    dict
        The final flow_run dict from the in-memory store, including state
        (``"success"`` or ``"failed"``), ``finished_at``, and all task_run
        results accessible via ``store.list_task_runs(flow_run_id)``.

    Raises
    ------
    RuntimeError
        If called from inside a running asyncio event loop.  Use ``arun()``
        instead.
    AttributeError
        If *flow_fn* does not have a ``.compile()`` method (i.e. it was not
        decorated with ``@flow``).

    Examples
    --------
    ::

        from nubi.flows import flow, task
        from nubi.flows._run import run_local

        @task(kind="noop")
        def hello(): pass

        @flow
        def simple():
            hello()

        flow_run = run_local(simple)
        assert flow_run["state"] == "success"
    """
    # Q5: detect running loop and raise a clear error.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        raise RuntimeError(
            "run_local() was called from inside a running asyncio event loop. "
            "Use 'await arun(flow_fn, ...)' instead, or call run_local() from "
            "a non-async context (e.g. a plain Python script or __main__ block)."
        )

    return asyncio.run(_run_async(flow_fn, params or {}, max_steps, claims or {}))


async def arun(
    flow_fn: Callable,
    params: dict[str, Any] | None = None,
    *,
    max_steps: int = 200,
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Async variant of ``run_local``.

    Safe to ``await`` from inside an ``async def`` or a Jupyter notebook cell
    (where an event loop is already running).

    Parameters
    ----------
    flow_fn:
        A function decorated with ``@flow``.
    params:
        Flow-level parameter overrides passed to ``.compile()``.
    max_steps:
        Maximum task executions before giving up.
    claims:
        Auth claims dict passed through to all handlers.

    Returns
    -------
    dict
        The final flow_run dict (same as ``run_local``).

    Examples
    --------
    ::

        import asyncio
        from nubi.flows import flow, task
        from nubi.flows._run import arun

        @task(kind="noop")
        def hello(): pass

        @flow
        def simple():
            hello()

        async def main():
            flow_run = await arun(simple)
            print(flow_run["state"])

        asyncio.run(main())
    """
    return await _run_async(flow_fn, params or {}, max_steps, claims or {})


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


async def _run_async(
    flow_fn: Callable,
    params: dict[str, Any],
    max_steps: int,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Compile, materialise, drain, and return the flow_run."""
    from datetime import datetime, timezone  # noqa: PLC0415

    from app.flows.runtime import drain_flow_run, materialize_flow_run  # noqa: PLC0415
    from app.flows.store import InMemoryFlowStore  # noqa: PLC0415

    if not callable(getattr(flow_fn, "compile", None)):
        raise AttributeError(
            f"{flow_fn!r} does not have a .compile() method. "
            "Ensure it is decorated with @flow."
        )

    # Compile the flow function to a FlowSpec dict.
    spec_dict: dict[str, Any] = flow_fn.compile(**params)

    store = InMemoryFlowStore()
    now = datetime.now(timezone.utc)

    # Persist the flow in the store so _get_task_spec can resolve it by
    # flow_id when walking map/branch child task_runs.  Without this call
    # store.get_flow(flow_id) returns None and map fan-out silently fails.
    org_id: str = str(claims.get("org_id", "local"))
    flow_obj = await store.create_flow(
        org_id=org_id,
        created_by="sdk",
        name=spec_dict.get("name", "flow"),
        spec=spec_dict,
    )

    flow_run = await materialize_flow_run(
        store=store,
        flow=flow_obj,
        params=params,
        trigger="sdk",
        now=now,
    )

    flow_run = await drain_flow_run(
        store=store,
        flow_run_id=flow_run["id"],
        now=now,
        claims=claims,
        max_steps=max_steps,
    )

    return flow_run
