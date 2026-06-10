"""Tests for the AI dashboard generate -> validate -> repair loop.

These cover the REAL-provider path of ``generate_dashboard_spec`` (the path
that used to silently fall back to ``_build_null_spec`` on bad LLM output):

1. A provider that emits an INVALID spec on round 1 and a VALID spec on round 2
   is repaired by the loop — the final spec is valid with ``repair_rounds >= 1``.
2. A provider that emits an INVALID spec on EVERY round fails LOUDLY (raises
   ``AppError("dashboard_generation_failed")``) instead of silently
   substituting the deterministic NullProvider template.
3. The genuine NullProvider/no-API-key path still returns a valid deterministic
   template, clearly marked ``generated_by == "null_template"``.

Network safety
--------------
No real provider is used; the ``FakeProvider`` returns canned JSON strings and
makes zero network calls.
"""

from __future__ import annotations

import json

import pytest

from app.ai.dashboard import (
    MAX_DASHBOARD_REPAIR_ROUNDS,
    generate_dashboard_spec,
)
from app.ai.grounding import build_catalog
from app.ai.provider import LLMProvider, NullProvider
from app.errors import AppError


# ---------------------------------------------------------------------------
# Spec builders — emit raw JSON strings the way a real LLM would
# ---------------------------------------------------------------------------

_POS = {"x": 1, "y": 1, "w": 4, "h": 2}


def _valid_spec_json() -> str:
    """A minimal, fully-valid DashboardSpec JSON (one table widget)."""
    return json.dumps(
        {
            "version": 1,
            "title": "repaired dashboard",
            "layout": {"cols": 12, "row_height": 60},
            "widgets": [
                {
                    "id": "w1",
                    "type": "table",
                    "query_id": "demo_all",
                    "encoding": {},
                    "props": {"limit": 50},
                    "pos": _POS,
                }
            ],
        }
    )


def _invalid_spec_json() -> str:
    """A spec that PARSES but has HARD validation errors.

    A chart widget with no ``chart_type`` and no ``x``/``y`` encoding produces
    three hard errors from ``validate_spec`` (not the soft registry warning).
    """
    return json.dumps(
        {
            "version": 1,
            "title": "broken dashboard",
            "widgets": [
                {
                    "id": "w1",
                    "type": "chart",
                    "query_id": "demo_all",
                    "encoding": {},
                    "pos": _POS,
                }
            ],
        }
    )


# ---------------------------------------------------------------------------
# FakeProvider — scripted .complete() responses (no network)
# ---------------------------------------------------------------------------


class FakeProvider(LLMProvider):
    """Real-provider stand-in that returns scripted JSON, no network.

    It is NOT a ``NullProvider`` so ``generate_dashboard_spec`` takes the real
    LLM path (with the repair loop).  ``responses`` is consumed one item per
    ``.complete()`` call; the last item repeats if the loop calls more times.
    """

    name = "fake"

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str | None]] = []

    def complete(self, prompt: str, system: str | None = None) -> str:
        self.calls.append((prompt, system))
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[idx]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_repairs_invalid_then_valid():
    """Invalid on round 1, valid on round 2 -> repaired, valid, rounds >= 1."""
    catalog = build_catalog()
    provider = FakeProvider([_invalid_spec_json(), _valid_spec_json()])

    spec = generate_dashboard_spec("show me demo data", catalog, provider)

    assert spec.valid is True
    assert spec.generated_by == "llm"
    assert spec.repair_rounds >= 1
    # Exactly two completion calls: initial generation + one repair round.
    assert len(provider.calls) == 2
    # The second call is the repair prompt — it must echo the hard errors back.
    repair_prompt = provider.calls[1][0]
    assert "chart_type" in repair_prompt
    # The returned spec is the valid one (title from the corrected response).
    assert spec.title == "repaired dashboard"


def test_invalid_every_round_fails_loud_not_silent_template():
    """Invalid on EVERY round -> AppError, NOT a silent NullProvider template."""
    catalog = build_catalog()
    provider = FakeProvider([_invalid_spec_json()])  # always invalid

    with pytest.raises(AppError) as excinfo:
        generate_dashboard_spec("show me demo data", catalog, provider)

    err = excinfo.value
    assert err.code == "dashboard_generation_failed"
    # The structured hard issues are surfaced to the caller.
    assert "chart_type" in err.message
    # It tried the full budget (initial + repair rounds), not a single shot.
    assert len(provider.calls) == MAX_DASHBOARD_REPAIR_ROUNDS


def test_loud_failure_is_not_the_null_template():
    """The failure path must NOT quietly return the deterministic template."""
    catalog = build_catalog()
    provider = FakeProvider([_invalid_spec_json()])

    # NullProvider on the same question yields a valid template — proving a
    # template WOULD have been available to silently substitute.  The real
    # provider must instead raise rather than hand that back as success.
    null_spec = generate_dashboard_spec("show me demo data", catalog, NullProvider())
    assert null_spec.valid is True
    assert null_spec.generated_by == "null_template"

    with pytest.raises(AppError):
        generate_dashboard_spec("show me demo data", catalog, provider)


def test_valid_first_shot_has_zero_repair_rounds():
    """A clean first generation needs no repair rounds."""
    catalog = build_catalog()
    provider = FakeProvider([_valid_spec_json()])

    spec = generate_dashboard_spec("show me demo data", catalog, provider)

    assert spec.valid is True
    assert spec.repair_rounds == 0
    assert len(provider.calls) == 1


def test_malformed_non_json_then_valid_is_repaired():
    """Non-JSON garbage on round 1 is treated as a hard error and repaired."""
    catalog = build_catalog()
    provider = FakeProvider(["this is not json at all", _valid_spec_json()])

    spec = generate_dashboard_spec("show me demo data", catalog, provider)

    assert spec.valid is True
    assert spec.repair_rounds >= 1
