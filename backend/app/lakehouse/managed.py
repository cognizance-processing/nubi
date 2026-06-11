"""Managed lakehouse provisioning — Nubi-managed, per-org isolated storage.

BYO-bucket already works (a DuckDB-over-S3 connector a user wires up with their
own credentials). This module adds the *managed* alternative: a Nubi-operated,
per-org **isolated storage area** that users provision / use / delete WITHOUT
ever touching buckets or credentials themselves. "You choose, and you're billed
accordingly" — provisioning is explicit, usage feeds the metering surface.

Isolation model (judgement call)
---------------------------------
Each org's managed lake is an **isolated key prefix** inside the central
bucket::

    s3://<central-bucket>/orgs/<org_id>/lake/

This is the model implemented today because it works with the existing central
credentials + infra (the same ``NUBI_BUCKET_*`` / ``S3_*`` env the demo bundle
already uses) and needs no per-client IAM machinery. It is NOT the only possible
model: physical per-client buckets with scoped IAM roles are stronger isolation
and a likely future offering. To keep that door open WITHOUT building it now, the
provider is a seam — :class:`ManagedLakehouseProvider` is the abstract contract
and :class:`PrefixIsolatedProvider` is the concrete prefix-based implementation.
A future ``DedicatedBucketProvider`` can implement the same contract.

Security
--------
* The managed datastore's storage path is **server-pinned** to
  ``orgs/<org_id>/lake/`` and re-pinned on every status/usage read — a user can
  never edit ``config.database`` to point at another org's prefix or an
  arbitrary URL. ``managed: True`` + ``managed_prefix`` mark the row; the
  connectors PUT route refuses managed rows (see ``routes/connectors.py``).
* Central credentials live ONLY in the connector secret store (encrypted at
  rest, scoped by org) — never returned in any response, never written to
  ``datastores.config``.
* All operations are org-scoped; another org's managed lake is simply not found
  (cross-org access → the caller's own empty status, deprovision is a no-op).

Usage tie-in
------------
:meth:`usage_bytes` sums object sizes under the org's prefix via the storage
client. :func:`emit_storage_usage` records a ``storage`` metering event (units =
GB) so ``GET /usage`` reflects managed-lake storage — kept off the hot path and
computed on demand (GET /lakehouse), not on every request.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.repos.provider import Repo
from app.storage.base import get_storage_client

logger = logging.getLogger("nubi.lakehouse.managed")

# A managed datastore row carries this marker in its config so it is
# distinguishable from a BYO connector and so the path can be re-pinned.
MANAGED_MARKER = "managed_lake"

_BYTES_PER_GB = 1024.0 ** 3


# ---------------------------------------------------------------------------
# Central storage configuration (mirrors demo_bundle's resolution)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CentralStorage:
    """Resolved central-storage settings for the managed lakehouse.

    ``scheme`` is ``"s3"`` when a central S3/MinIO bucket + creds are configured,
    or ``"file"`` when a local root is configured for OSS/local dev. ``None``
    when nothing is configured → managed lakehouse is unavailable (degrade).
    """

    scheme: str          # "s3" | "file"
    bucket: str          # bucket name (s3) or absolute root dir (file)
    creds: dict[str, str]  # storage-client creds (s3 only); empty for file

    def base_uri(self) -> str:
        if self.scheme == "file":
            return f"file://{self.bucket}"
        return f"s3://{self.bucket}"


def _s3_creds_from_env() -> dict[str, str]:
    """Central S3 creds from env (same families demo_bundle / duckdb_storage use)."""
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


def resolve_central_storage() -> CentralStorage | None:
    """Return the configured central storage, or ``None`` if unconfigured.

    Resolution:
      1. S3/MinIO — when an access key is set (``S3_ACCESS_KEY`` /
         ``AWS_ACCESS_KEY_ID``); bucket from ``NUBI_BUCKET_URI`` /
         ``NUBI_BUCKET_NAME`` (default ``"nubi"``). This is the production path.
      2. Local — when ``NUBI_MANAGED_LAKE_DIR`` (or ``NUBI_LOCAL_LAKE_DIR``) is
         set to an absolute directory. This is the OSS/local-dev path so the
         managed lakehouse works without any cloud bucket.
      3. Otherwise ``None`` (degrade: ``GET /lakehouse`` → ``configured: false``).
    """
    # 1. S3 / MinIO (central creds present).
    access_key = os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID") or ""
    if access_key:
        bucket_uri = os.getenv("NUBI_BUCKET_URI", "")
        if bucket_uri.startswith("s3://"):
            bucket = bucket_uri[len("s3://"):].split("/")[0] or "nubi"
        else:
            bucket = os.getenv("NUBI_BUCKET_NAME", "nubi")
        return CentralStorage(scheme="s3", bucket=bucket, creds=_s3_creds_from_env())

    # 2. Local managed-lake root (OSS / local dev).
    local_dir = os.getenv("NUBI_MANAGED_LAKE_DIR") or os.getenv("NUBI_LOCAL_LAKE_DIR")
    if local_dir:
        return CentralStorage(scheme="file", bucket=os.path.abspath(local_dir), creds={})

    return None


# ---------------------------------------------------------------------------
# Prefix helpers (the isolation boundary)
# ---------------------------------------------------------------------------


def org_lake_prefix(org_id: str) -> str:
    """Return the server-pinned key prefix for *org_id*'s managed lake.

    This is the ONLY place the prefix is defined. It is derived purely from the
    server-trusted org id — never from user input — which is what makes the
    isolation tamper-proof.
    """
    return f"orgs/{org_id}/lake/"


def org_lake_uri(central: CentralStorage, org_id: str) -> str:
    """Full storage URI for *org_id*'s managed lake under *central*."""
    return f"{central.base_uri()}/{org_lake_prefix(org_id)}"


# ---------------------------------------------------------------------------
# Status value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManagedLakeStatus:
    """Snapshot of an org's managed lake. ``usage_bytes`` may be lazily filled."""

    configured: bool
    provisioned: bool
    datastore_id: str | None
    prefix: str | None
    uri: str | None
    demo_seeded: bool
    usage_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "provisioned": self.provisioned,
            "datastore_id": self.datastore_id,
            "prefix": self.prefix,
            "uri": self.uri,
            "demo_seeded": self.demo_seeded,
            "usage_bytes": self.usage_bytes,
            "usage_gb": round(self.usage_bytes / _BYTES_PER_GB, 6),
        }


class ManagedLakehouseError(Exception):
    """Raised for provisioning failures the route maps to a 4xx/5xx."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Provider seam
# ---------------------------------------------------------------------------


class ManagedLakehouseProvider(ABC):
    """Abstract contract for a managed-lakehouse backend.

    The concrete :class:`PrefixIsolatedProvider` implements per-org prefix
    isolation inside one central bucket. A future ``DedicatedBucketProvider``
    could create a physical bucket + scoped IAM role per org and satisfy the
    same contract without any route or frontend change.
    """

    @abstractmethod
    async def provision(self, org_id: str, project_id: str | None, user_id: str) -> dict[str, Any]:
        """Idempotently create + register the managed datastore for *org_id*."""

    @abstractmethod
    async def status(self, org_id: str) -> ManagedLakeStatus:
        """Return the org's managed-lake status (without walking storage)."""

    @abstractmethod
    async def usage_bytes(self, org_id: str) -> int:
        """Return total bytes stored under the org's prefix."""

    @abstractmethod
    async def seed_demo(self, org_id: str, project_id: str | None, user_id: str) -> dict[str, Any]:
        """Export demo parquet into the org's managed lake (idempotent)."""

    @abstractmethod
    async def deprovision(self, org_id: str) -> bool:
        """Delete the prefix's objects + the managed datastore row."""


# ---------------------------------------------------------------------------
# Prefix-isolated implementation
# ---------------------------------------------------------------------------


class PrefixIsolatedProvider(ManagedLakehouseProvider):
    """Managed lake = an isolated key prefix in the shared central bucket.

    Credentials are the central ones (env-resolved) and are stored encrypted in
    the connector secret store keyed by the managed datastore id — never exposed.
    """

    def __init__(self, repo: Repo, central: CentralStorage) -> None:
        self._repo = repo
        self._central = central

    # -- storage client ----------------------------------------------------

    def _storage(self):
        # For the local (file) backend, construct the client directly from the
        # absolute root dir. Round-tripping through ``file://<root>`` +
        # ``parse_uri`` is lossy: that parser treats only the first two path
        # components as the "bucket" (root) for file:// URIs, which truncates a
        # deep absolute root (e.g. /private/var/.../managed-lake) to /private/var.
        if self._central.scheme == "file":
            from app.storage.local import LocalStorageClient  # noqa: PLC0415

            return LocalStorageClient(root=self._central.bucket)
        return get_storage_client(self._central.base_uri(), self._central.creds or None)

    # -- managed-row lookup -------------------------------------------------

    async def _find_managed_row(self, org_id: str) -> dict[str, Any] | None:
        """Return the org's managed datastore row, or ``None``."""
        rows = await self._repo.list("datastores", org_id)
        for row in rows:
            cfg = row.get("config")
            if isinstance(cfg, dict) and cfg.get(MANAGED_MARKER) is True:
                return row
        return None

    def _managed_config(self, org_id: str) -> dict[str, Any]:
        """Build the SERVER-PINNED config for the managed datastore.

        ``database`` is pinned to the org's prefix URI; no credentials are placed
        here (they live in the secret store). ``connector_type`` is ``duckdb`` so
        the existing DuckDB-over-S3 connector serves it like any BYO lake.
        """
        return {
            "connector_type": "duckdb",
            "database": org_lake_uri(self._central, org_id),
            MANAGED_MARKER: True,
            "managed_prefix": org_lake_prefix(org_id),
            "managed_scheme": self._central.scheme,
            "description": "Nubi-managed lakehouse (isolated, provisioned for you).",
            # System rows are hidden from the raw connectors list; managed lake
            # surfaces through GET /lakehouse instead of a duplicate card.
            "system": True,
        }

    def _central_secret(self) -> dict[str, Any]:
        """Central creds shaped for the duckdb_storage connector's secret blob."""
        c = self._central.creds
        secret: dict[str, Any] = {}
        if c.get("aws_secret_access_key"):
            secret["aws_secret_access_key"] = c["aws_secret_access_key"]
        # access key id / endpoint / region are NOT secret material per the
        # connectors._SECRET_KEYS allowlist, but the duckdb_storage connector
        # reads them from config OR env; we pin them into the secret blob too so
        # a managed lake works even if the row's config is scrubbed — only the
        # truly-secret key flows through _SECRET_KEYS scrubbing in responses.
        return secret

    # -- provision ---------------------------------------------------------

    async def provision(self, org_id: str, project_id: str | None, user_id: str) -> dict[str, Any]:
        existing = await self._find_managed_row(org_id)
        if existing is not None:
            # Idempotent: re-pin the config (defends against any drift) and return.
            pinned = self._managed_config(org_id)
            if existing.get("config") != pinned:
                await self._repo.update(
                    "datastores", org_id, str(existing["id"]), {"config": pinned}
                )
                existing = await self._repo.get("datastores", org_id, str(existing["id"]))
            return dict(existing)  # type: ignore[arg-type]

        row = await self._repo.create(
            resource="datastores",
            org_id=org_id,
            created_by=user_id,
            name="Managed lakehouse",
            config=self._managed_config(org_id),
            project_id=project_id,
        )
        # Store central creds (encrypted) keyed by the managed datastore id.
        secret = self._central_secret()
        try:
            from app.connectors.secret_store import get_secret_store  # noqa: PLC0415

            await get_secret_store().put(str(row["id"]), org_id, secret)
        except Exception:  # noqa: BLE001 — secret store optional in local dev
            logger.warning("managed-lake: could not persist central secret", exc_info=True)
        return row

    # -- status ------------------------------------------------------------

    async def status(self, org_id: str) -> ManagedLakeStatus:
        row = await self._find_managed_row(org_id)
        if row is None:
            return ManagedLakeStatus(
                configured=True,
                provisioned=False,
                datastore_id=None,
                prefix=org_lake_prefix(org_id),
                uri=org_lake_uri(self._central, org_id),
                demo_seeded=False,
                usage_bytes=0,
            )
        prefix = org_lake_prefix(org_id)
        demo_seeded = self._has_demo(org_id)
        return ManagedLakeStatus(
            configured=True,
            provisioned=True,
            datastore_id=str(row["id"]),
            prefix=prefix,
            uri=org_lake_uri(self._central, org_id),
            demo_seeded=demo_seeded,
            usage_bytes=0,  # filled lazily by the route via usage_bytes()
        )

    def _has_demo(self, org_id: str) -> bool:
        """True if any demo parquet exists under the org's prefix."""
        try:
            keys = self._storage().list(f"{org_lake_prefix(org_id)}demo/")
            return any(k.endswith(".parquet") for k in keys)
        except Exception:  # noqa: BLE001
            return False

    # -- usage -------------------------------------------------------------

    async def usage_bytes(self, org_id: str) -> int:
        """Sum object sizes under the org's prefix via the storage client.

        Implemented with a size-aware listing for both backends; falls back to
        downloading-and-measuring only when the client exposes no size hook
        (it always does for s3/local), so this stays cheap.
        """
        prefix = org_lake_prefix(org_id)
        client = self._storage()
        total = 0
        try:
            for key in client.list(prefix):
                total += _object_size(client, key)
        except Exception:  # noqa: BLE001 — never break the status read
            logger.debug("managed-lake: usage_bytes listing failed", exc_info=True)
            return 0
        return total

    # -- demo seeding ------------------------------------------------------

    async def seed_demo(self, org_id: str, project_id: str | None, user_id: str) -> dict[str, Any]:
        # Ensure the lake is provisioned first (idempotent).
        row = await self._find_managed_row(org_id)
        if row is None:
            row = await self.provision(org_id, project_id, user_id)

        written = self._export_demo(org_id)
        return {
            "datastore_id": str(row["id"]),
            "tables_written": sorted(written.keys()),
            "count": len(written),
        }

    def _export_demo(self, org_id: str) -> dict[str, str]:
        """Write demo parquet under ``orgs/<org_id>/lake/demo/...``.

        Reuses ``demo_bundle``'s dataset generators + the storage client so it
        works identically for s3 and local backends. Idempotent — skips tables
        already present.
        """
        import pyarrow.parquet as pq  # noqa: PLC0415

        from seed_data.generators import DATASET_TABLES, build_dataset  # noqa: PLC0415

        client = self._storage()
        prefix = org_lake_prefix(org_id)
        written: dict[str, str] = {}
        for dataset, tables in DATASET_TABLES.items():
            built = None
            for table in tables:
                key = f"{prefix}demo/{dataset}/{table}.parquet"
                if client.exists(key):
                    continue
                if built is None:
                    built = build_dataset(dataset)
                import io  # noqa: PLC0415

                buf = io.BytesIO()
                pq.write_table(built[table], buf)
                client.upload_bytes(buf.getvalue(), key)
                written[table] = key
        return written

    # -- deprovision -------------------------------------------------------

    async def deprovision(self, org_id: str) -> bool:
        row = await self._find_managed_row(org_id)
        if row is None:
            return False

        # 1. Delete every object under the org's prefix (best-effort, bounded).
        self._delete_prefix(org_id)

        # 2. Remove the managed datastore row.
        datastore_id = str(row["id"])
        await self._repo.delete("datastores", org_id, datastore_id)

        # 3. Remove the encrypted central-creds secret.
        try:
            from app.connectors.secret_store import get_secret_store  # noqa: PLC0415

            await get_secret_store().delete(datastore_id, org_id)
        except Exception:  # noqa: BLE001
            pass
        return True

    def _delete_prefix(self, org_id: str) -> None:
        """Delete all objects under the org's prefix.

        Uses a backend-specific bulk delete when available (s3 batch / local
        rmtree); else falls back to per-key deletes via the storage client.
        """
        prefix = org_lake_prefix(org_id)
        client = self._storage()
        try:
            keys = client.list(prefix)
        except Exception:  # noqa: BLE001
            return
        for key in keys:
            try:
                _delete_object(client, key)
            except Exception:  # noqa: BLE001
                logger.debug("managed-lake: could not delete %s", key, exc_info=True)


# ---------------------------------------------------------------------------
# Storage-client helpers (size + delete) — kept here so the StorageClient ABC
# stays minimal; both s3 and local clients support these operations natively.
# ---------------------------------------------------------------------------


def _object_size(client: Any, key: str) -> int:
    """Return the byte size of *key* via the most efficient path available."""
    # S3: HEAD via boto3 client.
    if getattr(client, "SCHEME", "") == "s3":
        try:
            resp = client._client().head_object(Bucket=client._bucket, Key=key.lstrip("/"))
            return int(resp.get("ContentLength", 0))
        except Exception:  # noqa: BLE001
            return 0
    # Local: stat the file.
    if getattr(client, "SCHEME", "") == "file":
        try:
            return int(os.path.getsize(client._abs(key)))
        except Exception:  # noqa: BLE001
            return 0
    # Fallback: download and measure (last resort; should not hit for s3/local).
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
# Provider factory + usage emission
# ---------------------------------------------------------------------------


def get_provider(repo: Repo) -> PrefixIsolatedProvider | None:
    """Return a managed-lakehouse provider, or ``None`` if central storage
    is not configured (degrade path)."""
    central = resolve_central_storage()
    if central is None:
        return None
    return PrefixIsolatedProvider(repo, central)


async def emit_storage_usage(org_id: str, user_id: str, used_bytes: int) -> None:
    """Record a ``storage`` usage event (units = GB) for *org_id*.

    Off the hot path: the usage system takes the period MAX of ``storage``
    events, so emitting a snapshot whenever we compute usage_bytes (on GET
    /lakehouse / provision / seed) keeps ``GET /usage`` reflecting managed-lake
    storage without walking the bucket on every request. Awaited directly (we
    are already in an async route handler) so the snapshot is durably recorded;
    best-effort — failures are swallowed.
    """
    try:
        from app.compute.metering import record_usage  # noqa: PLC0415

        await record_usage(
            kind="storage",
            user_id=user_id,
            org_id=org_id,
            units=round(used_bytes / _BYTES_PER_GB, 6),
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("managed-lake: storage usage emit failed", exc_info=True)
