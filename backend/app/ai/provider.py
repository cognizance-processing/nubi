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

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger("nubi.ai.provider")


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


#: Vendor id → LiteLLM model-string prefix builder.  Mirrors
#: ``app.chat.models.to_litellm_model`` (the editor-chat model mapping) so the
#: two call sites agree on how a bare model id becomes a LiteLLM routing
#: string.  We delegate to that function rather than duplicating the mapping.
def to_litellm_model(provider: str, bare_model: str) -> str:
    """Return the LiteLLM ``"provider/model"`` string for *bare_model*.

    Parameters
    ----------
    provider:
        One of ``"anthropic"``, ``"openai"``, ``"gemini"`` (a key of
        :data:`ALLOWED_MODELS`).
    bare_model:
        A bare model id from :data:`ALLOWED_MODELS`, e.g. ``"claude-opus-4-8"``,
        ``"gpt-4o"``, ``"gemini-1.5-flash"``.

    Returns
    -------
    str
        ``"anthropic/claude-opus-4-8"``, ``"gpt-4o"`` (OpenAI needs no
        prefix), ``"gemini/gemini-1.5-flash"``.
    """
    from app.chat.models import to_litellm_model as _to_litellm  # noqa: PLC0415

    if provider == "anthropic" and not bare_model.startswith("anthropic/"):
        return f"anthropic/{bare_model}"
    if provider == "gemini" and not bare_model.startswith("gemini/"):
        return f"gemini/{bare_model}"
    if provider == "openai":
        return bare_model
    # Fall back to the shared prefix-sniffing helper for anything else
    # (defensive — every id we pass through here comes from ALLOWED_MODELS).
    return _to_litellm(bare_model)


def vendor_for_litellm_model(model: str) -> str | None:
    """Best-effort reverse mapping: a LiteLLM model string → vendor id.

    Returns ``"anthropic"``, ``"openai"``, or ``"gemini"`` when recognisable,
    else ``None``.  Used to label a :class:`LiteLLMProvider` constructed from
    an operator-supplied ``LITELLM_MODEL`` string (rather than through the
    per-vendor auto-routing path) so BYO-key resolution and ``GET
    /ai/providers`` can still identify which vendor it talks to.
    """
    if model.startswith("anthropic/") or model.startswith("claude"):
        return "anthropic"
    if model.startswith("gemini/") or model.startswith("gemini"):
        return "gemini"
    if model.startswith("gpt") or model.startswith("o1") or model.startswith("o3"):
        return "openai"
    return None


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


# ---------------------------------------------------------------------------
# Token + cost accounting
# ---------------------------------------------------------------------------
#
# LiteLLM ships a per-model pricing table and a ``completion_cost`` helper, so
# every real completion can be priced in USD without us hardcoding rates.  We
# accumulate tokens + cost in two places:
#   * ``LiteLLMProvider.usage`` — per-instance (typically per-request) totals.
#   * ``_PROCESS_USAGE`` (read via ``get_process_usage()``) — process-wide totals
#     since start, used for the optional spend cap and for observability.
#
# NOTE on scope: ``_PROCESS_USAGE`` is in-memory and per-process — it does not
# survive a restart and is not shared across replicas.  It is meant for local
# cost visibility and a soft single-process budget guard, NOT multi-tenant
# billing.  For org-scoped budgets, rpm/tpm limits, and provider fallbacks
# across a fleet, run the LiteLLM proxy/Router (see docs/ai-and-mcp.md).


@dataclass
class LLMUsage:
    """Token + cost accounting for LLM completions."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def add(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float,
    ) -> None:
        """Fold one completion's tokens + cost into the running totals."""
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        self.cost_usd += cost_usd

    def as_dict(self) -> dict:
        """Return a JSON-serialisable snapshot (for API responses / logs)."""
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


#: Process-wide running totals across every LiteLLM completion since start.
_PROCESS_USAGE = LLMUsage()


def get_process_usage() -> LLMUsage:
    """Return the process-wide token + cost accumulator (since process start)."""
    return _PROCESS_USAGE


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


class LiteLLMProvider(LLMProvider):
    """Unified provider backed by the LiteLLM SDK (lazy import).

    LiteLLM is used as an in-process library — NOT the standalone proxy server.
    A single ``litellm.completion()`` call fronts 100+ model providers via a
    ``"provider/model"`` string (e.g. ``"anthropic/claude-opus-4-8"``,
    ``"gpt-4o"``, ``"gemini/gemini-1.5-pro"``), so this one class replaces the
    per-provider classes above when ``LLM_PROVIDER=litellm``.

    Pricing / usage / cost
    -----------------------
    After each call we read ``response.usage`` (token counts) and price it with
    ``litellm.completion_cost`` (LiteLLM's per-model pricing table — no rates
    hardcoded here).  Totals are folded into ``self.usage`` (per request) and
    ``_PROCESS_USAGE`` (process-wide), and emitted on the ``nubi.ai.provider``
    logger.

    Rate limiting / cost control
    ----------------------------
    * ``num_retries`` / ``timeout`` are passed through to LiteLLM, which retries
      429/5xx/timeout with exponential backoff (SDK-level resilience).
    * ``max_budget_usd`` is a soft per-process spend cap: once cumulative cost
      reaches it, further calls raise ``AppError("llm_budget_exceeded", 429)``
      BEFORE hitting the network.

    For hard rpm/tpm throttling, org-scoped budgets, and provider fallbacks
    across multiple processes, use the LiteLLM proxy/Router instead.

    Raises
    ------
    ImportError
        If the ``litellm`` package is not installed.
    AppError("model_not_allowed", 400)
        If a per-request ``model`` is outside the configured allowlist.
    AppError("llm_budget_exceeded", 429)
        If the per-process spend cap has been reached.
    """

    name = "litellm"

    def __init__(
        self,
        model: str,
        allowed: list[str] | None = None,
        *,
        api_key: str | None = None,
        num_retries: int | None = None,
        timeout: float | None = None,
        max_budget_usd: float | None = None,
        provider_label: str | None = None,
        is_byo: bool = False,
    ) -> None:
        # Do NOT import litellm or open a connection here.
        self._default_model = model
        # Allowlist = configured default + any explicit extras (order-preserving
        # dedup).  With no extras, only the default model is permitted per-request,
        # preserving the cost/safety gate that the native providers have.
        self._allowed = list(dict.fromkeys([model, *(allowed or [])]))
        self._api_key = api_key
        self._num_retries = num_retries
        self._timeout = timeout
        self._max_budget_usd = max_budget_usd
        #: Per-instance (typically per-request) token + cost totals.
        self.usage = LLMUsage()
        #: Vendor id ("anthropic" | "openai" | "gemini") this instance routes
        #: to, when constructed via the per-vendor auto-routing path
        #: (``get_provider()`` selecting/auto-detecting anthropic/openai/gemini,
        #: or ``resolve_provider_for_org`` swapping in an org's BYO key).  ``None``
        #: when constructed from a raw operator ``LITELLM_MODEL`` string — in
        #: that case ``.name`` stays the generic ``"litellm"`` for back-compat.
        self.vendor: str | None = provider_label
        if provider_label:
            self.name = provider_label
        #: True when ``._api_key`` is an org-supplied BYO key rather than the
        #: operator's own vendor key.  Consulted by the billing metering hook
        #: (``app.ee.billing.token_billing``) to skip charging the org's
        #: wallet — a BYO call's cost is the org's own vendor bill, not ours.
        self.is_byo: bool = is_byo

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        """Call LiteLLM and return the completion text, recording usage + cost.

        The ``litellm`` package is imported here (lazy) so the module is safe to
        import without it installed.  When *model* is supplied it is gated
        against the configured allowlist (see ``resolve_model``).
        """
        # Validate the request cheaply BEFORE importing the heavy SDK: an
        # out-of-allowlist model fails fast with model_not_allowed (400) even
        # when litellm is not installed.
        effective_model = resolve_model(model, self._default_model, self._allowed)

        try:
            import litellm  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "The 'litellm' package is required for LiteLLMProvider. "
                "Install it with: pip install litellm"
            ) from exc

        # ── Budget guard (pre-flight) ───────────────────────────────────────
        if (
            self._max_budget_usd is not None
            and _PROCESS_USAGE.cost_usd >= self._max_budget_usd
        ):
            from app.errors import AppError  # noqa: PLC0415

            raise AppError(
                "llm_budget_exceeded",
                (
                    f"LLM spend cap of ${self._max_budget_usd:.2f} reached "
                    f"(spent ${_PROCESS_USAGE.cost_usd:.4f} this process). "
                    "Raise LITELLM_MAX_BUDGET_USD or restart to reset."
                ),
                429,
            )

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": effective_model,
            "messages": messages,
            "max_tokens": 1024,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._num_retries is not None:
            kwargs["num_retries"] = self._num_retries
        if self._timeout is not None:
            kwargs["timeout"] = self._timeout

        # Silently drop params a given model rejects (e.g. max_tokens) instead of
        # erroring — keeps one call path across heterogeneous providers.
        litellm.drop_params = True

        response = litellm.completion(**kwargs)
        self._record(litellm, response, effective_model)

        return response.choices[0].message.content or ""

    def _record(self, litellm, response, model: str) -> None:
        """Fold one completion's tokens + USD cost into both usage accumulators."""
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(
            getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0
        )
        try:
            cost = float(litellm.completion_cost(completion_response=response) or 0.0)
        except Exception:  # pragma: no cover — unknown model / missing pricing
            # Pricing is best-effort: an unknown model id should not fail the call.
            cost = 0.0

        self.usage.add(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
        )
        _PROCESS_USAGE.add(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
        )
        logger.info(
            "litellm completion model=%s prompt_tokens=%d completion_tokens=%d "
            "cost_usd=%.6f process_cost_usd=%.6f",
            model,
            prompt_tokens,
            completion_tokens,
            cost,
            _PROCESS_USAGE.cost_usd,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def _parse_int(value: str | None) -> int | None:
    """Parse an int env value; return None for unset/empty/invalid."""
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_float(value: str | None) -> float | None:
    """Parse a float env value; return None for unset/empty/invalid."""
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _make_litellm(env) -> LLMProvider:
    """Build a ``LiteLLMProvider`` from ``LITELLM_*`` env, validating config.

    Reads (via the *env* reader passed by ``get_provider``):
      * ``LITELLM_MODEL`` (required) — default model string, e.g.
        ``"anthropic/claude-opus-4-8"`` or ``"gpt-4o"``.
      * ``LITELLM_ALLOWED_MODELS`` — comma-separated extra models a request may
        select via ``model``.  The default model is always allowed.
      * ``LITELLM_API_KEY`` — optional explicit key; usually omitted because
        LiteLLM reads provider keys (ANTHROPIC_API_KEY/OPENAI_API_KEY/…) itself.
      * ``LITELLM_NUM_RETRIES`` / ``LITELLM_TIMEOUT`` — SDK retry + timeout.
      * ``LITELLM_MAX_BUDGET_USD`` — soft per-process spend cap.
    """
    from app.errors import AppError  # noqa: PLC0415

    model = env("LITELLM_MODEL")
    if not model:
        raise AppError(
            "llm_not_configured",
            "LITELLM_MODEL is required when using LiteLLM "
            "(e.g. 'anthropic/claude-opus-4-8' or 'gpt-4o').",
            503,
        )
    allowed_raw = env("LITELLM_ALLOWED_MODELS")
    allowed = (
        [m.strip() for m in allowed_raw.split(",") if m.strip()] if allowed_raw else []
    )
    return LiteLLMProvider(
        model,
        allowed,
        api_key=env("LITELLM_API_KEY"),
        num_retries=_parse_int(env("LITELLM_NUM_RETRIES")),
        timeout=_parse_float(env("LITELLM_TIMEOUT")),
        max_budget_usd=_parse_float(env("LITELLM_MAX_BUDGET_USD")),
    )


#: Env var name holding the vendor API key, per ``ALLOWED_MODELS`` key.
_VENDOR_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def make_litellm_for_vendor(
    provider_name: str,
    api_key: str,
    *,
    is_byo: bool = False,
) -> LiteLLMProvider:
    """Build a :class:`LiteLLMProvider` routed to vendor *provider_name*.

    This is the PRIMARY construction path for the anthropic/openai/gemini
    vendors: it maps :data:`ALLOWED_MODELS` bare ids to LiteLLM
    ``"provider/model"`` strings (see :func:`to_litellm_model`), so a single
    ``LiteLLMProvider`` instance fronts the vendor with the SAME allowlist +
    default model the legacy native provider classes used, plus LiteLLM's
    per-model cost tracking.

    Parameters
    ----------
    provider_name:
        One of the :data:`ALLOWED_MODELS` keys (``"anthropic"``, ``"openai"``,
        ``"gemini"``).
    api_key:
        The vendor API key to use for this instance — either the operator's
        own key (``get_provider()``) or an org's BYO key
        (``app.ee.billing.org_ai_keys.resolve_provider_for_org``).
    is_byo:
        Tag the instance as using an org-supplied key (see
        :attr:`LiteLLMProvider.is_byo`).

    Raises
    ------
    AppError("llm_not_configured", 503)
        If *provider_name* is not a recognised vendor.
    """
    from app.errors import AppError  # noqa: PLC0415

    bare_models = ALLOWED_MODELS.get(provider_name)
    if not bare_models:
        raise AppError(
            "llm_not_configured",
            f"Unknown AI provider {provider_name!r}.",
            503,
        )
    litellm_models = [to_litellm_model(provider_name, m) for m in bare_models]
    return LiteLLMProvider(
        litellm_models[0],
        litellm_models[1:],
        api_key=api_key,
        provider_label=provider_name,
        is_byo=is_byo,
    )


def get_provider() -> LLMProvider:
    """Return the configured LLM provider, or NullProvider when none is set.

    LiteLLM is the PRIMARY completion path: the anthropic/openai/gemini
    vendors are ALWAYS routed through :class:`LiteLLMProvider` (via
    :func:`make_litellm_for_vendor`) rather than the legacy native provider
    classes, so every real completion gets LiteLLM's per-model pricing table
    and token accounting (``provider.usage`` — see
    ``app.ee.billing.token_billing``).  ``AnthropicProvider`` /
    ``OpenAIProvider`` / ``GeminiProvider`` remain defined for direct/explicit
    construction (and as a fallback shape for callers that don't need billing
    integration) but are no longer returned by this factory.

    Selection order
    ---------------
    1. If ``LLM_PROVIDER`` env var / settings field is set to ``"litellm"``,
       ``"anthropic"``, ``"openai"``, or ``"gemini"``, that provider is returned
       (or ``AppError("llm_not_configured", 503)`` if its required config is
       missing — an API key, or ``LITELLM_MODEL`` for litellm).
    2. Otherwise, auto-detect: ``LITELLM_MODEL`` → ``ANTHROPIC_API_KEY`` →
       ``OPENAI_API_KEY`` → ``GEMINI_API_KEY`` (first match wins).
    3. If nothing is configured, return ``NullProvider()`` (safe default).

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
        if provider_name == "litellm":
            return _make_litellm(_env)
        if provider_name in _VENDOR_KEY_ENV:
            key = _env(_VENDOR_KEY_ENV[provider_name])
            if not key:
                raise AppError(
                    "llm_not_configured",
                    f"{_VENDOR_KEY_ENV[provider_name]} is required when "
                    f"LLM_PROVIDER={provider_name}.",
                    503,
                )
            return make_litellm_for_vendor(provider_name, key)
        # Unknown provider name — fall through to auto-detect.

    # ── Auto-detect from available config (priority order) ──────────────────
    # LiteLLM first: when LITELLM_MODEL is set the operator has opted into the
    # unified router with an explicit model string, even if a raw vendor key is
    # also present (LiteLLM needs those keys itself).
    if _env("LITELLM_MODEL"):
        return _make_litellm(_env)

    for provider_name, env_key in _VENDOR_KEY_ENV.items():
        key = _env(env_key)
        if key:
            return make_litellm_for_vendor(provider_name, key)

    # ── Default: NullProvider ────────────────────────────────────────────────
    return NullProvider()


# ---------------------------------------------------------------------------
# Provider / model metadata (GET /ai/providers)
# ---------------------------------------------------------------------------


def list_provider_metadata(enabled_providers: list[str] | None = None) -> list[dict]:
    """Return ``[{id, display_name, models, configured}]`` for enabled providers.

    Parameters
    ----------
    enabled_providers:
        Provider ids to include (from ``settings.ai_enabled_providers`` /
        ``NUBI_AI_ENABLED_PROVIDERS``).  Unknown ids (not a key of
        :data:`ALLOWED_MODELS`) are silently skipped.  ``None`` defaults to
        every known provider.

    Returns
    -------
    list[dict]
        One entry per enabled provider::

            {
                "id": "anthropic",
                "display_name": "Anthropic (Claude)",
                "configured": true,   # operator vendor key present
                "models": [
                    {"id": "claude-opus-4-8", "display_name": "claude-opus-4-8",
                     "default": true},
                    ...
                ],
            }

        ``configured`` reflects whether the OPERATOR has a vendor key set for
        this provider (i.e. the deployment can serve metered calls for it) —
        it says nothing about any individual org's BYO key.
    """
    display_names = {
        "anthropic": "Anthropic (Claude)",
        "openai": "OpenAI",
        "gemini": "Google (Gemini)",
    }
    ids = enabled_providers if enabled_providers is not None else list(ALLOWED_MODELS)

    def _configured(provider_name: str) -> bool:
        env_key = _VENDOR_KEY_ENV.get(provider_name)
        if not env_key:
            return False
        try:
            from app.config import get_settings  # noqa: PLC0415

            settings = get_settings()
            if getattr(settings, env_key, None):
                return True
        except Exception:  # noqa: BLE001
            pass
        return bool(os.environ.get(env_key))

    out: list[dict] = []
    for provider_name in ids:
        bare_models = ALLOWED_MODELS.get(provider_name)
        if not bare_models:
            continue
        out.append(
            {
                "id": provider_name,
                "display_name": display_names.get(provider_name, provider_name),
                "configured": _configured(provider_name),
                "models": [
                    {
                        "id": m,
                        "display_name": m,
                        "default": i == 0,
                    }
                    for i, m in enumerate(bare_models)
                ],
            }
        )
    return out
