#!/usr/bin/env python3
"""Run pending SQL migrations against a Postgres database.

Tracks applied migrations in a `_migrations` table so each file only runs once.
Also runs seed.sql if the seed hasn't been applied yet.

Usage:
    python database/migrate.py                      # local (default)
    python database/migrate.py --env dev             # dev environment
    python database/migrate.py --env prod --status   # prod migration status
    python database/migrate.py --env prod --reset    # reset prod (with confirmation)
"""

import argparse, asyncio, sys, os
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve().parent
BACKEND_DIR = HERE.parent / "backend"
MIGRATIONS_DIR = HERE / "migrations"
SEED_FILE = HERE / "seed.sql"

ENV_FILES = {
    "local": BACKEND_DIR / ".env",
    "dev":   BACKEND_DIR / ".env.development",
    "prod":  BACKEND_DIR / ".env.production",
}

DB_URL: str = ""


def _load_dotenv(path: Path):
    """Load a .env file into os.environ (existing vars take precedence)."""
    if not path.exists():
        print(f"WARNING: env file not found: {path}")
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def init_env(env_name: str):
    global DB_URL
    env_file = ENV_FILES[env_name]
    base_env = BACKEND_DIR / ".env"

    _load_dotenv(env_file)
    if env_name != "local" and base_env.exists():
        _load_dotenv(base_env)

    DB_URL = os.environ.get("DATABASE_URL", "postgresql://pc@localhost:5432/nubi")

    label = env_name.upper()
    display_url = DB_URL
    if "@" in DB_URL:
        pre, at_rest = DB_URL.split("@", 1)
        proto, _, creds = pre.rpartition("//")
        display_url = f"{proto}//*****@{at_rest}"

    print(f"Environment : {label}")
    print(f"Env file    : {env_file.relative_to(HERE.parent)}")
    print(f"Database    : {display_url}")
    print()


TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS _migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def get_connection() -> asyncpg.Connection:
    return await asyncpg.connect(DB_URL)


async def db_exec(sql: str) -> bool:
    conn = await get_connection()
    try:
        await conn.execute(sql)
        return True
    except Exception as e:
        print(f"    ERROR: {e}")
        return False
    finally:
        await conn.close()


async def db_query(sql: str) -> list[str]:
    conn = await get_connection()
    try:
        rows = await conn.fetch(sql)
        return [row[0] for row in rows]
    except Exception:
        return []
    finally:
        await conn.close()


async def ensure_tracking():
    await db_exec(TRACKING_TABLE)


async def get_applied() -> set[str]:
    rows = await db_query("SELECT filename FROM _migrations;")
    return set(rows)


def get_migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def apply(sql_file: Path) -> bool:
    sql = sql_file.read_text()
    conn = await get_connection()
    try:
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO _migrations (filename) VALUES ($1)", sql_file.name
            )
        return True
    except Exception as e:
        print(f"    ERROR: {e}")
        return False
    finally:
        await conn.close()


async def cmd_migrate():
    await ensure_tracking()
    applied = await get_applied()
    files = get_migration_files()

    pending = [f for f in files if f.name not in applied]

    if not pending and "seed.sql" in applied:
        print("Everything up to date.")
        return

    if pending:
        print(f"{len(pending)} pending migration(s):\n")
        for f in pending:
            print(f"  → {f.name} ... ", end="", flush=True)
            if await apply(f):
                print("ok")
            else:
                print("FAILED — aborting")
                sys.exit(1)
        print()

    if SEED_FILE.exists() and "seed.sql" not in applied:
        print("  → seed.sql ... ", end="", flush=True)
        if await apply(SEED_FILE):
            print("ok")
        else:
            print("FAILED")
            sys.exit(1)
        print()

    print("Done!")


async def cmd_status():
    await ensure_tracking()
    applied = await get_applied()
    files = get_migration_files()

    print(f"{'FILE':<50} STATUS")
    print("-" * 62)
    for f in files:
        status = "applied" if f.name in applied else "PENDING"
        marker = "✓" if f.name in applied else "•"
        print(f"  {marker} {f.name:<48} {status}")

    if SEED_FILE.exists():
        status = "applied" if "seed.sql" in applied else "PENDING"
        marker = "✓" if "seed.sql" in applied else "•"
        print(f"  {marker} {'seed.sql':<48} {status}")

    pending_count = sum(1 for f in files if f.name not in applied)
    if SEED_FILE.exists() and "seed.sql" not in applied:
        pending_count += 1
    print(f"\n{len(files)} migrations, {pending_count} pending")


async def cmd_reset(env_name: str):
    if env_name == "prod":
        answer = input("⚠️  You are about to RESET the PRODUCTION database. Type 'yes' to confirm: ")
        if answer.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)
    print("Dropping all objects in public schema...")
    await db_exec("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    print("Re-running all migrations...\n")
    await cmd_migrate()


async def async_main():
    parser = argparse.ArgumentParser(description="Database migration runner")
    parser.add_argument(
        "--env", choices=["local", "dev", "prod"], default="local",
        help="Target environment (default: local)",
    )
    parser.add_argument("--status", action="store_true", help="Show migration status")
    parser.add_argument("--reset", action="store_true", help="Drop tracking and re-run all")
    args = parser.parse_args()

    init_env(args.env)

    if args.status:
        await cmd_status()
    elif args.reset:
        await cmd_reset(args.env)
    else:
        await cmd_migrate()


if __name__ == "__main__":
    asyncio.run(async_main())
