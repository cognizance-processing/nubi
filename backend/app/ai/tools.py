"""Tool registry for the Nubi AI agent (M21-A).

Each tool is a ``ToolDef`` dataclass with:
  - ``name``        — stable identifier used in agent messages.
  - ``json_schema`` — JSON Schema dict describing the tool's input parameters.
  - ``fn``          — the callable that runs the tool.

Every tool callable accepts a ``claims`` keyword argument so it can enforce
the caller's auth scope.  Tools that touch data (``run_query``) NEVER exceed
the caller's scope — they pass claims through to the planner, which injects
RLS predicates.

Public API
----------
get_tool(name) -> ToolDef | None
    Return the registered ToolDef for *name*, or None if unknown.

all_tools() -> list[ToolDef]
    Return all registered tools.

tool_schemas() -> list[dict]
    Return the JSON Schema block for every tool (for injecting into LLM prompts).

execute_tool(name, arguments, claims) -> dict
    Validate *arguments* against the tool's schema and call its ``fn``.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# ToolDef
# ---------------------------------------------------------------------------


@dataclass
class ToolDef:
    """A single registered tool.

    Attributes
    ----------
    name:
        Stable, underscore-separated identifier (e.g. ``"run_query"``).
    description:
        One-sentence human / LLM description.
    json_schema:
        JSON Schema ``object`` describing the tool's ``arguments``.  MUST be a
        valid JSON Schema ``{"type": "object", "properties": {...}, ...}``.
    fn:
        Callable ``fn(claims, **kwargs) -> dict`` that executes the tool and
        returns a JSON-serialisable dict result.
    """

    name: str
    description: str
    json_schema: dict[str, Any]
    fn: Callable[..., dict[str, Any]]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_get_schema(claims: dict[str, Any]) -> dict[str, Any]:
    """Return the catalog schema (tables + columns) visible to the caller.

    The catalog is built from the live query registry and the lineage graph.
    No filtering by claims is applied here — the catalog only exposes
    registered (already allowlisted) metadata, not raw data.
    """
    from app.ai.grounding import build_catalog  # noqa: PLC0415

    return build_catalog()


def _tool_list_queries(claims: dict[str, Any]) -> dict[str, Any]:
    """Return all registered queries (id, name, sql summary, params)."""
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    registry = get_query_registry()
    queries = []
    for rq in registry.all():
        queries.append(
            {
                "id": rq.id,
                "name": rq.name,
                "required_scope": rq.required_scope,
                "params": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "default": p.default,
                    }
                    for p in rq.params
                ],
            }
        )
    return {"queries": queries}


def _tool_generate_sql(
    question: str,
    claims: dict[str, Any],
    datastore_id: str | None = None,
) -> dict[str, Any]:
    """Generate a grounded SQL SELECT for a natural-language *question*.

    Reuses ``app.ai.sql.generate_sql`` (M18).  With NullProvider (the default
    when no API key is set) the result is fully deterministic.

    Returns ``{sql, valid, issues}``.
    """
    from app.ai.grounding import build_catalog  # noqa: PLC0415
    from app.ai.provider import get_provider  # noqa: PLC0415
    from app.ai.sql import generate_sql  # noqa: PLC0415

    catalog = build_catalog()
    provider = get_provider()
    return generate_sql(
        question=question,
        catalog=catalog,
        provider=provider,
        datastore_id=datastore_id,
    )


def _tool_create_query(
    id: str,
    sql: str,
    claims: dict[str, Any],
    params: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Register a query in the query registry under the given *id*.

    Parameters
    ----------
    id:
        Stable, URL-safe identifier.
    sql:
        The SELECT SQL string to register.
    params:
        Optional list of ``{name, type, required?, default?}`` param descriptors.

    Returns
    -------
    dict
        ``{id, name, registered: True}``
    """
    from app.queries.registry import QueryParam, get_query_registry  # noqa: PLC0415

    param_objs: list[QueryParam] = []
    if params:
        for p in params:
            param_objs.append(
                QueryParam(
                    name=p["name"],
                    type=p.get("type", "text"),  # type: ignore[arg-type]
                    default=p.get("default"),
                    required=bool(p.get("required", False)),
                    options_query_id=p.get("options_query_id"),
                )
            )

    registry = get_query_registry()
    rq = registry.register(
        id=id,
        sql=sql,
        name=id.replace("_", " ").title(),
        params=param_objs if param_objs else None,
    )
    return {"id": rq.id, "name": rq.name, "registered": True}


def _tool_run_query(
    claims: dict[str, Any],
    query_id: str | None = None,
    sql: str | None = None,
    named_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a query and return the result rows.

    Either *query_id* (a registered query) or *sql* (an ad-hoc SELECT) must be
    provided.  The caller's *claims* are passed to the planner — RLS policies
    in ``claims["policies"]`` are injected as AST-level WHERE predicates.  This
    ensures the tool NEVER returns data outside the caller's scope.

    Parameters
    ----------
    query_id:
        Id of a registered query (takes precedence over *sql*).
    sql:
        Ad-hoc SELECT SQL (used only if *query_id* is None).
    named_params:
        Named parameter values for ``{{name}}`` placeholders in registry SQL.
    claims:
        Caller's auth claims (RLS enforced via the planner).

    Returns
    -------
    dict
        ``{rows: list[dict], row_count: int, columns: list[str]}``
    """
    from app.connectors.duckdb_conn import DuckDBConnector  # noqa: PLC0415
    from app.connectors.planner import plan, resolve_named_params  # noqa: PLC0415
    from app.errors import AppError  # noqa: PLC0415
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    # ── Resolve SQL ──────────────────────────────────────────────────────────
    resolved_sql: str
    positional_params: list[Any] = []

    if query_id is not None:
        registry = get_query_registry()
        rq = registry.get(query_id)
        if rq is None:
            raise AppError("query_not_found", f"No registered query with id {query_id!r}.", 404)
        resolved_sql = rq.sql
        # Resolve named params → positional if the query has placeholders.
        if named_params and rq.params:
            # Build the resolved dict (apply defaults for missing optional params).
            resolved: dict[str, Any] = {}
            for p in rq.params:
                if p.name in named_params:
                    resolved[p.name] = named_params[p.name]
                elif p.default is not None:
                    resolved[p.name] = p.default
                elif p.required:
                    raise AppError(
                        "missing_required_param",
                        f"Required param {p.name!r} was not supplied.",
                        400,
                    )
            resolved_sql, positional_params = resolve_named_params(resolved_sql, resolved)
    elif sql is not None:
        resolved_sql = sql
    else:
        raise AppError("invalid_tool_input", "Either query_id or sql must be provided.", 400)

    # ── Plan + execute via DuckDB (deterministic demo engine) ────────────────
    physical_plan = plan(resolved_sql, claims=claims, params=positional_params)
    connector = DuckDBConnector()
    # Seed the demo table for queries that reference it.
    _seed_demo_table(connector)

    arrow_table = connector.execute(physical_plan)

    # Convert to JSON-serialisable rows.
    columns = arrow_table.schema.names
    rows = arrow_table.to_pylist()
    return {"rows": rows, "row_count": len(rows), "columns": columns}


def _seed_demo_table(connector: Any) -> None:
    """Seed the ``demo`` table into a fresh DuckDB connector.

    This mirrors the demo seeding done in the query route so tools that
    reference ``demo_all`` / ``demo_active`` work in the agent context.
    """
    try:
        import pyarrow as pa  # noqa: PLC0415

        demo = pa.table(
            {
                "id": pa.array([1, 2, 3, 4, 5], type=pa.int32()),
                "name": pa.array(["alpha", "beta", "gamma", "delta", "epsilon"]),
                "active": pa.array([True, True, False, True, False]),
                "value": pa.array([10.0, 20.0, 30.0, 40.0, 50.0], type=pa.float64()),
            }
        )
        connector.register({"demo": demo})
    except Exception:  # noqa: BLE001
        pass  # If seeding fails, the query will just fail naturally.


def _tool_create_dashboard(
    question: str,
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Generate a canonical DashboardSpec for *question*.

    Reuses ``app.ai.dashboard.generate_dashboard_spec`` (M8).  With NullProvider
    the result is fully deterministic.

    Returns ``{spec: dict, html: str}``.
    """
    from app.ai.dashboard import generate_dashboard_spec, validate_dashboard_html  # noqa: PLC0415
    from app.ai.grounding import build_catalog  # noqa: PLC0415
    from app.ai.provider import get_provider  # noqa: PLC0415
    from app.dashboards.spec import spec_to_html  # noqa: PLC0415

    catalog = build_catalog()
    provider = get_provider()
    spec = generate_dashboard_spec(question, catalog, provider)
    html_out = spec_to_html(spec)
    ok, issues = validate_dashboard_html(html_out)
    return {
        "spec": spec.model_dump(),
        "html": html_out,
        "valid": ok,
        "issues": issues,
    }


def _tool_edit_dashboard(
    spec: dict[str, Any],
    op: dict[str, Any],
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Apply an edit operation to a DashboardSpec and re-validate.

    The *op* dict describes what to do.  Supported operations:

    ``{"action": "add_widget", "widget": {...}}``
        Add a new widget to the spec.  The widget dict must conform to the
        ``Widget`` schema.

    ``{"action": "move_widget", "widget_id": "w1", "pos": {"x":1,"y":2,"w":4,"h":2}}``
        Update the position of an existing widget.

    ``{"action": "configure_widget", "widget_id": "w1", "updates": {...}}``
        Merge *updates* into the widget's fields (except ``id`` and ``type``).

    ``{"action": "remove_widget", "widget_id": "w1"}``
        Remove a widget by id.

    Returns
    -------
    dict
        ``{spec: dict, valid: bool, issues: list[str]}``
    """
    from app.dashboards.spec import validate_spec  # noqa: PLC0415

    # Deep-copy to avoid mutating caller's dict.
    working_spec = copy.deepcopy(spec)

    action = op.get("action", "")

    if action == "add_widget":
        widget_data = op.get("widget")
        if not widget_data or not isinstance(widget_data, dict):
            from app.errors import AppError  # noqa: PLC0415
            raise AppError(
                "invalid_tool_input",
                "edit_dashboard add_widget requires a 'widget' dict.",
                400,
            )
        working_spec.setdefault("widgets", []).append(widget_data)

    elif action == "move_widget":
        widget_id = op.get("widget_id")
        new_pos = op.get("pos")
        if not widget_id or not isinstance(new_pos, dict):
            from app.errors import AppError  # noqa: PLC0415
            raise AppError(
                "invalid_tool_input",
                "edit_dashboard move_widget requires 'widget_id' and 'pos'.",
                400,
            )
        for w in working_spec.get("widgets", []):
            if w.get("id") == widget_id:
                w["pos"] = new_pos
                break

    elif action == "configure_widget":
        widget_id = op.get("widget_id")
        updates = op.get("updates", {})
        if not widget_id or not isinstance(updates, dict):
            from app.errors import AppError  # noqa: PLC0415
            raise AppError(
                "invalid_tool_input",
                "edit_dashboard configure_widget requires 'widget_id' and 'updates'.",
                400,
            )
        for w in working_spec.get("widgets", []):
            if w.get("id") == widget_id:
                for k, v in updates.items():
                    if k not in ("id", "type"):  # protect immutable fields
                        w[k] = v
                break

    elif action == "remove_widget":
        widget_id = op.get("widget_id")
        if not widget_id:
            from app.errors import AppError  # noqa: PLC0415
            raise AppError(
                "invalid_tool_input",
                "edit_dashboard remove_widget requires 'widget_id'.",
                400,
            )
        working_spec["widgets"] = [
            w for w in working_spec.get("widgets", []) if w.get("id") != widget_id
        ]

    else:
        from app.errors import AppError  # noqa: PLC0415
        raise AppError(
            "invalid_tool_input",
            f"Unknown edit_dashboard action {action!r}. "
            "Supported: add_widget, move_widget, configure_widget, remove_widget.",
            400,
        )

    # Re-validate via dashboards/spec.py
    result_spec, issues = validate_spec(working_spec)
    if result_spec is not None:
        return {
            "spec": result_spec.model_dump(),
            "valid": len([i for i in issues if "not in the registered" not in i]) == 0,
            "issues": issues,
        }
    # Pydantic parse failed → return the raw working_spec + issues.
    return {"spec": working_spec, "valid": False, "issues": issues}


# ---------------------------------------------------------------------------
# JSON Schemas for each tool
# ---------------------------------------------------------------------------

_SCHEMA_GET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCHEMA_LIST_QUERIES: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SCHEMA_GENERATE_SQL: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "Natural-language question to convert to SQL.",
        },
        "datastore_id": {
            "type": "string",
            "description": "Optional datastore id for context.",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}

_SCHEMA_CREATE_QUERY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {
            "type": "string",
            "description": "Stable URL-safe identifier for the query.",
        },
        "sql": {
            "type": "string",
            "description": "The SELECT SQL string to register.",
        },
        "params": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["text", "number", "date", "daterange", "select", "multiselect"],
                    },
                    "required": {"type": "boolean"},
                    "default": {},
                    "options_query_id": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            "description": "Optional named parameter descriptors.",
        },
    },
    "required": ["id", "sql"],
    "additionalProperties": False,
}

_SCHEMA_RUN_QUERY: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query_id": {
            "type": "string",
            "description": "Id of a registered query to execute.",
        },
        "sql": {
            "type": "string",
            "description": "Ad-hoc SELECT SQL (used if query_id not provided).",
        },
        "named_params": {
            "type": "object",
            "description": "Named parameter values for {{name}} placeholders.",
            "additionalProperties": True,
        },
    },
    "additionalProperties": False,
}

_SCHEMA_CREATE_DASHBOARD: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "Natural-language question describing the desired dashboard.",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}

_SCHEMA_EDIT_DASHBOARD: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": {
            "type": "object",
            "description": "The current DashboardSpec dict to modify.",
        },
        "op": {
            "type": "object",
            "description": (
                "Edit operation. "
                "{'action':'add_widget','widget':{...}} | "
                "{'action':'move_widget','widget_id':'w1','pos':{x,y,w,h}} | "
                "{'action':'configure_widget','widget_id':'w1','updates':{...}} | "
                "{'action':'remove_widget','widget_id':'w1'}"
            ),
            "properties": {
                "action": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    "required": ["spec", "op"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _make_registry() -> dict[str, ToolDef]:
    """Build and return the module-level tool registry."""

    def _wrap_get_schema(claims: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        return _tool_get_schema(claims)

    def _wrap_list_queries(claims: dict[str, Any], **_kw: Any) -> dict[str, Any]:
        return _tool_list_queries(claims)

    def _wrap_generate_sql(
        claims: dict[str, Any],
        question: str,
        datastore_id: str | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_generate_sql(question, claims, datastore_id=datastore_id)

    def _wrap_create_query(
        claims: dict[str, Any],
        id: str,
        sql: str,
        params: list[dict[str, Any]] | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_create_query(id, sql, claims, params=params)

    def _wrap_run_query(
        claims: dict[str, Any],
        query_id: str | None = None,
        sql: str | None = None,
        named_params: dict[str, Any] | None = None,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_run_query(claims, query_id=query_id, sql=sql, named_params=named_params)

    def _wrap_create_dashboard(
        claims: dict[str, Any],
        question: str,
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_create_dashboard(question, claims)

    def _wrap_edit_dashboard(
        claims: dict[str, Any],
        spec: dict[str, Any],
        op: dict[str, Any],
        **_kw: Any,
    ) -> dict[str, Any]:
        return _tool_edit_dashboard(spec, op, claims)

    from app.ai.flow_tools import make_flow_tool_defs  # noqa: PLC0415

    tools = [
        ToolDef(
            name="get_schema",
            description="Return the catalog schema (tables and columns) from the query registry.",
            json_schema=_SCHEMA_GET_SCHEMA,
            fn=_wrap_get_schema,
        ),
        ToolDef(
            name="list_queries",
            description="List all registered queries with their ids, names, and parameter descriptors.",
            json_schema=_SCHEMA_LIST_QUERIES,
            fn=_wrap_list_queries,
        ),
        ToolDef(
            name="generate_sql",
            description="Generate a grounded SQL SELECT from a natural-language question.",
            json_schema=_SCHEMA_GENERATE_SQL,
            fn=_wrap_generate_sql,
        ),
        ToolDef(
            name="create_query",
            description="Register a SQL query in the query registry under a given id.",
            json_schema=_SCHEMA_CREATE_QUERY,
            fn=_wrap_create_query,
        ),
        ToolDef(
            name="run_query",
            description=(
                "Execute a registered query (by query_id) or an ad-hoc SELECT. "
                "Caller's RLS claims are enforced — results never exceed caller scope."
            ),
            json_schema=_SCHEMA_RUN_QUERY,
            fn=_wrap_run_query,
        ),
        ToolDef(
            name="create_dashboard",
            description="Generate a canonical DashboardSpec for a natural-language question.",
            json_schema=_SCHEMA_CREATE_DASHBOARD,
            fn=_wrap_create_dashboard,
        ),
        ToolDef(
            name="edit_dashboard",
            description=(
                "Apply an edit operation (add/move/configure/remove widget) to a DashboardSpec "
                "and re-validate it."
            ),
            json_schema=_SCHEMA_EDIT_DASHBOARD,
            fn=_wrap_edit_dashboard,
        ),
    ]
    # Append flow orchestrator tools.
    tools.extend(make_flow_tool_defs())
    return {t.name: t for t in tools}


_REGISTRY: dict[str, ToolDef] = _make_registry()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_tool(name: str) -> ToolDef | None:
    """Return the ``ToolDef`` for *name*, or ``None`` if unknown."""
    return _REGISTRY.get(name)


def all_tools() -> list[ToolDef]:
    """Return all registered tools in insertion order."""
    return list(_REGISTRY.values())


def tool_schemas() -> list[dict[str, Any]]:
    """Return a list of tool descriptors ready to inject into an LLM prompt.

    Each entry has the shape::

        {
            "name": "<tool name>",
            "description": "...",
            "input_schema": { ... json schema ... }
        }

    This format matches the Anthropic tool-use API convention.  Other providers
    can adapt the shape; the agent loop is responsible for formatting.
    """
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.json_schema,
        }
        for t in _REGISTRY.values()
    ]


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Validate *arguments* against the tool's schema and execute it.

    Parameters
    ----------
    name:
        Tool name (must be registered).
    arguments:
        Dict of tool arguments from the agent / model.
    claims:
        Caller's auth claims (passed to every tool).

    Returns
    -------
    dict
        JSON-serialisable result from the tool.

    Raises
    ------
    AppError("tool_not_found", 404)
        If *name* is not a registered tool.
    AppError("invalid_tool_input", 400)
        If *arguments* fails basic schema validation.
    """
    from app.errors import AppError  # noqa: PLC0415

    tool = _REGISTRY.get(name)
    if tool is None:
        raise AppError("tool_not_found", f"No tool named {name!r}.", 404)

    # Basic schema validation: check required fields.
    required_fields: list[str] = tool.json_schema.get("required", [])
    for req in required_fields:
        if req not in arguments:
            raise AppError(
                "invalid_tool_input",
                f"Tool {name!r} requires argument {req!r}.",
                400,
            )

    # Check for unexpected arguments when additionalProperties is False.
    if not tool.json_schema.get("additionalProperties", True):
        allowed = set(tool.json_schema.get("properties", {}).keys())
        extra = set(arguments.keys()) - allowed
        if extra:
            raise AppError(
                "invalid_tool_input",
                f"Tool {name!r} received unexpected arguments: {sorted(extra)}.",
                400,
            )

    return tool.fn(claims=claims, **arguments)
