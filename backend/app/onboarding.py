"""Onboarding helpers — org slug allocation.

Demo content (the removable sample bundle seeded by ``app.sample``) lives in the
org's single DEFAULT project — there is NO separate "Demo" project. Users opt in
to demo data at onboarding (the "add demo data" checkbox → ``demo_project`` flag
on /auth/register, or ``seed.py --demo``), which seeds the bundle straight into
the default project they just created. After onboarding the bundle can be
added/removed for the current project via POST /projects/sample/restore and
/projects/sample/remove.

The seeded resources are tagged ``config.sample = true`` so they can be bulk
removed/restored; they are otherwise indistinguishable from user-created
resources and live in the same (default) project.
"""

from __future__ import annotations

import re
import uuid

from app.db import execute, fetchrow


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
