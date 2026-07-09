"""Cross-tenant query-registry hijack regression tests (CRITICAL 1).

Vulnerability (found + fixed in this pass)
------------------------------------------
``app.queries.registry`` exposes a PROCESS-GLOBAL ``QueryRegistry`` singleton
(one ``dict[query_id -> RegisteredQuery]`` spanning every org on the
deployment) PLUS a lazy DB loader ``ensure_persisted_query``.  Before this fix
both the run/read paths (``app.routes.query._resolve_request_plan``,
``app.dashboards.collect.run_query_rows``, and the ``kind="query"`` job runner
``app.jobs.executor._run_query_job``) resolved a caller-supplied ``query_id``
via the pattern::

    registry.get(query_id) or await ensure_persisted_query(query_id)

which had TWO holes:

1. ``ensure_persisted_query`` ran ``SELECT ... FROM queries WHERE id = $1``
   with NO org filter — so any caller who supplied ANOTHER org's persisted
   ``query_id`` loaded (and could then run / read) that org's query.
2. A ``registry.get(query_id)`` HIT was never re-checked against the caller's
   org — the in-memory dict is keyed only by id, so once ANY org's request
   loaded a query, every OTHER org's request calling ``.get(same_id)`` got it
   back with zero ownership check.

Fix: a single org-scoped choke point ``resolve_registered_query(query_id,
org_id)``.  ``ensure_persisted_query`` now REQUIRES an ``org_id`` and scopes
the DB read to ``WHERE id = $1 AND org_id = $2`` (fails CLOSED on a missing
org).  Persisted / save_as registrations now stamp ``owner_org_id`` so the
cache-hit path can enforce ownership; built-in ``system`` seeds (``demo_*``)
stay globally visible, and purely in-memory (never-persisted) registrations
keep their historical id-is-the-handle behaviour.

This file proves org B can never resolve/run org A's query_id — at the choke
point, at ``ensure_persisted_query``, AND end-to-end through ``POST /query``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.queries.registry import (
    ensure_persisted_query,
    get_query_registry,
    resolve_registered_query,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str, email: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ===========================================================================
# 1. resolve_registered_query — the org-scoped choke point (unit level)
# ===========================================================================


class TestResolveRegisteredQueryOwnership:
    @pytest.mark.asyncio
    async def test_org_b_cannot_resolve_org_a_owned_query(self):
        """An explicitly org-A-owned registry entry is invisible to org B."""
        registry = get_query_registry()
        qid = f"sec_{uuid.uuid4().hex[:10]}"
        registry.register(
            id=qid,
            sql="SELECT customer_ssn, revenue FROM org_a_private",
            name="Org A private",
            owner_org_id="org-a",
        )
        try:
            # Org B (mismatched) — must NOT resolve, even though the id exists
            # in the shared process-global registry.
            assert await resolve_registered_query(qid, "org-b") is None
            # A caller with NO org resolved must not get it either.
            assert await resolve_registered_query(qid, None) is None
            # Org A (owner) still resolves its own query — no functional regression.
            rq = await resolve_registered_query(qid, "org-a")
            assert rq is not None and rq.id == qid
        finally:
            registry.unregister(qid)

    @pytest.mark.asyncio
    async def test_system_seed_visible_to_every_org(self):
        """Built-in demo_* seeds (system=True) stay globally resolvable."""
        assert (await resolve_registered_query("demo_all", "org-a")) is not None
        assert (await resolve_registered_query("demo_all", "org-b")) is not None
        assert (await resolve_registered_query("demo_all", None)) is not None

    @pytest.mark.asyncio
    async def test_unknown_id_resolves_to_none(self):
        assert await resolve_registered_query("does-not-exist", "org-a") is None


# ===========================================================================
# 2. ensure_persisted_query — the unscoped-DB-read hole
# ===========================================================================


class TestEnsurePersistedQueryOrgScoping:
    @pytest.mark.asyncio
    async def test_missing_org_fails_closed_without_db_read(self):
        """org_id=None must refuse to touch the DB at all (fail closed)."""
        fetchrow = AsyncMock()
        with patch("app.db.fetchrow", fetchrow):
            result = await ensure_persisted_query("some-id", None)
        assert result is None
        fetchrow.assert_not_called()  # never ran an unscoped read

    @pytest.mark.asyncio
    async def test_db_read_is_org_scoped(self):
        """The DB read must carry BOTH the id AND the caller's org_id."""
        fetchrow = AsyncMock(return_value=None)
        with patch("app.db.fetchrow", fetchrow):
            await ensure_persisted_query("q-123", "org-a")
        assert fetchrow.await_count == 1
        sql, *args = fetchrow.await_args.args
        # id + org_id both bound; the SQL filters on org_id.
        assert "org_id" in sql.lower()
        assert args == ["q-123", "org-a"]

    @pytest.mark.asyncio
    async def test_loaded_row_is_stamped_with_owner_org(self):
        """A row loaded for org A is stamped owner_org_id=org A so the next
        cache-hit resolution can enforce ownership."""
        registry = get_query_registry()
        qid = str(uuid.uuid4())
        row = {
            "id": qid,
            "name": "Persisted A",
            "config": {"sql": "SELECT 1 AS x"},
            "org_id": "org-a",
        }
        fetchrow = AsyncMock(return_value=row)
        try:
            with patch("app.db.fetchrow", fetchrow):
                rq = await ensure_persisted_query(qid, "org-a")
            assert rq is not None
            assert rq.owner_org_id == "org-a"
            # And now org B cannot resolve the freshly-cached entry.
            assert await resolve_registered_query(qid, "org-b") is None
        finally:
            registry.unregister(qid)


# ===========================================================================
# 3. End-to-end: POST /query cannot run another org's registered query_id
# ===========================================================================


@pytest_asyncio.fixture
async def two_org_query_clients(app, fake_db):
    """Two orgs, each with a seeded owner user + membership, sharing the app."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    user_a = str(uuid.uuid4())
    org_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    org_b = str(uuid.uuid4())

    fake_db.users[user_a] = _make_user(user_a, "org_a_owner@example.com")
    fake_db.users[user_b] = _make_user(user_b, "org_b_owner@example.com")
    repo.seed_org_member(org_id=org_a, user_id=user_a, role="owner")
    repo.seed_org_member(org_id=org_b, user_id=user_b, role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_a, org_a, user_b, org_b

    set_repo(None)


@pytest.fixture
def org_a_registered_query(two_org_query_clients):
    """Register a real, org-A-owned query in the process-global registry."""
    _ac, _ua, org_a, _ub, _ob = two_org_query_clients
    registry = get_query_registry()
    qid = f"sec_e2e_{uuid.uuid4().hex[:10]}"
    registry.register(
        id=qid,
        sql="SELECT * FROM demo",
        name="Org A revenue",
        owner_org_id=org_a,
    )
    yield qid
    registry.unregister(qid)


class TestPostQueryCrossOrg:
    @pytest.mark.asyncio
    async def test_org_b_cannot_run_org_a_query_id(
        self, two_org_query_clients, org_a_registered_query
    ):
        ac, _ua, _oa, user_b, _ob = two_org_query_clients
        resp = await ac.post(
            "/api/v1/query",
            json={"query_id": org_a_registered_query},
            headers=_auth_headers(user_b),
        )
        # Org B must be told the query is not registered (for its org) — never
        # a 200 that streams org A's rows.
        assert resp.status_code == 403, (
            f"SECURITY: org B ran org A's query_id — got {resp.status_code}: "
            f"{resp.text[:300]}"
        )
        assert resp.json()["error"]["code"] == "query_not_registered"

    @pytest.mark.asyncio
    async def test_org_a_can_run_its_own_query_id(
        self, two_org_query_clients, org_a_registered_query
    ):
        ac, user_a, _oa, _ub, _ob = two_org_query_clients
        resp = await ac.post(
            "/api/v1/query",
            json={"query_id": org_a_registered_query},
            headers=_auth_headers(user_a),
        )
        assert resp.status_code == 200, (
            "Regression: org A can no longer run its own registered query — "
            f"got {resp.status_code}: {resp.text[:300]}"
        )


# ===========================================================================
# 4. kind="query" job runner cannot execute another org's query (HIGH)
# ===========================================================================


class TestQueryJobCrossOrg:
    def test_job_in_org_b_cannot_execute_org_a_query(self):
        """A kind='query' job stamped org B must not resolve org A's owned
        query_id from the process-global registry."""
        from app.jobs.executor import execute_job

        registry = get_query_registry()
        qid = f"sec_job_{uuid.uuid4().hex[:10]}"
        registry.register(
            id=qid,
            sql="SELECT * FROM demo",
            name="Org A job query",
            owner_org_id="org-a",
        )
        try:
            job = {
                "id": str(uuid.uuid4()),
                "kind": "query",
                "target": qid,
                "org_id": "org-b",
            }
            run = execute_job(job)
            assert run["status"] == "error", (
                "SECURITY: org B's job executed org A's query — "
                f"got status={run['status']} row_count={run['row_count']}"
            )
            assert run["row_count"] == 0
        finally:
            registry.unregister(qid)

    def test_job_in_org_a_executes_its_own_query(self):
        """The owning org's job still runs its query — no functional regression."""
        from app.jobs.executor import execute_job

        registry = get_query_registry()
        qid = f"sec_job_{uuid.uuid4().hex[:10]}"
        registry.register(
            id=qid,
            sql="SELECT 1 AS x",
            name="Org A job query",
            owner_org_id="org-a",
        )
        try:
            job = {
                "id": str(uuid.uuid4()),
                "kind": "query",
                "target": qid,
                "org_id": "org-a",
            }
            run = execute_job(job)
            assert run["status"] == "success", (
                f"Regression: org A's own job failed: {run['message']}"
            )
            assert run["row_count"] == 1
        finally:
            registry.unregister(qid)

    def test_builtin_system_query_job_still_runs(self):
        """A demo_* system seed (no owner) stays runnable by any job (e.g. the
        existing demo_points_10k test path)."""
        from app.jobs.executor import execute_job

        job = {
            "id": str(uuid.uuid4()),
            "kind": "query",
            "target": "demo_points_10k",
            "org_id": "org-a",
        }
        run = execute_job(job)
        assert run["status"] == "success", run["message"]
        assert run["row_count"] == 10_000
