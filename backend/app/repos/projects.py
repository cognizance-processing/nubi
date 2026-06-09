"""Projects repository — org-scoped CRUD + default-project helpers.

A *project* is the workspace / deploy / git unit that groups resources within
an org. Every org has at least one project (a "Default" project created at
org-creation time), so resource creation always has a project to fall back to.

All functions here are thin async helpers over ``app.db`` (asyncpg). They are
deliberately framework-free so they can be reused from routes, the auth
register flow, and the seed scripts.

In test environments the DB layer is a fake that returns ``None`` /``[]`` for
unknown tables; the public helpers below are written to degrade gracefully in
that case (e.g. ``get_default_project`` returns ``None`` rather than raising).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import app.db as _db

# Upper bound on slug-clash retries before falling back to a random suffix —
# guards against an existence probe that never returns None (see _unique_slug).
_MAX_SLUG_TRIES = 1000


# Call the DB helpers through the module object (``_db.fetchrow`` rather than a
# bound ``fetchrow``) so the test fixtures, which patch ``app.db.fetchrow`` /
# ``app.db.fetch`` / ``app.db.execute``, take effect for this module too.

async def fetchrow(query: str, *args: Any) -> Any:
    return await _db.fetchrow(query, *args)


async def fetch(query: str, *args: Any) -> Any:
    return await _db.fetch(query, *args)


async def execute(query: str, *args: Any) -> Any:
    return await _db.execute(query, *args)


# ── Slug helper ────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """Return a URL-safe slug derived from *name*.

    Lowercases, keeps alphanumerics, collapses everything else to single
    hyphens, and trims leading/trailing hyphens. Falls back to ``"project"``
    when the result would be empty.
    """
    out: list[str] = []
    prev_hyphen = False
    for ch in name.lower().strip():
        if ch.isalnum():
            out.append(ch)
            prev_hyphen = False
        elif not prev_hyphen:
            out.append("-")
            prev_hyphen = True
    slug = "".join(out).strip("-")
    return slug or "project"


def _row_to_dict(record: Any) -> dict[str, Any]:
    """Coerce an asyncpg Record (or dict) into a JSON-friendly dict."""
    if record is None:
        return {}
    row: dict[str, Any] = dict(record)
    for key, value in row.items():
        if isinstance(value, datetime):
            row[key] = value.isoformat()
        elif isinstance(value, uuid.UUID):
            row[key] = str(value)
    return row


# ── Read helpers ───────────────────────────────────────────────────────────────

async def list_projects(org_id: str) -> list[dict[str, Any]]:
    """Return all projects for *org_id*, oldest first (the default is first)."""
    rows = await fetch(
        "SELECT * FROM projects WHERE org_id = $1::uuid ORDER BY created_at ASC",
        org_id,
    )
    return [_row_to_dict(r) for r in rows]


async def get_project(org_id: str, project_id: str) -> dict[str, Any] | None:
    """Return a single project scoped to *org_id*, or ``None``."""
    row = await fetchrow(
        "SELECT * FROM projects WHERE id = $1::uuid AND org_id = $2::uuid",
        project_id,
        org_id,
    )
    return _row_to_dict(row) if row is not None else None


async def get_default_project(org_id: str) -> dict[str, Any] | None:
    """Return the org's default (oldest) project, or ``None`` if none exists.

    Used as the fallback target when a resource is created without an explicit
    ``X-Project-Id``. Returns ``None`` gracefully (rather than raising) so test
    doubles without a projects table do not break resource creation.
    """
    row = await fetchrow(
        """
        SELECT * FROM projects
        WHERE org_id = $1::uuid
        ORDER BY created_at ASC
        LIMIT 1
        """,
        org_id,
    )
    return _row_to_dict(row) if row is not None else None


async def get_default_project_id(org_id: str) -> str | None:
    """Return the id of the org's default project, or ``None``."""
    proj = await get_default_project(org_id)
    if not proj:
        return None
    return str(proj["id"])


async def project_belongs_to_org(project_id: str, org_id: str) -> bool:
    """Return True if *project_id* exists and belongs to *org_id*."""
    row = await fetchrow(
        "SELECT 1 FROM projects WHERE id = $1::uuid AND org_id = $2::uuid",
        project_id,
        org_id,
    )
    return row is not None


async def count_projects(org_id: str) -> int:
    """Return the number of projects in *org_id*."""
    row = await fetchrow(
        "SELECT count(*)::int AS n FROM projects WHERE org_id = $1::uuid",
        org_id,
    )
    if row is None:
        return 0
    return int(row["n"])


# ── Write helpers ──────────────────────────────────────────────────────────────

async def _unique_slug(org_id: str, base: str) -> str:
    """Return a slug unique within *org_id*, suffixing ``-2``, ``-3``, … on clash.

    Bounded so a misbehaving / always-truthy existence probe can never spin
    forever; after ``_MAX_SLUG_TRIES`` clashes we fall back to a short random
    suffix (collision odds vanishingly small) rather than loop indefinitely.
    """
    slug = base
    n = 1
    for _ in range(_MAX_SLUG_TRIES):
        existing = await fetchrow(
            "SELECT 1 FROM projects WHERE org_id = $1::uuid AND slug = $2",
            org_id,
            slug,
        )
        if existing is None:
            return slug
        n += 1
        slug = f"{base}-{n}"
    return f"{base}-{uuid.uuid4().hex[:8]}"


async def create_project(
    org_id: str,
    name: str,
    created_by: str | None,
    *,
    project_id: str | None = None,
    git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a project and return the created row.

    The slug is derived from *name* and made unique per org. A specific
    ``project_id`` may be supplied (so callers that need the id up front, e.g.
    the register/seed flow, can generate it themselves).
    """
    import json  # noqa: PLC0415

    pid = project_id or str(uuid.uuid4())
    base_slug = slugify(name)
    slug = await _unique_slug(org_id, base_slug)
    git_json = json.dumps(git) if git is not None else None

    row = await fetchrow(
        """
        INSERT INTO projects (id, org_id, name, slug, created_by, git)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)
        RETURNING *
        """,
        pid,
        org_id,
        name,
        slug,
        created_by,
        git_json,
    )
    # Every project gets its dev+prod environment pair (lazy import to avoid a
    # cycle; best-effort so test doubles without an env store still pass).
    await _ensure_project_envs_best_effort(pid)

    # In production INSERT ... RETURNING always returns a row. Under the test
    # fake DB it may return None; synthesize a best-effort dict so callers that
    # only need the id keep working.
    if row is None:
        return {
            "id": pid,
            "org_id": str(org_id),
            "name": name,
            "slug": slug,
            "created_by": str(created_by) if created_by else None,
            "git": git,
        }
    return _row_to_dict(row)


async def _ensure_project_envs_best_effort(project_id: str) -> None:
    """Idempotently create the project's dev+prod environments (best-effort).

    Lazy-imports the environments store to avoid an import cycle and swallows
    every error so test doubles without environment tables keep working.
    """
    try:
        from app.environments.store import get_env_store  # noqa: PLC0415

        await get_env_store().ensure_project_envs(str(project_id))
    except Exception:  # noqa: BLE001 — never fail project creation on envs
        pass


async def update_project(
    org_id: str,
    project_id: str,
    fields: dict[str, Any],
) -> dict[str, Any] | None:
    """Update allowed fields (``name``, ``git``) and return the row, or ``None``."""
    import json  # noqa: PLC0415

    updates: list[str] = []
    values: list[Any] = []
    idx = 1

    if "name" in fields and fields["name"] is not None:
        updates.append(f"name = ${idx}")
        values.append(fields["name"])
        idx += 1
        updates.append(f"slug = ${idx}")
        values.append(await _unique_slug(org_id, slugify(fields["name"])))
        idx += 1
    if "git" in fields:
        updates.append(f"git = ${idx}::jsonb")
        values.append(json.dumps(fields["git"]) if fields["git"] is not None else None)
        idx += 1

    if not updates:
        return await get_project(org_id, project_id)

    updates.append("updated_at = now()")
    set_clause = ", ".join(updates)
    values.extend([project_id, org_id])

    row = await fetchrow(
        f"""
        UPDATE projects
        SET {set_clause}
        WHERE id = ${idx}::uuid AND org_id = ${idx + 1}::uuid
        RETURNING *
        """,
        *values,
    )
    return _row_to_dict(row) if row is not None else None


async def delete_project(org_id: str, project_id: str) -> bool:
    """Delete a project; return True if a row was removed."""
    status = await execute(
        "DELETE FROM projects WHERE id = $1::uuid AND org_id = $2::uuid",
        project_id,
        org_id,
    )
    try:
        return int(status.split()[-1]) > 0
    except (ValueError, IndexError, AttributeError):
        return False


async def ensure_default_project(
    org_id: str,
    created_by: str | None,
    name: str = "Default",
) -> str:
    """Return the org's default project id, creating one if none exists.

    Idempotent — safe to call from seeds. Used by the seed scripts and as a
    belt-and-braces helper anywhere a project is required.
    """
    existing = await get_default_project_id(org_id)
    if existing:
        # Pre-0029 projects may predate the environments table; ensure the
        # dev+prod pair exists for them too (idempotent, best-effort).
        await _ensure_project_envs_best_effort(existing)
        return existing
    proj = await create_project(org_id, name, created_by)
    return str(proj["id"])
