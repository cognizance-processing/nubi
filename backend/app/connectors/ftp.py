"""FTP file connector — ftplib-backed (ingestion design §2).

A *file-only* connector implementing :class:`FileConnectorMixin`
(``list_files`` / ``open`` / ``move`` / ``delete``).  Not SQL-queryable —
``execute`` / ``execute_stream`` raise ``AppError("not_queryable", 400)``.

TLS
---
``config["tls"]`` (default ``True``) selects ``ftplib.FTP_TLS`` (FTPS — the
control + data channels are encrypted).  Setting ``tls: False`` uses plain
``ftplib.FTP``; plaintext FTP sends credentials and data in the clear, so the
connector sets ``config["insecure"] = True`` is NOT done here — instead the
*caller/UI* should warn.  The flag the UI reads is simply ``tls`` (``False`` →
warn).  ``ftplib`` is stdlib, so there is no optional driver to import.

Auth
----
Username + ``password`` (from the encrypted secret store; ``password`` is in
the ``connectors._SECRET_KEYS`` allow-list).  Anonymous FTP is allowed when no
user is supplied (``anonymous`` / empty password).  Secrets are never logged.

Network mode
------------
``network_mode="bridge"`` is honoured the same way as every TCP connector: the
caller resolves a local proxy endpoint via
``app.connectors.network.resolve_network_async`` and passes the proxied
``host``/``port`` in *config*.  No FTP-specific bridge code is required.
"""

from __future__ import annotations

import ftplib
import posixpath
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING, Any, BinaryIO, Iterator

from app.connectors.base import Connector, FileConnectorMixin, FileStat, file_capabilities
from app.connectors.file_support import finalize, split_pattern
from app.connectors.ssrf import guard_url
from app.errors import AppError

if TYPE_CHECKING:
    import pyarrow as pa

SOURCE_TYPE = "ftp"

_DEFAULT_PORT = 21


class FTPConnector(Connector, FileConnectorMixin):
    """File connector for an FTP / FTPS server.

    Parameters
    ----------
    config:
        Recognised keys: ``host`` (required), ``port`` (default 21),
        ``user`` / ``username`` (omit for anonymous), ``tls`` (default
        ``True`` → FTPS; ``False`` → plain FTP, UI should warn), ``passive``
        (default ``True``), ``root`` / ``base_path`` (path prefix), ``timeout``
        (seconds, default 30), plus the ``password`` secret.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = dict(config or {})
        self._root: str = str(self._config.get("root") or self._config.get("base_path") or "").strip("/")
        self.validate_capabilities()

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        """Return capability flags: file-interface only, not queryable."""
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

    @property
    def tls_enabled(self) -> bool:
        """Whether the connector uses FTPS (the UI warns when this is False)."""
        return bool(self._config.get("tls", True))

    # ------------------------------------------------------------------
    # Query interface — unsupported
    # ------------------------------------------------------------------

    def execute(self, plan: Any) -> "pa.Table":
        raise AppError(
            "not_queryable",
            "The FTP connector is file-only and cannot run SQL. Ingest its files "
            "into a queryable target (file_ingest) and query that instead.",
            status=400,
        )

    def execute_stream(self, plan: Any) -> Iterator["pa.RecordBatch"]:
        raise AppError(
            "not_queryable",
            "The FTP connector is file-only and cannot run SQL.",
            status=400,
        )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _abs(self, path: str) -> str:
        path = (path or "").lstrip("/")
        if self._root:
            return posixpath.join(self._root, path)
        return path

    @staticmethod
    def _max_download_bytes() -> int:
        """Cap (bytes) for a single in-memory FTP download; 0 disables."""
        import os  # noqa: PLC0415

        try:
            v = int(os.getenv("NUBI_INGEST_MAX_SOURCE_FILE_BYTES", "") or 2 * 1024 * 1024 * 1024)
            return v if v > 0 else 0
        except ValueError:
            return 2 * 1024 * 1024 * 1024

    def _connect(self) -> "ftplib.FTP":
        host = self._config.get("host")
        if not host:
            raise AppError("config_error", "FTP connector requires 'host' in config.", status=400)
        port = int(self._config.get("port") or _DEFAULT_PORT)
        guard_url(f"https://{host}:{port}")  # SSRF guard on the user-supplied host

        user = self._config.get("user") or self._config.get("username") or "anonymous"
        password = self._config.get("password") or ""
        timeout = int(self._config.get("timeout") or 30)

        try:
            ftp: ftplib.FTP = ftplib.FTP_TLS(timeout=timeout) if self.tls_enabled else ftplib.FTP(timeout=timeout)
            ftp.connect(host=str(host), port=port)
            ftp.login(user=str(user), passwd=str(password))
            if self.tls_enabled and isinstance(ftp, ftplib.FTP_TLS):
                # Encrypt the data channel too (PROT P), not just the control channel.
                ftp.prot_p()
            ftp.set_pasv(bool(self._config.get("passive", True)))
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "connection_error",
                f"FTP connection to {host}:{port} failed: {exc}",
                status=502,
            ) from exc
        return ftp

    # ------------------------------------------------------------------
    # File interface
    # ------------------------------------------------------------------

    def list_files(self, pattern: str, since: datetime | None = None) -> list[FileStat]:
        """List files matching *pattern* (newer than *since*) via MLSD/SIZE/MDTM.

        Prefers ``MLSD`` (RFC 3659 — structured size + modify facts); falls back
        to per-file ``SIZE`` + ``MDTM`` when the server lacks MLSD.
        """
        prefix, _glob = split_pattern(pattern)
        ftp = self._connect()
        try:
            base = self._abs(prefix)
            stats: list[FileStat] = []
            self._walk(ftp, base, prefix, stats)
            return finalize(stats, pattern, since)
        finally:
            self._quit(ftp)

    def _walk(self, ftp: "ftplib.FTP", abs_dir: str, rel_dir: str, out: list[FileStat]) -> None:
        """Recursively collect entries under *abs_dir* using MLSD when available."""
        target = abs_dir or "."
        try:
            entries = list(ftp.mlsd(target))
        except (ftplib.error_perm, ftplib.error_proto, AttributeError):
            entries = self._mlsd_fallback(ftp, target)

        for name, facts in entries:
            if name in (".", ".."):
                continue
            rel = posixpath.join(rel_dir, name) if rel_dir else name
            abs_path = posixpath.join(abs_dir, name) if abs_dir else name
            ftype = (facts.get("type") or "").lower()
            if ftype in ("dir", "cdir", "pdir"):
                if ftype == "dir":
                    self._walk(ftp, abs_path, rel, out)
                continue
            # Treat anything that is not a directory (file, or unknown) as a file.
            size = int(facts["size"]) if facts.get("size", "").isdigit() else 0
            mtime = self._parse_modify(facts.get("modify"))
            out.append(FileStat(path=rel, size=size, mtime=mtime, etag=None))

    def _mlsd_fallback(self, ftp: "ftplib.FTP", target: str) -> list[tuple[str, dict[str, str]]]:
        """Build MLSD-shaped entries from NLST + SIZE + MDTM when MLSD is absent."""
        try:
            names = ftp.nlst(target)
        except ftplib.all_errors:
            return []
        out: list[tuple[str, dict[str, str]]] = []
        for full in names:
            name = posixpath.basename(full)
            if name in (".", ".."):
                continue
            facts: dict[str, str] = {"type": "file"}
            try:
                size = ftp.size(posixpath.join(target, name))
                if size is not None:
                    facts["size"] = str(size)
            except ftplib.all_errors:
                # No SIZE → likely a directory; recurse cautiously.
                try:
                    cwd = ftp.pwd()
                    ftp.cwd(posixpath.join(target, name))
                    ftp.cwd(cwd)
                    facts["type"] = "dir"
                except ftplib.all_errors:
                    pass
            try:
                resp = ftp.sendcmd(f"MDTM {posixpath.join(target, name)}")
                if resp.startswith("213"):
                    facts["modify"] = resp[3:].strip()
            except ftplib.all_errors:
                pass
            out.append((name, facts))
        return out

    @staticmethod
    def _parse_modify(modify: str | None) -> datetime | None:
        """Parse an MLSD/MDTM ``YYYYMMDDHHMMSS`` timestamp into UTC datetime."""
        if not modify:
            return None
        text = modify.split(".", 1)[0]  # drop fractional seconds if present
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def open(self, path: str) -> BinaryIO:
        """Download *path* into memory and return a streaming reader.

        FTP has no random-access file handle, so the object is retrieved fully
        via ``RETR`` into a ``BytesIO`` buffer (then the control connection is
        released).  Suitable for the per-file streaming the loader does; very
        large files are handled by the staging writer downstream.
        """
        ftp = self._connect()
        buf = BytesIO()
        # Bound the in-memory download: FTP RETR streams the whole object into
        # the buffer, so an oversized (or maliciously huge) file would OOM the
        # worker before any downstream ingest cap could apply.  Abort the
        # transfer as soon as the cap is crossed.
        max_bytes = self._max_download_bytes()
        written = 0

        def _sink(chunk: bytes) -> None:
            nonlocal written
            written += len(chunk)
            if max_bytes and written > max_bytes:
                raise AppError(
                    "file_too_large",
                    f"FTP file {path!r} exceeds the download size limit of "
                    f"{max_bytes} bytes (NUBI_INGEST_MAX_SOURCE_FILE_BYTES).",
                    status=413,
                )
            buf.write(chunk)

        try:
            ftp.retrbinary(f"RETR {self._abs(path)}", _sink)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "file_open_error",
                f"FTP RETR of {path!r} failed: {exc}",
                status=502,
            ) from exc
        finally:
            self._quit(ftp)
        buf.seek(0)
        return buf

    def move(self, src: str, dst: str) -> None:
        """Rename *src* to *dst* on the server (post_action archive)."""
        ftp = self._connect()
        try:
            dst_abs = self._abs(dst)
            self._mkdirs(ftp, posixpath.dirname(dst_abs))
            ftp.rename(self._abs(src), dst_abs)
        except Exception as exc:
            raise AppError(
                "file_move_error",
                f"FTP rename {src!r} -> {dst!r} failed: {exc}",
                status=502,
            ) from exc
        finally:
            self._quit(ftp)

    def delete(self, path: str) -> None:
        """Delete *path* on the server (post_action delete)."""
        ftp = self._connect()
        try:
            ftp.delete(self._abs(path))
        except Exception as exc:
            raise AppError(
                "file_delete_error",
                f"FTP delete of {path!r} failed: {exc}",
                status=502,
            ) from exc
        finally:
            self._quit(ftp)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _mkdirs(self, ftp: "ftplib.FTP", abs_dir: str) -> None:
        if not abs_dir:
            return
        cur = ""
        for part in abs_dir.strip("/").split("/"):
            cur = posixpath.join(cur, part) if cur else part
            try:
                ftp.mkd(cur)
            except ftplib.all_errors:
                pass  # already exists / not permitted

    @staticmethod
    def _quit(ftp: "ftplib.FTP") -> None:
        try:
            ftp.quit()
        except Exception:
            try:
                ftp.close()
            except Exception:  # pragma: no cover
                pass
