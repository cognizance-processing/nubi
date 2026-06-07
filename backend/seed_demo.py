"""Seed a full demo workspace into $DATABASE_URL (idempotent).

Creates:
  - 1 owner user   (reuses seed.py's TEST_EMAIL / TEST_PASSWORD)
  - 1 personal org for that user
  - 2 datastores   (both duckdb type, different configs)
  - 4 registered queries (2 plain, 2 with {{param}} declarations)
  - 2 boards:
      Board A — kpi + table + chart widgets (rich data dashboard)
      Board B — filter widget + spec variables (route-param / drill-down dashboard)
  - 1 scheduled job (query kind)

All objects are keyed by a stable "demo slug" stored in config.seed_id so the
script is safe to run multiple times (idempotent).  On each run it prints a
summary of what was found vs. newly created plus the login credentials.

Usage
-----
    cd backend && DATABASE_URL=postgresql://... python seed_demo.py
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.auth.passwords import hash_password
from app.db import close_db, execute, fetch, fetchrow, init_db
from app.routes.auth import _create_personal_org

# ── Superuser credentials (from SUPERUSER_* in the root .env) ─────────────────
from app.config import get_settings  # noqa: E402

_s = get_settings()
TEST_EMAIL = _s.SUPERUSER_EMAIL
TEST_PASSWORD = _s.SUPERUSER_PASSWORD
TEST_NAME = _s.SUPERUSER_NAME

# ── Stable seed identifiers (stored in config.seed_id) ────────────────────────
SEED_DS_SALES = "demo:datastore:sales_duckdb"
SEED_DS_ANALYTICS = "demo:datastore:analytics_duckdb"
SEED_Q_ALL = "demo:query:sales_all"
SEED_Q_ACTIVE = "demo:query:sales_active"
SEED_Q_BY_REGION = "demo:query:sales_by_region"
SEED_Q_POINTS = "demo:query:points_10k"
SEED_BOARD_OVERVIEW = "demo:board:sales_overview"
SEED_BOARD_FILTER = "demo:board:filter_dashboard"
SEED_JOB_DAILY = "demo:job:daily_sales_sync"

# ── Board spec helpers ─────────────────────────────────────────────────────────

def _board_overview_spec(q_all_id: str, q_active_id: str, q_points_id: str) -> dict[str, Any]:
    """DashboardSpec dict for Board A (kpi + table + chart)."""
    return {
        "version": 1,
        "title": "Sales Overview",
        "layout": {"cols": 12, "row_height": 60},
        "variables": [],
        "widgets": [
            {
                "id": "kpi_total",
                "type": "kpi",
                "query_id": q_all_id,
                "encoding": {"value": "amount"},
                "props": {"label": "Total Sales", "format": "currency"},
                "pos": {"x": 1, "y": 1, "w": 4, "h": 2},
            },
            {
                "id": "kpi_active",
                "type": "kpi",
                "query_id": q_active_id,
                "encoding": {"value": "amount"},
                "props": {"label": "Active Sales", "format": "currency"},
                "pos": {"x": 5, "y": 1, "w": 4, "h": 2},
            },
            {
                "id": "table_all",
                "type": "table",
                "query_id": q_all_id,
                "encoding": {},
                "props": {"limit": 20, "columns": ["id", "region", "amount", "active"]},
                "pos": {"x": 1, "y": 3, "w": 8, "h": 5},
            },
            {
                "id": "chart_scatter",
                "type": "chart",
                "query_id": q_points_id,
                "chart_type": "scatter",
                "encoding": {"x": "x", "y": "y", "color": "category"},
                "props": {},
                "pos": {"x": 9, "y": 1, "w": 4, "h": 7},
            },
        ],
    }


def _board_filter_spec(q_by_region_id: str) -> dict[str, Any]:
    """DashboardSpec dict for Board B (filter widget + spec variables)."""
    return {
        "version": 1,
        "title": "Sales by Region",
        "layout": {"cols": 12, "row_height": 60},
        "variables": [
            {"name": "region", "type": "select", "default": "north"},
        ],
        "widgets": [
            {
                "id": "filter_region",
                "type": "filter",
                "query_id": "",
                "subtype": "select",
                "target_var": "region",
                "encoding": {},
                "props": {"label": "Region"},
                "pos": {"x": 1, "y": 1, "w": 3, "h": 2},
            },
            {
                "id": "table_region",
                "type": "table",
                "query_id": q_by_region_id,
                "encoding": {},
                "props": {"limit": 50},
                "params": {"region": {"ref": "region"}},
                "pos": {"x": 1, "y": 3, "w": 12, "h": 6},
            },
        ],
    }


# ── Generic resource helpers ───────────────────────────────────────────────────

async def _find_by_seed_id(table: str, seed_id: str, org_id: str) -> dict[str, Any] | None:
    """Return the first row in *table* whose config->>'seed_id' matches."""
    row = await fetchrow(
        f"""
        SELECT * FROM {table}
        WHERE org_id = $1::uuid
          AND config->>'seed_id' = $2
        LIMIT 1
        """,
        org_id,
        seed_id,
    )
    if row is None:
        return None
    return dict(row)


async def _upsert_resource(
    table: str,
    seed_id: str,
    org_id: str,
    created_by: str,
    name: str,
    config: dict[str, Any],
    project_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create or return an existing resource row keyed by *seed_id*.

    Returns (row_dict, created: bool). New rows are assigned *project_id*
    (the org's default project).
    """
    existing = await _find_by_seed_id(table, seed_id, org_id)
    if existing is not None:
        return existing, False

    if project_id is None:
        from app.repos import projects as projects_repo

        project_id = await projects_repo.get_default_project_id(org_id)

    cfg = json.dumps({**config, "seed_id": seed_id})
    row = await fetchrow(
        f"""
        INSERT INTO {table} (org_id, created_by, name, config, project_id)
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::uuid)
        RETURNING *
        """,
        org_id,
        created_by,
        name,
        cfg,
        project_id,
    )
    assert row is not None
    return dict(row), True


# ── Job helper ─────────────────────────────────────────────────────────────────

async def _upsert_job(
    seed_id: str,
    org_id: str,
    created_by: str,
    name: str,
    kind: str,
    target: str,
    schedule: str,
    project_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create or return an existing job keyed by a stable name prefix."""
    rows = await fetch(
        """
        SELECT * FROM jobs
        WHERE org_id = $1::uuid AND name = $2
        LIMIT 1
        """,
        org_id,
        name,
    )
    if rows:
        return dict(rows[0]), False

    if project_id is None:
        from app.repos import projects as projects_repo

        project_id = await projects_repo.get_default_project_id(org_id)

    row = await fetchrow(
        """
        INSERT INTO jobs (org_id, created_by, name, kind, target, schedule, enabled, project_id)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, true, $7::uuid)
        RETURNING *
        """,
        org_id,
        created_by,
        name,
        kind,
        target,
        schedule,
        project_id,
    )
    assert row is not None
    return dict(row), True


# ── Main seeder ───────────────────────────────────────────────────────────────

async def seed_demo() -> None:
    """Idempotent demo workspace seed.  Prints a summary when done."""
    await init_db()
    try:
        # ── 1. Owner user ──────────────────────────────────────────────────────
        existing_user = await fetchrow(
            "SELECT id FROM users WHERE email = $1", TEST_EMAIL
        )
        user_created = False
        if existing_user is None:
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
            user_created = True
        else:
            user_id = str(existing_user["id"])

        # ── 2. Resolve org_id ──────────────────────────────────────────────────
        org_row = await fetchrow(
            """
            SELECT org_id FROM org_members
            WHERE user_id = $1::uuid
            ORDER BY org_id
            LIMIT 1
            """,
            user_id,
        )
        assert org_row is not None, "User has no org membership — seed.py invariant violated."
        org_id = str(org_row["org_id"])

        # ── 3. Datastores ──────────────────────────────────────────────────────
        ds_sales, ds_sales_created = await _upsert_resource(
            "datastores",
            SEED_DS_SALES,
            org_id,
            user_id,
            "Demo Sales DuckDB",
            {
                "type": "duckdb",
                "path": ":memory:",
                "description": "In-memory DuckDB with demo sales data.",
            },
        )

        ds_analytics, ds_analytics_created = await _upsert_resource(
            "datastores",
            SEED_DS_ANALYTICS,
            org_id,
            user_id,
            "Demo Analytics DuckDB",
            {
                "type": "duckdb",
                "path": ":memory:",
                "description": "In-memory DuckDB for point-cloud analytics demo.",
            },
        )

        # ── 4. Registered queries ──────────────────────────────────────────────
        # We persist these as rows in the `queries` table (config stores the SQL
        # and param declarations so the frontend/seeder can re-register them).

        q_all, q_all_created = await _upsert_resource(
            "queries",
            SEED_Q_ALL,
            org_id,
            user_id,
            "Sales — all rows",
            {
                "sql": "SELECT * FROM sales ORDER BY id",
                "datastore_id": str(ds_sales["id"]),
                "params": [],
            },
        )

        q_active, q_active_created = await _upsert_resource(
            "queries",
            SEED_Q_ACTIVE,
            org_id,
            user_id,
            "Sales — active rows",
            {
                "sql": "SELECT * FROM sales WHERE active = true ORDER BY id",
                "datastore_id": str(ds_sales["id"]),
                "params": [],
            },
        )

        q_by_region, q_by_region_created = await _upsert_resource(
            "queries",
            SEED_Q_BY_REGION,
            org_id,
            user_id,
            "Sales — by region ({{region}})",
            {
                "sql": "SELECT * FROM sales WHERE region = {{region}} ORDER BY amount DESC",
                "datastore_id": str(ds_sales["id"]),
                "params": [
                    {
                        "name": "region",
                        "type": "select",
                        "default": "north",
                        "required": False,
                        "options_query_id": None,
                    }
                ],
            },
        )

        q_points, q_points_created = await _upsert_resource(
            "queries",
            SEED_Q_POINTS,
            org_id,
            user_id,
            "Point cloud — 10 000 points ({{limit}})",
            {
                "sql": (
                    "SELECT i AS id, random() AS x, random() AS y, (i % 5) AS category"
                    " FROM generate_series(1, {{limit}}) AS t(i)"
                ),
                "datastore_id": str(ds_analytics["id"]),
                "params": [
                    {
                        "name": "limit",
                        "type": "number",
                        "default": 10000,
                        "required": False,
                        "options_query_id": None,
                    }
                ],
            },
        )

        # ── 5. Boards ──────────────────────────────────────────────────────────
        board_overview_spec = _board_overview_spec(
            str(q_all["id"]), str(q_active["id"]), str(q_points["id"])
        )
        board_a, board_a_created = await _upsert_resource(
            "boards",
            SEED_BOARD_OVERVIEW,
            org_id,
            user_id,
            "Sales Overview",
            {"spec": board_overview_spec},
        )

        board_filter_spec = _board_filter_spec(str(q_by_region["id"]))
        board_b, board_b_created = await _upsert_resource(
            "boards",
            SEED_BOARD_FILTER,
            org_id,
            user_id,
            "Sales by Region",
            {"spec": board_filter_spec},
        )

        # ── 6. Job ─────────────────────────────────────────────────────────────
        job, job_created = await _upsert_job(
            SEED_JOB_DAILY,
            org_id,
            user_id,
            "Daily Sales Sync",
            "query",
            str(q_all["id"]),
            "0 6 * * *",  # 06:00 UTC daily
        )

        # ── Summary ────────────────────────────────────────────────────────────
        def _status(created: bool) -> str:
            return "CREATED" if created else "exists "

        print()
        print("=" * 60)
        print("  Nubi demo workspace seed")
        print("=" * 60)
        print(f"  User        [{_status(user_created)}]  {TEST_EMAIL}")
        print(f"  Org ID                   {org_id}")
        print(f"  Datastore A [{_status(ds_sales_created)}]  {ds_sales['name']}")
        print(f"  Datastore B [{_status(ds_analytics_created)}]  {ds_analytics['name']}")
        print(f"  Query 1     [{_status(q_all_created)}]  {q_all['name']}")
        print(f"  Query 2     [{_status(q_active_created)}]  {q_active['name']}")
        print(f"  Query 3     [{_status(q_by_region_created)}]  {q_by_region['name']}")
        print(f"  Query 4     [{_status(q_points_created)}]  {q_points['name']}")
        print(f"  Board A     [{_status(board_a_created)}]  {board_a['name']}")
        print(f"  Board B     [{_status(board_b_created)}]  {board_b['name']}")
        print(f"  Job         [{_status(job_created)}]  {job['name']}")
        print()
        print("  Login credentials:")
        print(f"    email:    {TEST_EMAIL}")
        print(f"    password: {TEST_PASSWORD}")
        print("=" * 60)
        print()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(seed_demo())
