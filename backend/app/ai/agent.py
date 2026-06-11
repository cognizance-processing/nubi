"""AI agent with a tool-calling loop (M21-A).

Public API
----------
run_agent(messages, provider, claims, *, max_steps=8) -> dict
    Execute the agent loop:
    1. Ask the provider for the next action given the conversation + tool schemas.
    2. If the provider returns a tool call, execute it and append the observation.
    3. Repeat until the provider returns a final text reply or max_steps is reached.
    4. Return ``{reply: str, actions: list[dict]}``.

NullProvider path
-----------------
Since NullProvider has no tool-use protocol, the agent scripts a DETERMINISTIC
path based on the last user message content:

- Contains "chart" or "dashboard"
    → generate_sql → create_dashboard → final reply
- Contains "run" or "query"
    → generate_sql → run_query → final reply
- Default (any other message)
    → generate_sql → final reply

This scripted path ensures the test suite passes with no model/network access.

Real-provider path
------------------
A real provider (Anthropic, OpenAI, Gemini) drives a REAL tool-use loop.  The
base ``LLMProvider.complete()`` only returns text, so we define a simple,
text-based tool-call protocol (no native function-calling needed):

  * The system prompt lists the available tools (name + JSON input schema) and
    instructs the model to either CALL a tool by emitting a single JSON object::

        {"tool": "<tool name>", "arguments": { ... }}

    (optionally fenced in a ```json code block, optionally surrounded by prose),
    or to give its FINAL answer as plain text with NO JSON tool block.

  * Each turn we call ``provider.complete(conversation, system=...)``.  We parse
    the reply: if it contains a tool-call JSON object we ``execute_tool(name,
    arguments, claims)`` (claims threaded for RLS), append the observation to the
    conversation, and loop.  A plain-text reply with no tool block is the final
    answer and ends the loop.

  * The loop is bounded by ``max_steps`` tool calls.  If the cap is hit before a
    final text reply, we terminate gracefully and synthesise a reply from the
    observations gathered so far.

The return shape is IDENTICAL to the NullProvider path: ``{reply, actions}``.

Signature (locked — M22 codes against it)
-----------------------------------------
    run_agent(messages, provider, claims, *, max_steps=8) -> dict
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Iterator

from app.ai.provider import LLMProvider, NullProvider
from app.ai.tools import execute_tool, tool_schemas


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    """Extract the text of the most recent user message, or ''."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content.lower()
            if isinstance(content, list):
                # Handle Anthropic-style content blocks.
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "").lower()
    return ""


def _build_intent(last_user_text: str) -> str:
    """Map the last user message text to one of three scripted intents.

    Returns
    -------
    str
        One of ``"chart"``, ``"run"``, or ``"default"``.
    """
    if any(kw in last_user_text for kw in ("chart", "dashboard", "visuali", "graph", "plot")):
        return "chart"
    if any(kw in last_user_text for kw in ("run", "query", "execute", "fetch", "show", "list")):
        return "run"
    return "default"


def _extract_question(messages: list[dict[str, Any]]) -> str:
    """Extract a short question string from the last user message."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "")
    return "show me the data"


# ---------------------------------------------------------------------------
# Real-provider tool-call protocol (text-based)
# ---------------------------------------------------------------------------


def _tool_use_system_prompt() -> str:
    """Build the system prompt describing the tool-call protocol + tool catalog."""
    schemas = tool_schemas()
    lines = [
        "You are Nubi's analytics assistant. You can call tools to answer the "
        "user's request. Prefer GOVERNED metrics (list_metrics / query_metric) "
        "over hallucinated SQL when a metric fits the question.",
        "",
        "To call a tool, reply with ONE JSON object and nothing else:",
        '  {"tool": "<tool name>", "arguments": { ... }}',
        "",
        "When you are done and want to give the user your final answer, reply "
        "with plain text and NO JSON tool object.",
        "",
        "Available tools:",
    ]
    for s in schemas:
        lines.append(
            f"- {s['name']}: {s['description']} "
            f"input_schema={json.dumps(s['input_schema'])}"
        )
    return "\n".join(lines)


def _render_conversation(
    messages: list[dict[str, Any]],
    transcript: list[dict[str, Any]],
) -> str:
    """Render the conversation + tool transcript into a single prompt string.

    *messages* is the caller's history; *transcript* is the running list of
    assistant tool calls and their observations appended during the loop.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        parts.append(f"{role}: {content}")
    for entry in transcript:
        if entry.get("type") == "tool_call":
            parts.append(
                f"assistant: {json.dumps({'tool': entry['tool'], 'arguments': entry['arguments']})}"
            )
        elif entry.get("type") == "observation":
            parts.append(
                f"tool ({entry['tool']}): {json.dumps(entry['result'], default=str)[:4000]}"
            )
    parts.append("assistant:")
    return "\n".join(parts)


def _parse_tool_call(text: str) -> dict[str, Any] | None:
    """Parse a tool-call JSON object from *text*, tolerating fences/prose.

    Returns ``{"tool": str, "arguments": dict}`` when a well-formed tool call is
    found, else ``None`` (meaning the text is a final plain-text answer).
    """
    if not text:
        return None

    candidates: list[str] = []

    # 1. Fenced ```json ... ``` or ``` ... ``` blocks.
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        candidates.append(m.group(1).strip())

    # 2. The raw text itself (model may emit bare JSON).
    candidates.append(text.strip())

    # 3. The first balanced {...} span anywhere in the text.
    brace = _first_json_object(text)
    if brace is not None:
        candidates.append(brace)

    for cand in candidates:
        if not cand:
            continue
        try:
            obj = json.loads(cand)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
            args = obj.get("arguments")
            if not isinstance(args, dict):
                args = {}
            return {"tool": obj["tool"], "arguments": args}
    return None


def _first_json_object(text: str) -> str | None:
    """Return the first balanced ``{...}`` substring of *text*, or ``None``."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _run_real_provider_loop(
    messages: list[dict[str, Any]],
    provider: LLMProvider,
    claims: dict[str, Any],
    max_steps: int,
) -> dict[str, Any]:
    """Drive the real-provider tool-use loop. Returns ``{reply, actions}``.

    See the module docstring for the text-based tool-call protocol.  Every tool
    execution threads *claims* through ``execute_tool`` so RLS is enforced and
    scope is never widened.  Bounded by *max_steps* tool calls.
    """
    system = _tool_use_system_prompt()
    transcript: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    steps = 0
    final_reply: str | None = None
    # One extra completion budget beyond max_steps so the model can produce a
    # final text answer immediately after its last permitted tool call.
    while steps <= max_steps:
        prompt = _render_conversation(messages, transcript)
        try:
            reply_text = provider.complete(prompt, system=system)
        except Exception:  # noqa: BLE001 — provider failure → terminate gracefully.
            break

        call = _parse_tool_call(reply_text)
        if call is None:
            # Plain-text reply → final answer.
            final_reply = (reply_text or "").strip()
            break

        if steps >= max_steps:
            # Model still wants a tool but we've hit the cap — stop here.
            break

        tool_name = call["tool"]
        arguments = call["arguments"]
        try:
            result = execute_tool(tool_name, arguments, claims)
        except Exception as exc:  # noqa: BLE001 — surface tool errors to the model.
            result = {"error": {"code": "tool_error", "message": str(exc)}}

        transcript.append({"type": "tool_call", "tool": tool_name, "arguments": arguments})
        transcript.append({"type": "observation", "tool": tool_name, "result": result})
        actions.append({"tool": tool_name, "arguments": arguments, "result": result})
        steps += 1

    if final_reply is None or not final_reply:
        final_reply = _synthesise_reply(actions)

    return {"reply": final_reply, "actions": actions}


def _synthesise_reply(actions: list[dict[str, Any]]) -> str:
    """Compose a deterministic fallback reply from the gathered tool actions."""
    if not actions:
        return "I wasn't able to complete a tool call for your request."
    summary = ", ".join(a["tool"] for a in actions)
    last = actions[-1].get("result") or {}
    if isinstance(last, dict) and "row_count" in last:
        return f"I ran {summary} and the final step returned {last['row_count']} row(s)."
    return f"I ran the following tools for your request: {summary}."


# ---------------------------------------------------------------------------
# Scripted tool sequences for the NullProvider path
# ---------------------------------------------------------------------------


def _scripted_chart_sequence(
    question: str,
    claims: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """generate_sql → create_dashboard → reply.

    Returns
    -------
    tuple[list[dict], str]
        ``(actions, reply)``
    """
    actions: list[dict[str, Any]] = []

    # Step 1: generate_sql
    sql_result = execute_tool("generate_sql", {"question": question}, claims)
    actions.append(
        {
            "tool": "generate_sql",
            "arguments": {"question": question},
            "result": sql_result,
        }
    )

    # Step 2: create_dashboard
    dash_result = execute_tool("create_dashboard", {"question": question}, claims)
    actions.append(
        {
            "tool": "create_dashboard",
            "arguments": {"question": question},
            "result": {"spec": dash_result.get("spec", {}), "valid": dash_result.get("valid")},
        }
    )

    reply = (
        f"I generated a SQL query for your request and created a dashboard spec. "
        f"The SQL is: `{sql_result.get('sql', '')}`. "
        f"The dashboard has {len(dash_result.get('spec', {}).get('widgets', []))} widget(s)."
    )
    return actions, reply


def _scripted_run_sequence(
    question: str,
    claims: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """generate_sql → run_query → reply.

    Returns
    -------
    tuple[list[dict], str]
        ``(actions, reply)``
    """
    actions: list[dict[str, Any]] = []

    # Step 1: generate_sql
    sql_result = execute_tool("generate_sql", {"question": question}, claims)
    actions.append(
        {
            "tool": "generate_sql",
            "arguments": {"question": question},
            "result": sql_result,
        }
    )

    generated_sql: str = sql_result.get("sql", "SELECT 1")

    # Step 2: run_query with the generated SQL (if valid).
    if sql_result.get("valid", False):
        try:
            run_result = execute_tool("run_query", {"sql": generated_sql}, claims)
        except Exception as exc:  # noqa: BLE001
            run_result = {"rows": [], "row_count": 0, "columns": [], "error": str(exc)}
    else:
        run_result = {"rows": [], "row_count": 0, "columns": [], "error": "Invalid SQL"}

    actions.append(
        {
            "tool": "run_query",
            "arguments": {"sql": generated_sql},
            "result": {
                "row_count": run_result.get("row_count", 0),
                "columns": run_result.get("columns", []),
            },
        }
    )

    row_count = run_result.get("row_count", 0)
    reply = (
        f"I ran the query for your request. "
        f"The SQL was: `{generated_sql}`. "
        f"Returned {row_count} row(s)."
    )
    return actions, reply


def _scripted_default_sequence(
    question: str,
    claims: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    """generate_sql → reply.

    Returns
    -------
    tuple[list[dict], str]
        ``(actions, reply)``
    """
    actions: list[dict[str, Any]] = []

    sql_result = execute_tool("generate_sql", {"question": question}, claims)
    actions.append(
        {
            "tool": "generate_sql",
            "arguments": {"question": question},
            "result": sql_result,
        }
    )

    sql = sql_result.get("sql", "")
    valid = sql_result.get("valid", False)

    reply = (
        f"Here is a SQL query for your request: `{sql}`. "
        f"Validity: {'valid' if valid else 'may need review'}."
    )
    return actions, reply


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_agent(
    messages: list[dict[str, Any]],
    provider: LLMProvider,
    claims: dict[str, Any],
    *,
    max_steps: int = 8,
) -> dict[str, Any]:
    """Run the AI agent loop and return ``{reply, actions}``.

    Parameters
    ----------
    messages:
        Conversation history.  Each message is ``{"role": "user"|"assistant",
        "content": str}``.  The agent appends tool calls and observations
        internally but does NOT mutate the caller's list.
    provider:
        ``LLMProvider`` instance.  With ``NullProvider`` (or when the real
        provider does not yet support tool use) the agent uses a deterministic
        scripted tool sequence based on the user's intent.
    claims:
        Caller's auth claims (passed to every tool call — RLS enforced).
    max_steps:
        Maximum number of tool-call iterations before a forced final reply.
        Defaults to 8.

    Returns
    -------
    dict
        ``{"reply": str, "actions": list[dict]}``

        ``actions`` is a list of dicts, one per tool call::

            {
                "tool": "<tool name>",
                "arguments": { ... },
                "result": { ... }    # JSON-serialisable summary
            }
    """
    last_user_text = _last_user_message(messages)
    question = _extract_question(messages)
    intent = _build_intent(last_user_text)

    # ── NullProvider (or fallback) — deterministic scripted path ────────────
    if isinstance(provider, NullProvider):
        if intent == "chart":
            actions, reply = _scripted_chart_sequence(question, claims)
        elif intent == "run":
            actions, reply = _scripted_run_sequence(question, claims)
        else:
            actions, reply = _scripted_default_sequence(question, claims)
        # Enforce max_steps.
        actions = actions[:max_steps]
        return {"reply": reply, "actions": actions}

    # ── Real provider path — iterative tool-use loop ────────────────────────
    # The base LLMProvider.complete() returns text only, so we drive a
    # text-based tool-call protocol (see module docstring): the model emits a
    # JSON {"tool", "arguments"} object to call a tool, or plain text to finish.
    # Every tool execution threads ``claims`` for RLS; bounded by max_steps.
    return _run_real_provider_loop(messages, provider, claims, max_steps)


# ---------------------------------------------------------------------------
# Streaming agent loop (live tool events) — M21-B
# ---------------------------------------------------------------------------
#
# ``run_agent_stream`` drives the same deterministic plan as ``run_agent`` but
# *yields* events as each step happens, so the UI can render tool calls live
# (Claude-Code-style). Event shapes (all JSON-serialisable dicts):
#
#   {"type": "status",      "text": str}
#   {"type": "tool_start",  "id": str, "tool": str, "arguments": dict}
#   {"type": "tool_result", "id": str, "tool": str, "ok": bool, "result": dict}
#   {"type": "text",        "delta": str}
#   {"type": "done",        "reply": str, "actions": list[dict]}
#   {"type": "error",       "message": str}
#
# It is a *synchronous* generator; the route iterates it in a threadpool so the
# blocking tool calls / pacing sleeps never block the event loop.


def _summarise_result(tool: str, result: dict[str, Any]) -> dict[str, Any]:
    """Trim a raw tool result to a compact, UI-friendly payload."""
    if not isinstance(result, dict):
        return {"value": str(result)}

    if tool == "generate_sql":
        return {
            "sql": result.get("sql", ""),
            "valid": result.get("valid", False),
            "issues": result.get("issues", []),
            "tables": (result.get("grounding") or {}).get("relevant_tables", []),
        }
    if tool == "run_query":
        rows = result.get("rows", []) or []
        return {
            "row_count": result.get("row_count", len(rows)),
            "columns": result.get("columns", []),
            "rows": rows[:15],
            **({"error": result["error"]} if result.get("error") else {}),
        }
    if tool == "create_dashboard":
        spec = result.get("spec", {}) or {}
        widgets = spec.get("widgets", []) or []
        return {
            "title": spec.get("title") or spec.get("name") or "Dashboard",
            "widget_count": len(widgets),
            "widgets": [
                {"type": w.get("type") or w.get("kind") or "widget",
                 "title": w.get("title") or w.get("name") or ""}
                for w in widgets[:8]
            ],
            "valid": result.get("valid", False),
            "issues": result.get("issues", []),
        }
    # default — shallow copy, drop heavy nested blobs
    return {k: v for k, v in result.items() if k not in ("html",)}


def _tokenise_for_stream(text: str) -> list[str]:
    """Split text into word-ish chunks (keeping trailing whitespace) for paced streaming."""
    chunks = re.findall(r"\S+\s*", text)
    return chunks or ([text] if text else [])


def _final_reply_text(
    provider: LLMProvider,
    question: str,
    intent: str,
    actions: list[dict[str, Any]],
) -> str:
    """Compose the assistant's final natural-language reply.

    With a real provider we ask it to summarise what was done; with NullProvider
    (or on any failure) we fall back to a deterministic templated reply.
    """
    def _scripted() -> str:
        sql = ""
        rows = None
        widgets = None
        for a in actions:
            r = a.get("result") or {}
            if a["tool"] == "generate_sql":
                sql = r.get("sql", "")
            elif a["tool"] == "run_query":
                rows = r.get("row_count")
            elif a["tool"] == "create_dashboard":
                widgets = r.get("widget_count")
        parts: list[str] = []
        if sql:
            parts.append(f"I generated SQL for your request:\n\n```sql\n{sql}\n```")
        if rows is not None:
            parts.append(f"Running it returned **{rows}** row(s).")
        if widgets is not None:
            parts.append(f"I assembled a dashboard with **{widgets}** widget(s) — review it on the right.")
        if not parts:
            parts.append("Here's what I found for your request.")
        return "\n\n".join(parts)

    if isinstance(provider, NullProvider):
        return _scripted()

    try:
        context = "; ".join(
            f"{a['tool']} → {_summarise_result(a['tool'], a.get('result', {}))}"
            for a in actions
        )
        out = provider.complete(
            f"User asked: {question}\n\nTools run: {context}\n\n"
            "Write a concise, friendly reply (markdown) summarising what was done.",
            system="You are Nubi's analytics assistant. Be concise and helpful.",
        )
        return out.strip() or _scripted()
    except Exception:  # noqa: BLE001
        return _scripted()


def _stream_real_provider_loop(
    messages: list[dict[str, Any]],
    provider: LLMProvider,
    claims: dict[str, Any],
    *,
    max_steps: int,
    pace: float,
) -> Iterator[dict[str, Any]]:
    """Stream the real-provider tool-use loop as live events.

    Mirrors :func:`_run_real_provider_loop` but yields ``tool_start`` /
    ``tool_result`` / ``text`` / ``done`` events as each step happens. Every
    tool execution threads *claims* for RLS; bounded by *max_steps*.
    """
    system = _tool_use_system_prompt()
    transcript: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    steps = 0
    final_reply: str | None = None
    while steps <= max_steps:
        prompt = _render_conversation(messages, transcript)
        try:
            reply_text = provider.complete(prompt, system=system)
        except Exception:  # noqa: BLE001 — provider failure → terminate.
            break

        call = _parse_tool_call(reply_text)
        if call is None:
            final_reply = (reply_text or "").strip()
            break

        if steps >= max_steps:
            break

        tool_name = call["tool"]
        arguments = call["arguments"]
        tid = f"t{steps + 1}"
        yield {"type": "status", "text": f"Calling {tool_name}…"}
        yield {"type": "tool_start", "id": tid, "tool": tool_name, "arguments": arguments}
        time.sleep(pace * 6)
        try:
            result = execute_tool(tool_name, arguments, claims)
            ok = not (isinstance(result, dict) and "error" in result)
        except Exception as exc:  # noqa: BLE001
            result = {"error": {"code": "tool_error", "message": str(exc)}}
            ok = False
        summary = _summarise_result(tool_name, result)
        transcript.append({"type": "tool_call", "tool": tool_name, "arguments": arguments})
        transcript.append({"type": "observation", "tool": tool_name, "result": result})
        actions.append({"tool": tool_name, "arguments": arguments, "result": summary})
        yield {"type": "tool_result", "id": tid, "tool": tool_name, "ok": ok, "result": summary}
        steps += 1

    if final_reply is None or not final_reply:
        final_reply = _synthesise_reply(actions)

    yield {"type": "status", "text": "Writing response…"}
    for chunk in _tokenise_for_stream(final_reply):
        yield {"type": "text", "delta": chunk}
        time.sleep(pace)
    yield {"type": "done", "reply": final_reply, "actions": actions}


def run_agent_stream(
    messages: list[dict[str, Any]],
    provider: LLMProvider,
    claims: dict[str, Any],
    *,
    max_steps: int = 8,
    pace: float = 0.012,
) -> Iterator[dict[str, Any]]:
    """Run the agent loop, yielding live events. See block comment above for shapes."""
    question = _extract_question(messages)
    last_user_text = _last_user_message(messages)
    intent = _build_intent(last_user_text)

    yield {"type": "status", "text": "Understanding your request…"}

    # ── Real provider — iterative tool-use loop, streamed live ──────────────
    if not isinstance(provider, NullProvider):
        yield from _stream_real_provider_loop(
            messages, provider, claims, max_steps=max_steps, pace=pace
        )
        return

    actions: list[dict[str, Any]] = []
    step = 0

    # Step 1 — generate_sql (always).
    yield {"type": "status", "text": "Generating SQL…"}
    tid = f"t{step + 1}"
    args = {"question": question}
    yield {"type": "tool_start", "id": tid, "tool": "generate_sql", "arguments": args}
    time.sleep(pace * 6)
    try:
        sql_result = execute_tool("generate_sql", args, claims)
        ok = True
    except Exception as exc:  # noqa: BLE001
        sql_result = {"error": str(exc), "sql": "", "valid": False}
        ok = False
    sql_summary = _summarise_result("generate_sql", sql_result)
    actions.append({"tool": "generate_sql", "arguments": args, "result": sql_summary})
    yield {"type": "tool_result", "id": tid, "tool": "generate_sql", "ok": ok, "result": sql_summary}
    step += 1

    # Step 2 — run_query OR create_dashboard, by intent.
    if intent == "run" and step < max_steps:
        sql = sql_result.get("sql", "SELECT 1")
        yield {"type": "status", "text": "Running query…"}
        tid = f"t{step + 1}"
        yield {"type": "tool_start", "id": tid, "tool": "run_query", "arguments": {"sql": sql}}
        time.sleep(pace * 6)
        if sql_result.get("valid", False):
            try:
                rr = execute_tool("run_query", {"sql": sql}, claims)
                ok = True
            except Exception as exc:  # noqa: BLE001
                rr = {"rows": [], "row_count": 0, "columns": [], "error": str(exc)}
                ok = False
        else:
            rr = {"rows": [], "row_count": 0, "columns": [], "error": "Invalid SQL"}
            ok = False
        summary = _summarise_result("run_query", rr)
        actions.append({"tool": "run_query", "arguments": {"sql": sql}, "result": summary})
        yield {"type": "tool_result", "id": tid, "tool": "run_query", "ok": ok, "result": summary}
        step += 1

    elif intent == "chart" and step < max_steps:
        yield {"type": "status", "text": "Assembling dashboard…"}
        tid = f"t{step + 1}"
        args = {"question": question}
        yield {"type": "tool_start", "id": tid, "tool": "create_dashboard", "arguments": args}
        time.sleep(pace * 6)
        try:
            dr = execute_tool("create_dashboard", args, claims)
            ok = True
        except Exception as exc:  # noqa: BLE001
            dr = {"error": str(exc)}
            ok = False
        summary = _summarise_result("create_dashboard", dr)
        actions.append({"tool": "create_dashboard", "arguments": args, "result": summary})
        yield {"type": "tool_result", "id": tid, "tool": "create_dashboard", "ok": ok, "result": summary}
        step += 1

    # Final reply, streamed token-by-token.
    yield {"type": "status", "text": "Writing response…"}
    reply = _final_reply_text(provider, question, intent, actions)
    for chunk in _tokenise_for_stream(reply):
        yield {"type": "text", "delta": chunk}
        time.sleep(pace)

    yield {"type": "done", "reply": reply, "actions": actions}
