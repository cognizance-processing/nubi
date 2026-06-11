"""Staging area + manifest verification for ingestion (design §5).

A *staging area* is a per-run, prefix-isolated transient store where an
ingestion producer (a central worker today; a bridge agent in phase 3) lands
Parquet/object bytes BEFORE they are promoted/loaded into a target connector.

Layout (server-pinned, never user input)::

    <staging-store>/orgs/<org_id>/staging/<run_id>/<rel-path>

The store root + org/run prefix are resolved by
:func:`app.lakehouse.managed.get_staging_area`; this module owns the *writer*,
*reader*, and the **manifest build + verify** helpers that gate promotion.

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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.lakehouse.managed import CentralStorage
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
# Staging area
# ---------------------------------------------------------------------------


class StagingArea:
    """A per-run, prefix-pinned view over a staging store.

    Construct via :func:`app.lakehouse.managed.get_staging_area` (which pins the
    org/run prefix from trusted ids).  Callers pass only RELATIVE sub-paths;
    every key is joined under ``orgs/<org>/staging/<run>/`` so user-supplied
    paths can never escape the prefix.
    """

    def __init__(self, central: "CentralStorage", org_id: str, run_id: str) -> None:
        self._central = central
        self._org_id = org_id
        self._run_id = run_id
        from app.lakehouse.managed import org_staging_prefix  # noqa: PLC0415

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
        # Mirror PrefixIsolatedProvider._storage: build the local client from the
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

        Failed-run cleanup is otherwise handled by the dedicated bucket's
        lifecycle policy (design §5); this is the in-band cleanup after a
        successful promote/load on the same-bucket posture.
        """
        client = self._storage()
        try:
            keys = client.list(self._prefix)
        except Exception:  # noqa: BLE001
            return
        from app.lakehouse.managed import _delete_object  # noqa: PLC0415

        for key in keys:
            try:
                _delete_object(client, key)
            except Exception:  # noqa: BLE001
                continue
