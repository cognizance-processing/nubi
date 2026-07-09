"""Streaming agent loop via LiteLLM (tool use + token deltas).

This module owns the actual model call for the chat backend.  It runs the
agentic loop manually so it can stream token deltas AND surface tool_use /
tool_result events live, Cursor-style.

LiteLLM (in-process, NOT the proxy server) normalises the streaming wire
format to the OpenAI shape regardless of provider, which means this file
works with Anthropic, OpenAI, and Gemini models without any provider-specific
branching.  The heavy ``litellm`` import is deferred to the first real
request so it doesn't slow startup and doesn't break tests that mock the
provider.

Credential detection
--------------------
The loop checks for any of the well-known provider API-key environment
variables.  If none are found it falls through to the offline path, which
emits the same event shapes so the endpoint works in dev/CI without network
access.

The function ``_resolve_anthropic_key()`` is preserved by name because the
test suite patches it to force the offline path.  It returns a non-None value
when *any* LLM credential is configured.

Event shapes yielded by ``stream_chat`` (all JSON-serialisable dicts)::

    {"type": "token",       "text": str}
    {"type": "tool_use",    "id": str, "name": str, "input": dict}
    {"type": "tool_result", "id": str, "output": dict}
    {"type": "error",       "message": str}

The final assistant turn (full text + tool calls + any proposed spec) is NOT
emitted here — the route assembles it from ``stream_chat`` (below) so it can
persist it and send the terminal ``message`` event.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterator

from app.chat.tools import openai_tool_specs, execute_tool

# Namespaced host-tool names look like ``mcp__<label>__<tool>`` so they can never
# collide with a built-in tool name.
_MCP_PREFIX = "mcp__"

# Hard cap on agentic iterations so a misbehaving model can't loop forever.
_MAX_STEPS = 6
_MAX_TOKENS = 4096

# Aggregate per-turn token budget (sum across ALL steps in one turn).
# Prevents cost-DoS when max_steps × per-call tokens would burn unbounded spend.
# Default: 16000 tokens per turn. Override with NUBI_CHAT_TURN_TOKEN_BUDGET.
def _turn_token_budget() -> int:
    try:
        return int(os.environ.get("NUBI_CHAT_TURN_TOKEN_BUDGET", "16000"))
    except (ValueError, TypeError):
        return 16000

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
# Credential resolution (reuses app.ai provider config, multi-provider)
# ---------------------------------------------------------------------------


def _resolve_anthropic_key() -> str | None:
    """Return a credential string if any LLM provider is configured, else None.

    The name ``_resolve_anthropic_key`` is preserved for backwards-compat with
    tests that patch this function to force the offline path — returning None
    from it still triggers the offline fallback.

    Checks, in order: ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY,
    LITELLM_API_KEY (env vars), then falls back to app.config.get_settings()
    for ANTHROPIC_API_KEY (same resolution order as the original).
    """
    import os  # noqa: PLC0415

    # Check common provider env vars directly first (fastest path).
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "LITELLM_API_KEY"):
        val = os.environ.get(var)
        if val:
            return val

    # Fall back to app settings for ANTHROPIC_API_KEY (mirrors original behaviour).
    try:
        from app.config import get_settings  # noqa: PLC0415

        val = getattr(get_settings(), "ANTHROPIC_API_KEY", None)
        if val:
            return str(val)
    except Exception:  # noqa: BLE001
        pass

    return None


def _resolve_model_string(model_id: str) -> str:
    """Return the LiteLLM provider-prefixed model string for *model_id*.

    Delegates to :func:`app.chat.models.to_litellm_model` so the mapping
    lives in a single place.
    """
    from app.chat.models import to_litellm_model  # noqa: PLC0415

    return to_litellm_model(model_id)


# ---------------------------------------------------------------------------
# Turn accumulator
# ---------------------------------------------------------------------------


class _Turn:
    """Accumulates the assistant's text + tool calls across the streamed turn."""

    def __init__(self) -> None:
        self.text_parts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []  # {id, name, input, output}
        self.spec: dict[str, Any] | None = None
        #: Real USD cost of this turn, accumulated across all agentic steps.
        #: Fed to ``record_chat_cost`` so the per-user/per-org daily ceiling
        #: (app.ai.cost_ceiling) actually accumulates real spend instead of
        #: always recording 0.0 — see SECURITY note on _stream_real below.
        self.cost_usd: float = 0.0
        #: Total prompt+completion tokens across all agentic steps. Fed to the
        #: token-passthrough billing hook (app.ee.billing.token_billing via
        #: app.features.meter_ai_usage) so real-time wallet overage charging
        #: has an accurate token count to prorate against the free allowance —
        #: see app.routes.chat.chat_stream.
        self.total_tokens: int = 0

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


# ---------------------------------------------------------------------------
# Host-supplied MCP tools (Nubi as MCP client — see app.chat.mcp_client)
# ---------------------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run *coro* to completion from synchronous code.

    ``stream_chat`` is a sync generator driven in a worker thread (the route
    runs it via ``iterate_in_threadpool``), so there is normally no running
    event loop; ``asyncio.run`` is the fast path.  If a loop *is* running we
    off-load to a fresh thread so we never re-enter it.
    """
    import asyncio  # noqa: PLC0415

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    return asyncio.run(coro)


def _sanitize_label(label: str) -> str:
    """Reduce *label* to the tool-name-safe ``[a-zA-Z0-9_-]`` alphabet."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", label).strip("_")
    return cleaned or "srv"


def _build_mcp_tools(
    mcp_servers: list[Any],
) -> tuple[list[dict[str, Any]], dict[str, tuple[Any, str]]]:
    """Discover host MCP tools and return ``(openai_tool_specs, routing_map)``.

    Each host tool is exposed to the model under a namespaced name
    ``mcp__<label>__<tool>`` (label = server name or its index) so it can never
    collide with a built-in tool.  The routing map is keyed by that namespaced
    name and yields ``(server_spec, original_tool_name)`` so a tool call can be
    dispatched back to the right server.

    A server that fails to list its tools (SSRF block, network/protocol error)
    simply contributes nothing — it must never crash the turn.
    """
    from app.chat import mcp_client  # noqa: PLC0415

    tools: list[dict[str, Any]] = []
    routing: dict[str, tuple[Any, str]] = {}
    used: set[str] = set()

    for index, server in enumerate(mcp_servers):
        label = _sanitize_label(getattr(server, "name", None) or str(index))
        try:
            host_tools = _run_async(mcp_client.list_tools(server))
        except Exception:  # noqa: BLE001 — a bad server contributes no tools
            continue

        for t in host_tools:
            raw_name = str(t.get("name") or "").strip()
            if not raw_name:
                continue
            namespaced = f"{_MCP_PREFIX}{label}__{_sanitize_label(raw_name)}"[:64]
            # Guarantee uniqueness after truncation/sanitisation.
            base = namespaced
            n = 1
            while namespaced in used:
                namespaced = f"{base[:60]}_{n}"
                n += 1
            used.add(namespaced)

            routing[namespaced] = (server, raw_name)
            schema = t.get("inputSchema")
            if not isinstance(schema, dict):
                schema = {"type": "object"}
            description = str(t.get("description") or "")
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": namespaced,
                        "description": (
                            f"[Host tool via MCP server {label!r}] {description}"
                        ).strip(),
                        "parameters": schema,
                    },
                }
            )

    return tools, routing


# ---------------------------------------------------------------------------
# Real-provider streaming loop (LiteLLM / OpenAI-normalised chunks)
# ---------------------------------------------------------------------------


def _stream_real(
    model: str,
    history: list[dict[str, Any]],
    turn: _Turn,
    *,
    system: str | None = None,
    mcp_servers: list[Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Run the manual tool-use loop via LiteLLM, yielding live events.

    LiteLLM normalises all provider streaming to the OpenAI delta format:
    - ``delta.content`` (str) → text token
    - ``delta.tool_calls`` (list) → tool-call fragment (accumulated per index)

    After the stream ends, ``litellm.stream_chunk_builder`` assembles the full
    ModelResponse so we can read ``choices[0].message.tool_calls`` without
    manually stitching argument fragments ourselves.

    Messages follow the OpenAI convention: the system prompt is prepended as a
    ``{"role": "system", ...}`` message; tool results go in
    ``{"role": "tool", "tool_call_id": ..., "content": ...}`` messages.
    """
    import litellm  # noqa: PLC0415

    litellm.drop_params = True

    tools = openai_tool_specs()

    # Merge host-supplied MCP tools (namespaced) alongside the built-in tools.
    mcp_routing: dict[str, tuple[Any, str]] = {}
    if mcp_servers:
        mcp_tools, mcp_routing = _build_mcp_tools(mcp_servers)
        tools = [*tools, *mcp_tools]

    # The host system prompt is APPENDED to Nubi's base prompt — never replaces
    # it — so the assistant keeps its core behaviour plus the host's context.
    system_prompt = _SYSTEM_PROMPT
    if system and system.strip():
        system_prompt = f"{_SYSTEM_PROMPT}\n\n{system.strip()}"

    # Prepend system message (OpenAI convention — system lives in messages).
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *history,
    ]

    budget = _turn_token_budget()
    tokens_used = 0  # aggregate across all steps in this turn

    for _step in range(_MAX_STEPS):
        # Check aggregate budget before starting a new step.
        if tokens_used >= budget:
            yield {
                "type": "error",
                "message": (
                    f"Turn token budget ({budget}) reached after {tokens_used} tokens "
                    "across steps. Conversation truncated."
                ),
            }
            return

        chunks: list[Any] = []

        response_iter = litellm.completion(
            model=model,
            messages=messages,
            tools=tools,
            stream=True,
            max_tokens=_MAX_TOKENS,
        )

        for chunk in response_iter:
            chunks.append(chunk)
            try:
                delta = chunk.choices[0].delta
            except (AttributeError, IndexError):
                continue

            # Stream text tokens live.
            content = getattr(delta, "content", None)
            if content:
                turn.text_parts.append(content)
                yield {"type": "token", "text": content}

            # Tool-call deltas are accumulated via stream_chunk_builder below;
            # we do not need to manually stitch them here.

        # Re-assemble the full response from all collected chunks.
        rebuilt = litellm.stream_chunk_builder(chunks, messages=messages)
        try:
            message = rebuilt.choices[0].message
        except (AttributeError, IndexError):
            return

        # Accumulate tokens from this step's usage metadata (when available).
        # Also fed onto turn.total_tokens (distinct from the per-turn `budget`
        # local below) for the token-passthrough billing hook — see the
        # `total_tokens` docstring on _Turn.
        try:
            usage = rebuilt.usage
            if usage is not None:
                step_tokens = (
                    (getattr(usage, "total_tokens", None) or 0)
                    or (getattr(usage, "prompt_tokens", 0) or 0)
                    + (getattr(usage, "completion_tokens", 0) or 0)
                )
                tokens_used += step_tokens
                turn.total_tokens += int(step_tokens)
        except Exception:  # noqa: BLE001
            pass

        # SECURITY: accumulate the REAL USD cost of this step onto the turn so
        # the caller (app.routes.chat) can record actual spend into the
        # per-user/per-org cost ceiling (app.ai.cost_ceiling). Without this,
        # record_chat_cost would always be called with 0.0 and the configured
        # NUBI_CHAT_USER_DAILY_USD / NUBI_CHAT_ORG_DAILY_USD ceilings could
        # never trip against real (billed) traffic. Best-effort: an unknown
        # model / missing pricing table must not fail the turn.
        try:
            step_cost = float(litellm.completion_cost(completion_response=rebuilt) or 0.0)
            turn.cost_usd += step_cost
        except Exception:  # noqa: BLE001
            pass

        finish_reason = rebuilt.choices[0].finish_reason
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        wants_tools = finish_reason == "tool_calls" or bool(raw_tool_calls)

        # Build the assistant turn for message history.
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        if raw_tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in raw_tool_calls
            ]
        messages.append(assistant_msg)

        if not wants_tools:
            return

        # Execute each tool call, emit events, collect results.
        for tc in raw_tool_calls:
            tool_id: str = tc.id
            tool_name: str = tc.function.name
            raw_args: str = tc.function.arguments or "{}"
            try:
                tool_input: dict[str, Any] = json.loads(raw_args)
            except (json.JSONDecodeError, ValueError):
                tool_input = {}

            yield {"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input}

            if tool_name in mcp_routing:
                # Host-supplied MCP tool → dispatch to the host's MCP server.
                # call_tool never raises; any failure comes back as an error
                # string that is fed to the model as the tool result.
                server, original_name = mcp_routing[tool_name]
                from app.chat import mcp_client  # noqa: PLC0415

                result_str = _run_async(
                    mcp_client.call_tool(server, original_name, tool_input)
                )
                output, extra = {"content": result_str}, {}
            else:
                try:
                    output, extra = execute_tool(tool_name, tool_input)
                except Exception as exc:  # noqa: BLE001
                    output, extra = {"error": str(exc)}, {}

            if extra.get("spec"):
                turn.spec = extra["spec"]

            turn.tool_calls.append(
                {"id": tool_id, "name": tool_name, "input": tool_input, "output": output}
            )
            yield {"type": "tool_result", "id": tool_id, "output": output}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": json.dumps(output),
                }
            )

    # Exhausted steps without a natural stop — end the turn quietly.


# ---------------------------------------------------------------------------
# Offline fallback (no LLM credential configured)
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
    *,
    system: str | None = None,
    mcp_servers: list[Any] | None = None,
) -> Iterator[tuple[dict[str, Any], _Turn]]:
    """Stream the assistant's turn for *history*, yielding ``(event, turn)``.

    *history* is the OpenAI-format message list (roles ``user`` / ``assistant``
    with string or block content).  The *model* id is mapped to a LiteLLM
    provider-prefixed string via :func:`_resolve_model_string` before the call.

    Optional host inputs (backward compatible — both default to today's
    behaviour when absent):

    * *system* — extra host context APPENDED to Nubi's base system prompt
      (never replaces it).
    * *mcp_servers* — a list of :class:`app.chat.mcp_client.McpServerSpec`
      describing MCP servers hosted by the embedding application.  Their tools
      are discovered and offered to the model (namespaced ``mcp__…``) in
      addition to Nubi's built-in tools; when the model calls one it is routed
      to that host server.  Ignored when ``NUBI_CHAT_MCP_ENABLED`` is False.

    The same mutable ``_Turn`` is yielded with every event; after iteration
    completes it holds the full assistant text, the list of tool calls, and any
    proposed dashboard spec — the route uses it to persist the turn and emit
    the terminal ``message`` event.
    """
    turn = _Turn()

    # Master switch: ignore host MCP servers entirely when disabled in config.
    if mcp_servers:
        try:
            from app.config import get_settings  # noqa: PLC0415

            if not get_settings().NUBI_CHAT_MCP_ENABLED:
                mcp_servers = None
        except Exception:  # noqa: BLE001
            pass

    try:
        credential = _resolve_anthropic_key()
        if credential:
            litellm_model = _resolve_model_string(model)
            for ev in _stream_real(
                litellm_model, history, turn, system=system, mcp_servers=mcp_servers
            ):
                yield ev, turn
        else:
            for ev in _stream_offline(history, turn):
                yield ev, turn
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": str(exc)}, turn


__all__ = ["stream_chat"]
