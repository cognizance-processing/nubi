"""Canonical Flow SPEC — shared format for the DAG builder and the LLM.

Public API
----------
FlowParam
    A flow-level parameter declaration (name, type, default, required).
TaskUi
    Canvas position for a task node in the React Flow builder.
TaskSpec
    A single task in the flow DAG (key, kind, needs, config, retries, …).
    Cell-specific additions (all optional, backward-compatible):
      cell_type       — user-facing label: 'sql' | 'python' | 'markdown' | None.
      execution_mode  — per-cell override: 'preview' | 'durable' | None (inherit).
      freshness_sla_s — stale-alert threshold in seconds (0 = no alert).
    Cell config keys (stored in the existing ``config`` dict):
      source_dialect  — SQL dialect the cell was authored in.
      datastore_id    — BYO warehouse connector id (absent = demo DuckDB).
      preview_limit   — row cap for interactive/preview runs (default 500).
      use_remote_kernel — route Python cell to E2B/Modal in durable mode.
FlowSpec
    The complete flow specification document (version, name, params, tasks,
    runtime_config).
    runtime_config  — top-level runtime hints dict (not inside cells).
    NOTE: the spec deliberately carries NO ``env`` field — the execution
    environment is resolved at trigger time (explicit override → the flow's
    project default environment).  A legacy ``env`` key in incoming spec
    dicts is ignored (stripped by validation), never an error.

validate_flow_spec(data) -> (FlowSpec | None, list[str])
    Parse a raw dict into a FlowSpec, collecting all validation issues.
    Hard errors (Pydantic failure, duplicate keys, missing dep, cycle,
    missing kind-required config fields) cause the function to treat the
    spec as invalid.  Soft warnings (unknown query_id in registry) are
    appended to the issues list but do not block validity.

flow_spec_is_valid(issues) -> bool
    Return True when the issues list contains no hard-error markers.
    Soft warnings are prefixed with "[warn]"; everything else is hard.

flow_spec_json_schema() -> dict
    Return the JSON Schema for FlowSpec (for grounding the LLM author tool).

Supported task kinds
--------------------
- ``query``         — run a SQL query against a registered data source.
- ``python``        — run an arbitrary Python code snippet.
- ``agent``         — run an LLM-agent step.
- ``materialize``   — merge upstream results into a DuckDB materialization.
- ``noop``          — no-operation (useful as a join/synchronisation point).
- ``bucket_load``   — upload upstream task result to a storage bucket.
- ``file_ingest``   — ingest files from a file connector (sftp/ftp/bucket) into
                      any target connector via staging + the loader layer.
- ``preagg_refresh``— refresh a pre-aggregated rollup for an org.
- ``map``           — fan-out over an iterable; body is a nested sub-DAG.
- ``branch``        — conditional routing; evaluates conditions and activates
                      matching downstream tasks.
- ``map_collect``   — collector handler for map fan-in; returns
                      ``{items: [...], item_count: N}``.

Security notes
--------------
- No HTML rendering in this module — purely a data-validation layer.
- Config dicts are stored verbatim; callers (executor) are responsible
  for sanitising values before use.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic v2 models
# ---------------------------------------------------------------------------

_WARN_PREFIX = "[warn]"


class FlowParam(BaseModel):
    """A flow-level parameter declaration.

    Parameters can be referenced inside task ``config`` strings as
    ``{{ params.<name> }}``.

    Attributes
    ----------
    name:
        Unique parameter name within this flow (e.g. ``"region"``).
    type:
        Value type — one of ``'text'``, ``'number'``, ``'date'``,
        ``'daterange'``, ``'select'``, or ``'multiselect'``.
    default:
        Optional default value.
    required:
        Whether callers must supply this parameter at run time.
    """

    name: str = Field(min_length=1, description="Unique parameter name.")
    type: Literal["text", "number", "date", "daterange", "select", "multiselect"] = (
        Field(description="Parameter value type.")
    )
    default: Any = Field(default=None, description="Default value.")
    required: bool = Field(default=False, description="Whether the param is required.")


class MaterializedConfig(BaseModel):
    """SQLMesh-style materialization config for a ``materialize`` task.

    Stored nested under ``config['materialized']``.  Absent ⇒ the task behaves
    as today (``kind="view"`` — no persistence).

    Attributes
    ----------
    kind:
        ``'view'`` (no persistence, current behaviour), ``'full'`` (overwrite
        the target each run), or ``'incremental'`` (process only rows newer than
        the stored watermark; append or upsert into the target).
    target:
        Logical target path WITHOUT the env prefix.  Required when
        ``kind != 'view'``.  The runtime joins ``<base_uri>/<env>/<target>`` to
        form the physical object-storage URI so dev/prod never clobber.
    time_column:
        Column carrying the row timestamp.  Required when ``kind ==
        'incremental'`` — only rows where ``time_column > watermark`` (minus an
        optional ``lookback``) are processed.
    unique_key:
        Optional list of columns forming the merge key.  When present the
        incremental write upserts (delete-then-insert) on these columns; when
        absent rows are appended.
    lookback:
        Optional human duration (e.g. ``'3 days'``, ``'12h'``) re-processed
        below the watermark to absorb late-arriving rows.
    base_uri:
        Optional per-task override of the materialization base URI.
    """

    kind: Literal["view", "full", "incremental"] = Field(
        default="view",
        description="Materialization kind (view/full/incremental).",
    )
    target: str | None = Field(
        default=None,
        description="Logical target path (no env prefix); required for full/incremental.",
    )
    time_column: str | None = Field(
        default=None,
        description="Timestamp column for incremental watermarking.",
    )
    unique_key: list[str] = Field(
        default_factory=list,
        description="Merge key columns (present ⇒ upsert; absent ⇒ append).",
    )
    lookback: str | None = Field(
        default=None,
        description="Optional lookback window re-processed below the watermark.",
    )
    base_uri: str | None = Field(
        default=None,
        description="Optional per-task override of the materialization base URI.",
    )


class TaskUi(BaseModel):
    """Canvas position for a task node in the React Flow builder.

    Attributes
    ----------
    x:
        Horizontal position in pixels on the builder canvas.
    y:
        Vertical position in pixels on the builder canvas.
    """

    x: float = Field(default=0.0, description="Canvas x position.")
    y: float = Field(default=0.0, description="Canvas y position.")


class TaskSpec(BaseModel):
    """A single task in the flow DAG.

    Attributes
    ----------
    key:
        Unique slug within this flow (e.g. ``"pull"``).  Used as the
        canonical task identifier in ``needs`` lists and ``inputs`` maps.
    kind:
        Execution kind — ``'query'``, ``'python'``, ``'agent'``,
        ``'materialize'``, ``'noop'``, ``'bucket_load'``,
        ``'preagg_refresh'``, ``'map'``, ``'branch'``, or ``'map_collect'``.
    needs:
        List of upstream task keys this task depends on.  An empty list
        means the task is a root (no dependencies).
    config:
        Kind-specific configuration dict.  Required sub-fields per kind:

        - ``query``       → ``query_id`` OR ``sql`` (at least one required).
        - ``python``      → ``code`` (required).
        - ``agent``       → ``prompt`` (required).
        - ``materialize`` → ``combine_sql`` (required).  Merges the upstream
          source-task results in DuckDB and writes them to a materialized
          single-source dataset (see ``app/flows/materialize.py``).  Other
          config keys: ``sources`` (list of source ``key`` strings to register
          as DuckDB tables), ``rls_keys`` (columns that MUST survive the merge
          so the planner can inject ``WHERE <key> = <claim>`` at read time),
          ``database`` (abs path to the DuckDB file to write), ``table`` (target
          table name, default ``blend``), ``datastore_id`` / ``query_id`` (the
          pre-created rows the result is exposed through).
        - ``noop``        → no required fields.
        - ``bucket_load`` → ``uri`` (required destination URI) AND ``source``
          (required — key of an upstream task whose result provides the data).
          Optional: ``format`` (``'csv'``|``'json'``|``'ndjson'``|``'parquet'``,
          default ``'csv'``), ``mode`` (``'overwrite'``|``'append'``, default
          ``'overwrite'``), ``secret``.
        - ``file_ingest`` → ``source`` (required — ``{connector_id, path}``) AND
          ``target`` (required — ``{connector_id, object}``).  Optional:
          ``format`` (``csv``|``json``|``ndjson``|``parquet``|``zip``|``auto``,
          default ``auto``), ``inner_format`` (zip entry format, default
          ``csv``), ``mode`` (``append``|``overwrite``|``merge``, default
          ``append``), ``incremental`` (``{strategy: mtime|filename|none}``),
          ``post_action`` (``none``|``move:<dir>``|``delete``).
        - ``map``         → ``item_expr`` (required — template expression
          resolving to an iterable at runtime) AND ``body`` (required — non-empty
          list of TaskSpec dicts forming the per-item sub-DAG).  Optional:
          ``item_var`` (default ``"item"``), ``max_concurrency`` (default 0 =
          unlimited), ``max_map_size`` (default 1000), ``collect_key`` (which
          body task key's result is collected; defaults to the last body task).
        - ``branch``      → ``conditions`` (required — non-empty ordered list of
          ``{when: <template_bool_expr>, next: [task_key, ...]}`` dicts; first
          match wins).  Optional: ``default`` (list of task keys to activate when
          no condition matches; empty list = no-op on unmatched path per Q1).
        - ``map_collect`` → no required fields (internal collector handler).
        - ``preagg_refresh`` → ``org_id`` (required).
        - ``noop``        → no required fields.
    retries:
        Number of retry attempts after the first failure (``0`` = no retry).
    retry_backoff_s:
        Seconds to wait between retry attempts.
    timeout_s:
        Per-attempt timeout in seconds.  ``0`` means no timeout.
    cache_ttl_s:
        Cache duration in seconds.  ``0`` means no caching.  When ``> 0``,
        the engine memoises the result by a content-based ``cache_key``.
    ui:
        Builder canvas position.  Ignored by the execution engine.
    """

    key: str = Field(min_length=1, description="Unique task slug within this flow.")
    kind: Literal[
        "query",
        "python",
        "agent",
        "materialize",
        "noop",
        "bucket_load",
        "file_ingest",  # ingest files from a file connector into any target
        "preagg_refresh",
        "map",          # fan-out; config.body is a sub-DAG of TaskSpec dicts
        "branch",       # conditional routing; config.conditions list
        "map_collect",  # collector for map fan-in (internal / handler use)
    ] = Field(description="Execution kind.")
    needs: list[str] = Field(
        default_factory=list,
        description="Upstream task keys (DAG edges).",
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Kind-specific configuration dict.",
    )
    retries: int = Field(default=0, ge=0, description="Retry attempts on failure.")
    retry_backoff_s: int = Field(
        default=30, ge=0, description="Seconds between retry attempts."
    )
    timeout_s: int = Field(
        default=60, ge=0, description="Per-attempt timeout in seconds (0 = none)."
    )
    cache_ttl_s: int = Field(
        default=0, ge=0, description="Cache TTL in seconds (0 = no cache)."
    )
    ui: TaskUi = Field(
        default_factory=TaskUi,
        description="Builder canvas position (ignored by the engine).",
    )

    # ── Notebook / cell extensions (additive, all optional) ──────────────
    cell_type: Literal["sql", "python", "markdown"] | None = Field(
        default=None,
        description=(
            "User-facing cell label.  Maps to kind: sql→query, python→python, "
            "markdown→noop.  None for plain flow tasks (backward-compatible)."
        ),
    )
    execution_mode: Literal["preview", "durable"] | None = Field(
        default=None,
        description=(
            "Per-cell execution-mode override.  None = inherit from the "
            "parent notebook's execution_mode.  'preview' = interactive / "
            "sampled run.  'durable' = full work-pool run."
        ),
    )
    freshness_sla_s: int = Field(
        default=0,
        ge=0,
        description=(
            "Stale-alert threshold in seconds.  0 = no alert.  When > 0, "
            "the flow_tick emits a staleness event if the cell has not "
            "succeeded within this window."
        ),
    )


class FlowSpec(BaseModel):
    """Canonical flow specification — version 1.

    This is the single source of truth for both the React Flow DAG builder
    and the LLM authoring pipeline.

    Attributes
    ----------
    version:
        Schema version.  Currently ``1``.
    name:
        Human-readable flow name (e.g. ``"daily_revenue"``).
    params:
        Optional list of flow-level parameter declarations.
    tasks:
        Ordered list of tasks that form the DAG.  The execution engine
        derives the run order from the ``needs`` edges.
    """

    version: int = Field(default=1, ge=1, description="Schema version (currently 1).")
    name: str = Field(min_length=1, description="Human-readable flow name.")
    params: list[FlowParam] = Field(
        default_factory=list,
        description="Flow-level parameter declarations.",
    )
    tasks: list[TaskSpec] = Field(
        default_factory=list,
        description="Ordered list of tasks forming the DAG.",
    )
    runtime_config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Top-level runtime hints for the notebook envelope.  Not stored "
            "inside cells.  Keys are free-form; the notebook runtime consults "
            "this dict for settings such as interactive_row_limit and "
            "duckdb_memory_limit."
        ),
    )


# ---------------------------------------------------------------------------
# validate_flow_spec
# ---------------------------------------------------------------------------


def _validate_materialized_block(task: "TaskSpec", issues: list[str]) -> None:
    """Validate the optional nested ``config.materialized`` block (if present).

    Shared by the legacy ``materialize`` task AND ``query`` (SQL) cells — the
    block has IDENTICAL shape/checks for both: full/incremental require
    ``target``; incremental additionally requires ``time_column``.  Absent ⇒
    valid (no-op).
    """
    mat = task.config.get("materialized")
    if mat is None:
        return
    label = task.kind
    if not isinstance(mat, dict):
        issues.append(
            f"Task {task.key!r} ({label}): 'materialized' must be a nested object."
        )
        return
    try:
        mat_cfg = MaterializedConfig.model_validate(mat)
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError
        issues.append(
            f"Task {task.key!r} ({label}): invalid 'materialized' config: {exc}"
        )
        return
    if mat_cfg.kind in ("full", "incremental") and not mat_cfg.target:
        issues.append(
            f"Task {task.key!r} ({label}): 'materialized.target' is required "
            f"when kind={mat_cfg.kind!r}."
        )
    if mat_cfg.kind == "incremental" and not mat_cfg.time_column:
        issues.append(
            f"Task {task.key!r} ({label}): 'materialized.time_column' is "
            "required when kind='incremental'."
        )


def _validate_cell_config_blocks(task: "TaskSpec", issues: list[str]) -> None:
    """Validate the optional ``for_each`` / ``run_when`` cell config blocks.

    Additive (cells-not-kinds); absent ⇒ valid.

    - ``for_each`` must be an object carrying a non-empty string ``items``.
    - ``run_when`` must be a string.
    """
    cfg = task.config

    fe = cfg.get("for_each")
    if fe is not None:
        if not isinstance(fe, dict):
            issues.append(
                f"Task {task.key!r}: 'for_each' must be a nested object "
                "with an 'items' expression."
            )
        else:
            items = fe.get("items")
            if not items or not isinstance(items, str) or not items.strip():
                issues.append(
                    f"Task {task.key!r}: 'for_each.items' must be a non-empty "
                    "string expression resolving to a list."
                )
            var = fe.get("var")
            if var is not None and not isinstance(var, str):
                issues.append(
                    f"Task {task.key!r}: 'for_each.var' must be a string."
                )

    rw = cfg.get("run_when")
    if rw is not None and not isinstance(rw, str):
        issues.append(
            f"Task {task.key!r}: 'run_when' must be a string boolean expression."
        )


def validate_flow_spec(data: Any) -> tuple[FlowSpec | None, list[str]]:
    """Parse and validate a raw dict as a FlowSpec.

    Validation steps
    ----------------
    1. Pydantic model parse — field types, required fields, enum values.
    2. Task ``key`` uniqueness — duplicate keys are a hard error.
    3. Every ``needs`` entry references a declared task key — hard error.
    4. DAG is acyclic — topological sort; the offending cycle is reported
       as a hard error (e.g. ``"Cycle detected: a → b → a"``).
    5. Kind-specific ``config`` required fields — hard error:

       - ``query``       → ``query_id`` or ``sql`` must be present.
       - ``python``      → ``code`` must be present.
       - ``agent``       → ``prompt`` must be present.
       - ``materialize`` → ``combine_sql`` must be present.
       - ``noop``        → no requirements.
       - ``bucket_load`` → ``uri`` and ``source`` must both be present.
       - ``map``         → ``item_expr`` and ``body`` must be present;
         ``body`` is validated recursively as a sub-DAG; nested map nodes
         inside body are rejected; ``collect_key`` must reference a body key.
       - ``branch``      → ``conditions`` must be a non-empty list of
         ``{when, next}`` dicts.
       - ``map_collect`` → no requirements.
    5.5 Branch cross-reference post-pass (after the per-task loop):
       - Every key in ``conditions[i].next`` must be a declared task key.
       - Every key in ``default`` must be a declared task key.
       - Any task that lists a branch key in its ``needs`` must appear in at
         least one ``next`` or ``default`` list (unreachable-task guard).
    6. ``query_id`` checked against the live query registry (soft warning,
       prefixed with ``"[warn]"``).

    Hard errors vs soft warnings
    ----------------------------
    Hard errors are plain strings in the returned issues list.
    Soft warnings are prefixed with ``"[warn]"`` so that
    :func:`flow_spec_is_valid` can distinguish them.

    Parameters
    ----------
    data:
        Raw Python dict (e.g. parsed from JSON).

    Returns
    -------
    tuple[FlowSpec | None, list[str]]
        ``(spec, [])``           — valid spec, no issues.
        ``(None, [issue, ...])`` — parse failure (Pydantic errors).
        ``(spec, [issue, ...])`` — parse succeeded but issues exist
                                   (hard errors and/or soft warnings).
    """
    issues: list[str] = []

    # ── Step 1: Pydantic parse ─────────────────────────────────────────────
    try:
        spec = FlowSpec.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError or similar
        try:
            from pydantic import ValidationError  # noqa: PLC0415

            if isinstance(exc, ValidationError):
                for err in exc.errors():
                    loc = ".".join(str(p) for p in err["loc"])
                    issues.append(f"Field '{loc}': {err['msg']}")
            else:
                issues.append(str(exc))
        except ImportError:
            issues.append(str(exc))
        return None, issues

    # ── Step 2: Task key uniqueness ───────────────────────────────────────
    seen_keys: set[str] = set()
    for task in spec.tasks:
        if task.key in seen_keys:
            issues.append(
                f"Duplicate task key {task.key!r} — task keys must be unique."
            )
        seen_keys.add(task.key)

    declared_keys: set[str] = {t.key for t in spec.tasks}

    # ── Step 3: needs references ──────────────────────────────────────────
    for task in spec.tasks:
        for dep in task.needs:
            if dep not in declared_keys:
                issues.append(
                    f"Task {task.key!r} needs {dep!r}, "
                    f"which is not a declared task key. "
                    f"Declared keys: {sorted(declared_keys) or '[]'}."
                )

    # ── Step 4: Acyclic check (topological sort, Kahn's algorithm) ────────
    # Build adjacency: key → set of keys that depend on it (reverse for Kahn).
    # We do a standard Kahn's BFS on the *dependency* graph to detect cycles.
    adjacency: dict[str, list[str]] = {t.key: [] for t in spec.tasks}
    in_degree: dict[str, int] = {t.key: 0 for t in spec.tasks}

    for task in spec.tasks:
        for dep in task.needs:
            if dep in adjacency:  # skip already-reported missing deps
                adjacency[dep].append(task.key)
                in_degree[task.key] += 1

    queue: list[str] = [k for k, deg in in_degree.items() if deg == 0]
    visited_count = 0
    while queue:
        node = queue.pop(0)
        visited_count += 1
        for child in adjacency[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if visited_count < len(declared_keys):
        # There is at least one cycle.  Find and report the shortest one.
        cycle_nodes = [k for k, deg in in_degree.items() if deg > 0]
        cycle_str = _find_cycle(adjacency, cycle_nodes)
        issues.append(f"Cycle detected: {cycle_str}.")

    # ── Step 5: Kind-specific config validation ───────────────────────────
    for task in spec.tasks:
        cfg = task.config
        # ── Cell config blocks (cells-not-kinds; additive, all optional) ──────
        # These apply to any cell regardless of kind; absent ⇒ valid.
        _validate_cell_config_blocks(task, issues)
        if task.kind == "query":
            if not cfg.get("query_id") and not cfg.get("sql"):
                issues.append(
                    f"Task {task.key!r} (query): config must include "
                    "'query_id' or 'sql'."
                )
            # A SQL cell may persist its own SELECT result via config.materialized
            # (full/incremental) — same shape/checks as the materialize task.
            _validate_materialized_block(task, issues)
        elif task.kind == "python":
            if not cfg.get("code"):
                issues.append(
                    f"Task {task.key!r} (python): config must include 'code'."
                )
        elif task.kind == "agent":
            if not cfg.get("prompt"):
                issues.append(
                    f"Task {task.key!r} (agent): config must include 'prompt'."
                )
        elif task.kind == "materialize":
            if not cfg.get("combine_sql"):
                issues.append(
                    f"Task {task.key!r} (materialize): config must include "
                    "'combine_sql'."
                )
            # Validate the optional nested materialization config block.
            _validate_materialized_block(task, issues)
        elif task.kind == "bucket_load":
            if not cfg.get("uri"):
                issues.append(
                    f"Task {task.key!r} (bucket_load): config must include 'uri'."
                )
            if not cfg.get("source"):
                issues.append(
                    f"Task {task.key!r} (bucket_load): config must include 'source'."
                )
        elif task.kind == "file_ingest":
            src = cfg.get("source")
            if not isinstance(src, dict) or not src.get("connector_id"):
                issues.append(
                    f"Task {task.key!r} (file_ingest): config must include "
                    "'source.connector_id'."
                )
            tgt = cfg.get("target")
            if not isinstance(tgt, dict) or not tgt.get("connector_id"):
                issues.append(
                    f"Task {task.key!r} (file_ingest): config must include "
                    "'target.connector_id'."
                )
            elif not tgt.get("object"):
                issues.append(
                    f"Task {task.key!r} (file_ingest): config must include "
                    "'target.object'."
                )
        elif task.kind == "preagg_refresh":
            if not cfg.get("org_id"):
                issues.append(
                    f"Task {task.key!r} (preagg_refresh): config must include 'org_id'."
                )
        elif task.kind == "map":
            if not cfg.get("item_expr"):
                issues.append(
                    f"Task {task.key!r} (map): config must include 'item_expr'."
                )
            body = cfg.get("body")
            if not body or not isinstance(body, list):
                issues.append(
                    f"Task {task.key!r} (map): config must include 'body' "
                    "(non-empty list of TaskSpec dicts)."
                )
            else:
                # Recursive validation of the sub-DAG body.
                sub_spec_data: dict[str, Any] = {
                    "version": 1,
                    "name": f"{task.key}__body",
                    "params": [],
                    "tasks": body,
                }
                sub_spec, sub_issues = validate_flow_spec(sub_spec_data)
                for si in sub_issues:
                    prefix = _WARN_PREFIX if si.startswith(_WARN_PREFIX) else ""
                    bare = si[len(_WARN_PREFIX):].strip() if prefix else si
                    issues.append(f"{prefix}Task {task.key!r} body: {bare}")
                # Prohibit nested map nodes inside body sub-DAGs.
                if sub_spec:
                    for bt in sub_spec.tasks:
                        if bt.kind == "map":
                            issues.append(
                                f"Task {task.key!r} (map): body may not contain another "
                                f"map node (nested fan-out is not supported). "
                                f"Offending key: {bt.key!r}."
                            )
                    # Validate collect_key against body task keys.
                    collect_key = cfg.get("collect_key")
                    if collect_key:
                        body_keys = {bt.key for bt in sub_spec.tasks}
                        if collect_key not in body_keys:
                            issues.append(
                                f"Task {task.key!r} (map): 'collect_key' {collect_key!r} "
                                f"is not a key in body. Body keys: {sorted(body_keys) or '[]'}."
                            )
        elif task.kind == "branch":
            conditions = cfg.get("conditions")
            if not conditions or not isinstance(conditions, list):
                issues.append(
                    f"Task {task.key!r} (branch): config must include 'conditions' "
                    "(non-empty list of {{when, next}} dicts)."
                )
            else:
                for i, cond in enumerate(conditions):
                    if not isinstance(cond, dict):
                        issues.append(
                            f"Task {task.key!r} (branch): condition[{i}] must be a dict "
                            "with 'when' and 'next' keys."
                        )
                        continue
                    if not cond.get("when"):
                        issues.append(
                            f"Task {task.key!r} (branch): condition[{i}] missing 'when' "
                            "expression."
                        )
                    next_list = cond.get("next")
                    if not next_list or not isinstance(next_list, list):
                        issues.append(
                            f"Task {task.key!r} (branch): condition[{i}] missing 'next' "
                            "list (must be a non-empty list of task keys)."
                        )
        # map_collect: no required config fields
        # noop: no required config fields

    # ── Step 5.5: Branch cross-reference post-pass ────────────────────────
    # Build a set of all task keys referenced by branch conditions so we can
    # cross-check against declared_keys and also guard unreachable tasks.
    # We must do this after the full task loop so forward-references work.
    for task in spec.tasks:
        if task.kind != "branch":
            continue
        cfg = task.config
        conditions = cfg.get("conditions") or []
        default_list: list[str] = cfg.get("default") or []

        # Collect all keys that this branch can activate.
        all_next_keys: set[str] = set(default_list)
        for cond in conditions:
            if isinstance(cond, dict) and isinstance(cond.get("next"), list):
                all_next_keys.update(cond["next"])

        # Every next key must be a declared task key.
        for key in all_next_keys:
            if key not in declared_keys:
                issues.append(
                    f"Task {task.key!r} (branch): 'next'/'default' references task "
                    f"{key!r}, which is not a declared task key. "
                    f"Declared keys: {sorted(declared_keys) or '[]'}."
                )

        # Any task that lists this branch in its needs must be reachable via
        # at least one 'next' or 'default' list.  Tasks NOT listed will be set
        # to upstream_failed by the runtime — this is a spec authoring error.
        branch_key = task.key
        for other_task in spec.tasks:
            if branch_key in other_task.needs and other_task.key not in all_next_keys:
                issues.append(
                    f"Task {other_task.key!r} lists branch {branch_key!r} in its "
                    f"'needs' but is not referenced in any 'next' or 'default' list "
                    f"of that branch. This task will never become ready (unreachable "
                    f"task guard)."
                )

    # ── Step 6: query_id registry check (soft warning) ───────────────────
    try:
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        registry = get_query_registry()
        known_ids = {rq.id for rq in registry.all()}
        for task in spec.tasks:
            qid = task.config.get("query_id")
            if qid and qid not in known_ids:
                issues.append(
                    f"{_WARN_PREFIX} Task {task.key!r}: query_id {qid!r} is not in "
                    "the registered query registry (may be a forward reference)."
                )
    except Exception:  # noqa: BLE001 — registry unavailable; skip silently
        pass

    return spec, issues


def _find_cycle(adjacency: dict[str, list[str]], cycle_nodes: list[str]) -> str:
    """Return a human-readable description of a cycle.

    Performs a DFS from *cycle_nodes* to reconstruct the shortest cycle path.
    Returns a string like ``"a → b → c → a"`` for display in error messages.
    """
    # Restrict adjacency to only cycle nodes for the search.
    cycle_set = set(cycle_nodes)

    def dfs(start: str, current: str, path: list[str], visited: set[str]) -> list[str] | None:
        for neighbor in adjacency.get(current, []):
            if neighbor not in cycle_set:
                continue
            if neighbor == start:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                result = dfs(start, neighbor, path + [neighbor], visited)
                if result is not None:
                    return result
        return None

    for start in cycle_nodes:
        result = dfs(start, start, [start], {start})
        if result:
            return " → ".join(result)

    # Fallback: just list the nodes involved.
    return " → ".join(sorted(cycle_nodes))


# ---------------------------------------------------------------------------
# flow_spec_is_valid
# ---------------------------------------------------------------------------


def flow_spec_is_valid(issues: list[str]) -> bool:
    """Return ``True`` when *issues* contains no hard errors.

    Soft warnings are prefixed with ``"[warn]"`` by :func:`validate_flow_spec`
    and do not cause this function to return ``False``.

    Parameters
    ----------
    issues:
        The issues list returned by :func:`validate_flow_spec`.

    Returns
    -------
    bool
        ``True`` if there are no hard errors (issues that are not warnings).
    """
    return all(i.startswith(_WARN_PREFIX) for i in issues)


# ---------------------------------------------------------------------------
# flow_spec_json_schema
# ---------------------------------------------------------------------------


def flow_spec_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for FlowSpec.

    Used to ground the LLM: the schema is injected into the system prompt
    so the model knows the exact format it must emit.

    Returns
    -------
    dict
        JSON Schema dict (Pydantic v2 ``model_json_schema()`` output).
    """
    return FlowSpec.model_json_schema()
