"""B3 — Governed write-back engine tests.

Coverage
--------
1.  Idempotent submit: same idempotency_key → one apply, existing record returned.
2.  Dry-run mode: does NOT commit rows; returns diff with dry_run=True.
3.  RBAC: 'viewer' role denied on submit (403); writer roles (owner/admin/member) pass.
4.  Approval gate: approval_required=True → state='pending_approval'; write NOT applied.
5.  Approval approve: moves to 'committed'; connector_write_fn called exactly once.
6.  Approval reject: moves to 'rejected'; connector_write_fn NOT called.
7.  Approval edit: replaces rows, then commits; connector_write_fn called with new rows.
8.  Cross-org isolation: org B cannot see org A's write-back (store.get returns None).
9.  Terminal state guard: approving a committed record raises 409.
10. Auto-commit (no approval): state immediately 'committed' after submit.
11. Connector_write_fn failure → state='failed', error captured.
12. Route: POST /flows/writeback returns 403 for viewer via HTTP.
13. Route: POST /flows/writeback returns 201 for member via HTTP.
14. Route: POST /flows/writeback (dry_run=True) returns dry-run diff.
15. Route: POST /flows/writeback/{id}/approval returns 403 for non-approver (member).
16. Route: POST /flows/writeback/{id}/approval approve succeeds for owner.
17. Route: GET /flows/writeback lists records for org.
18. Route: GET /flows/writeback/{id} returns 404 for cross-org id.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.connectors.writeback import (
    InMemoryWritebackStore,
    _require_approver_role,
    _require_writer_role,
    approve_writeback,
    dry_run_writeback,
    get_writeback_store,
    set_writeback_store,
    submit_writeback,
)
from app.errors import AppError

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _idem() -> str:
    """Return a unique idempotency key."""
    return str(uuid.uuid4())


def _target(obj: str = "raw.orders") -> dict:
    return {"connector_id": "conn1", "object": obj}


def _noop_write(rows: list, target: dict, mode: str) -> dict:
    """Synchronous no-op write function that records calls."""
    return {
        "rows_written": len(rows),
        "mode": mode,
        "target_object": target.get("object", ""),
    }


# ---------------------------------------------------------------------------
# 1. Idempotent submit: same idempotency_key → one apply, same record returned
# ---------------------------------------------------------------------------


async def test_idempotent_submit_returns_existing():
    store = InMemoryWritebackStore()
    key = _idem()
    rows = [{"id": 1}, {"id": 2}]

    calls = []

    def tracking_write(r, t, m):
        calls.append(len(r))
        return {"rows_written": len(r), "mode": m, "target_object": t.get("object", "")}

    r1 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=rows,
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
        connector_write_fn=tracking_write,
        store=store,
    )
    r2 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=rows,
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
        connector_write_fn=tracking_write,
        store=store,
    )

    # Same record returned (idempotent)
    assert r1["id"] == r2["id"]
    # Connector called exactly once
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 2. Dry-run mode: does NOT commit rows; returns diff with dry_run=True
# ---------------------------------------------------------------------------


async def test_dry_run_does_not_commit():
    rows = [{"id": 10}, {"id": 11}]
    diff = dry_run_writeback(rows=rows, target=_target(), mode="append")

    assert diff["dry_run"] is True
    assert diff["row_count"] == 2
    assert diff["rows"] == rows
    assert diff["target_object"] == "raw.orders"
    assert diff["mode"] == "append"


# ---------------------------------------------------------------------------
# 3. RBAC: viewer denied; owner/admin/member pass
# ---------------------------------------------------------------------------


async def test_rbac_writer_role_viewer_denied():
    with pytest.raises(AppError) as exc_info:
        _require_writer_role("viewer")
    assert exc_info.value.status == 403


async def test_rbac_writer_role_owner_passes():
    _require_writer_role("owner")  # no exception


async def test_rbac_writer_role_admin_passes():
    _require_writer_role("admin")


async def test_rbac_writer_role_member_passes():
    _require_writer_role("member")


async def test_rbac_writer_role_none_denied():
    with pytest.raises(AppError) as exc_info:
        _require_writer_role(None)
    assert exc_info.value.status == 403


# ---------------------------------------------------------------------------
# 4. Approval gate: approval_required=True → pending_approval; write NOT applied
# ---------------------------------------------------------------------------


async def test_approval_gate_blocks_write():
    store = InMemoryWritebackStore()
    calls = []

    def write_fn(r, t, m):
        calls.append(True)
        return {"rows_written": len(r)}

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=write_fn,
        store=store,
    )

    assert record["state"] == "pending_approval"
    assert len(calls) == 0  # connector NOT called


# ---------------------------------------------------------------------------
# 5. Approval approve: moves to 'committed'; connector_write_fn called once
# ---------------------------------------------------------------------------


async def test_approval_approve_commits():
    store = InMemoryWritebackStore()
    calls = []

    def write_fn(r, t, m):
        calls.append(len(r))
        return {"rows_written": len(r), "mode": m}

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}, {"id": 2}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=write_fn,
        store=store,
    )
    assert record["state"] == "pending_approval"

    approved = await approve_writeback(
        org_id="orgA",
        wb_id=record["id"],
        action="approve",
        approver_id="approver1",
        connector_write_fn=write_fn,
        store=store,
    )

    assert approved["state"] == "committed"
    assert approved["approved_by"] == "approver1"
    assert len(calls) == 1
    assert calls[0] == 2


# ---------------------------------------------------------------------------
# 6. Approval reject: moves to 'rejected'; connector_write_fn NOT called
# ---------------------------------------------------------------------------


async def test_approval_reject():
    store = InMemoryWritebackStore()
    calls = []

    def write_fn(r, t, m):
        calls.append(True)
        return {}

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=write_fn,
        store=store,
    )

    rejected = await approve_writeback(
        org_id="orgA",
        wb_id=record["id"],
        action="reject",
        approver_id="approver1",
        connector_write_fn=write_fn,
        store=store,
    )

    assert rejected["state"] == "rejected"
    assert len(calls) == 0  # NOT called


# ---------------------------------------------------------------------------
# 7. Approval edit: replaces rows, then commits
# ---------------------------------------------------------------------------


async def test_approval_edit_replaces_rows_then_commits():
    store = InMemoryWritebackStore()
    written_rows: list[list] = []

    def write_fn(r, t, m):
        written_rows.append(list(r))
        return {"rows_written": len(r)}

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1, "value": "original"}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=write_fn,
        store=store,
    )

    new_rows = [{"id": 1, "value": "edited"}, {"id": 2, "value": "extra"}]
    edited = await approve_writeback(
        org_id="orgA",
        wb_id=record["id"],
        action="edit",
        approver_id="approver1",
        connector_write_fn=write_fn,
        store=store,
        rows_override=new_rows,
    )

    assert edited["state"] == "committed"
    assert len(written_rows) == 1
    assert written_rows[0] == new_rows


# ---------------------------------------------------------------------------
# 8. Cross-org isolation: org B cannot see org A's write-back
# ---------------------------------------------------------------------------


async def test_cross_org_isolation():
    store = InMemoryWritebackStore()

    record = await store.create(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
    )

    # Org B lookup returns None
    result = await store.get(org_id="orgB", wb_id=record["id"])
    assert result is None


async def test_cross_org_idempotency_isolation():
    store = InMemoryWritebackStore()
    key = "shared-key"

    # Create a record for orgA
    await store.create(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
    )

    # OrgB using the same idempotency_key gets None (no clash)
    result = await store.get_by_idempotency_key(org_id="orgB", idempotency_key=key)
    assert result is None


# ---------------------------------------------------------------------------
# 9. Terminal state guard: approving a committed record raises 409
# ---------------------------------------------------------------------------


async def test_terminal_state_guard():
    store = InMemoryWritebackStore()

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,  # auto-commit
        connector_write_fn=_noop_write,
        store=store,
    )
    assert record["state"] == "committed"

    with pytest.raises(AppError) as exc_info:
        await approve_writeback(
            org_id="orgA",
            wb_id=record["id"],
            action="approve",
            approver_id="approver1",
            connector_write_fn=_noop_write,
            store=store,
        )
    assert exc_info.value.status == 409


# ---------------------------------------------------------------------------
# 10. Auto-commit (no approval): state immediately 'committed'
# ---------------------------------------------------------------------------


async def test_auto_commit_without_approval():
    store = InMemoryWritebackStore()

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}, {"id": 2}],
        target=_target(),
        mode="overwrite",
        created_by="user1",
        approval_required=False,
        connector_write_fn=_noop_write,
        store=store,
    )

    assert record["state"] == "committed"
    assert record["result"] is not None
    assert record["result"]["rows_written"] == 2


# ---------------------------------------------------------------------------
# 11. Connector_write_fn failure → state='failed', error captured
# ---------------------------------------------------------------------------


async def test_connector_write_failure_sets_failed_state():
    store = InMemoryWritebackStore()

    def failing_write(r, t, m):
        raise RuntimeError("Warehouse unavailable")

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
        connector_write_fn=failing_write,
        store=store,
    )

    assert record["state"] == "failed"
    assert "Warehouse unavailable" in record["error"]


# ---------------------------------------------------------------------------
# 12–18. Route-level tests (HTTP via FastAPI TestClient)
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict:
    from app.auth.jwt import mint_access_token  # noqa: PLC0415
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def wb_client(app, fake_db):
    """HTTP client with InMemoryRepo injected, write-back store reset between tests."""
    # Ensure routes module is imported (self-registers on api_router).
    import importlib as _importlib
    _importlib.import_module("app.routes.flows")

    from app.repos.memory import InMemoryRepo  # noqa: PLC0415
    from app.repos.provider import set_repo  # noqa: PLC0415

    # Fresh in-memory writeback store per test
    wb_store = InMemoryWritebackStore()
    set_writeback_store(wb_store)

    repo = InMemoryRepo()
    set_repo(repo)

    # Alice (owner)
    alice_id = str(uuid.uuid4())
    alice_org = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(user_id=alice_id, email="alice@example.com")
    repo.seed_org_member(org_id=alice_org, user_id=alice_id, role="owner")

    # Bob (member — writer but not approver)
    bob_id = str(uuid.uuid4())
    fake_db.users[bob_id] = _make_user(user_id=bob_id, email="bob@example.com")
    repo.seed_org_member(org_id=alice_org, user_id=bob_id, role="member")

    # Carol (viewer — read-only)
    carol_id = str(uuid.uuid4())
    fake_db.users[carol_id] = _make_user(user_id=carol_id, email="carol@example.com")
    repo.seed_org_member(org_id=alice_org, user_id=carol_id, role="viewer")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, alice_id, bob_id, carol_id, alice_org, wb_store, repo

    set_repo(None)
    set_writeback_store(None)


# 12. POST /flows/writeback — 403 for viewer
@pytest.mark.asyncio
async def test_route_submit_viewer_forbidden(wb_client):
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": _idem(),
            "rows": [{"id": 1}],
            "target": {"connector_id": "c1", "object": "raw.t"},
            "mode": "append",
            "approval_required": False,
            "dry_run": False,
            "meta": {},
        },
        headers=_auth_headers(carol_id),
    )
    assert resp.status_code == 403


# 13. POST /flows/writeback — 201 for member
@pytest.mark.asyncio
async def test_route_submit_member_succeeds(wb_client):
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": _idem(),
            "rows": [{"id": 1}],
            "target": {"connector_id": "c1", "object": "raw.t"},
            "mode": "append",
            "approval_required": False,
            "dry_run": False,
            "meta": {},
        },
        headers=_auth_headers(bob_id),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "committed"


# 14. POST /flows/writeback (dry_run=True) returns dry-run diff
@pytest.mark.asyncio
async def test_route_submit_dry_run(wb_client):
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": _idem(),
            "rows": [{"id": 1}, {"id": 2}],
            "target": {"connector_id": "c1", "object": "raw.orders"},
            "mode": "overwrite",
            "approval_required": False,
            "dry_run": True,
            "meta": {},
        },
        headers=_auth_headers(alice_id),
    )
    # POST /writeback always returns 201 even for dry_run (the response body
    # signals dry_run=True; callers inspect the body, not the status code).
    assert resp.status_code == 201
    body = resp.json()
    assert body["dry_run"] is True
    assert body["row_count"] == 2
    assert body["target_object"] == "raw.orders"


# 15. POST /flows/writeback/{id}/approval — 403 for member (not approver)
@pytest.mark.asyncio
async def test_route_approval_member_forbidden(wb_client):
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    # Alice (owner) submits a gated write-back
    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": _idem(),
            "rows": [{"id": 1}],
            "target": {"connector_id": "c1", "object": "raw.t"},
            "mode": "append",
            "approval_required": True,
            "dry_run": False,
            "meta": {},
        },
        headers=_auth_headers(alice_id),
    )
    assert resp.status_code == 201
    wb_id = resp.json()["id"]

    # Bob (member) tries to approve — should get 403
    resp2 = await client.post(
        f"/api/v1/flows/writeback/{wb_id}/approval",
        json={"action": "approve"},
        headers=_auth_headers(bob_id),
    )
    assert resp2.status_code == 403


# 16. POST /flows/writeback/{id}/approval — approve succeeds for owner
@pytest.mark.asyncio
async def test_route_approval_owner_approve(wb_client):
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": _idem(),
            "rows": [{"id": 1}],
            "target": {"connector_id": "c1", "object": "raw.t"},
            "mode": "append",
            "approval_required": True,
            "dry_run": False,
            "meta": {},
        },
        headers=_auth_headers(alice_id),
    )
    assert resp.status_code == 201
    wb_id = resp.json()["id"]
    assert resp.json()["state"] == "pending_approval"

    # Alice (owner) approves
    resp2 = await client.post(
        f"/api/v1/flows/writeback/{wb_id}/approval",
        json={"action": "approve"},
        headers=_auth_headers(alice_id),
    )
    assert resp2.status_code == 200
    assert resp2.json()["state"] == "committed"


# 17. GET /flows/writeback — lists records for the org
@pytest.mark.asyncio
async def test_route_list_writebacks(wb_client):
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    for i in range(3):
        await client.post(
            "/api/v1/flows/writeback",
            json={
                "idempotency_key": _idem(),
                "rows": [{"id": i}],
                "target": {"connector_id": "c1", "object": "raw.t"},
                "mode": "append",
                "approval_required": False,
                "dry_run": False,
                "meta": {},
            },
            headers=_auth_headers(alice_id),
        )

    resp = await client.get("/api/v1/flows/writeback", headers=_auth_headers(alice_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert len(body["writebacks"]) == 3


# 18. GET /flows/writeback/{id} — 404 for cross-org id
@pytest.mark.asyncio
async def test_route_get_writeback_cross_org_404(wb_client):
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    # Plant a record directly for a different org (bypass route)
    other_org = str(uuid.uuid4())
    other_record = await wb_store.create(
        org_id=other_org,
        idempotency_key=_idem(),
        rows=[{"id": 99}],
        target=_target(),
        mode="append",
        created_by="other-user",
        approval_required=False,
    )

    resp = await client.get(
        f"/api/v1/flows/writeback/{other_record['id']}",
        headers=_auth_headers(alice_id),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Extra: RBAC approver role checks
# ---------------------------------------------------------------------------


async def test_rbac_approver_role_member_denied():
    with pytest.raises(AppError) as exc_info:
        _require_approver_role("member")
    assert exc_info.value.status == 403


async def test_rbac_approver_role_owner_passes():
    _require_approver_role("owner")


async def test_rbac_approver_role_admin_passes():
    _require_approver_role("admin")


# ---------------------------------------------------------------------------
# 19. Row cap: submit with rows over cap → 400; within cap → 201
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_submit_over_row_cap_rejected(wb_client, monkeypatch):
    """Submitting more rows than the server cap returns 400."""
    import app.routes.flows as flows_module  # noqa: PLC0415

    monkeypatch.setattr(flows_module, "_MAX_WRITEBACK_ROWS", 5)

    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    over_cap_rows = [{"id": i} for i in range(6)]  # 6 > cap of 5
    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": _idem(),
            "rows": over_cap_rows,
            "target": {"connector_id": "c1", "object": "raw.t"},
            "mode": "append",
            "approval_required": False,
            "dry_run": False,
            "meta": {},
        },
        headers=_auth_headers(alice_id),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_route_submit_within_row_cap_succeeds(wb_client, monkeypatch):
    """Submitting rows at or below the cap succeeds with 201."""
    import app.routes.flows as flows_module  # noqa: PLC0415

    monkeypatch.setattr(flows_module, "_MAX_WRITEBACK_ROWS", 5)

    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    within_cap_rows = [{"id": i} for i in range(5)]  # exactly 5 == cap
    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": _idem(),
            "rows": within_cap_rows,
            "target": {"connector_id": "c1", "object": "raw.t"},
            "mode": "append",
            "approval_required": False,
            "dry_run": False,
            "meta": {},
        },
        headers=_auth_headers(alice_id),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["state"] == "committed"
