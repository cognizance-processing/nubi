"""Tools the streaming chat assistant can call.

The assistant can do ALL dashboard editing through these tools — not just
propose a whole spec from scratch.  Two broad families:

Whole-spec authoring
--------------------
- ``propose_dashboard_spec(instruction)`` — generate a canonical ``DashboardSpec``
  JSON from a natural-language instruction.  Reuses
  ``app.ai.dashboard.generate_dashboard_spec`` (the generator behind
  ``POST /ai/dashboard``).  Best for building a brand-new dashboard.

Incremental spec edits (pure transforms over the *current* spec)
---------------------------------------------------------------
Each of these takes the current dashboard ``spec`` (the dict the editor
round-trips) and returns the updated spec, which becomes the turn's proposed
spec the editor applies.  They are thin wrappers over ``app.chat.spec_ops``:

- ``add_widget`` / ``update_widget`` / ``remove_widget``
- ``set_widget_style`` — per-widget background (incl. transparent), border, …
- ``set_layout`` — grid cols / row_height / compaction / margins
- ``set_background`` — dashboard background (solid / gradient / image)
- ``add_variable`` / ``set_drilldown``

Query registry
--------------
- ``list_registered_queries()`` — read the live query registry so widgets bind
  to REAL ``query_id`` values.  Reuses ``app.queries.get_query_registry()``.
- ``register_query(name, sql, params?, datastore_id?)`` — create/upsert a
  registered query (same path as ``POST /query/registry``) and return its id so
  a widget can bind it.

Each tool is exposed to the Anthropic Messages API via :func:`anthropic_tool_specs`
(``{name, description, input_schema}``) and executed via :func:`execute_tool`,
which returns ``(output, extra)`` where *extra* may carry a proposed spec that the
route surfaces in the final assistant message.
"""

from __future__ import annotations

import re
from typing import Any

from app.chat import spec_ops

# ---------------------------------------------------------------------------
# Shared schema fragments
# ---------------------------------------------------------------------------

_WIDGET_TYPE_ENUM = ["kpi", "table", "chart", "filter", "text"]
_CHART_TYPE_ENUM = ["line", "bar", "scatter", "area", "pie"]
_VAR_TYPE_ENUM = ["text", "number", "date", "daterange", "select", "multiselect"]

#: The current dashboard spec the edit tools operate on.  The model passes the
#: spec it is editing; when omitted the op starts from a blank dashboard.
_SPEC_PROP: dict[str, Any] = {
    "type": "object",
    "description": (
        "The CURRENT dashboard spec being edited (the full DashboardSpec JSON: "
        "version, title, layout, background, variables, widgets). Pass the spec "
        "from the latest proposed/applied state. Omit only when starting a brand "
        "new empty dashboard."
    ),
    "additionalProperties": True,
}

_POS_PROP: dict[str, Any] = {
    "type": "object",
    "description": (
        "Optional grid position/size {x,y,w,h} (1-based x/y, column/row spans "
        "w/h). Omit to auto-place the widget in the first free grid spot."
    ),
    "properties": {
        "x": {"type": "integer", "minimum": 1},
        "y": {"type": "integer", "minimum": 1},
        "w": {"type": "integer", "minimum": 1},
        "h": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Anthropic tool specifications
# ---------------------------------------------------------------------------

_PROPOSE_SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "instruction": {
            "type": "string",
            "description": (
                "Natural-language description of the dashboard (or the change to "
                "it) the user wants. Be specific about metrics, charts, and tables."
            ),
        },
    },
    "required": ["instruction"],
    "additionalProperties": False,
}

_LIST_QUERIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_ADD_WIDGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": _SPEC_PROP,
        "type": {
            "type": "string",
            "enum": _WIDGET_TYPE_ENUM,
            "description": "Widget kind to add.",
        },
        "query_id": {
            "type": "string",
            "description": (
                "Registered query id backing the widget (required for kpi/table/"
                "chart). Use list_registered_queries or register_query to obtain a "
                "real id."
            ),
        },
        "chart_type": {
            "type": "string",
            "enum": _CHART_TYPE_ENUM,
            "description": "Chart variant — required when type='chart'.",
        },
        "encoding": {
            "type": "object",
            "description": (
                "Column encoding. Charts: {x,y,color?}. KPI: {value}. Uses real "
                "column names."
            ),
            "additionalProperties": {"type": "string"},
        },
        "props": {
            "type": "object",
            "description": "Extra widget props (label, limit, format, columns, …).",
            "additionalProperties": True,
        },
        "style": {
            "type": "object",
            "description": "Per-widget style (background, border, …). Optional.",
            "additionalProperties": True,
        },
        "subtype": {
            "type": "string",
            "enum": ["select", "multiselect", "daterange", "text"],
            "description": "Filter sub-type — required when type='filter'.",
        },
        "target_var": {
            "type": "string",
            "description": "Variable a filter widget writes to (type='filter').",
        },
        "options_query_id": {
            "type": "string",
            "description": "Query providing options for a select/multiselect filter.",
        },
        "content": {
            "type": "string",
            "description": "Markdown content — required when type='text'.",
        },
        "params": {
            "type": "object",
            "description": (
                "Named param bindings: {paramName: {ref:'<varName>'} | <literal>}."
            ),
            "additionalProperties": True,
        },
        "pos": _POS_PROP,
    },
    "required": ["type"],
    "additionalProperties": False,
}

_UPDATE_WIDGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": _SPEC_PROP,
        "widget_id": {"type": "string", "description": "Id of the widget to update."},
        "patch": {
            "type": "object",
            "description": (
                "Fields to change. Dict fields (props, encoding, style, params, "
                "pos) are shallow-merged; scalar fields (query_id, chart_type, "
                "type, content, subtype, target_var, …) are replaced. The widget "
                "id cannot be changed."
            ),
            "additionalProperties": True,
        },
    },
    "required": ["widget_id", "patch"],
    "additionalProperties": False,
}

_REMOVE_WIDGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": _SPEC_PROP,
        "widget_id": {"type": "string", "description": "Id of the widget to remove."},
    },
    "required": ["widget_id"],
    "additionalProperties": False,
}

_SET_WIDGET_STYLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": _SPEC_PROP,
        "widget_id": {"type": "string", "description": "Id of the widget to style."},
        "style": {
            "type": "object",
            "description": (
                "Style props to merge: background (CSS colour / gradient / "
                "'transparent'), border, borderRadius, padding, boxShadow, …."
            ),
            "additionalProperties": True,
        },
    },
    "required": ["widget_id", "style"],
    "additionalProperties": False,
}

_SET_LAYOUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": _SPEC_PROP,
        "options": {
            "type": "object",
            "description": (
                "Grid options to merge: cols (column count), row_height (px), "
                "compaction ('vertical'|'horizontal'|'none'), margin ([x,y] px)."
            ),
            "additionalProperties": True,
        },
    },
    "required": ["options"],
    "additionalProperties": False,
}

_SET_BACKGROUND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": _SPEC_PROP,
        "background": {
            "description": (
                "Dashboard background. Either a CSS string (colour like '#0b0f1a', "
                "a 'linear-gradient(...)', or 'transparent') or an object such as "
                "{type:'image',url:'...'} or {type:'gradient',from:'#111',to:'#333'}."
            ),
        },
    },
    "required": ["background"],
    "additionalProperties": False,
}

_ADD_VARIABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": _SPEC_PROP,
        "name": {"type": "string", "description": "Unique variable name."},
        "type": {
            "type": "string",
            "enum": _VAR_TYPE_ENUM,
            "description": "Variable value type.",
        },
        "default": {"description": "Optional default value for the variable."},
    },
    "required": ["name", "type"],
    "additionalProperties": False,
}

_SET_DRILLDOWN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spec": _SPEC_PROP,
        "widget_id": {
            "type": "string",
            "description": "Widget whose clicks drive the drilldown.",
        },
        "target_var": {
            "type": "string",
            "description": "Variable the clicked value is written to.",
        },
        "value_field": {
            "type": "string",
            "description": "Result column whose value is captured on click.",
        },
    },
    "required": ["widget_id", "target_var", "value_field"],
    "additionalProperties": False,
}

_REGISTER_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Human-readable query label."},
        "sql": {
            "type": "string",
            "description": (
                "SELECT SQL. Named placeholders use {{name}} syntax (resolved to "
                "positional params at execution)."
            ),
        },
        "id": {
            "type": "string",
            "description": (
                "Optional stable id. When omitted a slug is derived from name. "
                "An existing id is overwritten (upsert)."
            ),
        },
        "params": {
            "type": "array",
            "description": "Declared named params for the {{name}} placeholders.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": _VAR_TYPE_ENUM},
                    "default": {},
                    "required": {"type": "boolean"},
                    "options_query_id": {"type": "string"},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        "datastore_id": {
            "type": "string",
            "description": "Optional datastore the query binds to (else demo data).",
        },
        "required_scope": {
            "type": "string",
            "description": "Optional extra scope required to run the query.",
        },
    },
    "required": ["name", "sql"],
    "additionalProperties": False,
}


def anthropic_tool_specs() -> list[dict[str, Any]]:
    """Return the tool definitions in Anthropic Messages API format."""
    return [
        {
            "name": "propose_dashboard_spec",
            "description": (
                "Generate a complete Nubi DashboardSpec (JSON) from a natural-"
                "language instruction — best for BUILDING A NEW dashboard from "
                "scratch. The returned spec references real registered query_ids "
                "and real column names. Call list_registered_queries first if you "
                "need to know which query_ids exist. For small edits to an EXISTING "
                "dashboard prefer the granular tools (add_widget, update_widget, …)."
            ),
            "input_schema": _PROPOSE_SPEC_SCHEMA,
        },
        {
            "name": "list_registered_queries",
            "description": (
                "List all registered queries (id, name, params) so you can wire "
                "dashboard widgets to real query_ids. Takes no arguments."
            ),
            "input_schema": _LIST_QUERIES_SCHEMA,
        },
        {
            "name": "register_query",
            "description": (
                "Create or update a registered query (same as POST /query/registry) "
                "and return its id so a widget can bind it via query_id. Use this "
                "when the data the user wants is not covered by an existing "
                "registered query."
            ),
            "input_schema": _REGISTER_QUERY_SCHEMA,
        },
        {
            "name": "add_widget",
            "description": (
                "Add a single widget to the current dashboard spec and return the "
                "updated spec. Auto-places the widget in the first free grid spot "
                "when pos is omitted and assigns a unique id. kpi/table/chart need "
                "a query_id; chart needs chart_type + encoding {x,y}; filter needs "
                "subtype + target_var; text needs content."
            ),
            "input_schema": _ADD_WIDGET_SCHEMA,
        },
        {
            "name": "update_widget",
            "description": (
                "Patch an existing widget by id and return the updated spec. Dict "
                "fields (props, encoding, style, params, pos) are merged; scalar "
                "fields (query_id, chart_type, type, content, …) are replaced. Use "
                "for retitling, rebinding a query, changing chart type, resizing, "
                "moving, etc."
            ),
            "input_schema": _UPDATE_WIDGET_SCHEMA,
        },
        {
            "name": "remove_widget",
            "description": (
                "Delete a widget by id from the current spec and return the updated "
                "spec."
            ),
            "input_schema": _REMOVE_WIDGET_SCHEMA,
        },
        {
            "name": "set_widget_style",
            "description": (
                "Merge style props into one widget (background incl. 'transparent', "
                "border, borderRadius, padding, boxShadow, …) and return the "
                "updated spec."
            ),
            "input_schema": _SET_WIDGET_STYLE_SCHEMA,
        },
        {
            "name": "set_layout",
            "description": (
                "Update the dashboard grid layout (cols, row_height, compaction, "
                "margin) and return the updated spec."
            ),
            "input_schema": _SET_LAYOUT_SCHEMA,
        },
        {
            "name": "set_background",
            "description": (
                "Set the dashboard-level background (solid colour, gradient, image, "
                "or 'transparent') and return the updated spec."
            ),
            "input_schema": _SET_BACKGROUND_SCHEMA,
        },
        {
            "name": "add_variable",
            "description": (
                "Declare (or update) a dashboard variable that filter widgets and "
                "drilldowns write to and other widgets bind via params {ref:name}. "
                "Returns the updated spec."
            ),
            "input_schema": _ADD_VARIABLE_SCHEMA,
        },
        {
            "name": "set_drilldown",
            "description": (
                "Configure click-to-drilldown on a widget: clicking captures "
                "value_field and writes it to target_var (declare the variable "
                "first with add_variable). Returns the updated spec."
            ),
            "input_schema": _SET_DRILLDOWN_SCHEMA,
        },
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_list_registered_queries() -> dict[str, Any]:
    """Read the live query registry — reuses ``app.queries.get_query_registry``."""
    from app.queries.registry import get_query_registry  # noqa: PLC0415

    registry = get_query_registry()
    queries = [
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
        for rq in registry.all()
    ]
    return {"queries": queries}


def _tool_propose_dashboard_spec(instruction: str) -> dict[str, Any]:
    """Generate a DashboardSpec — reuses ``app.ai.dashboard.generate_dashboard_spec``."""
    from app.ai.dashboard import generate_dashboard_spec  # noqa: PLC0415
    from app.ai.grounding import build_catalog  # noqa: PLC0415
    from app.ai.provider import get_provider  # noqa: PLC0415

    catalog = build_catalog()
    provider = get_provider()
    spec = generate_dashboard_spec(instruction, catalog, provider)
    spec_dict = spec.model_dump()
    return {
        "spec": spec_dict,
        "title": spec_dict.get("title", "Dashboard"),
        "widget_count": len(spec_dict.get("widgets", []) or []),
    }


def _slugify(name: str) -> str:
    """Derive a stable URL-safe id from *name* (mirrors POST /query/registry)."""
    slug = name.lower()
    slug = re.sub(r"[\s\-]+", "_", slug)
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    return slug.strip("_") or "query"


def _tool_register_query(arguments: dict[str, Any]) -> dict[str, Any]:
    """Register/upsert a query in the runtime registry (POST /query/registry path)."""
    from app.queries.registry import QueryParam, get_query_registry  # noqa: PLC0415

    name = str(arguments.get("name") or "").strip()
    sql = str(arguments.get("sql") or "").strip()
    if not name:
        return {"error": "name must not be empty."}
    if not sql:
        return {"error": "sql must not be empty."}

    raw_id = str(arguments.get("id") or "").strip()
    query_id = raw_id or _slugify(name)

    param_objs: list[QueryParam] = []
    for p in arguments.get("params") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        param_objs.append(
            QueryParam(
                name=str(p["name"]),
                type=p.get("type", "text"),
                default=p.get("default"),
                required=bool(p.get("required", False)),
                options_query_id=p.get("options_query_id"),
            )
        )

    datastore_id = arguments.get("datastore_id")
    registry = get_query_registry()
    rq = registry.register(
        id=query_id,
        sql=sql,
        name=name,
        required_scope=arguments.get("required_scope"),
        params=param_objs,
        datastore_id=str(datastore_id) if datastore_id else None,
    )
    return {
        "id": rq.id,
        "name": rq.name,
        "params": [{"name": p.name, "type": p.type, "required": p.required} for p in rq.params],
        "datastore_id": rq.datastore_id,
    }


# ── Spec-edit dispatch ─────────────────────────────────────────────────────
# Each handler returns the UPDATED spec dict.  ``_run_spec_op`` wraps it into
# the ``(summary_output, {"spec": updated})`` contract.


def _summarize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Compact, model-friendly summary of a spec (the big spec rides in *extra*)."""
    widgets = spec.get("widgets") or []
    return {
        "ok": True,
        "title": spec.get("title", "Dashboard"),
        "widget_count": len(widgets),
        "widgets": [
            {
                "id": w.get("id"),
                "type": w.get("type"),
                "query_id": w.get("query_id"),
                "chart_type": w.get("chart_type"),
            }
            for w in widgets
        ],
        "variables": [v.get("name") for v in (spec.get("variables") or []) if isinstance(v, dict)],
    }


def _do_add_widget(args: dict[str, Any]) -> dict[str, Any]:
    spec = args.get("spec")
    passthrough = {
        k: args[k]
        for k in ("subtype", "target_var", "options_query_id", "content", "params")
        if args.get(k) is not None
    }
    updated, new_id = spec_ops.add_widget(
        spec,
        type=str(args.get("type") or ""),
        query_id=args.get("query_id"),
        chart_type=args.get("chart_type"),
        encoding=args.get("encoding"),
        props=args.get("props"),
        style=args.get("style"),
        pos=args.get("pos"),
        **passthrough,
    )
    updated["_added_widget_id"] = new_id  # transient hint; stripped before return
    return updated


def _run_spec_op(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Dispatch a spec-edit tool, returning ``(summary, {"spec": updated})``."""
    if name == "add_widget":
        updated = _do_add_widget(arguments)
        new_id = updated.pop("_added_widget_id", None)
        summary = _summarize_spec(updated)
        if new_id:
            summary["added_widget_id"] = new_id
        return summary, {"spec": updated}

    if name == "update_widget":
        updated = spec_ops.update_widget(
            arguments.get("spec"),
            str(arguments.get("widget_id") or ""),
            arguments.get("patch") or {},
        )
    elif name == "remove_widget":
        updated = spec_ops.remove_widget(
            arguments.get("spec"), str(arguments.get("widget_id") or "")
        )
    elif name == "set_widget_style":
        updated = spec_ops.set_widget_style(
            arguments.get("spec"),
            str(arguments.get("widget_id") or ""),
            arguments.get("style") or {},
        )
    elif name == "set_layout":
        updated = spec_ops.set_layout(arguments.get("spec"), arguments.get("options") or {})
    elif name == "set_background":
        updated = spec_ops.set_background(arguments.get("spec"), arguments.get("background"))
    elif name == "add_variable":
        updated = spec_ops.add_variable(
            arguments.get("spec"),
            str(arguments.get("name") or ""),
            type=str(arguments.get("type") or "text"),
            default=arguments.get("default"),
        )
    elif name == "set_drilldown":
        updated = spec_ops.set_drilldown(
            arguments.get("spec"),
            str(arguments.get("widget_id") or ""),
            str(arguments.get("target_var") or ""),
            str(arguments.get("value_field") or ""),
        )
    else:  # pragma: no cover — guarded by caller
        return {"error": f"unknown spec op {name!r}"}, {}

    return _summarize_spec(updated), {"spec": updated}


_SPEC_OP_NAMES = frozenset(
    {
        "add_widget",
        "update_widget",
        "remove_widget",
        "set_widget_style",
        "set_layout",
        "set_background",
        "add_variable",
        "set_drilldown",
    }
)


def execute_tool(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute the tool *name* with *arguments*.

    Returns
    -------
    tuple[dict, dict]
        ``(output, extra)``.  *output* is the JSON-serialisable result returned to
        the model as the tool result.  *extra* carries side-channel data for the
        route — ``{"spec": {...}}`` when a dashboard spec was proposed/edited (so
        the final assistant message can include it for the frontend to apply).
    """
    if name == "list_registered_queries":
        return _tool_list_registered_queries(), {}

    if name == "register_query":
        return _tool_register_query(arguments), {}

    if name == "propose_dashboard_spec":
        instruction = str(arguments.get("instruction") or "").strip()
        if not instruction:
            return {"error": "instruction is required"}, {}
        result = _tool_propose_dashboard_spec(instruction)
        # The tool RESULT sent back to the model is a compact summary (the spec
        # is large and the model does not need to re-read every field); the full
        # spec rides in *extra* so the final message can carry it.
        spec = result["spec"]
        summary = {
            "ok": True,
            "title": result["title"],
            "widget_count": result["widget_count"],
            "widgets": [
                {"id": w.get("id"), "type": w.get("type"), "query_id": w.get("query_id")}
                for w in (spec.get("widgets") or [])
            ],
        }
        return summary, {"spec": spec}

    if name in _SPEC_OP_NAMES:
        try:
            return _run_spec_op(name, arguments)
        except spec_ops.SpecOpError as exc:
            return {"error": str(exc)}, {}

    return {"error": f"unknown tool {name!r}"}, {}


__all__ = ["anthropic_tool_specs", "execute_tool"]
