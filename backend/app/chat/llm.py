"""Streaming agent loop over the Anthropic Messages API (tool use + token deltas).

This module owns the actual model call for the chat backend.  It runs the
agentic loop manually so it can stream token deltas AND surface tool_use /
tool_result events live, Cursor-style.

Reusing the existing LLM client + key
-------------------------------------
The rest of the app talks to Claude through ``app.ai.provider.AnthropicProvider``,
which (a) reads the API key from ``ANTHROPIC_API_KEY`` (settings or env, via
``get_provider()``'s resolution) and (b) lazily imports the ``anthropic`` SDK.
That provider only exposes a plain ``complete()`` with no streaming/tool-use, so
here we reuse its *key resolution* and the *same anthropic SDK client*, and add
the streaming tool-use loop on top.  We do NOT hardcode a key.

When no Anthropic key is configured (the default in dev/CI), ``stream_chat``
falls back to a deterministic offline path that still emits the same event
shapes (including a real ``propose_dashboard_spec`` tool call when the user asks
for a dashboard) so the endpoint works without network access.

Event shapes yielded by ``stream_chat`` (all JSON-serialisable dicts)::

    {"type": "token",       "text": str}
    {"type": "tool_use",    "id": str, "name": str, "input": dict}
    {"type": "tool_result", "id": str, "output": dict}
    {"type": "error",       "message": str}

The final assistant turn (full text + tool calls + any proposed spec) is NOT
emitted here — the route assembles it from ``collect_turn`` (below) so it can
persist it and send the terminal ``message`` event.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from app.chat.tools import anthropic_tool_specs, execute_tool

# Hard cap on agentic iterations so a misbehaving model can't loop forever.
_MAX_STEPS = 6
_MAX_TOKENS = 4096

_SYSTEM_PROMPT = """\
You are Nubi's dashboard assistant, embedded in a Cursor-like editor.

You help the user build and edit analytics dashboards. You can perform ALL
dashboard editing through tools — not just propose a whole new spec.

Choosing a tool:
- To BUILD a brand-new dashboard from a description, call
  `propose_dashboard_spec` with a clear instruction.
- To make a TARGETED change to the EXISTING dashboard, use the granular edit
  tools and pass the current spec in the `spec` argument: `add_widget`,
  `update_widget`, `remove_widget`, `set_widget_style`, `set_layout`,
  `set_background`, `add_variable`, `set_drilldown`. Each returns the updated
  spec, which the editor applies.
- Each granular edit tool takes the CURRENT `spec` and returns the updated one.
  When chaining several edits in a turn, pass the spec returned by the previous
  tool into the next so changes accumulate.

Wiring queries:
- Call `list_registered_queries` to discover real `query_id` values before
  binding kpi/table/chart widgets.
- If the data the user needs has no registered query, call `register_query`
  (name + SELECT sql, with {{name}} params if needed) and bind widgets to the
  returned id.

Be concise. After editing, briefly tell the user what changed (widgets, the
metric, the chart type) in one or two sentences. Use markdown.
"""


# ---------------------------------------------------------------------------
# Anthropic key / client resolution (reuses app.ai provider config)
# ---------------------------------------------------------------------------


def _resolve_anthropic_key() -> str | None:
    """Return the Anthropic API key the rest of the app would use, or None.

    Mirrors ``app.ai.provider.get_provider``'s resolution: settings field first,
    then the ``ANTHROPIC_API_KEY`` environment variable.  Never raises.
    """
    import os  # noqa: PLC0415

    try:
        from app.config import get_settings  # noqa: PLC0415

        val = getattr(get_settings(), "ANTHROPIC_API_KEY", None)
        if val:
            return str(val)
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("ANTHROPIC_API_KEY") or None


def _anthropic_client(api_key: str) -> Any:
    """Build an Anthropic SDK client (lazy import, same SDK as AnthropicProvider)."""
    import anthropic  # noqa: PLC0415

    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Turn accumulator
# ---------------------------------------------------------------------------


class _Turn:
    """Accumulates the assistant's text + tool calls across the streamed turn."""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []  # {id, name, input, output}
        self.spec: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


# ---------------------------------------------------------------------------
# Real-provider streaming loop
# ---------------------------------------------------------------------------


def _stream_real(
    client: Any,
    model: str,
    history: list[dict[str, Any]],
    turn: _Turn,
) -> Iterator[dict[str, Any]]:
    """Run the manual tool-use loop against Anthropic, yielding live events."""
    messages: list[dict[str, Any]] = list(history)
    tools = anthropic_tool_specs()

    for _step in range(_MAX_STEPS):
        assistant_blocks: list[dict[str, Any]] = []
        # Track in-progress tool_use blocks by content index.
        tool_inputs: dict[int, str] = {}
        tool_meta: dict[int, dict[str, Any]] = {}

        with client.messages.stream(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        ) as stream:
            for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        tool_meta[event.index] = {"id": block.id, "name": block.name}
                        tool_inputs[event.index] = ""
                elif etype == "content_block_delta":
                    delta = event.delta
                    if getattr(delta, "type", None) == "text_delta":
                        turn.text_parts.append(delta.text)
                        yield {"type": "token", "text": delta.text}
                    elif getattr(delta, "type", None) == "input_json_delta":
                        tool_inputs[event.index] += delta.partial_json

            final = stream.get_final_message()

        # Reconstruct assistant content blocks from the final message.
        tool_use_blocks: list[Any] = []
        for block in final.content:
            if block.type == "text":
                assistant_blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_blocks.append(
                    {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                )
                tool_use_blocks.append(block)

        messages.append({"role": "assistant", "content": assistant_blocks})

        if final.stop_reason != "tool_use" or not tool_use_blocks:
            return

        # Execute each tool call, emit events, and collect results.
        tool_results: list[dict[str, Any]] = []
        for block in tool_use_blocks:
            tool_input = block.input if isinstance(block.input, dict) else {}
            yield {"type": "tool_use", "id": block.id, "name": block.name, "input": tool_input}
            try:
                output, extra = execute_tool(block.name, tool_input)
            except Exception as exc:  # noqa: BLE001
                output, extra = {"error": str(exc)}, {}
            if extra.get("spec"):
                turn.spec = extra["spec"]
            turn.tool_calls.append(
                {"id": block.id, "name": block.name, "input": tool_input, "output": output}
            )
            yield {"type": "tool_result", "id": block.id, "output": output}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output),
                }
            )

        messages.append({"role": "user", "content": tool_results})

    # Exhausted steps without a natural stop — end the turn quietly.


# ---------------------------------------------------------------------------
# Offline fallback (no Anthropic key configured)
# ---------------------------------------------------------------------------


def _stream_offline(
    history: list[dict[str, Any]],
    turn: _Turn,
) -> Iterator[dict[str, Any]]:
    """Deterministic offline path emitting the same event shapes (no network).

    If the latest user message looks like a dashboard request it makes a real
    ``propose_dashboard_spec`` tool call (which runs fully offline via the
    NullProvider generator) so the editor still receives a spec.
    """
    last_user = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                last_user = content
            elif isinstance(content, list):
                last_user = " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                )
            break

    wants_dashboard = any(
        kw in last_user.lower()
        for kw in ("dashboard", "chart", "graph", "visual", "widget", "kpi", "table", "plot")
    )

    if wants_dashboard:
        tool_id = "toolu_offline_spec"
        args = {"instruction": last_user or "an overview dashboard"}
        yield {"type": "tool_use", "id": tool_id, "name": "propose_dashboard_spec", "input": args}
        try:
            output, extra = execute_tool("propose_dashboard_spec", args)
        except Exception as exc:  # noqa: BLE001
            output, extra = {"error": str(exc)}, {}
        if extra.get("spec"):
            turn.spec = extra["spec"]
        turn.tool_calls.append(
            {"id": tool_id, "name": "propose_dashboard_spec", "input": args, "output": output}
        )
        yield {"type": "tool_result", "id": tool_id, "output": output}

        widgets = output.get("widget_count", 0) if isinstance(output, dict) else 0
        reply = (
            f"I assembled a dashboard with **{widgets}** widget(s) — review it in the editor "
            "and let me know what to adjust."
        )
    else:
        reply = (
            "I'm Nubi's dashboard assistant. Ask me to build or change a dashboard "
            "(e.g. \"add a bar chart of revenue by month\") and I'll propose a spec the "
            "editor can apply."
        )

    for chunk in _tokenise(reply):
        turn.text_parts.append(chunk)
        yield {"type": "token", "text": chunk}


def _tokenise(text: str) -> list[str]:
    import re  # noqa: PLC0415

    return re.findall(r"\S+\s*", text) or ([text] if text else [])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stream_chat(
    history: list[dict[str, Any]],
    model: str,
) -> Iterator[tuple[dict[str, Any], _Turn]]:
    """Stream the assistant's turn for *history*, yielding ``(event, turn)``.

    *history* is the Anthropic-format message list (roles ``user`` / ``assistant``
    with string or block content).  The same mutable ``_Turn`` is yielded with
    every event; after iteration completes it holds the full assistant text, the
    list of tool calls, and any proposed dashboard spec — the route uses it to
    persist the turn and emit the terminal ``message`` event.
    """
    turn = _Turn()
    try:
        api_key = _resolve_anthropic_key()
        if api_key:
            client = _anthropic_client(api_key)
            for ev in _stream_real(client, model, history, turn):
                yield ev, turn
        else:
            for ev in _stream_offline(history, turn):
                yield ev, turn
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": str(exc)}, turn


__all__ = ["stream_chat"]
