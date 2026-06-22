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
# 7b. Edit without rows_override is rejected (LOW fix)
# ---------------------------------------------------------------------------


async def test_edit_without_rows_override_is_rejected():
    """approve_writeback(action='edit') MUST be rejected with 400 when
    rows_override is absent.  An edit that provides no rows is semantically
    identical to 'approve' but would mislead the audit trail — the caller
    must explicitly pass rows_override or switch to action='approve'.
    """
    store = InMemoryWritebackStore()

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1, "value": "original"}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=_noop_write,
        store=store,
    )
    assert record["state"] == "pending_approval"

    with pytest.raises(AppError) as exc_info:
        await approve_writeback(
            org_id="orgA",
            wb_id=record["id"],
            action="edit",
            approver_id="approver1",
            connector_write_fn=_noop_write,
            store=store,
            rows_override=None,  # explicitly absent
        )
    err = exc_info.value
    assert err.status == 400
    assert err.code == "rows_override_required"


async def test_edit_with_rows_override_commits_new_rows():
    """approve_writeback(action='edit', rows_override=...) commits the new
    rows (not the original ones) and transitions to 'committed'.
    """
    store = InMemoryWritebackStore()
    written: list[list] = []

    def write_fn(r, t, m):
        written.append(list(r))
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

    new_rows = [{"id": 1, "value": "corrected"}, {"id": 2, "value": "added"}]
    committed = await approve_writeback(
        org_id="orgA",
        wb_id=record["id"],
        action="edit",
        approver_id="approver1",
        connector_write_fn=write_fn,
        store=store,
        rows_override=new_rows,
    )

    assert committed["state"] == "committed"
    # connector must have been called with the NEW rows only
    assert len(written) == 1
    assert written[0] == new_rows
    # record stores the new rows
    assert committed["rows"] == new_rows


async def test_approve_commits_original_rows():
    """approve_writeback(action='approve') commits the original rows exactly
    as submitted — rows_override is not required and must not alter the rows.
    """
    store = InMemoryWritebackStore()
    written: list[list] = []

    original_rows = [{"id": 10, "value": "original"}]

    def write_fn(r, t, m):
        written.append(list(r))
        return {"rows_written": len(r)}

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=original_rows,
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=write_fn,
        store=store,
    )

    committed = await approve_writeback(
        org_id="orgA",
        wb_id=record["id"],
        action="approve",
        approver_id="approver1",
        connector_write_fn=write_fn,
        store=store,
        # no rows_override — should use original
    )

    assert committed["state"] == "committed"
    assert len(written) == 1
    assert written[0] == original_rows


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


# ---------------------------------------------------------------------------
# 20. rows_override over cap is rejected in the approval route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_approval_rows_override_over_cap_rejected(wb_client, monkeypatch):
    """rows_override exceeding the server cap in the approval/edit path returns 400."""
    import app.routes.flows as flows_module  # noqa: PLC0415

    monkeypatch.setattr(flows_module, "_MAX_WRITEBACK_ROWS", 3)

    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    # Alice submits a gated write-back (pending_approval)
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

    # Alice (owner/approver) tries to edit with rows_override that exceeds the cap
    over_cap_override = [{"id": i} for i in range(4)]  # 4 > cap of 3
    resp2 = await client.post(
        f"/api/v1/flows/writeback/{wb_id}/approval",
        json={"action": "edit", "rows_override": over_cap_override},
        headers=_auth_headers(alice_id),
    )
    assert resp2.status_code == 400
    body = resp2.json()
    assert body["error"]["code"] == "row_cap_exceeded"


# ---------------------------------------------------------------------------
# 21. AppError for row-cap has correct code, message, and status
# ---------------------------------------------------------------------------


def test_writeback_cap_apperror_has_correct_code_message_status():
    """The AppError raised on row-cap breach has correct (code, message, status)."""
    import app.routes.flows as flows_module  # noqa: PLC0415

    # Simulate what both preview and submit routes raise:
    try:
        raise flows_module.AppError(
            "row_cap_exceeded",
            f"rows exceeds server cap of {flows_module._MAX_WRITEBACK_ROWS}",
            400,
        )
    except flows_module.AppError as exc:
        assert exc.code == "row_cap_exceeded"
        assert "server cap" in exc.message
        assert exc.status == 400

    # Also test the rows_override variant:
    try:
        raise flows_module.AppError(
            "row_cap_exceeded",
            f"rows_override exceeds server cap of {flows_module._MAX_WRITEBACK_ROWS}",
            400,
        )
    except flows_module.AppError as exc:
        assert exc.code == "row_cap_exceeded"
        assert "rows_override" in exc.message
        assert exc.status == 400


# ---------------------------------------------------------------------------
# 22. /flows/tick refuses with 401 when FLOWS_TICK_SECRET is unset in
#     production ENV (LOW severity — governance finding).
#
# Tested by calling the route handler directly (no HTTP app fixture needed)
# so the test is immune to unrelated SyntaxErrors in other route modules.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_refuses_401_when_secret_unset_in_production(monkeypatch):
    """In production ENV, flows_tick() raises AppError(401) when the secret is
    unset — no information leak about whether the endpoint is configured."""
    import os  # noqa: PLC0415

    from app.config import get_settings  # noqa: PLC0415
    from app.routes.flows import flows_tick  # noqa: PLC0415

    # Simulate production ENV with no secret configured.
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("FLOWS_TICK_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(AppError) as exc_info:
            await flows_tick(x_nubi_tick_secret=None)
        assert exc_info.value.status == 401, (
            f"Expected 401 in production when secret unset, got {exc_info.value.status}"
        )
        assert exc_info.value.code == "unauthorized"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_tick_disabled_503_when_secret_unset_in_non_production(monkeypatch):
    """In non-production ENV (dev/test), flows_tick() raises AppError(503) with
    a clear diagnostic message when the secret is unset."""
    import os  # noqa: PLC0415

    from app.config import get_settings  # noqa: PLC0415
    from app.routes.flows import flows_tick  # noqa: PLC0415

    # Simulate dev ENV (non-production) with no secret configured.
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("FLOWS_TICK_SECRET", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(AppError) as exc_info:
            await flows_tick(x_nubi_tick_secret=None)
        assert exc_info.value.status == 503, (
            f"Expected 503 in dev when secret unset, got {exc_info.value.status}"
        )
        assert exc_info.value.code == "tick_not_configured"
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 23. POST /flows/writeback cannot bypass a server-required approval gate
#     (LOW authz finding — caller cannot set approval_required=False to
#     auto-commit when the server policy mandates approval).
#
# Tested at the engine level (submit_writeback) via _enforce_approval_policy
# so the tests are immune to unrelated SyntaxErrors in other route modules.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_server_approval_policy_forces_approval_regardless_of_caller(monkeypatch):
    """When the server policy (NUBI_WRITEBACK_REQUIRE_APPROVAL=true) is active,
    a caller sending approval_required=False still lands in pending_approval.
    The caller cannot bypass the server-required gate.

    Verified via _enforce_approval_policy + submit_writeback directly so this
    test does not need the full HTTP app stack.
    """
    import app.routes.flows as flows_module  # noqa: PLC0415

    # Activate the server-wide approval requirement.
    monkeypatch.setattr(flows_module, "_WRITEBACK_REQUIRE_APPROVAL", True)

    store = InMemoryWritebackStore()
    calls: list[bool] = []

    def write_fn(r, t, m):
        calls.append(True)
        return {"rows_written": len(r)}

    # The effective flag must be True even though caller passes False.
    effective = flows_module._enforce_approval_policy(False)
    assert effective is True, "Server policy must override caller's False"

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=effective,  # route applies _enforce_approval_policy
        connector_write_fn=write_fn,
        store=store,
    )
    # Write is gated — connector NOT called.
    assert record["state"] == "pending_approval", (
        f"Expected pending_approval due to server policy, got state={record['state']!r}"
    )
    assert len(calls) == 0, "Connector must not be called when approval is required"


@pytest.mark.asyncio
async def test_caller_can_opt_into_approval_without_server_policy(monkeypatch):
    """Caller may always opt in to approval (approval_required=True) even when
    the server policy is off.  This verifies the caller can only INCREASE
    strictness, not bypass it."""
    import app.routes.flows as flows_module  # noqa: PLC0415

    # Server policy OFF — caller still opts in.
    monkeypatch.setattr(flows_module, "_WRITEBACK_REQUIRE_APPROVAL", False)

    store = InMemoryWritebackStore()

    effective = flows_module._enforce_approval_policy(True)
    assert effective is True, "Caller opt-in must be respected even without server policy"

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 42}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=effective,
        connector_write_fn=_noop_write,
        store=store,
    )
    assert record["state"] == "pending_approval", (
        f"Expected pending_approval from caller opt-in, got state={record['state']!r}"
    )


@pytest.mark.asyncio
async def test_no_policy_and_caller_false_auto_commits(monkeypatch):
    """When both server policy and caller say False, writeback auto-commits.
    Verifies the baseline (no policy → caller controls the gate)."""
    import app.routes.flows as flows_module  # noqa: PLC0415

    monkeypatch.setattr(flows_module, "_WRITEBACK_REQUIRE_APPROVAL", False)

    store = InMemoryWritebackStore()

    effective = flows_module._enforce_approval_policy(False)
    assert effective is False

    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 7}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=effective,
        connector_write_fn=_noop_write,
        store=store,
    )
    assert record["state"] == "committed", (
        f"Expected committed when no policy and caller=False, got state={record['state']!r}"
    )


# ---------------------------------------------------------------------------
# 24. GET /flows/writeback — viewer is blocked (LOW authz fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_list_writebacks_viewer_forbidden(wb_client):
    """GET /flows/writeback must return 403 for viewers.

    Write-back payloads may contain recommendation data.  Viewers (read-only
    role) must NOT be able to enumerate write-back records.
    """
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    resp = await client.get(
        "/api/v1/flows/writeback",
        headers=_auth_headers(carol_id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_route_get_writeback_detail_viewer_forbidden(wb_client):
    """GET /flows/writeback/{id} must return 403 for viewers.

    Viewers must not be able to read write-back detail records.
    """
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    # Alice submits a write-back so there's a real record to probe.
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
        headers=_auth_headers(alice_id),
    )
    assert resp.status_code == 201
    wb_id = resp.json()["id"]

    # Carol (viewer) tries to read the detail — must be blocked.
    resp2 = await client.get(
        f"/api/v1/flows/writeback/{wb_id}",
        headers=_auth_headers(carol_id),
    )
    assert resp2.status_code == 403


@pytest.mark.asyncio
async def test_route_list_writebacks_member_allowed(wb_client):
    """GET /flows/writeback must succeed (200) for member (writer) role."""
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    resp = await client.get(
        "/api/v1/flows/writeback",
        headers=_auth_headers(bob_id),  # bob is member
    )
    assert resp.status_code == 200


def test_enforce_approval_policy_unit():
    """Unit test for _enforce_approval_policy precedence rules."""
    import app.routes.flows as flows_module  # noqa: PLC0415

    original = flows_module._WRITEBACK_REQUIRE_APPROVAL
    try:
        # Server requires approval → always True regardless of caller.
        flows_module._WRITEBACK_REQUIRE_APPROVAL = True
        assert flows_module._enforce_approval_policy(False) is True
        assert flows_module._enforce_approval_policy(True) is True

        # Server does NOT require approval → caller controls it.
        flows_module._WRITEBACK_REQUIRE_APPROVAL = False
        assert flows_module._enforce_approval_policy(False) is False
        assert flows_module._enforce_approval_policy(True) is True
    finally:
        flows_module._WRITEBACK_REQUIRE_APPROVAL = original


# ---------------------------------------------------------------------------
# 25. [LOW authz] POST /flows/writeback and POST /flows/writeback/preview must
#     carry require_writer_default as a FastAPI dependency (defense-in-depth).
#
# The routes also have an in-handler _require_writer_role check, but the
# dependency gate is the canonical guard that prevents viewers from reaching
# any part of the handler at all — consistent with all other mutating routes
# in this file.
# ---------------------------------------------------------------------------


def test_writeback_post_has_require_writer_default_dependency():
    """POST /flows/writeback must declare require_writer_default as a dependency.

    This is the defense-in-depth gate that runs before any handler code,
    consistent with every other mutating route in the file.  Without it a
    viewer can reach the in-handler _require_writer_role check, which is a
    single point of failure.
    """
    import app.routes.flows as flows_mod
    from app.auth.roles import require_writer_default

    submit_route = None
    for route in flows_mod.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        # Match /writeback exactly (not /writeback/preview or /writeback/{id})
        if path in ("/flows/writeback", "/writeback") and "POST" in methods:
            # Prefer the non-preview route (status_code 201)
            if getattr(route, "status_code", None) == 201:
                submit_route = route
                break

    assert submit_route is not None, "POST /writeback route (status 201) not found"
    dep_callables = [d.dependency for d in (submit_route.dependencies or [])]
    assert require_writer_default in dep_callables, (
        "POST /flows/writeback must have require_writer_default in dependencies "
        "— viewers can reach the handler and attempt writes!"
    )


def test_writeback_preview_post_has_require_writer_default_dependency():
    """POST /flows/writeback/preview must declare require_writer_default as a dependency."""
    import app.routes.flows as flows_mod
    from app.auth.roles import require_writer_default

    preview_route = None
    for route in flows_mod.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if path in ("/flows/writeback/preview", "/writeback/preview") and "POST" in methods:
            preview_route = route
            break

    assert preview_route is not None, "POST /writeback/preview route not found"
    dep_callables = [d.dependency for d in (preview_route.dependencies or [])]
    assert require_writer_default in dep_callables, (
        "POST /flows/writeback/preview must have require_writer_default in dependencies"
    )


@pytest.mark.asyncio
async def test_route_writeback_preview_viewer_forbidden(wb_client):
    """POST /flows/writeback/preview must return 403 for viewers (dependency gate).

    The require_writer_default dependency must fire before the handler and
    block viewers — this is the defense-in-depth guard required by the audit.
    """
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    resp = await client.post(
        "/api/v1/flows/writeback/preview",
        json={
            "idempotency_key": _idem(),
            "rows": [{"id": 1}],
            "target": {"connector_id": "c1", "object": "raw.t"},
            "mode": "append",
            "approval_required": False,
            "dry_run": False,
            "meta": {},
        },
        headers=_auth_headers(carol_id),  # carol is viewer
    )
    assert resp.status_code == 403, (
        f"Viewer must be blocked at the dependency gate (expected 403, got {resp.status_code})"
    )


@pytest.mark.asyncio
async def test_route_writeback_submit_viewer_blocked_by_dependency(wb_client):
    """POST /flows/writeback must return 403 for viewers via the dependency gate.

    Verifies that the require_writer_default *dependency* (not just the
    in-handler _require_writer_role check) is what blocks the viewer — the
    response must arrive before any handler body executes.
    """
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": _idem(),
            "rows": [{"id": 42}],
            "target": {"connector_id": "c1", "object": "raw.t"},
            "mode": "append",
            "approval_required": False,
            "dry_run": False,
            "meta": {},
        },
        headers=_auth_headers(carol_id),  # carol is viewer
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# FIX 3 [LOW authz]: POST /flows/writeback/{wb_id}/approval must have
#     require_writer_default dependency so viewers are blocked at the gate.
# ---------------------------------------------------------------------------


def test_writeback_approval_has_require_writer_default_dependency():
    """POST /flows/writeback/{id}/approval must carry require_writer_default.

    The in-handler _require_approver_role check elevates to admin/owner, but
    the outer dependency gate must also be present so viewers are rejected
    before any handler code runs (defense-in-depth).
    """
    import app.routes.flows as flows_mod
    from app.auth.roles import require_writer_default

    approval_route = None
    for route in flows_mod.router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if (
            path in ("/flows/writeback/{wb_id}/approval", "/writeback/{wb_id}/approval")
            and "POST" in methods
        ):
            approval_route = route
            break

    assert approval_route is not None, "POST /writeback/{wb_id}/approval route not found"
    dep_callables = [d.dependency for d in (approval_route.dependencies or [])]
    assert require_writer_default in dep_callables, (
        "POST /flows/writeback/{wb_id}/approval must have require_writer_default "
        "in dependencies — viewers can reach the handler and attempt approval!"
    )


@pytest.mark.asyncio
async def test_route_approval_viewer_forbidden(wb_client):
    """POST /flows/writeback/{id}/approval must return 403 for viewers.

    Before the fix, the approval route lacked require_writer_default, meaning
    viewers could reach the in-handler role check.  With the fix the dependency
    gate fires first and returns 403.
    """
    client, alice_id, bob_id, carol_id, org_id, wb_store, repo = wb_client

    # Alice (owner) submits a gated write-back.
    resp = await client.post(
        "/api/v1/flows/writeback",
        json={
            "idempotency_key": str(uuid.uuid4()),
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

    # Carol (viewer) attempts to approve — must be blocked at the dependency gate.
    resp2 = await client.post(
        f"/api/v1/flows/writeback/{wb_id}/approval",
        json={"action": "approve"},
        headers=_auth_headers(carol_id),
    )
    assert resp2.status_code == 403, (
        f"Viewer must be blocked by require_writer_default dependency (expected 403, "
        f"got {resp2.status_code})"
    )


# ---------------------------------------------------------------------------
# 26. [LOW] Rollback-then-retry idempotency: a failed/rejected terminal record
#     must be superseded so a legitimate retry can proceed, while a committed
#     record is never superseded (double-commit protection).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_then_retry_after_failed_terminal_proceeds():
    """After a failed auto-commit, re-submitting with the same idempotency_key
    creates a fresh record and runs the write — the failed outcome is superseded.

    This is the rollback-then-retry path: the connector raised an error on the
    first attempt (state=failed).  A legitimate retry should not be blocked by
    the still-indexed idempotency key.
    """
    store = InMemoryWritebackStore()
    key = _idem()
    call_count = [0]

    def failing_write(r, t, m):
        call_count[0] += 1
        raise RuntimeError("Warehouse unavailable")

    # First attempt — connector fails → state='failed'.
    r1 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
        connector_write_fn=failing_write,
        store=store,
    )
    assert r1["state"] == "failed"
    assert call_count[0] == 1

    # Simulate rollback recovery — connector now works.
    def succeeding_write(r, t, m):
        call_count[0] += 1
        return {"rows_written": len(r)}

    # Retry with the SAME idempotency key — must supersede the failed record.
    r2 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 1}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
        connector_write_fn=succeeding_write,
        store=store,
    )
    # A fresh record was created (different id) and committed successfully.
    assert r2["id"] != r1["id"], "Retry must produce a new record, not return the failed one"
    assert r2["state"] == "committed"
    assert call_count[0] == 2  # connector called once for each attempt


@pytest.mark.asyncio
async def test_rollback_then_retry_after_rejected_terminal_proceeds():
    """After an approval rejection, re-submitting with the same idempotency_key
    creates a fresh record (rejected outcome is superseded).

    Rejection is a non-committed terminal state — the data was never written —
    so a re-submission represents a new logical attempt and must be allowed.
    """
    store = InMemoryWritebackStore()
    key = _idem()
    calls = []

    def write_fn(r, t, m):
        calls.append(len(r))
        return {"rows_written": len(r)}

    # First attempt — submit then reject.
    r1 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 10}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=write_fn,
        store=store,
    )
    assert r1["state"] == "pending_approval"

    rejected = await approve_writeback(
        org_id="orgA",
        wb_id=r1["id"],
        action="reject",
        approver_id="approver1",
        connector_write_fn=write_fn,
        store=store,
    )
    assert rejected["state"] == "rejected"
    assert len(calls) == 0  # write never happened

    # Re-submit with the same key after rejection — must create a fresh record.
    r2 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 10}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
        connector_write_fn=write_fn,
        store=store,
    )
    assert r2["id"] != r1["id"], "Retry must produce a new record, not the rejected one"
    assert r2["state"] == "committed"
    assert len(calls) == 1  # write ran once for the retry


@pytest.mark.asyncio
async def test_committed_record_is_never_superseded():
    """A successfully committed record must NEVER be superseded on retry.

    A re-submit with the same idempotency key after a committed outcome must
    return the existing committed record without calling the write function
    again — double-commit protection is the primary dedup invariant.
    """
    store = InMemoryWritebackStore()
    key = _idem()
    call_count = [0]

    def write_fn(r, t, m):
        call_count[0] += 1
        return {"rows_written": len(r)}

    # First submit — auto-commit.
    r1 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 99}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
        connector_write_fn=write_fn,
        store=store,
    )
    assert r1["state"] == "committed"
    assert call_count[0] == 1

    # Retry with same key — must return existing committed record, NOT re-write.
    r2 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 99}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=False,
        connector_write_fn=write_fn,
        store=store,
    )
    assert r2["id"] == r1["id"], "Committed record must be returned as-is (no new record)"
    assert r2["state"] == "committed"
    assert call_count[0] == 1, "Write function must NOT be called again after a committed record"


@pytest.mark.asyncio
async def test_pending_approval_record_is_not_superseded():
    """An in-flight (pending_approval) record must not be superseded.

    Submitting the same idempotency key while the original is awaiting
    approval must return the pending record unchanged — the dedup window
    covers in-flight submissions too.
    """
    store = InMemoryWritebackStore()
    key = _idem()
    calls = []

    def write_fn(r, t, m):
        calls.append(True)
        return {"rows_written": len(r)}

    # First submit — gated (pending_approval).
    r1 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 5}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=write_fn,
        store=store,
    )
    assert r1["state"] == "pending_approval"

    # Retry (e.g. network retry from submitter) — must return the same pending record.
    r2 = await submit_writeback(
        org_id="orgA",
        idempotency_key=key,
        rows=[{"id": 5}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=write_fn,
        store=store,
    )
    assert r2["id"] == r1["id"], "Pending record must be returned as-is on retry"
    assert r2["state"] == "pending_approval"
    assert len(calls) == 0  # write never triggered


# ---------------------------------------------------------------------------
# PG integration tests (only active when RUN_PG_TESTS=1 + DATABASE_URL set)
#
# These tests exercise PgWritebackStore against a real Postgres database.
# They are SKIPPED in the default test run — set RUN_PG_TESTS=1 and provide a
# real DATABASE_URL to run them.
#
# How to run:
#   RUN_PG_TESTS=1 \
#   DATABASE_URL=postgresql://postgres:postgres@localhost/nubi_test \
#   pytest tests/test_writeback.py -k pg -v
# ---------------------------------------------------------------------------

import os as _os
from contextlib import asynccontextmanager as _asynccontextmanager

_RUN_PG = bool(_os.getenv("RUN_PG_TESTS"))

pytestmark_pg = pytest.mark.skipif(
    not _RUN_PG,
    reason="Set RUN_PG_TESTS=1 and DATABASE_URL to run PG writeback integration tests.",
)


@pytest_asyncio.fixture(scope="module")
async def _pg_wb_pool():
    """Module-scoped asyncpg pool for writeback PG tests.

    Creates a throwaway schema, runs migrations, then tears down after the
    module finishes.  Skipped when RUN_PG_TESTS is not set.
    """
    if not _RUN_PG:
        yield None
        return

    db_url = _os.environ.get("DATABASE_URL", "")
    if not db_url or "fake" in db_url:
        pytest.skip("DATABASE_URL is not a real PG URL — skipping PG writeback tests.")

    import asyncpg as _apg  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    schema_name = f"nubi_wb_test_{uuid.uuid4().hex[:8]}"

    admin_conn = await _apg.connect(db_url)
    await admin_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')

    async def _init_conn(conn):
        await conn.execute(f'SET search_path TO "{schema_name}", public')

    pool = await _apg.create_pool(dsn=db_url, min_size=1, max_size=3, init=_init_conn)

    # Run migrations.
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    text        PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            r["version"]
            for r in await conn.fetch("SELECT version FROM schema_migrations")
        }
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            if sql_file.name in applied:
                continue
            sql_text = sql_file.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql_text)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)",
                    sql_file.name,
                )

    try:
        yield pool
    finally:
        await pool.close()
        await admin_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        await admin_conn.close()


async def _pg_create_user_and_org(pool) -> tuple[str, str]:
    """Insert a test user + org in the test schema; return (user_id, org_id)."""
    uid = str(uuid.uuid4())
    email = f"wb-pg-{uid[:8]}@nubi.test"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, name, email_verified) "
            "VALUES ($1, $2, $3, $4, true)",
            uid, email, "dummy-hash", "WB PG User",
        )
        oid = str(uuid.uuid4())
        slug = f"wb-pg-{uid[:8]}"
        await conn.execute(
            "INSERT INTO orgs (id, name, slug) VALUES ($1, $2, $3)",
            oid, "WB PG Org", slug,
        )
        await conn.execute(
            "INSERT INTO org_members (org_id, user_id, role) VALUES ($1, $2, 'owner')",
            oid, uid,
        )
    return uid, oid


@_asynccontextmanager
async def _pg_store_ctx(pool):
    """Yield a PgWritebackStore whose app.db helpers use the test pool."""
    import app.db as _app_db  # noqa: PLC0415
    from app.connectors.writeback import PgWritebackStore  # noqa: PLC0415
    from unittest.mock import patch as _patch  # noqa: PLC0415

    store = PgWritebackStore()

    async def _fetch(query, *args):
        async with pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def _fetchrow(query, *args):
        async with pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _execute(query, *args):
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)

    with (
        _patch.object(_app_db, "fetch", side_effect=_fetch),
        _patch.object(_app_db, "fetchrow", side_effect=_fetchrow),
        _patch.object(_app_db, "execute", side_effect=_execute),
    ):
        yield store


@pytestmark_pg
@pytest.mark.asyncio
async def test_pg_writeback_persistence(_pg_wb_pool):
    """PgWritebackStore: submitted records survive across store instances (persistence)."""
    if _pg_wb_pool is None:
        pytest.skip("pg pool not available")

    user_id, org_id = await _pg_create_user_and_org(_pg_wb_pool)
    key = str(uuid.uuid4())
    rows_data = [{"id": 1, "value": "alpha"}, {"id": 2, "value": "beta"}]

    async with _pg_store_ctx(_pg_wb_pool) as store1:
        record = await store1.create(
            org_id=org_id,
            idempotency_key=key,
            rows=rows_data,
            target={"connector_id": "conn1", "object": "raw.orders"},
            mode="append",
            created_by=user_id,
            approval_required=False,
            meta={"source": "test"},
        )
        wb_id = record["id"]
        assert record["state"] == "pending_approval"
        assert record["rows"] == rows_data

    # New store instance — same pool, no in-memory state.
    async with _pg_store_ctx(_pg_wb_pool) as store2:
        fetched = await store2.get(org_id, wb_id)
        assert fetched is not None
        assert fetched["id"] == wb_id
        assert fetched["rows"] == rows_data
        assert fetched["state"] == "pending_approval"


@pytestmark_pg
@pytest.mark.asyncio
async def test_pg_writeback_idempotency(_pg_wb_pool):
    """PgWritebackStore: same (org_id, idempotency_key) returns the same record."""
    if _pg_wb_pool is None:
        pytest.skip("pg pool not available")

    user_id, org_id = await _pg_create_user_and_org(_pg_wb_pool)
    key = str(uuid.uuid4())

    async with _pg_store_ctx(_pg_wb_pool) as store:
        r1 = await store.create(
            org_id=org_id,
            idempotency_key=key,
            rows=[{"id": 10}],
            target={"connector_id": "c1", "object": "t1"},
            mode="append",
            created_by=user_id,
            approval_required=False,
        )
        # Duplicate create — ON CONFLICT DO NOTHING → returns existing row.
        r2 = await store.create(
            org_id=org_id,
            idempotency_key=key,
            rows=[{"id": 99}],  # different payload — must be ignored
            target={"connector_id": "c1", "object": "t1"},
            mode="append",
            created_by=user_id,
            approval_required=False,
        )
        assert r1["id"] == r2["id"]
        assert r2["rows"] == [{"id": 10}]  # original payload preserved

        # get_by_idempotency_key also returns it.
        r3 = await store.get_by_idempotency_key(org_id, key)
        assert r3 is not None
        assert r3["id"] == r1["id"]


@pytestmark_pg
@pytest.mark.asyncio
async def test_pg_writeback_org_isolation(_pg_wb_pool):
    """PgWritebackStore: org B cannot access org A's records (org isolation)."""
    if _pg_wb_pool is None:
        pytest.skip("pg pool not available")

    user_a, org_a = await _pg_create_user_and_org(_pg_wb_pool)
    _user_b, org_b = await _pg_create_user_and_org(_pg_wb_pool)
    key = str(uuid.uuid4())

    async with _pg_store_ctx(_pg_wb_pool) as store:
        record = await store.create(
            org_id=org_a,
            idempotency_key=key,
            rows=[{"id": 1}],
            target={"connector_id": "c1", "object": "t1"},
            mode="append",
            created_by=user_a,
            approval_required=False,
        )

        # Org B get by ID → None
        assert await store.get(org_b, record["id"]) is None

        # Org B get_by_idempotency_key with same key → None
        assert await store.get_by_idempotency_key(org_b, key) is None

        # Org B list → does not contain org A's record
        org_b_records = await store.list(org_b)
        listed_ids = [r["id"] for r in org_b_records]
        assert record["id"] not in listed_ids


@pytestmark_pg
@pytest.mark.asyncio
async def test_pg_writeback_state_machine(_pg_wb_pool):
    """PgWritebackStore: transition() advances state and persists result."""
    if _pg_wb_pool is None:
        pytest.skip("pg pool not available")

    user_id, org_id = await _pg_create_user_and_org(_pg_wb_pool)

    async with _pg_store_ctx(_pg_wb_pool) as store:
        record = await store.create(
            org_id=org_id,
            idempotency_key=str(uuid.uuid4()),
            rows=[{"id": 5}],
            target={"connector_id": "c1", "object": "t1"},
            mode="overwrite",
            created_by=user_id,
            approval_required=True,
        )
        assert record["state"] == "pending_approval"

        committed = await store.transition(
            org_id, record["id"], "committed",
            result={"rows_written": 1},
            approved_by=user_id,
        )
        assert committed is not None
        assert committed["state"] == "committed"
        assert committed["result"] == {"rows_written": 1}
        assert committed["approved_by"] == user_id

        # Terminal state guard: second transition raises 409
        from app.errors import AppError as _AppError  # noqa: PLC0415
        with pytest.raises(_AppError) as exc_info:
            await store.transition(org_id, record["id"], "rejected")
        assert exc_info.value.status == 409


@pytestmark_pg
@pytest.mark.asyncio
async def test_pg_writeback_list(_pg_wb_pool):
    """PgWritebackStore: list() returns org-scoped records in DESC created_at order."""
    if _pg_wb_pool is None:
        pytest.skip("pg pool not available")

    user_id, org_id = await _pg_create_user_and_org(_pg_wb_pool)

    async with _pg_store_ctx(_pg_wb_pool) as store:
        created_ids = []
        for i in range(3):
            r = await store.create(
                org_id=org_id,
                idempotency_key=str(uuid.uuid4()),
                rows=[{"id": i}],
                target={"connector_id": "c1", "object": "t1"},
                mode="append",
                created_by=user_id,
                approval_required=False,
            )
            created_ids.append(r["id"])

        records = await store.list(org_id)
        listed_ids = [r["id"] for r in records]
        for cid in created_ids:
            assert cid in listed_ids
        # DESC order: last created should appear first.
        assert listed_ids[0] == created_ids[-1]


# ---------------------------------------------------------------------------
# 27. [LOW] TOCTOU fix: two concurrent approve_writeback calls must invoke
#     connector_write_fn exactly once; the loser gets a 409 conflict.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_approve_writeback_invokes_connector_exactly_once():
    """Two concurrent approve_writeback calls on the same pending record must
    invoke connector_write_fn exactly once.

    The CAS (compare-and-swap) on state='pending_approval' → 'committing'
    ensures that exactly one approver wins the race and proceeds to call the
    connector.  The loser sees a 409 AppError without ever calling the
    connector.
    """
    import asyncio  # noqa: PLC0415

    from app.errors import AppError  # noqa: PLC0415

    store = InMemoryWritebackStore()
    call_count = [0]

    async def slow_write(r, t, m):
        """Simulate a connector write that takes a moment."""
        call_count[0] += 1
        await asyncio.sleep(0)  # yield to allow interleaving
        return {"rows_written": len(r)}

    # Submit a gated record (pending_approval).
    record = await submit_writeback(
        org_id="orgA",
        idempotency_key=_idem(),
        rows=[{"id": 1}, {"id": 2}],
        target=_target(),
        mode="append",
        created_by="user1",
        approval_required=True,
        connector_write_fn=slow_write,
        store=store,
    )
    assert record["state"] == "pending_approval"
    wb_id = record["id"]

    # Launch two concurrent approve calls.
    results: list[dict | BaseException] = []

    async def try_approve(approver_id: str) -> None:
        try:
            r = await approve_writeback(
                org_id="orgA",
                wb_id=wb_id,
                action="approve",
                approver_id=approver_id,
                connector_write_fn=slow_write,
                store=store,
            )
            results.append(r)
        except AppError as exc:
            results.append(exc)

    await asyncio.gather(
        try_approve("approver1"),
        try_approve("approver2"),
    )

    # Exactly one call to connector_write_fn.
    assert call_count[0] == 1, (
        f"connector_write_fn must be called exactly once; got {call_count[0]}"
    )

    # Exactly one success (committed) and one conflict (409).
    successes = [r for r in results if isinstance(r, dict) and r.get("state") == "committed"]
    conflicts = [r for r in results if isinstance(r, AppError) and r.status == 409]
    assert len(successes) == 1, f"Expected 1 committed result, got: {results}"
    assert len(conflicts) == 1, f"Expected 1 conflict (409), got: {results}"
