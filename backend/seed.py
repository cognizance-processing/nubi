"""Seed the superuser, and optionally the "Demo" project with the demo bundle.

Mirrors the /auth/register flow: argon2id password hash + a personal org with
owner membership + an EMPTY "Default" project, so the seeded user can use the
editor/boards immediately.

With ``--demo`` it ALSO creates the org's deletable "Demo" project (identified
by ``slug == 'demo'`` — see ``app/onboarding.py``) and seeds the demo bundle
into it via the same shared helper used by POST /orgs/{org_id}/demo-project
and the ``demo_project`` register flag. Nothing is ever seeded into the
default project; demo content lives only in the Demo project.

Usage:
    cd backend && DATABASE_URL=postgresql://... python seed.py           # superuser only
    cd backend && DATABASE_URL=postgresql://... python seed.py --demo    # + Demo project
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import close_db, execute, fetchrow, init_db
from app.routes.auth import _create_personal_org

# Superuser credentials come from the environment (SUPERUSER_* in the root .env),
# so the DB reset/seed flow always provisions the same known admin login.
_s = get_settings()
TEST_EMAIL = _s.SUPERUSER_EMAIL
TEST_PASSWORD = _s.SUPERUSER_PASSWORD
TEST_NAME = _s.SUPERUSER_NAME


async def _ensure_superuser() -> str:
    """Create the superuser + personal org if absent; return the user id."""
    existing = await fetchrow("SELECT id FROM users WHERE email = $1", TEST_EMAIL)
    if existing is not None:
        return str(existing["id"])
    user_id = str(uuid.uuid4())
    await execute(
        "INSERT INTO users (id, email, password_hash, name, email_verified) "
        "VALUES ($1, $2, $3, $4, true)",
        user_id, TEST_EMAIL, hash_password(TEST_PASSWORD), TEST_NAME,
    )
    await _create_personal_org(user_id, TEST_NAME, TEST_EMAIL)
    return user_id


async def _seed_demo_project(user_id: str) -> None:
    """Create the superuser org's "Demo" project + demo bundle (idempotent)."""
    org_row = await fetchrow(
        "SELECT org_id FROM org_members WHERE user_id = $1::uuid ORDER BY org_id LIMIT 1",
        user_id,
    )
    assert org_row is not None, "Superuser has no org membership."
    org_id = str(org_row["org_id"])

    from app.onboarding import ensure_demo_project  # noqa: PLC0415

    result = await ensure_demo_project(org_id, user_id)
    project = result["project"]
    status = "CREATED" if result["created"] else "exists "
    seed = result["seed"] or {}
    if "skipped" in seed:
        seed_note = f"bundle skipped: {seed['skipped']}"
    else:
        seed_note = f"bundle created: {seed.get('created', [])!r}" if seed else "bundle: best-effort failure"
    print(f"  demo project   [{status}]  {project.get('name')} ({project.get('id')}) — {seed_note}")


async def main() -> None:
    demo = "--demo" in sys.argv
    await init_db()
    try:
        user_id = await _ensure_superuser()
        print(f"Superuser: {TEST_EMAIL} / {TEST_PASSWORD}")
        if demo:
            await _seed_demo_project(user_id)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
