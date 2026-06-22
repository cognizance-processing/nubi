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

``'snapshot_refresh'``
    Re-run a board's widget collector and rewrite its frozen DuckDB sidecar
    artifact in place, so a daily/cron flow keeps an embed snapshot fresh.
    Delegates to ``app.embedding.snapshot.snapshot_refresh_handler``.  The
    captured RLS view is declared in ``config['policies']`` (a scheduled tick
    has no user JWT) and is exposed to ALL artifact holders.

``'report_send'``
    Resolve a board, render it (CSV / PDF / PPTX) and deliver the result to
    the configured recipients via email (+ optional Slack/Teams channels).
    Supports per-recipient RLS via ``locked_params`` and is schedulable
    (daily/cron) like ``snapshot_refresh`` — the captured RLS view comes
    from ``config['policies']``, not a live JWT.
    Delegates to ``app.flows.handlers.report_send.handle``.
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
    - ``watermark``  — the stored incremental ISO mark (``None`` on first run);
      a cell advances it by returning ``{"watermark": "<iso>"}`` (design §6.1).
    - ``staging``    — a pinned Parquet writer (design §6.2):
      ``staging.write(df_or_batches, "orders/", format="parquet") -> manifest``.
      It SPOOLS Parquet locally; the parent re-stages each object under the
      server-pinned ``orgs/<org>/staging/<run>/`` prefix (user code can never
      escape it), verifies it, and — if ``config['target']`` is declared
      (``{connector_id, object}``, same shape as file_ingest) — loads it via the
      loader layer.  Large pulls land as Parquet manifests in staging, NOT rows
      serialised through the task result.  The returned manifest is the §5
      ``{files:[{path,size,sha256}], row_counts}`` shape.

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

    # ── Staging spool dir (design §6.2) ────────────────────────────────────────
    # ``ctx.staging.write(...)`` cannot hand a live StagingArea (storage clients)
    # across the subprocess boundary, so the subprocess writes Parquet into this
    # local SPOOL dir and emits a manifest of relative paths; the PARENT then
    # re-stages each spooled object under the server-pinned
    # ``orgs/<org>/staging/<run>/`` prefix and verifies it (the §5 producer/
    # verifier split — a producer can only name relative paths; the server pins
    # the prefix).  The spool dir is private to this run and cleaned up after.
    spool_dir = tempfile.mkdtemp(prefix="nubi-pystage-")

    # ── B4: pre-fetch artifact bytes into the spool dir ───────────────────────
    # The subprocess cannot call the parent's StorageClient directly.  We
    # pre-download every ArtifactHandle found in ctx.inputs and write the raw
    # bytes into ``<spool_dir>/artifacts/<artifact_id>`` so the subprocess can
    # read them via ``ctx.get_artifact(handle)``.  Handles carry their artifact_id;
    # the subprocess reads the spool file by id.  Only handles belonging to the
    # executing org are materialised (isolation).
    artifact_spool_dir = os.path.join(spool_dir, "artifacts")
    os.makedirs(artifact_spool_dir, exist_ok=True)

    def _pre_fetch_artifacts() -> None:
        """Best-effort: download artifact bytes from ctx.inputs into the spool.

        SECURITY: the parent verifies the HMAC envelope and writes only the
        inner payload (already-verified bytes) to the spool file.  The
        subprocess therefore deserialises clean bytes and never sees the raw
        signed envelope — closing the deserialization gap where the subprocess
        would otherwise pickle.loads an unverified blob.
        """
        try:
            from app.flows.artifacts import (  # noqa: PLC0415
                get_artifact_store,
                is_handle,
                is_valid_artifact_id,
                _hmac_verify_and_strip,
            )
        except Exception:
            return
        art_store = getattr(ctx, "_artifact_store", None) or get_artifact_store()
        executing_org = ctx.org_id or ""
        if not executing_org:
            return
        # Resolve the spool dir once so we can confine every dest under it.
        spool_root = os.path.realpath(artifact_spool_dir)
        # Scan all values in ctx.inputs for ArtifactHandles.
        for _result in ctx.inputs.values():
            if not isinstance(_result, dict):
                continue
            # The result itself may be a handle, or contain handles as top-level values.
            candidates = [_result] + list(_result.values())
            for cand in candidates:
                if not is_handle(cand):
                    continue
                if str(cand.get("org_id", "")) != str(executing_org):
                    continue  # never materialise cross-org artifacts
                artifact_id = cand.get("artifact_id", "")
                if not artifact_id:
                    continue
                # SECURITY (path traversal / cross-org): a real artifact_id is
                # always a bare uuid4.  A writer-crafted id like
                # ``../../other_org/artifacts/<uuid>`` (with this org's org_id
                # on the handle) would pass the org check above and then both
                # (a) normalise to another org's storage key on a file:// store
                # and (b) escape the spool dir via os.path.join.  Reject any id
                # that is not a bare UUID before it touches a key or a path.
                if not is_valid_artifact_id(artifact_id):
                    continue
                dest = os.path.join(artifact_spool_dir, artifact_id)
                # Defence in depth: confirm the resolved dest stays under the
                # spool root even if the UUID check above is ever loosened.
                dest_real = os.path.realpath(dest)
                if dest_real != spool_root and not dest_real.startswith(spool_root + os.sep):
                    continue  # would escape the spool dir — refuse
                if os.path.exists(dest):
                    continue
                try:
                    signed_blob = art_store.download(artifact_id, executing_org)
                    # Verify HMAC and strip the envelope BEFORE writing to the
                    # spool so the subprocess only ever reads already-verified
                    # inner payload bytes.  Verification is BOUND to the
                    # executing org so a blob signed for another org (even via
                    # a key-normalisation trick) cannot verify here.  A
                    # tampered/unsigned/cross-org blob raises ValueError; we
                    # catch it and skip (subprocess will raise FileNotFoundError
                    # on access — safe fail-closed).
                    inner_payload = _hmac_verify_and_strip(
                        signed_blob, org_id=str(executing_org)
                    )
                    with open(dest, "wb") as fh:
                        fh.write(inner_payload)
                except Exception:  # noqa: BLE001
                    pass  # artifact may not exist yet / HMAC failed; subprocess will raise on access

    _pre_fetch_artifacts()

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
        # Incremental watermark (design §6.1): the stored ISO mark the runtime
        # read before this task ran; the cell may advance it via its return.
        watermark_json = json.dumps(ctx.watermark)
        spool_dir_json = json.dumps(spool_dir)
        # B4: artifact context
        artifact_spool_dir_json = json.dumps(artifact_spool_dir)
        artifact_org_id_json = json.dumps(ctx.org_id or "")
        artifact_run_id_json = json.dumps(ctx.run_id or "")
        # FIX [MED resource]: mirror the parent-process size cap in the subprocess.
        from app.flows.artifacts import _ARTIFACT_MAX_BYTES as _parent_max_bytes  # noqa: PLC0415
        artifact_max_bytes_json = json.dumps(_parent_max_bytes)
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
        # Incremental watermark (design §6.1) — read-only in the cell; advance it
        # by returning {{"watermark": "<iso>"}} (runtime persists on success only).
        watermark = _json.loads({watermark_json!r})

        # ── ctx.staging writer (design §6.2) ─────────────────────────────────
        # `staging.write(df_or_batches, "orders/", format="parquet") -> manifest`
        # In-subprocess shim: encodes a pandas DataFrame OR an iterable of
        # pyarrow RecordBatches to Parquet and SPOOLS it to a local dir; the
        # parent re-stages it under the server-pinned run prefix and verifies it.
        # User code names only RELATIVE sub-paths — it cannot escape the prefix.
        import hashlib as _hashlib
        import os as _os
        _STAGE_SPOOL_DIR = _json.loads({spool_dir_json!r})
        _staged_manifest = {{"files": [], "row_counts": {{}}}}

        def _sanitise_rel(rel):
            # Strip any ``..`` / absolute components; mirrors StagingArea._key so
            # the parent and child agree on the pinned, escape-proof key shape.
            rel = str(rel).strip().lstrip("/")
            parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
            return "/".join(parts)

        class _StagingWriter:
            _seq = 0
            def write(self, df_or_batches, dest="", format="parquet"):
                import pyarrow as _pa
                import pyarrow.parquet as _pq
                import io as _io
                fmt = str(format or "parquet").lower()
                if fmt != "parquet":
                    raise ValueError("ctx.staging.write only supports format='parquet'")
                # Accept a pandas DataFrame OR an iterable of pyarrow RecordBatches.
                if _pd is not None and isinstance(df_or_batches, _pd.DataFrame):
                    table = _pa.Table.from_pandas(df_or_batches, preserve_index=False)
                elif isinstance(df_or_batches, _pa.Table):
                    table = df_or_batches
                elif isinstance(df_or_batches, _pa.RecordBatch):
                    table = _pa.Table.from_batches([df_or_batches])
                elif isinstance(df_or_batches, (list, tuple)) and df_or_batches and \\
                        all(isinstance(b, _pa.RecordBatch) for b in df_or_batches):
                    table = _pa.Table.from_batches(list(df_or_batches))
                elif isinstance(df_or_batches, (list, tuple)):
                    # A list of row dicts (the templates' default) → table.
                    table = _pa.Table.from_pylist(list(df_or_batches))
                else:
                    # Any other iterable of RecordBatches.
                    table = _pa.Table.from_batches(list(df_or_batches))
                _buf = _io.BytesIO()
                _pq.write_table(table, _buf)
                data = _buf.getvalue()
                # Build a stable relative path under the requested dest folder.
                dest_rel = _sanitise_rel(dest)
                _StagingWriter._seq += 1
                leaf = "part-%05d.parquet" % _StagingWriter._seq
                rel = (dest_rel + "/" + leaf) if dest_rel else leaf
                # Spool to a local file (parent re-stages it under the run prefix).
                local_path = _os.path.join(_STAGE_SPOOL_DIR, "spool-%05d.parquet" % _StagingWriter._seq)
                with open(local_path, "wb") as _fh:
                    _fh.write(data)
                entry = {{
                    "path": rel,
                    "size": len(data),
                    "sha256": _hashlib.sha256(data).hexdigest(),
                    "_spool": local_path,
                }}
                nrows = int(table.num_rows)
                _staged_manifest["files"].append(entry)
                _staged_manifest["row_counts"][rel] = nrows
                # The manifest handed back to user code mirrors the §5 shape
                # ({{files:[{{path,size,sha256}}], row_counts}}) WITHOUT the
                # private _spool field, so it is identical to what the server
                # records after verification.
                return {{
                    "files": [{{"path": rel, "size": entry["size"], "sha256": entry["sha256"]}}],
                    "row_counts": {{rel: nrows}},
                }}

        staging = _StagingWriter()

        # ── B4: ctx.put_artifact / ctx.get_artifact ───────────────────────
        # ctx.put_artifact(obj, name, kind) serialises *obj* into the spool dir
        # under a deterministic filename and returns a pending ArtifactHandle.
        # The PARENT process picks up the spool files after the subprocess exits,
        # uploads them to the object store, and replaces the handles in the result
        # with real URIs.
        # ctx.get_artifact(handle) reads the pre-fetched bytes that the PARENT
        # downloaded from the object store into the spool dir before launching
        # the subprocess (``<spool_dir>/artifacts/<artifact_id>``).
        import uuid as _uuid_mod
        import pickle as _pickle
        import io as _artifact_io

        _ARTIFACT_SPOOL_DIR = _json.loads({artifact_spool_dir_json!r})
        _ARTIFACT_ORG_ID = _json.loads({artifact_org_id_json!r})
        _ARTIFACT_RUN_ID = _json.loads({artifact_run_id_json!r})
        _ARTIFACT_MAX_BYTES = _json.loads({artifact_max_bytes_json!r})
        _artifact_handles = {{}}  # name → handle dict (for __ARTIFACT_HANDLES__ sentinel)

        class _ArtifactCtx:
            def _put_artifact(self, obj, name=None, kind="pickle", meta=None):
                # Serialise obj to a spool file.
                import hashlib as _ahash
                artifact_id = str(_uuid_mod.uuid4())
                # Serialise according to kind.
                if kind == "pickle":
                    data = _pickle.dumps(obj, protocol=_pickle.HIGHEST_PROTOCOL)
                elif kind == "joblib":
                    try:
                        import joblib as _jbl
                    except ImportError as _e:
                        raise ImportError("joblib is required for kind='joblib'") from _e
                    _buf = _artifact_io.BytesIO()
                    _jbl.dump(obj, _buf)
                    data = _buf.getvalue()
                elif kind == "bytes":
                    data = bytes(obj)
                elif kind == "json":
                    data = _json.dumps(obj).encode("utf-8")
                else:
                    raise ValueError(f"Unsupported artifact kind {{kind!r}}")
                # FIX [MED resource]: enforce size cap (mirrors parent process).
                if _ARTIFACT_MAX_BYTES > 0 and len(data) > _ARTIFACT_MAX_BYTES:
                    raise ValueError(
                        f"Artifact size {{len(data):,}} bytes exceeds the maximum allowed "
                        f"{{_ARTIFACT_MAX_BYTES:,}} bytes (NUBI_ARTIFACT_MAX_BYTES). "
                        "Reduce the artifact size or raise the limit."
                    )
                # Write to spool.
                spool_path = _os.path.join(_ARTIFACT_SPOOL_DIR, artifact_id)
                _os.makedirs(_ARTIFACT_SPOOL_DIR, exist_ok=True)
                with open(spool_path, "wb") as _fh:
                    _fh.write(data)
                handle = {{
                    "artifact_id": artifact_id,
                    "kind": kind,
                    "uri": None,  # parent will fill in the real URI
                    "org_id": _ARTIFACT_ORG_ID,
                    "produced_by_run": _ARTIFACT_RUN_ID or None,
                    "name": name,
                    "meta": meta,
                    "__type__": "artifact_handle",
                    "__spool__": spool_path,  # private field for parent
                }}
                _artifact_handles[artifact_id] = handle
                return handle

            def _get_artifact(self, handle):
                # Validate.
                if not (isinstance(handle, dict) and handle.get("__type__") == "artifact_handle"):
                    raise ValueError("get_artifact() requires a valid ArtifactHandle dict.")
                handle_org = handle.get("org_id", "")
                if handle_org and _ARTIFACT_ORG_ID and str(handle_org) != str(_ARTIFACT_ORG_ID):
                    raise PermissionError(
                        f"Artifact org isolation violation: handle org {{handle_org!r}} "
                        f"but executing org {{_ARTIFACT_ORG_ID!r}}."
                    )
                artifact_id = handle.get("artifact_id", "")
                kind = handle.get("kind", "pickle")
                # Look for pre-fetched bytes in the artifacts sub-dir.
                spool_path = _os.path.join(_ARTIFACT_SPOOL_DIR, artifact_id)
                if not _os.path.exists(spool_path):
                    raise FileNotFoundError(
                        f"Artifact {{artifact_id!r}} not found in the spool dir. "
                        "Ensure the upstream cell returned the ArtifactHandle in its result."
                    )
                with open(spool_path, "rb") as _fh:
                    data = _fh.read()
                if kind == "pickle":
                    return _pickle.loads(data)
                elif kind == "joblib":
                    try:
                        import joblib as _jbl
                    except ImportError as _e:
                        raise ImportError("joblib is required for kind='joblib'") from _e
                    return _jbl.load(_artifact_io.BytesIO(data))
                elif kind == "bytes":
                    return data
                elif kind == "json":
                    return _json.loads(data.decode("utf-8"))
                else:
                    raise ValueError(f"Unsupported artifact kind {{kind!r}}")

        _art_ctx = _ArtifactCtx()

        class ctx:
            put_artifact = staticmethod(_art_ctx._put_artifact)
            get_artifact = staticmethod(_art_ctx._get_artifact)
            staging = staging  # keep existing staging writer accessible via ctx too

        # set_var (A5): a python cell can publish a variable for LATER cells in
        # the same run. Collected into `_set_vars`, emitted under the reserved
        # `__set_vars__` key (never collides with user `result` keys). persist=True
        # also flushes to the long-term store (handled by the runtime).
        _set_vars = {{}}
        def set_var(name, value, persist=False):
            _set_vars[str(name)] = {{"value": value, "persist": bool(persist)}}
            vars[str(name)] = value  # visible to the rest of THIS cell too

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

        if _set_vars and isinstance(_out, dict):
            _out["__set_vars__"] = _set_vars

        # Emit the staging manifest (incl. private _spool local paths) on a
        # separate sentinel line so the PARENT can re-stage + verify the spooled
        # Parquet under the server-pinned run prefix (design §5/§6.2).  Never put
        # the _spool paths into the user-visible result.
        if _staged_manifest["files"]:
            print("__STAGING_MANIFEST__:" + _json.dumps(_staged_manifest, default=str))

        # B4: emit artifact handles so the PARENT can upload the spooled bytes
        # and replace the pending handles with real URIs in the task result.
        # Emitted BEFORE __FLOW_RESULT__ so the parent can patch handles in _out.
        if _artifact_handles:
            print("__ARTIFACT_HANDLES__:" + _json.dumps(list(_artifact_handles.values()), default=str))

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
        _cleanup_spool(spool_dir)
        raise subprocess.TimeoutExpired(argv, timeout_s)

    stdout_text = run.stdout.decode("utf-8", errors="replace")
    stderr_text = run.stderr.decode("utf-8", errors="replace")

    # Collect stdout lines (excluding the tagged result + staging sentinels).
    stdout_lines: list[str] = []
    task_result: dict[str, Any] = {}
    staged_manifest_raw: dict[str, Any] | None = None
    artifact_handles_raw: list[dict[str, Any]] | None = None
    for line in stdout_text.splitlines():
        if line.startswith("__FLOW_RESULT__:"):
            try:
                task_result = json.loads(line[len("__FLOW_RESULT__:"):])
            except Exception:  # noqa: BLE001
                task_result = {"raw": line}
        elif line.startswith("__STAGING_MANIFEST__:"):
            try:
                staged_manifest_raw = json.loads(line[len("__STAGING_MANIFEST__:"):])
            except Exception:  # noqa: BLE001
                staged_manifest_raw = None
        elif line.startswith("__ARTIFACT_HANDLES__:"):
            try:
                artifact_handles_raw = json.loads(line[len("__ARTIFACT_HANDLES__:"):])
            except Exception:  # noqa: BLE001
                artifact_handles_raw = None
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
        _cleanup_spool(spool_dir)
        raise RuntimeError(f"Python task failed (exit {run.returncode}): {stderr[:500]}")

    # ── B4: Upload spooled artifacts and patch handles in task_result ─────────
    # The subprocess serialised artifacts to ``<spool_dir>/artifacts/<artifact_id>``
    # and emitted their pending handles (uri=None).  The PARENT now uploads each
    # spool file to the artifact store and patches the handle in task_result with
    # the real URI (so downstream cells can call ctx.get_artifact(handle)).
    try:
        if artifact_handles_raw:
            _promote_python_artifacts(artifact_handles_raw, ctx, task_result)
    except Exception:  # noqa: BLE001 — artifact upload must not break a flow run
        pass

    # ── Promote spooled Parquet into the server-pinned staging prefix ──────────
    # The subprocess wrote Parquet into the private spool dir and reported a
    # manifest of relative paths.  The PARENT re-stages each spooled object under
    # ``orgs/<org>/staging/<run>/`` (pinned from trusted ids — user code can never
    # escape it), verifies size + sha256, and (optionally) loads it into a
    # declared target via the loader layer.  This is the §5 producer/verifier
    # split applied to the python task kind.
    try:
        if staged_manifest_raw and staged_manifest_raw.get("files"):
            staging_result = _promote_python_staging(
                staged_manifest_raw, ctx, config, claims
            )
            if staging_result is not None:
                task_result["staging"] = staging_result
    finally:
        _cleanup_spool(spool_dir)

    # Attach captured stdout lines as metadata so the executor can extract them.
    task_result["_stdout_lines"] = stdout_lines
    return task_result


def _cleanup_spool(spool_dir: str) -> None:
    """Best-effort delete of the python-task staging spool dir."""
    import shutil  # noqa: PLC0415

    try:
        shutil.rmtree(spool_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _promote_python_artifacts(
    handles_raw: list[dict[str, Any]],
    ctx: Any,
    task_result: dict[str, Any],
) -> None:
    """Upload spooled artifact bytes and patch handles in *task_result* with real URIs.

    For each pending handle in *handles_raw*:
    1. Reads the spool file from ``handle["__spool__"]``.
    2. Uploads to the artifact store (org-namespaced).
    3. Replaces the handle's ``uri`` with the real URI and strips the private
       ``__spool__`` field.
    4. Patches any occurrence of the same ``artifact_id`` in *task_result* so
       downstream cells receive a complete, real handle.

    Best-effort: errors on individual handles are logged and skipped so one bad
    artifact does not kill a cell that produced other valid outputs.
    """
    import logging as _logging  # noqa: PLC0415

    _log = _logging.getLogger(__name__)

    try:
        from app.flows.artifacts import (  # noqa: PLC0415
            get_artifact_store,
            is_valid_artifact_id,
            _hmac_sign,
        )
    except Exception:
        return

    art_store = getattr(ctx, "_artifact_store", None) or get_artifact_store()
    org_id: str = str(getattr(ctx, "org_id", "") or "")
    if not org_id:
        return

    for handle in handles_raw:
        artifact_id = handle.get("artifact_id", "")
        spool_path = handle.get("__spool__", "")
        kind = handle.get("kind", "pickle")
        name = handle.get("name")
        meta = handle.get("meta")

        if not artifact_id or not spool_path:
            continue

        # SECURITY: the artifact_id is generated by the subprocess (uuid4) but
        # is attacker-influenceable via a crafted result dict.  Reject anything
        # that is not a bare UUID before it is used as a storage key.
        if not is_valid_artifact_id(artifact_id):
            _log.warning(
                "Skipping artifact with non-canonical id %r (expected bare UUID).",
                artifact_id,
            )
            continue

        try:
            if not __import__("os").path.exists(spool_path):
                continue
            with open(spool_path, "rb") as fh:
                data = fh.read()
            # Sign the payload before uploading so the HMAC envelope is
            # present in the store — _pre_fetch_artifacts verifies it before
            # writing to the spool for downstream subprocess cells.  The
            # signature is BOUND to org_id so it can only be verified/fetched
            # by the owning org (closes the cross-org HMAC bypass).
            signed_data = _hmac_sign(data, org_id=org_id)
            uri = art_store.upload(artifact_id, org_id, signed_data)
        except Exception as exc:  # noqa: BLE001
            _log.debug("Failed to upload artifact %s: %s", artifact_id, exc)
            continue

        # Build the final (public) handle — no __spool__ field.
        final_handle: dict[str, Any] = {
            "artifact_id": artifact_id,
            "kind": kind,
            "uri": uri,
            "org_id": org_id,
            "produced_by_run": handle.get("produced_by_run"),
            "name": name,
            "meta": meta,
            "__type__": "artifact_handle",
        }

        # Patch task_result: replace any pending handle that has the same artifact_id.
        _patch_artifact_handle(task_result, artifact_id, final_handle)


def _patch_artifact_handle(
    obj: Any,
    artifact_id: str,
    final_handle: dict[str, Any],
    _depth: int = 0,
) -> None:
    """Recursively replace a pending artifact handle in *obj* with *final_handle*.

    Mutates *obj* in place.  Only traverses dicts (no lists inside lists) to
    avoid unbounded recursion — task results are shallow dicts in practice.
    Depth-limited to 3 to be safe.
    """
    if _depth > 3 or not isinstance(obj, dict):
        return
    for k, v in list(obj.items()):
        if (
            isinstance(v, dict)
            and v.get("__type__") == "artifact_handle"
            and v.get("artifact_id") == artifact_id
        ):
            obj[k] = final_handle
        elif isinstance(v, dict):
            _patch_artifact_handle(v, artifact_id, final_handle, _depth + 1)


def _promote_python_staging(
    staged_manifest_raw: dict[str, Any],
    ctx: "TaskContext",
    config: dict[str, Any],
    claims: dict[str, Any],
) -> dict[str, Any] | None:
    """Re-stage spooled Parquet under the pinned prefix, verify, optional load.

    Implements the parent half of ``ctx.staging`` (design §6.2): the subprocess
    spooled Parquet to a local dir and reported ``staged_manifest_raw`` (each
    file carrying a private ``_spool`` local path).  Here we:

    1. Resolve the server-pinned :class:`StagingArea` for ``(org, run)`` — the
       prefix comes from trusted ids, so a producer can never escape it.
    2. Write each spooled object into staging via its RELATIVE path and build the
       manifest from the bytes the server actually wrote (not the producer's
       claimed sizes/hashes), then verify (the §5 trust gate).
    3. If the python task config declares a ``target`` (same shape as
       file_ingest's ``target``: ``{connector_id, object}``), run the loader to
       promote/load the staged manifest into that connector.

    Returns a dict ``{prefix, files, row_counts, total_rows, load?}`` describing
    the SERVER-recorded staging, or ``None`` when no staging store is configured
    (degrade — the spooled bytes are simply dropped, same as file_ingest).
    """
    import uuid  # noqa: PLC0415

    from app.lakehouse.managed import get_staging_area  # noqa: PLC0415

    org_id = ctx.org_id or (claims or {}).get("org_id") or ""
    run_id = getattr(ctx, "run_id", None) or str(uuid.uuid4())
    if not org_id:
        # No org context → cannot pin a prefix; skip staging (back-compat).
        return None

    staging = get_staging_area(org_id, run_id)
    if staging is None:
        return None

    entries = []
    row_counts_in: dict[str, int] = dict(staged_manifest_raw.get("row_counts") or {})
    row_counts: dict[str, int] = {}
    for f in staged_manifest_raw.get("files") or []:
        rel = str(f.get("path") or "").strip()
        spool = f.get("_spool")
        if not rel or not spool:
            continue
        with open(spool, "rb") as fh:
            data = fh.read()
        # The server pins the prefix + re-computes size/sha256 from the bytes IT
        # wrote (write_bytes returns the authoritative ManifestEntry); we never
        # trust the producer's claimed digest.
        entry = staging.write_bytes(data, rel)
        entries.append(entry)
        row_counts[entry.path] = int(row_counts_in.get(rel, 0))

    if not entries:
        return None

    manifest = staging.build_manifest(entries, row_counts)
    # Verify against the just-written bytes (the §5 gate — defends downstream
    # loads even though the parent wrote them, keeping one code path).
    staging.verify(manifest)

    out: dict[str, Any] = {
        "prefix": staging.prefix,
        "files": [e.to_dict() for e in entries],
        "row_counts": row_counts,
        "total_rows": manifest.total_rows,
    }

    # ── Optional target: auto-promote/load via the Phase-1 loader layer ───────
    target = config.get("target")
    if isinstance(target, dict) and target.get("connector_id") and target.get("object"):
        from app.flows.handlers.file_ingest import _bind_promote, _resolve_target  # noqa: PLC0415
        from app.flows.loaders import load_staged  # noqa: PLC0415

        load_target = _resolve_target(
            str(target["connector_id"]), str(target["object"]), org_id
        )
        _bind_promote(load_target, staging, manifest)
        out["load"] = load_staged(staging, manifest, load_target, verify=False)

    return out


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


async def _handle_snapshot_refresh(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Re-run the board collector and rewrite a frozen snapshot artifact in place.

    Delegates to ``app.embedding.snapshot.snapshot_refresh_handler``.  A daily /
    cron flow keeps a board's frozen DuckDB sidecar fresh by rewriting the SAME
    artifact URI each tick.  The captured RLS view is declared in the task
    ``config['policies']`` (a scheduled tick carries no user JWT) and is exposed
    to ALL artifact holders — see ``app.embedding.snapshot`` for the security note.

    Config keys
    -----------
    ``board_id`` (required), ``snapshot_id`` (required), ``org_id`` (optional —
    falls back to ``ctx.org_id`` / ``claims['org_id']``), ``policies`` (optional
    — the policy view to capture).

    Returns
    -------
    dict
        ``{board_id, snapshot_id, artifact_uri, refreshed_at, tables}``.
    """
    from app.embedding.snapshot import snapshot_refresh_handler  # noqa: PLC0415

    return await snapshot_refresh_handler(config, ctx, claims)


def _handle_file_ingest(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Ingest files from a file connector into a target connector (design §3).

    Delegates to ``app.flows.handlers.file_ingest.handle``.  Symmetric to
    ``bucket_load`` but in the INGEST direction: lists files from the source
    connector's file interface (sftp/ftp/bucket), stages each as Parquet,
    verifies the manifest, and loads into the target via the loader layer.

    Returns ``{files_ingested, rows_ingested, strategy, manifest, new_watermark,
    post_action, ...}``.  See ``app.flows.handlers.file_ingest`` for the schema.
    """
    from app.flows.handlers.file_ingest import handle  # noqa: PLC0415

    return handle(config, ctx, claims)


def _handle_connector_write(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Write an upstream flow result into any target connector (design §4).

    Delegates to ``app.flows.handlers.connector_write.handle``.  The write-side
    sibling of ``file_ingest``/``bucket_load``: stages the upstream task result
    as Parquet, verifies the manifest, and loads it into the target connector via
    the SAME loader layer (promote / bulk / stream).

    Returns ``{rows_written, strategy, mode, target_object, manifest,
    final_uris}``.  See ``app.flows.handlers.connector_write`` for the schema.
    """
    from app.flows.handlers.connector_write import handle  # noqa: PLC0415

    return handle(config, ctx, claims)


# ---------------------------------------------------------------------------
# Module-level singleton / provider
# ---------------------------------------------------------------------------

_registry: TaskKindRegistry | None = None


def get_task_kind_registry() -> TaskKindRegistry:
    """Return (or lazily create) the module-level ``TaskKindRegistry`` singleton.

    Pre-populates with built-in handlers: ``query``, ``python``, ``agent``,
    ``materialize``, ``noop``, ``bucket_load``, ``preagg_refresh``,
    ``snapshot_refresh``, ``map``, ``branch``, ``map_collect``.
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
    registry.register("file_ingest", _handle_file_ingest)
    registry.register("connector_write", _handle_connector_write)
    registry.register("preagg_refresh", _handle_preagg_refresh)
    registry.register("snapshot_refresh", _handle_snapshot_refresh)

    from app.flows.handlers.map import handle_map  # noqa: PLC0415
    from app.flows.handlers.branch import handle_branch  # noqa: PLC0415
    from app.flows.handlers.map_collect import handle_map_collect  # noqa: PLC0415
    from app.flows.handlers.report_send import handle as _handle_report_send  # noqa: PLC0415

    registry.register("map", handle_map)
    registry.register("branch", handle_branch)
    registry.register("map_collect", handle_map_collect)
    registry.register("report_send", _handle_report_send)
