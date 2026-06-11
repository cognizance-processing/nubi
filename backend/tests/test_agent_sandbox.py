"""Tests for the "governance as agent sandbox" controls on resource CRUD.

Feature under test (app/auth/scopes.py + app/routes/resources.py):

1. **Env-scoped write tokens** — a token carrying ``write:board:dev`` may
   create/update boards in the ``dev`` environment ONLY and is rejected (403)
   when it targets the protected ``prod`` env (promotion). A ``write:*`` token
   retains full access to any env.
2. **AI-authorship attribution** — a write is stamped ``author_kind: "agent"``
   when the token identifies an AI agent (``actor: "agent"`` claim) and
   ``"human"`` otherwise; surfaced on the response for versionable kinds.
3. **Idempotency** — a re-upsert (PUT) with identical config is a clean no-op:
   the resource route dedupes by canonical-JSON config hash, so the write is
   skipped and the response is stamped ``deduped: true``.  NOTE: CRUD writes do
   NOT mint environment versions — env versioning is the explicit checkpoint
   flow's job, so this is checked on the resource itself, not the version chain.

Strategy (house pattern — see test_environments_versions.py)
------------------------------------------------------------
- conftest ``app`` fixture patches all app.db helpers with FakeDB.
- ``InMemoryRepo`` via ``set_repo`` for boards/queries; ``InMemoryEnvStore`` via
  ``set_env_store`` for environments/versions.
- A tiny dict-backed projects table is layered over ``app.db.fetchrow``/
  ``fetch`` so the route can resolve the project + its protected ``prod`` env.
- Auth: seed users in fake_db + mint_access_token JWTs, with ``scope`` /
  ``actor`` extra claims to model agent-scoped tokens.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
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

# Self-registering routers (idempotent; main.py imports them too).
import app.routes.environments  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc),
    }


def _headers(
    user_id: str,
    *,
    scope: str | None = None,
    actor: str | None = None,
) -> dict[str, str]:
    """Mint a bearer token, optionally with a ``scope`` and ``actor`` claim."""
    extra: dict[str, Any] = {}
    if scope is not None:
        extra["scope"] = scope
    if actor is not None:
        extra["actor"] = actor
    token = mint_access_token(user_id, extra_claims=extra or None)
    return {"Authorization": f"Bearer {token}"}


def _make_projects_db(projects: dict[str, dict[str, Any]], fallback_fetchrow, fallback_fetch):
    """fetchrow/fetch fakes serving a dict-backed projects table (else delegate)."""

    def _norm(q: str) -> str:
        return " ".join(q.split()).upper()

    def _by_org(org_id: str) -> list[dict[str, Any]]:
        rows = [p for p in projects.values() if p["org_id"] == str(org_id)]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def _fetchrow(query: str, *args: Any):
        q = _norm(query)
        if "FROM PROJECTS" in q or "INTO PROJECTS" in q or q.startswith("UPDATE PROJECTS"):
            if "ID = $1" in q and "ORG_ID = $2" in q:
                p = projects.get(str(args[0]))
                if p is not None and p["org_id"] == str(args[1]):
                    return {"?column?": 1} if q.startswith("SELECT 1") else dict(p)
                return None
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
# Fixture: one org + user + default project with dev/prod envs ensured.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sandbox_ctx(app, fake_db):
    """Yield ``(client, ctx)`` with stores injected and a seeded workspace."""
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

    # Ensure dev (unprotected) + prod (protected, default) envs exist.
    await env_store.ensure_project_envs(project_id)

    with ExitStack() as stack:
        stack.enter_context(patch("app.db.fetchrow", side_effect=fetchrow_fake))
        stack.enter_context(patch("app.db.fetch", side_effect=fetch_fake))
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
            ctx = {
                "alice_id": alice_id,
                "org_id": org_id,
                "project_id": project_id,
                "repo": repo,
                "env_store": env_store,
            }
            yield client, ctx

    set_env_store(None)
    set_repo(None)


# ---------------------------------------------------------------------------
# (a) Env-scoped write tokens — dev OK, prod/promote rejected.
# ---------------------------------------------------------------------------


class TestEnvScopedWriteTokens:
    @pytest.mark.asyncio
    async def test_dev_scoped_token_can_upsert_in_dev(self, sandbox_ctx):
        """A ``write:board:dev`` token may create a board targeting dev."""
        client, ctx = sandbox_ctx
        resp = await client.post(
            "/api/v1/boards?env=dev",
            json={"name": "Agent Board", "config": {"theme": "dark"}},
            headers=_headers(ctx["alice_id"], scope="write:board:dev", actor="agent"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "Agent Board"

    @pytest.mark.asyncio
    async def test_dev_scoped_token_rejected_in_prod(self, sandbox_ctx):
        """A ``write:board:dev`` token is 403 when creating in prod."""
        client, ctx = sandbox_ctx
        resp = await client.post(
            "/api/v1/boards?env=prod",
            json={"name": "Sneaky Board", "config": {"theme": "dark"}},
            headers=_headers(ctx["alice_id"], scope="write:board:dev", actor="agent"),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_dev_scoped_token_rejected_promoting_via_put(self, sandbox_ctx):
        """A dev-scoped token cannot PUT/promote a board into prod."""
        client, ctx = sandbox_ctx
        # Create in dev first (allowed).
        create = await client.post(
            "/api/v1/boards?env=dev",
            json={"name": "B", "config": {"v": 1}},
            headers=_headers(ctx["alice_id"], scope="write:board:dev", actor="agent"),
        )
        assert create.status_code == 201, create.text
        board_id = create.json()["id"]

        # Promote the same board to prod → 403.
        promote = await client.put(
            f"/api/v1/boards/{board_id}?env=prod",
            json={"config": {"v": 2}},
            headers=_headers(ctx["alice_id"], scope="write:board:dev", actor="agent"),
        )
        assert promote.status_code == 403, promote.text

    @pytest.mark.asyncio
    async def test_wrong_resource_scope_rejected(self, sandbox_ctx):
        """A ``write:query:dev`` token cannot write boards even in dev."""
        client, ctx = sandbox_ctx
        resp = await client.post(
            "/api/v1/boards?env=dev",
            json={"name": "X", "config": {}},
            headers=_headers(ctx["alice_id"], scope="write:query:dev", actor="agent"),
        )
        assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# (b) Broad write:* token — full access to any env.
# ---------------------------------------------------------------------------


class TestBroadWriteToken:
    @pytest.mark.asyncio
    async def test_wildcard_token_can_write_dev_and_prod(self, sandbox_ctx):
        """A ``write:*`` token may create boards in both dev and prod."""
        client, ctx = sandbox_ctx
        dev = await client.post(
            "/api/v1/boards?env=dev",
            json={"name": "Wild Dev", "config": {"v": 1}},
            headers=_headers(ctx["alice_id"], scope="write:*"),
        )
        assert dev.status_code == 201, dev.text

        prod = await client.post(
            "/api/v1/boards?env=prod",
            json={"name": "Wild Prod", "config": {"v": 1}},
            headers=_headers(ctx["alice_id"], scope="write:*"),
        )
        assert prod.status_code == 201, prod.text

    @pytest.mark.asyncio
    async def test_resource_wildcard_can_promote_to_prod(self, sandbox_ctx):
        """A ``write:board:*`` token may promote a board to prod."""
        client, ctx = sandbox_ctx
        create = await client.post(
            "/api/v1/boards?env=dev",
            json={"name": "B", "config": {"v": 1}},
            headers=_headers(ctx["alice_id"], scope="write:board:*"),
        )
        assert create.status_code == 201, create.text
        board_id = create.json()["id"]

        promote = await client.put(
            f"/api/v1/boards/{board_id}?env=prod",
            json={"config": {"v": 2}},
            headers=_headers(ctx["alice_id"], scope="write:board:*"),
        )
        assert promote.status_code == 200, promote.text


# ---------------------------------------------------------------------------
# (c) Idempotency — identical re-upsert is a no-op.
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_identical_reput_is_idempotent(self, sandbox_ctx):
        """Re-PUT with identical config is a deduped no-op; a change writes through."""
        client, ctx = sandbox_ctx
        hdrs = _headers(ctx["alice_id"], scope="write:board:dev", actor="agent")

        create = await client.post(
            "/api/v1/boards?env=dev",
            json={"name": "B", "config": {"k": "v"}},
            headers=hdrs,
        )
        assert create.status_code == 201, create.text
        board_id = create.json()["id"]

        # PUT the SAME config as the create → idempotent no-op (deduped).
        put1 = await client.put(
            f"/api/v1/boards/{board_id}?env=dev",
            json={"config": {"k": "v"}},
            headers=hdrs,
        )
        assert put1.status_code == 200, put1.text
        assert put1.json()["deduped"] is True

        # Change the config → a real write (not deduped).
        put2 = await client.put(
            f"/api/v1/boards/{board_id}?env=dev",
            json={"config": {"k": "v2"}},
            headers=hdrs,
        )
        assert put2.status_code == 200, put2.text
        assert put2.json()["deduped"] is False

        # Re-PUT the changed config again → idempotent no-op.
        put3 = await client.put(
            f"/api/v1/boards/{board_id}?env=dev",
            json={"config": {"k": "v2"}},
            headers=hdrs,
        )
        assert put3.status_code == 200, put3.text
        assert put3.json()["deduped"] is True

        # The persisted resource config reflects the last real write.
        row = await ctx["repo"].get("boards", ctx["org_id"], board_id)
        assert row["config"] == {"k": "v2"}


# ---------------------------------------------------------------------------
# (d) AI-authorship attribution.
# ---------------------------------------------------------------------------


class TestAttribution:
    @pytest.mark.asyncio
    async def test_agent_write_is_stamped_agent(self, sandbox_ctx):
        """An agent token (actor='agent') stamps author_kind='agent' on the response."""
        client, ctx = sandbox_ctx
        resp = await client.post(
            "/api/v1/boards?env=dev",
            json={"name": "B", "config": {"k": "v"}},
            headers=_headers(ctx["alice_id"], scope="write:board:dev", actor="agent"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["author_kind"] == "agent"

    @pytest.mark.asyncio
    async def test_human_write_is_stamped_human(self, sandbox_ctx):
        """A non-agent (human) token stamps author_kind='human'."""
        client, ctx = sandbox_ctx
        resp = await client.post(
            "/api/v1/boards?env=dev",
            json={"name": "B", "config": {"k": "v"}},
            headers=_headers(ctx["alice_id"], scope="write:board:*"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["author_kind"] == "human"
