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


# ---------------------------------------------------------------------------
# (8) Layered metric + active RLS policy → no DuckDB binder error
# ---------------------------------------------------------------------------
# Regression for: _run_metric called compile_metric without policy_cols, so a
# layered (derived_measures) metric with rls_keys=[] and active RLS claims
# would compile __base WITHOUT the policy column, then the planner injected
# WHERE <policy_col>=<val> on the outer SELECT and DuckDB raised a Binder Error
# ("column <policy_col> not found").
#
# Fix: policy_cols = tuple((claims or {}).get("policies") or {})
#      compile_metric(metric, mq, policy_cols=policy_cols)


@pytest.mark.asyncio
async def test_evaluate_watch_layered_metric_with_rls_policy_no_binder_error(app, fake_db):
    """A watch on a layered metric with an active policy compiles+executes cleanly.

    Before the fix, _run_metric called compile_metric(metric, mq) without
    policy_cols.  A layered path (derived_measures present) with rls_keys=[]
    did not hoist the policy column into __base, so the planner's injected
    WHERE active=True hit a DuckDB Binder Error.  After the fix the policy
    column is hoisted and the query executes without error.
    """
    from app.ai.watch import Watch, evaluate_watch
    from app.metrics.models import DerivedMeasure, Dimension, Measure, MetricDefinition
    from app.metrics.registry import get_metric_registry

    # Register a layered metric (derived_measures → layered compile path)
    # backed by the in-process demo table.  rls_keys=[] means the compiler
    # will NOT auto-hoist 'active'; it MUST arrive via policy_cols from claims.
    slug = f"test_watch_layered_{uuid.uuid4().hex[:8]}"
    get_metric_registry().register(
        MetricDefinition(
            id=slug,
            name="Watch Layered Ratio",
            measure=Measure(name="total_value", agg="sum", expr="value", type="additive"),
            base_table="demo",
            dimensions=(
                Dimension(name="name", type="text"),
                Dimension(name="active", type="bool"),
            ),
            time_dimension=None,
            derived_measures=(
                DerivedMeasure(
                    name="value_ratio",
                    formula="total_value / total_value",
                    format="number",
                ),
            ),
            rls_keys=(),  # empty → policy_cols must come from claims
            description="Test-only layered metric for watch policy_cols regression.",
        )
    )

    metric = get_metric_registry().get(slug)
    assert metric is not None

    watch = Watch.from_config(
        id="layered-rls-watch",
        name="Layered RLS Watch",
        metric_id=slug,
        config={"dimensions": ["name"], "threshold": {"op": ">", "value": 0}},
    )

    # Claims carry an RLS policy on 'active' — the planner will inject
    # WHERE active = True on the outer SELECT over __base.  Without the fix
    # this would raise a DuckDB Binder Error.
    claims = {"policies": {"active": True}}

    result = await evaluate_watch(watch, metric, claims)

    # The key assertion: no binder error — the query compiled and ran cleanly.
    assert result.error is None, f"Expected no error but got: {result.error}"
    # active=True rows: 10+20+40 = 70 (rows with active=True in the demo table)
    # Threshold > 0 → breached.
    assert result.breached is True


# ---------------------------------------------------------------------------
# labels passthrough — watch_breach webhook carries host-supplied labels
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breach_event_carries_labels(app, fake_db):
    """A watch with labels={"category_id":"X"} emits a watch_breach payload
    that contains labels.category_id == "X" and the watch name.
    """
    from unittest.mock import patch

    from app.ai.watch import Watch, evaluate_watch, fire_watch
    from app.metrics.registry import get_metric_registry

    metric = get_metric_registry().get("demo_revenue")
    assert metric is not None

    watch = Watch.from_config(
        id="labels-watch",
        name="Category Revenue Watch",
        metric_id="demo_revenue",
        config={
            "dimensions": ["name"],
            "threshold": {"op": ">", "value": 10},
            "labels": {"category_id": "cat-abc", "region": "ZA"},
        },
    )

    # Confirm labels are parsed from config.
    assert watch.labels == {"category_id": "cat-abc", "region": "ZA"}

    # Evaluate — should breach (demo total 16.5 > 10).
    result = await evaluate_watch(watch, metric, {"policies": {}})
    assert result.breached is True

    # Capture what emit_watch_breach is called with.
    # Patch the function in the events module (where it is defined and looked up
    # via the lazy import inside fire_watch).
    captured: dict = {}

    def _fake_emit(org_id, **kwargs):
        captured.update(kwargs)

    with patch("app.webhooks.events.emit_watch_breach", side_effect=_fake_emit):
        await fire_watch(watch, result, "Revenue breached.", org_id="org-1")

    assert captured["name"] == "Category Revenue Watch"
    assert captured["watch_id"] == "labels-watch"
    assert captured["metric_id"] == "demo_revenue"
    assert captured["labels"] == {"category_id": "cat-abc", "region": "ZA"}
    assert captured["labels"]["category_id"] == "cat-abc"


@pytest.mark.asyncio
async def test_breach_event_without_labels_still_works(app, fake_db):
    """A watch with no labels configured still fires and emits an empty labels map."""
    from unittest.mock import patch

    from app.ai.watch import Watch, evaluate_watch, fire_watch
    from app.metrics.registry import get_metric_registry

    metric = get_metric_registry().get("demo_revenue")
    watch = Watch.from_config(
        id="no-labels-watch",
        name="No Labels Watch",
        metric_id="demo_revenue",
        config={"dimensions": ["name"], "threshold": {"op": ">", "value": 10}},
    )

    assert watch.labels == {}

    result = await evaluate_watch(watch, metric, {"policies": {}})
    assert result.breached is True

    captured: dict = {}

    def _fake_emit(org_id, **kwargs):
        captured.update(kwargs)

    with patch("app.webhooks.events.emit_watch_breach", side_effect=_fake_emit):
        await fire_watch(watch, result, "Revenue breached.", org_id="org-1")

    # Labels absent from config → emitted as empty dict (never missing key).
    assert "labels" in captured
    assert captured["labels"] == {} or captured["labels"] is None


@pytest.mark.asyncio
async def test_watch_breach_payload_has_no_pii(app, fake_db):
    """The watch_breach event payload must not contain any row data or secrets.

    Only metadata fields (watch_id, name, metric_id, value, explanation,
    labels) are permitted.  labels are host-supplied identifiers, not row data.
    """
    from app.webhooks.events import build_envelope, WATCH_BREACH

    payload = {
        "watch_id": "wid-1",
        "name": "Revenue Watch",
        "metric_id": "demo_revenue",
        "value": 16.5,
        "explanation": "Revenue is 16.5, > the 10 threshold.",
        "labels": {"category_id": "cat-xyz"},
    }
    envelope = build_envelope(WATCH_BREACH, "org-1", payload)

    assert envelope["type"] == WATCH_BREACH
    data = envelope["data"]

    # Required metadata fields present.
    assert data["watch_id"] == "wid-1"
    assert data["name"] == "Revenue Watch"
    assert data["metric_id"] == "demo_revenue"
    assert data["labels"]["category_id"] == "cat-xyz"

    # No raw rows / SQL / PII fields.
    forbidden = {"rows", "sql", "result", "filter", "filters", "where", "params"}
    assert not forbidden.intersection(set(data.keys())), (
        f"payload must not carry any of {forbidden}; got: {set(data.keys())}"
    )


@pytest.mark.asyncio
async def test_labels_from_top_level_body_folded_into_config(w_client):
    """POST /watches with top-level labels stores them under config.labels."""
    client, user_id, _org_id = w_client
    headers = _auth_headers(user_id)

    name = f"Labelled Watch {uuid.uuid4().hex[:8]}"
    body = {
        "name": name,
        "metric_id": "demo_revenue",
        "labels": {"category_id": "cat-top"},
        "config": {
            "threshold": {"op": ">", "value": 10},
            "enabled": True,
        },
    }
    resp = await client.post("/api/v1/watches", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["config"]["labels"] == {"category_id": "cat-top"}


@pytest.mark.asyncio
async def test_labels_inside_config_wins_over_top_level(w_client):
    """When labels exist in both config and top-level, config takes precedence."""
    client, user_id, _org_id = w_client
    headers = _auth_headers(user_id)

    name = f"Labels Priority Watch {uuid.uuid4().hex[:8]}"
    body = {
        "name": name,
        "metric_id": "demo_revenue",
        "labels": {"category_id": "top-level"},
        "config": {
            "threshold": {"op": ">", "value": 10},
            "labels": {"category_id": "config-level"},
        },
    }
    resp = await client.post("/api/v1/watches", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    # config.labels wins.
    assert created["config"]["labels"]["category_id"] == "config-level"


@pytest.mark.asyncio
async def test_evaluate_breaching_watch_with_labels_emits_label(app, fake_db):
    """run_watch on a labelled breaching watch emits labels in the webhook payload."""
    from unittest.mock import patch

    from app.ai.watch import Watch, run_watch
    from app.metrics.registry import get_metric_registry

    metric = get_metric_registry().get("demo_revenue")

    watch = Watch.from_config(
        id="full-path-labels-watch",
        name="Full Path Labels Watch",
        metric_id="demo_revenue",
        config={
            "dimensions": ["name"],
            "threshold": {"op": ">", "value": 10},
            "labels": {"category_id": "cat-full"},
        },
    )

    emitted: list[dict] = []

    def _capture(org_id, **kwargs):
        emitted.append({"org_id": org_id, **kwargs})

    # Patch the function where it is defined (looked up lazily from fire_watch).
    with patch("app.webhooks.events.emit_watch_breach", side_effect=_capture):
        summary = await run_watch(watch, metric, {"policies": {}, "org_id": "org-full"})

    assert summary["breached"] is True
    assert len(emitted) == 1
    ev = emitted[0]
    assert ev["labels"] == {"category_id": "cat-full"}
    assert ev["name"] == "Full Path Labels Watch"
    assert ev["watch_id"] == "full-path-labels-watch"
