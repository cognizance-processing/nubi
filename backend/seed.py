"""Seed the superuser, and optionally automate onboarding + the Demo project.

By default this creates the BARE superuser account only (argon2id hash,
``is_superadmin = true``) — no org, no project. On first login the superuser
goes through the exact same /onboarding wizard as every other user (create
org → "Default" project → optional Demo project).

``--demo`` automates that same onboarding flow for local dev and e2e (so a
reset leaves a ready workspace without clicking through the wizard): it
creates the personal org + EMPTY "Default" project via the same helper the
/auth/register flow uses, then the org's deletable "Demo" project (identified
by ``slug == 'demo'`` — see ``app/onboarding.py``) seeded with the demo
bundle via the same shared helper used by POST /orgs/{org_id}/demo-project
and the ``demo_project`` register flag. Nothing is ever seeded into the
default project; demo content lives only in the Demo project.

Usage:
    cd backend && DATABASE_URL=postgresql://... python seed.py           # bare superuser → onboarding wizard
    cd backend && DATABASE_URL=postgresql://... python seed.py --demo    # + org/Default/Demo (dev & e2e)
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
    """Create the BARE superuser if absent; return the user id.

    No org/project is created here — the superuser goes through the SAME
    /onboarding wizard as every other user on first login (``--demo``
    automates that flow via :func:`_ensure_workspace`).

    Always (idempotently) marks the account ``is_superadmin = true`` — the
    seed script and manual SQL are the ONLY ways to grant superadmin; no API
    endpoint can set the flag.
    """
    existing = await fetchrow("SELECT id FROM users WHERE email = $1", TEST_EMAIL)
    if existing is not None:
        user_id = str(existing["id"])
    else:
        user_id = str(uuid.uuid4())
        await execute(
            "INSERT INTO users (id, email, password_hash, name, email_verified) "
            "VALUES ($1, $2, $3, $4, true)",
            user_id, TEST_EMAIL, hash_password(TEST_PASSWORD), TEST_NAME,
        )
    # Idempotent superadmin grant (see migration 0028 header).
    await execute(
        "UPDATE users SET is_superadmin = true WHERE id = $1::uuid", user_id
    )
    return user_id


async def _ensure_workspace(user_id: str) -> None:
    """Automate the onboarding flow: personal org + EMPTY "Default" project.

    Idempotent — skipped when the user already belongs to an org. Uses the
    same ``_create_personal_org`` helper as /auth/register so the seeded
    workspace is byte-for-byte what the wizard would have produced.
    """
    member = await fetchrow(
        "SELECT org_id FROM org_members WHERE user_id = $1::uuid LIMIT 1", user_id
    )
    if member is None:
        await _create_personal_org(user_id, TEST_NAME, TEST_EMAIL, project_name="Default")


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
            await _ensure_workspace(user_id)
            await _seed_demo_project(user_id)
        else:
            print("  no workspace seeded — superuser will go through /onboarding on first login")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
