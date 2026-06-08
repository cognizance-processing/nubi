"""Tests for the 'extract' flow handler (backend/app/flows/handlers/extract.py).

Coverage
--------
1. ZIP archive: handle() extracts members and uploads them under dest_uri.
2. tar.gz archive: handle() extracts members and uploads them under dest_uri.
3. bare .gz archive: handle() decompresses and uploads the single output file.
4. plain tar archive: handle() extracts members.
5. Path-traversal: handle() rejects a ZIP with a ``../`` member name.
6. source_uri vs source (upstream task): using 'source' key resolves 'uri' from inputs.
7. Missing config validation: missing dest_uri raises ValueError.
8. Both source_uri and source raises ValueError.
9. format='auto' detects ZIP and tar.gz correctly via magic bytes.

All tests use the ``file://`` storage backend + temporary directories
(no cloud SDK required).  TaskContext is constructed directly from
``app.flows.executor.TaskContext``.
"""

from __future__ import annotations

import gzip
import io
import os
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.executor import TaskContext
from app.flows.handlers.extract import handle, _safe_member_path, _detect_format


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ctx(**kwargs: Any) -> TaskContext:
    """Build a minimal TaskContext for handler tests."""
    return TaskContext(
        flow_params=kwargs.get("flow_params", {}),
        inputs=kwargs.get("inputs", {}),
        now=_NOW,
    )


def _file_uri(root: str, key: str = "") -> str:
    """Construct a ``file://`` URI for *root* with optional *key* appended."""
    if key:
        return f"file://{root}/{key}"
    return f"file://{root}"


# ---------------------------------------------------------------------------
# 1. ZIP archive — source_uri
# ---------------------------------------------------------------------------


def test_extract_zip_source_uri(tmp_path: Any) -> None:
    """ZIP archive is downloaded, extracted, and members uploaded to dest."""
    # Create a zip archive with two files.
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive_path = archive_dir / "data.zip"

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("hello.txt", "hello world")
        zf.writestr("subdir/nested.txt", "nested content")

    # dest storage root
    dest_root = tmp_path / "dest"
    dest_root.mkdir()

    source_uri = f"file://{archive_dir}/data.zip"
    dest_uri = f"file://{dest_root}/extracted"

    result = handle(
        config={
            "source_uri": source_uri,
            "dest_uri": dest_uri,
            "format": "auto",
        },
        ctx=_ctx(),
        claims=_CLAIMS,
    )

    assert result["file_count"] == 2
    names = {f["name"] for f in result["files"]}
    assert "hello.txt" in names
    assert "subdir/nested.txt" in names

    # Verify files landed on disk.
    for entry in result["files"]:
        assert entry["size"] > 0
        assert entry["uri"].startswith("file://")
        # Parse key from uri and read via LocalStorageClient.
        from app.storage.base import parse_uri, get_storage_client
        scheme, bucket, key = parse_uri(entry["uri"])
        client = get_storage_client(entry["uri"])
        data = client.download_bytes(key)
        assert len(data) > 0


# ---------------------------------------------------------------------------
# 2. tar.gz archive — source_uri
# ---------------------------------------------------------------------------


def test_extract_tar_gz_source_uri(tmp_path: Any) -> None:
    """tar.gz archive is extracted and members uploaded."""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive_path = str(archive_dir / "bundle.tar.gz")

    with tarfile.open(archive_path, "w:gz") as tf:
        for name, content in [("a.csv", b"col1,col2\n1,2\n"), ("b.txt", b"text")]:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

    dest_root = tmp_path / "dest"
    dest_root.mkdir()

    source_uri = f"file://{archive_dir}/bundle.tar.gz"
    dest_uri = f"file://{dest_root}/out"

    result = handle(
        config={"source_uri": source_uri, "dest_uri": dest_uri, "format": "auto"},
        ctx=_ctx(),
        claims=_CLAIMS,
    )

    assert result["file_count"] == 2
    names = {f["name"] for f in result["files"]}
    assert "a.csv" in names
    assert "b.txt" in names


# ---------------------------------------------------------------------------
# 3. bare .gz archive
# ---------------------------------------------------------------------------


def test_extract_bare_gz(tmp_path: Any) -> None:
    """A bare .gz file (not a tarball) is decompressed to a single output."""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive_path = archive_dir / "report.json.gz"

    content = b'{"rows": 42}'
    with gzip.open(str(archive_path), "wb") as fh:
        fh.write(content)

    dest_root = tmp_path / "dest"
    dest_root.mkdir()

    source_uri = f"file://{archive_dir}/report.json.gz"
    dest_uri = f"file://{dest_root}/out"

    result = handle(
        config={"source_uri": source_uri, "dest_uri": dest_uri, "format": "gz"},
        ctx=_ctx(),
        claims=_CLAIMS,
    )

    assert result["file_count"] == 1
    entry = result["files"][0]
    # The output name should be derived from stripping .gz
    assert entry["name"] == "report.json"
    assert entry["size"] == len(content)


# ---------------------------------------------------------------------------
# 4. plain tar archive
# ---------------------------------------------------------------------------


def test_extract_plain_tar(tmp_path: Any) -> None:
    """Uncompressed tar archive is extracted correctly."""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive_path = str(archive_dir / "flat.tar")

    members = [("x.txt", b"xxx"), ("y.txt", b"yyy")]
    with tarfile.open(archive_path, "w") as tf:
        for name, content in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

    dest_root = tmp_path / "dest"
    dest_root.mkdir()

    result = handle(
        config={
            "source_uri": f"file://{archive_dir}/flat.tar",
            "dest_uri": f"file://{dest_root}/out",
            "format": "tar",
        },
        ctx=_ctx(),
        claims=_CLAIMS,
    )

    assert result["file_count"] == 2
    names = {f["name"] for f in result["files"]}
    assert "x.txt" in names
    assert "y.txt" in names


# ---------------------------------------------------------------------------
# 5. Path-traversal guard
# ---------------------------------------------------------------------------


def test_extract_zip_path_traversal_blocked(tmp_path: Any) -> None:
    """A ZIP containing a path-traversal member name is rejected."""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive_path = archive_dir / "evil.zip"

    # Craft a zip with a traversal member name.
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("safe.txt", "ok")
        # This name should trigger the traversal guard.
        info = zipfile.ZipInfo("../escape.txt")
        zf.writestr(info, "escaped!")

    dest_root = tmp_path / "dest"
    dest_root.mkdir()

    with pytest.raises(ValueError, match="[Pp]ath traversal"):
        handle(
            config={
                "source_uri": f"file://{archive_dir}/evil.zip",
                "dest_uri": f"file://{dest_root}/out",
            },
            ctx=_ctx(),
            claims=_CLAIMS,
        )


def test_safe_member_path_rejects_traversal(tmp_path: Any) -> None:
    """Unit test for _safe_member_path itself."""
    extract_dir = str(tmp_path / "extract")
    os.makedirs(extract_dir, exist_ok=True)

    # Normal path should succeed.
    result = _safe_member_path(extract_dir, "subdir/file.txt")
    assert result.startswith(extract_dir)

    # Traversal attempt should raise.
    with pytest.raises(ValueError, match="[Pp]ath traversal"):
        _safe_member_path(extract_dir, "../../etc/passwd")


# ---------------------------------------------------------------------------
# 6. source from upstream task result ('source' key)
# ---------------------------------------------------------------------------


def test_extract_source_from_upstream_uri(tmp_path: Any) -> None:
    """'source' key resolves the archive URI from an upstream task's result."""
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    archive_path = archive_dir / "data.zip"

    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("result.txt", "from upstream")

    dest_root = tmp_path / "dest"
    dest_root.mkdir()

    upstream_uri = f"file://{archive_dir}/data.zip"

    ctx = _ctx(inputs={"downloader": {"uri": upstream_uri}})

    result = handle(
        config={
            "source": "downloader",
            "dest_uri": f"file://{dest_root}/out",
            "format": "zip",
        },
        ctx=ctx,
        claims=_CLAIMS,
    )

    assert result["file_count"] == 1
    assert result["files"][0]["name"] == "result.txt"


# ---------------------------------------------------------------------------
# 7. Missing dest_uri raises ValueError
# ---------------------------------------------------------------------------


def test_extract_missing_dest_uri_raises(tmp_path: Any) -> None:
    """extract handler raises ValueError when 'dest_uri' is absent."""
    with pytest.raises(ValueError, match="dest_uri"):
        handle(
            config={"source_uri": "file:///tmp/fake/archive.zip"},
            ctx=_ctx(),
            claims=_CLAIMS,
        )


# ---------------------------------------------------------------------------
# 8. Both source_uri and source raises ValueError
# ---------------------------------------------------------------------------


def test_extract_both_source_and_source_uri_raises(tmp_path: Any) -> None:
    """extract handler raises ValueError when both 'source_uri' and 'source' are set."""
    with pytest.raises(ValueError, match="source"):
        handle(
            config={
                "source_uri": "file:///tmp/fake/archive.zip",
                "source": "some_task",
                "dest_uri": "file:///tmp/fake/dest",
            },
            ctx=_ctx(),
            claims=_CLAIMS,
        )


# ---------------------------------------------------------------------------
# 9. format='auto' detects ZIP and tar.gz from magic bytes
# ---------------------------------------------------------------------------


def test_detect_format_auto_zip(tmp_path: Any) -> None:
    """_detect_format returns 'zip' for a real ZIP file with format='auto'."""
    archive_path = tmp_path / "test.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("f.txt", "x")
    assert _detect_format(str(archive_path), "auto") == "zip"


def test_detect_format_auto_tar_gz(tmp_path: Any) -> None:
    """_detect_format returns 'tar.gz' for a real .tar.gz file with format='auto'."""
    archive_path = str(tmp_path / "bundle.tar.gz")
    with tarfile.open(archive_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="f.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    assert _detect_format(archive_path, "auto") == "tar.gz"


def test_detect_format_explicit_override(tmp_path: Any) -> None:
    """Explicit format hint is respected without magic-byte inspection."""
    # Write a real zip but request 'zip' explicitly.
    archive_path = tmp_path / "test.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("f.txt", "x")
    assert _detect_format(str(archive_path), "zip") == "zip"
    assert _detect_format(str(archive_path), "gz") == "gz"


# ---------------------------------------------------------------------------
# 10. secret-based creds: bad JSON raises ValueError
# ---------------------------------------------------------------------------


def test_extract_secret_bad_json_raises(tmp_path: Any) -> None:
    """If the named secret is not valid JSON a clear ValueError is raised."""
    # We must set ctx.secrets — the field may not exist yet on TaskContext,
    # so we set it as an attribute directly (defensive shim).
    ctx = _ctx()
    ctx.secrets = {"my-cred": "not-valid-json{"}  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="[Jj][Ss][Oo][Nn]|not valid"):
        handle(
            config={
                "source_uri": "file:///tmp/fake/a.zip",
                "dest_uri": "file:///tmp/fake/dest",
                "secret": "my-cred",
            },
            ctx=ctx,
            claims=_CLAIMS,
        )
