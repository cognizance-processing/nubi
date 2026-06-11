"""Whole-project bundle export / import (files-as-code F-3 / F-4).

Endpoints (mounted under ``/api/v1`` via ``api_router``)::

    GET  /projects/{project_id}/export
        → the whole project as ONE bundle: every dashboard, query, flow, and
          NON-SECRET connector in the project, each as a portability envelope.
          Shape::
              {apiVersion: "nubi/v1", kind: "project",
               metadata: {id, name}, resources: [<envelope>, …]}
          Connectors are scrubbed to non-secret config (the connector secret
          store is NEVER read). Cross-org / cross-project access → 404.

    POST /projects/{project_id}/import
        body = a bundle (the F-3 shape) OR a bare list of envelopes.
        → bulk upsert each envelope via the SAME per-kind logic ``POST /import``
          uses (``portability.upsert_envelope``), best-effort with per-resource
          error capture (one bad envelope never aborts the rest).
          Returns ``{results: [{kind, id, name, action, error?}], …counts}``.
          Connector imports never touch the connector secret store.

Auth + scoping
--------------
Authed via the first-party Bearer token (``current_user``); the effective org
is resolved exactly like the rest of the portability surface (``resolve_org_id``,
``X-Org-Id`` aware). The ``project_id`` path param is validated to belong to the
resolved org — a cross-org/unknown project returns 404 (no information leak),
mirroring the project-scoped routes in ``environments.py`` / ``git.py``. Import
additionally requires write access (``require_writer``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.errors import AppError
from app.portability import API_VERSION, to_envelope
from app.repos import projects as projects_repo
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id
from app.routes.portability import _is_connector_row, upsert_envelope

router = APIRouter(tags=["portability"])


async def _require_project(org_id: str, project_id: str) -> dict[str, Any]:
    """Return the project row if it belongs to *org_id*, else 404.

    Mirrors the tenant check used by the project-scoped routes: a project from
    another org (or one that does not exist) is invisible → 404, never 403.
    """
    project = await projects_repo.get_project(org_id, project_id)
    if project is None:
        raise AppError("not_found", "Project not found.", 404)
    return project


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/export
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/export")
async def export_project_bundle(
    project_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Export the whole project as one bundle of portability envelopes.

    Reuses the SAME ``to_envelope`` handlers as ``GET /export/{kind}/{id}`` so
    each envelope is byte-for-byte identical. Connectors carry NON-SECRET config
    only (the connector secret store is never read).
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project = await _require_project(org_id, project_id)

    resources: list[dict[str, Any]] = []

    # ── Dashboards (boards) ────────────────────────────────────────────────
    try:
        for board in await repo.list("boards", org_id, project_id):
            resources.append(to_envelope("dashboard", board))
    except AppError:
        pass

    # ── Queries ────────────────────────────────────────────────────────────
    try:
        for q in await repo.list("queries", org_id, project_id):
            resources.append(to_envelope("query", q))
    except AppError:
        pass

    # ── Connectors (datastores) — NON-SECRET only ──────────────────────────
    # Filter to real, user-facing connector rows (skip demo-hidden markers and
    # seeded system rows), exactly like GET /export/connector/{id}. The
    # connector envelope is scrubbed of secret keys by the kind handler.
    try:
        for ds in await repo.list("datastores", org_id, project_id):
            if _is_connector_row(ds):
                resources.append(to_envelope("connector", ds))
    except AppError:
        pass

    # ── Flows (flow store) ─────────────────────────────────────────────────
    try:
        from app.flows.store import get_flow_store  # noqa: PLC0415

        for flow in await get_flow_store().list_flows(org_id, project_id):
            resources.append(
                {
                    "kind": "flow",
                    "apiVersion": API_VERSION,
                    "metadata": {
                        "name": flow.get("name", ""),
                        "id": str(flow.get("id", "")),
                    },
                    "spec": flow.get("spec") or {},
                }
            )
    except Exception:  # noqa: BLE001 — flows are optional; never 5xx a bundle export
        pass

    return {
        "apiVersion": API_VERSION,
        "kind": "project",
        "metadata": {"id": str(project["id"]), "name": project.get("name") or ""},
        "resources": resources,
    }


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/import
# ---------------------------------------------------------------------------


def _extract_envelopes(body: Any) -> list[dict[str, Any]]:
    """Return the list of envelopes from a bundle body (F-3 shape OR bare list).

    Accepts:
    - the F-3 bundle ``{kind: "project", resources: [...]}``,
    - a bare list of envelopes,
    - ``{resources: [...]}`` without the project wrapper.

    Raises ``AppError("validation_error", 400)`` for anything else.
    """
    if isinstance(body, list):
        envelopes = body
    elif isinstance(body, dict) and isinstance(body.get("resources"), list):
        envelopes = body["resources"]
    else:
        raise AppError(
            "validation_error",
            "Body must be a project bundle ({resources: [...]}) or a list of envelopes.",
            400,
        )
    return [e for e in envelopes if isinstance(e, dict)]


@router.post(
    "/projects/{project_id}/import",
    status_code=200,
    dependencies=[Depends(require_writer)],
)
async def import_project_bundle(
    project_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Bulk-upsert a project bundle, returning a per-resource result.

    Every envelope is upserted into *project_id* via the SAME per-kind logic
    ``POST /import`` uses (``portability.upsert_envelope``). Application is
    best-effort: a single envelope that fails validation or upsert is recorded
    with its ``error`` and the rest still apply.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    await _require_project(org_id, project_id)

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise AppError("validation_error", f"Invalid JSON body: {exc}", 400) from exc

    envelopes = _extract_envelopes(body)

    results: list[dict[str, Any]] = []
    created = updated = failed = 0

    for env in envelopes:
        kind = env.get("kind")
        meta = env.get("metadata") or {}
        name = meta.get("name")
        env_id = meta.get("id")
        try:
            row, action = await upsert_envelope(
                env,
                org_id=org_id,
                user=user,
                repo=repo,
                project_id=project_id,
            )
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1
            results.append(
                {
                    "kind": kind,
                    "id": str(row.get("id")) if row.get("id") is not None else None,
                    "name": row.get("name", name),
                    "action": action,
                }
            )
        except AppError as exc:
            failed += 1
            results.append(
                {
                    "kind": kind,
                    "id": str(env_id) if env_id else None,
                    "name": name,
                    "action": "skipped",
                    "error": exc.message,
                }
            )
        except Exception as exc:  # noqa: BLE001 — never let one bad envelope abort the rest
            failed += 1
            results.append(
                {
                    "kind": kind,
                    "id": str(env_id) if env_id else None,
                    "name": name,
                    "action": "skipped",
                    "error": str(exc),
                }
            )

    return {
        "results": results,
        "created": created,
        "updated": updated,
        "failed": failed,
        "total": len(results),
    }


# ── Register on the shared api_router ─────────────────────────────────────
# Prepend (like portability.py) so the literal ``/projects/{project_id}/export``
# and ``/import`` paths win over the generic resources catch-all regardless of
# import order.
_before = len(api_router.routes)
api_router.include_router(router)
_new_routes = api_router.routes[_before:]
_old_routes = api_router.routes[:_before]
api_router.routes[:] = _new_routes + _old_routes
