"""Tests for the nightly watch-sweep job kind.

Coverage
--------
1.  run_watch_sweep over multiple watches emits WATCH_BREACH only for breached ones.
2.  Disabled watches are skipped (not evaluated, not emitted).
3.  Cross-org isolation — sweep for org A never touches org B's watches.
4.  One watch erroring does NOT abort the sweep (remaining watches still run).
5.  execute_watch_sweep_sync (the sync executor bridge) returns (breached_count, message).
6.  execute_job dispatches watch_sweep correctly end-to-end.
7.  HTTP POST /jobs with kind='watch_sweep' creates and runs the job via the route.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.watch import Watch
from app.auth.jwt import mint_access_token
from app.jobs.executor import execute_job
from app.jobs.store import InMemoryJobStore, set_job_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(hour: int = 2) -> datetime:
    return datetime(2025, 6, 1, hour, 0, 0, tzinfo=timezone.utc)


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_watch_record(
    watch_id: str,
    org_id: str,
    *,
    threshold_value: float = 10.0,
    enabled: bool = True,
) -> dict[str, Any]:
    """Build a minimal in-process watch record (no DB persist needed)."""
    return {
        "id": watch_id,
        "org_id": org_id,
        "name": f"Watch-{watch_id[:8]}",
        "metric_id": "demo_revenue",
        "config": {
            "dimensions": ["name"],
            "threshold": {"op": ">", "value": threshold_value},
            "enabled": enabled,
        },
    }


def _seed_watches(records: list[dict[str, Any]]) -> None:
    """Insert records directly into the in-process watch registry."""
    from app.routes.watches import _registry_put

    for r in records:
        _registry_put(r)


def _clear_watches() -> None:
    from app.routes.watches import reset_for_tests

    reset_for_tests()


# ---------------------------------------------------------------------------
# 1. Sweep emits WATCH_BREACH only for breached watches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_breach_only_for_breached(app, fake_db):
    """run_watch_sweep evaluates all org watches, emits breach only for breached ones."""
    from app.jobs.watch_sweep import run_watch_sweep

    org_id = str(uuid.uuid4())
    watch_breach_id = str(uuid.uuid4())
    watch_calm_id = str(uuid.uuid4())

    # demo_revenue total = 16.5  → > 10 breaches, > 100 does not.
    _seed_watches([
        _make_watch_record(watch_breach_id, org_id, threshold_value=10.0),  # breaches
        _make_watch_record(watch_calm_id, org_id, threshold_value=100.0),    # does not
    ])

    emitted: list[dict[str, Any]] = []

    def fake_emit(o_id, *, watch_id, **kwargs):
        emitted.append({"org_id": o_id, "watch_id": watch_id, **kwargs})

    try:
        with patch("app.webhooks.events.emit_watch_breach", side_effect=fake_emit):
            summary = await run_watch_sweep(org_id, _utc())
    finally:
        _clear_watches()

    assert summary["evaluated"] == 2
    assert summary["breached"] == 1
    assert summary["errors"] == 0

    breached_ids = [w["id"] for w in summary["watches"] if w.get("breached")]
    calm_ids = [w["id"] for w in summary["watches"] if not w.get("breached")]
    assert watch_breach_id in breached_ids
    assert watch_calm_id in calm_ids

    # emit_watch_breach was called exactly once (for the breaching watch).
    assert len(emitted) == 1
    assert emitted[0]["watch_id"] == watch_breach_id


# ---------------------------------------------------------------------------
# 2. Disabled watches are skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_skips_disabled_watches(app, fake_db):
    """Disabled watches (config.enabled=False) are not evaluated."""
    from app.jobs.watch_sweep import run_watch_sweep

    org_id = str(uuid.uuid4())
    enabled_id = str(uuid.uuid4())
    disabled_id = str(uuid.uuid4())

    _seed_watches([
        _make_watch_record(enabled_id, org_id, threshold_value=10.0, enabled=True),
        _make_watch_record(disabled_id, org_id, threshold_value=10.0, enabled=False),
    ])

    try:
        summary = await run_watch_sweep(org_id, _utc())
    finally:
        _clear_watches()

    # Only the enabled watch should appear in evaluated results.
    evaluated_ids = [w["id"] for w in summary["watches"]]
    assert enabled_id in evaluated_ids
    assert disabled_id not in evaluated_ids
    assert summary["evaluated"] == 1


# ---------------------------------------------------------------------------
# 3. Cross-org isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_cross_org_isolation(app, fake_db):
    """Sweep for org A never evaluates org B's watches."""
    from app.jobs.watch_sweep import run_watch_sweep

    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    watch_a_id = str(uuid.uuid4())
    watch_b_id = str(uuid.uuid4())

    _seed_watches([
        _make_watch_record(watch_a_id, org_a, threshold_value=10.0),
        _make_watch_record(watch_b_id, org_b, threshold_value=10.0),
    ])

    try:
        summary_a = await run_watch_sweep(org_a, _utc())
        summary_b = await run_watch_sweep(org_b, _utc())
    finally:
        _clear_watches()

    a_ids = [w["id"] for w in summary_a["watches"]]
    b_ids = [w["id"] for w in summary_b["watches"]]

    assert watch_a_id in a_ids
    assert watch_b_id not in a_ids
    assert watch_b_id in b_ids
    assert watch_a_id not in b_ids


# ---------------------------------------------------------------------------
# 4. One watch erroring does NOT abort the sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_error_in_one_watch_does_not_abort(app, fake_db):
    """A failing watch (e.g. metric not found) is recorded as error; sweep continues."""
    from app.jobs.watch_sweep import run_watch_sweep

    org_id = str(uuid.uuid4())
    bad_watch_id = str(uuid.uuid4())
    good_watch_id = str(uuid.uuid4())

    _seed_watches([
        {
            "id": bad_watch_id,
            "org_id": org_id,
            "name": "Bad Watch",
            "metric_id": "nonexistent_metric_xyz",
            "config": {
                "threshold": {"op": ">", "value": 1},
                "enabled": True,
            },
        },
        _make_watch_record(good_watch_id, org_id, threshold_value=10.0),
    ])

    try:
        summary = await run_watch_sweep(org_id, _utc())
    finally:
        _clear_watches()

    # Both were attempted.
    assert summary["evaluated"] == 2
    # The bad one is an error.
    assert summary["errors"] == 1
    # The good one still evaluated (breached since 16.5 > 10).
    assert summary["breached"] == 1

    states = {w["id"]: w.get("state", "ok" if not w.get("error") else "error")
              for w in summary["watches"]}
    assert states.get(bad_watch_id) == "error"
    assert states.get(good_watch_id) in ("ok", "breached")


# ---------------------------------------------------------------------------
# 5. execute_watch_sweep_sync returns (breached_count, message)
# ---------------------------------------------------------------------------


def test_execute_watch_sweep_sync_returns_count_and_message(app, fake_db):
    """The sync bridge returns (int, str) and the message contains key metadata."""
    from app.jobs.watch_sweep import execute_watch_sweep_sync

    org_id = str(uuid.uuid4())
    watch_id = str(uuid.uuid4())

    _seed_watches([
        _make_watch_record(watch_id, org_id, threshold_value=10.0),  # will breach
    ])

    job = {
        "id": str(uuid.uuid4()),
        "org_id": org_id,
        "kind": "watch_sweep",
        "target": "",
    }

    try:
        breached_count, message = execute_watch_sweep_sync(job, _utc())
    finally:
        _clear_watches()

    assert isinstance(breached_count, int)
    assert breached_count == 1
    assert "watch sweep" in message.lower()
    assert "breached=1" in message


# ---------------------------------------------------------------------------
# 6. execute_job dispatches watch_sweep end-to-end
# ---------------------------------------------------------------------------


def test_execute_job_watch_sweep_end_to_end(app, fake_db):
    """execute_job with kind='watch_sweep' succeeds and returns a run dict."""
    org_id = str(uuid.uuid4())
    watch_id = str(uuid.uuid4())

    _seed_watches([
        _make_watch_record(watch_id, org_id, threshold_value=10.0),
    ])

    job = {
        "id": str(uuid.uuid4()),
        "org_id": org_id,
        "kind": "watch_sweep",
        "target": "",
    }

    try:
        run = execute_job(job, now=_utc())
    finally:
        _clear_watches()

    assert run["status"] == "success", f"Expected success, got: {run['message']}"
    assert isinstance(run["row_count"], int)  # row_count == breached_count
    assert run["row_count"] >= 0
    assert "watch sweep" in run["message"].lower()


# ---------------------------------------------------------------------------
# 7. HTTP route: POST /jobs with kind='watch_sweep' creates the job
# ---------------------------------------------------------------------------


class _AsyncJobStoreDouble:
    """Thin async wrapper around InMemoryJobStore for the route layer."""

    def __init__(self) -> None:
        self._inner = InMemoryJobStore()

    async def create_job(self, **kwargs):
        return self._inner.create_job(**kwargs)

    async def get_job(self, job_id):
        return self._inner.get_job(job_id)

    async def list_jobs(self, org_id):
        return self._inner.list_jobs(org_id)

    async def update_job(self, job_id, fields):
        return self._inner.update_job(job_id, fields)

    async def delete_job(self, job_id):
        return self._inner.delete_job(job_id)

    async def add_run(self, job_id, run):
        return self._inner.add_run(job_id, run)

    async def list_runs(self, job_id):
        return self._inner.list_runs(job_id)


@pytest_asyncio.fixture
async def sweep_client(app, fake_db):
    """Client pre-seeded with a user+org for watch-sweep job tests."""
    from app.routes.watches import reset_for_tests as _reset_watches

    store = _AsyncJobStoreDouble()
    set_job_store(store)
    repo = InMemoryRepo()
    set_repo(repo)
    _reset_watches()

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "sweeper@example.com",
        "name": "Sweeper",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        yield client, user_id, org_id, store

    set_job_store(None)
    set_repo(None)
    _reset_watches()


@pytest.mark.asyncio
async def test_create_watch_sweep_job_via_route(sweep_client):
    """POST /jobs with kind='watch_sweep' returns 201 with the correct kind."""
    client, user_id, org_id, store = sweep_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "Nightly Watch Sweep",
            "kind": "watch_sweep",
            "target": "",
            "schedule": "0 2 * * *",  # 02:00 UTC every night
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "watch_sweep"
    assert body["name"] == "Nightly Watch Sweep"
    assert body["org_id"] == org_id
    assert body["next_run_at"] is not None


@pytest.mark.asyncio
async def test_run_now_watch_sweep_via_route(sweep_client, fake_db):
    """POST /jobs/{id}/run with kind='watch_sweep' executes and returns a run dict."""
    client, user_id, org_id, store = sweep_client
    headers = _auth_headers(user_id)

    # Create the job.
    create_resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "On-demand Sweep",
            "kind": "watch_sweep",
            "target": "",
            "schedule": "0 2 * * *",
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    job_id = create_resp.json()["id"]

    # Run it immediately (no watches seeded → evaluated=0, errors=0).
    run_resp = await client.post(f"/api/v1/jobs/{job_id}/run", headers=headers)
    assert run_resp.status_code == 200, run_resp.text
    run = run_resp.json()
    assert run["status"] == "success"
    assert "watch sweep" in run["message"].lower()
    assert isinstance(run["row_count"], int)
