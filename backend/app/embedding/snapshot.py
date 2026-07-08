"""Frozen DuckDB snapshot of a board's widget data (Mode 3a).

A *snapshot* captures the rows behind every data widget on a board at a point
in time and writes them as a **single sidecar artifact** — a ``.duckdb`` file —
to object storage (or a local path for dev).  An embed can then render the board
against the frozen artifact with **no live database connection**: the viewer
reads ``widget_<widget_id>`` tables out of the sidecar instead of re-planning and
re-executing queries.

Why a sidecar
-------------
* One self-contained file per snapshot — easy to copy, cache at the edge, hand
  to a CDN, or ship to an air-gapped viewer.
* Reuses the project's ``app.storage`` layer (uploads to ``s3://`` / cloud
  destinations and writes local/``file://`` paths directly), and the
  ``FLOWS_MATERIALIZE_BASE_URI`` storage-URI convention from
  :mod:`app.config`.

Artifact layout
---------------
The ``.duckdb`` file contains:

* ``widget_<widget_id>`` — one table per data widget that collected rows.  The
  widget id is sanitised to a SQL-safe identifier; a sidecar manifest maps the
  sanitised table name back to the original widget id / query id / columns.
* ``_nubi_snapshot_meta`` — a single-row table holding the snapshot id, board
  id, source datastore (if any), the policy-view fingerprint, and the
  ``created_at`` ISO timestamp, so a viewer can introspect the artifact
  stand-alone.

Snapshot metadata (board spec, source datastore, created_at, artifact URI,
refresh schedule) is persisted **org-scoped** on the board row itself, under
``board.config["snapshots"]`` — no new resource table is introduced, so RLS and
org-scoping flow through the existing ``boards`` repo path untouched.

RLS-at-snapshot — READ THIS
---------------------------
A snapshot **freezes data through a single policy view**.  The rows are captured
under whatever ``policies`` were supplied at capture time (from the verified
token, never a request body — see :func:`collect_board_data`).  Once written,
the sidecar is *plaintext frozen data*: **every holder of the artifact sees the
same rows**, regardless of their own RLS context.  There is no per-viewer
predicate injection at frozen-render time because there is no live query.

**RLS-at-capture hardening (this module)**:

When :func:`create_snapshot` or :func:`refresh_snapshot` is called with empty
``policies`` (i.e. ``claims["policies"] == {}``) AND the board references a
datastore-backed query (a multi-tenant capable data source), a **WARNING** is
emitted to the operational log.  The warning documents:

* That the artifact was captured WITHOUT an RLS filter (``policy_fingerprint``
  will be ``"none"``).
* That every holder of the artifact sees ALL rows in the frozen data.
* The board id, so operators can trace which artifact may be over-exposed.

The capture model is NOT changed — the warning is informational only.  If you
deliberately want a full-data snapshot (e.g. for a public marketing board),
this warning is expected and can be suppressed by setting a non-empty sentinel
policy or by using a board that only references the built-in demo connector.

Operational guidance:

* Snapshot with ``policies={}`` (the default) only for boards whose data is safe
  to expose uniformly to all artifact holders (e.g. a public marketing board).
* For multi-tenant data, capture **one snapshot per tenant policy view** and
  hand each artifact only to holders entitled to that view.  The captured policy
  fingerprint is recorded in the metadata so operators can audit which view a
  given artifact froze.

Public API
----------
create_snapshot(board_id, org_id, claims, repo, *, created_by, datastore=None,
                schedule=None) -> dict
    Collect the board's widget data, write the sidecar ``.duckdb`` artifact, and
    persist the snapshot metadata on the board.  Returns the snapshot descriptor.

refresh_snapshot(board_id, org_id, snapshot_id, claims, repo) -> dict
    Re-run the collector and rewrite the SAME artifact URI in place; bump
    ``refreshed_at``.  Used by the ``snapshot_refresh`` flow task kind.

get_snapshot(board_id, org_id, snapshot_id, repo) -> dict | None
    Return the persisted snapshot descriptor, or ``None``.

list_snapshots(board_id, org_id, repo) -> list[dict]
    Return all persisted snapshot descriptors for a board.

snapshot_artifact_uri(board_id, org_id, snapshot_id, *, base_uri=None) -> str
    Compute the deterministic artifact URI for a snapshot.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import logging

from app.dashboards.collect import collect_board_data, spec_from_board
from app.errors import AppError
from app.repos.provider import Repo

logger = logging.getLogger(__name__)

# Table-name prefix for a widget's frozen rows inside the sidecar.
_WIDGET_TABLE_PREFIX = "widget_"
# Single-row metadata table embedded in the sidecar.
_META_TABLE = "_nubi_snapshot_meta"


# ---------------------------------------------------------------------------
# URI / identifier helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _policy_fingerprint(policies: dict[str, Any]) -> str:
    """Return a stable short fingerprint of the captured policy view.

    Recorded in the snapshot metadata so operators can audit *which* RLS view a
    frozen artifact captured (see the module's RLS-at-snapshot note).  Two
    captures with the same policies share a fingerprint; ``{}`` (no RLS) maps to
    a fixed sentinel.
    """
    if not policies:
        return "none"
    canonical = json.dumps(policies, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _sanitise_table(widget_id: str, index: int) -> str:
    """Map a widget id to a SQL-safe ``widget_<...>`` table name.

    Non-alphanumeric characters are replaced with ``_``; the original widget id
    is preserved separately in the sidecar manifest, so this only needs to be
    deterministic and collision-resistant (the trailing index disambiguates two
    widgets that sanitise to the same stem).
    """
    stem = "".join(c if c.isalnum() else "_" for c in str(widget_id))
    return f"{_WIDGET_TABLE_PREFIX}{stem}_{index}"


def _materialize_base_uri() -> str:
    """Return the configured storage base URI, falling back to a local dev path.

    Reuses :data:`app.config.Settings.FLOWS_MATERIALIZE_BASE_URI` (the same
    storage-URI convention as flow materialization).  When unset, falls back to
    ``<backend>/seed_data/snapshots`` as a ``file://`` URI — fine for local dev
    only, mirroring ``incremental.py``'s fallback.
    """
    from app.config import get_settings  # noqa: PLC0415

    base = (get_settings().FLOWS_MATERIALIZE_BASE_URI or "").strip()
    if base:
        return base.rstrip("/") + "/snapshots"
    # Local dev fallback: <backend>/seed_data/snapshots
    backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    local_dir = os.path.join(backend_root, "seed_data", "snapshots")
    return "file://" + local_dir


def snapshot_artifact_uri(
    board_id: str,
    org_id: str,
    snapshot_id: str,
    *,
    base_uri: str | None = None,
) -> str:
    """Compute the deterministic sidecar artifact URI for a snapshot.

    The URI is org-namespaced so artifacts from different orgs never collide::

        <base>/snapshots/<org_id>/<board_id>/<snapshot_id>.duckdb

    Parameters
    ----------
    board_id, org_id, snapshot_id:
        Identifiers that compose the deterministic key.
    base_uri:
        Storage base override (for tests); defaults to
        :func:`_materialize_base_uri`.
    """
    base = (base_uri.rstrip("/") if base_uri else _materialize_base_uri())
    return f"{base}/{org_id}/{board_id}/{snapshot_id}.duckdb"


# ---------------------------------------------------------------------------
# Sidecar writer
# ---------------------------------------------------------------------------


def _write_sidecar(
    artifact_uri: str,
    widgets: list[dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    """Write the frozen widget data + metadata to a ``.duckdb`` sidecar artifact.

    Builds the artifact in a fresh local DuckDB file (one table per data widget
    plus the ``_nubi_snapshot_meta`` row), then — for cloud destinations — copies
    it to object storage via the project's storage layer.  Returns a manifest
    describing the tables written (table name → widget id / query id / columns /
    row count).

    Local / ``file://`` destinations are written in place; ``s3://`` (and other
    cloud) destinations are written to a temp file and uploaded through the
    ``app.storage`` client.

    Per-widget errors carried in *widgets* (entries with an ``error`` key) are
    recorded in the manifest but produce no table — the artifact only contains
    successfully collected data.
    """
    import tempfile  # noqa: PLC0415

    import duckdb  # noqa: PLC0415
    import pyarrow as pa  # noqa: PLC0415

    scheme = artifact_uri.split("://", 1)[0] if "://" in artifact_uri else ""
    is_local = scheme in ("", "file")

    # Resolve the local path to build the .duckdb file at.
    if is_local:
        local_path = artifact_uri[len("file://"):] if scheme == "file" else artifact_uri
        parent = os.path.dirname(local_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # DuckDB will not overwrite an existing file with connect(); remove it so
        # a refresh rewrites the artifact in place cleanly.
        if os.path.exists(local_path):
            os.remove(local_path)
        build_path = local_path
        tmp_dir = None
    else:
        tmp_dir = tempfile.mkdtemp(prefix="nubi-snapshot-")
        build_path = os.path.join(tmp_dir, "snapshot.duckdb")

    manifest: list[dict[str, Any]] = []
    conn = duckdb.connect(database=build_path)
    try:
        for idx, w in enumerate(widgets):
            if "error" in w:
                manifest.append(
                    {
                        "widget_id": w.get("widget_id"),
                        "query_id": w.get("query_id"),
                        "error": w["error"],
                    }
                )
                continue
            columns: list[str] = list(w.get("columns") or [])
            rows: list[list[Any]] = list(w.get("rows") or [])
            table_name = _sanitise_table(w["widget_id"], idx)
            # Build an Arrow table column-wise (rows are row-major lists).
            data: dict[str, list[Any]] = {c: [] for c in columns}
            for row in rows:
                for ci, c in enumerate(columns):
                    data[c].append(row[ci] if ci < len(row) else None)
            arrow_table = pa.table(data) if columns else pa.table({})
            conn.register("_src", arrow_table)
            conn.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _src')
            conn.unregister("_src")
            manifest.append(
                {
                    "widget_id": w["widget_id"],
                    "query_id": w.get("query_id"),
                    "table": table_name,
                    "columns": columns,
                    "row_count": len(rows),
                }
            )

        # Embedded metadata table (single row) — lets a viewer introspect the
        # artifact stand-alone without the board row.
        meta_row = pa.table(
            {
                "snapshot_id": [str(meta.get("snapshot_id") or "")],
                "board_id": [str(meta.get("board_id") or "")],
                "datastore": [meta.get("datastore") if meta.get("datastore") else ""],
                "policy_fingerprint": [str(meta.get("policy_fingerprint") or "none")],
                "created_at": [str(meta.get("created_at") or "")],
                "manifest_json": [json.dumps(manifest, default=str)],
            }
        )
        conn.register("_meta_src", meta_row)
        conn.execute(f'CREATE TABLE "{_META_TABLE}" AS SELECT * FROM _meta_src')
        conn.unregister("_meta_src")
    finally:
        conn.close()

    # Upload to cloud storage when the destination is not local.
    if not is_local:
        try:
            from app.storage.base import get_storage_client, parse_uri  # noqa: PLC0415

            # Stream the file via SDK multipart upload — avoids reading the
            # entire .duckdb artifact into process memory as a bytes object.
            _scheme, _bucket, key = parse_uri(artifact_uri)
            client = get_storage_client(artifact_uri)
            client.upload_file(build_path, key)
        finally:
            import shutil  # noqa: PLC0415

            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"tables": manifest}


# ---------------------------------------------------------------------------
# Snapshot metadata persistence (on the board row, org-scoped)
# ---------------------------------------------------------------------------


def _board_snapshots(board: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the snapshot descriptor list stored on a board's config."""
    config = board.get("config") or {}
    snaps = config.get("snapshots")
    return list(snaps) if isinstance(snaps, list) else []


# ---------------------------------------------------------------------------
# RLS-at-capture hardening helpers
# ---------------------------------------------------------------------------


def _board_references_datastore(board: dict[str, Any]) -> bool:
    """Return True when the board has at least one widget backed by a datastore.

    A widget is datastore-backed when its ``query_id`` resolves to a query that
    carries a ``datastore_id``.  We check the query registry synchronously so
    this helper is safe to call in the synchronous path; queries that cannot be
    resolved are skipped (best-effort).

    A board that only uses the built-in demo connector (no datastore_id) is NOT
    considered multi-tenant — the demo data is static and public by design.
    """
    from app.dashboards.collect import spec_from_board, widget_query_targets  # noqa: PLC0415
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    spec = spec_from_board(board)
    targets = widget_query_targets(spec, only_query_id=None)
    if not targets:
        return False

    registry = get_query_registry()
    for t in targets:
        registered = registry.get(t["query_id"])
        if registered is not None and getattr(registered, "datastore_id", None):
            return True
    return False


def _warn_if_no_rls_on_multi_tenant(
    board_id: str,
    policies: dict[str, Any],
    board: dict[str, Any],
    policy_fingerprint: str,
) -> None:
    """Log a WARNING when capturing a snapshot without RLS on a datastore board.

    A snapshot with empty *policies* (no per-viewer predicate) bakes a FULL-DATA
    view into the artifact.  Every holder of the artifact sees the same rows —
    regardless of their own RLS context — because the sidecar is plaintext frozen
    data with no live query.

    When the board is datastore-backed (multi-tenant capable) and *policies* is
    empty, this is a potential data-exposure hazard: operators may not realise
    they are capturing a cross-tenant full-data snapshot.  This function emits a
    loud WARNING so the decision is auditable in the operational logs.

    The *policy_fingerprint* is included in the log so operators can trace which
    view was baked into a given artifact (``"none"`` fingerprint = no RLS filter).

    Parameters
    ----------
    board_id:
        The board being snapshotted.
    policies:
        The RLS claim dict from the capture token (``claims["policies"]``).
    board:
        The board row (used to check for datastore references).
    policy_fingerprint:
        The pre-computed policy fingerprint for the log entry.
    """
    if policies:
        # RLS policies are present — capture is filtered.  No warning needed.
        return

    if not _board_references_datastore(board):
        # Demo-only board: no multi-tenant data, safe to capture without RLS.
        return

    logger.warning(
        "snapshot RLS-at-capture: board %r captured with NO RLS policies "
        "(policy_fingerprint=%r).  The frozen artifact exposes FULL-DATA "
        "to every holder — no per-viewer predicate is injected at render time.  "
        "If this board is multi-tenant, capture one snapshot PER tenant policy "
        "view and hand each artifact only to entitled holders.",
        board_id,
        policy_fingerprint,
    )


async def _persist_snapshot(
    board: dict[str, Any],
    org_id: str,
    repo: Repo,
    descriptor: dict[str, Any],
) -> None:
    """Upsert *descriptor* into ``board.config["snapshots"]`` (org-scoped).

    The board row is the org-scoped persistence point — the update flows through
    the same ``repo.update("boards", org_id, …)`` path as every other board
    mutation, so org-scoping/RLS are unchanged.
    """
    config = dict(board.get("config") or {})
    snaps = [s for s in _board_snapshots(board) if s.get("id") != descriptor["id"]]
    snaps.append(descriptor)
    config["snapshots"] = snaps
    await repo.update("boards", org_id, str(board["id"]), {"config": config})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_snapshot(
    board_id: str,
    org_id: str,
    claims: dict[str, Any],
    repo: Repo,
    *,
    created_by: str,
    datastore: str | None = None,
    schedule: str | None = None,
    base_uri: str | None = None,
) -> dict[str, Any]:
    """Capture a board's widget data and write a frozen ``.duckdb`` sidecar.

    Collects the board's per-widget rows via :func:`collect_board_data` (RLS is
    read ONLY from ``claims["policies"]`` — never a request body), writes the
    sidecar artifact, and persists the snapshot descriptor org-scoped on the
    board row.

    Parameters
    ----------
    board_id, org_id:
        The board to snapshot, resolved org-scoped (never cross-org).
    claims:
        Verified token claims.  Only ``claims["policies"]`` is consulted (the
        captured RLS view — see the module's RLS-at-snapshot note).
    repo:
        Active repository.
    created_by:
        User id recorded on the descriptor.
    datastore:
        Optional whole-dashboard datastore override id (the host-signed
        ``datastore`` claim).  Recorded as the snapshot's source datastore.
    schedule:
        Optional cron string for scheduled refresh (nullable).  Recorded on the
        descriptor; the actual scheduling hook is the ``snapshot_refresh`` flow
        task kind (see :func:`snapshot_refresh_handler`).
    base_uri:
        Storage base override (tests).

    Returns
    -------
    dict
        The snapshot descriptor (id, board_id, artifact, created_at, …).

    Raises
    ------
    AppError("board_not_found", 404)
        When the board does not exist in *org_id*.
    """
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    snapshot_id = str(uuid.uuid4())
    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})
    fingerprint = _policy_fingerprint(policies)

    # RLS-at-capture hardening: warn loudly when capturing a full-data view on a
    # datastore-backed (multi-tenant capable) board.  The frozen artifact exposes
    # all rows to every holder — there is no per-viewer predicate at render time.
    _warn_if_no_rls_on_multi_tenant(board_id, policies, board, fingerprint)

    widgets = await collect_board_data(board_id, org_id, claims=claims, repo=repo)
    artifact_uri = snapshot_artifact_uri(board_id, org_id, snapshot_id, base_uri=base_uri)
    created_at = _now_iso()

    artifact = await asyncio.to_thread(
        _write_sidecar,
        artifact_uri,
        widgets,
        {
            "snapshot_id": snapshot_id,
            "board_id": board_id,
            "datastore": datastore,
            "policy_fingerprint": fingerprint,
            "created_at": created_at,
        },
    )

    descriptor: dict[str, Any] = {
        "id": snapshot_id,
        "board_id": board_id,
        "created_by": created_by,
        "created_at": created_at,
        "refreshed_at": created_at,
        "datastore": datastore,
        "schedule": schedule,
        "policy_fingerprint": fingerprint,
        "spec": spec_from_board(board),
        "artifact": {
            "uri": artifact_uri,
            "format": "duckdb",
            "tables": artifact["tables"],
        },
    }

    await _persist_snapshot(board, org_id, repo, descriptor)
    return descriptor


async def refresh_snapshot(
    board_id: str,
    org_id: str,
    snapshot_id: str,
    claims: dict[str, Any],
    repo: Repo,
    *,
    base_uri: str | None = None,
) -> dict[str, Any]:
    """Re-run the collector and rewrite the SAME sidecar artifact in place.

    Used by the ``snapshot_refresh`` flow task so a daily/cron flow keeps a
    snapshot fresh.  The artifact URI is recomputed deterministically from the
    snapshot id (so it is rewritten in place), ``refreshed_at`` is bumped, and
    the captured policy view comes from *claims* exactly as in
    :func:`create_snapshot`.

    Raises
    ------
    AppError("board_not_found", 404)
        When the board does not exist in *org_id*.
    AppError("snapshot_not_found", 404)
        When no snapshot with *snapshot_id* exists on the board.
    """
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    existing = next((s for s in _board_snapshots(board) if s.get("id") == snapshot_id), None)
    if existing is None:
        raise AppError("snapshot_not_found", f"Snapshot {snapshot_id!r} not found.", 404)

    policies: dict[str, Any] = dict((claims or {}).get("policies") or {})
    fingerprint = _policy_fingerprint(policies)

    # RLS-at-capture hardening: warn when refreshing a full-data view on a
    # multi-tenant board.  See create_snapshot for the full rationale.
    _warn_if_no_rls_on_multi_tenant(board_id, policies, board, fingerprint)

    widgets = await collect_board_data(board_id, org_id, claims=claims, repo=repo)
    artifact_uri = snapshot_artifact_uri(board_id, org_id, snapshot_id, base_uri=base_uri)
    refreshed_at = _now_iso()

    artifact = await asyncio.to_thread(
        _write_sidecar,
        artifact_uri,
        widgets,
        {
            "snapshot_id": snapshot_id,
            "board_id": board_id,
            "datastore": existing.get("datastore"),
            "policy_fingerprint": fingerprint,
            "created_at": refreshed_at,
        },
    )

    descriptor = dict(existing)
    descriptor["refreshed_at"] = refreshed_at
    descriptor["policy_fingerprint"] = fingerprint
    descriptor["spec"] = spec_from_board(board)
    descriptor["artifact"] = {
        "uri": artifact_uri,
        "format": "duckdb",
        "tables": artifact["tables"],
    }

    await _persist_snapshot(board, org_id, repo, descriptor)
    return descriptor


async def get_snapshot(
    board_id: str,
    org_id: str,
    snapshot_id: str,
    repo: Repo,
) -> dict[str, Any] | None:
    """Return the persisted snapshot descriptor, or ``None`` if absent."""
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        return None
    return next((s for s in _board_snapshots(board) if s.get("id") == snapshot_id), None)


async def list_snapshots(
    board_id: str,
    org_id: str,
    repo: Repo,
) -> list[dict[str, Any]]:
    """Return all persisted snapshot descriptors for *board_id* (org-scoped)."""
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        return []
    return _board_snapshots(board)


# ---------------------------------------------------------------------------
# Flows task kind: snapshot_refresh
# ---------------------------------------------------------------------------


async def snapshot_refresh_handler(
    config: dict[str, Any],
    ctx: Any,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Flow task handler for the ``snapshot_refresh`` task kind.

    Re-runs the collector and rewrites the SAME sidecar artifact in place so a
    scheduled (daily/cron) flow keeps a snapshot fresh.  Mirrors ``fx_refresh``
    (:func:`app.ee.billing.fx._fx_refresh_handler`) and ``preagg_refresh``
    (:mod:`app.flows.handlers.preagg_refresh`) in shape.

    Registered as task kind ``'snapshot_refresh'`` by
    :func:`register_snapshot_refresh_task`.

    Config keys
    -----------
    ``board_id``    (required) — board whose snapshot is refreshed.
    ``snapshot_id`` (required) — the snapshot to rewrite in place.
    ``org_id``      (optional) — falls back to ``ctx.org_id`` / ``claims['org_id']``.
    ``policies``    (optional) — the RLS view to CAPTURE for the frozen artifact.
                    This is the ONE place a scheduled refresh declares the policy
                    view, because a cron tick carries no user JWT.  The captured
                    rows are exposed to ALL artifact holders — see the module's
                    RLS-at-snapshot note.
    ``base_uri``    (optional) — storage base override (ops / tests); defaults to
                    the configured ``FLOWS_MATERIALIZE_BASE_URI`` convention.

    Returns
    -------
    dict
        ``{board_id, snapshot_id, artifact_uri, refreshed_at, tables}``.
    """
    board_id: str | None = config.get("board_id")
    snapshot_id: str | None = config.get("snapshot_id")
    if not board_id or not snapshot_id:
        raise AppError(
            "invalid_task_config",
            "snapshot_refresh task requires 'board_id' and 'snapshot_id' in config.",
            400,
        )

    org_id: str = (
        config.get("org_id")
        or getattr(ctx, "org_id", None)
        or (claims or {}).get("org_id", "")
        or ""
    )
    if not org_id:
        raise AppError(
            "invalid_task_config",
            "snapshot_refresh task could not resolve an org_id.",
            400,
        )

    # The captured policy view comes from the task config (a scheduled tick has
    # no user JWT).  Frozen data is exposed to all artifact holders.
    refresh_claims = {"policies": dict(config.get("policies") or {})}

    from app.repos.provider import get_repo  # noqa: PLC0415

    repo = get_repo()
    descriptor = await refresh_snapshot(
        board_id,
        org_id,
        snapshot_id,
        refresh_claims,
        repo,
        base_uri=config.get("base_uri"),
    )
    return {
        "board_id": board_id,
        "snapshot_id": snapshot_id,
        "artifact_uri": descriptor["artifact"]["uri"],
        "refreshed_at": descriptor["refreshed_at"],
        "tables": descriptor["artifact"]["tables"],
    }


def register_snapshot_refresh_task() -> None:
    """Register the ``'snapshot_refresh'`` task kind in the core flows registry.

    Mirrors :func:`app.ee.billing._register_fx_refresh_task` — a runtime
    registration onto the already-instantiated core registry.  Safe to call
    multiple times (idempotent overwrite).
    """
    from app.flows.registry import get_task_kind_registry  # noqa: PLC0415

    get_task_kind_registry().register("snapshot_refresh", snapshot_refresh_handler)
