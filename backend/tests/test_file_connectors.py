"""Tests for the file-connector layer (ingestion design §2 + §4 flags).

Coverage
--------
1.  FileStat shape  — exact fields (path, size, mtime, etag) + defaults.
2.  file_capabilities helper — safe defaults / explicit values.
3.  capabilities() extension flags on sftp / ftp / duckdb_storage; query
    connectors keep the strict 7-flag contract and default file flags off.
4.  sftp list_files / open / move / delete against a fake paramiko module
    (paramiko is not installed in CI — the connector imports it lazily).
5.  sftp host-key pinning / TOFU policy selection.
6.  ftp list_files / open against a fake ftplib.FTP (MLSD path).
7.  storage file interface (list/open/move/delete) over the LOCAL backend via
    duckdb_storage — proving a bucket connector is both queryable and
    file-capable.
8.  bridge-routing path selection — file connectors receive proxied host/port
    from network resolution exactly like query connectors (no file-specific
    bridge code).
9.  registry registers sftp + ftp lazily.
10. SSRF guard fires on a loopback host.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.connectors.base import FileStat, file_capabilities
from app.connectors.file_support import finalize, matches, split_pattern
from app.errors import AppError


# ---------------------------------------------------------------------------
# 1. FileStat shape
# ---------------------------------------------------------------------------


def test_filestat_shape_and_defaults():
    fs = FileStat(path="a/b.csv", size=10)
    assert fs.path == "a/b.csv"
    assert fs.size == 10
    assert fs.mtime is None
    assert fs.etag is None
    # Exact field set — the pipeline agent integrates against this shape.
    assert set(FileStat.__dataclass_fields__.keys()) == {"path", "size", "mtime", "etag"}
    full = FileStat(path="x", size=1, mtime=datetime.now(timezone.utc), etag="e")
    assert full.etag == "e"


# ---------------------------------------------------------------------------
# 2. file_capabilities helper
# ---------------------------------------------------------------------------


def test_file_capabilities_defaults_and_values():
    assert file_capabilities() == {
        "file_interface": False,
        "bulk_load_from": [],
        "stream_load": False,
    }
    caps = file_capabilities(file_interface=True, bulk_load_from=["s3"], stream_load=True)
    assert caps == {
        "file_interface": True,
        "bulk_load_from": ["s3"],
        "stream_load": True,
    }


# ---------------------------------------------------------------------------
# 3. capabilities() extension flags
# ---------------------------------------------------------------------------


def test_sftp_ftp_capabilities_file_only():
    from app.connectors.ftp import FTPConnector
    from app.connectors.sftp import SFTPConnector

    for conn in (SFTPConnector({"host": "h"}), FTPConnector({"host": "h"})):
        caps = conn.capabilities()
        # File interface on, query flags off.
        assert caps["file_interface"] is True
        assert caps["native_arrow"] is False
        assert caps["predicate_rls"] is False
        # Reserved loader keys present with safe defaults (pure source).
        assert caps["bulk_load_from"] == []
        assert caps["stream_load"] is False


def test_query_connector_keeps_strict_contract_and_no_file_iface():
    from app.connectors.duckdb_conn import DuckDBConnector

    caps = DuckDBConnector().capabilities()
    # Strict 7-flag query contract, untouched.
    assert set(caps.keys()) == {
        "native_arrow",
        "predicate_pushdown",
        "projection_pushdown",
        "partition_pushdown",
        "predicate_rls",
        "column_masking",
        "streaming_cdc",
    }
    # A pure query connector has no file flags → treated as non-file.
    assert "file_interface" not in caps


def test_validate_capabilities_allows_list_valued_extension_key():
    # bulk_load_from is a list (not bool) — must not trip validate_capabilities.
    from app.connectors.ftp import FTPConnector

    FTPConnector({"host": "h"}).validate_capabilities()  # no raise


# ---------------------------------------------------------------------------
# file_support helpers
# ---------------------------------------------------------------------------


def test_split_pattern_and_matches():
    assert split_pattern("outbound/2024/*.csv") == ("outbound/2024", "outbound/2024/*.csv")
    assert split_pattern("*.csv")[0] == ""
    assert split_pattern("data/orders.csv") == ("data", "data/orders.csv")
    assert matches("outbound/a.csv", "outbound/*.csv")
    assert not matches("outbound/a.txt", "outbound/*.csv")


def test_finalize_filters_since_and_sorts():
    old = FileStat(path="b.csv", size=1, mtime=datetime(2020, 1, 1, tzinfo=timezone.utc))
    new = FileStat(path="a.csv", size=1, mtime=datetime(2030, 1, 1, tzinfo=timezone.utc))
    unknown = FileStat(path="c.csv", size=1, mtime=None)
    out = finalize([old, new, unknown], "*.csv", since=datetime(2025, 1, 1, tzinfo=timezone.utc))
    # old dropped (older than since); unknown kept; sorted by path.
    assert [f.path for f in out] == ["a.csv", "c.csv"]


# ---------------------------------------------------------------------------
# Fake paramiko (paramiko is NOT installed in CI — lazy import lets us mock it)
# ---------------------------------------------------------------------------


class _FakeSFTPAttr:
    def __init__(self, filename, size, mtime, is_dir=False):
        self.filename = filename
        self.st_size = size
        self.st_mtime = mtime
        # 0o040000 dir, 0o100000 regular file
        self.st_mode = 0o040755 if is_dir else 0o100644


class _FakeSFTPFile(io.BytesIO):
    def prefetch(self):  # paramiko API
        pass


class _FakeSFTPClient:
    def __init__(self, tree):
        # tree: {dirpath: [(name, size, mtime, is_dir)]}; files: {path: bytes}
        self._tree = tree
        self.removed = []
        self.renamed = []

    def listdir_attr(self, path):
        path = path.rstrip("/") or "."
        if path.startswith("./") and path != ".":
            path = path[2:]
        entries = self._tree["dirs"].get(path)
        if entries is None:
            raise FileNotFoundError(path)
        return [_FakeSFTPAttr(*e) for e in entries]

    def open(self, path, mode):
        return _FakeSFTPFile(self._tree["files"][path])

    def posix_rename(self, src, dst):
        self.renamed.append((src, dst))

    def stat(self, path):
        raise IOError("no")

    def mkdir(self, path):
        pass

    def remove(self, path):
        self.removed.append(path)

    def close(self):
        pass


class _FakeTransport:
    def get_remote_server_key(self):
        return SimpleNamespace(get_name=lambda: "ssh-ed25519", get_base64=lambda: "AAAAFAKEKEY")


class _FakeSSHClient:
    last_policy = None

    def __init__(self):
        self._hostkeys = SimpleNamespace(add=lambda *a, **k: None)
        self.connected_kwargs = None

    def set_missing_host_key_policy(self, policy):
        _FakeSSHClient.last_policy = type(policy).__name__

    def get_host_keys(self):
        return self._hostkeys

    def connect(self, **kwargs):
        self.connected_kwargs = kwargs

    def get_transport(self):
        return _FakeTransport()

    def open_sftp(self):
        return self._sftp

    def close(self):
        pass


def _make_fake_paramiko(sftp_client):
    mod = SimpleNamespace()
    mod.AutoAddPolicy = type("AutoAddPolicy", (), {})
    mod.RejectPolicy = type("RejectPolicy", (), {})

    class _SSHClient(_FakeSSHClient):
        def __init__(self):
            super().__init__()
            self._sftp = sftp_client

    mod.SSHClient = _SSHClient
    mod.Ed25519Key = SimpleNamespace(from_private_key=lambda *a, **k: "PKEY")
    mod.RSAKey = SimpleNamespace(from_private_key=lambda *a, **k: "PKEY")
    mod.ECDSAKey = None
    mod.DSSKey = None
    mod.PKey = SimpleNamespace()
    return mod


@pytest.fixture
def fake_sftp(monkeypatch):
    mtime = 1_700_000_000  # fixed epoch
    tree = {
        "dirs": {
            # Root listing (prefix "" → _abs("") == ".") contains the outbound dir.
            ".": [("outbound", 0, mtime, True)],
            "outbound": [
                ("a.csv", 5, mtime, False),
                ("b.txt", 3, mtime, False),
                ("sub", 0, mtime, True),
            ],
            "outbound/sub": [("c.csv", 2, mtime + 10, False)],
        },
        "files": {
            "outbound/a.csv": b"hello",
            "outbound/sub/c.csv": b"cc",
        },
    }
    client = _FakeSFTPClient(tree)
    fake = _make_fake_paramiko(client)
    monkeypatch.setattr("app.connectors.sftp._import_paramiko", lambda: fake)
    # Allow private/loopback hosts in the SSRF guard for the test host.
    monkeypatch.setenv("NUBI_SSRF_ALLOW_PRIVATE", "1")
    return client


# ---------------------------------------------------------------------------
# 4. sftp list/open/move/delete
# ---------------------------------------------------------------------------


def test_sftp_list_files_recursive_and_glob(fake_sftp):
    from app.connectors.sftp import SFTPConnector

    conn = SFTPConnector({"host": "sftp.local", "user": "u", "password": "p"})
    files = conn.list_files("outbound/*.csv")
    # b.txt filtered out by glob; recursion into sub/ included.
    paths = [f.path for f in files]
    assert "outbound/a.csv" in paths
    assert "outbound/sub/c.csv" in paths
    assert "outbound/b.txt" not in paths
    a = next(f for f in files if f.path == "outbound/a.csv")
    assert a.size == 5
    assert a.mtime is not None and a.mtime.tzinfo is not None


def test_sftp_list_files_since_watermark(fake_sftp):
    from app.connectors.sftp import SFTPConnector

    conn = SFTPConnector({"host": "sftp.local", "user": "u", "password": "p"})
    # since between the two mtimes → only the newer sub/c.csv survives.
    since = datetime.fromtimestamp(1_700_000_005, tz=timezone.utc)
    files = conn.list_files("**/*.csv", since=since)
    assert [f.path for f in files] == ["outbound/sub/c.csv"]


def test_sftp_open_reads_and_closes(fake_sftp):
    from app.connectors.sftp import SFTPConnector

    conn = SFTPConnector({"host": "sftp.local", "user": "u", "password": "p"})
    with conn.open("outbound/a.csv") as fh:
        assert fh.read() == b"hello"


def test_sftp_move_and_delete(fake_sftp):
    from app.connectors.sftp import SFTPConnector

    conn = SFTPConnector({"host": "sftp.local", "user": "u", "password": "p"})
    conn.move("outbound/a.csv", "archive/a.csv")
    conn.delete("outbound/b.txt")
    assert fake_sftp.renamed == [("outbound/a.csv", "archive/a.csv")]
    assert fake_sftp.removed == ["outbound/b.txt"]


def test_sftp_requires_a_secret(fake_sftp):
    from app.connectors.sftp import SFTPConnector

    conn = SFTPConnector({"host": "sftp.local", "user": "u"})  # no password/key
    with pytest.raises(AppError) as ei:
        conn.list_files("*")
    assert ei.value.code == "config_error"


# ---------------------------------------------------------------------------
# 5. sftp host-key policy (TOFU vs reject) + observed key capture
# ---------------------------------------------------------------------------


def test_sftp_tofu_policy_and_observed_key(fake_sftp):
    from app.connectors.sftp import SFTPConnector

    conn = SFTPConnector({"host": "sftp.local", "user": "u", "password": "p"})
    conn.list_files("*")
    # TOFU → AutoAddPolicy selected on first connect.
    assert _FakeSSHClient.last_policy == "AutoAddPolicy"
    # Observed host key captured so the caller can pin it.
    assert conn.observed_host_key == "ssh-ed25519 AAAAFAKEKEY"


def test_sftp_reject_policy_when_configured(fake_sftp):
    from app.connectors.sftp import SFTPConnector

    conn = SFTPConnector(
        {"host": "sftp.local", "user": "u", "password": "p", "host_key_policy": "reject"}
    )
    conn.list_files("*")
    assert _FakeSSHClient.last_policy == "RejectPolicy"


# ---------------------------------------------------------------------------
# 6. ftp list/open against a fake ftplib.FTP
# ---------------------------------------------------------------------------


class _FakeFTP:
    instances = []

    def __init__(self, timeout=30):
        self.timeout = timeout
        self.cmds = []
        _FakeFTP.instances.append(self)

    # connection lifecycle
    def connect(self, host, port):
        self.host, self.port = host, port

    def login(self, user, passwd):
        self.user = user

    def set_pasv(self, flag):
        pass

    def prot_p(self):
        pass

    # listing
    def mlsd(self, path="."):
        data = {
            "outbound": [
                ("a.csv", {"type": "file", "size": "5", "modify": "20231114000000"}),
                ("b.txt", {"type": "file", "size": "3", "modify": "20231114000000"}),
            ],
        }
        if path.rstrip("/") not in data:
            return iter([])
        return iter(data[path.rstrip("/")])

    def retrbinary(self, cmd, callback):
        callback(b"hello-ftp")

    def rename(self, src, dst):
        self.cmds.append(("rename", src, dst))

    def delete(self, path):
        self.cmds.append(("delete", path))

    def mkd(self, path):
        pass

    def quit(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_ftp(monkeypatch):
    import ftplib

    _FakeFTP.instances = []
    monkeypatch.setattr(ftplib, "FTP", _FakeFTP)
    # tls defaults to True → FTP_TLS; tests pass tls=False to use the plain class.
    monkeypatch.setenv("NUBI_SSRF_ALLOW_PRIVATE", "1")
    return _FakeFTP


def test_ftp_list_files_mlsd(fake_ftp):
    from app.connectors.ftp import FTPConnector

    conn = FTPConnector({"host": "ftp.local", "user": "u", "password": "p", "tls": False})
    files = conn.list_files("outbound/*.csv")
    assert [f.path for f in files] == ["outbound/a.csv"]
    f = files[0]
    assert f.size == 5
    assert f.mtime == datetime(2023, 11, 14, tzinfo=timezone.utc)


def test_ftp_open_retr(fake_ftp):
    from app.connectors.ftp import FTPConnector

    conn = FTPConnector({"host": "ftp.local", "user": "u", "password": "p", "tls": False})
    with conn.open("outbound/a.csv") as fh:
        assert fh.read() == b"hello-ftp"


def test_ftp_move_and_delete(fake_ftp):
    from app.connectors.ftp import FTPConnector

    conn = FTPConnector({"host": "ftp.local", "user": "u", "password": "p", "tls": False})
    conn.move("outbound/a.csv", "archive/a.csv")
    conn.delete("outbound/b.txt")
    cmds = [c for inst in _FakeFTP.instances for c in inst.cmds]
    assert ("rename", "outbound/a.csv", "archive/a.csv") in cmds
    assert ("delete", "outbound/b.txt") in cmds


def test_ftp_tls_flag_exposed():
    from app.connectors.ftp import FTPConnector

    assert FTPConnector({"host": "h"}).tls_enabled is True
    assert FTPConnector({"host": "h", "tls": False}).tls_enabled is False


# ---------------------------------------------------------------------------
# 7. storage file interface over the local backend (duckdb_storage)
# ---------------------------------------------------------------------------


@pytest.fixture
def local_storage_connector():
    import duckdb

    d = tempfile.mkdtemp()
    dbp = os.path.join(d, "wh.duckdb")
    con = duckdb.connect(dbp)
    con.execute("CREATE TABLE t(x int)")
    con.close()
    os.makedirs(os.path.join(d, "outbound"))
    with open(os.path.join(d, "outbound", "a.csv"), "w") as fh:
        fh.write("hello")
    with open(os.path.join(d, "outbound", "b.txt"), "w") as fh:
        fh.write("yy")

    from app.connectors.duckdb_storage import DuckDBStorageConnector

    return DuckDBStorageConnector.from_config({"database": dbp}), d


def test_storage_connector_is_both_queryable_and_file_capable(local_storage_connector):
    conn, _ = local_storage_connector
    caps = conn.capabilities()
    # Query contract intact (DuckDB).
    assert caps["native_arrow"] is True
    # File interface + target flags on for a local-backed bucket.
    assert caps["file_interface"] is True
    assert caps["stream_load"] is True
    # Local backend isn't a bulk-load staging scheme.
    assert caps["bulk_load_from"] == []


def test_storage_list_open_over_local(local_storage_connector):
    conn, _ = local_storage_connector
    files = conn.list_files("outbound/*.csv")
    assert [f.path for f in files] == ["outbound/a.csv"]
    fs = files[0]
    assert fs.size == 5
    assert fs.mtime is not None
    assert fs.etag is not None  # local surrogate etag
    with conn.open("outbound/a.csv") as fh:
        assert fh.read() == b"hello"


def test_storage_move_and_delete_over_local(local_storage_connector):
    conn, _ = local_storage_connector
    conn.move("outbound/a.csv", "archive/a.csv")
    assert [f.path for f in conn.list_files("outbound/*.csv")] == []
    assert [f.path for f in conn.list_files("archive/*.csv")] == ["archive/a.csv"]
    conn.delete("archive/a.csv")
    assert [f.path for f in conn.list_files("archive/*.csv")] == []


def test_in_memory_storage_has_no_file_interface():
    from app.connectors.duckdb_storage import DuckDBStorageConnector

    conn = DuckDBStorageConnector.from_config({"database": ":memory:"})
    assert conn.capabilities()["file_interface"] is False
    with pytest.raises(AppError) as ei:
        conn.list_files("*")
    assert ei.value.code == "file_interface_unavailable"


def test_storage_bulk_load_from_for_s3_scheme():
    # An s3:// files root advertises s3 bulk-load + stream-load (target flags),
    # without opening any connection (capabilities only reads config).
    from app.connectors.duckdb_storage import _storage_file_uri, DuckDBStorageConnector

    cfg = {"database": ":memory:", "storage_uri": "s3://my-bucket"}
    assert _storage_file_uri(cfg) == "s3://my-bucket"
    conn = DuckDBStorageConnector.for_memory()
    conn._config = cfg
    caps = conn.capabilities()
    assert caps["file_interface"] is True
    assert caps["bulk_load_from"] == ["s3"]
    assert caps["stream_load"] is True


# ---------------------------------------------------------------------------
# 8. bridge-routing path selection (no file-specific bridge code)
# ---------------------------------------------------------------------------


def test_bridge_routing_supplies_proxied_host_to_file_connector(monkeypatch):
    """A file connector consumes the proxied host/port that network resolution
    produces — exactly like a query connector. Phase 1 needs no bridge changes:
    the connector connects to whatever (host, port) it is handed, so writing the
    proxy endpoint into the config is the entire integration."""
    from app.connectors.sftp import SFTPConnector

    captured: dict = {}
    fake = _make_fake_paramiko(_FakeSFTPClient({"dirs": {"": []}, "files": {}}))

    orig_sshclient = fake.SSHClient

    class _CapturingSSH(orig_sshclient):
        def connect(self, **kwargs):
            captured.update(kwargs)
            super().connect(**kwargs)

    fake.SSHClient = _CapturingSSH
    monkeypatch.setattr("app.connectors.sftp._import_paramiko", lambda: fake)
    monkeypatch.setenv("NUBI_SSRF_ALLOW_PRIVATE", "1")

    # Simulate network_mode='bridge': resolve_network_async hands back a local
    # proxy endpoint, which the caller writes into the connector config.
    proxied = {"host": "127.0.0.1", "port": 54321, "user": "u", "password": "p"}
    SFTPConnector(proxied).list_files("*")

    # The connector connected to the PROXIED endpoint, not a VPC-internal host —
    # the file path rides the existing tunnel mechanism unchanged.
    assert captured["hostname"] == "127.0.0.1"
    assert captured["port"] == 54321


def test_network_resolve_direct_passthrough():
    # Confirm the shared resolver hands back host/port verbatim for 'direct'
    # mode — the same NetworkTarget a file connector would be built from.
    from app.connectors.network import resolve_network

    target = resolve_network({"host": "sftp.example.com", "port": 22})
    assert target.host == "sftp.example.com"
    assert target.port == 22
    assert target.mode == "direct"


# ---------------------------------------------------------------------------
# 9. registry + 10. SSRF
# ---------------------------------------------------------------------------


def test_registry_registers_sftp_and_ftp():
    from app.connectors.registry import get_connector_registry, reset_for_tests

    reset_for_tests()
    reg = get_connector_registry()
    assert "sftp" in reg.all()
    assert "ftp" in reg.all()
    conn = reg.get("sftp")({"host": "h", "password": "p"})
    assert conn.capabilities()["file_interface"] is True


def test_sftp_ssrf_guard_blocks_metadata(monkeypatch):
    from app.connectors.sftp import SFTPConnector

    # No NUBI_SSRF_ALLOW_PRIVATE here; the metadata IP is always blocked.
    monkeypatch.delenv("NUBI_SSRF_ALLOW_PRIVATE", raising=False)
    conn = SFTPConnector({"host": "169.254.169.254", "user": "u", "password": "p"})
    # Provide a fake paramiko so we reach the guard, not the import error.
    fake = _make_fake_paramiko(_FakeSFTPClient({"dirs": {}, "files": {}}))
    monkeypatch.setattr("app.connectors.sftp._import_paramiko", lambda: fake)
    with pytest.raises(AppError) as ei:
        conn.list_files("*")
    assert ei.value.code == "ssrf_blocked"
