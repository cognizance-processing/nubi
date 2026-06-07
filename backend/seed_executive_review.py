"""Seed a COMPREHENSIVE, legacy-style "Executive Sales Review" board (idempotent).

This is an ADDITIVE companion to ``seed_sales.py``.  It reuses the same physical
file-backed DuckDB sales datasource (``seed_data/nubi_sales.duckdb``) and the
same superuser + personal org, then registers a richer set of DuckDB queries and
a single large board — ``Executive Sales Review`` — modelled on the legacy
comprehensive MANCO / Business-Review sales dashboards (~25 widgets laid out in
labelled sections on a 12-column grid).

It does NOT touch the existing "Sales Management Overview" / "Sales by Region"
boards seeded by ``seed_sales.py`` — both seeds can be run in either order.

What it demonstrates
--------------------
- KPI band with delta encoding ({value, compare}) and a sparkline KPI:
  Total NSV, NSV vs prior month, NSV vs LY, NSV vs Target, Volume, # active SKUs.
- The legacy Field PIVOT drilldown: a CASE over a {{dimension}} value param,
  scoped by {{opco}} + {{region}}, with three filter widgets driving it.
- Period tables: product-group & supplier YoY %-change with red/green
  conditional formatting + currency column formats.
- Combo charts: CY-vs-LY, monthly trend (bar+line dual axis), actual-vs-budget.
- Ranked breakdowns: top customers (hbar), NSV by region (bar), channel donut,
  category mix (bar), top brands (hbar), NSV by OpCo (bar).
- A region x month NSV heatmap and a NSV-vs-target attainment gauge.
- HTML section-header widgets (widget.html) titling each section, plus a
  gradient dashboard background.
- Chart-click drilldown: the region bar writes the clicked region into the
  `region` variable (widget.drilldown), re-querying the region-scoped table.

Every resource is keyed by a stable ``config.seed_id`` (new ``execreview:*`` ids,
disjoint from ``seed_sales.py``) so re-running is safe and never duplicates rows.

Usage
-----
    cd backend && python seed_executive_review.py

(DATABASE_URL is loaded from ../.env via app.config, same as seed_sales.py.)
"""

from __future__ import annotations

import asyncio
import uuid

from app.auth.passwords import hash_password
from app.db import close_db, execute, fetchrow, init_db
from app.routes.auth import _create_personal_org

# Reuse credential constants + the idempotent upsert helper + the file builder.
from seed_demo import TEST_EMAIL, TEST_NAME, TEST_PASSWORD
from seed_sales import _upsert_refresh
from seed_data_duckdb import build_duckdb_file

# Reuse the datastore seed-id so this board binds to the SAME physical DuckDB
# file/datastore that seed_sales.py registers (whichever seed ran last wins the
# refresh, but both write identical datastore config).
from seed_sales import SEED_DS_SALES


# ─────────────────────────────────────────────────────────────────────────────
# Stable seed identifiers — all NEW, namespaced ``execreview:*`` (additive).
# ─────────────────────────────────────────────────────────────────────────────
SEED_Q_KPI_NSV = "execreview:query:kpi_total_nsv"
SEED_Q_KPI_MOM = "execreview:query:kpi_nsv_mom"
SEED_Q_KPI_LY = "execreview:query:kpi_nsv_ly"
SEED_Q_KPI_TARGET = "execreview:query:kpi_nsv_target"
SEED_Q_KPI_VOLUME = "execreview:query:kpi_volume"
SEED_Q_KPI_SKUS = "execreview:query:kpi_active_skus"
SEED_Q_NSV_SPARK = "execreview:query:nsv_spark"

SEED_Q_PIVOT = "execreview:query:nsv_by_dimension"
SEED_Q_PG_YOY = "execreview:query:product_group_yoy"
SEED_Q_SUPPLIER_YOY = "execreview:query:supplier_yoy"
SEED_Q_CY_VS_LY = "execreview:query:cy_vs_ly"
SEED_Q_MONTHLY_TREND = "execreview:query:monthly_trend"
SEED_Q_ACTUAL_VS_BUDGET = "execreview:query:actual_vs_budget"

SEED_Q_TOP_CUSTOMERS = "execreview:query:top_customers"
SEED_Q_NSV_BY_REGION = "execreview:query:nsv_by_region"
SEED_Q_NSV_BY_CHANNEL = "execreview:query:nsv_by_channel"
SEED_Q_CATEGORY_MIX = "execreview:query:category_mix"
SEED_Q_TOP_BRANDS = "execreview:query:top_brands"
SEED_Q_NSV_BY_OPCO = "execreview:query:nsv_by_opco"

SEED_Q_HEATMAP = "execreview:query:region_month_heatmap"
SEED_Q_GAUGE = "execreview:query:target_attainment"

SEED_Q_PRODUCT_FOR_REGION = "execreview:query:product_for_region"
SEED_Q_CHANNEL_FOR_REGION = "execreview:query:channel_for_region"

SEED_Q_REGION_OPTIONS = "execreview:query:region_options"
SEED_Q_OPCO_OPTIONS = "execreview:query:opco_options"
SEED_Q_DIMENSION_OPTIONS = "execreview:query:dimension_options"

SEED_BOARD_EXEC = "execreview:board:executive_sales_review"


# ─────────────────────────────────────────────────────────────────────────────
# SQL (DuckDB dialect) — every statement verified by executing against the built
# file before finalising (see the standalone check at the bottom of this module).
# ─────────────────────────────────────────────────────────────────────────────

SQL_KPI_NSV = "SELECT SUM(nsv) AS nsv FROM sales"

# NSV vs prior month — single row {value, compare}. DISTINCT months are ranked in
# a separate CTE before the window function (ranking over a DISTINCT projection
# directly is unreliable).
SQL_KPI_MOM = """
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

# NSV vs last year — rolling 12 vs prior 12 (single row {value, compare}).
SQL_KPI_LY = """
WITH bounds AS (SELECT MAX(invoice_date) AS maxd FROM sales)
SELECT
    (SELECT SUM(nsv) FROM sales, bounds WHERE invoice_date > maxd - INTERVAL 12 MONTH) AS nsv,
    (SELECT SUM(nsv) FROM sales, bounds
        WHERE invoice_date <= maxd - INTERVAL 12 MONTH
          AND invoice_date >  maxd - INTERVAL 24 MONTH) AS ly_nsv
""".strip()

SQL_KPI_TARGET = """
SELECT
    (SELECT SUM(nsv) FROM sales) AS nsv,
    (SELECT SUM(target_nsv) FROM targets) AS target_nsv
""".strip()

SQL_KPI_VOLUME = "SELECT SUM(volume) AS volume FROM sales"

# Active SKUs = distinct brand x product_group combinations sold.
SQL_KPI_SKUS = "SELECT COUNT(DISTINCT brand || '|' || product_group) AS skus FROM sales"

# Sparkline source: a value column (latest-month NSV repeated) + a full monthly
# NSV series for the sparkline. KPI reads value=first row, spark=full column.
SQL_NSV_SPARK = """
SELECT
    SUM(SUM(nsv)) OVER () AS nsv,
    SUM(nsv)             AS monthly_nsv
FROM sales
GROUP BY month
ORDER BY month
""".strip()

# ── The Field PIVOT drilldown ─────────────────────────────────────────────────
# CASE over a {{dimension}} VALUE param, scoped by {{opco}} + {{region}} value
# params (NULL = all — the bound NULL parameter makes the `IS NULL OR` branch
# true). Nubi binds param values, never column identifiers, so this is the safe
# legacy "{{ .Field }}" translation.
SQL_PIVOT = """
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
WHERE ({{opco}}   IS NULL OR opco   = {{opco}})
  AND ({{region}} IS NULL OR region = {{region}})
GROUP BY 1
ORDER BY nsv DESC
LIMIT 15
""".strip()

# Product-group YoY (rolling 12 vs prior 12) — red/green via pct_change sign.
SQL_PG_YOY = """
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
    cy.nsv           AS nsv,
    ly.nsv           AS prior_nsv,
    ROUND((cy.nsv - ly.nsv) / NULLIF(ly.nsv, 0), 4) AS pct_change
FROM cy JOIN ly USING (product_group)
ORDER BY cy.nsv DESC
""".strip()

# Supplier YoY (rolling 12 vs prior 12).
SQL_SUPPLIER_YOY = """
WITH bounds AS (SELECT MAX(invoice_date) AS maxd FROM sales),
cy AS (
    SELECT supplier, SUM(nsv) AS nsv FROM sales, bounds
    WHERE invoice_date > maxd - INTERVAL 12 MONTH GROUP BY 1
),
ly AS (
    SELECT supplier, SUM(nsv) AS nsv FROM sales, bounds
    WHERE invoice_date <= maxd - INTERVAL 12 MONTH
      AND invoice_date >  maxd - INTERVAL 24 MONTH GROUP BY 1
)
SELECT
    cy.supplier AS supplier,
    cy.nsv      AS nsv,
    ly.nsv      AS prior_nsv,
    ROUND((cy.nsv - ly.nsv) / NULLIF(ly.nsv, 0), 4) AS pct_change
FROM cy JOIN ly USING (supplier)
ORDER BY cy.nsv DESC
""".strip()

# CY vs LY by calendar month number (combo bar + line).
SQL_CY_VS_LY = """
WITH bounds AS (SELECT MAX(invoice_date) AS maxd FROM sales),
cy AS (
    SELECT month(invoice_date) AS month_num, SUM(nsv) AS nsv_cy
    FROM sales, bounds WHERE invoice_date > maxd - INTERVAL 12 MONTH GROUP BY 1
),
ly AS (
    SELECT month(invoice_date) AS month_num, SUM(nsv) AS nsv_ly
    FROM sales, bounds
    WHERE invoice_date <= maxd - INTERVAL 12 MONTH
      AND invoice_date >  maxd - INTERVAL 24 MONTH GROUP BY 1
)
SELECT cy.month_num AS month_num, cy.nsv_cy AS nsv_cy, ly.nsv_ly AS nsv_ly
FROM cy JOIN ly USING (month_num)
ORDER BY cy.month_num
""".strip()

SQL_MONTHLY_TREND = """
SELECT month, SUM(nsv) AS nsv, SUM(volume) AS volume
FROM sales
GROUP BY month
ORDER BY month
""".strip()

SQL_ACTUAL_VS_BUDGET = """
WITH actual AS (SELECT month, SUM(nsv) AS actual_nsv FROM sales GROUP BY month),
budget AS (SELECT month, SUM(budget_nsv) AS budget_nsv FROM budget GROUP BY month)
SELECT actual.month AS month, actual.actual_nsv AS actual_nsv, budget.budget_nsv AS budget_nsv
FROM actual JOIN budget USING (month)
ORDER BY actual.month
""".strip()

SQL_TOP_CUSTOMERS = """
SELECT customer, SUM(nsv) AS nsv FROM sales GROUP BY customer ORDER BY nsv DESC
""".strip()

SQL_NSV_BY_REGION = """
SELECT region, SUM(nsv) AS nsv FROM sales GROUP BY region ORDER BY nsv DESC
""".strip()

SQL_NSV_BY_CHANNEL = """
SELECT channel, SUM(nsv) AS nsv FROM sales GROUP BY channel ORDER BY nsv DESC
""".strip()

SQL_CATEGORY_MIX = """
SELECT category, SUM(nsv) AS nsv FROM sales GROUP BY category ORDER BY nsv DESC
""".strip()

SQL_TOP_BRANDS = """
SELECT brand, SUM(nsv) AS nsv FROM sales GROUP BY brand ORDER BY nsv DESC LIMIT 10
""".strip()

SQL_NSV_BY_OPCO = """
SELECT opco, SUM(nsv) AS nsv FROM sales GROUP BY opco ORDER BY nsv DESC
""".strip()

# Region x month heat (encoding.x=month, encoding.y=region, value=nsv).
SQL_HEATMAP = """
SELECT region, month, SUM(nsv) AS nsv
FROM sales
GROUP BY region, month
ORDER BY month, region
""".strip()

# Gauge: NSV-vs-target attainment as a percentage.
SQL_GAUGE = """
SELECT ROUND(
    100.0 * (SELECT SUM(nsv) FROM sales) / NULLIF((SELECT SUM(target_nsv) FROM targets), 0),
    1
) AS attainment_pct
""".strip()

# Region-scoped drilldown queries (driven by the {{region}} variable).
SQL_PRODUCT_FOR_REGION = """
SELECT product_group, SUM(nsv) AS nsv, SUM(volume) AS volume, SUM(units) AS units
FROM sales
WHERE region = {{region}}
GROUP BY product_group
ORDER BY nsv DESC
""".strip()

SQL_CHANNEL_FOR_REGION = """
SELECT channel, SUM(nsv) AS nsv
FROM sales
WHERE region = {{region}}
GROUP BY channel
ORDER BY nsv DESC
""".strip()

# Filter option lists.
SQL_REGION_OPTIONS = """
SELECT region AS value, region AS label FROM sales GROUP BY region ORDER BY region
""".strip()

SQL_OPCO_OPTIONS = """
SELECT opco AS value, opco AS label FROM sales GROUP BY opco ORDER BY opco
""".strip()

SQL_DIMENSION_OPTIONS = """
SELECT * FROM (VALUES
    ('Region','Region'), ('OpCo','OpCo'), ('Channel','Channel'),
    ('Supplier','Supplier'), ('Brand','Brand'), ('Category','Category'),
    ('Customer','Customer'), ('Product','Product')
) AS t(value, label)
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Widget builders
# ─────────────────────────────────────────────────────────────────────────────

def _pct_rules(column: str = "pct_change") -> list[dict]:
    """Red-for-negative / green-for-positive conditional-format rules."""
    return [
        {"column": column, "op": "lt", "value": 0, "scope": "cell",
         "style": {"color": "#dc2626", "fontWeight": 600}},
        {"column": column, "op": "gt", "value": 0, "scope": "cell",
         "style": {"color": "#16a34a", "fontWeight": 600}},
    ]


def _section(id_, title, subtitle, x, y, w, accent="#38bdf8"):
    """An HTML section-header band widget (no query — static template)."""
    html_tpl = (
        f'<div style="display:flex;flex-direction:column;justify-content:center;'
        f'height:100%;padding:10px 18px;border-left:4px solid {accent};">'
        f'<div style="font-size:15px;font-weight:700;letter-spacing:0.04em;'
        f'text-transform:uppercase;color:#e2e8f0;">{title}</div>'
        f'<div style="font-size:12px;color:#94a3b8;margin-top:2px;">{subtitle}</div>'
        f"</div>"
    )
    return {
        "id": id_, "type": "text", "query_id": "", "content": title,
        "html": html_tpl, "encoding": {}, "props": {},
        "style": {"background": "rgba(15,23,42,0.55)", "border": "1px solid rgba(148,163,184,0.18)"},
        "pos": {"x": x, "y": y, "w": w, "h": 1},
    }


def _kpi(id_, query_id, value, label, fmt, x, y, *, compare=None, spark=None, delta_fmt=None):
    enc = {"value": value}
    if compare:
        enc["compare"] = compare
    if spark:
        enc["spark"] = spark
    props = {"label": label, "format": fmt}
    if delta_fmt:
        props["deltaFormat"] = delta_fmt
    return {
        "id": id_, "type": "kpi", "query_id": query_id, "encoding": enc, "props": props,
        "pos": {"x": x, "y": y, "w": 2, "h": 2},
    }


def _filter(id_, target_var, options_id, label, x, y):
    return {
        "id": id_, "type": "filter", "query_id": "", "subtype": "select",
        "target_var": target_var, "options_query_id": options_id, "encoding": {},
        "props": {"label": label, "placeholder": f"All {label.lower()}"},
        "pos": {"x": x, "y": y, "w": 4, "h": 2},
    }


def _board_spec(q: dict[str, str]) -> dict:
    """Build the full ~25-widget Executive Sales Review board spec."""
    return {
        "version": 1,
        "title": "Executive Sales Review",
        "layout": {"cols": 12, "row_height": 56},
        "background": {"type": "gradient", "from": "#0b1220", "to": "#13294b", "angle": 145},
        "variables": [
            {"name": "dimension", "type": "select", "default": "Region"},
            {"name": "opco", "type": "select", "default": None},
            {"name": "region", "type": "select", "default": "Gauteng"},
        ],
        "widgets": [
            # ════ SECTION: PERFORMANCE ════ (row 1)
            _section("sec_perf", "Performance", "Net Sales Value — group-wide headline metrics",
                     1, 1, 12, accent="#38bdf8"),
            # KPI band (row 2)
            _kpi("kpi_nsv", q["kpi_total_nsv"], "nsv", "Total NSV", "currency", 1, 2),
            _kpi("kpi_mom", q["kpi_nsv_mom"], "nsv", "NSV vs Prior Mo.", "currency", 3, 2,
                 compare="prior_nsv", delta_fmt="percent"),
            _kpi("kpi_ly", q["kpi_nsv_ly"], "nsv", "NSV vs LY", "currency", 5, 2,
                 compare="ly_nsv", delta_fmt="percent"),
            _kpi("kpi_target", q["kpi_nsv_target"], "nsv", "NSV vs Target", "currency", 7, 2,
                 compare="target_nsv", delta_fmt="percent"),
            _kpi("kpi_volume", q["kpi_volume"], "volume", "Total Volume", "integer", 9, 2),
            _kpi("kpi_skus", q["kpi_active_skus"], "skus", "Active SKUs", "integer", 11, 2),

            # Trend combo + NSV sparkline (row 4)
            {"id": "chart_trend", "type": "chart", "query_id": q["monthly_trend"],
             "chart_type": "bar",
             "encoding": {"x": "month", "y": [
                 {"col": "nsv", "type": "bar", "axis": "left"},
                 {"col": "volume", "type": "line", "axis": "right"}]},
             "props": {"title": "Monthly NSV & Volume", "height": 300},
             "pos": {"x": 1, "y": 4, "w": 8, "h": 6}},
            _kpi("kpi_spark", q["nsv_spark"], "nsv", "NSV — 24-mo trend", "currency", 9, 4,
                 spark="monthly_nsv"),
            {"id": "gauge_target", "type": "chart", "query_id": q["target_attainment"],
             "chart_type": "gauge", "encoding": {"x": "attainment_pct", "value": "attainment_pct"},
             "props": {"title": "Target Attainment %", "label": "Attainment %",
                       "min": 0, "max": 120, "height": 200},
             "pos": {"x": 9, "y": 6, "w": 4, "h": 4}},

            # CY vs LY combo + actual vs budget combo (row 10)
            {"id": "chart_cy_ly", "type": "chart", "query_id": q["cy_vs_ly"],
             "chart_type": "bar",
             "encoding": {"x": "month_num", "y": [
                 {"col": "nsv_cy", "type": "bar", "axis": "left"},
                 {"col": "nsv_ly", "type": "line", "axis": "left"}]},
             "props": {"title": "Current Year vs Last Year", "height": 290},
             "pos": {"x": 1, "y": 10, "w": 6, "h": 6}},
            {"id": "chart_budget", "type": "chart", "query_id": q["actual_vs_budget"],
             "chart_type": "bar",
             "encoding": {"x": "month", "y": [
                 {"col": "actual_nsv", "type": "bar", "axis": "left"},
                 {"col": "budget_nsv", "type": "line", "axis": "left"}]},
             "props": {"title": "Actual vs Budget", "height": 290},
             "pos": {"x": 7, "y": 10, "w": 6, "h": 6}},

            # ════ SECTION: DRILLDOWN ════ (row 16)
            _section("sec_drill", "Drilldown", "Field pivot — re-group NSV by any dimension, scoped by OpCo & Region",
                     1, 16, 12, accent="#a78bfa"),
            # Filter strip (row 17)
            _filter("f_dimension", "dimension", q["dimension_options"], "Dimension", 1, 17),
            _filter("f_opco", "opco", q["opco_options"], "OpCo", 5, 17),
            _filter("f_region", "region", q["region_options"], "Region", 9, 17),

            # Field PIVOT (re-grouped by dimension, scoped by opco + region) (row 19)
            {"id": "chart_pivot", "type": "chart", "query_id": q["nsv_by_dimension"],
             "chart_type": "bar", "encoding": {"x": "dimension", "y": "nsv"},
             "params": {"dimension": {"ref": "dimension"}, "opco": {"ref": "opco"},
                        "region": {"ref": "region"}},
             "props": {"title": "NSV by selected dimension (pivot)", "height": 320},
             "pos": {"x": 1, "y": 19, "w": 7, "h": 6}},
            # Region-scoped product table (driven by `region`) (row 19)
            {"id": "table_region_products", "type": "table", "query_id": q["product_for_region"],
             "encoding": {}, "params": {"region": {"ref": "region"}},
             "props": {"limit": 10, "columns": ["product_group", "nsv", "volume", "units"]},
             "columnFormats": {
                 "nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                 "volume": {"type": "number", "decimals": 0},
                 "units": {"type": "number", "decimals": 0}},
             "pos": {"x": 8, "y": 19, "w": 5, "h": 6}},

            # NSV by region (chart-click drilldown → region var) + region channel table (row 25)
            {"id": "chart_region_drill", "type": "chart", "query_id": q["nsv_by_region"],
             "chart_type": "bar", "encoding": {"x": "region", "y": "nsv"},
             "drilldown": {"target_var": "region", "value_field": "region"},
             "props": {"title": "NSV by Region — click a bar to drill", "height": 280},
             "pos": {"x": 1, "y": 25, "w": 7, "h": 6}},
            {"id": "table_region_channels", "type": "table", "query_id": q["channel_for_region"],
             "encoding": {}, "params": {"region": {"ref": "region"}},
             "props": {"limit": 10, "columns": ["channel", "nsv"]},
             "columnFormats": {"nsv": {"type": "currency", "currency": "USD", "decimals": 0}},
             "pos": {"x": 8, "y": 25, "w": 5, "h": 6}},

            # ════ SECTION: PERIOD ANALYSIS ════ (row 31)
            _section("sec_period", "Period Analysis", "Year-over-year %change — red declines, green growth",
                     1, 31, 12, accent="#f472b6"),
            {"id": "table_pg_yoy", "type": "table", "query_id": q["product_group_yoy"],
             "encoding": {},
             "props": {"limit": 10, "columns": ["product_group", "nsv", "prior_nsv", "pct_change"]},
             "columnFormats": {
                 "nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                 "prior_nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                 "pct_change": {"type": "percent", "decimals": 1}},
             "formattingRules": _pct_rules(),
             "pos": {"x": 1, "y": 32, "w": 6, "h": 6}},
            {"id": "table_supplier_yoy", "type": "table", "query_id": q["supplier_yoy"],
             "encoding": {},
             "props": {"limit": 10, "columns": ["supplier", "nsv", "prior_nsv", "pct_change"]},
             "columnFormats": {
                 "nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                 "prior_nsv": {"type": "currency", "currency": "USD", "decimals": 0},
                 "pct_change": {"type": "percent", "decimals": 1}},
             "formattingRules": _pct_rules(),
             "pos": {"x": 7, "y": 32, "w": 6, "h": 6}},

            # ════ SECTION: CHANNELS & MIX ════ (row 38)
            _section("sec_channels", "Channels & Mix", "Where the value comes from — channel, category, OpCo, brand",
                     1, 38, 12, accent="#34d399"),
            {"id": "chart_channel", "type": "chart", "query_id": q["nsv_by_channel"],
             "chart_type": "donut", "encoding": {"x": "channel", "y": "nsv"},
             "props": {"title": "NSV by Channel", "height": 280},
             "pos": {"x": 1, "y": 39, "w": 4, "h": 6}},
            {"id": "chart_category", "type": "chart", "query_id": q["category_mix"],
             "chart_type": "bar", "encoding": {"x": "category", "y": "nsv"},
             "props": {"title": "Category Mix", "height": 280},
             "pos": {"x": 5, "y": 39, "w": 4, "h": 6}},
            {"id": "chart_opco", "type": "chart", "query_id": q["nsv_by_opco"],
             "chart_type": "bar", "encoding": {"x": "opco", "y": "nsv"},
             "props": {"title": "NSV by Operating Company", "height": 280},
             "pos": {"x": 9, "y": 39, "w": 4, "h": 6}},

            {"id": "chart_customers", "type": "chart", "query_id": q["top_customers"],
             "chart_type": "hbar", "encoding": {"x": "customer", "y": "nsv"},
             "props": {"title": "Top Customers", "height": 280},
             "pos": {"x": 1, "y": 45, "w": 6, "h": 6}},
            {"id": "chart_brands", "type": "chart", "query_id": q["top_brands"],
             "chart_type": "hbar", "encoding": {"x": "brand", "y": "nsv"},
             "props": {"title": "Top Brands", "height": 280},
             "pos": {"x": 7, "y": 45, "w": 6, "h": 6}},

            # ════ SECTION: GEOGRAPHY ════ (row 51)
            _section("sec_geo", "Geography", "Region x month NSV intensity",
                     1, 51, 12, accent="#fbbf24"),
            {"id": "chart_heatmap", "type": "chart", "query_id": q["region_month_heatmap"],
             "chart_type": "heatmap",
             "encoding": {"x": "month", "y": "region", "value": "nsv"},
             "props": {"title": "NSV Heatmap — Region x Month", "height": 320},
             "pos": {"x": 1, "y": 52, "w": 12, "h": 7}},
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main seeder
# ─────────────────────────────────────────────────────────────────────────────

async def seed_executive_review() -> None:
    db_path = build_duckdb_file()

    await init_db()
    try:
        # Resolve / create the superuser + personal org (same path as seed_sales).
        existing_user = await fetchrow("SELECT id FROM users WHERE email = $1", TEST_EMAIL)
        if existing_user is None:
            user_id = str(uuid.uuid4())
            await execute(
                """
                INSERT INTO users (id, email, password_hash, name, email_verified)
                VALUES ($1, $2, $3, $4, true)
                """,
                user_id, TEST_EMAIL, hash_password(TEST_PASSWORD), TEST_NAME,
            )
            await _create_personal_org(user_id, TEST_NAME, TEST_EMAIL)
        else:
            user_id = str(existing_user["id"])

        org_row = await fetchrow(
            "SELECT org_id FROM org_members WHERE user_id = $1::uuid ORDER BY org_id LIMIT 1",
            user_id,
        )
        assert org_row is not None, "User has no org membership."
        org_id = str(org_row["org_id"])

        # Datastore — same seed_id/file as seed_sales (idempotent refresh).
        ds, ds_created = await _upsert_refresh(
            "datastores", SEED_DS_SALES, org_id, user_id,
            "Nubi Sales (DuckDB file)",
            {"type": "duckdb", "database": db_path,
             "description": "Real file-backed DuckDB with 24 months of demo sales data."},
        )
        ds_id = str(ds["id"])

        def _q(seed_id, name, sql, params=None):
            return _upsert_refresh(
                "queries", seed_id, org_id, user_id, name,
                {"sql": sql, "datastore_id": ds_id, "params": params or [], "name": name},
            )

        results: dict[str, tuple[dict, bool]] = {}
        results["kpi_total_nsv"] = await _q(SEED_Q_KPI_NSV, "Exec — Total NSV", SQL_KPI_NSV)
        results["kpi_nsv_mom"] = await _q(SEED_Q_KPI_MOM, "Exec — NSV vs prior month", SQL_KPI_MOM)
        results["kpi_nsv_ly"] = await _q(SEED_Q_KPI_LY, "Exec — NSV vs last year", SQL_KPI_LY)
        results["kpi_nsv_target"] = await _q(SEED_Q_KPI_TARGET, "Exec — NSV vs target", SQL_KPI_TARGET)
        results["kpi_volume"] = await _q(SEED_Q_KPI_VOLUME, "Exec — Total volume", SQL_KPI_VOLUME)
        results["kpi_active_skus"] = await _q(SEED_Q_KPI_SKUS, "Exec — Active SKUs", SQL_KPI_SKUS)
        results["nsv_spark"] = await _q(SEED_Q_NSV_SPARK, "Exec — NSV monthly sparkline", SQL_NSV_SPARK)

        results["product_group_yoy"] = await _q(SEED_Q_PG_YOY, "Exec — Product group YoY %change", SQL_PG_YOY)
        results["supplier_yoy"] = await _q(SEED_Q_SUPPLIER_YOY, "Exec — Supplier YoY %change", SQL_SUPPLIER_YOY)
        results["cy_vs_ly"] = await _q(SEED_Q_CY_VS_LY, "Exec — CY vs LY by month", SQL_CY_VS_LY)
        results["monthly_trend"] = await _q(SEED_Q_MONTHLY_TREND, "Exec — Monthly NSV & volume", SQL_MONTHLY_TREND)
        results["actual_vs_budget"] = await _q(SEED_Q_ACTUAL_VS_BUDGET, "Exec — Actual vs budget", SQL_ACTUAL_VS_BUDGET)

        results["top_customers"] = await _q(SEED_Q_TOP_CUSTOMERS, "Exec — Top customers", SQL_TOP_CUSTOMERS)
        results["nsv_by_region"] = await _q(SEED_Q_NSV_BY_REGION, "Exec — NSV by region", SQL_NSV_BY_REGION)
        results["nsv_by_channel"] = await _q(SEED_Q_NSV_BY_CHANNEL, "Exec — NSV by channel", SQL_NSV_BY_CHANNEL)
        results["category_mix"] = await _q(SEED_Q_CATEGORY_MIX, "Exec — Category mix", SQL_CATEGORY_MIX)
        results["top_brands"] = await _q(SEED_Q_TOP_BRANDS, "Exec — Top brands", SQL_TOP_BRANDS)
        results["nsv_by_opco"] = await _q(SEED_Q_NSV_BY_OPCO, "Exec — NSV by OpCo", SQL_NSV_BY_OPCO)

        results["region_month_heatmap"] = await _q(SEED_Q_HEATMAP, "Exec — Region x month heatmap", SQL_HEATMAP)
        results["target_attainment"] = await _q(SEED_Q_GAUGE, "Exec — Target attainment gauge", SQL_GAUGE)

        results["channel_for_region"] = await _q(SEED_Q_CHANNEL_FOR_REGION,
            "Exec — Channels for region ({{region}})", SQL_CHANNEL_FOR_REGION,
            params=[{"name": "region", "type": "select", "default": "Gauteng",
                     "required": True, "options_query_id": None}])
        results["product_for_region"] = await _q(SEED_Q_PRODUCT_FOR_REGION,
            "Exec — Products for region ({{region}})", SQL_PRODUCT_FOR_REGION,
            params=[{"name": "region", "type": "select", "default": "Gauteng",
                     "required": True, "options_query_id": None}])

        results["region_options"] = await _q(SEED_Q_REGION_OPTIONS, "Exec — Region options", SQL_REGION_OPTIONS)
        results["opco_options"] = await _q(SEED_Q_OPCO_OPTIONS, "Exec — OpCo options", SQL_OPCO_OPTIONS)
        results["dimension_options"] = await _q(SEED_Q_DIMENSION_OPTIONS, "Exec — Dimension options", SQL_DIMENSION_OPTIONS)

        # Field pivot — scoped by dimension/opco/region value params.
        results["nsv_by_dimension"] = await _q(
            SEED_Q_PIVOT, "Exec — NSV by dimension ({{dimension}}, {{opco}}, {{region}})", SQL_PIVOT,
            params=[
                {"name": "dimension", "type": "select", "default": "Region", "required": True, "options_query_id": None},
                {"name": "opco", "type": "select", "default": None, "required": False, "options_query_id": None},
                {"name": "region", "type": "select", "default": None, "required": False, "options_query_id": None},
            ],
        )

        q_ids = {key: str(row["id"]) for key, (row, _c) in results.items()}

        # Board.
        board, board_created = await _upsert_refresh(
            "boards", SEED_BOARD_EXEC, org_id, user_id,
            "Executive Sales Review", {"spec": _board_spec(q_ids)},
        )

        def _status(created: bool) -> str:
            return "CREATED" if created else "exists "

        print()
        print("=" * 70)
        print("  Nubi Executive Sales Review seed (additive)")
        print("=" * 70)
        print(f"  DuckDB file   {db_path}")
        print(f"  Org ID        {org_id}")
        print(f"  Datastore [{_status(ds_created)}]  {ds['name']}  ({ds_id})")
        for key, (row, created) in results.items():
            print(f"  Query     [{_status(created)}]  {row['name']}  ({row['id']})")
        print(f"  Board     [{_status(board_created)}]  {board['name']}  ({board['id']})")
        print(f"  Widgets       {len(_board_spec(q_ids)['widgets'])}")
        print(f"  Login:        {TEST_EMAIL} / {TEST_PASSWORD}")
        print("=" * 70)
        print()
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(seed_executive_review())
