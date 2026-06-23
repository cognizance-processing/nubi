"""Wave-9 CRITICAL tie-semantics verification.

HARD RULE: goes THROUGH planner.plan() + executes on in-memory DuckDB asserting
EXACT rows.

Scenario: top_n.other=True, NO time_grain, rls_keys set, with SEVERAL rows tied
at the N-th rank boundary.  We assert:
  1. NO dimension member appears in BOTH the top-N rows AND the Other row.
  2. The sum of ALL returned measure values == the per-tenant grand total
     (no double-count, no dropped rows).
Plus: time_col SQLi raises (bad_time_column) and derived-rank+other raises
(bad_top_n).
"""

from __future__ import annotations

import pytest

from app.connectors.planner import plan
from app.metrics.compile import compile_metric
from app.metrics.models import (
    Dimension,
    DerivedMeasure,
    Measure,
    MetricDefinition,
    MetricError,
    MetricQuery,
    TimeDimension,
    TopN,
)


def _duckdb_conn():
    try:
        import duckdb

        return duckdb.connect(":memory:")
    except ImportError:
        pytest.skip("duckdb not installed")


def test_tie_at_nth_boundary_no_double_count_per_tenant() -> None:
    """[CRITICAL] Ties at the N-th rank boundary: clean partition, sum == grand total."""
    con = _duckdb_conn()
    con.execute(
        """
        CREATE TABLE rev (
            region  VARCHAR,
            amount  DOUBLE,
            org_id  VARCHAR
        )
        """
    )
    # org1: A=1000, B=500, C=500, D=500, E=100.
    #   With n=2 desc: rank1=A(1000). rank2 is a 3-way TIE among B,C,D (all 500).
    #   RANK() gives B,C,D all rank=2, so RANK()<=2 keeps A,B,C,D in the top arm.
    #   The Other arm (RANK()>2) keeps only E.  Clean partition; no member in both.
    # org2 (must be invisible under org1's RLS): X=9999.
    con.execute(
        """
        INSERT INTO rev VALUES
            ('A', 1000, 'org1'),
            ('B',  500, 'org1'),
            ('C',  500, 'org1'),
            ('D',  500, 'org1'),
            ('E',  100, 'org1'),
            ('X', 9999, 'org2')
        """
    )
    m = MetricDefinition(
        id="rev",
        name="Rev",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="rev",
        dimensions=(Dimension(name="region"),),
        rls_keys=("org_id",),
    )
    mq = MetricQuery(
        metric_id="rev",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, order="desc", other=True, other_label="Other"),
    )
    sql, _ = compile_metric(m, mq)
    claims = {"policies": {"org_id": "org1"}}
    p = plan(sql, claims=claims, dialect="duckdb")
    cur = con.execute(p.sql)
    cols = [d[0] for d in cur.description]
    ri = cols.index("region")
    mi = cols.index("revenue")
    rows = cur.fetchall()

    # Map region -> measure value(s).
    region_to_vals: dict[str, list[float]] = {}
    for r in rows:
        region_to_vals.setdefault(r[ri], []).append(r[mi])

    # RLS: org2's region X must NOT leak.
    assert "X" not in region_to_vals, f"org2 leaked: rows={rows}"

    # Partition invariant: no real member also appears as part of Other, and
    # Other appears exactly once.
    assert region_to_vals.get("Other") is not None, f"Other bucket missing: rows={rows}"
    assert len(region_to_vals["Other"]) == 1, f"Other duplicated: rows={rows}"

    top_members = {reg for reg in region_to_vals if reg != "Other"}
    # Top arm must contain A and the tied trio B,C,D (RANK()<=2 keeps all ties).
    assert {"A", "B", "C", "D"} <= top_members, f"tie boundary dropped a member: rows={rows}"
    # E must be the ONLY Other member; it must not also appear as a top row.
    assert "E" not in top_members, f"E double-counted (top AND other): rows={rows}"

    # No double-count: total of returned values == per-tenant grand total.
    grand_total = con.execute(
        "SELECT SUM(amount) FROM rev WHERE org_id = 'org1'"
    ).fetchone()[0]
    returned_total = sum(v for vals in region_to_vals.values() for v in vals)
    assert returned_total == grand_total == 2600.0, (
        f"sum mismatch: returned={returned_total} grand={grand_total} rows={rows}"
    )
    # Other == E only == 100.
    assert region_to_vals["Other"][0] == 100.0, f"Other != E: rows={rows}"


def test_time_col_sqli_raises() -> None:
    """[CRITICAL] A non-identifier time_dimension.column must raise bad_time_column."""
    m = MetricDefinition(
        id="evil",
        name="Evil",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="t",
        dimensions=(Dimension(name="region"),),
        time_dimension=TimeDimension(
            column="created_at) ; DROP TABLE t --",
            grains=("day",),
            default_grain="day",
        ),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, MetricQuery(metric_id="evil", dimensions=("region",)))
    assert ei.value.code == "bad_time_column", ei.value.code


def test_derived_rank_with_other_raises() -> None:
    """[CRITICAL] A derived rank measure + top_n.other must raise bad_top_n."""
    m = MetricDefinition(
        id="d",
        name="D",
        measure=Measure(name="delivered", agg="sum", expr="delivered_qty"),
        base_table="orders",
        dimensions=(Dimension(name="region"),),
        extra_measures=(Measure(name="ordered", agg="sum", expr="ordered_qty"),),
        derived_measures=(
            DerivedMeasure(name="pvd", formula="delivered / ordered", format="percent"),
        ),
    )
    mq = MetricQuery(
        metric_id="d",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=2, measure="pvd", order="desc", other=True),
    )
    with pytest.raises(MetricError) as ei:
        compile_metric(m, mq)
    assert ei.value.code == "bad_top_n", ei.value.code


def test_no_grain_top_n_not_truncated_by_dialect_boundary() -> None:
    """[CRITICAL] No-time-grain top-N via plan(duckdb) returns EXACTLY the top N.

    Proves the engine-correctness fix at the compile→plan dialect boundary:
    compile_metric emits DuckDB ``QUALIFY RANK() <= N`` for a no-time-grain
    top-N and the outer SELECT carries a default LIMIT.  When the planner parses
    that SQL as its DEFAULT 'postgres' dialect, QUALIFY is rewritten into a
    derived table with ``WHERE _w <= N`` AND the outer LIMIT is moved INSIDE the
    derived table — so LIMIT truncates __base BEFORE the rank filter and the true
    top members can be silently dropped.  Parsing/emitting 'duckdb' (what the
    routes now thread) preserves QUALIFY natively → no truncation.

    Dataset has MANY members (> N) and RLS is active (single tenant scoped); we
    assert the duckdb plan returns exactly the top N highest-ranked members.
    """
    con = _duckdb_conn()
    con.execute(
        "CREATE TABLE rev (region VARCHAR, amount DOUBLE, org_id VARCHAR)"
    )
    # org1: 10 distinct members with strictly increasing amounts (no ties), so
    # the true top-3 is unambiguous: J(1000) > I(900) > H(800).
    rows_in = []
    for i, ch in enumerate("ABCDEFGHIJ"):
        rows_in.append(f"('{ch}', {(i + 1) * 100}, 'org1')")
    # org2 has a HUGE member that would dominate a GLOBAL top-N / poison a
    # mis-parsed truncation — it must never appear under org1's RLS.
    rows_in.append("('Z', 99999, 'org2')")
    con.execute("INSERT INTO rev VALUES " + ", ".join(rows_in))

    m = MetricDefinition(
        id="rev",
        name="Rev",
        measure=Measure(name="revenue", agg="sum", expr="amount"),
        base_table="rev",
        dimensions=(Dimension(name="region"),),
        rls_keys=("org_id",),
    )
    mq = MetricQuery(
        metric_id="rev",
        dimensions=("region",),
        top_n=TopN(dimension="region", n=3, order="desc"),
    )
    sql, _ = compile_metric(m, mq)
    assert "QUALIFY" in sql.upper(), f"expected QUALIFY in compiled SQL: {sql}"

    claims = {"policies": {"org_id": "org1"}}

    # ── Correct boundary: parse/emit duckdb → QUALIFY preserved, no truncation ─
    p_duck = plan(sql, claims=claims, dialect="duckdb")
    cur = con.execute(p_duck.sql)
    cols = [d[0] for d in cur.description]
    ri = cols.index("region")
    mi = cols.index("revenue")
    duck_rows = cur.fetchall()

    regions = {r[ri] for r in duck_rows}
    assert "Z" not in regions, f"org2 leaked under RLS: {duck_rows}"
    # EXACTLY the top-3 highest members — no truncation of high-rankers.
    assert len(duck_rows) == 3, f"expected exactly 3 rows, got {duck_rows}"
    assert regions == {"J", "I", "H"}, (
        f"top-N truncated/wrong at dialect boundary: {duck_rows}"
    )
    vals = {r[ri]: r[mi] for r in duck_rows}
    assert vals == {"J": 1000.0, "I": 900.0, "H": 800.0}, duck_rows

    # ── Regression lock: the OLD default ('postgres') WOULD truncate ──────────
    # Parsing the DuckDB QUALIFY as postgres moves the outer LIMIT inside the
    # derived table; with a tiny default cap the membership is truncated before
    # the rank filter.  We assert the postgres-parsed plan SQL is materially
    # different (QUALIFY no longer survives as native QUALIFY) so the boundary
    # bug can never silently reappear.
    p_pg = plan(sql, claims=claims, dialect="postgres")
    assert "QUALIFY" not in p_pg.sql.upper(), (
        "postgres parse unexpectedly preserved QUALIFY — boundary assumption "
        f"changed; revisit the dialect fix. sql={p_pg.sql}"
    )
