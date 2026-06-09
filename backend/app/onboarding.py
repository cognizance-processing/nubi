"""Onboarding helpers — the shared "Demo" project creator.

A *Demo project* is a regular, fully deletable project that holds the demo
content (the removable sample bundle seeded by ``app.sample``). Demo content
lives ONLY here — new orgs/projects start empty and users opt in to demo data
by creating the Demo project (POST /orgs/{org_id}/demo-project, the
``demo_project`` flag on /auth/register, or ``seed.py --demo``).

Tagging mechanism
-----------------
The ``projects`` table (migration 0013) has no metadata/config jsonb column —
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

    return {"project": project, "created": created, "seed": seed}
