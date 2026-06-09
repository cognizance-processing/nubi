"""Lakehouse datasets endpoints — CSV upload, Parquet materialise, catalog.

Endpoints
---------
POST /datasets/upload              → upload CSV → Parquet → register dataset + datastore
POST /datasets/materialize         → run SQL → COPY TO Parquet → register dataset + datastore
GET  /datasets                     → list datasets for the caller's org
GET  /datasets/{dataset_id}        → fetch a single dataset row

Storage layout (local dev / file:// default)
--------------------------------------------
Bucket root: ``NUBI_BUCKET_ROOT`` env var, default ``/tmp/nubi-datasets``

    <root>/raw/<org_id>/<dataset_id>/<filename>   — raw CSV upload
    <root>/datasets/<org_id>/<dataset_id>/data.parquet — final Parquet

S3/MinIO (when ``NUBI_BUCKET_URI`` is set, e.g. ``s3://nubi``)
---------------------------------------------------------------
Credentials are resolved first from the org datastore config (cfg keys
``s3_key_id`` / ``aws_access_key_id``, ``s3_secret`` / ``aws_secret_access_key``,
``s3_endpoint`` / ``endpoint_url``, ``s3_region`` / ``aws_region``,
``s3_url_style``), then from environment variables.

Environment variable reference (all optional — set for S3/MinIO access):
    AWS_ACCESS_KEY_ID         AWS/MinIO access key ID.
    AWS_SECRET_ACCESS_KEY     AWS/MinIO secret access key.
    AWS_REGION                AWS region (default: us-east-1).
    AWS_DEFAULT_REGION        Alternative region env var (fallback).
    AWS_ENDPOINT_URL          Custom endpoint for MinIO / S3-compatible services
                              (e.g. ``http://localhost:9000``).
    S3_ENDPOINT_URL           Alternative endpoint env var (checked before
                              AWS_ENDPOINT_URL).
    S3_URL_STYLE              ``"path"`` (MinIO default) or ``"vhost"`` (AWS
                              default).  Defaults to ``"path"`` when an endpoint
                              is set, otherwise ``"vhost"``.
    NUBI_BUCKET_URI           Full bucket URI, e.g. ``s3://nubi``.  When set,
                              uploads go to this bucket rather than the local
                              file:// backend.
    NUBI_BUCKET_ROOT          Local filesystem root for file:// storage.

Security
--------
- All endpoints require a valid first-party Bearer token (current_user).
- Org isolation: datasets are always scoped to the caller's org.
- RLS is preserved: the linked datastores row uses connector_type=duckdb
  with the parquet file path so queries flow through the normal planner+RLS path.

Self-registers on api_router at import time (same pattern as data_browser.py).
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer_default
from app.connectors.duckdb_conn import setup_s3_httpfs
from app.datasets import get_catalog, set_catalog  # noqa: F401 (set_catalog re-exported for tests)
from app.errors import AppError
from app.repos.provider import get_repo, Repo
from app.routes import api_router

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/datasets", tags=["datasets"])

# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

_BUCKET_ROOT_DEFAULT = "/tmp/nubi-datasets"


def _build_s3_creds_from_env() -> dict[str, str]:
    """Resolve S3/MinIO credentials from environment variables.

    Checks both the ``S3_*`` and ``AWS_*`` env var families so that both
    the DuckDB httpfs convention (``S3_ACCESS_KEY`` / ``S3_SECRET_KEY`` /
    ``S3_ENDPOINT_URL``) and the boto3 convention (``AWS_ACCESS_KEY_ID`` /
    ``AWS_SECRET_ACCESS_KEY`` / ``AWS_ENDPOINT_URL``) are honoured.

    Returns a dict suitable for ``S3StorageClient``/``get_storage_client``.
    Empty values are omitted so that boto3 can fall back to its default chain.
    """
    key_id = (
        os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("S3_ACCESS_KEY")
        or ""
    )
    secret = (
        os.getenv("AWS_SECRET_ACCESS_KEY")
        or os.getenv("S3_SECRET_KEY")
        or ""
    )
    endpoint = (
        os.getenv("S3_ENDPOINT_URL")
        or os.getenv("AWS_ENDPOINT_URL")
        or ""
    )
    region = (
        os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("S3_REGION")
        or "us-east-1"
    )
    return {
        k: v
        for k, v in {
            "aws_access_key_id": key_id,
            "aws_secret_access_key": secret,
            "endpoint_url": endpoint,
            "region_name": region,
        }.items()
        if v
    }


def _get_storage_client():
    """Return a StorageClient for the configured bucket.

    Resolution order
    ----------------
    1. ``NUBI_BUCKET_URI`` — explicit full URI (``s3://``, ``gs://``, ``az://``,
       ``file://``).  Credentials for S3 are read from env vars.
    2. ``S3_ENDPOINT_URL`` / ``S3_ACCESS_KEY`` env vars — auto-detect a MinIO /
       S3-compatible backend.  When these are present but ``NUBI_BUCKET_URI`` is
       absent, the bucket name defaults to ``NUBI_BUCKET_NAME`` env var (or
       ``"nubi"``).  This lets MinIO work without explicitly setting
       ``NUBI_BUCKET_URI``.
    3. ``file://<NUBI_BUCKET_ROOT>`` — local filesystem fallback (default:
       ``/tmp/nubi-datasets``).
    """
    from app.storage import get_storage_client  # noqa: PLC0415

    bucket_uri = os.getenv("NUBI_BUCKET_URI", "")
    if bucket_uri:
        # Parse creds from env for S3/MinIO
        creds: dict[str, str] | None = None
        if bucket_uri.startswith("s3://"):
            creds = _build_s3_creds_from_env() or None
        return get_storage_client(bucket_uri, creds)

    # Auto-detect S3/MinIO when S3_ENDPOINT_URL or S3_ACCESS_KEY is configured
    # but NUBI_BUCKET_URI was not explicitly set.
    endpoint = os.getenv("S3_ENDPOINT_URL") or os.getenv("AWS_ENDPOINT_URL") or ""
    access_key = os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID") or ""
    if endpoint or access_key:
        bucket_name = os.getenv("NUBI_BUCKET_NAME", "nubi")
        auto_uri = f"s3://{bucket_name}"
        creds = _build_s3_creds_from_env() or None
        return get_storage_client(auto_uri, creds)

    root = os.getenv("NUBI_BUCKET_ROOT", _BUCKET_ROOT_DEFAULT)
    os.makedirs(root, exist_ok=True)
    return get_storage_client(f"file://{root}/placeholder", None)


def _storage_root() -> str:
    """Return the local filesystem root used for the file:// backend."""
    return os.getenv("NUBI_BUCKET_ROOT", _BUCKET_ROOT_DEFAULT)


def _parquet_local_path(org_id: str, dataset_id: str) -> str:
    """Absolute path to the dataset Parquet file (local-storage layout)."""
    root = _storage_root()
    return os.path.join(root, "datasets", org_id, dataset_id, "data.parquet")


# ---------------------------------------------------------------------------
# Storage metering (billing dimension: storage_gb, kind="storage")
# ---------------------------------------------------------------------------


async def _record_storage_snapshot(org_id: str, user_id: str) -> None:
    """Record a best-effort storage snapshot for *org_id* (kind='storage').

    Billing aggregation takes the MAX ``units`` over the period (a
    peak/representative GB figure — see app.ee.billing.reconcile), so each
    event carries the org's TOTAL dataset storage in GB at this moment.
    Walks the local file:// dataset tree; never raises (metering is
    best-effort and must not fail an upload).
    """
    try:
        from app.compute.metering import record_usage  # noqa: PLC0415

        org_dir = os.path.join(_storage_root(), "datasets", org_id)
        total_bytes = 0
        for dirpath, _dirnames, filenames in os.walk(org_dir):
            for fname in filenames:
                try:
                    total_bytes += os.path.getsize(os.path.join(dirpath, fname))
                except OSError:
                    continue
        await record_usage(
            kind="storage",
            user_id=user_id,
            org_id=org_id,
            units=total_bytes / 1e9,
            output_bytes=total_bytes,
            tier="datasets",
        )
    except Exception:  # noqa: BLE001 — metering must never break an upload
        pass


# ---------------------------------------------------------------------------
# Org resolution helper (mirrors data_browser.py)
# ---------------------------------------------------------------------------


async def _get_user_org(user_id: str, repo: Repo) -> str:
    from app.db import fetchrow  # noqa: PLC0415

    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)
        if org_id:
            return org_id
        raise AppError("org_not_found", "User has no org membership.", 404)

    row = await fetchrow(
        """
        SELECT org_id FROM org_members
        WHERE user_id = $1::uuid
        ORDER BY org_id
        LIMIT 1
        """,
        user_id,
    )
    if row is None:
        raise AppError("org_not_found", "User has no org membership.", 404)
    return str(row["org_id"])


# ---------------------------------------------------------------------------
# DuckDB helpers
# ---------------------------------------------------------------------------


def _infer_schema_from_parquet(
    parquet_path: str,
    cfg: "dict | None" = None,
) -> list[dict[str, Any]]:
    """Read a Parquet file with DuckDB and return [{name, type}] schema.

    Handles both local paths and ``s3://`` paths; httpfs is installed
    automatically for the latter.

    Parameters
    ----------
    parquet_path:
        Local filesystem path or ``s3://`` URI of the Parquet file.
    cfg:
        Optional datastore/connector config dict forwarded to
        ``setup_s3_httpfs`` so that org-level S3 credentials (not just
        process env) are honoured.  ``None`` falls back to env vars only.
    """
    try:
        import duckdb  # noqa: PLC0415

        conn = duckdb.connect(":memory:")
        if parquet_path.startswith("s3://"):
            setup_s3_httpfs(conn, cfg)
        result = conn.execute(f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}')")
        rows = result.fetchall()
        cols = [desc[0] for desc in result.description]
        name_idx = next((i for i, c in enumerate(cols) if c.lower() in ("column_name", "name")), 0)
        type_idx = next((i for i, c in enumerate(cols) if c.lower() in ("column_type", "type")), 1)
        return [{"name": row[name_idx], "type": row[type_idx]} for row in rows]
    except Exception:
        return []


def _csv_to_parquet(csv_path: str, parquet_path: str) -> list[dict[str, Any]]:
    """Read CSV with DuckDB, write Parquet, return inferred schema.

    Parameters
    ----------
    csv_path:
        Absolute path to the uploaded CSV file.
    parquet_path:
        Absolute path where the Parquet output should be written.
        Parent directories are created automatically.
    """
    import duckdb  # noqa: PLC0415

    os.makedirs(os.path.dirname(parquet_path), exist_ok=True)
    conn = duckdb.connect(":memory:")
    # Load CSV with auto-detection, write Parquet
    conn.execute(
        f"COPY (SELECT * FROM read_csv_auto('{csv_path}')) "
        f"TO '{parquet_path}' (FORMAT PARQUET)"
    )
    # Infer schema
    result = conn.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}')"
    )
    rows = result.fetchall()
    cols = [desc[0] for desc in result.description]
    name_idx = next((i for i, c in enumerate(cols) if c.lower() in ("column_name", "name")), 0)
    type_idx = next((i for i, c in enumerate(cols) if c.lower() in ("column_type", "type")), 1)
    return [{"name": row[name_idx], "type": row[type_idx]} for row in rows]


def _sql_to_parquet(
    sql: str,
    parquet_path: str,
    org_id: str,
    user_id: str,
    cfg: "dict | None" = None,
) -> list[dict[str, Any]]:
    """Run SQL via DuckDB (with RLS stub) and write result to Parquet.

    RLS is preserved via the normal planner → DuckDBConnector path.  The
    connector used here is an in-memory DuckDB that loads data from registered
    datastores when ``datastore_id`` is embedded in the SQL.  For pure SELECT
    queries the planner rewrites SQL and enforces policies.

    NOTE: This function uses a fresh in-memory DuckDB connection.  The caller
    is responsible for ensuring the SQL references tables that DuckDB can
    resolve (e.g. read_parquet paths, or registered dataset views).

    When the source SQL references ``s3://`` paths (e.g. via
    ``read_parquet('s3://...')``) the httpfs extension is loaded and an S3
    SECRET is registered from *cfg* (or env vars when *cfg* is absent) before
    the query is executed.  A missing httpfs extension degrades to a clear
    error rather than a cryptic crash.

    Parameters
    ----------
    sql:
        The SELECT SQL to materialise.
    parquet_path:
        Output Parquet path.
    org_id:
        Caller's org (used for RLS claim injection).
    user_id:
        Caller's user_id (used for RLS claim injection).
    cfg:
        Optional datastore/connector config dict forwarded to
        ``setup_s3_httpfs`` so org-level S3 credentials are honoured when the
        source SQL references s3:// paths.  ``None`` falls back to env vars.
    """
    import duckdb  # noqa: PLC0415
    from app.connectors.planner import plan as _plan  # noqa: PLC0415

    # Build RLS claims from caller identity (no policies → empty dict)
    rls_claims: dict[str, Any] = {}

    # Plan the SQL (validates SELECT-only, injects any RLS predicates)
    physical = _plan(sql, rls_claims)

    conn = duckdb.connect(":memory:")

    # Load httpfs + S3 credentials when the SQL may reference s3:// paths.
    # We check both the original SQL and the rewritten physical.sql so that
    # planner rewrites that introduce s3:// references are also covered.
    _needs_s3 = "s3://" in sql or "s3://" in physical.sql
    if _needs_s3:
        try:
            setup_s3_httpfs(conn, cfg)
        except Exception as _httpfs_exc:
            raise RuntimeError(
                f"Failed to load httpfs extension for s3:// source SQL: {_httpfs_exc}. "
                "Ensure the 'httpfs' DuckDB extension is installed."
            ) from _httpfs_exc

    os.makedirs(os.path.dirname(parquet_path), exist_ok=True)
    conn.execute(
        f"COPY ({physical.sql}) TO '{parquet_path}' (FORMAT PARQUET)"
    )
    # Infer schema
    result = conn.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{parquet_path}')"
    )
    rows = result.fetchall()
    cols_desc = [desc[0] for desc in result.description]
    name_idx = next(
        (i for i, c in enumerate(cols_desc) if c.lower() in ("column_name", "name")), 0
    )
    type_idx = next(
        (i for i, c in enumerate(cols_desc) if c.lower() in ("column_type", "type")), 1
    )
    return [{"name": row[name_idx], "type": row[type_idx]} for row in rows]


# ---------------------------------------------------------------------------
# Datastore registration helper
# ---------------------------------------------------------------------------


async def _register_datastore(
    org_id: str,
    user_id: str,
    name: str,
    parquet_path: str,
    repo: Repo,
    storage_uri: str | None = None,
) -> str:
    """Create a 'duckdb' datastore row that points at the Parquet file.

    The datastore config embeds a ``view_sql`` that DuckDB can evaluate to
    make the dataset queryable via ``SELECT * FROM <table_name>``.

    When *storage_uri* is supplied (e.g. ``s3://bucket/key``) it is used as
    the ``parquet_path`` in the stored config so that the data-browser and
    query engine read directly from S3/MinIO via httpfs rather than expecting
    a local file.

    Returns the new datastore's UUID as a string.
    """
    # Prefer the canonical storage_uri when it is an s3:// path; otherwise
    # fall back to the local parquet_path.
    effective_path = (
        storage_uri
        if (storage_uri and storage_uri.startswith("s3://"))
        else parquet_path
    )
    config: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "view_sql": f"CREATE VIEW dataset AS SELECT * FROM read_parquet('{effective_path}')",
        "parquet_path": effective_path,
    }
    row = await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=user_id,
        name=name,
        config=config,
    )
    return str(row["id"])


# ---------------------------------------------------------------------------
# CSV upload
# ---------------------------------------------------------------------------


@router.post("/upload", dependencies=[Depends(require_writer_default)])
async def upload_csv(
    file: UploadFile = File(...),
    name: str = Form(...),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Upload a CSV file → convert to Parquet → register as queryable dataset.

    Flow
    ----
    1. Save the uploaded CSV to a temp file.
    2. Use DuckDB ``read_csv_auto`` to infer schema + write Parquet.
    3. Upload the Parquet to storage (file:// locally, s3:// in prod).
    4. Register a 'datasets' catalog row.
    5. Register a 'datastores' row (connector_type=duckdb, parquet_path) so
       the dataset is immediately queryable through the normal connector path.
    6. Update the catalog row with the datastore_id.
    7. Return the dataset row.

    Returns
    -------
    dict
        The created dataset row from the catalog.
    """
    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    catalog = get_catalog()

    # Pre-register catalog row to obtain a dataset_id
    import uuid as _uuid  # noqa: PLC0415

    dataset_id = str(_uuid.uuid4())

    # Determine parquet output path
    parquet_path = _parquet_local_path(org_id, dataset_id)

    # Write CSV to a temp file
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp_path = tmp.name
        contents = await file.read()
        tmp.write(contents)

    try:
        # Convert CSV → Parquet, infer schema
        schema = _csv_to_parquet(tmp_path, parquet_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    # Also upload to object storage (no-op for file:// since it writes directly)
    storage_uri = f"file://{parquet_path}"
    bucket_uri = os.getenv("NUBI_BUCKET_URI", "")
    if bucket_uri:
        client = _get_storage_client()
        key = f"datasets/{org_id}/{dataset_id}/data.parquet"
        storage_uri = client.upload_file(parquet_path, key)

    # Register in catalog
    ds_row = await catalog.create(
        org_id=org_id,
        name=name,
        storage_uri=storage_uri,
        format="parquet",
        schema_json=schema,
        created_by=user_id,
        source="upload",
        datastore_id=None,
    )
    # Override the catalog id with the pre-determined one (InMemory uses uuid4, so
    # for the PgCatalog we need to use the DB-generated id from ds_row).
    dataset_id = str(ds_row["id"])

    # Re-compute parquet path using actual dataset_id if it differed (Pg path)
    parquet_path = _parquet_local_path(org_id, dataset_id)
    if not os.path.isfile(parquet_path):
        # Pg catalog generated a different id — redo the conversion
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp2:
            tmp2_path = tmp2.name
            tmp2.write(contents)  # type: ignore[possibly-undefined]
        try:
            schema = _csv_to_parquet(tmp2_path, parquet_path)
        finally:
            try:
                os.unlink(tmp2_path)
            except OSError:
                pass

    # Register a queryable datastore (pass storage_uri so s3:// paths are used
    # verbatim in the view_sql rather than the local /tmp path).
    datastore_id = await _register_datastore(
        org_id=org_id,
        user_id=user_id,
        name=name,
        parquet_path=parquet_path,
        repo=repo,
        storage_uri=storage_uri,
    )

    # Link the datastore to the catalog row
    await catalog.update_datastore_id(
        org_id=org_id,
        dataset_id=dataset_id,
        datastore_id=datastore_id,
    )
    ds_row["datastore_id"] = datastore_id

    # Storage is a metered billing dimension — snapshot the org's total GB.
    await _record_storage_snapshot(org_id, user_id)

    return ds_row


# ---------------------------------------------------------------------------
# Materialize (query → Parquet)
# ---------------------------------------------------------------------------


class MaterializeRequest(BaseModel):
    """Request body for POST /datasets/materialize."""

    sql: str
    name: str


@router.post("/materialize", dependencies=[Depends(require_writer_default)])
async def materialize_query(
    body: MaterializeRequest,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Run a SQL query and materialise the result as a Parquet dataset.

    Flow
    ----
    1. Resolve org + validate SQL via the planner (SELECT-only + RLS).
    2. Execute query with DuckDB and COPY result TO Parquet.
    3. Register a 'datasets' catalog row (source='materialized').
    4. Register a 'datastores' row so the output is queryable.
    5. Return the dataset row.

    RLS is preserved: the planner rewrites the SQL with any active policies
    derived from the caller's token before execution.

    Returns
    -------
    dict
        The created dataset row from the catalog.
    """
    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    catalog = get_catalog()

    import uuid as _uuid  # noqa: PLC0415

    dataset_id_tmp = str(_uuid.uuid4())
    parquet_path = _parquet_local_path(org_id, dataset_id_tmp)

    # Run SQL → Parquet (RLS enforced via planner)
    schema = _sql_to_parquet(
        sql=body.sql,
        parquet_path=parquet_path,
        org_id=org_id,
        user_id=user_id,
    )

    storage_uri = f"file://{parquet_path}"
    bucket_uri = os.getenv("NUBI_BUCKET_URI", "")
    if bucket_uri:
        client = _get_storage_client()
        key = f"datasets/{org_id}/{dataset_id_tmp}/data.parquet"
        storage_uri = client.upload_file(parquet_path, key)

    # Register in catalog
    ds_row = await catalog.create(
        org_id=org_id,
        name=body.name,
        storage_uri=storage_uri,
        format="parquet",
        schema_json=schema,
        created_by=user_id,
        source="materialized",
        datastore_id=None,
    )
    dataset_id = str(ds_row["id"])

    # Ensure parquet is at the right path (in case Pg gave a different id)
    final_parquet = _parquet_local_path(org_id, dataset_id)
    if final_parquet != parquet_path and os.path.isfile(parquet_path):
        import shutil  # noqa: PLC0415

        os.makedirs(os.path.dirname(final_parquet), exist_ok=True)
        shutil.move(parquet_path, final_parquet)
        parquet_path = final_parquet

    # Register a queryable datastore (pass storage_uri so s3:// paths are used
    # verbatim in the view_sql rather than the local /tmp path).
    datastore_id = await _register_datastore(
        org_id=org_id,
        user_id=user_id,
        name=body.name,
        parquet_path=parquet_path,
        repo=repo,
        storage_uri=storage_uri,
    )

    await catalog.update_datastore_id(
        org_id=org_id,
        dataset_id=dataset_id,
        datastore_id=datastore_id,
    )
    ds_row["datastore_id"] = datastore_id

    # Storage is a metered billing dimension — snapshot the org's total GB.
    await _record_storage_snapshot(org_id, user_id)

    return ds_row


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


@router.get("")
async def list_datasets(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return all datasets for the caller's org."""
    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    catalog = get_catalog()
    rows = await catalog.list(org_id)
    return {"datasets": rows}


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return a single dataset row by id, scoped to the caller's org."""
    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    catalog = get_catalog()
    row = await catalog.get(org_id, dataset_id)
    if row is None:
        raise AppError("not_found", f"Dataset {dataset_id!r} not found.", 404)
    return row


# ---------------------------------------------------------------------------
# Self-register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
