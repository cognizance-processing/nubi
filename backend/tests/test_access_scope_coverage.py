"""Tests for app/access/scope.py — effective scope resolution (was 0% coverage).

Tests cover:
- subject_for_identity: user vs embed token kinds
- _as_value_list: scalar/list/dict inputs
- resolve_scope: no-org path, baseline effective policies, hierarchy expansion,
  grants merge, fail-closed on exception, expanded flag tracking
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.access.scope import _as_value_list, resolve_scope, subject_for_identity


# ---------------------------------------------------------------------------
# Fake identity
# ---------------------------------------------------------------------------

class FakeIdentity:
    """Minimal VerifiedIdentity stub."""

    def __init__(
        self,
        *,
        kind: str = "user",
        user_id: str = "user-abc",
        org: str | None = "org-123",
        policies: dict[str, Any] | None = None,
        scope: list[str] | None = None,
    ):
        self.kind = kind
        self.user_id = user_id
        self.org = org
        self.policies = policies or {}
        self.scope = scope or []


# ---------------------------------------------------------------------------
# _as_value_list
# ---------------------------------------------------------------------------

class TestAsValueList:
    def test_scalar_string_becomes_singleton_list(self):
        assert _as_value_list("Cape Town") == ["Cape Town"]

    def test_scalar_int_becomes_singleton_list(self):
        assert _as_value_list(42) == ["42"]

    def test_list_of_mixed_types_stringified(self):
        assert _as_value_list(["a", 1, True]) == ["a", "1", "True"]

    def test_empty_list_returns_empty_list(self):
        assert _as_value_list([]) == []

    def test_dict_returns_empty_list(self):
        """Range-band dicts are not value-enumerable → empty."""
        assert _as_value_list({"gte": "2024-01-01", "lte": "2024-12-31"}) == []

    def test_empty_dict_returns_empty_list(self):
        assert _as_value_list({}) == []

    def test_none_value_omitted_not_stringified(self):
        # A null policy value must NOT become the literal string "None"
        # (which would inject `col IN ('None')`); it is omitted instead.
        assert _as_value_list(None) == []
        assert _as_value_list(["a", None, "b"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# subject_for_identity
# ---------------------------------------------------------------------------

class TestSubjectForIdentity:
    def test_user_kind_returns_user_type(self):
        identity = FakeIdentity(kind="user", user_id="u-123")
        sub_type, sub_id = subject_for_identity(identity)
        assert sub_type == "user"
        assert sub_id == "u-123"

    def test_embed_kind_returns_embed_sub_type(self):
        identity = FakeIdentity(kind="embed", user_id="embed-456")
        sub_type, sub_id = subject_for_identity(identity)
        assert sub_type == "embed_sub"
        assert sub_id == "embed-456"

    def test_user_id_is_stringified(self):
        identity = FakeIdentity(kind="user", user_id=99)
        _, sub_id = subject_for_identity(identity)
        assert isinstance(sub_id, str)
        assert sub_id == "99"


# ---------------------------------------------------------------------------
# resolve_scope: no-org path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_scope_no_org_returns_raw_policies_without_expansion():
    """Identity with no org → skip expansion/grants, return raw policies fail-closed."""
    identity = FakeIdentity(
        org=None,
        policies={"region": "Western Cape"},
    )

    result = await resolve_scope(identity)

    assert result["org"] is None
    assert result["expanded"] is False
    assert result["policies"] == {"region": "Western Cape"}
    # effective_policies is baseline normalisation only (no expansion)
    assert result["effective_policies"] == {"region": ["Western Cape"]}


@pytest.mark.asyncio
async def test_resolve_scope_empty_org_string_returns_raw_policies():
    """Empty string org → no expansion (falsy org guard)."""
    identity = FakeIdentity(org="", policies={"store_id": "10"})
    result = await resolve_scope(identity)
    assert result["expanded"] is False
    assert result["effective_policies"]["store_id"] == ["10"]


# ---------------------------------------------------------------------------
# resolve_scope: baseline normalisation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_scope_no_policies_returns_empty_effective():
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value={})),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        mock_store.effective_for_subject = AsyncMock(return_value={})
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-1", policies={})
        result = await resolve_scope(identity)

    assert result["effective_policies"] == {}
    assert result["expanded"] is False


@pytest.mark.asyncio
async def test_resolve_scope_dict_policy_baseline_is_empty_list():
    """A range-band dict policy is normalised to [] in the baseline."""
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value={})),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        mock_store.effective_for_subject = AsyncMock(return_value={})
        mock_gs.return_value = mock_store

        identity = FakeIdentity(
            org="org-1",
            policies={"created_at": {"gte": "2024-01-01", "lte": "2024-12-31"}},
        )
        result = await resolve_scope(identity)

    # Dict value → _as_value_list returns [] → baseline has [] for that key
    assert result["effective_policies"]["created_at"] == []
    assert result["expanded"] is False


# ---------------------------------------------------------------------------
# resolve_scope: hierarchy expansion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_scope_hierarchy_expansion_sets_expanded_flag():
    """When expand_rls_policies returns extra values, expanded=True."""
    expanded = {"region": ["10", "11", "12"]}  # expanded scalar → list

    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value=expanded)),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        mock_store.effective_for_subject = AsyncMock(return_value={})
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-1", policies={"region": "Western Cape"})
        result = await resolve_scope(identity)

    assert result["expanded"] is True
    # The expanded list should be merged into effective_policies
    assert "10" in result["effective_policies"]["region"]
    assert "11" in result["effective_policies"]["region"]


@pytest.mark.asyncio
async def test_resolve_scope_hierarchy_expansion_exception_is_fail_closed():
    """If expand_rls_policies raises, effective_policies falls back to baseline."""
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        mock_store.effective_for_subject = AsyncMock(return_value={})
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-1", policies={"region": "Western Cape"})
        result = await resolve_scope(identity)

    # Fail-closed: raw baseline only, no expansion
    assert result["expanded"] is False
    assert result["effective_policies"] == {"region": ["Western Cape"]}


# ---------------------------------------------------------------------------
# resolve_scope: grants merge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_scope_grants_merge_adds_values():
    """Access grants add extra dimension values and set expanded=True."""
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value={})),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        mock_store.effective_for_subject = AsyncMock(
            return_value={"region": ["East", "West"]}
        )
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-1", policies={})
        result = await resolve_scope(identity)

    assert result["expanded"] is True
    assert result["effective_policies"]["region"] == ["East", "West"]


@pytest.mark.asyncio
async def test_resolve_scope_grants_merge_deduplicates_values():
    """Values already in effective_policies from raw claims are not duplicated."""
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value={})),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        # Grant returns the same value already in raw policies
        mock_store.effective_for_subject = AsyncMock(
            return_value={"region": ["Western Cape"]}
        )
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-1", policies={"region": "Western Cape"})
        result = await resolve_scope(identity)

    # Should be deduped — only one occurrence of "Western Cape"
    assert result["effective_policies"]["region"].count("Western Cape") == 1
    # expanded == False because the merged set equals the raw baseline
    assert result["expanded"] is False


@pytest.mark.asyncio
async def test_resolve_scope_grants_merge_exception_is_best_effort():
    """If grants merge raises, effective_policies keeps whatever was expanded so far."""
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value={})),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        mock_store.effective_for_subject = AsyncMock(side_effect=RuntimeError("db down"))
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-1", policies={"region": "WC"})
        result = await resolve_scope(identity)

    # Grants failed but expansion succeeded (no expansion here) → baseline returned
    assert result["effective_policies"] == {"region": ["WC"]}


@pytest.mark.asyncio
async def test_resolve_scope_empty_grant_values_are_skipped():
    """Grants with empty value lists do not widen effective_policies."""
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value={})),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        # Grant returns empty list for a dimension → should be skipped
        mock_store.effective_for_subject = AsyncMock(
            return_value={"region": []}
        )
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-1", policies={})
        result = await resolve_scope(identity)

    # Empty grant skipped → effective_policies has no "region" key
    assert result["effective_policies"].get("region") is None
    assert result["expanded"] is False


# ---------------------------------------------------------------------------
# resolve_scope: scope list is passed through
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_scope_passes_scope_list():
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value={})),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        mock_store.effective_for_subject = AsyncMock(return_value={})
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-1", scope=["read:metrics", "write:dashboards"])
        result = await resolve_scope(identity)

    assert result["scope"] == ["read:metrics", "write:dashboards"]


@pytest.mark.asyncio
async def test_resolve_scope_org_is_in_result():
    with (
        patch("app.connectors.planner.expand_rls_policies", AsyncMock(return_value={})),
        patch("app.access.scope.get_grants_store") as mock_gs,
    ):
        mock_store = MagicMock()
        mock_store.effective_for_subject = AsyncMock(return_value={})
        mock_gs.return_value = mock_store

        identity = FakeIdentity(org="org-42")
        result = await resolve_scope(identity)

    assert result["org"] == "org-42"
