"""Canonical Flow SPEC — shared format for the DAG builder and the LLM.

Public API
----------
FlowParam
    A flow-level parameter declaration (name, type, default, required).
TaskUi
    Canvas position for a task node in the React Flow builder.
TaskSpec
    A single task in the flow DAG (key, kind, needs, config, retries, …).
FlowSpec
    The complete flow specification document (version, name, params, tasks).

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
        ``'materialize'``, or ``'noop'``.
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
    kind: Literal["query", "python", "agent", "materialize", "noop"] = Field(
        description="Execution kind."
    )
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


# ---------------------------------------------------------------------------
# validate_flow_spec
# ---------------------------------------------------------------------------


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
        if task.kind == "query":
            if not cfg.get("query_id") and not cfg.get("sql"):
                issues.append(
                    f"Task {task.key!r} (query): config must include "
                    "'query_id' or 'sql'."
                )
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
        # noop: no required config fields

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
