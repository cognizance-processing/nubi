"""Reset the local database: drop everything, re-migrate, seed the superuser.

The superuser login is read from the root .env (SUPERUSER_EMAIL / SUPERUSER_PASSWORD),
so a reset always reprovisions the same known admin account.

Usage:
    cd backend && python reset_db.py          # reset + seed superuser
    cd backend && python reset_db.py --demo   # reset + seed superuser + demo workspace

DATABASE_URL / SUPERUSER_* are read from the root .env via app.config.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg

from app.config import get_settings

_REPO = Path(__file__).resolve().parents[1]
_BACKEND = _REPO / "backend"


async def _reset_schema(url: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        # Wipe everything (tables, extensions, the _migrations tracker) and start clean.
        await conn.execute("DROP SCHEMA IF EXISTS public CASCADE;")
        await conn.execute("CREATE SCHEMA public;")
        await conn.execute(f'GRANT ALL ON SCHEMA public TO "{conn._params.user}";')
        await conn.execute("GRANT ALL ON SCHEMA public TO public;")
    finally:
        await conn.close()


def main() -> None:
    s = get_settings()
    url = s.DATABASE_URL
    # migrate.py reads DATABASE_URL straight from the environment — make sure it's set.
    env = {**os.environ, "DATABASE_URL": url}

    safe = url.split("@")[-1]
    print(f"[reset] target: {safe}")

    print("[reset] dropping + recreating schema 'public'...")
    asyncio.run(_reset_schema(url))

    print("[reset] applying migrations...")
    subprocess.run([sys.executable, str(_REPO / "database" / "migrate.py")], check=True, env=env)

    print("[reset] seeding superuser...")
    subprocess.run([sys.executable, "seed.py"], check=True, cwd=str(_BACKEND), env=env)

    if "--demo" in sys.argv:
        print("[reset] seeding demo workspace...")
        subprocess.run([sys.executable, "seed_demo.py"], check=True, cwd=str(_BACKEND), env=env)

    print("\n[reset] done.")
    print(f"        superuser: {s.SUPERUSER_EMAIL} / {s.SUPERUSER_PASSWORD}")


if __name__ == "__main__":
    main()
