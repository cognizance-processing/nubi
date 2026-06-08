"""Seed the superuser, and optionally the comprehensive demo workspace.

Mirrors the /auth/register flow: argon2id password hash + a personal org with
owner membership, so the seeded user can use the editor/boards immediately.

With ``--demo`` it ALSO materialises the full demo workspace for that user — one
read-only DuckDB connector + the demo queries + all 10 dashboards — from the
declarative fixtures in ``seed_data/demo/`` (see ``app/demo_bundle.py``).  New
projects get only the small ``starter`` subset of those same fixtures, seeded by
``app/sample.py`` on signup.

Usage:
    cd backend && DATABASE_URL=postgresql://... python seed.py           # superuser only
    cd backend && DATABASE_URL=postgresql://... python seed.py --demo    # + full demo
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import Any

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import close_db, execute, fetchrow, init_db
from app.demo_bundle import (
    datastore_config,
    load_boards,
    load_queries,
    resolve_placeholders,
    sample_db_path,
)
from app.routes.auth import _create_personal_org

# Superuser credentials come from the environment (SUPERUSER_* in the root .env),
# so the DB reset/seed flow always provisions the same known admin login.
_s = get_settings()
TEST_EMAIL = _s.SUPERUSER_EMAIL
TEST_PASSWORD = _s.SUPERUSER_PASSWORD
TEST_NAME = _s.SUPERUSER_NAME

DEMO_DS = "demo:datastore:duckdb"


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


# ── Idempotent upsert keyed by config.seed_id ─────────────────────────────────

async def _upsert(table: str, seed_id: str, org_id: str, created_by: str,
                  name: str, config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    existing = await fetchrow(
        f"SELECT * FROM {table} WHERE org_id = $1::uuid AND config->>'seed_id' = $2 LIMIT 1",
        org_id, seed_id,
    )
    if existing is not None:
        return dict(existing), False
    from app.repos import projects as projects_repo
    project_id = await projects_repo.get_default_project_id(org_id)
    cfg = json.dumps({**config, "seed_id": seed_id})
    row = await fetchrow(
        f"INSERT INTO {table} (org_id, created_by, name, config, project_id) "
        f"VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::uuid) RETURNING *",
        org_id, created_by, name, cfg, project_id,
    )
    assert row is not None
    return dict(row), True


async def _seed_demo(user_id: str) -> None:
    """Materialise the full demo workspace (all fixtures) for the superuser org."""
    org_row = await fetchrow(
        "SELECT org_id FROM org_members WHERE user_id = $1::uuid ORDER BY org_id LIMIT 1",
        user_id,
    )
    assert org_row is not None, "Superuser has no org membership."
    org_id = str(org_row["org_id"])

    # 1. Datasource — one read-only DuckDB connector over the bundled file.
    db_path = sample_db_path()
    ds, ds_created = await _upsert("datastores", DEMO_DS, org_id, user_id, "Demo Data",
                                  datastore_config(db_path))
    datastore_id = str(ds["id"])

    # 2. Queries (all of them) — build the @placeholder → uuid map.
    queries = load_queries()
    idmap: dict[str, str] = {}
    q_created = 0
    for key, q in queries.items():
        row, created = await _upsert(
            "queries", f"demo:query:{key}", org_id, user_id, q["name"],
            {"sql": q["sql"], "datastore_id": datastore_id, "params": q["params"]},
        )
        idmap[f"@{key}"] = str(row["id"])
        q_created += int(created)

    # 3. Boards — resolve @placeholders to real query UUIDs.
    boards = load_boards()
    b_created = 0
    for b in boards:
        spec = resolve_placeholders(b["spec"], idmap)
        _row, created = await _upsert("boards", b["seed_id"], org_id, user_id, b["name"],
                                     {"spec": spec})
        b_created += int(created)

    print(f"  demo datastore [{'CREATED' if ds_created else 'exists '}]  Demo Data ({db_path})")
    print(f"  demo queries   {q_created} created / {len(queries)} total")
    print(f"  demo boards    {b_created} created / {len(boards)} total")


async def main() -> None:
    demo = "--demo" in sys.argv
    await init_db()
    try:
        user_id = await _ensure_superuser()
        print(f"Superuser: {TEST_EMAIL} / {TEST_PASSWORD}")
        if demo:
            await _seed_demo(user_id)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
