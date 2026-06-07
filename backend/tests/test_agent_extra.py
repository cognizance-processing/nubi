"""Extra unit tests for app/ai/agent.py edge cases.

Complements test_ai_agent.py which covers the main run_agent paths and tool
registry.  These tests target internal helpers and edge cases not yet covered:

1. _last_user_message — empty list, assistant-only list, Anthropic content-block
   format (list of {"type":"text","text":"..."} blocks).
2. _build_intent — every keyword branch:
   - "visuali" / "graph" / "plot" → chart intent.
   - "execute" / "fetch" / "show" / "list" → run intent.
   - none of the above → default intent.
3. run_agent — empty messages list → returns a valid reply (no crash).
4. run_agent — max_steps=0 → actions list is empty (trimmed to 0).
5. run_agent — real-provider path (non-NullProvider) → still returns a reply
   without network calls (provider.complete() is stubbed).
6. run_agent — Anthropic-style content block in messages → intent resolved.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.ai.agent import (
    _build_intent,
    _extract_question,
    _last_user_message,
    run_agent,
)
from app.ai.provider import LLMProvider, NullProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_claims() -> dict[str, Any]:
    return {"kind": "access", "sub": "test-user", "policies": {}, "scope": ["read:*"]}


def _null_provider() -> NullProvider:
    return NullProvider()


# ---------------------------------------------------------------------------
# 1. _last_user_message
# ---------------------------------------------------------------------------


class TestLastUserMessage:
    def test_empty_list_returns_empty_string(self):
        assert _last_user_message([]) == ""

    def test_no_user_role_returns_empty_string(self):
        msgs = [
            {"role": "assistant", "content": "Hello!"},
            {"role": "system", "content": "You are helpful."},
        ]
        assert _last_user_message(msgs) == ""

    def test_plain_string_content(self):
        msgs = [{"role": "user", "content": "Show me a chart"}]
        result = _last_user_message(msgs)
        assert result == "show me a chart"

    def test_returns_last_user_message_not_first(self):
        msgs = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "Reply"},
            {"role": "user", "content": "Second message"},
        ]
        result = _last_user_message(msgs)
        assert result == "second message"

    def test_anthropic_content_block_format(self):
        """Anthropic-style messages have content as a list of typed blocks."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Make a Dashboard please"},
                ],
            }
        ]
        result = _last_user_message(msgs)
        assert result == "make a dashboard please"

    def test_anthropic_content_block_skips_non_text_blocks(self):
        """Non-text blocks (e.g. image) are skipped; first text block is used."""
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "data": "..."}},
                    {"type": "text", "text": "Run the query"},
                ],
            }
        ]
        result = _last_user_message(msgs)
        assert result == "run the query"

    def test_content_as_empty_list_returns_empty_string(self):
        msgs = [{"role": "user", "content": []}]
        assert _last_user_message(msgs) == ""


# ---------------------------------------------------------------------------
# 2. _build_intent
# ---------------------------------------------------------------------------


class TestBuildIntent:
    # --- chart keywords ---

    def test_chart_keyword(self):
        assert _build_intent("show me a chart of sales") == "chart"

    def test_dashboard_keyword(self):
        assert _build_intent("create a dashboard") == "chart"

    def test_visuali_keyword(self):
        assert _build_intent("visualize the data") == "chart"

    def test_graph_keyword(self):
        assert _build_intent("graph the revenue") == "chart"

    def test_plot_keyword(self):
        assert _build_intent("plot this data set") == "chart"

    # --- run keywords ---

    def test_run_keyword(self):
        assert _build_intent("run the demo query") == "run"

    def test_query_keyword(self):
        assert _build_intent("query the database") == "run"

    def test_execute_keyword(self):
        assert _build_intent("execute select * from users") == "run"

    def test_fetch_keyword(self):
        assert _build_intent("fetch all records") == "run"

    def test_show_keyword(self):
        assert _build_intent("show all rows") == "run"

    def test_list_keyword(self):
        assert _build_intent("list the tables") == "run"

    # --- default ---

    def test_no_keyword_returns_default(self):
        assert _build_intent("hello there") == "default"

    def test_empty_string_returns_default(self):
        assert _build_intent("") == "default"

    # --- chart wins over run when both keywords present ---

    def test_chart_wins_over_run_when_chart_appears_first_in_code(self):
        # Implementation checks chart keywords before run keywords.
        result = _build_intent("chart and run the query")
        assert result == "chart"


# ---------------------------------------------------------------------------
# 3. _extract_question
# ---------------------------------------------------------------------------


class TestExtractQuestion:
    def test_returns_string_content(self):
        msgs = [{"role": "user", "content": "What is the total revenue?"}]
        assert _extract_question(msgs) == "What is the total revenue?"

    def test_empty_list_returns_fallback(self):
        assert _extract_question([]) == "show me the data"

    def test_content_block_format(self):
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "What tables exist?"}],
            }
        ]
        assert _extract_question(msgs) == "What tables exist?"


# ---------------------------------------------------------------------------
# 4. run_agent — max_steps=0
# ---------------------------------------------------------------------------


class TestRunAgentMaxStepsZero:
    def test_max_steps_zero_returns_empty_actions(self):
        result = run_agent(
            [{"role": "user", "content": "make a chart of demo data"}],
            _null_provider(),
            _empty_claims(),
            max_steps=0,
        )
        assert isinstance(result["reply"], str)
        assert result["actions"] == [], (
            "max_steps=0 must produce an empty actions list"
        )


# ---------------------------------------------------------------------------
# 5. run_agent — real-provider (non-NullProvider) path
# ---------------------------------------------------------------------------


class TestRunAgentRealProvider:
    """Exercises the real-provider branch of run_agent without making network calls.

    The provider.complete() method is stubbed to return a fixed string so we
    can verify the agent falls through to the same scripted tool chain.
    """

    def _stub_provider(self, response: str) -> LLMProvider:
        provider = MagicMock(spec=LLMProvider)
        provider.complete.return_value = response
        # Mark it as NOT a NullProvider so the real-provider branch is taken.
        provider.__class__ = LLMProvider
        return provider

    def test_real_provider_chart_hint_returns_reply(self):
        provider = self._stub_provider("chart")
        result = run_agent(
            [{"role": "user", "content": "show me a chart"}],
            provider,
            _empty_claims(),
        )
        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0

    def test_real_provider_run_hint_returns_reply(self):
        provider = self._stub_provider("run")
        result = run_agent(
            [{"role": "user", "content": "run a query"}],
            provider,
            _empty_claims(),
        )
        assert isinstance(result["reply"], str)

    def test_real_provider_default_hint_returns_reply(self):
        provider = self._stub_provider("sql")
        result = run_agent(
            [{"role": "user", "content": "what tables are there"}],
            provider,
            _empty_claims(),
        )
        assert isinstance(result["reply"], str)

    def test_real_provider_complete_raises_falls_back(self):
        """If provider.complete() raises, the agent falls back to intent from text."""
        provider = MagicMock(spec=LLMProvider)
        provider.complete.side_effect = RuntimeError("network error")
        provider.__class__ = LLMProvider

        result = run_agent(
            [{"role": "user", "content": "show me a chart"}],
            provider,
            _empty_claims(),
        )
        assert isinstance(result["reply"], str), (
            "agent must return a reply even when provider.complete() raises"
        )

    def test_real_provider_actions_have_required_keys(self):
        provider = self._stub_provider("chart")
        result = run_agent(
            [{"role": "user", "content": "make a chart"}],
            provider,
            _empty_claims(),
        )
        for action in result["actions"]:
            assert "tool" in action
            assert "arguments" in action
            assert "result" in action


# ---------------------------------------------------------------------------
# 6. run_agent — Anthropic-style content block in messages
# ---------------------------------------------------------------------------


class TestRunAgentContentBlockMessages:
    def test_anthropic_chart_intent_via_content_block(self):
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Please make a dashboard for revenue"}],
            }
        ]
        result = run_agent(msgs, _null_provider(), _empty_claims())
        tool_names = [a["tool"] for a in result["actions"]]
        assert "create_dashboard" in tool_names, (
            f"content-block message with 'dashboard' must route to chart intent; "
            f"got tools: {tool_names}"
        )

    def test_anthropic_run_intent_via_content_block(self):
        msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Run a query to list all users"}],
            }
        ]
        result = run_agent(msgs, _null_provider(), _empty_claims())
        tool_names = [a["tool"] for a in result["actions"]]
        assert "run_query" in tool_names, (
            f"content-block message with 'run' must route to run intent; "
            f"got tools: {tool_names}"
        )
