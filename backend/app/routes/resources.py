"""Generic CRUD router for org-scoped resources.

Exposes five endpoints for each resource in the allowlist
(``datastores``, ``boards``, ``queries``, ``widgets``)::

    GET    /{resource}         — list all rows for the caller's org.
    POST   /{resource}         — create a new row; returns 201.
    GET    /{resource}/{id}    — fetch a single row (404 if wrong org or missing).
    PUT    /{resource}/{id}    — update name/config (404 if wrong org or missing).
    DELETE /{resource}/{id}    — delete the row; returns 204.

Authentication
--------------
Every endpoint requires a valid first-party Bearer token (``current_user``
dependency).  The caller's ``org_id`` is resolved via ``resolve_org_id``
which honours the ``X-Org-Id`` request header when present (the user must be
a member of that org — otherwise 403; or falls back to their default org).

Cross-org protection
--------------------
``get`` / ``update`` / ``delete`` return 404 (not 403) for rows that exist
but belong to a different org — no information leaks about other orgs'
resources.

Unknown resource names in the URL path also return 404.

This module attaches itself to the shared ``api_router`` at import time so
that ``main.py``'s ``include_router(api_router, prefix="/api/v1")`` picks
it up automatically.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.auth.scopes import author_kind, caller_claims, require_env_write
from app.db import fetchrow, fetch
from app.errors import AppError
from app.repos.base import VALID_RESOURCES
from app.repos.provider import get_repo, Repo
from app.routes import api_router

# ── Sub-router ────────────────────────────────────────────────────────────────
router = APIRouter(tags=["resources"])


# ── Pydantic request schemas ──────────────────────────────────────────────────

class CreateIn(BaseModel):
    """Request body for POST /{resource}."""

    name: str
    config: dict[str, Any] = {}


class UpdateIn(BaseModel):
    """Request body for PUT /{resource}/{id}."""

    name: str | None = None
    config: dict[str, Any] | None = None


# ── Org / project resolution helpers ──────────────────────────────────────────
# These live in the route-free ``app.routes._org`` module so that other routers
# can reuse them WITHOUT importing this module (which would trigger the greedy
# ``/{resource}`` catch-all to register ahead of their own prefixed routes).
# Re-exported here for backwards compatibility with existing call sites.
from app.routes._org import (  # noqa: E402
    _requested_project_id,
    _user_is_member,
    get_user_org,
    resolve_org_id,
    resolve_project_filter,
    resolve_project_id_for_create,
)


# ── Validation helper ─────────────────────────────────────────────────────────

def _require_valid_resource(resource: str) -> None:
    """Raise AppError 404 if *resource* is not in the allowlist."""
    if resource not in VALID_RESOURCES:
        raise AppError("not_found", f"Unknown resource: {resource!r}.", 404)


# ── Environment / version helpers ─────────────────────────────────────────────

# Versionable resources → polymorphic kind used by the environments store.
_RESOURCE_KIND: dict[str, str] = {"boards": "board", "queries": "query"}

#: Singular scope-resource name per URL resource (``boards`` → ``board``), used
#: when checking env-scoped write tokens like ``write:board:dev``.
_SCOPE_RESOURCE: dict[str, str] = {
    "boards": "board",
    "queries": "query",
    "datastores": "datastore",
    "widgets": "widget",
}


def _target_env(request: Request) -> str:
    """Return the environment a write targets.

    Honours ``?env=<key>`` (and the ``X-Target-Env`` header); defaults to
    ``"dev"`` — the unprotected working environment agents operate in.  A write
    with no explicit env is treated as a ``dev`` write, never a prod promotion.
    """
    env = (request.query_params.get("env") or "").strip()
    if not env:
        env = (request.headers.get("x-target-env") or "").strip()
    return env or "dev"


async def _env_is_protected(org_id: str, project_id: str | None, env_key: str) -> bool:
    """Best-effort: is *env_key* a protected env for this project?

    Falls back to the well-known ``prod`` key when the environments layer can't
    resolve the project (e.g. test doubles without a projects table) so the
    protected-env rule still bites for prod-targeted promotions.
    """
    if env_key == "prod":
        protected_default = True
    else:
        protected_default = False
    try:
        from app.environments.store import get_env_store  # noqa: PLC0415
        from app.routes._org import resolve_org_default_project_id  # noqa: PLC0415

        pid = project_id or await resolve_org_default_project_id(org_id)
        if not pid:
            return protected_default
        env = await get_env_store().get_environment_by_key(str(pid), env_key)
        if env is None:
            return protected_default
        return bool(env.get("protected"))
    except Exception:  # noqa: BLE001 — env layer optional; use the key default.
        return protected_default


async def _check_env_write(
    resource: str,
    claims: dict[str, Any],
    request: Request,
    org_id: str,
    project_id: str | None,
) -> None:
    """Enforce env-scoped write tokens BEFORE any DB mutation.

    Resolves the target env (``?env=`` / ``X-Target-Env``, default ``dev``) and
    calls :func:`require_env_write`.  Raises ``AppError`` 403 when the token's
    write scopes do not authorise this (resource, env) — e.g. a
    ``write:board:dev`` agent token targeting the protected ``prod`` env.
    No-op for full-access first-party callers (tokens with no ``write:`` scope).
    """
    env_key = _target_env(request)
    protected = await _env_is_protected(org_id, project_id, env_key)
    require_env_write(
        claims,
        _SCOPE_RESOURCE.get(resource, resource),
        env_key,
        protected=protected,
    )


def _is_versionable(resource: str) -> bool:
    """True for resources that carry environment-pinnable versions (boards/queries)."""
    return _RESOURCE_KIND.get(resource) is not None


def _config_is_unchanged(old_config: Any, new_config: Any) -> bool:
    """Config-hash idempotency: True when *new_config* matches *old_config*.

    Uses the same canonical-JSON SHA-256 (:func:`config_hash`) the env-version
    chain dedupes on, so a retried agent upsert with identical config is a clean
    no-op.  NOTE: this operates on the RESOURCE's own config — it deliberately
    does NOT mint an environment version.  Env versioning is driven by the
    explicit checkpoint flow (app/git/env_sync), not by every CRUD write; minting
    a version here would double-count against that chain.
    """
    if not isinstance(old_config, dict) or not isinstance(new_config, dict):
        return False
    from app.environments.store import config_hash  # noqa: PLC0415

    try:
        return config_hash(old_config) == config_hash(new_config)
    except Exception:  # noqa: BLE001 — never fail a write on a hashing edge case
        return False


async def _apply_env_resolution(
    resource: str, row: dict[str, Any], org_id: str, env_key: str
) -> None:
    """Resolve ``?env=<key>`` for a get-one response (in place).

    When the resource's project has an environment named *env_key* with a
    pinned version for this row, the response ``config`` is replaced by the
    pinned snapshot and ``resolved_version: {id, version}`` is added.  When no
    pointer exists the draft is returned unchanged with
    ``resolved_version: null``.
    """
    kind = _RESOURCE_KIND.get(resource)
    if kind is None:
        return
    from app.environments.store import get_env_store  # noqa: PLC0415
    from app.routes._org import resolve_org_default_project_id  # noqa: PLC0415

    row["resolved_version"] = None
    project_id = row.get("project_id") or await resolve_org_default_project_id(org_id)
    if not project_id:
        return
    env_store = get_env_store()
    env = await env_store.get_environment_by_key(str(project_id), env_key)
    if env is None:
        return
    pointer = await env_store.get_pointer(kind, str(row["id"]), env["id"])
    if pointer is None:
        return
    version = await env_store.get_version_by_id(pointer["version_id"])
    if version is None:
        return
    row["config"] = version["config"]
    row["resolved_version"] = {"id": version["id"], "version": version["version"]}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/{resource}")
async def list_resources(
    resource: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all rows for the caller's org.

    Honoures the ``X-Org-Id`` header to switch org context (membership checked).

    Returns
    -------
    list[dict]
        Possibly empty list of resource rows.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_filter(org_id, request)
    rows = await repo.list(resource, org_id, project_id)

    # Strict-visibility support: versionable rows (boards/queries) always carry
    # ``pinned_envs`` — the env keys that have a pinned version — so the UI can
    # render "not in <env>" badges when the active env is protected.
    kind = _RESOURCE_KIND.get(resource)
    if kind is not None:
        from app.environments.store import attach_pinned_envs  # noqa: PLC0415

        await attach_pinned_envs(kind, rows)
    return rows


@router.post("/{resource}", status_code=201, dependencies=[Depends(require_writer)])
async def create_resource(
    resource: str,
    body: CreateIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    claims: dict[str, Any] = Depends(caller_claims),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new resource row.

    Honoures the ``X-Org-Id`` header to switch org context (membership checked).

    Governance (agent sandbox)
    --------------------------
    Env-scoped write tokens (e.g. ``write:board:dev``) are restricted to their
    environment and may never target a protected/prod env (403).  The new
    version is attributed to an AI agent or human via the token identity.

    Returns
    -------
    dict
        The newly created row (includes ``id``, ``created_at``, etc.).
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_id_for_create(org_id, request)

    # Env-scoped write enforcement (agent sandbox) — before any DB mutation.
    # Covers every resource kind, versionable or not.
    await _check_env_write(resource, claims, request, org_id, project_id)

    row = await repo.create(
        resource=resource,
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        config=body.config,
        project_id=project_id,
    )

    # Versionable resources (boards/queries): surface AI-authorship attribution
    # on the response so callers/UI can show "AI-authored vs human".  (Persisting
    # attribution onto the env-version chain is done at CHECKPOINT time, not here
    # — minting a version on every CRUD write double-counts the checkpoint chain.)
    if _is_versionable(resource):
        row["author_kind"] = author_kind(claims)
    return row


@router.get("/{resource}/{id}")
async def get_resource(
    resource: str,
    id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a single resource row.

    Returns 404 if the row does not exist OR belongs to a different org —
    no cross-org information leaks.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    row = await repo.get(resource, org_id, id)
    if row is None:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)

    # ``?env=<key>``: serve the version pinned to that environment (boards /
    # queries only); draft + resolved_version=null when nothing is pinned.
    env_key = (request.query_params.get("env") or "").strip()
    if env_key:
        await _apply_env_resolution(resource, row, org_id, env_key)
    return row


@router.put("/{resource}/{id}", dependencies=[Depends(require_writer)])
async def update_resource(
    resource: str,
    id: str,
    body: UpdateIn,
    request: Request,
    response: Response,
    user: dict[str, Any] = Depends(current_user),
    claims: dict[str, Any] = Depends(caller_claims),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update (upsert) a resource row's ``name`` and/or ``config``.

    Returns 404 if the row does not exist OR belongs to a different org.

    Governance (agent sandbox)
    --------------------------
    - Env-scoped write tokens may only target their own env; promotion to a
      protected/prod env is rejected (403).
    - The recorded version is attributed to an AI agent or human.
    - **Idempotency**: a re-upsert with identical ``config`` is a clean no-op —
      the existing version is returned (no new version) and the response is
      stamped ``deduped=True``.  Retried agent upserts therefore add no noise.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)

    # Env-scoped write enforcement (agent sandbox) — before any DB mutation.
    await _check_env_write(resource, claims, request, org_id, None)

    # Config-hash idempotency (agent sandbox): a retried upsert with identical
    # config is a clean no-op — return the existing row stamped deduped=True and
    # skip the DB write so retries add no noise.  Checked against the resource's
    # current config (NOT the env-version chain — see _config_is_unchanged).
    if body.config is not None and _is_versionable(resource):
        existing = await repo.get(resource, org_id, id)
        if existing is None:
            raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)
        name_unchanged = body.name is None or body.name == existing.get("name")
        if name_unchanged and _config_is_unchanged(existing.get("config"), body.config):
            existing["author_kind"] = author_kind(claims)
            existing["deduped"] = True
            response.status_code = 200
            return existing

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.config is not None:
        fields["config"] = body.config

    row = await repo.update(resource, org_id, id, fields)
    if row is None:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)

    # Surface AI-authorship attribution on the response for versionable kinds.
    if _is_versionable(resource):
        row["author_kind"] = author_kind(claims)
        row["deduped"] = False
    return row


@router.delete("/{resource}/{id}", status_code=204, dependencies=[Depends(require_writer)])
async def delete_resource(
    resource: str,
    id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a resource row.

    Returns 204 on success, 404 if not found or wrong org.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    deleted = await repo.delete(resource, org_id, id)
    if not deleted:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)

    # Best-effort cleanup of versions + environment pointers (polymorphic
    # tables — no FK cascade from the resource row).
    kind = _RESOURCE_KIND.get(resource)
    if kind is not None:
        try:
            from app.environments.store import get_env_store  # noqa: PLC0415

            await get_env_store().delete_resource_data(kind, id)
        except Exception:  # noqa: BLE001 — never fail the delete on cleanup
            pass
    return Response(status_code=204)


# ── Register on the shared api_router ─────────────────────────────────────────
api_router.include_router(router)
