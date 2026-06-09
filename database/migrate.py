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
- Scans database/migrations/*.sql (OSS core) in lexical order.
- EE/cloud migrations live in database/migrations/ee/*.sql and are applied ONLY
  with --ee (or NUBI_CLOUD=1 / NUBI_EE=1), AFTER core — so OSS self-host stays
  thin (no billing/wallet/fx/invoice tables). EE versions are keyed "ee/<file>".
- Applies only those not already recorded in schema_migrations.
- Migrations that moved from core into ee/ (0017/0018/0022/0027) may be
  recorded under their legacy bare file name on already-deployed databases;
  the runner re-keys those ledger rows to "ee/<file>" instead of re-applying.
- Each migration runs inside its own transaction; failure rolls back that
  migration and stops the runner (no partial state).
- Idempotent: safe to run multiple times. Concurrent runners (multi-replica
  deploys, CI overlapping a manual run) serialize on a Postgres advisory
  lock, so the second runner simply finds nothing pending.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# EE / Nubi Cloud migrations (billing, FX, wallet, invoices). These are applied
# ONLY when the cloud/EE layer is active — keeping the open-source self-host
# schema thin (no billing tables it never uses). Enable with --ee or the
# NUBI_CLOUD / NUBI_EE env var. EE migrations are keyed in the ledger as
# "ee/<file>" and always applied AFTER core migrations so their FKs to core
# tables (e.g. orgs) resolve.
EE_MIGRATIONS_DIR = MIGRATIONS_DIR / "ee"

# Session-level Postgres advisory lock key serializing concurrent runners
# (e.g. several backend replicas starting at once). Released automatically
# when the connection closes.
MIGRATION_LOCK_ID = 727274


def ee_enabled(cli_flag: bool = False) -> bool:
    """Whether EE/cloud migrations should be applied."""
    if cli_flag:
        return True
    for var in ("NUBI_CLOUD", "NUBI_EE"):
        if os.environ.get(var, "").strip().lower() in ("1", "true", "yes", "on"):
            return True
    return False

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


def discover_migrations(include_ee: bool = False) -> list[tuple[str, Path]]:
    """Return ``(version, path)`` for each migration in apply order.

    Core migrations (``database/migrations/*.sql``) come first, keyed by file
    name. When *include_ee* is set, EE/cloud migrations
    (``database/migrations/ee/*.sql``) are appended, keyed as ``ee/<file>`` so
    they never collide with core versions and are applied after core.
    """
    if not MIGRATIONS_DIR.is_dir():
        print(
            f"ERROR: migrations directory not found: {MIGRATIONS_DIR}",
            file=sys.stderr,
        )
        sys.exit(1)
    out: list[tuple[str, Path]] = [
        (f.name, f) for f in sorted(MIGRATIONS_DIR.glob("*.sql"))
    ]
    if include_ee and EE_MIGRATIONS_DIR.is_dir():
        out += [(f"ee/{f.name}", f) for f in sorted(EE_MIGRATIONS_DIR.glob("*.sql"))]
    return out


async def ensure_ledger(conn: asyncpg.Connection) -> None:
    """Create the schema_migrations table if it does not yet exist."""
    await conn.execute(CREATE_LEDGER_SQL)


async def applied_versions(conn: asyncpg.Connection) -> set[str]:
    """Return the set of migration versions already recorded in the ledger."""
    rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
    return {row["version"] for row in rows}


def legacy_ee_rekeys(
    versions: list[str], applied: set[str]
) -> list[tuple[str, str]]:
    """Return ``(legacy_version, ee_version)`` ledger re-keys that are needed.

    Migrations that moved from ``database/migrations/`` into
    ``database/migrations/ee/`` (0017_billing, 0018_fx_rates, 0022_wallet,
    0027_invoices) were recorded by already-deployed databases under their
    bare file name (e.g. ``0017_billing.sql``). Such a legacy ledger row
    satisfies the new ``ee/<file>`` key — the migration must NOT be
    re-applied (re-running ee/0018 would transiently swap the subscriptions
    tier CHECK). The caller re-keys the matched rows so the ledger converges
    on the new keys.
    """
    out: list[tuple[str, str]] = []
    for version in versions:
        if not version.startswith("ee/"):
            continue
        legacy = version[len("ee/"):]
        if legacy in applied and version not in applied:
            out.append((legacy, version))
    return out


async def rekey_legacy_ee_versions(
    conn: asyncpg.Connection, versions: list[str]
) -> set[str]:
    """Re-key legacy bare ledger rows to their ``ee/<file>`` version.

    Returns the (possibly updated) set of applied versions.
    """
    applied = await applied_versions(conn)
    rekeys = legacy_ee_rekeys(versions, applied)
    for legacy, version in rekeys:
        await conn.execute(
            "UPDATE schema_migrations SET version = $1 WHERE version = $2",
            version,
            legacy,
        )
        print(f"  Ledger: re-keyed {legacy} -> {version} (migration moved into ee/).")
    if rekeys:
        applied = await applied_versions(conn)
    return applied


async def apply_migrations(url: str, include_ee: bool = False) -> None:
    """Apply all pending migrations to the database."""
    conn: asyncpg.Connection = await asyncpg.connect(url)
    try:
        # Serialize concurrent runners BEFORE reading the ledger: two
        # simultaneous runs (multi-replica deploy, CI + manual) would
        # otherwise compute the same pending set and race on the DDL.
        await conn.execute("SELECT pg_advisory_lock($1)", MIGRATION_LOCK_ID)
        await ensure_ledger(conn)
        migrations = discover_migrations(include_ee)
        done = await rekey_legacy_ee_versions(conn, [v for v, _ in migrations])
        pending = [(v, p) for (v, p) in migrations if v not in done]

        if include_ee:
            print("EE/cloud migrations: ENABLED (billing schema will be applied).")
        else:
            print("EE/cloud migrations: skipped (OSS core schema only). Enable with --ee or NUBI_CLOUD=1.")

        if not pending:
            print("No pending migrations — database is up to date.")
            return

        for version, migration_file in pending:
            sql = migration_file.read_text(encoding="utf-8")
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


async def show_status(url: str, include_ee: bool = False) -> None:
    """Print a table of applied vs pending migration files."""
    conn: asyncpg.Connection = await asyncpg.connect(url)
    try:
        await ensure_ledger(conn)
        done = await applied_versions(conn)
        all_files = discover_migrations(include_ee)

        print(f"EE/cloud migrations: {'included' if include_ee else 'excluded (--ee / NUBI_CLOUD to include)'}")
        print(f"{'VERSION':<40}  {'STATUS':<10}  APPLIED_AT")
        print("-" * 70)

        # Print applied rows (with timestamp) in order
        rows = await conn.fetch(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        )
        applied_map = {row["version"]: row["applied_at"] for row in rows}

        versions = {v for v, _ in all_files}

        # Legacy bare-key ledger rows for migrations that moved into ee/
        # count as applied (the apply run re-keys them; --status stays
        # read-only). Maps "ee/<file>" -> "<file>".
        alias_for = {
            ee: legacy
            for legacy, ee in legacy_ee_rekeys(
                [v for v, _ in all_files], set(applied_map)
            )
        }

        # Show all known files first (apply order: core then ee)
        for version, _ in all_files:
            if version in applied_map:
                ts = applied_map[version].strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"{version:<40}  {'applied':<10}  {ts}")
            elif version in alias_for:
                ts = applied_map[alias_for[version]].strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"{version:<40}  {'applied':<10}  {ts}  [legacy key: {alias_for[version]}]")
            else:
                print(f"{version:<40}  {'pending':<10}  —")

        # Any applied versions not found on disk (e.g. after a rollback of file)
        for version, ts in sorted(applied_map.items()):
            if version not in versions and version not in alias_for.values():
                ts_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"{version:<40}  {'applied*':<10}  {ts_str}  [file missing]")

        print()
        pending_count = len(
            [v for v, _ in all_files if v not in done and v not in alias_for]
        )
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
    parser.add_argument(
        "--ee",
        action="store_true",
        help="Also apply EE/cloud migrations (billing). Or set NUBI_CLOUD=1 / NUBI_EE=1.",
    )
    args = parser.parse_args()

    url = get_database_url()
    include_ee = ee_enabled(args.ee)

    if args.status:
        asyncio.run(show_status(url, include_ee))
    else:
        asyncio.run(apply_migrations(url, include_ee))


if __name__ == "__main__":
    main()
