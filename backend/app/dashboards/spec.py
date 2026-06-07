"""Canonical Dashboard SPEC — shared format for the DnD editor and the LLM (Wave EDITOR-2A).

Public API
----------
WidgetPos
    Grid position/size for a widget.
Variable
    A dashboard-level variable (name, type, default).
Widget
    A single dashboard widget (kpi | table | chart | filter | text).
DashboardSpec
    The complete dashboard specification document.

validate_spec(data) -> (DashboardSpec | None, list[str])
    Parse a raw dict into a DashboardSpec, collecting all validation issues.

spec_to_html(spec) -> str
    Compile a DashboardSpec into a CSS-grid HTML fragment composed exclusively
    of ``<nubi-kpi>``, ``<nubi-table>``, ``<nubi-chart>``, ``<nubi-filter>``,
    and ``<nubi-text>`` custom elements.
    The output survives the frontend DOMPurify sanitizer in
    ``src/dashboards/sanitize.js``.

spec_json_schema() -> dict
    Return the JSON Schema for DashboardSpec (for grounding the LLM).

Security notes
--------------
- ``spec_to_html`` never emits ``<script>`` tags or ``on*=`` handlers.
- All string values written into HTML attributes are escaped with
  ``html.escape``.
- The ``style`` attribute used for grid layout contains only known-safe CSS
  property values (integers, units) — no user-supplied strings are injected
  into style values.
"""

from __future__ import annotations

import html
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic v2 models
# ---------------------------------------------------------------------------


class WidgetPos(BaseModel):
    """Grid position and size for a widget on the dashboard canvas.

    All values are in CSS-grid units:
      x / y   — 1-based column / row start index.
      w / h   — span in columns / rows.
    """

    x: int = Field(ge=1, description="Column start (1-based).")
    y: int = Field(ge=1, description="Row start (1-based).")
    w: int = Field(ge=1, description="Column span.")
    h: int = Field(ge=1, description="Row span.")


class Variable(BaseModel):
    """A dashboard-level variable.

    Variables are declared at the spec level and can be referenced by widget
    ``params`` via ``{ref: '<varName>'}``.  Filter widgets write to variables;
    data widgets re-query when their referenced variables change.

    Attributes
    ----------
    name:
        Unique variable name within this spec (e.g. ``"region"``).
    type:
        Value type — ``'text'``, ``'number'``, ``'date'``, ``'daterange'``,
        ``'select'``, or ``'multiselect'``.
    default:
        Optional default value for the variable.
    """

    name: str = Field(min_length=1, description="Unique variable name.")
    type: Literal["text", "number", "date", "daterange", "select", "multiselect"] = (
        Field(description="Variable value type.")
    )
    default: Any = Field(default=None, description="Default value for the variable.")


class Widget(BaseModel):
    """A single dashboard widget.

    Attributes
    ----------
    id:
        Stable, unique string identifier within this spec (e.g. ``"w1"``).
    type:
        Widget kind — ``'kpi'``, ``'table'``, ``'chart'``, ``'filter'``,
        or ``'text'``.
    query_id:
        Registered query id that backs this widget (must exist in the
        registry).  Required for ``kpi``, ``table``, ``chart``; optional for
        ``filter`` (when it uses ``options_query_id`` instead) and ``text``.
    chart_type:
        Chart variant — required when ``type == 'chart'``.  One of
        ``'line'``, ``'bar'``, ``'scatter'``, ``'area'``, ``'pie'``.
    encoding:
        Column encoding map.  For charts: ``x``, ``y`` (required), optionally
        ``color``.  For KPI: ``value`` (alias for the value column).
    props:
        Arbitrary extra widget props (e.g. ``label``, ``limit``, ``format``).
    pos:
        Grid position/size.
    subtype:
        Filter widget sub-type — ``'select'``, ``'multiselect'``,
        ``'daterange'``, or ``'text'``.  Required when ``type == 'filter'``.
    options_query_id:
        Registered query id that provides option values for a ``filter``
        widget of sub-type ``'select'`` or ``'multiselect'``.  Optional.
    target_var:
        The variable name this filter writes to.  Required when
        ``type == 'filter'``.
    content:
        Markdown content for a ``text`` widget.  Required when
        ``type == 'text'``.
    params:
        Named parameter bindings for this widget.  Each value is either a
        ``{ref: '<varName>'}`` reference or a literal scalar.  Ref names must
        resolve to declared ``variables`` on the spec.
    """

    id: str = Field(min_length=1)
    type: Literal[
        "kpi", "table", "chart", "filter", "text",
        # Extended widget types (rendered by the frontend SpecRenderer):
        "metric", "pivot", "section", "html",
    ]
    query_id: str = Field(default="", description="Backing query id (empty for text widgets).")
    chart_type: Literal["line", "bar", "scatter", "area", "pie"] | None = None
    encoding: dict[str, str] = Field(default_factory=dict)
    props: dict[str, Any] = Field(default_factory=dict)
    pos: WidgetPos
    # ── drawer / drilldown placement ──────────────────────────────────────
    # A widget with ``drawer=True`` is NOT placed on the main grid; instead it
    # is rendered inside a slide-out drawer keyed by ``drawer_group``:
    #   - ``"filters"``        — the shared dashboard filters drawer.
    #   - ``"dg_<id>"``        — a drilldown drawer opened by a trigger widget
    #                            (a ``section`` widget whose props carry
    #                            ``drilldown_group`` matching this id).
    # ``order`` sorts widgets within a drawer.
    drawer: bool = Field(default=False, description="Render inside a drawer, not the grid.")
    drawer_group: str | None = Field(
        default=None,
        description="Drawer this widget belongs to ('filters' or a 'dg_*' drilldown id).",
    )
    order: int = Field(default=0, description="Sort order within a drawer.")
    # filter-specific fields
    subtype: Literal["select", "multiselect", "daterange", "text"] | None = Field(
        default=None,
        description="Filter sub-type (required for filter widgets).",
    )
    options_query_id: str | None = Field(
        default=None,
        description="Query providing dropdown options for select/multiselect filters.",
    )
    target_var: str | None = Field(
        default=None,
        description="Variable name this filter widget writes to.",
    )
    # text-specific fields
    content: str | None = Field(
        default=None,
        description="Markdown content for text widgets.",
    )
    # variable binding
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Named param bindings: {paramName: {ref:'<varName>'} | <literal>}."
        ),
    )


class DashboardSpec(BaseModel):
    """Canonical dashboard specification.

    This is the single source of truth for both the drag-and-drop editor and
    the LLM authoring pipeline.

    Attributes
    ----------
    version:
        Schema version.  Currently ``1``.
    title:
        Human-readable dashboard title.
    layout:
        Grid layout config.  ``cols`` defaults to 12; ``row_height`` to 60 (px).
    variables:
        Optional list of dashboard-level variables.  Widgets can reference
        these via ``params: {paramName: {ref: '<varName>'}}``.
    widgets:
        Ordered list of widgets to render on the dashboard.
    """

    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1)
    layout: dict[str, Any] = Field(
        default_factory=lambda: {"cols": 12, "row_height": 60}
    )
    variables: list[Variable] = Field(
        default_factory=list,
        description="Dashboard-level variables (optional).",
    )
    widgets: list[Widget] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# validate_spec
# ---------------------------------------------------------------------------


def validate_spec(data: Any) -> tuple[DashboardSpec | None, list[str]]:
    """Parse and validate a raw dict as a DashboardSpec.

    Validation steps
    ----------------
    1. Pydantic model parse — field types, required fields, enum values.
    2. Widget ``id`` uniqueness — duplicate ids produce a warning.
    3. Chart widgets must have ``chart_type`` and ``encoding`` with at least
       ``x`` and ``y`` keys.
    4. Filter widgets must have ``subtype`` and ``target_var``.
    5. Text widgets must have ``content``.
    6. Widget ``params`` that use ``{ref: '<varName>'}`` must reference a
       declared variable name — undeclared refs are a hard error.
    7. Each ``query_id`` is checked against the live query registry.
       Unknown ids produce a warning (not a hard failure — forward compat).

    Parameters
    ----------
    data:
        Raw Python dict (e.g. parsed from JSON).

    Returns
    -------
    tuple[DashboardSpec | None, list[str]]
        ``(spec, [])``          — valid spec, no issues.
        ``(None, [issue, ...])``— parse failure (Pydantic errors).
        ``(spec, [issue, ...])``— parse succeeded but soft warnings exist.
    """
    issues: list[str] = []

    # ── Step 1: Pydantic parse ─────────────────────────────────────────────
    try:
        spec = DashboardSpec.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError or similar
        # Convert Pydantic errors to human-readable strings.
        try:
            from pydantic import ValidationError  # noqa: PLC0415

            if isinstance(exc, ValidationError):
                for err in exc.errors():
                    loc = ".".join(str(p) for p in err["loc"])
                    issues.append(f"Field '{loc}': {err['msg']}")
            else:
                issues.append(str(exc))
        except ImportError:
            issues.append(str(exc))
        return None, issues

    # Build a set of declared variable names for ref-checking.
    declared_var_names: set[str] = {v.name for v in spec.variables}

    # ── Step 2: Unique widget ids ──────────────────────────────────────────
    seen_ids: set[str] = set()
    for widget in spec.widgets:
        if widget.id in seen_ids:
            issues.append(
                f"Duplicate widget id {widget.id!r} — widget ids must be unique."
            )
        seen_ids.add(widget.id)

    # ── Step 3: Chart widget requirements ────────────────────────────────
    for widget in spec.widgets:
        if widget.type == "chart":
            if widget.chart_type is None:
                issues.append(
                    f"Widget {widget.id!r} (chart): 'chart_type' is required "
                    "for chart widgets."
                )
            if "x" not in widget.encoding:
                issues.append(
                    f"Widget {widget.id!r} (chart): encoding must include 'x' column."
                )
            if "y" not in widget.encoding:
                issues.append(
                    f"Widget {widget.id!r} (chart): encoding must include 'y' column."
                )

    # ── Step 4: Filter widget requirements ───────────────────────────────
    for widget in spec.widgets:
        if widget.type == "filter":
            if widget.subtype is None:
                issues.append(
                    f"Widget {widget.id!r} (filter): 'subtype' is required "
                    "for filter widgets ('select'|'multiselect'|'daterange'|'text')."
                )
            if not widget.target_var:
                issues.append(
                    f"Widget {widget.id!r} (filter): 'target_var' is required "
                    "for filter widgets."
                )

    # ── Step 5: Text widget requirements ─────────────────────────────────
    for widget in spec.widgets:
        if widget.type == "text":
            if not widget.content:
                issues.append(
                    f"Widget {widget.id!r} (text): 'content' is required "
                    "for text widgets."
                )

    # ── Step 6: Widget params ref validation (hard error) ─────────────────
    for widget in spec.widgets:
        for param_name, param_val in widget.params.items():
            if isinstance(param_val, dict) and "ref" in param_val:
                ref_var = param_val["ref"]
                if ref_var not in declared_var_names:
                    issues.append(
                        f"Widget {widget.id!r} param {param_name!r}: "
                        f"ref {ref_var!r} is not declared in spec 'variables'. "
                        f"Declared variables: {sorted(declared_var_names) or '[]'}."
                    )

    # ── Step 7: Query id registry check (soft warning) ───────────────────
    try:
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        registry = get_query_registry()
        known_ids = {rq.id for rq in registry.all()}
        for widget in spec.widgets:
            # Only check non-empty query_ids; text/filter may have empty query_id.
            if widget.query_id and widget.query_id not in known_ids:
                issues.append(
                    f"Widget {widget.id!r}: query_id {widget.query_id!r} is not in "
                    "the registered query registry (may be a forward reference)."
                )
            # Also check options_query_id for filter widgets.
            if widget.options_query_id and widget.options_query_id not in known_ids:
                issues.append(
                    f"Widget {widget.id!r}: options_query_id "
                    f"{widget.options_query_id!r} is not in the registered query "
                    "registry (may be a forward reference)."
                )
    except Exception:  # noqa: BLE001 — registry unavailable; skip silently
        pass

    return spec, issues


# ---------------------------------------------------------------------------
# spec_to_html
# ---------------------------------------------------------------------------

# Sanitizer-safe inline style template for the grid container.
_GRID_CONTAINER_STYLE = (
    "display:grid;"
    "grid-template-columns:repeat({cols},1fr);"
    "grid-auto-rows:{row_height}px;"
    "gap:1rem;"
    "padding:1rem;"
)

_WIDGET_WRAPPER_STYLE = (
    "grid-column:{col_start}/span {col_span};"
    "grid-row:{row_start}/span {row_span};"
)


def _esc(value: Any) -> str:
    """HTML-attribute-safe escape for a value."""
    return html.escape(str(value), quote=True)


def _kpi_tag(widget: Widget) -> str:
    """Render a ``<nubi-kpi>`` element from a KPI widget."""
    query_id = _esc(widget.query_id)
    # value-col: from encoding['value'] or encoding['y'] or props['value_col']
    value_col = (
        widget.encoding.get("value")
        or widget.encoding.get("y")
        or widget.props.get("value_col", "")
        or widget.encoding.get("x", "value")
    )
    label = widget.props.get("label", value_col.replace("_", " ").title())
    fmt = widget.props.get("format", "")

    parts = [f'<nubi-kpi query-id="{query_id}"']
    if value_col:
        parts.append(f' value-col="{_esc(value_col)}"')
    if label:
        parts.append(f' label="{_esc(label)}"')
    if fmt:
        parts.append(f' format="{_esc(fmt)}"')
    parts.append("></nubi-kpi>")
    return "".join(parts)


def _table_tag(widget: Widget) -> str:
    """Render a ``<nubi-table>`` element from a table widget."""
    query_id = _esc(widget.query_id)
    limit = widget.props.get("limit", 50)
    columns = widget.props.get("columns", "")

    parts = [f'<nubi-table query-id="{query_id}" limit="{_esc(limit)}"']
    if columns:
        if isinstance(columns, list):
            columns = ",".join(str(c) for c in columns)
        parts.append(f' columns="{_esc(columns)}"')
    parts.append("></nubi-table>")
    return "".join(parts)


def _chart_tag(widget: Widget) -> str:
    """Render a ``<nubi-chart>`` element from a chart widget."""
    query_id = _esc(widget.query_id)
    # chart_type: line|bar|scatter|area|pie → map area→line (no area in embed), pie→bar
    chart_type = widget.chart_type or "scatter"
    # Normalize to the subset supported by nubi-chart.js: scatter|line|bar
    _type_map = {"area": "line", "pie": "bar"}
    embed_type = _type_map.get(chart_type, chart_type)

    x_col = _esc(widget.encoding.get("x", "x"))
    y_col = _esc(widget.encoding.get("y", "y"))
    color_col = widget.encoding.get("color", "")

    parts = [
        f'<nubi-chart query-id="{query_id}"'
        f' type="{_esc(embed_type)}"'
        f' x="{x_col}"'
        f' y="{y_col}"'
    ]
    if color_col:
        parts.append(f' color="{_esc(color_col)}"')
    parts.append("></nubi-chart>")
    return "".join(parts)


def _filter_tag(widget: Widget) -> str:
    """Render a ``<nubi-filter>`` element from a filter widget.

    Emitted attributes
    ------------------
    subtype:
        Filter sub-type (``select`` | ``multiselect`` | ``daterange`` | ``text``).
    target-var:
        The variable name this filter writes to.
    query-id:
        Optional — backing query for the widget (if set and non-empty).
    options-query-id:
        Optional — query that provides select/multiselect option values.
    label:
        Human-readable label from ``props.label`` (if set).
    """
    parts = ["<nubi-filter"]
    if widget.subtype:
        parts.append(f' subtype="{_esc(widget.subtype)}"')
    if widget.target_var:
        parts.append(f' target-var="{_esc(widget.target_var)}"')
    if widget.query_id:
        parts.append(f' query-id="{_esc(widget.query_id)}"')
    if widget.options_query_id:
        parts.append(f' options-query-id="{_esc(widget.options_query_id)}"')
    label = widget.props.get("label", "")
    if label:
        parts.append(f' label="{_esc(label)}"')
    parts.append("></nubi-filter>")
    return "".join(parts)


def _text_tag(widget: Widget) -> str:
    """Render a ``<nubi-text>`` element from a text widget.

    The markdown ``content`` is placed as the text content of the element,
    HTML-escaped so it is safe for innerHTML.  The frontend custom element is
    responsible for rendering the markdown.
    """
    content = html.escape(widget.content or "", quote=False)
    return f"<nubi-text>{content}</nubi-text>"


def spec_to_html(spec: DashboardSpec) -> str:
    """Compile a DashboardSpec to a sanitizer-safe CSS-grid HTML fragment.

    The output consists exclusively of standard layout tags (``<div>``) and
    the five allowed ``<nubi-*>`` custom elements.  It contains:

    - No ``<script>`` tags.
    - No ``on*=`` inline event handlers.
    - No ``javascript:`` or ``data:`` URIs.
    - Only attributes from the sanitizer's allowlist (``query-id``,
      ``value-col``, ``label``, ``format``, ``limit``, ``columns``, ``type``,
      ``x``, ``y``, ``color``, ``subtype``, ``target-var``,
      ``options-query-id``, ``style``, ``class``).

    Grid layout is expressed via inline ``style`` attributes containing only
    numeric values and CSS grid shorthand — safe per the sanitizer config.

    Parameters
    ----------
    spec:
        A validated ``DashboardSpec`` instance.

    Returns
    -------
    str
        HTML fragment starting with ``<div class="nubi-dashboard">``.
    """
    cols = int(spec.layout.get("cols", 12))
    row_height = int(spec.layout.get("row_height", 60))

    container_style = _GRID_CONTAINER_STYLE.format(
        cols=cols,
        row_height=row_height,
    )

    title_safe = html.escape(spec.title, quote=False)

    lines: list[str] = [
        f'<div class="nubi-dashboard" style="{container_style}">',
        f'  <div class="nubi-dashboard__title" style="grid-column:1/-1;">'
        f'<h2 style="margin:0;font-size:1.25rem;font-weight:600;">'
        f"{title_safe}</h2></div>",
    ]

    for widget in spec.widgets:
        pos = widget.pos
        wrapper_style = _WIDGET_WRAPPER_STYLE.format(
            col_start=pos.x,
            col_span=pos.w,
            row_start=pos.y,
            row_span=pos.h,
        )

        # Build the inner nubi-* element.
        if widget.type == "kpi":
            inner = _kpi_tag(widget)
        elif widget.type == "table":
            inner = _table_tag(widget)
        elif widget.type == "chart":
            inner = _chart_tag(widget)
        elif widget.type == "filter":
            inner = _filter_tag(widget)
        elif widget.type == "text":
            inner = _text_tag(widget)
        else:
            # Unreachable due to Literal type, but be defensive.
            continue

        lines.append(
            f'  <div class="nubi-widget nubi-widget--{widget.type}"'
            f' style="{wrapper_style}">{inner}</div>'
        )

    lines.append("</div>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# spec_json_schema
# ---------------------------------------------------------------------------


def spec_json_schema() -> dict[str, Any]:
    """Return the JSON Schema for DashboardSpec.

    Used to ground the LLM: the schema is injected into the system prompt so
    the model knows the exact format it must emit.

    Returns
    -------
    dict
        JSON Schema dict (Pydantic v2 ``model_json_schema()`` output).
    """
    return DashboardSpec.model_json_schema()
