"""NotebookSpec — thin envelope over FlowSpec for the Nubi notebook UI.

Design (from docs/notebook-system-blueprint.md, §2.2)
------------------------------------------------------
A NOTEBOOK is a FlowSpec.  A CELL is a TaskSpec with optional cell_type and
execution_mode fields.  ``NotebookSpec`` wraps a ``FlowSpec`` with notebook-
specific metadata (notebook_id, view, runtime_config, source) and provides
round-trip converters so the executor and runtime consume plain ``FlowSpec``
objects — they need zero changes.

Public API
----------
NotebookRuntimeConfig
    Root-level notebook runtime settings (row limits, memory, compute target).
CellSpec
    TaskSpec alias used in notebook context.  Serialises to/from the same wire
    format as TaskSpec; the two additive top-level fields (cell_type,
    execution_mode) are simply ignored by the base FlowSpec parser.
NotebookSpec
    The notebook envelope.  Validates unique cell keys and recognised
    cell_type / execution_mode values.
notebook_to_flowspec(notebook) -> FlowSpec
    Compile a NotebookSpec to a plain FlowSpec ready for the executor.
flowspec_to_notebook(flow_spec, notebook_id, **kwargs) -> NotebookSpec
    Wrap an existing FlowSpec as a NotebookSpec (for loading persisted flows
    back into the notebook UI).
notebook_to_flow(nb, infer_edges) -> FlowSpec
    Alias for notebook_to_flowspec (backward-compat name used by tests/routes).
flow_to_notebook(spec, notebook_id, view, runtime_config) -> NotebookSpec
    Alias for flowspec_to_notebook (backward-compat name).
infer_notebook_edges(cells) -> list[CellSpec]
    Fill empty ``needs`` lists by inspecting SQL FROM-clauses and Python
    ``inputs["<key>"]`` patterns, with a sequential fallback.

Notes
-----
- CellSpec extends TaskSpec by adding two top-level optional fields
  (cell_type, execution_mode).  These are NOT new config keys — they mirror
  the corresponding fields added to TaskSpec in spec.py so round-trip
  serialisation is lossless for both models.
- Additive config keys (source_dialect, datastore_id, preview_limit,
  use_remote_kernel) live in ``config`` dict — unchanged wire format.
- ``infer_notebook_edges`` is called by ``notebook_to_flowspec`` for
  ``view="notebook"``; for ``view="dag"`` explicit needs are preserved.
- No new DB table — notebooks are stored as flows via store.py.
"""

from __future__ import annotations

import ast
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.flows.spec import FlowParam, FlowSpec, TaskSpec, TaskUi


# ---------------------------------------------------------------------------
# CellSpec — TaskSpec + two top-level additive fields
# ---------------------------------------------------------------------------


class CellSpec(BaseModel):
    """A notebook cell — TaskSpec plus cell_type and execution_mode.

    Wire-compatible with TaskSpec: the two extra top-level fields are
    additive.  Existing FlowSpec consumers (executor, runtime, SDK) that
    receive a CellSpec via the ``tasks`` list see a valid TaskSpec because
    Pydantic ignores unknown fields by default when parsing.

    The four additive *config* keys described in the blueprint are stored in
    ``config`` (existing TaskSpec field) and are NOT modelled as top-level
    Pydantic fields to preserve round-trip compatibility with FlowSpec.

    Additive config keys
    --------------------
    SQL cells (kind='query'):
        config.source_dialect   — dialect the SQL was authored in.
        config.datastore_id     — BYO warehouse connector; absent = demo DuckDB.
        config.preview_limit    — row cap for interactive runs (default 500).

    Python cells (kind='python'):
        config.use_remote_kernel — route to E2B/Modal in durable mode.

    Materialize cells (kind='materialize'):
        config.incremental          — bool.
        config.incremental_ts_col   — timestamp column for incremental filter.
        config.freshness_sla_s      — stale-alert threshold in seconds.
    """

    # TaskSpec fields (identical — keeps CellSpec wire-compatible with TaskSpec)
    key: str = Field(min_length=1, description="Stable cell slug within this notebook.")
    kind: Literal[
        "query", "python", "agent", "materialize", "noop",
        "bucket_load", "preagg_refresh", "map", "branch", "map_collect",
    ] = Field(default="query", description="Execution kind.")
    needs: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    retries: int = Field(default=0, ge=0)
    retry_backoff_s: int = Field(default=30, ge=0)
    timeout_s: int = Field(default=60, ge=0)
    cache_ttl_s: int = Field(default=0, ge=0)
    ui: TaskUi = Field(default_factory=TaskUi)

    # CellSpec-only top-level fields (additive — ignored by FlowSpec parser)
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
        description="Stale-alert threshold in seconds (0 = no alert).",
    )

    def to_task_spec(self) -> TaskSpec:
        """Return this cell as a TaskSpec (all fields including additive ones).

        Since TaskSpec now also carries cell_type, execution_mode, and
        freshness_sla_s (added in this sprint), the compiled TaskSpec is
        a full-fidelity representation that the executor can inspect.
        """
        return TaskSpec(
            key=self.key,
            kind=self.kind,
            needs=self.needs,
            config=self.config,
            retries=self.retries,
            retry_backoff_s=self.retry_backoff_s,
            timeout_s=self.timeout_s,
            cache_ttl_s=self.cache_ttl_s,
            ui=self.ui,
            cell_type=self.cell_type,
            execution_mode=self.execution_mode,
            freshness_sla_s=self.freshness_sla_s,
        )


# ---------------------------------------------------------------------------
# NotebookRuntimeConfig — top-level compute hints
# ---------------------------------------------------------------------------


class NotebookRuntimeConfig(BaseModel):
    """Top-level notebook compute sizing hints.

    Lives at the notebook root (NOT inside a cell body) to avoid the
    Fabric ``%%configure``-must-be-first-cell trap.  Cells never know about
    compute sizing; they are pure computation declarations.
    """

    interactive_row_limit: int = Field(
        default=500,
        ge=1,
        description="Row cap for preview/interactive runs.",
    )
    duckdb_memory_limit: str = Field(
        default="512MB",
        description="DuckDB in-process memory limit.",
    )
    pyodide_packages: list[str] = Field(
        default_factory=list,
        description="Future Pyodide packages (deferred to Phase 5).",
    )
    durable_compute: Literal["local", "e2b", "modal"] = Field(
        default="local",
        description="Compute backend for durable runs.",
    )
    durable_timeout_s: int = Field(
        default=3600,
        ge=0,
        description="Notebook-level durable run timeout in seconds.",
    )


# ---------------------------------------------------------------------------
# NotebookSpec — the notebook envelope
# ---------------------------------------------------------------------------


class NotebookSpec(BaseModel):
    """Notebook envelope — ordered cell array with stable human-slug keys.

    Compiles to a FlowSpec via ``notebook_to_flowspec()`` (or the alias
    ``notebook_to_flow()``).  The executor, runtime, and SDK operate on the
    compiled FlowSpec and are unaware of ``NotebookSpec``.

    Attributes
    ----------
    version:
        Schema version (matches FlowSpec.version).
    name:
        Human-readable notebook name.
    notebook_id:
        Stable UUID — maps to ``flows.id`` in the store.
    view:
        Active UI view: 'notebook' (top-to-bottom cells) or 'dag' (canvas).
    params:
        Flow-level parameter declarations (= Fabric parameter cell).
    tasks:
        Cells in top-to-bottom order (field named ``tasks`` for FlowSpec
        compatibility; use ``cells`` property as a convenient alias).
    execution_mode:
        Notebook-level default execution mode.  Per-cell ``execution_mode``
        overrides this when set to a non-None value.
    runtime_config:
        Top-level compute hints.
    source:
        Origin of this spec: 'notebook' | 'flow' | 'query'.
    """

    version: int = Field(default=1, ge=1, description="Schema version (currently 1).")
    name: str = Field(min_length=1, description="Human-readable notebook name.")
    notebook_id: str = Field(
        default="",
        description="Stable notebook UUID (= flows.id in the store).",
    )
    view: Literal["notebook", "dag"] = Field(
        default="notebook",
        description="Active UI view: top-to-bottom cells vs DAG canvas.",
    )
    params: list[FlowParam] = Field(
        default_factory=list,
        description="Flow-level parameter declarations (= Fabric parameter cell).",
    )
    tasks: list[CellSpec] = Field(
        default_factory=list,
        description="Cells in top-to-bottom order.",
    )
    execution_mode: Literal["preview", "durable"] = Field(
        default="preview",
        description=(
            "Notebook-level default execution mode.  Per-cell execution_mode "
            "overrides this when set."
        ),
    )
    runtime_config: NotebookRuntimeConfig = Field(
        default_factory=NotebookRuntimeConfig,
        description="Top-level compute hints.",
    )
    source: Literal["notebook", "flow", "query"] = Field(
        default="notebook",
        description="Origin of this spec.",
    )

    # Convenience alias -------------------------------------------------

    @property
    def cells(self) -> list[CellSpec]:
        """Alias for ``tasks`` — use in notebook-context code for clarity."""
        return self.tasks

    # Validators --------------------------------------------------------

    @model_validator(mode="after")
    def _validate_cells(self) -> "NotebookSpec":
        """Enforce unique cell keys after full model construction."""
        seen: set[str] = set()
        for cell in self.tasks:
            if cell.key in seen:
                raise ValueError(
                    f"Duplicate cell key {cell.key!r} — cell keys must be "
                    "unique within a notebook."
                )
            seen.add(cell.key)
        return self


# ---------------------------------------------------------------------------
# infer_notebook_edges
# ---------------------------------------------------------------------------

# Matches bare table references that look like cell keys: ``cell_<slug>``
# (conservative regex; sqlglot AST walk is attempted first for SQL cells).
_CELL_REF_RE = re.compile(r"\bcell_([a-z0-9_]+)\b", re.IGNORECASE)


def infer_notebook_edges(cells: list[CellSpec]) -> list[CellSpec]:
    """Fill ``needs`` for notebook-mode cells using automatic edge inference.

    Rules (applied in order; explicit ``needs`` always win):

    1. **SQL cells** (cell_type='sql' or kind='query'): scan the SQL for
       ``cell_<key>`` table refs.  Any matched key that names an earlier cell
       is added to ``needs``.  Tries ``extract_lineage`` (sqlglot) first;
       falls back to regex on import failure.
    2. **Python cells** (cell_type='python' or kind='python'): ``ast.parse``
       the code and find ``inputs["<key>"]`` subscript patterns.  Matched
       earlier-cell keys are added to ``needs``.
    3. **Sequential fallback**: if a cell still has no inferred or explicit
       needs AND it is not the first cell, add ``[previous_cell.key]``.
    4. **Markdown/noop cells**: never get inferred needs (decorative only).
    5. Explicit ``needs`` is preserved as-is (not overwritten).

    Returns a new list of CellSpec instances with updated ``needs``.  The
    input list is not mutated.
    """
    earlier_keys: list[str] = []
    result: list[CellSpec] = []

    for i, cell in enumerate(cells):
        # Explicit needs — honour as-is.
        if cell.needs:
            result.append(cell)
            earlier_keys.append(cell.key)
            continue

        # Markdown / noop cells are decorative — no inferred needs.
        is_markdown = cell.cell_type == "markdown" or cell.kind == "noop"
        if is_markdown:
            result.append(cell)
            earlier_keys.append(cell.key)
            continue

        inferred: list[str] = []
        available: set[str] = set(earlier_keys)

        is_sql = cell.cell_type == "sql" or cell.kind in ("query", "materialize")
        if is_sql:
            sql_source: str = (
                cell.config.get("sql")
                or cell.config.get("combine_sql")
                or ""
            )
            if sql_source:
                # Attempt sqlglot-based table extraction.
                try:
                    from app.lineage.extract import extract_lineage  # noqa: PLC0415
                    lineage = extract_lineage(sql_source)
                    for tbl in lineage.get("tables", []):
                        tbl_norm = str(tbl).lower()
                        if tbl_norm in available:
                            inferred.append(tbl_norm)
                        elif tbl_norm.startswith("cell_"):
                            # table name IS a full cell key
                            for ek in earlier_keys:
                                if ek == tbl_norm:
                                    inferred.append(ek)
                except Exception:  # noqa: BLE001
                    for m in _CELL_REF_RE.finditer(sql_source):
                        full_ref = f"cell_{m.group(1).lower()}"
                        if full_ref in available:
                            inferred.append(full_ref)

        is_python = cell.cell_type == "python" or cell.kind == "python"
        if is_python:
            code: str = cell.config.get("code") or ""
            if code:
                try:
                    tree = ast.parse(code)
                    for node in ast.walk(tree):
                        if (
                            isinstance(node, ast.Subscript)
                            and isinstance(node.value, ast.Name)
                            and node.value.id == "inputs"
                        ):
                            slice_node = node.slice
                            # Python 3.8 wraps slice in ast.Index.
                            if isinstance(slice_node, ast.Index):
                                slice_node = slice_node.value  # type: ignore[attr-defined]
                            if isinstance(slice_node, ast.Constant) and isinstance(
                                slice_node.value, str
                            ):
                                ref_key = str(slice_node.value)
                                if ref_key in available:
                                    inferred.append(ref_key)
                except SyntaxError:
                    pass

        # Sequential fallback.
        if not inferred and i > 0:
            inferred = [earlier_keys[-1]]

        # Build updated cell (avoid mutating input); deduplicate.
        updated = CellSpec(
            key=cell.key,
            kind=cell.kind,
            needs=list(dict.fromkeys(inferred)),
            config=cell.config,
            retries=cell.retries,
            retry_backoff_s=cell.retry_backoff_s,
            timeout_s=cell.timeout_s,
            cache_ttl_s=cell.cache_ttl_s,
            ui=cell.ui,
            cell_type=cell.cell_type,
            execution_mode=cell.execution_mode,
            freshness_sla_s=cell.freshness_sla_s,
        )
        result.append(updated)
        earlier_keys.append(cell.key)

    return result


# ---------------------------------------------------------------------------
# notebook_to_flowspec  (primary name)
# ---------------------------------------------------------------------------


def notebook_to_flowspec(
    nb: NotebookSpec,
    infer_edges: bool = True,
) -> FlowSpec:
    """Compile a NotebookSpec to a FlowSpec ready for the executor.

    Steps
    -----
    1. For ``view='notebook'`` (and ``infer_edges=True``), run
       ``infer_notebook_edges()`` to fill missing ``needs`` edges.
    2. For ``view='dag'``, preserve explicit ``needs`` as-is.
    3. Convert each CellSpec to a TaskSpec (full-fidelity — additive fields
       cell_type / execution_mode / freshness_sla_s are forwarded).
    4. Forward a ``runtime_config`` dict to the FlowSpec.  The dict encodes
       the NotebookRuntimeConfig plus notebook-envelope keys (notebook_id,
       execution_mode, source) for downstream consumers.

    Parameters
    ----------
    nb:
        A validated ``NotebookSpec`` instance.
    infer_edges:
        When ``True`` (default) and ``nb.view == "notebook"``, run edge
        inference.  Pass ``False`` to skip (e.g. when edges are already set).

    Returns
    -------
    FlowSpec
        Plain flow spec, ready for ``validate_flow_spec`` or direct execution.
    """
    cells: list[CellSpec]
    if infer_edges and nb.view == "notebook":
        cells = infer_notebook_edges(nb.tasks)
    else:
        cells = list(nb.tasks)

    tasks = [cell.to_task_spec() for cell in cells]

    rc_dict: dict[str, Any] = nb.runtime_config.model_dump()
    rc_dict["notebook_id"] = nb.notebook_id
    rc_dict["notebook_execution_mode"] = nb.execution_mode
    rc_dict["notebook_source"] = nb.source

    return FlowSpec(
        version=nb.version,
        name=nb.name,
        params=nb.params,
        tasks=tasks,
        runtime_config=rc_dict,
    )


# Backward-compat alias used by existing routes / tests.
notebook_to_flow = notebook_to_flowspec


# ---------------------------------------------------------------------------
# flowspec_to_notebook  (primary name)
# ---------------------------------------------------------------------------


def flowspec_to_notebook(
    spec: FlowSpec,
    notebook_id: str = "",
    view: Literal["notebook", "dag"] = "notebook",
    runtime_config: NotebookRuntimeConfig | None = None,
) -> NotebookSpec:
    """Round-trip a FlowSpec back into a NotebookSpec.

    The resulting ``NotebookSpec.tasks`` are ``CellSpec`` instances.
    ``cell_type`` and ``execution_mode`` are preserved when the FlowSpec was
    produced by ``notebook_to_flowspec`` (they survive as TaskSpec fields);
    otherwise they are inferred from ``task.kind``.

    Parameters
    ----------
    spec:
        The FlowSpec to wrap.
    notebook_id:
        The stable notebook UUID (``flows.id``).
    view:
        The active UI view.
    runtime_config:
        Optional runtime config; uses defaults when ``None``.  If ``None``
        and ``spec.runtime_config`` contains NotebookRuntimeConfig keys, those
        are used to populate the config.
    """
    # Resolve runtime_config from the FlowSpec's stored dict if not provided.
    if runtime_config is None:
        rc_dict = spec.runtime_config or {}
        nrc_fields = {
            k: rc_dict[k]
            for k in (
                "interactive_row_limit",
                "duckdb_memory_limit",
                "pyodide_packages",
                "durable_compute",
                "durable_timeout_s",
            )
            if k in rc_dict
        }
        runtime_config = NotebookRuntimeConfig(**nrc_fields)

    # Resolve execution_mode from stored runtime_config or default.
    rc_dict = spec.runtime_config or {}
    em_raw = rc_dict.get("notebook_execution_mode", "preview")
    execution_mode: Literal["preview", "durable"] = (
        em_raw if em_raw in ("preview", "durable") else "preview"
    )

    # Resolve source.
    src_raw = rc_dict.get("notebook_source", "flow")
    source: Literal["notebook", "flow", "query"] = (
        src_raw if src_raw in ("notebook", "flow", "query") else "flow"
    )

    cells: list[CellSpec] = []
    for task in spec.tasks:
        # If task already carries cell_type (was produced by notebook_to_flowspec),
        # use it; otherwise infer from kind.
        if task.cell_type is not None:
            cell_type = task.cell_type
        elif task.kind in ("query", "materialize"):
            cell_type = "sql"
        elif task.kind == "python":
            cell_type = "python"
        elif task.kind == "noop":
            cell_type = "markdown"
        else:
            cell_type = None

        cells.append(
            CellSpec(
                key=task.key,
                kind=task.kind,
                needs=task.needs,
                config=task.config,
                retries=task.retries,
                retry_backoff_s=task.retry_backoff_s,
                timeout_s=task.timeout_s,
                cache_ttl_s=task.cache_ttl_s,
                ui=task.ui,
                cell_type=cell_type,
                execution_mode=task.execution_mode,
                freshness_sla_s=task.freshness_sla_s,
            )
        )

    return NotebookSpec(
        version=spec.version,
        name=spec.name,
        notebook_id=notebook_id,
        view=view,
        params=spec.params,
        tasks=cells,
        execution_mode=execution_mode,
        runtime_config=runtime_config,
        source=source,
    )


# Backward-compat alias.
flow_to_notebook = flowspec_to_notebook
