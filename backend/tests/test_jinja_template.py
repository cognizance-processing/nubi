"""Tests for the Jinja2-based safe SQL template engine (template.py).

Coverage
--------
(1)  Basic ``{{ name }}`` binding → $N, value in params list.
(2)  Multiple distinct placeholders → $1, $2, … in order.
(3)  Repeated same placeholder → single $N slot, one value in params (dedup).
(4)  No placeholders → SQL unchanged, empty params.
(5)  Conditional ({% if %}) — region set → binds; region absent → no clause.
(6)  Loop ({% for %}) — generates multiple bound params.
(7)  ``| inclause`` filter — list → ($1, $2, $3), each element bound.
(8)  ``| sqlsafe`` filter — value emitted raw, NOT bound (escape hatch).
(9)  Sandbox blocks ``{{ ''.__class__ }}`` style attribute access.
(10) Missing context key → raises (KeyError from planner / UndefinedError from template).
(11) Injection string ``"' OR 1=1 --"`` is bound, never in SQL text.
(12) Drop-table injection is bound as literal string.
(13) UNION injection is bound as literal string.
(14) Multi-statement injection is bound as literal string.
(15) Conditional + injection: branch SQL is safe, value still bound.
(16) inclause with injected strings — each element bound separately.
(17) Dialect mapping: postgres → $N, mysql → %s, sqlite → ?.
(18) Complex template: nested if/for with multiple bindings.
(19) Filter chain ({% if %} uses raw value, output still bound).
(20) Empty inclause raises ValueError (IN () is invalid SQL).
(21) ``render_sql_template`` directly exported from template module.
(22) ``resolve_named_params`` still works end-to-end (planner integration).
"""

from __future__ import annotations

import pytest
import jinja2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(sql: str, context: dict, dialect: str = "postgres"):
    """Shortcut: call render_sql_template and return (rendered_sql, params)."""
    from app.connectors.template import render_sql_template
    return render_sql_template(sql, context, dialect=dialect)


def _resolve(sql: str, named_values: dict, dialect: str = "postgres"):
    """Shortcut: call resolve_named_params from planner."""
    from app.connectors.planner import resolve_named_params
    return resolve_named_params(sql, named_values, dialect=dialect)


# ===========================================================================
# (1) Basic {{ name }} binding
# ===========================================================================


class TestBasicBinding:
    def test_single_var_binds_to_dollar_one(self):
        sql, params = _render("SELECT * FROM t WHERE id = {{ id }}", {"id": 42})
        assert "$1" in sql
        assert "42" not in sql
        assert params == [42]

    def test_string_value_bound_not_interpolated(self):
        sql, params = _render("SELECT * FROM t WHERE name = {{ name }}", {"name": "alice"})
        assert "alice" not in sql
        assert params == ["alice"]

    def test_float_value_bound(self):
        sql, params = _render("SELECT * FROM t WHERE score > {{ threshold }}", {"threshold": 3.14})
        assert "3.14" not in sql
        assert params == [3.14]

    def test_boolean_value_bound(self):
        sql, params = _render("SELECT * FROM t WHERE active = {{ flag }}", {"flag": True})
        assert "True" not in sql
        assert params == [True]

    def test_none_value_bound(self):
        sql, params = _render("SELECT * FROM t WHERE x = {{ val }}", {"val": None})
        assert params == [None]

    def test_no_space_syntax_works(self):
        """{{name}} without spaces is standard Jinja2 and must work."""
        sql, params = _render("SELECT {{x}} FROM t", {"x": 99})
        assert params == [99]
        assert "99" not in sql


# ===========================================================================
# (2) Multiple distinct placeholders
# ===========================================================================


class TestMultiplePlaceholders:
    def test_two_vars_in_order(self):
        sql, params = _render(
            "SELECT * FROM t WHERE a = {{ a }} AND b = {{ b }}",
            {"a": "foo", "b": 10},
        )
        assert "$1" in sql
        assert "$2" in sql
        assert params == ["foo", 10]

    def test_order_follows_first_appearance(self):
        sql, params = _render(
            "SELECT {{ b }}, {{ a }}, {{ c }} FROM t",
            {"a": 1, "b": 2, "c": 3},
        )
        # b appears first → $1, a → $2, c → $3
        assert params == [2, 1, 3]

    def test_three_vars_all_bound(self):
        sql, params = _render(
            "SELECT * FROM t WHERE x = {{ x }} AND y = {{ y }} AND z = {{ z }}",
            {"x": "X", "y": "Y", "z": "Z"},
        )
        assert params == ["X", "Y", "Z"]
        for v in ("X", "Y", "Z"):
            assert v not in sql


# ===========================================================================
# (3) Deduplication: same name used twice → single $N slot
# ===========================================================================


class TestDeduplication:
    def test_same_name_twice_one_slot(self):
        sql, params = _render(
            "SELECT * FROM t WHERE a = {{ x }} OR b = {{ x }}",
            {"x": 7},
        )
        assert params == [7]
        assert sql.count("$1") == 2

    def test_same_name_three_times_one_slot(self):
        sql, params = _render(
            "SELECT {{ n }}, {{ n }}, {{ n }} FROM t",
            {"n": 99},
        )
        assert params == [99]
        assert sql.count("$1") == 3

    def test_dedup_via_resolve_named_params(self):
        """resolve_named_params preserves the single-slot behaviour via Jinja2."""
        from app.connectors.planner import resolve_named_params
        sql, params = resolve_named_params(
            "SELECT * FROM t WHERE a = {{x}} OR b = {{x}}",
            {"x": 7},
        )
        assert params == [7]
        assert sql.count("$1") == 2


# ===========================================================================
# (4) No placeholders
# ===========================================================================


class TestNoPlaceholders:
    def test_plain_sql_unchanged(self):
        original = "SELECT * FROM demo"
        sql, params = _render(original, {})
        assert sql == original
        assert params == []

    def test_extra_context_ignored(self):
        original = "SELECT 1"
        sql, params = _render(original, {"unused": "value"})
        assert sql == original
        assert params == []


# ===========================================================================
# (5) Conditionals ({% if %})
# ===========================================================================


class TestConditionals:
    BASE = "SELECT * FROM t WHERE 1=1 {% if region %} AND region = {{ region }} {% endif %}"

    def test_if_with_value_set_emits_clause_and_binds(self):
        sql, params = _render(self.BASE, {"region": "us-east-1"})
        assert "AND region = $1" in sql.replace("  ", " ").strip() or "$1" in sql
        assert params == ["us-east-1"]
        assert "us-east-1" not in sql

    def test_if_with_empty_string_omits_clause(self):
        sql, params = _render(self.BASE, {"region": ""})
        # Empty string is falsy in Jinja2 → clause omitted
        assert "AND region" not in sql
        assert params == []

    def test_if_with_none_omits_clause(self):
        sql, params = _render(self.BASE, {"region": None})
        assert "AND region" not in sql
        assert params == []

    def test_if_with_zero_omits_clause(self):
        # 0 is falsy — clause omitted
        sql, params = _render(self.BASE, {"region": 0})
        assert "AND region" not in sql
        assert params == []

    def test_if_else_branch(self):
        tmpl = "SELECT * FROM t {% if flag %} WHERE x = {{ x }} {% else %} WHERE y = {{ y }} {% endif %}"
        sql_true, p_true = _render(tmpl, {"flag": True, "x": 1, "y": 2})
        assert "x = $1" in sql_true or "$1" in sql_true
        assert 1 in p_true
        assert 2 not in p_true

        sql_false, p_false = _render(tmpl, {"flag": False, "x": 1, "y": 2})
        assert "y = $1" in sql_false or "$1" in sql_false
        assert 2 in p_false
        assert 1 not in p_false

    def test_nested_if(self):
        tmpl = (
            "SELECT * FROM t WHERE 1=1 "
            "{% if a %} AND a = {{ a }} "
            "{% if b %} AND b = {{ b }} {% endif %}"
            "{% endif %}"
        )
        sql, params = _render(tmpl, {"a": "alpha", "b": "beta"})
        assert params == ["alpha", "beta"]
        assert "alpha" not in sql
        assert "beta" not in sql

    def test_condition_evaluates_on_raw_value_not_placeholder(self):
        """The if condition must branch on the real value, not on a $N string."""
        tmpl = "SELECT {% if level == 'high' %} 'HIGH' {% else %} 'LOW' {% endif %} AS lvl FROM t"
        sql_high, _ = _render(tmpl, {"level": "high"})
        sql_low, _ = _render(tmpl, {"level": "low"})
        assert "HIGH" in sql_high
        assert "LOW" in sql_low


# ===========================================================================
# (6) Loops ({% for %})
# ===========================================================================


class TestLoops:
    def test_for_loop_generates_bindings(self):
        tmpl = "SELECT * FROM t WHERE id IN ({% for i in items %}{{ i }}{% if not loop.last %},{% endif %}{% endfor %})"
        sql, params = _render(tmpl, {"items": [1, 2, 3]})
        # Each item should be bound as a separate $N
        assert "$1" in sql
        assert "$2" in sql
        assert "$3" in sql
        assert params == [1, 2, 3]

    def test_for_loop_no_raw_values_in_sql(self):
        tmpl = "{% for v in vals %}{{ v }}{% if not loop.last %},{% endif %}{% endfor %}"
        sql, params = _render(tmpl, {"vals": ["a", "b", "c"]})
        for v in ("a", "b", "c"):
            assert v not in sql
        assert params == ["a", "b", "c"]

    def test_for_with_index(self):
        tmpl = "{% for x in items %}({{ loop.index }}, {{ x }}){% if not loop.last %},{% endif %}{% endfor %}"
        sql, params = _render(tmpl, {"items": [10, 20]})
        # loop.index (1, 2) and items (10, 20) are all bound
        assert len(params) == 4
        assert 10 in params
        assert 20 in params


# ===========================================================================
# (7) inclause filter
# ===========================================================================


class TestIncluaseFilter:
    def test_list_emits_parenthesised_placeholders(self):
        sql, params = _render(
            "SELECT * FROM t WHERE id IN {{ ids | inclause }}",
            {"ids": [1, 2, 3]},
        )
        assert "($1, $2, $3)" in sql
        assert params == [1, 2, 3]

    def test_single_element_list(self):
        sql, params = _render(
            "SELECT * FROM t WHERE id IN {{ ids | inclause }}",
            {"ids": [42]},
        )
        assert "($1)" in sql
        assert params == [42]

    def test_string_elements_bound(self):
        sql, params = _render(
            "SELECT * FROM t WHERE name IN {{ names | inclause }}",
            {"names": ["alice", "bob"]},
        )
        assert "alice" not in sql
        assert "bob" not in sql
        assert params == ["alice", "bob"]

    def test_mixed_types_bound(self):
        sql, params = _render(
            "SELECT * FROM t WHERE x IN {{ vals | inclause }}",
            {"vals": [1, "two", 3.0]},
        )
        assert params == [1, "two", 3.0]

    def test_inclause_after_other_param(self):
        sql, params = _render(
            "SELECT * FROM t WHERE grp = {{ grp }} AND id IN {{ ids | inclause }}",
            {"grp": "A", "ids": [10, 20]},
        )
        assert params == ["A", 10, 20]
        assert "$1" in sql  # grp
        assert "$2" in sql  # ids[0]
        assert "$3" in sql  # ids[1]

    def test_bind_in_alias_works(self):
        sql, params = _render(
            "SELECT * FROM t WHERE id IN {{ ids | bind_in }}",
            {"ids": [5, 6]},
        )
        assert params == [5, 6]

    def test_empty_list_raises_value_error(self):
        with pytest.raises(ValueError, match="empty"):
            _render(
                "SELECT * FROM t WHERE id IN {{ ids | inclause }}",
                {"ids": []},
            )

    def test_injection_strings_in_inclause_are_bound(self):
        """Injection strings passed through inclause are each bound, not in SQL."""
        injections = ["' OR 1=1 --", "x'); DROP TABLE t;--"]
        sql, params = _render(
            "SELECT * FROM t WHERE name IN {{ names | inclause }}",
            {"names": injections},
        )
        for inj in injections:
            assert inj not in sql
        assert params == injections


# ===========================================================================
# (8) sqlsafe filter (escape hatch)
# ===========================================================================


class TestSqlsafeFilter:
    def test_trusted_value_emitted_raw(self):
        sql, params = _render(
            "SELECT {{ col | sqlsafe }} FROM t",
            {"col": "revenue"},
        )
        assert "revenue" in sql
        assert params == []

    def test_sqlsafe_does_not_bind(self):
        sql, params = _render(
            "SELECT {{ col | sqlsafe }}, {{ val }} FROM t",
            {"col": "id", "val": 99},
        )
        assert "id" in sql
        assert "$1" in sql
        assert params == [99]  # only val is bound

    def test_sqlsafe_documented_as_dangerous(self):
        """Confirm that a malicious string passed through sqlsafe appears raw in SQL.

        This is expected and documented behaviour — callers must ONLY pass
        trusted server-controlled values through sqlsafe.
        """
        # We deliberately put a 'dangerous' string through sqlsafe to show it's raw.
        dangerous = "'; DROP TABLE users; --"
        sql, params = _render(
            "SELECT {{ col | sqlsafe }} FROM t",
            {"col": dangerous},
        )
        # The dangerous string IS in the SQL — this is why sqlsafe is for
        # trusted content only.
        assert dangerous in sql
        assert params == []


# ===========================================================================
# (9) Sandbox security
# ===========================================================================


class TestSandboxSecurity:
    def test_class_attribute_blocked(self):
        """{{ ''.__class__ }} must be blocked by the sandbox."""
        with pytest.raises(Exception):
            _render("SELECT {{ x.__class__ }} FROM t", {"x": ""})

    def test_globals_access_blocked(self):
        """{{ x.__globals__ }} must be blocked."""
        with pytest.raises(Exception):
            _render("SELECT {{ x.__globals__ }} FROM t", {"x": lambda: None})

    def test_builtins_access_blocked(self):
        """{{ ''.__class__.__mro__[-1].__subclasses__() }} pattern is blocked."""
        with pytest.raises(Exception):
            _render(
                "SELECT {{ x.__class__.__mro__ }} FROM t",
                {"x": ""},
            )

    def test_import_blocked(self):
        """{% import %} and similar are blocked by the sandbox."""
        with pytest.raises(Exception):
            _render(
                "{% set x = __import__('os') %}SELECT {{ x.getcwd() | sqlsafe }} FROM t",
                {},
            )

    def test_arbitrary_python_call_via_attr_blocked(self):
        """Chained attribute dereference that reaches Python internals is blocked."""
        with pytest.raises(Exception):
            _render("{{ ().__class__.__bases__[0].__subclasses__() }}", {})


# ===========================================================================
# (10) Missing context key
# ===========================================================================


class TestMissingKey:
    def test_missing_var_raises_key_error_from_planner(self):
        """resolve_named_params raises KeyError for undefined names."""
        with pytest.raises(KeyError):
            _resolve("SELECT {{missing}} FROM t", {})

    def test_missing_var_raises_undefined_error_from_template(self):
        """render_sql_template raises UndefinedError for undefined names."""
        with pytest.raises(jinja2.UndefinedError):
            _render("SELECT {{ missing }} FROM t", {})

    def test_partially_missing(self):
        with pytest.raises((KeyError, jinja2.UndefinedError)):
            _render("SELECT {{ a }}, {{ b }} FROM t", {"a": 1})


# ===========================================================================
# (11–14) SQL injection via named params — all must be safe
# ===========================================================================


class TestSQLInjectionSafety:
    def test_or_1_equals_1_injection_is_bound(self):
        """Classic OR injection → value appears in params, NOT in SQL text."""
        injection = "' OR 1=1 --"
        sql, params = _render(
            "SELECT * FROM t WHERE name = {{ name }}",
            {"name": injection},
        )
        assert injection not in sql, f"SECURITY: injection in SQL: {sql!r}"
        assert "$1" in sql
        assert params == [injection]

    def test_drop_table_injection_is_bound(self):
        injection = "x'); DROP TABLE demo;--"
        sql, params = _render(
            "SELECT * FROM t WHERE name = {{ name }}",
            {"name": injection},
        )
        assert injection not in sql
        assert "DROP TABLE" not in sql.upper()
        assert params == [injection]

    def test_union_injection_is_bound(self):
        injection = "alice' UNION SELECT 1,2,3 --"
        sql, params = _render(
            "SELECT * FROM t WHERE name = {{ name }}",
            {"name": injection},
        )
        assert "UNION" not in sql.upper()
        assert params == [injection]

    def test_multi_statement_injection_is_bound(self):
        injection = "alice; SELECT * FROM t; --"
        sql, params = _render(
            "SELECT * FROM t WHERE name = {{ name }}",
            {"name": injection},
        )
        assert "; SELECT" not in sql
        assert params == [injection]

    def test_injection_via_conditional_still_bound(self):
        """Even when injection is in a conditional branch, output is still bound."""
        injection = "' OR 1=1 --"
        tmpl = "SELECT * FROM t WHERE 1=1 {% if region %} AND region = {{ region }} {% endif %}"
        sql, params = _render(tmpl, {"region": injection})
        assert injection not in sql
        assert "$1" in sql
        assert params == [injection]

    def test_integer_injection_attempt(self):
        """Integer-looking injection (e.g. "1 OR 1=1") bound as string."""
        injection = "1 OR 1=1"
        sql, params = _render(
            "SELECT * FROM t WHERE id = {{ id }}",
            {"id": injection},
        )
        assert "OR" not in sql
        assert params == [injection]

    def test_template_control_flow_cannot_inject(self):
        """A for loop over user-supplied items still binds each element."""
        items = ["alice' OR '1'='1", "'; DROP TABLE t; --"]
        tmpl = "SELECT * FROM t WHERE name IN ({% for v in items %}{{ v }}{% if not loop.last %},{% endif %}{% endfor %})"
        sql, params = _render(tmpl, {"items": items})
        for item in items:
            assert item not in sql
        assert params == items


# ===========================================================================
# (15–16) Conditional + injection / inclause + injection
# ===========================================================================


class TestConditionalAndIncluaseInjection:
    def test_if_with_injection_value_binds_safely(self):
        injection = "'; DELETE FROM users; --"
        tmpl = "SELECT * FROM t WHERE 1=1 {% if v %} AND col = {{ v }} {% endif %}"
        sql, params = _render(tmpl, {"v": injection})
        assert injection not in sql
        assert params == [injection]

    def test_inclause_injection_strings_all_bound(self):
        injections = ["' OR 1=1", "'; DROP TABLE t;--", "1 UNION SELECT 1"]
        sql, params = _render(
            "SELECT * FROM t WHERE id IN {{ vals | inclause }}",
            {"vals": injections},
        )
        for inj in injections:
            assert inj not in sql
        assert params == injections


# ===========================================================================
# (17) Dialect placeholder mapping
# ===========================================================================


class TestDialectPlaceholders:
    def test_postgres_uses_dollar_n(self):
        sql, params = _render("SELECT {{ x }}, {{ y }} FROM t", {"x": 1, "y": 2}, dialect="postgres")
        assert "$1" in sql
        assert "$2" in sql
        assert params == [1, 2]

    def test_duckdb_uses_dollar_n(self):
        sql, params = _render("SELECT {{ x }} FROM t", {"x": 99}, dialect="duckdb")
        assert "$1" in sql
        assert params == [99]

    def test_mysql_uses_percent_s(self):
        sql, params = _render("SELECT {{ x }}, {{ y }} FROM t", {"x": 1, "y": 2}, dialect="mysql")
        assert sql.count("%s") == 2
        assert params == [1, 2]

    def test_sqlite_uses_qmark(self):
        sql, params = _render("SELECT {{ x }} FROM t", {"x": 5}, dialect="sqlite")
        assert "?" in sql
        assert params == [5]

    def test_unknown_dialect_uses_qmark(self):
        sql, params = _render("SELECT {{ x }} FROM t", {"x": 5}, dialect="bigquery")
        assert "?" in sql
        assert params == [5]

    def test_inclause_respects_dialect_mysql(self):
        sql, params = _render(
            "SELECT * FROM t WHERE id IN {{ ids | inclause }}",
            {"ids": [1, 2, 3]},
            dialect="mysql",
        )
        assert "(%s, %s, %s)" in sql
        assert params == [1, 2, 3]


# ===========================================================================
# (18) Complex template: nested if/for with multiple bindings
# ===========================================================================


class TestComplexTemplate:
    def test_full_dynamic_query(self):
        """Simulate a real-world parameterised reporting query."""
        tmpl = """
SELECT
    region,
    SUM(revenue) AS total
FROM sales
WHERE 1=1
{% if start_date %} AND sale_date >= {{ start_date }} {% endif %}
{% if end_date %} AND sale_date <= {{ end_date }} {% endif %}
{% if regions %}
AND region IN {{ regions | inclause }}
{% endif %}
GROUP BY region
ORDER BY total DESC
{% if limit %} LIMIT {{ limit }} {% endif %}
        """.strip()

        sql, params = _render(
            tmpl,
            {
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "regions": ["us-east", "eu-west"],
                "limit": 10,
            },
        )

        assert "2024-01-01" not in sql
        assert "2024-12-31" not in sql
        assert "us-east" not in sql
        assert "eu-west" not in sql
        assert "10" not in sql
        assert params == ["2024-01-01", "2024-12-31", "us-east", "eu-west", 10]

    def test_optional_filters_all_absent(self):
        tmpl = """
SELECT * FROM sales WHERE 1=1
{% if start_date %} AND sale_date >= {{ start_date }} {% endif %}
{% if regions %} AND region IN {{ regions | inclause }} {% endif %}
        """.strip()

        sql, params = _render(
            tmpl,
            {"start_date": None, "regions": None},
        )
        assert "AND sale_date" not in sql
        assert "AND region" not in sql
        assert params == []


# ===========================================================================
# (19) Jinja2 filters on values that are still bound
# ===========================================================================


class TestFiltersOnBoundValues:
    def test_default_filter_applied(self):
        """{{ x | default('fallback') }} — fallback is bound if x is undefined."""
        sql, params = _render(
            "SELECT * FROM t WHERE name = {{ x | default('fallback') }}",
            {},  # x not in context → default kicks in
        )
        assert "fallback" not in sql
        assert params == ["fallback"]

    def test_upper_filter_applied_before_binding(self):
        """{{ x | upper }} — value is uppercased, then the uppercase str is bound."""
        sql, params = _render(
            "SELECT * FROM t WHERE code = {{ code | upper }}",
            {"code": "abc"},
        )
        assert "abc" not in sql
        assert "ABC" not in sql
        # The bound value is the uppercased string
        assert params == ["ABC"]


# ===========================================================================
# (20) Empty inclause raises ValueError
# ===========================================================================


def test_empty_inclause_raises_value_error():
    with pytest.raises(ValueError, match="empty"):
        _render("SELECT * FROM t WHERE id IN {{ ids | inclause }}", {"ids": []})


# ===========================================================================
# (21) Module exports
# ===========================================================================


def test_render_sql_template_importable():
    from app.connectors.template import render_sql_template
    assert callable(render_sql_template)


def test_render_sql_template_returns_tuple():
    from app.connectors.template import render_sql_template
    result = render_sql_template("SELECT 1", {})
    assert isinstance(result, tuple)
    assert len(result) == 2
    sql, params = result
    assert isinstance(sql, str)
    assert isinstance(params, list)


# ===========================================================================
# (22) resolve_named_params integration: planner delegates to template engine
# ===========================================================================


class TestResolvePlannerIntegration:
    def test_basic_binding(self):
        sql, params = _resolve("SELECT * FROM t WHERE id = {{id}}", {"id": 5})
        assert sql == "SELECT * FROM t WHERE id = $1"
        assert params == [5]

    def test_conditional_template_via_planner(self):
        """resolve_named_params supports conditional templates."""
        tmpl = "SELECT * FROM t WHERE 1=1 {% if region %} AND region = {{ region }} {% endif %}"
        sql_with, params_with = _resolve(tmpl, {"region": "eu"})
        assert "AND region" in sql_with
        assert params_with == ["eu"]

        sql_without, params_without = _resolve(tmpl, {"region": None})
        assert "AND region" not in sql_without
        assert params_without == []

    def test_inclause_via_planner(self):
        sql, params = _resolve(
            "SELECT * FROM t WHERE id IN {{ ids | inclause }}",
            {"ids": [1, 2, 3]},
        )
        assert "($1, $2, $3)" in sql
        assert params == [1, 2, 3]

    def test_missing_key_raises_key_error(self):
        with pytest.raises(KeyError):
            _resolve("SELECT {{missing}} FROM t", {})

    def test_injection_safe_via_planner(self):
        injection = "' OR 1=1 --"
        sql, params = _resolve("SELECT * FROM t WHERE x = {{x}}", {"x": injection})
        assert injection not in sql
        assert params == [injection]
