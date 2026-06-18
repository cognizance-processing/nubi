"""Tests for the LiteLLM provider integration.

Coverage
--------
1. Factory selection — ``LLM_PROVIDER=litellm`` + ``LITELLM_MODEL`` returns a
   ``LiteLLMProvider``; a missing ``LITELLM_MODEL`` raises
   ``AppError("llm_not_configured", 503)``; ``LITELLM_MODEL`` alone (no
   ``LLM_PROVIDER``) is auto-detected ahead of raw provider keys.
2. Allowlist gating — the default model is always allowed; ``LITELLM_ALLOWED_MODELS``
   adds extras; an unlisted per-request model raises
   ``AppError("model_not_allowed", 400)`` WITHOUT importing litellm.
3. Usage accounting — ``LLMUsage.add`` accumulates tokens + cost; the
   process-wide accumulator is exposed via ``get_process_usage``.
4. Env parse helpers — ``_parse_int`` / ``_parse_float`` tolerate unset/blank/junk.

Network safety
--------------
No real network calls and no ``litellm`` import are exercised here — the
provider is only constructed and its pre-import validation path is asserted.
``litellm`` need not be installed for this suite to pass.
"""

from __future__ import annotations

import pytest

from app.ai.provider import (
    LiteLLMProvider,
    LLMUsage,
    _make_litellm,
    _parse_float,
    _parse_int,
    get_process_usage,
    get_provider,
)
from app.errors import AppError


def _clear_llm_env(monkeypatch):
    """Strip every LLM-related env var and reset the settings cache."""
    for key in (
        "LLM_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "LITELLM_MODEL",
        "LITELLM_ALLOWED_MODELS",
        "LITELLM_API_KEY",
        "LITELLM_NUM_RETRIES",
        "LITELLM_TIMEOUT",
        "LITELLM_MAX_BUDGET_USD",
    ):
        monkeypatch.delenv(key, raising=False)
    from app.config import get_settings  # noqa: PLC0415

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 1. Factory selection
# ---------------------------------------------------------------------------


class TestFactorySelectsLiteLLM:
    def test_explicit_provider_returns_litellm(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "litellm")
        monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-opus-4-8")
        provider = get_provider()
        assert isinstance(provider, LiteLLMProvider)
        assert provider.name == "litellm"

    def test_explicit_provider_missing_model_raises_503(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "litellm")
        with pytest.raises(AppError) as exc:
            get_provider()
        assert exc.value.code == "llm_not_configured"
        assert exc.value.status == 503

    def test_litellm_model_auto_detected(self, monkeypatch):
        # LITELLM_MODEL set with no LLM_PROVIDER → litellm auto-detected.
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LITELLM_MODEL", "gpt-4o")
        provider = get_provider()
        assert isinstance(provider, LiteLLMProvider)

    def test_litellm_takes_priority_over_raw_provider_key(self, monkeypatch):
        # LiteLLM needs the raw provider key itself, so when LITELLM_MODEL is set
        # it wins over bare-key auto-detection.
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-opus-4-8")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
        provider = get_provider()
        assert isinstance(provider, LiteLLMProvider)


# ---------------------------------------------------------------------------
# 2. Allowlist gating (no litellm import required)
# ---------------------------------------------------------------------------


class TestAllowlistGating:
    def test_default_model_always_allowed(self):
        p = LiteLLMProvider("gpt-4o")
        assert "gpt-4o" in p._allowed

    def test_extra_allowed_models_threaded(self):
        p = LiteLLMProvider("gpt-4o", ["gpt-4o-mini", "anthropic/claude-haiku-4-5"])
        assert p._allowed == ["gpt-4o", "gpt-4o-mini", "anthropic/claude-haiku-4-5"]

    def test_default_deduped_from_extras(self):
        p = LiteLLMProvider("gpt-4o", ["gpt-4o", "gpt-4o-mini"])
        assert p._allowed == ["gpt-4o", "gpt-4o-mini"]

    def test_unlisted_model_raises_before_import(self):
        # complete() must fail with model_not_allowed (400) on an unlisted model
        # WITHOUT needing litellm installed — validation precedes the lazy import.
        p = LiteLLMProvider("gpt-4o")
        with pytest.raises(AppError) as exc:
            p.complete("hi", model="some/unlisted-model")
        assert exc.value.code == "model_not_allowed"
        assert exc.value.status == 400


# ---------------------------------------------------------------------------
# 3. Usage accounting
# ---------------------------------------------------------------------------


class TestUsageAccounting:
    def test_add_accumulates(self):
        u = LLMUsage()
        u.add(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost_usd=0.001)
        u.add(prompt_tokens=20, completion_tokens=10, total_tokens=30, cost_usd=0.002)
        assert u.calls == 2
        assert u.prompt_tokens == 30
        assert u.completion_tokens == 15
        assert u.total_tokens == 45
        assert u.cost_usd == pytest.approx(0.003)

    def test_as_dict_rounds_cost(self):
        u = LLMUsage()
        u.add(prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.123456789)
        d = u.as_dict()
        assert d["calls"] == 1
        assert d["cost_usd"] == 0.123457

    def test_process_usage_is_singleton(self):
        assert get_process_usage() is get_process_usage()
        assert isinstance(get_process_usage(), LLMUsage)

    def test_new_provider_has_zeroed_usage(self):
        p = LiteLLMProvider("gpt-4o")
        assert p.usage.calls == 0
        assert p.usage.cost_usd == 0.0


# ---------------------------------------------------------------------------
# 4. Env parse helpers + _make_litellm wiring
# ---------------------------------------------------------------------------


class TestParseHelpers:
    @pytest.mark.parametrize("value", [None, "", "abc", "1.5"])
    def test_parse_int_bad_values_return_none(self, value):
        assert _parse_int(value) is None

    def test_parse_int_good(self):
        assert _parse_int("3") == 3

    @pytest.mark.parametrize("value", [None, "", "abc"])
    def test_parse_float_bad_values_return_none(self, value):
        assert _parse_float(value) is None

    def test_parse_float_good(self):
        assert _parse_float("2.5") == 2.5


class TestMakeLiteLLMWiring:
    def test_threads_all_config(self):
        env = {
            "LITELLM_MODEL": "anthropic/claude-opus-4-8",
            "LITELLM_ALLOWED_MODELS": "gpt-4o, gpt-4o-mini",
            "LITELLM_API_KEY": "sk-explicit",
            "LITELLM_NUM_RETRIES": "2",
            "LITELLM_TIMEOUT": "45",
            "LITELLM_MAX_BUDGET_USD": "1.50",
        }
        p = _make_litellm(env.get)
        assert isinstance(p, LiteLLMProvider)
        assert p._default_model == "anthropic/claude-opus-4-8"
        assert p._allowed == ["anthropic/claude-opus-4-8", "gpt-4o", "gpt-4o-mini"]
        assert p._api_key == "sk-explicit"
        assert p._num_retries == 2
        assert p._timeout == 45.0
        assert p._max_budget_usd == 1.5

    def test_missing_model_raises(self):
        with pytest.raises(AppError) as exc:
            _make_litellm({}.get)
        assert exc.value.code == "llm_not_configured"
