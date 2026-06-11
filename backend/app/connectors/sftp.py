"""SFTP file connector — paramiko-backed (ingestion design §2).

A *file-only* connector: it implements :class:`FileConnectorMixin`
(``list_files`` / ``open`` / ``move`` / ``delete``) and advertises
``file_interface: True``, but it is NOT SQL-queryable — ``execute`` /
``execute_stream`` raise ``AppError("not_queryable", 400)``.  Non-queryable
sources become queryable by landing in a queryable target (the ``file_ingest``
task), per the design.

Auth
----
Password OR private key.  Both arrive merged in from the encrypted connector
secret store (they are in the ``connectors._SECRET_KEYS`` allow-list:
``password`` / ``private_key``); they are NEVER read from ``config`` directly
for storage and are never logged.

Host-key pinning (TOFU)
-----------------------
``config["host_key"]`` may pin the server's host key as
``"<keytype> <base64>"`` (e.g. ``"ssh-ed25519 AAAA..."``).  When present, the
connection rejects any server whose key does not match (defeats MITM).  When
absent, the first connection runs in *trust-on-first-use* mode: it accepts the
key and returns it via :attr:`observed_host_key` so the caller (connector
create/test flow) can persist it into ``config["host_key"]`` and pin it for all
subsequent connections.  ``host_key_policy: "reject"`` forces strict mode even
on the first connect (no TOFU) for high-assurance deployments.

Lazy import
-----------
``paramiko`` is optional and imported inside the methods that need it, so the
module loads without it installed; using the connector without paramiko raises
``AppError("driver_unavailable", 500)`` with an install hint.

Network mode
------------
For ``network_mode="bridge"`` the caller resolves a local TCP proxy endpoint via
``app.connectors.network.resolve_network_async`` and passes the proxied
``host``/``port`` in *config* — exactly like every TCP query connector — so SFTP
inside a customer VPC needs no bridge-specific code here (design §2).
"""

from __future__ import annotations

import posixpath
import stat as _stat
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING, Any, BinaryIO, Iterator

from app.connectors.base import Connector, FileConnectorMixin, FileStat, file_capabilities
from app.connectors.file_support import finalize, split_pattern
from app.connectors.ssrf import guard_url
from app.errors import AppError

if TYPE_CHECKING:
    import pyarrow as pa

SOURCE_TYPE = "sftp"

_DEFAULT_PORT = 22


def _import_paramiko() -> Any:
    """Import paramiko lazily; return the module or raise ``AppError``."""
    try:
        import paramiko  # noqa: PLC0415

        return paramiko
    except ImportError as exc:  # pragma: no cover - import guard
        raise AppError(
            "driver_unavailable",
            "paramiko is not installed (needed for the SFTP connector). "
            "Install it with: pip install paramiko",
            status=500,
        ) from exc


class SFTPConnector(Connector, FileConnectorMixin):
    """File connector for an SFTP server.

    Parameters
    ----------
    config:
        Connection parameters.  Recognised keys:

        ``host`` (required), ``port`` (default 22), ``user`` / ``username``,
        ``root`` / ``base_path`` (optional path prefix prepended to every
        relative file path), ``host_key`` (pinned host key, see module
        docstring), ``host_key_policy`` (``"tofu"`` default / ``"reject"``),
        plus the secrets ``password`` and/or ``private_key`` (merged in from the
        secret store).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config or {})
        self._root: str = str(self._config.get("root") or self._config.get("base_path") or "").strip("/")
        #: Host key observed during a TOFU connect (``"<keytype> <base64>"``),
        #: populated after the first connection when no key was pinned.
        self.observed_host_key: str | None = None
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities — file-only, not queryable
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        """Return capability flags: file-interface only, no query support.

        The 7 query flags are all ``False`` (SFTP is not SQL-queryable); the
        ingestion extension marks ``file_interface: True``.  As a pure source
        (not a target) it advertises no bulk-load / stream-load.
        """
        return {
            "native_arrow": False,
            "predicate_pushdown": False,
            "projection_pushdown": False,
            "partition_pushdown": False,
            "predicate_rls": False,
            "column_masking": False,
            "streaming_cdc": False,
            **file_capabilities(file_interface=True),
        }

    # ------------------------------------------------------------------
    # Query interface — explicitly unsupported
    # ------------------------------------------------------------------

    def execute(self, plan: Any) -> "pa.Table":  # noqa: D401 - see message
        raise AppError(
            "not_queryable",
            "The SFTP connector is file-only and cannot run SQL. Ingest its files "
            "into a queryable target (file_ingest) and query that instead.",
            status=400,
        )

    def execute_stream(self, plan: Any) -> Iterator["pa.RecordBatch"]:
        raise AppError(
            "not_queryable",
            "The SFTP connector is file-only and cannot run SQL.",
            status=400,
        )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _abs(self, path: str) -> str:
        """Join *path* under the configured root (POSIX semantics)."""
        path = (path or "").lstrip("/")
        if self._root:
            return posixpath.join(self._root, path)
        return path or "."

    def _load_pkey(self, paramiko: Any, key_str: str) -> Any:
        """Parse a private key string into a paramiko PKey, trying each type."""
        password = self._config.get("private_key_password") or self._config.get("key_password")
        last_exc: Exception | None = None
        for key_cls in (
            getattr(paramiko, "Ed25519Key", None),
            getattr(paramiko, "RSAKey", None),
            getattr(paramiko, "ECDSAKey", None),
            getattr(paramiko, "DSSKey", None),
        ):
            if key_cls is None:
                continue
            try:
                return key_cls.from_private_key(BytesIO(key_str.encode("utf-8")), password=password)
            except Exception as exc:  # try the next key type
                last_exc = exc
                continue
        raise AppError(
            "config_error",
            f"Could not parse the SFTP private key (tried ed25519/rsa/ecdsa/dss): {last_exc}",
            status=400,
        )

    def _apply_host_key_policy(self, paramiko: Any, client: Any) -> None:
        """Configure host-key verification (pinned, TOFU, or strict-reject)."""
        pinned = (self._config.get("host_key") or "").strip()
        policy = (self._config.get("host_key_policy") or "tofu").strip().lower()

        if pinned:
            # Pin the supplied key: load it into the client's host-key store and
            # reject anything that does not match.
            try:
                keytype, b64 = pinned.split(None, 1)
                import base64  # noqa: PLC0415

                key = paramiko.PKey.from_type_string(keytype, base64.b64decode(b64)) \
                    if hasattr(paramiko.PKey, "from_type_string") \
                    else paramiko.RSAKey(data=base64.b64decode(b64))
            except Exception as exc:
                raise AppError(
                    "config_error",
                    f"Invalid pinned host_key (expected '<keytype> <base64>'): {exc}",
                    status=400,
                ) from exc
            host = str(self._config.get("host"))
            client.get_host_keys().add(host, key.get_name(), key)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            return

        if policy == "reject":
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            return

        # TOFU: accept on first connect, capture the key for the caller to pin.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def _connect(self) -> Any:
        """Open a paramiko SSHClient + SFTP channel; return (client, sftp)."""
        paramiko = _import_paramiko()

        host = self._config.get("host")
        if not host:
            raise AppError("config_error", "SFTP connector requires 'host' in config.", status=400)
        port = int(self._config.get("port") or _DEFAULT_PORT)
        # SSRF guard: a user supplies the host — block loopback/metadata/etc.
        # (reuse the http(s) guard via a synthetic URL; private hosts are allowed
        # only when NUBI_SSRF_ALLOW_PRIVATE is set, matching every other
        # user-host connector).
        guard_url(f"https://{host}:{port}")

        user = self._config.get("user") or self._config.get("username")
        password = self._config.get("password")
        private_key = self._config.get("private_key")

        connect_kwargs: dict[str, Any] = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": int(self._config.get("timeout") or 30),
            "allow_agent": False,
            "look_for_keys": False,
        }
        if private_key:
            connect_kwargs["pkey"] = self._load_pkey(paramiko, str(private_key))
        elif password:
            connect_kwargs["password"] = str(password)
        else:
            raise AppError(
                "config_error",
                "SFTP connector requires a 'password' or 'private_key' secret.",
                status=400,
            )

        client = paramiko.SSHClient()
        self._apply_host_key_policy(paramiko, client)
        try:
            client.connect(**connect_kwargs)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "connection_error",
                f"SFTP connection to {host}:{port} failed: {exc}",
                status=502,
            ) from exc

        # Capture the negotiated host key for TOFU persistence.
        try:
            transport = client.get_transport()
            remote_key = transport.get_remote_server_key() if transport else None
            if remote_key is not None and not (self._config.get("host_key") or "").strip():
                self.observed_host_key = f"{remote_key.get_name()} {remote_key.get_base64()}"
        except Exception:  # best-effort — never block the connection
            pass

        sftp = client.open_sftp()
        return client, sftp

    # ------------------------------------------------------------------
    # File interface
    # ------------------------------------------------------------------

    def list_files(self, pattern: str, since: datetime | None = None) -> list[FileStat]:
        """List files matching *pattern* on the SFTP server (newer than *since*).

        Walks the literal directory prefix of *pattern* recursively, stats each
        regular file (size + mtime), then applies glob + ``since`` filtering and
        a lexicographic sort via the shared helper.
        """
        prefix, _glob = split_pattern(pattern)
        client, sftp = self._connect()
        try:
            base = self._abs(prefix)
            stats: list[FileStat] = []
            self._walk(sftp, base, prefix, stats)
            return finalize(stats, pattern, since)
        finally:
            self._close(client, sftp)

    def _walk(self, sftp: Any, abs_dir: str, rel_dir: str, out: list[FileStat]) -> None:
        """Recursively collect ``FileStat`` entries under *abs_dir*."""
        try:
            entries = sftp.listdir_attr(abs_dir or ".")
        except FileNotFoundError:
            return
        except IOError:
            return
        for attr in entries:
            name = attr.filename
            rel = posixpath.join(rel_dir, name) if rel_dir else name
            abs_path = posixpath.join(abs_dir, name) if abs_dir else name
            mode = attr.st_mode or 0
            if _stat.S_ISDIR(mode):
                self._walk(sftp, abs_path, rel, out)
                continue
            if not _stat.S_ISREG(mode):
                continue
            mtime = (
                datetime.fromtimestamp(attr.st_mtime, tz=timezone.utc)
                if attr.st_mtime is not None
                else None
            )
            out.append(
                FileStat(
                    path=rel,
                    size=int(attr.st_size or 0),
                    mtime=mtime,
                    etag=None,
                )
            )

    def open(self, path: str) -> BinaryIO:
        """Open *path* for streaming read.

        Returns a file-like object whose ``close()`` also tears down the
        underlying SSH connection, so the caller owns the full lifecycle by
        simply closing the handle (use as a context manager).
        """
        client, sftp = self._connect()
        try:
            handle = sftp.open(self._abs(path), "rb")
            handle.prefetch()
        except Exception as exc:
            self._close(client, sftp)
            raise AppError(
                "file_open_error",
                f"SFTP open of {path!r} failed: {exc}",
                status=502,
            ) from exc
        return _ClosingSFTPFile(handle, client, sftp)

    def move(self, src: str, dst: str) -> None:
        """Rename/move *src* to *dst* on the server (post_action archive)."""
        client, sftp = self._connect()
        try:
            dst_abs = self._abs(dst)
            # Ensure the destination directory exists (best-effort mkdir chain).
            self._mkdirs(sftp, posixpath.dirname(dst_abs))
            try:
                sftp.posix_rename(self._abs(src), dst_abs)
            except (AttributeError, IOError):
                sftp.rename(self._abs(src), dst_abs)
        finally:
            self._close(client, sftp)

    def delete(self, path: str) -> None:
        """Delete *path* on the server (post_action delete)."""
        client, sftp = self._connect()
        try:
            sftp.remove(self._abs(path))
        finally:
            self._close(client, sftp)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mkdirs(self, sftp: Any, abs_dir: str) -> None:
        """Create *abs_dir* and parents on the server (best-effort)."""
        if not abs_dir or abs_dir in (".", "/"):
            return
        parts = abs_dir.strip("/").split("/")
        cur = "/" if abs_dir.startswith("/") else ""
        for part in parts:
            cur = posixpath.join(cur, part) if cur else part
            try:
                sftp.stat(cur)
            except IOError:
                try:
                    sftp.mkdir(cur)
                except IOError:
                    pass

    @staticmethod
    def _close(client: Any, sftp: Any) -> None:
        for obj in (sftp, client):
            try:
                obj.close()
            except Exception:  # pragma: no cover - best-effort close
                pass


class _ClosingSFTPFile:
    """Wrap a paramiko SFTPFile so closing it also closes the SSH session.

    Delegates all reads to the underlying handle; on ``close()`` it closes the
    handle, the SFTP channel, and the SSH client so a single ``with
    conn.open(path) as fh:`` fully cleans up.
    """

    def __init__(self, handle: Any, client: Any, sftp: Any) -> None:
        self._handle = handle
        self._client = client
        self._sftp = sftp

    def read(self, size: int = -1) -> bytes:
        return self._handle.read() if size is None or size < 0 else self._handle.read(size)

    def readable(self) -> bool:
        return True

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)

    def __enter__(self) -> "_ClosingSFTPFile":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        for obj in (self._handle, self._sftp, self._client):
            try:
                obj.close()
            except Exception:  # pragma: no cover
                pass
