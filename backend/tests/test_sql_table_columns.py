"""Tests for app.queries.sql_table_columns.extract_table_columns.

The function feeds the estate-wide catalog (app.ai.grounding.build_catalog)
that dashboard filter discovery (Layer 1) and AI grounding both depend on to
answer "which real columns does this table have?" — built by scanning every
registered query's SQL, not by asking the database. Column attribution is the
part that matters most: a column read only through a derived-table alias
(a query that computes a subtotal before joining it — a very common shape)
used to stay keyed by the alias forever, never reaching the real table.
"""

from __future__ import annotations

from app.queries.sql_table_columns import extract_table_columns


def _cols(sql: str, dialect: str = "duckdb"):
    result = extract_table_columns(sql, dialect=dialect)
    assert "error" not in result, result.get("error")
    return {(c["table"], c["column"]) for c in result["columns"]}


class TestDirectTableAttribution:
    """Baseline: a column qualified by (or attributable to) a real table."""

    def test_qualified_column_resolves_through_alias(self):
        cols = _cols("SELECT u.id, u.name FROM users u")
        assert ("users", "id") in cols
        assert ("users", "name") in cols

    def test_unqualified_column_attributed_when_scope_has_one_source(self):
        cols = _cols("SELECT id, name FROM users")
        assert ("users", "id") in cols

    def test_unqualified_column_unresolved_across_a_join(self):
        """Ambiguous — could come from either side — must not guess."""
        cols = _cols("SELECT id FROM users JOIN orders ON users.id = orders.user_id")
        assert (None, "id") in cols

    def test_star_is_skipped(self):
        result = extract_table_columns("SELECT * FROM users", dialect="duckdb")
        assert result["columns"] == []

    def test_column_inside_aggregate_is_included(self):
        cols = _cols("SELECT SUM(amount) AS total FROM fin_invoices")
        assert ("fin_invoices", "amount") in cols


class TestDerivedTableAliasChase:
    """The fix: a column read through a subquery/CTE alias traces back to the
    real table it ultimately comes from, instead of staying keyed by the
    alias (which is not a table at all — it never belonged in the catalog)."""

    def test_passthrough_column_through_one_derived_table_resolves(self):
        sql = (
            "SELECT b.month, b.billed "
            "FROM (SELECT month, SUM(amount) AS billed FROM fin_invoices "
            "GROUP BY month) b"
        )
        cols = _cols(sql)
        # month is a plain passthrough column — chased back to fin_invoices.
        assert ("fin_invoices", "month") in cols
        # billed is a computed SUM — cannot be chased further, stays unresolved.
        assert ("fin_invoices", "billed") not in cols
        assert (None, "billed") in cols
        # The alias itself must never appear as a "table".
        assert not any(t == "b" for t, _ in cols)

    def test_the_exact_headline_metrics_shape_that_motivated_the_fix(self):
        """Mirrors the real board query this was found against: a net-cash
        chart that computes each side's monthly total in its own derived
        table before joining them, while sibling KPI widgets read the SAME
        base tables directly. Before the fix, fin_invoices/fin_payments never
        picked up `month` from this query at all."""
        sql = (
            "SELECT m.month AS month, COALESCE(b.billed, 0) AS billed, "
            "COALESCE(c.collected, 0) AS collected "
            "FROM (SELECT DISTINCT month FROM fin_expenses) m "
            "LEFT JOIN (SELECT month, ROUND(SUM(amount), 2) AS billed "
            "FROM fin_invoices GROUP BY month) b ON m.month = b.month "
            "LEFT JOIN (SELECT month, ROUND(SUM(amount), 2) AS collected "
            "FROM fin_payments GROUP BY month) c ON m.month = c.month"
        )
        cols = _cols(sql)
        assert ("fin_invoices", "month") in cols
        assert ("fin_invoices", "amount") in cols
        assert ("fin_payments", "month") in cols
        assert ("fin_payments", "amount") in cols
        assert ("fin_expenses", "month") in cols
        # Computed aggregates stay unresolved, never misattributed.
        assert ("fin_invoices", "billed") not in cols
        assert ("fin_payments", "collected") not in cols
        # No bogus alias-as-table entries.
        assert not any(t in ("m", "b", "c") for t, _ in cols)

    def test_chase_through_two_levels_of_derived_tables(self):
        sql = (
            "SELECT outer_alias.month "
            "FROM (SELECT inner_alias.month "
            "      FROM (SELECT month FROM fin_expenses) inner_alias"
            "      ) outer_alias"
        )
        cols = _cols(sql)
        assert ("fin_expenses", "month") in cols

    def test_cte_passthrough_column_resolves(self):
        sql = (
            "WITH m AS (SELECT month, amount FROM fin_invoices) "
            "SELECT m.amount FROM m"
        )
        cols = _cols(sql)
        assert ("fin_invoices", "amount") in cols

    def test_unqualified_column_inside_an_ambiguous_join_stays_unresolved(self):
        """The derived table's own unqualified `id` is exactly as ambiguous
        (two joined sources) as it would be at the top level — picking one
        would be a guess. Its OWN qualified references (the join condition)
        are unaffected and still resolve normally."""
        sql = (
            "SELECT d.id FROM "
            "(SELECT id FROM users JOIN orders ON users.id = orders.user_id) d"
        )
        cols = _cols(sql)
        assert (None, "id") in cols
        assert ("users", "id") in cols
        assert ("orders", "user_id") in cols
        # The outer alias itself is never mistaken for a table.
        assert not any(t == "d" for t, _ in cols)

    def test_derived_table_alias_never_appears_as_a_table_name(self):
        """Regression guard for the exact bug this fix closes: the alias
        itself (a fake 'table') must never leak into the attributed set."""
        sql = "SELECT x.a FROM (SELECT a FROM real_table) x"
        cols = _cols(sql)
        assert not any(t == "x" for t, _ in cols)
        assert ("real_table", "a") in cols


class TestRealTablesAndCteExclusion:
    def test_cte_name_excluded_from_tables_list(self):
        sql = "WITH m AS (SELECT id FROM real_table) SELECT * FROM m"
        result = extract_table_columns(sql, dialect="duckdb")
        assert "m" not in result["tables"]
        assert "real_table" in result["tables"]

    def test_subquery_alias_excluded_from_tables_list(self):
        sql = "SELECT * FROM (SELECT 1 AS x FROM real_table) sub"
        result = extract_table_columns(sql, dialect="duckdb")
        assert "sub" not in result["tables"]
        assert "real_table" in result["tables"]


class TestErrorHandling:
    def test_unparseable_sql_returns_error_not_raise(self):
        result = extract_table_columns("SELECT FROM ***", dialect="duckdb")
        assert result == {
            "tables": [], "columns": [], "outputs": [],
            "error": result["error"],
        }
        assert result["error"].startswith("parse_error")

    def test_non_select_statement_returns_error(self):
        result = extract_table_columns("DELETE FROM users", dialect="duckdb")
        assert result["error"] == "not_a_select"
        assert result["tables"] == [] and result["columns"] == []


class TestOutputs:
    def test_aliased_and_bare_outputs(self):
        result = extract_table_columns(
            "SELECT id, name AS full_name FROM users", dialect="duckdb"
        )
        assert result["outputs"] == ["full_name", "id"]

    def test_star_output_is_skipped(self):
        result = extract_table_columns("SELECT * FROM users", dialect="duckdb")
        assert result["outputs"] == []
