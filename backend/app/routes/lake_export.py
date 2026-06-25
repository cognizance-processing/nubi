"""Bulk lake-export endpoints — data-custody tier capability 5.

Provides a **first-class bulk export** of a managed lake's contents to a
deployer-controlled destination bucket (anti-lock-in / "no roach motel").
The SOURCE is always the server-pinned managed-lake prefix for the given
datastore; the DESTINATION is deployer-supplied by design (that's the point —
exporting to YOUR bucket).

Endpoints
---------
POST /lake/{datastore_id}/export
    Bulk-export one or more tables from a managed lake to a deployer-controlled
    destination URI.  Body fields:

    - ``dest_uri``   (REQUIRED): where to write, e.g. ``s3://my-bucket/out/``
                     or ``file:///local/dir/``.
    - ``dest_creds`` (optional): credentials for the destination bucket.  When
                     absent the lake's own resolved credentials are reused.
    - ``table``      (optional): single table name within the lake.
    - ``sql``        (optional): an explicit SELECT to export.  Must be a
                     bare SELECT — any other statement is rejected with
                     ``invalid_export_sql`` (400) to guard against SQL injection.
    - ``format``     (optional, default ``parquet``): ``parquet`` or ``csv``.

GET /lake/{datastore_id}/tables
    List the table names available in the managed lake, derived from the
    ``.parquet`` keys under the lake's storage prefix.

Security
--------
* ``assert_custody_enabled()`` is called first on every route — fail-closed.
* The datastore is resolved **org-scoped** (a cross-org / non-managed / missing
  id returns 404; no information is leaked).
* The SOURCE path is server-pinned (``lake_prefix(org_id, datastore_id)``),
  derived purely from trusted ids — never user input.
* The DEST URI is validated to be a well-formed storage URI (``://`` present).
  Credentials in ``dest_creds`` are never logged or echoed.
* ``sql`` is parsed defensively: only bare SELECT statements are permitted.
  COPY / ATTACH / PRAGMA / multi-statement inputs are rejected.

TODO (deferred — not built here)
---------------------------------
- Async / job-backed export for very large lakes.  The current synchronous path
  is appropriate for reasonable sizes (tens of GB under DuckDB's local COPY TO
  throughput).  For larger exports, callers should chunk by table and run
  multiple requests or request a job-system integration (future capability).

Router wiring
-------------
``api_router`` is mounted under ``/api/v1``; the final paths are::

    POST /api/v1/lake/{datastore_id}/export
    GET  /api/v1/lake/{datastore_id}/tables
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import current_user
from app.errors import AppError
from app.lakehouse.custody import assert_custody_enabled
from app.lakehouse.managed import (
    MANAGED_MARKER,
    lake_prefix,
    resolve_central_storage,
)
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

logger = logging.getLogger("nubi.lake_export")

router = APIRouter(prefix="/lake", tags=["lake_export"])

# ---------------------------------------------------------------------------
# SQL-injection guard
# ---------------------------------------------------------------------------

# Tokens that indicate a non-SELECT statement.  We parse defensively: strip
# leading whitespace + SQL comments, then check the first keyword.  This is
# NOT a full parser — it is a defence-in-depth guard on top of DuckDB itself
# (DuckDB will also refuse a COPY/ATTACH inside ``COPY (...) TO`` — we just
# want a clear 400 before any DuckDB call).
_NON_SELECT_PATTERNS = re.compile(
    r"""
    ^[\s;]*                   # skip leading whitespace / semicolons
    (                         # first keyword group (any of these → reject)
        COPY
      | ATTACH
      | DETACH
      | CREATE
      | DROP
      | INSERT
      | UPDATE
      | DELETE
      | REPLACE
      | PRAGMA
      | CALL
      | EXECUTE
      | SET\s+
      | EXPORT
      | IMPORT
      | LOAD
      | INSTALL
    )
    \b                        # word boundary — "SELECT" must not match "SET"
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Also reject a trailing semicolon followed by another statement
# (multi-statement injection).
_MULTI_STATEMENT_RE = re.compile(r";\s*\S")


def _validate_export_sql(sql: str) -> None:
    """Raise ``AppError("invalid_export_sql", …, 400)`` for unsafe SQL.

    Allowed: a single, bare SELECT statement (including WITH … SELECT, i.e. CTEs).
    Rejected: COPY, ATTACH, CREATE, DROP, INSERT, UPDATE, DELETE, REPLACE,
    PRAGMA, CALL, EXECUTE, SET, multi-statement inputs, or anything that is
    not a SELECT after stripping comments.

    Parameters
    ----------
    sql:
        Raw SQL string supplied by the caller.
    """
    stripped = sql.strip()
    if not stripped:
        raise AppError("invalid_export_sql", "Export SQL must not be empty.", 400)

    # Strip single-line (--) and block (/* */) comments before keyword check.
    cleaned = re.sub(r"--[^\n]*", "", stripped)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL).strip()

    if _NON_SELECT_PATTERNS.match(cleaned):
        raise AppError(
            "invalid_export_sql",
            "Export SQL must be a single SELECT statement.  "
            "COPY, ATTACH, CREATE, DROP, INSERT, UPDATE, DELETE, "
            "PRAGMA, and other DML/DDL statements are not permitted.",
            400,
        )

    if _MULTI_STATEMENT_RE.search(cleaned):
        raise AppError(
            "invalid_export_sql",
            "Export SQL must be a single SELECT statement.  "
            "Multi-statement inputs are not permitted.",
            400,
        )

    # Must actually start with SELECT or WITH (CTEs).
    first_kw = cleaned.split()[0].upper() if cleaned.split() else ""
    if first_kw not in ("SELECT", "WITH"):
        raise AppError(
            "invalid_export_sql",
            f"Export SQL must start with SELECT or WITH (got {first_kw!r}).  "
            "Only bare SELECT statements are accepted.",
            400,
        )


# ---------------------------------------------------------------------------
# Table-name guard (Bug 1 — SQL injection via table parameter)
# ---------------------------------------------------------------------------

# Only alphanumerics, underscores, hyphens, and dots are allowed.  This
# rejects quotes, semicolons, slashes, spaces, and any other character that
# could break out of a DuckDB string literal or path concatenation.
_SAFE_TABLE_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")


def _validate_table_name(table: str) -> None:
    """Raise ``AppError("invalid_table_name", …, 400)`` for unsafe table names.

    A table name is safe only when it matches the strict safe-segment regex
    (``[A-Za-z0-9_-.]`` only) AND is non-empty.  Anything else — quotes,
    semicolons, slashes, spaces, ``read_csv`` payloads, ``..`` traversal
    sequences — is rejected before any interpolation occurs.

    Parameters
    ----------
    table:
        The caller-supplied table name from the request body.
    """
    if not table:
        raise AppError("invalid_table_name", "Table name must not be empty.", 400)
    if not _SAFE_TABLE_RE.match(table):
        raise AppError(
            "invalid_table_name",
            f"Table name contains invalid characters: {table!r}.  "
            "Only alphanumerics, underscores, hyphens, and dots are permitted.",
            400,
        )


# ---------------------------------------------------------------------------
# URI validation
# ---------------------------------------------------------------------------


def _validate_dest_uri(dest_uri: str) -> None:
    """Raise ``AppError("invalid_dest_uri", …, 400)`` for malformed URIs.

    We require a ``://`` scheme separator at minimum; the destination scheme
    can be anything the DuckDB/storage layer supports (s3, gs, az, file).
    """
    if not dest_uri or "://" not in dest_uri:
        raise AppError(
            "invalid_dest_uri",
            f"dest_uri must be a well-formed storage URI containing '://' "
            f"(e.g. 's3://bucket/prefix/' or 'file:///local/dir/').  "
            f"Got: {dest_uri!r}",
            400,
        )


def _validate_dest_not_in_source(dest_uri: str, source_base_uri: str) -> None:
    """Raise ``AppError("invalid_dest_uri", …, 400)`` when dest is inside the source lake.

    Normalises both URIs to their slash-terminated forms and rejects any
    ``dest_uri`` that resolves to or under the managed-lake source prefix.
    This prevents an export from overwriting or shadowing source data.

    Parameters
    ----------
    dest_uri:
        The deployer-supplied destination URI.
    source_base_uri:
        The full source lake URI (e.g. ``file:///managed/orgs/x/lake/y/``),
        derived server-side from ``resolve_central_storage()`` + ``lake_prefix``.
    """
    # Normalise: ensure trailing slash so ``startswith`` is prefix-safe
    # (avoids ``file:///lake/a`` matching ``file:///lake/ab/``).
    dest_norm = dest_uri.rstrip("/") + "/"
    src_norm = source_base_uri.rstrip("/") + "/"
    if dest_norm.startswith(src_norm) or src_norm.startswith(dest_norm):
        raise AppError(
            "invalid_dest_uri",
            "Destination must be outside the source lake.",
            400,
        )


# ---------------------------------------------------------------------------
# Managed-lake datastore resolution (org-scoped)
# ---------------------------------------------------------------------------


async def _resolve_managed_lake(
    datastore_id: str, org_id: str, repo: Repo
) -> dict[str, Any]:
    """Return the managed-lake datastore row or raise 404.

    Fails closed: a cross-org id, a non-existent id, or a row that is NOT a
    managed lake all surface as ``lake_not_found`` (404) to avoid leaking
    information about other orgs' datastores.

    Parameters
    ----------
    datastore_id:
        The caller-supplied datastore id.
    org_id:
        The VERIFIED org id for this request (from ``resolve_org_id``).
    repo:
        The active Repo implementation.

    Returns
    -------
    dict
        The managed-lake datastore row.

    Raises
    ------
    AppError("lake_not_found", 404)
        For any lookup miss, cross-org id, or non-managed-lake row.
    """
    row = await repo.get("datastores", org_id, datastore_id)
    cfg = row.get("config") if row else None
    if not isinstance(cfg, dict) or cfg.get(MANAGED_MARKER) is not True:
        # Both "not found" and "found but not a managed lake" map to 404.
        raise AppError(
            "lake_not_found",
            f"Managed lake {datastore_id!r} not found in this organisation.",
            404,
        )
    return row  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Storage-client builder for the SOURCE lake (for table listing)
# ---------------------------------------------------------------------------


def _lake_storage_client(org_id: str, datastore_id: str):
    """Return a ``StorageClient`` over the managed lake's storage root.

    Uses the same ``resolve_central_storage()`` / ``LocalStorageClient`` path
    as :class:`~app.lakehouse.managed.PrefixIsolatedProvider`.  Falls back to
    ``get_storage_client`` for cloud schemes.

    Returns
    -------
    StorageClient
        A storage client rooted at the managed lake's central storage bucket.

    Raises
    ------
    AppError("lake_storage_unavailable", 503)
        When central storage is not configured (degrade path).
    """
    central = resolve_central_storage()
    if central is None:
        raise AppError(
            "lake_storage_unavailable",
            "Central storage is not configured on this deployment.  "
            "Set NUBI_MANAGED_LAKE_DIR (local) or NUBI_BUCKET_URI + S3 credentials.",
            503,
        )
    if central.scheme == "file":
        from app.storage.local import LocalStorageClient  # noqa: PLC0415

        return LocalStorageClient(root=central.bucket)

    from app.storage.base import get_storage_client  # noqa: PLC0415

    return get_storage_client(central.base_uri(), central.creds or None)


# ---------------------------------------------------------------------------
# DuckDB connector builder for the SOURCE lake (for export)
# ---------------------------------------------------------------------------


async def _build_lake_connector(
    row: dict[str, Any],
    org_id: str,
    datastore_id: str,
):
    """Build a ``DuckDBStorageConnector`` over the managed lake.

    Replicates the secret-injection pattern from ``query.py``
    (``_build_connector_for_plan``) so the lake connector carries the same
    credentials as any BYO DuckDB connector.  Central credentials live in the
    secret store (encrypted at rest); we merge them into ``cfg`` before
    constructing the connector.

    Managed lakes on the local-file backend store Parquet files in a DIRECTORY
    (not a ``.duckdb`` database file).  For those we build an **in-memory**
    DuckDB connection that uses ``read_parquet()`` glob calls at export time —
    exactly what ``_export_table_parquet`` and ``write_result`` do.  For S3
    managed lakes the database URI is an S3 prefix; ``from_config`` detects the
    scheme and correctly sets up httpfs + the S3 secret.

    Parameters
    ----------
    row:
        The managed-lake datastore row from the repo.
    org_id:
        Verified org id (used for secret resolution).
    datastore_id:
        The managed-lake datastore id (used for secret resolution).

    Returns
    -------
    DuckDBStorageConnector
        A fully-configured connector ready to execute COPY TO queries.
    """
    from app.connectors.duckdb_storage import (  # noqa: PLC0415
        DuckDBStorageConnector,
        _detect_scheme,
        _get_creds,
    )

    cfg: dict = dict(row.get("config") or {})

    # Inject central credentials from the secret store (same path as query.py).
    secret: dict | None = None
    try:
        from app.connectors.secret_resolver import get_resolver as _get_resolver  # noqa: PLC0415

        _resolver = _get_resolver(cfg, org_id=org_id)
        # A zero-claims dict is safe here: we are not running an RLS-gated SELECT,
        # just fetching credentials for COPY TO which the planner never touches.
        secret = await _resolver.resolve(datastore_id, {})
        if not secret:
            secret = None
    except ImportError:
        try:
            from app.connectors.secret_store import get_secret_store as _get_ss  # noqa: PLC0415

            secret = await _get_ss().get(datastore_id, org_id)
        except ImportError:
            secret = None

    if secret:
        # Merge secret fields that are not already present in cfg.
        for k, v in secret.items():
            if k not in cfg:
                cfg[k] = v

    db_path: str = cfg.get("database") or cfg.get("path") or ":memory:"
    scheme = _detect_scheme(db_path)

    if scheme in ("s3", "s3a", "gs", "az"):
        # Cloud managed lake — from_config handles httpfs + secret registration.
        return DuckDBStorageConnector.from_config(cfg)

    # Local-file managed lake: the "database" value is a DIRECTORY URI
    # (e.g. ``file:///…/orgs/<org>/lake/<id>/``), not a DuckDB file.
    # DuckDB cannot open a directory as a database; instead we build an
    # in-memory connection.  The export helpers use ``read_parquet()`` glob
    # calls to read source data, so no ATTACH is needed.
    connector = DuckDBStorageConnector.for_memory()
    connector._config = dict(cfg)
    return connector


# ---------------------------------------------------------------------------
# Table discovery helper
# ---------------------------------------------------------------------------


def _discover_tables(org_id: str, datastore_id: str) -> list[str]:
    """Discover table names under the managed lake's prefix.

    Lists ``.parquet`` objects under ``lake_prefix(org_id, datastore_id)``
    and infers table names from the file paths.  Deduplicated and sorted.

    Convention: ``<prefix>/<table>.parquet`` → table ``<table>``.
    Also handles nested layouts: ``<prefix><table_dir>/<file>.parquet`` →
    table name is the first sub-segment after the prefix.

    Parameters
    ----------
    org_id:
        Verified org id.
    datastore_id:
        The managed-lake datastore id.

    Returns
    -------
    list[str]
        Sorted list of unique table names available in the lake.
    """
    client = _lake_storage_client(org_id, datastore_id)
    prefix = lake_prefix(org_id, datastore_id)
    try:
        keys = client.list(prefix)
    except Exception:  # noqa: BLE001
        logger.debug("lake_export: listing failed for %s/%s", org_id, datastore_id, exc_info=True)
        return []

    tables: set[str] = set()
    for key in keys:
        if not key.endswith(".parquet"):
            continue
        # Strip the lake prefix from the key to get the relative path.
        relative = key[len(prefix):].lstrip("/")
        if not relative:
            continue
        # First path segment is the table name (strip .parquet if flat layout).
        segment = relative.split("/")[0]
        table = segment.removesuffix(".parquet") if segment.endswith(".parquet") else segment
        if table:
            tables.add(table)

    return sorted(tables)


# ---------------------------------------------------------------------------
# Export helper — runs COPY TO for one table
# ---------------------------------------------------------------------------


def _export_table_parquet(
    connector,
    prefix: str,
    table: str,
    dest_base: str,
    format_: str,
) -> dict[str, Any]:
    """Export one table from the lake to ``dest_base/<table>.<ext>``.

    Builds a ``SELECT * FROM read_parquet('<prefix><table>/**/*.parquet')``
    and delegates to ``DuckDBStorageConnector.write_result()``.

    Parameters
    ----------
    connector:
        A ``DuckDBStorageConnector`` configured over the lake.
    prefix:
        The lake prefix (e.g. ``orgs/<org>/lake/<ds_id>/``), relative key form.
    table:
        Table name (maps to ``<prefix><table>/**/*.parquet``).
    dest_base:
        Destination base URI (e.g. ``s3://bucket/out/`` or ``file:///dir/``).
    format_:
        ``"parquet"`` or ``"csv"``.

    Returns
    -------
    dict
        ``{table, dest_uri, format}``
    """
    ext = "parquet" if format_ == "parquet" else "csv"

    # Resolve the source URI — the connector's config carries the full database
    # URI (e.g. ``file:///managed-lake-dir/orgs/<org>/lake/<ds>/``); we
    # reconstruct the parquet glob from the central storage URI + the prefix.
    central = resolve_central_storage()
    if central is None:
        raise AppError("lake_storage_unavailable", "Central storage not configured.", 503)

    base_uri = central.base_uri()
    # Normalise: ensure prefix has no leading slash for URI concatenation.
    src_uri = f"{base_uri}/{prefix.lstrip('/')}{table}/**/*.parquet"

    dest_uri = dest_base.rstrip("/") + f"/{table}.{ext}"

    # Build the SELECT; DuckDB's read_parquet accepts a glob pattern.
    sql = f"SELECT * FROM read_parquet('{src_uri}', union_by_name=true)"

    if format_ == "csv":
        # write_result always writes Parquet via COPY; for CSV we use a
        # separate COPY statement with FORMAT csv.
        effective_dest = dest_uri
        if effective_dest.startswith("file://"):
            effective_dest = effective_dest[len("file://"):]
        import os as _os  # noqa: PLC0415

        parent = _os.path.dirname(effective_dest)
        if parent:
            _os.makedirs(parent, exist_ok=True)

        copy_sql = f"COPY ({sql}) TO '{effective_dest}' (FORMAT csv, HEADER true)"
        try:
            connector._inner._conn.execute(copy_sql)
        except Exception as exc:
            raise AppError(
                "write_result_error",
                f"DuckDB COPY TO '{dest_uri}' (csv) failed: {exc}",
                500,
            ) from exc
        return {"table": table, "dest_uri": dest_uri, "format": "csv"}
    else:
        connector.write_result(sql, dest_uri)
        return {"table": table, "dest_uri": dest_uri, "format": "parquet"}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Request body for ``POST /lake/{datastore_id}/export``."""

    dest_uri: str
    """Destination URI the deployer controls (e.g. ``s3://my-bucket/exports/``)."""

    dest_creds: dict[str, str] | None = None
    """Optional credentials for the destination bucket.

    When absent, the lake's own resolved credentials are reused (only works
    when source and destination share the same S3 environment).  Never
    logged or echoed.
    """

    table: str | None = None
    """Single table name to export.  Mutually exclusive with ``sql``."""

    sql: str | None = None
    """A single SELECT to export.  Mutually exclusive with ``table``.

    Must be a bare SELECT statement — any other statement type is rejected
    (``invalid_export_sql``, 400) to prevent SQL injection via the COPY path.
    """

    format: str = "parquet"
    """Output format: ``parquet`` (default) or ``csv``."""


# ---------------------------------------------------------------------------
# POST /lake/{datastore_id}/export
# ---------------------------------------------------------------------------


@router.post("/{datastore_id}/export", status_code=200)
async def export_lake(
    datastore_id: str,
    body: ExportRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Bulk-export a managed lake (or a named table/SELECT within it).

    Resolves the managed datastore org-scoped, builds a
    ``DuckDBStorageConnector`` over the lake (reusing the same secret-injection
    path as ``POST /query``), then runs ``write_result()`` (or a per-table
    loop) writing Parquet/CSV to ``dest_uri``.

    This is a **synchronous** export; for very large lakes (hundreds of GB)
    the caller should chunk by table and issue multiple requests, or wait for
    the async-job export capability (future — see module TODO).

    Parameters
    ----------
    datastore_id:
        The managed-lake datastore to export from (org-scoped; 404 if not
        a managed lake in the caller's org).
    body:
        Export parameters (see :class:`ExportRequest`).

    Returns
    -------
    dict
        ``{exported: [{table, dest_uri, format}], dest_uri, format, tables_exported}``

    Raises
    ------
    AppError("custody_disabled", 403)
        When the data-custody tier is not enabled on this deployment.
    AppError("lake_not_found", 404)
        When *datastore_id* does not exist in the caller's org or is not a
        managed lake.
    AppError("invalid_dest_uri", 400)
        When ``dest_uri`` is not a well-formed storage URI.
    AppError("invalid_export_sql", 400)
        When ``sql`` is not a single SELECT statement.
    AppError("invalid_format", 400)
        When ``format`` is not ``parquet`` or ``csv``.
    AppError("export_conflict", 400)
        When both ``table`` and ``sql`` are supplied.
    """
    # ── Tier gate (fail-closed) ────────────────────────────────────────────
    assert_custody_enabled()

    # ── Org resolution ────────────────────────────────────────────────────
    org_id = await resolve_org_id(str(user["id"]), repo, request)

    # ── Validate format ───────────────────────────────────────────────────
    fmt = (body.format or "parquet").strip().lower()
    if fmt not in ("parquet", "csv"):
        raise AppError(
            "invalid_format",
            f"format must be 'parquet' or 'csv', got {fmt!r}.",
            400,
        )

    # ── Validate dest_uri ─────────────────────────────────────────────────
    _validate_dest_uri(body.dest_uri)

    # ── Reject conflicting table + sql ───────────────────────────────────
    if body.table and body.sql:
        raise AppError(
            "export_conflict",
            "Provide either 'table' or 'sql', not both.",
            400,
        )

    # ── Validate sql (if supplied) ────────────────────────────────────────
    if body.sql:
        _validate_export_sql(body.sql)

    # ── Validate table name (if supplied) — Bug 1 fix ────────────────────
    # Must happen BEFORE any interpolation of body.table into SQL/paths.
    if body.table:
        _validate_table_name(body.table)

    # ── Resolve managed lake (org-scoped; 404 for cross-org / non-managed) ─
    row = await _resolve_managed_lake(datastore_id, org_id, repo)
    prefix = lake_prefix(org_id, datastore_id)

    # ── Validate dest_uri is not inside the source lake — Bug 2 fix ──────
    central_for_check = resolve_central_storage()
    if central_for_check is not None:
        source_lake_uri = f"{central_for_check.base_uri()}/{prefix.lstrip('/')}"
        _validate_dest_not_in_source(body.dest_uri, source_lake_uri)

    # ── Build the lake connector (secret injection reused from query.py) ───
    connector = await _build_lake_connector(row, org_id, datastore_id)

    # ── Register destination creds on the connector when provided ─────────
    # When dest_creds are given AND the destination is s3://, we register an
    # additional DuckDB secret scoped to the dest URI so COPY TO uses the
    # deployer's credentials for the destination while the source continues
    # using the lake's credentials (different scope).  Never logged.
    if body.dest_creds and body.dest_uri.startswith("s3://"):
        try:
            from app.connectors.duckdb_storage import (  # noqa: PLC0415
                _get_creds,
                _install_httpfs,
                _register_s3_secret,
            )

            dest_cred_dict = _get_creds(dict(body.dest_creds))
            # Scope the secret to the destination bucket so it never overrides
            # the source-lake secret (per-scope secret isolation).
            dest_cred_dict["scope"] = body.dest_uri.rstrip("/")
            _install_httpfs(connector._inner._conn)
            _register_s3_secret(
                connector._inner._conn,
                dest_cred_dict,
                secret_name="nubi_s3_dest",
            )
            logger.debug(
                "lake_export: registered destination S3 secret (scope=%s)",
                body.dest_uri,
            )
        except Exception:  # noqa: BLE001
            # Best-effort: if registering the dest secret fails, continue —
            # the COPY may still succeed when dest creds match env vars.
            logger.warning(
                "lake_export: could not register dest_creds secret; "
                "continuing without it (may use env-var creds for dest).",
                exc_info=True,
            )

    exported: list[dict[str, Any]] = []

    # ── Export path A: explicit SELECT sql ────────────────────────────────
    if body.sql:
        # sql already validated as a SELECT above.
        dest_uri = body.dest_uri.rstrip("/") + f"/export.{fmt}"
        if fmt == "csv":
            effective_dest = dest_uri
            if effective_dest.startswith("file://"):
                effective_dest = effective_dest[len("file://"):]
            import os as _os  # noqa: PLC0415

            _os.makedirs(_os.path.dirname(effective_dest) or ".", exist_ok=True)
            copy_sql = (
                f"COPY ({body.sql}) TO '{effective_dest}' (FORMAT csv, HEADER true)"
            )
            try:
                connector._inner._conn.execute(copy_sql)
            except Exception as exc:
                raise AppError(
                    "write_result_error",
                    f"DuckDB COPY TO failed: {exc}",
                    500,
                ) from exc
        else:
            connector.write_result(body.sql, dest_uri)
        exported.append({"table": None, "dest_uri": dest_uri, "format": fmt})

    # ── Export path B: named table ────────────────────────────────────────
    elif body.table:
        # Membership check: reject table names not present in the lake.
        # This is a STRONGER guard on top of the regex check above —
        # an attacker supplying a syntactically-valid but non-existent table
        # name (e.g. to probe path traversal) gets a 404, not a silent
        # empty export.  We discover tables here so the check is fresh.
        available_tables = _discover_tables(org_id, datastore_id)
        if body.table not in available_tables:
            raise AppError(
                "table_not_found",
                f"Table {body.table!r} not found in this managed lake.  "
                f"Available tables: {available_tables}",
                404,
            )
        result = _export_table_parquet(
            connector, prefix, body.table, body.dest_uri, fmt
        )
        exported.append(result)

    # ── Export path C: all tables ─────────────────────────────────────────
    else:
        tables = _discover_tables(org_id, datastore_id)
        if not tables:
            logger.info(
                "lake_export: no tables found in lake %s/%s — nothing exported",
                org_id,
                datastore_id,
            )
        for t in tables:
            try:
                result = _export_table_parquet(
                    connector, prefix, t, body.dest_uri, fmt
                )
                exported.append(result)
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "write_result_error",
                    f"Export failed for table {t!r}: {exc}",
                    500,
                ) from exc

    logger.info(
        "lake_export: exported %d table(s) from %s/%s → %s",
        len(exported),
        org_id,
        datastore_id,
        body.dest_uri,
    )

    return {
        "exported": exported,
        "dest_uri": body.dest_uri,
        "format": fmt,
        "tables_exported": len(exported),
    }


# ---------------------------------------------------------------------------
# GET /lake/{datastore_id}/tables
# ---------------------------------------------------------------------------


@router.get("/{datastore_id}/tables", status_code=200)
async def list_lake_tables(
    datastore_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List the table names available in a managed lake.

    Discovers tables by listing ``*.parquet`` objects under the lake's storage
    prefix.  This is a fast listing operation — it does not read any data.

    Parameters
    ----------
    datastore_id:
        The managed-lake datastore to inspect (org-scoped).

    Returns
    -------
    dict
        ``{datastore_id, tables: [str], count: int}``

    Raises
    ------
    AppError("custody_disabled", 403)
        When the data-custody tier is not enabled on this deployment.
    AppError("lake_not_found", 404)
        When *datastore_id* does not exist in the caller's org or is not a
        managed lake.
    """
    # ── Tier gate (fail-closed) ────────────────────────────────────────────
    assert_custody_enabled()

    # ── Org resolution ────────────────────────────────────────────────────
    org_id = await resolve_org_id(str(user["id"]), repo, request)

    # ── Resolve managed lake (org-scoped) ──────────────────────────────────
    await _resolve_managed_lake(datastore_id, org_id, repo)

    # ── Discover tables via storage listing ────────────────────────────────
    tables = _discover_tables(org_id, datastore_id)

    return {
        "datastore_id": datastore_id,
        "tables": tables,
        "count": len(tables),
    }


# ---------------------------------------------------------------------------
# Register the router on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
