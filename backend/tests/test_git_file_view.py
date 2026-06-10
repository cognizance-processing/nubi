"""Tests for the READ-ONLY git file-view endpoints (charter workstream A7).

Feature under test (routes in app/routes/environments.py, primitives in
app/git/env_sync.py ``ProjectGit.list_known_files`` / ``read_file``):

- GET /projects/{pid}/git/files?ref= → ``{ref, files: [...]}`` listing the
  tracked resource files (plus an allowlisted manifest when present). ``ref``
  defaults to the prod env's bound branch.
- GET /projects/{pid}/git/files/content?path=&ref= → ``{path, ref, content}``
  via ``ProjectGit.read_file``.  SECURITY: a path containing ``..`` or not
  under a known resource folder (env_sync.FOLDER_KIND) / allowlisted manifest
  → 400; a missing file (read_file → None) → 404.

Strategy
--------
- conftest ``app`` fixture patches app.db with FakeDB; ``InMemoryRepo`` via
  ``set_repo()``; ``InMemoryEnvStore`` via ``set_env_store()``.
- A dict-backed projects table is layered over ``app.db.fetchrow/fetch``
  (mirrors test_git_env.py ``_make_projects_db``).
- ``env_sync.get_project_git`` is PATCHED to return an in-memory ``StubGit``
  so NO real git repo / disk is needed.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.environments.store import InMemoryEnvStore, set_env_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Self-registering router (idempotent; main.py imports it too).
import app.routes.environments  # noqa: F401, E402
from app.git import env_sync


# ---------------------------------------------------------------------------
# Helpers (copied from test_git_env.py house patterns)
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


def _make_projects_db(projects, fallback_fetchrow, fallback_fetch):
    def _norm(q: str) -> str:
        return " ".join(q.split()).upper()

    def _by_org(org_id: str):
        rows = [p for p in projects.values() if p["org_id"] == str(org_id)]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def _fetchrow(query: str, *args: Any):
        q = _norm(query)
        if "FROM PROJECTS" in q or "INTO PROJECTS" in q or q.startswith("UPDATE PROJECTS"):
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
# Stub ProjectGit — in-memory, no disk / no git binary
# ---------------------------------------------------------------------------


class StubGit:
    """In-memory stand-in for ``env_sync.ProjectGit`` for the file view."""

    def __init__(self, files: dict[str, dict[str, str]] | None = None, exists: bool = True):
        # files: {ref: {path: content}}
        self._files = files or {}
        self._exists = exists

    def exists(self) -> bool:
        return self._exists

    def list_known_files(self, ref: str) -> list[str]:
        tree = self._files.get(ref, {})
        return [
            path
            for path in tree
            if path.split("/", 1)[0] in env_sync.FOLDER_KIND
        ]

    def read_file(self, ref: str, path: str) -> str | None:
        return self._files.get(ref, {}).get(path)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _workspace(app, fake_db, stub_git: StubGit):
    repo = InMemoryRepo()
    set_repo(repo)
    env_store = InMemoryEnvStore()
    set_env_store(env_store)

    alice_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
    repo.seed_org_member(org_id=org_id, user_id=alice_id)

    now = datetime.now(tz=timezone.utc)
    projects_tbl = {
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
            stack.enter_context(
                patch.object(env_sync, "get_project_git", return_value=stub_git)
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client:
                # Lazy-create the default envs (dev + prod → branch 'main').
                resp = await client.get(
                    f"/api/v1/projects/{project_id}/environments",
                    headers=_auth_headers(alice_id),
                )
                assert resp.status_code == 200, resp.text

                ctx = {
                    "alice_id": alice_id,
                    "org_id": org_id,
                    "project_id": project_id,
                }
                yield client, ctx
    finally:
        set_env_store(None)
        set_repo(None)


@pytest_asyncio.fixture
async def files_ctx(app, fake_db):
    """(client, ctx) with a StubGit holding a couple of known files on 'main'."""
    stub = StubGit(
        files={
            "main": {
                "queries/abc.sql": "select 1 as v",
                "dashboards/def.json": "{}",
                "nubi.yaml": "project: default\n",
            }
        }
    )
    async with _workspace(app, fake_db, stub) as (client, ctx):
        ctx["stub"] = stub
        yield client, ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGitFileList:
    @pytest.mark.asyncio
    async def test_lists_known_files_and_manifest(self, files_ctx):
        client, ctx = files_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/git/files",
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Default ref is the prod env's branch ('main').
        assert body["ref"] == "main"
        files = set(body["files"])
        assert "queries/abc.sql" in files
        assert "dashboards/def.json" in files
        assert "nubi.yaml" in files, "an allowlisted manifest must be listed when present"

    @pytest.mark.asyncio
    async def test_empty_when_no_repo(self, app, fake_db):
        stub = StubGit(files={}, exists=False)
        async with _workspace(app, fake_db, stub) as (client, ctx):
            resp = await client.get(
                f"/api/v1/projects/{ctx['project_id']}/git/files",
                headers=_auth_headers(ctx["alice_id"]),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["files"] == []


class TestGitFileContent:
    @pytest.mark.asyncio
    async def test_valid_known_path_returns_content(self, files_ctx):
        client, ctx = files_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/git/files/content",
            params={"path": "queries/abc.sql"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["path"] == "queries/abc.sql"
        assert body["ref"] == "main"
        assert body["content"] == "select 1 as v"

    @pytest.mark.asyncio
    async def test_manifest_path_returns_content(self, files_ctx):
        client, ctx = files_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/git/files/content",
            params={"path": "nubi.yaml"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["content"] == "project: default\n"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_path",
        ["../etc/passwd", "queries/../../x", "queries/../secret", "/etc/passwd"],
    )
    async def test_path_traversal_rejected_400(self, files_ctx, bad_path):
        client, ctx = files_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/git/files/content",
            params={"path": bad_path},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 400, resp.text

    @pytest.mark.asyncio
    async def test_disallowed_top_folder_rejected_400(self, files_ctx):
        client, ctx = files_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/git/files/content",
            params={"path": "secrets/token.txt"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 400, resp.text

    @pytest.mark.asyncio
    async def test_missing_file_returns_404(self, files_ctx):
        client, ctx = files_ctx
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/git/files/content",
            params={"path": "queries/does-not-exist.sql"},
            headers=_auth_headers(ctx["alice_id"]),
        )
        assert resp.status_code == 404, resp.text


class TestGitFileViewAuth:
    @pytest.mark.asyncio
    async def test_endpoints_require_auth(self, files_ctx):
        client, ctx = files_ctx
        resp = await client.get(f"/api/v1/projects/{ctx['project_id']}/git/files")
        assert resp.status_code == 401
        resp = await client.get(
            f"/api/v1/projects/{ctx['project_id']}/git/files/content",
            params={"path": "queries/abc.sql"},
        )
        assert resp.status_code == 401
