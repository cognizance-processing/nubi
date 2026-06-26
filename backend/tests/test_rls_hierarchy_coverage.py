"""Tests for RLS hierarchical scope expansion (app/connectors/rls_hierarchy.py).

Coverage targets (module was at 0%):
- NullHierarchyResolver: always returns empty, expand_policy passthrough
- InMemoryHierarchyResolver: add/add_sync, resolve, expand_policy expansion, clear
- HierarchyResolver.expand_policy: scalar passthrough vs list expansion
- Module-level singleton: get/set/reset_for_tests
- DbHierarchyResolver interface (via mock pool)
- Cross-org isolation at the dict-key level
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.rls_hierarchy import (
    DbHierarchyResolver,
    InMemoryHierarchyResolver,
    NullHierarchyResolver,
    get_hierarchy_resolver,
    reset_for_tests,
    set_hierarchy_resolver,
)


# ---------------------------------------------------------------------------
# NullHierarchyResolver
# ---------------------------------------------------------------------------

class TestNullHierarchyResolver:
    """NullHierarchyResolver always returns [] — every scalar passes through."""

    def test_resolve_returns_empty(self):
        resolver = NullHierarchyResolver()
        result = asyncio.run(
            resolver.resolve("org-A", "region", "Western Cape")
        )
        assert result == []

    def test_resolve_any_org_dimension_value_returns_empty(self):
        resolver = NullHierarchyResolver()
        assert asyncio.run(resolver.resolve("", "", "")) == []
        assert asyncio.run(resolver.resolve("x", "y", "z")) == []

    def test_expand_policy_returns_scalar_unchanged(self):
        resolver = NullHierarchyResolver()
        result = asyncio.run(
            resolver.expand_policy("org-A", "region", "Cape Town")
        )
        # No children → scalar passes through unchanged
        assert result == "Cape Town"

    def test_expand_policy_integer_scalar(self):
        resolver = NullHierarchyResolver()
        result = asyncio.run(
            resolver.expand_policy("org-A", "store_id", 42)
        )
        assert result == 42


# ---------------------------------------------------------------------------
# InMemoryHierarchyResolver
# ---------------------------------------------------------------------------

class TestInMemoryHierarchyResolver:
    """InMemoryHierarchyResolver is the in-process test double."""

    def setup_method(self):
        self.resolver = InMemoryHierarchyResolver()

    def test_add_and_resolve(self):
        asyncio.run(
            self.resolver.add("org-1", "region", "Western Cape", ["10", "11", "12"])
        )
        result = asyncio.run(
            self.resolver.resolve("org-1", "region", "Western Cape")
        )
        assert result == ["10", "11", "12"]

    def test_add_sync_and_resolve(self):
        self.resolver.add_sync("org-1", "region", "Eastern Cape", ["20", "21"])
        result = asyncio.run(
            self.resolver.resolve("org-1", "region", "Eastern Cape")
        )
        assert result == ["20", "21"]

    def test_resolve_returns_copy_not_reference(self):
        """Returned list must be independent — mutations cannot corrupt the store."""
        self.resolver.add_sync("org-1", "region", "GP", ["1", "2", "3"])
        result1 = asyncio.run(
            self.resolver.resolve("org-1", "region", "GP")
        )
        result1.append("INJECTED")
        result2 = asyncio.run(
            self.resolver.resolve("org-1", "region", "GP")
        )
        assert "INJECTED" not in result2

    def test_resolve_unknown_key_returns_empty(self):
        result = asyncio.run(
            self.resolver.resolve("org-1", "region", "Nonexistent")
        )
        assert result == []

    def test_cross_org_isolation(self):
        """org-A's children must NOT appear when querying org-B."""
        self.resolver.add_sync("org-A", "region", "Cape Town", ["10", "11"])
        result = asyncio.run(
            self.resolver.resolve("org-B", "region", "Cape Town")
        )
        assert result == []

    def test_expand_policy_with_children_returns_list(self):
        """When children exist, expand_policy returns the child list (→ IN predicate)."""
        self.resolver.add_sync("org-1", "region", "Western Cape", ["10", "11"])
        result = asyncio.run(
            self.resolver.expand_policy("org-1", "region", "Western Cape")
        )
        assert result == ["10", "11"]

    def test_expand_policy_without_children_returns_scalar(self):
        """No children registered → original scalar returned (→ equality predicate)."""
        result = asyncio.run(
            self.resolver.expand_policy("org-1", "region", "Unknown Region")
        )
        assert result == "Unknown Region"

    def test_expand_policy_non_string_scalar_stringified_for_lookup(self):
        """expand_policy str-ifies the value before lookup; matching key → list."""
        # We seed with str key "99"; calling with int 99 str-ifies to "99"
        self.resolver.add_sync("org-1", "store_id", "99", ["100", "101"])
        result = asyncio.run(
            self.resolver.expand_policy("org-1", "store_id", 99)
        )
        assert result == ["100", "101"]

    def test_clear_removes_all_data(self):
        self.resolver.add_sync("org-1", "region", "Cape Town", ["1", "2"])
        self.resolver.clear()
        result = asyncio.run(
            self.resolver.resolve("org-1", "region", "Cape Town")
        )
        assert result == []

    def test_add_overwrites_existing_key(self):
        self.resolver.add_sync("org-1", "region", "GP", ["old_1"])
        asyncio.run(
            self.resolver.add("org-1", "region", "GP", ["new_1", "new_2"])
        )
        result = asyncio.run(
            self.resolver.resolve("org-1", "region", "GP")
        )
        assert result == ["new_1", "new_2"]

    def test_add_sync_child_list_is_copied(self):
        """Internal store must not hold a reference to the caller's list."""
        children = ["a", "b"]
        self.resolver.add_sync("org-1", "dim", "parent", children)
        children.append("c")  # mutate caller's list
        result = asyncio.run(
            self.resolver.resolve("org-1", "dim", "parent")
        )
        assert "c" not in result

    def test_multiple_dimensions_are_independent(self):
        self.resolver.add_sync("org-1", "region", "WC", ["10", "11"])
        self.resolver.add_sync("org-1", "city", "WC", ["cape_town", "stellenbosch"])
        region = asyncio.run(self.resolver.resolve("org-1", "region", "WC"))
        city = asyncio.run(self.resolver.resolve("org-1", "city", "WC"))
        assert region == ["10", "11"]
        assert city == ["cape_town", "stellenbosch"]

    def test_empty_child_list_stored_and_expand_policy_returns_scalar(self):
        """An empty child list means 'no expansion' — scalar passes through."""
        self.resolver.add_sync("org-1", "region", "Nowhere", [])
        result = asyncio.run(
            self.resolver.expand_policy("org-1", "region", "Nowhere")
        )
        # Empty list is falsy → expand_policy returns original scalar
        assert result == "Nowhere"


# ---------------------------------------------------------------------------
# DbHierarchyResolver (interface coverage via mock pool)
# ---------------------------------------------------------------------------

class TestDbHierarchyResolver:
    """DbHierarchyResolver queries the DB; we mock the pool to cover the code path."""

    def test_resolve_returns_child_values_from_db(self):
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[
            {"child_value": "10"},
            {"child_value": "11"},
        ])

        resolver = DbHierarchyResolver(pool=mock_pool)
        result = asyncio.run(
            resolver.resolve("org-1", "region", "Western Cape")
        )

        assert result == ["10", "11"]
        mock_pool.fetch.assert_awaited_once()
        # Verify the query uses parameterised args (not string concat)
        call_args = mock_pool.fetch.call_args
        positional_args = call_args[0]
        assert "org-1" in positional_args
        assert "region" in positional_args
        assert "Western Cape" in positional_args

    def test_resolve_empty_result_from_db(self):
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        resolver = DbHierarchyResolver(pool=mock_pool)
        result = asyncio.run(
            resolver.resolve("org-1", "region", "Unknown")
        )
        assert result == []

    def test_expand_policy_with_db_children(self):
        """expand_policy (on base class) calls resolve → triggers DB fetch."""
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[
            {"child_value": "X"},
            {"child_value": "Y"},
        ])

        resolver = DbHierarchyResolver(pool=mock_pool)
        result = asyncio.run(
            resolver.expand_policy("org-1", "region", "Parent")
        )
        assert result == ["X", "Y"]

    def test_expand_policy_no_children_scalar_passthrough(self):
        mock_pool = MagicMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        resolver = DbHierarchyResolver(pool=mock_pool)
        result = asyncio.run(
            resolver.expand_policy("org-1", "region", "Solo Region")
        )
        assert result == "Solo Region"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestModuleLevelSingleton:
    """get/set/reset_for_tests control the module-level active resolver."""

    def teardown_method(self):
        reset_for_tests()  # always restore to NullHierarchyResolver

    def test_default_is_null_resolver(self):
        reset_for_tests()
        resolver = get_hierarchy_resolver()
        assert isinstance(resolver, NullHierarchyResolver)

    def test_set_resolver_replaces_singleton(self):
        custom = InMemoryHierarchyResolver()
        custom.add_sync("org-1", "dim", "parent", ["child_1"])
        set_hierarchy_resolver(custom)
        active = get_hierarchy_resolver()
        assert active is custom

    def test_reset_for_tests_restores_null(self):
        set_hierarchy_resolver(InMemoryHierarchyResolver())
        reset_for_tests()
        assert isinstance(get_hierarchy_resolver(), NullHierarchyResolver)

    def test_set_then_get_same_object(self):
        mem = InMemoryHierarchyResolver()
        set_hierarchy_resolver(mem)
        assert get_hierarchy_resolver() is mem
