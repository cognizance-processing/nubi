"""Git sync endpoints for queries and dashboards — M20-A.

Endpoints
---------
POST   /git/sync     — serialize and commit all registered queries + boards for
                       the caller's org.
GET    /git/history  — return commit history for the org's workspace (optionally
                       filtered to a single file path).
POST   /git/restore  — return the content of a file at a historical commit SHA.

Authentication
--------------
All endpoints require a valid first-party Bearer token (``current_user``
dependency).  Operations are org-scoped: each org gets its own subdirectory
inside the workspace (``<workspace>/<org_id>/``).

The ``APIRouter`` is exported as ``router`` — the orchestrator wires it into
``main.py`` by importing this module and calling
``api_router.include_router(router)`` (or via a bare import that triggers the
bottom-of-file ``api_router.include_router(router)`` call).

No network calls are made.  Remote push (GitHub-App / deploy-key) is stubbed
via ``RemoteAuth`` and deferred to a future milestone.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer_default
from app.config import get_settings
from app.db import fetchrow
from app.errors import AppError
from app.git.remotes import make_provider
from app.git.sync import (
    GitSync,
    build_manifest,
    serialize_envelope,
    serialize_resource,
)
from app.portability import (
    KIND_REGISTRY,
    parse_document,
    row_fields_for_kind,
    to_envelope,
    validate_spec_for_kind,
)
from app.queries.registry import get_query_registry
from app.repos import projects as projects_repo
from app.repos.provider import Repo, get_repo
from app.routes import api_router

# Project git tokens are stored in the connector secret store keyed by the
# project's own uuid (the project_id doubles as the secret "datastore" id — a
# distinct uuid space from real datastores). The binding on projects.git stores
# only token_ref=project_id, never the token itself.
from app.connectors.secret_store import get_secret_store  # noqa: E402

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/git", tags=["git"])


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def _workspace_root() -> Path:
    """Return the root workspace directory.

    Checks ``NUBI_GIT_WORKSPACE`` env var first; falls back to
    ``<tempdir>/nubi_git_workspace``.
    """
    env_val = os.environ.get("NUBI_GIT_WORKSPACE", "")
    if env_val:
        return Path(env_val)
    return Path(tempfile.gettempdir()) / "nubi_git_workspace"


def _org_repo(org_id: str) -> GitSync:
    """Return a ``GitSync`` instance scoped to *org_id*."""
    repo_dir = _workspace_root() / str(org_id)
    return GitSync(repo_dir)


# ---------------------------------------------------------------------------
# Org resolution helper (mirrors routes/jobs.py to avoid circular imports)
# ---------------------------------------------------------------------------

async def _get_user_org(user_id: str, repo: Repo) -> str:
    """Return the org_id for the user's first membership."""
    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)  # type: ignore[attr-defined]
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
# Pydantic schemas
# ---------------------------------------------------------------------------

class SyncIn(BaseModel):
    """Optional body for POST /git/sync."""

    message: str = "chore: sync resources"
    author: str = "Nubi Git Sync <nubi-git-sync@nubi.local>"


class SyncOut(BaseModel):
    """Response for POST /git/sync."""

    sha: str
    files_committed: int
    message: str


class HistoryEntry(BaseModel):
    """A single entry in the commit history."""

    sha: str
    message: str
    author: str
    ts: str


class RestoreIn(BaseModel):
    """Request body for POST /git/restore."""

    path: str
    sha: str


class RestoreOut(BaseModel):
    """Response for POST /git/restore."""

    path: str
    sha: str
    content: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/sync", response_model=SyncOut, dependencies=[Depends(require_writer_default)])
async def sync_resources(
    body: SyncIn = SyncIn(),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Serialize and commit all registered queries and boards for the org.

    Registered queries are sourced from the ``QueryRegistry`` singleton.
    Boards are loaded from the ``boards`` resource table via the repo.

    Returns
    -------
    dict
        ``{sha, files_committed, message}``
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    git_sync = _org_repo(org_id)

    items: list[dict[str, str]] = []

    # ── Registered queries ─────────────────────────────────────────────────
    registry = get_query_registry()
    for rq in registry.all():
        # Build a resource-like dict from the RegisteredQuery
        rq_resource: dict[str, Any] = {
            "id": rq.id,
            "name": rq.name,
            "sql": rq.sql,
            "params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "default": p.default,
                    "required": p.required,
                    "options_query_id": p.options_query_id,
                }
                for p in rq.params_as_list()
            ],
            "required_scope": rq.required_scope,
            "config": {},
        }
        items.extend(serialize_resource("query", rq_resource))

    # ── Boards (from repo) ────────────────────────────────────────────────
    try:
        boards = await repo.list("boards", org_id)
        for board in boards:
            items.extend(serialize_resource("dashboard", board))
    except AppError:
        # If repo isn't seeded / boards don't exist yet, skip gracefully
        pass

    if not items:
        # Nothing to commit; return a synthetic no-op response
        return {
            "sha": "",
            "files_committed": 0,
            "message": body.message,
        }

    sha = git_sync.commit_resources(
        items=items,
        message=body.message,
        author=body.author,
    )

    return {
        "sha": sha,
        "files_committed": len(items),
        "message": body.message,
    }


@router.get("/history", response_model=list[HistoryEntry])
async def get_history(
    path: str | None = Query(default=None, description="Optional file path to filter history."),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, str]]:
    """Return commit history for the org's git workspace.

    Parameters
    ----------
    path:
        Optional relative file path (e.g. ``queries/demo_all.sql``).  When
        supplied, only commits that touched that path are returned.

    Returns
    -------
    list[dict]
        Ordered list of ``{sha, message, author, ts}`` dicts (most recent
        first).
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    git_sync = _org_repo(org_id)
    return git_sync.history(path=path)


@router.post("/restore", response_model=RestoreOut, dependencies=[Depends(require_writer_default)])
async def restore_file(
    body: RestoreIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Restore the contents of a file at a historical commit.

    Parameters
    ----------
    body.path:
        Relative file path inside the workspace (e.g. ``dashboards/abc.json``).
    body.sha:
        Commit SHA to read from.

    Returns
    -------
    dict
        ``{path, sha, content}``

    Raises
    ------
    AppError("not_found", 404)
        If the path or SHA does not exist.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    git_sync = _org_repo(org_id)

    try:
        content = git_sync.restore(body.path, body.sha)
    except (RuntimeError, Exception) as exc:
        raise AppError("not_found", f"Could not restore {body.path!r} at {body.sha!r}: {exc}", 404) from exc

    return {
        "path": body.path,
        "sha": body.sha,
        "content": content,
    }


# NOTE: The org-level ``POST /git/push`` (NullRemote / GIT_REMOTE_PROVIDER) was
# superseded by the project-scoped ``POST /git/push`` defined below. The legacy
# org-level local-only flow remains available via ``POST /git/sync``.


# ===========================================================================
# Project-scoped GitHub / GitLab sync — M20-C
# ===========================================================================
#
# These endpoints bind a *project* to a remote repo (GitHub or GitLab) using a
# PAT / deploy token (stored in the secret store, referenced from project.git).
# The DB stays canonical; git is the mirror.
#
#   POST /git/connect  {project_id, provider, repo_url, branch, base_path, token}
#   GET  /git/status   ?project_id=...
#   POST /git/push     {project_id, message?}     (project body → project sync)
#   POST /git/pull     {project_id}
#
# Connectors are NEVER serialized (product decision).
# ---------------------------------------------------------------------------


class ConnectIn(BaseModel):
    """Request body for POST /git/connect."""

    project_id: str
    provider: str  # 'github' | 'gitlab'
    repo_url: str
    branch: str = "main"
    base_path: str = ""
    token: str


class GitBindingOut(BaseModel):
    """A project's git binding (token is NEVER returned — only token_ref)."""

    provider: str
    repo_url: str
    branch: str
    base_path: str
    token_ref: str | None = None
    connected: bool = True


class StatusOut(BaseModel):
    """Response for GET /git/status."""

    connected: bool
    binding: GitBindingOut | None = None
    last_sync: dict[str, Any] | None = None


class ProjectPushIn(BaseModel):
    """Request body for POST /git/push (project-scoped)."""

    project_id: str
    message: str = "chore: sync nubi resources"
    open_pr: bool = False


class ProjectPushOut(BaseModel):
    sha: str
    committed: bool
    pushed: bool
    files: int
    change_request: dict[str, Any] | None = None


class PullIn(BaseModel):
    """Request body for POST /git/pull."""

    project_id: str


class PullOut(BaseModel):
    imported: int
    kinds: dict[str, int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_repo_dir(org_id: str, project_id: str) -> Path:
    """Return the local working-clone dir for a project's git mirror."""
    return _workspace_root() / str(org_id) / "projects" / str(project_id)


async def _load_binding(org_id: str, project_id: str) -> dict[str, Any] | None:
    """Return the project's ``git`` binding dict, or ``None`` if not connected."""
    project = await projects_repo.get_project(org_id, project_id)
    if project is None:
        raise AppError("not_found", "Project not found.", 404)
    git = project.get("git")
    if not git or not isinstance(git, dict) or not git.get("provider"):
        return None
    return git


async def _provider_for_project(org_id: str, project_id: str):
    """Build a configured RemoteProvider for a project (token from secret store)."""
    binding = await _load_binding(org_id, project_id)
    if binding is None:
        raise AppError(
            "git_not_connected",
            "This project is not connected to a git remote. Call POST /git/connect first.",
            400,
        )
    secret = await get_secret_store().get(project_id, org_id)
    token = (secret or {}).get("token", "")
    if not token:
        raise AppError(
            "git_token_missing",
            "No git token is stored for this project. Re-connect to set the token.",
            400,
        )
    provider = make_provider(
        binding["provider"], binding["repo_url"], binding.get("branch", "main"), token
    )
    return provider, binding


async def _serialize_project(org_id: str, project_id: str, repo: Repo) -> list[dict[str, str]]:
    """Serialize a project's resources into portability-envelope files.

    Covers dashboards (boards), queries, and flows.  Connectors are NEVER
    serialized.  Returns a list of ``{path, content}`` items (base_path applied).
    """
    binding = await _load_binding(org_id, project_id)
    base_path = (binding or {}).get("base_path", "") or ""
    project = await projects_repo.get_project(org_id, project_id) or {"id": project_id}

    items: list[dict[str, str]] = []
    counts = {"dashboards": 0, "queries": 0, "flows": 0, "automations": 0}

    # ── Dashboards (boards) ───────────────────────────────────────────────
    try:
        boards = await repo.list("boards", org_id, project_id)
        for board in boards:
            env = to_envelope("dashboard", board)
            items.append(serialize_envelope("dashboard", env, base_path))
            counts["dashboards"] += 1
    except AppError:
        pass

    # ── Queries ───────────────────────────────────────────────────────────
    try:
        queries = await repo.list("queries", org_id, project_id)
        for q in queries:
            env = to_envelope("query", q)
            items.append(serialize_envelope("query", env, base_path))
            counts["queries"] += 1
    except AppError:
        pass

    # ── Flows (the home of scheduled automations) ─────────────────────────
    try:
        from app.flows.store import get_flow_store

        flows = await get_flow_store().list_flows(org_id)
        for flow in flows:
            if project_id and flow.get("project_id") not in (None, project_id):
                continue
            env = {
                "kind": "flow",
                "apiVersion": "nubi/v1",
                "metadata": {"name": flow.get("name", ""), "id": str(flow.get("id", ""))},
                "spec": flow.get("spec") or {},
            }
            items.append(serialize_envelope("flow", env, base_path))
            counts["flows"] += 1
    except Exception:
        # Flows are optional; never block a sync on a flow-store error.
        pass

    # ── Manifest (nubi.yaml) ──────────────────────────────────────────────
    items.append(build_manifest(project, counts, base_path))
    return items


# ---------------------------------------------------------------------------
# Endpoints (project-scoped)
# ---------------------------------------------------------------------------


@router.post("/connect", response_model=GitBindingOut, dependencies=[Depends(require_writer_default)])
async def connect_repo(
    body: ConnectIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Bind a project to a GitHub/GitLab remote and store its token securely.

    The token is written to the secret store (keyed by project_id, org-scoped);
    the project's ``git`` jsonb stores only a reference (``token_ref``), never
    the token itself.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    if body.provider.lower() not in ("github", "gitlab"):
        raise AppError(
            "git_provider_unknown",
            "provider must be 'github' or 'gitlab'.",
            400,
        )
    if not await projects_repo.project_belongs_to_org(body.project_id, org_id):
        raise AppError("not_found", "Project not found.", 404)

    # Store the token in the secret store (encrypted, org-scoped).
    await get_secret_store().put(body.project_id, org_id, {"token": body.token})

    binding = {
        "provider": body.provider.lower(),
        "repo_url": body.repo_url.strip(),
        "branch": (body.branch or "main").strip() or "main",
        "base_path": body.base_path.strip().strip("/"),
        "token_ref": body.project_id,
    }
    await projects_repo.update_project(org_id, body.project_id, {"git": binding})

    return {**binding, "connected": True}


@router.get("/status", response_model=StatusOut)
async def git_status(
    project_id: str = Query(...),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return the git binding + last-sync info for a project."""
    org_id = await _get_user_org(str(user["id"]), repo)
    binding = await _load_binding(org_id, project_id)
    if binding is None:
        return {"connected": False, "binding": None, "last_sync": None}

    # Last-sync info from the local working clone (best-effort).
    last_sync: dict[str, Any] | None = None
    repo_dir = _project_repo_dir(org_id, project_id)
    if (repo_dir / ".git").exists():
        try:
            git_sync = GitSync(repo_dir)
            hist = git_sync.history()
            if hist:
                last_sync = hist[0]
        except Exception:
            last_sync = None

    return {
        "connected": True,
        "binding": {
            "provider": binding.get("provider", ""),
            "repo_url": binding.get("repo_url", ""),
            "branch": binding.get("branch", "main"),
            "base_path": binding.get("base_path", ""),
            "token_ref": binding.get("token_ref"),
            "connected": True,
        },
        "last_sync": last_sync,
    }


@router.post("/pull", response_model=PullOut, dependencies=[Depends(require_writer_default)])
async def pull_project(
    body: PullIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch the project's remote branch and import/upsert resources via portability.

    DB stays canonical: each YAML envelope under ``base_path`` is parsed and
    upserted into the matching resource. Folders are driven off ``KIND_REGISTRY``
    so push and pull cover identical kinds — dashboards, queries, AND flows
    (flows previously pushed but were never imported). Connectors are never
    imported (no connector kind exists).
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    provider, binding = await _provider_for_project(org_id, body.project_id)
    repo_dir = _project_repo_dir(org_id, body.project_id)

    provider.clone_or_pull(repo_dir)

    base = (binding.get("base_path") or "").strip("/")
    root = repo_dir / base if base else repo_dir

    flow_store = None  # lazily constructed only if a flows/ folder exists
    kinds = {kind: 0 for kind in KIND_REGISTRY}
    imported = 0

    for handler in KIND_REGISTRY.values():
        dir_path = root / handler.folder
        if not dir_path.is_dir():
            continue
        for fp in sorted(dir_path.glob("*.y*ml")):
            try:
                env = parse_document(fp.read_text(encoding="utf-8"))
            except AppError:
                continue
            # Skip flow envelopes that fail HARD validation so a hand-edited or
            # corrupt file can never register a broken flow. Soft "[warn]"
            # issues (e.g. forward query_id refs) are allowed through. Dashboards
            # and queries keep their prior import-without-validate behaviour.
            spec = env.get("spec") or {}
            if handler.kind == "flow":
                hard = [
                    i
                    for i in validate_spec_for_kind(handler.kind, spec)
                    if not str(i).startswith("[warn]")
                ]
                if hard:
                    continue
            fields = row_fields_for_kind(handler.kind, env)
            meta_id = (env.get("metadata") or {}).get("id")

            if handler.kind == "flow":
                if flow_store is None:
                    from app.flows.store import get_flow_store

                    flow_store = get_flow_store()
                existing = (
                    await flow_store.get_flow(str(meta_id)) if meta_id else None
                )
                if existing is not None:
                    await flow_store.update_flow(
                        str(meta_id),
                        {"name": fields["name"], "spec": fields["spec"]},
                    )
                else:
                    await flow_store.create_flow(
                        org_id,
                        str(user["id"]),
                        fields["name"],
                        fields["spec"],
                        project_id=body.project_id,
                    )
            else:
                existing = None
                if meta_id:
                    existing = await repo.get(handler.resource, org_id, str(meta_id))
                if existing is not None:
                    await repo.update(handler.resource, org_id, str(meta_id), fields)
                else:
                    await repo.create(
                        handler.resource,
                        org_id,
                        str(user["id"]),
                        fields["name"],
                        fields["config"],
                        project_id=body.project_id,
                    )
            kinds[handler.kind] += 1
            imported += 1

    return {"imported": imported, "kinds": kinds}


@router.post("/push", response_model=ProjectPushOut, dependencies=[Depends(require_writer_default)])
async def push_project(
    body: ProjectPushIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Serialize a project's resources, commit, and push to its remote branch.

    Flow: clone/pull → write serialized files → stage/commit → push. Optionally
    opens a PR/MR when ``open_pr`` is set and the branch differs from the default.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    provider, _binding = await _provider_for_project(org_id, body.project_id)
    repo_dir = _project_repo_dir(org_id, body.project_id)

    # Sync the working clone to the remote tip first so we commit on top of it.
    provider.clone_or_pull(repo_dir)

    items = await _serialize_project(org_id, body.project_id, repo)

    # Write files into the working clone.
    for item in items:
        fp = repo_dir / item["path"]
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(item["content"], encoding="utf-8")

    result = provider.push(repo_dir, message=body.message)

    change_request = None
    if body.open_pr and result.get("committed"):
        change_request = provider.open_change_request(
            title=body.message,
            body="Automated sync from Nubi.",
        )

    return {
        "sha": result.get("sha", ""),
        "committed": result.get("committed", False),
        "pushed": result.get("pushed", False),
        "files": len(items),
        "change_request": change_request,
    }


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
