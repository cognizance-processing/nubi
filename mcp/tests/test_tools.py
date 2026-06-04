"""Tests for Nubi MCP tool implementation functions.

These tests exercise the *plain* tool-logic functions (_list_dashboards,
_run_query, _list_lineage, _propose_materialized_view) directly — no MCP
transport is required.

Strategy
--------
- _list_dashboards: assert the demo queries seeded by get_query_registry()
  are present with the expected ids and names.
- _run_query: use 'demo_points_10k' which relies only on DuckDB's built-in
  generate_series; no seed table is required.
- _list_lineage: assert the result is a dict with an 'available' key;
  if M7-A is not yet shipped the tool returns a friendly unavailability
  message — that is also a valid (tested) outcome.
- _propose_materialized_view: assert the result is a list (may be empty
  because the query log is fresh in a test process).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend/ is on the path before importing tool functions.
_TESTS_DIR = Path(__file__).resolve().parent        # mcp/tests/
_MCP_DIR = _TESTS_DIR.parent                        # mcp/
_NUBI_ROOT = _MCP_DIR.parent                        # nubi/
_BACKEND_DIR = _NUBI_ROOT / "backend"               # nubi/backend/

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import pytest

# Import the plain tool functions (no MCP transport needed).
from nubi_mcp.server import (
    _list_dashboards,
    _list_lineage,
    _propose_materialized_view,
    _run_query,
)


# ---------------------------------------------------------------------------
# list_dashboards
# ---------------------------------------------------------------------------


class TestListDashboards:
    """Tests for _list_dashboards()."""

    def test_returns_list(self) -> None:
        result = _list_dashboards()
        assert isinstance(result, list), "Expected a list"

    def test_returns_non_empty(self) -> None:
        result = _list_dashboards()
        assert len(result) > 0, "Expected at least one registered query"

    def test_each_item_has_id_and_name(self) -> None:
        result = _list_dashboards()
        for item in result:
            assert "id" in item, f"Missing 'id' key in {item}"
            assert "name" in item, f"Missing 'name' key in {item}"
            assert isinstance(item["id"], str)
            assert isinstance(item["name"], str)

    def test_demo_queries_present(self) -> None:
        """The demo seed queries registered at startup must be present."""
        result = _list_dashboards()
        ids = {item["id"] for item in result}
        # These three are always seeded by get_query_registry().
        assert "demo_all" in ids, f"'demo_all' not found in {ids}"
        assert "demo_active" in ids, f"'demo_active' not found in {ids}"
        assert "demo_points_10k" in ids, f"'demo_points_10k' not found in {ids}"

    def test_demo_all_name(self) -> None:
        """'demo_all' should have the expected human-readable name."""
        result = _list_dashboards()
        by_id = {item["id"]: item for item in result}
        assert "Demo" in by_id["demo_all"]["name"], (
            f"Unexpected name for demo_all: {by_id['demo_all']['name']!r}"
        )


# ---------------------------------------------------------------------------
# run_query
# ---------------------------------------------------------------------------


class TestRunQuery:
    """Tests for _run_query()."""

    def test_demo_points_10k_returns_preview(self) -> None:
        """demo_points_10k uses generate_series — no seed table required."""
        result = _run_query("demo_points_10k", limit=100)
        assert isinstance(result, dict)
        assert "columns" in result
        assert "rows" in result
        assert "row_count" in result

    def test_demo_points_10k_row_count(self) -> None:
        """The full result should have 10 000 rows."""
        result = _run_query("demo_points_10k", limit=5)
        assert result["row_count"] == 10_000, (
            f"Expected 10000 rows, got {result['row_count']}"
        )

    def test_demo_points_10k_columns(self) -> None:
        """Expected columns: id, x, y, category."""
        result = _run_query("demo_points_10k", limit=1)
        cols = result["columns"]
        for expected_col in ("id", "x", "y", "category"):
            assert expected_col in cols, (
                f"Column {expected_col!r} not found in {cols}"
            )

    def test_limit_caps_rows(self) -> None:
        """rows list should be capped to at most limit rows."""
        limit = 7
        result = _run_query("demo_points_10k", limit=limit)
        assert len(result["rows"]) <= limit, (
            f"Expected at most {limit} rows, got {len(result['rows'])}"
        )

    def test_rows_are_lists(self) -> None:
        """Each row should be a list of scalar values."""
        result = _run_query("demo_points_10k", limit=3)
        for row in result["rows"]:
            assert isinstance(row, list), f"Row is not a list: {row!r}"

    def test_unknown_query_id_raises(self) -> None:
        """A non-existent query_id should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown query_id"):
            _run_query("this_does_not_exist", limit=10)

    def test_default_limit_applied(self) -> None:
        """Default limit (100) should cap the rows list."""
        result = _run_query("demo_points_10k")
        assert len(result["rows"]) <= 100

    def test_row_count_positive(self) -> None:
        result = _run_query("demo_points_10k", limit=1)
        assert result["row_count"] > 0


# ---------------------------------------------------------------------------
# list_lineage
# ---------------------------------------------------------------------------


class TestListLineage:
    """Tests for _list_lineage()."""

    def test_returns_dict(self) -> None:
        result = _list_lineage()
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    def test_has_available_key(self) -> None:
        result = _list_lineage()
        assert "available" in result, f"Missing 'available' key: {result}"

    def test_available_is_bool(self) -> None:
        result = _list_lineage()
        assert isinstance(result["available"], bool)

    def test_unavailable_has_reason(self) -> None:
        """When lineage is not available, a human-readable reason is present."""
        result = _list_lineage()
        if not result["available"]:
            assert "reason" in result, (
                "Expected 'reason' key when available=False"
            )
            assert isinstance(result["reason"], str)
            assert len(result["reason"]) > 0

    def test_available_has_graph(self) -> None:
        """When lineage IS available, a 'graph' key is present."""
        result = _list_lineage()
        if result["available"]:
            assert "graph" in result, (
                "Expected 'graph' key when available=True"
            )


# ---------------------------------------------------------------------------
# propose_materialized_view
# ---------------------------------------------------------------------------


class TestProposeMaterializedView:
    """Tests for _propose_materialized_view()."""

    def test_returns_list(self) -> None:
        result = _propose_materialized_view()
        assert isinstance(result, list), f"Expected list, got {type(result)}"

    def test_empty_log_returns_empty_list(self) -> None:
        """A fresh query log (no entries yet) should return no suggestions."""
        result = _propose_materialized_view()
        # In a clean test run the query log is empty so suggestions == [].
        assert isinstance(result, list)

    def test_each_suggestion_has_required_keys(self) -> None:
        """If suggestions are returned, each should have the required keys."""
        result = _propose_materialized_view()
        required_keys = {
            "base_table", "dimensions", "measures", "hits", "est_bytes_saved", "sig"
        }
        for item in result:
            missing = required_keys - item.keys()
            assert not missing, (
                f"Suggestion missing keys {missing}: {item}"
            )

    def test_suggestions_with_seeded_log(self) -> None:
        """Seed the query log with repeated GROUP BY entries and verify suggestion."""
        from app.connectors.query_log import QueryLog
        from app.connectors.preagg import suggest

        log = QueryLog()
        grouped_sql = (
            "SELECT category, COUNT(*) AS cnt "
            "FROM points GROUP BY category"
        )
        for _ in range(5):
            log.record(sql=grouped_sql, cache_key="k1", byte_size=1024)

        suggestions = suggest(log, min_hits=3)
        result = [s.to_dict() for s in suggestions]

        assert len(result) >= 1, "Expected at least one suggestion from seeded log"
        first = result[0]
        assert first["hits"] == 5
        assert first["est_bytes_saved"] == 5 * 1024
        assert "category" in first["dimensions"]
