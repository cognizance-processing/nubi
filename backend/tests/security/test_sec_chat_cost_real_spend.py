"""Regression guard: the chat cost ceiling must accumulate REAL LLM spend.

REAL VULNERABILITY FOUND + FIXED IN THIS PASS
-----------------------------------------------
Severity: Medium (cost-based DoS / billing-bypass — the per-user/per-org daily
USD ceiling documented in ``app/ai/cost_ceiling.py`` was silently unenforceable
against real (billed) traffic).

Repro (pre-fix)
    ``app/routes/chat.py::chat_stream`` called
    ``record_chat_cost(org_id, user_id, 0.0)`` UNCONDITIONALLY after every
    turn — the real per-turn LiteLLM cost computed inside
    ``app/chat/llm.py::_stream_real`` (via ``litellm.completion_cost``) was
    computed into a local variable and simply discarded; it was never
    attached to the ``_Turn`` object returned to the route. Every real LLM
    turn therefore recorded exactly $0.00 into the metering store regardless
    of actual spend, so ``check_chat_budget``'s rolling-24h sum could NEVER
    exceed a configured ``NUBI_CHAT_USER_DAILY_USD`` / ``NUBI_CHAT_ORG_DAILY_USD``
    ceiling — the budget guard was live in code but dead in practice for any
    paid model traffic. An org could run unlimited chat turns against a real
    (billed) LLM provider with no server-side spend cap ever tripping.

Fix
    ``app/chat/llm.py``: ``_Turn`` now carries ``cost_usd`` (accumulated via
    ``litellm.completion_cost(completion_response=rebuilt)`` per agentic
    step, best-effort — an unknown model / missing pricing entry must not
    fail the turn). ``app/routes/chat.py`` now records
    ``last_turn.cost_usd`` instead of a hardcoded ``0.0``.

This file guards BOTH halves of the fix so a future refactor cannot silently
reintroduce the bypass:
  1. ``_Turn.cost_usd`` genuinely accumulates real per-step cost.
  2. ``app/routes/chat.py`` sources the recorded cost from the turn, not a
     literal ``0.0`` (source-level regression guard — the SSE endpoint itself
     needs a live Postgres chat store to exercise end-to-end, per
     ``tests/test_chat_stream.py``'s module docstring, so a source-shape
     check is the appropriate unit-level backstop here).
"""

from __future__ import annotations

import inspect
import sys
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake litellm plumbing (mirrors tests/test_chat_stream.py's _FakeLiteLLM,
# extended with completion_cost so cost accumulation is exercised).
# ---------------------------------------------------------------------------


class _Delta:
    def __init__(self, content=None):
        self.content = content
        self.tool_calls = None


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _StreamChoice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, content):
        self.choices = [_StreamChoice(_Delta(content=content))]


class _RebuiltChoice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason


class _Rebuilt:
    def __init__(self, message, finish_reason, usage=None):
        self.choices = [_RebuiltChoice(message, finish_reason)]
        self.usage = usage


class _FakeLiteLLMWithCost:
    """Stand-in for litellm that reports a KNOWN, non-zero cost per step."""

    def __init__(self, steps: list[dict[str, Any]], cost_per_step: list[float]):
        self._steps = steps
        self._cost_per_step = cost_per_step
        self._i = 0
        self.drop_params = False

    def completion(self, **kwargs):
        return iter([_Chunk(t) for t in self._steps[self._i]["tokens"]])

    def stream_chunk_builder(self, chunks, messages=None):
        rebuilt = self._steps[self._i]["rebuilt"]
        self._i += 1
        return rebuilt

    def completion_cost(self, completion_response=None):
        # Called once per step (index already advanced by stream_chunk_builder).
        return self._cost_per_step[self._i - 1]


class TestTurnCostAccumulation:
    """1: _Turn.cost_usd accumulates real per-step LiteLLM cost."""

    def test_single_step_turn_records_nonzero_cost(self, monkeypatch):
        monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: "sk-test")

        steps = [{"tokens": ["Hello."], "rebuilt": _Rebuilt(_Msg("Hello."), finish_reason="stop")}]
        fake = _FakeLiteLLMWithCost(steps, cost_per_step=[0.0042])
        monkeypatch.setitem(sys.modules, "litellm", fake)

        from app.chat.llm import stream_chat

        history = [{"role": "user", "content": "hi"}]
        turn = None
        for _ev, t in stream_chat(history, "claude-opus-4-8"):
            turn = t

        assert turn is not None
        assert turn.cost_usd == pytest.approx(0.0042), (
            f"SECURITY REGRESSION: real LLM turn cost was not accumulated onto "
            f"the turn (got cost_usd={turn.cost_usd!r}) — this is the exact bug "
            f"that let every real chat turn record $0.00 spend."
        )

    def test_multi_step_tool_use_turn_sums_cost_across_steps(self, monkeypatch):
        from app.chat.llm import stream_chat

        monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: "sk-test")

        class _Fn:
            def __init__(self, name, arguments):
                self.name = name
                self.arguments = arguments

        class _ToolCall:
            def __init__(self, id, name, arguments):
                self.id = id
                self.type = "function"
                self.function = _Fn(name, arguments)

        steps = [
            {
                "tokens": ["Looking up… "],
                "rebuilt": _Rebuilt(
                    _Msg("", tool_calls=[_ToolCall("call_1", "list_registered_queries", "{}")]),
                    finish_reason="tool_calls",
                ),
            },
            {
                "tokens": ["Done."],
                "rebuilt": _Rebuilt(_Msg("Done."), finish_reason="stop"),
            },
        ]
        fake = _FakeLiteLLMWithCost(steps, cost_per_step=[0.0010, 0.0025])
        monkeypatch.setitem(sys.modules, "litellm", fake)

        history = [{"role": "user", "content": "what queries exist?"}]
        turn = None
        for _ev, t in stream_chat(history, "claude-opus-4-8"):
            turn = t

        assert turn.cost_usd == pytest.approx(0.0035), (
            f"Expected cost across both agentic steps to sum, got {turn.cost_usd!r}"
        )

    def test_unknown_model_pricing_failure_does_not_break_the_turn(self, monkeypatch):
        """Best-effort: completion_cost raising must not fail the turn, and
        must leave cost_usd at 0.0 for that step (never crash / never a
        negative or garbage value)."""
        monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: "sk-test")

        steps = [{"tokens": ["Hi."], "rebuilt": _Rebuilt(_Msg("Hi."), finish_reason="stop")}]

        class _BrokenCostLiteLLM(_FakeLiteLLMWithCost):
            def completion_cost(self, completion_response=None):
                raise RuntimeError("unknown model pricing")

        fake = _BrokenCostLiteLLM(steps, cost_per_step=[0.0])
        monkeypatch.setitem(sys.modules, "litellm", fake)

        from app.chat.llm import stream_chat

        turn = None
        for _ev, t in stream_chat([{"role": "user", "content": "hi"}], "claude-opus-4-8"):
            turn = t

        assert turn.cost_usd == 0.0
        assert turn.text == "Hi."

    def test_offline_null_provider_turn_has_zero_cost(self, monkeypatch):
        """No LLM credential -> offline path -> cost_usd stays 0.0 (correct;
        NullProvider turns genuinely have no spend)."""
        monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: None)

        from app.chat.llm import stream_chat

        turn = None
        for _ev, t in stream_chat([{"role": "user", "content": "hello"}], "claude-opus-4-8"):
            turn = t

        assert turn.cost_usd == 0.0


class TestChatRouteRecordsRealCost:
    """2: app/routes/chat.py sources record_chat_cost's amount from the turn."""

    def test_record_chat_cost_call_uses_turn_cost_not_hardcoded_zero(self):
        import app.routes.chat as chat_mod

        src = inspect.getsource(chat_mod.chat_stream)
        assert "record_chat_cost, org_id, user_id, 0.0)" not in src, (
            "SECURITY REGRESSION: app/routes/chat.py calls record_chat_cost with "
            "a hardcoded 0.0 — this silently disables the per-user/per-org daily "
            "chat budget ceiling for all real (non-offline) LLM traffic."
        )
        assert "last_turn.cost_usd" in src, (
            "app/routes/chat.py must source the recorded cost from "
            "last_turn.cost_usd (the real accumulated per-turn LiteLLM spend)."
        )
