#!/usr/bin/env python3
"""Run pending SQL migrations against a local Postgres database.

Tracks applied migrations in a `_migrations` table so each file only runs once.
Also runs seed.sql if the seed hasn't been applied yet.

Usage:
    python database/migrate.py           # run pending migrations + seed
    python database/migrate.py --status  # show migration status
    python database/migrate.py --reset   # drop tracking table and re-run all
"""

import argparse, subprocess, sys, os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parent / "backend" / ".env"

def _load_dotenv():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

_load_dotenv()

DB_URL = os.environ.get("DATABASE_URL", "postgresql://pc@localhost:5432/nubi")
MIGRATIONS_DIR = HERE / "migrations"
SEED_FILE = HERE / "seed.sql"

TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS _migrations (
    filename TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def psql_query(sql: str) -> str:
    r = subprocess.run(
        ["psql", DB_URL, "-tA", "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, capture_output=True,
    )
    return r.stdout.strip()


def psql_exec(sql: str) -> bool:
    r = subprocess.run(
        ["psql", DB_URL, "-v", "ON_ERROR_STOP=1"],
        input=sql, text=True, capture_output=True,
    )
    if r.returncode != 0 or (r.stderr and "ERROR" in r.stderr):
        err = r.stderr.strip() if r.stderr else r.stdout.strip()
        print(f"    ERROR: {err}")
        return False
    return True


def ensure_tracking():
    psql_exec(TRACKING_TABLE)


def get_applied() -> set[str]:
    rows = psql_query("SELECT filename FROM _migrations;")
    if not rows:
        return set()
    return set(rows.splitlines())


def get_migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply(sql_file: Path) -> bool:
    sql = sql_file.read_text()
    wrapped = f"""
BEGIN;
{sql}
INSERT INTO _migrations (filename) VALUES ('{sql_file.name}');
COMMIT;
"""
    return psql_exec(wrapped)


def cmd_migrate():
    ensure_tracking()
    applied = get_applied()
    files = get_migration_files()

    pending = [f for f in files if f.name not in applied]

    if not pending and "seed.sql" in applied:
        print("Everything up to date.")
        return

    if pending:
        print(f"{len(pending)} pending migration(s):\n")
        for f in pending:
            print(f"  → {f.name} ... ", end="", flush=True)
            if apply(f):
                print("ok")
            else:
                print("FAILED — aborting")
                sys.exit(1)
        print()

    if SEED_FILE.exists() and "seed.sql" not in applied:
        print("  → seed.sql ... ", end="", flush=True)
        if apply(SEED_FILE):
            print("ok")
        else:
            print("FAILED")
            sys.exit(1)
        print()

    print("Done!")


def cmd_status():
    ensure_tracking()
    applied = get_applied()
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


def cmd_reset():
    print("Dropping all objects in public schema...")
    psql_exec("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    print("Re-running all migrations...\n")
    cmd_migrate()


def main():
    parser = argparse.ArgumentParser(description="Database migration runner")
    parser.add_argument("--status", action="store_true", help="Show migration status")
    parser.add_argument("--reset", action="store_true", help="Drop tracking and re-run all")
    args = parser.parse_args()

    if args.status:
        cmd_status()
    elif args.reset:
        cmd_reset()
    else:
        cmd_migrate()


if __name__ == "__main__":
    main()
