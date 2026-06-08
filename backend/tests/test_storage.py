"""Tests for the Nubi storage abstraction layer.

Coverage
--------
1. ``parse_uri`` — correct decomposition of all four schemes.
2. ``get_storage_client`` — routes each scheme to the right backend class.
3. ``LocalStorageClient`` — full end-to-end round-trip in a tmp dir:
   a. upload_bytes / download_bytes round-trip.
   b. upload_file / download_to_file round-trip.
   c. open_read returns correct content.
   d. exists returns True after upload, False for unknown key.
   e. list with no prefix returns all keys.
   f. list with a prefix filters correctly.
   g. download_bytes raises FileNotFoundError for missing key.
   h. open_read raises FileNotFoundError for missing key.
   i. Parent directories are created automatically on upload.
4. ``get_storage_client`` raises ValueError for an unknown scheme.
5. Cloud backends raise RuntimeError when their SDK is absent (monkey-patched).
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# parse_uri
# ---------------------------------------------------------------------------


def test_parse_uri_s3():
    from app.storage.base import parse_uri  # noqa: PLC0415

    scheme, bucket, key = parse_uri("s3://my-bucket/path/to/object.parquet")
    assert scheme == "s3"
    assert bucket == "my-bucket"
    assert key == "path/to/object.parquet"


def test_parse_uri_gs():
    from app.storage.base import parse_uri  # noqa: PLC0415

    scheme, bucket, key = parse_uri("gs://gcs-bucket/exports/report.csv")
    assert scheme == "gs"
    assert bucket == "gcs-bucket"
    assert key == "exports/report.csv"


def test_parse_uri_az():
    from app.storage.base import parse_uri  # noqa: PLC0415

    scheme, bucket, key = parse_uri("az://my-container/data/file.json")
    assert scheme == "az"
    assert bucket == "my-container"
    assert key == "data/file.json"


def test_parse_uri_file():
    from app.storage.base import parse_uri  # noqa: PLC0415

    scheme, bucket, key = parse_uri("file:///tmp/nubi-data/uploads/file.parquet")
    assert scheme == "file"
    assert bucket == "/tmp/nubi-data"
    assert key == "uploads/file.parquet"


def test_parse_uri_no_key():
    from app.storage.base import parse_uri  # noqa: PLC0415

    scheme, bucket, key = parse_uri("s3://my-bucket")
    assert scheme == "s3"
    assert bucket == "my-bucket"
    assert key == ""


def test_parse_uri_invalid():
    from app.storage.base import parse_uri  # noqa: PLC0415

    with pytest.raises(ValueError, match="missing '://'"):
        parse_uri("not-a-valid-uri")


# ---------------------------------------------------------------------------
# get_storage_client dispatch
# ---------------------------------------------------------------------------


def test_get_storage_client_file(tmp_path):
    from app.storage.base import get_storage_client  # noqa: PLC0415
    from app.storage.local import LocalStorageClient  # noqa: PLC0415

    uri = f"file://{tmp_path}/unused_key.txt"
    client = get_storage_client(uri)
    assert isinstance(client, LocalStorageClient)


def test_get_storage_client_unknown_scheme():
    from app.storage.base import get_storage_client  # noqa: PLC0415

    with pytest.raises(ValueError, match="Unknown storage scheme"):
        get_storage_client("ftp://my-bucket/key.txt")


def test_get_storage_client_s3_class():
    """S3 dispatch returns S3StorageClient (no SDK call needed for construction)."""
    from app.storage.base import get_storage_client  # noqa: PLC0415
    from app.storage.s3 import S3StorageClient  # noqa: PLC0415

    client = get_storage_client("s3://my-bucket/", creds={})
    assert isinstance(client, S3StorageClient)


def test_get_storage_client_gcs_class():
    from app.storage.base import get_storage_client  # noqa: PLC0415
    from app.storage.gcs import GCSStorageClient  # noqa: PLC0415

    client = get_storage_client("gs://my-bucket/", creds={})
    assert isinstance(client, GCSStorageClient)


def test_get_storage_client_azure_class():
    from app.storage.base import get_storage_client  # noqa: PLC0415
    from app.storage.azure import AzureStorageClient  # noqa: PLC0415

    client = get_storage_client("az://my-container/", creds={})
    assert isinstance(client, AzureStorageClient)


# ---------------------------------------------------------------------------
# LocalStorageClient — full round-trip tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def local_client(tmp_path):
    """Return a LocalStorageClient rooted at a fresh tmp directory."""
    from app.storage.local import LocalStorageClient  # noqa: PLC0415

    return LocalStorageClient(root=str(tmp_path))


class TestLocalStorageClient:
    """End-to-end tests for LocalStorageClient."""

    # ------------------------------------------------------------------
    # upload_bytes / download_bytes
    # ------------------------------------------------------------------

    def test_upload_and_download_bytes(self, local_client):
        payload = b"hello, Nubi storage!"
        uri = local_client.upload_bytes(payload, "test/hello.bin")
        assert uri.startswith("file://")
        assert uri.endswith("test/hello.bin")

        result = local_client.download_bytes("test/hello.bin")
        assert result == payload

    def test_download_bytes_missing_raises(self, local_client):
        with pytest.raises(FileNotFoundError):
            local_client.download_bytes("does/not/exist.bin")

    # ------------------------------------------------------------------
    # upload_file / download_to_file
    # ------------------------------------------------------------------

    def test_upload_file_and_download_to_file(self, local_client, tmp_path):
        # Create a source file
        src = tmp_path / "source.txt"
        src.write_bytes(b"file upload content")

        uri = local_client.upload_file(str(src), "uploads/source.txt")
        assert uri.startswith("file://")

        dest = tmp_path / "downloaded.txt"
        result_path = local_client.download_to_file("uploads/source.txt", str(dest))
        assert result_path == str(dest)
        assert dest.read_bytes() == b"file upload content"

    def test_download_to_file_missing_raises(self, local_client, tmp_path):
        with pytest.raises(FileNotFoundError):
            local_client.download_to_file("no/such/key.txt", str(tmp_path / "out.txt"))

    # ------------------------------------------------------------------
    # open_read
    # ------------------------------------------------------------------

    def test_open_read(self, local_client):
        local_client.upload_bytes(b"stream me", "stream/data.bin")
        with local_client.open_read("stream/data.bin") as fh:
            content = fh.read()
        assert content == b"stream me"

    def test_open_read_missing_raises(self, local_client):
        with pytest.raises(FileNotFoundError):
            local_client.open_read("ghost.bin")

    # ------------------------------------------------------------------
    # exists
    # ------------------------------------------------------------------

    def test_exists_true_after_upload(self, local_client):
        local_client.upload_bytes(b"check", "check.txt")
        assert local_client.exists("check.txt") is True

    def test_exists_false_for_unknown_key(self, local_client):
        assert local_client.exists("never/uploaded.txt") is False

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    def test_list_empty_root(self, local_client):
        assert local_client.list() == []

    def test_list_all_keys(self, local_client):
        local_client.upload_bytes(b"a", "alpha/a.txt")
        local_client.upload_bytes(b"b", "beta/b.txt")
        local_client.upload_bytes(b"c", "alpha/c.txt")
        keys = local_client.list()
        assert sorted(keys) == ["alpha/a.txt", "alpha/c.txt", "beta/b.txt"]

    def test_list_with_prefix(self, local_client):
        local_client.upload_bytes(b"a", "alpha/a.txt")
        local_client.upload_bytes(b"b", "beta/b.txt")
        local_client.upload_bytes(b"c", "alpha/c.txt")

        alpha_keys = local_client.list("alpha/")
        assert "alpha/a.txt" in alpha_keys
        assert "alpha/c.txt" in alpha_keys
        assert "beta/b.txt" not in alpha_keys

    def test_list_prefix_no_match(self, local_client):
        local_client.upload_bytes(b"x", "data/x.txt")
        assert local_client.list("zzz/") == []

    # ------------------------------------------------------------------
    # Parent directory creation
    # ------------------------------------------------------------------

    def test_upload_creates_parent_dirs(self, local_client, tmp_path):
        """Deeply nested keys must not require pre-created directories."""
        local_client.upload_bytes(b"deep", "a/b/c/d/deep.bin")
        assert local_client.exists("a/b/c/d/deep.bin")

    # ------------------------------------------------------------------
    # URI returned is consistent
    # ------------------------------------------------------------------

    def test_uri_matches_root(self, local_client, tmp_path):
        uri = local_client.upload_bytes(b"uri-test", "uri/test.txt")
        root = str(tmp_path)
        assert root in uri
        assert uri.startswith("file://")

    # ------------------------------------------------------------------
    # Round-trip integrity: large-ish payload
    # ------------------------------------------------------------------

    def test_large_payload_round_trip(self, local_client):
        data = os.urandom(1024 * 256)  # 256 KiB
        local_client.upload_bytes(data, "large/blob.bin")
        assert local_client.download_bytes("large/blob.bin") == data


# ---------------------------------------------------------------------------
# Cloud backend SDK missing → RuntimeError
# ---------------------------------------------------------------------------


class _ForbidImport:
    """A sys.meta_path finder that raises ImportError for a specific module.

    Implements the modern ``find_spec`` API (Python 3.4+) so that it works
    on Python 3.12+ where ``find_module``/``load_module`` are no longer
    called by the import machinery.
    """

    def __init__(self, *module_names: str) -> None:
        self._names = set(module_names)

    def _matches(self, name: str) -> bool:
        return name in self._names or any(
            name == n or name.startswith(n + ".") for n in self._names
        )

    # Modern API (Python 3.4+; required on 3.12+)
    def find_spec(self, name: str, path: Any, target: Any = None):
        if self._matches(name):
            raise ImportError(f"Blocked import of {name!r} in test")
        return None

    # Legacy API kept for completeness but not relied upon in Python 3.12+.
    def find_module(self, name: str, path: Any = None):
        if self._matches(name):
            return self
        return None

    def load_module(self, name: str):
        raise ImportError(f"Blocked import of {name!r} in test")


def _block_import(module_name: str):
    """Context manager that blocks import of *module_name* for one test."""
    import contextlib  # noqa: PLC0415

    @contextlib.contextmanager
    def _ctx():
        blocker = _ForbidImport(module_name)
        # Remove already-cached module so the lazy import is triggered
        cached = {k: v for k, v in sys.modules.items() if k == module_name or k.startswith(module_name + ".")}
        for key in cached:
            sys.modules.pop(key)
        sys.meta_path.insert(0, blocker)
        try:
            yield
        finally:
            sys.meta_path.remove(blocker)
            # Restore cached modules
            sys.modules.update(cached)

    return _ctx()


def test_s3_missing_sdk_raises_runtime_error():
    with _block_import("boto3"):
        from app.storage.s3 import S3StorageClient  # noqa: PLC0415

        client = S3StorageClient(bucket="test-bucket", creds={})
        with pytest.raises(RuntimeError, match="boto3"):
            client.upload_bytes(b"data", "key.txt")


def test_gcs_missing_sdk_raises_runtime_error():
    with _block_import("google"):
        # Re-import module fresh so it hits the lazy import path
        if "app.storage.gcs" in sys.modules:
            importlib.reload(sys.modules["app.storage.gcs"])
        from app.storage.gcs import GCSStorageClient  # noqa: PLC0415

        client = GCSStorageClient(bucket="test-bucket", creds={"type": "service_account"})
        with pytest.raises(RuntimeError, match="google-cloud-storage"):
            client.upload_bytes(b"data", "key.txt")


def test_azure_missing_sdk_raises_runtime_error():
    with _block_import("azure"):
        from app.storage.azure import AzureStorageClient  # noqa: PLC0415

        client = AzureStorageClient(
            container="test-container",
            creds={"connection_string": "DefaultEndpointsProtocol=https;..."},
        )
        with pytest.raises(RuntimeError, match="azure-storage-blob"):
            client.upload_bytes(b"data", "key.txt")
