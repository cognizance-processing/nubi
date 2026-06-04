"""
database/migrate.py — forward-only SQL migration runner using asyncpg.

Usage
-----
Apply pending migrations:
    python database/migrate.py

Show applied vs pending migrations:
    python database/migrate.py --status

Requirements
------------
- DATABASE_URL env var (Neon Postgres URL, e.g. postgresql://user:pass@host/db?sslmode=require)
- asyncpg installed  (pip install asyncpg)

Behaviour
---------
- On first run, creates the schema_migrations ledger table.
- Scans database/migrations/*.sql in lexical order.
- Applies only those not already recorded in schema_migrations.
- Each migration runs inside its own transaction; failure rolls back that
  migration and stops the runner (no partial state).
- Idempotent: safe to run multiple times.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

CREATE_LEDGER_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text        PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


def get_database_url() -> str:
    """Read DATABASE_URL from the environment; abort if absent."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("ERROR: DATABASE_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return url


def discover_migrations() -> list[Path]:
    """Return all *.sql files in MIGRATIONS_DIR, sorted lexically."""
    if not MIGRATIONS_DIR.is_dir():
        print(
            f"ERROR: migrations directory not found: {MIGRATIONS_DIR}",
            file=sys.stderr,
        )
        sys.exit(1)
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return files


async def ensure_ledger(conn: asyncpg.Connection) -> None:
    """Create the schema_migrations table if it does not yet exist."""
    await conn.execute(CREATE_LEDGER_SQL)


async def applied_versions(conn: asyncpg.Connection) -> set[str]:
    """Return the set of migration versions already recorded in the ledger."""
    rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
    return {row["version"] for row in rows}


async def apply_migrations(url: str) -> None:
    """Apply all pending migrations to the database."""
    conn: asyncpg.Connection = await asyncpg.connect(url)
    try:
        await ensure_ledger(conn)
        done = await applied_versions(conn)
        pending = [f for f in discover_migrations() if f.name not in done]

        if not pending:
            print("No pending migrations — database is up to date.")
            return

        for migration_file in pending:
            sql = migration_file.read_text(encoding="utf-8")
            version = migration_file.name
            print(f"  Applying {version} ...", end=" ", flush=True)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)",
                    version,
                )
            print("done")

        print(f"\nApplied {len(pending)} migration(s) successfully.")
    finally:
        await conn.close()


async def show_status(url: str) -> None:
    """Print a table of applied vs pending migration files."""
    conn: asyncpg.Connection = await asyncpg.connect(url)
    try:
        await ensure_ledger(conn)
        done = await applied_versions(conn)
        all_files = discover_migrations()

        print(f"{'VERSION':<40}  {'STATUS':<10}  APPLIED_AT")
        print("-" * 70)

        # Print applied rows (with timestamp) in order
        rows = await conn.fetch(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        )
        applied_map = {row["version"]: row["applied_at"] for row in rows}

        file_names = {f.name for f in all_files}

        # Show all known files first (lexical order)
        for migration_file in all_files:
            name = migration_file.name
            if name in applied_map:
                ts = applied_map[name].strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"{name:<40}  {'applied':<10}  {ts}")
            else:
                print(f"{name:<40}  {'pending':<10}  —")

        # Any applied versions not found on disk (e.g. after a rollback of file)
        for version, ts in sorted(applied_map.items()):
            if version not in file_names:
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"{version:<40}  {'applied*':<10}  {ts_str}  [file missing]")

        print()
        pending_count = len([f for f in all_files if f.name not in done])
        print(
            f"{len(done)} applied, {pending_count} pending"
            + (" — run without --status to apply" if pending_count else "")
        )
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Forward-only SQL migration runner for Nubi (asyncpg / Neon Postgres)."
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="List applied and pending migrations without applying anything.",
    )
    args = parser.parse_args()

    url = get_database_url()

    if args.status:
        asyncio.run(show_status(url))
    else:
        asyncio.run(apply_migrations(url))


if __name__ == "__main__":
    main()
