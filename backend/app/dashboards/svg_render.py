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

import json
import os
import subprocess
from typing import Any

from app.dashboards.spec import DashboardSpec, get_surface_layout
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
# Low-level subprocess helpers
# ---------------------------------------------------------------------------


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

    input_bytes = json.dumps(payload).encode("utf-8")
    try:
        result = subprocess.run(
            [node_bin, script_path],
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            "renderer_timeout",
            f"Node renderer timed out after {timeout}s: {script_path!r}.",
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
        {"widgets": widget_payloads},
        timeout=timeout,
    )
    return list(result.get("widgets") or [])


def compose_page_svg(
    surface_widgets: list[dict[str, Any]],
    *,
    surface: str = "grid",
    cols: int = 12,
    page_width_px: int = _DEFAULT_PAGE_WIDTH_PX,
    row_height_px: int = _DEFAULT_ROW_HEIGHT_PX,
    margin_px: int = _DEFAULT_MARGIN_PX,
    gap_px: int = _DEFAULT_GAP_PX,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call the SVG-composer subprocess to assemble a full page SVG.

    Parameters
    ----------
    surface_widgets:
        List of ``{id, layout: {x,y,w,h}, svg, webgl, error?}`` dicts.
        ``layout`` uses 1-based x/y (same units as ``SurfaceGridEntry``).
    surface:
        Surface name (``'grid'`` or ``'slides'``).
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
        "widgets": surface_widgets,
    }
    return _run_node_script(_SVG_COMPOSER_SCRIPT, payload, timeout=timeout)


# ---------------------------------------------------------------------------
# Public API: high-level
# ---------------------------------------------------------------------------


def render_board_svg(
    spec: DashboardSpec,
    widget_data: list[dict[str, Any]],
    *,
    surface: str = "grid",
    page_width_px: int = _DEFAULT_PAGE_WIDTH_PX,
    row_height_px: int = _DEFAULT_ROW_HEIGHT_PX,
    margin_px: int = _DEFAULT_MARGIN_PX,
    gap_px: int = _DEFAULT_GAP_PX,
    ssr_timeout: float = 60.0,
    compose_timeout: float = 30.0,
) -> str:
    """Render a board to a composed page SVG string.

    This is the main entry point called by the backend (flows tasks, report
    delivery, PDF/PPTX pipeline).  It:

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
        Layout surface to render (``'grid'`` only for now; ``'report'`` and
        ``'slides'`` are reserved).
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
        When *surface* is not ``'grid'`` (reserved surfaces not yet implemented).
    """
    cols = int((spec.layout or {}).get("cols", 12))

    # 1. Resolve grid layout (backward-compatible accessor).
    layout = get_surface_layout(spec, surface)

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

    for widget in spec.widgets:
        # Only grid-placed widgets have a position in the surface layout.
        entry = layout.get(widget.id)
        if entry is None:
            continue  # header/drawer widgets have no grid position

        w_px = max(1, int(round(entry.w * col_w_px - gap_px)))
        h_px = max(1, int(entry.h * row_height_px - gap_px))

        payloads.append(
            _widget_to_payload(
                widget,
                data_by_widget.get(widget.id),
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
    rendered = render_widgets_svg(payloads, timeout=ssr_timeout)

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
    from app.repos.provider import Repo  # noqa: PLC0415 — avoid circular at module level

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

    widget_data = await collect_board_data(board_id, org_id, claims=claims, repo=repo)

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
