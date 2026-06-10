"""Tests for the deterministic demo dataset generators + parquet distribution.

Coverage
--------
1. Determinism: two independent generations of every dataset are identical.
2. Inventory: every dataset builds exactly its declared tables; table names
   are globally unique (one flat namespace for the single demo datastore).
3. Volume: fact tables stay within the agreed budget (≤ ~50k rows) and are
   non-trivial; all facts span the 24-month window ending 2026-05.
4. Plausibility: MRR grows over the window; web conversion is a sane rate;
   AR aging has open invoices; headcount grows; retail star join resolves.
5. Local parquet export: all 17 files written to a temp dir; a DuckDB
   ``:memory:`` connection with the generated ``view_sql`` (the exact config
   the datastore row stores) can query every table — i.e. the parquet-backed
   connector pipeline works end-to-end in local mode.
6. ``build_duckdb_file`` still works and contains all 17 tables.
"""

from __future__ import annotations

import duckdb
import pytest

from seed_data.generators import ALL_TABLES, DATASET_TABLES, build_dataset

_FACT_TABLES = {
    "retail_sales": "sales",
    "saas_metrics": "saas_invoices",
    "web_analytics": "web_sessions",
    "finance_ops": "fin_invoices",
}


@pytest.fixture(scope="module")
def datasets():
    """Build every dataset once for the whole module (generation is ~1s)."""
    return {ds: build_dataset(ds) for ds in DATASET_TABLES}


# ---------------------------------------------------------------------------
# 1+2. Determinism & inventory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", list(DATASET_TABLES))
def test_generation_is_deterministic(dataset, datasets) -> None:
    rebuilt = build_dataset(dataset)
    for table in DATASET_TABLES[dataset]:
        assert rebuilt[table].equals(datasets[dataset][table]), (
            f"{dataset}.{table}: two generations differ — generator is not deterministic"
        )


def test_table_names_globally_unique() -> None:
    assert len(ALL_TABLES) == len(set(ALL_TABLES))
    assert len(ALL_TABLES) == 17


@pytest.mark.parametrize("dataset", list(DATASET_TABLES))
def test_dataset_builds_declared_tables(dataset, datasets) -> None:
    assert set(datasets[dataset]) == set(DATASET_TABLES[dataset])
    for table, tbl in datasets[dataset].items():
        assert tbl.num_rows > 0, f"{dataset}.{table} is empty"


# ---------------------------------------------------------------------------
# 3. Volume & date window
# ---------------------------------------------------------------------------


def test_fact_tables_within_row_budget(datasets) -> None:
    for dataset, fact in _FACT_TABLES.items():
        n = datasets[dataset][fact].num_rows
        assert n <= 50_000, f"{dataset}.{fact} too large: {n} rows"
    # Pageviews is the largest secondary fact — also within budget.
    assert datasets["web_analytics"]["web_pageviews"].num_rows <= 50_000
    # Non-trivial volumes.
    assert datasets["retail_sales"]["sales"].num_rows > 30_000
    assert datasets["saas_metrics"]["saas_invoices"].num_rows > 3_000
    assert datasets["web_analytics"]["web_sessions"].num_rows > 8_000
    assert datasets["finance_ops"]["fin_invoices"].num_rows > 500


def test_facts_span_24_months_ending_2026_05(datasets) -> None:
    for dataset, fact in _FACT_TABLES.items():
        months = datasets[dataset][fact].column("month").to_pylist()
        assert min(months) == "2024-06", f"{dataset}.{fact} starts at {min(months)}"
        assert max(months) == "2026-05", f"{dataset}.{fact} ends at {max(months)}"


# ---------------------------------------------------------------------------
# 4. Plausibility
# ---------------------------------------------------------------------------


def test_saas_mrr_grows_over_the_window(datasets) -> None:
    inv = datasets["saas_metrics"]["saas_invoices"]
    mrr: dict[str, float] = {}
    for m, a in zip(inv.column("month").to_pylist(), inv.column("amount").to_pylist()):
        mrr[m] = mrr.get(m, 0.0) + a
    months = sorted(mrr)
    assert mrr[months[-1]] > 5 * mrr[months[0]], "MRR should grow strongly over 24 months"


def test_web_conversion_rate_is_sane(datasets) -> None:
    ws = datasets["web_analytics"]["web_sessions"]
    conv = sum(ws.column("converted").to_pylist()) / ws.num_rows
    assert 0.01 < conv < 0.25, f"implausible conversion rate {conv:.3f}"
    # Funnel monotonicity: sessions reaching step k never exceed step k-1.
    pv = datasets["web_analytics"]["web_pageviews"]
    step_counts: dict[int, int] = {}
    for s in pv.column("step").to_pylist():
        if s is not None:
            step_counts[s] = step_counts.get(s, 0) + 1
    for k in range(2, 6):
        assert step_counts[k] <= step_counts[k - 1], f"funnel step {k} exceeds step {k - 1}"


def test_finance_has_open_ar_and_growing_headcount(datasets) -> None:
    inv = datasets["finance_ops"]["fin_invoices"]
    statuses = inv.column("status").to_pylist()
    assert "paid" in statuses and "overdue" in statuses and "open" in statuses
    unpaid = sum(1 for s in statuses if s != "paid")
    assert 0 < unpaid < inv.num_rows * 0.5, "expected a minority of unpaid invoices"

    hc = datasets["finance_ops"]["fin_headcount"]
    totals: dict[str, int] = {}
    for m, n in zip(hc.column("month").to_pylist(), hc.column("headcount").to_pylist()):
        totals[m] = totals.get(m, 0) + n
    months = sorted(totals)
    assert totals[months[-1]] > totals[months[0]], "headcount should grow"


def test_retail_star_join_resolves_every_fact_row(datasets) -> None:
    con = duckdb.connect(":memory:")
    try:
        for name, tbl in datasets["retail_sales"].items():
            con.register(name, tbl)
        total = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        joined = con.execute(
            """
            SELECT COUNT(*) FROM sales s
            JOIN dim_regions   r ON s.region_id   = r.region_id
            JOIN dim_products  p ON s.product_id  = p.product_id
            JOIN dim_customers c ON s.customer_id = c.customer_id
            """
        ).fetchone()[0]
        assert total > 0 and joined == total
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 5. Local parquet export + view_sql pipeline (temp dir; forces a fresh write)
# ---------------------------------------------------------------------------


def test_local_parquet_export_and_views_queryable(tmp_path) -> None:
    from app.demo_bundle import (
        export_demo_parquet_local,
        local_parquet_datastore_config,
    )

    paths = export_demo_parquet_local(base_dir=tmp_path)
    assert set(paths) == set(ALL_TABLES)
    for table, path in paths.items():
        assert path.endswith(f"{table}.parquet")

    cfg = local_parquet_datastore_config(base_dir=tmp_path)
    assert cfg["connector_type"] == "duckdb"
    assert cfg["database"] == ":memory:"

    con = duckdb.connect(":memory:")
    try:
        for stmt in cfg["view_sql"].split(";"):
            if stmt.strip():
                con.execute(stmt)
        for table in ALL_TABLES:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert n > 0, f"view {table} returned 0 rows from local parquet"
    finally:
        con.close()


def test_local_parquet_export_is_idempotent(tmp_path) -> None:
    from pathlib import Path

    from app.demo_bundle import export_demo_parquet_local

    first = export_demo_parquet_local(base_dir=tmp_path)
    stamps = {t: Path(p).stat().st_mtime_ns for t, p in first.items()}

    second = export_demo_parquet_local(base_dir=tmp_path)
    assert second == first
    for t, p in second.items():
        assert Path(p).stat().st_mtime_ns == stamps[t], (
            f"{t} was rewritten on idempotent re-export"
        )


# ---------------------------------------------------------------------------
# 6. Legacy .duckdb file builder still works (all 17 tables)
# ---------------------------------------------------------------------------


def test_build_duckdb_file_contains_all_tables(tmp_path) -> None:
    from seed_data_duckdb import build_duckdb_file

    path = build_duckdb_file(str(tmp_path / "sample.duckdb"))
    con = duckdb.connect(database=path, read_only=True)
    try:
        have = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        assert set(ALL_TABLES) <= have, f"missing tables: {set(ALL_TABLES) - have}"
        n = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        assert n > 0
    finally:
        con.close()
