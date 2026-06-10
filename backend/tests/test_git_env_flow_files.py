"""Flows-as-files + output-shape sidecar wired into env_sync (W1-C).

These tests cover the two PARTS of W1-C against a REAL on-disk git repo
(via :class:`app.git.env_sync.ProjectGit`), reusing the same on-disk harness
style as ``tests/test_git_env.py`` (a tmp workspace, local commits only):

PART 1 — flows serialize to the canonical per-cell file tree
    ``flows/<slug>__<id8>/flow.toml`` + ``cells/NN_<key>.{sql,py,md}`` and pull
    reconstructs the spec losslessly (``model_dump`` equality). The nested
    layout must be recognised by ``refs_from_paths`` and the real flow uuid
    recovered from ``flow.toml`` (NOT the 8-char dir id).

PART 2 — a query with ``output_schema`` emits the ``queries/<id>.json`` sidecar
    and ``load_resource_at`` reads it back into ``config['output_schema']``;
    a query WITHOUT one emits no sidecar.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from app.environments.store import InMemoryEnvStore, set_env_store
from app.flows.spec import validate_flow_spec
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.git.env_sync import (
    ProjectGit,
    load_resource_at,
    pull_env,
    push_env,
    refs_from_paths,
    serialize_version_files,
)
from app.git.flow_files import flow_dir
from app.repos.memory import InMemoryRepo


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _flow_spec() -> dict:
    """A flow with one sql + one python + one markdown cell (like test_flow_files)."""
    return {
        "version": 1,
        "name": "Daily Revenue",
        "params": [
            {"name": "region", "type": "text", "default": "us", "required": False}
        ],
        "tasks": [
            {
                "key": "pull",
                "kind": "query",
                "cell_type": "sql",
                "needs": [],
                "config": {"sql": "SELECT * FROM orders", "datastore_id": "ds-1"},
                "retries": 2,
                "retry_backoff_s": 30,
                "timeout_s": 60,
                "cache_ttl_s": 0,
                "ui": {"x": 10, "y": 20},
            },
            {
                "key": "transform",
                "kind": "python",
                "cell_type": "python",
                "needs": ["pull"],
                "config": {"code": "result = {'rows': inputs['pull']}"},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 60,
                "cache_ttl_s": 0,
                "ui": {"x": 200, "y": 20},
            },
            {
                "key": "note",
                "kind": "noop",
                "cell_type": "markdown",
                "needs": [],
                "config": {"markdown": "# Heading\n\nExplanatory text."},
                "retries": 0,
                "retry_backoff_s": 30,
                "timeout_s": 60,
                "cache_ttl_s": 0,
            },
        ],
    }


def _commit_serialized(
    git: ProjectGit, branch: str, items: list[dict[str, str]]
) -> str:
    """Commit serialized {path,content} items onto *branch*; return the tip sha."""
    sha = git.commit_files(branch, items, "seed", replace_known=True)
    assert sha is not None
    return sha


@pytest.fixture()
def git_repo(tmp_path: Path) -> ProjectGit:
    git = ProjectGit(tmp_path / "repo")
    git.ensure()
    return git


# ---------------------------------------------------------------------------
# PART 1 — flows-as-files round-trip through serialize→commit→load
# ---------------------------------------------------------------------------


def test_serialize_flow_emits_per_cell_tree(git_repo: ProjectGit):
    flow_id = str(uuid.uuid4())
    spec = _flow_spec()
    items = serialize_version_files("flow", flow_id, spec["name"], spec)
    paths = {i["path"] for i in items}

    base = flow_dir(flow_id, spec["name"])
    assert f"{base}/flow.toml" in paths
    assert f"{base}/cells/01_pull.sql" in paths
    assert f"{base}/cells/02_transform.py" in paths
    assert f"{base}/cells/03_note.md" in paths
    # NO legacy single-blob flows/<id>.json is emitted (canonical layout).
    assert f"flows/{flow_id}.json" not in paths
    assert not any(p == f"{base}.json" for p in paths)


def test_flow_round_trips_losslessly_through_git(git_repo: ProjectGit):
    """serialize → commit → refs_from_paths → load reconstructs the spec.

    model_dump equality through the canonical FlowSpec model on both sides.
    """
    flow_id = str(uuid.uuid4())
    spec = _flow_spec()
    items = serialize_version_files("flow", flow_id, spec["name"], spec)
    head = _commit_serialized(git_repo, "dev", items)

    # The nested flows/<dir>/** layout must be recognised (NOT only depth-2).
    known = git_repo.list_known_files(head)
    assert any("/cells/" in p for p in known), "nested cell files must be tracked"
    refs = refs_from_paths(known)
    flow_refs = [r for r in refs if r[0] == "flow"]
    assert len(flow_refs) == 1, f"expected one flow ref, got {flow_refs}"
    kind, ref_key = flow_refs[0]
    # The ref key is the DIRECTORY name, not the full uuid.
    assert ref_key == flow_dir(flow_id, spec["name"]).split("/", 1)[1]

    loaded = load_resource_at(git_repo, head, kind, ref_key)
    assert loaded is not None
    config, name, real_id = loaded

    # The REAL uuid is recovered from flow.toml, not the 8-char dir id.
    assert real_id == flow_id
    assert name == spec["name"]

    orig, _ = validate_flow_spec(spec)
    back, _ = validate_flow_spec(config)
    assert orig is not None and back is not None
    assert back.model_dump() == orig.model_dump()


def test_legacy_flow_json_blob_still_readable(git_repo: ProjectGit):
    """A legacy flows/<id>.json blob still imports for back-compat."""
    import json

    flow_id = str(uuid.uuid4())
    spec = _flow_spec()
    blob = {"id": flow_id, "name": spec["name"], "spec": spec}
    head = _commit_serialized(
        git_repo,
        "dev",
        [{"path": f"flows/{flow_id}.json", "content": json.dumps(blob)}],
    )
    refs = refs_from_paths(git_repo.list_known_files(head))
    assert ("flow", flow_id) in refs
    loaded = load_resource_at(git_repo, head, "flow", flow_id)
    assert loaded is not None
    config, name, real_id = loaded
    assert real_id == flow_id
    orig, _ = validate_flow_spec(spec)
    back, _ = validate_flow_spec(config)
    assert back.model_dump() == orig.model_dump()


# ---------------------------------------------------------------------------
# PART 2 — query output-shape sidecar
# ---------------------------------------------------------------------------


def test_query_with_output_schema_emits_sidecar(git_repo: ProjectGit):
    qid = str(uuid.uuid4())
    config = {
        "sql": "select 1 as id, 'x' as label",
        "datastore_id": "ds-1",
        "output_schema": [
            {"name": "id", "type": "number"},
            {"name": "label", "type": "text"},
        ],
    }
    items = serialize_version_files("query", qid, "Q1", config)
    paths = {i["path"] for i in items}
    assert f"queries/{qid}.sql" in paths
    assert f"queries/{qid}.meta.json" in paths
    assert f"queries/{qid}.json" in paths, "output_schema must emit the sidecar"

    head = _commit_serialized(git_repo, "dev", items)

    # The sidecar is NOT treated as a standalone resource ref.
    refs = refs_from_paths(git_repo.list_known_files(head))
    assert ("query", qid) in refs
    assert sum(1 for k, r in refs if k == "query") == 1

    loaded = load_resource_at(git_repo, head, "query", qid)
    assert loaded is not None
    out_config, name, real_id = loaded
    assert real_id == qid
    assert out_config["sql"] == config["sql"]
    assert out_config["output_schema"] == config["output_schema"]


def test_query_without_output_schema_emits_no_sidecar(git_repo: ProjectGit):
    qid = str(uuid.uuid4())
    config = {"sql": "select 1", "datastore_id": "ds-1"}
    items = serialize_version_files("query", qid, "Q1", config)
    paths = {i["path"] for i in items}
    assert f"queries/{qid}.json" not in paths, "no schema → no sidecar"

    head = _commit_serialized(git_repo, "dev", items)
    loaded = load_resource_at(git_repo, head, "query", qid)
    assert loaded is not None
    out_config, _name, _rid = loaded
    assert "output_schema" not in out_config


def test_empty_output_schema_list_is_a_declared_contract(git_repo: ProjectGit):
    """An explicit empty [] is a declared (empty) contract → sidecar + round-trip."""
    qid = str(uuid.uuid4())
    config = {"sql": "select 1", "output_schema": []}
    items = serialize_version_files("query", qid, "Q1", config)
    # An empty declared contract still serialises the sidecar (file present),
    # so the "declared empty" state survives a round-trip rather than collapsing
    # into "no contract".
    assert any(i["path"] == f"queries/{qid}.json" for i in items)
    head = _commit_serialized(git_repo, "dev", items)
    loaded = load_resource_at(git_repo, head, "query", qid)
    assert loaded is not None
    out_config, _n, _r = loaded
    assert out_config.get("output_schema") == []


# ---------------------------------------------------------------------------
# Integration — push_env → pull_env carries flows + output_schema end-to-end
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def env_world(tmp_path, monkeypatch):
    """A real on-disk workspace + injected in-memory stores (push/pull world)."""
    monkeypatch.setenv("NUBI_GIT_WORKSPACE", str(tmp_path / "ws"))
    repo = InMemoryRepo()
    flow_store = InMemoryFlowStore()
    env_store = InMemoryEnvStore()
    set_flow_store(flow_store)
    set_env_store(env_store)
    try:
        yield {"repo": repo, "flows": flow_store, "envs": env_store}
    finally:
        set_flow_store(None)
        set_env_store(None)


@pytest.mark.asyncio
async def test_push_then_pull_round_trips_flow_and_output_schema(env_world):
    repo = env_world["repo"]
    flows = env_world["flows"]
    envs = env_world["envs"]
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    dev = await envs.create_environment(project_id, "dev", "Dev")
    qa = await envs.create_environment(project_id, "qa", "QA")

    # Seed a flow (sql+python+markdown) and pin it in dev.
    spec = _flow_spec()
    flow = await flows.create_flow(
        org_id=org_id, created_by=user_id, name=spec["name"], spec=spec,
        project_id=project_id,
    )
    flow_id = flow["id"]
    fv = await envs.create_version(
        org_id=org_id, project_id=project_id, kind="flow",
        resource_id=flow_id, config=spec, created_by=user_id,
    )
    await envs.set_pointer("flow", flow_id, dev["id"], fv["id"], promoted_by=user_id)

    # Seed a query carrying an output_schema and pin it in dev.
    qid = str(uuid.uuid4())
    qconfig = {
        "sql": "select 1 as id, 'x' as label",
        "datastore_id": "ds-1",
        "output_schema": [
            {"name": "id", "type": "number"},
            {"name": "label", "type": "text"},
        ],
    }
    qv = await envs.create_version(
        org_id=org_id, project_id=project_id, kind="query",
        resource_id=qid, config=qconfig, created_by=user_id,
    )
    await envs.set_pointer("query", qid, dev["id"], qv["id"], promoted_by=user_id)

    # PUSH dev → its branch ('dev').
    pushed = await push_env(
        org_id=org_id, project_id=project_id,
        env=await _env(envs, dev["id"]), repo=repo,
    )
    assert pushed["committed"] is True, pushed
    assert pushed["sha"]

    # The qa env's branch must start from the same commit so pull can ff.
    git = ProjectGit(
        Path(env_world_root()) / org_id / "projects" / project_id
    )
    git.create_branch_at("qa", pushed["sha"])

    # PULL into qa (first sync → imports everything on the branch).
    pulled = await pull_env(
        org_id=org_id, project_id=project_id,
        env=await _env(envs, qa["id"]), repo=repo, user_id=user_id,
    )
    assert pulled.get("pulled") is True, pulled
    assert pulled["updated"].get("flow") == 1, pulled
    assert pulled["updated"].get("query") == 1, pulled

    # Flow re-imported into qa with the SAME uuid (recovered from flow.toml).
    flow_ptr = await envs.get_pointer("flow", flow_id, qa["id"])
    assert flow_ptr is not None, "flow must be pinned in qa after pull"
    flow_ver = await envs.get_version_by_id(flow_ptr["version_id"])
    orig, _ = validate_flow_spec(spec)
    back, _ = validate_flow_spec(flow_ver["config"])
    assert back.model_dump() == orig.model_dump()

    # Query re-imported with its output_schema intact.
    q_ptr = await envs.get_pointer("query", qid, qa["id"])
    assert q_ptr is not None
    q_ver = await envs.get_version_by_id(q_ptr["version_id"])
    assert q_ver["config"]["output_schema"] == qconfig["output_schema"]
    assert q_ver["config"]["sql"] == qconfig["sql"]


def env_world_root() -> str:
    import os as _os

    return _os.environ["NUBI_GIT_WORKSPACE"]


async def _env(env_store, env_id):
    for e in env_store._envs.values():  # noqa: SLF001 — test-only introspection
        if e["id"] == env_id:
            return e
    raise AssertionError("env not found")
