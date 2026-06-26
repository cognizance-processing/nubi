"""Tests for the async export-job endpoints and worker.

Strategy
--------
- Reuses the same ``lake_client`` fixture from ``test_lake_export.py`` (same
  local storage setup: NUBI_MANAGED_LAKE_DIR + NUBI_CUSTODY_ENABLED=true).
- The export job store is reset between tests via ``set_export_job_store``.
- Real DuckDB execution is gated the same way as the sync tests: the shared
  function is mocked out for worker tests that don't need actual file I/O.

Coverage
--------
1.  Enqueue → job row created (state=queued, 202 returned)
2.  Enqueue → status_url in response points at the right endpoint
3.  GET job → returns queued state before worker runs
4.  CAS single-winner — two workers racing only one wins
5.  Worker run → state transitions queued→running→succeeded, result populated
6.  Worker run → failed job → state=failed, error populated
7.  Cross-org cannot enqueue into another org's datastore (404)
8.  Cross-org cannot read another org's job (404)
9.  Custody-disabled → 403 on enqueue
10. Custody-disabled → 403 on GET job
11. Shared export function still enforces SELECT-only (regression)
12. Shared export function still enforces dest-outside-lake (regression)
13. Invalid format → 400 on enqueue
14. table + sql conflict → 400 on enqueue
15. Bad dest_uri → 400 on enqueue
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Self-register the lake_export router BEFORE the app is created.
import app.routes.lake_export  # noqa: F401

from app.auth.jwt import mint_access_token
from app.lakehouse.managed import MANAGED_MARKER, lake_prefix
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.routes.lake_export import (
    InMemoryExportJobStore,
    run_one_export_job,
    set_export_job_store,
    _run_export_core,
    _validate_export_sql,
    _validate_dest_not_in_source,
)


# ---------------------------------------------------------------------------
# Helpers (mirrors test_lake_export.py helpers)
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _managed_ds_row(
    ds_id: str,
    org_id: str,
    db_uri: str,
    prefix: str,
    user_id: str = "user-x",
) -> dict[str, Any]:
    return {
        "id": ds_id,
        "org_id": org_id,
        "name": "Test managed lake",
        "created_by": user_id,
        "config": {
            "connector_type": "duckdb",
            "database": db_uri,
            MANAGED_MARKER: True,
            "managed_prefix": prefix,
            "managed_scheme": "file",
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def job_client(tmp_path, monkeypatch, app, fake_db):
    """Yield ``(client, alice_id, alice_org_id, repo, ds_id, dest_dir)``.

    Same setup as ``lake_client`` but yields the dest_dir instead of a
    pre-seeded table name.  We seed a Parquet file here too so the worker
    can actually run an export.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    lake_dir = tmp_path / "managed-lake"
    lake_dir.mkdir()
    dest_dir = tmp_path / "export-dest"
    dest_dir.mkdir()

    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", str(lake_dir))
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    from app.config import get_settings
    get_settings.cache_clear()

    # Fresh job store for every test.
    fresh_store = InMemoryExportJobStore()
    set_export_job_store(fresh_store)

    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(user_id=alice_id)
    fake_db.users[alice_id] = alice
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    ds_id = str(uuid.uuid4())
    prefix = lake_prefix(alice_org_id, ds_id)
    db_uri = f"file://{lake_dir}/{prefix}"

    ds_row = _managed_ds_row(
        ds_id=ds_id,
        org_id=alice_org_id,
        db_uri=db_uri,
        prefix=prefix,
        user_id=alice_id,
    )
    repo._store["datastores"][ds_id] = {
        **ds_row,
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "project_id": None,
    }

    # Seed a small Parquet file so the worker export path can succeed.
    table_name = "events"
    parquet_key = f"{prefix}{table_name}/part-0.parquet"
    parquet_abs = lake_dir / parquet_key
    parquet_abs.parent.mkdir(parents=True, exist_ok=True)
    arrow_table = pa.table({
        "id": pa.array(list(range(1, 6)), type=pa.int32()),
        "name": pa.array([f"event_{i}" for i in range(1, 6)], type=pa.string()),
    })
    pq.write_table(arrow_table, str(parquet_abs))

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, alice_id, alice_org_id, repo, ds_id, dest_dir, fresh_store

    # Teardown.
    set_export_job_store(None)
    set_repo(None)
    from app.config import get_settings as _gs
    _gs.cache_clear()


# ---------------------------------------------------------------------------
# Tests — enqueue endpoint
# ---------------------------------------------------------------------------


class TestEnqueueExportJob:
    """POST /lake/{id}/export/jobs"""

    @pytest.mark.asyncio
    async def test_enqueue_creates_job_queued(self, job_client):
        """Enqueue returns 202 and the job store has a row with state=queued."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 202, resp.text
        data = resp.json()
        assert "job_id" in data
        assert data["state"] == "queued"
        assert "status_url" in data

        # Verify the job row was created in the store.
        job = store.get_job(data["job_id"], org_id)
        assert job is not None
        assert job["state"] == "queued"
        assert job["org_id"] == org_id
        assert job["datastore_id"] == ds_id

    @pytest.mark.asyncio
    async def test_enqueue_status_url_is_correct(self, job_client):
        """status_url in the 202 response points at the GET job endpoint."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 202
        data = resp.json()
        job_id = data["job_id"]
        assert data["status_url"].endswith(f"/api/v1/lake/{ds_id}/export/jobs/{job_id}")

    @pytest.mark.asyncio
    async def test_enqueue_custody_disabled_returns_403(self, job_client, monkeypatch):
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "false")
        from app.config import get_settings
        get_settings.cache_clear()

        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "custody_disabled"

    @pytest.mark.asyncio
    async def test_enqueue_cross_org_returns_404(self, job_client):
        """Bob cannot enqueue an export on Alice's datastore."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        bob_id = str(uuid.uuid4())
        bob_org_id = str(uuid.uuid4())
        from tests.conftest import _fake_db
        _fake_db.users[bob_id] = _make_user(user_id=bob_id, email="bob@example.com")
        repo.seed_org_member(org_id=bob_org_id, user_id=bob_id)

        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(bob_id),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "lake_not_found"

    @pytest.mark.asyncio
    async def test_enqueue_invalid_format_returns_400(self, job_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}", "format": "xlsx"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_format"

    @pytest.mark.asyncio
    async def test_enqueue_table_and_sql_conflict_returns_400(self, job_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={
                "dest_uri": f"file://{dest_dir}",
                "table": "events",
                "sql": "SELECT 1",
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "export_conflict"

    @pytest.mark.asyncio
    async def test_enqueue_bad_dest_uri_returns_400(self, job_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": "/not-a-valid-uri"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_dest_uri"

    @pytest.mark.asyncio
    async def test_enqueue_non_select_sql_returns_400(self, job_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}", "sql": "DROP TABLE x"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_export_sql"

    @pytest.mark.asyncio
    async def test_enqueue_does_not_store_dest_creds_ref_plaintext(self, job_client):
        """dest_creds_ref (secret-store key) must be stored but never echoed in job GET."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={
                "dest_uri": f"file://{dest_dir}",
                "dest_creds_ref": "secret-store://my-dest-creds",
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # The GET response must NOT include dest_creds_ref.
        get_resp = await ac.get(
            f"/api/v1/lake/{ds_id}/export/jobs/{job_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 200
        assert "dest_creds_ref" not in get_resp.json()


# ---------------------------------------------------------------------------
# Tests — GET job status endpoint
# ---------------------------------------------------------------------------


class TestGetExportJob:
    """GET /lake/{id}/export/jobs/{job_id}"""

    @pytest.mark.asyncio
    async def test_get_job_queued_state(self, job_client):
        """GET returns queued state before the worker runs."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        post_resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert post_resp.status_code == 202
        job_id = post_resp.json()["job_id"]

        get_resp = await ac.get(
            f"/api/v1/lake/{ds_id}/export/jobs/{job_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["id"] == job_id
        assert data["state"] == "queued"

    @pytest.mark.asyncio
    async def test_get_job_cross_org_returns_404(self, job_client):
        """Bob cannot read Alice's export job even if he guesses the job_id."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        # Alice enqueues a job.
        post_resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert post_resp.status_code == 202
        job_id = post_resp.json()["job_id"]

        # Bob registers.
        bob_id = str(uuid.uuid4())
        bob_org_id = str(uuid.uuid4())
        from tests.conftest import _fake_db
        _fake_db.users[bob_id] = _make_user(user_id=bob_id, email="bob@example.com")
        repo.seed_org_member(org_id=bob_org_id, user_id=bob_id)

        # Bob tries to read Alice's job.
        get_resp = await ac.get(
            f"/api/v1/lake/{ds_id}/export/jobs/{job_id}",
            headers=_auth_headers(bob_id),
        )
        assert get_resp.status_code == 404
        assert get_resp.json()["error"]["code"] == "export_job_not_found"

    @pytest.mark.asyncio
    async def test_get_job_nonexistent_returns_404(self, job_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        fake_job_id = str(uuid.uuid4())
        resp = await ac.get(
            f"/api/v1/lake/{ds_id}/export/jobs/{fake_job_id}",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "export_job_not_found"

    @pytest.mark.asyncio
    async def test_get_job_custody_disabled_returns_403(self, job_client, monkeypatch):
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        # Create a job while custody is on.
        job = store.create_job({
            "org_id": org_id,
            "datastore_id": ds_id,
            "user_id": alice_id,
            "dest_uri": f"file://{dest_dir}",
            "format": "parquet",
        })
        monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "false")
        from app.config import get_settings
        get_settings.cache_clear()

        resp = await ac.get(
            f"/api/v1/lake/{ds_id}/export/jobs/{job['id']}",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "custody_disabled"


# ---------------------------------------------------------------------------
# Tests — worker CAS + execution
# ---------------------------------------------------------------------------


class TestExportWorker:
    """Worker claim + execution tests."""

    @pytest.mark.asyncio
    async def test_worker_run_succeeds_state_transition(self, job_client, tmp_path):
        """Worker claims queued job, runs export, sets state=succeeded + result."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        # Enqueue via API.
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Run the worker.
        updated = await run_one_export_job(store, repo, worker_id="test-worker:1")
        assert updated is not None
        assert updated["id"] == job_id
        assert updated["state"] == "succeeded"
        assert updated["result"] is not None
        assert "exported" in updated["result"]
        assert updated["result"]["tables_exported"] >= 1

        # Verify the job store reflects the completed state.
        final = store.get_job(job_id, org_id)
        assert final["state"] == "succeeded"

    @pytest.mark.asyncio
    async def test_worker_no_queued_jobs_returns_none(self, job_client):
        """Worker returns None when no queued jobs exist."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client
        result = await run_one_export_job(store, repo, worker_id="test-worker:1")
        assert result is None

    @pytest.mark.asyncio
    async def test_worker_cas_single_winner(self, job_client, tmp_path):
        """Two concurrent claim attempts only one wins (CAS guarantee)."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client

        # Enqueue one job.
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Simulate two workers racing: manually call claim_job twice.
        winner1 = store.claim_job(job_id, "worker-A")
        winner2 = store.claim_job(job_id, "worker-B")

        # Exactly one should win (the first claim), the other gets None.
        winners = [w for w in (winner1, winner2) if w is not None]
        losers = [w for w in (winner1, winner2) if w is None]
        assert len(winners) == 1, "Exactly one worker should win the CAS race"
        assert len(losers) == 1, "The other worker should lose the CAS race"
        assert winners[0]["state"] == "running"
        assert winners[0]["worker_id"] in ("worker-A", "worker-B")

    @pytest.mark.asyncio
    async def test_worker_failed_export_sets_error(self, job_client, tmp_path):
        """When _run_export_core raises, the worker sets state=failed + error."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client

        # Enqueue a job with an invalid SQL (SELECT-only guard).
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}", "sql": "SELECT 1"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Patch _run_export_core to simulate a failure.
        from app.errors import AppError as _AppError

        async def _fail(*args, **kwargs):
            raise _AppError("write_result_error", "Simulated DuckDB failure", 500)

        with patch("app.routes.lake_export._run_export_core", side_effect=_fail):
            updated = await run_one_export_job(store, repo, worker_id="test-worker:1")

        assert updated is not None
        assert updated["state"] == "failed"
        assert updated["error"] is not None
        assert "write_result_error" in updated["error"] or "Simulated" in updated["error"]

    @pytest.mark.asyncio
    async def test_worker_custody_disabled_fails_job(self, job_client, monkeypatch):
        """Worker fails the job when custody is disabled at execution time."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client

        # Enqueue while custody is enabled.
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export/jobs",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Disable custody before the worker runs.
        monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "false")
        from app.config import get_settings
        get_settings.cache_clear()

        updated = await run_one_export_job(store, repo, worker_id="test-worker:1")
        assert updated is not None
        assert updated["state"] == "failed"
        assert "custody_disabled" in (updated.get("error") or "")


# ---------------------------------------------------------------------------
# Regression tests — shared security guards still apply
# ---------------------------------------------------------------------------


class TestSharedGuardsRegression:
    """_run_export_core must enforce the same guards as the sync endpoint.

    These tests call the shared function directly to confirm the guards
    are not bypassed by the async path.
    """

    @pytest.mark.asyncio
    async def test_select_only_guard_enforced(self, job_client):
        """_run_export_core rejects non-SELECT sql (SELECT-only guard)."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client

        from app.errors import AppError as _AppError
        with pytest.raises(_AppError) as exc_info:
            await _run_export_core(
                org_id=org_id,
                datastore_id=ds_id,
                dest_uri=f"file://{dest_dir}",
                dest_creds=None,
                table=None,
                sql="DROP TABLE orders",
                fmt="parquet",
                repo=repo,
            )
        assert exc_info.value.code == "invalid_export_sql"

    @pytest.mark.asyncio
    async def test_dest_outside_lake_guard_enforced(self, job_client):
        """_run_export_core rejects dest_uri inside the source lake (dest-outside guard)."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client

        from app.lakehouse.managed import lake_prefix as _lake_prefix, resolve_central_storage
        from app.config import get_settings
        get_settings.cache_clear()
        central = resolve_central_storage()
        if central is None:
            pytest.skip("Central storage not configured — skipping overlap test")

        prefix = _lake_prefix(org_id, ds_id)
        source_lake_uri = f"{central.base_uri()}/{prefix.lstrip('/')}"

        from app.errors import AppError as _AppError
        with pytest.raises(_AppError) as exc_info:
            await _run_export_core(
                org_id=org_id,
                datastore_id=ds_id,
                dest_uri=source_lake_uri,
                dest_creds=None,
                table=None,
                sql=None,
                fmt="parquet",
                repo=repo,
            )
        assert exc_info.value.code == "invalid_dest_uri"

    @pytest.mark.asyncio
    async def test_table_name_validation_enforced(self, job_client):
        """_run_export_core rejects unsafe table names."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client

        from app.errors import AppError as _AppError
        with pytest.raises(_AppError) as exc_info:
            await _run_export_core(
                org_id=org_id,
                datastore_id=ds_id,
                dest_uri=f"file://{dest_dir}",
                dest_creds=None,
                table="orders'; DROP TABLE orders--",
                sql=None,
                fmt="parquet",
                repo=repo,
            )
        assert exc_info.value.code == "invalid_table_name"

    @pytest.mark.asyncio
    async def test_org_scoping_enforced(self, job_client):
        """_run_export_core rejects access to another org's datastore (404)."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, store = job_client

        # Build a different org_id (Bob's org has no such datastore).
        bob_org_id = str(uuid.uuid4())

        from app.errors import AppError as _AppError
        with pytest.raises(_AppError) as exc_info:
            await _run_export_core(
                org_id=bob_org_id,  # wrong org — should 404
                datastore_id=ds_id,
                dest_uri=f"file://{dest_dir}",
                dest_creds=None,
                table=None,
                sql=None,
                fmt="parquet",
                repo=repo,
            )
        assert exc_info.value.code == "lake_not_found"

    def test_validate_export_sql_rejects_ddl(self):
        """_validate_export_sql rejects DDL/DML statements."""
        from app.errors import AppError as _AppError
        for bad in ["DROP TABLE x", "INSERT INTO x VALUES (1)", "COPY x TO 'out'"]:
            with pytest.raises(_AppError) as exc_info:
                _validate_export_sql(bad)
            assert exc_info.value.code == "invalid_export_sql"

    def test_validate_export_sql_accepts_select(self):
        """_validate_export_sql accepts SELECT and WITH…SELECT (CTEs)."""
        _validate_export_sql("SELECT 1")
        _validate_export_sql("SELECT * FROM orders WHERE id > 0")
        _validate_export_sql("WITH cte AS (SELECT 1) SELECT * FROM cte")

    def test_validate_dest_not_in_source(self):
        """_validate_dest_not_in_source rejects dest inside source."""
        from app.errors import AppError as _AppError
        with pytest.raises(_AppError):
            _validate_dest_not_in_source(
                "file:///managed/orgs/x/lake/y/",
                "file:///managed/orgs/x/lake/y/",
            )
        # Outside is fine.
        _validate_dest_not_in_source(
            "file:///my-export-bucket/",
            "file:///managed/orgs/x/lake/y/",
        )


# ---------------------------------------------------------------------------
# InMemoryExportJobStore unit tests
# ---------------------------------------------------------------------------


class TestInMemoryExportJobStore:
    """Unit tests for the in-memory job store."""

    def test_create_and_get_job(self):
        store = InMemoryExportJobStore()
        org_id = str(uuid.uuid4())
        job = store.create_job({
            "org_id": org_id,
            "datastore_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "dest_uri": "file:///out/",
            "format": "parquet",
        })
        assert job["state"] == "queued"
        assert "id" in job
        fetched = store.get_job(job["id"], org_id)
        assert fetched is not None
        assert fetched["id"] == job["id"]

    def test_get_job_wrong_org_returns_none(self):
        store = InMemoryExportJobStore()
        org_id = str(uuid.uuid4())
        job = store.create_job({
            "org_id": org_id,
            "datastore_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "dest_uri": "file:///out/",
            "format": "parquet",
        })
        # Query with a different org_id.
        assert store.get_job(job["id"], str(uuid.uuid4())) is None

    def test_claim_job_cas_single_winner(self):
        """Two claim_job calls on the same queued job: exactly one wins."""
        store = InMemoryExportJobStore()
        job = store.create_job({
            "org_id": str(uuid.uuid4()),
            "datastore_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "dest_uri": "file:///out/",
            "format": "parquet",
        })
        job_id = job["id"]

        result_a = store.claim_job(job_id, "worker-A")
        result_b = store.claim_job(job_id, "worker-B")

        winners = [r for r in (result_a, result_b) if r is not None]
        losers = [r for r in (result_a, result_b) if r is None]
        assert len(winners) == 1, "Exactly one claim should succeed"
        assert len(losers) == 1, "The second claim should fail (None)"
        assert winners[0]["state"] == "running"

    def test_claim_already_running_returns_none(self):
        store = InMemoryExportJobStore()
        job = store.create_job({
            "org_id": str(uuid.uuid4()),
            "datastore_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "dest_uri": "file:///out/",
            "format": "parquet",
        })
        store.claim_job(job["id"], "worker-1")  # first claim succeeds
        result = store.claim_job(job["id"], "worker-2")  # second must fail
        assert result is None

    def test_list_queued_only_queued(self):
        store = InMemoryExportJobStore()
        j1 = store.create_job({"org_id": "o", "datastore_id": "d", "user_id": "u", "dest_uri": "s3://x/", "format": "parquet"})
        j2 = store.create_job({"org_id": "o", "datastore_id": "d", "user_id": "u", "dest_uri": "s3://y/", "format": "parquet"})
        store.claim_job(j2["id"], "w")  # j2 is now running
        queued = store.list_queued()
        queued_ids = {j["id"] for j in queued}
        assert j1["id"] in queued_ids
        assert j2["id"] not in queued_ids

    def test_update_job_state(self):
        store = InMemoryExportJobStore()
        job = store.create_job({
            "org_id": "o",
            "datastore_id": "d",
            "user_id": "u",
            "dest_uri": "file:///out/",
            "format": "parquet",
        })
        updated = store.update_job(job["id"], {"state": "succeeded", "result": {"exported": []}})
        assert updated["state"] == "succeeded"
        assert updated["result"] == {"exported": []}
