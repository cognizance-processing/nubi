"""Tests for two follow-up fixes in the Flows engine.

Follow-up 1 — BYO org_id threading
------------------------------------
``_handle_query`` in registry.py used to resolve ``org_id`` exclusively from
``claims["org_id"]``.  For durable / scheduled flows the runtime calls
``execute_task`` with an empty ``claims={}`` dict (no JWT), but always
populates ``ctx.org_id`` from ``flow_run.org_id``.  The fix: PREFER
``ctx.org_id`` over ``claims.get("org_id")``.

Coverage
~~~~~~~~
1. org_id threaded through TaskContext → _handle_query with empty claims.
   a. BYO DuckDB connector resolves correctly when ctx.org_id is set and
      claims is {} (scheduled-tick case).
   b. Claims org_id is used as fallback when ctx.org_id is None.
   c. ctx.org_id wins over claims["org_id"] when both are set.

Follow-up 2 — Preview LIMIT pushdown
--------------------------------------
SQL cells running in preview mode must inject a genuine ``LIMIT <n>`` into the
SQL via the planner (sqlglot AST) before the warehouse executes — not a
post-fetch Python cap — so big BYO-warehouse cells never pull millions of rows.
RLS predicate injection must remain independent of (before) the LIMIT.

Coverage
~~~~~~~~
2. Preview LIMIT pushed into planned SQL.
   a. ``_handle_query`` in preview mode injects ``LIMIT <n>`` into the SQL.
   b. The LIMIT respects the preview_limit value on the TaskContext.
   c. RLS predicates are still injected even when a preview limit is set.
   d. Non-preview mode does NOT inject a LIMIT.
   e. ``_execute_query_with_bridge`` (bridge path) also injects LIMIT via plan().
"""

from __future__ import annotations

from typing import Any

import pytest

from app.flows.executor import TaskContext, _execute_query_with_bridge
from app.flows.registry import (
    _handle_query,
    reset_for_tests,
)
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORG_ID = "org-followup-1"
OTHER_ORG_ID = "org-followup-2"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Re-bootstrap the task kind registry before each test."""
    reset_for_tests()


@pytest.fixture()
def repo() -> InMemoryRepo:
    """InMemoryRepo wired as the global repo provider."""
    r = InMemoryRepo()
    set_repo(r)
    return r


@pytest.fixture(autouse=True)
def _restore_repo(repo: InMemoryRepo):  # noqa: ANN001
    """Ensure the global repo singleton is cleared after each test."""
    yield
    set_repo(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_datastore(
    repo: InMemoryRepo,
    datastore_id: str,
    org_id: str,
    connector_type: str = "duckdb",
) -> dict[str, Any]:
    """Insert a minimal datastore row directly into the InMemoryRepo store."""
    from copy import deepcopy
    from datetime import datetime, timezone

    row: dict[str, Any] = {
        "id": datastore_id,
        "org_id": org_id,
        "project_id": None,
        "created_by": "user-test",
        "name": f"ds-{connector_type}",
        "config": deepcopy({"connector_type": connector_type}),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    repo._store["datastores"][datastore_id] = row
    return row


# ---------------------------------------------------------------------------
# Follow-up 1: BYO org_id threading
# ---------------------------------------------------------------------------


class TestOrgIdThreading:
    """ctx.org_id is preferred over claims['org_id'] in _handle_query."""

    def test_ctx_org_id_used_when_claims_empty(self, repo: InMemoryRepo) -> None:
        """Scheduled tick: claims={}, ctx.org_id set → connector resolves OK."""
        ds_id = "ds-ctx-org"
        _seed_datastore(repo, ds_id, ORG_ID)

        ctx = TaskContext(org_id=ORG_ID)
        # Empty claims — simulates a scheduled tick with no JWT.
        result = _handle_query(
            {"sql": "SELECT 42 AS n", "datastore_id": ds_id},
            ctx,
            {},  # empty claims
        )

        assert result["row_count"] == 1
        assert result["rows"][0]["n"] == 42

    def test_claims_org_id_fallback_when_ctx_org_id_none(
        self, repo: InMemoryRepo
    ) -> None:
        """When ctx.org_id is None, claims['org_id'] is used as fallback."""
        ds_id = "ds-claims-fallback"
        _seed_datastore(repo, ds_id, ORG_ID)

        ctx = TaskContext(org_id=None)  # not set
        result = _handle_query(
            {"sql": "SELECT 99 AS x", "datastore_id": ds_id},
            ctx,
            {"org_id": ORG_ID},  # claims carry org_id
        )

        assert result["row_count"] == 1
        assert result["rows"][0]["x"] == 99

    def test_ctx_org_id_wins_over_claims_org_id(self, repo: InMemoryRepo) -> None:
        """ctx.org_id takes precedence when both ctx.org_id and claims['org_id'] differ."""
        from app.errors import AppError

        ds_id = "ds-ctx-wins"
        # Datastore belongs to ORG_ID, not OTHER_ORG_ID.
        _seed_datastore(repo, ds_id, ORG_ID)

        ctx = TaskContext(org_id=ORG_ID)
        # claims carry a *different* org — ctx.org_id should win.
        result = _handle_query(
            {"sql": "SELECT 7 AS v", "datastore_id": ds_id},
            ctx,
            {"org_id": OTHER_ORG_ID},  # wrong org in claims
        )

        # ctx.org_id=ORG_ID matches the datastore → resolves OK.
        assert result["row_count"] == 1
        assert result["rows"][0]["v"] == 7

    def test_empty_ctx_org_id_and_empty_claims_raises(
        self, repo: InMemoryRepo
    ) -> None:
        """When both ctx.org_id and claims['org_id'] are absent, connector not found."""
        from app.errors import AppError

        ds_id = "ds-no-org"
        _seed_datastore(repo, ds_id, ORG_ID)

        ctx = TaskContext(org_id=None)
        with pytest.raises(AppError) as exc_info:
            _handle_query(
                {"sql": "SELECT 1", "datastore_id": ds_id},
                ctx,
                {},  # no org_id anywhere
            )

        assert exc_info.value.code == "datastore_not_found"


# ---------------------------------------------------------------------------
# Follow-up 2: Preview LIMIT pushdown
# ---------------------------------------------------------------------------


class TestPreviewLimitPushdown:
    """LIMIT is injected into the planned SQL via sqlglot before warehouse exec."""

    def test_preview_limit_injected_into_sql(self, repo: InMemoryRepo) -> None:
        """In preview mode, LIMIT is pushed down — row count is capped at limit."""
        # Use demo path (no datastore_id) so we control the data exactly.
        ctx = TaskContext(preview_mode=True, preview_limit=3)
        claims: dict[str, Any] = {"org_id": ORG_ID}

        # Demo table has 5 rows; preview_limit=3 should cap the result.
        result = _handle_query(
            {"sql": "SELECT * FROM demo ORDER BY id"},
            ctx,
            claims,
        )

        assert result["row_count"] == 3, (
            f"Expected 3 rows (capped by preview_limit), got {result['row_count']}"
        )

    def test_preview_limit_value_respected(self, repo: InMemoryRepo) -> None:
        """The exact preview_limit value (not just 'some limit') is pushed."""
        ctx = TaskContext(preview_mode=True, preview_limit=2)
        claims: dict[str, Any] = {"org_id": ORG_ID}

        result = _handle_query(
            {"sql": "SELECT * FROM demo ORDER BY id"},
            ctx,
            claims,
        )

        assert result["row_count"] == 2

    def test_preview_limit_with_rls_both_applied(self, repo: InMemoryRepo) -> None:
        """RLS predicate AND preview LIMIT are both enforced simultaneously.

        Demo table: 5 rows total, 3 with active=True.
        With RLS active=True + preview_limit=2 → expect exactly 2 rows.
        """
        ctx = TaskContext(preview_mode=True, preview_limit=2)
        claims: dict[str, Any] = {
            "org_id": ORG_ID,
            "policies": {"active": True},
        }

        result = _handle_query(
            {"sql": "SELECT * FROM demo ORDER BY id"},
            ctx,
            claims,
        )

        # RLS narrows to 3 active rows, then LIMIT 2 caps the result.
        assert result["row_count"] == 2
        # All returned rows must satisfy the RLS predicate.
        assert all(r["active"] is True for r in result["rows"])

    def test_non_preview_mode_no_limit_injected(self, repo: InMemoryRepo) -> None:
        """Durable (non-preview) mode does NOT inject any LIMIT."""
        ctx = TaskContext(preview_mode=False, preview_limit=2)
        claims: dict[str, Any] = {"org_id": ORG_ID}

        # 5 rows in demo table — all should be returned when not in preview.
        result = _handle_query(
            {"sql": "SELECT * FROM demo ORDER BY id"},
            ctx,
            claims,
        )

        assert result["row_count"] == 5

    def test_preview_limit_bridge_path(self, repo: InMemoryRepo) -> None:
        """_execute_query_with_bridge also injects LIMIT via plan() in preview mode.

        The bridge path (used when upstream Python cells exist) must behave the
        same as the direct _handle_query path: LIMIT is pushed into the SQL.
        """
        ctx = TaskContext(preview_mode=True, preview_limit=2)
        claims: dict[str, Any] = {"org_id": ORG_ID}

        # No bridge tables — the function path still calls plan(limit=...).
        result = _execute_query_with_bridge(
            {"sql": "SELECT * FROM demo ORDER BY id"},
            ctx,
            claims,
            {},  # no bridge tables
        )

        assert result["row_count"] == 2

    def test_preview_limit_smaller_existing_limit_preserved(
        self, repo: InMemoryRepo
    ) -> None:
        """If the SQL already has LIMIT < preview_limit, the smaller limit wins.

        The planner's push_limit() keeps the existing (smaller) LIMIT.
        """
        ctx = TaskContext(preview_mode=True, preview_limit=10)
        claims: dict[str, Any] = {"org_id": ORG_ID}

        # SQL already has LIMIT 1, preview_limit=10 → should stay at 1.
        result = _handle_query(
            {"sql": "SELECT * FROM demo LIMIT 1"},
            ctx,
            claims,
        )

        # The existing LIMIT 1 is smaller than preview_limit=10, so it stays.
        assert result["row_count"] == 1
