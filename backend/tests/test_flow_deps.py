"""Unit tests for app.flows.deps — inferred SQL dependencies (SQLMesh-style).

Covers:
- referenced_table_names: sibling FROM/JOIN extraction, CTE exclusion,
  parse-failure → empty set, subquery refs.
- effective_needs: sibling match union with explicit needs, deterministic order,
  CTE exclusion, parse-failure → explicit-only, non-sibling ignored, query_id
  (no sql) → no inferred refs, self-reference excluded, non-query kind → explicit
  only.
"""

from __future__ import annotations

from app.flows.deps import effective_needs, referenced_table_names


# ---------------------------------------------------------------------------
# referenced_table_names
# ---------------------------------------------------------------------------


class TestReferencedTableNames:
    def test_simple_from(self):
        assert referenced_table_names("SELECT * FROM first") == {"first"}

    def test_join(self):
        refs = referenced_table_names("SELECT * FROM a JOIN b ON a.id = b.id")
        assert refs == {"a", "b"}

    def test_qualified_name_uses_base(self):
        # schema-qualified — base name only.
        assert referenced_table_names("SELECT * FROM analytics.sales") == {"sales"}

    def test_cte_alias_excluded(self):
        sql = "WITH t AS (SELECT * FROM first) SELECT * FROM t"
        refs = referenced_table_names(sql)
        assert "t" not in refs
        assert refs == {"first"}

    def test_subquery_ref(self):
        sql = "SELECT * FROM (SELECT * FROM first) AS sub"
        assert referenced_table_names(sql) == {"first"}

    def test_parse_failure_returns_empty(self):
        # Not valid SQL — best-effort returns empty, never raises.
        assert referenced_table_names("this is not sql ;;;(") == set()

    def test_empty_input(self):
        assert referenced_table_names("") == set()
        assert referenced_table_names("   ") == set()


# ---------------------------------------------------------------------------
# effective_needs
# ---------------------------------------------------------------------------


class TestEffectiveNeeds:
    def test_sibling_match_inferred(self):
        task = {"key": "second", "kind": "query", "needs": [], "config": {"sql": "SELECT * FROM first"}}
        assert effective_needs(task, {"first", "second"}) == ["first"]

    def test_union_explicit_then_inferred_sorted(self):
        task = {
            "key": "third",
            "kind": "query",
            "needs": ["manual_dep"],
            "config": {"sql": "SELECT * FROM b JOIN a ON a.id = b.id"},
        }
        keys = {"a", "b", "manual_dep", "third"}
        # explicit first (original order), then inferred sorted: a, b
        assert effective_needs(task, keys) == ["manual_dep", "a", "b"]

    def test_non_sibling_table_ignored(self):
        # `demo` is a real warehouse table, not a task key — ignored.
        task = {"key": "q", "kind": "query", "needs": [], "config": {"sql": "SELECT * FROM demo"}}
        assert effective_needs(task, {"q", "other"}) == []

    def test_cte_excluded(self):
        task = {
            "key": "q",
            "kind": "query",
            "needs": [],
            "config": {"sql": "WITH t AS (SELECT * FROM first) SELECT * FROM t"},
        }
        assert effective_needs(task, {"q", "first", "t"}) == ["first"]

    def test_parse_failure_falls_back_to_explicit(self):
        task = {
            "key": "q",
            "kind": "query",
            "needs": ["explicit"],
            "config": {"sql": "garbage ;;("},
        }
        assert effective_needs(task, {"q", "explicit"}) == ["explicit"]

    def test_query_id_contributes_nothing(self):
        task = {"key": "q", "kind": "query", "needs": [], "config": {"query_id": "registered_q"}}
        assert effective_needs(task, {"q", "other"}) == []

    def test_self_reference_excluded(self):
        task = {"key": "loop", "kind": "query", "needs": [], "config": {"sql": "SELECT * FROM loop"}}
        assert effective_needs(task, {"loop"}) == []

    def test_explicit_dedup(self):
        task = {"key": "q", "kind": "query", "needs": ["a", "a", "b"], "config": {}}
        assert effective_needs(task, {"q", "a", "b"}) == ["a", "b"]

    def test_non_query_kind_explicit_only(self):
        # A python cell whose code happens to mention a sibling name gets no
        # inferred refs — only query tasks parse SQL.
        task = {"key": "p", "kind": "python", "needs": ["a"], "config": {"code": "result = 1  # FROM b"}}
        assert effective_needs(task, {"p", "a", "b"}) == ["a"]

    def test_explicit_and_inferred_same_key_not_duplicated(self):
        task = {"key": "q", "kind": "query", "needs": ["first"], "config": {"sql": "SELECT * FROM first"}}
        assert effective_needs(task, {"q", "first"}) == ["first"]
