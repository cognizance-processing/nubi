"""Tests for the ``connector_write`` task handler — design §4 (write side).

Strategy: like ``test_file_ingest``, we monkeypatch the resolution seams the
handler reaches through ``file_ingest`` (``_resolve_target`` /
``_resolve_staging``) onto local fakes so no live connector/warehouse is needed.

Coverage
--------
- stages an upstream ``{"rows": [...]}`` result and loads it via a PROMOTE
  (local-storage) target — end-to-end through the loader layer.
- loads via a MOCKED BULK target (warehouse) and asserts the COPY reached the
  client.
- upstream as a plain list of dicts and as an Arrow table.
- mode handling (default append; overwrite/merge accepted; invalid rejected).
- config validation errors (missing input / target).

NOT covered (needs live creds): a real warehouse COPY/load-job round-trip — the
client seam is the boundary; see test_bulk_loaders for the mocked-client dispatch.
"""

from __future__ import annotations

import io
import os
import tempfile
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import app.flows.handlers.file_ingest as fi
from app.connectors.base import file_capabilities
from app.flows.executor import TaskContext
from app.flows.handlers.connector_write import handle
from app.flows.loaders import LoadTarget
from app.lakehouse.managed import CentralStorage
from app.lakehouse.staging import StagingArea


# ---------------------------------------------------------------------------
# Fixture: wire seams to local fakes
# ---------------------------------------------------------------------------


@pytest.fixture()
def patched(monkeypatch):
    tmp = tempfile.mkdtemp()
    final_dir = os.path.join(tmp, "final")
    staging_root = os.path.join(tmp, "staging")
    os.makedirs(final_dir, exist_ok=True)
    os.makedirs(staging_root, exist_ok=True)

    state: dict = {"bulk_calls": [], "stream_rows": []}

    def _run(config, upstream, *, target="promote", run_id="run1", org_id="orgA"):
        central = CentralStorage(scheme="file", bucket=staging_root, creds={})
        monkeypatch.setattr(
            fi, "_resolve_staging", lambda org, rid: StagingArea(central=central, org_id=org, run_id=rid)
        )

        if target == "promote":
            from app.storage.local import LocalStorageClient

            client = LocalStorageClient(root=final_dir)

            def _final_key(staged_rel: str) -> str:
                obj = config["target"]["object"].replace(".", "/")
                leaf = staged_rel.rsplit("/", 1)[-1]
                return f"{obj}/{leaf}"

            def _mk_target(cid, obj, org):
                t = LoadTarget(object_name=obj, capabilities=file_capabilities(file_interface=True))
                t._promote_client = client  # type: ignore[attr-defined]
                t._final_key = _final_key  # type: ignore[attr-defined]
                return t

            monkeypatch.setattr(fi, "_resolve_target", _mk_target)

        elif target == "bulk":
            # A bulk warehouse target whose bulk callable is a mock capturing the
            # object name + row count (no live warehouse).
            def _mk_target(cid, obj, org):
                t = LoadTarget(
                    object_name=obj,
                    capabilities=file_capabilities(bulk_load_from=["file"]),
                )

                def _bulk(table: str) -> int:
                    state["bulk_calls"].append(table)
                    return 3  # rows the warehouse reports loaded

                # Pre-bind the bulk callable directly (not via resolve_bulk_target),
                # so _bind_promote (no _bulk_ctype set) leaves it untouched — this
                # isolates the connector_write → loader dispatch from the warehouse
                # driver, which is covered with a mocked client in test_bulk_loaders.
                t.bulk = _bulk
                return t

            monkeypatch.setattr(fi, "_resolve_target", _mk_target)

        else:  # stream
            def _stream(batches, table):
                n = 0
                for b in batches:
                    for r in b.to_pylist():
                        state["stream_rows"].append(r)
                        n += 1
                return n

            def _mk_target(cid, obj, org):
                return LoadTarget(
                    object_name=obj,
                    capabilities=file_capabilities(stream_load=True),
                    stream=_stream,
                )

            monkeypatch.setattr(fi, "_resolve_target", _mk_target)

        ctx = TaskContext(
            inputs={"upstream": upstream},
            now=datetime(2025, 1, 1, tzinfo=timezone.utc),
            org_id=org_id,
            run_id=run_id,
        )
        result = handle(config, ctx, {})
        return result, state, final_dir

    yield _run


def _cfg(mode="append", input_key="upstream", obj="raw.orders"):
    return {"input": input_key, "target": {"connector_id": "tgt1", "object": obj}, "mode": mode}


def _read_final_parquet(final_dir: str) -> list[dict]:
    rows: list[dict] = []
    for dp, _dn, fn in os.walk(final_dir):
        for f in fn:
            if f.endswith(".parquet"):
                rows.extend(pq.read_table(os.path.join(dp, f)).to_pylist())
    return rows


# ---------------------------------------------------------------------------
# Promote path (local object-storage target)
# ---------------------------------------------------------------------------


def test_connector_write_rows_promote(patched):
    upstream = {"rows": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}
    result, _state, final_dir = patched(_cfg(), upstream, target="promote")
    assert result["strategy"] == "promote"
    assert result["rows_written"] == 2
    assert result["mode"] == "append"
    assert result["target_object"] == "raw.orders"
    written = _read_final_parquet(final_dir)
    assert sorted(r["id"] for r in written) == [1, 2]


def test_connector_write_plain_list_upstream(patched):
    upstream = [{"id": 10}, {"id": 11}, {"id": 12}]
    result, _state, final_dir = patched(_cfg(), upstream)
    assert result["rows_written"] == 3
    assert len(_read_final_parquet(final_dir)) == 3


def test_connector_write_arrow_upstream(patched):
    table = pa.Table.from_pylist([{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}])
    result, _state, final_dir = patched(_cfg(), table)
    assert result["rows_written"] == 4
    assert len(_read_final_parquet(final_dir)) == 4


# ---------------------------------------------------------------------------
# Bulk path (mocked warehouse target)
# ---------------------------------------------------------------------------


def test_connector_write_bulk_target(patched):
    upstream = {"rows": [{"id": 1}, {"id": 2}, {"id": 3}]}
    # The loader picks bulk because file_capabilities(bulk_load_from=["file"])
    # intersects the local (file) staging scheme.
    result, state, _ = patched(_cfg(obj="db.raw_orders"), upstream, target="bulk")
    assert result["strategy"] == "bulk"
    assert result["rows_written"] == 3
    assert state["bulk_calls"] == ["db.raw_orders"]


# ---------------------------------------------------------------------------
# Stream path
# ---------------------------------------------------------------------------


def test_connector_write_stream_target(patched):
    upstream = {"rows": [{"id": i} for i in range(5)]}
    result, state, _ = patched(_cfg(), upstream, target="stream")
    assert result["strategy"] == "stream"
    assert result["rows_written"] == 5
    assert len(state["stream_rows"]) == 5


# ---------------------------------------------------------------------------
# Mode handling
# ---------------------------------------------------------------------------


def test_connector_write_overwrite_mode(patched):
    result, _s, _ = patched(_cfg(mode="overwrite"), {"rows": [{"id": 1}]})
    assert result["mode"] == "overwrite"


def test_connector_write_merge_mode_accepted(patched):
    result, _s, _ = patched(_cfg(mode="merge"), {"rows": [{"id": 1}]})
    assert result["mode"] == "merge"


def test_connector_write_invalid_mode_raises(patched):
    with pytest.raises(ValueError, match="Invalid mode"):
        patched(_cfg(mode="upsert"), {"rows": [{"id": 1}]})


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_connector_write_missing_input_raises(patched):
    cfg = {"target": {"connector_id": "t", "object": "o"}}
    with pytest.raises(ValueError, match="requires 'input'"):
        patched(cfg, {"rows": []})


def test_connector_write_missing_target_object_raises(patched):
    cfg = {"input": "upstream", "target": {"connector_id": "t"}}
    with pytest.raises(ValueError, match="target.object"):
        patched(cfg, {"rows": []})


def test_connector_write_input_not_in_inputs_raises(patched):
    cfg = _cfg(input_key="nope")
    with pytest.raises(KeyError):
        patched(cfg, {"rows": [{"id": 1}]})
