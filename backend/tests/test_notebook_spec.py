"""Tests for the NotebookSpec data model and round-trip converters.

Covers:
- TaskSpec backward-compat: existing FlowSpec dicts parse cleanly with new
  optional fields (cell_type, execution_mode, freshness_sla_s).
- FlowSpec backward-compat: new fields (env, runtime_config) default cleanly
  so existing specs serialise/deserialise unchanged.
- CellSpec construction and to_task_spec() round-trip.
- NotebookSpec validation: unique cell keys, invalid cell_type/execution_mode
  are hard errors.
- notebook_to_flowspec(): 3-cell SQL+Python notebook compiles to a valid
  FlowSpec; infer_notebook_edges() fills needs.
- flowspec_to_notebook(): compiled FlowSpec round-trips back to a NotebookSpec
  with the original cell metadata preserved.
- infer_notebook_edges(): SQL cross-cell references, Python inputs["key"],
  and sequential fallback all produce correct needs lists.
- store.notebook_spec_from_flow(): helper reconstructs NotebookSpec from a
  stored Flow dict.
"""

from __future__ import annotations

import pytest

from app.flows.notebook import (
    CellSpec,
    NotebookRuntimeConfig,
    NotebookSpec,
    flowspec_to_notebook,
    infer_notebook_edges,
    notebook_to_flowspec,
)
from app.flows.spec import FlowSpec, TaskSpec, validate_flow_spec
from app.flows.store import InMemoryFlowStore, notebook_spec_from_flow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sql_cell(key: str, sql: str = "SELECT 1") -> CellSpec:
    return CellSpec(
        key=key,
        kind="query",
        config={"sql": sql},
        cell_type="sql",
    )


def _make_python_cell(key: str, code: str = "result = {}") -> CellSpec:
    return CellSpec(
        key=key,
        kind="python",
        config={"code": code},
        cell_type="python",
    )


def _make_markdown_cell(key: str) -> CellSpec:
    return CellSpec(
        key=key,
        kind="noop",
        config={},
        cell_type="markdown",
    )


# ---------------------------------------------------------------------------
# 1. Backward-compat: existing TaskSpec dicts still parse
# ---------------------------------------------------------------------------


def test_task_spec_backward_compat():
    """Existing FlowSpec dicts (without cell_type / execution_mode) parse fine."""
    data = {
        "version": 1,
        "name": "legacy_flow",
        "tasks": [
            {"key": "pull", "kind": "query", "config": {"sql": "SELECT 1"}},
            {"key": "save", "kind": "python", "config": {"code": "x = 1"}, "needs": ["pull"]},
        ],
    }
    spec, issues = validate_flow_spec(data)
    assert spec is not None
    assert not [i for i in issues if not i.startswith("[warn]")]
    # New fields default to None / 0
    assert spec.tasks[0].cell_type is None
    assert spec.tasks[0].execution_mode is None
    assert spec.tasks[0].freshness_sla_s == 0


def test_flow_spec_env_runtime_config_defaults():
    """FlowSpec.env defaults to 'prod' and runtime_config to {} for old specs."""
    spec = FlowSpec(name="test", tasks=[])
    assert spec.env == "prod"
    assert spec.runtime_config == {}


# ---------------------------------------------------------------------------
# 2. TaskSpec with new fields
# ---------------------------------------------------------------------------


def test_task_spec_cell_fields():
    """TaskSpec accepts cell_type, execution_mode, freshness_sla_s."""
    task = TaskSpec(
        key="revenue",
        kind="query",
        config={"sql": "SELECT * FROM orders"},
        cell_type="sql",
        execution_mode="preview",
        freshness_sla_s=3600,
    )
    assert task.cell_type == "sql"
    assert task.execution_mode == "preview"
    assert task.freshness_sla_s == 3600


def test_task_spec_invalid_cell_type_rejected():
    """TaskSpec rejects invalid cell_type via Pydantic."""
    with pytest.raises(Exception):
        TaskSpec(
            key="x",
            kind="query",
            config={"sql": "SELECT 1"},
            cell_type="spreadsheet",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# 3. CellSpec
# ---------------------------------------------------------------------------


def test_cell_spec_to_task_spec_round_trip():
    """CellSpec.to_task_spec() preserves all fields including additive ones."""
    cell = CellSpec(
        key="revenue",
        kind="query",
        config={"sql": "SELECT amount FROM sales", "preview_limit": 200},
        cell_type="sql",
        execution_mode="durable",
        freshness_sla_s=7200,
    )
    task = cell.to_task_spec()
    assert isinstance(task, TaskSpec)
    assert task.key == "revenue"
    assert task.kind == "query"
    assert task.cell_type == "sql"
    assert task.execution_mode == "durable"
    assert task.freshness_sla_s == 7200
    assert task.config["preview_limit"] == 200


def test_cell_spec_config_keys():
    """CellSpec stores additive config keys in the config dict unchanged."""
    cell = CellSpec(
        key="bq_cell",
        kind="query",
        config={
            "sql": "SELECT * FROM bigquery_table",
            "source_dialect": "bigquery",
            "datastore_id": "ds-abc",
            "preview_limit": 500,
        },
        cell_type="sql",
    )
    assert cell.config["source_dialect"] == "bigquery"
    assert cell.config["datastore_id"] == "ds-abc"
    assert cell.config["preview_limit"] == 500


def test_python_cell_use_remote_kernel():
    """Python cell use_remote_kernel config key is stored correctly."""
    cell = CellSpec(
        key="transform",
        kind="python",
        config={"code": "result = inputs['raw']", "use_remote_kernel": True},
        cell_type="python",
    )
    assert cell.config["use_remote_kernel"] is True


# ---------------------------------------------------------------------------
# 4. NotebookSpec validation
# ---------------------------------------------------------------------------


def test_notebook_spec_duplicate_key_error():
    """NotebookSpec rejects duplicate cell keys."""
    with pytest.raises(ValueError, match="Duplicate cell key"):
        NotebookSpec(
            name="test",
            notebook_id="nb-001",
            tasks=[
                _make_sql_cell("cell_a"),
                _make_sql_cell("cell_a"),  # duplicate
            ],
        )


def test_notebook_spec_empty_is_valid():
    """An empty notebook (no cells) is valid."""
    nb = NotebookSpec(name="empty", notebook_id="nb-000")
    assert nb.tasks == []


def test_notebook_spec_unique_keys_ok():
    """NotebookSpec with unique keys passes validation."""
    nb = NotebookSpec(
        name="two_cells",
        notebook_id="nb-002",
        tasks=[_make_sql_cell("cell_a"), _make_python_cell("cell_b")],
    )
    assert len(nb.tasks) == 2


def test_notebook_spec_cells_alias():
    """NotebookSpec.cells is an alias for .tasks."""
    nb = NotebookSpec(
        name="alias_test",
        notebook_id="nb-003",
        tasks=[_make_sql_cell("cell_a")],
    )
    assert nb.cells is nb.tasks


def test_notebook_spec_defaults():
    """NotebookSpec has sensible defaults for optional fields."""
    nb = NotebookSpec(name="defaults", notebook_id="nb-004")
    assert nb.version == 1
    assert nb.view == "notebook"
    assert nb.execution_mode == "preview"
    assert nb.source == "notebook"
    assert nb.env == "prod"
    assert isinstance(nb.runtime_config, NotebookRuntimeConfig)
    assert nb.runtime_config.interactive_row_limit == 500


# ---------------------------------------------------------------------------
# 5. infer_notebook_edges
# ---------------------------------------------------------------------------


def test_infer_edges_explicit_needs_preserved():
    """Cells with explicit needs are not modified."""
    cells = [
        _make_sql_cell("cell_base", "SELECT 1"),
        CellSpec(
            key="cell_transform",
            kind="query",
            config={"sql": "SELECT * FROM cell_base"},
            cell_type="sql",
            needs=["cell_base"],  # explicit
        ),
    ]
    result = infer_notebook_edges(cells)
    assert result[1].needs == ["cell_base"]


def test_infer_edges_sql_cross_cell_reference():
    """SQL cell with FROM cell_<key> gets inferred needs."""
    cells = [
        _make_sql_cell("cell_raw", "SELECT id, amount FROM orders"),
        _make_sql_cell("cell_agg", "SELECT sum(amount) FROM cell_raw"),
    ]
    result = infer_notebook_edges(cells)
    assert result[0].needs == []
    assert "cell_raw" in result[1].needs


def test_infer_edges_python_inputs_reference():
    """Python cell with inputs['cell_raw'] gets inferred needs."""
    cells = [
        _make_sql_cell("cell_raw", "SELECT id FROM orders"),
        _make_python_cell(
            "cell_transform",
            'raw = inputs["cell_raw"]\nresult = {"rows": raw["rows"]}',
        ),
    ]
    result = infer_notebook_edges(cells)
    assert "cell_raw" in result[1].needs


def test_infer_edges_sequential_fallback():
    """A cell with no references gets the previous cell as its sole dependency."""
    cells = [
        _make_sql_cell("cell_a", "SELECT 1"),
        _make_sql_cell("cell_b", "SELECT 2"),  # no reference to cell_a
    ]
    result = infer_notebook_edges(cells)
    assert result[0].needs == []
    assert result[1].needs == ["cell_a"]


def test_infer_edges_markdown_no_deps():
    """Markdown/noop cells never get inferred dependencies."""
    cells = [
        _make_sql_cell("cell_a", "SELECT 1"),
        _make_markdown_cell("cell_note"),
        _make_sql_cell("cell_b", "SELECT 2"),
    ]
    result = infer_notebook_edges(cells)
    assert result[1].needs == []  # markdown — no inferred deps
    assert result[2].needs == ["cell_note"]  # sequential fallback from previous


def test_infer_edges_first_cell_no_deps():
    """The first cell never gets a sequential fallback dependency."""
    cells = [_make_sql_cell("cell_first", "SELECT 1")]
    result = infer_notebook_edges(cells)
    assert result[0].needs == []


# ---------------------------------------------------------------------------
# 6. notebook_to_flowspec  (primary 3-cell integration test)
# ---------------------------------------------------------------------------


def test_three_cell_notebook_compiles_to_valid_flowspec():
    """A 3-cell SQL + Python + SQL notebook compiles to a valid FlowSpec.

    Cell layout:
        cell_raw    (sql)    — root, reads from external source
        cell_trans  (python) — reads cell_raw via inputs["cell_raw"]
        cell_final  (sql)    — reads cell_trans via FROM cell_trans

    Expected edges:
        cell_raw   → []
        cell_trans → [cell_raw]   (inferred from inputs["cell_raw"])
        cell_final → [cell_trans] (inferred from FROM cell_trans)
    """
    nb = NotebookSpec(
        name="revenue_notebook",
        notebook_id="nb-revenue-001",
        tasks=[
            CellSpec(
                key="cell_raw",
                kind="query",
                config={"sql": "SELECT id, amount, region FROM orders"},
                cell_type="sql",
            ),
            CellSpec(
                key="cell_trans",
                kind="python",
                config={
                    "code": (
                        'raw = inputs["cell_raw"]\n'
                        "rows = [r for r in raw['rows'] if r['amount'] > 0]\n"
                        "result = {'rows': rows}"
                    ),
                    "use_remote_kernel": False,
                },
                cell_type="python",
            ),
            CellSpec(
                key="cell_final",
                kind="query",
                config={"sql": "SELECT region, sum(amount) as total FROM cell_trans GROUP BY 1"},
                cell_type="sql",
            ),
        ],
        view="notebook",
        execution_mode="preview",
        env="dev",
    )

    flow = notebook_to_flowspec(nb)

    # FlowSpec structural checks.
    assert isinstance(flow, FlowSpec)
    assert flow.name == "revenue_notebook"
    assert flow.env == "dev"
    assert len(flow.tasks) == 3

    # runtime_config carries notebook envelope keys.
    assert flow.runtime_config["notebook_id"] == "nb-revenue-001"
    assert flow.runtime_config["notebook_execution_mode"] == "preview"
    assert flow.runtime_config["notebook_source"] == "notebook"

    # Validate via validate_flow_spec.
    spec, issues = validate_flow_spec(flow.model_dump())
    hard_issues = [i for i in issues if not i.startswith("[warn]")]
    assert spec is not None, f"FlowSpec parse failed: {issues}"
    assert hard_issues == [], f"Hard validation errors: {hard_issues}"

    # Edge inference.
    key_to_task = {t.key: t for t in flow.tasks}
    assert key_to_task["cell_raw"].needs == []
    assert "cell_raw" in key_to_task["cell_trans"].needs
    assert "cell_trans" in key_to_task["cell_final"].needs


def test_notebook_to_flowspec_dag_view_preserves_explicit_needs():
    """DAG-view notebooks preserve explicit needs without running inference."""
    nb = NotebookSpec(
        name="dag_notebook",
        notebook_id="nb-dag-001",
        tasks=[
            _make_sql_cell("cell_a"),
            CellSpec(
                key="cell_b",
                kind="query",
                config={"sql": "SELECT * FROM cell_a"},
                cell_type="sql",
                needs=["cell_a"],
            ),
        ],
        view="dag",
    )
    flow = notebook_to_flowspec(nb)
    assert flow.tasks[1].needs == ["cell_a"]


# ---------------------------------------------------------------------------
# 7. flowspec_to_notebook  (round-trip)
# ---------------------------------------------------------------------------


def test_flowspec_to_notebook_round_trip():
    """A compiled FlowSpec round-trips back to an equivalent NotebookSpec."""
    nb_original = NotebookSpec(
        name="round_trip_test",
        notebook_id="nb-rt-001",
        tasks=[
            CellSpec(
                key="cell_a",
                kind="query",
                config={"sql": "SELECT 1"},
                cell_type="sql",
                execution_mode="preview",
            ),
            CellSpec(
                key="cell_b",
                kind="python",
                config={"code": "result = inputs['cell_a']"},
                cell_type="python",
                execution_mode="durable",
            ),
        ],
        view="notebook",
        execution_mode="preview",
        env="staging",
    )

    # Compile to FlowSpec.
    flow = notebook_to_flowspec(nb_original)

    # Round-trip back to NotebookSpec.
    nb_restored = flowspec_to_notebook(flow, notebook_id="nb-rt-001")

    assert nb_restored.name == nb_original.name
    assert nb_restored.notebook_id == "nb-rt-001"
    assert nb_restored.env == "staging"
    assert nb_restored.execution_mode == "preview"
    assert len(nb_restored.tasks) == 2

    # Cell metadata is preserved.
    cell_a = nb_restored.tasks[0]
    assert cell_a.key == "cell_a"
    assert cell_a.cell_type == "sql"
    assert cell_a.execution_mode == "preview"

    cell_b = nb_restored.tasks[1]
    assert cell_b.key == "cell_b"
    assert cell_b.cell_type == "python"
    assert cell_b.execution_mode == "durable"


def test_flowspec_to_notebook_infers_cell_type_from_kind():
    """flowspec_to_notebook infers cell_type from task.kind when not present."""
    # Build a plain FlowSpec with no cell_type.
    spec = FlowSpec(
        name="plain_flow",
        tasks=[
            TaskSpec(key="t_query", kind="query", config={"sql": "SELECT 1"}),
            TaskSpec(key="t_python", kind="python", config={"code": "x = 1"}),
            TaskSpec(key="t_noop", kind="noop"),
        ],
    )
    nb = flowspec_to_notebook(spec, notebook_id="nb-infer")
    assert nb.tasks[0].cell_type == "sql"
    assert nb.tasks[1].cell_type == "python"
    assert nb.tasks[2].cell_type == "markdown"


# ---------------------------------------------------------------------------
# 8. NotebookRuntimeConfig
# ---------------------------------------------------------------------------


def test_notebook_runtime_config_defaults():
    """NotebookRuntimeConfig has correct defaults."""
    nrc = NotebookRuntimeConfig()
    assert nrc.interactive_row_limit == 500
    assert nrc.duckdb_memory_limit == "512MB"
    assert nrc.durable_compute == "local"
    assert nrc.durable_timeout_s == 3600
    assert nrc.pyodide_packages == []


def test_notebook_runtime_config_in_flowspec():
    """Runtime config is encoded in the compiled FlowSpec's runtime_config dict."""
    nrc = NotebookRuntimeConfig(interactive_row_limit=1000, durable_compute="e2b")
    nb = NotebookSpec(
        name="nrc_test",
        notebook_id="nb-nrc-001",
        runtime_config=nrc,
    )
    flow = notebook_to_flowspec(nb)
    assert flow.runtime_config["interactive_row_limit"] == 1000
    assert flow.runtime_config["durable_compute"] == "e2b"


# ---------------------------------------------------------------------------
# 9. store.notebook_spec_from_flow
# ---------------------------------------------------------------------------


def test_notebook_spec_from_flow_reconstructs_notebook():
    """notebook_spec_from_flow() rebuilds a NotebookSpec from a stored Flow dict."""
    nb = NotebookSpec(
        name="stored_notebook",
        notebook_id="nb-store-001",
        tasks=[
            _make_sql_cell("cell_a"),
            _make_python_cell("cell_b"),
        ],
        env="dev",
    )
    flow_spec = notebook_to_flowspec(nb)

    # Simulate the stored flow dict.
    fake_flow = {
        "id": "nb-store-001",
        "spec": flow_spec.model_dump(),
    }

    restored_nb = notebook_spec_from_flow(fake_flow)
    assert restored_nb.name == "stored_notebook"
    assert restored_nb.notebook_id == "nb-store-001"
    assert restored_nb.env == "dev"
    assert len(restored_nb.tasks) == 2
    assert restored_nb.tasks[0].key == "cell_a"
    assert restored_nb.tasks[1].key == "cell_b"


def test_notebook_spec_from_flow_raises_on_missing_spec():
    """notebook_spec_from_flow() raises ValueError when spec is missing."""
    with pytest.raises(ValueError, match="no valid 'spec' dict"):
        notebook_spec_from_flow({"id": "nb-bad", "spec": None})


def test_notebook_spec_from_flow_with_in_memory_store():
    """Round-trip through InMemoryFlowStore.create_flow + notebook_spec_from_flow."""
    import asyncio

    nb = NotebookSpec(
        name="in_memory_notebook",
        notebook_id="",  # will be assigned by store
        tasks=[_make_sql_cell("cell_a"), _make_sql_cell("cell_b")],
    )
    flow_spec = notebook_to_flowspec(nb)

    store = InMemoryFlowStore()

    async def run():
        flow = await store.create_flow(
            org_id="org-1",
            created_by="user-1",
            name=nb.name,
            spec=flow_spec.model_dump(),
        )
        return flow

    flow = asyncio.run(run())
    restored = notebook_spec_from_flow(flow)
    assert restored.name == "in_memory_notebook"
    assert restored.notebook_id == flow["id"]
    assert len(restored.tasks) == 2
