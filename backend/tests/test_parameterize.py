"""Tests for app.queries.parameterize — adding a filter to a query's SQL.

The rewriter's job is to put a guarded filter block where a human would put
it, without disturbing anything else. The tests that matter most are the ones
asserting it does NOT fire: a rewrite that lands in the wrong scope produces
plausible-but-wrong numbers on a dashboard, which is worse than refusing.
"""

from __future__ import annotations

import pytest

from app.queries.parameterize import (
    build_filter_block,
    filterable_columns,
    neutralize_jinja,
    parameterize_sql,
)


# ---------------------------------------------------------------------------
# neutralize_jinja — analysing a query that ALREADY has a filter
# ---------------------------------------------------------------------------


class TestNeutralizeJinja:
    """A query with one filter is exactly the query someone wants to add a
    second filter to, but sqlglot cannot parse Jinja. Substitutions must keep
    the byte length identical so source offsets still address the original."""

    def test_preserves_length_exactly(self):
        sql = "SELECT a FROM t WHERE 1=1 {% if r %} AND c IN {{ r | inclause }} {% endif %}"
        assert len(neutralize_jinja(sql)) == len(sql)

    def test_untouched_when_there_is_no_jinja(self):
        sql = "SELECT a FROM t WHERE b = 1"
        assert neutralize_jinja(sql) == sql

    def test_guarded_block_becomes_parseable_sql(self):
        import sqlglot

        sql = "SELECT a FROM t WHERE 1=1 {% if r %} AND c IN {{ r | inclause }} {% endif %}"
        sqlglot.parse_one(neutralize_jinja(sql), dialect="mysql")

    def test_if_else_endif_drops_the_else_branch(self):
        """Keeping both branches leaves two juxtaposed expressions
        (`r.country_id = NULL TRUE`) which will not parse."""
        import sqlglot

        sql = "SELECT a FROM t WHERE ({% if c %}r.country_id = {{ c }}{% else %}TRUE{% endif %})"
        out = neutralize_jinja(sql)
        assert len(out) == len(sql)
        assert "TRUE" not in out.split("r.country_id")[1]
        sqlglot.parse_one(out, dialect="mysql")

    def test_real_converted_query_shape_parses(self):
        """The shape every converted board carries."""
        import sqlglot

        sql = (
            "SELECT 'Period 1' AS Period, COUNT(o.id) AS Orders "
            "FROM orders o JOIN regions r ON r.id = o.region_id "
            "WHERE ({% if country_description %}r.country_id = {{ country_description }}"
            "{% else %}TRUE{% endif %})"
        )
        out = neutralize_jinja(sql)
        assert len(out) == len(sql)
        sqlglot.parse_one(out, dialect="mysql")

    def test_columns_are_found_in_a_query_that_already_has_a_filter(self):
        sql = (
            "SELECT region, n FROM t "
            "WHERE 1=1 {% if country %} AND country = {{ country }} {% endif %}"
        )
        names = {c["name"].lower() for c in filterable_columns(sql, dialect="mysql")}
        assert "region" in names

    def test_a_second_filter_can_be_added_alongside_an_existing_one(self):
        sql = (
            "SELECT region, n FROM t "
            "WHERE 1=1 {% if country %} AND country = {{ country }} {% endif %}"
        )
        r = parameterize_sql(sql, "region", "region", dialect="mysql")
        assert r.ok
        # The original filter survives untouched next to the new one.
        assert "{% if country %}" in r.sql
        assert "{% if region %}" in r.sql


# ---------------------------------------------------------------------------
# build_filter_block
# ---------------------------------------------------------------------------


class TestBuildFilterBlock:
    def test_multiselect_uses_inclause(self):
        got = build_filter_block("region", "r.description", "multiselect")
        assert got == "{% if region %} AND r.description IN {{ region | inclause }} {% endif %}"

    def test_select_uses_equality(self):
        got = build_filter_block("region", "r.description", "select")
        assert got == "{% if region %} AND r.description = {{ region }} {% endif %}"

    def test_daterange_is_two_sided_and_half_open(self):
        got = build_filter_block("window", "o.order_date", "daterange")
        assert ">= {{ window.from }}" in got
        assert "< {{ window.to }}" in got

    def test_never_uses_sqlsafe(self):
        """Values must always be bound, never interpolated into SQL text."""
        for subtype in ("multiselect", "select", "daterange", "list"):
            assert "sqlsafe" not in build_filter_block("p", "col", subtype)


# ---------------------------------------------------------------------------
# parameterize_sql — the happy paths
# ---------------------------------------------------------------------------


class TestParameterizeSimple:
    def test_adds_where_to_a_query_that_has_none(self):
        r = parameterize_sql("SELECT region, n FROM t", "region", "region", dialect="mysql")
        assert r.ok
        assert "WHERE 1=1 {% if region %}" in r.sql
        assert "AND region IN {{ region | inclause }}" in r.sql

    def test_appends_to_an_existing_where(self):
        r = parameterize_sql(
            "SELECT region, n FROM t WHERE n > 0", "region", "region", dialect="mysql"
        )
        assert r.ok
        # Extends the existing WHERE rather than opening a second one.
        assert r.sql.count("WHERE") == 1
        assert "n > 0 {% if region %}" in r.sql

    def test_preserves_the_original_sql_verbatim(self):
        """Text is spliced, never regenerated — round-tripping real SQL
        through a generator reformats it wholesale and destroys the author's
        formatting and comments."""
        sql = "SELECT  region,\n   n   -- keep this comment\nFROM t"
        r = parameterize_sql(sql, "region", "region", dialect="mysql")
        assert r.ok
        assert "-- keep this comment" in r.sql
        # Everything before the insertion point is byte-identical.
        assert r.sql.startswith("SELECT  region,\n   n   -- keep this comment")

    def test_filters_on_the_underlying_expression_not_the_alias(self):
        """A SELECT alias is not referenceable from its own WHERE, so the
        predicate must use the expression the alias was built from."""
        r = parameterize_sql(
            "SELECT r.description AS Region FROM regions r",
            "region",
            "Region",
            dialect="mysql",
        )
        assert r.ok
        assert r.column_expr == "r.description"
        assert "AND r.description IN" in r.sql
        assert "AND Region IN" not in r.sql

    def test_column_match_is_case_insensitive(self):
        r = parameterize_sql("SELECT Region FROM t", "region", "region", dialect="mysql")
        assert r.ok


# ---------------------------------------------------------------------------
# parameterize_sql — nested scopes (the case that motivates the whole module)
# ---------------------------------------------------------------------------


class TestParameterizeNested:
    NESTED = (
        "SELECT order_date, SUM(completed) AS completed_units FROM ("
        "SELECT region_desc AS Region, order_date, units_shipped AS completed "
        "FROM order_facts"
        ") t GROUP BY order_date"
    )

    def test_injects_into_the_inner_scope_that_exposes_the_column(self):
        """The outer SELECT aggregates `Region` away entirely, so a predicate
        appended at the top would reference a column that does not exist
        there. It has to go into the subquery."""
        r = parameterize_sql(self.NESTED, "v_region", "Region", dialect="mysql")
        assert r.ok
        assert r.column_expr == "region_desc"
        # The block lands inside the subquery, before the closing paren.
        inner_end = r.sql.index(") t GROUP BY")
        assert r.sql.index("{% if v_region %}") < inner_end

    def test_group_by_stays_after_the_new_where(self):
        r = parameterize_sql(self.NESTED, "v_region", "Region", dialect="mysql")
        assert r.ok
        assert r.sql.index("{% if v_region %}") < r.sql.index("GROUP BY order_date")

    def test_result_parses_once_the_unset_filter_is_removed(self):
        import sqlglot

        r = parameterize_sql(self.NESTED, "v_region", "Region", dialect="mysql")
        assert r.ok
        block = r.sql[r.sql.index("{% if"): r.sql.index("{% endif %}") + len("{% endif %}")]
        sqlglot.parse_one(r.sql.replace(block, ""), dialect="mysql")


# ---------------------------------------------------------------------------
# parameterize_sql — refusals. These are the safety-critical assertions.
# ---------------------------------------------------------------------------


class TestParameterizeRefuses:
    def test_refuses_a_column_the_query_does_not_expose(self):
        r = parameterize_sql("SELECT a, b FROM t", "p", "not_a_column", dialect="mysql")
        assert not r.ok
        assert "not_a_column" in r.reason

    def test_refuses_unparseable_sql(self):
        r = parameterize_sql("SELECT FROM WHERE ***", "p", "a", dialect="mysql")
        assert not r.ok
        assert r.sql is None

    def test_refuses_empty_inputs(self):
        assert not parameterize_sql("", "p", "a").ok
        assert not parameterize_sql("SELECT a FROM t", "", "a").ok
        assert not parameterize_sql("SELECT a FROM t", "p", "").ok

    def test_does_not_match_a_bare_star_projection(self):
        """`SELECT *` might expose the column, but we cannot prove it without
        full schema resolution — guessing risks injecting a predicate on a
        column that isn't there, so it must refuse."""
        r = parameterize_sql("SELECT * FROM t", "region", "region", dialect="mysql")
        assert not r.ok


# ---------------------------------------------------------------------------
# filterable_columns
# ---------------------------------------------------------------------------


class TestFilterableColumns:
    def test_lists_columns_from_every_scope_with_output_flag(self):
        cols = filterable_columns(TestParameterizeNested.NESTED, dialect="mysql")
        by_name = {c["name"].lower(): c for c in cols}
        assert "region" in by_name
        # Region lives only in the subquery — it does not reach the output.
        assert by_name["region"]["in_output"] is False
        assert by_name["region"]["expr"] == "region_desc"
        assert by_name["order_date"]["in_output"] is True

    def test_excludes_aggregates(self):
        """Filtering a SUM needs HAVING, not WHERE — offering it would
        mislead the author into a rewrite that cannot work."""
        cols = filterable_columns(
            "SELECT region, SUM(x) AS total FROM t GROUP BY region", dialect="mysql"
        )
        names = {c["name"].lower() for c in cols}
        assert "region" in names
        assert "total" not in names

    def test_empty_for_unparseable_sql(self):
        assert filterable_columns("SELECT FROM ***") == []

    def test_deduplicates_names_across_scopes(self):
        cols = filterable_columns(TestParameterizeNested.NESTED, dialect="mysql")
        names = [c["name"].lower() for c in cols]
        assert len(names) == len(set(names))
