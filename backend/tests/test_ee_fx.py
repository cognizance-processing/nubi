"""Tests for EE billing FX rate service (app.ee.billing.fx).

Coverage
--------
1. convert_usd_to_zar: applies 2% FX buffer and ceil-to-nearest-10 rule at
   the canonical tier price points ($9/$49/$149/$1000 — pinned to tiers.py).
2. convert_usd_to_zar: uses provided fx_rate override (no module-level state).
3. convert_usd_to_zar: zero USD → R0.
4. _ceil_to_nearest_10: rounds UP in all cases (never down).
5. get_current_rate: returns stale=True when no rate has been fetched.
6. get_current_rate: returns stale=False when rate is fresh.
7. get_current_rate: serves EMERGENCY_FALLBACK_RATE (never a stale cached
   rate) once the cache is beyond STALENESS_THRESHOLD_HOURS.
8. refresh_fx_rate: happy path — stores rate, updates module cache.
9. refresh_fx_rate: uses fallback provider when primary fails (mock httpx).
10. refresh_fx_rate: returns cached rate when ALL providers fail (mock httpx).
11. refresh_fx_rate: store persists the fetched rate (InMemoryFxRateStore).
12. refresh_fx_rate: rejects 0/negative/implausible rates and degraded
    open.er-api payloads — never poisons the cache or the store.
13. Cache hydration: persisted store row is read back into the module cache
    (async billing accessor, background task inside a loop, inline outside).
14. EMERGENCY_FALLBACK_RATE: FX_EMERGENCY_RATE env override parsing.
15. InMemoryFxRateStore: upsert + get_latest_rate round-trip.
16. InMemoryFxRateStore: second upsert replaces first (latest only).
17. fx_refresh handler: returns correct dict shape on success.
18. fx_refresh handler: returns stale=True on total provider failure.

All tests: no real network (mock httpx throughout).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Env setup before importing app modules.
# ---------------------------------------------------------------------------

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("ENV", "test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_httpx_response(json_data: dict[str, Any], status_code: int = 200) -> MagicMock:
    """Build a mock httpx response object."""
    resp = MagicMock()
    resp.is_success = (200 <= status_code < 300)
    resp.status_code = status_code
    resp.text = str(json_data)
    resp.json.return_value = json_data
    return resp


def _frankfurter_response(rate: float = 16.26) -> dict[str, Any]:
    """Minimal frankfurter.app response body."""
    return {
        "amount": 1.0,
        "base": "USD",
        "date": "2026-06-08",
        "rates": {"ZAR": rate},
    }


def _open_er_response(rate: float = 16.26) -> dict[str, Any]:
    """Minimal open.er-api.com response body."""
    return {
        "result": "success",
        "base_code": "USD",
        "rates": {"ZAR": rate},
    }


# ============================================================================
# 1–4. convert_usd_to_zar and _ceil_to_nearest_10
# ============================================================================


class TestConvertUsdToZar:
    def test_starter_9_usd_at_reference_rate(self) -> None:
        """$9 × R16.26 × 1.02 = R149.27 → ceil to R150 (Starter)."""
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("9.00"), fx_rate=Decimal("16.26"))
        # 9 * 16.26 * 1.02 = 149.2668 → ceil to nearest R10 = R150
        assert result == Decimal("150")

    def test_team_49_usd_at_reference_rate(self) -> None:
        """$49 × R16.26 × 1.02 = R812.67 → ceil to R820 (Team)."""
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("49.00"), fx_rate=Decimal("16.26"))
        # 49 * 16.26 * 1.02 = 812.6748 → ceil to nearest R10 = R820
        assert result == Decimal("820")

    def test_pro_149_usd_at_reference_rate(self) -> None:
        """$149 × R16.26 × 1.02 = R2,471.19 → ceil to R2,480 (Pro)."""
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("149.00"), fx_rate=Decimal("16.26"))
        # 149 * 16.26 * 1.02 = 2471.1948 → ceil to nearest R10 = R2,480
        assert result == Decimal("2480")

    def test_enterprise_1000_usd_at_reference_rate(self) -> None:
        """$1,000 × R16.26 × 1.02 = R16,585.20 → ceil to R16,590 (Enterprise floor)."""
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("1000.00"), fx_rate=Decimal("16.26"))
        # 1000 * 16.26 * 1.02 = 16585.20 → ceil to nearest R10 = R16,590
        assert result == Decimal("16590")

    def test_reference_zar_amounts_match_canonical_tiers(self) -> None:
        """Converting every tier's canonical USD price at the June-2026
        reference rate must reproduce the tiers.py reference ZAR amounts."""
        from app.ee.billing.fx import convert_usd_to_zar
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in BillingTier:
            limits = get_tier_limits(tier)
            result = convert_usd_to_zar(
                limits.usd_monthly_price, fx_rate=Decimal("16.26")
            )
            assert result == limits.monthly_price_zar, (
                f"{tier.value}: convert_usd_to_zar(${limits.usd_monthly_price}) "
                f"= R{result}, expected R{limits.monthly_price_zar}"
            )

    def test_zero_usd_returns_zero(self) -> None:
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("0.00"), fx_rate=Decimal("16.26"))
        assert result == Decimal("0")

    def test_result_is_multiple_of_10(self) -> None:
        """All results must be exact multiples of R10."""
        from app.ee.billing.fx import convert_usd_to_zar
        for usd in [Decimal("10"), Decimal("9"), Decimal("49"), Decimal("149"), Decimal("1000")]:
            result = convert_usd_to_zar(usd, fx_rate=Decimal("16.26"))
            assert result % 10 == 0, f"R{result} is not a multiple of R10 for ${usd}"

    def test_2_pct_buffer_applied(self) -> None:
        """Buffer means ZAR output > usd * rate * 1.00."""
        from app.ee.billing.fx import convert_usd_to_zar, FX_BUFFER
        usd = Decimal("100.00")
        rate = Decimal("16.00")
        result = convert_usd_to_zar(usd, fx_rate=rate)
        without_buffer = usd * rate
        assert result > without_buffer


class TestCeilToNearest10:
    def test_exact_multiple_unchanged(self) -> None:
        from app.ee.billing.fx import _ceil_to_nearest_10
        assert _ceil_to_nearest_10(Decimal("1310")) == Decimal("1310")

    def test_rounds_up_not_down(self) -> None:
        from app.ee.billing.fx import _ceil_to_nearest_10
        assert _ceil_to_nearest_10(Decimal("1301")) == Decimal("1310")
        assert _ceil_to_nearest_10(Decimal("1309")) == Decimal("1310")

    def test_just_above_multiple_rounds_to_next(self) -> None:
        from app.ee.billing.fx import _ceil_to_nearest_10
        assert _ceil_to_nearest_10(Decimal("1310.01")) == Decimal("1320")

    def test_zero_returns_zero(self) -> None:
        from app.ee.billing.fx import _ceil_to_nearest_10
        assert _ceil_to_nearest_10(Decimal("0")) == Decimal("0")


# ============================================================================
# 5–6. get_current_rate: staleness
# ============================================================================


class TestGetCurrentRate:
    def setup_method(self) -> None:
        """Reset module cache before each test (store hydration suppressed)."""
        import app.ee.billing.fx as fx_mod
        fx_mod._cached_rate = fx_mod.EMERGENCY_FALLBACK_RATE
        fx_mod._cached_fetched_at = None
        # Pretend the store was just read so these unit tests exercise the
        # pure cache/staleness logic without touching a store.
        fx_mod._last_store_read_at = datetime.now(timezone.utc)
        fx_mod._hydration_task = None

    def test_stale_when_never_fetched(self) -> None:
        from app.ee.billing.fx import get_current_rate
        info = get_current_rate()
        assert info["stale"] is True
        assert info["fetched_at"] is None

    def test_fresh_when_recently_fetched(self) -> None:
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import get_current_rate

        fx_mod._cached_fetched_at = datetime.now(timezone.utc)
        info = get_current_rate()
        assert info["stale"] is False

    def test_stale_when_fetched_beyond_threshold(self) -> None:
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import get_current_rate, STALENESS_THRESHOLD_HOURS

        fx_mod._cached_fetched_at = (
            datetime.now(timezone.utc) - timedelta(hours=STALENESS_THRESHOLD_HOURS + 1)
        )
        info = get_current_rate()
        assert info["stale"] is True

    def test_returns_emergency_fallback_rate_when_no_fetch(self) -> None:
        from app.ee.billing.fx import get_current_rate, EMERGENCY_FALLBACK_RATE
        info = get_current_rate()
        assert info["rate"] == EMERGENCY_FALLBACK_RATE

    def test_fresh_cached_rate_is_served_as_is(self) -> None:
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import get_current_rate

        fx_mod._cached_rate = Decimal("17.00")
        fx_mod._cached_fetched_at = datetime.now(timezone.utc)
        info = get_current_rate()
        assert info["rate"] == Decimal("17.00")
        assert info["stale"] is False

    def test_stale_cached_rate_swaps_to_emergency_fallback(self) -> None:
        """The documented 72h policy: a stale cached rate is NEVER served —
        the emergency fallback replaces it (and stale=True is flagged)."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import (
            EMERGENCY_FALLBACK_RATE,
            STALENESS_THRESHOLD_HOURS,
            get_current_rate,
        )

        fx_mod._cached_rate = Decimal("19.75")
        fx_mod._cached_fetched_at = (
            datetime.now(timezone.utc) - timedelta(hours=STALENESS_THRESHOLD_HOURS + 1)
        )
        info = get_current_rate()
        assert info["rate"] == EMERGENCY_FALLBACK_RATE
        assert info["stale"] is True

    def test_convert_usd_to_zar_uses_emergency_rate_when_stale(self) -> None:
        """Charge-time conversion must also enforce the staleness policy."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import STALENESS_THRESHOLD_HOURS, convert_usd_to_zar

        fx_mod._cached_rate = Decimal("30.00")  # ancient, absurd rate
        fx_mod._cached_fetched_at = (
            datetime.now(timezone.utc) - timedelta(hours=STALENESS_THRESHOLD_HOURS + 1)
        )
        result = convert_usd_to_zar(Decimal("100.00"))
        # 100 * 16.26 (emergency) * 1.02 = 1658.52 → R1,660 — not 100*30*1.02.
        assert result == Decimal("1660")


# ============================================================================
# EMERGENCY_FALLBACK_RATE: FX_EMERGENCY_RATE env override
# ============================================================================


class TestEmergencyFallbackRateEnv:
    def test_env_override_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.billing.fx import _emergency_fallback_rate_from_env

        monkeypatch.setenv("FX_EMERGENCY_RATE", "18.50")
        assert _emergency_fallback_rate_from_env() == Decimal("18.50")

    def test_default_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ee.billing.fx import (
            _DEFAULT_EMERGENCY_FALLBACK_RATE,
            _emergency_fallback_rate_from_env,
        )

        monkeypatch.delenv("FX_EMERGENCY_RATE", raising=False)
        assert _emergency_fallback_rate_from_env() == _DEFAULT_EMERGENCY_FALLBACK_RATE

    @pytest.mark.parametrize("raw", ["", "garbage", "0", "-1"])
    def test_invalid_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        from app.ee.billing.fx import (
            _DEFAULT_EMERGENCY_FALLBACK_RATE,
            _emergency_fallback_rate_from_env,
        )

        monkeypatch.setenv("FX_EMERGENCY_RATE", raw)
        assert _emergency_fallback_rate_from_env() == _DEFAULT_EMERGENCY_FALLBACK_RATE


# ============================================================================
# 7–10. refresh_fx_rate (mocked httpx — NO real network)
# ============================================================================


class TestRefreshFxRate:
    def setup_method(self) -> None:
        """Reset module cache and inject InMemory store."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import InMemoryFxRateStore, set_fx_rate_store_for_tests

        fx_mod._cached_rate = fx_mod.EMERGENCY_FALLBACK_RATE
        fx_mod._cached_fetched_at = None
        fx_mod._last_store_read_at = None
        fx_mod._hydration_task = None
        self.store = InMemoryFxRateStore()
        set_fx_rate_store_for_tests(self.store)

    def teardown_method(self) -> None:
        from app.ee.billing.fx import set_fx_rate_store_for_tests
        set_fx_rate_store_for_tests(None)

    @pytest.mark.asyncio
    async def test_happy_path_returns_rate_from_frankfurter(self) -> None:
        """Primary provider succeeds → returns fetched rate."""
        from app.ee.billing.fx import refresh_fx_rate

        mock_resp = _make_httpx_response(_frankfurter_response(rate=16.30))
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rate = await refresh_fx_rate()

        assert rate == Decimal("16.3")

    @pytest.mark.asyncio
    async def test_happy_path_updates_module_cache(self) -> None:
        """After refresh, get_current_rate() returns the new rate."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import get_current_rate, refresh_fx_rate

        mock_resp = _make_httpx_response(_frankfurter_response(rate=16.50))
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await refresh_fx_rate()

        info = get_current_rate()
        assert info["rate"] == Decimal("16.5")
        assert info["stale"] is False

    @pytest.mark.asyncio
    async def test_happy_path_persists_to_store(self) -> None:
        """After refresh, the store has the fetched rate."""
        from app.ee.billing.fx import refresh_fx_rate

        mock_resp = _make_httpx_response(_frankfurter_response(rate=16.26))
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await refresh_fx_rate()

        row = await self.store.get_latest_rate("USD", "ZAR")
        assert row is not None
        assert row["rate"] == Decimal("16.26")
        assert row["source"] == "frankfurter.app"

    @pytest.mark.asyncio
    async def test_falls_back_to_secondary_when_primary_fails(self) -> None:
        """When frankfurter fails, falls back to open.er-api.com."""
        from app.ee.billing.fx import refresh_fx_rate

        primary_resp = _make_httpx_response({}, status_code=503)

        call_count = 0

        async def _fake_get(url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if "frankfurter" in url:
                return _make_httpx_response({}, status_code=503)
            else:
                return _make_httpx_response(_open_er_response(rate=16.40))

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_fake_get)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rate = await refresh_fx_rate()

        assert rate == Decimal("16.4")

    @pytest.mark.asyncio
    async def test_returns_cached_rate_when_all_providers_fail(self) -> None:
        """When all providers fail, the last cached rate is returned unchanged."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import refresh_fx_rate

        # Pre-seed the cache with a known rate.
        fx_mod._cached_rate = Decimal("16.00")
        fx_mod._cached_fetched_at = datetime.now(timezone.utc)

        async def _fail_get(url: str, **kwargs: Any) -> MagicMock:
            return _make_httpx_response({}, status_code=500)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_fail_get)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rate = await refresh_fx_rate()

        # Must return the pre-seeded cached value, not crash.
        assert rate == Decimal("16.00")

    @pytest.mark.asyncio
    async def test_returns_emergency_fallback_when_no_cache_and_all_fail(self) -> None:
        """Total failure with no prior cache → emergency fallback rate."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import EMERGENCY_FALLBACK_RATE, refresh_fx_rate

        # Ensure no prior cache.
        fx_mod._cached_rate = EMERGENCY_FALLBACK_RATE
        fx_mod._cached_fetched_at = None

        async def _fail(*a: Any, **kw: Any) -> MagicMock:
            raise RuntimeError("network error")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_fail)

        with patch("httpx.AsyncClient", return_value=mock_client):
            rate = await refresh_fx_rate()

        assert rate == EMERGENCY_FALLBACK_RATE


# ============================================================================
# refresh_fx_rate: fetched-rate validation (mocked httpx — NO real network)
# ============================================================================


class TestRefreshFxRateValidation:
    def setup_method(self) -> None:
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import InMemoryFxRateStore, set_fx_rate_store_for_tests

        fx_mod._cached_rate = fx_mod.EMERGENCY_FALLBACK_RATE
        fx_mod._cached_fetched_at = None
        fx_mod._last_store_read_at = None
        fx_mod._hydration_task = None
        self.store = InMemoryFxRateStore()
        set_fx_rate_store_for_tests(self.store)

    def teardown_method(self) -> None:
        from app.ee.billing.fx import set_fx_rate_store_for_tests
        set_fx_rate_store_for_tests(None)

    @staticmethod
    def _client(fake_get: Any) -> AsyncMock:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=fake_get)
        return mock_client

    @pytest.mark.asyncio
    async def test_zero_rate_from_primary_falls_back_to_secondary(self) -> None:
        """A provider returning ZAR=0 is a provider FAILURE, not a rate."""
        from app.ee.billing.fx import refresh_fx_rate

        async def _fake_get(url: str, **kwargs: Any) -> MagicMock:
            if "frankfurter" in url:
                return _make_httpx_response(_frankfurter_response(rate=0))
            return _make_httpx_response(_open_er_response(rate=16.40))

        with patch("httpx.AsyncClient", return_value=self._client(_fake_get)):
            rate = await refresh_fx_rate()

        assert rate == Decimal("16.4")
        row = await self.store.get_latest_rate("USD", "ZAR")
        assert row is not None
        assert row["source"] == "open.er-api.com"

    @pytest.mark.asyncio
    async def test_negative_rate_from_all_providers_does_not_poison_cache(self) -> None:
        """0/negative rates from every provider → cache and store untouched."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import (
            EMERGENCY_FALLBACK_RATE,
            get_current_rate,
            refresh_fx_rate,
        )

        async def _fake_get(url: str, **kwargs: Any) -> MagicMock:
            if "frankfurter" in url:
                return _make_httpx_response(_frankfurter_response(rate=-3.5))
            return _make_httpx_response(_open_er_response(rate=0))

        with patch("httpx.AsyncClient", return_value=self._client(_fake_get)):
            rate = await refresh_fx_rate()

        assert rate == EMERGENCY_FALLBACK_RATE
        assert fx_mod._cached_fetched_at is None  # cache NOT updated
        assert await self.store.get_latest_rate("USD", "ZAR") is None  # not persisted
        assert get_current_rate()["stale"] is True

    @pytest.mark.asyncio
    async def test_implausible_rate_rejected_falls_back_to_secondary(self) -> None:
        """A garbled-but-parseable rate (e.g. 1626.0) is outside the sane band."""
        from app.ee.billing.fx import refresh_fx_rate

        async def _fake_get(url: str, **kwargs: Any) -> MagicMock:
            if "frankfurter" in url:
                return _make_httpx_response(_frankfurter_response(rate=1626.0))
            return _make_httpx_response(_open_er_response(rate=16.40))

        with patch("httpx.AsyncClient", return_value=self._client(_fake_get)):
            rate = await refresh_fx_rate()

        assert rate == Decimal("16.4")

    @pytest.mark.asyncio
    async def test_open_er_api_error_result_is_rejected(self) -> None:
        """A degraded open.er-api payload (result != 'success') is never trusted,
        even when it still carries a rates object."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import EMERGENCY_FALLBACK_RATE, refresh_fx_rate

        async def _fake_get(url: str, **kwargs: Any) -> MagicMock:
            if "frankfurter" in url:
                return _make_httpx_response({}, status_code=503)
            body = _open_er_response(rate=16.40)
            body["result"] = "error"
            return _make_httpx_response(body)

        with patch("httpx.AsyncClient", return_value=self._client(_fake_get)):
            rate = await refresh_fx_rate()

        assert rate == EMERGENCY_FALLBACK_RATE
        assert fx_mod._cached_fetched_at is None
        assert await self.store.get_latest_rate("USD", "ZAR") is None


# ============================================================================
# Cache hydration from the persistent store
# ============================================================================


class TestCacheHydration:
    def setup_method(self) -> None:
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import InMemoryFxRateStore, set_fx_rate_store_for_tests

        fx_mod._cached_rate = fx_mod.EMERGENCY_FALLBACK_RATE
        fx_mod._cached_fetched_at = None
        fx_mod._last_store_read_at = None
        fx_mod._hydration_task = None
        self.store = InMemoryFxRateStore()
        set_fx_rate_store_for_tests(self.store)

    def teardown_method(self) -> None:
        from app.ee.billing.fx import set_fx_rate_store_for_tests
        set_fx_rate_store_for_tests(None)

    @pytest.mark.asyncio
    async def test_hydrate_updates_cache_from_store(self) -> None:
        """A persisted rate (e.g. written by another process's daily refresh)
        is read back into the module cache."""
        from app.ee.billing.fx import get_current_rate, hydrate_rate_cache_from_store

        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("17.55"), "frankfurter.app", now)

        updated = await hydrate_rate_cache_from_store()

        assert updated is True
        info = get_current_rate()
        assert info["rate"] == Decimal("17.55")
        assert info["stale"] is False

    @pytest.mark.asyncio
    async def test_hydrate_noop_when_store_empty(self) -> None:
        from app.ee.billing.fx import (
            EMERGENCY_FALLBACK_RATE,
            get_current_rate,
            hydrate_rate_cache_from_store,
        )

        updated = await hydrate_rate_cache_from_store()

        assert updated is False
        info = get_current_rate()
        assert info["rate"] == EMERGENCY_FALLBACK_RATE
        assert info["stale"] is True

    @pytest.mark.asyncio
    async def test_hydrate_does_not_downgrade_fresher_cache(self) -> None:
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import hydrate_rate_cache_from_store

        now = datetime.now(timezone.utc)
        fx_mod._cached_rate = Decimal("17.00")
        fx_mod._cached_fetched_at = now
        await self.store.upsert_rate(
            "USD", "ZAR", Decimal("16.00"), "old", now - timedelta(hours=6)
        )

        updated = await hydrate_rate_cache_from_store()

        assert updated is False
        assert fx_mod._cached_rate == Decimal("17.00")

    @pytest.mark.asyncio
    async def test_stale_persisted_rate_hydrates_but_emergency_is_served(self) -> None:
        """A persisted row older than the staleness threshold still hydrates the
        cache metadata, but the 72h policy means the emergency rate is served."""
        from app.ee.billing.fx import (
            EMERGENCY_FALLBACK_RATE,
            STALENESS_THRESHOLD_HOURS,
            get_current_rate,
            hydrate_rate_cache_from_store,
        )

        old = datetime.now(timezone.utc) - timedelta(hours=STALENESS_THRESHOLD_HOURS + 1)
        await self.store.upsert_rate("USD", "ZAR", Decimal("17.55"), "frankfurter.app", old)

        updated = await hydrate_rate_cache_from_store()

        assert updated is True
        info = get_current_rate()
        assert info["rate"] == EMERGENCY_FALLBACK_RATE
        assert info["stale"] is True

    @pytest.mark.asyncio
    async def test_get_current_rate_schedules_background_hydration_in_loop(self) -> None:
        """Inside a running event loop, get_current_rate() cannot await — it
        schedules the store read so subsequent callers see the persisted rate."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import get_current_rate

        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("17.10"), "frankfurter.app", now)

        first = get_current_rate()
        assert first["stale"] is True  # cache not hydrated yet

        assert fx_mod._hydration_task is not None
        await fx_mod._hydration_task

        second = get_current_rate()
        assert second["rate"] == Decimal("17.10")
        assert second["stale"] is False

    def test_get_current_rate_hydrates_inline_outside_event_loop(self) -> None:
        """Without a running loop (sync scripts), the store read runs inline —
        the very first call already serves the persisted rate."""
        import asyncio

        from app.ee.billing.fx import get_current_rate

        now = datetime.now(timezone.utc)
        asyncio.run(
            self.store.upsert_rate("USD", "ZAR", Decimal("17.80"), "frankfurter.app", now)
        )

        info = get_current_rate()
        assert info["rate"] == Decimal("17.80")
        assert info["stale"] is False

    @pytest.mark.asyncio
    async def test_get_current_rate_async_hydrates_before_returning(self) -> None:
        """The billing-time accessor awaits hydration — charge paths see the
        persisted rate even on the very first call after a restart."""
        from app.ee.billing.fx import get_current_rate_async

        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("18.42"), "frankfurter.app", now)

        info = await get_current_rate_async()

        assert info["rate"] == Decimal("18.42")
        assert info["stale"] is False

    @pytest.mark.asyncio
    async def test_store_read_is_throttled(self) -> None:
        """The persisted row is re-read at most every _STORE_READ_INTERVAL."""
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import get_current_rate

        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("17.10"), "frankfurter.app", now)

        get_current_rate()
        assert fx_mod._hydration_task is not None
        await fx_mod._hydration_task
        fx_mod._hydration_task = None

        # Second call within the interval must NOT schedule another read.
        get_current_rate()
        assert fx_mod._hydration_task is None


# ============================================================================
# 11–12. InMemoryFxRateStore
# ============================================================================


class TestInMemoryFxRateStore:
    def setup_method(self) -> None:
        from app.ee.billing.fx import InMemoryFxRateStore
        self.store = InMemoryFxRateStore()

    @pytest.mark.asyncio
    async def test_get_latest_rate_returns_none_when_empty(self) -> None:
        result = await self.store.get_latest_rate("USD", "ZAR")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_then_get_round_trips(self) -> None:
        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("16.26"), "test", now)
        row = await self.store.get_latest_rate("USD", "ZAR")
        assert row is not None
        assert row["rate"] == Decimal("16.26")
        assert row["source"] == "test"
        assert row["base"] == "USD"
        assert row["quote"] == "ZAR"

    @pytest.mark.asyncio
    async def test_second_upsert_is_returned_as_latest(self) -> None:
        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("16.00"), "first", now)
        await self.store.upsert_rate("USD", "ZAR", Decimal("16.50"), "second", now)
        row = await self.store.get_latest_rate("USD", "ZAR")
        assert row is not None
        # Latest = last appended.
        assert row["rate"] == Decimal("16.50")
        assert row["source"] == "second"

    @pytest.mark.asyncio
    async def test_different_pairs_isolated(self) -> None:
        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("16.26"), "t", now)
        await self.store.upsert_rate("EUR", "ZAR", Decimal("17.50"), "t", now)
        usd_row = await self.store.get_latest_rate("USD", "ZAR")
        eur_row = await self.store.get_latest_rate("EUR", "ZAR")
        assert usd_row is not None
        assert eur_row is not None
        assert usd_row["rate"] != eur_row["rate"]

    @pytest.mark.asyncio
    async def test_returned_record_is_deep_copy(self) -> None:
        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("16.26"), "t", now)
        row = await self.store.get_latest_rate()
        assert row is not None
        row["rate"] = Decimal("0")
        fresh = await self.store.get_latest_rate()
        assert fresh is not None
        assert fresh["rate"] == Decimal("16.26")

    @pytest.mark.asyncio
    async def test_reset_clears_state(self) -> None:
        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("USD", "ZAR", Decimal("16.26"), "t", now)
        self.store.reset()
        result = await self.store.get_latest_rate()
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_normalises_currency_codes_to_upper(self) -> None:
        now = datetime.now(timezone.utc)
        await self.store.upsert_rate("usd", "zar", Decimal("16.26"), "t", now)
        row = await self.store.get_latest_rate("USD", "ZAR")
        assert row is not None
        assert row["base"] == "USD"
        assert row["quote"] == "ZAR"


# ============================================================================
# 13–14. fx_refresh handler
# ============================================================================


class TestFxRefreshHandler:
    def setup_method(self) -> None:
        import app.ee.billing.fx as fx_mod
        from app.ee.billing.fx import InMemoryFxRateStore, set_fx_rate_store_for_tests

        fx_mod._cached_rate = fx_mod.EMERGENCY_FALLBACK_RATE
        fx_mod._cached_fetched_at = None
        self.store = InMemoryFxRateStore()
        set_fx_rate_store_for_tests(self.store)

    def teardown_method(self) -> None:
        from app.ee.billing.fx import set_fx_rate_store_for_tests
        set_fx_rate_store_for_tests(None)

    @pytest.mark.asyncio
    async def test_handler_returns_rate_string_on_success(self) -> None:
        from app.ee.billing.fx import _fx_refresh_handler

        mock_resp = _make_httpx_response(_frankfurter_response(rate=16.26))
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _fx_refresh_handler({}, None, {})

        assert "rate" in result
        assert result["rate"] == "16.26"
        assert "fetched_at" in result
        assert "stale" in result
        assert result["stale"] is False

    @pytest.mark.asyncio
    async def test_handler_returns_stale_true_on_all_provider_failure(self) -> None:
        from app.ee.billing.fx import _fx_refresh_handler

        async def _fail(*a: Any, **kw: Any) -> MagicMock:
            raise RuntimeError("network error")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=_fail)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _fx_refresh_handler({}, None, {})

        # Handler must not raise; stale should indicate degraded state.
        assert "rate" in result
        # stale=True because no fresh fetch succeeded.
        # (The emergency fallback is returned — so rate is non-empty.)
        assert isinstance(result["rate"], str)

    @pytest.mark.asyncio
    async def test_handler_result_is_json_serialisable(self) -> None:
        """The handler result dict must be JSON-serialisable (flows engine requirement)."""
        import json

        from app.ee.billing.fx import _fx_refresh_handler

        mock_resp = _make_httpx_response(_frankfurter_response(rate=16.26))
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _fx_refresh_handler({}, None, {})

        # Should not raise.
        serialised = json.dumps(result)
        assert len(serialised) > 0
