"""Deterministic demo dataset generators — one module per dataset.

Four datasets, each a small, well-shaped schema with ~24 months of plausible
data (June 2024 → May 2026).  Every number derives from a SHA-256-based
pseudo-random generator (see ``_common.noise``), so regeneration is
byte-for-byte stable — no ``faker`` / ``random``.

Datasets
--------
``retail_sales``  : FMCG sales star schema (facts + dims + planning tables).
``saas_metrics``  : accounts / plans / subscriptions / events / invoices —
                    supports MRR/ARR, churn and cohort dashboards.
``web_analytics`` : sessions + pageviews with UTM/device/country — supports
                    traffic, funnel and conversion dashboards.
``finance_ops``   : invoices / payments / expenses / headcount — supports
                    cashflow, AR-aging and burn dashboards.

Table names are globally unique across datasets so all four can live as views
in a single DuckDB datastore (one flat namespace per project).

Public API
----------
``DATASET_TABLES``        : ``{dataset: (table, ...)}`` — the full inventory.
``build_dataset(name)``   : ``{table: pyarrow.Table}`` for one dataset.
``build_all()``           : ``{dataset: {table: pyarrow.Table}}`` for all four.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyarrow as pa

# Dataset → table inventory.  Kept static (no heavy imports) so app code can
# enumerate tables without importing pyarrow / generating data.
DATASET_TABLES: dict[str, tuple[str, ...]] = {
    "retail_sales": (
        "dim_regions",
        "dim_products",
        "dim_customers",
        "sales",
        "budget",
        "targets",
    ),
    "saas_metrics": (
        "saas_plans",
        "saas_accounts",
        "saas_subscriptions",
        "saas_subscription_events",
        "saas_invoices",
    ),
    "web_analytics": (
        "web_sessions",
        "web_pageviews",
    ),
    "finance_ops": (
        "fin_invoices",
        "fin_payments",
        "fin_expenses",
        "fin_headcount",
    ),
}

ALL_TABLES: tuple[str, ...] = tuple(t for ts in DATASET_TABLES.values() for t in ts)


def build_dataset(name: str) -> "dict[str, pa.Table]":
    """Build one dataset deterministically; returns ``{table: pyarrow.Table}``."""
    if name not in DATASET_TABLES:
        raise KeyError(f"unknown demo dataset {name!r}; have {sorted(DATASET_TABLES)}")
    mod = importlib.import_module(f"seed_data.generators.{name}")
    tables = mod.build_tables()
    expected = set(DATASET_TABLES[name])
    got = set(tables)
    if got != expected:  # defensive: generator drifted from the inventory
        raise RuntimeError(f"dataset {name!r} built {sorted(got)} != inventory {sorted(expected)}")
    return tables


def build_all() -> "dict[str, dict[str, pa.Table]]":
    """Build all four datasets; returns ``{dataset: {table: pyarrow.Table}}``."""
    return {ds: build_dataset(ds) for ds in DATASET_TABLES}


def build_all_flat() -> "dict[str, pa.Table]":
    """Build all datasets flattened to ``{table: pyarrow.Table}``.

    Table names are globally unique across the four datasets (see module
    docstring), so flattening never collides. Convenient for registering the
    full demo dataset into a single DuckDB connector.
    """
    return {table: tbl for ds in build_all().values() for table, tbl in ds.items()}
