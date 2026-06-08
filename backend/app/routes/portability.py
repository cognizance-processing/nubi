"""Portability router — export/import dashboards + queries as portable specs.

Endpoints (mounted under ``/api/v1`` by ``main.py`` via ``api_router``)::

    GET  /export/{kind}/{id}?format=yaml|json
        → the resource serialised as a Kubernetes-style envelope.
          Content-Type: application/yaml (or application/json).
          Content-Disposition: attachment; filename="<slug>.<ext>".
          404 for unknown kind / id / cross-org rows.

    POST /import
        body = a YAML or JSON envelope document (raw text or JSON).
        → parse + validate + upsert:
            * metadata.id present AND owned by caller's org → UPDATE
            * else → CREATE via the SAME resource create path (so project
              scoping via X-Project-Id and project_id are applied by the repo).
        → returns the created/updated resource row.

``GET /export/{kind}/{id}`` round-trips with ``POST /import``: exporting a
resource then re-importing the document is a no-op (the id is carried in
metadata, so import updates in place).

Auth + scoping
--------------
Org-scoped and authed exactly like the generic resources router: every request
requires a first-party Bearer token (``current_user``); the effective org is
resolved via ``resolve_org_id`` (honours ``X-Org-Id`` with membership check).
Cross-org rows return 404, never 403, so no information leaks.

Connectors are explicitly out of scope — there is no ``connector`` kind.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.auth.deps import current_user
from app.errors import AppError
from app.portability import (
    dump_envelope,
    get_handler,
    parse_document,
    row_fields_for_kind,
    slug_for_envelope,
    to_envelope,
    validate_spec_for_kind,
)
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

router = APIRouter(tags=["portability"])


_CONTENT_TYPES = {
    "yaml": "application/yaml",
    "json": "application/json",
}
_EXTENSIONS = {"yaml": "yaml", "json": "json"}


def _normalise_format(fmt: str | None) -> str:
    """Return a normalised serialisation format ('yaml' | 'json')."""
    f = (fmt or "yaml").lower()
    if f in ("yml",):
        f = "yaml"
    if f not in ("yaml", "json"):
        raise AppError(
            "validation_error",
            f"Unsupported format: {fmt!r}. Use 'yaml' or 'json'.",
            400,
        )
    return f


# ---------------------------------------------------------------------------
# GET /export/{kind}/{id}
# ---------------------------------------------------------------------------


@router.get("/export/{kind}/{id}")
async def export_resource(
    kind: str,
    id: str,
    request: Request,
    format: str = "yaml",
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Export a dashboard or query as a portable envelope document.

    Returns the serialised envelope with an ``application/yaml`` (or json)
    content-type and a downloadable ``Content-Disposition`` filename.

    Raises
    ------
    AppError("not_found", 404)
        Unknown kind, unknown id, or the row belongs to a different org.
    AppError("validation_error", 400)
        Unsupported ``format``.
    """
    handler = get_handler(kind)  # 404 for unknown / connector kinds
    fmt = _normalise_format(format)

    org_id = await resolve_org_id(str(user["id"]), repo, request)
    row = await repo.get(handler.resource, org_id, id)
    if row is None:
        raise AppError("not_found", f"{kind.capitalize()} not found.", 404)

    env = to_envelope(kind, row)
    body = dump_envelope(env, format=fmt)

    slug = slug_for_envelope(env)
    ext = _EXTENSIONS[fmt]
    headers = {
        "Content-Disposition": f'attachment; filename="{slug}.{ext}"',
    }
    return Response(
        content=body,
        media_type=_CONTENT_TYPES[fmt],
        headers=headers,
    )


# ---------------------------------------------------------------------------
# POST /import
# ---------------------------------------------------------------------------


async def _read_document(request: Request) -> str:
    """Return the request body as a text document (YAML or JSON).

    Accepts both raw text/YAML bodies and JSON bodies:
    - If the body is a JSON object, it is re-serialised to a JSON string (which
      is valid YAML) so the same parser handles both.
    - Otherwise the raw bytes are decoded as UTF-8 text.
    """
    raw = await request.body()
    if not raw:
        raise AppError("validation_error", "Empty request body.", 400)

    content_type = (request.headers.get("content-type") or "").lower()
    text = raw.decode("utf-8")

    if "application/json" in content_type:
        # Body is declared JSON — keep it as-is (valid YAML), but validate it
        # parses so we surface a clean 400 rather than a downstream YAML error.
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError(
                "validation_error",
                f"Invalid JSON body: {exc}",
                400,
            ) from exc
    return text


@router.post("/import", status_code=200)
async def import_resource(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Import (upsert) a dashboard or query from a portable envelope document.

    Body
    ----
    A YAML or JSON envelope document (``kind``/``apiVersion``/``metadata``/
    ``spec``).  Sent either as raw ``text/yaml`` / ``application/yaml`` or as
    ``application/json``.

    Upsert behaviour
    ----------------
    - ``metadata.id`` present AND owned by the caller's org → UPDATE in place.
    - otherwise → CREATE through the SAME resource create path (``repo.create``)
      so project scoping (X-Project-Id → project_id) is applied automatically.

    Returns
    -------
    dict
        The created or updated resource row.

    Raises
    ------
    AppError("validation_error", 400)
        Malformed document or spec validation failure.
    AppError("not_found", 404)
        Unknown kind.
    """
    text = await _read_document(request)
    env = parse_document(text)  # validates envelope shape; 404 unknown kind

    kind = env["kind"]
    handler = get_handler(kind)
    spec = env.get("spec") or {}

    # ── Reuse the kind's existing validator ────────────────────────────────
    issues = validate_spec_for_kind(kind, spec)
    if issues:
        raise AppError(
            "validation_error",
            "Spec validation failed: " + "; ".join(issues),
            400,
        )

    org_id = await resolve_org_id(str(user["id"]), repo, request)
    fields = row_fields_for_kind(kind, env)

    # ── UPSERT ─────────────────────────────────────────────────────────────
    meta = env.get("metadata") or {}
    existing_id = meta.get("id")
    if existing_id:
        existing = await repo.get(handler.resource, org_id, str(existing_id))
        if existing is not None:
            # Owned by caller's org → update in place (round-trip no-op).
            updated = await repo.update(
                handler.resource,
                org_id,
                str(existing_id),
                {"name": fields["name"], "config": fields["config"]},
            )
            if updated is None:
                # Lost a race (deleted between get and update) — treat as create.
                return await _create(repo, handler.resource, org_id, user, fields)
            return updated
        # id provided but NOT owned by this org (or doesn't exist) → create new.
        # We intentionally do NOT update a cross-org row (404-equivalent: it is
        # invisible to this org), and we do not error — import creates a fresh
        # copy in the caller's org.

    return await _create(repo, handler.resource, org_id, user, fields)


async def _create(
    repo: Repo,
    resource: str,
    org_id: str,
    user: dict[str, Any],
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Create a resource via the SAME create path the resources router uses.

    Calls ``repo.create`` with the standard ``(resource, org_id, created_by,
    name, config)`` signature.  Project scoping (project_id from X-Project-Id)
    is applied by the repo layer exactly as for normal resource creation.
    """
    return await repo.create(
        resource=resource,
        org_id=org_id,
        created_by=str(user["id"]),
        name=fields["name"],
        config=fields["config"],
    )


# ── Register on the shared api_router ─────────────────────────────────────
# IMPORTANT — route precedence: the generic resources router registers the
# catch-all ``/{resource}`` and ``/{resource}/{id}`` paths.  ``POST /import``
# (one path segment) would otherwise be shadowed by ``POST /{resource}``.
# FastAPI matches routes in registration order with no literal-over-param
# priority, so we PREPEND our routes onto ``api_router`` so the literal
# ``/import`` / ``/export/...`` paths win regardless of import order.
_before = len(api_router.routes)
api_router.include_router(router)
# Move the just-added portability routes (the tail) to the front of
# api_router so they are matched before the resources catch-all.
_new_routes = api_router.routes[_before:]
_old_routes = api_router.routes[:_before]
api_router.routes[:] = _new_routes + _old_routes
