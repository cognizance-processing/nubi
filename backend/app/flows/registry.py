"""Task-kind registry for the Flows engine.

Mirrors ``app/connectors/registry.py`` in structure and style.

Public API
----------
TaskKindRegistry
    Registry mapping task kind strings to handler callables.
    Handler signature: ``handler(config, ctx, claims) -> dict``
    where *config* is the resolved task config dict, *ctx* is a
    ``TaskContext``, and *claims* is the caller's auth claims dict.
    The handler must return a JSON-serialisable dict (the task result).

get_task_kind_registry() -> TaskKindRegistry
    Return the module-level singleton, pre-populated with built-in handlers.

reset_for_tests() -> None
    Re-bootstrap the singleton (test helper — production code must not call).

Pre-registered kinds
--------------------
``'query'``
    Run a registered query or ad-hoc SQL via the planner + DuckDB.
    Mirrors ``_tool_run_query`` in ``app/ai/tools.py``.

``'python'``
    Execute a Python code snippet via ``LocalSubprocessRunner``.
    Mirrors the ``_run_python_job`` path in ``app/jobs/executor.py``;
    injects ``inputs`` and ``params`` as local variables.

``'agent'``
    Call ``run_agent`` with the caller's claims.  Returns
    ``{reply: str, actions: list[dict]}``.  With ``NullProvider`` the
    result is fully deterministic (no network calls).

``'materialize'``
    Merge upstream single-source ``query`` results in DuckDB via an
    author-supplied ``combine_sql`` and write the combined result to a
    materialized single-source DuckDB dataset (the "blend").  Preserves the
    declared ``rls_keys`` so the planner can inject RLS at read time.  See
    ``app/flows/materialize.py``.

``'noop'``
    Pass-through join/fork node.  Returns ``{inputs: ctx.inputs}``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from app.errors import AppError

if TYPE_CHECKING:
    from app.flows.executor import TaskContext


# ---------------------------------------------------------------------------
# TaskKindRegistry
# ---------------------------------------------------------------------------


class TaskKindRegistry:
    """Registry mapping task kind strings to handler callables.

    Usage
    -----
    ::

        registry = get_task_kind_registry()

        # Register a custom handler
        registry.register("my_kind", my_handler)

        # Retrieve and call
        handler = registry.get("my_kind")
        result = handler(config, ctx, claims)

        # Inspect all registered kinds
        all_handlers = registry.all()
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {}

    def register(self, kind: str, handler: Callable[..., dict[str, Any]]) -> None:
        """Register *handler* under *kind*.

        Parameters
        ----------
        kind:
            Lowercase kind string (e.g. ``"query"``, ``"python"``).
        handler:
            Callable with signature
            ``(config: dict, ctx: TaskContext, claims: dict) -> dict``.
            Registering the same *kind* twice overwrites the previous handler.
        """
        self._handlers[kind] = handler

    def get(self, kind: str) -> Callable[..., dict[str, Any]]:
        """Return the handler for *kind*.

        Raises
        ------
        AppError("unknown_task_kind", 400)
            If *kind* has not been registered.
        """
        try:
            return self._handlers[kind]
        except KeyError:
            raise AppError(
                "unknown_task_kind",
                f"No handler registered for task kind {kind!r}. "
                f"Registered kinds: {sorted(self._handlers)}",
                400,
            )

    def all(self) -> dict[str, Callable[..., dict[str, Any]]]:
        """Return a shallow copy of the full handler mapping."""
        return dict(self._handlers)


# ---------------------------------------------------------------------------
# Built-in kind handlers
# ---------------------------------------------------------------------------


def _handle_query(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Run a query task: registered query or ad-hoc SQL via planner + DuckDB.

    Mirrors ``_tool_run_query`` from ``app/ai/tools.py`` exactly, including
    ``_seed_demo_table`` so the ``demo`` table is available in tests.

    Config keys (after template resolution)
    ----------------------------------------
    ``query_id``   (optional) — id of a registered query.
    ``sql``        (optional) — ad-hoc SELECT SQL (used if query_id absent).
    ``named_params`` (optional) — param overrides.

    Returns
    -------
    dict
        ``{rows, row_count, columns}``
    """
    from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415
    from app.connectors.planner import plan, resolve_named_params  # noqa: PLC0415
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

    physical_plan = plan(resolved_sql, claims=claims, params=positional_params)
    connector = DuckDBConnector()
    _seed_demo_table(connector)

    arrow_table = connector.execute(physical_plan)
    columns = arrow_table.schema.names
    rows = arrow_table.to_pylist()
    return {"rows": rows, "row_count": len(rows), "columns": columns}


def _seed_demo_table(connector: Any) -> None:
    """Seed the ``demo`` table into a fresh DuckDB connector (mirrors tools.py)."""
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


def _handle_python(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Run a Python code snippet in a subprocess with injected context.

    The code snippet may assign any JSON-serialisable value to ``result``.
    If ``result`` is a dict it is returned directly; otherwise it is wrapped
    as ``{"value": result}``.  If ``result`` is not set, ``{}`` is returned.

    Context variables injected as locals:
    - ``inputs``  — dict of upstream task results (task_key → result dict).
    - ``params``  — dict of flow-level parameter values.

    This handler runs the code in a fresh subprocess using the same Python
    interpreter as the parent (``sys.executable``), without using
    ``LocalSubprocessRunner`` (which requires a pyarrow.Table result).

    Config keys
    -----------
    ``code`` — required Python source code string.
    """
    import json  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    import textwrap  # noqa: PLC0415
    import os  # noqa: PLC0415

    code: str = config.get("code", "")
    if not code:
        raise AppError("invalid_task_config", "python task requires 'code' in config.", 400)

    timeout_s: int = int(config.get("timeout_s", 60) or 60)

    # Serialise context for injection.
    try:
        inputs_json = json.dumps(ctx.inputs)
        params_json = json.dumps(ctx.flow_params)
    except (TypeError, ValueError) as exc:
        raise AppError("invalid_task_context", f"Context not JSON-serialisable: {exc}", 400)

    # Build the wrapper script.  We write it to a temp file and run it via
    # sys.executable so the subprocess inherits the same packages.
    wrapper = textwrap.dedent(f"""\
        import json as _json
        import sys as _sys

        # Inject flow context as local variables.
        inputs = _json.loads({inputs_json!r})
        params = _json.loads({params_json!r})

        # ── User code ────────────────────────────────────────────────────
{textwrap.indent(code, '        ')}
        # ── End user code ────────────────────────────────────────────────

        try:
            _result_val = result
        except NameError:
            _result_val = None

        if _result_val is None:
            _out = {{}}
        elif isinstance(_result_val, dict):
            _out = _result_val
        else:
            try:
                _serialised = _json.dumps(_result_val)
                _out = {{"value": _result_val}}
            except (TypeError, ValueError):
                _out = {{"value": str(_result_val)}}

        print("__FLOW_RESULT__:" + _json.dumps(_out))
    """)

    # Build a safe environment (forward PYTHONPATH so packages are importable).
    env: dict[str, str] = {}
    for key in ("PATH", "PYTHONPATH", "HOME", "TMPDIR", "TEMP", "TMP",
                "LANG", "LC_ALL", "LC_CTYPE", "VIRTUAL_ENV"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    # Ensure site-packages from the current interpreter are on PYTHONPATH.
    site_paths = [p for p in sys.path if p and "site-packages" in p]
    existing_pp = env.get("PYTHONPATH", "")
    combined_pp = ":".join(filter(None, [existing_pp] + site_paths))
    if combined_pp:
        env["PYTHONPATH"] = combined_pp

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(wrapper)
        tmp_path = tmp.name

    try:
        proc = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout_s if timeout_s > 0 else None,
            env=env,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Collect stdout lines (excluding the tagged result sentinel).
    stdout_lines: list[str] = []
    task_result: dict[str, Any] = {}
    for line in (proc.stdout or "").splitlines():
        if line.startswith("__FLOW_RESULT__:"):
            try:
                task_result = json.loads(line[len("__FLOW_RESULT__:"):])
            except Exception:  # noqa: BLE001
                task_result = {"raw": line}
        else:
            # Every other stdout line is a user log line.
            stdout_lines.append(line)

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        # Attach stderr lines to stdout_lines for capture.
        if stderr:
            for ln in stderr.splitlines():
                stdout_lines.append(ln)
        raise RuntimeError(f"Python task failed (exit {proc.returncode}): {stderr[:500]}")

    # Attach captured stdout lines as metadata so the executor can extract them.
    task_result["_stdout_lines"] = stdout_lines
    return task_result


def _handle_agent(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Run an agent task via ``run_agent``.

    Returns ``{reply: str, actions: list[dict]}``.  With ``NullProvider``
    (the default when no API key is set) the result is fully deterministic
    and needs no network access.

    Config keys
    -----------
    ``prompt``     — required.  Used as the user message.
    ``max_steps``  — optional int, defaults to 4.
    """
    from app.ai.agent import run_agent  # noqa: PLC0415
    from app.ai.provider import get_provider  # noqa: PLC0415

    prompt: str = config.get("prompt", "")
    if not prompt:
        raise AppError("invalid_task_config", "agent task requires 'prompt' in config.", 400)

    max_steps: int = int(config.get("max_steps", 4))
    provider = get_provider()

    messages = [{"role": "user", "content": prompt}]
    result = run_agent(messages, provider, claims, max_steps=max_steps)
    return result


def _handle_materialize(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Merge upstream source results in DuckDB and materialize a blend dataset.

    Delegates to ``app.flows.materialize.materialize_blend`` which registers
    each upstream source result (``inputs[source_key]``) as a DuckDB table,
    runs ``config['combine_sql']``, writes the combined result to the
    materialized DuckDB file (``config['database']``, table ``config['table']``),
    verifies the declared ``rls_keys`` survived, and registers a runtime query
    bound to the blend datastore so a dashboard widget can read via one
    ``query_id``.

    Config keys
    -----------
    ``combine_sql`` (required), ``sources``, ``rls_keys``, ``table``,
    ``database``, ``datastore_id``, ``query_id``.

    Returns
    -------
    dict
        The materialization manifest (datastore_id, query_id, database, table,
        row_count, columns, rls_keys).
    """
    from app.flows.materialize import materialize_blend  # noqa: PLC0415

    return materialize_blend(config, ctx.inputs)


def _handle_noop(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Pass-through node — returns all upstream inputs."""
    return {"inputs": ctx.inputs}


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

_registry: TaskKindRegistry | None = None


def get_task_kind_registry() -> TaskKindRegistry:
    """Return (or lazily create) the module-level ``TaskKindRegistry`` singleton.

    Pre-populates with the four built-in handlers: ``query``, ``python``,
    ``agent``, ``noop``.
    """
    global _registry
    if _registry is None:
        _registry = TaskKindRegistry()
        _bootstrap(_registry)
    return _registry


def reset_for_tests() -> None:
    """Re-bootstrap the singleton with built-in handlers.

    Intended for test setup only — production code must never call this.
    """
    global _registry
    if _registry is None:
        _registry = TaskKindRegistry()
    _bootstrap(_registry)


def _bootstrap(registry: TaskKindRegistry) -> None:
    """Pre-register the four built-in kind handlers."""
    registry.register("query", _handle_query)
    registry.register("python", _handle_python)
    registry.register("agent", _handle_agent)
    registry.register("materialize", _handle_materialize)
    registry.register("noop", _handle_noop)
