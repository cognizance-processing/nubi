"""AI grounding endpoints (M7-B + M8-C + EDITOR-2A).

Endpoints
---------
POST /ai/ask
    Accepts ``{question: str}``, runs the deterministic grounding pipeline,
    calls the configured LLM provider (defaults to NullProvider — no network),
    and returns the grounding context plus a SQL suggestion.

    Requires a valid first-party Bearer token (``current_user`` dependency).

POST /ai/dashboard
    Accepts ``{question: str}``, runs the grounding pipeline, generates a
    canonical DashboardSpec (EDITOR-2A format) and its compiled HTML.

    With NullProvider (the default) the response is fully deterministic.

GET /ai/dashboard/schema
    Returns the JSON Schema for DashboardSpec.  Used by the frontend editor
    and the LLM authoring pipeline to know the exact spec format.

Response shape (/ai/ask)
------------------------
::

    {
        "grounding": {
            "relevant_tables": [...],
            "relevant_columns": [{"table": "...", "column": "..."}, ...],
            "related_queries": [...],
            "snippets": [...]
        },
        "suggestion": "<SQL string from provider>",
        "provider": "<provider name>"
    }

Response shape (/ai/dashboard)
-------------------------------
::

    {
        "spec": { ...DashboardSpec dict... },
        "html": "<div class='nubi-dashboard'>...</div>",
        "grounding": { ... },
        "provider": "<provider name>",
        "valid": true,
        "issues": []
    }

With NullProvider (the default when no API key is configured) both responses are
fully deterministic and require no network access.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends
from pydantic import BaseModel

from app.ai.grounding import build_catalog, build_prompt, ground
from app.ai.provider import get_provider
from app.auth.deps import current_user
from app.routes import api_router


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    """Request body for POST /ai/ask."""

    question: str


class AskResponse(BaseModel):
    """Response body for POST /ai/ask."""

    grounding: dict[str, Any]
    suggestion: str
    provider: str


class DashboardRequest(BaseModel):
    """Request body for POST /ai/dashboard."""

    question: str


class DashboardResponse(BaseModel):
    """Response body for POST /ai/dashboard."""

    spec: dict[str, Any]
    html: str
    grounding: dict[str, Any]
    provider: str
    valid: bool
    issues: list[str]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@api_router.post("/ai/ask", response_model=AskResponse, tags=["ai"])
async def ask(
    body: AskRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> AskResponse:
    """Generate a grounded SQL suggestion for *question*.

    Pipeline
    --------
    1. Build the catalog from the live query registry + lineage graph.
    2. Run deterministic grounding (token-overlap scoring) to find the most
       relevant tables and columns for the question.
    3. Construct a grounded prompt (system + user) from the grounding context.
    4. Call the configured LLM provider.  With ``NullProvider`` (the default
       when no API key is set) this is a pure in-memory operation — no network.
    5. Return the grounding context + suggestion + provider name.

    Parameters
    ----------
    body:
        ``{question: str}`` — the natural-language question to answer.
    _user:
        Injected by ``current_user`` dependency.  Not used in the response but
        ensures the endpoint requires a valid authenticated session.

    Returns
    -------
    AskResponse
        ``{grounding, suggestion, provider}``

    Raises
    ------
    AppError("unauthorized", 401)
        If no valid Bearer token is provided.
    AppError("llm_not_configured", 503)
        If ``LLM_PROVIDER`` is explicitly set but the corresponding API key is
        absent.
    """
    catalog = build_catalog()
    grounding = ground(body.question, catalog)
    provider = get_provider()
    system_prompt, user_prompt = build_prompt(body.question, grounding)
    suggestion = provider.complete(user_prompt, system=system_prompt)

    return AskResponse(
        grounding=grounding,
        suggestion=suggestion,
        provider=provider.name,
    )


# ---------------------------------------------------------------------------
# POST /ai/dashboard
# ---------------------------------------------------------------------------


@api_router.post("/ai/dashboard", response_model=DashboardResponse, tags=["ai"])
async def create_dashboard(
    body: DashboardRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> DashboardResponse:
    """Generate a grounded DashboardSpec + HTML for *question* (EDITOR-2A).

    Pipeline
    --------
    1. Build the catalog from the live query registry + lineage graph.
    2. Run deterministic grounding to find relevant tables/columns/queries.
    3. Call ``generate_dashboard_spec`` — with NullProvider (the default) this
       is a pure in-memory operation that produces a canonical DashboardSpec
       referencing REAL registered query_ids and REAL column names.
    4. Compile the spec to HTML with ``spec_to_html``.
    5. Run ``validate_dashboard_html`` as a server-side sanity check.
    6. Return the spec dict, HTML, grounding context, provider name, and
       validation result.

    Parameters
    ----------
    body:
        ``{question: str}`` — natural-language description of the dashboard.
    _user:
        Injected by ``current_user``; ensures the endpoint requires auth.

    Returns
    -------
    DashboardResponse
        ``{spec, html, grounding, provider, valid, issues}``

    Raises
    ------
    AppError("unauthorized", 401)
        If no valid Bearer token is provided.
    """
    from app.ai.dashboard import generate_dashboard_spec, validate_dashboard_html  # noqa: PLC0415
    from app.dashboards.spec import spec_to_html  # noqa: PLC0415

    catalog = build_catalog()
    provider = get_provider()
    spec = generate_dashboard_spec(body.question, catalog, provider)
    html_output = spec_to_html(spec)
    ok, issues = validate_dashboard_html(html_output)

    # Re-run ground() to include grounding in the response (generate already
    # ran it internally; we run it again here for the response body — cheap,
    # deterministic, no network).
    grounding = ground(body.question, catalog)

    return DashboardResponse(
        spec=spec.model_dump(),
        html=html_output,
        grounding=grounding,
        provider=provider.name,
        valid=ok,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# GET /ai/dashboard/schema
# ---------------------------------------------------------------------------


@api_router.get("/ai/dashboard/schema", tags=["ai"])
async def dashboard_schema(
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the JSON Schema for the canonical DashboardSpec format.

    This endpoint exposes the spec schema so that:
    - The frontend drag-and-drop editor knows the exact format to read/write.
    - The LLM authoring pipeline can be grounded with the schema.
    - External tools (e.g. MCP clients) can validate specs before submitting.

    Requires a valid Bearer token (same auth as other AI endpoints).

    Returns
    -------
    dict
        JSON Schema dict describing DashboardSpec (Pydantic v2 output).

    Raises
    ------
    AppError("unauthorized", 401)
        If no valid Bearer token is provided.
    """
    from app.dashboards.spec import spec_json_schema  # noqa: PLC0415

    return spec_json_schema()
