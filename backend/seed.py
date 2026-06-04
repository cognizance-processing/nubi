"""Seed a known test user (idempotent).

Mirrors the /auth/register flow: argon2id password hash + a personal org with
owner membership, so the seeded user can use the editor/boards immediately.

Usage:
    cd backend && DATABASE_URL=postgresql://... python seed.py

Credentials created:
    email:    test@nubi.dev
    password: nubitest123
"""

from __future__ import annotations

import asyncio
import uuid

from app.auth.passwords import hash_password
from app.db import close_db, execute, fetchrow, init_db
from app.routes.auth import _create_personal_org

TEST_EMAIL = "test@nubi.dev"
TEST_PASSWORD = "nubitest123"  # >= 8 chars (matches RegisterIn policy)
TEST_NAME = "Test User"


async def main() -> None:
    await init_db()
    try:
        existing = await fetchrow("SELECT id FROM users WHERE email = $1", TEST_EMAIL)
        if existing is not None:
            print(f"User already exists: {TEST_EMAIL} / {TEST_PASSWORD}")
            return

        user_id = str(uuid.uuid4())
        await execute(
            """
            INSERT INTO users (id, email, password_hash, name, email_verified)
            VALUES ($1, $2, $3, $4, true)
            """,
            user_id,
            TEST_EMAIL,
            hash_password(TEST_PASSWORD),
            TEST_NAME,
        )
        await _create_personal_org(user_id, TEST_NAME, TEST_EMAIL)
        print(f"Seeded test user: {TEST_EMAIL} / {TEST_PASSWORD}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
