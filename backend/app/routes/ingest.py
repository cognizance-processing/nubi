"""Versioned WRITE/ingest API — external producer → managed lake (capability 2).

This router implements the session-based producer protocol that lets an
external caller land Parquet/Arrow parts into a Nubi-managed lakehouse with
atomic publish.  The design mirrors the staging → verify → promote
atomicity that the internal ``file_ingest`` handler already uses, but exposes
it over HTTP so any producer (ETL tool, CI pipeline, notebook) can write
without needing direct object-storage credentials.

API contract version
--------------------
Version is ``v1`` (the ``/api/v1`` prefix is applied by main.py mounting
``api_router``).  The ``lake`` sub-path is the capability namespace.  Breaking
changes will bump the prefix to ``/api/v2/lake/…``; additive fields are
non-breaking within ``v1``.

Final paths (all under ``/api/v1``)::

    POST   /lake/{datastore_id}/ingest/sessions
    PUT    /lake/{datastore_id}/ingest/sessions/{session_id}/parts/{relpath:path}
    POST   /lake/{datastore_id}/ingest/sessions/{session_id}/commit
    GET    /lake/{datastore_id}/ingest/sessions/{session_id}
    POST   /lake/{datastore_id}/ingest/sessions/{session_id}/abort

Session protocol
----------------
1. **Open** — ``POST …/sessions``:
   Declare the intent (mode, schema, partition, idempotency_key).  Server
   validates the datastore is a managed lake owned by the request's org and
   returns a ``{session_id, run_id, upload_prefix}``.  Same ``idempotency_key``
   returns the existing session unchanged (retry-safe).

2. **Upload** — ``PUT …/sessions/{id}/parts/{relpath}``:
   Stream one Parquet/Arrow part's bytes into the session's ``StagingArea``.
   Returns the ``ManifestEntry`` (size + sha256) so the producer can build its
   manifest client-side.

3. **Commit** — ``POST …/sessions/{id}/commit``:
   Producer submits the manifest ``{files:[{path,size,sha256}], row_counts}``.
   Server re-verifies every staged byte then atomically promotes into the lake:

   * ``full_replace``: promote all parts, then sweep old objects under the
     table prefix that are NOT in this publish.  Publish-then-sweep — the
     window between publish-done and sweep-done is visible on reads.  Object
     stores lack true multi-object atomicity; this is documented.
   * ``append``: promote parts under ``<table>/<partition>/`` without touching
     other partitions.  Re-committing the same session is idempotent.

   Returns ``{published, files, rows, mode, partition, final_uris}``.

4. **Status** — ``GET …/sessions/{id}``: return the session record.

5. **Abort** — ``POST …/sessions/{id}/abort``: cleanup staging, mark aborted.

Schema declaration + evolution
--------------------------------
The producer declares ``schema`` (``[{name, type}, …]``) at session-open.  On
``append``, the server loads the last committed schema for the table from the
lake sidecar (``_nubi/schema.json``) and rejects any attempt to NARROW it
(remove or incompatibly change a column type).  Adding new columns is allowed
(additive evolution).  Schema checks are structural (names + types as strings)
and pragmatic; type-level coercion semantics are up to the consumer.

Atomicity / idempotency notes
-----------------------------
* Promote is an **object-by-object** upload; object stores have no
  multi-object atomic commit.  The commit-then-sweep ordering in
  ``full_replace`` means readers see the NEW data before the old is swept.
* Concurrent commits on the SAME session are serialised by the CAS on the
  ``committing`` state; only one caller wins, the others get a 409.
* Concurrent commits on DIFFERENT sessions for the SAME table are NOT
  serialised here (the lake has no table lock).  Callers must coordinate
  externally or use ``append`` with non-overlapping partitions.
* The ``idempotency_key`` prevents duplicate opens (same key, same org,
  same datastore → same session, no double-publish).

Security
--------
* :func:`app.lakehouse.custody.assert_custody_enabled` is called first on
  every route — fail-closed 403 when the tier is off.
* ``org_id`` is resolved from the authenticated token / org-membership (never
  from the request body).  A ``datastore_id`` in another org is a 404.
* ``relpath`` is validated to strip ``..`` and absolute prefixes; the
  ``StagingArea`` further pins every write under the run prefix.
* Manifest verification runs server-side (size + sha256) before any promote.
  A tampered part aborts the commit.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.auth.deps import current_user
from app.errors import AppError
from app.lakehouse.custody import assert_custody_enabled
from app.lakehouse.ingest_session import (
    IngestSessionStore,
    get_ingest_session_store,
)
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

logger = logging.getLogger("nubi.routes.ingest")

# ---------------------------------------------------------------------------
# Router — mounted as /lake under /api/v1 by self-registration below
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/lake", tags=["ingest"])

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_VALID_MODES = {"full_replace", "append"}

# Partition component: safe single-level or multi-level key segment.
# Each slash-separated component must start with a word character (not a dot)
# so that '.' and '..' are structurally impossible.
# E.g. "dt=2026-06-25", "region=za", "year=2026/month=06".
_PARTITION_SEG_RE = re.compile(r"^\w[\w\-\.=]*$")


def _validate_partition(partition: str) -> None:
    """Validate a producer-supplied partition value.

    Splits on '/' and applies per-segment rules:
      - Each segment must match ``^\\w[\\w\\-\\.=]*$`` (starts with word char,
        not a dot, so '.' and '..' are impossible).
      - Explicitly rejects any segment equal to '.' or '..'.
      - Rejects any segment containing '..' as a substring.
    Raises ``AppError("invalid_partition", ..., 400)`` on violation.
    """
    segs = partition.split("/")
    for seg in segs:
        if not seg:
            raise AppError(
                "invalid_partition",
                "partition must not contain empty segments.",
                400,
            )
        if seg in (".", "..") or ".." in seg:
            raise AppError(
                "invalid_partition",
                f"partition segment {seg!r} is not allowed (path traversal).",
                400,
            )
        if not _PARTITION_SEG_RE.match(seg):
            raise AppError(
                "invalid_partition",
                f"partition segment {seg!r} contains disallowed characters. "
                "Each segment must start with a letter/digit/underscore.",
                400,
            )

# Part relative path: must be a non-empty sequence of safe path segments.
# No double-dots, no absolute start, no control chars.
_SAFE_SEG_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


def _validate_relpath(relpath: str) -> str:
    """Normalise and validate a producer-supplied relative part path.

    Strips leading slashes, rejects ``..`` segments and empty segments so the
    path can never escape the StagingArea prefix (the StagingArea also pins,
    but we validate early to return a clear 400).

    Returns the normalised relative path.
    """
    # relpath comes from the URL path parameter captured by FastAPI.
    rel = relpath.strip().lstrip("/")
    parts = rel.split("/")
    for part in parts:
        if not part or part in (".", ".."):
            raise AppError(
                "invalid_relpath",
                "Part path must not contain empty, '.', or '..' segments.",
                400,
            )
        if not _SAFE_SEG_RE.match(part):
            raise AppError(
                "invalid_relpath",
                f"Path segment {part!r} contains disallowed characters. "
                "Use only alphanumerics, hyphens, underscores, and dots.",
                400,
            )
    return "/".join(parts)


def _validate_schema(schema: Any) -> list[dict[str, str]]:
    """Validate the declared schema list.

    Expected shape: ``[{"name": str, "type": str}, …]``.  Returns the
    normalised list.  Raises ``AppError("invalid_schema", 400)`` on bad input.
    """
    if not isinstance(schema, list):
        raise AppError("invalid_schema", "schema must be a list of {name, type} objects.", 400)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, col in enumerate(schema):
        if not isinstance(col, dict):
            raise AppError("invalid_schema", f"schema[{i}] must be an object.", 400)
        name = str(col.get("name") or "").strip()
        ctype = str(col.get("type") or "").strip()
        if not name:
            raise AppError("invalid_schema", f"schema[{i}].name is required.", 400)
        if not ctype:
            raise AppError("invalid_schema", f"schema[{i}].type is required.", 400)
        if name in seen:
            raise AppError("invalid_schema", f"Duplicate column name {name!r} in schema.", 400)
        seen.add(name)
        out.append({"name": name, "type": ctype})
    return out


def _check_schema_compatible(
    existing: list[dict[str, str]], incoming: list[dict[str, str]], partition: str | None
) -> None:
    """Assert that *incoming* schema is backward-compatible with *existing*.

    Append-mode schema evolution rules:
      * Columns present in *existing* must also appear in *incoming* with the
        SAME type (narrowing/type-change is rejected).
      * New columns in *incoming* not in *existing* are allowed (additive).
      * Removing an existing column is rejected (narrowing).

    Raises ``AppError("schema_incompatible", 409)`` on incompatibility.
    """
    existing_map = {c["name"]: c["type"] for c in existing}
    incoming_map = {c["name"]: c["type"] for c in incoming}
    # Detect removed columns.
    removed = set(existing_map) - set(incoming_map)
    if removed:
        raise AppError(
            "schema_incompatible",
            f"Append would remove existing columns {sorted(removed)!r}. "
            "Schema narrowing is not allowed; only additive changes are permitted. "
            f"Partition: {partition!r}",
            409,
        )
    # Detect type changes.
    changed = [
        name
        for name in existing_map
        if name in incoming_map and incoming_map[name] != existing_map[name]
    ]
    if changed:
        details = {n: (existing_map[n], incoming_map[n]) for n in changed}
        raise AppError(
            "schema_incompatible",
            f"Append changes existing column types: {details!r}. "
            "Type changes are not allowed; only additive columns are permitted.",
            409,
        )


# ---------------------------------------------------------------------------
# Managed-lake resolution helpers
# ---------------------------------------------------------------------------


async def _require_managed_lake(
    org_id: str, datastore_id: str, repo: Repo
) -> dict[str, Any]:
    """Return the managed-lake datastore row or raise 404.

    Validates the datastore exists, is org-scoped, and carries the
    ``managed_lake: True`` marker.  A BYO connector, a missing id, or a
    cross-org id all return 404 (no information leak).
    """
    from app.lakehouse.managed import MANAGED_MARKER, get_provider  # noqa: PLC0415

    provider = get_provider(repo)
    if provider is None:
        raise AppError(
            "lake_not_configured",
            "No central storage is configured for this deployment.",
            404,
        )
    # Use the provider's managed-row lookup (handles both prefix and dedicated).
    row = await repo.get("datastores", org_id, str(datastore_id))
    if row is None:
        raise AppError("datastore_not_found", "Datastore not found.", 404)
    cfg = row.get("config")
    if not isinstance(cfg, dict) or cfg.get(MANAGED_MARKER) is not True:
        raise AppError(
            "datastore_not_found",
            "Datastore is not a managed lakehouse.",
            404,
        )
    return row


def _get_staging_area(org_id: str, run_id: str) -> "Any":
    """Return a StagingArea for the session's run, or raise 503."""
    from app.lakehouse.managed import get_staging_area  # noqa: PLC0415

    area = get_staging_area(org_id, run_id)
    if area is None:
        raise AppError(
            "staging_unavailable",
            "No staging store is configured. "
            "Set NUBI_STAGING_DIR or NUBI_MANAGED_LAKE_DIR.",
            503,
        )
    return area


# ---------------------------------------------------------------------------
# Schema sidecar helpers (lake-prefix JSON sidecar)
# ---------------------------------------------------------------------------
#
# Layout: <lake-prefix>/_nubi/schema.json
# Shape:  {<table_key>: [{name, type}, …], …}
#
# ``table_key`` is ``<object_name>`` (the logical table).


def _schema_storage(org_id: str, datastore_id: str) -> "tuple[Any, str] | None":
    """Return ``(client, lake_prefix)`` for the schema sidecar, or ``None``."""
    try:
        from app.lakehouse.managed import (  # noqa: PLC0415
            resolve_central_storage,
            lake_prefix,
        )

        central = resolve_central_storage()
        if central is None:
            return None
        if central.scheme == "file":
            from app.storage.local import LocalStorageClient  # noqa: PLC0415

            client = LocalStorageClient(root=central.bucket)
        else:
            from app.storage.base import get_storage_client  # noqa: PLC0415

            client = get_storage_client(central.base_uri(), central.creds or None)
        prefix = lake_prefix(org_id, datastore_id)
        return client, prefix
    except Exception:  # noqa: BLE001
        return None


def _load_table_schema(
    org_id: str, datastore_id: str, table_key: str
) -> list[dict[str, str]] | None:
    """Load the stored schema for *table_key*, or ``None`` if absent."""
    pair = _schema_storage(org_id, datastore_id)
    if pair is None:
        return None
    client, prefix = pair
    key = f"{prefix}_nubi/schema.json"
    try:
        raw = client.download_bytes(key)
        all_schemas: dict[str, Any] = json.loads(raw.decode())
        return all_schemas.get(table_key)
    except Exception:  # noqa: BLE001
        return None


def _save_table_schema(
    org_id: str, datastore_id: str, table_key: str, schema: list[dict[str, str]]
) -> None:
    """Best-effort: upsert the schema for *table_key* in the sidecar."""
    pair = _schema_storage(org_id, datastore_id)
    if pair is None:
        return
    client, prefix = pair
    key = f"{prefix}_nubi/schema.json"
    try:
        existing: dict[str, Any] = {}
        try:
            raw = client.download_bytes(key)
            existing = json.loads(raw.decode())
        except Exception:  # noqa: BLE001
            pass
        existing[table_key] = schema
        client.upload_bytes(json.dumps(existing).encode(), key)
    except Exception:  # noqa: BLE001
        logger.debug("ingest: schema sidecar write failed for table %s", table_key, exc_info=True)


# ---------------------------------------------------------------------------
# Promote helpers
# ---------------------------------------------------------------------------


def _build_promote_callable(
    org_id: str,
    datastore_id: str,
    staging: Any,
    table_key: str,
    partition: str | None,
) -> "tuple[Any, Any]":
    """Return ``(storage_client, final_key_fn)`` for promote.

    The final key for each staged part is:
      full_replace:  <lake-prefix><table_key>/<filename>
      append:        <lake-prefix><table_key>/<partition>/<filename>
    """
    from app.lakehouse.managed import (  # noqa: PLC0415
        resolve_central_storage,
        lake_prefix,
    )

    central = resolve_central_storage()
    if central is None:
        raise AppError(
            "lake_not_configured",
            "No central storage configured for promote.",
            503,
        )
    if central.scheme == "file":
        from app.storage.local import LocalStorageClient  # noqa: PLC0415

        client = LocalStorageClient(root=central.bucket)
    else:
        from app.storage.base import get_storage_client  # noqa: PLC0415

        client = get_storage_client(central.base_uri(), central.creds or None)

    prefix = lake_prefix(org_id, datastore_id)
    # Normalise table_key to a slash-separated path (dots → slashes).
    table_path = table_key.replace(".", "/").strip("/")

    import posixpath  # noqa: PLC0415

    def _final_key(staged_rel: str) -> str:
        leaf = staged_rel.rsplit("/", 1)[-1]
        if partition:
            candidate = f"{prefix}{table_path}/{partition}/{leaf}"
        else:
            candidate = f"{prefix}{table_path}/{leaf}"
        # Belt-and-suspenders: normalise and assert the key stays under the
        # server-pinned datastore prefix.  A crafted partition or table_key
        # that somehow survived earlier validation cannot escape the prefix.
        normalised = posixpath.normpath(candidate)
        if not normalised.startswith(prefix.rstrip("/")):
            raise AppError(
                "invalid_partition",
                "path_escape: resolved key escapes the datastore prefix.",
                400,
            )
        return candidate

    return client, _final_key


def _list_table_objects(org_id: str, datastore_id: str, table_key: str) -> list[str]:
    """List all object keys under the table prefix (for full_replace sweep)."""
    from app.lakehouse.managed import (  # noqa: PLC0415
        resolve_central_storage,
        lake_prefix,
    )

    central = resolve_central_storage()
    if central is None:
        return []
    prefix = lake_prefix(org_id, datastore_id)
    table_path = table_key.replace(".", "/").strip("/")
    table_prefix = f"{prefix}{table_path}/"

    if central.scheme == "file":
        from app.storage.local import LocalStorageClient  # noqa: PLC0415

        client = LocalStorageClient(root=central.bucket)
    else:
        from app.storage.base import get_storage_client  # noqa: PLC0415

        client = get_storage_client(central.base_uri(), central.creds or None)

    try:
        return list(client.list(table_prefix))
    except Exception:  # noqa: BLE001
        return []


def _delete_keys(org_id: str, datastore_id: str, keys: list[str]) -> None:
    """Best-effort delete of *keys* from the lake store."""
    from app.lakehouse.managed import (  # noqa: PLC0415
        resolve_central_storage,
        _delete_object,
    )

    central = resolve_central_storage()
    if central is None:
        return
    if central.scheme == "file":
        from app.storage.local import LocalStorageClient  # noqa: PLC0415

        client = LocalStorageClient(root=central.bucket)
    else:
        from app.storage.base import get_storage_client  # noqa: PLC0415

        client = get_storage_client(central.base_uri(), central.creds or None)

    for key in keys:
        try:
            _delete_object(client, key)
        except Exception:  # noqa: BLE001
            logger.debug("ingest: could not delete old object %s", key, exc_info=True)


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------


def _session_response(record: dict[str, Any]) -> dict[str, Any]:
    """Shape the session record for API responses (safe subset)."""
    return {
        "session_id": record["id"],
        "run_id": record["run_id"],
        "datastore_id": record["datastore_id"],
        "mode": record["mode"],
        "table_name": record.get("table_name", "default"),
        "partition": record.get("partition"),
        "schema": record.get("schema", []),
        "state": record["state"],
        "result": record.get("result"),
        "error": record.get("error"),
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
    }


# ---------------------------------------------------------------------------
# 1. POST /lake/{datastore_id}/ingest/sessions — open a session
# ---------------------------------------------------------------------------


@router.post("/{datastore_id}/ingest/sessions", status_code=201)
async def open_ingest_session(
    datastore_id: str,
    body: dict[str, Any],
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Open a new ingest session for a managed lakehouse datastore.

    **Request body**::

        {
          "mode":             "full_replace" | "append",
          "partition":        "dt=2026-06-25",          // append only, optional
          "schema":           [{"name": "col", "type": "string"}, ...],
          "idempotency_key":  "<client-uuid>"
        }

    **Response (201)**::

        {
          "session_id":     "<uuid>",
          "run_id":         "<uuid>",
          "upload_prefix":  "<staging URI prefix>",
          "state":          "open",
          ...
        }

    Idempotency
    -----------
    The same ``idempotency_key`` + ``datastore_id`` + org always returns the
    existing session without opening a duplicate (retry-safe).  A new key
    opens a fresh session even when prior sessions exist.

    Errors
    ------
    * 403 ``custody_disabled`` — tier is off.
    * 404 ``datastore_not_found`` — datastore missing, cross-org, or not managed.
    * 400 ``invalid_mode`` — mode not in ``{full_replace, append}``.
    * 400 ``missing_idempotency_key`` — required field absent.
    * 400 ``invalid_schema`` — schema malformed.
    * 400 ``missing_partition`` — ``mode=append`` requires ``partition``.
    """
    assert_custody_enabled()

    user_id = str(user["id"])
    org_id = await resolve_org_id(user_id, repo, request)

    # -- Validate the datastore is a managed lake for this org ----------------
    await _require_managed_lake(org_id, datastore_id, repo)

    # -- Validate body fields -------------------------------------------------
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in _VALID_MODES:
        raise AppError(
            "invalid_mode",
            f"mode must be one of {sorted(_VALID_MODES)!r}.",
            400,
        )

    partition = body.get("partition")
    if partition is not None:
        partition = str(partition).strip()
        _validate_partition(partition)
    if mode == "append" and not partition:
        raise AppError(
            "missing_partition",
            "mode=append requires a 'partition' value (e.g. 'dt=2026-06-25').",
            400,
        )

    schema_raw = body.get("schema") or []
    schema = _validate_schema(schema_raw)

    # Optional table name: scopes the schema sidecar + lake prefix to a named
    # table within the datastore.  Defaults to "default" for backward compat.
    table_name = str(body.get("table_name") or "").strip() or "default"
    # Validate: must be a safe single segment (no slashes, no dots-traversal).
    _validate_partition(table_name)  # reuse the same per-segment rules

    idempotency_key = str(body.get("idempotency_key") or "").strip()
    if not idempotency_key:
        raise AppError(
            "missing_idempotency_key",
            "idempotency_key is required.",
            400,
        )

    # -- Idempotency check ----------------------------------------------------
    store: IngestSessionStore = get_ingest_session_store()
    existing = await store.get_by_idempotency_key(org_id, datastore_id, idempotency_key)
    if existing is not None:
        # Return the existing session (all modes/partitions must match to truly
        # be idempotent; mismatch is a client error but we return existing for
        # now — producers should use distinct keys for distinct intents).
        staging = _get_staging_area(org_id, existing["run_id"])
        resp = _session_response(existing)
        resp["upload_prefix"] = staging.uri()
        return resp

    # -- Create the session ---------------------------------------------------
    run_id = str(uuid.uuid4())
    record = await store.create(
        org_id=org_id,
        datastore_id=datastore_id,
        user_id=user_id,
        mode=mode,
        idempotency_key=idempotency_key,
        schema=schema,
        partition=partition or None,
        run_id=run_id,
        table_name=table_name,
    )

    staging = _get_staging_area(org_id, run_id)
    resp = _session_response(record)
    resp["upload_prefix"] = staging.uri()
    logger.info(
        "ingest: opened session %s (org=%s datastore=%s mode=%s run=%s)",
        record["id"], org_id, datastore_id, mode, run_id,
    )
    return resp


# ---------------------------------------------------------------------------
# 2. PUT /lake/{datastore_id}/ingest/sessions/{session_id}/parts/{relpath:path}
# ---------------------------------------------------------------------------


@router.put(
    "/{datastore_id}/ingest/sessions/{session_id}/parts/{relpath:path}",
    status_code=200,
)
async def upload_part(
    datastore_id: str,
    session_id: str,
    relpath: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Upload one Parquet/Arrow part into the session's staging area.

    The request body must be the raw bytes of the Parquet/Arrow file.
    The ``Content-Length`` header is strongly recommended.

    **Response (200)**::

        {
          "path":   "<relpath>",
          "size":   <int>,
          "sha256": "<hex>"
        }

    This is the ``ManifestEntry`` for this part; include it in the commit
    manifest exactly as returned.

    Path safety
    -----------
    ``relpath`` must contain only alphanumerics, hyphens, underscores, dots,
    and forward slashes (directory separator).  ``..`` and absolute paths are
    rejected.  The StagingArea additionally pins all writes under the run
    prefix server-side.

    Errors
    ------
    * 403 ``custody_disabled`` — tier is off.
    * 404 ``session_not_found`` — session not found or cross-org.
    * 409 ``session_not_open`` — session is not in ``open`` state.
    * 400 ``invalid_relpath`` — path escapes or contains disallowed chars.
    * 400 ``empty_part`` — zero-byte upload rejected.
    """
    assert_custody_enabled()

    user_id = str(user["id"])
    org_id = await resolve_org_id(user_id, repo, request)

    # -- Resolve and validate session -----------------------------------------
    store = get_ingest_session_store()
    record = await store.get(org_id, datastore_id, session_id)
    if record is None:
        raise AppError("session_not_found", "Ingest session not found.", 404)
    if record["state"] != "open":
        raise AppError(
            "session_not_open",
            f"Session is in state {record['state']!r}; only 'open' sessions accept uploads.",
            409,
        )

    # -- Validate relpath ------------------------------------------------------
    safe_rel = _validate_relpath(relpath)

    # -- Read the uploaded bytes -----------------------------------------------
    data = await request.body()
    if not data:
        raise AppError("empty_part", "Part upload must be non-empty.", 400)

    # -- Write to staging -----------------------------------------------------
    staging = _get_staging_area(org_id, record["run_id"])
    entry = staging.write_bytes(data, safe_rel)

    logger.debug(
        "ingest: staged part %s for session %s (%d bytes)",
        safe_rel, session_id, entry.size,
    )
    return entry.to_dict()


# ---------------------------------------------------------------------------
# 3. POST /lake/{datastore_id}/ingest/sessions/{session_id}/commit
# ---------------------------------------------------------------------------


@router.post("/{datastore_id}/ingest/sessions/{session_id}/commit", status_code=200)
async def commit_session(
    datastore_id: str,
    session_id: str,
    body: dict[str, Any],
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Commit an open ingest session — verify staged parts and promote to the lake.

    **Request body (producer manifest)**::

        {
          "files": [
            {"path": "<relpath>", "size": <int>, "sha256": "<hex>"},
            ...
          ],
          "row_counts": {"<relpath>": <int>, ...}
        }

    The server re-reads every staged object, checks size + sha256 against the
    manifest, then atomically promotes all parts into the lake.

    Atomicity notes
    ---------------
    Object stores have no multi-object atomic commit.

    * **full_replace**: promote all parts, then sweep old objects under the
      table prefix that are NOT in the publish set.  Ordering is
      publish-first then sweep (readers see new data before old is removed).
      The window between publish-done and sweep-done is visible on reads.
    * **append**: promote parts under ``<table>/<partition>/`` without
      touching other partitions.  Re-committing the same session_id is a
      no-op (returns the stored result).

    Schema evolution (append mode)
    --------------------------------
    The server compares the session schema against the last committed schema
    for this table.  Removing columns or changing existing column types is
    rejected with 409 ``schema_incompatible``; adding new columns is allowed.

    **Response (200)**::

        {
          "published": true,
          "files":     <int>,
          "rows":      <int>,
          "mode":      "full_replace" | "append",
          "partition": "<partition>" | null,
          "final_uris": ["<uri>", ...]
        }

    Idempotency
    -----------
    Re-committing an already-committed session (same ``session_id``) returns
    the stored result without re-running promote (safe under network retries).

    Errors
    ------
    * 403 ``custody_disabled`` — tier is off.
    * 404 ``session_not_found`` — session not found or cross-org.
    * 409 ``session_not_open`` — session already committed, aborted, or locked.
    * 400 ``invalid_manifest`` — manifest missing or malformed.
    * 422 ``manifest_verification_failed`` — staged bytes don't match manifest.
    * 409 ``schema_incompatible`` — append schema narrows existing schema.
    """
    assert_custody_enabled()

    user_id = str(user["id"])
    org_id = await resolve_org_id(user_id, repo, request)

    store = get_ingest_session_store()
    record = await store.get(org_id, datastore_id, session_id)
    if record is None:
        raise AppError("session_not_found", "Ingest session not found.", 404)

    # -- Idempotency: already committed → return stored result ----------------
    if record["state"] == "committed" and record.get("result"):
        return record["result"]

    # -- Guard: must be in 'open' state ---------------------------------------
    if record["state"] not in ("open",):
        raise AppError(
            "session_not_open",
            f"Session is in state {record['state']!r}; "
            "only 'open' sessions can be committed. "
            "If a prior commit is in progress, retry after it completes.",
            409,
        )

    # -- Parse + validate the producer manifest --------------------------------
    files_raw = body.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise AppError(
            "invalid_manifest",
            "'files' must be a non-empty list of {path, size, sha256} objects.",
            400,
        )
    from app.lakehouse.staging import ManifestEntry, StagingManifest  # noqa: PLC0415

    entries: list[ManifestEntry] = []
    for i, f in enumerate(files_raw):
        if not isinstance(f, dict):
            raise AppError("invalid_manifest", f"files[{i}] must be an object.", 400)
        path = str(f.get("path") or "").strip()
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            raise AppError("invalid_manifest", f"files[{i}].size must be an integer.", 400)
        sha = str(f.get("sha256") or "").strip().lower()
        if not path or size <= 0 or len(sha) != 64:
            raise AppError(
                "invalid_manifest",
                f"files[{i}] requires non-empty path, positive size, "
                "and a 64-character hex sha256.",
                400,
            )
        entries.append(ManifestEntry(path=path, size=size, sha256=sha))

    row_counts: dict[str, int] = {}
    for k, v in (body.get("row_counts") or {}).items():
        try:
            row_counts[str(k)] = int(v)
        except (TypeError, ValueError):
            pass
    manifest = StagingManifest(files=entries, row_counts=row_counts)

    # -- CAS: open → committing (serialise concurrent commits) ----------------
    locked = await store.transition(
        org_id, datastore_id, session_id, "committing", from_state="open"
    )
    if locked is None:
        raise AppError(
            "session_not_open",
            "Could not acquire commit lock; another commit may be in progress.",
            409,
        )

    # ── From here: we own the session; failures transition to 'open' again ───
    try:
        return await _do_commit(
            org_id=org_id,
            datastore_id=datastore_id,
            session_id=session_id,
            record=locked,
            manifest=manifest,
            store=store,
        )
    except Exception as exc:
        # Revert to 'open' so the producer can retry.
        err_msg = str(exc)
        await store.transition(
            org_id, datastore_id, session_id, "open",
            from_state="committing",
            error=err_msg,
        )
        raise


async def _do_commit(
    *,
    org_id: str,
    datastore_id: str,
    session_id: str,
    record: dict[str, Any],
    manifest: "Any",
    store: IngestSessionStore,
) -> dict[str, Any]:
    """Execute the verify + promote + sweep logic and seal the session."""
    from app.lakehouse.staging import ManifestVerificationError  # noqa: PLC0415

    mode = record["mode"]
    partition = record.get("partition")
    schema = record.get("schema") or []
    run_id = record["run_id"]

    staging = _get_staging_area(org_id, run_id)

    # -- Verify staged bytes against producer manifest -------------------------
    try:
        staging.verify(manifest)
    except ManifestVerificationError as exc:
        raise AppError(
            "manifest_verification_failed",
            str(exc),
            422,
        ) from exc

    # -- Derive per-table key -------------------------------------------------
    # table_name is set at session-open (defaults to "default").  It scopes
    # both the schema sidecar slot and the lake storage prefix to the named
    # table, allowing multiple tables to coexist within one datastore.
    table_key = record.get("table_name") or "default"

    # -- Schema evolution check ------------------------------------------------
    # append: reject narrowing (remove / type-change).
    # full_replace: validate declared schema matches stored schema if present.
    existing_schema = _load_table_schema(org_id, datastore_id, table_key)
    if mode == "append":
        if existing_schema:
            _check_schema_compatible(existing_schema, schema, partition)
    elif mode == "full_replace":
        if existing_schema and schema:
            # For full_replace, just verify declared schema is consistent with
            # what was previously declared (prevents silent schema divergence
            # across full-replace cycles).
            _check_schema_compatible(existing_schema, schema, partition)

    # -- Promote staged objects into the lake ---------------------------------
    client, final_key_fn = _build_promote_callable(
        org_id, datastore_id, staging, table_key, partition
    )
    final_uris: list[str] = []
    promoted_keys: set[str] = set()
    for entry in manifest.files:
        data = staging.read_bytes(entry.path)
        fk = final_key_fn(entry.path)
        client.upload_bytes(data, fk)
        promoted_keys.add(fk)
        # Build the URI from the central storage base.
        from app.lakehouse.managed import resolve_central_storage  # noqa: PLC0415

        central = resolve_central_storage()
        if central:
            final_uris.append(f"{central.base_uri()}/{fk}")
        else:
            final_uris.append(fk)

    # -- full_replace: sweep old objects NOT in this publish ------------------
    # Ordering: promote-then-sweep (publish-first semantics documented in
    # module docstring).  The window between promote-done and sweep-done is
    # visible on concurrent reads — object stores have no multi-object atomicity.
    if mode == "full_replace":
        from app.lakehouse.managed import lake_prefix  # noqa: PLC0415

        _ds_prefix = lake_prefix(org_id, datastore_id)
        _nubi_prefix = f"{_ds_prefix}_nubi/"
        existing_keys = _list_table_objects(org_id, datastore_id, table_key)
        stale_keys = [
            k for k in existing_keys
            if k not in promoted_keys
            # Belt-and-suspenders: NEVER delete the schema sidecar or session
            # sidecars under _nubi/ regardless of table scope.
            and not k.startswith(_nubi_prefix)
        ]
        if stale_keys:
            _delete_keys(org_id, datastore_id, stale_keys)
            logger.info(
                "ingest: full_replace swept %d stale objects for session %s",
                len(stale_keys), session_id,
            )

    # -- Save schema sidecar --------------------------------------------------
    _save_table_schema(org_id, datastore_id, table_key, schema)

    # -- Best-effort staging cleanup ------------------------------------------
    try:
        staging.cleanup()
    except Exception:  # noqa: BLE001
        pass

    result: dict[str, Any] = {
        "published": True,
        "files": len(manifest.files),
        "rows": manifest.total_rows,
        "mode": mode,
        "partition": partition,
        "final_uris": final_uris,
    }

    # -- Seal the session as committed ----------------------------------------
    await store.transition(
        org_id, datastore_id, session_id, "committed",
        from_state="committing",
        result=result,
    )
    logger.info(
        "ingest: committed session %s (org=%s datastore=%s mode=%s files=%d rows=%d)",
        session_id, org_id, datastore_id, mode,
        len(manifest.files), manifest.total_rows,
    )
    return result


# ---------------------------------------------------------------------------
# 4. GET /lake/{datastore_id}/ingest/sessions/{session_id}
# ---------------------------------------------------------------------------


@router.get("/{datastore_id}/ingest/sessions/{session_id}", status_code=200)
async def get_session(
    datastore_id: str,
    session_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Retrieve the current state of an ingest session.

    **Response (200)**: the session record with ``state``, ``result`` (if
    committed), and ``error`` (if the last commit attempt failed).

    Errors
    ------
    * 403 ``custody_disabled``
    * 404 ``session_not_found``
    """
    assert_custody_enabled()
    user_id = str(user["id"])
    org_id = await resolve_org_id(user_id, repo, request)

    store = get_ingest_session_store()
    record = await store.get(org_id, datastore_id, session_id)
    if record is None:
        raise AppError("session_not_found", "Ingest session not found.", 404)
    return _session_response(record)


# ---------------------------------------------------------------------------
# 5. POST /lake/{datastore_id}/ingest/sessions/{session_id}/abort
# ---------------------------------------------------------------------------


@router.post("/{datastore_id}/ingest/sessions/{session_id}/abort", status_code=200)
async def abort_session(
    datastore_id: str,
    session_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Abort an open ingest session.

    Cleans up the staging area (best-effort) and marks the session aborted.
    Aborting an already-terminal session (committed or aborted) is a no-op
    that returns the current record.

    **Response (200)**: the updated session record.

    Errors
    ------
    * 403 ``custody_disabled``
    * 404 ``session_not_found``
    """
    assert_custody_enabled()
    user_id = str(user["id"])
    org_id = await resolve_org_id(user_id, repo, request)

    store = get_ingest_session_store()
    record = await store.get(org_id, datastore_id, session_id)
    if record is None:
        raise AppError("session_not_found", "Ingest session not found.", 404)

    if record["state"] in ("committed", "aborted"):
        # Already terminal — idempotent no-op.
        return _session_response(record)

    # Best-effort staging cleanup.
    try:
        staging = _get_staging_area(org_id, record["run_id"])
        staging.cleanup()
    except Exception:  # noqa: BLE001
        pass

    updated = await store.transition(org_id, datastore_id, session_id, "aborted")
    final = updated or record
    logger.info("ingest: aborted session %s (org=%s)", session_id, org_id)
    return _session_response(final)


# ---------------------------------------------------------------------------
# Self-register on api_router (mirrors snapshot.py convention)
# ---------------------------------------------------------------------------

api_router.include_router(router)
