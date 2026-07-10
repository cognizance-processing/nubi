"""LiteLLM model-catalog + pricing sync (``app.ai.provider.build_model_catalog``).

These cover the "sync models + pricing for allowed providers" feature:

1. Each ENABLED provider surfaces its curated chat models with pricing synced
   from ``litellm.model_cost`` (per-1M-token USD + context-window fields).
2. Prices are the source-of-truth ``litellm.model_cost`` rates converted to a
   per-1M-token figure — not hardcoded here.
3. A DISABLED provider never appears in the catalog / metadata.

The catalog is derived purely from LiteLLM's static table + the curated
``ALLOWED_MODELS`` allowlist, so no network/API keys are involved.
"""

from __future__ import annotations

import pytest

from app.ai.provider import (
    ALLOWED_MODELS,
    build_model_catalog,
    list_provider_metadata,
)

# The pricing tests need LiteLLM's cost table available.
litellm = pytest.importorskip("litellm")

_PRICE_FIELDS = (
    "input_cost_per_1m",
    "output_cost_per_1m",
    "max_input_tokens",
    "max_output_tokens",
)


def test_catalog_returns_priced_chat_models_for_enabled_providers():
    catalog = build_model_catalog(["anthropic", "openai", "gemini"])
    assert set(catalog) == {"anthropic", "openai", "gemini"}

    for provider, models in catalog.items():
        # Model ids match the curated allowlist exactly (same order).
        assert [m["id"] for m in models] == ALLOWED_MODELS[provider]
        # Every model exposes the pricing/context fields.
        for m in models:
            for field in _PRICE_FIELDS:
                assert field in m
        # Exactly one default per provider.
        assert sum(1 for m in models if m["default"]) == 1


def test_pricing_matches_litellm_cost_table():
    """Per-1M prices are the litellm.model_cost rates × 1e6 (source of truth)."""
    catalog = build_model_catalog(["anthropic", "openai"])
    table = litellm.model_cost

    for provider in ("anthropic", "openai"):
        for m in catalog[provider]:
            info = table.get(m["id"])
            if not info:  # pragma: no cover — curated ids are all in the table
                continue
            expected_in = round(info["input_cost_per_token"] * 1_000_000, 4)
            expected_out = round(info["output_cost_per_token"] * 1_000_000, 4)
            assert m["input_cost_per_1m"] == expected_in
            assert m["output_cost_per_1m"] == expected_out
            # These curated flagships have real, positive pricing.
            assert m["input_cost_per_1m"] > 0
            assert m["output_cost_per_1m"] > 0


def test_disabled_provider_is_excluded():
    catalog = build_model_catalog(["openai"])
    assert set(catalog) == {"openai"}
    assert "anthropic" not in catalog
    assert "gemini" not in catalog


def test_list_provider_metadata_carries_pricing_and_respects_allowlist():
    meta = list_provider_metadata(["anthropic"])
    assert [p["id"] for p in meta] == ["anthropic"]
    models = meta[0]["models"]
    assert models  # non-empty
    # Backward-compatible fields retained…
    for m in models:
        assert {"id", "display_name", "default"} <= set(m)
        # …plus the new pricing fields.
        assert {"input_cost_per_1m", "output_cost_per_1m"} <= set(m)
