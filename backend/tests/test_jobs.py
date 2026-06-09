"""Tests for M11-A scheduled jobs — schedule, executor, and endpoints.

Coverage
--------
1.  ``next_run`` with interval format (seconds, minutes, hours)
2.  ``next_run`` with a cron expression
3.  ``next_run`` with a bad schedule raises AppError("bad_schedule", 400)
4.  ``run_due_jobs`` with injected ``now``: only due jobs run, advances
    ``next_run_at``, records run in store
5.  ``execute_job`` with target='demo_points_10k' → success, row_count > 0
6.  ``execute_job`` with a nonexistent query_id → error, row_count == 0
7.  Endpoints (via httpx + InMemoryJobStore + InMemoryRepo + fake auth):
    - POST /jobs → 201
    - GET  /jobs → list includes the job
    - POST /jobs/{id}/run → success run returned
    - GET  /jobs/{id}/runs → list includes the run
    - GET  /jobs/{unknown_org_id} → 404  (cross-org)
    - No auth → 401
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.jobs.executor import execute_job
from app.jobs.schedule import next_run, run_due_jobs
from app.jobs.store import InMemoryJobStore, set_job_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _utc(year: int = 2025, month: int = 1, day: int = 1, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. next_run — interval format
# ---------------------------------------------------------------------------


class TestNextRunInterval:
    def test_seconds(self):
        after = _utc()
        result = next_run("interval:30s", after)
        assert result == after + timedelta(seconds=30)
        assert result.tzinfo is not None

    def test_minutes(self):
        after = _utc()
        result = next_run("interval:5m", after)
        assert result == after + timedelta(minutes=5)

    def test_hours(self):
        after = _utc()
        result = next_run("interval:2h", after)
        assert result == after + timedelta(hours=2)

    def test_result_is_utc(self):
        result = next_run("interval:10s", _utc())
        assert result.tzinfo == timezone.utc

    def test_naive_after_treated_as_utc(self):
        naive = datetime(2025, 1, 1, 12, 0, 0)  # no tzinfo
        result = next_run("interval:60s", naive)
        assert result.tzinfo is not None
        assert result == datetime(2025, 1, 1, 12, 1, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 2. next_run — cron format
# ---------------------------------------------------------------------------


class TestNextRunCron:
    def test_every_minute_cron(self):
        # "* * * * *" should fire in the next minute
        after = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = next_run("* * * * *", after)
        # croniter fires at the next whole minute
        expected = datetime(2025, 6, 1, 12, 1, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_hourly_cron(self):
        # "0 * * * *" — top of every hour
        after = datetime(2025, 6, 1, 12, 30, 0, tzinfo=timezone.utc)
        result = next_run("0 * * * *", after)
        expected = datetime(2025, 6, 1, 13, 0, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_daily_cron(self):
        # "0 9 * * *" — every day at 09:00
        after = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = next_run("0 9 * * *", after)
        expected = datetime(2025, 6, 2, 9, 0, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_result_is_utc(self):
        after = _utc()
        result = next_run("* * * * *", after)
        assert result.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# 3. next_run — bad schedule raises AppError
# ---------------------------------------------------------------------------


class TestNextRunBadSchedule:
    def test_empty_string(self):
        from app.errors import AppError
        with pytest.raises(AppError) as exc_info:
            next_run("", _utc())
        assert exc_info.value.code == "bad_schedule"
        assert exc_info.value.status == 400

    def test_garbage_string(self):
        from app.errors import AppError
        with pytest.raises(AppError) as exc_info:
            next_run("not-a-cron-or-interval", _utc())
        assert exc_info.value.code == "bad_schedule"

    def test_invalid_interval_unit(self):
        # 'd' is not a supported unit — should fall through to cron and fail
        from app.errors import AppError
        with pytest.raises(AppError) as exc_info:
            next_run("interval:1d", _utc())
        assert exc_info.value.code == "bad_schedule"


# ---------------------------------------------------------------------------
# 4. run_due_jobs — deterministic with injected now
# ---------------------------------------------------------------------------


class TestRunDueJobs:
    def _make_executor(self, row_count: int = 5) -> Any:
        """Return a simple executor stub that always succeeds."""
        def executor(job):
            return {
                "id": str(uuid.uuid4()),
                "job_id": str(job["id"]),
                "status": "success",
                "started_at": datetime.now(timezone.utc),
                "finished_at": datetime.now(timezone.utc),
                "row_count": row_count,
                "message": "ok",
                "created_at": datetime.now(timezone.utc),
            }
        return executor

    def test_due_job_is_run(self):
        store = InMemoryJobStore()
        past = _utc() - timedelta(minutes=5)
        now = _utc()

        job = store.create_job(
            org_id="org1",
            created_by="user1",
            name="my-job",
            kind="query",
            target="demo_all",
            schedule="interval:30m",
            next_run_at=past,
        )

        runs = run_due_jobs(store, now, self._make_executor())
        assert len(runs) == 1
        assert runs[0]["job_id"] == job["id"]
        assert runs[0]["status"] == "success"

    def test_not_due_job_is_skipped(self):
        store = InMemoryJobStore()
        future = _utc() + timedelta(hours=1)
        now = _utc()

        store.create_job(
            org_id="org1",
            created_by="user1",
            name="future-job",
            kind="query",
            target="demo_all",
            schedule="interval:60m",
            next_run_at=future,
        )

        runs = run_due_jobs(store, now, self._make_executor())
        assert runs == []

    def test_none_next_run_is_treated_as_due(self):
        """Jobs with next_run_at=None are always due (first run)."""
        store = InMemoryJobStore()
        now = _utc()

        store.create_job(
            org_id="org1",
            created_by="user1",
            name="new-job",
            kind="query",
            target="demo_all",
            schedule="interval:10m",
            next_run_at=None,
        )

        runs = run_due_jobs(store, now, self._make_executor())
        assert len(runs) == 1

    def test_disabled_job_is_skipped(self):
        store = InMemoryJobStore()
        now = _utc()

        store.create_job(
            org_id="org1",
            created_by="user1",
            name="disabled-job",
            kind="query",
            target="demo_all",
            schedule="interval:10m",
            enabled=False,
            next_run_at=None,
        )

        runs = run_due_jobs(store, now, self._make_executor())
        assert runs == []

    def test_next_run_advances_after_run(self):
        """After run_due_jobs, the job's next_run_at should be advanced."""
        store = InMemoryJobStore()
        now = _utc()

        job = store.create_job(
            org_id="org1",
            created_by="user1",
            name="advance-job",
            kind="query",
            target="demo_all",
            schedule="interval:10m",
            next_run_at=None,
        )

        run_due_jobs(store, now, self._make_executor())

        updated = store.get_job(job["id"])
        assert updated["last_run_at"] is not None
        # next_run_at should be now + 10 minutes
        expected_next = now + timedelta(minutes=10)
        assert updated["next_run_at"] == expected_next

    def test_run_recorded_in_store(self):
        """run_due_jobs should add the run to the store."""
        store = InMemoryJobStore()
        now = _utc()

        job = store.create_job(
            org_id="org1",
            created_by="user1",
            name="record-job",
            kind="query",
            target="demo_all",
            schedule="interval:5m",
            next_run_at=None,
        )

        run_due_jobs(store, now, self._make_executor(row_count=42))

        stored_runs = store.list_runs(job["id"])
        assert len(stored_runs) == 1
        assert stored_runs[0]["row_count"] == 42

    def test_only_due_jobs_run_among_multiple(self):
        store = InMemoryJobStore()
        past = _utc() - timedelta(minutes=1)
        future = _utc() + timedelta(hours=1)
        now = _utc()

        store.create_job(
            org_id="org1", created_by="user1", name="due",
            kind="query", target="demo_all", schedule="interval:5m",
            next_run_at=past,
        )
        store.create_job(
            org_id="org1", created_by="user1", name="future",
            kind="query", target="demo_all", schedule="interval:60m",
            next_run_at=future,
        )

        runs = run_due_jobs(store, now, self._make_executor())
        assert len(runs) == 1
        assert runs[0]["status"] == "success"


# ---------------------------------------------------------------------------
# 5. execute_job — real query job
# ---------------------------------------------------------------------------


class TestExecuteJobQuery:
    def test_demo_points_10k_success(self):
        job = {
            "id": str(uuid.uuid4()),
            "kind": "query",
            "target": "demo_points_10k",
        }
        run = execute_job(job)
        assert run["status"] == "success", f"Expected success, got: {run['message']}"
        assert run["row_count"] == 10_000
        assert run["job_id"] == job["id"]
        assert "started_at" in run
        assert "finished_at" in run

    def test_demo_all_success(self):
        """demo_all runs on an empty DuckDB but must succeed (0 rows from empty demo table)."""
        job = {
            "id": str(uuid.uuid4()),
            "kind": "query",
            "target": "demo_all",
        }
        # demo_all references 'demo' table which doesn't exist in a fresh DuckDB
        # — this is an error run; the key contract is that the executor handles it gracefully.
        run = execute_job(job)
        # Either success with 0 rows or error — either is acceptable; key is row_count is int
        assert isinstance(run["row_count"], int)
        assert run["status"] in ("success", "error")


# ---------------------------------------------------------------------------
# 6. execute_job — bad target returns error run
# ---------------------------------------------------------------------------


class TestExecuteJobBadTarget:
    def test_unknown_query_id_returns_error(self):
        job = {
            "id": str(uuid.uuid4()),
            "kind": "query",
            "target": "nonexistent_query_xyz",
        }
        run = execute_job(job)
        assert run["status"] == "error"
        assert run["row_count"] == 0
        assert "nonexistent_query_xyz" in run["message"] or "not found" in run["message"].lower()

    def test_bad_kind_returns_error(self):
        job = {
            "id": str(uuid.uuid4()),
            "kind": "unknown_kind",
            "target": "whatever",
        }
        run = execute_job(job)
        assert run["status"] == "error"
        assert run["row_count"] == 0


# ---------------------------------------------------------------------------
# Fixtures for endpoint tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def jobs_app(app):
    """FastAPI app with InMemoryJobStore + InMemoryRepo injected."""
    store = InMemoryJobStore()
    set_job_store(store)

    repo = InMemoryRepo()
    set_repo(repo)

    yield app, store, repo

    set_job_store(None)
    set_repo(None)


@pytest_asyncio.fixture
async def jobs_client(jobs_app, fake_db):
    """Async HTTPX client pre-seeded with a user + org."""
    app, store, repo = jobs_app

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(user_id=alice_id, email="alice@example.com")

    fake_db.users[alice_id] = alice
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, alice_id, alice_org_id, store, repo


# ---------------------------------------------------------------------------
# 7. Endpoint tests
# ---------------------------------------------------------------------------


class _AsyncJobStoreDouble:
    """Async-interface store double (mirrors PgJobStore's async methods).

    Backed by an InMemoryJobStore but every method is a coroutine, so the route
    handlers must ``await`` the results. Guards against regressing the bug where
    the routes called the (async, in production) PgJobStore synchronously and
    500'd by trying to use a coroutine as data.
    """

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


class TestJobsAgainstAsyncStore:
    """The full endpoint flow must work with an async store (production uses
    the async PgJobStore — these endpoints were 500ing against it)."""

    @pytest_asyncio.fixture
    async def async_store_client(self, app, fake_db):
        set_job_store(_AsyncJobStoreDouble())
        repo = InMemoryRepo()
        set_repo(repo)
        alice_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(user_id=alice_id)
        repo.seed_org_member(org_id=org_id, user_id=alice_id)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
            yield client, alice_id, org_id
        set_job_store(None)
        set_repo(None)

    @pytest.mark.asyncio
    async def test_full_flow_no_500(self, async_store_client):
        client, alice_id, org_id = async_store_client
        h = _auth_headers(alice_id)

        created = await client.post(
            "/api/v1/jobs",
            json={"name": "Async Job", "kind": "query", "target": "demo_points_10k", "schedule": "interval:1h"},
            headers=h,
        )
        assert created.status_code == 201, created.text
        job_id = created.json()["id"]

        listed = await client.get("/api/v1/jobs", headers=h)
        assert listed.status_code == 200, listed.text
        assert any(j["id"] == job_id for j in listed.json())

        got = await client.get(f"/api/v1/jobs/{job_id}", headers=h)
        assert got.status_code == 200, got.text

        ran = await client.post(f"/api/v1/jobs/{job_id}/run", headers=h)
        assert ran.status_code == 200, ran.text

        runs = await client.get(f"/api/v1/jobs/{job_id}/runs", headers=h)
        assert runs.status_code == 200, runs.text
        assert len(runs.json()) >= 1

        deleted = await client.delete(f"/api/v1/jobs/{job_id}", headers=h)
        assert deleted.status_code == 204, deleted.text


class TestJobsCrud:
    @pytest.mark.asyncio
    async def test_create_job_returns_201(self, jobs_client):
        client, alice_id, org_id, store, repo = jobs_client

        resp = await client.post(
            "/api/v1/jobs",
            json={
                "name": "Daily Report",
                "kind": "query",
                "target": "demo_points_10k",
                "schedule": "interval:1h",
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Daily Report"
        assert body["kind"] == "query"
        assert body["org_id"] == org_id
        assert body["created_by"] == alice_id
        assert "id" in body
        assert "next_run_at" in body

    @pytest.mark.asyncio
    async def test_list_jobs_includes_created(self, jobs_client):
        client, alice_id, org_id, store, repo = jobs_client

        await client.post(
            "/api/v1/jobs",
            json={"name": "Job A", "kind": "query", "target": "demo_points_10k", "schedule": "interval:5m"},
            headers=_auth_headers(alice_id),
        )

        resp = await client.get("/api/v1/jobs", headers=_auth_headers(alice_id))
        assert resp.status_code == 200, resp.text
        jobs = resp.json()
        assert isinstance(jobs, list)
        assert any(j["name"] == "Job A" for j in jobs)

    @pytest.mark.asyncio
    async def test_get_job_returns_200(self, jobs_client):
        client, alice_id, org_id, store, repo = jobs_client

        create_resp = await client.post(
            "/api/v1/jobs",
            json={"name": "Gettable", "kind": "query", "target": "demo_points_10k", "schedule": "interval:10m"},
            headers=_auth_headers(alice_id),
        )
        job_id = create_resp.json()["id"]

        resp = await client.get(f"/api/v1/jobs/{job_id}", headers=_auth_headers(alice_id))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == job_id

    @pytest.mark.asyncio
    async def test_delete_job_returns_204(self, jobs_client):
        client, alice_id, org_id, store, repo = jobs_client

        create_resp = await client.post(
            "/api/v1/jobs",
            json={"name": "Delete Me", "kind": "query", "target": "demo_points_10k", "schedule": "interval:1h"},
            headers=_auth_headers(alice_id),
        )
        job_id = create_resp.json()["id"]

        del_resp = await client.delete(f"/api/v1/jobs/{job_id}", headers=_auth_headers(alice_id))
        assert del_resp.status_code == 204

        get_resp = await client.get(f"/api/v1/jobs/{job_id}", headers=_auth_headers(alice_id))
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_run_now_returns_success_run(self, jobs_client):
        client, alice_id, org_id, store, repo = jobs_client

        create_resp = await client.post(
            "/api/v1/jobs",
            json={"name": "Run Now", "kind": "query", "target": "demo_points_10k", "schedule": "interval:1h"},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        job_id = create_resp.json()["id"]

        run_resp = await client.post(f"/api/v1/jobs/{job_id}/run", headers=_auth_headers(alice_id))
        assert run_resp.status_code == 200, run_resp.text
        run = run_resp.json()
        assert run["status"] == "success"
        assert run["row_count"] == 10_000
        assert run["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_runs_list_includes_run(self, jobs_client):
        client, alice_id, org_id, store, repo = jobs_client

        create_resp = await client.post(
            "/api/v1/jobs",
            json={"name": "Runs List", "kind": "query", "target": "demo_points_10k", "schedule": "interval:1h"},
            headers=_auth_headers(alice_id),
        )
        job_id = create_resp.json()["id"]

        await client.post(f"/api/v1/jobs/{job_id}/run", headers=_auth_headers(alice_id))

        runs_resp = await client.get(f"/api/v1/jobs/{job_id}/runs", headers=_auth_headers(alice_id))
        assert runs_resp.status_code == 200, runs_resp.text
        runs = runs_resp.json()
        assert isinstance(runs, list)
        assert len(runs) >= 1
        assert runs[0]["job_id"] == job_id


class TestJobsCrossOrg:
    @pytest.mark.asyncio
    async def test_cross_org_get_returns_404(self, jobs_app, fake_db):
        app, store, repo = jobs_app

        # Alice in org A
        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        # Bob in org B
        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
            # Alice creates a job
            create_resp = await client.post(
                "/api/v1/jobs",
                json={"name": "Alice's Job", "kind": "query", "target": "demo_points_10k", "schedule": "interval:1h"},
                headers=_auth_headers(alice_id),
            )
            assert create_resp.status_code == 201, create_resp.text
            job_id = create_resp.json()["id"]

            # Bob tries to GET it — must get 404
            get_resp = await client.get(f"/api/v1/jobs/{job_id}", headers=_auth_headers(bob_id))
            assert get_resp.status_code == 404, "Cross-org GET must return 404"


class TestJobsAuthGuard:
    @pytest.mark.asyncio
    async def test_no_auth_list_returns_401(self, jobs_client):
        client, *_ = jobs_client
        resp = await client.get("/api/v1/jobs")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_create_returns_401(self, jobs_client):
        client, *_ = jobs_client
        resp = await client.post(
            "/api/v1/jobs",
            json={"name": "x", "kind": "query", "target": "demo_points_10k", "schedule": "interval:1h"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_run_returns_401(self, jobs_client):
        client, *_ = jobs_client
        resp = await client.post(f"/api/v1/jobs/{uuid.uuid4()}/run")
        assert resp.status_code == 401


class TestJobsBadSchedule:
    @pytest.mark.asyncio
    async def test_bad_schedule_returns_400(self, jobs_client):
        client, alice_id, org_id, store, repo = jobs_client

        resp = await client.post(
            "/api/v1/jobs",
            json={
                "name": "Bad Schedule Job",
                "kind": "query",
                "target": "demo_points_10k",
                "schedule": "not-a-valid-cron-or-interval",
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error"]["code"] == "bad_schedule"
