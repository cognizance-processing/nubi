"""Unit tests for app.ee.licensing.license (0 coverage prior to this file).

Coverage
--------
1. Tier resolution from ``NUBI_LICENSE_KEY``
   a. Missing / empty / whitespace-only key -> FREE.
   b. Recognised "nubi_pro_*" prefix -> PRO (case-insensitive).
   c. Recognised "nubi_enterprise_*" prefix -> ENTERPRISE (case-insensitive).
   d. Unrecognised / malformed / near-miss key -> FREE (fail CLOSED to the
      lowest-privilege tier, never grants a paid tier for garbage input).
   e. A key that merely *contains* a valid prefix without starting with it
      does not match (prefix check is anchored at the start).
2. License value object
   a. is_free / is_paid / is_enterprise predicates for every tier.
   b. Immutability — the dataclass is frozen; tier cannot be mutated in place.
   c. repr() never leaks raw_key (field(repr=False)).
3. Caching
   a. get_license() is cached — changing the env var without resetting the
      cache does not change the resolved tier.
   b. reset_license_cache() busts the cache so a new env value takes effect.
4. Attack-shaped inputs
   a. SQL/format-string-injection-shaped keys -> FREE, no crash.
   b. Extremely long key -> FREE, no crash (no ReDoS / buffer issues).
   c. Non-string-like whitespace/control-character keys -> FREE, no crash.

All tests are pure (no DB, no network) — license.py only reads
``os.environ["NUBI_LICENSE_KEY"]``, which is deployer-controlled at deploy
time, never attacker-reachable over the network.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _clean_license_env():
    """Ensure NUBI_LICENSE_KEY is unset before/after each test and the
    process-lifetime lru_cache is busted so tests don't leak into each other."""
    from app.ee.licensing.license import reset_license_cache

    old = os.environ.pop("NUBI_LICENSE_KEY", None)
    reset_license_cache()
    yield
    if old is None:
        os.environ.pop("NUBI_LICENSE_KEY", None)
    else:
        os.environ["NUBI_LICENSE_KEY"] = old
    reset_license_cache()


def _set_key_and_get(key: str):
    from app.ee.licensing.license import get_license, reset_license_cache

    if key:
        os.environ["NUBI_LICENSE_KEY"] = key
    else:
        os.environ.pop("NUBI_LICENSE_KEY", None)
    reset_license_cache()
    return get_license()


# ============================================================================
# 1. Tier resolution
# ============================================================================


class TestTierResolution:
    def test_missing_key_is_free(self) -> None:
        from app.ee.licensing.license import Tier

        lic = _set_key_and_get("")
        assert lic.tier is Tier.FREE
        assert lic.raw_key == ""

    def test_whitespace_only_key_is_free(self) -> None:
        from app.ee.licensing.license import Tier

        lic = _set_key_and_get("   \t\n  ")
        assert lic.tier is Tier.FREE

    def test_pro_prefix_resolves_to_pro(self) -> None:
        from app.ee.licensing.license import Tier

        lic = _set_key_and_get("nubi_pro_abc123")
        assert lic.tier is Tier.PRO

    def test_enterprise_prefix_resolves_to_enterprise(self) -> None:
        from app.ee.licensing.license import Tier

        lic = _set_key_and_get("nubi_enterprise_abc123")
        assert lic.tier is Tier.ENTERPRISE

    def test_prefix_match_is_case_insensitive(self) -> None:
        from app.ee.licensing.license import Tier

        assert _set_key_and_get("NUBI_PRO_ABC123").tier is Tier.PRO
        assert _set_key_and_get("Nubi_Enterprise_Xyz").tier is Tier.ENTERPRISE

    @pytest.mark.parametrize(
        "bogus_key",
        [
            "nubi_free_abc",
            "nubi_pr0_abc",
            "nubipro_abc",
            "pro_nubi_abc",
            "nubi-pro-abc",
            "random-garbage-key",
            "nubi_pro",  # missing trailing underscore/content
            "xnubi_pro_abc",  # prefix not anchored at start
            " nubi_pro_abc",  # leading space defeats naive prefix match... but stripped first
        ],
    )
    def test_unrecognised_key_fails_closed_to_free(self, bogus_key: str) -> None:
        """Any key that doesn't exactly match a known prefix must resolve to
        FREE — never silently grant a paid tier for attacker-guessable or
        malformed input."""
        from app.ee.licensing.license import Tier

        lic = _set_key_and_get(bogus_key)
        # " nubi_pro_abc" strips to "nubi_pro_abc" which DOES match — carve
        # that one case out explicitly since .strip() is intentional behaviour.
        if bogus_key.strip().lower().startswith("nubi_pro_"):
            assert lic.tier is Tier.PRO
        else:
            assert lic.tier is Tier.FREE

    def test_leading_trailing_whitespace_is_stripped_before_matching(self) -> None:
        from app.ee.licensing.license import Tier

        lic = _set_key_and_get("  nubi_pro_abc123  ")
        assert lic.tier is Tier.PRO
        # raw_key preserves the original (unstripped) value passed via env.
        assert lic.raw_key == "  nubi_pro_abc123  "

    def test_substring_prefix_not_anchored_is_rejected(self) -> None:
        """A key that merely *contains* the prefix (not at position 0) must
        not match — otherwise e.g. "myservice=nubi_enterprise_x" would forge
        an ENTERPRISE tier."""
        from app.ee.licensing.license import Tier

        lic = _set_key_and_get("something_nubi_enterprise_hack")
        assert lic.tier is Tier.FREE


# ============================================================================
# 2. License value object
# ============================================================================


class TestLicenseValueObject:
    def test_is_free_true_only_for_free(self) -> None:
        lic = _set_key_and_get("")
        assert lic.is_free is True
        assert lic.is_paid is False
        assert lic.is_enterprise is False

    def test_is_paid_true_for_pro(self) -> None:
        lic = _set_key_and_get("nubi_pro_x")
        assert lic.is_free is False
        assert lic.is_paid is True
        assert lic.is_enterprise is False

    def test_is_paid_and_is_enterprise_true_for_enterprise(self) -> None:
        lic = _set_key_and_get("nubi_enterprise_x")
        assert lic.is_free is False
        assert lic.is_paid is True
        assert lic.is_enterprise is True

    def test_license_is_frozen_dataclass(self) -> None:
        """A License object cannot be mutated after construction — a caller
        that got a FREE license object cannot flip it to ENTERPRISE."""
        import dataclasses

        from app.ee.licensing.license import Tier

        lic = _set_key_and_get("")
        with pytest.raises(dataclasses.FrozenInstanceError):
            lic.tier = Tier.ENTERPRISE  # type: ignore[misc]

    def test_repr_does_not_leak_raw_key(self) -> None:
        """raw_key is declared with field(repr=False) — logging a License
        object (e.g. in an exception traceback) must never leak the raw
        license key."""
        lic = _set_key_and_get("nubi_enterprise_super-secret-key-12345")
        r = repr(lic)
        assert "super-secret-key-12345" not in r
        assert "enterprise" in r  # tier is still shown


# ============================================================================
# 3. Caching behaviour
# ============================================================================


class TestCaching:
    def test_get_license_is_cached_across_calls(self) -> None:
        from app.ee.licensing.license import get_license

        os.environ["NUBI_LICENSE_KEY"] = "nubi_pro_abc"
        from app.ee.licensing.license import reset_license_cache

        reset_license_cache()
        first = get_license()
        # Mutate the env var WITHOUT resetting the cache.
        os.environ["NUBI_LICENSE_KEY"] = "nubi_enterprise_abc"
        second = get_license()
        assert first is second  # same cached object
        assert second.tier.value == "pro"  # stale cache — did NOT pick up the change

    def test_reset_license_cache_busts_cache(self) -> None:
        from app.ee.licensing.license import get_license, reset_license_cache

        os.environ["NUBI_LICENSE_KEY"] = "nubi_pro_abc"
        reset_license_cache()
        get_license()

        os.environ["NUBI_LICENSE_KEY"] = "nubi_enterprise_abc"
        reset_license_cache()
        refreshed = get_license()
        assert refreshed.tier.value == "enterprise"


# ============================================================================
# 4. Attack-shaped inputs — must never crash, must always fail closed
# ============================================================================


class TestAttackShapedInputs:
    @pytest.mark.parametrize(
        "hostile_key",
        [
            "'; DROP TABLE users; --",
            "{{7*7}}",
            "%s%s%s%s%s",
            "../../etc/passwd",
            "nubi_pro_" + "x" * 100_000,  # very long key
            "\n\n\nnubi_enterprise_x\n\n\n",
        ],
    )
    def test_hostile_key_never_crashes_and_fails_closed_unless_stripped_prefix_matches(
        self, hostile_key: str
    ) -> None:
        from app.ee.licensing.license import Tier

        lic = _set_key_and_get(hostile_key)
        # Only a key whose *stripped* form starts with a known prefix may
        # resolve to a paid tier; everything else must be FREE, and none of
        # these inputs should raise.
        stripped = hostile_key.strip().lower()
        if stripped.startswith("nubi_pro_"):
            assert lic.tier is Tier.PRO
        elif stripped.startswith("nubi_enterprise_"):
            assert lic.tier is Tier.ENTERPRISE
        else:
            assert lic.tier is Tier.FREE

    def test_null_byte_prefixed_key_does_not_forge_enterprise(self) -> None:
        """os.environ itself rejects embedded NUL bytes (OS-level C-string
        constraint), so this shape can never reach us via a real env var —
        but exercise the pure resolver directly for defence in depth."""
        from app.ee.licensing.license import Tier, _resolve_tier

        # "\x00nubi_enterprise_x".strip() does NOT strip the embedded NUL, and
        # lower()/startswith("nubi_enterprise_") is False because of the
        # leading NUL byte -> must resolve to FREE.
        assert _resolve_tier("\x00nubi_enterprise_x") is Tier.FREE
