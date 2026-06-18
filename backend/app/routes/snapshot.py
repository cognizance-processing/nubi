"""Board snapshot router — frozen DuckDB sidecar create/refresh + frozen viewer.

Endpoints
---------
POST /boards/{id}/snapshot   (first-party auth, org-scoped)
    Create (or, with ``?snapshot_id=<id>``, refresh) a frozen snapshot of the
    board's widget data.  Collects every data widget server-side and writes a
    single ``.duckdb`` sidecar artifact to object storage (or a local path for
    dev), then persists the snapshot descriptor org-scoped on the board row.
    Returns the snapshot id + artifact descriptor.

GET  /boards/{id}/snapshot           (first-party auth, org-scoped)
    List the board's persisted snapshot descriptors.

GET  /embed/frozen/{dashboard_id}    (verified identity — first-party OR embed)
    Frozen-viewer path: serve the board spec + the sidecar artifact reference so
    an embed can render against FROZEN data with no live DB.  Pick a specific
    snapshot with ``?snapshot_id=<id>``; otherwise the most recently refreshed
    snapshot is returned.

RLS-at-snapshot — IMPORTANT
---------------------------
A snapshot freezes data through ONE policy view captured at snapshot time (from
the verified token's ``policies`` claim — never a request body).  The frozen
artifact is then exposed UNIFORMLY to every holder; there is no per-viewer
predicate injection at frozen-render time (there is no live query).  See
``app.embedding.snapshot`` for the full security note and per-tenant guidance.

Router wiring
-------------
``api_router`` is mounted under ``/api/v1`` so the final paths are::

    POST /api/v1/boards/{id}/snapshot
    GET  /api/v1/boards/{id}/snapshot
    GET  /api/v1/embed/frozen/{dashboard_id}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.deps import current_user, verified_identity
from app.auth.scopes import has_scope
from app.auth.verify import VerifiedIdentity
from app.embedding.public_export import export_public
from app.embedding.snapshot import (
    create_snapshot,
    get_snapshot,
    list_snapshots,
    refresh_snapshot,
)
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

# First-party create/refresh + list live under /boards (mirrors export_share).
router = APIRouter(prefix="/boards", tags=["snapshot"])
# The frozen viewer lives under /embed (mirrors the embed config endpoint).
frozen_router = APIRouter(prefix="/embed", tags=["snapshot"])


# ---------------------------------------------------------------------------
# POST /boards/{id}/snapshot
# ---------------------------------------------------------------------------


@router.post("/{board_id}/snapshot", status_code=200)
async def create_or_refresh_snapshot(
    board_id: str,
    request: Request,
    snapshot_id: str | None = None,
    schedule: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new snapshot, or refresh an existing one when ``snapshot_id`` is given.

    First-party access tokens carry no RLS policies (full-access editor view), so
    the captured artifact freezes the full dataset.  For a tenant-scoped frozen
    view, drive the refresh through the ``snapshot_refresh`` flow task with the
    desired ``policies`` instead (a cron tick has no user JWT).

    Parameters
    ----------
    board_id:
        The board to snapshot (org-scoped; never cross-org).
    snapshot_id:
        When given, rewrite that snapshot's artifact in place; otherwise a new
        snapshot is created.
    schedule:
        Optional cron string recorded on a newly-created snapshot for scheduled
        refresh (nullable).

    Returns
    -------
    dict
        ``{snapshot_id, board_id, artifact, created_at, refreshed_at, ...}``.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    # First-party = no RLS (full-access editor view).
    claims: dict[str, Any] = {"policies": {}}

    if snapshot_id:
        descriptor = await refresh_snapshot(board_id, org_id, snapshot_id, claims, repo)
    else:
        descriptor = await create_snapshot(
            board_id,
            org_id,
            claims,
            repo,
            created_by=str(user["id"]),
            schedule=schedule,
        )

    return {
        "snapshot_id": descriptor["id"],
        "board_id": descriptor["board_id"],
        "created_at": descriptor["created_at"],
        "refreshed_at": descriptor["refreshed_at"],
        "schedule": descriptor.get("schedule"),
        "policy_fingerprint": descriptor["policy_fingerprint"],
        "artifact": descriptor["artifact"],
    }


# ---------------------------------------------------------------------------
# GET /boards/{id}/snapshot
# ---------------------------------------------------------------------------


@router.get("/{board_id}/snapshot")
async def list_board_snapshots(
    board_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List the persisted snapshot descriptors for *board_id* (org-scoped)."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)
    snaps = await list_snapshots(board_id, org_id, repo)
    return {"board_id": board_id, "snapshots": snaps}


# ---------------------------------------------------------------------------
# POST /boards/{id}/export/public  — UNSAFE no-auth public/CDN static export
# ---------------------------------------------------------------------------


@router.post("/{board_id}/export/public", status_code=200)
async def export_board_public(
    board_id: str,
    request: Request,
    snapshot_id: str | None = None,
    public_base_url: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """UNSAFE — produce a NO-AUTH public/CDN static export of a board.

    .. danger::

        **THE PRODUCED FILE/URL IS PUBLIC.**  Authentication is required to
        *create* an export, but the resulting static HTML and the snapshot
        sidecar it links are exposed to ANYONE with the URL — no auth, no expiry,
        no per-viewer security.  The data is frozen with the EXPORTER's RLS view
        (the verified token's ``policies`` — never a request body) and shown
        uniformly to every viewer.  Only export boards whose data is safe to make
        fully public.

    Disabled by default.  Refused with 403 ``public_exports_disabled`` unless
    BOTH the deployment switch ``ALLOW_UNSAFE_PUBLIC_EXPORTS`` is on AND the
    caller's org holds the ``public_exports`` feature gate.  Every successful
    export writes an audit entry (org-scoped on the board row under
    ``config.public_export_audit`` + a WARNING log line).

    Reuses Mode 3a's snapshot artifact: pass ``?snapshot_id=<id>`` to publish an
    existing frozen snapshot, or omit it to capture a fresh one.  Data collection
    is never re-implemented here.

    Parameters
    ----------
    board_id:
        The board to export (org-scoped; never cross-org).
    snapshot_id:
        Optional existing Mode 3a snapshot to publish; a new one is created when
        omitted.
    public_base_url:
        Public origin (CDN / bucket) the snapshot sidecar is served from;
        embedded in the generated HTML.

    Returns
    -------
    dict
        ``{export_id, board_id, snapshot_id, html_uri, snapshot_public_url,
        policy_fingerprint, exported_at, unsafe: True}``.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    # First-party = no RLS (full-access editor view) — the captured frozen view.
    claims: dict[str, Any] = {"policies": {}}
    return await export_public(
        board_id,
        org_id,
        claims,
        repo,
        exported_by=str(user["id"]),
        snapshot_id=snapshot_id,
        public_base_url=public_base_url,
    )


# ---------------------------------------------------------------------------
# GET /embed/frozen/{dashboard_id}  — frozen viewer
# ---------------------------------------------------------------------------


@frozen_router.get("/frozen/{dashboard_id}")
async def get_frozen_view(
    dashboard_id: str,
    snapshot_id: str | None = None,
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return the board spec + sidecar artifact reference for a frozen render.

    The viewer renders against the FROZEN artifact (no live DB).  When
    ``snapshot_id`` is omitted the most recently refreshed snapshot is returned.

    Parameters
    ----------
    dashboard_id:
        Board id, resolved org-scoped.
    snapshot_id:
        Optional specific snapshot; defaults to the latest by ``refreshed_at``.
    identity:
        Verified token identity (first-party OR host-signed embed).

    Returns
    -------
    dict
        ``{dashboard_id, snapshot_id, spec, artifact, refreshed_at,
        policy_fingerprint, frozen: True}``.

    Raises
    ------
    AppError("insufficient_scope", 403)
        If the token carries no qualifying read scope.
    AppError("dashboard_not_found", 404)
        If no board with *dashboard_id* exists in the caller's org.
    AppError("snapshot_not_found", 404)
        If the board has no snapshot (or no snapshot with *snapshot_id*).
    """
    # ── Scope gate (mirrors GET /embed/config) ────────────────────────────────
    _scopes = identity.scope
    _has_read = has_scope(_scopes, "read:query") or any(
        s.startswith("read:") for s in _scopes
    )
    if not _has_read:
        raise AppError(
            "insufficient_scope",
            "Token does not carry the required scope: read:query",
            403,
        )

    # ── Resolve org_id (embed token carries it; first-party looks it up) ──────
    if identity.kind == "embed" and identity.org:
        org_id = identity.org
    else:
        from app.routes.resources import get_user_org  # noqa: PLC0415

        org_id = await get_user_org(identity.user_id, repo)

    board = await repo.get("boards", org_id, dashboard_id)
    if board is None:
        raise AppError("dashboard_not_found", f"Dashboard {dashboard_id!r} not found.", 404)

    if snapshot_id:
        descriptor = await get_snapshot(dashboard_id, org_id, snapshot_id, repo)
        if descriptor is None:
            raise AppError("snapshot_not_found", f"Snapshot {snapshot_id!r} not found.", 404)
    else:
        snaps = await list_snapshots(dashboard_id, org_id, repo)
        if not snaps:
            raise AppError(
                "snapshot_not_found",
                f"No snapshot exists for dashboard {dashboard_id!r}.",
                404,
            )
        descriptor = max(snaps, key=lambda s: s.get("refreshed_at") or s.get("created_at") or "")

    return {
        "dashboard_id": dashboard_id,
        "snapshot_id": descriptor["id"],
        "frozen": True,
        "spec": descriptor.get("spec"),
        "artifact": descriptor["artifact"],
        "refreshed_at": descriptor.get("refreshed_at"),
        "policy_fingerprint": descriptor.get("policy_fingerprint"),
    }


# ---------------------------------------------------------------------------
# Register routers on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
api_router.include_router(frozen_router)
