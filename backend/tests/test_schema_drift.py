"""Tests for schema-drift detection.

Test plan
---------
1. First observation -> snapshot only, no events.
2. Added column -> 'added' event.
3. Removed column -> 'removed' event.
4. Type change -> 'type_changed' event.
5. No change -> no event.
6. Cross-org isolation: org A cannot read org B's drift.
7. SCHEMA_DRIFT webhook event fires on drift.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.health.schema_drift import (
    InMemoryDriftStore,
    _diff_columns,
    detect_schema_drift,
    get_drift_store,
    set_drift_store,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ORG_A = "aaaaaaaa-0000-0000-0000-000000000001"
ORG_B = "bbbbbbbb-0000-0000-0000-000000000002"
DATASET = "raw/orders"

COLS_V1 = [
    {"name": "id", "type": "int64"},
    {"name": "amount", "type": "float64"},
    {"name": "status", "type": "text"},
]

COLS_V2_ADDED = [
    {"name": "id", "type": "int64"},
    {"name": "amount", "type": "float64"},
    {"name": "status", "type": "text"},
    {"name": "created_at", "type": "timestamp"},  # new
]

COLS_V2_REMOVED = [
    {"name": "id", "type": "int64"},
    {"name": "amount", "type": "float64"},
    # status removed
]

COLS_V2_TYPE_CHANGED = [
    {"name": "id", "type": "int64"},
    {"name": "amount", "type": "text"},  # was float64
    {"name": "status", "type": "text"},
]


@pytest.fixture(autouse=True)
def use_memory_store():
    """Inject an InMemoryDriftStore for every test; reset after."""
    store = InMemoryDriftStore()
    set_drift_store(store)
    yield store
    set_drift_store(None)


# ---------------------------------------------------------------------------
# Unit tests for the pure diff function
# ---------------------------------------------------------------------------

def test_diff_empty_to_empty():
    assert _diff_columns([], []) == []


def test_diff_no_change():
    result = _diff_columns(COLS_V1, COLS_V1)
    assert result == []


def test_diff_added_column():
    result = _diff_columns(COLS_V1, COLS_V2_ADDED)
    assert len(result) == 1
    ev = result[0]
    assert ev["change_type"] == "added"
    assert ev["column_name"] == "created_at"
    assert ev["from_type"] is None
    assert ev["to_type"] == "timestamp"


def test_diff_removed_column():
    result = _diff_columns(COLS_V1, COLS_V2_REMOVED)
    assert len(result) == 1
    ev = result[0]
    assert ev["change_type"] == "removed"
    assert ev["column_name"] == "status"
    assert ev["from_type"] == "text"
    assert ev["to_type"] is None


def test_diff_type_changed():
    result = _diff_columns(COLS_V1, COLS_V2_TYPE_CHANGED)
    assert len(result) == 1
    ev = result[0]
    assert ev["change_type"] == "type_changed"
    assert ev["column_name"] == "amount"
    assert ev["from_type"] == "float64"
    assert ev["to_type"] == "text"


# ---------------------------------------------------------------------------
# Integration tests using InMemoryDriftStore
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# 1. First observation -> snapshot stored, no events
def test_first_observation_no_events(use_memory_store):
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))
    events = use_memory_store.list_events(ORG_A, DATASET)
    assert events == [], "First observation must produce zero drift events"
    snap = use_memory_store.get_snapshot(ORG_A, DATASET)
    assert snap is not None, "Snapshot should be stored after first observation"
    assert len(snap) == len(COLS_V1)


# 2. Added column -> 'added' event
def test_added_column_event(use_memory_store):
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V2_ADDED))
    events = use_memory_store.list_events(ORG_A, DATASET)
    assert len(events) == 1
    assert events[0]["change_type"] == "added"
    assert events[0]["column_name"] == "created_at"


# 3. Removed column -> 'removed' event
def test_removed_column_event(use_memory_store):
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V2_REMOVED))
    events = use_memory_store.list_events(ORG_A, DATASET)
    assert len(events) == 1
    assert events[0]["change_type"] == "removed"
    assert events[0]["column_name"] == "status"


# 4. Type change -> 'type_changed' event
def test_type_changed_event(use_memory_store):
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V2_TYPE_CHANGED))
    events = use_memory_store.list_events(ORG_A, DATASET)
    assert len(events) == 1
    assert events[0]["change_type"] == "type_changed"
    assert events[0]["column_name"] == "amount"
    assert events[0]["from_type"] == "float64"
    assert events[0]["to_type"] == "text"


# 5. No change -> no event, snapshot stays the same
def test_no_change_no_event(use_memory_store):
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))
    events_before = use_memory_store.list_events(ORG_A, DATASET)
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))
    events_after = use_memory_store.list_events(ORG_A, DATASET)
    assert len(events_before) == 0
    assert len(events_after) == 0


# 6. Cross-org isolation: org A events not visible to org B
def test_cross_org_isolation(use_memory_store):
    # Give org A a baseline + drift
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))
    _run(detect_schema_drift(ORG_A, DATASET, COLS_V2_ADDED))

    # Org B has no data at all
    events_b = use_memory_store.list_events(ORG_B, DATASET)
    assert events_b == [], "Org B must see zero events despite org A having drift"

    snap_b = use_memory_store.get_snapshot(ORG_B, DATASET)
    assert snap_b is None, "Org B must see no snapshot for org A's dataset"


# 7. SCHEMA_DRIFT webhook fires on drift
def test_schema_drift_webhook_emitted(use_memory_store):
    emitted = []

    def fake_emit_schema_drift(org_id, *, dataset_key, changes):
        emitted.append({"org_id": org_id, "dataset_key": dataset_key, "changes": changes})

    with patch("app.webhooks.events.emit_schema_drift", side_effect=fake_emit_schema_drift):
        _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))   # first obs — no webhook
        assert emitted == []

        _run(detect_schema_drift(ORG_A, DATASET, COLS_V2_ADDED))  # drift — webhook fires
        assert len(emitted) == 1
        payload = emitted[0]
        assert payload["org_id"] == ORG_A
        assert payload["dataset_key"] == DATASET
        assert len(payload["changes"]) == 1
        assert payload["changes"][0]["change_type"] == "added"


# 7b. No webhook on first observation
def test_no_webhook_on_first_observation(use_memory_store):
    emitted = []

    def fake_emit(org_id, *, dataset_key, changes):
        emitted.append({"org_id": org_id, "changes": changes})

    with patch("app.webhooks.events.emit_schema_drift", side_effect=fake_emit):
        _run(detect_schema_drift(ORG_A, DATASET, COLS_V1))
        assert emitted == [], "No webhook should fire on the first (baseline) observation"


# ---------------------------------------------------------------------------
# Route-level tests (httpx test client)
# ---------------------------------------------------------------------------

def _make_app():
    """Return a test FastAPI app with drift routes and in-memory auth bypass."""
    from fastapi import FastAPI
    from app.routes import api_router  # noqa: PLC0415
    from app.errors import register_handlers  # noqa: PLC0415
    # Import the drift router (self-registers on api_router)
    import app.routes.drift  # noqa: F401  # pylint: disable=unused-import

    test_app = FastAPI()
    register_handlers(test_app)
    test_app.include_router(api_router, prefix="/api/v1")
    return test_app


def test_drift_api_returns_empty_list(use_memory_store):
    """GET /health/drift returns an empty event list when no drift has occurred."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from app.auth.deps import verified_identity  # noqa: PLC0415
    from app.auth.verify import VerifiedIdentity  # noqa: PLC0415
    from app.routes._org import get_user_org  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    app = _make_app()

    # Stub verified_identity + get_user_org so we don't need a real DB/token.
    fake_identity = VerifiedIdentity(
        user_id="u1", kind="access", org=ORG_A, scope=["read:*"],
        policies={}, datastore=None, embed_origin=None, raw_claims={},
        project=None, roles=[],
    )
    app.dependency_overrides[verified_identity] = lambda: fake_identity

    with patch("app.routes.drift.get_user_org", return_value=ORG_A):
        client = TestClient(app)
        resp = client.get("/api/v1/health/drift")
        assert resp.status_code == 200
        body = resp.json()
        assert body["org_id"] == ORG_A
        assert body["events"] == []


def test_drift_dataset_404_when_no_snapshot(use_memory_store):
    """GET /health/drift/{key} returns 404 when the dataset has never been observed."""
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from app.auth.deps import verified_identity  # noqa: PLC0415
    from app.auth.verify import VerifiedIdentity  # noqa: PLC0415

    app = _make_app()
    fake_identity = VerifiedIdentity(
        user_id="u1", kind="access", org=ORG_A, scope=["read:*"],
        policies={}, datastore=None, embed_origin=None, raw_claims={},
        project=None, roles=[],
    )
    app.dependency_overrides[verified_identity] = lambda: fake_identity

    with patch("app.routes.drift.get_user_org", return_value=ORG_A):
        client = TestClient(app)
        resp = client.get(f"/api/v1/health/drift/{DATASET}")
        assert resp.status_code == 404
