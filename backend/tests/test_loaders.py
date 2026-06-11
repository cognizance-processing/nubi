"""Tests for the loader layer (``app.flows.loaders``) — design §4.

Coverage
--------
- ``choose_strategy``: file_interface → promote; stream_load → stream; bulk seam
  is OFF in phase 1 so a bulk-capable target still streams.
- ``load_staged`` promote path into a local-storage target.
- ``load_staged`` stream path into a fake stream target (Parquet batches).
- manifest verification rejects a tampered staged file BEFORE load.
- staging prefix is server-pinned (org/run) and a ``..`` escape is neutralised.
"""

from __future__ import annotations

import io
import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.flows.loaders import (
    LoadTarget,
    _BULK_LOAD_ENABLED,
    choose_strategy,
    load_staged,
    read_parquet_batches,
)
from app.lakehouse.managed import CentralStorage, org_staging_prefix
from app.lakehouse.staging import (
    ManifestVerificationError,
    StagingArea,
    sha256_bytes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parquet_bytes(rows: list[dict]) -> bytes:
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _local_staging(tmp: str, org="org1", run="run1") -> StagingArea:
    central = CentralStorage(scheme="file", bucket=tmp, creds={})
    return StagingArea(central=central, org_id=org, run_id=run)


# ---------------------------------------------------------------------------
# choose_strategy
# ---------------------------------------------------------------------------


def test_choose_strategy_promote_for_file_interface():
    caps = {"file_interface": True, "bulk_load_from": [], "stream_load": False}
    assert choose_strategy(caps, "file") == "promote"


def test_choose_strategy_stream_for_stream_load():
    caps = {"file_interface": False, "bulk_load_from": [], "stream_load": True}
    assert choose_strategy(caps, "file") == "stream"


def test_choose_strategy_bulk_seam_off_falls_back_to_stream():
    # A bulk-capable target still STREAMS in phase 1 (bulk seam disabled).
    assert _BULK_LOAD_ENABLED is False
    caps = {"file_interface": False, "bulk_load_from": ["s3"], "stream_load": True}
    assert choose_strategy(caps, "s3") == "stream"


# ---------------------------------------------------------------------------
# Staging prefix pinning
# ---------------------------------------------------------------------------


def test_staging_prefix_is_server_pinned():
    assert org_staging_prefix("orgA", "runX") == "orgs/orgA/staging/runX/"


def test_staging_key_cannot_escape_prefix():
    with tempfile.TemporaryDirectory() as tmp:
        area = _local_staging(tmp, org="orgA", run="runX")
        # A traversal attempt is neutralised: the key stays under the prefix.
        key = area._key("../../etc/passwd")
        assert key.startswith("orgs/orgA/staging/runX/")
        assert ".." not in key


# ---------------------------------------------------------------------------
# Manifest verification
# ---------------------------------------------------------------------------


def test_manifest_verify_passes_for_untampered_files():
    with tempfile.TemporaryDirectory() as tmp:
        area = _local_staging(tmp)
        data = _parquet_bytes([{"id": 1}, {"id": 2}])
        entry = area.write_bytes(data, "orders.parquet")
        manifest = area.build_manifest([entry], {"orders.parquet": 2})
        # Should not raise.
        area.verify(manifest)


def test_manifest_verify_rejects_tampered_file():
    with tempfile.TemporaryDirectory() as tmp:
        area = _local_staging(tmp)
        data = _parquet_bytes([{"id": 1}])
        entry = area.write_bytes(data, "orders.parquet")
        manifest = area.build_manifest([entry], {"orders.parquet": 1})

        # Tamper with the staged bytes on disk AFTER the manifest was built.
        staged_path = os.path.join(tmp, area._key("orders.parquet"))
        with open(staged_path, "wb") as fh:
            fh.write(b"corrupted-bytes")

        with pytest.raises(ManifestVerificationError) as exc:
            area.verify(manifest)
        assert exc.value.path == "orders.parquet"


def test_manifest_verify_rejects_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        area = _local_staging(tmp)
        # Build a manifest entry for a file we never wrote.
        from app.lakehouse.staging import ManifestEntry

        fake = ManifestEntry(path="ghost.parquet", size=10, sha256=sha256_bytes(b"x" * 10))
        manifest = area.build_manifest([fake], {})
        with pytest.raises(ManifestVerificationError):
            area.verify(manifest)


# ---------------------------------------------------------------------------
# load_staged — promote
# ---------------------------------------------------------------------------


def test_load_staged_promote_to_local_target():
    with tempfile.TemporaryDirectory() as tmp:
        area = _local_staging(tmp)
        data = _parquet_bytes([{"id": 1}, {"id": 2}, {"id": 3}])
        entry = area.write_bytes(data, "orders.parquet")
        manifest = area.build_manifest([entry], {"orders.parquet": 3})

        promoted: list[str] = []

        def _promote(staged_rel: str, object_name: str) -> str:
            promoted.append(staged_rel)
            return f"file://{tmp}/final/{object_name}/{staged_rel}"

        target = LoadTarget(
            object_name="raw.orders",
            capabilities={"file_interface": True, "bulk_load_from": [], "stream_load": False},
            promote=_promote,
        )
        result = load_staged(area, manifest, target)
        assert result["strategy"] == "promote"
        assert result["rows_loaded"] == 3
        assert result["files_loaded"] == 1
        assert promoted == ["orders.parquet"]
        assert result["final_uris"][0].endswith("raw.orders/orders.parquet")


# ---------------------------------------------------------------------------
# load_staged — stream
# ---------------------------------------------------------------------------


def test_load_staged_stream_to_fake_target():
    with tempfile.TemporaryDirectory() as tmp:
        area = _local_staging(tmp)
        rows = [{"id": i, "name": f"n{i}"} for i in range(5)]
        entry = area.write_bytes(_parquet_bytes(rows), "orders.parquet")
        manifest = area.build_manifest([entry], {"orders.parquet": 5})

        captured_rows: list[dict] = []

        def _stream(batches, table: str) -> int:
            assert table == "raw.orders"
            n = 0
            for batch in batches:
                for r in batch.to_pylist():
                    captured_rows.append(r)
                    n += 1
            return n

        target = LoadTarget(
            object_name="raw.orders",
            capabilities={"file_interface": False, "bulk_load_from": [], "stream_load": True},
            stream=_stream,
        )
        result = load_staged(area, manifest, target)
        assert result["strategy"] == "stream"
        assert result["rows_loaded"] == 5
        assert len(captured_rows) == 5
        assert captured_rows[0] == {"id": 0, "name": "n0"}


def test_load_staged_verifies_before_loading():
    # A tampered file must abort the load (verify runs first).
    with tempfile.TemporaryDirectory() as tmp:
        area = _local_staging(tmp)
        entry = area.write_bytes(_parquet_bytes([{"id": 1}]), "orders.parquet")
        manifest = area.build_manifest([entry], {"orders.parquet": 1})
        staged_path = os.path.join(tmp, area._key("orders.parquet"))
        with open(staged_path, "wb") as fh:
            fh.write(b"corrupt")

        called = {"promote": False}

        def _promote(rel, obj):
            called["promote"] = True
            return "x"

        target = LoadTarget(
            object_name="raw.orders",
            capabilities={"file_interface": True, "bulk_load_from": [], "stream_load": False},
            promote=_promote,
        )
        with pytest.raises(ManifestVerificationError):
            load_staged(area, manifest, target)
        assert called["promote"] is False  # never promoted tampered data


def test_read_parquet_batches_chunks():
    rows = [{"id": i} for i in range(25)]
    data = _parquet_bytes(rows)
    batches = list(read_parquet_batches(data, batch_rows=10))
    assert sum(b.num_rows for b in batches) == 25
