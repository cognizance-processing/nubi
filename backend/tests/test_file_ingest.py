"""Tests for the ``file_ingest`` task handler — design §3.

Strategy: a FAKE file connector implementing ``FileConnectorMixin`` (so we do
not hard-depend on the parallel file-connector agent), a LOCAL staging area, and
a target that is either a local-storage *promote* target or a fake *stream*
target.  The handler's resolution seams (``_resolve_source_connector`` /
``_resolve_target`` / ``_resolve_staging``) are monkeypatched onto these fakes.

Coverage
--------
- csv / json / ndjson / parquet ingest into a promote (local-storage) target.
- zip expansion (format=zip applies inner_format).
- stream target receives Parquet record-batches.
- mtime watermark advance (only newly-mtimed files ingested; mark advances).
- filename watermark advance (lexicographic).
- watermark is NOT advanced when nothing new (returns None).
- post_action move + delete via the connector.
- manifest tamper would be rejected (covered in test_loaders; here we assert the
  happy path verifies fine).
- config validation errors.
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any, BinaryIO

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import app.flows.handlers.file_ingest as fi
from app.connectors.base import FileConnectorMixin, FileStat, file_capabilities
from app.flows.executor import TaskContext
from app.flows.handlers.file_ingest import handle
from app.flows.loaders import LoadTarget
from app.lakehouse.managed import CentralStorage
from app.lakehouse.staging import StagingArea


# ---------------------------------------------------------------------------
# Fake file connector
# ---------------------------------------------------------------------------


class FakeFileConnector(FileConnectorMixin):
    """In-memory file connector for tests (path → (bytes, mtime))."""

    def __init__(self, files: dict[str, tuple[bytes, datetime | None]]):
        self._files = dict(files)
        self.moved: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def capabilities(self) -> dict[str, Any]:
        return file_capabilities(file_interface=True)

    def list_files(self, pattern: str, since: datetime | None = None) -> list[FileStat]:
        out = []
        for path, (data, mtime) in self._files.items():
            if since is not None and mtime is not None and mtime <= since:
                continue
            out.append(FileStat(path=path, size=len(data), mtime=mtime))
        return sorted(out, key=lambda f: f.path)

    def open(self, path: str) -> BinaryIO:
        return io.BytesIO(self._files[path][0])

    def move(self, src: str, dst: str) -> None:
        self.moved.append((src, dst))
        self._files[dst] = self._files.pop(src)

    def delete(self, path: str) -> None:
        self.deleted.append(path)
        self._files.pop(path, None)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _csv(rows: list[dict]) -> bytes:
    import csv

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def _ndjson(rows: list[dict]) -> bytes:
    import json

    return "\n".join(json.dumps(r) for r in rows).encode()


def _json(rows: list[dict]) -> bytes:
    import json

    return json.dumps(rows).encode()


def _parquet(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    return buf.getvalue()


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture()
def patched(monkeypatch):
    """Wire the handler's resolution seams to local fakes; yield a controller.

    Returns a callable ``run(connector, config, *, target='promote', watermark=None)``
    that runs ``handle`` and returns ``(result, ctx, final_dir, stream_capture)``.
    """
    tmp = tempfile.mkdtemp()
    final_dir = os.path.join(tmp, "final")
    staging_root = os.path.join(tmp, "staging")
    os.makedirs(final_dir, exist_ok=True)
    os.makedirs(staging_root, exist_ok=True)

    stream_capture: list[dict] = []

    def _run(connector, config, *, target="promote", watermark=None, run_id="run1"):
        # Source resolution → our fake connector.
        monkeypatch.setattr(fi, "_resolve_source_connector", lambda cid, org: connector)

        # Staging resolution → a local StagingArea.
        central = CentralStorage(scheme="file", bucket=staging_root, creds={})

        def _stage(org, rid):
            return StagingArea(central=central, org_id=org, run_id=rid)

        monkeypatch.setattr(fi, "_resolve_staging", _stage)

        # Target resolution → promote (local storage) or stream (fake).
        if target == "promote":
            from app.storage.local import LocalStorageClient

            client = LocalStorageClient(root=final_dir)

            def _final_key(staged_rel: str) -> str:
                obj = config["target"]["object"].replace(".", "/")
                leaf = staged_rel.rsplit("/", 1)[-1]
                return f"{obj}/{leaf}"

            def _mk_target(cid, obj, org):
                t = LoadTarget(
                    object_name=obj,
                    capabilities=file_capabilities(file_interface=True),
                )
                t._promote_client = client  # type: ignore[attr-defined]
                t._final_key = _final_key  # type: ignore[attr-defined]
                return t

            monkeypatch.setattr(fi, "_resolve_target", _mk_target)
        else:
            def _stream(batches, table):
                n = 0
                for b in batches:
                    for r in b.to_pylist():
                        stream_capture.append(r)
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
            inputs={},
            now=datetime(2025, 1, 1, tzinfo=timezone.utc),
            org_id="orgA",
            run_id=run_id,
            watermark=watermark,
        )
        result = handle(config, ctx, {})
        return result, ctx, final_dir, stream_capture

    yield _run


def _cfg(fmt="auto", strategy="none", post_action="none", inner_format="csv", target_obj="raw.orders"):
    return {
        "source": {"connector_id": "src1", "path": "outbound/*"},
        "format": fmt,
        "inner_format": inner_format,
        "target": {"connector_id": "tgt1", "object": target_obj},
        "mode": "append",
        "incremental": {"strategy": strategy},
        "post_action": post_action,
    }


def _read_final_parquet(final_dir: str) -> list[dict]:
    rows: list[dict] = []
    for dp, _dn, fn in os.walk(final_dir):
        for f in fn:
            if f.endswith(".parquet"):
                rows.extend(pq.read_table(os.path.join(dp, f)).to_pylist())
    return rows


# ---------------------------------------------------------------------------
# Format ingest → promote
# ---------------------------------------------------------------------------


def test_csv_ingest_promote(patched):
    conn = FakeFileConnector({"outbound/a.csv": (_csv([{"id": "1"}, {"id": "2"}]), None)})
    result, _ctx, final_dir, _ = patched(conn, _cfg(fmt="csv"))
    assert result["files_ingested"] == 1
    assert result["rows_ingested"] == 2
    assert result["strategy"] == "promote"
    assert len(_read_final_parquet(final_dir)) == 2


def test_json_ingest_promote(patched):
    conn = FakeFileConnector({"outbound/a.json": (_json([{"id": 1}, {"id": 2}, {"id": 3}]), None)})
    result, _ctx, final_dir, _ = patched(conn, _cfg(fmt="json"))
    assert result["rows_ingested"] == 3
    assert len(_read_final_parquet(final_dir)) == 3


def test_ndjson_ingest_promote(patched):
    conn = FakeFileConnector({"outbound/a.ndjson": (_ndjson([{"id": 1}, {"id": 2}]), None)})
    result, _ctx, _final, _ = patched(conn, _cfg(fmt="ndjson"))
    assert result["rows_ingested"] == 2


def test_parquet_ingest_promote(patched):
    conn = FakeFileConnector({"outbound/a.parquet": (_parquet([{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]), None)})
    result, _ctx, final_dir, _ = patched(conn, _cfg(fmt="parquet"))
    assert result["rows_ingested"] == 4
    assert len(_read_final_parquet(final_dir)) == 4


def test_auto_format_detects_by_extension(patched):
    conn = FakeFileConnector({"outbound/a.csv": (_csv([{"id": "1"}]), None)})
    result, _ctx, _final, _ = patched(conn, _cfg(fmt="auto"))
    assert result["rows_ingested"] == 1


# ---------------------------------------------------------------------------
# Zip expansion
# ---------------------------------------------------------------------------


def test_zip_expansion_applies_inner_format(patched):
    members = {
        "part1.csv": _csv([{"id": "1"}, {"id": "2"}]),
        "part2.csv": _csv([{"id": "3"}]),
    }
    conn = FakeFileConnector({"outbound/bundle.zip": (_zip(members), None)})
    result, _ctx, final_dir, _ = patched(conn, _cfg(fmt="zip", inner_format="csv"))
    # One source file, but multiple staged objects from the archive.
    assert result["files_ingested"] == 1
    assert result["rows_ingested"] == 3
    assert len(result["manifest"]["files"]) == 2
    assert len(_read_final_parquet(final_dir)) == 3


# ---------------------------------------------------------------------------
# Stream target
# ---------------------------------------------------------------------------


def test_stream_target_receives_batches(patched):
    conn = FakeFileConnector({"outbound/a.csv": (_csv([{"id": "1", "n": "x"}, {"id": "2", "n": "y"}]), None)})
    result, _ctx, _final, captured = patched(conn, _cfg(fmt="csv"), target="stream")
    assert result["strategy"] == "stream"
    assert result["rows_ingested"] == 2
    assert len(captured) == 2
    assert captured[0] == {"id": "1", "n": "x"}


# ---------------------------------------------------------------------------
# Watermarks
# ---------------------------------------------------------------------------


def test_mtime_watermark_advances_and_filters(patched):
    t_old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t_new = datetime(2025, 6, 1, tzinfo=timezone.utc)
    conn = FakeFileConnector({
        "outbound/old.csv": (_csv([{"id": "1"}]), t_old),
        "outbound/new.csv": (_csv([{"id": "2"}]), t_new),
    })
    # Mark sits between old and new → only new.csv ingested.
    mark = datetime(2025, 3, 1, tzinfo=timezone.utc).isoformat()
    result, _ctx, _final, _ = patched(conn, _cfg(fmt="csv", strategy="mtime"), watermark=mark)
    assert result["files_ingested"] == 1
    assert result["new_watermark"] == t_new.isoformat()


def test_mtime_watermark_no_new_files_returns_none(patched):
    t_old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    conn = FakeFileConnector({"outbound/old.csv": (_csv([{"id": "1"}]), t_old)})
    mark = datetime(2025, 3, 1, tzinfo=timezone.utc).isoformat()
    result, _ctx, _final, _ = patched(conn, _cfg(fmt="csv", strategy="mtime"), watermark=mark)
    assert result["files_ingested"] == 0
    # Nothing new → no advance (runtime must not clobber the stored mark).
    assert result["new_watermark"] is None


def test_filename_watermark_advances(patched):
    conn = FakeFileConnector({
        "outbound/2025-01.csv": (_csv([{"id": "1"}]), None),
        "outbound/2025-02.csv": (_csv([{"id": "2"}]), None),
        "outbound/2025-03.csv": (_csv([{"id": "3"}]), None),
    })
    # Mark = last ingested filename → only files lexicographically after it.
    result, _ctx, _final, _ = patched(
        conn, _cfg(fmt="csv", strategy="filename"), watermark="outbound/2025-01.csv"
    )
    assert result["files_ingested"] == 2
    assert result["new_watermark"] == "outbound/2025-03.csv"


# ---------------------------------------------------------------------------
# post_action
# ---------------------------------------------------------------------------


def test_post_action_delete(patched):
    conn = FakeFileConnector({"outbound/a.csv": (_csv([{"id": "1"}]), None)})
    result, _ctx, _final, _ = patched(conn, _cfg(fmt="csv", post_action="delete"))
    assert result["post_action"]["action"] == "delete"
    assert conn.deleted == ["outbound/a.csv"]


def test_post_action_move(patched):
    conn = FakeFileConnector({"outbound/a.csv": (_csv([{"id": "1"}]), None)})
    result, _ctx, _final, _ = patched(conn, _cfg(fmt="csv", post_action="move:archive"))
    assert result["post_action"]["action"] == "move"
    assert conn.moved == [("outbound/a.csv", "archive/a.csv")]


def test_post_action_none_leaves_source(patched):
    conn = FakeFileConnector({"outbound/a.csv": (_csv([{"id": "1"}]), None)})
    result, _ctx, _final, _ = patched(conn, _cfg(fmt="csv", post_action="none"))
    assert result["post_action"]["action"] == "none"
    assert conn.deleted == []
    assert conn.moved == []


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_missing_source_connector_id_raises(patched):
    conn = FakeFileConnector({})
    cfg = _cfg()
    cfg["source"] = {"path": "x"}
    with pytest.raises(ValueError, match="source.connector_id"):
        patched(conn, cfg)


def test_missing_target_object_raises(patched):
    conn = FakeFileConnector({})
    cfg = _cfg()
    cfg["target"] = {"connector_id": "tgt1"}
    with pytest.raises(ValueError, match="target.object"):
        patched(conn, cfg)


def test_invalid_format_raises(patched):
    conn = FakeFileConnector({})
    with pytest.raises(ValueError, match="Invalid format"):
        patched(conn, _cfg(fmt="xml"))


def test_invalid_strategy_raises(patched):
    conn = FakeFileConnector({})
    with pytest.raises(ValueError, match="Invalid incremental.strategy"):
        patched(conn, _cfg(strategy="bogus"))


# ---------------------------------------------------------------------------
# SECURITY: decompression-DoS / zip-bomb + oversized source-file guards
# ---------------------------------------------------------------------------


def _zip_with_big_entry(uncompressed_bytes: int) -> bytes:
    """A small archive whose single entry decompresses to *uncompressed_bytes*."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Highly compressible payload (all zeros) → small archive, big expansion.
        zf.writestr("bomb.csv", b"\x00" * uncompressed_bytes)
    return buf.getvalue()


def test_expand_zip_rejects_oversized_entry(monkeypatch):
    monkeypatch.setattr(fi, "_MAX_ZIP_ENTRY_BYTES", 1024)
    data = _zip_with_big_entry(50_000)  # expands well past the 1 KiB cap
    with pytest.raises(fi.IngestLimitExceeded, match="per-entry limit"):
        list(fi._expand_zip(data, "csv"))


def test_expand_zip_rejects_too_many_entries(monkeypatch):
    monkeypatch.setattr(fi, "_MAX_ZIP_ENTRIES", 2)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(5):
            zf.writestr(f"f{i}.csv", b"id\n1\n")
    with pytest.raises(fi.IngestLimitExceeded, match="entries"):
        list(fi._expand_zip(buf.getvalue(), "csv"))


def test_expand_zip_rejects_total_expansion(monkeypatch):
    monkeypatch.setattr(fi, "_MAX_ZIP_ENTRY_BYTES", 10_000_000)
    monkeypatch.setattr(fi, "_MAX_ZIP_TOTAL_BYTES", 4096)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(4):
            zf.writestr(f"f{i}.csv", b"\x00" * 4096)  # each under per-entry cap
    with pytest.raises(fi.IngestLimitExceeded, match="total limit"):
        list(fi._expand_zip(buf.getvalue(), "csv"))


def test_expand_zip_allows_within_limits():
    members = {"a.csv": _csv([{"id": "1"}]), "b.csv": _csv([{"id": "2"}])}
    out = list(fi._expand_zip(_zip(members), "csv"))
    assert {name for name, _b, _f in out} == {"a.csv", "b.csv"}


def test_read_capped_rejects_oversized_source(monkeypatch):
    monkeypatch.setattr(fi, "_MAX_SOURCE_FILE_BYTES", 1024)
    conn = FakeFileConnector({"big.csv": (b"x" * 5000, None)})
    with pytest.raises(fi.IngestLimitExceeded, match="size limit"):
        fi._read_capped(conn, "big.csv")


def test_read_capped_allows_within_limit():
    conn = FakeFileConnector({"ok.csv": (b"hello", None)})
    assert fi._read_capped(conn, "ok.csv") == b"hello"


# ---------------------------------------------------------------------------
# SECURITY: Postgres COPY statement — table + column injection
# ---------------------------------------------------------------------------


class _FakeCopy:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, row):
        self.rows.append(row)


class _FakePgCursor:
    def __init__(self):
        self.copy_sql = None

    def copy(self, sql):
        self.copy_sql = sql
        return _FakeCopy()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakePgConn:
    def __init__(self):
        self.cur = _FakePgCursor()

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_psycopg(monkeypatch, conn):
    import sys
    import types

    mod = types.ModuleType("psycopg")
    mod.connect = lambda dsn: conn  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "psycopg", mod)


def test_pg_copy_rejects_injected_table(monkeypatch):
    conn = _FakePgConn()
    _patch_psycopg(monkeypatch, conn)

    class _B:
        def to_pylist(self):
            return [{"id": 1}]

    from app.errors import AppError

    with pytest.raises(AppError) as ei:
        fi._pg_copy("postgresql://x", "orders; DROP TABLE secrets; --", iter([_B()]))
    assert ei.value.code == "invalid_identifier"


def test_pg_copy_escapes_malicious_column_name(monkeypatch):
    conn = _FakePgConn()
    _patch_psycopg(monkeypatch, conn)

    class _B:
        def to_pylist(self):
            # A column name coming from an untrusted source file that tries to
            # break out of the quoted identifier.
            return [{'id") FROM STDIN; DROP TABLE t; --': 1}]

    fi._pg_copy("postgresql://x", "raw.orders", iter([_B()]))
    sql = conn.cur.copy_sql
    # The embedded double-quote must be doubled, not left to terminate the ident.
    assert '""' in sql
    assert "DROP TABLE t" in sql  # present, but safely inside a quoted identifier
    assert sql.count('"') % 2 == 0  # balanced quotes → no break-out
