"""AI grounding endpoints (M7-B + M8-C + EDITOR-2A + M21-A).

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

POST /ai/chat
    Agentic chat endpoint (M21-A).  Accepts ``{messages, board_id?}``, resolves
    the LLM provider (default NullProvider) and the caller's claims from the
    Bearer token, runs ``agent.run_agent``, and returns ``{reply, actions}``.

    With NullProvider the response is deterministic (scripted tool sequence
    based on intent extracted from the last user message).

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

import logging
import re
from typing import Any

from fastapi import Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.ai.grounding import build_catalog, build_prompt, ground
from app.ai.provider import get_provider
from app.auth.deps import current_user
from app.compute.metering import record_usage
from app.features import enforce_quota
from app.routes import api_router

logger = logging.getLogger("nubi.ai")


# ---------------------------------------------------------------------------
# Billing — AI calls are a metered dimension (tiers.max_ai_calls_per_month)
# ---------------------------------------------------------------------------


async def _resolve_org_id(user: dict[str, Any]) -> str | None:
    """Best-effort org resolution for billing attribution (never raises)."""
    try:
        from app.repos.provider import get_repo  # noqa: PLC0415
        from app.routes._org import get_user_org  # noqa: PLC0415

        return await get_user_org(str(user["id"]), get_repo())
    except Exception:  # noqa: BLE001 — attribution is best-effort, never a 500
        logger.warning(
            "ai: could not resolve org for user=%s — "
            "AI call will be metered without org attribution",
            user.get("id"),
        )
        return None


async def _enforce_ai_quota(user: dict[str, Any]) -> str | None:
    """Resolve the caller's org and enforce the ai_calls quota.

    Returns the org_id for subsequent metering.  Raises
    ``AppError("quota_exceeded", …, 402)`` when the EE-registered quota
    checker denies (e.g. FREE tier: 0 AI calls, no overage billing).
    A no-op allow in OSS builds (no checker registered).
    """
    org_id = await _resolve_org_id(user)
    await enforce_quota(org_id, "ai_calls", amount=1.0)
    return org_id


async def _record_ai_call(user: dict[str, Any], org_id: str | None, *, endpoint: str) -> None:
    """Record one ai_call usage event (kind='ai_call', units=1)."""
    await record_usage(
        kind="ai_call",
        user_id=str(user.get("id", "")),
        org_id=org_id,
        units=1.0,
        tier=endpoint,
    )


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
    org_id = await _enforce_ai_quota(_user)

    catalog = build_catalog()
    grounding = ground(body.question, catalog)
    provider = get_provider()
    system_prompt, user_prompt = build_prompt(body.question, grounding)
    suggestion = provider.complete(user_prompt, system=system_prompt)

    await _record_ai_call(_user, org_id, endpoint="ai_ask")

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

    org_id = await _enforce_ai_quota(_user)

    catalog = build_catalog()
    provider = get_provider()
    spec = generate_dashboard_spec(body.question, catalog, provider)
    html_output = spec_to_html(spec)
    ok, issues = validate_dashboard_html(html_output)

    # Re-run ground() to include grounding in the response (generate already
    # ran it internally; we run it again here for the response body — cheap,
    # deterministic, no network).
    grounding = ground(body.question, catalog)

    await _record_ai_call(_user, org_id, endpoint="ai_dashboard")

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


# ---------------------------------------------------------------------------
# POST /ai/chat — agentic chat with tool registry (M21-A)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    """A single conversation message."""

    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    """Request body for POST /ai/chat."""

    messages: list[ChatMessage]
    board_id: str | None = None
    model: str | None = None
    """Optional model identifier to route the request to a specific LLM model.

    When provided this value is echoed back in the response as ``model`` so
    the frontend can confirm which model was used.  Provider-level model
    routing is deferred to a future milestone.

    TODO: thread ``model`` through to the provider/agent once providers
    expose per-request model selection (e.g. AnthropicProvider.complete
    accepts a ``model`` kwarg).
    """


class ChatAction(BaseModel):
    """A single tool call recorded by the agent."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]


class ChatResponse(BaseModel):
    """Response body for POST /ai/chat."""

    reply: str
    actions: list[dict[str, Any]]
    model: str | None = None
    """The model that was used (or requested).  Echoes ``ChatRequest.model``
    when supplied; ``None`` when the caller did not specify a model.

    TODO: once provider-level model routing is wired in, this field will
    reflect the model that actually processed the request.
    """


@api_router.post("/ai/chat", response_model=ChatResponse, tags=["ai"])
async def ai_chat(
    body: ChatRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> ChatResponse:
    """Run the agentic AI chat loop and return a reply + the tool actions taken.

    Pipeline
    --------
    1. Resolve the LLM provider (default NullProvider when no API key is set).
    2. Build caller claims from the authenticated user (first-party scope).
    3. Call ``run_agent(messages, provider, claims, max_steps=8)``.
       - With NullProvider the agent follows a deterministic scripted path.
       - With a real provider the agent calls the tool registry in a loop.
    4. Return ``{reply, actions}`` where ``actions`` records every tool call.

    Parameters
    ----------
    body:
        ``{messages: [{role, content}, ...], board_id?: str}``
    _user:
        Injected by ``current_user``; ensures the endpoint requires auth.

    Returns
    -------
    ChatResponse
        ``{reply: str, actions: list[dict]}``

    Raises
    ------
    AppError("unauthorized", 401)
        If no valid Bearer token is provided.
    """
    from app.ai.agent import run_agent  # noqa: PLC0415

    org_id = await _enforce_ai_quota(_user)

    provider = get_provider()

    # Build first-party claims from the authenticated user.
    # For first-party callers, policies are empty (no RLS restrictions).
    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(_user.get("id", "")),
        "policies": {},
        "scope": ["read:*", "write:*"],
    }

    # Convert Pydantic ChatMessage objects to plain dicts for the agent.
    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    result = run_agent(messages, provider, claims, max_steps=8)

    await _record_ai_call(_user, org_id, endpoint="ai_chat")

    return ChatResponse(
        reply=result["reply"],
        actions=result["actions"],
        # Echo the requested model back so the frontend can confirm it.
        # TODO: once providers support per-request model selection, pass
        # body.model into run_agent/provider instead of only echoing it.
        model=body.model,
    )


@api_router.post("/ai/chat/stream", tags=["ai"])
async def ai_chat_stream(
    body: ChatRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> StreamingResponse:
    """Streaming variant of POST /ai/chat — Server-Sent Events.

    Runs the agent loop and emits live events (``text/event-stream``) so the UI
    can render tool calls + the streamed reply as they happen (Claude-Code
    style). Each event is a JSON object on a ``data:`` line; event types:
    ``status``, ``tool_start``, ``tool_result``, ``text``, ``done``, ``error``
    (see ``agent.run_agent_stream``).
    """
    import json as _json  # noqa: PLC0415

    from starlette.concurrency import iterate_in_threadpool  # noqa: PLC0415

    from app.ai.agent import run_agent_stream  # noqa: PLC0415

    org_id = await _enforce_ai_quota(_user)

    provider = get_provider()

    # Record the AI call up-front: a streamed agent run consumes the call
    # when dispatched (the stream may be abandoned mid-flight by the client).
    await _record_ai_call(_user, org_id, endpoint="ai_chat_stream")

    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(_user.get("id", "")),
        "policies": {},
        "scope": ["read:*", "write:*"],
    }
    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    def _sync_events():
        try:
            for ev in run_agent_stream(messages, provider, claims, max_steps=8):
                yield "data: " + _json.dumps(ev) + "\n\n"
        except Exception as exc:  # noqa: BLE001
            yield "data: " + _json.dumps({"type": "error", "message": str(exc)}) + "\n\n"

    async def _event_stream():
        # Iterate the blocking generator in a threadpool so tool calls / pacing
        # sleeps never block the event loop.
        async for chunk in iterate_in_threadpool(_sync_events()):
            yield chunk

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
        },
    )


# ---------------------------------------------------------------------------
# POST /ai/sql — text-to-SQL with catalog grounding (M18-A)
# ---------------------------------------------------------------------------

#: Regex to extract ``{{name}}`` placeholders from generated SQL.
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class SqlRequest(BaseModel):
    """Request body for POST /ai/sql."""

    question: str
    datastore_id: str | None = None
    save_as: str | None = None


class SqlResponse(BaseModel):
    """Response body for POST /ai/sql."""

    sql: str
    valid: bool
    issues: list[str]
    provider: str
    grounding: dict[str, Any]
    registered_id: str | None = None


@api_router.post("/ai/sql", response_model=SqlResponse, tags=["ai"])
async def generate_sql_endpoint(
    body: SqlRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> SqlResponse:
    """Generate a grounded SQL SELECT from a natural-language question (M18-A).

    Pipeline
    --------
    1. Build the catalog from the live query registry + lineage graph.
    2. Run deterministic grounding (token-overlap scoring) to find the most
       relevant tables and columns for the question.
    3. Call ``generate_sql`` which builds a SQL-focused grounded prompt, calls
       the configured LLM provider, and validates the result with sqlglot.
       With ``NullProvider`` (the default when no API key is set) this is a
       pure in-memory operation — no network call.
    4. If ``save_as`` is provided, register the generated SQL into the query
       registry under that id.  Named ``{{name}}`` placeholders found in the
       SQL are inferred as ``QueryParam`` descriptors (type ``'text'``, not
       required, no default).  Returns the registered id in ``registered_id``.

    Parameters
    ----------
    body:
        ``{question, datastore_id?, save_as?}``
    _user:
        Injected by ``current_user``; ensures the endpoint requires auth.

    Returns
    -------
    SqlResponse
        ``{sql, valid, issues, provider, grounding, registered_id}``

    Raises
    ------
    AppError("unauthorized", 401)
        If no valid Bearer token is provided.
    AppError("llm_not_configured", 503)
        If ``LLM_PROVIDER`` is explicitly set but the corresponding API key is
        absent.
    """
    from app.ai.sql import generate_sql  # noqa: PLC0415
    from app.queries.registry import QueryParam, get_query_registry  # noqa: PLC0415

    org_id = await _enforce_ai_quota(_user)

    catalog = build_catalog()
    grounding = ground(body.question, catalog)
    provider = get_provider()

    result = generate_sql(
        question=body.question,
        catalog=catalog,
        provider=provider,
        datastore_id=body.datastore_id,
    )

    sql: str = result["sql"]
    valid: bool = result["valid"]
    issues: list[str] = result["issues"]

    registered_id: str | None = None

    if body.save_as:
        # Infer named params from {{name}} placeholders in the SQL.
        placeholder_names = list(dict.fromkeys(_PLACEHOLDER_RE.findall(sql)))
        params = [
            QueryParam(name=name, type="text", required=False)
            for name in placeholder_names
        ]
        registry = get_query_registry()
        registry.register(
            id=body.save_as,
            sql=sql,
            name=body.question[:200],
            params=params if params else None,
        )
        registered_id = body.save_as

    await _record_ai_call(_user, org_id, endpoint="ai_sql")

    return SqlResponse(
        sql=sql,
        valid=valid,
        issues=issues,
        provider=provider.name,
        grounding=grounding,
        registered_id=registered_id,
    )
