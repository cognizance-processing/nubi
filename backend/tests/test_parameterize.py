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
    options_query_for_table,
    options_query_sql,
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


# ---------------------------------------------------------------------------
# Clause-end detection — the anchor sqlglot's token offsets cannot give us
# ---------------------------------------------------------------------------


class TestClauseEnd:
    """Where the guarded block lands when the WHERE clause does not end in a
    token sqlglot records an offset for."""

    def test_steps_over_a_subquery_closing_paren(self):
        # The `)` closing a scalar subquery carries no token offset, so the
        # last recorded offset sits INSIDE the subquery.
        sql = (
            "SELECT a.account, i.plan, SUM(i.amount) AS mrr "
            "FROM inv i JOIN acct a ON i.aid = a.id "
            "WHERE i.month = (SELECT MAX(month) FROM inv) "
            "GROUP BY a.account, i.plan"
        )
        r = parameterize_sql(sql, "plan", "plan", dialect="mysql")
        assert r.ok, r.reason
        assert "FROM inv) {% if plan %}" in r.sql
        assert "GROUP BY" in r.sql.split("{% endif %}")[1]

    def test_steps_over_a_paren_wrapping_the_whole_condition(self):
        # Landing inside these parens parses and passes the inert check, then
        # silently binds the filter to one side of the OR.
        sql = "SELECT plan, x FROM t WHERE (a = 1 OR b = 2)"
        r = parameterize_sql(sql, "plan", "plan", dialect="mysql")
        assert r.ok, r.reason
        assert r.sql.startswith("SELECT plan, x FROM t WHERE (a = 1 OR b = 2) {% if plan %}")

    def test_does_not_escape_the_derived_table_being_filtered(self):
        sql = "SELECT m, SUM(mrr) FROM (SELECT m, plan, amt AS mrr FROM inv) z GROUP BY m"
        r = parameterize_sql(sql, "plan", "plan", dialect="mysql")
        assert r.ok, r.reason
        assert "{% endif %}) z" in r.sql

    def test_handles_a_where_ending_in_a_bare_keyword(self):
        # NULL is an exp.Null with no source offset at all.
        sql = "SELECT plan, x FROM t WHERE a IS NOT NULL GROUP BY plan, x"
        r = parameterize_sql(sql, "plan", "plan", dialect="mysql")
        assert r.ok, r.reason
        assert "IS NOT NULL {% if plan %}" in r.sql

    def test_ignores_parens_inside_a_string_literal(self):
        sql = "SELECT plan, n FROM s WHERE note = 'a)b('"
        r = parameterize_sql(sql, "plan", "plan", dialect="mysql")
        assert r.ok, r.reason
        assert r.sql.endswith("{% endif %}")

    def test_a_second_multiselect_filter_lands_after_the_first_guard(self):
        # `IN {{ x | inclause }}` is the shape this module itself emits, so a
        # regression here means only ONE filter can ever be added by the button.
        sql = (
            "SELECT plan, x FROM t WHERE 1=1 "
            "{% if region %} AND region IN {{ region | inclause }} {% endif %} "
            "GROUP BY plan, x"
        )
        r = parameterize_sql(sql, "plan", "plan", dialect="mysql")
        assert r.ok, r.reason
        assert r.sql.index("{% if region %}") < r.sql.index("{% if plan %}")
        assert r.sql.index("{% endif %}") < r.sql.index("{% if plan %}")


# ---------------------------------------------------------------------------
# options_query_sql — where a dropdown's choices come from
# ---------------------------------------------------------------------------


class TestOptionsQuerySql:
    def test_reads_distinct_values_from_the_base_table(self):
        r = options_query_sql("SELECT month, plan, amt FROM inv", "plan", dialect="postgres")
        assert r.ok, r.reason
        assert "DISTINCT" in r.sql
        assert "AS value" in r.sql and "AS label" in r.sql
        assert "inv" in r.sql

    def test_follows_the_column_back_through_ctes(self):
        sql = (
            "WITH base AS (SELECT plan AS tier FROM warehouse.public.inv) "
            "SELECT tier, COUNT(*) FROM base GROUP BY tier"
        )
        r = options_query_sql(sql, "tier", dialect="postgres")
        assert r.ok, r.reason
        assert "warehouse.public.inv" in r.sql
        assert "inv.plan" in r.sql  # renamed back to the physical column

    def test_filters_on_the_underlying_column_not_the_alias(self):
        r = options_query_sql(
            "SELECT br.description AS Region FROM regions br", "Region", dialect="postgres"
        )
        assert r.ok, r.reason
        assert "description" in r.sql
        assert "Region" not in r.sql.replace("AS value", "").replace("AS label", "")

    def test_is_identical_however_the_source_query_aliased_its_tables(self):
        # Four widgets filtering one column must not leave four options queries.
        a = options_query_sql("SELECT i.plan FROM inv i JOIN acct a ON a.id=i.aid", "plan")
        b = options_query_sql("SELECT plan FROM inv GROUP BY plan", "plan")
        assert a.ok and b.ok
        assert a.sql == b.sql

    def test_refuses_a_computed_column(self):
        r = options_query_sql("SELECT UPPER(plan) AS p FROM inv", "p")
        assert not r.ok
        assert "computed" in r.reason

    def test_refuses_an_ambiguous_unqualified_column_across_a_join(self):
        r = options_query_sql("SELECT plan FROM a JOIN b ON a.id = b.id", "plan")
        assert not r.ok
        assert "ambiguous" in r.reason

    def test_renders_native_row_limiting_per_dialect(self):
        sql = "SELECT plan FROM inv"
        assert "LIMIT 1000" in options_query_sql(sql, "plan", dialect="postgres").sql
        assert "TOP 1000" in options_query_sql(sql, "plan", dialect="tsql").sql
        assert "FETCH FIRST 1000 ROWS ONLY" in options_query_sql(
            sql, "plan", dialect="oracle"
        ).sql

    def test_every_supported_connector_dialect_generates(self):
        from app.connectors.dialects import CONNECTOR_DIALECT

        for dialect in sorted(set(CONNECTOR_DIALECT.values())):
            r = options_query_sql("SELECT plan FROM inv", "plan", dialect=dialect)
            assert r.ok, f"{dialect}: {r.reason}"
            assert "DISTINCT" in r.sql


# ---------------------------------------------------------------------------
# options_query_for_table — the query picker's "build one from a column" path
# ---------------------------------------------------------------------------


class TestOptionsQueryForTable:
    """Building a dropdown's choices from a table/column the author picked.

    The counterpart to inferring the source from an existing query, for the
    cases inference cannot serve: a filter not yet connected to any widget, or
    a column that is computed everywhere it appears.
    """

    def test_builds_from_a_table_and_column(self):
        r = options_query_for_table("invoices", "plan", dialect="postgres")
        assert r.ok, r.reason
        assert "DISTINCT" in r.sql
        assert "AS value" in r.sql and "AS label" in r.sql
        assert "invoices" in r.sql

    def test_qualifies_with_the_schema_when_given(self):
        r = options_query_for_table("invoices", "plan", schema="analytics", dialect="postgres")
        assert r.ok, r.reason
        assert "analytics.invoices" in r.sql

    def test_matches_the_inferred_generator_byte_for_byte(self):
        # Two spellings of the same query would defeat de-duplication, leaving
        # one options query per ENTRY POINT rather than one per column.
        picked = options_query_for_table("inv", "plan", dialect="duckdb")
        inferred = options_query_sql(
            "SELECT i.plan FROM inv i JOIN acct a ON a.id = i.aid", "plan", dialect="duckdb"
        )
        assert picked.ok and inferred.ok
        assert picked.sql == inferred.sql

    def test_respects_the_row_cap(self):
        r = options_query_for_table("inv", "plan", dialect="postgres", limit=25)
        assert r.ok, r.reason
        assert "LIMIT 25" in r.sql

    def test_quotes_a_hostile_identifier_rather_than_interpolating_it(self):
        # sqlglot renders this as ONE quoted identifier; the statement stays a
        # single SELECT. (It then fails at execution, which the route reports.)
        r = options_query_for_table("inv; DROP TABLE users", "plan", dialect="postgres")
        assert r.ok, r.reason
        assert '"inv; DROP TABLE users"' in r.sql
        assert r.sql.count("SELECT") == 1
        assert "DROP TABLE users;" not in r.sql

    def test_refuses_an_identifier_that_does_not_round_trip(self):
        # `inv--x` parses as `inv` plus a comment: the object queried would not
        # be the one the author named.
        assert not options_query_for_table("inv--x", "plan").ok
        assert not options_query_for_table("a.b.c.d", "plan").ok
        assert not options_query_for_table("inv", "plan--x").ok

    def test_refuses_a_qualified_column(self):
        # It would silently disagree with the table argument.
        r = options_query_for_table("inv", "other.plan")
        assert not r.ok
        assert "unqualified" in r.reason

    def test_refuses_empty_inputs(self):
        assert not options_query_for_table("", "plan").ok
        assert not options_query_for_table("inv", "  ").ok

    def test_generates_for_every_supported_connector_dialect(self):
        from app.connectors.dialects import CONNECTOR_DIALECT

        for dialect in sorted(set(CONNECTOR_DIALECT.values())):
            r = options_query_for_table("inv", "plan", dialect=dialect)
            assert r.ok, f"{dialect}: {r.reason}"
            assert "DISTINCT" in r.sql
