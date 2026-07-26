"""Server-side SVG render glue — Python entry point for T2.

Bridges the Python backend to the Node.js echarts-SSR pipeline
(``scripts/render/echarts-ssr.mjs`` + ``scripts/render/svg-composer.mjs``).

Design
------
* **Node subprocess** — the renderer runs as a child process:
    ``node scripts/render/echarts-ssr.mjs``
  JSON is passed on stdin; the SVG result is read from stdout.
  A separate call to ``svg-composer.mjs`` composes the per-widget SVGs into a
  full page SVG.
* **Lazy import** — Node.js is checked only when the function is first called.
  A missing Node binary raises :class:`~app.errors.AppError` with a clear
  install message (never an ImportError).
* **Reuses existing infrastructure** — widget data comes from
  :func:`app.dashboards.collect.collect_board_data` (or a pre-collected list)
  and the layout is read via :func:`app.dashboards.spec.get_surface_layout`.

Public API
----------
``render_board_svg(spec, widget_data, *, surface='grid', ...) -> str``
    Given a validated :class:`~app.dashboards.spec.DashboardSpec` and the
    pre-collected widget data (from ``collect_board_data``), render the board
    as a composed page SVG string.

``render_widgets_svg(widget_payloads) -> list[dict]``
    Low-level: call the Node SSR subprocess with the given widget payloads,
    return the list of ``{id, svg, webgl, error?}`` dicts.

``compose_page_svg(surface_widgets, ...) -> dict``
    Low-level: call the Node SVG-composer subprocess, return the composed
    page dict ``{svg, page_width_px, page_height_px, widget_rects}``.

``render_board_svg_from_data(board_id, org_id, claims, repo, ...) -> str``  [async]
    Convenience wrapper: collect board data then render.  Equivalent to calling
    ``collect_board_data`` + ``render_board_svg`` in sequence.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
from typing import Any

from app.dashboards.spec import DashboardSpec, get_surface_layout, validate_spec
from app.errors import AppError

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(_BACKEND_ROOT)
_ECHARTS_SSR_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "render", "echarts-ssr.mjs")
_SVG_COMPOSER_SCRIPT = os.path.join(_REPO_ROOT, "scripts", "render", "svg-composer.mjs")

# Default page geometry (mirrors svg-composer.mjs defaults).
_DEFAULT_PAGE_WIDTH_PX = 1200
_DEFAULT_ROW_HEIGHT_PX = 120
_DEFAULT_MARGIN_PX = 16
_DEFAULT_GAP_PX = 8

# Widget pixel dimensions fed to echarts-ssr.mjs (computed from grid pos in
# render_board_svg; these are per-widget fallbacks when no layout is known).
_DEFAULT_WIDGET_WIDTH_PX = 800
_DEFAULT_WIDGET_HEIGHT_PX = 400

# ---------------------------------------------------------------------------
# Concurrency limiter — cap concurrent Node subprocesses at ~2× CPU count.
# Each export spawns up to two Node processes (SSR + composer); this semaphore
# prevents unbounded forking under concurrent export requests.
# ---------------------------------------------------------------------------

def _make_node_semaphore() -> threading.BoundedSemaphore:
    cpu = os.cpu_count() or 2
    return threading.BoundedSemaphore(max(2, cpu * 2))


# Module-level singleton; tests may replace this to verify the cap.
_NODE_SEMAPHORE: threading.BoundedSemaphore = _make_node_semaphore()


# ---------------------------------------------------------------------------
# Node binary check (lazy)
# ---------------------------------------------------------------------------


def _require_node() -> str:
    """Return the path to the Node.js binary, raising AppError if unavailable.

    Checks ``$NODE_PATH`` env var first, then falls back to ``node`` on PATH.
    """
    import shutil  # noqa: PLC0415

    node_path = os.environ.get("NODE_PATH") or shutil.which("node")
    if not node_path:
        raise AppError(
            "node_not_found",
            "Node.js is required for SVG rendering but was not found on PATH. "
            "Install Node.js >= 18 and ensure `node` is on the system PATH, or "
            "set the NODE_PATH environment variable to the node binary.",
            503,
        )
    return node_path


def _require_script(path: str) -> str:
    """Ensure the renderer script exists, raising AppError if missing."""
    if not os.path.exists(path):
        raise AppError(
            "renderer_script_missing",
            f"Renderer script not found: {path!r}. "
            "Ensure the Nubi repository is complete (scripts/render/ directory).",
            503,
        )
    return path


# ---------------------------------------------------------------------------
# Availability check (used by tests and callers to gate on echarts being
# installed — returns True only when Node *and* the echarts module are both
# reachable, so tests skip cleanly instead of failing with a confusing error).
# ---------------------------------------------------------------------------

_ECHARTS_AVAILABLE: bool | None = None  # cached result


def renderer_available() -> bool:
    """Return True when Node.js *and* the echarts SSR script can run.

    Runs the echarts script with an empty widget list and checks for a clean
    exit.  The result is cached at module level so repeated calls (e.g. from
    multiple tests) pay the subprocess cost at most once per process.

    Use this as a pytest skip guard::

        pytestmark = pytest.mark.skipif(
            not renderer_available(),
            reason="Node.js / echarts not available",
        )
    """
    global _ECHARTS_AVAILABLE  # noqa: PLW0603
    if _ECHARTS_AVAILABLE is not None:
        return _ECHARTS_AVAILABLE

    import shutil  # noqa: PLC0415

    node_bin = os.environ.get("NODE_PATH") or shutil.which("node")
    if not node_bin or not os.path.exists(_ECHARTS_SSR_SCRIPT):
        _ECHARTS_AVAILABLE = False
        return False

    try:
        result = subprocess.run(
            [node_bin, _ECHARTS_SSR_SCRIPT],
            input=b'{"widgets":[]}',
            capture_output=True,
            timeout=30.0,
        )
        _ECHARTS_AVAILABLE = result.returncode == 0
    except Exception:  # noqa: BLE001
        _ECHARTS_AVAILABLE = False

    return _ECHARTS_AVAILABLE


# ---------------------------------------------------------------------------
# Low-level subprocess helpers
# ---------------------------------------------------------------------------


def _json_default(o: Any) -> Any:
    """Coerce driver-native values into something JSON (and ECharts) can take.

    Widget rows arrive straight off the database driver, so they carry real
    Python objects — ``datetime.date`` for a day axis, ``Decimal`` for a numeric
    column, occasionally ``UUID``/``bytes``. Plain ``json.dumps`` raises
    ``TypeError: Object of type date is not JSON serializable`` on the first of
    those, which surfaced as a **500 on any board with a date axis** (and took
    the PDF export down the same way). It went unnoticed because boards whose SQL
    already ``CAST(... AS CHAR)``s its date axis hand over plain strings.

    Decimal → float, not str: it is a MEASURE, and a stringified number would be
    plotted by ECharts as a category. Dates → ISO strings, which is what a
    category axis wants anyway.
    """
    import datetime as _dt  # noqa: PLC0415
    import decimal as _dec  # noqa: PLC0415
    import uuid as _uuid  # noqa: PLC0415

    if isinstance(o, _dec.Decimal):
        # float() can lose precision on absurd scales, but a chart pixel cannot
        # express it anyway — and NaN/Inf would break json.dumps, so guard.
        try:
            f = float(o)
        except (ValueError, OverflowError):
            return str(o)
        return f if f == f and f not in (float("inf"), float("-inf")) else str(o)
    if isinstance(o, (_dt.datetime, _dt.date, _dt.time)):
        return o.isoformat()
    if isinstance(o, _dt.timedelta):
        return o.total_seconds()
    if isinstance(o, _uuid.UUID):
        return str(o)
    if isinstance(o, (bytes, bytearray, memoryview)):
        return bytes(o).decode("utf-8", "replace")
    if isinstance(o, set):
        return list(o)
    return str(o)


def _run_node_script(
    script_path: str,
    payload: dict[str, Any],
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run a Node.js script with JSON payload on stdin, parse JSON from stdout.

    Parameters
    ----------
    script_path:
        Absolute path to the ``.mjs`` script.
    payload:
        Dict that will be JSON-serialised onto the script's stdin.
    timeout:
        Subprocess timeout in seconds (default 60 — SSR can be slow for large boards).

    Returns
    -------
    dict
        Parsed JSON from the script's stdout.

    Raises
    ------
    AppError("renderer_error", …)
        On non-zero exit code, timeout, or invalid JSON response.
    """
    node_bin = _require_node()
    _require_script(script_path)

    input_bytes = json.dumps(payload, default=_json_default).encode("utf-8")
    try:
        with _NODE_SEMAPHORE:
            result = subprocess.run(
                [node_bin, script_path],
                input=input_bytes,
                capture_output=True,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or b"").decode("utf-8", errors="replace")[:200]
        raise AppError(
            "renderer_timeout",
            f"Node renderer timed out after {timeout}s: {script_path!r}."
            + (f" Partial output: {partial}" if partial else ""),
            503,
        ) from exc
    except OSError as exc:
        raise AppError(
            "renderer_error",
            f"Failed to launch Node renderer: {exc}",
            503,
        ) from exc

    if result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        # Exit code 2 is the echarts-ssr convention for "echarts not installed".
        if result.returncode == 2 and "not available" in stderr:
            raise AppError(
                "echarts_not_installed",
                "echarts is not installed. Run `npm install` in the repo root "
                f"to install it. stderr: {stderr[:300]}",
                503,
            )
        raise AppError(
            "renderer_error",
            f"Node renderer exited with code {result.returncode}. "
            f"stderr: {stderr[:500]}",
            503,
        )

    stdout = (result.stdout or b"").decode("utf-8", errors="replace").strip()
    if not stdout:
        raise AppError(
            "renderer_empty_output",
            "Node renderer produced no output.",
            503,
        )

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AppError(
            "renderer_invalid_json",
            f"Node renderer returned invalid JSON: {exc}. "
            f"Output preview: {stdout[:200]}",
            503,
        ) from exc


# ---------------------------------------------------------------------------
# Widget payload builder
# ---------------------------------------------------------------------------


def _resolve_stepper_step(
    widget: Any,
    data_by_widget: dict[str, dict[str, Any]],
) -> tuple[Any, dict[str, Any] | None]:
    """For a ``stepper``, return its first step's widget + that child's data.

    A stepper is a container: it holds several widgets and shows one at a time,
    so it has no content of its own to draw. The first step is what a viewer
    sees when the board loads, which makes it the honest thing to put in a
    static picture.

    The child keeps its own id (that is how its data was collected) but has no
    position — it occupies the parent's tile — so the caller renders it at the
    stepper's rect. Anything else, or a malformed/empty stepper, passes through
    untouched.
    """
    if getattr(widget, "type", None) != "stepper":
        return widget, data_by_widget.get(widget.id)

    props = getattr(widget, "props", None) or {}
    steps = props.get("steps") if isinstance(props, dict) else None
    if not isinstance(steps, list) or not steps:
        return widget, data_by_widget.get(widget.id)

    first = steps[0]
    child = first.get("widget") if isinstance(first, dict) else None
    if not isinstance(child, dict):
        return widget, data_by_widget.get(widget.id)

    # The child's data was collected under the CHILD's id, but everything
    # downstream — the layout map and the composer's svg-by-id merge — is keyed
    # by the PARENT's id, since the parent is the widget with a position. So
    # look the data up by the child's id, then render it under the parent's.
    child_data = data_by_widget.get(str(child.get("id") or ""))

    # Children are plain dicts inside props (the Widget model treats props as an
    # opaque pass-through), so validate to the same model the renderer expects.
    try:
        from app.dashboards.spec import Widget  # noqa: PLC0415

        child_spec = dict(child)
        child_spec["id"] = widget.id
        # Widget requires a position; the caller overrides the rect anyway.
        child_spec.setdefault("pos", {"x": 1, "y": 1, "w": 1, "h": 1})
        child_widget = Widget.model_validate(child_spec)
    except Exception:  # noqa: BLE001 - an unparseable child just isn't drawn
        return widget, data_by_widget.get(widget.id)

    return child_widget, child_data


def _widget_to_payload(
    widget_spec: Any,
    widget_data: dict[str, Any] | None,
    *,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Build the per-widget payload dict for echarts-ssr.mjs.

    Parameters
    ----------
    widget_spec:
        A :class:`~app.dashboards.spec.Widget` instance.
    widget_data:
        The collected data entry ``{widget_id, query_id, columns, rows}`` for
        this widget (or ``None`` / an entry with an ``error`` key).
    width, height:
        Pixel dimensions for this widget's SVG.

    Returns
    -------
    dict
        Payload entry for the ``widgets`` array sent to ``echarts-ssr.mjs``.
    """
    columns: list[str] = []
    rows: list[list[Any]] = []
    if widget_data and "error" not in widget_data:
        columns = list(widget_data.get("columns") or [])
        rows = list(widget_data.get("rows") or [])

    return {
        "id": widget_spec.id,
        "type": widget_spec.type,
        "chart_type": widget_spec.chart_type,
        "encoding": dict(widget_spec.encoding or {}),
        "props": dict(widget_spec.props or {}),
        # The widget's own look — background, text colour, radius. Omitting this
        # was why every server render came out light: a board styled with dark
        # tiles rendered on the SSR default white, so the picture was accurate
        # about the DATA and wrong about the DESIGN.
        "style": dict(widget_spec.style or {}),
        "content": widget_spec.content,
        "columns": columns,
        "rows": rows,
        "width": width,
        "height": height,
    }


# ---------------------------------------------------------------------------
# Public API: low-level
# ---------------------------------------------------------------------------


def render_widgets_svg(
    widget_payloads: list[dict[str, Any]],
    theme: str = "light",
    *,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Call the echarts-SSR subprocess to render a list of widget payloads.

    Parameters
    ----------
    widget_payloads:
        List of widget payload dicts (shape accepted by ``echarts-ssr.mjs``).
    timeout:
        Subprocess timeout in seconds.

    Returns
    -------
    list[dict]
        One entry per widget: ``{id, svg, webgl, error?}``.
        ``svg`` is a valid SVG string, or ``None`` on error / WebGL widgets.
    """
    result = _run_node_script(
        _ECHARTS_SSR_SCRIPT,
        {"widgets": widget_payloads, "theme": theme},
        timeout=timeout,
    )
    return list(result.get("widgets") or [])


_PAGE_BG = {"light": "#ffffff", "dark": "#111a2e"}   # mirrors --surface in src/index.css


def compose_page_svg(
    surface_widgets: list[dict[str, Any]],
    *,
    surface: str = "grid",
    cols: int = 12,
    page_width_px: int = _DEFAULT_PAGE_WIDTH_PX,
    row_height_px: int = _DEFAULT_ROW_HEIGHT_PX,
    margin_px: int = _DEFAULT_MARGIN_PX,
    gap_px: int = _DEFAULT_GAP_PX,
    page_bg: str = "#ffffff",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call the SVG-composer subprocess to assemble a full page SVG.

    Parameters
    ----------
    surface_widgets:
        List of ``{id, layout: {x,y,w,h}, svg, webgl, error?}`` dicts.
        ``layout`` uses 1-based x/y (same units as ``SurfaceGridEntry``).
    surface:
        Surface name (``'grid'`` — the only surface Nubi renders).
    cols:
        Grid column count (from ``spec.layout.cols``).
    page_width_px, row_height_px, margin_px, gap_px:
        Page geometry in pixels.
    timeout:
        Subprocess timeout in seconds.

    Returns
    -------
    dict
        ``{svg: str, page_width_px: int, page_height_px: int, widget_rects: list}``.
    """
    payload: dict[str, Any] = {
        "surface": surface,
        "cols": cols,
        "page_width_px": page_width_px,
        "row_height_px": row_height_px,
        "margin_px": margin_px,
        "gap_px": gap_px,
        "page_bg": page_bg,
        "widgets": surface_widgets,
    }
    return _run_node_script(_SVG_COMPOSER_SCRIPT, payload, timeout=timeout)


# ---------------------------------------------------------------------------
# Public API: high-level
# ---------------------------------------------------------------------------


# Top-level spec keys that describe BEHAVIOUR, not the picture. A renderer draws
# geometry; these have no pixels.
_NON_VISUAL_SPEC_KEYS = ("variables",)


def spec_for_render(spec_dict: dict[str, Any], *, fallback_title: str | None = None):
    """Parse *spec_dict* into a DashboardSpec good enough to DRAW, or None.

    The strict validator (``validate_spec``) is the wrong gate for a picture, and
    using it as one had a visible cost: **every board with a filter lost its
    thumbnail**. Filter boards carry ``spec.variables``, and real boards declare
    e.g. ``variables[].type: "string"`` (or a filter ``subtype: "list"``) — values
    the Pydantic schema rejects but the FRONTEND renderer happily ignores. Those
    boards work perfectly in the app; they simply could not be validated, so they
    could not be drawn.

    Neither field has anything to do with the output: ``variables`` are filter
    state, and a filter widget renders as a placeholder chip regardless of its
    subtype. So when the whole spec won't validate, retry without the parts that
    cannot affect pixels, and draw what's left.

    This is deliberately a RENDERING concession, not a loosening of the product's
    contract — ``validate_spec`` and ``POST /dashboards/validate`` are untouched,
    so a genuinely malformed spec still fails there. The underlying problem is
    that the backend schema has drifted behind the frontend renderer (the combo
    ``encoding.y`` array form is rejected the same way); fixing that drift at the
    schema is a separate, contract-level decision.

    Returns ``None`` when even the geometry won't parse — callers should then
    show a placeholder rather than a blank, which would be a convincing lie.
    """
    validated, _ = validate_spec(spec_dict)
    if validated is not None:
        return validated

    if not isinstance(spec_dict, dict):
        return None

    probe = copy.deepcopy(spec_dict)
    for key in _NON_VISUAL_SPEC_KEYS:
        probe.pop(key, None)
    for w in probe.get("widgets") or []:
        # A filter draws as a chip; its subtype only decides how it BEHAVES.
        if isinstance(w, dict) and w.get("type") == "filter":
            w.pop("subtype", None)
    if fallback_title and not probe.get("title"):
        probe["title"] = fallback_title

    validated, _ = validate_spec(probe)
    return validated


def widgets_for_tab(spec: DashboardSpec, tab_id: str | None = None) -> list[Any]:
    """The widgets that belong on one tab — the SHARED partition contract.

    Mirrors the frontend renderer exactly (SpecRenderer's tab partition, and
    ``widgetsForTab`` in the editor):

      - no tabs declared        → every widget (today's behaviour)
      - widget.tab_id == tab    → in
      - widget.tab_id is None   → belongs to the FIRST tab

    Why this exists: ``render_board_svg`` used to iterate ``spec.widgets``
    wholesale, so a tabbed board rendered EVERY tab stacked on top of itself.
    Tabs reuse the same grid coordinates, so on the real 5-tab MacMobile board
    that meant 31 widgets fighting for the 9 widgets' worth of space a viewer
    actually sees — five different widgets all at grid cell (1,1). It looked
    like a layout engine bug; it was a missing partition. This affected the PDF
    export too, not just thumbnails.
    """
    tabs = list(getattr(spec, "tabs", None) or [])
    if not tabs:
        return list(spec.widgets)
    first_tab_id = getattr(tabs[0], "id", None)
    effective = tab_id or first_tab_id
    out = []
    for w in spec.widgets:
        wt = getattr(w, "tab_id", None)
        if wt == effective or (wt is None and effective == first_tab_id):
            out.append(w)
    return out


def render_board_svg(
    spec: DashboardSpec,
    widget_data: list[dict[str, Any]],
    *,
    surface: str = "grid",
    tab_id: str | None = None,
    theme: str = "light",
    page_width_px: int = _DEFAULT_PAGE_WIDTH_PX,
    row_height_px: int | None = None,
    margin_px: int = _DEFAULT_MARGIN_PX,
    gap_px: int = _DEFAULT_GAP_PX,
    ssr_timeout: float = 60.0,
    compose_timeout: float = 30.0,
) -> str:
    """Render a board to a composed page SVG string.

    This is the main entry point called by the backend (flows tasks, report
    delivery, PDF pipeline).  It:

    1. Resolves the grid layout via :func:`~app.dashboards.spec.get_surface_layout`
       (backward-compatible — handles both new ``surfaces.grid`` and legacy
       ``widget.pos`` boards).
    2. Computes each widget's pixel dimensions from its grid position.
    3. Calls the Node echarts-SSR subprocess to render per-widget SVGs.
    4. Calls the Node SVG-composer subprocess to assemble the page SVG.

    Parameters
    ----------
    spec:
        A validated :class:`~app.dashboards.spec.DashboardSpec`.
    widget_data:
        Pre-collected widget data — output of
        :func:`~app.dashboards.collect.collect_board_data` (list of
        ``{widget_id, query_id, columns?, rows?, error?}`` dicts).
    surface:
        Layout surface to render. Only ``'grid'`` is defined.
    page_width_px:
        Output page width in pixels (default 1200).
    row_height_px:
        Output row height in pixels — each grid row unit becomes this many
        pixels on the page (default 120, ≈ 2× the frontend 60-unit row_height
        for better print fidelity).
    margin_px, gap_px:
        Outer margin and inter-widget gap in pixels.
    ssr_timeout, compose_timeout:
        Subprocess timeouts in seconds.

    Returns
    -------
    str
        A valid SVG string (outer ``<svg>`` element enclosing all widget SVGs).

    Raises
    ------
    AppError("node_not_found", 503)
        When Node.js is not on the system PATH.
    AppError("renderer_error", 503)
        When the Node subprocess exits non-zero.
    ValueError
        When *surface* is not ``'grid'``.
    """
    cols = int((spec.layout or {}).get("cols", 12))

    # Honour the board's OWN row height. This used to always use the module
    # default (120), so a board declaring the usual row_height=60 rendered at
    # double height — every widget correctly placed relative to the others, but
    # the whole board twice as tall as it looks in the app. An explicit caller
    # argument still wins.
    if row_height_px is None:
        _layout_cfg = spec.layout or {}
        _spec_rh = _layout_cfg.get("row_height")
        _rh = int(_spec_rh) if _spec_rh else _DEFAULT_ROW_HEIGHT_PX

        # Rows stack at (row_height + marginY), not at row_height — that is how
        # react-grid-layout lays out the real board, so it is what the picture
        # has to match. Taking row_height literally rendered a board declaring
        # `row_height: 1, margin: [10, 10]` (an 11px pitch) as a 124px-tall
        # sliver at 1000px wide: every widget correctly placed, but squeezed to
        # ~8% of its true height and illegible.
        _margin = _layout_cfg.get("margin")
        _margin_y = (
            int(_margin[1])
            if isinstance(_margin, (list, tuple)) and len(_margin) > 1
            else 0
        )
        row_height_px = _rh + _margin_y

    # 1. Resolve grid layout (backward-compatible accessor).
    layout = get_surface_layout(spec, surface)

    # Partition to ONE tab — tabs share grid coordinates, so rendering them all
    # stacks them (see widgets_for_tab).
    tab_widgets = widgets_for_tab(spec, tab_id)

    # Index widget data by widget_id for quick lookup.
    data_by_widget: dict[str, dict[str, Any]] = {
        entry["widget_id"]: entry
        for entry in widget_data
        if isinstance(entry, dict) and "widget_id" in entry
    }

    col_w_px = (page_width_px - 2 * margin_px) / cols

    # 2. Build per-widget payload for echarts-ssr.mjs.
    payloads: list[dict[str, Any]] = []
    widget_layout_map: dict[str, dict[str, Any]] = {}

    for widget in tab_widgets:
        # Only grid-placed widgets have a position in the surface layout.
        entry = layout.get(widget.id)
        if entry is None:
            continue  # header/drawer widgets have no grid position

        w_px = max(1, int(round(entry.w * col_w_px - gap_px)))
        h_px = max(1, int(entry.h * row_height_px - gap_px))

        # A stepper shows one child at a time in its tile, so a static render
        # draws the step a viewer sees on arrival — the first. Rendering the
        # container itself would only ever produce an "unknown widget" chip.
        render_spec, render_data = _resolve_stepper_step(widget, data_by_widget)

        payloads.append(
            _widget_to_payload(
                render_spec,
                render_data,
                width=w_px,
                height=h_px,
            )
        )
        widget_layout_map[widget.id] = {
            "x": entry.x,
            "y": entry.y,
            "w": entry.w,
            "h": entry.h,
        }

    if not payloads:
        # Board has no placeable widgets — return a minimal valid SVG.
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{page_width_px}" height="{row_height_px + 2 * margin_px}">'
            f'<rect width="{page_width_px}" height="{row_height_px + 2 * margin_px}" fill="#ffffff"/>'
            f'</svg>'
        )

    # 3. Render per-widget SVGs via Node SSR.
    rendered = render_widgets_svg(payloads, theme=theme, timeout=ssr_timeout)

    # 4. Merge layout + SVG for the composer.
    svg_by_id: dict[str, dict[str, Any]] = {r["id"]: r for r in rendered if "id" in r}
    surface_widgets: list[dict[str, Any]] = []
    for wid, layout_entry in widget_layout_map.items():
        svg_entry = svg_by_id.get(wid, {})
        surface_widgets.append(
            {
                "id": wid,
                "layout": layout_entry,
                "svg": svg_entry.get("svg"),
                "webgl": bool(svg_entry.get("webgl")),
                "error": svg_entry.get("error"),
            }
        )

    # 5. Compose the full page SVG.
    composed = compose_page_svg(
        surface_widgets,
        surface=surface,
        cols=cols,
        page_width_px=page_width_px,
        row_height_px=row_height_px,
        margin_px=margin_px,
        gap_px=gap_px,
        page_bg=_PAGE_BG.get(theme, _PAGE_BG["light"]),
        timeout=compose_timeout,
    )
    return composed["svg"]


async def render_board_svg_from_data(
    board_id: str,
    org_id: str,
    claims: dict[str, Any],
    repo: Any,
    *,
    surface: str = "grid",
    page_width_px: int = _DEFAULT_PAGE_WIDTH_PX,
    row_height_px: int = _DEFAULT_ROW_HEIGHT_PX,
    margin_px: int = _DEFAULT_MARGIN_PX,
    gap_px: int = _DEFAULT_GAP_PX,
    ssr_timeout: float = 60.0,
    compose_timeout: float = 30.0,
) -> str:
    """Async convenience wrapper: collect board data then render to SVG.

    Calls :func:`~app.dashboards.collect.collect_board_data` to fetch widget
    data, then delegates to :func:`render_board_svg`.

    Parameters
    ----------
    board_id, org_id, claims, repo:
        Forwarded verbatim to ``collect_board_data``.
    surface, page_width_px, row_height_px, margin_px, gap_px:
        Forwarded to ``render_board_svg``.
    ssr_timeout, compose_timeout:
        Subprocess timeouts.

    Returns
    -------
    str
        Composed page SVG string.
    """
    from app.dashboards.collect import collect_board_data  # noqa: PLC0415

    board = await repo.get("boards", org_id, board_id)
    if board is None:
        raise AppError("board_not_found", f"Board {board_id!r} not found.", 404)

    # Parse spec (best-effort — fall back to empty spec on parse failure).
    from app.dashboards.spec import DashboardSpec, validate_spec  # noqa: PLC0415

    config = board.get("config") or {}
    spec_dict = config.get("spec") or {}
    parsed_spec, _ = validate_spec(spec_dict)
    if parsed_spec is None:
        # Unparseable spec — render a blank page.
        parsed_spec = DashboardSpec(title=board.get("name") or board_id, widgets=[])

    widget_data = await collect_board_data(board_id, org_id, claims=claims, repo=repo, board=board)

    return render_board_svg(
        parsed_spec,
        widget_data,
        surface=surface,
        page_width_px=page_width_px,
        row_height_px=row_height_px,
        margin_px=margin_px,
        gap_px=gap_px,
        ssr_timeout=ssr_timeout,
        compose_timeout=compose_timeout,
    )
