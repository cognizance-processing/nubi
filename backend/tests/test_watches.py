"""Watch routes + engine — CRUD, evaluate, breach explanation, best-effort fire.

Coverage
--------
(1)  POST /watches then GET /watches/{id} round-trips the record.
(2)  POST /watches/{id}/evaluate on a BREACHING threshold → breached=true with a
     deterministic explanation string (NullProvider) and sent=0 (no channel
     configured → no-op, no error).
(3)  A NON-breaching threshold → breached=false, no explanation, no alert.
(4)  PUT updates the watch; DELETE removes it (subsequent GET 404s).
(5)  A watch with no rule → 400; an embed token cannot create → 403;
     unauthenticated create → 401.
(6)  Direct engine: evaluate_watch passes claims through the metric execution
     path (governance/RLS) and reduces to the demo total.

The demo metric ``demo_revenue`` aggregates SUM(value) over the 5-row demo table
(1.1+2.2+3.3+4.4+5.5 = 16.5). A ``> 10`` threshold breaches; ``> 100`` does not.
Tests use the seeded metric + NullProvider so they are deterministic and offline.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _embed_headers(user_id: str) -> dict[str, str]:
    import time

    import jwt

    from app.config import get_settings

    settings = get_settings()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": user_id,
            "kind": "embed",
            "scope": ["read:query"],
            "iat": now,
            "exp": now + 900,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def w_client(app, fake_db):
    """HTTPX client with a seeded user + org membership for the watch tests."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo
    from app.routes import watches as watches_route

    repo = InMemoryRepo()
    set_repo(repo)
    watches_route.reset_for_tests()

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "watch_tester@example.com",
        "name": "Watch Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    # Watch routes are now tenant-scoped: the caller must have an org membership.
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, org_id

    set_repo(None)
    watches_route.reset_for_tests()


def _watch_body(name: str, *, op: str = ">", value: float = 10) -> dict:
    """A watch over the seeded demo_revenue metric with a level threshold."""
    return {
        "name": name,
        "metric_id": "demo_revenue",
        "config": {
            "dimensions": ["name"],
            "threshold": {"op": op, "value": value},
            "enabled": True,
        },
    }


# ---------------------------------------------------------------------------
# (1) Create → Get round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_then_get_watch(w_client):
    client, user_id, _org_id = w_client
    headers = _auth_headers(user_id)

    name = f"Revenue Watch {uuid.uuid4().hex[:8]}"
    resp = await client.post("/api/v1/watches", json=_watch_body(name), headers=headers)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == name
    assert created["metric_id"] == "demo_revenue"
    assert created["config"]["threshold"]["op"] == ">"
    watch_id = created["id"]

    got = await client.get(f"/api/v1/watches/{watch_id}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["id"] == watch_id


# ---------------------------------------------------------------------------
# (2) Evaluate a BREACHING threshold → breached + explanation, fire is no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_breaching_watch(w_client):
    client, user_id, _org_id = w_client
    headers = _auth_headers(user_id)

    name = f"Breach Watch {uuid.uuid4().hex[:8]}"
    create = await client.post(
        "/api/v1/watches", json=_watch_body(name, op=">", value=10), headers=headers
    )
    assert create.status_code == 201, create.text
    watch_id = create.json()["id"]

    resp = await client.post(f"/api/v1/watches/{watch_id}/evaluate", headers=headers)
    assert resp.status_code == 200, resp.text
    summary = resp.json()

    # Total demo revenue 16.5 > 10 → breached.
    assert summary["breached"] is True
    assert summary["state"] == "breached"
    assert summary["value"] == pytest.approx(16.5)

    # NullProvider → a deterministic explanation string is returned.
    assert isinstance(summary["explanation"], str)
    assert summary["explanation"]
    assert "threshold" in summary["explanation"].lower()

    # No channel configured → fire is best-effort no-op (0 sent, no error raised).
    assert summary["sent"] == 0

    # The top contributing dimension is surfaced for context (epsilon = 5.5).
    top = summary["result"]["top_dimension"]
    assert top is not None
    assert top["dimension"] == "name"


# ---------------------------------------------------------------------------
# (3) A NON-breaching threshold → breached=false, no explanation, no alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_non_breaching_watch(w_client):
    client, user_id, _org_id = w_client
    headers = _auth_headers(user_id)

    name = f"Calm Watch {uuid.uuid4().hex[:8]}"
    create = await client.post(
        "/api/v1/watches", json=_watch_body(name, op=">", value=100), headers=headers
    )
    assert create.status_code == 201, create.text
    watch_id = create.json()["id"]

    resp = await client.post(f"/api/v1/watches/{watch_id}/evaluate", headers=headers)
    assert resp.status_code == 200, resp.text
    summary = resp.json()

    # 16.5 is NOT > 100 → no breach, no explanation, no dispatch.
    assert summary["breached"] is False
    assert summary["state"] == "ok"
    assert summary["value"] == pytest.approx(16.5)
    assert "explanation" not in summary
    assert summary["sent"] == 0


# ---------------------------------------------------------------------------
# (4) PUT updates, DELETE removes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_then_delete_watch(w_client):
    client, user_id, _org_id = w_client
    headers = _auth_headers(user_id)

    name = f"Mutable Watch {uuid.uuid4().hex[:8]}"
    create = await client.post("/api/v1/watches", json=_watch_body(name), headers=headers)
    assert create.status_code == 201, create.text
    watch_id = create.json()["id"]

    # Update the threshold value.
    upd = await client.put(
        f"/api/v1/watches/{watch_id}",
        json={
            "name": name,
            "metric_id": "demo_revenue",
            "config": {"threshold": {"op": ">", "value": 999}, "enabled": False},
        },
        headers=headers,
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["config"]["threshold"]["value"] == 999
    assert upd.json()["config"]["enabled"] is False

    # Delete and confirm gone.
    delete = await client.delete(f"/api/v1/watches/{watch_id}", headers=headers)
    assert delete.status_code == 200, delete.text
    assert delete.json()["deleted"] is True

    after = await client.get(f"/api/v1/watches/{watch_id}", headers=headers)
    assert after.status_code == 404, after.text


# ---------------------------------------------------------------------------
# (5) Validation + auth gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_watch_without_rule_returns_400(w_client):
    client, user_id, _org_id = w_client
    headers = _auth_headers(user_id)

    bad = {
        "name": f"No Rule {uuid.uuid4().hex[:8]}",
        "metric_id": "demo_revenue",
        "config": {"dimensions": ["name"]},  # no threshold / comparison
    }
    resp = await client.post("/api/v1/watches", json=bad, headers=headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "invalid_watch"


@pytest.mark.asyncio
async def test_embed_token_cannot_create_watch(w_client):
    client, user_id, _org_id = w_client

    resp = await client.post(
        "/api/v1/watches",
        json=_watch_body(f"Embed Attempt {uuid.uuid4().hex[:8]}"),
        headers=_embed_headers(user_id),
    )
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.asyncio
async def test_unauthenticated_create_returns_401(w_client):
    client, _, _org_id = w_client

    resp = await client.post("/api/v1/watches", json=_watch_body("Anon Watch"))
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_list_watches(w_client):
    client, user_id, _org_id = w_client
    headers = _auth_headers(user_id)

    name = f"Listed Watch {uuid.uuid4().hex[:8]}"
    create = await client.post("/api/v1/watches", json=_watch_body(name), headers=headers)
    assert create.status_code == 201, create.text
    watch_id = create.json()["id"]

    resp = await client.get("/api/v1/watches", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = [w["id"] for w in resp.json()["watches"]]
    assert watch_id in ids


@pytest.mark.asyncio
async def test_watch_cross_org_isolation(w_client, fake_db):
    """A user in org B cannot list/get/update/delete a watch in org A (IDOR)."""
    client, alice_id, _alice_org = w_client
    from app.repos.provider import get_repo

    # Alice (org A) creates a watch.
    name = f"Alice Watch {uuid.uuid4().hex[:8]}"
    create = await client.post(
        "/api/v1/watches", json=_watch_body(name), headers=_auth_headers(alice_id)
    )
    assert create.status_code == 201, create.text
    watch_id = create.json()["id"]

    # Seed Bob in a DIFFERENT org.
    bob_id = str(uuid.uuid4())
    bob_org = str(uuid.uuid4())
    fake_db.users[bob_id] = {
        "id": bob_id,
        "email": "bob@example.com",
        "name": "Bob",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    get_repo().seed_org_member(org_id=bob_org, user_id=bob_id)
    bob = _auth_headers(bob_id)

    # Bob's list does NOT include Alice's watch.
    resp = await client.get("/api/v1/watches", headers=bob)
    assert resp.status_code == 200, resp.text
    assert watch_id not in [w["id"] for w in resp.json()["watches"]]

    # Bob cannot GET / DELETE Alice's watch by id → 404 (no info leak).
    assert (await client.get(f"/api/v1/watches/{watch_id}", headers=bob)).status_code == 404
    assert (await client.delete(f"/api/v1/watches/{watch_id}", headers=bob)).status_code == 404
    # Bob cannot overwrite Alice's watch via PUT either.
    put = await client.put(
        f"/api/v1/watches/{watch_id}", json=_watch_body(name, value=999), headers=bob
    )
    assert put.status_code == 404, put.text

    # Alice's watch is intact and still hers.
    got = await client.get(f"/api/v1/watches/{watch_id}", headers=_auth_headers(alice_id))
    assert got.status_code == 200 and got.json()["id"] == watch_id


# ---------------------------------------------------------------------------
# (6) Direct engine: evaluate_watch reuses the metric execution path with claims
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_watch_engine_passes_claims(app, fake_db):
    """evaluate_watch compiles + executes the metric (RLS via claims) → scalar."""
    from app.ai.watch import Watch, evaluate_watch, run_watch
    from app.metrics.registry import get_metric_registry

    metric = get_metric_registry().get("demo_revenue")
    assert metric is not None

    watch = Watch.from_config(
        id="engine-watch",
        name="Engine Watch",
        metric_id="demo_revenue",
        config={"dimensions": ["name"], "threshold": {"op": ">", "value": 10}},
    )

    # Governance/RLS: claims are threaded into the planner exactly like the route.
    result = await evaluate_watch(watch, metric, {"policies": {}})
    assert result.error is None
    assert result.breached is True
    assert result.value == pytest.approx(16.5)
    assert result.measure_name == "revenue"

    # run_watch wraps evaluate → explain → fire; fire is a no-op (0 sent).
    summary = await run_watch(watch, metric, {"policies": {}})
    assert summary["breached"] is True
    assert summary["sent"] == 0
    assert summary["explanation"]


@pytest.mark.asyncio
async def test_explain_breach_deterministic_under_nullprovider(app, fake_db):
    """explain_breach with NullProvider returns the deterministic template."""
    from app.ai.provider import NullProvider
    from app.ai.watch import Watch, evaluate_watch, explain_breach
    from app.metrics.registry import get_metric_registry

    metric = get_metric_registry().get("demo_revenue")
    watch = Watch.from_config(
        id="explain-watch",
        name="Explain Watch",
        metric_id="demo_revenue",
        config={"dimensions": ["name"], "threshold": {"op": ">", "value": 1}},
    )
    result = await evaluate_watch(watch, metric, {"policies": {}})
    assert result.breached is True

    text1 = await explain_breach(watch, result, provider=NullProvider())
    text2 = await explain_breach(watch, result, provider=NullProvider())
    assert text1 == text2  # deterministic
    assert "revenue" in text1
    assert "16.5" in text1 or "16" in text1
