"""Onboarding helpers — the shared "Demo" project creator.

A *Demo project* is a regular, fully deletable project that holds the demo
content (the removable sample bundle seeded by ``app.sample``). Demo content
lives ONLY here — new orgs/projects start empty and users opt in to demo data
by creating the Demo project (POST /orgs/{org_id}/demo-project, the
``demo_project`` flag on /auth/register, or ``seed.py --demo``).

Tagging mechanism
-----------------
The ``projects`` table (0002_orgs_projects.sql) has no metadata/config jsonb column —
its only jsonb column is ``git``, which is reserved for git configuration and
must not be overloaded with app flags. The demo project is therefore identified
by its **slug**: ``slug == 'demo'`` (created with name "Demo"). Slugs are
unique per org, which doubles as the idempotency key. There is deliberately no
special-casing anywhere else: the Demo project is deletable via the normal
DELETE /projects/{id} flow (last-project rule still applies).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.db import execute, fetchrow
from app.repos import projects as projects_repo
from app.repos.provider import Repo

DEMO_PROJECT_NAME = "Demo"
DEMO_PROJECT_SLUG = "demo"


def slugify_org(base: str) -> str:
    """Lowercase *base* to a URL-safe slug ('Acme Corp!' → 'acme-corp')."""
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in base.lower())
    return re.sub(r"-+", "-", slug).strip("-") or "org"


async def insert_org_with_unique_slug(org_id: str, name: str, slug_base: str) -> str:
    """Insert an orgs row with a clean-first, collision-free, immutable slug.

    Tries the bare slugified base first ('acme'); only on collision falls back
    to suffixed candidates ('acme-3f9a', 'acme-3f9a2b1c', then a random hex).
    Insert races on the unique slug constraint are absorbed by retrying the
    next candidate. Returns the slug actually inserted.
    """
    base = slugify_org(slug_base)
    candidates = [
        base,
        f"{base}-{org_id[:4]}",
        f"{base}-{org_id[:8]}",
        f"{base}-{uuid.uuid4().hex[:12]}",
    ]
    last_err: Exception | None = None
    for i, candidate in enumerate(candidates):
        taken = await fetchrow("SELECT 1 FROM orgs WHERE slug = $1", candidate)
        if taken is not None:
            continue
        try:
            await execute(
                "INSERT INTO orgs (id, name, slug) VALUES ($1, $2, $3)",
                org_id, name, candidate,
            )
            return candidate
        except Exception as err:  # unique-violation race — try the next candidate
            last_err = err
            if i == len(candidates) - 1:
                raise
    raise last_err or RuntimeError("Could not allocate a unique org slug.")


async def find_demo_project(org_id: str) -> dict[str, Any] | None:
    """Return the org's Demo project (``slug == 'demo'``), or ``None``."""
    for project in await projects_repo.list_projects(org_id):
        if str(project.get("slug") or "").lower() == DEMO_PROJECT_SLUG:
            return project
    return None


async def ensure_demo_project(
    org_id: str,
    created_by: str,
    repo: Repo | None = None,
) -> dict[str, Any]:
    """Idempotently create the org's "Demo" project and seed the demo bundle.

    - If a project with ``slug == 'demo'`` already exists it is reused
      (``created=False``); otherwise a project named "Demo" is created.
    - The demo bundle is (re-)seeded into the project via
      ``app.sample.seed_sample_bundle`` — itself idempotent, so calling this
      twice never duplicates resources. Seeding is best-effort: a bundle
      failure never fails the project creation (``seed`` is ``None`` then).

    Parameters
    ----------
    org_id:
        The org to create the Demo project in.
    created_by:
        User id recorded as the creator of the project + seeded resources.
    repo:
        Optional Repo override (passed through to ``seed_sample_bundle``;
        ``None`` lets the seeder resolve the active repo itself).

    Returns
    -------
    dict
        ``{"project": <project row>, "created": bool, "seed": <summary|None>}``
    """
    project = await find_demo_project(org_id)
    created = False
    if project is None:
        project = await projects_repo.create_project(
            org_id=org_id,
            name=DEMO_PROJECT_NAME,
            created_by=created_by,
        )
        created = True

    seed: dict[str, Any] | None = None
    try:
        from app.sample import seed_sample_bundle  # noqa: PLC0415

        seed = await seed_sample_bundle(
            org_id=org_id,
            project_id=str(project["id"]),
            created_by=created_by,
            repo=repo,
        )
    except Exception:  # noqa: BLE001 — demo content is best-effort
        seed = None

    # Checkpoint + promote the bundle (v1 pinned in dev AND prod) so the demo
    # works end-to-end under strict protected-env visibility.  Best-effort —
    # the helper reports failures in its return value instead of raising.
    if seed is not None and "skipped" not in seed:
        try:
            from app.sample import checkpoint_and_promote_bundle  # noqa: PLC0415

            seed["envs"] = await checkpoint_and_promote_bundle(
                org_id=org_id,
                project_id=str(project["id"]),
                created_by=created_by,
                repo=repo,
            )
        except Exception:  # noqa: BLE001 — never fail demo creation on envs
            pass

    return {"project": project, "created": created, "seed": seed}


async def relocate_demo_to_demo_project(
    org_id: str,
    created_by: str | None = None,
    repo: Repo | None = None,
) -> dict[str, Any]:
    """Move any mis-placed demo bundle into the org's "Demo" project (idempotent).

    Remediates environments where the demo bundle was seeded into a NON-Demo
    project (e.g. the user's Default/working project under an older code path).
    Finds ``config.sample = true`` rows sitting outside the Demo project,
    removes that mis-placed bundle, and (re-)seeds a clean bundle into the Demo
    project — creating the Demo project if necessary.

    Re-seeding (rather than moving rows + parquet across project prefixes) is
    deliberate: the demo datasets are exported per-project to
    ``s3://<bucket>/projects/<project_id>/demo/...`` keyed by project id, so the
    cleanest, least error-prone fix is to drop the misplaced rows and let
    ``ensure_demo_project`` re-export the parquet under the Demo project's id.
    The stale parquet under the old project prefix is harmless (no row points at
    it) and is left in place — best-effort, never blocking.

    Idempotent: when nothing is mis-placed (demo already lives in the Demo
    project, or there is no demo at all) it only ensures the Demo project's
    bundle is present and reports ``relocated=False``.

    Parameters
    ----------
    org_id:
        The org to remediate (tenant-scoped — only this org's rows are touched).
    created_by:
        User id recorded as the creator when (re-)seeding. Falls back to the
        ``created_by`` of a mis-placed row, then a synthetic system id.
    repo:
        Optional Repo override; ``None`` resolves the active repo.

    Returns
    -------
    dict
        ``{"relocated": bool, "removed": {table: n}, "demo": <ensure result>}``
    """
    from app.repos.provider import get_repo  # noqa: PLC0415
    from app.sample import _SAMPLE_TABLES, remove_sample_bundle  # noqa: PLC0415

    repo = repo or get_repo()

    demo = await find_demo_project(org_id)
    demo_id = str(demo["id"]) if demo is not None else None

    # Scan every sample-bearing table for rows tagged sample=true that sit
    # OUTSIDE the Demo project (project_id != demo_id, including a NULL
    # project_id). Collect the distinct offending project ids; NULL-project rows
    # are tracked separately (no per-project remove can target them precisely
    # except the no-Demo-yet case below).
    misplaced_project_ids: set[str] = set()
    has_null_misplaced = False
    fallback_creator: str | None = None
    for table in _SAMPLE_TABLES:
        for row in await repo.list(table, org_id):
            cfg = row.get("config") or {}
            if cfg.get("sample") is not True:
                continue
            pid = row.get("project_id")
            pid_str = str(pid) if pid is not None else None
            if pid_str != demo_id:
                if pid_str is None:
                    has_null_misplaced = True
                else:
                    misplaced_project_ids.add(pid_str)
                fallback_creator = fallback_creator or row.get("created_by")

    creator = str(created_by or fallback_creator or "00000000-0000-0000-0000-000000000000")

    removed: dict[str, int] = {}
    relocated = False

    def _tally(counts: dict[str, int]) -> None:
        nonlocal relocated
        for table, n in counts.items():
            removed[table] = removed.get(table, 0) + n
            if n:
                relocated = True

    if demo_id is None and has_null_misplaced:
        # No Demo project exists yet and the bundle has NULL project_id rows
        # (older orgless seed). There is nothing legitimately sample-tagged to
        # preserve, so a single org-wide remove clears the misplaced bundle
        # (this also covers any non-null misplaced ids).
        _tally(await remove_sample_bundle(org_id, None, repo))
    else:
        # Demo project present (or only non-null misplaced ids): remove the
        # bundle from each offending project precisely, never touching Demo.
        for pid in sorted(misplaced_project_ids):
            _tally(await remove_sample_bundle(org_id, pid, repo))

    # (Re-)seed a clean bundle into the Demo project (creates it if needed).
    demo_result = await ensure_demo_project(org_id, creator, repo=repo)

    return {"relocated": relocated, "removed": removed, "demo": demo_result}
