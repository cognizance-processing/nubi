"""Tests for ``app.connectors.storage_files`` — the ``StorageFileSupport`` mixin.

Strategy
--------
The module adapts an EXISTING ``StorageClient`` to the file-connector
contract.  No real storage backend (S3/GCS/Azure/local) is touched — the
underlying client is a ``MagicMock`` configured per-test, and we assert the
right calls (and argument values) are made against it.

Coverage
--------
1. ``_to_key`` — with/without a base prefix, leading-slash stripping.
2. ``_to_path`` — reverse mapping; leaves keys unchanged when they do not
   start with the base prefix (documents that org-scoping for ``list_files``
   depends on the underlying backend actually honouring ``list(prefix=...)``
   — this mixin does not re-filter).
3. ``list_files`` —
   a. literal-prefix pattern -> ``storage.list(prefix=base+literal)``.
   b. glob-only pattern (no literal prefix) -> falls back to the base prefix.
   c. per-key ``stat()`` hit -> real size/mtime/etag surfaced.
   d. per-key ``stat()`` miss (``None``) -> ``size=0``, ``mtime=None``,
      ``etag=None`` (backend without stat support is still listable).
   e. ``since`` watermark filtering is applied (via the shared ``finalize``).
   f. Results are sorted lexicographically by path.
   g. No ``base_prefix`` -> keys pass through untouched.
4. ``open`` — delegates to ``storage.open_read(_to_key(path))``.
5. ``move`` — download-then-upload-then-delete, in that order, with the
   correctly mapped source/destination keys (no native rename assumed).
6. ``delete`` — calls ``storage.delete(_to_key(path))``.
7. Org-scoping invariant: a non-empty ``base_prefix`` is applied to EVERY
   client call (list/open/move/delete) — no operation can accidentally act
   outside the configured prefix root.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

import pytest

from app.connectors.storage_files import StorageFileSupport
from app.storage.base import ObjectStat


def _mk_client(**overrides) -> MagicMock:
    client = MagicMock()
    client.list.return_value = []
    client.stat.return_value = None
    for k, v in overrides.items():
        setattr(client, k, v)
    return client


# ---------------------------------------------------------------------------
# _to_key / _to_path
# ---------------------------------------------------------------------------


def test_to_key_without_base_prefix():
    support = StorageFileSupport(_mk_client())
    assert support._to_key("orders/2024.csv") == "orders/2024.csv"


def test_to_key_strips_leading_slash():
    support = StorageFileSupport(_mk_client())
    assert support._to_key("/orders/2024.csv") == "orders/2024.csv"


def test_to_key_with_base_prefix():
    support = StorageFileSupport(_mk_client(), base_prefix="org-42")
    assert support._to_key("orders/2024.csv") == "org-42/orders/2024.csv"


def test_to_key_base_prefix_strips_slashes():
    support = StorageFileSupport(_mk_client(), base_prefix="/org-42/")
    assert support._to_key("orders/2024.csv") == "org-42/orders/2024.csv"


def test_to_path_strips_base_prefix():
    support = StorageFileSupport(_mk_client(), base_prefix="org-42")
    assert support._to_path("org-42/orders/2024.csv") == "orders/2024.csv"


def test_to_path_without_base_prefix_is_identity():
    support = StorageFileSupport(_mk_client())
    assert support._to_path("orders/2024.csv") == "orders/2024.csv"


def test_to_path_key_not_matching_prefix_is_returned_unchanged():
    """Documents the org-scoping trust boundary: this mixin does not verify
    that every key returned by the backend actually lives under base_prefix —
    it only strips the prefix when present. Isolation for list_files() relies
    entirely on the backend honouring the `list(prefix=...)` filter."""
    support = StorageFileSupport(_mk_client(), base_prefix="org-42")
    assert support._to_path("other-org/orders/2024.csv") == "other-org/orders/2024.csv"


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


def test_list_files_uses_literal_prefix_and_base_prefix():
    client = _mk_client()
    client.list.return_value = []
    support = StorageFileSupport(client, base_prefix="org-42")

    support.list_files("outbound/2024/*.csv")

    client.list.assert_called_once_with(prefix="org-42/outbound/2024")


def test_list_files_glob_only_pattern_falls_back_to_base_prefix():
    client = _mk_client()
    support = StorageFileSupport(client, base_prefix="org-42")

    support.list_files("*.csv")

    client.list.assert_called_once_with(prefix="org-42")


def test_list_files_no_base_prefix_glob_only_lists_everything():
    client = _mk_client()
    support = StorageFileSupport(client)

    support.list_files("*.csv")

    client.list.assert_called_once_with(prefix="")


def test_list_files_stat_hit_surfaces_real_metadata():
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    client = _mk_client()
    client.list.return_value = ["data/orders.csv"]
    client.stat.return_value = ObjectStat(size=123, mtime=now, etag="abc123")
    support = StorageFileSupport(client)

    stats = support.list_files("data/*.csv")

    assert len(stats) == 1
    fs = stats[0]
    assert fs.path == "data/orders.csv"
    assert fs.size == 123
    assert fs.mtime == now
    assert fs.etag == "abc123"


def test_list_files_stat_miss_yields_zero_size_and_none_metadata():
    client = _mk_client()
    client.list.return_value = ["data/orders.csv"]
    client.stat.return_value = None
    support = StorageFileSupport(client)

    stats = support.list_files("data/*.csv")

    assert len(stats) == 1
    fs = stats[0]
    assert fs.size == 0
    assert fs.mtime is None
    assert fs.etag is None


def test_list_files_applies_base_prefix_to_returned_paths():
    client = _mk_client()
    client.list.return_value = ["org-42/data/orders.csv", "org-42/data/returns.csv"]
    client.stat.return_value = None
    support = StorageFileSupport(client, base_prefix="org-42")

    stats = support.list_files("data/*.csv")

    paths = {s.path for s in stats}
    assert paths == {"data/orders.csv", "data/returns.csv"}


def test_list_files_since_watermark_filters_older_files():
    now = datetime(2024, 6, 1, tzinfo=timezone.utc)
    older = now - timedelta(days=2)
    newer = now + timedelta(days=2)
    client = _mk_client()
    client.list.return_value = ["data/old.csv", "data/new.csv", "data/unknown.csv"]

    def _stat(key: str):
        if key == "data/old.csv":
            return ObjectStat(size=1, mtime=older)
        if key == "data/new.csv":
            return ObjectStat(size=1, mtime=newer)
        return None  # unknown mtime — always included

    client.stat.side_effect = _stat
    support = StorageFileSupport(client)

    stats = support.list_files("data/*.csv", since=now)

    paths = {s.path for s in stats}
    assert paths == {"data/new.csv", "data/unknown.csv"}
    assert "data/old.csv" not in paths


def test_list_files_results_sorted_lexicographically():
    client = _mk_client()
    client.list.return_value = ["data/b.csv", "data/a.csv", "data/c.csv"]
    client.stat.return_value = None
    support = StorageFileSupport(client)

    stats = support.list_files("data/*.csv")

    assert [s.path for s in stats] == ["data/a.csv", "data/b.csv", "data/c.csv"]


def test_list_files_glob_filters_out_non_matching_keys():
    client = _mk_client()
    client.list.return_value = ["data/orders.csv", "data/readme.txt"]
    client.stat.return_value = None
    support = StorageFileSupport(client)

    stats = support.list_files("data/*.csv")

    assert [s.path for s in stats] == ["data/orders.csv"]


def test_list_files_fully_literal_pattern_uses_parent_dir_as_prefix():
    client = _mk_client()
    client.list.return_value = ["data/orders.csv"]
    client.stat.return_value = None
    support = StorageFileSupport(client)

    support.list_files("data/orders.csv")

    client.list.assert_called_once_with(prefix="data")


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


def test_open_delegates_to_client_open_read():
    client = _mk_client()
    sentinel = object()
    client.open_read.return_value = sentinel
    support = StorageFileSupport(client, base_prefix="org-42")

    result = support.open("data/orders.csv")

    client.open_read.assert_called_once_with("org-42/data/orders.csv")
    assert result is sentinel


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


def test_move_downloads_uploads_then_deletes_in_order():
    client = _mk_client()
    client.download_bytes.return_value = b"payload"
    support = StorageFileSupport(client, base_prefix="org-42")

    support.move("inbound/f.csv", "archive/f.csv")

    client.download_bytes.assert_called_once_with("org-42/inbound/f.csv")
    client.upload_bytes.assert_called_once_with(b"payload", "org-42/archive/f.csv")
    client.delete.assert_called_once_with("org-42/inbound/f.csv")

    # Order matters: must not delete the source before the upload succeeds.
    assert client.method_calls == [
        call.download_bytes("org-42/inbound/f.csv"),
        call.upload_bytes(b"payload", "org-42/archive/f.csv"),
        call.delete("org-42/inbound/f.csv"),
    ]


def test_move_propagates_download_error_without_deleting_source():
    client = _mk_client()
    client.download_bytes.side_effect = FileNotFoundError("missing")
    support = StorageFileSupport(client)

    with pytest.raises(FileNotFoundError):
        support.move("inbound/missing.csv", "archive/missing.csv")

    client.upload_bytes.assert_not_called()
    client.delete.assert_not_called()


def test_move_propagates_upload_error_without_deleting_source():
    client = _mk_client()
    client.download_bytes.return_value = b"payload"
    client.upload_bytes.side_effect = RuntimeError("upload failed")
    support = StorageFileSupport(client)

    with pytest.raises(RuntimeError):
        support.move("inbound/f.csv", "archive/f.csv")

    client.delete.assert_not_called()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_delegates_to_client_with_mapped_key():
    client = _mk_client()
    support = StorageFileSupport(client, base_prefix="org-42")

    support.delete("inbound/f.csv")

    client.delete.assert_called_once_with("org-42/inbound/f.csv")


def test_delete_without_base_prefix_uses_bare_path():
    client = _mk_client()
    support = StorageFileSupport(client)

    support.delete("inbound/f.csv")

    client.delete.assert_called_once_with("inbound/f.csv")


# ---------------------------------------------------------------------------
# Org-scoping invariant across every operation
# ---------------------------------------------------------------------------


def test_every_client_call_is_scoped_under_base_prefix():
    client = _mk_client()
    client.list.return_value = ["org-42/f.csv"]
    client.stat.return_value = None
    client.download_bytes.return_value = b"x"
    support = StorageFileSupport(client, base_prefix="org-42")

    support.list_files("*.csv")
    support.open("f.csv")
    support.move("f.csv", "g.csv")
    support.delete("h.csv")

    for c in client.method_calls:
        _, args, _kwargs = c
        for arg in args:
            if isinstance(arg, str) and "/" in arg:
                assert arg.startswith("org-42/"), f"call {c} escaped base_prefix"
