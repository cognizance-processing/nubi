"""Tests for the GIT-ENV phase — env<->branch git system (contract tests).

Feature under test (decision 5 of the migration-fold/strict-env/git-env
contract; routes in app/routes/environments.py + app/git/sync.py):

- environments carry ``git_branch`` (creation default: 'main' for key='prod',
  else the key) and ``last_synced_sha``.
- POST /projects/{pid}/environments accepts optional ``git_branch`` and
  ``from_branch`` (imports queries/ + dashboards/ files from that branch into
  resource_versions, pinned to the new env, git_commit_sha = branch head;
  missing repo → env created empty with a ``warning`` field).
- Checkpoint commits the serialized resource to the pinned env's branch
  (default dev), stamping ``git_commit_sha`` and chaining
  ``parent_version_id``.
- Promote copies pointers THEN best-effort merges the from-env branch into
  the to-env branch.
- POST /environments/{env_id}/git/push serializes ALL pinned resources in
  ONE commit and updates ``last_synced_sha``.
- POST /environments/{env_id}/git/pull: no-op at last_synced; fast-forward →
  new versions (parent = current pinned) + repoint + last_synced update;
  diverged → 409 {diverged: true, ...} unless strategy take_branch/take_env.
- GET /projects/{pid}/git/graph returns env-bound branches with commits.
- ALL git ops are best-effort: with an unusable NUBI_GIT_WORKSPACE the DB
  side still succeeds (no 5xx).

Strategy (house pattern — see test_environments_versions.py)
------------------------------------------------------------
- conftest ``app`` fixture patches app.db with FakeDB; ``InMemoryRepo`` via
  ``set_repo()``; ``InMemoryEnvStore`` via ``set_env_store()``;
  ``InMemoryFlowStore`` via ``set_flow_store()``.
- A dict-backed projects table is layered over ``app.db.fetchrow/fetch``.
- A REAL git workspace: ``NUBI_GIT_WORKSPACE`` is pointed at a tmp_path so
  GitSync operates on disk (local commits only — no remote, no network).
  The per-project repo lives at ``<ws>/<org_id>/projects/<project_id>``
  (see app/routes/git.py ``_project_repo_dir``).  GitPython is used directly
  in tests to seed branches / advance them externally / inspect heads.
"""

from __future__ import annotations

import json
import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import git as gitpython
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.environments.store import InMemoryEnvStore, set_env_store
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Self-registering router (idempotent; main.py imports it too).
import app.routes.environments  # noqa: F401, E402


SQL_V1 = "select 1 as v"
SQL_V2 = "select 2 as v"
SQL_EXTERNAL = "select 100 as v -- external"
SQL_DIVERGENT = "select 777 as v -- divergent"


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


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


def _make_projects_db(projects: dict[str, dict[str, Any]], fallback_fetchrow, fallback_fetch):
    """Dict-backed projects table over app.db (copy of the house helper)."""

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
# Git helpers (GitPython, on-disk)
# ---------------------------------------------------------------------------


def _project_repo_dir(ws_root: Path, org_id: str, project_id: str) -> Path:
    """Per-project workspace repo dir (mirrors routes/git.py _project_repo_dir)."""
    return Path(ws_root) / str(org_id) / "projects" / str(project_id)


def _open_repo(repo_dir: Path) -> gitpython.Repo:
    return gitpython.Repo(str(repo_dir))


def _branch_head(repo_dir: Path, branch: str) -> str:
    """Return the head sha of *branch*."""
    return str(_open_repo(repo_dir).commit(branch).hexsha)


def _file_at(repo_dir: Path, branch: str, path: str) -> str | None:
    """Return file contents at the branch head, or None if absent."""
    try:
        blob = _open_repo(repo_dir).commit(branch).tree / path
    except KeyError:
        return None
    return blob.data_stream.read().decode("utf-8")


def _commit_count(repo_dir: Path, branch: str) -> int:
    return sum(1 for _ in _open_repo(repo_dir).iter_commits(branch))


def _commit_shas(repo_dir: Path, branch: str) -> list[str]:
    return [str(c.hexsha) for c in _open_repo(repo_dir).iter_commits(branch)]


def _external_commit(
    repo_dir: Path,
    branch: str,
    files: dict[str, str],
    message: str = "external change",
) -> str:
    """Commit *files* directly onto *branch* (simulates an outside actor)."""
    repo = _open_repo(repo_dir)
    repo.git.checkout(branch)
    for rel, content in files.items():
        p = Path(repo.working_tree_dir) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    repo.index.add(list(files))
    actor = gitpython.Actor("External", "external@example.com")
    return str(repo.index.commit(message, author=actor, committer=actor).hexsha)


def _reset_branch(repo_dir: Path, branch: str, sha: str) -> None:
    """Hard-reset *branch* to *sha* (rewrites history → divergence)."""
    repo = _open_repo(repo_dir)
    repo.git.checkout(branch)
    repo.git.reset("--hard", sha)


def _seed_repo_with_branch(repo_dir: Path, branch: str, files: dict[str, str]) -> str:
    """Init the project workspace repo with *files* committed on *branch*."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    repo = gitpython.Repo.init(str(repo_dir))
    with repo.config_writer() as cw:
        cw.set_value("user", "email", "seeder@example.com")
        cw.set_value("user", "name", "Seeder")
    repo.git.checkout("-b", branch)  # unborn HEAD → branch is born at first commit
    for rel, content in files.items():
        p = Path(repo.working_tree_dir) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    repo.index.add(list(files))
    actor = gitpython.Actor("Seeder", "seeder@example.com")
    return str(repo.index.commit("seed branch", author=actor, committer=actor).hexsha)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _workspace(app, fake_db):
    """Yield (client, ctx) with stores injected and a seeded org/project/query/board."""
    repo = InMemoryRepo()
    set_repo(repo)
    flow_store = InMemoryFlowStore()
    set_flow_store(flow_store)
    env_store = InMemoryEnvStore()
    set_env_store(env_store)

    alice_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
    repo.seed_org_member(org_id=org_id, user_id=alice_id)

    now = datetime.now(tz=timezone.utc)
    projects_tbl: dict[str, dict[str, Any]] = {
        project_id: {
            "id": project_id, "org_id": org_id, "name": "Default",
            "slug": "default", "created_by": alice_id, "git": None,
            "created_at": now,
        },
    }
    fetchrow_fake, fetch_fake = _make_projects_db(
        projects_tbl, fake_db.fake_fetchrow, fake_db.fake_fetch
    )

    try:
        with ExitStack() as stack:
            stack.enter_context(patch("app.db.fetchrow", side_effect=fetchrow_fake))
            stack.enter_context(patch("app.db.fetch", side_effect=fetch_fake))

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client:
                headers = _auth_headers(alice_id)

                resp = await client.post(
                    "/api/v1/queries",
                    json={"name": "Q1", "config": {"sql": SQL_V1}},
                    headers=headers,
                )
                assert resp.status_code == 201, resp.text
                query = resp.json()

                resp = await client.post(
                    "/api/v1/boards",
                    json={"name": "B1", "config": {"title": "B1", "layout": []}},
                    headers=headers,
                )
                assert resp.status_code == 201, resp.text
                board = resp.json()

                # Ensure dev+prod exist (lazy creation).
                resp = await client.get(
                    f"/api/v1/projects/{project_id}/environments",
                    headers=headers,
                )
                assert resp.status_code == 200, resp.text
                envs = resp.json()

                ctx = {
                    "alice_id": alice_id,
                    "org_id": org_id,
                    "project_id": project_id,
                    "query": query,
                    "board": board,
                    "envs": envs,
                    "repo": repo,
                    "env_store": env_store,
                    "projects_tbl": projects_tbl,
                }
                yield client, ctx
    finally:
        set_env_store(None)
        set_repo(None)


@pytest_asyncio.fixture
async def git_ctx(app, fake_db, tmp_path, monkeypatch):
    """(client, ctx) with a REAL on-disk git workspace under tmp_path."""
    ws_root = tmp_path / "gitws"
    monkeypatch.setenv("NUBI_GIT_WORKSPACE", str(ws_root))
    async with _workspace(app, fake_db) as (client, ctx):
        ctx["ws_root"] = ws_root
        ctx["repo_dir"] = _project_repo_dir(ws_root, ctx["org_id"], ctx["project_id"])
        yield client, ctx


@pytest_asyncio.fixture
async def nogit_ctx(app, fake_db, tmp_path, monkeypatch):
    """(client, ctx) with an UNUSABLE git workspace (a file blocks the path).

    Every git operation must fail internally; the contract requires data
    operations to still succeed (best-effort git, never fatal).
    """
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("blocks workspace creation", encoding="utf-8")
    monkeypatch.setenv("NUBI_GIT_WORKSPACE", str(blocker / "ws"))
    async with _workspace(app, fake_db) as (client, ctx):
        yield client, ctx


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


async def _list_envs(client: AsyncClient, ctx: dict[str, Any], project_id: str | None = None):
    resp = await client.get(
        f"/api/v1/projects/{project_id or ctx['project_id']}/environments",
        headers=_auth_headers(ctx["alice_id"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _env_by_key(client: AsyncClient, ctx: dict[str, Any], key: str) -> dict[str, Any]:
    envs = await _list_envs(client, ctx)
    return next(e for e in envs if e["key"] == key)


async def _create_env(client: AsyncClient, ctx: dict[str, Any], payload: dict[str, Any]):
    return await client.post(
        f"/api/v1/projects/{ctx['project_id']}/environments",
        json=payload,
        headers=_auth_headers(ctx["alice_id"]),
    )


async def _checkpoint(
    client: AsyncClient,
    ctx: dict[str, Any],
    kind: str,
    resource_id: str,
    *,
    message: str | None = None,
    expect: int = 201,
):
    payload: dict[str, Any] = {}
    if message is not None:
        payload["message"] = message
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


async def _full_version(
    client: AsyncClient, ctx: dict[str, Any], kind: str, resource_id: str, version: int
):
    resp = await client.get(
        f"/api/v1/versions/{kind}/{resource_id}/{version}",
        headers=_auth_headers(ctx["alice_id"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pointer_for(versions_body: dict[str, Any], env_key: str) -> dict[str, Any] | None:
    for ptr in versions_body.get("pointers", []):
        if ptr.get("env_key") == env_key:
            return ptr
    return None


async def _promote(
    client: AsyncClient,
    ctx: dict[str, Any],
    kind: str,
    resource_id: str,
    from_env: str = "dev",
    to_env: str = "prod",
):
    return await client.post(
        "/api/v1/environments/promote",
        json={
            "kind": kind,
            "resource_id": resource_id,
            "from_env": from_env,
            "to_env": to_env,
        },
        headers=_auth_headers(ctx["alice_id"]),
    )


async def _push(client: AsyncClient, ctx: dict[str, Any], env_id: str):
    return await client.post(
        f"/api/v1/environments/{env_id}/git/push",
        json={},
        headers=_auth_headers(ctx["alice_id"]),
    )


async def _pull(
    client: AsyncClient, ctx: dict[str, Any], env_id: str, body: dict[str, Any] | None = None
):
    return await client.post(
        f"/api/v1/environments/{env_id}/git/pull",
        json=body or {},
        headers=_auth_headers(ctx["alice_id"]),
    )


async def _set_query_sql(client: AsyncClient, ctx: dict[str, Any], sql: str):
    resp = await client.put(
        f"/api/v1/queries/{ctx['query']['id']}",
        json={"config": {"sql": sql}},
        headers=_auth_headers(ctx["alice_id"]),
    )
    assert resp.status_code == 200, resp.text


def _diverged_payload(body: Any) -> dict[str, Any]:
    """Extract the diverged payload from a 409 body (tolerates envelopes)."""
    if isinstance(body, dict):
        for candidate in (body, body.get("detail"), body.get("error")):
            if isinstance(candidate, dict) and "diverged" in candidate:
                return candidate
    raise AssertionError(f"No diverged payload in 409 body: {body!r}")


# ---------------------------------------------------------------------------
# 1. Env creation: git_branch defaults + explicit git_branch
# ---------------------------------------------------------------------------


class TestEnvBranchDefaults:
    @pytest.mark.asyncio
    async def test_dev_and_prod_branch_defaults(self, git_ctx):
        """dev → branch 'dev'; prod → branch 'main'; last_synced_sha starts null."""
        client, ctx = git_ctx
        envs = await _list_envs(client, ctx)
        by_key = {e["key"]: e for e in envs}

        assert by_key["dev"]["git_branch"] == "dev"
        assert by_key["prod"]["git_branch"] == "main"
        for env in envs:
            assert "last_synced_sha" in env
            assert env["last_synced_sha"] is None

    @pytest.mark.asyncio
    async def test_custom_env_branch_defaults_to_key(self, git_ctx):
        client, ctx = git_ctx
        resp = await _create_env(client, ctx, {"key": "staging", "name": "Staging"})
        assert resp.status_code == 201, resp.text
        env = await _env_by_key(client, ctx, "staging")
        assert env["git_branch"] == "staging"

    @pytest.mark.asyncio
    async def test_create_env_with_explicit_git_branch(self, git_ctx):
        client, ctx = git_ctx
        resp = await _create_env(
            client, ctx, {"key": "qa", "name": "QA", "git_branch": "release/qa"}
        )
        assert resp.status_code == 201, resp.text
        env = await _env_by_key(client, ctx, "qa")
        assert env["git_branch"] == "release/qa"


# ---------------------------------------------------------------------------
# 2. Env creation with from_branch (import from workspace repo)
# ---------------------------------------------------------------------------


class TestEnvFromBranch:
    @pytest.mark.asyncio
    async def test_from_branch_imports_versions_and_pins(self, git_ctx):
        """from_branch deserializes queries/ + dashboards/ files into versions
        pinned to the new env, with git_commit_sha = branch head."""
        client, ctx = git_ctx
        qid = ctx["query"]["id"]
        bid = ctx["board"]["id"]
        board_config = {"title": "Imported", "layout": [{"w": 1}]}

        head_sha = _seed_repo_with_branch(
            ctx["repo_dir"],
            "feature-x",
            {
                f"queries/{qid}.sql": "select 42 as imported",
                f"queries/{qid}.meta.json": json.dumps(
                    {"name": "Q1", "params": [], "required_scope": None},
                    indent=2,
                    sort_keys=True,
                ),
                f"dashboards/{bid}.json": json.dumps(
                    {"id": str(bid), "name": "B1", "config": board_config},
                    indent=2,
                    sort_keys=True,
                ),
            },
        )

        resp = await _create_env(
            client, ctx, {"key": "qa", "name": "QA", "from_branch": "feature-x"}
        )
        assert resp.status_code == 201, resp.text

        # Query: one imported version, stamped with the branch head sha,
        # pinned to the new env.
        qbody = await _versions(client, ctx, "query", qid)
        assert len(qbody["versions"]) == 1
        assert qbody["versions"][0]["git_commit_sha"] == head_sha
        q_ptr = _pointer_for(qbody, "qa")
        assert q_ptr is not None, "imported query version must be pinned to the new env"
        full = await _full_version(client, ctx, "query", qid, 1)
        assert full["config"]["sql"] == "select 42 as imported"

        # Board: imported + pinned too, config round-trips.
        bbody = await _versions(client, ctx, "board", bid)
        assert len(bbody["versions"]) == 1
        assert bbody["versions"][0]["git_commit_sha"] == head_sha
        assert _pointer_for(bbody, "qa") is not None
        bfull = await _full_version(client, ctx, "board", bid, 1)
        assert bfull["config"] == board_config

    @pytest.mark.asyncio
    async def test_from_branch_missing_repo_creates_empty_env_with_warning(self, git_ctx):
        """No workspace repo on disk → env still created, response warns."""
        client, ctx = git_ctx
        assert not ctx["repo_dir"].exists()

        resp = await _create_env(
            client, ctx, {"key": "qa", "name": "QA", "from_branch": "feature-y"}
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "warning" in body and body["warning"], (
            "missing repo must surface a warning field in the response"
        )

        # Env exists; nothing was imported/pinned.
        env = await _env_by_key(client, ctx, "qa")
        assert env is not None
        qbody = await _versions(client, ctx, "query", ctx["query"]["id"])
        assert qbody["versions"] == []
        assert _pointer_for(qbody, "qa") is None


# ---------------------------------------------------------------------------
# 3. Checkpoint → commit on the dev branch, sha stamping, parent chaining
# ---------------------------------------------------------------------------


class TestCheckpointGit:
    @pytest.mark.asyncio
    async def test_checkpoint_commits_to_dev_branch_and_stamps_sha(self, git_ctx):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="v1")

        # The serialized query exists at the dev branch head.
        assert ctx["repo_dir"].exists(), "checkpoint must materialize the workspace repo"
        content = _file_at(ctx["repo_dir"], "dev", f"queries/{qid}.sql")
        assert content == SQL_V1

        # The version carries the dev head sha.
        vbody = await _versions(client, ctx, "query", qid)
        v1 = vbody["versions"][0]
        assert v1["git_commit_sha"] == _branch_head(ctx["repo_dir"], "dev")
        assert v1["parent_version_id"] is None

    @pytest.mark.asyncio
    async def test_checkpoint_chain_parents_and_advances_branch(self, git_ctx):
        """Second checkpoint: new dev commit, parent_version_id chains to v1,
        and v1's commit is an ancestor (parent) of v2's commit."""
        client, ctx = git_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="v1")
        vbody = await _versions(client, ctx, "query", qid)
        v1 = vbody["versions"][0]

        await _set_query_sql(client, ctx, SQL_V2)
        await _checkpoint(client, ctx, "query", qid, message="v2")

        vbody = await _versions(client, ctx, "query", qid)
        assert [v["version"] for v in vbody["versions"]] == [2, 1]
        v2 = vbody["versions"][0]

        assert v2["parent_version_id"] == v1["id"], "lineage must chain to the previous version"
        head = _branch_head(ctx["repo_dir"], "dev")
        assert v2["git_commit_sha"] == head
        assert v2["git_commit_sha"] != v1["git_commit_sha"]
        assert _file_at(ctx["repo_dir"], "dev", f"queries/{qid}.sql") == SQL_V2

        parents = [str(p.hexsha) for p in _open_repo(ctx["repo_dir"]).commit(head).parents]
        assert v1["git_commit_sha"] in parents


# ---------------------------------------------------------------------------
# 4. Promote → branch merge (and pointer copy without git)
# ---------------------------------------------------------------------------


class TestPromoteGit:
    @pytest.mark.asyncio
    async def test_promote_merges_dev_branch_into_prod_branch(self, git_ctx):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="v1")
        resp = await _promote(client, ctx, "query", qid, "dev", "prod")
        assert resp.status_code in (200, 201), resp.text

        # Pointer copy (existing behavior).
        vbody = await _versions(client, ctx, "query", qid)
        prod_ptr = _pointer_for(vbody, "prod")
        assert prod_ptr is not None
        assert prod_ptr["version"] == 1

        # Git merge: prod's branch ('main') now contains the file from dev.
        content = _file_at(ctx["repo_dir"], "main", f"queries/{qid}.sql")
        assert content == SQL_V1, "promote must merge the dev branch into the prod branch"

    @pytest.mark.asyncio
    async def test_promote_pointer_copy_still_happens_when_git_absent(self, nogit_ctx):
        client, ctx = nogit_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="v1")
        resp = await _promote(client, ctx, "query", qid, "dev", "prod")
        assert resp.status_code in (200, 201), resp.text

        vbody = await _versions(client, ctx, "query", qid)
        prod_ptr = _pointer_for(vbody, "prod")
        assert prod_ptr is not None, "pointer copy must not depend on a working git layer"
        assert prod_ptr["version"] == 1


# ---------------------------------------------------------------------------
# 5. Push: one commit for all pinned resources + last_synced_sha
# ---------------------------------------------------------------------------


class TestPush:
    @pytest.mark.asyncio
    async def test_push_serializes_all_pinned_in_one_commit_and_updates_last_synced(
        self, git_ctx
    ):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]
        bid = ctx["board"]["id"]
        dev = await _env_by_key(client, ctx, "dev")

        # Query pinned via the API (this also commits queries/<id>.sql to dev).
        await _checkpoint(client, ctx, "query", qid, message="v1")

        # Board pinned directly via the store — NO git commit happened for it,
        # so push has real work to do.
        board_version = await ctx["env_store"].create_version(
            org_id=ctx["org_id"],
            project_id=ctx["project_id"],
            kind="board",
            resource_id=bid,
            config={"title": "B1", "layout": []},
            created_by=ctx["alice_id"],
        )
        await ctx["env_store"].set_pointer(
            "board", bid, dev["id"], board_version["id"], promoted_by=ctx["alice_id"]
        )

        before = _commit_count(ctx["repo_dir"], "dev")
        resp = await _push(client, ctx, dev["id"])
        assert resp.status_code in (200, 201), resp.text
        after = _commit_count(ctx["repo_dir"], "dev")

        assert after - before == 1, "push must serialize all pinned resources in ONE commit"

        head = _branch_head(ctx["repo_dir"], "dev")
        assert _file_at(ctx["repo_dir"], "dev", f"queries/{qid}.sql") is not None
        assert _file_at(ctx["repo_dir"], "dev", f"dashboards/{bid}.json") is not None

        dev_after = await _env_by_key(client, ctx, "dev")
        assert dev_after["last_synced_sha"] == head


# ---------------------------------------------------------------------------
# 6. Pull: no-op / fast-forward / diverged (+ strategies)
# ---------------------------------------------------------------------------


async def _checkpoint_and_push(client, ctx) -> dict[str, Any]:
    """Checkpoint the query to dev and push; return the refreshed dev env."""
    await _checkpoint(client, ctx, "query", ctx["query"]["id"], message="v1")
    dev = await _env_by_key(client, ctx, "dev")
    resp = await _push(client, ctx, dev["id"])
    assert resp.status_code in (200, 201), resp.text
    return await _env_by_key(client, ctx, "dev")


async def _diverge(client, ctx) -> tuple[str, str]:
    """Create a diverged dev branch.

    1. checkpoint v1 (commit A) ; 2. checkpoint v2 (commit B, env pinned v2) ;
    3. push (last_synced = dev head) ; 4. rewrite: reset dev to A and commit a
    divergent change E.  last_synced is then NOT an ancestor of the branch
    head → diverged.

    Returns (sha_a, divergent_head_sha).
    """
    qid = ctx["query"]["id"]
    await _checkpoint(client, ctx, "query", qid, message="v1")
    vbody = await _versions(client, ctx, "query", qid)
    sha_a = vbody["versions"][0]["git_commit_sha"]
    assert sha_a

    await _set_query_sql(client, ctx, SQL_V2)
    await _checkpoint(client, ctx, "query", qid, message="v2")

    dev = await _env_by_key(client, ctx, "dev")
    resp = await _push(client, ctx, dev["id"])
    assert resp.status_code in (200, 201), resp.text

    _reset_branch(ctx["repo_dir"], "dev", sha_a)
    ext_sha = _external_commit(
        ctx["repo_dir"], "dev", {f"queries/{qid}.sql": SQL_DIVERGENT}, "divergent edit"
    )
    return sha_a, ext_sha


class TestPull:
    @pytest.mark.asyncio
    async def test_pull_noop_when_branch_head_equals_last_synced(self, git_ctx):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]
        dev = await _checkpoint_and_push(client, ctx)
        synced = dev["last_synced_sha"]
        assert synced == _branch_head(ctx["repo_dir"], "dev")

        n_before = len((await _versions(client, ctx, "query", qid))["versions"])
        resp = await _pull(client, ctx, dev["id"])
        assert resp.status_code == 200, resp.text

        vbody = await _versions(client, ctx, "query", qid)
        assert len(vbody["versions"]) == n_before, "no-op pull must not create versions"
        dev_after = await _env_by_key(client, ctx, "dev")
        assert dev_after["last_synced_sha"] == synced

    @pytest.mark.asyncio
    async def test_pull_fast_forward_creates_version_and_repoints(self, git_ctx):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]
        dev = await _checkpoint_and_push(client, ctx)

        vbody = await _versions(client, ctx, "query", qid)
        pinned_before = _pointer_for(vbody, "dev")
        assert pinned_before is not None

        ext_sha = _external_commit(
            ctx["repo_dir"], "dev", {f"queries/{qid}.sql": SQL_EXTERNAL}, "external edit"
        )

        resp = await _pull(client, ctx, dev["id"])
        assert resp.status_code == 200, resp.text

        vbody = await _versions(client, ctx, "query", qid)
        new_version = vbody["versions"][0]
        assert new_version["version"] == pinned_before["version"] + 1
        assert new_version["parent_version_id"] == pinned_before["version_id"], (
            "pulled version's parent must be the previously pinned version"
        )

        full = await _full_version(client, ctx, "query", qid, new_version["version"])
        assert full["config"]["sql"] == SQL_EXTERNAL

        ptr = _pointer_for(vbody, "dev")
        assert ptr["version_id"] == new_version["id"], "env must be repointed to the new version"

        dev_after = await _env_by_key(client, ctx, "dev")
        assert dev_after["last_synced_sha"] == ext_sha

    @pytest.mark.asyncio
    async def test_pull_diverged_returns_409(self, git_ctx):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]
        _, ext_sha = await _diverge(client, ctx)

        n_before = len((await _versions(client, ctx, "query", qid))["versions"])
        resp = await _pull(client, ctx, (await _env_by_key(client, ctx, "dev"))["id"])
        assert resp.status_code == 409, resp.text

        payload = _diverged_payload(resp.json())
        assert payload["diverged"] is True
        assert payload.get("branch_sha") == ext_sha
        assert "env_sha" in payload
        assert "files" in payload

        # Nothing moved DB-side.
        vbody = await _versions(client, ctx, "query", qid)
        assert len(vbody["versions"]) == n_before

    @pytest.mark.asyncio
    async def test_pull_diverged_take_branch_imports_branch_state(self, git_ctx):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]
        _, ext_sha = await _diverge(client, ctx)
        dev = await _env_by_key(client, ctx, "dev")

        resp = await _pull(client, ctx, dev["id"], {"strategy": "take_branch"})
        assert resp.status_code == 200, resp.text

        vbody = await _versions(client, ctx, "query", qid)
        latest = vbody["versions"][0]
        full = await _full_version(client, ctx, "query", qid, latest["version"])
        assert full["config"]["sql"] == SQL_DIVERGENT, "take_branch must adopt the branch content"

        ptr = _pointer_for(vbody, "dev")
        assert ptr["version_id"] == latest["id"]

        dev_after = await _env_by_key(client, ctx, "dev")
        assert dev_after["last_synced_sha"] == ext_sha

    @pytest.mark.asyncio
    async def test_pull_diverged_take_env_overwrites_branch(self, git_ctx):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]
        await _diverge(client, ctx)
        dev = await _env_by_key(client, ctx, "dev")

        n_before = len((await _versions(client, ctx, "query", qid))["versions"])
        pinned_before = _pointer_for(await _versions(client, ctx, "query", qid), "dev")

        resp = await _pull(client, ctx, dev["id"], {"strategy": "take_env"})
        assert resp.status_code == 200, resp.text

        # The branch now reflects the env's pinned state (v2 content).
        assert _file_at(ctx["repo_dir"], "dev", f"queries/{qid}.sql") == SQL_V2, (
            "take_env must overwrite the branch from the env state"
        )

        # DB-side unchanged: same versions, same pointer.
        vbody = await _versions(client, ctx, "query", qid)
        assert len(vbody["versions"]) == n_before
        assert _pointer_for(vbody, "dev")["version_id"] == pinned_before["version_id"]

        dev_after = await _env_by_key(client, ctx, "dev")
        assert dev_after["last_synced_sha"] == _branch_head(ctx["repo_dir"], "dev")


# ---------------------------------------------------------------------------
# 7. Graph endpoint
# ---------------------------------------------------------------------------


class TestGraph:
    @pytest.mark.asyncio
    async def test_graph_returns_env_bound_branches_with_commits(self, git_ctx):
        client, ctx = git_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="v1")
        resp = await _promote(client, ctx, "query", qid, "dev", "prod")
        assert resp.status_code in (200, 201), resp.text

        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/git/graph",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body.get("branches"), list)

        by_env = {b.get("env_key"): b for b in body["branches"]}
        assert "dev" in by_env, "dev's branch must appear in the graph"
        assert "prod" in by_env, "prod's branch must appear in the graph"
        assert by_env["dev"]["branch"] == "dev"
        assert by_env["prod"]["branch"] == "main"

        dev_entry = by_env["dev"]
        dev_head = _branch_head(ctx["repo_dir"], "dev")
        assert dev_entry["head_sha"] == dev_head
        assert dev_entry["commits"], "dev has commits — the graph must list them"
        shas = [c["sha"] for c in dev_entry["commits"]]
        assert dev_head in shas
        for commit in dev_entry["commits"]:
            for key in ("sha", "parents", "message", "author", "date"):
                assert key in commit, f"graph commit missing {key!r}"
            assert isinstance(commit["parents"], list)

    @pytest.mark.asyncio
    async def test_graph_empty_structure_when_no_repo(self, git_ctx):
        client, ctx = git_ctx

        # A second project whose workspace repo was never created.
        fresh_pid = str(uuid.uuid4())
        ctx["projects_tbl"][fresh_pid] = {
            "id": fresh_pid, "org_id": ctx["org_id"], "name": "Fresh",
            "slug": "fresh", "created_by": ctx["alice_id"], "git": None,
            "created_at": datetime.now(tz=timezone.utc) + timedelta(seconds=1),
        }

        resp = await client.get(
            f"/api/v1/projects/{fresh_pid}/git/graph",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert isinstance(body.get("branches"), list)
        for branch in body["branches"]:
            assert not branch.get("commits"), "no repo → no commits anywhere in the graph"


# ---------------------------------------------------------------------------
# 8. No-git degradation: DB operations never blocked by a broken git layer
# ---------------------------------------------------------------------------


class TestNoGitDegradation:
    @pytest.mark.asyncio
    async def test_checkpoint_succeeds_without_git(self, nogit_ctx):
        client, ctx = nogit_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="v1")

        vbody = await _versions(client, ctx, "query", qid)
        assert len(vbody["versions"]) == 1
        assert vbody["versions"][0]["git_commit_sha"] is None, (
            "no git → no sha stamped, but the version must exist"
        )
        assert _pointer_for(vbody, "dev") is not None

    @pytest.mark.asyncio
    async def test_push_and_pull_degrade_without_5xx(self, nogit_ctx):
        client, ctx = nogit_ctx
        qid = ctx["query"]["id"]
        await _checkpoint(client, ctx, "query", qid, message="v1")
        dev = await _env_by_key(client, ctx, "dev")

        resp = await _push(client, ctx, dev["id"])
        assert resp.status_code < 500, f"push must not 5xx without git: {resp.text}"

        resp = await _pull(client, ctx, dev["id"])
        assert resp.status_code < 500, f"pull must not 5xx without git: {resp.text}"

        # DB state intact afterwards.
        vbody = await _versions(client, ctx, "query", qid)
        assert len(vbody["versions"]) == 1
        assert _pointer_for(vbody, "dev")["version"] == 1

    @pytest.mark.asyncio
    async def test_full_flow_checkpoint_promote_without_git(self, nogit_ctx):
        """checkpoint + promote both succeed end-to-end with a broken workspace."""
        client, ctx = nogit_ctx
        qid = ctx["query"]["id"]

        await _checkpoint(client, ctx, "query", qid, message="v1")
        resp = await _promote(client, ctx, "query", qid, "dev", "prod")
        assert resp.status_code in (200, 201), resp.text

        vbody = await _versions(client, ctx, "query", qid)
        assert _pointer_for(vbody, "dev") is not None
        assert _pointer_for(vbody, "prod") is not None


# ---------------------------------------------------------------------------
# 9. Auth
# ---------------------------------------------------------------------------


class TestGitEnvAuth:
    @pytest.mark.asyncio
    async def test_git_endpoints_require_auth(self, git_ctx):
        client, ctx = git_ctx
        dev = await _env_by_key(client, ctx, "dev")

        resp = await client.post(f"/api/v1/environments/{dev['id']}/git/push", json={})
        assert resp.status_code == 401

        resp = await client.post(f"/api/v1/environments/{dev['id']}/git/pull", json={})
        assert resp.status_code == 401

        resp = await client.get(f"/api/v1/projects/{ctx['project_id']}/git/graph")
        assert resp.status_code == 401
