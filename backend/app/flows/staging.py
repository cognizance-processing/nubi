"""Flows staging area — transient per-run scratch space for the ETL pipeline.

A *staging area* is a per-run, prefix-isolated transient store where a Flows
task (``file_ingest`` / ``connector_write`` / a ``python`` cell's
``ctx.staging``) lands Parquet bytes BEFORE they are loaded into a target
connector (:mod:`app.flows.loaders`).

This is scratch space for ONE flow run, not a hosted data-warehouse product —
Nubi does not operate a persistent, billed "managed lakehouse" (that surface
was removed).  Resolution order:

  1. A dedicated staging bucket/dir the deployer configured
     (``NUBI_STAGING_BUCKET_URI`` / ``NUBI_STAGING_DIR``) — useful in
     production so staging bytes never touch local disk.
  2. A process-local ephemeral temp directory (created lazily, reused for the
     life of the process) — the zero-config default so Flows staging just
     works out of the box without any bucket.

Layout (server-pinned, never user input)::

    <staging-store>/orgs/<org_id>/staging/<run_id>/<rel-path>

Manifest contract (design §5)
-----------------------------
The producer reports::

    {
        "files": [{"path": "<rel>", "size": <int>, "sha256": "<hex>"}, ...],
        "row_counts": {"<rel>": <int>, ...},
    }

``path`` is RELATIVE to the staging prefix (the producer cannot name another
org/run's object).  The server re-reads each staged object and verifies its
*size* and *sha256* against the manifest BEFORE promote/load.  A malicious
producer can write garbage into its own prefix but cannot silently poison a
target: a size/hash mismatch raises :class:`ManifestVerificationError` and the
load is aborted.

All I/O is synchronous (run inside a thread executor from async callers),
consistent with the :class:`~app.storage.base.StorageClient` abstraction.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.storage.base import StorageClient


class ManifestVerificationError(Exception):
    """Raised when a staged object fails size/sha256 verification.

    Carries the offending relative ``path`` and a human reason so the
    file_ingest handler can fail the task with an actionable message and refuse
    to promote/load tampered or truncated data.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"staging manifest verification failed for {path!r}: {reason}")
        self.path = path
        self.reason = reason


# ---------------------------------------------------------------------------
# Manifest value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestEntry:
    """One staged object in a run manifest (``{path, size, sha256}``)."""

    path: str          # RELATIVE to the staging prefix
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


@dataclass(frozen=True)
class StagingManifest:
    """The producer-reported manifest for a run's staged output (design §5)."""

    files: list[ManifestEntry] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [e.to_dict() for e in self.files],
            "row_counts": dict(self.row_counts),
        }

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts.values())

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.files)


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of *data*."""
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Staging-store resolution (design: scratch space, not a hosted product)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CentralStorage:
    """Resolved staging-store settings.

    ``scheme`` is ``"s3"`` when a dedicated staging bucket is configured, or
    ``"file"`` for a local directory (deployer-configured OR the zero-config
    ephemeral fallback).
    """

    scheme: str          # "s3" | "file"
    bucket: str          # bucket name (s3) or absolute root dir (file)
    creds: dict[str, str]  # storage-client creds (s3 only); empty for file

    def base_uri(self) -> str:
        if self.scheme == "file":
            return f"file://{self.bucket}"
        return f"s3://{self.bucket}"


def _s3_creds_from_env() -> dict[str, str]:
    creds: dict[str, str] = {}
    key = os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret = os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    endpoint = os.getenv("S3_ENDPOINT_URL") or os.getenv("AWS_ENDPOINT_URL")
    region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION")
    if key:
        creds["aws_access_key_id"] = key
    if secret:
        creds["aws_secret_access_key"] = secret
    if endpoint:
        creds["endpoint_url"] = endpoint
    if region:
        creds["region_name"] = region
    return creds


_ephemeral_root_dir: str | None = None


def _ephemeral_root() -> str:
    """Return (creating once) a process-local temp dir for zero-config staging."""
    global _ephemeral_root_dir
    if _ephemeral_root_dir is None or not os.path.isdir(_ephemeral_root_dir):
        _ephemeral_root_dir = tempfile.mkdtemp(prefix="nubi-flows-staging-")
    return _ephemeral_root_dir


def resolve_staging_storage() -> CentralStorage:
    """Return the configured staging store — never ``None`` (always available).

    Resolution order:
      1. Dedicated staging bucket (S3) — ``NUBI_STAGING_BUCKET_URI``.
      2. Dedicated staging dir (local) — ``NUBI_STAGING_DIR``.
      3. Zero-config fallback — a process-local ephemeral temp directory.
    """
    bucket_uri = os.getenv("NUBI_STAGING_BUCKET_URI", "")
    if bucket_uri.startswith("s3://"):
        bucket = bucket_uri[len("s3://"):].split("/")[0]
        if bucket:
            return CentralStorage(scheme="s3", bucket=bucket, creds=_s3_creds_from_env())

    staging_dir = os.getenv("NUBI_STAGING_DIR")
    if staging_dir:
        return CentralStorage(scheme="file", bucket=os.path.abspath(staging_dir), creds={})

    return CentralStorage(scheme="file", bucket=_ephemeral_root(), creds={})


def org_staging_prefix(org_id: str, run_id: str) -> str:
    """Server-pinned per-run staging key prefix for *org_id* / *run_id*.

    The ONLY definition of the staging prefix — derived purely from trusted ids
    so a producer can never escape its own run's prefix (design §5).
    """
    safe_run = str(run_id).strip().strip("/") or "_run"
    return f"orgs/{org_id}/staging/{safe_run}/"


def get_staging_area(org_id: str, run_id: str) -> "StagingArea":
    """Return a :class:`StagingArea` for *org_id* / *run_id*.

    Always resolves (staging is process-local scratch space, not a hosted
    product that can be "unconfigured") — the org/run prefix is pinned here
    from trusted ids; callers pass only *relative* sub-paths.
    """
    staging = resolve_staging_storage()
    return StagingArea(central=staging, org_id=org_id, run_id=run_id)


def _object_size(client: Any, key: str) -> int:
    if getattr(client, "SCHEME", "") == "s3":
        try:
            resp = client._client().head_object(Bucket=client._bucket, Key=key.lstrip("/"))
            return int(resp.get("ContentLength", 0))
        except Exception:  # noqa: BLE001
            return 0
    if getattr(client, "SCHEME", "") == "file":
        try:
            return int(os.path.getsize(client._abs(key)))
        except Exception:  # noqa: BLE001
            return 0
    try:
        return len(client.download_bytes(key))
    except Exception:  # noqa: BLE001
        return 0


def _delete_object(client: Any, key: str) -> None:
    """Delete *key* via the backend's native delete."""
    if getattr(client, "SCHEME", "") == "s3":
        client._client().delete_object(Bucket=client._bucket, Key=key.lstrip("/"))
        return
    if getattr(client, "SCHEME", "") == "file":
        path = client._abs(key)
        if os.path.isfile(path):
            os.remove(path)
        return
    raise RuntimeError(f"delete not supported for backend {client!r}")


# ---------------------------------------------------------------------------
# Staging area
# ---------------------------------------------------------------------------


class StagingArea:
    """A per-run, prefix-pinned view over a staging store.

    Construct via :func:`get_staging_area` (which pins the org/run prefix from
    trusted ids).  Callers pass only RELATIVE sub-paths; every key is joined
    under ``orgs/<org>/staging/<run>/`` so user-supplied paths can never escape
    the prefix.
    """

    def __init__(self, central: "CentralStorage", org_id: str, run_id: str) -> None:
        self._central = central
        self._org_id = org_id
        self._run_id = run_id
        self._prefix = org_staging_prefix(org_id, run_id)

    # -- introspection -----------------------------------------------------

    @property
    def prefix(self) -> str:
        """The server-pinned ``orgs/<org>/staging/<run>/`` key prefix."""
        return self._prefix

    @property
    def base_uri(self) -> str:
        """The store root URI (without the org/run prefix)."""
        return self._central.base_uri()

    def uri(self, rel_path: str = "") -> str:
        """Full URI for *rel_path* under this run's staging prefix."""
        return f"{self._central.base_uri()}/{self._key(rel_path)}"

    # -- storage client / key pinning -------------------------------------

    def _storage(self) -> "StorageClient":
        # Mirror the storage-client resolution: build the local client from the
        # absolute root directly (file:// round-trip is lossy for deep roots).
        if self._central.scheme == "file":
            from app.storage.local import LocalStorageClient  # noqa: PLC0415

            return LocalStorageClient(root=self._central.bucket)
        from app.storage.base import get_storage_client  # noqa: PLC0415

        return get_storage_client(self._central.base_uri(), self._central.creds or None)

    def _key(self, rel_path: str) -> str:
        """Join *rel_path* under the pinned prefix, refusing prefix escapes.

        ``..`` segments / absolute paths are stripped so the resulting key can
        never climb above ``orgs/<org>/staging/<run>/``.
        """
        rel = str(rel_path).strip().lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        return self._prefix + "/".join(parts)

    # -- write / read ------------------------------------------------------

    def write_bytes(self, data: bytes, rel_path: str) -> ManifestEntry:
        """Write *data* at *rel_path* under the staging prefix.

        Returns the :class:`ManifestEntry` (size + sha256) the producer reports
        in its manifest, so the writer and the verifier agree on the contract.
        """
        client = self._storage()
        client.upload_bytes(data, self._key(rel_path))
        return ManifestEntry(path=rel_path, size=len(data), sha256=sha256_bytes(data))

    def read_bytes(self, rel_path: str) -> bytes:
        """Read the staged object at *rel_path* (relative to the prefix)."""
        return self._storage().download_bytes(self._key(rel_path))

    # -- manifest build + verify ------------------------------------------

    def build_manifest(
        self, entries: list[ManifestEntry], row_counts: dict[str, int] | None = None
    ) -> StagingManifest:
        """Assemble a :class:`StagingManifest` from already-written *entries*."""
        return StagingManifest(files=list(entries), row_counts=dict(row_counts or {}))

    def verify(self, manifest: StagingManifest) -> None:
        """Verify every manifest entry against the staged bytes (design §5).

        Re-reads each object under the pinned prefix and checks size + sha256.
        Raises :class:`ManifestVerificationError` on the FIRST mismatch / missing
        object — the caller must abort promote/load.  This is the trust gate:
        the producer reports the manifest, the SERVER verifies the bytes.
        """
        client = self._storage()
        for entry in manifest.files:
            key = self._key(entry.path)
            try:
                data = client.download_bytes(key)
            except FileNotFoundError as exc:
                raise ManifestVerificationError(
                    entry.path, "staged object missing"
                ) from exc
            if len(data) != entry.size:
                raise ManifestVerificationError(
                    entry.path,
                    f"size mismatch (manifest {entry.size}, actual {len(data)})",
                )
            actual = sha256_bytes(data)
            if actual != entry.sha256:
                raise ManifestVerificationError(
                    entry.path,
                    f"sha256 mismatch (manifest {entry.sha256}, actual {actual})",
                )

    def cleanup(self) -> None:
        """Best-effort delete of every object under this run's staging prefix.

        Staging is ephemeral scratch space — cleanup after a successful
        promote/load (or best-effort on a failed run) keeps it from growing
        unbounded, since there is no lifecycle-policy backstop bucket here.
        """
        client = self._storage()
        try:
            keys = client.list(self._prefix)
        except Exception:  # noqa: BLE001
            return
        for key in keys:
            try:
                _delete_object(client, key)
            except Exception:  # noqa: BLE001
                continue
