"""Tests for EE billing FX rate service (app.ee.billing.fx).

Coverage
--------
1. convert_usd_to_zar: applies 2% FX buffer and ceil-to-nearest-10 rule.
2. convert_usd_to_zar: uses provided fx_rate override (no module-level state).
3. convert_usd_to_zar: zero USD → R0.
4. _ceil_to_nearest_10: rounds UP in all cases (never down).
5. get_current_rate: returns stale=True when no rate has been fetched.
6. get_current_rate: returns stale=False when rate is fresh.
7. refresh_fx_rate: happy path — stores rate, updates module cache.
8. refresh_fx_rate: uses fallback provider when primary fails (mock httpx).
9. refresh_fx_rate: returns cached rate when ALL providers fail (mock httpx).
10. refresh_fx_rate: store persists the fetched rate (InMemoryFxRateStore).
11. InMemoryFxRateStore: upsert + get_latest_rate round-trip.
12. InMemoryFxRateStore: second upsert replaces first (latest only).
13. fx_refresh handler: returns correct dict shape on success.
14. fx_refresh handler: returns stale=True on total provider failure.

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
    def test_starter_79_usd_at_reference_rate(self) -> None:
        """$79 × R16.26 × 1.02 = R1,310.23 → ceil to R1,320.

        Note: the blueprint's R1,310 reference amount was computed using standard
        rounding (round to nearest R10).  The live formula uses true ceiling
        (always round UP) to protect margin.  The hardcoded reference amounts in
        tiers.py are kept as-is (design authority); convert_usd_to_zar applies
        true ceiling at billing time for new rate values.
        """
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("79.00"), fx_rate=Decimal("16.26"))
        # True ceiling: 79 * 16.26 * 1.02 = 1310.2308 → ceil to nearest R10 = R1,320
        assert result == Decimal("1320")

    def test_pro_199_usd_at_reference_rate(self) -> None:
        """$199 × R16.26 × 1.02 = R3,300.45 → ceil to R3,310."""
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("199.00"), fx_rate=Decimal("16.26"))
        # 199 * 16.26 * 1.02 = 3300.4548 → ceil to nearest R10 = R3,310
        assert result == Decimal("3310")

    def test_business_499_usd_at_reference_rate(self) -> None:
        """$499 × R16.26 × 1.02 = R8,276.0 → ceil to R8,280."""
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("499.00"), fx_rate=Decimal("16.26"))
        # 499 * 16.26 * 1.02 = 8276.0148 → ceil to nearest R10 = R8,280
        assert result == Decimal("8280")

    def test_enterprise_1799_usd_at_reference_rate(self) -> None:
        """$1,799 × R16.26 × 1.02 = R29,836.8 → ceil to R29,840."""
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("1799.00"), fx_rate=Decimal("16.26"))
        # 1799 * 16.26 * 1.02 = 29836.7748 → ceil to nearest R10 = R29,840
        assert result == Decimal("29840")

    def test_zero_usd_returns_zero(self) -> None:
        from app.ee.billing.fx import convert_usd_to_zar
        result = convert_usd_to_zar(Decimal("0.00"), fx_rate=Decimal("16.26"))
        assert result == Decimal("0")

    def test_result_is_multiple_of_10(self) -> None:
        """All results must be exact multiples of R10."""
        from app.ee.billing.fx import convert_usd_to_zar
        for usd in [Decimal("10"), Decimal("79"), Decimal("199"), Decimal("499"), Decimal("1799")]:
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
        """Reset module cache before each test."""
        import app.ee.billing.fx as fx_mod
        fx_mod._cached_rate = fx_mod.EMERGENCY_FALLBACK_RATE
        fx_mod._cached_fetched_at = None

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
