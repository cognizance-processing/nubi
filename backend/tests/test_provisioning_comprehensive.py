"""Comprehensive edge-case tests for ``POST /apply`` — declarative provisioning.

These tests add NEW coverage on top of the existing test_apply.py suite.
Focus areas:
- Parametrised idempotency across resource kinds
- Dry-run invariants (no writes, correct action labels)
- Partial-failure ordering and isolation
- Cross-org safety (org A resources invisible to org B)
- Summary counter correctness in all combinations
- Large-batch handling
- Error-field presence invariant
- Resource-id propagation and UUID shape
- Body org field vs. X-Org-Id header precedence
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# ---------------------------------------------------------------------------
# Helpers (mirrors test_apply.py; kept local to avoid inter-file coupling)
# ---------------------------------------------------------------------------


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _org_header(org_id: str) -> dict[str, str]:
    return {"X-Org-Id": org_id}


def _dashboard_envelope(name: str, env_id: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"name": name}
    if env_id:
        meta["id"] = env_id
    return {
        "kind": "dashboard",
        "apiVersion": "nubi/v1",
        "metadata": meta,
        "spec": {"title": name, "panels": []},
    }


def _connector_envelope(name: str, env_id: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"name": name}
    if env_id:
        meta["id"] = env_id
    return {
        "kind": "connector",
        "apiVersion": "nubi/v1",
        "metadata": meta,
        "spec": {
            "connector_type": "postgres",
            "host": "db.example.com",
            "database": "prod",
        },
    }


def _unknown_envelope(name: str = "bad") -> dict[str, Any]:
    return {
        "kind": "gizmo",
        "apiVersion": "nubi/v1",
        "metadata": {"name": name},
        "spec": {},
    }


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def apply_setup(app, fake_db):
    """Yield (client, user_id, org_id, repo) for /apply tests."""
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "alice@example.com",
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }

    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, user_id, org_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# Convenience: second independent org setup
# ---------------------------------------------------------------------------


async def _make_user_org(fake_db, repo: InMemoryRepo) -> tuple[str, str]:
    """Create a fresh user+org pair and seed membership in *repo*."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": f"user-{user_id[:8]}@example.com",
        "name": "Bob",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)
    return user_id, org_id


# ---------------------------------------------------------------------------
# Apply creates resources
# ---------------------------------------------------------------------------


class TestApplyCreatesResources:
    @pytest.mark.asyncio
    async def test_single_dashboard_created(self, apply_setup):
        """Single dashboard → action='created', summary.created=1."""
        client, user_id, org_id, repo = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Alpha")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["action"] == "created"
        assert data["summary"]["created"] == 1

    @pytest.mark.asyncio
    async def test_multiple_mixed_kinds_all_created(self, apply_setup):
        """Dashboard + connector in one batch → both created."""
        client, user_id, org_id, repo = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("Dash1"),
                    _connector_envelope("Conn1"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["results"]) == 2
        assert all(r["action"] == "created" for r in data["results"])
        assert data["summary"]["created"] == 2
        assert data["summary"]["failed"] == 0

    @pytest.mark.asyncio
    async def test_apply_always_returns_http_200(self, apply_setup):
        """HTTP status is always 200 regardless of content (even bad envelopes)."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_unknown_envelope()], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Idempotency (parametrised)
# ---------------------------------------------------------------------------


class TestReapplyIdempotent:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "envelope_fn,kind",
        [
            (_dashboard_envelope, "dashboard"),
            (_connector_envelope, "connector"),
        ],
    )
    async def test_reapply_is_idempotent(self, apply_setup, envelope_fn, kind):
        """First apply → 'created'; second apply with same id → 'updated'.
        Repo still has exactly 1 resource of that kind.
        """
        client, user_id, org_id, repo = apply_setup

        # First apply
        r1 = await client.post(
            "/api/v1/apply",
            json={"resources": [envelope_fn("Resource-A")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert r1.status_code == 200, r1.text
        result1 = r1.json()["results"][0]
        assert result1["action"] == "created", f"First apply should create: {result1}"
        resource_id = result1["id"]
        assert resource_id

        # Second apply with same id
        r2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [envelope_fn("Resource-A", env_id=resource_id)],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert r2.status_code == 200, r2.text
        result2 = r2.json()["results"][0]
        assert result2["action"] == "updated", (
            f"Second apply with same id should be 'updated', got: {result2}"
        )

        # Repo has exactly 1 resource
        table_map = {"dashboard": "boards", "connector": "datastores"}
        table_resource = table_map[kind]
        rows = await repo.list(table_resource, org_id, None)
        assert len(rows) == 1, (
            f"Expected 1 {kind} in repo after re-apply, got {len(rows)}: {rows}"
        )

    @pytest.mark.asyncio
    async def test_reapply_summary_shows_updated_not_created(self, apply_setup):
        """Summary on re-apply: updated=1, created=0."""
        client, user_id, org_id, _ = apply_setup

        r1 = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("D")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        created_id = r1.json()["results"][0]["id"]

        r2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("D", env_id=created_id)],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        summary = r2.json()["summary"]
        assert summary["updated"] == 1
        assert summary["created"] == 0
        assert summary["failed"] == 0


# ---------------------------------------------------------------------------
# Dry-run / plan
# ---------------------------------------------------------------------------


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_flag_echoed_in_response(self, apply_setup):
        """Response carries dry_run=True when requested."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Plan Me")], "dry_run": True},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200
        assert resp.json()["dry_run"] is True

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing_to_repo(self, apply_setup):
        """dry_run=True: repo stays empty after the call."""
        client, user_id, org_id, repo = apply_setup
        await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Ghost")], "dry_run": True},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        boards = await repo.list("boards", org_id, None)
        assert boards == [], f"Expected 0 boards after dry-run, got: {boards}"

    @pytest.mark.asyncio
    async def test_dry_run_action_is_create_not_created(self, apply_setup):
        """dry_run: planned action is 'create', not 'created'."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Plan")], "dry_run": True},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        action = resp.json()["results"][0]["action"]
        assert action == "create", f"Expected 'create', got {action!r}"

    @pytest.mark.asyncio
    async def test_dry_run_with_existing_id_action_is_update(self, apply_setup):
        """dry_run with an id that already exists → action='update'."""
        client, user_id, org_id, repo = apply_setup
        existing = await repo.create(
            resource="boards",
            org_id=org_id,
            created_by=user_id,
            name="Live Board",
            config={"spec": {"title": "Live Board", "panels": []}},
        )
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("Live Board", env_id=str(existing["id"]))
                ],
                "dry_run": True,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        action = resp.json()["results"][0]["action"]
        assert action == "update", f"Expected 'update', got {action!r}"
        # Confirm still only one board in repo (no duplicate written)
        boards = await repo.list("boards", org_id, None)
        assert len(boards) == 1

    @pytest.mark.asyncio
    async def test_dry_run_unknown_kind_fails_no_writes(self, apply_setup):
        """dry_run + unknown kind → action='failed', no repo writes."""
        client, user_id, org_id, repo = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_unknown_envelope("NoWrite")], "dry_run": True},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert result["action"] == "failed"
        assert result.get("error"), "Failed dry-run result must carry an error message"
        boards = await repo.list("boards", org_id, None)
        assert boards == []

    @pytest.mark.asyncio
    async def test_dry_run_connector_action_is_create(self, apply_setup):
        """dry_run for a connector also produces 'create' action."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_connector_envelope("PlanConn")], "dry_run": True},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200
        action = resp.json()["results"][0]["action"]
        assert action == "create", f"Expected 'create', got {action!r}"


# ---------------------------------------------------------------------------
# Partial failure — batch continues
# ---------------------------------------------------------------------------


class TestPartialFailure:
    @pytest.mark.asyncio
    async def test_bad_first_good_last_all_processed(self, apply_setup):
        """[unknown_kind, good, good] → all 3 processed; 1 failed + 2 created."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _unknown_envelope("First"),
                    _dashboard_envelope("Second"),
                    _dashboard_envelope("Third"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200
        data = resp.json()
        results = data["results"]
        assert len(results) == 3, f"Expected 3 results, got {len(results)}"

        failed = [r for r in results if r["action"] == "failed"]
        created = [r for r in results if r["action"] == "created"]
        assert len(failed) == 1
        assert len(created) == 2

        summary = data["summary"]
        assert summary["failed"] == 1
        assert summary["created"] == 2

    @pytest.mark.asyncio
    async def test_failed_result_has_error_string(self, apply_setup):
        """A failed envelope result must carry a non-empty 'error' string."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_unknown_envelope()], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        result = resp.json()["results"][0]
        assert result["action"] == "failed"
        error = result.get("error")
        assert error, f"'error' must be a non-empty string, got {error!r}"
        assert isinstance(error, str)

    @pytest.mark.asyncio
    async def test_good_results_have_no_truthy_error(self, apply_setup):
        """Successful results must NOT carry a truthy 'error' key."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("OK1"),
                    _dashboard_envelope("OK2"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        for result in resp.json()["results"]:
            assert result["action"] == "created"
            assert not result.get("error"), (
                f"Successful result must not have a truthy 'error', got: {result}"
            )

    @pytest.mark.asyncio
    async def test_summary_created2_failed1(self, apply_setup):
        """After [created, created, failed]: created=2, failed=1, updated=0."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("Good1"),
                    _dashboard_envelope("Good2"),
                    _unknown_envelope("Bad"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        summary = resp.json()["summary"]
        assert summary["created"] == 2
        assert summary["failed"] == 1
        assert summary.get("updated", 0) == 0


# ---------------------------------------------------------------------------
# Cross-org safety
# ---------------------------------------------------------------------------


class TestCrossOrgSafety:
    @pytest.mark.asyncio
    async def test_org_b_cannot_update_org_a_resource(self, apply_setup, fake_db):
        """Org B applying with Org A's resource ID must not modify Org A's resource."""
        client, user_a, org_a, repo = apply_setup

        # Create a second user/org inside the same repo
        user_b, org_b = await _make_user_org(fake_db, repo)

        # Org A creates a dashboard
        r1 = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("OrgA-Dash")], "dry_run": False},
            headers={**_auth(user_a), **_org_header(org_a)},
        )
        assert r1.status_code == 200, r1.text
        resource_id = r1.json()["results"][0]["id"]

        # Org B tries to apply using Org A's resource id
        r2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("OrgB-Overwrite", env_id=resource_id)
                ],
                "dry_run": False,
            },
            headers={**_auth(user_b), **_org_header(org_b)},
        )
        assert r2.status_code == 200, r2.text

        # Org A's resource should be unchanged
        org_a_boards = await repo.list("boards", org_a, None)
        assert len(org_a_boards) == 1
        assert org_a_boards[0]["name"] == "OrgA-Dash", (
            f"Org A's dashboard was modified by Org B: {org_a_boards[0]}"
        )

    @pytest.mark.asyncio
    async def test_org_a_resources_invisible_to_org_b(self, apply_setup, fake_db):
        """Org B listing its own resources does not surface Org A's resources."""
        client, user_a, org_a, repo = apply_setup
        user_b, org_b = await _make_user_org(fake_db, repo)

        # Org A creates a dashboard
        await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("A-Private")], "dry_run": False},
            headers={**_auth(user_a), **_org_header(org_a)},
        )

        # Org B's boards must be empty
        org_b_boards = await repo.list("boards", org_b, None)
        assert org_b_boards == [], (
            f"Org B should see 0 boards, found: {org_b_boards}"
        )

    @pytest.mark.asyncio
    async def test_two_orgs_resources_are_isolated(self, apply_setup, fake_db):
        """Both orgs can create resources simultaneously without cross-contamination."""
        client, user_a, org_a, repo = apply_setup
        user_b, org_b = await _make_user_org(fake_db, repo)

        await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("A-Dash1"),
                    _dashboard_envelope("A-Dash2"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_a), **_org_header(org_a)},
        )

        await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("B-Dash1")], "dry_run": False},
            headers={**_auth(user_b), **_org_header(org_b)},
        )

        org_a_boards = await repo.list("boards", org_a, None)
        org_b_boards = await repo.list("boards", org_b, None)

        assert len(org_a_boards) == 2, f"Org A should have 2 boards: {org_a_boards}"
        assert len(org_b_boards) == 1, f"Org B should have 1 board: {org_b_boards}"
        # No IDs should overlap
        a_ids = {r["id"] for r in org_a_boards}
        b_ids = {r["id"] for r in org_b_boards}
        assert a_ids.isdisjoint(b_ids), f"ID overlap: {a_ids & b_ids}"


# ---------------------------------------------------------------------------
# Empty resources
# ---------------------------------------------------------------------------


class TestEmptyResources:
    @pytest.mark.asyncio
    async def test_empty_resources_http_200(self, apply_setup):
        """resources=[] → HTTP 200."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_empty_resources_empty_results(self, apply_setup):
        """resources=[] → results list is empty."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.json()["results"] == []

    @pytest.mark.asyncio
    async def test_empty_resources_all_summary_counts_zero(self, apply_setup):
        """resources=[] → all summary counts are 0."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        summary = resp.json()["summary"]
        assert summary.get("created", 0) == 0
        assert summary.get("updated", 0) == 0
        assert summary.get("failed", 0) == 0
        assert summary.get("unchanged", 0) == 0

    @pytest.mark.asyncio
    async def test_empty_dry_run_http_200(self, apply_setup):
        """resources=[], dry_run=True → HTTP 200, dry_run=True in response."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [], "dry_run": True},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200
        assert resp.json()["dry_run"] is True


# ---------------------------------------------------------------------------
# Auth checks
# ---------------------------------------------------------------------------


class TestAuth:
    @pytest.mark.asyncio
    async def test_no_token_rejected(self, apply_setup):
        """Request without Authorization header → 401 or 403."""
        client, _, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Unauth")], "dry_run": False},
            headers=_org_header(org_id),
        )
        assert resp.status_code in (401, 403), (
            f"Expected 401 or 403, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_bad_jwt_rejected(self, apply_setup):
        """Invalid JWT token → 401 or 403."""
        client, _, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Unauth")], "dry_run": False},
            headers={
                "Authorization": "Bearer this.is.not.a.valid.jwt",
                **_org_header(org_id),
            },
        )
        assert resp.status_code in (401, 403), (
            f"Expected 401 or 403, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_empty_bearer_rejected(self, apply_setup):
        """Empty Bearer token → 401 or 403."""
        client, _, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Unauth")], "dry_run": False},
            headers={"Authorization": "Bearer ", **_org_header(org_id)},
        )
        assert resp.status_code in (401, 403), (
            f"Expected 401 or 403, got {resp.status_code}: {resp.text}"
        )


# ---------------------------------------------------------------------------
# Summary calculation correctness
# ---------------------------------------------------------------------------


class TestSummaryCalculation:
    @pytest.mark.asyncio
    async def test_two_updates_summary(self, apply_setup):
        """After [updated, updated]: updated=2, created=0, failed=0."""
        client, user_id, org_id, _ = apply_setup

        # First round — create both
        r1 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("U1"),
                    _dashboard_envelope("U2"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        ids = [r["id"] for r in r1.json()["results"]]

        # Second round — re-apply with same ids
        r2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("U1", env_id=ids[0]),
                    _dashboard_envelope("U2", env_id=ids[1]),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        summary = r2.json()["summary"]
        assert summary.get("updated", 0) == 2
        assert summary.get("created", 0) == 0
        assert summary.get("failed", 0) == 0

    @pytest.mark.asyncio
    async def test_mixed_create_update_summary(self, apply_setup):
        """Create 2 dashboards; re-apply both → summary.updated=2, created=0."""
        client, user_id, org_id, _ = apply_setup

        r1 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("Mix1"),
                    _dashboard_envelope("Mix2"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert r1.json()["summary"]["created"] == 2
        ids = [r["id"] for r in r1.json()["results"]]

        r2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("Mix1", env_id=ids[0]),
                    _dashboard_envelope("Mix2", env_id=ids[1]),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        summary2 = r2.json()["summary"]
        assert summary2.get("updated", 0) == 2
        assert summary2.get("created", 0) == 0

    @pytest.mark.asyncio
    async def test_summary_all_zeros_for_empty(self, apply_setup):
        """Empty batch → all summary counters are 0."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        summary = resp.json()["summary"]
        for key in ("created", "updated", "failed"):
            assert summary.get(key, 0) == 0, f"Expected 0 for {key}, got {summary}"


# ---------------------------------------------------------------------------
# Large batch
# ---------------------------------------------------------------------------


class TestLargeBatch:
    @pytest.mark.asyncio
    async def test_10_dashboards_all_created(self, apply_setup):
        """10 dashboards in one request → all created, HTTP 200, summary.created=10."""
        client, user_id, org_id, repo = apply_setup
        resources = [_dashboard_envelope(f"Dash-{i}") for i in range(10)]
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": resources, "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["results"]) == 10
        assert all(r["action"] == "created" for r in data["results"])
        assert data["summary"]["created"] == 10
        assert data["summary"]["failed"] == 0

        # Sanity-check repo
        boards = await repo.list("boards", org_id, None)
        assert len(boards) == 10

    @pytest.mark.asyncio
    async def test_5_same_kind_all_created(self, apply_setup):
        """5 resources of the same kind → all 5 created."""
        client, user_id, org_id, _ = apply_setup
        resources = [_connector_envelope(f"Conn-{i}") for i in range(5)]
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": resources, "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["created"] == 5
        assert len(data["results"]) == 5


# ---------------------------------------------------------------------------
# Resource id propagation
# ---------------------------------------------------------------------------


class TestResourceIdPropagation:
    @pytest.mark.asyncio
    async def test_created_resource_has_non_null_id(self, apply_setup):
        """Created resource has a non-null 'id' in the result."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("HasId")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        result = resp.json()["results"][0]
        assert result.get("id"), f"Expected non-null id, got: {result.get('id')!r}"

    @pytest.mark.asyncio
    async def test_created_resource_id_is_valid_uuid(self, apply_setup):
        """Created resource id is a valid UUID string."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("UUIDTest")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        result_id = resp.json()["results"][0]["id"]
        try:
            uuid.UUID(str(result_id))
        except (ValueError, AttributeError) as exc:
            pytest.fail(f"Result id {result_id!r} is not a valid UUID: {exc}")

    @pytest.mark.asyncio
    async def test_id_stable_across_reapply(self, apply_setup):
        """Re-applying with the same id returns the same id in response."""
        client, user_id, org_id, _ = apply_setup
        r1 = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Stable")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        original_id = r1.json()["results"][0]["id"]

        r2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("Stable", env_id=original_id)],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        returned_id = r2.json()["results"][0]["id"]
        assert returned_id == original_id, (
            f"ID changed on re-apply: {original_id!r} → {returned_id!r}"
        )


# ---------------------------------------------------------------------------
# Connector kind
# ---------------------------------------------------------------------------


class TestConnectorKind:
    @pytest.mark.asyncio
    async def test_connector_apply_creates(self, apply_setup):
        """Connector envelope is handled → action='created'."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_connector_envelope("PG Prod")],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200
        result = resp.json()["results"][0]
        assert result["action"] == "created"
        assert result["kind"] == "connector"

    @pytest.mark.asyncio
    async def test_connector_reapply_updates(self, apply_setup):
        """Connector re-applied with same id → action='updated'."""
        client, user_id, org_id, _ = apply_setup
        r1 = await client.post(
            "/api/v1/apply",
            json={"resources": [_connector_envelope("PG Dev")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        conn_id = r1.json()["results"][0]["id"]

        r2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_connector_envelope("PG Dev", env_id=conn_id)],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert r2.json()["results"][0]["action"] == "updated"


# ---------------------------------------------------------------------------
# Org field in body vs. X-Org-Id header
# ---------------------------------------------------------------------------


class TestOrgFieldInBody:
    @pytest.mark.asyncio
    async def test_x_org_id_header_takes_precedence(self, apply_setup):
        """X-Org-Id header overrides any 'org' field in the request body."""
        client, user_id, org_id, repo = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("OrgField")],
                "org": "some-other-org-that-does-not-exist",
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        # Should succeed because X-Org-Id matches the seeded org
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["summary"]["created"] == 1

        # Resource must be visible under the header org, not the body org
        boards = await repo.list("boards", org_id, None)
        assert len(boards) == 1

    @pytest.mark.asyncio
    async def test_body_org_field_is_informational_only(self, apply_setup):
        """Providing 'org' in body alongside correct X-Org-Id header works normally."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("BodyOrg")],
                "org": org_id,  # matches header, no conflict
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp.status_code == 200
        assert resp.json()["summary"]["created"] == 1


# ---------------------------------------------------------------------------
# Error field presence invariant
# ---------------------------------------------------------------------------


class TestErrorFieldInvariant:
    @pytest.mark.asyncio
    async def test_failed_results_always_have_error(self, apply_setup):
        """Every result with action='failed' must have a truthy 'error' field."""
        client, user_id, org_id, _ = apply_setup
        resources = [
            _unknown_envelope("Bad1"),
            _dashboard_envelope("Good"),
            _unknown_envelope("Bad2"),
        ]
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": resources, "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        for result in resp.json()["results"]:
            if result["action"] == "failed":
                assert result.get("error"), (
                    f"Failed result missing 'error': {result}"
                )

    @pytest.mark.asyncio
    async def test_successful_results_have_no_truthy_error(self, apply_setup):
        """Results with action='created' or 'updated' must not carry truthy 'error'."""
        client, user_id, org_id, _ = apply_setup

        # Create two dashboards
        r1 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("E1"),
                    _dashboard_envelope("E2"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        ids = [r["id"] for r in r1.json()["results"]]

        # Re-apply to trigger 'updated'
        r2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("E1", env_id=ids[0]),
                    _dashboard_envelope("E2", env_id=ids[1]),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        for result in r1.json()["results"] + r2.json()["results"]:
            assert result["action"] in ("created", "updated")
            assert not result.get("error"), (
                f"Successful result must not carry truthy 'error': {result}"
            )


# ---------------------------------------------------------------------------
# Kind and name fields in results
# ---------------------------------------------------------------------------


class TestResultFields:
    @pytest.mark.asyncio
    async def test_result_kind_matches_envelope_kind(self, apply_setup):
        """Each result 'kind' field matches the envelope's 'kind'."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _dashboard_envelope("D"),
                    _connector_envelope("C"),
                ],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        results = resp.json()["results"]
        kinds = {r["kind"] for r in results}
        assert "dashboard" in kinds
        assert "connector" in kinds

    @pytest.mark.asyncio
    async def test_result_name_matches_envelope_name(self, apply_setup):
        """Each result 'name' matches the envelope metadata.name."""
        client, user_id, org_id, _ = apply_setup
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("MyName")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        result = resp.json()["results"][0]
        assert result.get("name") == "MyName", (
            f"Expected name='MyName', got {result.get('name')!r}"
        )

    @pytest.mark.asyncio
    async def test_results_count_matches_resources_count(self, apply_setup):
        """Number of results always equals number of resources submitted."""
        client, user_id, org_id, _ = apply_setup
        resources = [
            _dashboard_envelope("R1"),
            _connector_envelope("R2"),
            _unknown_envelope("R3"),
            _dashboard_envelope("R4"),
        ]
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": resources, "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        results = resp.json()["results"]
        assert len(results) == len(resources), (
            f"Result count {len(results)} != resource count {len(resources)}"
        )
