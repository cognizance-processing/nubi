"""CORE-phase contract tests — strict environments + project scoping + git fields.

Contract under test (implementation may land in parallel; these tests encode
the agreed contract, not the current behaviour):

1. PROJECT SCOPING
   - GET /flows is project-scoped: the ``X-Project-Id`` header switches the
     list; a flow created in project A is invisible when listing project B.
   - GET /query/registry is scoped to the caller's org + active project:
     persisted queries belonging to another project are excluded; slug-only
     registry entries (embed allowlist) are excluded from first-party
     project browsing.

2. STRICT PROTECTED-ENV VISIBILITY
   - List endpoints for boards/queries/flows ALWAYS include ``pinned_envs``
     per row: ``[]`` for unpromoted resources, ``['dev']`` after checkpoint,
     ``['dev','prod']`` after promote.
   - First-party org members always see drafts: an unpromoted board fetched
     with ``?env=prod`` returns the draft config + ``resolved_version: null``.

3. GIT-ENV FIELDS
   - environments carry ``git_branch`` (creation default ``'main'`` for
     ``key='prod'``, else the key) and ``last_synced_sha``.
   - resource versions carry ``parent_version_id`` (lineage chain:
     v2.parent == v1.id) and a ``git_commit_sha`` key.

4. FLOW ENV RESOLUTION (spec.env removed)
   - ``FlowSpec`` has NO ``env`` field; an incoming legacy ``env`` key is
     stripped (ignored, not an error).
   - A run without an explicit env override lands on the flow's PROJECT
     DEFAULT environment key (``is_default`` via the env store) — the spec
     is never consulted.

Strategy (house pattern — see test_environments_versions.py)
------------------------------------------------------------
- conftest ``app`` fixture patches all app.db helpers with FakeDB.
- ``InMemoryRepo`` via ``set_repo()``; ``InMemoryFlowStore`` via
  ``set_flow_store()``; ``InMemoryEnvStore`` via ``set_env_store()``.
- A dict-backed projects table is layered over ``app.db.fetchrow``/``fetch``
  (the projects repo calls through the module object, so the patch lands).
- One org with TWO projects (A is the oldest → the org default), so the
  ``X-Project-Id`` header meaningfully switches the active project.
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

# Self-registering router (idempotent; main.py imports it too).
import app.routes.environments  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: A minimal VALID flow spec (passes validate_flow_spec) — used wherever the
#: flow is created through the API or executed via POST /flows/{id}/run.
VALID_FLOW_SPEC: dict[str, Any] = {
    "version": 1,
    "name": "core-strict-flow",
    "tasks": [{"key": "t1", "kind": "noop"}],
}


def _hdrs(user_id: str, project_id: str | None = None) -> dict[str, str]:
    """Auth header + optional active-project header."""
    headers = {"Authorization": f"Bearer {mint_access_token(user_id)}"}
    if project_id is not None:
        headers["X-Project-Id"] = project_id
    return headers


def _make_user(user_id: str, email: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc),
    }


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
            return [
                dict(p)
                for p in sorted(projects.values(), key=lambda r: r["created_at"])
                if org_id is None or p["org_id"] == org_id
            ]
        return await fallback_fetch(query, *args)

    return _fetchrow, _fetch


# ---------------------------------------------------------------------------
# Fixture: one org + user + TWO projects (A = default, B = secondary), all
# stores injected, dev+prod environments ensured for both projects.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def core_ctx(app, fake_db):
    """Yield ``(client, ctx)`` with stores injected and a two-project org."""
    repo = InMemoryRepo()
    set_repo(repo)
    flow_store = InMemoryFlowStore()
    set_flow_store(flow_store)
    env_store = InMemoryEnvStore()
    set_env_store(env_store)

    alice_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_a = str(uuid.uuid4())
    project_b = str(uuid.uuid4())

    fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
    repo.seed_org_member(org_id=org_id, user_id=alice_id)

    now = datetime.now(tz=timezone.utc)
    projects_tbl: dict[str, dict[str, Any]] = {
        # Project A is the OLDEST row → the org default project.
        project_a: {
            "id": project_a, "org_id": org_id, "name": "Alpha",
            "slug": "alpha", "created_by": alice_id, "git": None,
            "created_at": now,
        },
        project_b: {
            "id": project_b, "org_id": org_id, "name": "Beta",
            "slug": "beta", "created_by": alice_id, "git": None,
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
            # Defensive: also patch module-local bindings if a routes module
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
                # Ensure dev+prod exist for both projects (lazy creation).
                for pid in (project_a, project_b):
                    resp = await client.get(
                        f"/api/v1/projects/{pid}/environments",
                        headers=_hdrs(alice_id),
                    )
                    assert resp.status_code == 200, resp.text

                ctx = {
                    "alice_id": alice_id,
                    "org_id": org_id,
                    "project_a": project_a,
                    "project_b": project_b,
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


async def _create_resource(
    client: AsyncClient,
    ctx: dict[str, Any],
    resource: str,
    name: str,
    config: dict[str, Any],
    project_id: str | None = None,
) -> dict[str, Any]:
    resp = await client.post(
        f"/api/v1/{resource}",
        json={"name": name, "config": config},
        headers=_hdrs(ctx["alice_id"], project_id or ctx["project_a"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_flow(
    client: AsyncClient,
    ctx: dict[str, Any],
    name: str,
    project_id: str | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resp = await client.post(
        "/api/v1/flows",
        json={"name": name, "spec": spec or dict(VALID_FLOW_SPEC)},
        headers=_hdrs(ctx["alice_id"], project_id or ctx["project_a"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _checkpoint(
    client: AsyncClient,
    ctx: dict[str, Any],
    kind: str,
    resource_id: str,
    message: str = "checkpoint",
) -> dict[str, Any]:
    resp = await client.post(
        f"/api/v1/versions/{kind}/{resource_id}",
        json={"message": message},
        headers=_hdrs(ctx["alice_id"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _promote(
    client: AsyncClient,
    ctx: dict[str, Any],
    kind: str,
    resource_id: str,
    from_env: str = "dev",
    to_env: str = "prod",
):
    resp = await client.post(
        "/api/v1/environments/promote",
        json={
            "kind": kind,
            "resource_id": resource_id,
            "from_env": from_env,
            "to_env": to_env,
        },
        headers=_hdrs(ctx["alice_id"]),
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _versions(client: AsyncClient, ctx: dict[str, Any], kind: str, resource_id: str):
    resp = await client.get(
        f"/api/v1/versions/{kind}/{resource_id}",
        headers=_hdrs(ctx["alice_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "versions" in body and "pointers" in body
    return body


def _row_by_id(rows: list[dict[str, Any]], row_id: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("id")) == str(row_id):
            return row
    return None


async def _list(
    client: AsyncClient, ctx: dict[str, Any], resource: str, project_id: str
) -> list[dict[str, Any]]:
    resp = await client.get(
        f"/api/v1/{resource}",
        headers=_hdrs(ctx["alice_id"], project_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    return body


# ---------------------------------------------------------------------------
# 1. Flows list is project-scoped
# ---------------------------------------------------------------------------


class TestFlowsListProjectScoped:
    @pytest.mark.asyncio
    async def test_x_project_id_header_switches_the_flow_list(self, core_ctx):
        """A flow created in project A is invisible when listing project B."""
        client, ctx = core_ctx
        flow_a = await _create_flow(client, ctx, "flow-in-a", ctx["project_a"])
        flow_b = await _create_flow(client, ctx, "flow-in-b", ctx["project_b"])

        list_a = await _list(client, ctx, "flows", ctx["project_a"])
        ids_a = {str(f["id"]) for f in list_a}
        assert str(flow_a["id"]) in ids_a, "project A's flow must be listed for A"
        assert str(flow_b["id"]) not in ids_a, "project B's flow must NOT leak into A's list"

        list_b = await _list(client, ctx, "flows", ctx["project_b"])
        ids_b = {str(f["id"]) for f in list_b}
        assert str(flow_b["id"]) in ids_b, "project B's flow must be listed for B"
        assert str(flow_a["id"]) not in ids_b, "project A's flow must NOT leak into B's list"

    @pytest.mark.asyncio
    async def test_no_header_lists_the_default_project_only(self, core_ctx):
        """Without X-Project-Id the list falls back to the org's default project (A)."""
        client, ctx = core_ctx
        flow_a = await _create_flow(client, ctx, "default-flow", ctx["project_a"])
        flow_b = await _create_flow(client, ctx, "other-flow", ctx["project_b"])

        resp = await client.get("/api/v1/flows", headers=_hdrs(ctx["alice_id"]))
        assert resp.status_code == 200, resp.text
        ids = {str(f["id"]) for f in resp.json()}
        assert str(flow_a["id"]) in ids
        assert str(flow_b["id"]) not in ids, (
            "headerless list must scope to the default project, not the whole org"
        )


# ---------------------------------------------------------------------------
# 2. Query registry list is org/project scoped
# ---------------------------------------------------------------------------


class TestQueryRegistryProjectScoped:
    async def _register(self, client, ctx, payload, project_id):
        resp = await client.post(
            "/api/v1/query/registry",
            json=payload,
            headers=_hdrs(ctx["alice_id"], project_id),
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    async def _registry_ids(self, client, ctx, project_id) -> set[str]:
        resp = await client.get(
            "/api/v1/query/registry",
            headers=_hdrs(ctx["alice_id"], project_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "queries" in body
        return {str(q["id"]) for q in body["queries"]}

    @pytest.mark.asyncio
    async def test_registry_list_excludes_other_projects_queries(self, core_ctx):
        """GET /query/registry only returns the active project's persisted queries."""
        client, ctx = core_ctx
        saved_a = await self._register(
            client, ctx, {"name": "Alpha metric", "sql": "select 1 as a"}, ctx["project_a"]
        )
        saved_b = await self._register(
            client, ctx, {"name": "Beta metric", "sql": "select 2 as b"}, ctx["project_b"]
        )

        ids_a = await self._registry_ids(client, ctx, ctx["project_a"])
        assert saved_a["id"] in ids_a, "project A's saved query must be listed for A"
        assert saved_b["id"] not in ids_a, (
            "project B's persisted query must be excluded from project A's registry list"
        )

        ids_b = await self._registry_ids(client, ctx, ctx["project_b"])
        assert saved_b["id"] in ids_b, "project B's saved query must be listed for B"
        assert saved_a["id"] not in ids_b, (
            "project A's persisted query must be excluded from project B's registry list"
        )

    @pytest.mark.asyncio
    async def test_slug_only_entries_excluded_from_first_party_browsing(self, core_ctx):
        """Slug-only (embed allowlist) registry entries don't appear in first-party lists."""
        client, ctx = core_ctx
        saved = await self._register(
            client,
            ctx,
            {"id": "embed_allowlist_q", "name": "Embed-only", "sql": "select 1"},
            ctx["project_a"],
        )
        assert saved["id"] == "embed_allowlist_q"

        ids_a = await self._registry_ids(client, ctx, ctx["project_a"])
        assert "embed_allowlist_q" not in ids_a, (
            "slug-only registry entries (no persisted row) are embed-allowlist "
            "internals and must be excluded from first-party project browsing"
        )


# ---------------------------------------------------------------------------
# 3. pinned_envs on list rows (boards / queries / flows)
# ---------------------------------------------------------------------------


class TestPinnedEnvsInLists:
    async def _assert_lifecycle(self, client, ctx, resource: str, kind: str, row_id: str):
        """Shared assertion: [] → ['dev'] → {'dev','prod'} across the lifecycle."""
        # Unpromoted: pinned_envs present and empty.
        row = _row_by_id(await _list(client, ctx, resource, ctx["project_a"]), row_id)
        assert row is not None, f"{resource} list must include the created row"
        assert "pinned_envs" in row, f"{resource} list rows must ALWAYS carry pinned_envs"
        assert row["pinned_envs"] == [], "unpromoted resources have no env pointers"

        # Checkpoint → pinned in dev only.
        await _checkpoint(client, ctx, kind, row_id)
        row = _row_by_id(await _list(client, ctx, resource, ctx["project_a"]), row_id)
        assert row["pinned_envs"] == ["dev"], "checkpoint pins the default-checkpoint env (dev)"

        # Promote dev → prod → pinned in both.
        await _promote(client, ctx, kind, row_id)
        row = _row_by_id(await _list(client, ctx, resource, ctx["project_a"]), row_id)
        assert set(row["pinned_envs"]) == {"dev", "prod"}, "promote adds the prod pointer"
        assert len(row["pinned_envs"]) == 2

    @pytest.mark.asyncio
    async def test_query_list_rows_carry_pinned_envs_lifecycle(self, core_ctx):
        """queries list: pinned_envs [] → ['dev'] → ['dev','prod']."""
        client, ctx = core_ctx
        query = await _create_resource(
            client, ctx, "queries", "Q-pinned", {"sql": "select 1", "rev": 1}
        )
        await self._assert_lifecycle(client, ctx, "queries", "query", str(query["id"]))

    @pytest.mark.asyncio
    async def test_board_list_rows_carry_pinned_envs_lifecycle(self, core_ctx):
        """boards list: pinned_envs [] → ['dev'] → ['dev','prod']."""
        client, ctx = core_ctx
        board = await _create_resource(
            client, ctx, "boards", "B-pinned", {"layout": [], "rev": 1}
        )
        await self._assert_lifecycle(client, ctx, "boards", "board", str(board["id"]))

    @pytest.mark.asyncio
    async def test_flow_list_rows_carry_pinned_envs_lifecycle(self, core_ctx):
        """flows list: pinned_envs [] → ['dev'] → ['dev','prod']."""
        client, ctx = core_ctx
        flow = await _create_flow(client, ctx, "flow-pinned", ctx["project_a"])
        await self._assert_lifecycle(client, ctx, "flows", "flow", str(flow["id"]))


# ---------------------------------------------------------------------------
# 4. New-resource semantics in protected envs (org members see drafts)
# ---------------------------------------------------------------------------


class TestProtectedEnvDraftVisibility:
    @pytest.mark.asyncio
    async def test_unpromoted_board_env_prod_returns_draft_for_member(self, core_ctx):
        """GET /boards/{id}?env=prod on an unpromoted board: draft + null resolved_version."""
        client, ctx = core_ctx
        config_draft = {"layout": [{"w": 1}], "rev": 1}
        board = await _create_resource(client, ctx, "boards", "B-draft", config_draft)

        resp = await client.get(
            f"/api/v1/boards/{board['id']}?env=prod",
            headers=_hdrs(ctx["alice_id"], ctx["project_a"]),
        )
        assert resp.status_code == 200, (
            "first-party org members always see drafts, even in a protected env"
        )
        body = resp.json()
        assert body["config"] == config_draft, "unpinned protected env serves the draft"
        assert body.get("resolved_version") is None, (
            "no prod pointer → resolved_version must be null"
        )


# ---------------------------------------------------------------------------
# 5. New env-store fields: git_branch + version lineage / git_commit_sha
# ---------------------------------------------------------------------------


class TestEnvGitFields:
    @pytest.mark.asyncio
    async def test_environments_carry_git_branch_defaults(self, core_ctx):
        """dev → git_branch 'dev'; prod → git_branch 'main'; last_synced_sha present."""
        client, ctx = core_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_a']}/environments",
            headers=_hdrs(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        by_key = {e["key"]: e for e in resp.json()}

        assert by_key["dev"]["git_branch"] == "dev", "non-prod envs default to their key"
        assert by_key["prod"]["git_branch"] == "main", "prod defaults to the 'main' branch"
        for env in by_key.values():
            assert "last_synced_sha" in env, "environments must expose last_synced_sha"

    @pytest.mark.asyncio
    async def test_custom_environment_git_branch_defaults_to_its_key(self, core_ctx):
        client, ctx = core_ctx
        resp = await client.post(
            f"/api/v1/projects/{ctx['project_a']}/environments",
            json={"key": "staging", "name": "Staging"},
            headers=_hdrs(ctx["alice_id"]),
        )
        assert resp.status_code == 201, resp.text
        env = resp.json()
        assert env["git_branch"] == "staging"
        assert env.get("last_synced_sha") is None

    @pytest.mark.asyncio
    async def test_version_parent_chain_and_git_commit_sha_key(self, core_ctx):
        """v1.parent is null, v2.parent == v1.id; every version carries git_commit_sha."""
        client, ctx = core_ctx
        query = await _create_resource(
            client, ctx, "queries", "Q-chain", {"sql": "select 1", "rev": 1}
        )
        qid = str(query["id"])

        await _checkpoint(client, ctx, "query", qid, message="v1")
        upd = await client.put(
            f"/api/v1/queries/{qid}",
            json={"config": {"sql": "select 2", "rev": 2}},
            headers=_hdrs(ctx["alice_id"], ctx["project_a"]),
        )
        assert upd.status_code == 200, upd.text
        await _checkpoint(client, ctx, "query", qid, message="v2")

        vbody = await _versions(client, ctx, "query", qid)
        versions = vbody["versions"]
        assert [v["version"] for v in versions] == [2, 1], "newest first"
        v2, v1 = versions[0], versions[1]

        for v in (v1, v2):
            assert "parent_version_id" in v, "versions must expose parent_version_id"
            assert "git_commit_sha" in v, "versions must expose git_commit_sha"

        assert v1["parent_version_id"] is None, "the first version has no parent"
        assert v2["parent_version_id"] == v1["id"], (
            "v2 must chain to v1 via parent_version_id"
        )


# ---------------------------------------------------------------------------
# 6. Flow run env resolution — spec.env is gone
# ---------------------------------------------------------------------------


class TestFlowRunEnvResolution:
    def test_flow_spec_has_no_env_field(self):
        """The FlowSpec model itself must not declare an env field."""
        from app.flows.spec import FlowSpec

        assert "env" not in FlowSpec.model_fields, (
            "spec.env was removed — environment is resolved at trigger time"
        )

    def test_validate_strips_legacy_env_key_without_error(self):
        """A legacy 'env' key in an incoming spec is ignored, not an error."""
        from app.flows.spec import flow_spec_is_valid, validate_flow_spec

        legacy = dict(VALID_FLOW_SPEC)
        legacy["env"] = "dev"
        spec, issues = validate_flow_spec(legacy)
        assert spec is not None, f"legacy env key must not break validation: {issues}"
        assert flow_spec_is_valid(issues), f"legacy env key must not be a hard error: {issues}"
        assert "env" not in spec.model_dump(), "the env key must be stripped from the spec"

    @pytest.mark.asyncio
    async def test_create_flow_strips_incoming_spec_env(self, core_ctx):
        """POST /flows with a legacy spec.env succeeds and persists no env key."""
        client, ctx = core_ctx
        legacy = dict(VALID_FLOW_SPEC)
        legacy["env"] = "dev"
        flow = await _create_flow(client, ctx, "legacy-env-flow", ctx["project_a"], spec=legacy)
        assert "env" not in (flow.get("spec") or {}), (
            "the stored draft spec must not carry the legacy env key"
        )

    @pytest.mark.asyncio
    async def test_run_without_env_lands_on_project_default_env(self, core_ctx):
        """flow_run.env == the project's default env key (not a hardcoded 'prod')."""
        client, ctx = core_ctx
        flow = await _create_flow(client, ctx, "runnable-flow", ctx["project_a"])
        fid = str(flow["id"])

        # Make 'dev' the project default so the resolved env is distinguishable
        # from the literal 'prod' fallback.
        envs_resp = await client.get(
            f"/api/v1/projects/{ctx['project_a']}/environments",
            headers=_hdrs(ctx["alice_id"]),
        )
        assert envs_resp.status_code == 200
        dev = next(e for e in envs_resp.json() if e["key"] == "dev")
        patched = await client.patch(
            f"/api/v1/environments/{dev['id']}",
            json={"is_default": True},
            headers=_hdrs(ctx["alice_id"]),
        )
        assert patched.status_code == 200, patched.text

        run_resp = await client.post(
            f"/api/v1/flows/{fid}/run",
            json={},
            headers=_hdrs(ctx["alice_id"], ctx["project_a"]),
        )
        assert run_resp.status_code == 200, run_resp.text
        run_id = run_resp.json()["id"]

        # The persisted flow_run carries the resolved env — the project's
        # default env key, NOT 'prod' and NOT anything from the spec.
        stored_run = await ctx["flow_store"].get_flow_run(run_id)
        assert stored_run is not None
        assert stored_run["env"] == "dev", (
            "run without explicit env must resolve to the project's default env key"
        )
