"""Tests for watch-kind envelopes in ``POST /apply``.

Coverage
--------
1. Bundle with watch envelopes → watches registered (queryable via GET /watches).
2. Re-apply with same payload → idempotent (no duplicate, action updated/unchanged).
3. Cross-org isolation: watch applied in org A is invisible to org B.
4. Watch labels/config round-trip through to the stored record.
5. Watch missing breach rule → action=failed, other resources still succeed.
6. Watch dry-run → planned action returned, no watch registered.
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
# Helpers
# ---------------------------------------------------------------------------


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _org_header(org_id: str) -> dict[str, str]:
    return {"X-Org-Id": org_id}


def _watch_envelope(
    name: str,
    metric_id: str = "demo_revenue",
    *,
    threshold: dict | None = None,
    config: dict | None = None,
    labels: dict | None = None,
    env_id: str | None = None,
) -> dict[str, Any]:
    """Return a minimal watch portability envelope for apply."""
    if threshold is None:
        threshold = {"op": ">", "value": 10}
    spec: dict[str, Any] = {
        "name": name,
        "metric_id": metric_id,
        "config": {**(config or {}), "threshold": threshold, "enabled": True},
    }
    if labels:
        spec["labels"] = labels
    meta: dict[str, Any] = {"name": name}
    if env_id:
        meta["id"] = env_id
    return {
        "kind": "watch",
        "apiVersion": "nubi/v1",
        "metadata": meta,
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def apply_watch_setup(app, fake_db):
    """Yield (client, user_id, org_id) for /apply watch tests."""
    from app.routes import watches as watches_route  # noqa: PLC0415

    repo = InMemoryRepo()
    set_repo(repo)
    watches_route.reset_for_tests()

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "apply_watch@example.com",
        "name": "Apply Watch Tester",
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
        yield client, user_id, org_id

    set_repo(None)
    watches_route.reset_for_tests()


# ---------------------------------------------------------------------------
# 1. Bundle with watches applied registers them (queryable via GET /watches)
# ---------------------------------------------------------------------------


class TestApplyWatchRegistration:
    @pytest.mark.asyncio
    async def test_apply_watch_registers_it(self, apply_watch_setup):
        """Applying a watch envelope → watch registered, queryable via GET /watches."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        name = f"Revenue Alert {uuid.uuid4().hex[:6]}"
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_watch_envelope(name)],
                "dry_run": False,
            },
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        results = data["results"]
        assert len(results) == 1
        r = results[0]
        assert r["kind"] == "watch"
        assert r["action"] in ("created", "updated")
        assert r["name"] == name
        assert data["summary"]["failed"] == 0

        # Confirm the watch is now queryable.
        list_resp = await client.get("/api/v1/watches", headers=headers)
        assert list_resp.status_code == 200, list_resp.text
        watches = list_resp.json()["watches"]
        names = [w["name"] for w in watches]
        assert name in names, f"Watch {name!r} not found in {names}"

    @pytest.mark.asyncio
    async def test_apply_multiple_watches(self, apply_watch_setup):
        """Multiple watch envelopes in one bundle are all registered."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        names = [f"Watch {uuid.uuid4().hex[:6]}" for _ in range(3)]
        resources = [_watch_envelope(n) for n in names]

        resp = await client.post(
            "/api/v1/apply",
            json={"resources": resources, "dry_run": False},
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["summary"]["failed"] == 0

        list_resp = await client.get("/api/v1/watches", headers=headers)
        registered_names = [w["name"] for w in list_resp.json()["watches"]]
        for n in names:
            assert n in registered_names, f"{n!r} not found in {registered_names}"

    @pytest.mark.asyncio
    async def test_apply_watches_mixed_with_other_resources(self, apply_watch_setup):
        """Watches and dashboards in the same bundle are both applied."""
        from tests.test_apply import _dashboard_envelope  # noqa: PLC0415

        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        watch_name = f"Mixed Watch {uuid.uuid4().hex[:6]}"
        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    _watch_envelope(watch_name),
                    _dashboard_envelope("Side Dashboard"),
                ],
                "dry_run": False,
            },
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["summary"]["failed"] == 0
        kinds = {r["kind"] for r in data["results"]}
        assert "watch" in kinds
        assert "dashboard" in kinds


# ---------------------------------------------------------------------------
# 2. Re-apply is idempotent (same payload → no duplicate)
# ---------------------------------------------------------------------------


class TestApplyWatchIdempotent:
    @pytest.mark.asyncio
    async def test_second_apply_does_not_create_duplicate(self, apply_watch_setup):
        """Applying the same watch twice → still only one watch registered."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        name = f"Idempotent Watch {uuid.uuid4().hex[:6]}"
        envelope = _watch_envelope(name)

        # First apply.
        r1 = await client.post(
            "/api/v1/apply",
            json={"resources": [envelope], "dry_run": False},
            headers=headers,
        )
        assert r1.status_code == 200, r1.text
        assert r1.json()["summary"]["failed"] == 0

        # Second apply — identical payload.
        r2 = await client.post(
            "/api/v1/apply",
            json={"resources": [envelope], "dry_run": False},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["summary"]["failed"] == 0

        # Only one watch with this name in the registry.
        list_resp = await client.get("/api/v1/watches", headers=headers)
        watches_with_name = [
            w for w in list_resp.json()["watches"] if w["name"] == name
        ]
        assert len(watches_with_name) == 1, (
            f"Expected 1 watch named {name!r}, got {len(watches_with_name)}: {watches_with_name}"
        )

    @pytest.mark.asyncio
    async def test_second_apply_reports_updated_or_ok(self, apply_watch_setup):
        """The second apply for a watch reports action=updated (not failed)."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        name = f"Re-Apply Watch {uuid.uuid4().hex[:6]}"
        envelope = _watch_envelope(name)

        # First apply.
        await client.post(
            "/api/v1/apply",
            json={"resources": [envelope], "dry_run": False},
            headers=headers,
        )

        # Second apply.
        r2 = await client.post(
            "/api/v1/apply",
            json={"resources": [envelope], "dry_run": False},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        result = r2.json()["results"][0]
        # Action must be updated (slug collision → upsert), not failed.
        assert result["action"] in ("updated", "unchanged"), (
            f"Expected updated/unchanged on re-apply, got {result['action']}"
        )
        assert result.get("error") is None, f"Unexpected error: {result.get('error')}"


# ---------------------------------------------------------------------------
# 3. Cross-org isolation
# ---------------------------------------------------------------------------


class TestApplyWatchCrossOrgIsolation:
    @pytest.mark.asyncio
    async def test_watch_not_visible_to_other_org(self, apply_watch_setup, fake_db):
        """A watch applied in org A is not queryable by org B."""
        client, alice_id, _alice_org = apply_watch_setup
        from app.repos.provider import get_repo  # noqa: PLC0415

        name = f"Alice Watch {uuid.uuid4().hex[:6]}"
        await client.post(
            "/api/v1/apply",
            json={"resources": [_watch_envelope(name)], "dry_run": False},
            headers={**_auth(alice_id), **_org_header(_alice_org)},
        )

        # Set up Bob in a different org.
        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = {
            "id": bob_id,
            "email": "bob_apply@example.com",
            "name": "Bob",
            "avatar_url": None,
            "email_verified": True,
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        get_repo().seed_org_member(org_id=bob_org, user_id=bob_id)

        # Bob's watch list must NOT include Alice's watch.
        resp = await client.get(
            "/api/v1/watches",
            headers={**_auth(bob_id), **_org_header(bob_org)},
        )
        assert resp.status_code == 200, resp.text
        bob_watches = resp.json()["watches"]
        assert name not in [w["name"] for w in bob_watches], (
            f"Alice's watch {name!r} leaked to Bob: {bob_watches}"
        )

    @pytest.mark.asyncio
    async def test_two_orgs_can_have_same_watch_name(self, apply_watch_setup, fake_db):
        """Two orgs can independently register a watch with the same name."""
        client, alice_id, alice_org = apply_watch_setup
        from app.repos.provider import get_repo  # noqa: PLC0415

        name = "Revenue Alert"  # same name in both orgs

        # Alice applies.
        r_alice = await client.post(
            "/api/v1/apply",
            json={"resources": [_watch_envelope(name)], "dry_run": False},
            headers={**_auth(alice_id), **_org_header(alice_org)},
        )
        assert r_alice.status_code == 200, r_alice.text
        assert r_alice.json()["summary"]["failed"] == 0

        # Bob in a different org applies the same watch name.
        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = {
            "id": bob_id,
            "email": "bob2_apply@example.com",
            "name": "Bob2",
            "avatar_url": None,
            "email_verified": True,
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        get_repo().seed_org_member(org_id=bob_org, user_id=bob_id)

        r_bob = await client.post(
            "/api/v1/apply",
            json={"resources": [_watch_envelope(name)], "dry_run": False},
            headers={**_auth(bob_id), **_org_header(bob_org)},
        )
        assert r_bob.status_code == 200, r_bob.text
        assert r_bob.json()["summary"]["failed"] == 0


# ---------------------------------------------------------------------------
# 4. Watch labels/config round-trip
# ---------------------------------------------------------------------------


class TestApplyWatchConfigRoundTrip:
    @pytest.mark.asyncio
    async def test_watch_config_preserved(self, apply_watch_setup):
        """Freeform config fields (dimensions, time_grain) survive the apply round-trip."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        name = f"Config Watch {uuid.uuid4().hex[:6]}"
        envelope = _watch_envelope(
            name,
            config={
                "dimensions": ["region", "product"],
                "time_grain": "day",
                "channel_config": {"slack_channel": "#alerts"},
                "enabled": True,
            },
        )

        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [envelope], "dry_run": False},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["summary"]["failed"] == 0

        # Fetch the watch and verify config is preserved.
        list_resp = await client.get("/api/v1/watches", headers=headers)
        watches = list_resp.json()["watches"]
        match = next((w for w in watches if w["name"] == name), None)
        assert match is not None, f"Watch {name!r} not found"
        cfg = match.get("config") or {}
        assert cfg.get("time_grain") == "day"
        assert cfg.get("dimensions") == ["region", "product"]
        assert cfg.get("channel_config", {}).get("slack_channel") == "#alerts"

    @pytest.mark.asyncio
    async def test_watch_labels_in_spec_accepted(self, apply_watch_setup):
        """A watch envelope with labels in spec is accepted (no failure)."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        name = f"Labeled Watch {uuid.uuid4().hex[:6]}"
        envelope = _watch_envelope(
            name,
            labels={"team": "data", "env": "production", "severity": "high"},
        )

        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [envelope], "dry_run": False},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        r = resp.json()["results"][0]
        assert r["action"] in ("created", "updated"), f"Unexpected action: {r}"
        assert r.get("error") is None

    @pytest.mark.asyncio
    async def test_watch_threshold_update_on_reapply(self, apply_watch_setup):
        """Re-applying a watch with a different threshold updates the stored config."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        name = f"Updatable Watch {uuid.uuid4().hex[:6]}"

        # First apply: threshold > 10.
        await client.post(
            "/api/v1/apply",
            json={"resources": [_watch_envelope(name, threshold={"op": ">", "value": 10})],
                  "dry_run": False},
            headers=headers,
        )

        # Second apply: threshold > 500.
        r2 = await client.post(
            "/api/v1/apply",
            json={"resources": [_watch_envelope(name, threshold={"op": ">", "value": 500})],
                  "dry_run": False},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["summary"]["failed"] == 0

        # Registry should now reflect the updated threshold.
        list_resp = await client.get("/api/v1/watches", headers=headers)
        match = next(
            (w for w in list_resp.json()["watches"] if w["name"] == name), None
        )
        assert match is not None
        cfg = match.get("config") or {}
        threshold = cfg.get("threshold") or {}
        assert threshold.get("value") == 500, f"Expected threshold 500, got {threshold}"


# ---------------------------------------------------------------------------
# 5. Watch missing breach rule → action=failed, other resources still succeed
# ---------------------------------------------------------------------------


class TestApplyWatchValidation:
    @pytest.mark.asyncio
    async def test_watch_without_rule_fails_gracefully(self, apply_watch_setup):
        """A watch with no breach rule returns action=failed; other resources succeed."""
        from tests.test_apply import _dashboard_envelope  # noqa: PLC0415

        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        bad_watch = {
            "kind": "watch",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "No Rule Watch"},
            "spec": {
                "name": "No Rule Watch",
                "metric_id": "demo_revenue",
                "config": {"dimensions": ["name"]},  # no threshold / comparison / change
            },
        }

        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [
                    bad_watch,
                    _dashboard_envelope("Good Dashboard"),
                ],
                "dry_run": False,
            },
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        results = data["results"]
        assert len(results) == 2

        watch_result = next(r for r in results if r["kind"] == "watch")
        dash_result = next(r for r in results if r["kind"] == "dashboard")

        assert watch_result["action"] == "failed"
        assert watch_result.get("error")
        assert "threshold" in watch_result["error"].lower() or "rule" in watch_result["error"].lower()

        assert dash_result["action"] == "created"

        assert data["summary"]["failed"] == 1
        assert data["summary"]["created"] == 1

    @pytest.mark.asyncio
    async def test_watch_missing_metric_id_fails(self, apply_watch_setup):
        """A watch with no metric_id returns action=failed."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        bad_watch = {
            "kind": "watch",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "No Metric Watch"},
            "spec": {
                "name": "No Metric Watch",
                # no metric_id
                "config": {"threshold": {"op": ">", "value": 10}},
            },
        }

        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [bad_watch], "dry_run": False},
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        r = resp.json()["results"][0]
        assert r["action"] == "failed"
        assert r.get("error")


# ---------------------------------------------------------------------------
# 6. Watch dry-run
# ---------------------------------------------------------------------------


class TestApplyWatchDryRun:
    @pytest.mark.asyncio
    async def test_watch_dry_run_returns_planned_action(self, apply_watch_setup):
        """dry_run=True for a watch returns a planned action without registering."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        name = f"Dry Run Watch {uuid.uuid4().hex[:6]}"
        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [_watch_envelope(name)], "dry_run": True},
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dry_run"] is True
        r = data["results"][0]
        # Should be a planned action (create), not failed.
        assert r["action"] in ("create", "update"), f"Expected planned action, got {r['action']}"
        assert r.get("error") is None

        # No watch should be registered.
        list_resp = await client.get("/api/v1/watches", headers=headers)
        names = [w["name"] for w in list_resp.json()["watches"]]
        assert name not in names, f"Watch {name!r} should NOT be registered after dry run"

    @pytest.mark.asyncio
    async def test_watch_dry_run_missing_rule_shows_failed(self, apply_watch_setup):
        """dry_run=True with a watch missing its rule → action=failed in plan."""
        client, user_id, org_id = apply_watch_setup
        headers = {**_auth(user_id), **_org_header(org_id)}

        bad_watch = {
            "kind": "watch",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Bad Dry Watch"},
            "spec": {
                "name": "Bad Dry Watch",
                "metric_id": "demo_revenue",
                "config": {},  # no rule
            },
        }

        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [bad_watch], "dry_run": True},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        r = resp.json()["results"][0]
        assert r["action"] == "failed"
        assert r.get("error")
