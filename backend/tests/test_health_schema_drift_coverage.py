"""Tests for app/health/schema_drift.py (was 23% coverage).

Coverage targets:
- InMemoryDriftStore: all methods (get_snapshot, upsert_snapshot, insert_events,
  list_events with/without dataset_key filter, reset)
- _diff_columns: added/removed/type_changed/unchanged cases, empty inputs
- detect_schema_drift: first observation, unchanged, changed (events+upsert),
  no-op on falsy org/dataset/None columns, webhook failure is swallowed
- Module-level singleton: get_drift_store, set_drift_store, reset_for_tests
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.health.schema_drift import (
    InMemoryDriftStore,
    _diff_columns,
    detect_schema_drift,
    get_drift_store,
    reset_for_tests,
    set_drift_store,
)


# ---------------------------------------------------------------------------
# InMemoryDriftStore
# ---------------------------------------------------------------------------

class TestInMemoryDriftStore:
    def setup_method(self):
        self.store = InMemoryDriftStore()

    def test_get_snapshot_returns_none_when_missing(self):
        assert self.store.get_snapshot("org-1", "raw/orders") is None

    def test_upsert_then_get_snapshot(self):
        cols = [{"name": "id", "type": "integer"}, {"name": "amount", "type": "numeric"}]
        self.store.upsert_snapshot("org-1", "raw/orders", cols)
        result = self.store.get_snapshot("org-1", "raw/orders")
        assert result == cols

    def test_upsert_overwrites_existing_snapshot(self):
        self.store.upsert_snapshot("org-1", "ds", [{"name": "a", "type": "int"}])
        self.store.upsert_snapshot("org-1", "ds", [{"name": "b", "type": "text"}])
        result = self.store.get_snapshot("org-1", "ds")
        assert result == [{"name": "b", "type": "text"}]

    def test_cross_org_isolation_for_snapshots(self):
        self.store.upsert_snapshot("org-A", "ds", [{"name": "x", "type": "int"}])
        assert self.store.get_snapshot("org-B", "ds") is None

    def test_insert_events_and_list_events_by_org(self):
        events = [
            {"org_id": "org-1", "dataset_key": "ds1", "change_type": "added",
             "column_name": "new_col", "from_type": None, "to_type": "text"},
        ]
        self.store.insert_events(events)
        result = self.store.list_events("org-1")
        assert len(result) == 1
        assert result[0]["change_type"] == "added"

    def test_list_events_filters_by_dataset_key(self):
        self.store.insert_events([
            {"org_id": "org-1", "dataset_key": "ds1", "change_type": "added",
             "column_name": "c1", "from_type": None, "to_type": "text"},
            {"org_id": "org-1", "dataset_key": "ds2", "change_type": "removed",
             "column_name": "c2", "from_type": "int", "to_type": None},
        ])
        result = self.store.list_events("org-1", dataset_key="ds1")
        assert len(result) == 1
        assert result[0]["column_name"] == "c1"

    def test_list_events_respects_limit(self):
        events = [
            {"org_id": "org-1", "dataset_key": "ds", "change_type": "added",
             "column_name": f"col_{i}", "from_type": None, "to_type": "text"}
            for i in range(10)
        ]
        self.store.insert_events(events)
        result = self.store.list_events("org-1", limit=3)
        assert len(result) == 3

    def test_list_events_cross_org_isolation(self):
        self.store.insert_events([
            {"org_id": "org-A", "dataset_key": "ds", "change_type": "added",
             "column_name": "c", "from_type": None, "to_type": "text"},
        ])
        result = self.store.list_events("org-B")
        assert result == []

    def test_reset_clears_all_state(self):
        self.store.upsert_snapshot("org-1", "ds", [{"name": "a", "type": "int"}])
        self.store.insert_events([
            {"org_id": "org-1", "dataset_key": "ds", "change_type": "added",
             "column_name": "a", "from_type": None, "to_type": "int"},
        ])
        self.store.reset()
        assert self.store.get_snapshot("org-1", "ds") is None
        assert self.store.list_events("org-1") == []


# ---------------------------------------------------------------------------
# _diff_columns
# ---------------------------------------------------------------------------

class TestDiffColumns:
    def test_empty_old_and_new_returns_no_events(self):
        assert _diff_columns([], []) == []

    def test_identical_columns_returns_no_events(self):
        cols = [{"name": "id", "type": "integer"}, {"name": "name", "type": "text"}]
        assert _diff_columns(cols, cols) == []

    def test_new_column_is_detected_as_added(self):
        old = [{"name": "id", "type": "integer"}]
        new = [{"name": "id", "type": "integer"}, {"name": "email", "type": "text"}]
        events = _diff_columns(old, new)
        assert len(events) == 1
        ev = events[0]
        assert ev["change_type"] == "added"
        assert ev["column_name"] == "email"
        assert ev["from_type"] is None
        assert ev["to_type"] == "text"

    def test_removed_column_is_detected(self):
        old = [{"name": "id", "type": "integer"}, {"name": "email", "type": "text"}]
        new = [{"name": "id", "type": "integer"}]
        events = _diff_columns(old, new)
        assert len(events) == 1
        ev = events[0]
        assert ev["change_type"] == "removed"
        assert ev["column_name"] == "email"
        assert ev["from_type"] == "text"
        assert ev["to_type"] is None

    def test_type_changed_column_is_detected(self):
        old = [{"name": "amount", "type": "integer"}]
        new = [{"name": "amount", "type": "numeric"}]
        events = _diff_columns(old, new)
        assert len(events) == 1
        ev = events[0]
        assert ev["change_type"] == "type_changed"
        assert ev["column_name"] == "amount"
        assert ev["from_type"] == "integer"
        assert ev["to_type"] == "numeric"

    def test_multiple_changes_detected_simultaneously(self):
        old = [
            {"name": "id", "type": "integer"},
            {"name": "old_col", "type": "text"},
            {"name": "amount", "type": "integer"},
        ]
        new = [
            {"name": "id", "type": "integer"},
            {"name": "new_col", "type": "boolean"},
            {"name": "amount", "type": "numeric"},
        ]
        events = _diff_columns(old, new)
        change_types = {e["change_type"] for e in events}
        assert "added" in change_types    # new_col
        assert "removed" in change_types  # old_col
        assert "type_changed" in change_types  # amount

    def test_first_snapshot_to_empty_detects_all_removed(self):
        old = [{"name": "a", "type": "int"}, {"name": "b", "type": "text"}]
        events = _diff_columns(old, [])
        assert all(e["change_type"] == "removed" for e in events)
        assert len(events) == 2

    def test_empty_to_new_detects_all_added(self):
        new = [{"name": "a", "type": "int"}, {"name": "b", "type": "text"}]
        events = _diff_columns([], new)
        assert all(e["change_type"] == "added" for e in events)
        assert len(events) == 2

    def test_same_type_not_flagged_as_changed(self):
        old = [{"name": "id", "type": "uuid"}]
        new = [{"name": "id", "type": "uuid"}]
        assert _diff_columns(old, new) == []


# ---------------------------------------------------------------------------
# detect_schema_drift
# ---------------------------------------------------------------------------

class TestDetectSchemaDrift:
    """End-to-end tests using InMemoryDriftStore."""

    def setup_method(self):
        self.store = InMemoryDriftStore()
        set_drift_store(self.store)

    def teardown_method(self):
        reset_for_tests()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_first_observation_stores_snapshot_no_events(self):
        cols = [{"name": "id", "type": "integer"}]
        self._run(detect_schema_drift("org-1", "ds1", cols))
        assert self.store.get_snapshot("org-1", "ds1") == cols
        assert self.store.list_events("org-1") == []

    def test_unchanged_columns_no_events_no_update(self):
        cols = [{"name": "id", "type": "integer"}]
        self._run(detect_schema_drift("org-1", "ds1", cols))
        # Second call with same columns
        self._run(detect_schema_drift("org-1", "ds1", cols))
        # Still no events and snapshot is unchanged
        assert self.store.list_events("org-1") == []
        assert self.store.get_snapshot("org-1", "ds1") == cols

    def test_added_column_records_event_and_updates_snapshot(self):
        cols_v1 = [{"name": "id", "type": "integer"}]
        cols_v2 = [{"name": "id", "type": "integer"}, {"name": "email", "type": "text"}]
        self._run(detect_schema_drift("org-1", "ds1", cols_v1))
        with patch("app.webhooks.events.emit_schema_drift"):
            self._run(detect_schema_drift("org-1", "ds1", cols_v2))
        events = self.store.list_events("org-1", dataset_key="ds1")
        assert len(events) == 1
        assert events[0]["change_type"] == "added"
        assert events[0]["column_name"] == "email"
        # Snapshot updated to v2
        snapshot = self.store.get_snapshot("org-1", "ds1")
        assert {"name": "email", "type": "text"} in snapshot

    def test_removed_column_records_event(self):
        cols_v1 = [{"name": "id", "type": "integer"}, {"name": "deprecated", "type": "text"}]
        cols_v2 = [{"name": "id", "type": "integer"}]
        self._run(detect_schema_drift("org-1", "ds1", cols_v1))
        with patch("app.webhooks.events.emit_schema_drift"):
            self._run(detect_schema_drift("org-1", "ds1", cols_v2))
        events = self.store.list_events("org-1", dataset_key="ds1")
        assert any(e["change_type"] == "removed" and e["column_name"] == "deprecated"
                   for e in events)

    def test_type_changed_column_records_event(self):
        cols_v1 = [{"name": "amount", "type": "integer"}]
        cols_v2 = [{"name": "amount", "type": "numeric"}]
        self._run(detect_schema_drift("org-1", "ds1", cols_v1))
        with patch("app.webhooks.events.emit_schema_drift"):
            self._run(detect_schema_drift("org-1", "ds1", cols_v2))
        events = self.store.list_events("org-1")
        assert any(e["change_type"] == "type_changed" for e in events)

    def test_falsy_org_is_no_op(self):
        self._run(detect_schema_drift("", "ds1", [{"name": "a", "type": "int"}]))
        self._run(detect_schema_drift(None, "ds1", [{"name": "a", "type": "int"}]))
        assert self.store.get_snapshot("", "ds1") is None

    def test_falsy_dataset_key_is_no_op(self):
        self._run(detect_schema_drift("org-1", "", [{"name": "a", "type": "int"}]))
        assert self.store.get_snapshot("org-1", "") is None

    def test_none_columns_is_no_op(self):
        self._run(detect_schema_drift("org-1", "ds1", None))
        assert self.store.get_snapshot("org-1", "ds1") is None

    def test_columns_normalised_to_name_and_type_only(self):
        """Extra keys in column dicts are stripped; comparison is on name+type only."""
        cols = [{"name": "id", "type": "integer", "nullable": True, "pk": False}]
        self._run(detect_schema_drift("org-1", "ds1", cols))
        snapshot = self.store.get_snapshot("org-1", "ds1")
        # stored snapshot should only have name + type
        assert snapshot == [{"name": "id", "type": "integer"}]

    def test_webhook_failure_does_not_propagate(self):
        """If the webhook emit fails, detect_schema_drift must still complete."""
        import app.webhooks.events as _wh_events
        cols_v1 = [{"name": "a", "type": "int"}]
        cols_v2 = [{"name": "a", "type": "int"}, {"name": "b", "type": "text"}]
        self._run(detect_schema_drift("org-1", "ds1", cols_v1))

        with patch.object(_wh_events, "emit_schema_drift", side_effect=RuntimeError("webhook down")):
            # Must not raise
            self._run(detect_schema_drift("org-1", "ds1", cols_v2))

        # Events still recorded despite webhook failure
        events = self.store.list_events("org-1")
        assert len(events) == 1

    def test_store_exception_is_swallowed(self):
        """detect_schema_drift never raises even when the store itself errors."""
        bad_store = InMemoryDriftStore()

        def _boom(*args, **kwargs):
            raise RuntimeError("store broken")

        bad_store.get_snapshot = _boom
        set_drift_store(bad_store)

        # Must NOT raise
        self._run(detect_schema_drift("org-1", "ds1", [{"name": "a", "type": "int"}]))


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def teardown_method(self):
        reset_for_tests()

    def test_reset_for_tests_resets_to_none(self):
        set_drift_store(InMemoryDriftStore())
        reset_for_tests()
        # After reset, get_drift_store should create a fresh PgDriftStore
        store = get_drift_store()
        assert store is not None

    def test_set_drift_store_replaces_singleton(self):
        mem = InMemoryDriftStore()
        set_drift_store(mem)
        assert get_drift_store() is mem

    def test_get_drift_store_creates_pg_store_when_none(self):
        reset_for_tests()
        store = get_drift_store()
        # Should be a PgDriftStore (the default)
        from app.health.schema_drift import PgDriftStore
        assert isinstance(store, PgDriftStore)
