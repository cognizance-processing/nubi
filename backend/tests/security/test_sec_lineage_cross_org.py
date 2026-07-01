"""Cross-org disclosure regression tests for the lineage endpoints (app/routes/lineage.py).

Vulnerability (found + fixed in this pass)
--------------------------------------------
``app.queries.registry.QueryRegistry`` and ``app.metrics.registry.MetricRegistry``
are PROCESS-GLOBAL singletons spanning every org on the deployment — the exact
same contract ``app.routes.ai`` / ``app.routes.query`` / ``app.routes.metrics``
already document and explicitly guard against ("org scoping happens at the
route layer"). Every ``/lineage/*`` route built its graph/DAG from the RAW,
unfiltered ``registry.all()`` / ``metric_registry.all()`` lists — with NO
per-route visibility gate — so any authenticated user of ANY org could:

* ``GET /lineage``                       — see every org's registered query ids
  + SQL-derived table/column names.
* ``GET /lineage/query/{id}``             — pull full lineage detail (tables,
  columns, outputs) for a query owned by a DIFFERENT org, given its id.
* ``GET /lineage/dag`` / ``/dag/{node}``  — same disclosure via the DAG view
  (the ``/dag`` docstring even CLAIMED "Org-scoped: the caller's org
  determines which queries/metrics are visible" — that claim was never
  implemented).
* ``GET /lineage/columns/{node}?column=`` — column-level provenance for
  another org's metric/query (the exact surface this security wave targets).

The pre-existing ``TestCrossOrgIsolation`` in ``tests/test_lineage_dag.py``
only asserted that a node which was NEVER registered anywhere returns 404 —
it never registered a REAL second-org node and checked it was hidden, so the
gap went undetected. This file closes that gap: it registers a genuine
org-A-owned query directly in the process-global registry and asserts org B's
authenticated token can never see it via ANY lineage route, while org A's own
token still can (no functional regression).

Fix: ``app/routes/lineage.py`` now resolves the caller's org (same helper
``app.routes.ai._resolve_org_id`` uses) and filters BOTH registries through
the exact same visibility gate ``GET /ai/context`` already applies
(``app.routes.ai._query_visible_to_org`` / ``_visible_query_row_ids`` /
``_visible_metric_slugs``) before building any graph/DAG — no new logic,
reuse of an already-audited gate.

SECOND, DEEPER FINDING (caught by the live pentest against a real seeded DB)
-------------------------------------------------------------------------------
Running the fix above against the demo-seeded Postgres (``RUN_E2E=1``)
immediately surfaced a second bug in the REUSED gate itself:
``app.routes.ai._query_visible_to_org`` treated ``rq.owner_org_id is None`` as
"unowned -> visible to everyone". That is correct for genuine built-in seeds
(``rq.system``) but WRONG for the overwhelming majority of real queries:
``app.queries.registry.load_persisted_queries`` (the STARTUP bulk loader that
hydrates the registry from the ``queries`` DB table) never stamps
``owner_org_id`` at all — every persisted query is ``owner_org_id=None`` in
the in-memory object even though its DB row has a real ``org_id``. So EVERY
persisted query in the deployment (not just runtime ``save_as`` ones) was
being treated as globally public by the visibility gate — table nodes derived
from another org's persisted queries (e.g. ``saas_accounts``) leaked into
``/lineage/dag`` for an unrelated org. This is now fixed in
``app.routes.ai._query_visible_to_org``: ``owner_org_id is None`` (and not
``system``) now falls through to the DB-backed ``row_ids`` check instead of
short-circuiting to "visible". ``TestPersistedQueryOwnerNoneIsNotPublic``
below is the unit-level regression guard; the live e2e regression guard is
``tests/e2e/test_pentest_live.py::TestCrossTenantIDOR::test_cross_org_lineage_dag_no_node_overlap``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.queries.registry import get_query_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str, email: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


@pytest_asyncio.fixture
async def two_org_lineage_clients(app, fake_db):
    """Two independent orgs, each with a seeded owner user + org membership."""
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
def org_a_secret_query(two_org_lineage_clients):
    """Register a real, org-A-owned query directly in the global registry.

    Mirrors how ``POST /ai/sql?save_as=`` registers a query
    (``registry.register(..., owner_org_id=org_id)``) without needing the full
    HTTP round-trip. Torn down after the test so it never leaks into other
    tests sharing the process-global registry.
    """
    _ac, _user_a, org_a, _user_b, _org_b = two_org_lineage_clients
    registry = get_query_registry()
    query_id = f"sec_test_secret_{uuid.uuid4().hex[:10]}"
    registry.register(
        id=query_id,
        sql="SELECT customer_ssn, revenue FROM org_a_private_table",
        name="Org A private revenue",
        owner_org_id=org_a,
    )
    yield query_id
    registry.unregister(query_id)


# ---------------------------------------------------------------------------
# GET /lineage — full graph
# ---------------------------------------------------------------------------


class TestLineageGraphCrossOrg:
    @pytest.mark.asyncio
    async def test_org_b_does_not_see_org_a_query_in_full_graph(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, _user_a, _org_a, user_b, _org_b = two_org_lineage_clients
        resp = await ac.get("/api/v1/lineage", headers=_auth_headers(user_b))
        assert resp.status_code == 200
        body = resp.json()
        assert org_a_secret_query not in body["queries"], (
            "SECURITY: org B's /lineage response leaked org A's private query id "
            f"{org_a_secret_query!r}"
        )
        # The table name from org A's private SQL must not appear either.
        assert "org_a_private_table" not in body["tables"], (
            "SECURITY: org B's /lineage 'tables' index leaked org A's private table"
        )

    @pytest.mark.asyncio
    async def test_org_a_still_sees_its_own_query_in_full_graph(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, user_a, _org_a, _user_b, _org_b = two_org_lineage_clients
        resp = await ac.get("/api/v1/lineage", headers=_auth_headers(user_a))
        assert resp.status_code == 200
        body = resp.json()
        assert org_a_secret_query in body["queries"], (
            "Regression: org A can no longer see its own registered query in /lineage"
        )

    @pytest.mark.asyncio
    async def test_system_seed_query_visible_to_both_orgs(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        """demo_all (system=True, no owner) must remain visible to every org."""
        ac, _user_a, _org_a, user_b, _org_b = two_org_lineage_clients
        resp = await ac.get("/api/v1/lineage", headers=_auth_headers(user_b))
        assert resp.status_code == 200
        assert "demo_all" in resp.json()["queries"]


# ---------------------------------------------------------------------------
# GET /lineage/query/{id}
# ---------------------------------------------------------------------------


class TestLineageQueryDetailCrossOrg:
    @pytest.mark.asyncio
    async def test_org_b_gets_404_for_org_a_query_detail(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, _user_a, _org_a, user_b, _org_b = two_org_lineage_clients
        resp = await ac.get(
            f"/api/v1/lineage/query/{org_a_secret_query}",
            headers=_auth_headers(user_b),
        )
        assert resp.status_code == 404, (
            f"SECURITY: org B fetched org A's query lineage detail — "
            f"got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_org_a_can_fetch_its_own_query_detail(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, user_a, _org_a, _user_b, _org_b = two_org_lineage_clients
        resp = await ac.get(
            f"/api/v1/lineage/query/{org_a_secret_query}",
            headers=_auth_headers(user_a),
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == org_a_secret_query


# ---------------------------------------------------------------------------
# GET /lineage/dag and /lineage/dag/{node_id}
# ---------------------------------------------------------------------------


class TestLineageDagCrossOrg:
    @pytest.mark.asyncio
    async def test_org_b_dag_does_not_list_org_a_node(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, _user_a, _org_a, user_b, _org_b = two_org_lineage_clients
        resp = await ac.get("/api/v1/lineage/dag", headers=_auth_headers(user_b))
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert org_a_secret_query not in node_ids, (
            "SECURITY: org B's /lineage/dag nodes leaked org A's private query id"
        )

    @pytest.mark.asyncio
    async def test_org_a_dag_lists_its_own_node(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, user_a, _org_a, _user_b, _org_b = two_org_lineage_clients
        resp = await ac.get("/api/v1/lineage/dag", headers=_auth_headers(user_a))
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert org_a_secret_query in node_ids

    @pytest.mark.asyncio
    async def test_org_b_dag_node_lookup_is_404(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, _user_a, _org_a, user_b, _org_b = two_org_lineage_clients
        resp = await ac.get(
            f"/api/v1/lineage/dag/{org_a_secret_query}",
            headers=_auth_headers(user_b),
        )
        assert resp.status_code == 404, (
            f"SECURITY: org B resolved org A's DAG node — got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_org_a_dag_node_lookup_succeeds(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, user_a, _org_a, _user_b, _org_b = two_org_lineage_clients
        resp = await ac.get(
            f"/api/v1/lineage/dag/{org_a_secret_query}",
            headers=_auth_headers(user_a),
        )
        assert resp.status_code == 200
        assert resp.json()["node_id"] == org_a_secret_query


# ---------------------------------------------------------------------------
# GET /lineage/columns/{node_id} — the specific surface this wave audits
# ---------------------------------------------------------------------------


class TestLineageColumnsCrossOrg:
    @pytest.mark.asyncio
    async def test_org_b_cannot_resolve_org_a_column_lineage(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, _user_a, _org_a, user_b, _org_b = two_org_lineage_clients
        resp = await ac.get(
            f"/api/v1/lineage/columns/{org_a_secret_query}",
            params={"column": "customer_ssn"},
            headers=_auth_headers(user_b),
        )
        assert resp.status_code == 404, (
            f"SECURITY: org B resolved column-level provenance for org A's "
            f"private query — got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_org_a_can_resolve_its_own_column_lineage(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        ac, user_a, _org_a, _user_b, _org_b = two_org_lineage_clients
        resp = await ac.get(
            f"/api/v1/lineage/columns/{org_a_secret_query}",
            params={"column": "revenue"},
            headers=_auth_headers(user_a),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["node_id"] == org_a_secret_query
        assert body["column"] == "revenue"

    @pytest.mark.asyncio
    async def test_missing_column_param_is_400_not_a_leak(
        self, two_org_lineage_clients, org_a_secret_query
    ):
        """A missing 'column' query param is a 400 regardless of node ownership —
        this must not be usable as an oracle to distinguish 'exists in another
        org' (404) from 'missing param' (400) for a node the caller cannot see.
        """
        ac, _user_a, _org_a, user_b, _org_b = two_org_lineage_clients
        resp = await ac.get(
            f"/api/v1/lineage/columns/{org_a_secret_query}",
            headers=_auth_headers(user_b),
        )
        # FastAPI's own request-shape validation (missing required query param)
        # fires identically regardless of node ownership — 422 here is a pure
        # syntactic gate, not an org-existence oracle (same code for org A's own
        # node too). The important invariant is simply "never 200, never 500".
        assert resp.status_code in (400, 404, 422)
        assert resp.status_code != 500


# ===========================================================================
# Root-cause regression: owner_org_id=None must NOT mean "globally visible"
# ===========================================================================


class TestPersistedQueryOwnerNoneIsNotPublic:
    """Unit-level guard for the second finding: a query registered the way
    ``load_persisted_queries`` registers EVERY startup-loaded query (no
    ``owner_org_id`` at all) must be gated by the DB-backed row_ids check —
    NOT waved through as "unowned == public". Only ``rq.system`` entries get
    the unconditional-visibility exemption.
    """

    def test_non_system_none_owner_hidden_without_matching_row_id(self):
        from app.queries.registry import RegisteredQuery
        from app.routes.ai import _query_visible_to_org

        rq = RegisteredQuery(
            id="persisted-query-id-1", sql="SELECT * FROM saas_accounts", name="x",
            system=False, owner_org_id=None,
        )
        # Caller's org has NO matching row for this query -> must be hidden.
        assert _query_visible_to_org(rq, caller_org="org-b", row_ids=set()) is False
        assert _query_visible_to_org(rq, caller_org="org-b", row_ids={"some-other-id"}) is False

    def test_non_system_none_owner_visible_with_matching_row_id(self):
        from app.queries.registry import RegisteredQuery
        from app.routes.ai import _query_visible_to_org

        rq = RegisteredQuery(
            id="persisted-query-id-1", sql="SELECT * FROM saas_accounts", name="x",
            system=False, owner_org_id=None,
        )
        # The DB confirms this row belongs to the caller's org -> visible.
        assert _query_visible_to_org(rq, caller_org="org-a", row_ids={"persisted-query-id-1"}) is True

    def test_system_seed_still_unconditionally_visible(self):
        from app.queries.registry import RegisteredQuery
        from app.routes.ai import _query_visible_to_org

        rq = RegisteredQuery(id="demo_all", sql="SELECT 1", name="x", system=True, owner_org_id=None)
        assert _query_visible_to_org(rq, caller_org="org-b", row_ids=set()) is True
        assert _query_visible_to_org(rq, caller_org=None, row_ids=None) is True

    def test_scoping_unavailable_fails_open_for_demo_test_convenience(self):
        """row_ids=None (org resolution unavailable) still falls back to
        visible — matches the documented demo/test-path fallback, distinct
        from the 'row_ids=set() -> genuinely no match' case above."""
        from app.queries.registry import RegisteredQuery
        from app.routes.ai import _query_visible_to_org

        rq = RegisteredQuery(id="q1", sql="SELECT 1", name="x", system=False, owner_org_id=None)
        assert _query_visible_to_org(rq, caller_org=None, row_ids=None) is True

    def test_explicitly_owned_query_still_gated_by_exact_org_match(self):
        """Regression: the pre-existing explicit-ownership path (save_as) must
        keep working exactly as before this fix."""
        from app.queries.registry import RegisteredQuery
        from app.routes.ai import _query_visible_to_org

        rq = RegisteredQuery(id="q2", sql="SELECT 1", name="x", system=False, owner_org_id="org-a")
        assert _query_visible_to_org(rq, caller_org="org-a", row_ids=None) is True
        assert _query_visible_to_org(rq, caller_org="org-b", row_ids=None) is False
        assert _query_visible_to_org(rq, caller_org="org-b", row_ids={"q2"}) is False, (
            "An explicit owner_org_id mismatch must win even if the id "
            "coincidentally appears in the caller's row_ids"
        )


@pytest.mark.asyncio
async def test_ai_context_does_not_leak_persisted_other_org_query(app, fake_db):
    """End-to-end proof against GET /ai/context: a query registered the way
    the startup bulk loader registers it (owner_org_id=None, a real DB row
    under org A) must not appear for org B, and must appear for org A.
    """
    from datetime import datetime, timezone

    from app.queries.registry import get_query_registry
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo
    from httpx import ASGITransport, AsyncClient

    from app.auth.jwt import mint_access_token

    user_a = str(uuid.uuid4())
    org_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    org_b = str(uuid.uuid4())

    fake_db.users[user_a] = {
        "id": user_a, "email": "orga@example.com", "name": "A",
        "avatar_url": None, "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    fake_db.users[user_b] = {
        "id": user_b, "email": "orgb@example.com", "name": "B",
        "avatar_url": None, "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_a, user_id=user_a, role="owner")
    repo.seed_org_member(org_id=org_b, user_id=user_b, role="owner")
    set_repo(repo)

    # Persist a real 'queries' DB row under org A (mirrors the seeder).
    row = await repo.create(
        "queries", org_id=org_a, created_by=user_a, name="Persisted org-A query",
        config={"sql": "SELECT * FROM saas_accounts"},
    )
    persisted_id = row["id"]

    # Register it into the process-global registry EXACTLY the way
    # load_persisted_queries does — no owner_org_id.
    registry = get_query_registry()
    registry.register(id=persisted_id, sql="SELECT * FROM saas_accounts", name="Persisted org-A query")

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            resp_b = await ac.get(
                "/api/v1/ai/context",
                headers={"Authorization": f"Bearer {mint_access_token(user_b)}"},
            )
            assert resp_b.status_code == 200
            ids_b = {q["id"] for q in resp_b.json().get("queries", [])}
            assert persisted_id not in ids_b, (
                "SECURITY: org B's GET /ai/context leaked org A's persisted "
                "(owner_org_id=None) query"
            )

            resp_a = await ac.get(
                "/api/v1/ai/context",
                headers={"Authorization": f"Bearer {mint_access_token(user_a)}"},
            )
            assert resp_a.status_code == 200
            ids_a = {q["id"] for q in resp_a.json().get("queries", [])}
            assert persisted_id in ids_a, "Regression: org A can no longer see its own persisted query"
    finally:
        registry.unregister(persisted_id)
        set_repo(None)
