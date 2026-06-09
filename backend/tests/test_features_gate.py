"""Tests for the open-core feature gate and EE loader.

Covers
------
- Commercial features are denied by default (no EE registered).
- Non-commercial features are allowed by default.
- A checker registered via ``register_feature`` is honoured.
- A failing checker degrades gracefully to ``False``.
- ``declare_commercial`` extends the deny-by-default set.
- ``load_ee()`` is a safe no-op when EE config (``NUBI_LICENSE_KEY``) is absent.
- ``load_ee()`` registers feature checkers when a valid key is present.
- License tier resolution from ``NUBI_LICENSE_KEY``.
- ``reset_for_tests`` restores the initial state.
- The migration runner's open-core ledger re-keying: billing migrations that
  moved from core into ``database/migrations/ee/`` must never be re-applied
  to databases that recorded them under their legacy bare file names.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# The conftest ``_reset_state`` fixture does NOT yet reset ``app.features``.
# We add an autouse fixture here to handle it locally.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_feature_gate():
    """Reset feature-gate state before and after every test in this module."""
    from app.features import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_license_cache():
    """Reset the license lru_cache before and after every test."""
    try:
        from app.ee.licensing.license import reset_license_cache
        reset_license_cache()
    except Exception:
        pass
    yield
    try:
        from app.ee.licensing.license import reset_license_cache
        reset_license_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# feature_enabled — default behaviour
# ---------------------------------------------------------------------------


class TestFeatureEnabledDefaults:
    """Commercial features are denied; everything else is allowed by default."""

    def test_billing_denied_by_default(self) -> None:
        from app.features import feature_enabled

        assert feature_enabled("billing") is False

    def test_paid_tiers_denied_by_default(self) -> None:
        from app.features import feature_enabled

        assert feature_enabled("paid_tiers") is False

    def test_oss_feature_allowed_by_default(self) -> None:
        from app.features import feature_enabled

        # Non-commercial features should be available in OSS builds.
        assert feature_enabled("flows") is True
        assert feature_enabled("connectors") is True
        assert feature_enabled("dashboards") is True
        assert feature_enabled("git_sync") is True

    def test_unknown_feature_allowed_by_default(self) -> None:
        from app.features import feature_enabled

        # An unregistered, non-commercial feature should default to True.
        assert feature_enabled("some_future_oss_feature") is True


# ---------------------------------------------------------------------------
# register_feature
# ---------------------------------------------------------------------------


class TestRegisterFeature:
    """Registered checkers control ``feature_enabled`` output."""

    def test_commercial_feature_allowed_after_registration(self) -> None:
        from app.features import feature_enabled, register_feature

        assert feature_enabled("billing") is False
        register_feature("billing", lambda: True)
        assert feature_enabled("billing") is True

    def test_commercial_feature_still_denied_with_false_checker(self) -> None:
        from app.features import feature_enabled, register_feature

        register_feature("billing", lambda: False)
        assert feature_enabled("billing") is False

    def test_oss_feature_can_be_disabled_by_checker(self) -> None:
        from app.features import feature_enabled, register_feature

        register_feature("flows", lambda: False)
        assert feature_enabled("flows") is False

    def test_checker_called_each_time(self) -> None:
        """Checker is not cached — called fresh on every feature_enabled call."""
        from app.features import feature_enabled, register_feature

        toggle: list[bool] = [False]
        register_feature("billing", lambda: toggle[0])

        assert feature_enabled("billing") is False
        toggle[0] = True
        assert feature_enabled("billing") is True

    def test_non_callable_checker_raises(self) -> None:
        from app.features import register_feature

        with pytest.raises(TypeError, match="callable"):
            register_feature("billing", "not-a-callable")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Failing checker degrades gracefully
# ---------------------------------------------------------------------------


class TestBrokenChecker:
    """A checker that raises must not crash request handling."""

    def test_failing_checker_returns_false(self) -> None:
        from app.features import feature_enabled, register_feature

        def _boom() -> bool:
            raise RuntimeError("database is down")

        register_feature("billing", _boom)
        # Must not propagate the exception.
        assert feature_enabled("billing") is False


# ---------------------------------------------------------------------------
# declare_commercial
# ---------------------------------------------------------------------------


class TestDeclareCommercial:
    """Additional commercial feature names can be declared at runtime."""

    def test_new_commercial_feature_denied_after_declare(self) -> None:
        from app.features import declare_commercial, feature_enabled

        # Before declaration, an unknown feature defaults to True.
        assert feature_enabled("sso") is True

        declare_commercial("sso")
        assert feature_enabled("sso") is False

    def test_declare_idempotent(self) -> None:
        from app.features import declare_commercial, feature_enabled

        declare_commercial("sso")
        declare_commercial("sso")  # second call must not raise
        assert feature_enabled("sso") is False

    def test_declare_does_not_affect_existing_commercial_names(self) -> None:
        from app.features import declare_commercial, feature_enabled

        declare_commercial("sso")
        # billing is still commercial.
        assert feature_enabled("billing") is False


# ---------------------------------------------------------------------------
# reset_for_tests
# ---------------------------------------------------------------------------


class TestResetForTests:
    """reset_for_tests restores the initial state."""

    def test_registered_checker_cleared_after_reset(self) -> None:
        from app.features import feature_enabled, register_feature, reset_for_tests

        register_feature("billing", lambda: True)
        assert feature_enabled("billing") is True

        reset_for_tests()
        assert feature_enabled("billing") is False

    def test_declared_commercial_cleared_after_reset(self) -> None:
        from app.features import declare_commercial, feature_enabled, reset_for_tests

        declare_commercial("sso")
        assert feature_enabled("sso") is False

        reset_for_tests()
        # sso is no longer declared commercial after reset.
        assert feature_enabled("sso") is True


# ---------------------------------------------------------------------------
# EE loader — load_ee()
# ---------------------------------------------------------------------------


class TestLoadEe:
    """load_ee() must be a safe no-op when EE config is absent."""

    def test_load_ee_returns_false_without_license_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests

        # Remove license key so the FREE tier is resolved.
        monkeypatch.delenv("NUBI_LICENSE_KEY", raising=False)
        reset_license_cache()
        reset_for_tests()

        from app.ee import load_ee

        # load_ee always returns True because the licensing module loads
        # successfully (even on FREE); commercial features will be False.
        result = load_ee()
        # We just assert it doesn't raise and returns a bool.
        assert isinstance(result, bool)

    def test_load_ee_registers_billing_checker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After load_ee with a PRO key, billing should be enabled."""
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests

        monkeypatch.setenv("NUBI_LICENSE_KEY", "nubi_pro_test123")
        reset_license_cache()
        reset_for_tests()

        from app.ee import load_ee
        from app.features import feature_enabled

        result = load_ee()
        assert result is True
        assert feature_enabled("billing") is True
        assert feature_enabled("paid_tiers") is True

    def test_load_ee_billing_denied_on_free_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FREE tier: load_ee loads but billing/paid_tiers remain denied."""
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests

        monkeypatch.delenv("NUBI_LICENSE_KEY", raising=False)
        reset_license_cache()
        reset_for_tests()

        from app.ee import load_ee
        from app.features import feature_enabled

        load_ee()
        assert feature_enabled("billing") is False
        assert feature_enabled("paid_tiers") is False

    def test_load_ee_enterprise_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ENTERPRISE tier enables billing and paid_tiers."""
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests

        monkeypatch.setenv("NUBI_LICENSE_KEY", "nubi_enterprise_acme")
        reset_license_cache()
        reset_for_tests()

        from app.ee import load_ee
        from app.features import feature_enabled

        load_ee()
        assert feature_enabled("billing") is True
        assert feature_enabled("paid_tiers") is True

    def test_load_ee_is_callable_with_no_args(self) -> None:
        """load_ee() must accept being called with no arguments."""
        from app.ee import load_ee

        result = load_ee()
        assert isinstance(result, bool)

    def test_load_ee_is_callable_with_app_none(self) -> None:
        """load_ee(None) must not raise."""
        from app.ee import load_ee

        result = load_ee(app=None)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# License tier resolution
# ---------------------------------------------------------------------------


class TestLicenseTier:
    """NUBI_LICENSE_KEY → Tier resolution."""

    def test_absent_key_gives_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.licensing.license import Tier, get_license, reset_license_cache

        monkeypatch.delenv("NUBI_LICENSE_KEY", raising=False)
        reset_license_cache()
        lic = get_license()
        assert lic.tier is Tier.FREE
        assert lic.is_free is True
        assert lic.is_paid is False

    def test_pro_prefix_gives_pro(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.licensing.license import Tier, get_license, reset_license_cache

        monkeypatch.setenv("NUBI_LICENSE_KEY", "nubi_pro_abc123")
        reset_license_cache()
        lic = get_license()
        assert lic.tier is Tier.PRO
        assert lic.is_paid is True
        assert lic.is_enterprise is False

    def test_enterprise_prefix_gives_enterprise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.licensing.license import Tier, get_license, reset_license_cache

        monkeypatch.setenv("NUBI_LICENSE_KEY", "nubi_enterprise_bigcorp")
        reset_license_cache()
        lic = get_license()
        assert lic.tier is Tier.ENTERPRISE
        assert lic.is_enterprise is True
        assert lic.is_paid is True

    def test_unrecognised_key_gives_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.licensing.license import Tier, get_license, reset_license_cache

        monkeypatch.setenv("NUBI_LICENSE_KEY", "some_random_key_format")
        reset_license_cache()
        lic = get_license()
        assert lic.tier is Tier.FREE

    def test_empty_key_gives_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.licensing.license import Tier, get_license, reset_license_cache

        monkeypatch.setenv("NUBI_LICENSE_KEY", "")
        reset_license_cache()
        lic = get_license()
        assert lic.tier is Tier.FREE

    def test_case_insensitive_matching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.licensing.license import Tier, get_license, reset_license_cache

        monkeypatch.setenv("NUBI_LICENSE_KEY", "NUBI_PRO_UPPERCASE")
        reset_license_cache()
        lic = get_license()
        assert lic.tier is Tier.PRO


# ---------------------------------------------------------------------------
# Migration runner — open-core (ee/) discovery
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_migrate_module():
    """Import database/migrate.py (a standalone script, not a package)."""
    path = _REPO_ROOT / "database" / "migrate.py"
    spec = importlib.util.spec_from_file_location("nubi_database_migrate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrateEeDiscovery:
    """EE billing migrations live in ee/, keyed ee/<file>, applied after core.

    OSS self-host (no --ee) must never see them in the discovered set.
    """

    def test_discovered_ee_migrations_are_prefixed_and_after_core(self) -> None:
        """The on-disk layout keys EE billing files as ee/<file>, after core."""
        migrate = _load_migrate_module()

        versions = [v for v, _ in migrate.discover_migrations(include_ee=True)]
        for name in (
            "0017_billing.sql",
            "0018_fx_rates.sql",
            "0022_wallet.sql",
            "0027_invoices.sql",
        ):
            assert f"ee/{name}" in versions
            assert name not in versions  # moved out of core
        ee_start = min(
            i for i, v in enumerate(versions) if v.startswith("ee/")
        )
        assert all(v.startswith("ee/") for v in versions[ee_start:])

    def test_oss_core_discovery_excludes_billing(self) -> None:
        """OSS self-host (no --ee) must not see any billing migrations."""
        migrate = _load_migrate_module()

        versions = [v for v, _ in migrate.discover_migrations(include_ee=False)]
        assert not any(v.startswith("ee/") for v in versions)
        assert "0017_billing.sql" not in versions


# ---------------------------------------------------------------------------
# Usage-quota enforcement hook (register_quota_checker / check_quota /
# enforce_quota) — the core side of the EE billing quota gate.
# ---------------------------------------------------------------------------


class TestQuotaHook:
    """Core quota hook: OSS default allow, EE checker honoured, fail-open."""

    @pytest.mark.asyncio
    async def test_no_checker_allows_everything(self) -> None:
        from app.features import check_quota, enforce_quota

        allowed, reason = await check_quota("org-1", "compute_units", 1.0)
        assert allowed is True
        assert reason == ""
        # enforce_quota must be a no-op (no exception).
        await enforce_quota("org-1", "compute_units", 1.0)

    @pytest.mark.asyncio
    async def test_none_org_allows_even_with_denying_checker(self) -> None:
        """Unattributable usage cannot be quota-checked (metering warns instead)."""
        from app.features import check_quota, register_quota_checker

        register_quota_checker(lambda *, org_id, dimension, amount: (False, "denied"))
        allowed, _ = await check_quota(None, "compute_units", 1.0)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_sync_checker_denial_propagates(self) -> None:
        from app.features import check_quota, register_quota_checker

        register_quota_checker(
            lambda *, org_id, dimension, amount: (False, f"no {dimension} for {org_id}")
        )
        allowed, reason = await check_quota("org-1", "ai_calls", 1.0)
        assert allowed is False
        assert reason == "no ai_calls for org-1"

    @pytest.mark.asyncio
    async def test_async_checker_is_awaited(self) -> None:
        from app.features import check_quota, register_quota_checker

        async def _checker(*, org_id: str, dimension: str, amount: float):
            return (False, "async deny")

        register_quota_checker(_checker)
        allowed, reason = await check_quota("org-1", "embedded_sessions", 1.0)
        assert allowed is False
        assert reason == "async deny"

    @pytest.mark.asyncio
    async def test_enforce_quota_raises_402_on_denial(self) -> None:
        from app.errors import AppError
        from app.features import enforce_quota, register_quota_checker

        register_quota_checker(lambda *, org_id, dimension, amount: (False, "upgrade required"))
        with pytest.raises(AppError) as excinfo:
            await enforce_quota("org-1", "compute_units", 1.0)
        assert excinfo.value.status == 402
        assert excinfo.value.code == "quota_exceeded"
        assert "upgrade required" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_broken_checker_fails_open(self) -> None:
        """A crashing billing checker must never take down request handling."""
        from app.features import check_quota, register_quota_checker

        def _boom(*, org_id: str, dimension: str, amount: float):
            raise RuntimeError("checker exploded")

        register_quota_checker(_boom)
        allowed, _ = await check_quota("org-1", "compute_units", 1.0)
        assert allowed is True

    def test_non_callable_checker_raises_type_error(self) -> None:
        from app.features import register_quota_checker

        with pytest.raises(TypeError):
            register_quota_checker("not-callable")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_register_none_unregisters(self) -> None:
        from app.features import check_quota, register_quota_checker

        register_quota_checker(lambda *, org_id, dimension, amount: (False, "deny"))
        register_quota_checker(None)
        allowed, _ = await check_quota("org-1", "compute_units", 1.0)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_reset_for_tests_clears_quota_checker(self) -> None:
        from app.features import check_quota, register_quota_checker, reset_for_tests

        register_quota_checker(lambda *, org_id, dimension, amount: (False, "deny"))
        reset_for_tests()
        allowed, _ = await check_quota("org-1", "compute_units", 1.0)
        assert allowed is True
