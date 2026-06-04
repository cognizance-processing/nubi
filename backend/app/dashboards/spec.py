"""Canonical Dashboard SPEC — shared format for the DnD editor and the LLM (Wave EDITOR-2A).

Public API
----------
WidgetPos
    Grid position/size for a widget.
Widget
    A single dashboard widget (kpi | table | chart).
DashboardSpec
    The complete dashboard specification document.

validate_spec(data) -> (DashboardSpec | None, list[str])
    Parse a raw dict into a DashboardSpec, collecting all validation issues.

spec_to_html(spec) -> str
    Compile a DashboardSpec into a CSS-grid HTML fragment composed exclusively
    of ``<nubi-kpi>``, ``<nubi-table>``, and ``<nubi-chart>`` custom elements.
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


class Widget(BaseModel):
    """A single dashboard widget.

    Attributes
    ----------
    id:
        Stable, unique string identifier within this spec (e.g. ``"w1"``).
    type:
        Widget kind — ``'kpi'``, ``'table'``, or ``'chart'``.
    query_id:
        Registered query id that backs this widget (must exist in the registry).
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
    """

    id: str = Field(min_length=1)
    type: Literal["kpi", "table", "chart"]
    query_id: str = Field(min_length=1)
    chart_type: Literal["line", "bar", "scatter", "area", "pie"] | None = None
    encoding: dict[str, str] = Field(default_factory=dict)
    props: dict[str, Any] = Field(default_factory=dict)
    pos: WidgetPos


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
    widgets:
        Ordered list of widgets to render on the dashboard.
    """

    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1)
    layout: dict[str, Any] = Field(
        default_factory=lambda: {"cols": 12, "row_height": 60}
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
    4. Each ``query_id`` is checked against the live query registry.
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

    # ── Step 4: Query id registry check (soft warning) ───────────────────
    try:
        from app.queries.registry import get_query_registry  # noqa: PLC0415

        registry = get_query_registry()
        known_ids = {rq.id for rq in registry.all()}
        for widget in spec.widgets:
            if widget.query_id not in known_ids:
                issues.append(
                    f"Widget {widget.id!r}: query_id {widget.query_id!r} is not in "
                    "the registered query registry (may be a forward reference)."
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


def spec_to_html(spec: DashboardSpec) -> str:
    """Compile a DashboardSpec to a sanitizer-safe CSS-grid HTML fragment.

    The output consists exclusively of standard layout tags (``<div>``) and
    the three allowed ``<nubi-*>`` custom elements.  It contains:

    - No ``<script>`` tags.
    - No ``on*=`` inline event handlers.
    - No ``javascript:`` or ``data:`` URIs.
    - Only attributes from the sanitizer's allowlist (``query-id``,
      ``value-col``, ``label``, ``format``, ``limit``, ``columns``, ``type``,
      ``x``, ``y``, ``color``, ``style``, ``class``).

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
