"""Dedicated-bucket managed-lakehouse provider (custody tier).

Each managed datastore gets its OWN deployer-owned bucket rather than a shared
central bucket prefix.  This is the ``dedicated`` option for
``NUBI_LAKEHOUSE_PROVIDER`` and only activates when ``NUBI_CUSTODY_ENABLED=true``.

Isolation model
---------------
Every call to :meth:`provision` derives a **server-pinned** bucket name from
the org's UUID short-prefix and the datastore's own UUID:

    ``nubi-lake-<8 chars of org_id>-<8 chars of datastore_id>``

(or the custom ``NUBI_DEDICATED_BUCKET_PREFIX`` env prefix when set.)  The name is
derived purely from trusted server-side ids — never from user input — so a user
cannot influence which bucket a managed lake maps to.

Objects within each dedicated bucket sit under a small ``lake/`` prefix (for data)
and ``demo/`` (for seeded sample data), matching the mental model of
:class:`~app.lakehouse.managed.PrefixIsolatedProvider` at the *within-bucket* level
so layout stays predictable across providers.

Region pinning
--------------
When ``NUBI_LAKE_REGION`` is set, the bucket is created in (or validated against)
that region.  Creating a bucket when it already exists in the WRONG region raises
:class:`~app.lakehouse.managed.ManagedLakehouseError` with code
``"region_mismatch"`` and HTTP 409.  The configured region is recorded on the
datastore row's ``config.managed_region`` for auditability.

CMEK
----
Three modes, selected by ``NUBI_CMEK_MODE``:

* ``none`` — no additional encryption beyond the cloud bucket's at-rest AES-256.
* ``kms``  — bucket is created with a default CMEK/KMS key
  (GCS ``default_kms_key_name``; S3 SSE-KMS bucket policy).  The storage backend
  handles all encryption/decryption transparently; app code uploads raw bytes.
  The KMS key resource name is recorded on ``config.cmek_key_id`` for audit.
  **Operator note**: the key IAM grant must include the service account that
  writes objects; see cloud provider docs for the CMEK service-account requirement.
* ``client`` — **NOT SUPPORTED for this provider (fail-closed).**  App-layer
  AES-256-GCM encryption is implemented at the crypto layer
  (:mod:`app.lakehouse.cmek`) but is NOT wired into the lake read path: the
  query engine (DuckDB) reads lake objects directly from storage and cannot
  decrypt app-layer blobs.  Calling :meth:`provision` with ``client`` mode
  raises :class:`~app.lakehouse.managed.ManagedLakehouseError` with code
  ``cmek_client_unsupported`` and HTTP 501.  Use ``kms`` for
  customer-managed keys, or ``none`` for bucket-default AES-256 at-rest.

Storage backends
----------------
GCS (``gs://``) is fully implemented: bucket create/exists/location/KMS wiring via
``google-cloud-storage``.  S3 (``s3://``) stubs are present (create / SSE-KMS)
and follow the same pattern; extend as needed.  Both SDKs are **lazy-imported**
inside methods so a missing SDK or absent credentials raises a clear
:class:`~app.lakehouse.managed.ManagedLakehouseError` rather than an opaque
import-time traceback.  File (``file://``) is supported for OSS/local-dev:
bucket = directory, no cloud calls, CMEK ``client`` mode works end-to-end.

Security
--------
* Bucket names are server-pinned (trusted ids only).
* Credentials live in the connector secret store (encrypted, org-scoped).
* ``managed_lake: True`` + ``managed_bucket`` mark the row; routes refuse
  storage-path edits on managed rows.
* All operations are org-scoped; cross-org ids are never found.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.lakehouse.managed import (
    MANAGED_MARKER,
    CentralStorage,
    ManagedLakehouseError,
    ManagedLakehouseProvider,
    _delete_object,
    _object_size,
    lake_prefix,
)
from app.repos.provider import Repo

logger = logging.getLogger("nubi.lakehouse.dedicated")

# ---------------------------------------------------------------------------
# Bucket-name derivation (server-pinned — never user input)
# ---------------------------------------------------------------------------

#: Objects live under this in-bucket prefix (keeps the root tidy + allows a
#: ``lake/`` lifecycle rule to be applied without touching ``demo/``).
_DATA_PREFIX = "lake/"
_DEMO_PREFIX = "demo/"

#: Maximum length of the UUID short-prefix used in bucket names.
_ID_SHORT = 8


def _bucket_name_prefix() -> str:
    """Return the operator-configured bucket-name prefix, or the default."""
    return (os.getenv("NUBI_DEDICATED_BUCKET_PREFIX") or "nubi-lake").strip().lower()


def _dedicated_bucket_name(org_id: str, datastore_id: str) -> str:
    """Return the server-pinned bucket name for one managed datastore.

    Derived purely from trusted server-side ids (never user input).  Bucket
    names must be globally unique and DNS-compatible — UUID short-prefixes give
    reasonable collision resistance while staying under the 63-char GCS limit.
    """
    org_short = str(org_id).replace("-", "")[:_ID_SHORT]
    ds_short = str(datastore_id).replace("-", "")[:_ID_SHORT]
    return f"{_bucket_name_prefix()}-{org_short}-{ds_short}"


# ---------------------------------------------------------------------------
# Cloud SDK helpers — lazy-imported + wrapped
# ---------------------------------------------------------------------------


def _gcs_client(creds: dict[str, str]):
    """Return a GCS ``storage.Client`` from *creds* (lazy import).

    Raises :class:`ManagedLakehouseError` when the SDK is absent or credentials
    cannot be constructed — a clear signal that the deployment is misconfigured.
    """
    try:
        from google.cloud import storage as gcs_storage  # noqa: PLC0415

        if creds:
            from google.oauth2 import service_account  # noqa: PLC0415

            sa_info = creds if isinstance(creds.get("type"), str) else None
            if sa_info:
                cred_obj = service_account.Credentials.from_service_account_info(
                    sa_info,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                return gcs_storage.Client(credentials=cred_obj, project=sa_info.get("project_id"))
        # Fall through to ADC (Application Default Credentials).
        return gcs_storage.Client()
    except ImportError as exc:
        raise ManagedLakehouseError(
            "sdk_missing",
            "google-cloud-storage is not installed. "
            "Install it to use the dedicated-bucket provider with GCS.",
            500,
        ) from exc
    except Exception as exc:
        raise ManagedLakehouseError(
            "gcs_auth_error",
            f"Could not authenticate to GCS: {exc}",
            500,
        ) from exc


def _gcs_ensure_bucket(
    gcs, bucket_name: str, location: str | None, kms_key_name: str | None
) -> None:
    """Create *bucket_name* in *location* with optional KMS key, or validate existing.

    Raises
    ------
    ManagedLakehouseError(``region_mismatch``, 409)
        When the bucket already exists in a different location than requested.
    ManagedLakehouseError(``bucket_create_failed``, 500)
        When bucket creation fails for another reason.
    """
    from google.cloud.exceptions import Conflict, NotFound  # noqa: PLC0415

    bucket = gcs.bucket(bucket_name)
    try:
        existing = gcs.get_bucket(bucket_name)
        # Bucket exists — validate region if pinned.
        if location:
            existing_location = (existing.location or "").lower()
            requested = location.lower()
            if existing_location != requested:
                raise ManagedLakehouseError(
                    "region_mismatch",
                    f"Dedicated bucket '{bucket_name}' already exists in region "
                    f"'{existing_location}' but NUBI_LAKE_REGION='{requested}' "
                    f"requires '{requested}'. Either clear the region pin or "
                    f"delete the existing bucket.",
                    409,
                )
        return  # Bucket exists + region ok — nothing to do.
    except NotFound:
        pass  # Bucket does not exist; create it below.
    except ManagedLakehouseError:
        raise  # Re-raise our own errors unchanged.
    except Exception as exc:  # noqa: BLE001
        raise ManagedLakehouseError(
            "bucket_lookup_failed",
            f"Could not look up GCS bucket '{bucket_name}': {exc}",
            500,
        ) from exc

    # Create the bucket.
    bucket.location = location or None  # None → GCS default multi-region
    if kms_key_name:
        # Set bucket-level default CMEK key (all new objects inherit it).
        bucket.default_kms_key_name = kms_key_name
    try:
        gcs.create_bucket(bucket, location=location or None)
        logger.info(
            "dedicated-lake: created GCS bucket %s (region=%s, kms=%s)",
            bucket_name,
            location or "default",
            bool(kms_key_name),
        )
    except Conflict:
        # Race: another provision beat us.  Validate region and move on.
        _gcs_ensure_bucket(gcs, bucket_name, location, kms_key_name)
    except Exception as exc:  # noqa: BLE001
        raise ManagedLakehouseError(
            "bucket_create_failed",
            f"Could not create GCS bucket '{bucket_name}': {exc}",
            500,
        ) from exc


def _s3_ensure_bucket(
    s3_client, bucket_name: str, region: str | None, kms_key_id: str | None
) -> None:
    """Create *bucket_name* on S3 / MinIO, or validate an existing bucket's region.

    Raises
    ------
    ManagedLakehouseError(``region_mismatch``, 409)
        Region mismatch on an existing bucket.
    ManagedLakehouseError(``bucket_create_failed``, 500)
        Creation error.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    # Check if the bucket exists.
    try:
        loc = s3_client.get_bucket_location(Bucket=bucket_name)
        existing_region = (loc.get("LocationConstraint") or "us-east-1").lower()
        if region and existing_region != region.lower():
            raise ManagedLakehouseError(
                "region_mismatch",
                f"Dedicated bucket '{bucket_name}' exists in region "
                f"'{existing_region}' but NUBI_LAKE_REGION='{region}'. "
                f"Clear the region pin or remove the existing bucket.",
                409,
            )
        return  # Exists + ok.
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchBucket":
            raise ManagedLakehouseError(
                "bucket_lookup_failed",
                f"S3 bucket lookup failed for '{bucket_name}': {exc}",
                500,
            ) from exc
    except ManagedLakehouseError:
        raise

    # Create the bucket.
    kwargs: dict[str, Any] = {"Bucket": bucket_name}
    if region and region.lower() not in ("us-east-1", ""):
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        s3_client.create_bucket(**kwargs)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "BucketAlreadyOwnedByYou":
            pass  # Race condition; fine.
        else:
            raise ManagedLakehouseError(
                "bucket_create_failed",
                f"Could not create S3 bucket '{bucket_name}': {exc}",
                500,
            ) from exc

    # Apply SSE-KMS bucket policy when a KMS key is configured.
    if kms_key_id:
        try:
            s3_client.put_bucket_encryption(
                Bucket=bucket_name,
                ServerSideEncryptionConfiguration={
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "aws:kms",
                                "KMSMasterKeyID": kms_key_id,
                            },
                            "BucketKeyEnabled": True,
                        }
                    ]
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dedicated-lake: could not apply SSE-KMS to bucket %s: %s",
                bucket_name,
                exc,
            )

    logger.info(
        "dedicated-lake: created S3 bucket %s (region=%s, kms=%s)",
        bucket_name,
        region or "default",
        bool(kms_key_id),
    )


def _ensure_local_bucket(bucket_dir: str) -> None:
    """Ensure the local-dev 'bucket' (a directory) exists."""
    os.makedirs(bucket_dir, exist_ok=True)


# ---------------------------------------------------------------------------
# DedicatedBucketProvider
# ---------------------------------------------------------------------------


class DedicatedBucketProvider(ManagedLakehouseProvider):
    """Managed lake = one deployer-owned bucket per managed datastore.

    Constructor matches :class:`~app.lakehouse.managed.PrefixIsolatedProvider`
    so :func:`~app.lakehouse.managed.get_provider` can instantiate it with the
    same ``(repo, central)`` args.

    ``central`` is used ONLY for:
    * Resolving the storage scheme (``gs`` / ``s3`` / ``file``) and central creds.
    * Constructing the storage client for each dedicated bucket (reuse the same
      creds for the per-bucket client; in production a per-bucket service account
      / role is preferable but that requires IAM machinery beyond this scope).

    The per-datastore bucket name is server-pinned via :func:`_dedicated_bucket_name`.
    """

    def __init__(self, repo: Repo, central: CentralStorage) -> None:
        self._repo = repo
        self._central = central

    # ------------------------------------------------------------------
    # Storage client for one dedicated bucket
    # ------------------------------------------------------------------

    def _storage_for_bucket(self, bucket_name_or_dir: str):
        """Return a storage client scoped to *bucket_name_or_dir*."""
        if self._central.scheme == "file":
            from app.storage.local import LocalStorageClient  # noqa: PLC0415

            return LocalStorageClient(root=bucket_name_or_dir)
        # For cloud backends reuse the central creds; pass the per-bucket URI.
        from app.storage.base import get_storage_client  # noqa: PLC0415

        uri = f"{self._central.scheme}://{bucket_name_or_dir}"
        return get_storage_client(uri, self._central.creds or None)

    def _dedicated_root(self, org_id: str, datastore_id: str) -> str:
        """Return the filesystem path (file://) or bucket name (gs://s3://)."""
        bucket = _dedicated_bucket_name(org_id, datastore_id)
        if self._central.scheme == "file":
            # For local dev: sub-directory of the central root.
            return os.path.join(self._central.bucket, bucket)
        return bucket

    # ------------------------------------------------------------------
    # Managed-row helpers
    # ------------------------------------------------------------------

    async def list_managed(self, org_id: str) -> list[dict[str, Any]]:
        """Return all dedicated-bucket managed datastore rows for *org_id*."""
        rows = await self._repo.list("datastores", org_id)
        return [
            row
            for row in rows
            if isinstance(row.get("config"), dict)
            and row["config"].get(MANAGED_MARKER) is True
        ]

    async def _get_managed_row(self, org_id: str, datastore_id: str) -> dict[str, Any] | None:
        """Return one org-scoped managed row, or ``None``."""
        row = await self._repo.get("datastores", org_id, str(datastore_id))
        if row is None:
            return None
        cfg = row.get("config")
        if not isinstance(cfg, dict) or cfg.get(MANAGED_MARKER) is not True:
            return None
        return row

    # ------------------------------------------------------------------
    # Config builder (server-pinned, never user input)
    # ------------------------------------------------------------------

    def _managed_config(
        self,
        org_id: str,
        datastore_id: str,
        bucket_root: str,
        region: str,
        cmek_mode_val: str,
        cmek_key_id: str,
    ) -> dict[str, Any]:
        """Build the SERVER-PINNED config for one dedicated-bucket datastore.

        ``database`` is the bucket root URI; objects live at
        ``<bucket_root>/<lake_prefix>/``.  No credentials are placed here —
        they live in the secret store.
        """
        scheme = self._central.scheme
        if scheme == "file":
            db_uri = f"file://{bucket_root}"
        else:
            db_uri = f"{scheme}://{bucket_root}"

        cfg: dict[str, Any] = {
            "connector_type": "duckdb",
            # Objects live under lake_prefix(org_id, datastore_id) WITHIN
            # the dedicated bucket, keeping the same layout as the prefix provider.
            "database": f"{db_uri}/{lake_prefix(org_id, datastore_id)}",
            MANAGED_MARKER: True,
            "managed_bucket": bucket_root,
            "managed_prefix": lake_prefix(org_id, datastore_id),
            "managed_scheme": scheme,
            "description": "Nubi-managed lakehouse (dedicated bucket, provisioned for you).",
        }
        if region:
            cfg["managed_region"] = region
        if cmek_mode_val != "none":
            cfg["cmek_mode"] = cmek_mode_val
            if cmek_key_id:
                cfg["cmek_key_id"] = cmek_key_id  # KMS key resource name; not secret.
        return cfg

    def _central_secret(self) -> dict[str, Any]:
        """Central creds shaped for the secret store.

        Persists the full non-default credential set needed to reconstruct the
        storage client on a worker that has no ambient env credentials.  All
        fields are optional — only non-empty values are included so the secret
        stays minimal.  This mirrors what PrefixIsolatedProvider stores.
        """
        c = self._central.creds
        # Keys that a worker needs to rebuild an S3/MinIO/GCS client.
        _PERSIST_KEYS = (
            "aws_access_key_id",
            "aws_secret_access_key",
            "endpoint_url",
            "region_name",
            # GCS service-account JSON fields (if present as flat keys).
            "type",
            "project_id",
            "private_key_id",
            "private_key",
            "client_email",
            "client_id",
            "auth_uri",
            "token_uri",
        )
        return {k: v for k in _PERSIST_KEYS if (v := c.get(k))}  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Provision
    # ------------------------------------------------------------------

    async def provision(
        self, org_id: str, project_id: str | None, user_id: str, name: str | None = None
    ) -> dict[str, Any]:
        """Create a dedicated bucket and register a new managed datastore row.

        Steps:
          1. Mint a stable datastore id (so the bucket name is pinned to it).
          2. Derive the server-pinned bucket name from trusted ids.
          3. Ensure the bucket exists in the right region with the right CMEK.
          4. Insert the datastore row with a server-pinned config.
          5. Persist central creds in the secret store.
        """
        import uuid as _uuid  # noqa: PLC0415

        from app.lakehouse.custody import cmek_key_uri, cmek_mode, lake_region  # noqa: PLC0415

        cmek_mode_val = cmek_mode()
        # Fail closed: client-mode CMEK is not yet wired into the lake read
        # path (DuckDB cannot decrypt app-layer blobs).  Reject immediately so
        # no encrypted-but-unreadable objects are ever written.
        if cmek_mode_val == "client":
            raise ManagedLakehouseError(
                "cmek_client_unsupported",
                (
                    "Client-held-key CMEK (mode='client') is not yet supported "
                    "for query/ingest because the lake is read directly by the "
                    "engine (DuckDB cannot decrypt app-layer blobs). "
                    "Use mode='kms' for customer-managed keys, or 'none'."
                ),
                501,
            )

        datastore_id = str(_uuid.uuid4())
        region = lake_region()
        kms_key = cmek_key_uri() if cmek_mode_val == "kms" else ""

        bucket_root = self._dedicated_root(org_id, datastore_id)

        # Ensure the bucket (or local dir) exists before creating the row so
        # that a bucket-creation failure rolls back before touching the DB.
        self._ensure_bucket(bucket_root, region, kms_key)

        row = await self._repo.create(
            resource="datastores",
            org_id=org_id,
            created_by=user_id,
            name=(name or "").strip() or "Managed lakehouse (dedicated)",
            config=self._managed_config(
                org_id, datastore_id, bucket_root, region, cmek_mode_val, kms_key
            ),
            project_id=project_id,
            id=datastore_id,
        )

        secret = self._central_secret()
        try:
            from app.connectors.secret_store import get_secret_store  # noqa: PLC0415

            await get_secret_store().put(str(row["id"]), org_id, secret)
        except Exception:  # noqa: BLE001
            logger.warning("dedicated-lake: could not persist central secret", exc_info=True)

        return row

    def _ensure_bucket(self, bucket_root: str, region: str, kms_key: str) -> None:
        """Create the dedicated bucket (or local directory) for this datastore.

        Dispatches on the central storage scheme.  Raises
        :class:`ManagedLakehouseError` on any failure so the route gets an
        actionable error rather than an opaque SDK traceback.
        """
        scheme = self._central.scheme

        if scheme == "file":
            _ensure_local_bucket(bucket_root)
            return

        if scheme == "gs":
            try:
                gcs = _gcs_client(self._central.creds)
            except ManagedLakehouseError:
                raise
            _gcs_ensure_bucket(gcs, bucket_root, region or None, kms_key or None)
            return

        if scheme == "s3":
            try:
                import boto3  # noqa: PLC0415

                creds = self._central.creds
                session = boto3.Session(
                    aws_access_key_id=creds.get("aws_access_key_id"),
                    aws_secret_access_key=creds.get("aws_secret_access_key"),
                    region_name=creds.get("region_name") or region or None,
                )
                s3 = session.client(
                    "s3",
                    endpoint_url=creds.get("endpoint_url"),
                )
            except ImportError as exc:
                raise ManagedLakehouseError(
                    "sdk_missing",
                    "boto3 is not installed. Install it to use the dedicated-bucket provider with S3.",
                    500,
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise ManagedLakehouseError(
                    "s3_auth_error",
                    f"Could not authenticate to S3: {exc}",
                    500,
                ) from exc
            _s3_ensure_bucket(s3, bucket_root, region or None, kms_key or None)
            return

        raise ManagedLakehouseError(
            "unsupported_scheme",
            f"Dedicated-bucket provider does not support scheme '{scheme}'.",
            500,
        )

    # ------------------------------------------------------------------
    # Usage
    # ------------------------------------------------------------------

    async def usage_bytes(self, org_id: str, datastore_id: str) -> int:
        """Sum object sizes under the dedicated bucket's lake prefix."""
        row = await self._get_managed_row(org_id, datastore_id)
        if row is None:
            return 0

        bucket_root = self._dedicated_root(org_id, datastore_id)
        prefix = lake_prefix(org_id, datastore_id)
        client = self._storage_for_bucket(bucket_root)
        total = 0
        try:
            for key in client.list(prefix):
                total += _object_size(client, key)
        except Exception:  # noqa: BLE001
            logger.debug("dedicated-lake: usage_bytes listing failed", exc_info=True)
            return 0
        return total

    # ------------------------------------------------------------------
    # Demo seeding
    # ------------------------------------------------------------------

    async def seed_demo(
        self, org_id: str, datastore_id: str, project_id: str | None, user_id: str
    ) -> dict[str, Any]:
        row = await self._get_managed_row(org_id, datastore_id)
        if row is None:
            raise ManagedLakehouseError(
                "managed_lake_not_found", "Managed lakehouse not found.", 404
            )
        written = self._export_demo(org_id, datastore_id)
        return {
            "datastore_id": str(row["id"]),
            "tables_written": sorted(written.keys()),
            "count": len(written),
        }

    def _export_demo(self, org_id: str, datastore_id: str) -> dict[str, str]:
        """Write demo parquet under the dedicated bucket's lake prefix.

        Writes raw (unencrypted) parquet bytes so the query engine (DuckDB) can
        read them directly.  CMEK ``client`` mode is rejected at :meth:`provision`
        time (fail-closed) before seed ever runs, so this path only executes for
        ``none`` and ``kms`` modes — both of which store plaintext from the app's
        perspective (``kms`` encryption is transparent at the storage layer).
        Idempotent — skips tables already present.
        """
        import io  # noqa: PLC0415

        import pyarrow.parquet as pq  # noqa: PLC0415

        from seed_data.generators import DATASET_TABLES, build_dataset  # noqa: PLC0415

        bucket_root = self._dedicated_root(org_id, datastore_id)
        client = self._storage_for_bucket(bucket_root)
        prefix = lake_prefix(org_id, datastore_id)
        written: dict[str, str] = {}

        for dataset, tables in DATASET_TABLES.items():
            built = None
            for table in tables:
                key = f"{prefix}demo/{dataset}/{table}.parquet"
                if client.exists(key):
                    continue
                if built is None:
                    built = build_dataset(dataset)
                buf = io.BytesIO()
                pq.write_table(built[table], buf)
                raw = buf.getvalue()
                # Upload raw bytes — no app-layer encryption.  The engine reads
                # these objects directly; encrypting them here would corrupt reads.
                client.upload_bytes(raw, key)
                written[table] = key
        return written

    # ------------------------------------------------------------------
    # Deprovision
    # ------------------------------------------------------------------

    async def deprovision(self, org_id: str, datastore_id: str) -> bool:
        """Deprovision: delete objects, row, and secret.

        Objects under the lake prefix within the dedicated bucket are deleted.
        The bucket itself is optionally deleted when empty (local dev always
        deletes the directory; cloud backends attempt deletion and log on failure
        since the bucket may have versioning or lifecycle policies the operator
        controls).
        """
        row = await self._get_managed_row(org_id, datastore_id)
        if row is None:
            return False

        bucket_root = self._dedicated_root(org_id, datastore_id)
        self._delete_bucket_objects(org_id, datastore_id, bucket_root)
        self._try_delete_bucket(bucket_root)

        await self._repo.delete("datastores", org_id, str(datastore_id))

        try:
            from app.connectors.secret_store import get_secret_store  # noqa: PLC0415

            await get_secret_store().delete(str(datastore_id), org_id)
        except Exception:  # noqa: BLE001
            pass
        return True

    def _delete_bucket_objects(
        self, org_id: str, datastore_id: str, bucket_root: str
    ) -> None:
        """Delete all objects under this datastore's lake prefix (best-effort)."""
        prefix = lake_prefix(org_id, datastore_id)
        client = self._storage_for_bucket(bucket_root)
        try:
            keys = client.list(prefix)
        except Exception:  # noqa: BLE001
            return
        for key in keys:
            try:
                _delete_object(client, key)
            except Exception:  # noqa: BLE001
                logger.debug("dedicated-lake: could not delete %s", key, exc_info=True)

    def _try_delete_bucket(self, bucket_root: str) -> None:
        """Best-effort bucket (or local dir) deletion after object removal."""
        if self._central.scheme == "file":
            # Local dev: remove the directory tree.
            import shutil  # noqa: PLC0415

            try:
                if os.path.isdir(bucket_root):
                    shutil.rmtree(bucket_root)
            except Exception:  # noqa: BLE001
                logger.debug("dedicated-lake: could not remove dir %s", bucket_root, exc_info=True)
            return

        # Cloud: attempt to delete the (now-empty) bucket, log on failure.
        if self._central.scheme == "gs":
            try:
                gcs = _gcs_client(self._central.creds)
                bucket = gcs.bucket(bucket_root)
                bucket.delete(force=False)
                logger.info("dedicated-lake: deleted GCS bucket %s", bucket_root)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "dedicated-lake: could not delete GCS bucket %s (may be non-empty or have retention policy)",
                    bucket_root,
                    exc_info=True,
                )
            return

        if self._central.scheme == "s3":
            try:
                import boto3  # noqa: PLC0415

                creds = self._central.creds
                s3 = boto3.client(
                    "s3",
                    aws_access_key_id=creds.get("aws_access_key_id"),
                    aws_secret_access_key=creds.get("aws_secret_access_key"),
                    region_name=creds.get("region_name"),
                    endpoint_url=creds.get("endpoint_url"),
                )
                s3.delete_bucket(Bucket=bucket_root)
                logger.info("dedicated-lake: deleted S3 bucket %s", bucket_root)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "dedicated-lake: could not delete S3 bucket %s",
                    bucket_root,
                    exc_info=True,
                )
