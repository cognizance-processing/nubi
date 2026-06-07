"""Selectable Claude models for the streaming chat backend.

The chat UI lets the user pick which Claude model answers.  We expose a small,
curated list of current models with their EXACT Anthropic model ids.  These ids
are passed straight through to the Anthropic Messages API by ``app.chat.llm``.

Keep this list in sync with the Anthropic model catalog.  The default (first
entry) is Opus 4.8 — the most capable model.
"""

from __future__ import annotations

from typing import Any

#: Curated, ordered list of selectable models.  ``id`` is the exact Anthropic
#: model id; ``label`` is the human-friendly name shown in the picker.
CHAT_MODELS: list[dict[str, str]] = [
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8"},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5"},
]

#: Fast lookup set of valid ids.
_VALID_IDS: frozenset[str] = frozenset(m["id"] for m in CHAT_MODELS)

#: Default model id used when the caller does not supply a (valid) one.
DEFAULT_MODEL_ID: str = CHAT_MODELS[0]["id"]


def list_models() -> list[dict[str, str]]:
    """Return the selectable model list (copied so callers can't mutate it)."""
    return [dict(m) for m in CHAT_MODELS]


def resolve_model(requested: str | None) -> str:
    """Return a valid Anthropic model id for *requested*.

    Falls back to :data:`DEFAULT_MODEL_ID` when *requested* is empty or not in
    the curated list (so a stale/invalid id from the client never reaches the
    Anthropic API).
    """
    if requested and requested in _VALID_IDS:
        return requested
    return DEFAULT_MODEL_ID


def is_valid_model(requested: str | None) -> bool:
    """Return True if *requested* is one of the curated model ids."""
    return bool(requested) and requested in _VALID_IDS


__all__: list[Any] = ["CHAT_MODELS", "DEFAULT_MODEL_ID", "list_models", "resolve_model", "is_valid_model"]
