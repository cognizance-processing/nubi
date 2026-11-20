"""GET /bootstrap — everything the app shell needs before its first page.

The shell used to load orgs, then projects (needs the org), then environments
(needs the project), plus the feature flags: four requests, three of them in
series, each paying the auth checks again. This returns the same four answers
in one response, in the exact shapes of ``GET /orgs``, ``GET /projects``,
``GET /projects/{id}/environments`` and ``GET /features``.

The client passes the org and project it last had selected as hints. Those
hints are loaded speculatively, side by side with the org list, and only
returned when the org list proves the caller belongs to that org and the
project list proves the project belongs to it; otherwise the first org and its
first (default) project are used, the same defaults the providers apply.

``POST /auth/refresh?bootstrap=1`` returns the same payload (built by
:func:`build_bootstrap`) so a hard page load needs no second request; this
endpoint stays as the fallback for an already-restored session.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.auth.deps import current_user
from app.environments.store import get_env_store
from app.repos import projects as projects_repo

router = APIRouter(tags=["bootstrap"])


async def _quiet(coro: Any) -> Any:
    """Await a speculative load; a failure just means "not usable"."""
    try:
        return await coro
    except Exception:  # noqa: BLE001 — the authoritative path below retries
        return None


@router.get("/bootstrap")
async def bootstrap(
    org_id: str | None = Query(default=None, max_length=64),
    project_id: str | None = Query(default=None, max_length=64),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return ``{orgs, active_org_id, projects, active_project_id, environments, features}``.

    ``environments`` is ``None`` when there is no active project, matching the
    shell's "not loaded" value.
    """
    return await build_bootstrap(user, org_id, project_id)


async def build_bootstrap(
    user: dict[str, Any],
    org_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    """Build the bootstrap payload for *user* (needs at least ``user["id"]``).

    The hints are untrusted: they only select among orgs and projects the
    membership-proving lists already contain.
    """
    from app.routes.features import list_features  # noqa: PLC0415
    from app.routes.orgs import list_orgs  # noqa: PLC0415

    # The membership-proving org list, plus speculative loads for the hinted
    # org and project (discarded below unless the lists vouch for them).
    orgs_resp, hinted_projects, hinted_envs = await asyncio.gather(
        list_orgs(user),
        _quiet(projects_repo.list_projects(org_id)) if org_id else asyncio.sleep(0),
        # Read-only: ensure_project_envs may INSERT, which must never happen
        # for a project not yet proven to be the caller's.
        _quiet(get_env_store().list_environments(project_id)) if project_id else asyncio.sleep(0),
    )
    features = (await list_features(user))["features"]
    orgs = [o.model_dump() for o in orgs_resp.orgs]

    empty: dict[str, Any] = {
        "orgs": orgs,
        "active_org_id": None,
        "projects": [],
        "active_project_id": None,
        "environments": None,
        "features": features,
    }
    if not orgs:
        return empty

    org_ids = [str(o["id"]) for o in orgs]
    active_org_id = str(org_id) if org_id and str(org_id) in org_ids else org_ids[0]

    if active_org_id == str(org_id) and isinstance(hinted_projects, list):
        projects = hinted_projects
    else:
        projects = await projects_repo.list_projects(active_org_id)

    project_ids = [str(p.get("id")) for p in projects]
    if project_id and str(project_id) in project_ids:
        active_project_id: str | None = str(project_id)
    else:
        active_project_id = project_ids[0] if project_ids else None

    environments: list[dict[str, Any]] | None = None
    if active_project_id is not None:
        if (
            active_project_id == str(project_id)
            and isinstance(hinted_envs, list)
            and {"dev", "prod"} <= {e.get("key") for e in hinted_envs}
        ):
            environments = hinted_envs
        else:
            environments = await get_env_store().ensure_project_envs(active_project_id)

    return {
        **empty,
        "active_org_id": active_org_id,
        "projects": projects,
        "active_project_id": active_project_id,
        "environments": environments,
    }
