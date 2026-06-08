"""Build the bundled, read-only DuckDB *sample* dataset (a small star schema).

This is the **file builder** half of Nubi's onboarding sample bundle.  It writes
one physical ``.duckdb`` file — by default ``backend/seed_data/sample.duckdb`` —
that every org's "Sample" connector points at (one shared, read-only file; the
per-org bundle in ``app/sample.py`` only creates the *metadata* rows pointing
here).

The data models ~24 months of plausible FMCG sales as a small, well-shaped
**star schema** so the bundled sample dashboard can demonstrate KPIs, a grouped
breakdown, and a real fact↔dim join:

    dim_customers ─┐
    dim_products ──┤──< sales (fact) >── dim_regions
                   │
              budget / targets (aggregate planning tables)

Everything is DETERMINISTIC — every number derives from a fixed dimension list
plus a SHA-256-based pseudo-random generator, so rebuilding yields byte-stable
data (no ``faker`` / ``random``).

Schema
------
``dim_regions`` (region dimension):
    region_id INT PK, region VARCHAR, country VARCHAR
``dim_products`` (product dimension):
    product_id INT PK, product_group VARCHAR, category VARCHAR,
    supplier VARCHAR, brand VARCHAR, unit_price DOUBLE
``dim_customers`` (customer dimension):
    customer_id INT PK, customer VARCHAR, channel VARCHAR
``sales`` (fact, one row per month×opco×region×channel×supplier×product_group):
    invoice_date DATE, month VARCHAR 'YYYY-MM', year INT, opco VARCHAR,
    region_id INT  → dim_regions.region_id,
    product_id INT → dim_products.product_id,
    customer_id INT → dim_customers.customer_id,
    region, channel, supplier, brand, category, product_group, customer VARCHAR
        (denormalised copies kept for back-compat with the legacy demo seeders),
    nsv DOUBLE, volume DOUBLE, units BIGINT
``budget`` (month×region target):  month, region VARCHAR, budget_nsv DOUBLE
``targets`` (month×opco target):   month, opco   VARCHAR, target_nsv DOUBLE

Usage
-----
    from seed_data_duckdb import build_duckdb_file, SAMPLE_DB_PATH
    path = build_duckdb_file()  # → SAMPLE_DB_PATH

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
# The ONE bundled, read-only sample file every org's Sample connector points at.
SAMPLE_DB_PATH = str(_HERE / "seed_data" / "sample.duckdb")
# Alternate build target (same schema as the bundled file). Kept as an optional
# standalone output; the demo seed (seed.py --demo) and the per-project sample
# bundle (app/sample.py) both point at SAMPLE_DB_PATH above.
DEFAULT_DB_PATH = str(_HERE / "seed_data" / "nubi_sales.duckdb")

# ── Fixed dimension lists (grounded in the legacy dump) ───────────────────────
OPCOS = ["Wutow", "Logico", "CASales Botswana", "Rainbow", "Eswatini PnP"]
REGIONS = ["Gauteng", "Western Cape", "KwaZulu-Natal", "Eastern Cape", "Free State"]
REGION_COUNTRY = {
    "Gauteng": "South Africa",
    "Western Cape": "South Africa",
    "KwaZulu-Natal": "South Africa",
    "Eastern Cape": "South Africa",
    "Free State": "South Africa",
}
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

# 24 months ending 2026-05 (demo "current date" is 2026-06).
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


# ── Dimension builders (deterministic surrogate keys, 1-based) ─────────────────

def _build_region_dim() -> tuple[list[tuple], dict[str, int]]:
    """``dim_regions`` rows + a ``region → region_id`` lookup."""
    rows: list[tuple] = []
    lookup: dict[str, int] = {}
    for i, region in enumerate(REGIONS, start=1):
        lookup[region] = i
        rows.append((i, region, REGION_COUNTRY[region]))
    return rows, lookup


def _build_product_dim() -> tuple[list[tuple], dict[tuple[str, str, str], int]]:
    """``dim_products`` rows + a ``(product_group, supplier, brand) → id`` lookup.

    One product row per (product_group, supplier, brand) combination that can
    actually occur in the fact table — so every fact row resolves to exactly one
    product dimension row (clean star-schema join).
    """
    rows: list[tuple] = []
    lookup: dict[tuple[str, str, str], int] = {}
    pid = 0
    for pg, (category, price, _base, _vol) in PRODUCT_GROUPS.items():
        for supplier in SUPPLIERS:
            for brand in SUPPLIER_BRANDS[supplier]:
                pid += 1
                key = (pg, supplier, brand)
                lookup[key] = pid
                rows.append((pid, pg, category, supplier, brand, price))
    return rows, lookup


def _build_customer_dim() -> tuple[list[tuple], dict[tuple[str, str], int]]:
    """``dim_customers`` rows + a ``(customer, channel) → id`` lookup."""
    rows: list[tuple] = []
    lookup: dict[tuple[str, str], int] = {}
    cid = 0
    for customer in CUSTOMERS:
        for channel in CHANNELS:
            cid += 1
            lookup[(customer, channel)] = cid
            rows.append((cid, customer, channel))
    return rows, lookup


def _build_sales_rows(
    region_ids: dict[str, int],
    product_ids: dict[tuple[str, str, str], int],
    customer_ids: dict[tuple[str, str], int],
) -> list[tuple]:
    """Generate the ``sales`` fact table (with FK surrogate keys)."""
    rows: list[tuple] = []
    for idx, d, month_str in _iter_months():
        season = _seasonality(d.month)
        for opco in OPCOS:
            ow = _OPCO_WEIGHT[opco]
            for region in REGIONS:
                rw = _REGION_WEIGHT[region]
                region_id = region_ids[region]
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

                            product_id = product_ids[(pg, supplier, brand)]
                            customer_id = customer_ids[(customer, channel)]

                            rows.append((
                                d, month_str, d.year, opco,
                                region_id, product_id, customer_id,
                                # Denormalised columns (back-compat with the
                                # legacy demo seeders that query sales directly).
                                region, channel, supplier, brand, category, pg, customer,
                                nsv, volume, units,
                            ))
    return rows


def _build_budget_rows(sales_rows: list[tuple]) -> list[tuple]:
    """Per (month, region) budget ≈ 95–108% of realised NSV (over/under mix)."""
    actual: dict[tuple[str, str], float] = {}
    for r in sales_rows:
        month_str, region, nsv = r[1], r[7], r[14]
        actual[(month_str, region)] = actual.get((month_str, region), 0.0) + nsv
    return [
        (month_str, region, round(total * (0.95 + 0.13 * _noise("budget", month_str, region)), 2))
        for (month_str, region), total in actual.items()
    ]


def _build_target_rows(sales_rows: list[tuple]) -> list[tuple]:
    """Per (month, opco) target ≈ 92–110% of realised NSV."""
    actual: dict[tuple[str, str], float] = {}
    for r in sales_rows:
        month_str, opco, nsv = r[1], r[3], r[14]
        actual[(month_str, opco)] = actual.get((month_str, opco), 0.0) + nsv
    return [
        (month_str, opco, round(total * (0.92 + 0.18 * _noise("target", month_str, opco)), 2))
        for (month_str, opco), total in actual.items()
    ]


# Column order for the ``sales`` fact table (matches ``_build_sales_rows``).
_SALES_COLS = [
    "invoice_date", "month", "year", "opco",
    "region_id", "product_id", "customer_id",
    "region", "channel", "supplier", "brand", "category", "product_group", "customer",
    "nsv", "volume", "units",
]


def _bulk_insert(con: "duckdb.DuckDBPyConnection", table: str, cols: list[str], rows: list[tuple]) -> None:
    """Bulk-load *rows* into *table* via a registered Arrow table (fast path).

    Transposes the row tuples into columns, builds a ``pyarrow.Table``, registers
    it, and runs a single ``INSERT INTO … SELECT`` — far faster than a row-by-row
    ``executemany`` for the large fact table.
    """
    import pyarrow as pa

    columns = {name: [row[i] for row in rows] for i, name in enumerate(cols)}
    arrow_tbl = pa.table(columns)
    con.register("_bulk_arrow", arrow_tbl)
    try:
        col_list = ", ".join(cols)
        con.execute(f"INSERT INTO {table} ({col_list}) SELECT {col_list} FROM _bulk_arrow")
    finally:
        con.unregister("_bulk_arrow")


def build_duckdb_file(db_path: str | None = None) -> str:
    """Build (or rebuild) the bundled sample DuckDB file at *db_path*.

    Idempotent: drops and recreates every table on each call so the output is
    byte-stable for a given code version.  Returns the absolute path written.
    """
    path = os.path.abspath(db_path or SAMPLE_DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    region_rows, region_ids = _build_region_dim()
    product_rows, product_ids = _build_product_dim()
    customer_rows, customer_ids = _build_customer_dim()

    sales_rows = _build_sales_rows(region_ids, product_ids, customer_ids)
    budget_rows = _build_budget_rows(sales_rows)
    target_rows = _build_target_rows(sales_rows)

    con = duckdb.connect(database=path)
    try:
        for tbl in ("sales", "dim_regions", "dim_products", "dim_customers", "budget", "targets"):
            con.execute(f"DROP TABLE IF EXISTS {tbl}")

        con.execute(
            """
            CREATE TABLE dim_regions (
                region_id INTEGER PRIMARY KEY,
                region    VARCHAR,
                country   VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE dim_products (
                product_id    INTEGER PRIMARY KEY,
                product_group VARCHAR,
                category      VARCHAR,
                supplier      VARCHAR,
                brand         VARCHAR,
                unit_price    DOUBLE
            )
            """
        )
        con.execute(
            """
            CREATE TABLE dim_customers (
                customer_id INTEGER PRIMARY KEY,
                customer    VARCHAR,
                channel     VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE sales (
                invoice_date  DATE,
                month         VARCHAR,
                year          INTEGER,
                opco          VARCHAR,
                region_id     INTEGER REFERENCES dim_regions(region_id),
                product_id    INTEGER REFERENCES dim_products(product_id),
                customer_id   INTEGER REFERENCES dim_customers(customer_id),
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

        # Dimensions are tiny — row-by-row inserts are fine and let DuckDB
        # validate the PRIMARY KEYs before the fact rows reference them.
        con.executemany("INSERT INTO dim_regions VALUES (?, ?, ?)", region_rows)
        con.executemany("INSERT INTO dim_products VALUES (?, ?, ?, ?, ?, ?)", product_rows)
        con.executemany("INSERT INTO dim_customers VALUES (?, ?, ?)", customer_rows)

        # Fact table is large (~86k rows) — a row-by-row executemany with FK
        # validation is painfully slow.  Bulk-load via a registered Arrow table
        # and a single INSERT … SELECT instead (orders of magnitude faster).
        _bulk_insert(con, "sales", _SALES_COLS, sales_rows)
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
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        months = con.execute("SELECT MIN(month), MAX(month) FROM sales").fetchone()
        # Verify the star-schema join actually resolves on every fact row.
        join_check = con.execute(
            """
            SELECT COUNT(*) AS joined_rows, ROUND(SUM(s.nsv), 2) AS total_nsv
            FROM sales s
            JOIN dim_regions   r ON s.region_id   = r.region_id
            JOIN dim_products  p ON s.product_id  = p.product_id
            JOIN dim_customers c ON s.customer_id = c.customer_id
            """
        ).fetchone()
        top_supplier = con.execute(
            """
            SELECT p.supplier, ROUND(SUM(s.nsv), 2) AS nsv
            FROM sales s JOIN dim_products p ON s.product_id = p.product_id
            GROUP BY p.supplier ORDER BY nsv DESC LIMIT 1
            """
        ).fetchone()
    finally:
        con.close()
    print(f"Built {p}")
    for t in tables:
        print(f"  {t:<14} {counts[t]:>8} rows")
    print(f"  month range  : {months[0]} .. {months[1]}")
    print(f"  star join    : {join_check[0]} fact rows joined, total NSV {join_check[1]}")
    print(f"  top supplier : {top_supplier[0]} (NSV {top_supplier[1]})")
