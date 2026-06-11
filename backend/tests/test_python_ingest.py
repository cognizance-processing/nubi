"""Tests for Python API ingestion ergonomics — design §6 (Phase 2).

Covers the additions wired into the ``python`` task kind:

* ``ctx.staging.write(df_or_batches, "dest/", format="parquet") -> manifest`` —
  lands Parquet under the server-pinned ``orgs/<org>/staging/<run>/`` prefix and
  returns a verifiable ``{files:[{path,size,sha256}], row_counts}`` manifest.
* prefix-escape attempts (``../``) are neutralised — staged keys stay pinned.
* watermark advance from a python return (``{"watermark": ...}``) persists ONLY
  on success and is NOT clobbered when the return omits the key.
* optional ``target`` on a python task auto-loads the staged manifest via the
  loader layer.
* an ingest TEMPLATE snippet parses + runs against a fake source.

The python handler runs real subprocesses, so these are slower integration-style
tests.  Staging is pointed at a local temp dir via ``NUBI_STAGING_DIR`` so
``get_staging_area`` resolves a :class:`StagingArea` without any cloud bucket.
"""

from __future__ import annotations

import os
import tempfile

import pyarrow.parquet as pq
import pytest

from app.flows.executor import TaskContext
from app.flows.registry import _handle_python
from app.flows.runtime import _persist_watermark, _uses_watermark


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def staging_dir(monkeypatch):
    """Point staging at a local temp dir (NUBI_STAGING_DIR) and yield its path."""
    tmp = tempfile.mkdtemp(prefix="nubi-stage-test-")
    monkeypatch.setenv("NUBI_STAGING_DIR", tmp)
    # Ensure no S3 / dedicated-bucket env shadows the local dir.
    for k in ("NUBI_STAGING_BUCKET_URI",):
        monkeypatch.delenv(k, raising=False)
    yield tmp


def _ctx(org="orgA", run="run1", watermark=None):
    return TaskContext(inputs={}, org_id=org, run_id=run, watermark=watermark)


def _staged_files(staging_dir: str, org="orgA", run="run1") -> list[str]:
    base = os.path.join(staging_dir, "orgs", org, "staging", run)
    out: list[str] = []
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            out.append(os.path.join(dp, f))
    return out


# ---------------------------------------------------------------------------
# ctx.staging.write — lands Parquet + returns a verifiable manifest
# ---------------------------------------------------------------------------


def test_staging_write_lands_parquet_under_run_prefix(staging_dir):
    code = (
        "rows = [{'id': 1, 'v': 'a'}, {'id': 2, 'v': 'b'}, {'id': 3, 'v': 'c'}]\n"
        "manifest = staging.write(rows, 'orders/', format='parquet')\n"
        "result = {'rows': manifest['row_counts'], 'mref': manifest}\n"
    )
    out = _handle_python({"code": code}, _ctx(), {})

    # Returned to the parent: a server-recorded staging summary.
    staging = out["staging"]
    assert staging["prefix"] == "orgs/orgA/staging/run1/"
    assert staging["total_rows"] == 3
    assert len(staging["files"]) == 1
    entry = staging["files"][0]
    assert entry["path"].startswith("orders/") and entry["path"].endswith(".parquet")
    assert entry["size"] > 0 and len(entry["sha256"]) == 64

    # The bytes actually landed under orgs/<org>/staging/<run>/orders/...
    files = _staged_files(staging_dir)
    assert len(files) == 1
    assert "orders" in files[0]
    rows = pq.read_table(files[0]).to_pylist()
    assert len(rows) == 3 and rows[0]["id"] == 1


def test_staging_write_accepts_pyarrow_batches(staging_dir):
    code = (
        "import pyarrow as pa\n"
        "tbl = pa.table({'id': [1, 2], 'n': ['x', 'y']})\n"
        "batches = tbl.to_batches()\n"
        "manifest = staging.write(batches, 'events/', format='parquet')\n"
        "result = {'rows': manifest['row_counts']}\n"
    )
    out = _handle_python({"code": code}, _ctx(), {})
    assert out["staging"]["total_rows"] == 2
    files = _staged_files(staging_dir)
    assert len(files) == 1 and "events" in files[0]


def test_staging_write_accepts_dataframe(staging_dir):
    code = (
        "import pandas as pd\n"
        "df = pd.DataFrame({'id': [1, 2, 3, 4], 'n': ['a', 'b', 'c', 'd']})\n"
        "manifest = staging.write(df, 'customers/', format='parquet')\n"
        "result = {'rows': manifest['row_counts']}\n"
    )
    out = _handle_python({"code": code}, _ctx(), {})
    assert out["staging"]["total_rows"] == 4


# ---------------------------------------------------------------------------
# Prefix-escape attempt is blocked
# ---------------------------------------------------------------------------


def test_staging_write_prefix_escape_is_neutralised(staging_dir):
    # User code tries to escape the pinned prefix with ../../etc.
    code = (
        "rows = [{'id': 1}]\n"
        "manifest = staging.write(rows, '../../../../etc/evil/', format='parquet')\n"
        "result = {'p': manifest['files'][0]['path']}\n"
    )
    out = _handle_python({"code": code}, _ctx(), {})

    # Every staged object stays under the org/run prefix — nothing escaped.
    base = os.path.abspath(os.path.join(staging_dir, "orgs", "orgA", "staging", "run1"))
    for f in _staged_files(staging_dir):
        assert os.path.abspath(f).startswith(base), f"escaped: {f}"
    # And nothing was written outside the staging root at all.
    for dp, _dn, fn in os.walk(staging_dir):
        for f in fn:
            assert "etc" not in dp or dp.startswith(base)
    # The recorded path has the .. segments stripped.
    assert ".." not in out["staging"]["files"][0]["path"]


# ---------------------------------------------------------------------------
# Watermark advance from a python return (success-only, no clobber)
# ---------------------------------------------------------------------------


class _FakeStore:
    """Minimal store capturing set_watermark calls."""

    def __init__(self):
        self.marks: dict[tuple, str] = {}

    async def set_watermark(self, flow_id, task_key, env, mark):
        self.marks[(flow_id, task_key, env)] = mark


@pytest.mark.asyncio
async def test_python_watermark_persists_on_success():
    store = _FakeStore()
    task_spec = {"kind": "python", "config": {"code": "result = {}"}}
    assert _uses_watermark(task_spec) is True  # python is watermark-eligible

    flow_run = {"flow_id": "flow1", "env": "prod"}
    task_run = {"task_key": "pull"}
    result = {"rows": {"orders/part.parquet": 10}, "watermark": "2025-06-01T00:00:00+00:00"}

    await _persist_watermark(store, flow_run, task_run, task_spec, result)
    assert store.marks[("flow1", "pull", "prod")] == "2025-06-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_python_watermark_not_clobbered_when_omitted():
    store = _FakeStore()
    task_spec = {"kind": "python", "config": {"code": "result = {}"}}
    flow_run = {"flow_id": "flow1", "env": "prod"}
    task_run = {"task_key": "pull"}

    # Return omits `watermark` → the stored mark must be left untouched.
    await _persist_watermark(store, flow_run, task_run, task_spec, {"rows": 5})
    assert store.marks == {}


@pytest.mark.asyncio
async def test_materialize_watermark_uses_new_watermark_not_watermark_key():
    # A non-python task that happens to carry a `watermark` key must NOT persist
    # it (only `new_watermark` advances materialize/file_ingest marks).
    store = _FakeStore()
    task_spec = {
        "kind": "query",
        "config": {"materialized": {"kind": "incremental"}},
    }
    flow_run = {"flow_id": "flow1", "env": "prod"}
    task_run = {"task_key": "m"}
    await _persist_watermark(store, flow_run, task_run, task_spec, {"watermark": "x"})
    assert store.marks == {}


def test_ctx_watermark_injected_into_python_cell(staging_dir):
    # The stored mark is readable inside the cell as `watermark`.
    code = "result = {'seen': watermark}"
    out = _handle_python({"code": code}, _ctx(watermark="2025-01-01T00:00:00+00:00"), {})
    assert out["seen"] == "2025-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Optional target auto-loads via the loader layer
# ---------------------------------------------------------------------------


def test_python_target_auto_loads_via_loader(staging_dir, monkeypatch):
    import app.flows.registry as reg
    from app.connectors.base import file_capabilities
    from app.flows.loaders import LoadTarget

    captured: list[dict] = []

    def _fake_stream(batches, table):
        n = 0
        for b in batches:
            for r in b.to_pylist():
                captured.append(r)
                n += 1
        return n

    def _fake_resolve_target(connector_id, object_name, org_id):
        return LoadTarget(
            object_name=object_name,
            capabilities=file_capabilities(stream_load=True),
            stream=_fake_stream,
        )

    # Patch the file_ingest resolver the python target path imports.
    import app.flows.handlers.file_ingest as fi
    monkeypatch.setattr(fi, "_resolve_target", _fake_resolve_target)

    code = (
        "rows = [{'id': 1, 'n': 'a'}, {'id': 2, 'n': 'b'}]\n"
        "staging.write(rows, 'orders/', format='parquet')\n"
        "result = {'ok': True}\n"
    )
    config = {
        "code": code,
        "target": {"connector_id": "tgt1", "object": "raw.orders"},
    }
    out = _handle_python(config, _ctx(), {})

    load = out["staging"]["load"]
    assert load["strategy"] == "stream"
    assert load["rows_loaded"] == 2
    assert len(captured) == 2
    assert captured[0] == {"id": 1, "n": "a"}


def test_python_no_target_is_back_compat(staging_dir):
    # Without a declared target the staged manifest is recorded but NOT loaded.
    code = "rows = [{'id': 1}]\nstaging.write(rows, 'x/', format='parquet')\nresult = {}"
    out = _handle_python({"code": code}, _ctx(), {})
    assert "load" not in out["staging"]
    assert out["staging"]["total_rows"] == 1


def test_python_without_staging_is_unchanged(staging_dir):
    # A plain python cell that never calls staging.write returns no staging key.
    out = _handle_python({"code": "result = {'value': 42}"}, _ctx(), {})
    assert out["value"] == 42
    assert "staging" not in out


# ---------------------------------------------------------------------------
# Template snippet parses + runs against a fake source
# ---------------------------------------------------------------------------


def test_templates_listed_and_well_formed():
    from app.flows.ingest_templates import get_ingest_template, list_ingest_templates

    templates = list_ingest_templates()
    ids = {t["id"] for t in templates}
    assert {
        "rest_offset_paginated",
        "rest_cursor_paginated",
        "rest_oauth_refresh",
        "rest_since_timestamp_incremental",
    } <= ids
    for t in templates:
        assert t["code"] and t["title"] and t["description"]
        # Creds via secrets[...] never inlined; staging.write present.
        assert "secrets[" in t["code"]
        assert "staging.write(" in t["code"]
        # compiles as valid python.
        compile(t["code"], f"<template:{t['id']}>", "exec")
    # incremental template reads + returns the watermark.
    inc = get_ingest_template("rest_since_timestamp_incremental")
    assert "watermark" in inc.code


def test_since_timestamp_template_runs_against_fake_source(staging_dir, monkeypatch):
    """The incremental template, rewired to a FAKE httpx, stages + advances mark."""
    from app.flows.ingest_templates import get_ingest_template

    # Build a self-contained cell: stub httpx in-process, then run the template's
    # body so it exercises secrets / staging.write / watermark return for real.
    template = get_ingest_template("rest_since_timestamp_incremental")
    # The template references secrets["EXAMPLE_API_TOKEN"]; provide it + a fake
    # httpx by prepending a stub module shim into the cell code.
    stub = (
        "import sys, types\n"
        "_m = types.ModuleType('httpx')\n"
        "class _Resp:\n"
        "    def __init__(self, data): self._data = data\n"
        "    def raise_for_status(self): pass\n"
        "    def json(self): return {'data': self._data}\n"
        "class _Client:\n"
        "    def __init__(self, *a, **k): pass\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *a): return False\n"
        "    def get(self, url, params=None, headers=None):\n"
        "        return _Resp([{'id': 1, 'updated_at': '2025-06-02T00:00:00+00:00'},\n"
        "                      {'id': 2, 'updated_at': '2025-06-03T00:00:00+00:00'}])\n"
        "    def post(self, url, data=None): return _Resp([])\n"
        "_m.Client = _Client\n"
        "sys.modules['httpx'] = _m\n"
    )
    code = stub + template.code

    ctx = TaskContext(
        inputs={},
        org_id="orgA",
        run_id="run1",
        watermark=None,
        secrets={"EXAMPLE_API_TOKEN": "tok-123"},
    )
    out = _handle_python({"code": code}, ctx, {})

    # Watermark advanced to the newest record's updated_at.
    assert out["watermark"] == "2025-06-03T00:00:00+00:00"
    # Rows staged as Parquet under the run prefix.
    assert out["staging"]["total_rows"] == 2
    files = _staged_files(staging_dir)
    assert len(files) == 1 and "orders_incremental" in files[0]
