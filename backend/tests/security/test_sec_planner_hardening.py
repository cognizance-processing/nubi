"""The planner only ever emits read-only SQL, and row policies reach every base table."""

from __future__ import annotations

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)


def _plan(sql, policies=None, dialect="duckdb"):
    from app.connectors.planner import plan

    return plan(sql, claims={"policies": policies or {}}, dialect=dialect)


NOT_READ_ONLY = [
    ("postgres", "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d"),
    ("postgres", "WITH u AS (UPDATE orders SET amount = 0 RETURNING *) SELECT * FROM u"),
    ("postgres", "WITH i AS (INSERT INTO orders VALUES (1) RETURNING *) SELECT * FROM i"),
    ("postgres", "SELECT * INTO stolen FROM orders"),
    ("postgres", "SELECT * FROM orders FOR UPDATE"),
    ("postgres", "SELECT * FROM orders FOR SHARE"),
    ("mysql", "SELECT * FROM orders INTO OUTFILE '/tmp/x'"),
    ("mysql", "SELECT * FROM orders FOR UPDATE"),
    ("postgres", "SELECT pg_read_file('/etc/passwd')"),
    ("postgres", "SELECT pg_read_binary_file('/etc/passwd')"),
    ("postgres", "SELECT pg_ls_dir('/')"),
    ("postgres", "SELECT lo_import('/etc/passwd')"),
    ("postgres", "SELECT pg_sleep(30)"),
    ("postgres", "SELECT * FROM orders WHERE id = (SELECT 1 FROM pg_sleep(30))"),
    ("postgres", "SELECT * FROM dblink('host=10.0.0.1', 'select 1') AS t(a int)"),
    ("postgres", "SELECT set_config('role', 'postgres', false)"),
    ("postgres", "SELECT query_to_xml('select 1', true, true, '')"),
    ("mysql", "SELECT load_file('/etc/passwd')"),
    ("mysql", "SELECT sleep(30)"),
    ("mysql", "SELECT benchmark(100000000, md5('x'))"),
    ("duckdb", "SELECT * FROM read_csv_auto('/etc/passwd')"),
    ("duckdb", "SELECT * FROM read_text('/proc/self/environ')"),
    ("duckdb", "SELECT * FROM read_parquet('s3://other/x.parquet')"),
    ("duckdb", "SELECT * FROM '/etc/passwd'"),
    ("duckdb", "SELECT * FROM orders WHERE x IN (SELECT a FROM read_json('/tmp/x.json'))"),
    ("duckdb", "SELECT getenv('JWT_SECRET')"),
    ("tsql", "SELECT * FROM OPENROWSET(BULK '/etc/passwd', SINGLE_CLOB) AS x"),
]


@pytest.mark.parametrize("dialect,sql", NOT_READ_ONLY, ids=[s[:60] for _d, s in NOT_READ_ONLY])
def test_planner_refuses_anything_that_is_not_a_plain_read(dialect, sql):
    from app.errors import AppError

    with pytest.raises(AppError) as exc:
        _plan(sql, dialect=dialect)
    assert exc.value.status == 400


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT region, sum(amount) AS total FROM orders GROUP BY region",
        "WITH t AS (SELECT * FROM orders) SELECT count(*) FROM t",
        "SELECT o.id, c.name FROM orders o JOIN customers c ON c.id = o.customer_id",
        "SELECT * FROM orders WHERE created_at >= date_trunc('month', current_date)",
        "SELECT a FROM x UNION ALL SELECT a FROM y",
        "SELECT coalesce(max(amount), 0), lower(trim(name)), length(name) FROM orders GROUP BY name",
    ],
)
def test_ordinary_reads_still_plan(sql):
    assert _plan(sql).sql


# ---------------------------------------------------------------------------
# Row policies
# ---------------------------------------------------------------------------


@pytest.fixture
def ledger():
    import duckdb

    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE ledger AS SELECT * FROM (VALUES "
        "('EU', 'eu-secret', 10), ('EU', 'eu-secret-2', 20), ('US', 'us-secret', 30)) t(region, secret, amount)"
    )
    conn.execute("CREATE TABLE dim AS SELECT * FROM (VALUES ('EU', 'Europe'), ('US', 'America')) t(code, label)")
    yield conn
    conn.close()


def _run(conn, sql, policies):
    from app.errors import AppError

    try:
        physical = _plan(sql, policies)
    except AppError:
        return None  # refused outright — also acceptable
    try:
        return conn.execute(physical.sql, physical.params or []).fetchall()
    except Exception:  # noqa: BLE001 — the rewritten query failing closed is acceptable
        return None


BYPASSES = [
    "SELECT * FROM (SELECT secret, 'EU' AS region FROM ledger) x",
    "SELECT secret FROM (SELECT secret, 'EU' AS region FROM ledger) x",
    "WITH x AS (SELECT secret, 'EU' AS region FROM ledger) SELECT * FROM x",
    "WITH x AS (SELECT secret FROM ledger) SELECT secret FROM x",
    "SELECT secret, 'EU' AS region FROM ledger UNION ALL SELECT secret, 'EU' FROM ledger",
    "SELECT (SELECT string_agg(secret, ',') FROM ledger) AS leak",
    "SELECT (SELECT string_agg(secret, ',') FROM ledger) AS leak FROM ledger",
    "SELECT region, (SELECT max(secret) FROM ledger l2 WHERE l2.region <> ledger.region) AS leak FROM ledger",
    "SELECT * FROM dim WHERE EXISTS (SELECT 1 FROM ledger WHERE secret LIKE 'us-%')",
    "SELECT (SELECT string_agg(c2, ',') FROM ledger AS x(c1, c2, c3)) AS leak FROM (SELECT 'EU' AS region) o",
    "SELECT secret FROM ledger AS x(r, secret, a)",
    "SELECT * FROM ledger a JOIN ledger b ON a.amount < b.amount",
    "SELECT secret FROM ledger, (SELECT 'EU' AS region) f",
    "SELECT d.label, l.secret FROM dim d LEFT JOIN ledger l ON true",
]


@pytest.mark.parametrize("sql", BYPASSES, ids=[s[:70] for s in BYPASSES])
def test_row_policies_cannot_be_sidestepped(ledger, sql):
    rows = _run(ledger, sql, {"region": "EU"})
    if rows is None:
        return
    assert "us-secret" not in str(rows), rows


def test_row_policies_still_return_the_callers_rows(ledger):
    assert sorted(r[0] for r in _run(ledger, "SELECT secret FROM ledger", {"region": "EU"})) == ["eu-secret", "eu-secret-2"]
    assert _run(ledger, "SELECT sum(a) FROM (SELECT amount AS a FROM ledger) x", {"region": "EU"}) == [(30,)]
    assert _run(ledger, "WITH t AS (SELECT * FROM ledger) SELECT count(*) FROM t", {"region": "US"}) == [(1,)]
    rows = _run(ledger, "SELECT l.secret, d.label FROM ledger l JOIN dim d ON d.code = l.region", {"region": "US"})
    assert rows == [("us-secret", "America")]
    assert _run(ledger, "SELECT count(*) FROM ledger", {}) == [(3,)]


# ---------------------------------------------------------------------------
# Raw interpolation in query templates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["revenue", "orders.amount", "col_1"])
def test_raw_interpolation_accepts_identifiers(value):
    from app.connectors.template import render_sql_template

    sql, params = render_sql_template("SELECT {{ col | sqlsafe }} FROM t", {"col": value})
    assert sql == f"SELECT {value} FROM t" and not params


@pytest.mark.parametrize(
    "value", ["1; DROP TABLE t", "a FROM t UNION SELECT secret", "x'--", "(select 1)", "a b", "", None, 5]
)
def test_raw_interpolation_refuses_anything_else(value):
    from app.connectors.template import render_sql_template

    with pytest.raises(Exception):
        render_sql_template("SELECT {{ col | sqlsafe }} FROM t", {"col": value})
