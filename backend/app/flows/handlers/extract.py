"""Extract handler for the Flows engine.

Unpacks archive files (zip, tar, tar.gz/tgz, gz) from a storage URI and
uploads each extracted member to a destination storage prefix.

Public API
----------
handle(config, ctx, claims) -> dict
    Implement the ``'extract'`` task kind.  See the function docstring for the
    full config schema.

Path-traversal guard
--------------------
Every archive member is checked with ``_safe_member_path``.  Any member whose
resolved destination falls *outside* the temp extraction directory is rejected
with a ``ValueError`` before any I/O happens.  This mirrors the recommendation
in the Python ``tarfile`` docs (``data_filter``) and the standard zipfile
security advice.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.flows.executor import TaskContext


# ---------------------------------------------------------------------------
# Path-traversal guard
# ---------------------------------------------------------------------------


def _safe_member_path(extract_dir: str, member_name: str) -> str:
    """Return the absolute extraction path for *member_name* inside *extract_dir*.

    Raises
    ------
    ValueError
        If *member_name* resolves to a path outside *extract_dir* (path
        traversal attempt detected).  This includes members with ``..``
        components, absolute paths, and any name that normalises to a
        location outside the extraction directory.
    """
    # Normalise the extract dir to a canonical absolute path with a
    # guaranteed trailing separator so that startswith() cannot be fooled
    # by a sibling directory whose name shares a prefix (e.g. ``/tmp/ext2``
    # vs ``/tmp/ext``).
    extract_dir_abs = os.path.normpath(os.path.abspath(extract_dir))
    extract_dir_prefix = extract_dir_abs + os.sep

    # Join the raw member name onto the extract dir and normalise — this
    # resolves any ``..`` or absolute-path components.
    dest = os.path.normpath(os.path.join(extract_dir_abs, member_name))

    # The resolved destination must be strictly inside the extract dir.
    if dest != extract_dir_abs and not dest.startswith(extract_dir_prefix):
        raise ValueError(
            f"Path traversal detected: archive member {member_name!r} resolves to "
            f"{dest!r} which is outside the extraction directory "
            f"{extract_dir_abs!r}. Archive rejected."
        )
    return dest


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def _detect_format(archive_path: str, hint: str) -> str:
    """Return one of ``'zip'``, ``'tar'``, ``'tar.gz'``, ``'gz'``.

    When *hint* is ``'auto'`` the format is detected from the file's magic
    bytes (and falling back to the filename extension).

    Raises
    ------
    ValueError
        If the format cannot be determined or is unsupported.
    """
    if hint not in ("auto", "zip", "tar", "tar.gz", "tgz", "gz"):
        raise ValueError(
            f"Unsupported archive format {hint!r}. "
            "Supported values: 'auto', 'zip', 'tar', 'tar.gz', 'tgz', 'gz'."
        )

    if hint in ("tar.gz", "tgz"):
        return "tar.gz"
    if hint in ("zip", "tar", "gz"):
        return hint

    # Auto-detect from magic bytes.
    with open(archive_path, "rb") as fh:
        magic = fh.read(6)

    # ZIP: PK\x03\x04
    if magic[:4] == b"PK\x03\x04":
        return "zip"
    # GZip: \x1f\x8b
    if magic[:2] == b"\x1f\x8b":
        # Could be .tar.gz — peek inside by trying tarfile.
        import tarfile  # noqa: PLC0415

        if tarfile.is_tarfile(archive_path):
            return "tar.gz"
        return "gz"
    # Tar (ustar): magic at byte 257
    # Rather than checking raw bytes, let tarfile decide.
    import tarfile  # noqa: PLC0415

    if tarfile.is_tarfile(archive_path):
        return "tar"

    # Fall back to extension.
    lower = archive_path.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if lower.endswith(".tar"):
        return "tar"
    if lower.endswith(".gz"):
        return "gz"

    raise ValueError(
        f"Cannot detect archive format for {os.path.basename(archive_path)!r}. "
        "Set 'format' explicitly (zip/tar/tar.gz/gz)."
    )


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _extract_zip(archive_path: str, extract_dir: str) -> list[str]:
    """Extract a ZIP archive into *extract_dir*; return list of member paths.

    Each member is validated with ``_safe_member_path`` before extraction.
    Raises ``ValueError`` on traversal attempt.
    """
    import zipfile  # noqa: PLC0415

    extracted: list[str] = []
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue  # skip directory entries; created implicitly
            dest = _safe_member_path(extract_dir, info.filename)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as dst:
                import shutil  # noqa: PLC0415

                shutil.copyfileobj(src, dst)
            extracted.append(dest)
    return extracted


def _extract_tar(archive_path: str, extract_dir: str, mode: str) -> list[str]:
    """Extract a tar (or tar.gz) archive into *extract_dir*.

    *mode* is passed directly to ``tarfile.open`` (e.g. ``'r'``, ``'r:gz'``).
    Returns a list of absolute paths of extracted regular files.
    """
    import tarfile  # noqa: PLC0415

    extracted: list[str] = []
    with tarfile.open(archive_path, mode) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue  # skip dirs, symlinks, etc.
            dest = _safe_member_path(extract_dir, member.name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            fobj = tf.extractfile(member)
            if fobj is None:
                continue
            import shutil  # noqa: PLC0415

            with fobj, open(dest, "wb") as dst:
                shutil.copyfileobj(fobj, dst)
            extracted.append(dest)
    return extracted


def _extract_gz(archive_path: str, extract_dir: str) -> list[str]:
    """Decompress a bare ``.gz`` file (single-member) into *extract_dir*.

    The output filename is derived by stripping the ``.gz`` suffix (or using
    ``decompressed`` if no suffix is present).  Returns a list with a single
    path.
    """
    import gzip  # noqa: PLC0415
    import shutil  # noqa: PLC0415

    basename = os.path.basename(archive_path)
    if basename.lower().endswith(".gz"):
        out_name = basename[:-3] or "decompressed"
    else:
        out_name = "decompressed"

    dest = _safe_member_path(extract_dir, out_name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with gzip.open(archive_path, "rb") as src, open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return [dest]


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def handle(
    config: dict[str, Any],
    ctx: "TaskContext",
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Unpack an archive from storage and upload extracted members.

    Config schema
    -------------
    ``source_uri`` (str, optional)
        Full storage URI of the archive to extract
        (e.g. ``s3://my-bucket/uploads/data.zip`` or
        ``file:///tmp/staging/archive.tar.gz``).
        Mutually exclusive with ``source``.

    ``source`` (str, optional)
        Key of an upstream task whose result dict contains either a ``'uri'``
        key (treated as a storage URI) or a ``'bytes'`` key (raw bytes,
        base64-encoded string, or ``bytes``).
        Mutually exclusive with ``source_uri``.

    ``dest_uri`` (str, required)
        Storage URI prefix under which extracted members are uploaded.
        Each member is uploaded as ``<dest_uri>/<member-relative-name>``.

    ``secret`` (str, optional)
        Name of a secret (looked up in ``ctx.secrets``) whose JSON-decoded
        value is passed as the ``creds`` dict to
        ``app.storage.get_storage_client``.  If absent, ``None`` is passed
        (environment-variable / credential-chain auth).

    ``format`` (str, optional, default ``'auto'``)
        One of ``'auto'``, ``'zip'``, ``'tar'``, ``'tar.gz'``, ``'tgz'``,
        ``'gz'``.  ``'auto'`` detects from magic bytes / extension.

    Returns
    -------
    dict
        ``{"files": [{"name": str, "size": int, "uri": str}], "file_count": int}``

    Raises
    ------
    ValueError
        For missing/invalid config, unsupported format, or path traversal.
    RuntimeError
        If a required storage SDK is not installed.
    """
    from app.storage.base import get_storage_client, parse_uri  # noqa: PLC0415

    # ------------------------------------------------------------------
    # Resolve credentials from secrets.
    # ------------------------------------------------------------------
    secrets: dict[str, str] = getattr(ctx, "secrets", {}) or {}
    creds: dict[str, Any] | None = None
    secret_name: str | None = config.get("secret")
    if secret_name:
        raw = secrets.get(secret_name)
        if raw is None:
            raise ValueError(
                f"Secret {secret_name!r} not found in task context. "
                "Ensure the secret exists and is accessible to the org."
            )
        try:
            creds = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Secret {secret_name!r} is not valid JSON: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Resolve source URI.
    # ------------------------------------------------------------------
    source_uri: str | None = config.get("source_uri")
    source_key: str | None = config.get("source")

    if source_uri and source_key:
        raise ValueError(
            "extract task config must specify either 'source_uri' or 'source', not both."
        )
    if not source_uri and not source_key:
        raise ValueError(
            "extract task config requires either 'source_uri' or 'source'."
        )

    dest_uri: str | None = config.get("dest_uri")
    if not dest_uri:
        raise ValueError("extract task config requires 'dest_uri'.")

    fmt: str = config.get("format", "auto") or "auto"

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = os.path.join(tmp_dir, "archive")
        extract_dir = os.path.join(tmp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        # ------------------------------------------------------------------
        # Download the archive into the temp dir.
        # ------------------------------------------------------------------
        if source_uri:
            _scheme, _bucket, src_key = parse_uri(source_uri)
            src_client = get_storage_client(source_uri, creds)
            # Detect file extension from the URI key to help format detection.
            archive_path = os.path.join(tmp_dir, os.path.basename(src_key) or "archive")
            src_client.download_to_file(src_key, archive_path)
        else:
            # source key references an upstream task result.
            assert source_key is not None  # guarded above
            upstream = ctx.inputs.get(source_key)
            if upstream is None:
                raise ValueError(
                    f"Upstream task {source_key!r} not found in inputs. "
                    "Ensure the task ran successfully before this extract node."
                )
            if "uri" in upstream:
                upstream_uri: str = upstream["uri"]
                _scheme, _bucket, src_key = parse_uri(upstream_uri)
                src_client = get_storage_client(upstream_uri, creds)
                archive_path = os.path.join(
                    tmp_dir, os.path.basename(src_key) or "archive"
                )
                src_client.download_to_file(src_key, archive_path)
            elif "bytes" in upstream:
                raw_bytes = upstream["bytes"]
                if isinstance(raw_bytes, str):
                    import base64  # noqa: PLC0415

                    archive_bytes = base64.b64decode(raw_bytes)
                elif isinstance(raw_bytes, (bytes, bytearray)):
                    archive_bytes = bytes(raw_bytes)
                else:
                    raise ValueError(
                        f"Upstream task {source_key!r} 'bytes' field must be bytes or "
                        f"a base64-encoded string; got {type(raw_bytes).__name__!r}."
                    )
                with open(archive_path, "wb") as fh:
                    fh.write(archive_bytes)
            else:
                raise ValueError(
                    f"Upstream task {source_key!r} result has neither 'uri' nor 'bytes' key. "
                    f"Available keys: {sorted(upstream.keys())}"
                )

        # ------------------------------------------------------------------
        # Detect format and extract.
        # ------------------------------------------------------------------
        detected = _detect_format(archive_path, fmt)

        if detected == "zip":
            local_files = _extract_zip(archive_path, extract_dir)
        elif detected == "tar":
            local_files = _extract_tar(archive_path, extract_dir, "r")
        elif detected == "tar.gz":
            local_files = _extract_tar(archive_path, extract_dir, "r:gz")
        elif detected == "gz":
            local_files = _extract_gz(archive_path, extract_dir)
        else:
            raise ValueError(f"Unhandled detected format: {detected!r}")

        # ------------------------------------------------------------------
        # Upload each extracted member to dest_uri/<relative-name>.
        # ------------------------------------------------------------------
        dest_client = get_storage_client(dest_uri, creds)
        _dest_scheme, _dest_bucket, dest_prefix = parse_uri(dest_uri)

        files: list[dict[str, Any]] = []
        for local_path in local_files:
            rel = os.path.relpath(local_path, extract_dir).replace(os.sep, "/")
            dest_key = f"{dest_prefix.rstrip('/')}/{rel}" if dest_prefix else rel
            size = os.path.getsize(local_path)
            uri = dest_client.upload_file(local_path, dest_key)
            files.append({"name": rel, "size": size, "uri": uri})

    return {"files": files, "file_count": len(files)}
