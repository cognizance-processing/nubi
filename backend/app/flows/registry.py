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
    (Archive extraction was previously ``'extract'`` — removed in favour
    of user-supplied Python code.  Use ``kind='python'`` with a code snippet
    that calls your preferred extraction library.)

``'bucket_load'``
    Serialise upstream row data and write it to object storage (S3,
    GCS, Azure, local).  Supports csv/json/ndjson/parquet formats and
    overwrite/append modes.
    Delegates to ``app.flows.handlers.bucket.handle``.

``'map'``
    Fan-out node.  Resolves ``config['item_expr']`` to a list, then signals
    the runtime to create one set of child task_runs per item (the body
    sub-DAG).  The handler returns a sentinel dict containing
    ``'__map_items__'``; the runtime transitions the map task_run to
    ``'waiting_children'`` and fans out.
    Delegates to ``app.flows.handlers.map.handle_map``.

``'branch'``
    Conditional routing node.  Evaluates ``config['conditions']`` (ordered
    list of ``{when, next}`` dicts) and returns the first matching condition's
    ``next`` task keys in ``'__branch_next__'``.  The runtime activates those
    tasks and marks all other dependents ``'upstream_failed'``.
    ``config['default']`` is optional (Q1: else_ is optional).
    Delegates to ``app.flows.handlers.branch.handle_branch``.

``'map_collect'``
    Fan-in collector node.  Reads the aggregated result from an upstream
    ``'map'`` node (``config['source']``) and returns
    ``{"items": [...], "item_count": N}`` (Q3: dedicated collector).
    Delegates to ``app.flows.handlers.map_collect.handle_map_collect``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from app.errors import AppError

if TYPE_CHECKING:
    from app.flows.executor import TaskContext


# ---------------------------------------------------------------------------
# Connector-dialect map (mirrors blueprint §3.4 / §7 row 1)
#
# Maps the ``connector_type`` string stored in a datastore's config dict to
# the sqlglot dialect name used for SQL generation.  When a task config
# carries ``datastore_id``, ``_resolve_flow_connector`` looks up this dict to
# determine the target dialect so sqlglot can produce warehouse-native SQL.
# ---------------------------------------------------------------------------

CONNECTOR_DIALECT: dict[str, str] = {
    "postgres": "postgres",
    "redshift": "postgres",
    "cockroachdb": "postgres",
    "cloudsql": "postgres",
    "duckdb": "duckdb",
    "duckdb_storage": "duckdb",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlserver": "tsql",
    "azuresql": "tsql",
    "azuresynapse": "tsql",
    "oracle": "oracle",
    "clickhouse": "clickhouse",
    "trino": "trino",
    "presto": "presto",
    "athena": "trino",
    "databricks": "databricks",
    "http_json": "postgres",
    "jdbc": "postgres",
}


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
# Connector resolution helper
# ---------------------------------------------------------------------------


def _resolve_flow_connector(
    datastore_id: str,
    org_id: str,
) -> tuple[Any, str]:
    """Resolve a BYO-warehouse connector from a datastore_id.

    Called from ``_handle_query`` when ``config.datastore_id`` is set.
    Runs synchronously (handlers execute in a ``ThreadPoolExecutor`` thread)
    using ``repo.get_sync()`` to avoid spawning a nested event loop on top
    of the running asyncio loop.

    Steps
    -----
    1. Fetch the datastore row via ``get_repo().get_sync("datastores", org_id, id)``.
    2. Inject any decrypted secrets from the secret store (optional; falls back
       gracefully when the secret store is unavailable).
    3. Build the connector instance via the ``ConnectorRegistry`` factory.
    4. Return ``(connector, target_dialect)`` where *target_dialect* is the
       sqlglot dialect string for SQL generation (e.g. ``"snowflake"``,
       ``"bigquery"``, ``"duckdb"``).

    Security notes
    --------------
    - The datastore lookup is org-scoped: ``repo.get_sync("datastores", org_id,
      id)`` returns ``None`` for rows that belong to a different org.
    - RLS predicates are injected by the caller (``_handle_query``) via
      ``plan(sql, claims=claims, ...)``, not inside this helper, so they are
      always applied before SQL reaches the connector.
    - Capability-gated RLS (``predicate_rls=False`` check) is also performed by
      the caller after connector construction.

    Parameters
    ----------
    datastore_id:
        UUID string of the datastore row to resolve.
    org_id:
        UUID string of the caller's organisation; enforces row-level scoping.

    Returns
    -------
    tuple[connector, dialect_str]
        The constructed connector instance and its sqlglot dialect string.

    Raises
    ------
    AppError("datastore_not_found", 404)
        When the datastore row is absent or belongs to a different org.
    AppError("unknown_connector", 404)
        When the connector type is not registered.
    """
    from app.connectors.registry import get_connector_registry  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    repo = get_repo()
    ds = repo.get_sync("datastores", org_id, datastore_id)
    if ds is None:
        raise AppError(
            "datastore_not_found",
            f"Datastore {datastore_id!r} not found for org {org_id!r}.",
            404,
        )

    cfg: dict[str, Any] = dict(ds.get("config") or {})
    ctype: str | None = cfg.get("connector_type") or cfg.get("type")

    # ── Secret injection (mirrors routes/query.py §(a)) ──────────────────────
    # Fetch decrypted credentials and merge into cfg.  Fails gracefully when
    # the secret store is unavailable (e.g. in unit tests without Vault/KMS).
    try:
        from app.connectors.secret_store import get_secret_store as _get_secret_store  # noqa: PLC0415
        _secret_store = _get_secret_store()
        # get_secret_store().get() is async; run it synchronously.
        import asyncio  # noqa: PLC0415
        _secret: dict | None = asyncio.run(_secret_store.get(datastore_id, org_id))
    except (ImportError, Exception):  # noqa: BLE001
        _secret = None

    if _secret:
        if ctype == "postgres":
            if "password" not in cfg:
                cfg["password"] = _secret.get("password", "")
        elif ctype == "http_json":
            _headers: dict = dict(cfg.get("headers") or {})
            if "token" in _secret:
                _headers["Authorization"] = f"Bearer {_secret['token']}"
            elif "api_key" in _secret:
                _headers["X-API-Key"] = _secret["api_key"]
            cfg["headers"] = _headers
        elif ctype == "bigquery":
            if "service_account_json" in _secret:
                cfg["service_account_json"] = _secret["service_account_json"]
        else:
            for k, v in _secret.items():
                if k not in cfg:
                    cfg[k] = v

    # ── Build connector ───────────────────────────────────────────────────────
    factory = get_connector_registry().get(ctype or "duckdb")
    if ctype == "duckdb":
        _db_path = cfg.get("database") or cfg.get("path")
        if _db_path and _db_path != ":memory:":
            import duckdb  # noqa: PLC0415
            _conn = duckdb.connect(database=_db_path, read_only=True)
            try:
                _conn.execute("SET enable_external_access=false")
            except Exception:  # noqa: BLE001
                pass
            connector = factory(_conn)
        else:
            connector = factory()
    elif ctype == "postgres":
        _dsn: str | None = cfg.get("dsn")
        if _dsn is None:
            _host = cfg.get("host", "localhost")
            _port = cfg.get("port", 5432)
            _dbname = cfg.get("dbname") or cfg.get("database") or "postgres"
            _user = cfg.get("user") or cfg.get("username") or "postgres"
            _password = cfg.get("password", "")
            _dsn = f"postgresql://{_user}:{_password}@{_host}:{_port}/{_dbname}"
        connector = factory(_dsn)
    else:
        connector = factory(cfg)

    target_dialect: str = CONNECTOR_DIALECT.get(ctype or "", "postgres")
    return connector, target_dialect


# ---------------------------------------------------------------------------
# Built-in kind handlers
# ---------------------------------------------------------------------------


def _handle_query(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Run a query task: registered query or ad-hoc SQL via planner + connector.

    Resolves the target connector from ``config.datastore_id`` when present,
    falling back to the built-in demo DuckDB connector when absent.  When a
    BYO-warehouse connector is resolved the SQL is first transpiled from
    ``config.source_dialect`` to the target warehouse dialect (if they differ)
    so warehouse-native syntax (QUALIFY, DATE_TRUNC variants, etc.) is
    preserved end-to-end.

    Config keys (after template resolution)
    ----------------------------------------
    ``query_id``       (optional) — id of a registered query.
    ``sql``            (optional) — ad-hoc SELECT SQL (used if query_id absent).
    ``named_params``   (optional) — param overrides for registered queries.
    ``datastore_id``   (optional) — UUID of a BYO-warehouse datastore row.
                        When absent the demo DuckDB connector is used.
    ``source_dialect`` (optional) — sqlglot dialect the SQL was authored in
                        (e.g. ``"bigquery"``).  When set and different from the
                        resolved target dialect, the SQL is transpiled via
                        sqlglot before planning so RLS injection always operates
                        on warehouse-native AST.

    Returns
    -------
    dict
        ``{rows, row_count, columns}``
    """
    import sqlglot  # noqa: PLC0415

    from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415
    from app.connectors.planner import plan, resolve_named_params  # noqa: PLC0415
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    query_id: str | None = config.get("query_id")
    sql: str | None = config.get("sql")
    named_params: dict[str, Any] = config.get("named_params") or {}
    datastore_id: str | None = config.get("datastore_id")
    source_dialect: str | None = config.get("source_dialect")

    resolved_sql: str
    positional_params: list[Any] = []

    if query_id is not None:
        query_registry = get_query_registry()
        rq = query_registry.get(query_id)
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

    # ── 1. Resolve connector + target dialect ──────────────────────────────────
    # When config.datastore_id is set, resolve the BYO-warehouse connector so
    # durable tasks execute against the user's actual warehouse.  Otherwise fall
    # back to the built-in demo DuckDB connector (original behaviour unchanged).
    #
    # org_id resolution order (BYO org_id threading — scheduled-tick fix):
    #   1. ctx.org_id  — set by the runtime from flow_run.org_id; authoritative
    #      for durable and scheduled flows where claims may be empty ({}).
    #   2. claims["org_id"] — fallback for interactive/HTTP callers that pass
    #      a populated claims dict but did not populate ctx.org_id.
    # This ensures BYO-warehouse connector resolution works even when claims is
    # empty (e.g. a scheduled tick where no JWT was issued).
    if datastore_id:
        org_id: str = ctx.org_id or (claims or {}).get("org_id", "") or ""
        connector, target_dialect = _resolve_flow_connector(datastore_id, org_id)
        seed_demo = False
    else:
        connector = DuckDBConnector()
        target_dialect = "duckdb"
        seed_demo = True

    # ── 2. Source-dialect transpile (cross-engine authoring) ──────────────────
    # Transpile BEFORE RLS injection so predicate stripping cannot occur.
    # A no-op when source and target dialects match (or source_dialect absent).
    if source_dialect and source_dialect != target_dialect:
        try:
            resolved_sql = sqlglot.transpile(
                resolved_sql,
                read=source_dialect,
                write=target_dialect,
                unsupported_level=sqlglot.ErrorLevel.WARN,
            )[0]
        except Exception:  # noqa: BLE001
            # Non-fatal: proceed with original SQL; the planner will surface
            # any syntax errors clearly.
            pass

    # ── 3. Plan with target dialect (RLS injection happens here) ──────────────
    # Preview LIMIT pushdown: when running in preview mode, inject a genuine
    # LIMIT <n> into the SQL via the planner (sqlglot AST rewrite) BEFORE the
    # warehouse connector executes.  This ensures big BYO-warehouse cells do not
    # pull millions of rows — the limit is enforced server-side / at the
    # warehouse, not as a post-fetch Python slice.
    # RLS predicate injection (step 5 inside plan()) always runs before the
    # LIMIT is appended (step 6 inside plan()), so RLS is independent of the cap.
    preview_limit: int | None = ctx.preview_limit if ctx.preview_mode else None

    physical_plan = plan(
        resolved_sql,
        claims=claims,
        params=positional_params,
        dialect=target_dialect,
        limit=preview_limit,
    )

    # ── 4. Capability-gated RLS check ─────────────────────────────────────────
    # Mirror the gate in routes/query.py: refuse before execute() if the
    # connector cannot enforce row-level security predicates.
    policies = (physical_plan.rls_claims or {}).get("policies") or {}
    if policies and connector.capabilities().get("predicate_rls") is False:
        raise AppError(
            "source_unsupported_rls",
            "This source does not support Row-Level Security (predicate_rls=False). "
            "Cannot execute a policy-bearing query on an unsecurable source.",
            501,
        )

    # ── 5. Seed demo table (only for the fallback DuckDB path) ────────────────
    if seed_demo:
        _seed_demo_table(connector)

    # ── 6. Execute ────────────────────────────────────────────────────────────
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
    If ``result`` is a dict it is returned directly; if it is a
    ``pandas.DataFrame`` it is auto-serialised to ``{rows, columns, row_count}``
    (so it flows through the Python→SQL bridge and downstream ``dataframes``
    like a query result); otherwise it is wrapped as ``{"value": result}``.
    If ``result`` is not set, ``{}`` is returned.

    Context variables injected as locals:
    - ``inputs``     — dict of upstream task results (task_key → result dict).
    - ``params``     — dict of flow-level parameter values.
    - ``secrets``    — dict of the org's resolved secrets (``{name: value}``),
      resolved server-side and passed via the wrapper script's JSON context
      (NOT via env vars — the subprocess env stays scrubbed).  Read a
      credential with ``secrets["MY_KEY"]``.  Any secret value printed to
      stdout is masked as ``'•••'`` in captured task logs by the executor.
    - ``dataframes`` — dict mapping each upstream key whose result has
      ``rows``+``columns`` to a ``pandas.DataFrame`` (empty when pandas is
      unavailable; ``inputs`` is unaffected).

    This handler runs the code in a fresh subprocess using the same Python
    interpreter as the parent (``sys.executable``), without using
    ``LocalSubprocessRunner`` (which requires a pyarrow.Table result).
    It shares the M4-SEC subprocess hardening via
    ``app.compute.sandbox.run_sandboxed``: scrubbed env, new process
    group/session with group-SIGKILL on timeout (so grandchildren cannot
    survive), POSIX rlimits (memory / file-size / nproc always; CPU only when
    a timeout is set — ``timeout_s == 0`` means "no timeout" and must not have
    long CPU work silently killed by RLIMIT_CPU), and 1 MiB stdout/stderr
    caps with a truncation marker (env-overridable — see sandbox.py).

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

    from app.compute.sandbox import (  # noqa: PLC0415
        RLIMIT_CPU_GRACE_S,
        STDERR_CAP_BYTES,
        STDOUT_CAP_BYTES,
        run_sandboxed,
    )

    code: str = config.get("code", "")
    if not code:
        raise AppError("invalid_task_config", "python task requires 'code' in config.", 400)

    timeout_s: int = int(config.get("timeout_s", 60) or 60)

    # Serialise context for injection.
    try:
        inputs_json = json.dumps(ctx.inputs)
        params_json = json.dumps(ctx.flow_params)
        # Org/project variable namespace (A5) — read-only in python cells via the
        # `vars` dict, mirroring the {{ vars.* }} SQL-cell namespace.
        vars_json = json.dumps(ctx.vars or {})
        # Org secrets, resolved server-side by the runtime.  Injected via the
        # wrapper's JSON context (never via env vars — env stays scrubbed).
        secrets_json = json.dumps({str(k): str(v) for k, v in (ctx.secrets or {}).items()})
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
        vars = _json.loads({vars_json!r})
        secrets = _json.loads({secrets_json!r})

        # ── DataFrame-native inputs (additive; pandas-guarded) ───────────
        # For each upstream key whose result has {{rows, columns}}, expose a
        # pandas.DataFrame under `dataframes[key]`.  Degrades to an empty dict
        # when pandas is unavailable so `inputs` keeps working unchanged.
        try:
            import pandas as _pd
        except Exception:  # pandas not installed
            _pd = None
            print("[warn] pandas unavailable; `dataframes` is empty")
        dataframes = {{}}
        if _pd is not None:
            for _k, _v in inputs.items():
                if isinstance(_v, dict) and "rows" in _v and "columns" in _v:
                    try:
                        dataframes[_k] = _pd.DataFrame(_v["rows"], columns=_v["columns"])
                    except Exception:
                        pass

        # ── User code ────────────────────────────────────────────────────
{textwrap.indent(code, '        ')}
        # ── End user code ────────────────────────────────────────────────

        try:
            _result_val = result
        except NameError:
            _result_val = None

        if _result_val is None:
            _out = {{}}
        elif _pd is not None and isinstance(_result_val, _pd.DataFrame):
            # Auto-serialise a returned DataFrame to the canonical row-result
            # shape so it flows through the Python→SQL bridge and downstream
            # `dataframes` identically to a query result.
            _df = _result_val
            _out = {{
                "columns": [str(_c) for _c in _df.columns],
                "rows": _df.to_dict(orient="records"),
                "row_count": int(len(_df)),
            }}
        elif isinstance(_result_val, dict):
            _out = _result_val
        else:
            try:
                _serialised = _json.dumps(_result_val)
                _out = {{"value": _result_val}}
            except (TypeError, ValueError):
                _out = {{"value": str(_result_val)}}

        print("__FLOW_RESULT__:" + _json.dumps(_out, default=str))
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

    # Run via the shared M4-SEC hardened sandbox (same helper as
    # LocalSubprocessRunner): new process group + group-SIGKILL on timeout
    # (plain subprocess.run(timeout=...) leaves grandchildren alive), POSIX
    # rlimits, and 1 MiB stdout/stderr caps with a truncation marker.
    # timeout_s == 0 means "no subprocess timeout" — in that case RLIMIT_CPU
    # is also skipped (cpu_limit_s=None) so legit long CPU work is not killed;
    # the memory / file-size / nproc caps still apply.
    argv = [sys.executable, tmp_path]
    try:
        run = run_sandboxed(
            argv,
            env=env,
            timeout_s=timeout_s if timeout_s > 0 else None,
            cpu_limit_s=(timeout_s + RLIMIT_CPU_GRACE_S) if timeout_s > 0 else None,
            stdout_cap=STDOUT_CAP_BYTES,
            stderr_cap=STDERR_CAP_BYTES,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if run.timed_out:
        # Preserve the pre-hardening timeout semantics: subprocess.run raised
        # TimeoutExpired, which propagated to the executor's broad except and
        # marked the task failed.  The process GROUP has already been killed.
        raise subprocess.TimeoutExpired(argv, timeout_s)

    stdout_text = run.stdout.decode("utf-8", errors="replace")
    stderr_text = run.stderr.decode("utf-8", errors="replace")

    # Collect stdout lines (excluding the tagged result sentinel).
    stdout_lines: list[str] = []
    task_result: dict[str, Any] = {}
    for line in stdout_text.splitlines():
        if line.startswith("__FLOW_RESULT__:"):
            try:
                task_result = json.loads(line[len("__FLOW_RESULT__:"):])
            except Exception:  # noqa: BLE001
                task_result = {"raw": line}
        else:
            # Every other stdout line is a user log line (the truncation
            # marker, when present, surfaces here as a log line too).
            stdout_lines.append(line)

    if run.returncode != 0:
        stderr = stderr_text.strip()
        # Attach stderr lines to stdout_lines for capture.
        if stderr:
            for ln in stderr.splitlines():
                stdout_lines.append(ln)
        raise RuntimeError(f"Python task failed (exit {run.returncode}): {stderr[:500]}")

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

    return materialize_blend(
        config,
        ctx.inputs,
        env=getattr(ctx, "env", "prod") or "prod",
        flow=getattr(ctx, "flow", None),
        watermark=getattr(ctx, "watermark", None),
    )


def _handle_noop(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Pass-through node — returns all upstream inputs."""
    return {"inputs": ctx.inputs}


def _handle_bucket_load(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Write upstream data to object storage.

    Delegates to ``app.flows.handlers.bucket.handle``.

    Returns ``{"uri": str, "format": str, "row_count": int, "bytes_written": int}``.

    Config keys
    -----------
    ``uri`` (required), ``source`` (required), ``format`` (optional, default
    ``'csv'``), ``mode`` (optional, default ``'overwrite'``), ``secret``
    (optional).  See ``app.flows.handlers.bucket`` for the full schema.
    """
    from app.flows.handlers.bucket import handle  # noqa: PLC0415

    return handle(config, ctx, claims)


def _handle_preagg_refresh(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Run the auto pre-aggregation suggest → materialize pass.

    Delegates to ``app.flows.handlers.preagg_refresh.handle`` which in turn
    calls ``app.preagg.scheduler.run_preagg_refresh``.

    Returns ``{org_id, candidates_found, rollups_built, rollup_ids, errors}``.

    Config keys
    -----------
    ``org_id``        (required) — org whose query log is mined.
    ``min_hits``      (optional, default 3) — minimum log frequency threshold.
    ``source_database`` (optional) — DuckDB file path; ``None`` for in-memory.

    See ``app.flows.handlers.preagg_refresh`` for the full schema.
    """
    from app.flows.handlers.preagg_refresh import handle  # noqa: PLC0415

    return handle(config, ctx, claims)


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

_registry: TaskKindRegistry | None = None


def get_task_kind_registry() -> TaskKindRegistry:
    """Return (or lazily create) the module-level ``TaskKindRegistry`` singleton.

    Pre-populates with built-in handlers: ``query``, ``python``, ``agent``,
    ``materialize``, ``noop``, ``bucket_load``, ``preagg_refresh``,
    ``map``, ``branch``, ``map_collect``.
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
    """Pre-register all built-in kind handlers."""
    registry.register("query", _handle_query)
    registry.register("python", _handle_python)
    registry.register("agent", _handle_agent)
    registry.register("materialize", _handle_materialize)
    registry.register("noop", _handle_noop)
    registry.register("bucket_load", _handle_bucket_load)
    registry.register("preagg_refresh", _handle_preagg_refresh)

    from app.flows.handlers.map import handle_map  # noqa: PLC0415
    from app.flows.handlers.branch import handle_branch  # noqa: PLC0415
    from app.flows.handlers.map_collect import handle_map_collect  # noqa: PLC0415

    registry.register("map", handle_map)
    registry.register("branch", handle_branch)
    registry.register("map_collect", handle_map_collect)
