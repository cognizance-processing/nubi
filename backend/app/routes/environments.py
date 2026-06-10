"""Environments + resource versioning endpoints.

Endpoints
---------
GET    /projects/{project_id}/environments              -> [env]  (lazily ensures dev+prod)
POST   /projects/{project_id}/environments {key, name, git_branch?, from_branch?} -> 201 env
PATCH  /environments/{env_id} {name?, is_default?, protected?, position?} -> env
DELETE /environments/{env_id}                           -> 204 (409 if default/protected)
GET    /versions/{kind}/{resource_id}                   -> {versions: [...], pointers: [...]}
POST   /versions/{kind}/{resource_id} {message?, env_key?='dev'} -> 201 checkpoint
GET    /versions/{kind}/{resource_id}/{version}         -> full version incl config
POST   /versions/{kind}/{resource_id}/{version}/restore -> updated draft
POST   /environments/promote {kind, resource_id, from_env, to_env, include_dependencies?}
POST   /environments/{env_id}/git/push {message?}       -> commit env pins to its branch (+remote)
POST   /environments/{env_id}/git/pull {strategy?}      -> sync env from its branch (409 on divergence)
GET    /projects/{project_id}/git/graph                 -> {branches: [...]} commit graph per env branch

Git integration (DECISION 5 — service layer in ``app.git.env_sync``)
--------------------------------------------------------------------
Every environment is bound to a branch (``git_branch``) in the project's
workspace repo.  Checkpoints commit to the env's branch and stamp
``git_commit_sha``; promote merges branches best-effort (a conflict is
reported as ``git_conflict`` WITHOUT rolling back pointers).  The whole git
layer is optional: when no repo/remote exists every endpoint degrades to
DB-only behaviour with ``warning`` fields — never a 5xx.

``kind`` is one of ``flow`` | ``board`` | ``query``.  Flows are read through
the flow store (``app.flows.store``); boards/queries through the generic Repo.

Authentication / scoping
------------------------
Every endpoint requires a valid first-party Bearer token (``current_user``)
and resolves the effective org via ``resolve_org_id`` (``X-Org-Id`` aware).
Projects, environments, and resources are all validated to belong to that
org; cross-org access returns 404 (no information leak).

This module attaches itself to the shared ``api_router`` at import time and
MUST be imported in ``main.py`` BEFORE ``app.routes.resources`` so its
concrete ``/environments`` + ``/versions`` paths register ahead of the
generic ``/{resource}`` catch-all.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.environments.store import get_env_store
from app.errors import AppError
from app.flows.store import get_flow_store
from app.git import env_sync
from app.repos import projects as projects_repo
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_default_project_id, resolve_org_id

# ── Sub-router ────────────────────────────────────────────────────────────────
router = APIRouter(tags=["environments"])

#: Valid polymorphic resource kinds → generic-repo resource names.
VALID_KINDS: frozenset[str] = frozenset({"flow", "board", "query"})
_KIND_RESOURCE: dict[str, str] = {"board": "boards", "query": "queries"}


# ── Pydantic request schemas ──────────────────────────────────────────────────


class CreateEnvIn(BaseModel):
    """Request body for POST /projects/{project_id}/environments.

    ``git_branch`` overrides the creation default ('main' for key='prod',
    else the key).  ``from_branch`` seeds the new environment from an
    existing branch in the project's workspace repo (best-effort: a missing
    repo/branch leaves the env empty with a ``warning`` in the response).
    """

    key: str
    name: str
    git_branch: str | None = None
    from_branch: str | None = None


class UpdateEnvIn(BaseModel):
    """Request body for PATCH /environments/{env_id}."""

    name: str | None = None
    is_default: bool | None = None
    protected: bool | None = None
    position: int | None = None


class CheckpointIn(BaseModel):
    """Request body for POST /versions/{kind}/{resource_id}."""

    message: str | None = None
    env_key: str = "dev"


class PromoteIn(BaseModel):
    """Request body for POST /environments/promote."""

    kind: str
    resource_id: str
    from_env: str
    to_env: str
    include_dependencies: bool = True


class EnvGitPushIn(BaseModel):
    """Request body for POST /environments/{env_id}/git/push."""

    message: str | None = None


class EnvGitPullIn(BaseModel):
    """Request body for POST /environments/{env_id}/git/pull.

    ``strategy`` resolves a diverged branch: ``'take_branch'`` imports the
    branch state into the env; ``'take_env'`` overwrites the branch from the
    env's pinned state (push --force-with-lease semantics).
    """

    strategy: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_kind(kind: str) -> None:
    """Raise AppError 404 if *kind* is not a versionable resource kind."""
    if kind not in VALID_KINDS:
        raise AppError("not_found", f"Unknown kind: {kind!r}.", 404)


async def _require_project(project_id: str, org_id: str) -> None:
    """Raise AppError 404 unless *project_id* belongs to *org_id*."""
    if not await projects_repo.project_belongs_to_org(project_id, org_id):
        raise AppError("not_found", "Project not found.", 404)


async def _require_resource(
    kind: str, resource_id: str, org_id: str, repo: Repo
) -> dict[str, Any]:
    """Return the resource row (flow dict or board/query row); 404 cross-org."""
    if kind == "flow":
        flow = await get_flow_store().get_flow(resource_id)
        if flow is None or str(flow["org_id"]) != str(org_id):
            raise AppError("not_found", "Flow not found.", 404)
        return flow
    row = await repo.get(_KIND_RESOURCE[kind], org_id, resource_id)
    if row is None:
        raise AppError("not_found", f"{kind.capitalize()} not found.", 404)
    return row


def _draft_config(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """Return the resource's current draft definition (spec or config)."""
    return (row.get("spec") if kind == "flow" else row.get("config")) or {}


async def _resource_project_id(row: dict[str, Any], org_id: str) -> str | None:
    """Return the resource's project id, falling back to the org default."""
    pid = row.get("project_id")
    if pid:
        return str(pid)
    return await resolve_org_default_project_id(org_id)


async def _require_env(env_id: str, org_id: str, env_store: Any) -> dict[str, Any]:
    """Return the environment if it exists in one of *org_id*'s projects."""
    env = await env_store.get_environment(env_id)
    if env is None:
        raise AppError("not_found", "Environment not found.", 404)
    if not await projects_repo.project_belongs_to_org(env["project_id"], org_id):
        raise AppError("not_found", "Environment not found.", 404)
    return env


def _collect_query_ids(node: Any) -> set[str]:
    """Recursively collect string values stored under query_id/queryId keys."""
    ids: set[str] = set()
    if isinstance(node, dict):
        for key, val in node.items():
            if key in ("query_id", "queryId") and isinstance(val, str) and val:
                ids.add(val)
            else:
                ids |= _collect_query_ids(val)
    elif isinstance(node, list):
        for item in node:
            ids |= _collect_query_ids(item)
    return ids


# ── Environment CRUD ──────────────────────────────────────────────────────────


@router.get("/projects/{project_id}/environments")
async def list_environments(
    project_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List the project's environments, lazily ensuring the dev+prod pair."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    await _require_project(project_id, org_id)
    return await get_env_store().ensure_project_envs(project_id)


@router.post(
    "/projects/{project_id}/environments",
    status_code=201,
    dependencies=[Depends(require_writer)],
)
async def create_environment(
    project_id: str,
    body: CreateEnvIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new environment in the project (key slugified, unique).

    ``git_branch`` defaults per :func:`app.environments.store.default_git_branch`.
    With ``from_branch`` the branch's known files (queries/, dashboards/,
    flows/) are imported as pinned versions (best-effort; a missing repo or
    branch adds a ``warning`` field and leaves the env empty).
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    await _require_project(project_id, org_id)

    key = projects_repo.slugify(body.key)
    name = (body.name or "").strip() or key
    env_store = get_env_store()

    existing = await env_store.get_environment_by_key(project_id, key)
    if existing is not None:
        raise AppError(
            "conflict",
            f"An environment with key {key!r} already exists in this project.",
            409,
        )

    envs = await env_store.list_environments(project_id)
    position = max((int(e.get("position", 0)) for e in envs), default=-1) + 1
    env = await env_store.create_environment(
        project_id, key, name, position=position,
        git_branch=(body.git_branch or "").strip() or None,
    )

    # Optionally seed the new env from an existing branch (best-effort).
    if body.from_branch:
        result = await env_sync.import_branch_into_env(
            org_id=org_id,
            project_id=project_id,
            env=env,
            branch=body.from_branch.strip(),
            user_id=str(user["id"]),
        )
        env = {**env, **result}
    return env


@router.patch("/environments/{env_id}", dependencies=[Depends(require_writer)])
async def update_environment(
    env_id: str,
    body: UpdateEnvIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update an environment's name / is_default / protected / position.

    Setting ``is_default=true`` clears ``is_default`` on the project's other
    environments (exactly one default per project).
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    env_store = get_env_store()
    env = await _require_env(env_id, org_id, env_store)

    if body.is_default is True:
        for other in await env_store.list_environments(env["project_id"]):
            if str(other["id"]) != str(env["id"]) and other.get("is_default"):
                await env_store.update_environment(other["id"], {"is_default": False})

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.is_default is not None:
        fields["is_default"] = body.is_default
    if body.protected is not None:
        fields["protected"] = body.protected
    if body.position is not None:
        fields["position"] = body.position

    updated = await env_store.update_environment(env_id, fields)
    if updated is None:
        raise AppError("not_found", "Environment not found.", 404)
    return updated


@router.delete(
    "/environments/{env_id}",
    status_code=204,
    dependencies=[Depends(require_writer)],
)
async def delete_environment(
    env_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete an environment.  Default / protected environments refuse (409)."""
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    env_store = get_env_store()
    env = await _require_env(env_id, org_id, env_store)

    if env.get("is_default") or env.get("protected"):
        raise AppError(
            "conflict",
            "Default or protected environments cannot be deleted.",
            409,
        )

    await env_store.delete_environment(env_id)
    return Response(status_code=204)


# ── Versions ──────────────────────────────────────────────────────────────────


@router.get("/versions/{kind}/{resource_id}")
async def list_versions(
    kind: str,
    resource_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return the resource's version history + environment pointers."""
    _require_kind(kind)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    await _require_resource(kind, resource_id, org_id, repo)

    env_store = get_env_store()
    return {
        "versions": await env_store.list_versions(kind, resource_id),
        "pointers": await env_store.list_pointers(kind, resource_id),
    }


@router.post(
    "/versions/{kind}/{resource_id}",
    status_code=201,
    dependencies=[Depends(require_writer)],
)
async def checkpoint(
    kind: str,
    resource_id: str,
    request: Request,
    body: CheckpointIn = CheckpointIn(),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Checkpoint the resource's current draft definition as a new version.

    Snapshots the draft (flow spec / board config / query config), deduping
    when nothing changed, then points ``env_key``'s environment at it.
    Protected environments only change via promote → 409.
    """
    _require_kind(kind)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    row = await _require_resource(kind, resource_id, org_id, repo)

    env_store = get_env_store()
    project_id = await _resource_project_id(row, org_id)

    version = await env_store.create_version(
        org_id=org_id,
        project_id=project_id,
        kind=kind,
        resource_id=resource_id,
        config=_draft_config(kind, row),
        created_by=str(user["id"]),
        message=body.message,
    )

    # Point env_key's environment at the new version (never a protected env).
    if project_id:
        await env_store.ensure_project_envs(project_id)
        env = await env_store.get_environment_by_key(project_id, body.env_key)
        if env is None:
            raise AppError(
                "not_found", f"Environment {body.env_key!r} not found.", 404
            )
        if env.get("protected"):
            raise AppError(
                "conflict",
                f"Environment {body.env_key!r} is protected; "
                "it only changes via promote.",
                409,
            )
        await env_store.set_pointer(
            kind, resource_id, env["id"], version["id"], promoted_by=str(user["id"])
        )

        # Best-effort: commit the serialized resource to the env's branch and
        # stamp git_commit_sha on the version.  Never fails the checkpoint.
        git_info = await env_sync.commit_checkpoint(
            org_id=org_id,
            project_id=project_id,
            env=env,
            kind=kind,
            resource_id=resource_id,
            name=str(row.get("name") or ""),
            version=version,
            user_message=body.message,
        )
        version.update(git_info)

    return version


@router.get("/versions/{kind}/{resource_id}/{version}")
async def get_version(
    kind: str,
    resource_id: str,
    version: int,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return one full version (including its config snapshot)."""
    _require_kind(kind)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    await _require_resource(kind, resource_id, org_id, repo)

    found = await get_env_store().get_version(kind, resource_id, version)
    if found is None:
        raise AppError("not_found", "Version not found.", 404)
    return found


@router.post(
    "/versions/{kind}/{resource_id}/{version}/restore",
    dependencies=[Depends(require_writer)],
)
async def restore_version(
    kind: str,
    resource_id: str,
    version: int,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Write a version's config back into the resource's draft definition."""
    _require_kind(kind)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    await _require_resource(kind, resource_id, org_id, repo)

    found = await get_env_store().get_version(kind, resource_id, version)
    if found is None:
        raise AppError("not_found", "Version not found.", 404)

    if kind == "flow":
        updated = await get_flow_store().update_flow(
            resource_id, {"spec": found["config"]}
        )
    else:
        updated = await repo.update(
            _KIND_RESOURCE[kind], org_id, resource_id, {"config": found["config"]}
        )
    if updated is None:
        raise AppError("not_found", f"{kind.capitalize()} not found.", 404)
    return updated


# ── Promote ───────────────────────────────────────────────────────────────────


async def _copy_flow_watermarks(
    flow_id: str, pinned_spec: dict[str, Any], from_env: str, to_env: str
) -> None:
    """Best-effort copy of incremental watermarks for materialize-type tasks."""
    flow_store = get_flow_store()
    for task in pinned_spec.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        cfg = task.get("config") or {}
        is_materialize = task.get("kind") == "materialize" or (
            isinstance(cfg, dict) and cfg.get("materialized")
        )
        if not is_materialize:
            continue
        try:
            await flow_store.copy_watermark(
                flow_id, str(task.get("key", "")), from_env, to_env
            )
        except Exception:  # noqa: BLE001 — never fail the promote on watermarks
            pass


@router.post("/environments/promote", dependencies=[Depends(require_writer)])
async def promote(
    body: PromoteIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Copy a resource's ``from_env`` pointer to ``to_env`` (env keys).

    For flows, incremental watermarks of materialize-type tasks are copied
    best-effort.  For boards with ``include_dependencies`` (default), every
    query referenced by the pinned board config that has a ``from_env``
    pointer is promoted too.  Returns ``{promoted: [...]}`` plus best-effort
    git fields: ``git_merge`` (the from-env branch merged into the to-env
    branch, ff preferred) or ``git_conflict: {files, from_sha, to_sha}``
    (pointers are NOT rolled back) or ``git_warning``.
    """
    _require_kind(body.kind)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    row = await _require_resource(body.kind, body.resource_id, org_id, repo)

    env_store = get_env_store()
    project_id = await _resource_project_id(row, org_id)
    if not project_id:
        raise AppError("not_found", "Project not found for this resource.", 404)

    await env_store.ensure_project_envs(project_id)
    from_env = await env_store.get_environment_by_key(project_id, body.from_env)
    if from_env is None:
        raise AppError("not_found", f"Environment {body.from_env!r} not found.", 404)
    to_env = await env_store.get_environment_by_key(project_id, body.to_env)
    if to_env is None:
        raise AppError("not_found", f"Environment {body.to_env!r} not found.", 404)

    pointer = await env_store.get_pointer(body.kind, body.resource_id, from_env["id"])
    if pointer is None:
        raise AppError(
            "not_found",
            f"No {body.from_env!r} version pinned for this {body.kind}.",
            404,
        )

    await env_store.set_pointer(
        body.kind,
        body.resource_id,
        to_env["id"],
        pointer["version_id"],
        promoted_by=str(user["id"]),
    )
    pinned = await env_store.get_version_by_id(pointer["version_id"])

    promoted: list[dict[str, Any]] = [
        {
            "kind": body.kind,
            "resource_id": str(body.resource_id),
            "version_id": pointer["version_id"],
            "version": (pinned or {}).get("version"),
            "from_env": body.from_env,
            "to_env": body.to_env,
        }
    ]

    # ── Flow: best-effort watermark copy for materialize-type tasks ──────────
    if body.kind == "flow":
        try:
            await _copy_flow_watermarks(
                str(body.resource_id),
                (pinned or {}).get("config") or {},
                body.from_env,
                body.to_env,
            )
        except Exception:  # noqa: BLE001 — never fail the promote on watermarks
            pass

    # ── Board: promote referenced queries too ────────────────────────────────
    if body.kind == "board" and body.include_dependencies:
        for query_id in sorted(_collect_query_ids((pinned or {}).get("config") or {})):
            query_row = await repo.get("queries", org_id, query_id)
            if query_row is None:
                continue
            q_project = await _resource_project_id(query_row, org_id)
            if not q_project:
                continue
            q_from = await env_store.get_environment_by_key(q_project, body.from_env)
            q_to = await env_store.get_environment_by_key(q_project, body.to_env)
            if q_from is None or q_to is None:
                continue
            q_pointer = await env_store.get_pointer("query", query_id, q_from["id"])
            if q_pointer is None:
                continue
            await env_store.set_pointer(
                "query",
                query_id,
                q_to["id"],
                q_pointer["version_id"],
                promoted_by=str(user["id"]),
            )
            q_version = await env_store.get_version_by_id(q_pointer["version_id"])
            promoted.append(
                {
                    "kind": "query",
                    "resource_id": str(query_id),
                    "version_id": q_pointer["version_id"],
                    "version": (q_version or {}).get("version"),
                    "from_env": body.from_env,
                    "to_env": body.to_env,
                }
            )

    # ── Best-effort git merge: from-env branch → to-env branch ──────────────
    # A conflict (or absent git layer) never rolls back the pointer copies.
    git_info = await env_sync.merge_env_branches(
        org_id=org_id, project_id=project_id, from_env=from_env, to_env=to_env
    )

    return {"promoted": promoted, **git_info}


# ── Environment ⇄ git branch sync (DECISION 5) ───────────────────────────────


@router.post(
    "/environments/{env_id}/git/push",
    dependencies=[Depends(require_writer)],
)
async def push_environment_git(
    env_id: str,
    request: Request,
    body: EnvGitPushIn = EnvGitPushIn(),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Serialize ALL resources pinned in the env to its branch (one commit).

    Updates ``last_synced_sha`` and pushes the branch to the project's remote
    when one is bound (``projects.git``).  Fully best-effort: an absent git
    layer degrades to ``{committed: false, warnings: [...]}`` — never a 5xx.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    env = await _require_env(env_id, org_id, get_env_store())
    return await env_sync.push_env(
        org_id=org_id,
        project_id=str(env["project_id"]),
        env=env,
        repo=repo,
        message=body.message,
    )


@router.post(
    "/environments/{env_id}/git/pull",
    dependencies=[Depends(require_writer)],
)
async def pull_environment_git(
    env_id: str,
    request: Request,
    body: EnvGitPullIn = EnvGitPullIn(),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Any:
    """Sync the environment from its branch (fetching the remote when bound).

    - branch head == ``last_synced_sha`` → no-op (``up_to_date: true``);
    - fast-forwardable from the last sync → changed files become new pinned
      versions (parent = current pin) and ``last_synced_sha`` advances;
    - DIVERGED → 409 ``{diverged: true, files, env_sha, branch_sha}`` unless
      ``strategy`` is ``'take_branch'`` or ``'take_env'`` (force-with-lease
      semantics: overwrite the branch from env state);
    - no repo / branch → ``{pulled: false, warning}`` (never a 5xx).
    """
    if body.strategy is not None and body.strategy not in env_sync.PULL_STRATEGIES:
        raise AppError(
            "invalid_strategy",
            "strategy must be 'take_branch' or 'take_env'.",
            400,
        )
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    env = await _require_env(env_id, org_id, get_env_store())
    result = await env_sync.pull_env(
        org_id=org_id,
        project_id=str(env["project_id"]),
        env=env,
        repo=repo,
        user_id=str(user["id"]),
        strategy=body.strategy,
    )
    if result.get("diverged"):
        return JSONResponse(status_code=409, content=result)
    return result


@router.get("/projects/{project_id}/git/graph")
async def get_project_git_graph(
    project_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return the commit graph for every env-bound branch in the project.

    ``{branches: [{branch, env_key, head_sha, commits: [{sha, parents,
    message, author, date}]}]}`` (≤100 commits per branch); an empty
    structure when the project has no workspace repo yet.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    await _require_project(project_id, org_id)
    return await env_sync.project_git_graph(org_id=org_id, project_id=project_id)


# ── Register on the shared api_router ─────────────────────────────────────────
api_router.include_router(router)
