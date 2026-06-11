"""LLM provider abstraction for Nubi AI grounding (M7-B).

Design
------
- ``LLMProvider`` is an ABC that every provider implements.
- ``NullProvider`` is the default: no network calls, deterministic output.
  It echoes back a templated SQL suggestion that includes the grounded tables
  so tests are fully deterministic without any installed LLM SDK.
- Real providers (``AnthropicProvider``, ``OpenAIProvider``, ``GeminiProvider``)
  import their respective SDKs INSIDE ``__init__`` / ``complete()`` (lazy import).
  This means the app and its test suite run correctly even when the SDKs are NOT
  installed — NullProvider is returned by ``get_provider()`` when no API keys are
  configured.
- ``get_provider()`` factory reads ``settings.LLM_PROVIDER`` (optional) and the
  known API-key settings to pick a provider.  Falls back to ``NullProvider`` when
  no key is set.

Network safety
--------------
No network call is made at module import time or during provider construction.
The real providers only touch the network inside ``complete()``, which is never
called during tests (tests use NullProvider exclusively).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


# ---------------------------------------------------------------------------
# Per-provider model allowlists + resolver
# ---------------------------------------------------------------------------
#
# Per-request model selection is gated by a STRICT per-provider allowlist.  A
# client may ask for a specific model id via the ``model`` argument to
# ``complete()``, but only ids that appear in the provider's allowlist are
# honoured — anything else raises ``AppError("model_not_allowed", ..., 400)``.
# This prevents a caller from steering the request onto an arbitrary,
# unexpectedly expensive, or otherwise unintended model (cost + safety).
#
# The first id in each list is the provider's DEFAULT (used when no model is
# requested), matching the model the provider hardcoded before per-request
# routing existed.

#: Default + allowed model ids per provider.  Exposed for introspection.
ALLOWED_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-8",      # default — most capable Opus
        "claude-sonnet-4-6",    # balanced speed/intelligence
        "claude-haiku-4-5",     # fastest / cheapest
    ],
    "openai": [
        "gpt-4o",               # default
        "gpt-4o-mini",          # smaller / cheaper
    ],
    "gemini": [
        "gemini-1.5-flash",     # default
        "gemini-1.5-pro",       # higher quality
    ],
}


def resolve_model(
    requested: str | None,
    default: str,
    allowed: list[str] | set[str],
) -> str:
    """Resolve the effective model id, gating against an allowlist.

    Parameters
    ----------
    requested:
        The model id the caller asked for, or ``None``/empty when unspecified.
    default:
        The provider's default model id (used when *requested* is falsy).
    allowed:
        The set/list of model ids this provider permits.

    Returns
    -------
    str
        *requested* when it is non-empty and present in *allowed*; otherwise
        *default* (when *requested* is falsy).

    Raises
    ------
    AppError("model_not_allowed", 400)
        When *requested* is non-empty but not in *allowed*.  The message lists
        the permitted ids so the caller can correct the request.
    """
    if not requested:
        return default
    if requested in allowed:
        return requested
    from app.errors import AppError  # noqa: PLC0415 — avoid circular import at module top

    raise AppError(
        "model_not_allowed",
        (
            f"Model {requested!r} is not allowed. "
            f"Allowed models: {', '.join(allowed)}."
        ),
        400,
    )


class LLMProvider(ABC):
    """Abstract base for LLM completion providers.

    Implementations must override ``complete`` to return a string response.
    They must NOT make any network call at construction time.
    """

    #: Human-readable name for the provider (used in API responses).
    name: str = "unknown"

    @abstractmethod
    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Return a completion string for the given *prompt*.

        Parameters
        ----------
        prompt:
            The user-facing prompt to complete.
        system:
            Optional system/instruction message (provider-specific semantics).
        model:
            Optional per-request model id.  ``None`` (the default) uses the
            provider's default model — backward-compatible with existing
            ``complete(prompt, system)`` calls.  When supplied, real providers
            gate it against their allowlist (see ``resolve_model``) and may
            raise ``AppError("model_not_allowed", 400)``.

        Returns
        -------
        str
            The model's completion text.
        """


# ---------------------------------------------------------------------------
# NullProvider — deterministic, no network
# ---------------------------------------------------------------------------


class NullProvider(LLMProvider):
    """No-op provider that returns a deterministic templated response.

    Used as the default when no LLM API key is configured, and in all tests.
    Makes zero network calls — safe to use in CI / offline environments.

    The returned string echoes the question and mentions the grounded tables
    so that test assertions can verify the grounding pipeline without an LLM.
    """

    name = "null"

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Return a deterministic templated SQL suggestion.

        The output format is::

            [NullProvider] Would generate SQL for: <first 120 chars of prompt>
            Grounded tables: <tables extracted from prompt if any>

        Parameters
        ----------
        prompt:
            The user prompt (may contain grounded table snippets injected by
            ``build_prompt``).
        system:
            Ignored by NullProvider.
        model:
            Ignored by NullProvider — there is no network call, so there is no
            model to route to.  Accepted for signature compatibility.

        Returns
        -------
        str
            A deterministic string that never requires a network call.
        """
        # Extract a short excerpt of the prompt for the echo.
        excerpt = prompt[:120].replace("\n", " ").strip()

        # Attempt to extract tables mentioned in the prompt (lines that look
        # like "table name(col1, col2)").  This makes NullProvider output
        # useful for assertion checks in tests.
        grounded_tables: list[str] = []
        for line in prompt.splitlines():
            stripped = line.strip()
            if stripped.startswith("table ") and "(" in stripped:
                table_name = stripped[len("table "):].split("(")[0].strip()
                if table_name:
                    grounded_tables.append(table_name)

        tables_part = (
            ", ".join(grounded_tables) if grounded_tables else "(none detected)"
        )
        return (
            f"[NullProvider] Would generate SQL for: {excerpt}\n"
            f"Grounded tables: {tables_part}"
        )


# ---------------------------------------------------------------------------
# Lazy real providers
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider (lazy SDK import).

    Reads the API key from ``settings.ANTHROPIC_API_KEY`` or the environment
    variable ``ANTHROPIC_API_KEY``.  The ``anthropic`` package is imported
    only inside ``complete()`` so the app starts fine without it installed.

    Raises
    ------
    AppError("llm_not_configured", 503)
        If the API key is missing at call time.
    ImportError
        If the ``anthropic`` package is not installed.
    """

    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        # Store key — do NOT import anthropic or open any connection here.
        self._api_key = api_key

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Call Anthropic Claude and return the completion text.

        The ``anthropic`` SDK is imported here (lazy) so the module is safe to
        import without the package installed.  When *model* is supplied it is
        gated against the Anthropic allowlist; an unlisted id raises
        ``AppError("model_not_allowed", 400)``.
        """
        try:
            import anthropic  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required for AnthropicProvider. "
                "Install it with: pip install anthropic"
            ) from exc

        allowed = ALLOWED_MODELS["anthropic"]
        effective_model = resolve_model(model, allowed[0], allowed)

        client = anthropic.Anthropic(api_key=self._api_key)
        messages = [{"role": "user", "content": prompt}]
        kwargs: dict = {
            "model": effective_model,
            "max_tokens": 1024,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = client.messages.create(**kwargs)
        return response.content[0].text


class OpenAIProvider(LLMProvider):
    """OpenAI ChatGPT provider (lazy SDK import).

    Reads the API key from ``settings.OPENAI_API_KEY`` or the environment.
    The ``openai`` package is imported only inside ``complete()``.

    Raises
    ------
    AppError("llm_not_configured", 503)
        If the API key is missing at call time.
    ImportError
        If the ``openai`` package is not installed.
    """

    name = "openai"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Call OpenAI ChatGPT and return the completion text.

        When *model* is supplied it is gated against the OpenAI allowlist; an
        unlisted id raises ``AppError("model_not_allowed", 400)``.
        """
        try:
            from openai import OpenAI  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'openai' package is required for OpenAIProvider. "
                "Install it with: pip install openai"
            ) from exc

        allowed = ALLOWED_MODELS["openai"]
        effective_model = resolve_model(model, allowed[0], allowed)

        client = OpenAI(api_key=self._api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=effective_model,
            messages=messages,
            max_tokens=1024,
        )
        return response.choices[0].message.content or ""


class GeminiProvider(LLMProvider):
    """Google Gemini provider (lazy SDK import).

    Reads the API key from ``settings.GEMINI_API_KEY`` or the environment.
    The ``google-generativeai`` package is imported only inside ``complete()``.

    Raises
    ------
    AppError("llm_not_configured", 503)
        If the API key is missing at call time.
    ImportError
        If the ``google-generativeai`` package is not installed.
    """

    name = "gemini"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Call Google Gemini and return the completion text.

        When *model* is supplied it is gated against the Gemini allowlist; an
        unlisted id raises ``AppError("model_not_allowed", 400)``.
        """
        try:
            import google.generativeai as genai  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'google-generativeai' package is required for GeminiProvider. "
                "Install it with: pip install google-generativeai"
            ) from exc

        allowed = ALLOWED_MODELS["gemini"]
        effective_model = resolve_model(model, allowed[0], allowed)

        genai.configure(api_key=self._api_key)
        gen_model = genai.GenerativeModel(effective_model)

        full_prompt = prompt
        if system:
            full_prompt = f"{system}\n\n{prompt}"

        response = gen_model.generate_content(full_prompt)
        return response.text


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_provider() -> LLMProvider:
    """Return the configured LLM provider, or NullProvider when none is set.

    Selection order
    ---------------
    1. If ``LLM_PROVIDER`` env var / settings field is set to ``"anthropic"``,
       ``"openai"``, or ``"gemini"``, that provider is returned (or
       ``AppError("llm_not_configured", 503)`` if the corresponding API key is
       missing).
    2. Otherwise, scan for API keys in priority order:
       ``ANTHROPIC_API_KEY`` → ``OPENAI_API_KEY`` → ``GEMINI_API_KEY``.
    3. If no key is found, return ``NullProvider()`` (safe default).

    No network call is made by this function.  Provider construction is also
    free of network I/O.

    Returns
    -------
    LLMProvider
        The selected provider instance.
    """
    from app.errors import AppError  # noqa: PLC0415 — avoid circular import at module top

    # Try to read settings without crashing if they don't have LLM fields.
    # We read from os.environ directly so this function works in tests that
    # do not configure these keys (they remain absent → NullProvider).
    def _env(key: str) -> str | None:
        """Read from environment, tolerating absent settings fields."""
        # First try settings (catches pydantic-settings defaults / .env values).
        try:
            from app.config import get_settings  # noqa: PLC0415
            settings = get_settings()
            val = getattr(settings, key, None)
            if val:
                return str(val)
        except Exception:
            pass
        # Fallback: raw os.environ.
        return os.environ.get(key) or None

    # ── Explicit provider selection ─────────────────────────────────────────
    explicit = _env("LLM_PROVIDER")
    if explicit:
        provider_name = explicit.lower()
        if provider_name == "anthropic":
            key = _env("ANTHROPIC_API_KEY")
            if not key:
                raise AppError(
                    "llm_not_configured",
                    "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic.",
                    503,
                )
            return AnthropicProvider(key)
        if provider_name == "openai":
            key = _env("OPENAI_API_KEY")
            if not key:
                raise AppError(
                    "llm_not_configured",
                    "OPENAI_API_KEY is required when LLM_PROVIDER=openai.",
                    503,
                )
            return OpenAIProvider(key)
        if provider_name == "gemini":
            key = _env("GEMINI_API_KEY")
            if not key:
                raise AppError(
                    "llm_not_configured",
                    "GEMINI_API_KEY is required when LLM_PROVIDER=gemini.",
                    503,
                )
            return GeminiProvider(key)
        # Unknown provider name — fall through to auto-detect.

    # ── Auto-detect from available API keys (priority order) ────────────────
    anthropic_key = _env("ANTHROPIC_API_KEY")
    if anthropic_key:
        return AnthropicProvider(anthropic_key)

    openai_key = _env("OPENAI_API_KEY")
    if openai_key:
        return OpenAIProvider(openai_key)

    gemini_key = _env("GEMINI_API_KEY")
    if gemini_key:
        return GeminiProvider(gemini_key)

    # ── Default: NullProvider ────────────────────────────────────────────────
    return NullProvider()
