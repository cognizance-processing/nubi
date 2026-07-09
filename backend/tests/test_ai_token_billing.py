"""Tests for LiteLLM multi-provider token-passthrough billing (M-LLM-BILLING).

Coverage
--------
1. Pure billing math — ``compute_token_charge`` (usd_cost × fx_rate × markup)
   and ``chargeable_fraction`` (free-allowance proration), with fixed inputs
   so the exact ZAR/USD-cent output is asserted.
2. ``meter_and_charge`` integration — free-allowance-then-wallet-overage: only
   the portion of a call's tokens beyond the tier's free monthly allowance is
   charged, at cost + markup.
3. BYO key: a BYO-tagged call never charges the wallet; a FREE-tier org
   cannot set/use a BYO key (``AppError("ai_key_requires_paid_tier", 402)``).
4. ``resolve_provider_for_org`` swaps in an org's BYO key on a paid tier and
   falls back to the operator default otherwise.
5. ``GET /ai/providers`` — provider/model metadata + BYO status shape.

Network safety
---------------
No real network calls and no ``litellm`` completion is ever invoked — all
tests exercise the billing math and store logic directly, or hit
``GET /ai/providers`` (a metadata-only endpoint with no provider.complete()
call).
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_PRO_LICENSE_KEY = "nubi_pro_test_key"


# ---------------------------------------------------------------------------
# 1. Pure billing math
# ---------------------------------------------------------------------------


class TestComputeTokenCharge:
    """usd_marked_up = usd_cost * (1 + markup_pct/100); zar_charge = usd_marked_up * fx_rate."""

    def test_default_markup_and_fixed_fx(self) -> None:
        from app.ee.billing.token_billing import compute_token_charge

        # $1.00 cost, 7.5% markup, R18.00/USD.
        charge = compute_token_charge(1.0, fx_rate=Decimal("18.00"), markup_pct=7.5)
        # usd_marked_up = 1.00 * 1.075 = 1.075
        assert charge.usd_marked_up == Decimal("1.075")
        # zar_charge = 1.075 * 18.00 = 19.35
        assert charge.zar_charge == Decimal("19.35")
        # usd_cents = 107.5 -> rounds to 108 (ROUND_HALF_UP)
        assert charge.usd_cents == 108

    def test_zero_markup_is_pass_through(self) -> None:
        from app.ee.billing.token_billing import compute_token_charge

        charge = compute_token_charge(2.00, fx_rate=Decimal("16.26"), markup_pct=0)
        assert charge.usd_marked_up == Decimal("2.00")
        assert charge.zar_charge == Decimal("32.52")
        assert charge.usd_cents == 200

    def test_small_cost_rounds_correctly(self) -> None:
        from app.ee.billing.token_billing import compute_token_charge

        # A typical small per-call cost: $0.0042, 7.5% markup, R18.42/USD.
        charge = compute_token_charge(Decimal("0.0042"), fx_rate=Decimal("18.42"), markup_pct=Decimal("7.5"))
        usd_marked_up = Decimal("0.0042") * Decimal("1.075")
        assert charge.usd_marked_up == usd_marked_up
        expected_zar = (usd_marked_up * Decimal("18.42")).quantize(Decimal("0.01"))
        assert charge.zar_charge == expected_zar

    def test_negative_cost_clamped_to_zero(self) -> None:
        """Defensive: a negative usd_cost (should never happen) never produces
        a negative charge — clamped to zero rather than crediting the wallet."""
        from app.ee.billing.token_billing import compute_token_charge

        charge = compute_token_charge(-5.0, fx_rate=Decimal("18.00"), markup_pct=7.5)
        assert charge.usd_cost == Decimal("0")
        assert charge.usd_cents == 0
        assert charge.zar_charge == Decimal("0.00")

    def test_configured_markup_pct_default_is_7_5(self) -> None:
        """NUBI_TOKEN_MARKUP_PCT defaults to 7.5 (see app.config.Settings)."""
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            for key in ("NUBI_TOKEN_MARKUP_PCT",):
                os.environ.pop(key, None)
            settings = get_settings()
            assert settings.NUBI_TOKEN_MARKUP_PCT == 7.5
        finally:
            get_settings.cache_clear()


class TestChargeableFraction:
    """Only the portion of a call's tokens beyond the free allowance is chargeable."""

    def test_entirely_within_allowance(self) -> None:
        from app.ee.billing.token_billing import chargeable_fraction

        frac = chargeable_fraction(total_tokens=1000, tokens_used_before=0, free_allowance_tokens=5000)
        assert frac == 0.0

    def test_straddles_the_allowance_boundary(self) -> None:
        from app.ee.billing.token_billing import chargeable_fraction

        # 4500 used before, this call adds 1000 -> only the last 500 tokens are over.
        frac = chargeable_fraction(total_tokens=1000, tokens_used_before=4500, free_allowance_tokens=5000)
        assert frac == pytest.approx(0.5)

    def test_entirely_beyond_allowance(self) -> None:
        from app.ee.billing.token_billing import chargeable_fraction

        frac = chargeable_fraction(total_tokens=1000, tokens_used_before=5000, free_allowance_tokens=5000)
        assert frac == 1.0

    def test_far_beyond_allowance_still_full(self) -> None:
        from app.ee.billing.token_billing import chargeable_fraction

        frac = chargeable_fraction(total_tokens=1000, tokens_used_before=50_000, free_allowance_tokens=5000)
        assert frac == 1.0

    def test_unlimited_allowance_never_charges(self) -> None:
        from app.ee.billing.token_billing import chargeable_fraction

        frac = chargeable_fraction(total_tokens=1_000_000, tokens_used_before=0, free_allowance_tokens=None)
        assert frac == 0.0

    def test_zero_tokens_never_charges(self) -> None:
        from app.ee.billing.token_billing import chargeable_fraction

        frac = chargeable_fraction(total_tokens=0, tokens_used_before=10_000, free_allowance_tokens=5000)
        assert frac == 0.0


# ---------------------------------------------------------------------------
# 2-4. meter_and_charge / BYO integration (InMemory stores)
# ---------------------------------------------------------------------------


class TestMeterAndChargeIntegration:
    def setup_method(self) -> None:
        os.environ["NUBI_LICENSE_KEY"] = _PRO_LICENSE_KEY
        from app.compute.metering import InMemorySink, set_sink
        from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
        from app.ee.billing.wallet_store import InMemoryWalletStore, set_wallet_store_for_tests
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests

        reset_for_tests()
        reset_license_cache()
        self.billing_store = InMemoryBillingStore()
        self.wallet_store = InMemoryWalletStore()
        set_billing_store_for_tests(self.billing_store)
        set_wallet_store_for_tests(self.wallet_store)
        set_sink(InMemorySink())

    def teardown_method(self) -> None:
        from app.compute.metering import set_sink
        from app.ee.billing.store import set_billing_store_for_tests
        from app.ee.billing.wallet_store import set_wallet_store_for_tests
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests

        reset_for_tests()
        set_billing_store_for_tests(None)
        set_wallet_store_for_tests(None)
        set_sink(None)
        os.environ.pop("NUBI_LICENSE_KEY", None)
        reset_license_cache()

    async def _seed_subscription(self, org_id: str, tier: str) -> None:
        await self.billing_store.upsert_subscription(org_id, tier=tier, status="active")

    async def _seed_ai_tokens(self, org_id: str, tokens: float) -> None:
        from app.compute.metering import record_usage

        await record_usage(kind="ai_call", user_id="u1", org_id=org_id, units=tokens)

    @pytest.mark.asyncio
    async def test_free_allowance_then_overage_math_is_prorated(self) -> None:
        """STARTER (1,000,000 free tokens/mo): a call that straddles the
        allowance computes ONLY the overage portion as chargeable, at cost +
        markup (wallet funding is exercised separately, in
        test_wallet_charged_when_funded)."""
        from app.ee.billing.fx import set_fx_rate_store_for_tests
        from app.ee.billing.token_billing import meter_and_charge, record_token_usage

        set_fx_rate_store_for_tests(None)  # use the emergency fallback rate deterministically
        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        # 999,000 tokens already used this period; this call adds 2,000 more
        # -> 1,000 tokens (half the call) are chargeable.
        await self._seed_ai_tokens(org_id, 999_000)
        # meter_and_charge assumes THIS call's tokens are already recorded
        # (the real app.routes.ai / app.routes.chat call sites record before
        # invoking the app.features.meter_ai_usage hook) — mirror that here.
        await record_token_usage(org_id=org_id, user_id="u1", total_tokens=2_000, endpoint="ai_ask")

        result = await meter_and_charge(
            org_id=org_id,
            user_id="u1",
            endpoint="ai_ask",
            prompt_tokens=1_500,
            completion_tokens=500,
            total_tokens=2_000,
            usd_cost=0.02,  # cost of the FULL call
            is_byo=False,
        )

        # The MATH is correct regardless of wallet funding: exactly half this
        # call's tokens are beyond the free allowance, so half the RAW cost
        # ($0.01) is chargeable — result["usd_cost"] is the PRE-markup
        # chargeable cost; usd_cents is the post-markup (7.5%) wallet debit.
        assert result["chargeable_fraction"] == pytest.approx(0.5)
        assert result["usd_cost"] == pytest.approx(0.01, rel=1e-6)
        assert result["usd_cents"] == round(0.01 * 1.075 * 100)  # 1 cent (rounds to nearest)
        # This org's wallet was never funded — the debit fails open
        # (WalletInsufficientError, logged, never raised) rather than blocking
        # the already-completed LLM response. See test_wallet_charged_when_funded
        # for the funded-wallet path where the debit actually lands.
        assert result["charged"] is False
        ledger = await self.wallet_store.list_ledger(org_id, entry_type="USAGE_LLM")
        assert len(ledger) == 0

    @pytest.mark.asyncio
    async def test_wallet_charged_when_funded(self) -> None:
        """With a funded wallet, the overage portion is actually debited."""
        from app.ee.billing.token_billing import meter_and_charge, record_token_usage

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        await self.wallet_store.set_balance(org_id, 100_000)  # $1000.00
        await self._seed_ai_tokens(org_id, 999_000)
        await record_token_usage(org_id=org_id, user_id="u1", total_tokens=2_000, endpoint="ai_ask")

        result = await meter_and_charge(
            org_id=org_id,
            user_id="u1",
            endpoint="ai_ask",
            prompt_tokens=1_500,
            completion_tokens=500,
            total_tokens=2_000,
            usd_cost=0.02,
            is_byo=False,
        )

        assert result["charged"] is True
        ledger = await self.wallet_store.list_ledger(org_id, entry_type="USAGE_LLM")
        assert len(ledger) == 1
        assert ledger[0]["amount_usd_cents"] == -result["usd_cents"]
        balance = await self.wallet_store.get_balance(org_id)
        assert balance["balance_usd_cents"] == 100_000 - result["usd_cents"]

    @pytest.mark.asyncio
    async def test_within_allowance_never_charges(self) -> None:
        """A call entirely within the free allowance never touches the wallet."""
        from app.ee.billing.token_billing import meter_and_charge

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        await self.wallet_store.set_balance(org_id, 100_000)

        result = await meter_and_charge(
            org_id=org_id,
            user_id="u1",
            endpoint="ai_ask",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            usd_cost=0.001,
            is_byo=False,
        )
        assert result["charged"] is False
        assert result["chargeable_fraction"] == 0.0
        balance = await self.wallet_store.get_balance(org_id)
        assert balance["balance_usd_cents"] == 100_000  # untouched

    @pytest.mark.asyncio
    async def test_byo_key_skips_charge(self) -> None:
        """is_byo=True never charges the wallet, regardless of allowance/cost."""
        from app.ee.billing.token_billing import meter_and_charge

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        await self.wallet_store.set_balance(org_id, 100_000)
        await self._seed_ai_tokens(org_id, 5_000_000)  # WAY over the 1M free allowance

        result = await meter_and_charge(
            org_id=org_id,
            user_id="u1",
            endpoint="ai_ask",
            prompt_tokens=1_000,
            completion_tokens=1_000,
            total_tokens=2_000,
            usd_cost=5.00,  # would be a big charge if not BYO
            is_byo=True,
        )
        assert result["charged"] is False
        assert result["usd_cents"] == 0
        balance = await self.wallet_store.get_balance(org_id)
        assert balance["balance_usd_cents"] == 100_000  # untouched

    @pytest.mark.asyncio
    async def test_free_tier_never_charges_even_over_allowance(self) -> None:
        """FREE has no wallet relationship — never attempt a charge, even if a
        single oversized call slips past the pre-flight 'any capacity' check."""
        from app.ee.billing.token_billing import meter_and_charge

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "free")
        await self._seed_ai_tokens(org_id, 99_000)  # near the 100k free allowance

        result = await meter_and_charge(
            org_id=org_id,
            user_id="u1",
            endpoint="ai_ask",
            prompt_tokens=5_000,
            completion_tokens=5_000,
            total_tokens=10_000,  # pushes well past the 100k allowance
            usd_cost=0.10,
            is_byo=False,
        )
        assert result["charged"] is False
        ledger = await self.wallet_store.list_ledger(org_id, entry_type="USAGE_LLM")
        assert ledger == []


# ---------------------------------------------------------------------------
# 3b. BYO key store + eligibility gating
# ---------------------------------------------------------------------------


class TestOrgAiKeys:
    def setup_method(self) -> None:
        os.environ["NUBI_LICENSE_KEY"] = _PRO_LICENSE_KEY
        os.environ["CONNECTOR_SECRET_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        from app.ee.billing.org_ai_keys import InMemoryOrgAiKeyStore, set_org_ai_key_store_for_tests
        from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests
        from app.security.crypto import reset_keys_for_tests

        reset_for_tests()
        reset_license_cache()
        reset_keys_for_tests()
        self.billing_store = InMemoryBillingStore()
        self.key_store = InMemoryOrgAiKeyStore()
        set_billing_store_for_tests(self.billing_store)
        set_org_ai_key_store_for_tests(self.key_store)

    def teardown_method(self) -> None:
        from app.ee.billing.org_ai_keys import set_org_ai_key_store_for_tests
        from app.ee.billing.store import set_billing_store_for_tests
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests
        from app.security.crypto import reset_keys_for_tests

        reset_for_tests()
        set_billing_store_for_tests(None)
        set_org_ai_key_store_for_tests(None)
        os.environ.pop("NUBI_LICENSE_KEY", None)
        os.environ.pop("CONNECTOR_SECRET_KEY", None)
        reset_license_cache()
        reset_keys_for_tests()

    async def _seed_subscription(self, org_id: str, tier: str) -> None:
        await self.billing_store.upsert_subscription(org_id, tier=tier, status="active")

    @pytest.mark.asyncio
    async def test_free_tier_cannot_byo(self) -> None:
        from app.ee.billing.org_ai_keys import set_org_ai_key
        from app.errors import AppError

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "free")
        with pytest.raises(AppError) as exc:
            await set_org_ai_key(org_id, "anthropic", "sk-ant-fake-byo-key")
        assert exc.value.code == "ai_key_requires_paid_tier"
        assert exc.value.status == 402

    @pytest.mark.asyncio
    async def test_paid_tier_can_byo_and_round_trips(self) -> None:
        from app.ee.billing.org_ai_keys import get_org_ai_key_store, set_org_ai_key

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        await set_org_ai_key(org_id, "anthropic", "sk-ant-real-secret-value")

        stored = await get_org_ai_key_store().get(org_id, "anthropic")
        assert stored == "sk-ant-real-secret-value"

    @pytest.mark.asyncio
    async def test_clear_removes_key(self) -> None:
        from app.ee.billing.org_ai_keys import clear_org_ai_key, get_org_ai_key_store, set_org_ai_key

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        await set_org_ai_key(org_id, "openai", "sk-openai-fake")
        await clear_org_ai_key(org_id, "openai")
        assert await get_org_ai_key_store().get(org_id, "openai") is None

    @pytest.mark.asyncio
    async def test_invalid_provider_rejected(self) -> None:
        from app.ee.billing.org_ai_keys import set_org_ai_key
        from app.errors import AppError

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        with pytest.raises(AppError) as exc:
            await set_org_ai_key(org_id, "not-a-real-vendor", "key")
        assert exc.value.code == "ai_key_provider_invalid"

    @pytest.mark.asyncio
    async def test_resolve_provider_for_org_swaps_in_byo_key(self) -> None:
        """A paid org with a stored key for the operator's active vendor gets
        its OWN key routed in, tagged is_byo=True."""
        from app.ai.provider import LiteLLMProvider
        from app.ee.billing.org_ai_keys import resolve_provider_for_org, set_org_ai_key

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        await set_org_ai_key(org_id, "anthropic", "sk-ant-byo-secret")

        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-operator-key"
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("LITELLM_MODEL", None)
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            provider, is_byo = await resolve_provider_for_org(org_id)
            assert is_byo is True
            assert isinstance(provider, LiteLLMProvider)
            assert provider.vendor == "anthropic"
            assert provider._api_key == "sk-ant-byo-secret"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_resolve_provider_for_org_no_key_uses_operator_default(self) -> None:
        from app.ee.billing.org_ai_keys import resolve_provider_for_org

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")  # paid, but no BYO key stored

        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-operator-key"
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("LITELLM_MODEL", None)
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            provider, is_byo = await resolve_provider_for_org(org_id)
            assert is_byo is False
            assert provider._api_key == "sk-ant-operator-key"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_resolve_provider_for_org_free_tier_never_byo(self) -> None:
        """Even if a key were somehow stored, a FREE-tier org never gets BYO."""
        from app.ee.billing.org_ai_keys import resolve_provider_for_org

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "free")
        # Directly seed the store (bypassing set_org_ai_key's own gate) to prove
        # resolve_provider_for_org ALSO gates on tier independently.
        await self.key_store.put(org_id, "anthropic", "sk-ant-should-never-be-used")

        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-operator-key"
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("LITELLM_MODEL", None)
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            provider, is_byo = await resolve_provider_for_org(org_id)
            assert is_byo is False
            assert provider._api_key == "sk-ant-operator-key"
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 5. GET /ai/providers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "byo-tester@example.com",
        "name": "BYO Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


@pytest_asyncio.fixture
async def providers_client(app, fake_db):
    """HTTPX async client with a seeded org-owner user for GET /ai/providers."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
    set_repo(repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as ac:
        yield ac, user_id

    set_repo(None)


class TestAiProvidersEndpoint:
    def _clear_llm_env(self, monkeypatch):
        for key in (
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
            "LLM_PROVIDER", "LITELLM_MODEL", "NUBI_AI_ENABLED_PROVIDERS",
        ):
            monkeypatch.delenv(key, raising=False)
        from app.config import get_settings
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_requires_auth(self, providers_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        ac, _ = providers_client
        resp = await ac.get("/api/v1/ai/providers")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_default_enabled_providers_and_shape(self, providers_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        ac, user_id = providers_client
        resp = await ac.get("/api/v1/ai/providers", headers=_auth_headers(user_id))
        assert resp.status_code == 200
        body = resp.json()
        assert "providers" in body and "byo" in body
        ids = {p["id"] for p in body["providers"]}
        # Default NUBI_AI_ENABLED_PROVIDERS = "anthropic,openai,gemini".
        assert ids == {"anthropic", "openai", "gemini"}
        for p in body["providers"]:
            assert isinstance(p["models"], list) and len(p["models"]) > 0
            assert sum(1 for m in p["models"] if m["default"]) == 1
            assert p["configured"] is False  # no vendor keys set in this test
        # No paid-tier / EE billing wired in this bare test app → BYO unavailable.
        assert body["byo"]["can_byo"] is False

    @pytest.mark.asyncio
    async def test_restricting_enabled_providers(self, providers_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        monkeypatch.setenv("NUBI_AI_ENABLED_PROVIDERS", "anthropic")
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = providers_client
        resp = await ac.get("/api/v1/ai/providers", headers=_auth_headers(user_id))
        assert resp.status_code == 200
        body = resp.json()
        assert [p["id"] for p in body["providers"]] == ["anthropic"]

    @pytest.mark.asyncio
    async def test_configured_reflects_operator_key(self, providers_client, monkeypatch):
        self._clear_llm_env(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-operator")
        from app.config import get_settings
        get_settings.cache_clear()

        ac, user_id = providers_client
        resp = await ac.get("/api/v1/ai/providers", headers=_auth_headers(user_id))
        body = resp.json()
        by_id = {p["id"]: p for p in body["providers"]}
        assert by_id["anthropic"]["configured"] is True
        assert by_id["openai"]["configured"] is False

    @pytest.mark.asyncio
    async def test_model_ids_match_allowlist(self, providers_client, monkeypatch):
        """Every advertised model id is exactly one provider.complete() would accept."""
        self._clear_llm_env(monkeypatch)
        from app.ai.provider import ALLOWED_MODELS

        ac, user_id = providers_client
        resp = await ac.get("/api/v1/ai/providers", headers=_auth_headers(user_id))
        body = resp.json()
        for p in body["providers"]:
            ids = [m["id"] for m in p["models"]]
            assert ids == ALLOWED_MODELS[p["id"]]
