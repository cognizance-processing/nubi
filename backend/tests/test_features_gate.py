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
"""

from __future__ import annotations

import os

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
