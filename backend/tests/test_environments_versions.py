"""Tests for project-scoped environments + resource versioning (contract tests).

Feature under test (0005_environments_versions.sql + app/environments/store.py +
app/routes/environments.py):

- Environments are project-scoped rows (dev/prod auto-created lazily; prod is
  the protected default).
- Resources (flow | board | query) get immutable config snapshots
  ("versions") with content-hash dedupe, plus per-environment pointers.
- Checkpoint: POST /versions/{kind}/{id} snapshots the current draft and
  points an unprotected env at it (protected envs only move via promote).
- Promote: POST /environments/promote copies the from_env pointer to to_env;
  boards also drag their referenced queries along (include_dependencies).
- Resolution: GET /queries/{id}?env=<key> (and /flows/{id}?env=<key>) serve
  the pinned config/spec with a resolved_version stamp; unpinned envs fall
  back to the draft with resolved_version null.

Strategy (house pattern — see test_resources.py / test_admin.py)
----------------------------------------------------------------
- conftest ``app`` fixture patches all app.db helpers with FakeDB.
- ``InMemoryRepo`` via ``set_repo()`` for boards/queries; ``InMemoryFlowStore``
  via ``set_flow_store()`` for flows; new ``InMemoryEnvStore`` via
  ``set_env_store()`` for environments/versions.
- Projects live in the real DB in prod, so this module layers a tiny
  dict-backed projects table over ``app.db.fetchrow``/``fetch`` (the projects
  repo deliberately calls through the module object, so the patch lands).
- Auth: seed users in fake_db + mint_access_token JWTs.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# New providers under test (implementation may land in parallel).
from app.environments.store import InMemoryEnvStore, set_env_store

# Self-registering router (idempotent; main.py imports it too — see the
# test_admin.py pattern). Must exist for the endpoints to be reachable.
import app.routes.environments  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FLOW_SPEC_V1: dict[str, Any] = {"tasks": [{"key": "t1", "kind": "noop"}], "rev": 1}
FLOW_SPEC_V2: dict[str, Any] = {"tasks": [{"key": "t1", "kind": "noop"}], "rev": 2}


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str, email: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc),
    }


def _version_number(body: dict[str, Any]) -> int:
    """Extract the integer version from a checkpoint response.

    Tolerates both contract-compatible shapes:
    flattened ``{..., "version": 1, "deduped": false}`` and nested
    ``{"version": {..., "version": 1}, "deduped": false}``.
    """
    v = body.get("version")
    if isinstance(v, dict):
        return int(v["version"])
    return int(v)


def _deduped(body: dict[str, Any]) -> bool:
    v = body.get("version")
    if isinstance(v, dict) and "deduped" in v:
        return bool(v["deduped"])
    return bool(body.get("deduped"))


def _pointer_for(versions_body: dict[str, Any], env_key: str) -> dict[str, Any] | None:
    """Return the pointer row for *env_key* from a GET /versions response."""
    for ptr in versions_body.get("pointers", []):
        if ptr.get("env_key") == env_key:
            return ptr
    return None


def _make_projects_db(projects: dict[str, dict[str, Any]], fallback_fetchrow, fallback_fetch):
    """Build fetchrow/fetch fakes that serve a dict-backed projects table.

    Everything that is not a projects query is delegated to the conftest
    FakeDB fakes so auth/session lookups keep working.
    """

    def _norm(q: str) -> str:
        return " ".join(q.split()).upper()

    def _by_org(org_id: str) -> list[dict[str, Any]]:
        rows = [p for p in projects.values() if p["org_id"] == str(org_id)]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def _fetchrow(query: str, *args: Any):
        q = _norm(query)
        if "FROM PROJECTS" in q or "INTO PROJECTS" in q or q.startswith("UPDATE PROJECTS"):
            if q.startswith("INSERT"):
                # create_project: id, org_id, name, slug, created_by, git
                row = {
                    "id": str(args[0]),
                    "org_id": str(args[1]),
                    "name": args[2],
                    "slug": args[3],
                    "created_by": str(args[4]) if args[4] else None,
                    "git": args[5],
                    "created_at": datetime.now(tz=timezone.utc),
                }
                projects[row["id"]] = row
                return dict(row)
            if "SLUG = $2" in q:
                # slug-uniqueness probe: (org_id, slug)
                for p in _by_org(str(args[0])):
                    if p["slug"] == str(args[1]):
                        return {"?column?": 1}
                return None
            if "ID = $1" in q and "ORG_ID = $2" in q:
                p = projects.get(str(args[0]))
                if p is not None and p["org_id"] == str(args[1]):
                    return {"?column?": 1} if q.startswith("SELECT 1") else dict(p)
                return None
            if "COUNT(*)" in q:
                return {"n": len(_by_org(str(args[0])))}
            if "ID = $1" in q:
                p = projects.get(str(args[0]))
                return dict(p) if p is not None else None
            if "ORG_ID = $1" in q:
                rows = _by_org(str(args[0]))
                return dict(rows[0]) if rows else None
            return None
        return await fallback_fetchrow(query, *args)

    async def _fetch(query: str, *args: Any):
        q = _norm(query)
        if "FROM PROJECTS" in q:
            org_id = str(args[0]) if args else None
            rows = [
                dict(p)
                for p in sorted(projects.values(), key=lambda r: r["created_at"])
                if org_id is None or p["org_id"] == org_id
            ]
            return rows
        return await fallback_fetch(query, *args)

    return _fetchrow, _fetch


# ---------------------------------------------------------------------------
# Fixture: one org + user + project + query + flow (plus a second org for
# isolation tests), all stores injected, envs ensured for the main project.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def env_ctx(app, fake_db):
    """Yield ``(client, ctx)`` with stores injected and a seeded workspace."""
    repo = InMemoryRepo()
    set_repo(repo)
    flow_store = InMemoryFlowStore()
    set_flow_store(flow_store)
    env_store = InMemoryEnvStore()
    set_env_store(env_store)

    alice_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())
    bob_org_id = str(uuid.uuid4())
    bob_project_id = str(uuid.uuid4())

    fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
    fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
    repo.seed_org_member(org_id=org_id, user_id=alice_id)
    repo.seed_org_member(org_id=bob_org_id, user_id=bob_id)

    now = datetime.now(tz=timezone.utc)
    projects_tbl: dict[str, dict[str, Any]] = {
        project_id: {
            "id": project_id, "org_id": org_id, "name": "Default",
            "slug": "default", "created_by": alice_id, "git": None,
            "created_at": now,
        },
        bob_project_id: {
            "id": bob_project_id, "org_id": bob_org_id, "name": "Default",
            "slug": "default", "created_by": bob_id, "git": None,
            "created_at": now + timedelta(seconds=1),
        },
    }
    fetchrow_fake, fetch_fake = _make_projects_db(
        projects_tbl, fake_db.fake_fetchrow, fake_db.fake_fetch
    )

    try:
        with ExitStack() as stack:
            stack.enter_context(patch("app.db.fetchrow", side_effect=fetchrow_fake))
            stack.enter_context(patch("app.db.fetch", side_effect=fetch_fake))
            # Defensive: also patch module-local bindings if the routes module
            # imported the helpers directly (``from app.db import fetchrow``).
            for target, fake in (
                ("app.routes.environments.fetchrow", fetchrow_fake),
                ("app.routes.environments.fetch", fetch_fake),
            ):
                try:
                    stack.enter_context(patch(target, side_effect=fake))
                except (AttributeError, ModuleNotFoundError):
                    pass

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client:
                # Seed a query through the API (lands in the default project).
                resp = await client.post(
                    "/api/v1/queries",
                    json={"name": "Q1", "config": {"sql": "select 1", "rev": 1}},
                    headers=_auth_headers(alice_id),
                )
                assert resp.status_code == 201, resp.text
                query = resp.json()

                # Seed a flow directly via the store (bypasses spec validation).
                flow = await flow_store.create_flow(
                    org_id=org_id,
                    created_by=alice_id,
                    name="F1",
                    spec=dict(FLOW_SPEC_V1),
                    project_id=project_id,
                )

                # Ensure dev+prod exist for the main project (lazy creation).
                resp = await client.get(
                    f"/api/v1/projects/{project_id}/environments",
                    headers=_auth_headers(alice_id),
                )
                assert resp.status_code == 200, resp.text

                ctx = {
                    "alice_id": alice_id,
                    "bob_id": bob_id,
                    "org_id": org_id,
                    "bob_org_id": bob_org_id,
                    "project_id": project_id,
                    "bob_project_id": bob_project_id,
                    "query": query,
                    "flow": flow,
                    "repo": repo,
                    "flow_store": flow_store,
                    "env_store": env_store,
                    "projects_tbl": projects_tbl,
                }
                yield client, ctx
    finally:
        set_env_store(None)
        set_repo(None)


# ---------------------------------------------------------------------------
# Shared request helpers
# ---------------------------------------------------------------------------


async def _list_envs(client: AsyncClient, ctx: dict[str, Any], project_id: str | None = None):
    resp = await client.get(
        f"/api/v1/projects/{project_id or ctx['project_id']}/environments",
        headers=_auth_headers(ctx["alice_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    return body


async def _checkpoint(
    client: AsyncClient,
    ctx: dict[str, Any],
    kind: str,
    resource_id: str,
    *,
    message: str | None = None,
    env_key: str | None = None,
    expect: int = 201,
):
    payload: dict[str, Any] = {}
    if message is not None:
        payload["message"] = message
    if env_key is not None:
        payload["env_key"] = env_key
    resp = await client.post(
        f"/api/v1/versions/{kind}/{resource_id}",
        json=payload,
        headers=_auth_headers(ctx["alice_id"]),
    )
    assert resp.status_code == expect, resp.text
    return resp.json() if resp.status_code < 300 else None


async def _versions(client: AsyncClient, ctx: dict[str, Any], kind: str, resource_id: str):
    resp = await client.get(
        f"/api/v1/versions/{kind}/{resource_id}",
        headers=_auth_headers(ctx["alice_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "versions" in body and "pointers" in body
    return body


async def _promote(
    client: AsyncClient,
    ctx: dict[str, Any],
    kind: str,
    resource_id: str,
    from_env: str = "dev",
    to_env: str = "prod",
    **extra: Any,
):
    return await client.post(
        "/api/v1/environments/promote",
        json={
            "kind": kind,
            "resource_id": resource_id,
            "from_env": from_env,
            "to_env": to_env,
            **extra,
        },
        headers=_auth_headers(ctx["alice_id"]),
    )


# ---------------------------------------------------------------------------
# 1. Environments CRUD
# ---------------------------------------------------------------------------


class TestEnvironments:
    @pytest.mark.asyncio
    async def test_list_lazily_creates_dev_and_prod(self, env_ctx):
        """First GET on a brand-new project creates dev+prod with the contract flags."""
        client, ctx = env_ctx

        # A project whose environments have never been listed.
        fresh_pid = str(uuid.uuid4())
        ctx["projects_tbl"][fresh_pid] = {
            "id": fresh_pid, "org_id": ctx["org_id"], "name": "Fresh",
            "slug": "fresh", "created_by": ctx["alice_id"], "git": None,
            "created_at": datetime.now(tz=timezone.utc),
        }

        envs = await _list_envs(client, ctx, project_id=fresh_pid)
        assert len(envs) == 2
        dev, prod = envs[0], envs[1]

        # dev first by position.
        assert dev["key"] == "dev"
        assert dev["name"] == "Development"
        assert dev["is_default"] is False
        assert dev["protected"] is False
        assert dev["position"] == 0

        assert prod["key"] == "prod"
        assert prod["name"] == "Production"
        assert prod["is_default"] is True
        assert prod["protected"] is True
        assert prod["position"] == 1

        # Idempotent: a second list does not duplicate the rows.
        envs_again = await _list_envs(client, ctx, project_id=fresh_pid)
        assert len(envs_again) == 2

    @pytest.mark.asyncio
    async def test_create_custom_environment(self, env_ctx):
        client, ctx = env_ctx
        resp = await client.post(
            f"/api/v1/projects/{ctx['project_id']}/environments",
            json={"key": "staging", "name": "Staging"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 201, resp.text
        env = resp.json()
        assert env["key"] == "staging"
        assert env["name"] == "Staging"
        assert env["is_default"] is False
        assert env["protected"] is False
        assert "id" in env

        keys = [e["key"] for e in await _list_envs(client, ctx)]
        assert "staging" in keys

    @pytest.mark.asyncio
    async def test_create_duplicate_key_rejected(self, env_ctx):
        client, ctx = env_ctx
        resp = await client.post(
            f"/api/v1/projects/{ctx['project_id']}/environments",
            json={"key": "dev", "name": "Development Again"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert 400 <= resp.status_code < 500, resp.text
        # Still exactly one 'dev'.
        keys = [e["key"] for e in await _list_envs(client, ctx)]
        assert keys.count("dev") == 1

    @pytest.mark.asyncio
    async def test_delete_protected_default_env_returns_409(self, env_ctx):
        """prod is both protected and is_default — DELETE must 409."""
        client, ctx = env_ctx
        envs = await _list_envs(client, ctx)
        prod = next(e for e in envs if e["key"] == "prod")

        resp = await client.delete(
            f"/api/v1/environments/{prod['id']}",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 409, resp.text
        assert "prod" in [e["key"] for e in await _list_envs(client, ctx)]

    @pytest.mark.asyncio
    async def test_delete_custom_env_returns_204(self, env_ctx):
        client, ctx = env_ctx
        create = await client.post(
            f"/api/v1/projects/{ctx['project_id']}/environments",
            json={"key": "scratch", "name": "Scratch"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert create.status_code == 201
        env_id = create.json()["id"]

        resp = await client.delete(
            f"/api/v1/environments/{env_id}",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 204, resp.text
        assert "scratch" not in [e["key"] for e in await _list_envs(client, ctx)]

    @pytest.mark.asyncio
    async def test_patch_is_default_clears_other_envs(self, env_ctx):
        client, ctx = env_ctx
        envs = await _list_envs(client, ctx)
        dev = next(e for e in envs if e["key"] == "dev")

        resp = await client.patch(
            f"/api/v1/environments/{dev['id']}",
            json={"is_default": True},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text

        envs = await _list_envs(client, ctx)
        by_key = {e["key"]: e for e in envs}
        assert by_key["dev"]["is_default"] is True
        assert by_key["prod"]["is_default"] is False
        assert sum(1 for e in envs if e["is_default"]) == 1


# ---------------------------------------------------------------------------
# 2. Checkpoints (versions)
# ---------------------------------------------------------------------------


class TestCheckpoints:
    @pytest.mark.asyncio
    async def test_checkpoint_creates_version_1_and_points_dev(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]

        body = await _checkpoint(client, ctx, "query", qid, message="first cut")
        assert _version_number(body) == 1
        assert _deduped(body) is False

        vbody = await _versions(client, ctx, "query", qid)
        assert len(vbody["versions"]) == 1
        ptr = _pointer_for(vbody, "dev")
        assert ptr is not None, "checkpoint must point the dev env at the version"
        assert ptr["version"] == 1

    @pytest.mark.asyncio
    async def test_checkpoint_same_config_dedupes(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]

        first = await _checkpoint(client, ctx, "query", qid, message="v1")
        assert _version_number(first) == 1

        second = await _checkpoint(client, ctx, "query", qid, message="same again")
        assert _version_number(second) == 1, "identical config must not bump the version"
        assert _deduped(second) is True

        vbody = await _versions(client, ctx, "query", qid)
        assert len(vbody["versions"]) == 1

    @pytest.mark.asyncio
    async def test_checkpoint_changed_config_bumps_version(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="v1")

        upd = await client.put(
            f"/api/v1/queries/{qid}",
            json={"config": {"sql": "select 2", "rev": 2}},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert upd.status_code == 200

        body = await _checkpoint(client, ctx, "query", qid, message="v2")
        assert _version_number(body) == 2
        assert _deduped(body) is False

        vbody = await _versions(client, ctx, "query", qid)
        assert len(vbody["versions"]) == 2
        # dev pointer follows the latest checkpoint.
        assert _pointer_for(vbody, "dev")["version"] == 2

    @pytest.mark.asyncio
    async def test_checkpoint_to_protected_env_returns_409(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]

        resp = await client.post(
            f"/api/v1/versions/query/{qid}",
            json={"message": "straight to prod", "env_key": "prod"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 409, resp.text

        # prod must remain unpinned.
        vbody = await _versions(client, ctx, "query", qid)
        assert _pointer_for(vbody, "prod") is None

    @pytest.mark.asyncio
    async def test_versions_list_newest_first_without_config(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="first")
        await client.put(
            f"/api/v1/queries/{qid}",
            json={"config": {"sql": "select 2", "rev": 2}},
            headers=_auth_headers(ctx["alice_id"]),
        )
        await _checkpoint(client, ctx, "query", qid, message="second")

        vbody = await _versions(client, ctx, "query", qid)
        versions = vbody["versions"]
        assert [v["version"] for v in versions] == [2, 1], "newest first"
        for v in versions:
            assert "config" not in v, "list must be summaries without config"
            for key in ("id", "version", "config_hash", "message", "created_by", "created_at"):
                assert key in v, f"summary missing {key}"
        assert versions[0]["message"] == "second"
        assert versions[1]["message"] == "first"

    @pytest.mark.asyncio
    async def test_get_version_returns_config_and_restore_writes_draft(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]
        config_v1 = {"sql": "select 1", "rev": 1}
        config_v2 = {"sql": "select 2", "rev": 2}

        await _checkpoint(client, ctx, "query", qid, message="v1")
        await client.put(
            f"/api/v1/queries/{qid}",
            json={"config": config_v2},
            headers=_auth_headers(ctx["alice_id"]),
        )
        await _checkpoint(client, ctx, "query", qid, message="v2")

        # Full version fetch includes the snapshotted config.
        resp = await client.get(
            f"/api/v1/versions/query/{qid}/1",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        full = resp.json()
        assert full["version"] == 1
        assert full["config"] == config_v1

        # Restore v1 → the draft row's config reverts.
        resp = await client.post(
            f"/api/v1/versions/query/{qid}/1/restore",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text

        draft = await client.get(
            f"/api/v1/queries/{qid}",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert draft.status_code == 200
        assert draft.json()["config"] == config_v1

    @pytest.mark.asyncio
    async def test_unknown_kind_rejected(self, env_ctx):
        client, ctx = env_ctx
        resp = await client.get(
            f"/api/v1/versions/gadget/{ctx['query']['id']}",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code in (400, 404, 422), resp.text


# ---------------------------------------------------------------------------
# 3. Promotion + env resolution
# ---------------------------------------------------------------------------


class TestPromotion:
    @pytest.mark.asyncio
    async def test_promote_query_dev_to_prod_and_env_resolution(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]
        config_v1 = {"sql": "select 1", "rev": 1}
        config_draft = {"sql": "select 99", "rev": 99}

        await _checkpoint(client, ctx, "query", qid, message="v1")

        resp = await _promote(client, ctx, "query", qid, "dev", "prod")
        assert resp.status_code in (200, 201), resp.text

        vbody = await _versions(client, ctx, "query", qid)
        dev_ptr = _pointer_for(vbody, "dev")
        prod_ptr = _pointer_for(vbody, "prod")
        assert prod_ptr is not None, "promote must create the prod pointer"
        assert prod_ptr["version_id"] == dev_ptr["version_id"]
        assert prod_ptr["version"] == 1

        # Mutate the draft so pinned != draft.
        upd = await client.put(
            f"/api/v1/queries/{qid}",
            json={"config": config_draft},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert upd.status_code == 200

        # ?env=prod serves the pinned config + resolved_version stamp.
        resp = await client.get(
            f"/api/v1/queries/{qid}?env=prod",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["config"] == config_v1
        rv = body.get("resolved_version")
        assert rv is not None
        assert rv["version"] == 1
        assert "id" in rv

        # ?env=dev likewise (dev pointer is also at v1).
        resp = await client.get(
            f"/api/v1/queries/{qid}?env=dev",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"] == config_v1
        assert body["resolved_version"]["version"] == 1

        # No env param → plain draft.
        resp = await client.get(
            f"/api/v1/queries/{qid}",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200
        assert resp.json()["config"] == config_draft

    @pytest.mark.asyncio
    async def test_unpinned_env_returns_draft_with_null_resolved_version(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]
        config_draft = {"sql": "select 42", "rev": 42}

        # An env with no pointer for this query.
        create = await client.post(
            f"/api/v1/projects/{ctx['project_id']}/environments",
            json={"key": "staging", "name": "Staging"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert create.status_code == 201

        await _checkpoint(client, ctx, "query", qid, message="v1")  # points dev only
        await client.put(
            f"/api/v1/queries/{qid}",
            json={"config": config_draft},
            headers=_auth_headers(ctx["alice_id"]),
        )

        resp = await client.get(
            f"/api/v1/queries/{qid}?env=staging",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["config"] == config_draft, "unpinned env must serve the draft"
        assert body.get("resolved_version") is None

    @pytest.mark.asyncio
    async def test_promote_without_from_pointer_returns_404(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]
        # No checkpoint at all → dev has no pointer.
        resp = await _promote(client, ctx, "query", qid, "dev", "prod")
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_flow_checkpoint_promote_and_env_resolution(self, env_ctx):
        client, ctx = env_ctx
        fid = ctx["flow"]["id"]

        body = await _checkpoint(client, ctx, "flow", fid, message="flow v1")
        assert _version_number(body) == 1

        # Mutate the draft spec so pinned != draft.
        await ctx["flow_store"].update_flow(fid, {"spec": dict(FLOW_SPEC_V2)})

        resp = await _promote(client, ctx, "flow", fid, "dev", "prod")
        assert resp.status_code in (200, 201), resp.text

        # ?env=prod serves the pinned spec.
        resp = await client.get(
            f"/api/v1/flows/{fid}?env=prod",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        flow_body = resp.json()
        assert flow_body["spec"] == FLOW_SPEC_V1
        rv = flow_body.get("resolved_version")
        assert rv is not None
        assert rv["version"] == 1

        # No env param → draft spec unchanged.
        resp = await client.get(
            f"/api/v1/flows/{fid}",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200
        assert resp.json()["spec"] == FLOW_SPEC_V2

    @pytest.mark.asyncio
    async def test_board_promote_includes_query_dependencies(self, env_ctx):
        client, ctx = env_ctx
        qid = ctx["query"]["id"]

        # Query has a dev pointer.
        await _checkpoint(client, ctx, "query", qid, message="dep v1")

        # Board references the query inside its config.
        create = await client.post(
            "/api/v1/boards",
            json={"name": "B1", "config": {"widgets": [{"query_id": qid}], "rev": 1}},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert create.status_code == 201, create.text
        bid = create.json()["id"]

        await _checkpoint(client, ctx, "board", bid, message="board v1")

        resp = await _promote(client, ctx, "board", bid, "dev", "prod")
        assert resp.status_code in (200, 201), resp.text
        body = resp.json()
        assert isinstance(body.get("promoted"), list)
        assert len(body["promoted"]) >= 2, "board AND its query must be promoted"

        # Board prod pointer exists.
        board_versions = await _versions(client, ctx, "board", bid)
        assert _pointer_for(board_versions, "prod") is not None

        # Referenced query was dragged to prod too.
        query_versions = await _versions(client, ctx, "query", qid)
        q_prod = _pointer_for(query_versions, "prod")
        assert q_prod is not None, "include_dependencies must promote referenced queries"
        assert q_prod["version"] == 1


# ---------------------------------------------------------------------------
# 4. Org isolation + auth
# ---------------------------------------------------------------------------


class TestIsolation:
    @pytest.mark.asyncio
    async def test_cross_org_versions_returns_404(self, env_ctx):
        """A user from another org gets 404 on someone else's resource versions."""
        client, ctx = env_ctx
        qid = ctx["query"]["id"]
        await _checkpoint(client, ctx, "query", qid, message="v1")

        resp = await client.get(
            f"/api/v1/versions/query/{qid}",
            headers=_auth_headers(ctx["bob_id"]),
        )
        assert resp.status_code == 404, "cross-org versions access must 404 (no leak)"

        resp = await client.post(
            f"/api/v1/versions/query/{qid}",
            json={"message": "hijack"},
            headers=_auth_headers(ctx["bob_id"]),
        )
        assert resp.status_code == 404, "cross-org checkpoint must 404 (no leak)"

    @pytest.mark.asyncio
    async def test_cross_org_environments_list_returns_404(self, env_ctx):
        client, ctx = env_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/environments",
            headers=_auth_headers(ctx["bob_id"]),
        )
        assert resp.status_code == 404, "foreign project's environments must 404"

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, env_ctx):
        client, ctx = env_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/environments"
        )
        assert resp.status_code == 401
        resp = await client.get(f"/api/v1/versions/query/{ctx['query']['id']}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 5. Registry-saved queries (POST /query/registry) are versionable
# ---------------------------------------------------------------------------
#
# Regression: a query saved from the UI (QueryWorkspace 'Save' → POST
# /query/registry) used to get a name-slug as its registry id while the
# best-effort persistence created the ``queries`` row under a separate
# DB-generated uuid.  The versioning endpoints resolve /versions/query/{id}
# against the ``queries`` row id, so Checkpoint/History/Restore 404'd for any
# freshly registered query.  The registry id and the row id must be the SAME
# uuid end-to-end.


class TestRegistrySavedQueryVersioning:
    async def _register(self, client, ctx, payload):
        resp = await client.post(
            "/api/v1/query/registry",
            json=payload,
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    @pytest.mark.asyncio
    async def test_fresh_register_returns_the_persisted_row_uuid(self, env_ctx):
        """A no-id save returns a uuid that resolves via GET /queries/{id}."""
        client, ctx = env_ctx
        saved = await self._register(
            client, ctx, {"name": "Revenue by region", "sql": "select 1 as v"}
        )
        qid = saved["id"]
        uuid.UUID(qid)  # the canonical id is the row uuid, not a name-slug

        resp = await client.get(
            f"/api/v1/queries/{qid}", headers=_auth_headers(ctx["alice_id"])
        )
        assert resp.status_code == 200, resp.text
        row = resp.json()
        assert str(row["id"]) == qid
        assert row["config"]["sql"] == "select 1 as v"

    @pytest.mark.asyncio
    async def test_fresh_register_then_checkpoint_history_restore(self, env_ctx):
        """Checkpoint + history + restore all work with the registry-returned id."""
        client, ctx = env_ctx
        saved = await self._register(
            client, ctx, {"name": "Daily sales", "sql": "select 1 as day1"}
        )
        qid = saved["id"]

        # Checkpoint v1 with the id the registry returned.
        body = await _checkpoint(client, ctx, "query", qid, message="v1")
        assert _version_number(body) == 1

        # Re-save through the same UI path (id now included) → same row, v2.
        saved2 = await self._register(
            client,
            ctx,
            {"id": qid, "name": "Daily sales", "sql": "select 2 as day2"},
        )
        assert saved2["id"] == qid
        body = await _checkpoint(client, ctx, "query", qid, message="v2")
        assert _version_number(body) == 2

        # History lists both versions; dev points at the latest.
        vbody = await _versions(client, ctx, "query", qid)
        assert [v["version"] for v in vbody["versions"]] == [2, 1]
        assert _pointer_for(vbody, "dev")["version"] == 2

        # Full version fetch returns the v1 snapshot.
        resp = await client.get(
            f"/api/v1/versions/query/{qid}/1",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["config"]["sql"] == "select 1 as day1"

        # Restore v1 → the draft row's config reverts to the v1 SQL.
        resp = await client.post(
            f"/api/v1/versions/query/{qid}/1/restore",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text

        draft = await client.get(
            f"/api/v1/queries/{qid}", headers=_auth_headers(ctx["alice_id"])
        )
        assert draft.status_code == 200
        assert draft.json()["config"]["sql"] == "select 1 as day1"

    @pytest.mark.asyncio
    async def test_reregister_without_id_upserts_the_same_row(self, env_ctx):
        """Saving the same name twice without an id updates one row (no dupes)."""
        client, ctx = env_ctx
        first = await self._register(
            client, ctx, {"name": "Weekly totals", "sql": "select 1"}
        )
        second = await self._register(
            client, ctx, {"name": "Weekly totals", "sql": "select 2"}
        )
        assert second["id"] == first["id"], "same name must upsert the same row"

        rows = await ctx["repo"].list("queries", ctx["org_id"])
        matches = [r for r in rows if r["name"] == "Weekly totals"]
        assert len(matches) == 1, "re-registering must not create duplicate rows"
        assert matches[0]["config"]["sql"] == "select 2"
        assert str(matches[0]["id"]) == first["id"]

    @pytest.mark.asyncio
    async def test_reregister_with_returned_uuid_updates_the_same_row(self, env_ctx):
        """Saving again with the returned id (UI second save) updates in place."""
        client, ctx = env_ctx
        first = await self._register(
            client, ctx, {"name": "Monthly totals", "sql": "select 1"}
        )
        qid = first["id"]
        second = await self._register(
            client, ctx, {"id": qid, "name": "Monthly totals", "sql": "select 3"}
        )
        assert second["id"] == qid

        rows = await ctx["repo"].list("queries", ctx["org_id"])
        matches = [r for r in rows if r["name"] == "Monthly totals"]
        assert len(matches) == 1
        assert matches[0]["config"]["sql"] == "select 3"

    @pytest.mark.asyncio
    async def test_slug_id_registration_stays_registry_only(self, env_ctx):
        """Explicit non-uuid (slug) ids keep the legacy registry-only contract."""
        client, ctx = env_ctx
        saved = await self._register(
            client,
            ctx,
            {"id": "embed_allowlist_q", "name": "Embed query", "sql": "select 1"},
        )
        assert saved["id"] == "embed_allowlist_q"

        # No queries row is created for slug ids (row PKs are uuids).
        rows = await ctx["repo"].list("queries", ctx["org_id"])
        assert all(r["name"] != "Embed query" for r in rows)
