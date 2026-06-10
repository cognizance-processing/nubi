"""Build the bundled, read-only DuckDB *sample* dataset file.

This is the **file builder** half of Nubi's demo content.  It writes one
physical ``.duckdb`` file — by default ``backend/seed_data/sample.duckdb`` —
containing ALL FOUR demo datasets (17 tables) generated deterministically by
``seed_data/generators/``:

- ``retail_sales``  : dim_regions, dim_products, dim_customers, sales, budget, targets
- ``saas_metrics``  : saas_plans, saas_accounts, saas_subscriptions,
                      saas_subscription_events, saas_invoices
- ``web_analytics`` : web_sessions, web_pageviews
- ``finance_ops``   : fin_invoices, fin_payments, fin_expenses, fin_headcount

NOTE: the primary distribution path for the demo connector is **parquet**
(``app/demo_bundle.export_demo_parquet_local`` / ``export_demo_to_s3`` with
DuckDB views over ``read_parquet``).  This file remains as a convenience build
target for offline tooling and the legacy ``seed.py --demo`` local fallback.

Everything is DETERMINISTIC — every number derives from a SHA-256-based
pseudo-random generator (see ``seed_data/generators/_common.py``), so
rebuilding yields stable data (no ``faker`` / ``random``).

Usage
-----
    from seed_data_duckdb import build_duckdb_file, SAMPLE_DB_PATH
    path = build_duckdb_file()  # → SAMPLE_DB_PATH

or standalone::

    cd backend && python seed_data_duckdb.py
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

# Re-exported for back-compat with older imports of the retail constants.
from seed_data.generators.retail_sales import (  # noqa: F401
    CHANNELS,
    CUSTOMERS,
    OPCOS,
    PRODUCT_GROUPS,
    REGIONS,
    SUPPLIERS,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
# The ONE bundled, read-only sample file (legacy/fallback distribution path).
SAMPLE_DB_PATH = str(_HERE / "seed_data" / "sample.duckdb")
# Alternate build target (same content). Kept as an optional standalone output.
DEFAULT_DB_PATH = str(_HERE / "seed_data" / "nubi_sales.duckdb")


def build_duckdb_file(db_path: str | None = None) -> str:
    """Build (or rebuild) the bundled sample DuckDB file at *db_path*.

    Loads every table of all four demo datasets from the deterministic
    generators.  Idempotent: drops and recreates every table on each call so
    the output is stable for a given code version.  Returns the absolute path
    written.
    """
    from seed_data.generators import DATASET_TABLES, build_dataset  # noqa: PLC0415

    path = os.path.abspath(db_path or SAMPLE_DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    con = duckdb.connect(database=path)
    try:
        for dataset, tables in DATASET_TABLES.items():
            built = build_dataset(dataset)
            for table in tables:
                con.register("_demo_src", built[table])
                try:
                    con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM _demo_src")
                finally:
                    con.unregister("_demo_src")
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
        # Verify the retail star-schema join actually resolves on every fact row.
        join_check = con.execute(
            """
            SELECT COUNT(*) AS joined_rows, ROUND(SUM(s.nsv), 2) AS total_nsv
            FROM sales s
            JOIN dim_regions   r ON s.region_id   = r.region_id
            JOIN dim_products  p ON s.product_id  = p.product_id
            JOIN dim_customers c ON s.customer_id = c.customer_id
            """
        ).fetchone()
        mrr = con.execute(
            "SELECT ROUND(SUM(amount), 2) FROM saas_invoices "
            "WHERE month = (SELECT MAX(month) FROM saas_invoices)"
        ).fetchone()
    finally:
        con.close()
    print(f"Built {p}")
    for t in sorted(tables):
        print(f"  {t:<26} {counts[t]:>8} rows")
    print(f"  retail month range : {months[0]} .. {months[1]}")
    print(f"  retail star join   : {join_check[0]} fact rows joined, total NSV {join_check[1]}")
    print(f"  saas latest MRR    : {mrr[0]}")
