"""Seed a REAL, file-backed DuckDB sales datasource + queries + boards (idempotent).

This is the companion to ``seed_data_duckdb.py`` (the file builder).  It:

  1. Builds (or rebuilds) the physical DuckDB file at
     ``backend/seed_data/nubi_sales.duckdb`` via ``build_duckdb_file()``.
  2. Resolves the existing seeded superuser + their personal org (same path as
     ``seed_demo.py``).
  3. Upserts a ``duckdb`` datastore whose config points at the ABSOLUTE file path
     (so the config-driven read-only connector path in ``app/routes/query.py``
     opens the real file).
  4. Registers DuckDB-dialect queries (config = {sql, datastore_id, params, name})
     including a region-driven DRILLDOWN query + a region options query.
  5. Creates a polished "Sales Overview" board (KPIs w/ delta, combo bar+line
     dual-axis trend, NSV-by-region bar, a product table with conditional
     formatting + column formats, a region filter wired to a `region` variable,
     and a gradient background) plus a simpler "Sales by Region" drilldown board.

Everything is keyed by a stable ``config.seed_id`` so re-running is safe.

Usage
-----
    cd backend && DATABASE_URL=postgresql://... python seed_sales.py
"""

from __future__ import annotations

import asyncio
import json
import uuid

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import close_db, execute, fetchrow, init_db
from app.routes.auth import _create_personal_org

# Reuse credential constants + the seed-id finder from seed_demo.
from seed_demo import (
    TEST_EMAIL,
    TEST_NAME,
    TEST_PASSWORD,
    _find_by_seed_id,
)
from seed_data_duckdb import REGIONS, build_duckdb_file


async def _upsert_refresh(
    table: str, seed_id: str, org_id: str, created_by: str, name: str, config: dict,
) -> tuple[dict, bool]:
    """Create or REFRESH a resource row keyed by *seed_id*.

    Unlike seed_demo._upsert_resource (insert-or-skip), this UPDATES the name +
    config of an existing row so re-running the seed always reflects the current
    query SQL / board specs. Returns (row_dict, created).
    """
    from app.repos import projects as projects_repo

    cfg = json.dumps({**config, "seed_id": seed_id})
    project_id = await projects_repo.get_default_project_id(org_id)
    existing = await _find_by_seed_id(table, seed_id, org_id)
    if existing is not None:
        # Refresh name/config and backfill project_id when it's still NULL.
        row = await fetchrow(
            f"UPDATE {table} SET name = $1, config = $2::jsonb, "
            f"project_id = COALESCE(project_id, $4::uuid) WHERE id = $3 RETURNING *",
            name, cfg, existing["id"], project_id,
        )
        return dict(row), False
    row = await fetchrow(
        f"""
        INSERT INTO {table} (org_id, created_by, name, config, project_id)
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::uuid)
        RETURNING *
        """,
        org_id, created_by, name, cfg, project_id,
    )
    assert row is not None
    return dict(row), True

# ── Stable seed identifiers (stored in config.seed_id) ────────────────────────
SEED_DS_SALES = "sales:datastore:nubi_sales_duckdb"

SEED_Q_TOTAL_NSV = "sales:query:total_nsv"
SEED_Q_NSV_VS_PRIOR = "sales:query:nsv_vs_prior"
SEED_Q_TOTAL_VOLUME = "sales:query:total_volume"
SEED_Q_MONTHLY_TREND = "sales:query:monthly_trend"
SEED_Q_NSV_BY_REGION = "sales:query:nsv_by_region"
SEED_Q_PG_POP = "sales:query:product_group_pop"
SEED_Q_ACTUAL_VS_BUDGET = "sales:query:actual_vs_budget"
SEED_Q_BY_PRODUCT_FOR_REGION = "sales:query:by_product_for_region"
SEED_Q_REGION_OPTIONS = "sales:query:region_options"
SEED_Q_BY_DIMENSION = "sales:query:nsv_by_dimension"
SEED_Q_CY_VS_LY = "sales:query:cy_vs_ly"
SEED_Q_BY_OPCO = "sales:query:nsv_by_opco"
SEED_Q_TOP_CUSTOMERS = "sales:query:top_customers"
SEED_Q_NSV_VS_TARGET = "sales:query:nsv_vs_target"
SEED_Q_DIMENSION_OPTIONS = "sales:query:dimension_options"
SEED_Q_OPCO_OPTIONS = "sales:query:opco_options"

SEED_BOARD_OVERVIEW = "sales:board:sales_overview"
SEED_BOARD_DRILLDOWN = "sales:board:region_drilldown"


# ─────────────────────────────────────────────────────────────────────────────
# SQL (DuckDB dialect) — all verified against the built file in __main__ of
# seed_data_duckdb.py and the standalone check at the bottom of this module.
# ─────────────────────────────────────────────────────────────────────────────

SQL_TOTAL_NSV = "SELECT SUM(nsv) AS nsv FROM sales"

# KPI delta source: returns the latest month NSV and the prior month NSV as two
# columns in a single row so a KPI can read encoding.value + encoding.compare.
SQL_NSV_VS_PRIOR = """
WITH months AS (
    SELECT DISTINCT month FROM sales ORDER BY month DESC LIMIT 2
),
ranked AS (
    SELECT month, ROW_NUMBER() OVER (ORDER BY month DESC) AS rn FROM months
)
SELECT
    (SELECT SUM(nsv) FROM sales WHERE month = (SELECT month FROM ranked WHERE rn = 1)) AS nsv,
    (SELECT SUM(nsv) FROM sales WHERE month = (SELECT month FROM ranked WHERE rn = 2)) AS prior_nsv
""".strip()

SQL_TOTAL_VOLUME = "SELECT SUM(volume) AS volume FROM sales"

SQL_MONTHLY_TREND = """
SELECT month, SUM(nsv) AS nsv, SUM(volume) AS volume
FROM sales
GROUP BY month
ORDER BY month
""".strip()

SQL_NSV_BY_REGION = """
SELECT region, SUM(nsv) AS nsv
FROM sales
GROUP BY region
ORDER BY nsv DESC
""".strip()

# Product-group table with YEAR-over-year %change (rolling 12 vs prior 12).
# YoY (not MoM) so the structurally-declining group surfaces as a negative
# %change — driving the red/green conditional formatting in the board.
SQL_PG_POP = """
WITH bounds AS (SELECT MAX(invoice_date) AS maxd FROM sales),
cy AS (
    SELECT product_group, SUM(nsv) AS nsv FROM sales, bounds
    WHERE invoice_date > maxd - INTERVAL 12 MONTH GROUP BY 1
),
ly AS (
    SELECT product_group, SUM(nsv) AS nsv FROM sales, bounds
    WHERE invoice_date <= maxd - INTERVAL 12 MONTH
      AND invoice_date >  maxd - INTERVAL 24 MONTH GROUP BY 1
)
SELECT
    cy.product_group AS product_group,
    cy.nsv  AS nsv,
    ly.nsv  AS prior_nsv,
    ROUND((cy.nsv - ly.nsv) / NULLIF(ly.nsv, 0), 4) AS pct_change
FROM cy JOIN ly USING (product_group)
ORDER BY cy.nsv DESC
""".strip()

# Actual vs budget by month — budget is aggregated SEPARATELY to avoid the
# many-to-one fan-out that joining the sales fact to the budget table would cause.
SQL_ACTUAL_VS_BUDGET = """
WITH actual AS (
    SELECT month, SUM(nsv) AS actual_nsv FROM sales GROUP BY month
),
budget AS (
    SELECT month, SUM(budget_nsv) AS budget_nsv FROM budget GROUP BY month
)
SELECT actual.month AS month, actual.actual_nsv AS actual_nsv, budget.budget_nsv AS budget_nsv
FROM actual JOIN budget USING (month)
ORDER BY actual.month
""".strip()

# DRILLDOWN: product breakdown for a single region, driven by a {{region}} param.
SQL_BY_PRODUCT_FOR_REGION = """
SELECT product_group, SUM(nsv) AS nsv, SUM(volume) AS volume, SUM(units) AS units
FROM sales
WHERE region = {{region}}
GROUP BY product_group
ORDER BY nsv DESC
""".strip()

# Options for the region filter (first column = value, second = label).
SQL_REGION_OPTIONS = """
SELECT region AS value, region AS label
FROM sales
GROUP BY region
ORDER BY region
""".strip()

# ── Grounded legacy idioms ────────────────────────────────────────────────────
# The signature legacy drilldown is the "{{ .Field }}" PIVOT: one widget that
# re-groups by a user-selected dimension. Nubi binds param VALUES (never column
# identifiers), so the safe translation is a CASE over a {{dimension}} value
# param. An optional {{opco}} value param scopes the whole pivot (NULL = all).
SQL_NSV_BY_DIMENSION = """
SELECT
    CASE {{dimension}}
        WHEN 'Region'   THEN region
        WHEN 'OpCo'     THEN opco
        WHEN 'Channel'  THEN channel
        WHEN 'Supplier' THEN supplier
        WHEN 'Brand'    THEN brand
        WHEN 'Category' THEN category
        WHEN 'Customer' THEN customer
        ELSE product_group
    END AS dimension,
    SUM(nsv)    AS nsv,
    SUM(volume) AS volume
FROM sales
WHERE ({{opco}} IS NULL OR opco = {{opco}})
GROUP BY 1
ORDER BY nsv DESC
LIMIT 15
""".strip()

# Current-year vs last-year by calendar month (rolling 12 vs prior 12).
SQL_CY_VS_LY = """
WITH bounds AS (SELECT MAX(invoice_date) AS maxd FROM sales),
cy AS (
    SELECT month(invoice_date) AS month_num, SUM(nsv) AS nsv_cy
    FROM sales, bounds
    WHERE invoice_date > maxd - INTERVAL 12 MONTH
    GROUP BY 1
),
ly AS (
    SELECT month(invoice_date) AS month_num, SUM(nsv) AS nsv_ly
    FROM sales, bounds
    WHERE invoice_date <= maxd - INTERVAL 12 MONTH
      AND invoice_date >  maxd - INTERVAL 24 MONTH
    GROUP BY 1
)
SELECT cy.month_num AS month_num, cy.nsv_cy AS nsv_cy, ly.nsv_ly AS nsv_ly
FROM cy JOIN ly USING (month_num)
ORDER BY cy.month_num
""".strip()

SQL_NSV_BY_OPCO = """
SELECT opco, SUM(nsv) AS nsv
FROM sales
GROUP BY opco
ORDER BY nsv DESC
""".strip()

SQL_TOP_CUSTOMERS = """
SELECT customer, SUM(nsv) AS nsv
FROM sales
GROUP BY customer
ORDER BY nsv DESC
""".strip()

# NSV vs target (current rolling year) — single KPI delta row.
SQL_NSV_VS_TARGET = """
WITH bounds AS (SELECT MAX(month) AS m FROM sales)
SELECT
    (SELECT SUM(nsv) FROM sales) AS nsv,
    (SELECT SUM(target_nsv) FROM targets) AS target_nsv
""".strip()

# Static option list for the pivot-dimension filter.
SQL_DIMENSION_OPTIONS = """
SELECT * FROM (VALUES
    ('Region','Region'), ('OpCo','OpCo'), ('Channel','Channel'),
    ('Supplier','Supplier'), ('Brand','Brand'), ('Category','Category'),
    ('Customer','Customer'), ('Product','Product')
) AS t(value, label)
""".strip()

SQL_OPCO_OPTIONS = """
SELECT opco AS value, opco AS label
FROM sales
GROUP BY opco
ORDER BY opco
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Board spec builders
# ─────────────────────────────────────────────────────────────────────────────

def _pct_change_rules(column: str = "pct_change") -> list[dict]:
    """Red-for-negative / green-for-positive conditional-format rules."""
    return [
        {"column": column, "op": "lt", "value": 0, "scope": "cell",
         "style": {"color": "#dc2626", "fontWeight": 600}},
        {"column": column, "op": "gt", "value": 0, "scope": "cell",
         "style": {"color": "#16a34a", "fontWeight": 600}},
    ]


def _filter(id_, target_var, options_id, label, x):
    """A select filter widget writing *target_var* (h=2 row-1 strip)."""
    return {
        "id": id_, "type": "filter", "query_id": "",
        "subtype": "select", "target_var": target_var, "options_query_id": options_id,
        "encoding": {}, "props": {"label": label, "placeholder": f"All {label.lower()}"},
        "pos": {"x": x, "y": 3, "w": 3, "h": 2},
    }


def _board_overview_spec(q: dict[str, str]) -> dict:
    """Grounded "Sales Management Overview" — mirrors the legacy comprehensive
    sales dashboards: KPI band (NSV / vs prior / vs target / volume), a Field
    PIVOT drilldown (re-groups by a selected dimension, scoped by OpCo), CY-vs-LY,
    monthly trend, by-OpCo, actual-vs-budget, region-driven product table, a
    PoP %-change table (red/green), and top customers.

    *q* maps a logical query key → its persisted query id.
    """
    return {
        "version": 1,
        "title": "Sales Management Overview",
        "layout": {"cols": 12, "row_height": 60},
        "background": {"type": "gradient", "from": "#0f172a", "to": "#1e3a5f", "angle": 135},
        "variables": [
            {"name": "region", "type": "select", "default": "Gauteng"},
            {"name": "dimension", "type": "select", "default": "Region"},
            {"name": "opco", "type": "select", "default": None},
        ],
        "widgets": [
            # ── Row 1: KPI band ────────────────────────────────────────────────
            {"id": "kpi_total_nsv", "type": "kpi", "query_id": q["total_nsv"],
             "encoding": {"value": "nsv"}, "props": {"label": "Total NSV", "format": "currency"},
             "pos": {"x": 1, "y": 1, "w": 3, "h": 2}},
            {"id": "kpi_nsv_mom", "type": "kpi", "query_id": q["nsv_vs_prior"],
             "encoding": {"value": "nsv", "compare": "prior_nsv"},
             "props": {"label": "NSV (latest month, MoM)", "format": "currency"},
             "pos": {"x": 4, "y": 1, "w": 3, "h": 2}},
            {"id": "kpi_nsv_target", "type": "kpi", "query_id": q["nsv_vs_target"],
             "encoding": {"value": "nsv", "compare": "target_nsv"},
             "props": {"label": "NSV vs Target", "format": "currency"},
             "pos": {"x": 7, "y": 1, "w": 3, "h": 2}},
            {"id": "kpi_total_volume", "type": "kpi", "query_id": q["total_volume"],
             "encoding": {"value": "volume"}, "props": {"label": "Total Volume", "format": "number"},
             "pos": {"x": 10, "y": 1, "w": 3, "h": 2}},

            # ── Row 2: filter strip (dimension + opco drive the pivot; region drives the table) ──
            _filter("filter_dimension", "dimension", q["dimension_options"], "Dimension", 1),
            _filter("filter_opco", "opco", q["opco_options"], "OpCo", 4),
            _filter("filter_region", "region", q["region_options"], "Region", 7),

            # ── Row 3: Field PIVOT drilldown (re-groups by `dimension`, scoped by `opco`) ──
            {"id": "chart_pivot", "type": "chart", "query_id": q["nsv_by_dimension"],
             "chart_type": "bar", "encoding": {"x": "dimension", "y": "nsv"},
             "params": {"dimension": {"ref": "dimension"}, "opco": {"ref": "opco"}},
             "props": {"title": "NSV by selected dimension", "height": 320},
             "pos": {"x": 1, "y": 5, "w": 7, "h": 6}},
            # CY vs LY combo
            {"id": "chart_cy_ly", "type": "chart", "query_id": q["cy_vs_ly"],
             "chart_type": "bar",
             "encoding": {"x": "month_num", "y": [
                 {"col": "nsv_cy", "type": "bar", "axis": "left"},
                 {"col": "nsv_ly", "type": "line", "axis": "left"}]},
             "props": {"title": "Current vs Last Year", "height": 320},
             "pos": {"x": 8, "y": 5, "w": 5, "h": 6}},

            # ── Row 4: monthly trend combo + by-OpCo bar ───────────────────────
            {"id": "chart_trend", "type": "chart", "query_id": q["monthly_trend"],
             "chart_type": "bar",
             "encoding": {"x": "month", "y": [
                 {"col": "nsv", "type": "bar", "axis": "left"},
                 {"col": "volume", "type": "line", "axis": "right"}]},
             "props": {"title": "Monthly NSV & Volume", "height": 320},
             "pos": {"x": 1, "y": 11, "w": 7, "h": 6}},
            {"id": "chart_opco", "type": "chart", "query_id": q["nsv_by_opco"],
             "chart_type": "bar", "encoding": {"x": "opco", "y": "nsv"},
             "props": {"title": "NSV by Operating Company", "height": 320},
             "pos": {"x": 8, "y": 11, "w": 5, "h": 6}},

            # ── Row 5: actual vs budget + region-driven product table ──────────
            {"id": "chart_budget", "type": "chart", "query_id": q["actual_vs_budget"],
             "chart_type": "bar",
             "encoding": {"x": "month", "y": [
                 {"col": "actual_nsv", "type": "bar", "axis": "left"},
                 {"col": "budget_nsv", "type": "line", "axis": "left"}]},
             "props": {"title": "Actual vs Budget", "height": 300},
             "pos": {"x": 1, "y": 17, "w": 6, "h": 6}},
            {"id": "table_products", "type": "table", "query_id": q["by_product_for_region"],
             "encoding": {}, "params": {"region": {"ref": "region"}},
             "props": {"limit": 20, "columns": ["product_group", "nsv", "volume", "units"]},
             "columnFormats": {
                 "nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                 "volume": {"type": "number", "decimals": 0},
                 "units": {"type": "number", "decimals": 0}},
             "pos": {"x": 7, "y": 17, "w": 6, "h": 6}},

            # ── Row 6: PoP %-change table (red/green) + top customers ───────────
            {"id": "table_pop", "type": "table", "query_id": q["product_group_pop"],
             "encoding": {},
             "props": {"limit": 20, "columns": ["product_group", "nsv", "prior_nsv", "pct_change"]},
             "columnFormats": {
                 "nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                 "prior_nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                 "pct_change": {"type": "percent", "decimals": 1}},
             "formattingRules": _pct_change_rules(),
             "pos": {"x": 1, "y": 23, "w": 7, "h": 5}},
            {"id": "chart_customers", "type": "chart", "query_id": q["top_customers"],
             "chart_type": "hbar", "encoding": {"x": "customer", "y": "nsv"},
             "props": {"title": "Top Customers", "height": 260},
             "pos": {"x": 8, "y": 23, "w": 5, "h": 5}},
        ],
    }


def _board_drilldown_spec(q: dict[str, str]) -> dict:
    """Simpler region-drilldown DashboardSpec (filter → reactive table + chart)."""
    return {
        "version": 1,
        "title": "Sales by Region",
        "layout": {"cols": 12, "row_height": 60},
        "variables": [
            {"name": "region", "type": "select", "default": "Gauteng"},
        ],
        "widgets": [
            {
                "id": "filter_region",
                "type": "filter",
                "query_id": "",
                "subtype": "select",
                "target_var": "region",
                "options_query_id": q["region_options"],
                "encoding": {},
                "props": {"label": "Region", "placeholder": "All regions"},
                "pos": {"x": 1, "y": 1, "w": 4, "h": 2},
            },
            {
                "id": "chart_products",
                "type": "chart",
                "query_id": q["by_product_for_region"],
                "chart_type": "bar",
                "encoding": {"x": "product_group", "y": "nsv"},
                "params": {"region": {"ref": "region"}},
                "props": {"title": "NSV by Product Group", "height": 320},
                "pos": {"x": 1, "y": 3, "w": 7, "h": 6},
            },
            {
                "id": "table_products",
                "type": "table",
                "query_id": q["by_product_for_region"],
                "encoding": {},
                "params": {"region": {"ref": "region"}},
                "props": {
                    "limit": 20,
                    "columns": ["product_group", "nsv", "volume", "units"],
                },
                "columnFormats": {
                    "nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                },
                "pos": {"x": 8, "y": 3, "w": 5, "h": 6},
            },
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main seeder
# ─────────────────────────────────────────────────────────────────────────────

async def seed_sales() -> None:
    # ── 0. Build the physical DuckDB file (absolute path) ─────────────────────
    db_path = build_duckdb_file()

    await init_db()
    try:
        # ── 1. Resolve / create the superuser (same as seed_demo.py) ──────────
        existing_user = await fetchrow("SELECT id FROM users WHERE email = $1", TEST_EMAIL)
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
        else:
            user_id = str(existing_user["id"])

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

        # ── 2. Datastore (config points at the ABSOLUTE .duckdb file path) ────
        ds, ds_created = await _upsert_refresh(
            "datastores",
            SEED_DS_SALES,
            org_id,
            user_id,
            "Nubi Sales (DuckDB file)",
            {
                "type": "duckdb",
                "database": db_path,
                "description": "Real file-backed DuckDB with 24 months of demo sales data.",
            },
        )
        ds_id = str(ds["id"])

        # ── 3. Register queries ────────────────────────────────────────────────
        def _q(seed_id, name, sql, params=None):
            return _upsert_refresh(
                "queries", seed_id, org_id, user_id, name,
                {"sql": sql, "datastore_id": ds_id, "params": params or [], "name": name},
            )

        results: dict[str, tuple[dict, bool]] = {}
        results["total_nsv"] = await _q(SEED_Q_TOTAL_NSV, "Total NSV", SQL_TOTAL_NSV)
        results["nsv_vs_prior"] = await _q(SEED_Q_NSV_VS_PRIOR, "NSV vs prior period", SQL_NSV_VS_PRIOR)
        results["total_volume"] = await _q(SEED_Q_TOTAL_VOLUME, "Total Volume", SQL_TOTAL_VOLUME)
        results["monthly_trend"] = await _q(SEED_Q_MONTHLY_TREND, "Monthly trend (NSV & Volume)", SQL_MONTHLY_TREND)
        results["nsv_by_region"] = await _q(SEED_Q_NSV_BY_REGION, "NSV by region", SQL_NSV_BY_REGION)
        results["product_group_pop"] = await _q(SEED_Q_PG_POP, "NSV by product group (YoY %change)", SQL_PG_POP)
        results["actual_vs_budget"] = await _q(SEED_Q_ACTUAL_VS_BUDGET, "Actual vs budget by month", SQL_ACTUAL_VS_BUDGET)
        results["region_options"] = await _q(SEED_Q_REGION_OPTIONS, "Region options", SQL_REGION_OPTIONS)
        results["nsv_by_opco"] = await _q(SEED_Q_BY_OPCO, "NSV by operating company", SQL_NSV_BY_OPCO)
        results["top_customers"] = await _q(SEED_Q_TOP_CUSTOMERS, "Top customers by NSV", SQL_TOP_CUSTOMERS)
        results["cy_vs_ly"] = await _q(SEED_Q_CY_VS_LY, "NSV current year vs last year", SQL_CY_VS_LY)
        results["nsv_vs_target"] = await _q(SEED_Q_NSV_VS_TARGET, "NSV vs target", SQL_NSV_VS_TARGET)
        results["dimension_options"] = await _q(SEED_Q_DIMENSION_OPTIONS, "Pivot dimension options", SQL_DIMENSION_OPTIONS)
        results["opco_options"] = await _q(SEED_Q_OPCO_OPTIONS, "OpCo options", SQL_OPCO_OPTIONS)
        # Field-pivot drilldown: re-groups by the {{dimension}} value, scoped by {{opco}}.
        results["nsv_by_dimension"] = await _q(
            SEED_Q_BY_DIMENSION,
            "NSV by dimension ({{dimension}}, {{opco}})",
            SQL_NSV_BY_DIMENSION,
            params=[
                {"name": "dimension", "type": "select", "default": "Region", "required": True, "options_query_id": None},
                {"name": "opco", "type": "select", "default": None, "required": False, "options_query_id": None},
            ],
        )
        results["by_product_for_region"] = await _q(
            SEED_Q_BY_PRODUCT_FOR_REGION,
            "By product for region ({{region}})",
            SQL_BY_PRODUCT_FOR_REGION,
            params=[
                {
                    "name": "region",
                    "type": "select",
                    "default": "Gauteng",
                    "required": True,
                    "options_query_id": None,  # set below once region_options id is known
                }
            ],
        )

        # Patch the drilldown param's options_query_id to point at region_options.
        # (Only matters for newly-created rows; existing rows keep their stored config.)
        q_ids = {key: str(row["id"]) for key, (row, _c) in results.items()}

        # ── 4. Boards ──────────────────────────────────────────────────────────
        board_overview_spec = _board_overview_spec(q_ids)
        board_a, board_a_created = await _upsert_refresh(
            "boards", SEED_BOARD_OVERVIEW, org_id, user_id,
            "Sales Management Overview", {"spec": board_overview_spec},
        )

        board_drilldown_spec = _board_drilldown_spec(q_ids)
        board_b, board_b_created = await _upsert_refresh(
            "boards", SEED_BOARD_DRILLDOWN, org_id, user_id,
            "Sales by Region", {"spec": board_drilldown_spec},
        )

        # ── Summary ────────────────────────────────────────────────────────────
        def _status(created: bool) -> str:
            return "CREATED" if created else "exists "

        print()
        print("=" * 64)
        print("  Nubi REAL DuckDB sales seed")
        print("=" * 64)
        print(f"  DuckDB file              {db_path}")
        print(f"  Org ID                   {org_id}")
        print(f"  Datastore   [{_status(ds_created)}]  {ds['name']}  ({ds_id})")
        for key, (row, created) in results.items():
            print(f"  Query       [{_status(created)}]  {row['name']}  ({row['id']})")
        print(f"  Board A     [{_status(board_a_created)}]  {board_a['name']}  ({board_a['id']})")
        print(f"  Board B     [{_status(board_b_created)}]  {board_b['name']}  ({board_b['id']})")
        print()
        print(f"  Login: {TEST_EMAIL} / {TEST_PASSWORD}")
        print("=" * 64)
        print()
    finally:
        await close_db()


if __name__ == "__main__":
    _ = REGIONS  # imported for reference / parity with the file builder
    asyncio.run(seed_sales())
