"""Build a real, file-backed DuckDB datasource with deterministic fake sales data.

This is the *file builder* half of the Nubi sales-demo seeder.  It writes a
physical ``.duckdb`` file (default ``backend/seed_data/nubi_sales.duckdb``) with
~24 months of plausible FMCG sales modelled on the legacy BI tool's real
dashboards.  The dimensions and measures mirror what those dashboards actually
slice on (operating company, supplier, brand, category, customer, channel,
region) and the measures they report (NSV, volume, units, period-over-period,
current-year-vs-last-year, budget/target).

Everything is DETERMINISTIC — every number derives from a fixed dimension list
plus a SHA-256-based pseudo-random generator, so rebuilding yields byte-stable
data (no ``faker`` / ``random``).

Grounding
---------
Dimension vocabularies are drawn from the legacy dump (``legacy/database``):
operating companies (Wutow, Logico, CASales Botswana, Rainbow, Eswatini PnP),
suppliers that appear in real dashboard names (Unilever, Kimberly-Clark, Lipton,
Nestlé, Tiger Brands, Rainbow), the "Mass Discounters" channel, and the
NSV / volume / period_1-vs-period_2 / CY-vs-LY measure shapes.

Schema
------
``sales`` (fact, one row per month×opco×region×channel×supplier×product_group):
    invoice_date DATE, month VARCHAR 'YYYY-MM', year INT,
    opco, region, channel, supplier, brand, category, product_group, customer VARCHAR,
    nsv DOUBLE, volume DOUBLE, units BIGINT
``budget`` (month×region target):      month, region VARCHAR, budget_nsv DOUBLE
``targets`` (month×opco target):       month, opco   VARCHAR, target_nsv DOUBLE

Usage
-----
    from seed_data_duckdb import build_duckdb_file, DEFAULT_DB_PATH
    path = build_duckdb_file()

or standalone::

    cd backend && python seed_data_duckdb.py
"""

from __future__ import annotations

import hashlib
import math
import os
from datetime import date
from pathlib import Path

import duckdb

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
DEFAULT_DB_PATH = str(_HERE / "seed_data" / "nubi_sales.duckdb")

# ── Fixed dimension lists (grounded in the legacy dump) ───────────────────────
OPCOS = ["Wutow", "Logico", "CASales Botswana", "Rainbow", "Eswatini PnP"]
REGIONS = ["Gauteng", "Western Cape", "KwaZulu-Natal", "Eastern Cape", "Free State"]
CHANNELS = ["Modern Trade", "Traditional Trade", "Wholesale", "Mass Discounters"]
SUPPLIERS = ["Unilever", "Kimberly-Clark", "Lipton", "Nestlé", "Tiger Brands", "Rainbow"]
CUSTOMERS = ["Pick n Pay", "Shoprite", "Spar", "Game", "Makro", "Woolworths"]

# product_group → (category, base unit price, base monthly units, units→volume).
# "Frozen" is a deliberately DECLINING group so PoP / CY-vs-LY shows red as well
# as green — realistic variety for conditional-formatting demos.
PRODUCT_GROUPS = {
    "Beverages":     ("Beverages",     2.10, 4200, 0.50),
    "Tea & Coffee":  ("Beverages",     3.40, 2600, 0.20),
    "Home Care":     ("Home Care",     2.75, 3600, 0.90),
    "Personal Care": ("Personal Care", 4.10, 2900, 0.30),
    "Foods":         ("Foods",         1.85, 5200, 0.70),
    "Frozen":        ("Frozen",        3.05, 2400, 0.80),  # declining
}
_DECLINING = {"Frozen"}

# Brands per supplier (row picks one deterministically).
SUPPLIER_BRANDS = {
    "Unilever":       ["Sunlight", "Omo", "Knorr"],
    "Kimberly-Clark": ["Huggies", "Kleenex"],
    "Lipton":         ["Lipton"],
    "Nestlé":         ["Nescafé", "Maggi"],
    "Tiger Brands":   ["Jungle", "Albany"],
    "Rainbow":        ["Rainbow"],
}

# 24 months ending 2026-05 (demo "current date" is 2026-06-05).
N_MONTHS = 24
_END_YEAR = 2026
_END_MONTH = 5

# Per-dimension demand multipliers — make breakdowns look genuinely different.
_OPCO_WEIGHT = {"Wutow": 1.35, "Logico": 1.15, "CASales Botswana": 0.85, "Rainbow": 1.00, "Eswatini PnP": 0.65}
_REGION_WEIGHT = {"Gauteng": 1.40, "Western Cape": 1.10, "KwaZulu-Natal": 1.15, "Eastern Cape": 0.80, "Free State": 0.70}
_CHANNEL_WEIGHT = {"Modern Trade": 1.30, "Traditional Trade": 1.00, "Wholesale": 1.45, "Mass Discounters": 1.20}
_SUPPLIER_WEIGHT = {"Unilever": 1.50, "Kimberly-Clark": 1.10, "Lipton": 0.70, "Nestlé": 1.25, "Tiger Brands": 1.05, "Rainbow": 0.80}


# ── Deterministic pseudo-random helper ────────────────────────────────────────
def _noise(*parts: object) -> float:
    """Deterministic float in [0, 1) from a stable SHA-256 over *parts*."""
    key = "|".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _pick(items: list[str], *parts: object) -> str:
    """Deterministically pick one of *items* from the hash of *parts*."""
    return items[int(_noise(*parts) * len(items)) % len(items)]


def _iter_months() -> list[tuple[int, date, str]]:
    """Return ``[(month_index, first_of_month, 'YYYY-MM'), ...]`` ascending."""
    y, m = _END_YEAR, _END_MONTH
    stack: list[date] = []
    for _ in range(N_MONTHS):
        stack.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    stack.reverse()
    return [(idx, d, f"{d.year:04d}-{d.month:02d}") for idx, d in enumerate(stack)]


def _seasonality(month_num: int) -> float:
    """Smooth seasonal multiplier (~0.85–1.15) peaking mid-year and December."""
    summer = math.sin((month_num - 3) / 12.0 * 2 * math.pi)
    festive = math.cos((month_num - 12) / 12.0 * 2 * math.pi)
    return 1.0 + 0.09 * summer + 0.06 * festive


def _build_sales_rows() -> list[tuple]:
    """Generate the ``sales`` fact table as a list of tuples."""
    rows: list[tuple] = []
    for idx, d, month_str in _iter_months():
        season = _seasonality(d.month)
        for opco in OPCOS:
            ow = _OPCO_WEIGHT[opco]
            for region in REGIONS:
                rw = _REGION_WEIGHT[region]
                for channel in CHANNELS:
                    cw = _CHANNEL_WEIGHT[channel]
                    for supplier in SUPPLIERS:
                        sw = _SUPPLIER_WEIGHT[supplier]
                        for pg, (category, price, base_units, vol_f) in PRODUCT_GROUPS.items():
                            # Growth trend: most groups grow ~+18% over the window;
                            # the declining group shrinks ~-15% so CY < LY for it.
                            if pg in _DECLINING:
                                trend = 1.0 - 0.0065 * idx
                            else:
                                trend = 1.0 + 0.0075 * idx
                            wobble = 0.85 + 0.30 * _noise(month_str, opco, region, channel, supplier, pg)
                            units = base_units * ow * rw * cw * sw * trend * season * wobble
                            units = max(1, int(round(units / (len(SUPPLIERS) * len(OPCOS)))))

                            eff_price = price * (1.0 + 0.0030 * idx)  # mild inflation
                            nsv = round(units * eff_price, 2)
                            volume = round(units * vol_f, 1)

                            brand = _pick(SUPPLIER_BRANDS[supplier], supplier, pg, region)
                            customer = _pick(CUSTOMERS, month_str, opco, region, channel, pg)

                            rows.append((
                                d, month_str, d.year,
                                opco, region, channel, supplier, brand, category, pg, customer,
                                nsv, volume, units,
                            ))
    return rows


def _build_budget_rows(sales_rows: list[tuple]) -> list[tuple]:
    """Per (month, region) budget ≈ 95–108% of realised NSV (over/under mix)."""
    actual: dict[tuple[str, str], float] = {}
    for r in sales_rows:
        month_str, region, nsv = r[1], r[4], r[11]
        actual[(month_str, region)] = actual.get((month_str, region), 0.0) + nsv
    return [
        (month_str, region, round(total * (0.95 + 0.13 * _noise("budget", month_str, region)), 2))
        for (month_str, region), total in actual.items()
    ]


def _build_target_rows(sales_rows: list[tuple]) -> list[tuple]:
    """Per (month, opco) target ≈ 92–110% of realised NSV."""
    actual: dict[tuple[str, str], float] = {}
    for r in sales_rows:
        month_str, opco, nsv = r[1], r[3], r[11]
        actual[(month_str, opco)] = actual.get((month_str, opco), 0.0) + nsv
    return [
        (month_str, opco, round(total * (0.92 + 0.18 * _noise("target", month_str, opco)), 2))
        for (month_str, opco), total in actual.items()
    ]


def build_duckdb_file(db_path: str | None = None) -> str:
    """Build (or rebuild) the DuckDB file at *db_path*. Idempotent."""
    path = os.path.abspath(db_path or DEFAULT_DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    sales_rows = _build_sales_rows()
    budget_rows = _build_budget_rows(sales_rows)
    target_rows = _build_target_rows(sales_rows)

    con = duckdb.connect(database=path)
    try:
        con.execute("DROP TABLE IF EXISTS sales")
        con.execute("DROP TABLE IF EXISTS budget")
        con.execute("DROP TABLE IF EXISTS targets")
        con.execute(
            """
            CREATE TABLE sales (
                invoice_date  DATE,
                month         VARCHAR,
                year          INTEGER,
                opco          VARCHAR,
                region        VARCHAR,
                channel       VARCHAR,
                supplier      VARCHAR,
                brand         VARCHAR,
                category      VARCHAR,
                product_group VARCHAR,
                customer      VARCHAR,
                nsv           DOUBLE,
                volume        DOUBLE,
                units         BIGINT
            )
            """
        )
        con.execute("CREATE TABLE budget (month VARCHAR, region VARCHAR, budget_nsv DOUBLE)")
        con.execute("CREATE TABLE targets (month VARCHAR, opco VARCHAR, target_nsv DOUBLE)")
        con.executemany(
            "INSERT INTO sales VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sales_rows
        )
        con.executemany("INSERT INTO budget VALUES (?, ?, ?)", budget_rows)
        con.executemany("INSERT INTO targets VALUES (?, ?, ?)", target_rows)
        con.commit()
    finally:
        con.close()

    return path


if __name__ == "__main__":
    p = build_duckdb_file()
    con = duckdb.connect(database=p, read_only=True)
    try:
        n_sales = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        n_budget = con.execute("SELECT COUNT(*) FROM budget").fetchone()[0]
        n_targets = con.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
        months = con.execute("SELECT MIN(month), MAX(month) FROM sales").fetchone()
        dims = con.execute(
            "SELECT COUNT(DISTINCT opco), COUNT(DISTINCT supplier), COUNT(DISTINCT brand), "
            "COUNT(DISTINCT category), COUNT(DISTINCT customer) FROM sales"
        ).fetchone()
    finally:
        con.close()
    print(f"Built {p}")
    print(f"  sales rows : {n_sales}")
    print(f"  budget rows: {n_budget}")
    print(f"  target rows: {n_targets}")
    print(f"  month range: {months[0]} .. {months[1]}")
    print(f"  distinct opco/supplier/brand/category/customer: {dims}")
