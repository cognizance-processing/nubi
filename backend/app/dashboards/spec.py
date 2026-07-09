"""Canonical Dashboard SPEC — shared format for the DnD editor and the LLM (Wave EDITOR-2A).

Public API
----------
WidgetPos
    Grid position/size for a widget.
Variable
    A dashboard-level variable (name, type, default).
Widget
    A single dashboard widget (kpi | table | chart | filter | text).
ProviderResult
    A single named result-set within a DataProvider (name + optional grain).
DataProvider
    Board-level semantic-engine data provider (BET-3). Widgets may bind to a
    provider result via ``widget.source`` instead of (or instead of) ``query_id``
    / ``metric``.  Board-level: ``spec.data`` holds the declared providers.
DashboardSpec
    The complete dashboard specification document.
SurfaceGridEntry
    Per-widget layout entry in surfaces.grid ({x, y, w, h, breakpoint}).
SurfaceGrid
    The grid surface layout: widgetId -> SurfaceGridEntry.
Surfaces
    Container for per-surface layout: {grid}. Nubi ships one dashboard
    authoring surface (the grid) — no report/presentation surfaces.
WidgetExportHints
    Per-widget export/PDF hints (T5).
BoardExportConfig
    Per-board export/PDF config (T5).

validate_spec(data) -> (DashboardSpec | None, list[str])
    Parse a raw dict into a DashboardSpec, collecting all validation issues.

get_surface_layout(spec, surface) -> dict[str, SurfaceGridEntry]
    Backward-compatible accessor: returns spec.surfaces.grid when present, else
    derives the grid layout from the legacy inline widget.pos fields. Surface
    must be 'grid' (only surface defined).

migrate_spec_to_surfaces(spec) -> DashboardSpec
    1:1 migration: populate spec.surfaces.grid from the current inline widget.pos
    values (for any widget that carries a pos). The widgets list is unchanged.
    Returns a NEW DashboardSpec; the original is not mutated.

get_export_config(spec) -> dict
    Return a normalized export/report config dict for the board. Absent or
    partial config is filled in with sensible defaults so callers never need
    to handle missing keys. (T5)

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

DataProvider / semantic-engine contract (BET-3 — SPEC MODELS ONLY)
-------------------------------------------------------------------
The ``DataProvider`` model and the ``widget.source`` binding are the *binding
contract* introduced in BET-3.  The resolver, routes, and SpecRenderer are
implemented in a LATER wave:

  Resolver  : backend/app/dashboards/board_data.py
              ``resolve_provider(provider, params, rls_hash) -> dict[str, ArrowTable]``
              — executes the provider (flow / inline CTE), caches by
              ``(provider_id, frozen_params, rls_hash)``, returns one Arrow table
              per ``results[*].name``.

  Route     : POST /boards/{id}/providers/{pid}/data
              Body: ``{params: {...}}``  (RLS comes ONLY from the verified token,
              never from the request body — INVARIANT).
              Response: multi-table Arrow IPC stream (one batch per result-set
              name). Cache key: ``(pid, frozen_params, rls_hash)``.

  SpecRenderer: groups widgets by ``widget.source.provider``, fetches each
                provider once, then fans results to the bound widgets.
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


# ---------------------------------------------------------------------------
# Surface layout models (T1 — additive container model)
# ---------------------------------------------------------------------------


class SurfaceGridEntry(BaseModel):
    """Per-widget layout entry inside surfaces.grid.

    Mirrors WidgetPos (same 1-based x/y/w/h units) plus an optional breakpoint
    token so future grid surfaces can carry multi-breakpoint overrides.

    All fields are optional with sensible defaults so a partially-specified
    entry remains valid (a grid surface need only carry the fields that differ
    from the widget's inline pos).
    """

    x: int = Field(ge=1, default=1, description="Column start (1-based).")
    y: int = Field(ge=1, default=1, description="Row start (1-based).")
    w: int = Field(ge=1, default=1, description="Column span.")
    h: int = Field(ge=1, default=1, description="Row span.")
    breakpoint: str | None = Field(
        default=None,
        description="Optional breakpoint token (e.g. 'lg', 'md', 'sm').",
    )


# SurfaceGrid maps widgetId -> SurfaceGridEntry.
# Using a plain dict[str, SurfaceGridEntry] keeps the JSON shape flat and
# identical to the legacy spec.responsive[bp] maps in responsiveLayout.js.
SurfaceGrid = dict[str, SurfaceGridEntry]


class Surfaces(BaseModel):
    """Container for per-surface layout data.

    Nubi ships one dashboard-authoring surface: the grid. The report
    (paginated document) and slides/presentation surfaces that used to live
    here have been removed — they were off-wedge authoring products. Scheduled
    email PDF delivery of a dashboard (``report_send``) is unaffected — it
    renders the grid surface, not a separate document.

    Attributes
    ----------
    grid:
        Mapping of widgetId -> SurfaceGridEntry for the grid (drag-and-drop)
        surface. This is the canonical replacement for inline widget.pos once
        a spec has been migrated. Un-migrated specs (widget.pos still inline)
        keep working via the backward-compatible accessor
        :func:`get_surface_layout`.
    """

    grid: SurfaceGrid = Field(
        default_factory=dict,
        description="widgetId → SurfaceGridEntry for the grid surface.",
    )


# ---------------------------------------------------------------------------
# Export / report layout config (T5 — additive, all defaults applied)
# ---------------------------------------------------------------------------


class WidgetExportHints(BaseModel):
    """Per-widget hints for the PDF export renderer.

    All fields are optional with sensible defaults so existing boards need no
    change — absent hints are identical to the defaults listed here.

    Attributes
    ----------
    include_in_report:
        Whether this widget appears in the generated PDF report.
        Defaults to ``True``. Set ``False`` to silently skip the widget.
    order:
        Override render order in the report.  ``None`` (default)
        means the widget appears in the same order as ``spec.widgets``.
    page_break_before:
        Insert a page break immediately before this widget.
        Defaults to ``False``.
    caption:
        Optional caption string rendered below the widget in the report.
        ``None`` (default) means no caption.
    render_as:
        Hint to the export renderer about the preferred output form.
        ``'auto'`` (default) lets the renderer decide based on widget type
        (charts → SVG chart, tables → native table, KPI → styled cell, …).
        ``'chart'`` forces chart rendering even for table/kpi widgets.
        ``'table'`` forces tabular rendering (raw data) for any widget type.
    """

    include_in_report: bool = Field(
        default=True,
        description="Include this widget in the PDF export (default True).",
    )
    order: int | None = Field(
        default=None,
        description="Override render order in the report (None = spec order).",
    )
    page_break_before: bool = Field(
        default=False,
        description="Insert a page break before this widget.",
    )
    caption: str | None = Field(
        default=None,
        description="Caption rendered below the widget in the report (None = no caption).",
    )
    render_as: Literal["auto", "chart", "table"] = Field(
        default="auto",
        description=(
            "Preferred output form for the export renderer. "
            "'auto' delegates to the renderer; 'chart' forces SVG chart; "
            "'table' forces tabular/raw-data output."
        ),
    )


class TitleSlideConfig(BaseModel):
    """Config for the auto-generated title page in a PDF report.

    ``None`` at the board level means no title slide is inserted.

    Attributes
    ----------
    heading:
        Main heading text.  Defaults to the board ``title``.
    subheading:
        Optional sub-heading / tagline.
    """

    heading: str | None = Field(
        default=None,
        description="Title slide heading (defaults to board title when None).",
    )
    subheading: str | None = Field(
        default=None,
        description="Optional sub-heading / tagline for the title slide.",
    )


class BoardExportConfig(BaseModel):
    """Per-board export/report configuration (T5).

    Lives under ``spec.export`` (additive, absent → all defaults apply).

    Attributes
    ----------
    title_slide:
        Config for the auto-generated title page inserted at the start of a
        paginated PDF export.  ``None`` (default) means no title page.
    header:
        Optional page-header string (e.g. board name, date).  Rendered at the
        top of every page.  ``None`` (default) means no header.
    footer:
        Optional page-footer string (e.g. "Confidential — Nubi Analytics").
        Rendered at the bottom of every page.  ``None`` (default) means no
        footer.
    page_size:
        Paper/canvas size for the exported PDF.
        ``'A4'`` (default) — ISO A4 portrait (210 × 297 mm).
        ``'Letter'`` — US Letter portrait (8.5 × 11 in).
        ``'16:9'`` — Widescreen canvas (33.87 × 19.05 cm) for a landscape PDF.
    widget_hints:
        Per-widget export hints keyed by widget id.  Absent widget ids inherit
        the ``WidgetExportHints`` defaults.
    """

    title_slide: TitleSlideConfig | None = Field(
        default=None,
        description="Title-page config (None = no title page).",
    )
    header: str | None = Field(
        default=None,
        description="Page header string (None = no header).",
    )
    footer: str | None = Field(
        default=None,
        description="Page footer string (None = no footer).",
    )
    page_size: Literal["A4", "Letter", "16:9"] = Field(
        default="A4",
        description="Paper/canvas size for the exported PDF.",
    )
    widget_hints: dict[str, WidgetExportHints] = Field(
        default_factory=dict,
        description="Per-widget export hints keyed by widget id.",
    )


# ---------------------------------------------------------------------------
# Default WidgetExportHints instance (re-used by get_export_config)
# ---------------------------------------------------------------------------

_DEFAULT_WIDGET_HINTS = WidgetExportHints()


# ---------------------------------------------------------------------------
# DataProvider models (BET-3 — semantic-engine binding contract)
# ---------------------------------------------------------------------------

# Maximum number of declared results allowed on a single DataProvider in a spec.
# Mirrors the runtime _PROVIDER_MAX_TABLES cap in board_data.py so oversized
# provider specs are rejected at validation time, before they reach the resolver.
_PROVIDER_MAX_RESULTS_SPEC: int = 100

# Maximum byte length for a DataProvider.base_cte SQL preamble.  64 KiB is
# generous for any realistic CTE block; above that the planner re-parse cost
# per cache-miss becomes unreasonable.
_BASE_CTE_MAX_BYTES: int = 65_536

# Maximum number of DataProvider entries in DashboardSpec.data.  Enforced at
# parse time so oversized provider lists are rejected before store/execute.
_SPEC_MAX_DATA_PROVIDERS: int = 200


class ProviderResult(BaseModel):
    """A single named result-set produced by a :class:`DataProvider`.

    Attributes
    ----------
    name:
        Stable result-set name referenced by ``widget.source.result``.
        Must be unique within its parent provider's ``results`` list.
    grain:
        Optional grain descriptor (e.g. ``"day"``, ``"week"``, ``"sku"``).
        Purely documentary in this wave — the resolver (later wave) uses it to
        pick the matching CTE / aggregate level.
    """

    name: str = Field(min_length=1, description="Result-set name (unique within provider).")
    grain: str | None = Field(
        default=None,
        description="Optional grain descriptor (e.g. 'day', 'sku'). Documentary in BET-3.",
    )


class DataProvider(BaseModel):
    """Board-level semantic-engine data provider (BET-3 binding contract).

    A ``DataProvider`` declares a named data source that one or more widgets can
    bind to via ``widget.source = {provider: '<id>', result: '<name>'}``.  This
    is the governed alternative to per-widget ``query_id`` / ``metric`` bindings:
    the engine fetches each provider **once** per request, caches the result, and
    fans the named result-sets to all bound widgets (Power BI / Tableau model).

    Attributes
    ----------
    id:
        Stable, unique provider id within this board spec (e.g. ``"p1"``).
    kind:
        ``'flow'`` — the provider is backed by a registered Flow (the engine
        executes ``flow.run(params)`` and maps outputs to ``results`` by name).
        ``'inline'`` — the provider is a self-contained SQL fragment (see
        ``base_cte``); the resolver runs it directly.
    params:
        Parameter bindings for this provider.  Each value is either a
        ``{ref: '<varName>'}`` reference to a dashboard variable or a literal
        scalar.  The resolver merges these with the request-time param overrides.
    base_cte:
        Optional shared base CTE (``WITH ... AS (...)`` preamble) used when
        ``kind == 'inline'``.  ``None`` for flow-backed providers.
    results:
        Ordered list of named result-sets this provider emits.  Each widget
        that binds to this provider references one result by ``name``.

    Later-wave notes
    ----------------
    - The resolver (``board_data.py``) executes the provider and caches by
      ``(id, frozen_params, rls_hash)``.
    - RLS comes ONLY from the verified JWT token (never from the request body).
    - Route: ``POST /boards/{board_id}/providers/{pid}/data``.
    """

    id: str = Field(min_length=1, description="Stable, unique provider id within this spec.")
    kind: Literal["flow", "inline"] = Field(
        description="Provider backend: 'flow' (registered Flow) or 'inline' (SQL CTE).",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Parameter bindings: {paramName: {ref:'<varName>'} | <literal>}. "
            "Merged with request-time overrides by the resolver."
        ),
    )
    base_cte: str | None = Field(
        default=None,
        max_length=_BASE_CTE_MAX_BYTES,
        description=(
            "Shared base CTE SQL preamble for kind='inline' providers. "
            "None for flow-backed providers. "
            f"Maximum {_BASE_CTE_MAX_BYTES} bytes."
        ),
    )
    results: list[ProviderResult] = Field(
        default_factory=list,
        max_length=_PROVIDER_MAX_RESULTS_SPEC,
        description=(
            "Named result-sets emitted by this provider (at least one expected). "
            f"Maximum {_PROVIDER_MAX_RESULTS_SPEC} entries."
        ),
    )


class WidgetSource(BaseModel):
    """Provider-binding for a widget (BET-3 alternative to query_id / metric).

    Attributes
    ----------
    provider:
        The ``id`` of a :class:`DataProvider` declared in ``spec.data``.
    result:
        The ``name`` of a :class:`ProviderResult` within that provider.

    Validation
    ----------
    A widget may set AT MOST ONE of ``query_id`` (non-empty) / ``metric`` /
    ``source``.  Having more than one binding is a hard validation error
    (enforced in ``validate_spec`` step 11).
    """

    provider: str = Field(min_length=1, description="Provider id (must be in spec.data).")
    result: str = Field(min_length=1, description="Result-set name within the provider.")


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
    mode: Literal["scan", "slice"] | None = Field(
        default=None,
        description=(
            "Client-compute param class (CLIENT_COMPUTE_PLAN.md §2.1). "
            "'scan' (the default; None is treated as 'scan') means changing the "
            "param re-reads server-side data. 'slice' marks a param that subsets "
            "rows already fetched into the base result client-side (DuckDB-WASM) "
            "and is never sent to the server. Pure metadata for now — absent/None "
            "behaves exactly as today ('scan')."
        ),
    )


class Tab(BaseModel):
    """A dashboard tab (Track T — DASHBOARD_TABS_AND_FILTERS_IMPLEMENTATION.md T1).

    Tabs are sections inside one board spec (Option A): one board, one spec, one
    version history.  Variables remain dashboard-global (shared across all tabs);
    tabs are a render partition, not a scope.  An empty/absent ``spec.tabs`` list
    means the dashboard behaves exactly as today (no tabs).

    Attributes
    ----------
    id:
        Stable, unique string identifier within this spec (e.g. ``"t1"``).
    label:
        Human-readable tab label shown in the tab bar.
    style:
        Optional per-tab overrides of the tab-bar style tokens.  All
        user-supplied colors/CSS flow through the sanitized ``tabStyleToCss``
        path on the frontend — never interpolated raw.
    """

    id: str = Field(min_length=1, description="Stable, unique tab id within the spec.")
    label: str = Field(min_length=1, description="Human-readable tab label.")
    style: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-tab overrides of the tab-bar style tokens (optional).",
    )


class MetricBinding(BaseModel):
    """A governed-metric binding for a data widget (alternative to ``query_id``).

    Where ``query_id`` binds a widget to a registered SQL query, a
    ``MetricBinding`` binds it to a GOVERNED metric definition (see
    ``app/metrics/models.py``): the embed renderer executes it via
    ``POST /metrics/{metric_id}/query`` with a ``MetricQuery`` body, so the same
    governance (allowed dimensions / grains / filters) the metric routes enforce
    applies to the widget. The fields mirror :class:`app.metrics.models.MetricQuery`.

    Attributes
    ----------
    metric_id:
        The governed metric id to bind (checked against the live metric registry
        — a miss is a soft WARNING, mirroring the ``query_id`` forward-ref rule).
    dimensions:
        Dimensions to group by — a SUBSET of the metric's allowed ``dimensions``.
    time_grain:
        Optional time bucket — one of the metric's allowed ``time_grains``.
    filters:
        User filters as ``[{field, op, value}]`` dicts (mirror
        :class:`app.metrics.models.MetricFilter`; kept as dicts and validated
        lightly here — the metric routes are the authoritative governance gate).
    limit:
        Optional row cap forwarded to the metric query.
    """

    metric_id: str = Field(min_length=1, description="Governed metric id to bind.")
    dimensions: list[str] = Field(
        default_factory=list,
        description="Dimensions to group by (subset of the metric's allowed dims).",
    )
    time_grain: str | None = Field(
        default=None,
        description="Optional time bucket (one of the metric's allowed grains).",
    )
    filters: list[dict] = Field(
        default_factory=list,
        description="User filters as [{field, op, value}] dicts (MetricFilter shape).",
    )
    limit: int | None = Field(
        default=None,
        description="Optional row cap forwarded to the metric query.",
    )


class Widget(BaseModel):
    """A single dashboard widget.

    Attributes
    ----------
    id:
        Stable, unique string identifier within this spec (e.g. ``"w1"``).
    tab_id:
        Optional id of the tab this widget belongs to (Track T).  When
        ``spec.tabs`` is non-empty, a widget with ``tab_id is None`` implicitly
        belongs to the **first** tab.  Drawer widgets (``drawer=True``) ignore
        ``tab_id`` — drawers stay global across tabs.  ``tab_id`` must reference
        a tab declared in ``spec.tabs`` (hard error otherwise).
    type:
        Widget kind — ``'kpi'``, ``'table'``, ``'chart'``, ``'filter'``,
        or ``'text'``.
    query_id:
        Registered query id that backs this widget (must exist in the
        registry).  Required for ``kpi``, ``table``, ``chart`` — UNLESS the
        widget carries a ``metric`` binding instead.  Optional for ``filter``
        (when it uses ``options_query_id`` instead) and ``text``.
    metric:
        Optional governed-metric binding (:class:`MetricBinding`).  A data widget
        (``kpi``/``table``/``chart``/``metric``/``pivot``) must carry EITHER a
        non-empty ``query_id`` OR a ``metric`` binding.  Both may be set, but
        ``metric`` takes precedence (the embed renderer reads the metric
        attributes and ignores ``query-id`` when a metric binding is present).
    chart_type:
        Chart variant — required when ``type == 'chart'``.  One of
        ``'line'``, ``'bar'``, ``'hbar'``, ``'scatter'``, ``'area'``,
        ``'pie'``, ``'donut'``, ``'heatmap'``, ``'gauge'`` — the full set the
        frontend chart renderer (``src/viz/chartOption.js``) supports.
    encoding:
        Column encoding map.  For charts: ``x``, ``y`` (required), optionally
        ``color``.  For KPI: ``value`` (alias for the value column).
    props:
        Arbitrary extra widget props (e.g. ``label``, ``limit``, ``format``).
    pos:
        Grid position/size.  Used for ``placement == 'grid'`` widgets; ignored
        for ``'header'`` / ``'drawer'`` placed widgets (which use ``order``).
    placement:
        Where this widget renders relative to the grid (additive; default
        ``'grid'`` => unchanged behaviour):
          - ``'grid'``   — a normal layout widget positioned by ``pos``.
          - ``'header'`` — rendered in a horizontal FILTER BAR above the grid
            (below the tab bar), ordered by ``order`` and IGNORING ``pos``.
            Typically a filter or text widget. Tab scoping matches grid widgets
            (a header filter with a ``tab_id`` shows on that tab; ``tab_id`` None
            => first tab / global per the existing rules).
          - ``'drawer'`` — rendered inside the slide-over drawer, equivalent to
            the legacy ``drawer=True`` / ``drawer_group 'filters'`` mechanism.
        BACK-COMPAT: a widget with the legacy ``drawer=True`` and the DEFAULT
        ``placement`` ('grid', i.e. not explicitly set otherwise) has an
        EFFECTIVE placement of ``'drawer'``. Use :func:`effective_placement`
        (or :meth:`Widget.effective_placement`) to resolve this so callers do
        not re-derive it. ``drawer`` / ``drawer_group`` keep working unchanged.
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
    tab_id: str | None = Field(
        default=None,
        description=(
            "Tab this widget belongs to (Track T). None => first tab when "
            "spec.tabs is non-empty. Ignored for drawer widgets. Must reference "
            "a declared tab id."
        ),
    )
    query_id: str = Field(default="", description="Backing query id (empty for text widgets).")
    metric: MetricBinding | None = Field(
        default=None,
        description=(
            "Optional governed-metric binding. When set, it takes precedence over "
            "query_id for rendering (the embed renderer reads the metric-* attrs)."
        ),
    )
    chart_type: (
        Literal["line", "bar", "hbar", "scatter", "area", "pie", "donut", "heatmap", "gauge"]
        | None
    ) = None
    encoding: dict[str, str] = Field(default_factory=dict)
    props: dict[str, Any] = Field(default_factory=dict)
    pos: WidgetPos | None = Field(
        default=None,
        description=(
            "Grid position/size (legacy inline layout). Required for grid-placed "
            "widgets when spec.surfaces.grid is absent. When spec.surfaces.grid is "
            "present the entry for this widget id there is authoritative; pos is "
            "kept for backward-compat and round-trip fidelity."
        ),
    )
    # ── placement (grid / header filter-bar / drawer) ─────────────────────
    placement: Literal["grid", "header", "drawer"] = Field(
        default="grid",
        description=(
            "Where this widget renders relative to the grid. 'grid' (default) "
            "uses pos; 'header' renders in the horizontal filter bar above the "
            "grid (ordered by 'order', ignores pos); 'drawer' renders in the "
            "slide-over drawer. Legacy drawer=True with the default placement "
            "resolves to an effective placement of 'drawer' (see "
            "effective_placement)."
        ),
    )
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
    # ── DataProvider binding (BET-3, alternative to query_id / metric) ────────
    # A widget may use AT MOST ONE of: non-empty query_id / metric / source.
    # validate_spec step 11 enforces this; existing query_id/metric widgets are
    # unaffected (source defaults to None → absent in serialization).
    source: WidgetSource | None = Field(
        default=None,
        description=(
            "DataProvider binding (BET-3). When set, this widget's data comes "
            "from the referenced provider result instead of query_id / metric. "
            "A widget may set at most one of: non-empty query_id, metric, source."
        ),
    )

    def effective_placement(self) -> str:
        """Resolve this widget's effective placement ('grid'|'header'|'drawer').

        Back-compat: a widget with the legacy ``drawer=True`` and the DEFAULT
        ``placement`` ('grid') is treated as ``'drawer'``. An explicit
        ``placement`` always wins (so ``placement='header'`` is honoured even if
        ``drawer`` were somehow True). Otherwise the stored ``placement`` is
        returned verbatim.
        """
        return effective_placement(self)


def effective_placement(widget: "Widget") -> str:
    """Module-level resolver for a widget's effective placement.

    Returns one of ``'grid'`` | ``'header'`` | ``'drawer'``. The legacy
    ``drawer=True`` flag maps to ``'drawer'`` ONLY when ``placement`` is still
    the default ``'grid'`` (i.e. not explicitly set to something else), keeping
    every existing drawer spec rendering identically.
    """
    if widget.placement == "grid" and widget.drawer:
        return "drawer"
    return widget.placement


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
    data:
        Board-level DataProvider declarations (BET-3 semantic engine, additive).
        Empty list (default) → absent in serialization for legacy boards.
        Widgets may bind to a provider result via ``widget.source``.

    Serialization / back-compat notes
    ----------------------------------
    - ``data`` defaults to ``[]``.  Legacy boards that never set it round-trip
      with ``data=[]`` in the object but the key may be omitted from the raw
      dict — ``model_validate`` handles either form.
    - ``widget.source`` defaults to ``None``.  Existing boards that never set
      it round-trip byte-identical (``source`` is absent from the serialized
      widget dict when ``None`` via ``exclude_none`` serialization).
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
    tabs: list[Tab] = Field(
        default_factory=list,
        description=(
            "Optional dashboard tabs (Track T). Empty/absent => no tabs, behaves "
            "exactly as today. Widgets bind to a tab via 'tab_id'; widgets with "
            "tab_id None implicitly belong to the first tab when tabs exist."
        ),
    )
    widgets: list[Widget] = Field(default_factory=list)
    surfaces: Surfaces = Field(
        default_factory=Surfaces,
        description=(
            "Per-surface layout containers (additive, T1). "
            "surfaces.grid holds widgetId → {x,y,w,h} for the grid surface. "
            "Un-migrated specs (widget.pos still inline) have an empty/absent "
            "surfaces.grid — the backward-compatible accessor "
            "get_surface_layout(spec, 'grid') derives the layout from inline pos."
        ),
    )
    export: BoardExportConfig = Field(
        default_factory=BoardExportConfig,
        description=(
            "Per-board export/report configuration (T5). Absent or empty → "
            "all defaults apply. Use get_export_config(spec) for a normalized "
            "dict with per-widget hints merged with their defaults."
        ),
    )
    data: list[DataProvider] = Field(
        default_factory=list,
        max_length=_SPEC_MAX_DATA_PROVIDERS,
        description=(
            "Board-level DataProvider declarations (BET-3 semantic engine). "
            "Empty list (default) = absent / not configured. Each entry declares "
            "a named data source that widgets can bind to via 'widget.source'. "
            "See DataProvider for the full contract and the later-wave notes on "
            f"the resolver + route. Maximum {_SPEC_MAX_DATA_PROVIDERS} entries."
        ),
    )


# ---------------------------------------------------------------------------
# get_surface_layout — backward-compatible accessor (T1)
# ---------------------------------------------------------------------------


def get_surface_layout(spec: "DashboardSpec", surface: str) -> SurfaceGrid:
    """Return the layout map for *surface*, with full backward compatibility.

    For the ``'grid'`` surface:

    1. If ``spec.surfaces.grid`` is non-empty, return it **as-is** (the spec
       has already been migrated / authored with the new model).
    2. Otherwise derive the layout from the legacy inline ``widget.pos`` fields
       (un-migrated board): each widget whose ``pos`` is not ``None`` contributes
       a :class:`SurfaceGridEntry`.  Widgets without a ``pos`` are silently
       skipped (header/drawer widgets legitimately have no grid position).

    This means un-migrated boards keep working identically — callers never need
    to distinguish between the two formats.

    Parameters
    ----------
    spec:
        A parsed (or freshly constructed) :class:`DashboardSpec`.
    surface:
        The surface to retrieve. Only ``'grid'`` is defined; any other value
        raises :class:`ValueError`.

    Returns
    -------
    SurfaceGrid
        ``{widgetId: SurfaceGridEntry}`` mapping.

    Raises
    ------
    ValueError
        When *surface* is not ``'grid'``.
    """
    if surface != "grid":
        raise ValueError(f"Unknown surface {surface!r}. Known surfaces: 'grid'.")

    # ── New-format: surfaces.grid is authoritative ─────────────────────────
    if spec.surfaces.grid:
        return spec.surfaces.grid

    # ── Legacy fallback: derive from inline widget.pos ────────────────────
    layout: SurfaceGrid = {}
    for widget in spec.widgets:
        if widget.pos is None:
            continue  # header/drawer widgets have no grid pos
        p = widget.pos
        layout[widget.id] = SurfaceGridEntry(x=p.x, y=p.y, w=p.w, h=p.h)
    return layout


# ---------------------------------------------------------------------------
# migrate_spec_to_surfaces — 1:1 migration helper (T1)
# ---------------------------------------------------------------------------


def migrate_spec_to_surfaces(spec: "DashboardSpec") -> "DashboardSpec":
    """Populate ``spec.surfaces.grid`` from the current inline widget.pos values.

    This is a **1:1 additive migration**: no widget data is removed or altered.
    The resulting spec is identical to the original in every way *except* that
    ``surfaces.grid`` now contains a complete layout map derived from
    ``widget.pos``.

    A spec that already has a non-empty ``surfaces.grid`` is returned unchanged
    (idempotent — safe to call repeatedly).

    Parameters
    ----------
    spec:
        A parsed :class:`DashboardSpec`.

    Returns
    -------
    DashboardSpec
        A **new** :class:`DashboardSpec` instance with ``surfaces.grid``
        populated.  The original *spec* is not mutated.
    """
    # Idempotent: already migrated
    if spec.surfaces.grid:
        return spec

    grid: SurfaceGrid = {}
    for widget in spec.widgets:
        if widget.pos is not None:
            p = widget.pos
            grid[widget.id] = SurfaceGridEntry(x=p.x, y=p.y, w=p.w, h=p.h)

    new_surfaces = Surfaces(grid=grid)
    data = spec.model_dump()
    data["surfaces"] = new_surfaces.model_dump()
    return DashboardSpec.model_validate(data)


# ---------------------------------------------------------------------------
# get_export_config — normalized export config accessor (T5)
# ---------------------------------------------------------------------------


def get_export_config(spec: "DashboardSpec") -> dict:
    """Return a fully-normalized export/report config dict for *spec*.

    The returned dict has the following shape (all keys always present)::

        {
            "title_slide": None | {"heading": str|None, "subheading": str|None},
            "header": str | None,
            "footer": str | None,
            "page_size": "A4" | "Letter" | "16:9",
            "widget_hints": {
                "<widgetId>": {
                    "include_in_report": bool,
                    "order": int | None,
                    "page_break_before": bool,
                    "caption": str | None,
                    "render_as": "auto" | "chart" | "table",
                },
                ...  # one entry per widget in spec.widgets
            },
        }

    Normalization rules
    -------------------
    - ``export`` absent / empty → every key carries its documented default.
    - Per-widget hints: for every widget in ``spec.widgets`` the entry in
      ``widget_hints`` is built by merging the stored hints (if any) over the
      :class:`WidgetExportHints` defaults. Widgets not mentioned in
      ``spec.export.widget_hints`` get pure defaults.
    - ``title_slide``: serialised to a plain ``{"heading", "subheading"}`` dict
      when present; ``None`` when absent (no title slide).

    Parameters
    ----------
    spec:
        A parsed :class:`DashboardSpec`.

    Returns
    -------
    dict
        Normalized export config dict. Always fully populated — callers do not
        need to handle missing keys.
    """
    cfg = spec.export

    # ── title_slide ───────────────────────────────────────────────────────────
    if cfg.title_slide is None:
        title_slide_out = None
    else:
        title_slide_out = {
            "heading": cfg.title_slide.heading,
            "subheading": cfg.title_slide.subheading,
        }

    # ── widget_hints: one entry per widget, defaults merged in ────────────────
    widget_hints_out: dict = {}
    for widget in spec.widgets:
        stored = cfg.widget_hints.get(widget.id)
        if stored is None:
            # Use the shared default instance — copy to a plain dict for output.
            hints_dict = _DEFAULT_WIDGET_HINTS.model_dump()
        else:
            # Merge: start from defaults, override with stored values.
            hints_dict = _DEFAULT_WIDGET_HINTS.model_dump()
            hints_dict.update(stored.model_dump(exclude_unset=False))
        widget_hints_out[widget.id] = hints_dict

    return {
        "title_slide": title_slide_out,
        "header": cfg.header,
        "footer": cfg.footer,
        "page_size": cfg.page_size,
        "widget_hints": widget_hints_out,
    }


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
    6b. Tabs (Track T): duplicate ``tab.id`` values and a ``widget.tab_id``
       not declared in ``spec.tabs`` are hard errors (drawer widgets are
       exempt; ``tab_id`` None implicitly maps to the first tab).
    7. Each ``query_id`` is checked against the live query registry.
       Unknown ids produce a warning (not a hard failure — forward compat).
    8. Data widgets (``kpi``/``table``/``chart``/``metric``/``pivot``) must carry
       at least one of: non-empty ``query_id``, ``metric`` binding, or ``source``
       provider binding. None of the three is a hard error.
    9. When a widget has a ``metric`` binding, its ``metric.metric_id`` is checked
       against the live metric registry. Unknown ids produce a WARNING (not a hard
       failure — mirrors the soft ``query_id`` rule so specs validate before the
       metric is registered).
    10. Header-bar placement sanity — a non-filter/text widget in 'header'
        placement is a soft WARNING.
    11. At-most-one binding (BET-3 hard error): a widget may carry at most one
        of: non-empty ``query_id`` / ``metric`` / ``source``.  Having more than
        one is ambiguous — hard error.
    12. Provider / result existence check (BET-3 hard error): ``widget.source``
        must reference a declared ``spec.data[*].id`` and a valid result name
        within that provider.

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

    # ── Step 6b: Tab validation (hard errors — mirror variable-ref rules) ─
    # Duplicate tab ids are a hard error; a widget.tab_id that does not match a
    # declared tab is a hard error (same severity as undeclared {ref} vars).
    # Drawer widgets ignore tab_id; widgets with tab_id None implicitly belong
    # to the first tab when tabs exist. Tabs absent/empty => unchanged behaviour.
    declared_tab_ids: set[str] = set()
    seen_tab_ids: set[str] = set()
    for tab in spec.tabs:
        if tab.id in seen_tab_ids:
            issues.append(
                f"Duplicate tab id {tab.id!r} — tab ids must be unique."
            )
        seen_tab_ids.add(tab.id)
        declared_tab_ids.add(tab.id)

    for widget in spec.widgets:
        # Drawer widgets stay global across tabs — their tab_id is ignored.
        if widget.drawer:
            continue
        if widget.tab_id is not None and widget.tab_id not in declared_tab_ids:
            issues.append(
                f"Widget {widget.id!r}: tab_id {widget.tab_id!r} is not declared "
                f"in spec 'tabs'. Declared tabs: {sorted(declared_tab_ids) or '[]'}."
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

    # ── Step 8: data widget must have a query_id OR a metric OR source binding ──
    # A data widget (kpi/table/chart/metric/pivot) needs a data source: either a
    # non-empty query_id, a metric binding, or a provider source (BET-3). None of
    # the three is a HARD error (mirrors the severity of a missing chart encoding).
    # Multiple bindings is caught by Step 11 (at-most-one); precedence when all
    # three somehow coexist: source > metric > query_id (resolver decides in the
    # later wave — at-most-one is the invariant in practice).
    _DATA_WIDGET_TYPES = {"kpi", "table", "chart", "metric", "pivot"}
    for widget in spec.widgets:
        if widget.type not in _DATA_WIDGET_TYPES:
            continue
        if not widget.query_id and widget.metric is None and widget.source is None:
            issues.append(
                f"Widget {widget.id!r} ({widget.type}): a data widget must have "
                "either a non-empty 'query_id', a 'metric' binding, or a "
                "'source' provider binding."
            )

    # ── Step 9: metric binding registry check (soft warning) ─────────────────
    # Mirror the soft query_id rule: an unknown metric_id is a WARNING (not a hard
    # error) so a spec authored before its metric is registered still validates.
    try:
        from app.metrics.registry import get_metric_registry  # noqa: PLC0415

        metric_registry = get_metric_registry()
        for widget in spec.widgets:
            if widget.metric is None:
                continue
            mid = widget.metric.metric_id
            if mid and metric_registry.get(mid) is None:
                issues.append(
                    f"[warn] Widget {widget.id!r}: metric_id {mid!r} is not in the "
                    "registered metric registry (may be a forward reference)."
                )
    except Exception:  # noqa: BLE001 — metric registry unavailable; skip silently
        pass

    # ── Step 10: header-bar placement sanity (soft warning) ──────────────────
    # The header filter bar is intended for filter/text widgets. A non-filter/
    # text widget placed there is a soft WARNING (not a hard error) — it still
    # renders; this just nudges the author. Mirrors the '[warn]'-prefixed soft
    # style used for metric forward-refs.
    for widget in spec.widgets:
        if effective_placement(widget) == "header" and widget.type not in (
            "filter",
            "text",
        ):
            issues.append(
                f"[warn] Widget {widget.id!r} ({widget.type}): placement 'header' "
                "is intended for filter/text widgets (the header bar is a filter "
                "bar) — a non-filter widget there may look out of place."
            )

    # ── Step 11: at-most-one binding (BET-3 hard error) ──────────────────────
    # A widget may carry AT MOST ONE of: non-empty query_id / metric / source.
    # Having more than one is a hard validation error — the spec is ambiguous.
    # (Existing boards with only query_id or only metric are unaffected.)
    for widget in spec.widgets:
        bindings = [
            ("query_id", bool(widget.query_id)),
            ("metric", widget.metric is not None),
            ("source", widget.source is not None),
        ]
        active = [name for name, active in bindings if active]
        if len(active) > 1:
            issues.append(
                f"Widget {widget.id!r}: at most one data binding may be set, "
                f"but found {active!r}. Use exactly one of: non-empty "
                "'query_id', 'metric', or 'source'."
            )

    # ── Step 12: provider / result existence check (hard errors, BET-3) ──────
    # A widget.source.provider must match a declared spec.data[*].id, and
    # widget.source.result must match one of that provider's results[*].name.
    provider_map: dict[str, set[str]] = {
        p.id: {r.name for r in p.results} for p in spec.data
    }
    for widget in spec.widgets:
        if widget.source is None:
            continue
        pid = widget.source.provider
        rname = widget.source.result
        if pid not in provider_map:
            issues.append(
                f"Widget {widget.id!r}: source.provider {pid!r} is not declared "
                f"in spec.data. Declared providers: "
                f"{sorted(provider_map.keys()) or '[]'}."
            )
        elif rname not in provider_map[pid]:
            issues.append(
                f"Widget {widget.id!r}: source.result {rname!r} is not declared "
                f"in provider {pid!r}. Declared results: "
                f"{sorted(provider_map[pid]) or '[]'}."
            )

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


def _metric_attrs(widget: Widget) -> str:
    """Additive metric-binding attributes for a custom element, or ``""``.

    When ``widget.metric`` is set we emit ``metric-id``, ``metric-dimensions``
    (comma-joined), ``metric-time-grain`` and ``metric-filters`` (JSON) so the
    embed renderer can drive ``POST /metrics/{id}/query`` instead of the query
    path. ``query-id`` is OMITTED for metric widgets (the metric binding takes
    precedence — see ``Widget.metric``). All values flow through ``_esc``.

    NOTE: the embed web-component that CONSUMES these attributes (reads them and
    calls the metric query endpoint) is a frontend follow-up — this only emits
    them. When ``widget.metric is None`` this returns ``""`` so the query_id path
    stays byte-identical to before.
    """
    mb = widget.metric
    if mb is None:
        return ""
    import json as _json  # noqa: PLC0415

    parts = [f' metric-id="{_esc(mb.metric_id)}"']
    if mb.dimensions:
        parts.append(f' metric-dimensions="{_esc(",".join(mb.dimensions))}"')
    if mb.time_grain:
        parts.append(f' metric-time-grain="{_esc(mb.time_grain)}"')
    if mb.filters:
        parts.append(f' metric-filters="{_esc(_json.dumps(mb.filters))}"')
    if mb.limit is not None:
        parts.append(f' metric-limit="{_esc(mb.limit)}"')
    return "".join(parts)


def _kpi_tag(widget: Widget) -> str:
    """Render a ``<nubi-kpi>`` element from a KPI widget.

    When ``widget.metric`` is set, emit the metric-binding attributes and OMIT
    ``query-id`` (the metric binding takes precedence); otherwise the query_id
    path is byte-identical to before.
    """
    # value-col: from encoding['value'] or encoding['y'] or props['value_col']
    value_col = (
        widget.encoding.get("value")
        or widget.encoding.get("y")
        or widget.props.get("value_col", "")
        or widget.encoding.get("x", "value")
    )
    label = widget.props.get("label", value_col.replace("_", " ").title())
    fmt = widget.props.get("format", "")

    if widget.metric is not None:
        parts = [f"<nubi-kpi{_metric_attrs(widget)}"]
    else:
        query_id = _esc(widget.query_id)
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
    """Render a ``<nubi-table>`` element from a table widget.

    When ``widget.metric`` is set, emit the metric-binding attributes and OMIT
    ``query-id``; otherwise the query_id path is byte-identical to before.
    """
    limit = widget.props.get("limit", 50)
    columns = widget.props.get("columns", "")

    if widget.metric is not None:
        parts = [f'<nubi-table{_metric_attrs(widget)} limit="{_esc(limit)}"']
    else:
        query_id = _esc(widget.query_id)
        parts = [f'<nubi-table query-id="{query_id}" limit="{_esc(limit)}"']
    if columns:
        if isinstance(columns, list):
            columns = ",".join(str(c) for c in columns)
        parts.append(f' columns="{_esc(columns)}"')
    parts.append("></nubi-table>")
    return "".join(parts)


def _chart_tag(widget: Widget) -> str:
    """Render a ``<nubi-chart>`` element from a chart widget.

    When ``widget.metric`` is set, emit the metric-binding attributes and OMIT
    ``query-id`` (the metric binding takes precedence); otherwise the query_id
    path is byte-identical to before. Chart encoding (x/y/color) is emitted in
    both cases — a metric-bound chart still needs encoding.
    """
    chart_type = widget.chart_type or "scatter"
    # Normalize to the subset supported by nubi-chart.js (scatter|line|bar).
    # Richer types degrade gracefully: area→line; pie/donut/heatmap/gauge→bar;
    # hbar→bar (orientation is lost in the embed renderer).
    _type_map = {
        "area": "line",
        "pie": "bar",
        "donut": "bar",
        "hbar": "bar",
        "heatmap": "bar",
        "gauge": "bar",
    }
    embed_type = _type_map.get(chart_type, chart_type)

    x_col = _esc(widget.encoding.get("x", "x"))
    y_col = _esc(widget.encoding.get("y", "y"))
    color_col = widget.encoding.get("color", "")

    if widget.metric is not None:
        parts = [
            f"<nubi-chart{_metric_attrs(widget)}"
            f' type="{_esc(embed_type)}"'
            f' x="{x_col}"'
            f' y="{y_col}"'
        ]
    else:
        query_id = _esc(widget.query_id)
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
      ``options-query-id``, ``style``, ``class``) plus the additive
      metric-binding attributes (``metric-id``, ``metric-dimensions``,
      ``metric-time-grain``, ``metric-filters``, ``metric-limit``) emitted in
      place of ``query-id`` for metric-bound data widgets.

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

    def _inner_tag(widget: "Widget") -> str:
        if widget.type == "kpi":
            return _kpi_tag(widget)
        if widget.type == "table":
            return _table_tag(widget)
        if widget.type == "chart":
            return _chart_tag(widget)
        if widget.type == "filter":
            return _filter_tag(widget)
        if widget.type == "text":
            return _text_tag(widget)
        return ""

    # Resolve the grid layout once (backward-compatible: surfaces.grid if
    # present, else derived from inline widget.pos).
    _grid_layout = get_surface_layout(spec, "grid")

    def _widget_line(widget: "Widget") -> str:
        # Prefer surfaces.grid entry; fall back to inline widget.pos.
        entry = _grid_layout.get(widget.id)
        if entry is None:
            if widget.pos is None:
                return ""  # no positional info at all → skip
            entry = widget.pos  # WidgetPos has the same x/y/w/h attrs
        wrapper_style = _WIDGET_WRAPPER_STYLE.format(
            col_start=entry.x, col_span=entry.w, row_start=entry.y, row_span=entry.h,
        )
        inner = _inner_tag(widget)
        if not inner:
            return ""
        return (
            f'  <div class="nubi-widget nubi-widget--{widget.type}"'
            f' style="{wrapper_style}">{inner}</div>'
        )

    def _header_bar(widgets: list["Widget"]) -> list[str]:
        """Render a horizontal filter bar containing ``widgets`` in ``order``.

        Returns ``[]`` when there are no header widgets so the output is
        byte-identical to before whenever nothing is header-placed (no empty
        bar). The bar spans the full grid width and ignores each widget's
        ``pos`` (header widgets are laid out by the frontend flex bar).
        """
        ordered = sorted(widgets, key=lambda w: w.order)
        inner_lines: list[str] = []
        for widget in ordered:
            inner = _inner_tag(widget)
            if not inner:
                continue
            inner_lines.append(
                f'    <div class="nubi-filter-bar__item'
                f' nubi-widget--{widget.type}">{inner}</div>'
            )
        if not inner_lines:
            return []
        return [
            '  <div class="nubi-filter-bar" style="grid-column:1/-1;'
            'display:flex;flex-wrap:wrap;gap:0.5rem;align-items:flex-end;">',
            *inner_lines,
            "  </div>",
        ]

    # Bucket every widget by its EFFECTIVE placement (legacy drawer=True =>
    # 'drawer'). Header widgets render in the filter bar; grid widgets in the
    # grid; drawer widgets stay in the drawer exactly as before.
    header_widgets = [w for w in spec.widgets if effective_placement(w) == "header"]
    grid_widgets = [w for w in spec.widgets if effective_placement(w) == "grid"]
    drawer_widgets = [w for w in spec.widgets if effective_placement(w) == "drawer"]

    tabs = spec.tabs or []
    if not tabs:
        # No tabs — header bar (if any) precedes the grid render. With no header
        # widgets this is byte-identical to the prior single-section render: the
        # non-header widgets (grid + legacy drawer) keep their original document
        # order. (The pre-placement no-tabs path rendered every widget inline in
        # order, so drawer-effective widgets stay where they were.)
        lines.extend(_header_bar(header_widgets))
        for widget in spec.widgets:
            if effective_placement(widget) == "header":
                continue
            line = _widget_line(widget)
            if line:
                lines.append(line)
    else:
        # Tabbed dashboard → stacked sections, one <h3> heading per tab in order.
        # A widget with tab_id=None belongs to the FIRST tab; drawer widgets stay
        # global (rendered once after all tab sections). Header widgets are tab-
        # scoped the same way grid widgets are — a per-tab filter bar before that
        # tab's grid widgets.
        for idx, tab in enumerate(tabs):
            label_safe = html.escape(tab.label or tab.id, quote=False)
            lines.append(
                f'  <div class="nubi-tab-section" data-tab-id="{html.escape(tab.id, quote=True)}"'
                f' style="grid-column:1/-1;">'
                f'<h3 style="margin:1rem 0 0.5rem;font-size:1.05rem;font-weight:600;">'
                f"{label_safe}</h3></div>"
            )

            def _on_tab(widget: "Widget", _idx: int = idx, _tab=tab) -> bool:
                wt = widget.tab_id
                return wt == _tab.id or (wt is None and _idx == 0)

            lines.extend(_header_bar([w for w in header_widgets if _on_tab(w)]))
            for widget in grid_widgets:
                if _on_tab(widget):
                    line = _widget_line(widget)
                    if line:
                        lines.append(line)
        for widget in drawer_widgets:
            line = _widget_line(widget)
            if line:
                lines.append(line)

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
