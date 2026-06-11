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
import uuid
from typing import Any, Literal

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.ai.grounding import build_catalog, build_prompt, ground
from app.ai.provider import get_provider
from app.auth.deps import current_user
from app.compute.metering import record_usage
from app.errors import AppError
from app.features import enforce_quota
from app.repos.provider import Repo, get_repo
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
# GET /ai/context — single-call authoring context for external agents (M23-A)
# ---------------------------------------------------------------------------


#: Static authoring conventions block.  Kept short + factual: it tells an agent
#: how to wire a query's params/outputs into a DashboardSpec.  Echoed verbatim
#: in both the full and compact /ai/context responses.
_AI_CONTEXT_CONVENTIONS: dict[str, Any] = {
    "query_binding": (
        "Bind a widget to a query by its `id`. The widget's data columns must "
        "reference names from that query's `output_schema` — never invent column "
        "names."
    ),
    "params": (
        "A query's `params` are named placeholders. Supply values per param "
        "`name`; respect `required` and `type`. `select`/`multiselect` params draw "
        "their options from `options_query_id` (another query's id)."
    ),
    "variables": (
        "Dashboard-level variables are referenced in text/config with the "
        "`{{vars.<name>}}` template syntax and are routed to a query's matching "
        "param by name at execution time."
    ),
    "spec_binding": (
        "Author specs against GET /ai/dashboard/schema. Every query_id and column "
        "you reference must exist in this context; values flow query.params -> "
        "named_params and query.output_schema -> widget columns."
    ),
    "metrics": (
        "A `metric` is a GOVERNED business definition (e.g. `revenue = SUM(amount)`) "
        "compiled to SQL on demand — prefer it over hand-writing SQL when one fits, "
        "so two answers can't silently disagree. Query a metric via "
        "POST /metrics/{id}/query with a MetricQuery body: `dimensions` (a SUBSET of "
        "the metric's allowed `dimensions`), `time_grain` (one of the metric's "
        "`time_grains`), and `filters` ([{field, op, value}] on allowed dims or the "
        "time column). Asking for an unknown dimension / grain / filter field is "
        "rejected (400) — that governance is the point. Use POST /metrics/{id}/sql "
        "for a dry compile (returns sql + params, no execution)."
    ),
}


def _context_query_entry(rq: Any, *, compact: bool) -> dict[str, Any]:
    """Build one /ai/context query entry from a ``RegisteredQuery``.

    Full form carries ``{id, name, description, datastore, params, output_schema}``.
    Compact form drops verbose fields (``description``, ``datastore``, per-param
    ``default``/``options_query_id``) to shrink the token footprint while keeping
    everything an agent needs to bind names.
    """
    from app.ai.grounding import (  # noqa: PLC0415
        _output_schema_to_dicts,
        _params_to_dicts,
    )

    params = _params_to_dicts(rq.params)
    output_schema = _output_schema_to_dicts(rq.output_schema)

    if compact:
        return {
            "id": rq.id,
            "name": rq.name,
            "params": [
                {"name": p["name"], "type": p["type"], "required": p["required"]}
                for p in params
            ],
            "output_schema": output_schema,
        }

    return {
        "id": rq.id,
        "name": rq.name,
        # No dedicated description field on RegisteredQuery — fall back to name.
        "description": rq.name,
        "datastore": rq.datastore_id,
        "params": params,
        "output_schema": output_schema,
    }


def _get_metric_registry() -> Any:
    """Return the metrics registry singleton, adapting to its accessor name.

    The metrics registry (``app/metrics/registry.py``) is owned by another agent
    and mirrors :class:`~app.queries.registry.QueryRegistry`. We import its
    ``get_metric_registry`` factory defensively: if the module is not present
    (e.g. the registry wave hasn't landed yet) we return ``None`` and the caller
    simply emits an empty ``metrics`` list — ``/ai/context`` never 500s on this.
    """
    try:
        from app.metrics.registry import get_metric_registry  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — registry may not exist yet; degrade gracefully.
        return None
    try:
        return get_metric_registry()
    except Exception:  # noqa: BLE001
        return None


def _metrics_from(registry: Any) -> list[Any]:
    """List all metric definitions from *registry*, tolerant of accessor names.

    Mirrors ``QueryRegistry.all()`` but adapts if the metrics registry exposes
    ``list()`` instead (the contract says one of list/all/get).
    """
    if registry is None:
        return []
    for accessor in ("all", "list"):
        fn = getattr(registry, accessor, None)
        if callable(fn):
            try:
                return list(fn())
            except Exception:  # noqa: BLE001
                return []
    return []


def _context_metric_entry(md: Any, *, compact: bool) -> dict[str, Any]:
    """Build one /ai/context metric entry from a ``MetricDefinition``.

    Full form carries ``{id, name, measure, dimensions, time_grains, description}``;
    compact form trims to ``{id, name, measure, dimensions}`` to shrink the
    token footprint (mirrors the query entry's compact shape).
    """
    measure = {
        "name": md.measure.name,
        "agg": md.measure.agg,
        "expr": md.measure.expr,
    }
    dimensions = [d.name for d in md.dimensions]

    if compact:
        return {
            "id": md.id,
            "name": md.name,
            "measure": measure,
            "dimensions": dimensions,
        }

    time_grains = list(md.time_dimension.grains) if md.time_dimension else []
    return {
        "id": md.id,
        "name": md.name,
        "measure": measure,
        "dimensions": dimensions,
        "time_grains": time_grains,
        "description": md.description,
    }


def _metric_matches(md: Any, q: str) -> bool:
    """Cheap relevance filter: token overlap on a metric's name/description/id.

    The grounding scorer ranks *queries* by their tables/columns; metrics have no
    tables in the catalog, so we do a simple lowercase token-match over the
    metric's id/name/description/dimension names instead of over-engineering a
    second scorer.
    """
    tokens = {t for t in re.split(r"\W+", q.lower()) if t}
    if not tokens:
        return True
    haystack = " ".join(
        [
            md.id,
            md.name,
            md.description or "",
            " ".join(d.name for d in md.dimensions),
        ]
    ).lower()
    hay_tokens = {t for t in re.split(r"\W+", haystack) if t}
    return bool(tokens & hay_tokens)


@api_router.get("/ai/context", tags=["ai"])
async def ai_context(
    q: str | None = None,
    compact: bool = False,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return everything an external agent needs to author against in one call.

    The response lists every registered query with its real ``params`` and
    ``output_schema`` (so the agent binds to real names instead of guessing —
    the chief cause of invalid specs) plus a static ``conventions`` block
    describing how variables / ``{{vars.*}}`` / spec binding work.

    Token-budget controls
    ----------------------
    ``?q=<text>``
        Reuse the deterministic grounding scorer to rank + filter the queries
        to the ones most relevant to *text*.  Queries whose tables score zero
        are dropped; the rest are ordered most-relevant-first.  Omit ``q`` to
        return every query (registry order).
    ``?compact=true``
        Return a trimmed per-query shape (drops ``description``, ``datastore``,
        and per-param ``default``/``options_query_id``) to shrink the payload.

    Requires a valid Bearer token (same auth as the sibling schema endpoint).

    Returns
    -------
    dict
        ``{queries: [{id, name, [description, datastore,] params, output_schema}],
        metrics: [{id, name, measure, dimensions, [time_grains, description]}],
        conventions: {...}, compact: bool, filtered_by: str | None}``

        Each ``metrics`` entry describes a GOVERNED definition: ``measure`` is
        ``{name, agg, expr}``, ``dimensions`` is the list of allowed grouping
        column names, and (full form only) ``time_grains`` lists the buckets the
        metric can be queried at. ``?compact=true`` trims a metric to
        ``{id, name, measure, dimensions}``; ``?q=`` keeps only metrics whose
        id/name/description/dimensions share a token with the query text.

    Raises
    ------
    AppError("unauthorized", 401)
        If no valid Bearer token is provided.
    """
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    registry = get_query_registry()
    all_queries = registry.all()

    if q:
        # Rank + filter via the deterministic grounding scorer so the response
        # only carries the queries most relevant to the agent's intent.
        catalog = build_catalog()
        grounding = ground(q, catalog)
        # related_queries is an ordered (most-relevant-first) list of ids.
        ranked_ids = list(grounding.get("related_queries", []))
        order = {qid: i for i, qid in enumerate(ranked_ids)}
        by_id = {rq.id: rq for rq in all_queries}
        selected = [by_id[qid] for qid in ranked_ids if qid in by_id]
    else:
        selected = all_queries

    queries = [_context_query_entry(rq, compact=compact) for rq in selected]

    # ── Governed metrics (Wave C3) ────────────────────────────────────────────
    # Additive: a `metrics` list alongside `queries` so an agent discovers the
    # governed semantic-layer definitions it can query via POST /metrics/{id}/query.
    metric_registry = _get_metric_registry()
    all_metrics = _metrics_from(metric_registry)
    if q:
        all_metrics = [md for md in all_metrics if _metric_matches(md, q)]
    metrics = [_context_metric_entry(md, compact=compact) for md in all_metrics]

    return {
        "queries": queries,
        "metrics": metrics,
        "conventions": _AI_CONTEXT_CONVENTIONS,
        "compact": compact,
        "filtered_by": q,
    }


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


# ---------------------------------------------------------------------------
# POST /ai/pin — pin a governed answer onto a dashboard as a widget (ask→PIN)
# ---------------------------------------------------------------------------
#
# This is the "ask → PIN" step: an answer the user trusts (a registered query,
# or a governed metric backed by a query) plus a chosen visualisation becomes a
# *validated* dashboard widget.  We compose the widget into a board spec, run it
# through the SAME ``validate_spec`` + structured-errors path that
# ``POST /dashboards/validate`` uses, and only persist when it is valid.  An
# invalid pin returns repair-grade structured errors (path + code +
# valid_options) so an agent can fix the viz in one round-trip.


class PinSource(BaseModel):
    """The governed answer being pinned — EXACTLY ONE of ``query_id`` /
    ``metric_id`` must be set.

    ``query_id``
        A registered query id (the widget binds directly to it).
    ``metric_id``
        A governed metric id.  Dashboard widgets bind to a ``query_id`` only —
        the canonical ``DashboardSpec`` has no native metric binding — so pinning
        a metric directly returns a clear 400 (``metric_pin_unsupported``).  We do
        not fabricate a binding the spec cannot honour: expose the metric as a
        registered query and pin that ``query_id`` instead.
    """

    query_id: str | None = None
    metric_id: str | None = None


class PinViz(BaseModel):
    """The visualisation to render the pinned answer with."""

    type: Literal["kpi", "table", "chart"]
    chart_type: str | None = None
    encoding: dict[str, str] | None = None


class PinRequest(BaseModel):
    """Request body for POST /ai/pin."""

    title: str
    source: PinSource
    viz: PinViz
    board_id: str | None = None
    params: dict[str, Any] | None = None


class PinResponse(BaseModel):
    """Response body for POST /ai/pin (success)."""

    board_id: str
    widget_id: str
    spec: dict[str, Any]
    valid: bool


def _next_widget_pos(widgets: list[dict[str, Any]], cols: int = 12) -> dict[str, int]:
    """Pick a sensible default grid position for an appended widget.

    Places the new widget on a fresh row below everything already on the board
    (so it never overlaps an existing widget).  Width defaults to a third of the
    grid (4 of 12 cols); a single-widget board starts at the top-left.
    """
    bottom = 1
    for w in widgets:
        pos = w.get("pos") or {}
        try:
            y = int(pos.get("y", 1))
            h = int(pos.get("h", 1))
        except (TypeError, ValueError):
            continue
        bottom = max(bottom, y + h)
    width = 4 if cols >= 4 else cols
    return {"x": 1, "y": bottom, "w": width, "h": 3}


def _build_pin_widget(
    body: PinRequest, widget_id: str, pos: dict[str, int]
) -> dict[str, Any]:
    """Build a Widget dict from the pin request's source + viz.

    The widget binds directly to ``source.query_id`` (the only binding the
    canonical spec supports), carries the chosen viz ``encoding``/``chart_type``,
    and folds in any bound ``params`` for the source query.
    """
    widget: dict[str, Any] = {
        "id": widget_id,
        "type": body.viz.type,
        "query_id": body.source.query_id or "",
        "encoding": dict(body.viz.encoding or {}),
        "props": {},
        "pos": pos,
    }
    if body.viz.chart_type is not None:
        widget["chart_type"] = body.viz.chart_type
    if body.params:
        # Bound params for the source query (literal scalars; refs would have to
        # resolve to declared spec variables, which a freshly pinned answer has
        # none of — so callers pass literals here).
        widget["params"] = dict(body.params)
    return widget


@api_router.post("/ai/pin", response_model=PinResponse, tags=["ai"])
async def pin_answer(
    body: PinRequest,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Any:
    """Pin a governed answer onto a dashboard as a *validated* widget (ask→PIN).

    Pipeline
    --------
    1. Resolve the caller's org/project (same helpers the resources routes use)
       and require a first-party authenticated user.
    2. Resolve the source binding — EXACTLY ONE of ``source.query_id`` /
       ``source.metric_id``.  A bare ``metric_id`` is honestly rejected
       (``metric_pin_unsupported``): the spec binds widgets to queries, not
       metrics — expose the metric as a query and pin that query_id.
    3. Build a Widget dict from source + viz with a stable id + default pos.
    4. Compose the target spec: append to ``board_id``'s spec, or start a new
       single-widget ``DashboardSpec``.
    5. Validate via ``validate_spec`` → on error, return a structured 400 built
       with the SAME ``to_structured_issues`` helper ``/dashboards/validate``
       uses (repair-grade: path + code + valid_options).  Nothing is persisted.
    6. Persist: create a new board or update the existing one, then return
       ``{board_id, widget_id, spec, valid: true}``.

    Parameters
    ----------
    body:
        ``{title, source:{query_id?|metric_id?}, viz:{type, chart_type?,
        encoding?}, board_id?, params?}``.
    request:
        Used for org/project resolution (honours ``X-Org-Id`` / ``?project_id``).
    user:
        Injected by ``current_user``; ensures a first-party authenticated caller.
    repo:
        Resource repository (boards live in the ``boards`` resource).

    Returns
    -------
    PinResponse
        ``{board_id, widget_id, spec, valid: true}`` on success.

    Raises
    ------
    AppError("unauthorized", 401)
        No valid Bearer token.
    AppError("invalid_pin_source", 400)
        Neither or both of ``query_id`` / ``metric_id`` supplied.
    AppError("metric_pin_unsupported", 400)
        A ``metric_id`` source — there is no native metric binding in the spec.
    AppError("not_found", 404)
        ``board_id`` given but no such board for the caller's org.
    AppError("invalid_pin_spec", 400)
        The composed spec failed validation — body carries structured
        ``errors``/``warnings`` (same shape as ``/dashboards/validate``).
    """
    from app.dashboards.errors import to_structured_issues  # noqa: PLC0415
    from app.dashboards.spec import validate_spec  # noqa: PLC0415
    from app.routes._org import (  # noqa: PLC0415
        resolve_org_id,
        resolve_project_id_for_create,
    )

    # ── Step 1: org/project resolution (resources-route conventions) ──────────
    org_id = await resolve_org_id(str(user["id"]), repo, request)

    # ── Step 2: validate the source binding (EXACTLY ONE id) ──────────────────
    # The source names the governed answer being pinned. A query_id binds the
    # widget directly. A metric_id is GOVERNED but the canonical DashboardSpec
    # has NO native metric binding — so a bare metric pin is honestly rejected
    # (metric_pin_unsupported) rather than faking a binding the spec can't honour.
    # To pin a metric, expose it as a registered query and pin that query_id; the
    # caller may stamp the originating metric on the widget via ``params`` /
    # ``viz`` props if they want provenance.
    has_query = bool(body.source.query_id)
    has_metric = bool(body.source.metric_id)
    if has_query == has_metric:
        # Neither set, or both set — the source is ambiguous.
        raise AppError(
            "invalid_pin_source",
            "source must set EXACTLY ONE of 'query_id' or 'metric_id'.",
            400,
        )
    if has_metric:
        # Honest limitation: no native metric binding in the spec.  We verify the
        # metric exists (so the error names the real reason) and return a clear
        # 400 pointing the caller at the supported query-backed path.
        registry = _get_metric_registry()
        known = registry is not None and registry.get(body.source.metric_id) is not None
        detail = "" if known else " (note: that metric id is also not registered)"
        raise AppError(
            "metric_pin_unsupported",
            (
                "Pinning a metric directly is not supported: a dashboard widget "
                "binds to a 'query_id', not a 'metric_id'. Expose the metric as a "
                "registered query and pin that query_id instead." + detail
            ),
            400,
        )

    # ── Step 3: build the widget ──────────────────────────────────────────────
    widget_id = "w_" + uuid.uuid4().hex[:8]

    # ── Step 4: compose the target spec (new board or append) ─────────────────
    existing_row: dict[str, Any] | None = None
    if body.board_id:
        existing_row = await repo.get("boards", org_id, body.board_id)
        if existing_row is None:
            raise AppError("not_found", "Board not found.", 404)
        spec_data = _board_spec_from_config(existing_row.get("config"))
        widgets = list(spec_data.get("widgets") or [])
        cols = int((spec_data.get("layout") or {}).get("cols", 12))
        pos = _next_widget_pos(widgets, cols)
        widget = _build_pin_widget(body, widget_id, pos)
        widgets.append(widget)
        spec_data["widgets"] = widgets
        # Preserve the existing board title; default it if the board had none.
        spec_data.setdefault("title", existing_row.get("name") or body.title)
    else:
        pos = _next_widget_pos([])
        widget = _build_pin_widget(body, widget_id, pos)
        spec_data = {"version": 1, "title": body.title, "widgets": [widget]}

    # ── Step 5: validate — reuse the structured-errors helper (NO persist) ────
    _spec, raw_issues = validate_spec(spec_data)
    structured = to_structured_issues(spec_data, raw_issues)
    errors = [i.to_dict() for i in structured if i.severity == "error"]
    if errors:
        warnings = [i.to_dict() for i in structured if i.severity == "warning"]
        # Repair-grade 400: same structured-issue shape as POST /dashboards/validate
        # (path + code + valid_options), wrapped in the standard error envelope so
        # an agent can fix the viz in one round-trip.  NOTHING is persisted.
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_pin_spec",
                    "message": "The pinned widget produced an invalid dashboard spec.",
                    "valid": False,
                    "errors": errors,
                    "warnings": warnings,
                }
            },
        )

    # ── Step 6: persist (create or update) ────────────────────────────────────
    config = {"spec": spec_data}
    if existing_row is not None:
        updated = await repo.update(
            "boards", org_id, body.board_id, {"config": config}
        )
        if updated is None:  # pragma: no cover — re-check after the get above
            raise AppError("not_found", "Board not found.", 404)
        board_id = str(updated["id"])
    else:
        project_id = await resolve_project_id_for_create(org_id, request)
        created = await repo.create(
            resource="boards",
            org_id=org_id,
            created_by=str(user["id"]),
            name=body.title,
            config=config,
            project_id=project_id,
        )
        board_id = str(created["id"])

    return PinResponse(
        board_id=board_id,
        widget_id=widget_id,
        spec=spec_data,
        valid=True,
    )


def _board_spec_from_config(config: Any) -> dict[str, Any]:
    """Return the canonical DashboardSpec dict stored in a board ``config``.

    Mirrors ``app.portability._dashboard_spec_from_row``: the editor nests the
    spec under ``config['spec']``; we fall back to treating the whole config as
    the spec for forward/backward compat.  Always returns a fresh dict.
    """
    if isinstance(config, dict) and isinstance(config.get("spec"), dict):
        return dict(config["spec"])
    if isinstance(config, dict):
        return dict(config)
    return {}
