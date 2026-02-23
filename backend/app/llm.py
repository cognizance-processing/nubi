"""
Unified LLM abstraction layer.

Provides a single call_llm() interface that routes to Gemini, Anthropic,
OpenAI, or DeepSeek based on the chosen model, converting message/tool
formats and extracting token usage from each provider's response.
"""

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# API keys (read once at import time; missing keys just disable that provider)
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# ---------------------------------------------------------------------------
# Provider key map
# ---------------------------------------------------------------------------

_PROVIDER_KEYS = {
    "gemini": GEMINI_API_KEY,
    "anthropic": ANTHROPIC_API_KEY,
    "openai": OPENAI_API_KEY,
    "deepseek": DEEPSEEK_API_KEY,
}

DEFAULT_MODEL = "gemini-2.0-flash"

# Models that don't support tool/function calling
_NO_TOOLS = {"deepseek-reasoner", "o1-mini", "o1-preview"}

# Gemini model name prefixes to include (chat/generation models only)
_GEMINI_INCLUDE = ("gemini-2.", "gemini-1.5-pro", "gemini-1.5-flash")
# Gemini model name parts to exclude
_GEMINI_EXCLUDE = ("tts", "embedding", "aqa", "imagen", "veo", "live", "search")

# OpenAI model prefixes to include
_OPENAI_INCLUDE = ("gpt-4", "gpt-3.5", "o1", "o3", "o4")
_OPENAI_EXCLUDE = ("realtime", "audio", "search", "transcribe")

# Anthropic model prefixes to include
_ANTHROPIC_INCLUDE = ("claude-",)

# ---------------------------------------------------------------------------
# Dynamic model fetching with cache
# ---------------------------------------------------------------------------

import time as _time

_model_cache: Dict[str, Any] = {"models": [], "fetched_at": 0}
_CACHE_TTL = 3600  # 1 hour


def _make_display_name(model_id: str, provider: str) -> str:
    """Generate a clean display name from a model ID."""
    name = model_id
    # Strip date suffixes like -20250514 or :latest
    parts = name.split(":")
    name = parts[0]

    replacements = {
        "gemini-": "Gemini ",
        "claude-": "Claude ",
        "gpt-": "GPT-",
        "deepseek-": "DeepSeek ",
        "o1-": "O1-",
        "o3-": "O3-",
        "o4-": "O4-",
    }
    for prefix, replacement in replacements.items():
        if name.startswith(prefix):
            name = replacement + name[len(prefix):]
            break

    # Capitalize common terms
    for old, new in [("-pro", " Pro"), ("-flash", " Flash"), ("-lite", " Lite"),
                     ("-mini", " Mini"), ("-haiku", " Haiku"), ("-sonnet", " Sonnet"),
                     ("-opus", " Opus"), ("-turbo", " Turbo"), ("4o", "4o")]:
        name = name.replace(old, new)

    return name.strip()


def _fetch_gemini_models() -> List[Dict[str, Any]]:
    if not GEMINI_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": GEMINI_API_KEY, "pageSize": 200},
            timeout=10,
        )
        if not resp.ok:
            return []
        data = resp.json()
        models = []
        seen = set()
        for m in data.get("models", []):
            model_id = m.get("name", "").replace("models/", "")
            if not model_id:
                continue
            if not any(model_id.startswith(p) for p in _GEMINI_INCLUDE):
                continue
            if any(ex in model_id.lower() for ex in _GEMINI_EXCLUDE):
                continue
            methods = m.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue
            if model_id in seen:
                continue
            seen.add(model_id)
            models.append({
                "id": model_id,
                "provider": "gemini",
                "name": m.get("displayName") or _make_display_name(model_id, "gemini"),
                "supports_tools": True,
            })
        return models
    except Exception:
        return []


def _fetch_anthropic_models() -> List[Dict[str, Any]]:
    if not ANTHROPIC_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
            params={"limit": 100},
            timeout=10,
        )
        if not resp.ok:
            return []
        data = resp.json()
        models = []
        seen = set()
        for m in data.get("data", []):
            model_id = m.get("id", "")
            if not any(model_id.startswith(p) for p in _ANTHROPIC_INCLUDE):
                continue
            if model_id in seen:
                continue
            seen.add(model_id)
            models.append({
                "id": model_id,
                "provider": "anthropic",
                "name": m.get("display_name") or _make_display_name(model_id, "anthropic"),
                "supports_tools": True,
            })
        return models
    except Exception:
        return []


def _fetch_openai_models() -> List[Dict[str, Any]]:
    if not OPENAI_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            timeout=10,
        )
        if not resp.ok:
            return []
        data = resp.json()
        models = []
        seen = set()
        for m in data.get("data", []):
            model_id = m.get("id", "")
            if not any(model_id.startswith(p) for p in _OPENAI_INCLUDE):
                continue
            if any(ex in model_id.lower() for ex in _OPENAI_EXCLUDE):
                continue
            if model_id in seen:
                continue
            seen.add(model_id)
            models.append({
                "id": model_id,
                "provider": "openai",
                "name": _make_display_name(model_id, "openai"),
                "supports_tools": model_id not in _NO_TOOLS,
            })
        return models
    except Exception:
        return []


def _fetch_deepseek_models() -> List[Dict[str, Any]]:
    if not DEEPSEEK_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://api.deepseek.com/models",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            timeout=10,
        )
        if not resp.ok:
            return []
        data = resp.json()
        models = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            if not model_id:
                continue
            models.append({
                "id": model_id,
                "provider": "deepseek",
                "name": _make_display_name(model_id, "deepseek"),
                "supports_tools": model_id not in _NO_TOOLS,
            })
        return models
    except Exception:
        return []


def get_available_models() -> List[Dict[str, Any]]:
    """Fetch models from all configured providers (cached for 1 hour)."""
    now = _time.time()
    if _model_cache["models"] and (now - _model_cache["fetched_at"]) < _CACHE_TTL:
        return _model_cache["models"]

    all_models = []
    all_models.extend(_fetch_gemini_models())
    all_models.extend(_fetch_anthropic_models())
    all_models.extend(_fetch_openai_models())
    all_models.extend(_fetch_deepseek_models())

    if all_models:
        _model_cache["models"] = all_models
        _model_cache["fetched_at"] = now

    return all_models


def _infer_provider(model: str) -> Optional[str]:
    """Infer the provider from a model ID string."""
    if model.startswith("gemini-"):
        return "gemini"
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    if model.startswith("deepseek-"):
        return "deepseek"
    return None


def get_model_info(model: str) -> Dict[str, Any]:
    """Look up model info from cached models or infer it."""
    for m in get_available_models():
        if m["id"] == model:
            return m
    provider = _infer_provider(model)
    if provider:
        return {
            "id": model,
            "provider": provider,
            "name": _make_display_name(model, provider),
            "supports_tools": model not in _NO_TOOLS,
        }
    raise ValueError(f"Unknown model: {model}")


# ---------------------------------------------------------------------------
# Unified response / function-call types
# ---------------------------------------------------------------------------

@dataclass
class FunctionCall:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: Optional[str] = None
    function_calls: Optional[List[FunctionCall]] = None
    finish_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""


# ---------------------------------------------------------------------------
# Unified tool format  (Gemini-style function_declarations)
# We accept tools in this format and convert per-provider.
# ---------------------------------------------------------------------------

def _gemini_tools_to_openai(gemini_tools: List[dict]) -> List[dict]:
    """Convert Gemini function_declarations to OpenAI tools format."""
    openai_tools = []
    for tool_group in gemini_tools:
        for decl in tool_group.get("function_declarations", []):
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": decl["name"],
                    "description": decl.get("description", ""),
                    "parameters": decl.get("parameters", {"type": "object", "properties": {}}),
                },
            })
    return openai_tools


def _gemini_tools_to_anthropic(gemini_tools: List[dict]) -> List[dict]:
    """Convert Gemini function_declarations to Anthropic tools format."""
    anthropic_tools = []
    for tool_group in gemini_tools:
        for decl in tool_group.get("function_declarations", []):
            params = decl.get("parameters", {"type": "object", "properties": {}})
            anthropic_tools.append({
                "name": decl["name"],
                "description": decl.get("description", ""),
                "input_schema": params,
            })
    return anthropic_tools


# ---------------------------------------------------------------------------
# Message format converters
# ---------------------------------------------------------------------------
# Internal canonical format (OpenAI-style):
#   {"role": "user"/"assistant"/"tool", "content": "..."}
#   For tool results we pass:
#     {"role": "tool", "tool_call_id": "...", "content": "..."}
#   For assistant tool calls we tag:
#     {"role": "assistant", "tool_calls": [...]}
#
# Gemini format:
#   {"role": "user"/"model", "parts": [{"text": "..."}]}
#   Function calls: parts contain {"functionCall": {"name": ..., "args": ...}}
#   Function responses: {"role": "user", "parts": [{"functionResponse": {...}}]}

def _messages_to_gemini(messages: List[dict]) -> List[dict]:
    """Convert canonical messages to Gemini contents format."""
    contents = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "assistant":
            gem_role = "model"
        elif role == "tool":
            # Gemini expects function responses as user-role parts
            func_resp = {
                "functionResponse": {
                    "name": msg.get("tool_name", msg.get("name", "unknown")),
                    "response": {"result": _safe_json_loads(msg.get("content", "{}"))},
                }
            }
            # Merge with previous user turn if it was also function responses
            if contents and contents[-1]["role"] == "user" and any("functionResponse" in p for p in contents[-1]["parts"]):
                contents[-1]["parts"].append(func_resp)
                continue
            contents.append({"role": "user", "parts": [func_resp]})
            continue
        else:
            gem_role = "user"

        # Handle assistant messages that contain tool calls
        if role == "assistant" and msg.get("tool_calls"):
            parts = []
            for tc in msg["tool_calls"]:
                parts.append({
                    "functionCall": {
                        "name": tc["name"],
                        "args": tc.get("args", {}),
                    }
                })
            contents.append({"role": "model", "parts": parts})
            continue

        text = msg.get("content", "")
        if text:
            contents.append({"role": gem_role, "parts": [{"text": text}]})
    return contents


def _messages_to_openai(messages: List[dict], system_instruction: str = "") -> List[dict]:
    """Convert canonical messages to OpenAI chat format."""
    oai = []
    if system_instruction:
        oai.append({"role": "system", "content": system_instruction})
    for msg in messages:
        role = msg.get("role", "user")
        if role == "tool":
            oai.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", msg.get("name", "call")),
                "content": msg.get("content", ""),
            })
        elif role == "assistant" and msg.get("tool_calls"):
            oai_tool_calls = []
            for tc in msg["tool_calls"]:
                oai_tool_calls.append({
                    "id": tc.get("id", tc["name"]),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("args", {})),
                    },
                })
            oai.append({"role": "assistant", "content": msg.get("content") or None, "tool_calls": oai_tool_calls})
        else:
            oai.append({"role": role, "content": msg.get("content", "")})
    return oai


def _messages_to_anthropic(messages: List[dict]) -> List[dict]:
    """Convert canonical messages to Anthropic format."""
    anth = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "tool":
            # Anthropic tool results go in user messages
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", msg.get("name", "call")),
                "content": msg.get("content", ""),
            }
            if anth and anth[-1]["role"] == "user" and isinstance(anth[-1]["content"], list):
                anth[-1]["content"].append(tool_result_block)
            else:
                anth.append({"role": "user", "content": [tool_result_block]})
        elif role == "assistant" and msg.get("tool_calls"):
            content_blocks = []
            if msg.get("content"):
                content_blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg["tool_calls"]:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", tc["name"]),
                    "name": tc["name"],
                    "input": tc.get("args", {}),
                })
            anth.append({"role": "assistant", "content": content_blocks})
        elif role == "assistant":
            anth.append({"role": "assistant", "content": msg.get("content", "")})
        else:
            # user
            anth.append({"role": "user", "content": msg.get("content", "")})
    return anth


def _safe_json_loads(s):
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s)
    except Exception:
        return {"raw": s}


# ---------------------------------------------------------------------------
# Provider call implementations
# ---------------------------------------------------------------------------

async def _call_gemini(
    model: str,
    messages: List[dict],
    system_instruction: str,
    tools: Optional[List[dict]],
    temperature: float,
    max_tokens: Optional[int],
) -> LLMResponse:
    api_key = GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    contents = _messages_to_gemini(messages)

    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "responseMimeType": "text/plain"},
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    if tools:
        payload["tools"] = tools
    if max_tokens:
        payload["generationConfig"]["maxOutputTokens"] = max_tokens

    resp = await asyncio.to_thread(
        requests.post,
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        json=payload,
        timeout=120,
    )

    if not resp.ok:
        raise RuntimeError(f"Gemini API error ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    candidate = data.get("candidates", [{}])[0]
    finish = candidate.get("finishReason", "")
    parts = candidate.get("content", {}).get("parts", [])

    # Token usage
    usage_meta = data.get("usageMetadata", {})
    input_tok = usage_meta.get("promptTokenCount", 0)
    output_tok = usage_meta.get("candidatesTokenCount", 0)

    # Extract function calls
    func_calls = []
    text_parts = []
    for p in parts:
        if "functionCall" in p:
            fc = p["functionCall"]
            func_calls.append(FunctionCall(name=fc["name"], args=fc.get("args", {})))
        if "text" in p:
            text_parts.append(p["text"])

    return LLMResponse(
        text="".join(text_parts) if text_parts else None,
        function_calls=func_calls if func_calls else None,
        finish_reason=finish,
        input_tokens=input_tok,
        output_tokens=output_tok,
        model=model,
        provider="gemini",
    )


async def _call_anthropic(
    model: str,
    messages: List[dict],
    system_instruction: str,
    tools: Optional[List[dict]],
    temperature: float,
    max_tokens: Optional[int],
) -> LLMResponse:
    api_key = ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    url = "https://api.anthropic.com/v1/messages"
    anth_messages = _messages_to_anthropic(messages)
    anth_tools = _gemini_tools_to_anthropic(tools) if tools else None

    body: Dict[str, Any] = {
        "model": model,
        "messages": anth_messages,
        "max_tokens": max_tokens or 8192,
        "temperature": temperature,
    }
    if system_instruction:
        body["system"] = system_instruction
    if anth_tools:
        body["tools"] = anth_tools

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    resp = await asyncio.to_thread(
        requests.post, url, headers=headers, json=body, timeout=120,
    )

    if not resp.ok:
        raise RuntimeError(f"Anthropic API error ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    usage = data.get("usage", {})
    stop = data.get("stop_reason", "")

    text_parts = []
    func_calls = []
    for block in data.get("content", []):
        if block["type"] == "text":
            text_parts.append(block["text"])
        elif block["type"] == "tool_use":
            func_calls.append(FunctionCall(
                name=block["name"],
                args=block.get("input", {}),
            ))

    # Map Anthropic stop reason to a generic finish reason
    finish = "STOP"
    if stop == "tool_use":
        finish = "TOOL_CALLS"
    elif stop == "max_tokens":
        finish = "MAX_TOKENS"

    return LLMResponse(
        text="".join(text_parts) if text_parts else None,
        function_calls=func_calls if func_calls else None,
        finish_reason=finish,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        model=model,
        provider="anthropic",
    )


async def _call_openai_compat(
    model: str,
    messages: List[dict],
    system_instruction: str,
    tools: Optional[List[dict]],
    temperature: float,
    max_tokens: Optional[int],
    provider: str,
) -> LLMResponse:
    """Shared implementation for OpenAI and DeepSeek (compatible API)."""
    if provider == "deepseek":
        api_key = DEEPSEEK_API_KEY
        base_url = "https://api.deepseek.com/v1/chat/completions"
    else:
        api_key = OPENAI_API_KEY
        base_url = "https://api.openai.com/v1/chat/completions"

    if not api_key:
        raise ValueError(f"{provider.upper()}_API_KEY not configured")

    oai_messages = _messages_to_openai(messages, system_instruction)
    oai_tools = _gemini_tools_to_openai(tools) if tools else None

    body: Dict[str, Any] = {
        "model": model,
        "messages": oai_messages,
        "temperature": temperature,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    if oai_tools:
        if model not in _NO_TOOLS:
            body["tools"] = oai_tools

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    resp = await asyncio.to_thread(
        requests.post, base_url, headers=headers, json=body, timeout=120,
    )

    if not resp.ok:
        raise RuntimeError(f"{provider} API error ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    usage = data.get("usage", {})
    oai_finish = choice.get("finish_reason", "")

    text = msg.get("content")
    func_calls = []
    for tc in msg.get("tool_calls", []):
        fn = tc.get("function", {})
        args = fn.get("arguments", "{}")
        try:
            parsed_args = json.loads(args)
        except json.JSONDecodeError:
            parsed_args = {"raw": args}
        func_calls.append(FunctionCall(name=fn.get("name", ""), args=parsed_args))

    finish = "STOP"
    if oai_finish == "tool_calls":
        finish = "TOOL_CALLS"
    elif oai_finish == "length":
        finish = "MAX_TOKENS"

    return LLMResponse(
        text=text,
        function_calls=func_calls if func_calls else None,
        finish_reason=finish,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        model=model,
        provider=provider,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def call_llm(
    model: str,
    messages: List[dict],
    system_instruction: str = "",
    tools: Optional[List[dict]] = None,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> LLMResponse:
    """
    Call any supported LLM with a unified interface.

    Parameters
    ----------
    model : str          Model ID (fetched dynamically from provider APIs).
    messages : list      Canonical message list (OpenAI-style roles).
    system_instruction   System prompt.
    tools                Gemini-format tool declarations (converted per-provider).
    temperature          Sampling temperature.
    max_tokens           Max output tokens (provider default if None).

    Returns
    -------
    LLMResponse with text, function_calls, token counts, etc.
    """
    info = get_model_info(model)
    provider = info["provider"]

    # Strip tools if model doesn't support them
    if not info.get("supports_tools", True):
        tools = None

    if provider == "gemini":
        return await _call_gemini(model, messages, system_instruction, tools, temperature, max_tokens)
    elif provider == "anthropic":
        return await _call_anthropic(model, messages, system_instruction, tools, temperature, max_tokens)
    elif provider in ("openai", "deepseek"):
        return await _call_openai_compat(model, messages, system_instruction, tools, temperature, max_tokens, provider)
    else:
        raise ValueError(f"Unknown provider: {provider}")
