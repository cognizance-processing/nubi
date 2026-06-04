"""AI grounding layer for Nubi (M7-B).

Provides:
- LLMProvider abstraction with NullProvider (no network) and lazy real providers.
- Deterministic retrieval-based grounding over the lineage catalog.
- A factory that picks the configured provider (defaults to NullProvider).

Public API
----------
get_provider() -> LLMProvider
    Return the configured provider instance.  NullProvider when no API keys are set.

build_catalog() -> dict
    Build a catalog of tables/columns/queries from the registry + lineage graph.

ground(question, catalog) -> dict
    Deterministic keyword/token-overlap ranking over the catalog.

build_prompt(question, grounding) -> tuple[str, str]
    Return (system, user) prompt strings ready for LLM completion.
"""

from app.ai.grounding import build_catalog, build_prompt, ground
from app.ai.provider import LLMProvider, NullProvider, get_provider

__all__ = [
    "LLMProvider",
    "NullProvider",
    "get_provider",
    "build_catalog",
    "ground",
    "build_prompt",
]
